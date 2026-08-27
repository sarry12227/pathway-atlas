import ast
import csv
import contextlib
import importlib
import inspect
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]

PRIVATE_OR_UNREVIEWED_PATHS = (
    "scripts/export_from_system.py",
    "scripts/parity_check.py",
    "scripts/fetch_via_qr.py",
    "data/hubei",
    "output",
    "scripts/build_template.py",
    "templates/方案模板.docx",
    "tests/test_docx_export.py",
    "scripts/verify_province.py",
    "tests/test_province_onboarding.py",
    "data/demo-xx",
    "scripts/estimate_rank.py",
    "tests/test_rank_estimation.py",
    "tests/test_generate_report.py",
    "scripts/recommend.py",
    "scripts/recommend_paths.py",
    "tests/test_path_recommend.py",
)

FORBIDDEN_MODULE_BASENAMES = frozenset(
    Path(relative).stem.casefold()
    for relative in PRIVATE_OR_UNREVIEWED_PATHS
    if relative.startswith("scripts/") and relative.endswith(".py")
)
FORBIDDEN_PATH_FRAGMENTS = tuple(
    relative.replace("\\", "/").casefold().strip("/")
    for relative in PRIVATE_OR_UNREVIEWED_PATHS
)

_MAX_STATIC_AST_DEPTH = 12
_MAX_STATIC_TEXT_LENGTH = 4096
_PATH_CONSTRUCTORS = frozenset({"Path", "PurePath", "pathlib.Path", "pathlib.PurePath"})
_PATH_JOIN_CALLS = frozenset({"os.path.join", "posixpath.join", "ntpath.join"})
_DYNAMIC_IMPORT_CALLS = frozenset(
    {"importlib.import_module", "import_module", "__import__"}
)

_RECOMMENDATION_RUNTIME_PATHS = frozenset({
    "scripts/generate_report.py",
    "scripts/path_recommend.py",
    "scripts/rank_calc.py",
    "scripts/school_recommend.py",
})
_FORBIDDEN_RECOMMENDATION_SYMBOLS = frozenset({
    "EQUIV_RANK_ADJUST",
    "_tier_threshold_labels",
    "equiv_adjust_from_config",
    "params_from_config",
    "recommend_paths",
})
_FORBIDDEN_REPORT_DATA_CALLS = frozenset({
    "load_path_table",
    "load_province_config",
    "load_toudang",
    "load_yifenyiduan",
    "score_to_rank",
})


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _dotted_name(node.value)
        if owner is not None:
            return f"{owner}.{node.attr}"
    return None


def _isolated_python_environment(source=None) -> dict[str, str]:
    environment = dict(os.environ if source is None else source)
    for name in tuple(environment):
        if name.upper().startswith("PYTHON"):
            del environment[name]
    return environment


def _join_static_path(parts: tuple[str, ...]) -> str | None:
    if not parts:
        return None
    normalized = [part.replace("\\", "/") for part in parts]
    result = normalized[0].rstrip("/")
    for part in normalized[1:]:
        result = f"{result}/{part.strip('/')}"
    if len(result) > _MAX_STATIC_TEXT_LENGTH:
        return None
    return result


def _fold_static_text(node: ast.AST, depth: int = 0) -> str | None:
    """Fold a deliberately finite subset of string/path AST without eval."""

    if depth > _MAX_STATIC_AST_DEPTH:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if len(node.value) <= _MAX_STATIC_TEXT_LENGTH:
            return node.value
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_static_text(node.left, depth + 1)
        right = _fold_static_text(node.right, depth + 1)
        if left is None or right is None or len(left) + len(right) > _MAX_STATIC_TEXT_LENGTH:
            return None
        return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _fold_static_text(node.left, depth + 1)
        right = _fold_static_text(node.right, depth + 1)
        if left is None or right is None:
            return None
        return _join_static_path((left, right))
    if isinstance(node, ast.Call) and not node.keywords:
        function_name = _dotted_name(node.func)
        if function_name not in _PATH_CONSTRUCTORS | _PATH_JOIN_CALLS:
            return None
        parts = tuple(_fold_static_text(argument, depth + 1) for argument in node.args)
        if any(part is None for part in parts):
            return None
        return _join_static_path(tuple(part for part in parts if part is not None))
    return None


def _fold_static_int(
    node: ast.AST,
    environment: dict[str, int] | None = None,
    depth: int = 0,
) -> int | None:
    """Fold signed integer literals and unambiguous simple-name bindings."""

    if depth > _MAX_STATIC_AST_DEPTH:
        return None
    if isinstance(node, ast.Name) and environment is not None:
        return environment.get(node.id)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            return node.value
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _fold_static_int(node.operand, environment, depth + 1)
        if value is None:
            return None
        return -value if isinstance(node.op, ast.USub) else value
    return None


def _fold_semantic_text(
    node: ast.AST,
    environment: dict[str, str],
    depth: int = 0,
) -> str | None:
    """Fold finite string literals/concatenation and simple-name bindings."""

    if depth > _MAX_STATIC_AST_DEPTH:
        return None
    if isinstance(node, ast.Name):
        return environment.get(node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (
            node.value
            if len(node.value) <= _MAX_STATIC_TEXT_LENGTH
            else None
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_semantic_text(node.left, environment, depth + 1)
        right = _fold_semantic_text(node.right, environment, depth + 1)
        if left is None or right is None or len(left) + len(right) > _MAX_STATIC_TEXT_LENGTH:
            return None
        return left + right
    return None


def _contains_rank_reference(node: ast.AST) -> bool:
    candidates = (
        child.id if isinstance(child, ast.Name) else child.attr
        for child in ast.walk(node)
        if isinstance(child, (ast.Name, ast.Attribute))
    )
    return any(
        candidate.casefold() == "rank"
        or candidate.casefold().endswith("_rank")
        for candidate in candidates
    )


def _recommendation_boundary_findings(
    source: str,
    relative: str = "scripts/generate_report.py",
) -> tuple[str, ...]:
    """Inspect a finite AST subset for removed recommendation surfaces."""

    tree = ast.parse(source, filename=relative)
    findings: list[str] = []
    module_aliases: dict[str, str] = {}
    call_aliases: dict[str, str] = {}
    static_ints: dict[str, int] = {}
    static_strings: dict[str, str] = {}
    ambiguous_ints: set[str] = set()
    ambiguous_strings: set[str] = set()
    ambiguous_modules: set[str] = set()
    ambiguous_calls: set[str] = set()

    def bind(
        environment: dict[str, object],
        ambiguous: set[str],
        name: str,
        value: object | None,
    ) -> bool:
        if value is None or name in ambiguous:
            return False
        if name not in environment:
            environment[name] = value
            return True
        if environment[name] != value:
            environment.pop(name, None)
            ambiguous.add(name)
            return True
        return False

    def simple_targets(node: ast.Assign | ast.AnnAssign) -> tuple[str, ...]:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return tuple(target.id for target in targets if isinstance(target, ast.Name))

    def resolve_module(node: ast.AST) -> str | None:
        dotted = _dotted_name(node)
        if dotted is not None and dotted.casefold().endswith("data_loader"):
            return "data_loader"
        if isinstance(node, ast.Name):
            return module_aliases.get(node.id)
        return None

    def resolve_callable(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            if node.id in _FORBIDDEN_RECOMMENDATION_SYMBOLS:
                return node.id
            return call_aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            if node.attr in _FORBIDDEN_RECOMMENDATION_SYMBOLS:
                return node.attr
            if (
                node.attr in _FORBIDDEN_REPORT_DATA_CALLS
                and resolve_module(node.value) == "data_loader"
            ):
                return node.attr
        if (
            isinstance(node, ast.Call)
            and _dotted_name(node.func) == "getattr"
            and len(node.args) >= 2
            and resolve_module(node.args[0]) == "data_loader"
        ):
            accessed = _fold_semantic_text(node.args[1], static_strings)
            if accessed in _FORBIDDEN_REPORT_DATA_CALLS:
                return accessed
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                if alias.name.casefold().endswith("data_loader") and alias.asname:
                    module_aliases[local] = "data_loader"
                if local in _FORBIDDEN_RECOMMENDATION_SYMBOLS:
                    findings.append(f"forbidden-import-alias:{local}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                if module.casefold() in {"scripts", ""} and (
                    alias.name.casefold() == "data_loader"
                ):
                    module_aliases[local] = "data_loader"
                elif module.casefold().endswith("data_loader"):
                    if alias.name in _FORBIDDEN_REPORT_DATA_CALLS:
                        call_aliases[local] = alias.name
                if alias.name in _FORBIDDEN_RECOMMENDATION_SYMBOLS:
                    findings.append(f"forbidden-import:{alias.name}")
                    call_aliases[local] = alias.name
                if local in _FORBIDDEN_RECOMMENDATION_SYMBOLS:
                    findings.append(f"forbidden-import-alias:{local}")

    assignments = tuple(
        node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))
    )
    for _iteration in range(_MAX_STATIC_AST_DEPTH):
        changed = False
        for node in assignments:
            for target in simple_targets(node):
                changed |= bind(
                    static_ints,
                    ambiguous_ints,
                    target,
                    _fold_static_int(node.value, static_ints),
                )
                changed |= bind(
                    static_strings,
                    ambiguous_strings,
                    target,
                    _fold_semantic_text(node.value, static_strings),
                )
                changed |= bind(
                    module_aliases,
                    ambiguous_modules,
                    target,
                    resolve_module(node.value),
                )
                changed |= bind(
                    call_aliases,
                    ambiguous_calls,
                    target,
                    resolve_callable(node.value),
                )
        if not changed:
            break

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in _FORBIDDEN_RECOMMENDATION_SYMBOLS:
                findings.append(f"forbidden-symbol:{node.name}")

        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for child in ast.walk(target):
                    if (
                        isinstance(child, ast.Name)
                        and child.id in _FORBIDDEN_RECOMMENDATION_SYMBOLS
                    ):
                        findings.append(f"forbidden-symbol:{child.id}")
                if isinstance(target, ast.Name) and target.id == "__all__":
                    value = node.value
                    elements = (
                        value.elts
                        if isinstance(value, (ast.List, ast.Tuple, ast.Set))
                        else (value,)
                    )
                    for element in elements:
                        exported = _fold_semantic_text(element, static_strings)
                        if exported in _FORBIDDEN_RECOMMENDATION_SYMBOLS:
                            findings.append(f"forbidden-export:{exported}")
                resolved = resolve_callable(node.value)
                if resolved in (
                    _FORBIDDEN_RECOMMENDATION_SYMBOLS
                    | _FORBIDDEN_REPORT_DATA_CALLS
                ):
                    for target in simple_targets(node):
                        findings.append(f"forbidden-alias:{target}->{resolved}")

        if isinstance(node, ast.BinOp) and relative in _RECOMMENDATION_RUNTIME_PATHS:
            left_rank = _contains_rank_reference(node.left)
            right_rank = _contains_rank_reference(node.right)
            left_value = _fold_static_int(node.left, static_ints)
            right_value = _fold_static_int(node.right, static_ints)
            fixed_adjustment = isinstance(node.op, (ast.Add, ast.Sub)) and (
                (left_rank and right_value is not None and abs(right_value) == 4000)
                or (right_rank and left_value is not None and abs(left_value) == 4000)
            )
            if fixed_adjustment:
                findings.append("fixed-rank-adjustment:4000")

        if isinstance(node, (ast.Constant, ast.BinOp, ast.Name)):
            runtime_string = _fold_semantic_text(node, static_strings)
            if runtime_string in {"legacy-local-dataset", "equiv_rank_adjust"}:
                findings.append(f"forbidden-runtime-string:{runtime_string}")

        if isinstance(node, ast.Subscript):
            owner = node.value
            if (
                isinstance(owner, ast.Call)
                and _dotted_name(owner.func) in {"globals", "locals", "vars"}
                and not owner.args
                and not owner.keywords
            ):
                accessed = _fold_semantic_text(node.slice, static_strings)
                if accessed in _FORBIDDEN_RECOMMENDATION_SYMBOLS:
                    findings.append(f"dynamic-access:{accessed}")

        if isinstance(node, ast.Call):
            function_name = _dotted_name(node.func)
            canonical = resolve_callable(node.func)
            if canonical in _FORBIDDEN_REPORT_DATA_CALLS:
                findings.append(f"old-data-call:{canonical}")
            if canonical in _FORBIDDEN_RECOMMENDATION_SYMBOLS:
                findings.append(f"forbidden-call:{canonical}")
            if function_name in {"getattr", "hasattr", "setattr"} and len(node.args) >= 2:
                accessed = _fold_semantic_text(node.args[1], static_strings)
                if accessed in _FORBIDDEN_RECOMMENDATION_SYMBOLS:
                    findings.append(f"dynamic-access:{accessed}")

    return tuple(dict.fromkeys(findings))


def _is_forbidden_path_reference(value: str) -> bool:
    normalized = value.replace("\\", "/").casefold().strip()
    haystack = "/" + normalized.strip("/")
    for fragment in FORBIDDEN_PATH_FRAGMENTS:
        needle = "/" + fragment
        # The renderer may create a fresh ignored output directory.  What the
        # public boundary forbids is a bundled/read-back artifact beneath it.
        if fragment == "output" and haystack == needle:
            continue
        if haystack == needle or haystack.endswith(needle):
            return True
        if needle + "/" in haystack:
            return True
    return False


def _runtime_references(path: Path) -> tuple[str, ...]:
    """Return executable references to private siblings or the old template.

    Comments and statement docstrings are deliberately outside this check: they
    may preserve attribution without creating a runtime dependency.
    """

    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    references: list[str] = []

    class ReferenceVisitor(ast.NodeVisitor):
        def visit_Expr(self, node: ast.Expr) -> None:
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                return
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                self._record_import(alias.name)
                self._record(alias.name)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module = node.module or ""
            self._record_import(module)
            self._record(module)
            if module.casefold() in {"", "scripts"}:
                for alias in node.names:
                    self._record_import(alias.name)

        def visit_Constant(self, node: ast.Constant) -> None:
            if isinstance(node.value, str):
                self._record(node.value)

        def visit_BinOp(self, node: ast.BinOp) -> None:
            folded = _fold_static_text(node)
            if folded is not None:
                self._record(folded)
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            function_name = _dotted_name(node.func)
            if function_name in _DYNAMIC_IMPORT_CALLS and node.args:
                module = _fold_static_text(node.args[0])
                if module is not None:
                    self._record_import(module)
                    self._record(module)
            folded = _fold_static_text(node)
            if folded is not None:
                self._record(folded)
            self.generic_visit(node)

        def _record(self, value: str) -> None:
            normalized = value.replace("\\", "/").casefold()
            if "shengxue-system" in normalized:
                references.append(value)
            if _is_forbidden_path_reference(value):
                references.append(value)
            if normalized in FORBIDDEN_MODULE_BASENAMES:
                references.append(value)
            if normalized.startswith("scripts.") and (
                normalized.rsplit(".", 1)[-1] in FORBIDDEN_MODULE_BASENAMES
            ):
                references.append(value)

        def _record_import(self, module: str) -> None:
            basename = module.casefold().rsplit(".", 1)[-1]
            if basename in FORBIDDEN_MODULE_BASENAMES:
                references.append(module)

    ReferenceVisitor().visit(tree)
    return tuple(dict.fromkeys(references))


def _strict_json_file(path: Path):
    def object_without_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_nonfinite(value):
        raise ValueError(f"non-finite JSON value: {value}")

    return json.loads(
        path.read_text("utf-8"),
        object_pairs_hook=object_without_duplicates,
        parse_constant=reject_nonfinite,
    )


_FIXTURE_SENSITIVE_TEXT = re.compile(
    r"https?://|www\."
    r"|(?<![0-9])1[3-9][0-9]{9}(?![0-9])"
    r"|(?<![0-9])[0-9]{17}[0-9Xx](?![0-9])"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)


def _fixture_policy_issues(fixtures: Path) -> tuple[str, ...]:
    issues = []
    policy_path = fixtures / "fixture-policy.json"
    try:
        policy = _strict_json_file(policy_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return ("fixture policy 缺失或不是严格 UTF-8 JSON",)

    if not isinstance(policy, dict) or set(policy) != {"schema_version", "fixtures"}:
        return ("fixture policy 顶层 schema 不严格",)
    if policy["schema_version"] != "1.0" or not isinstance(
        policy["fixtures"], dict
    ):
        return ("fixture policy 版本或 fixtures 字段无效",)

    children = [
        child
        for child in fixtures.iterdir()
        if child.is_dir() or child.is_symlink()
    ]
    discovered = {child.name for child in children}
    declared = set(policy["fixtures"])
    if discovered != declared:
        issues.append(
            "fixture 目录全集与 policy keys 不一致："
            f"missing={sorted(discovered - declared)!r}, "
            f"extra={sorted(declared - discovered)!r}"
        )

    for name in sorted(discovered & declared):
        directory = fixtures / name
        declaration = policy["fixtures"][name]
        if directory.is_symlink():
            issues.append(f"{name}: fixture 目录不得为符号链接")
            continue
        if declaration != {"classification": "synthetic"}:
            issues.append(f"{name}: v0.1 fixture 必须严格声明 synthetic")
            continue
        try:
            metadata = _strict_json_file(directory / "province.json")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            issues.append(f"{name}: province.json 无效")
            continue
        province = metadata.get("province") if isinstance(metadata, dict) else None
        if not isinstance(province, str) or not (
            province.startswith("演示") or province.startswith("虚构")
        ):
            issues.append(f"{name}: province 必须明确标记为演示或虚构")

        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.casefold() not in {".csv", ".json"}:
                continue
            try:
                text = path.read_text("utf-8")
            except (OSError, UnicodeError):
                issues.append(f"{name}/{path.name}: 不是安全 UTF-8 文本")
                continue
            if _FIXTURE_SENSITIVE_TEXT.search(text):
                issues.append(f"{name}/{path.name}: 含 PII、邮箱或 URL")

        admission_path = directory / "tou_dang.csv"
        try:
            with admission_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, UnicodeError, csv.Error):
            issues.append(f"{name}: tou_dang.csv 无效")
            continue
        if not rows:
            issues.append(f"{name}: tou_dang.csv 不得为空")
            continue
        if any(not (row.get("school_code") or "").startswith("SYN") for row in rows):
            issues.append(f"{name}: synthetic school_code 必须以 SYN 开头")
        if any(not (row.get("school_name") or "").startswith("虚构") for row in rows):
            issues.append(f"{name}: synthetic school_name 必须以虚构开头")

    return tuple(sorted(issues))


def _public_python_files():
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if path == Path(__file__).resolve():
            continue
        if relative.parts[0] in {".git", ".scratch", ".superpowers", ".worktrees"}:
            continue
        if relative.parts[0] == "tests":
            continue
        yield path


class PublicPackageBoundaryTest(unittest.TestCase):
    def test_readme_clis_run_from_the_git_index_snapshot(self):
        documents_available = importlib.util.find_spec("docx") is not None
        readme = (ROOT / "README.md").read_text("utf-8")
        commands = [
            shlex.split(line.strip())
            for line in readme.splitlines()
            if line.strip().startswith("python scripts/")
        ]
        self.assertEqual(len(commands), 5)

        tree = subprocess.run(
            ["git", "write-tree"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            archive = temporary_path / "public-package.zip"
            snapshot = temporary_path / "snapshot"
            subprocess.run(
                ["git", "archive", "--format=zip", f"--output={archive}", tree],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
            shutil.unpack_archive(archive, snapshot)

            polluted_env = os.environ.copy()
            polluted_env.update(
                {
                    "PYTHONPATH": str(ROOT / "scripts"),
                    "PYTHONHOME": str(ROOT / "malicious-python-home"),
                    "PYTHONSTARTUP": str(ROOT / "malicious-startup.py"),
                }
            )
            isolated_env = _isolated_python_environment(polluted_env)
            self.assertFalse(
                any(name.upper().startswith("PYTHON") for name in isolated_env)
            )

            def run_in_snapshot(snapshot_root: Path, command: list[str]):
                script = (snapshot_root / command[1]).resolve()
                self.assertTrue(script.is_relative_to(snapshot_root.resolve()))
                runner = (
                    "import os,runpy,sys;"
                    "assert sys.flags.isolated;"
                    "assert not any(k.upper().startswith('PYTHON') for k in os.environ);"
                    "root=sys.argv.pop(1);script=sys.argv.pop(1);"
                    "sys.path[:0]=[root,root+'/scripts'];"
                    "runpy.run_path(script,run_name='__main__')"
                )
                return subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        runner,
                        str(snapshot_root.resolve()),
                        str(script),
                        *command[2:],
                    ],
                    cwd=snapshot_root,
                    env=isolated_env,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

            executed = 0
            for command in commands:
                if command[1] == "scripts/docx_export.py" and not documents_available:
                    continue
                with self.subTest(command=command):
                    executed += 1
                    completed = run_in_snapshot(snapshot, command)
                    self.assertEqual(
                        completed.returncode,
                        0,
                        f"stdout={completed.stdout}\nstderr={completed.stderr}",
                    )
            self.assertEqual(executed, 5 if documents_available else 4)

            missing_module_snapshot = temporary_path / "missing-module-snapshot"
            shutil.unpack_archive(archive, missing_module_snapshot)
            (missing_module_snapshot / "scripts/compliance_scan.py").unlink()
            dependent_commands = [
                command
                for command in commands
                if command[1]
                in {"scripts/generate_report.py", "scripts/docx_export.py"}
                and (documents_available or command[1] != "scripts/docx_export.py")
            ]
            for command in dependent_commands:
                with self.subTest(missing_module=command[1]):
                    isolated = run_in_snapshot(missing_module_snapshot, command)
                    self.assertNotEqual(
                        isolated.returncode,
                        0,
                        "missing-module snapshot imported compliance_scan externally",
                    )
                    self.assertIn("compliance_scan", isolated.stderr)

    def test_compliance_scan_has_minimal_price_and_privacy_boundaries(self):
        from scripts.compliance_scan import contains_price_text, find_price_text

        self.assertTrue(contains_price_text("方案优惠价 3999"))
        self.assertEqual(find_price_text("方案优惠价 3999"), "优惠价 3999")
        self.assertFalse(contains_price_text("香港学费 15万港币；院校层次 985"))
        private_tail = r"，学生标识 PRIVATE-STUDENT；C:\Users\student\report.md"
        self.assertEqual(
            find_price_text("方案优惠价 3999" + private_tail),
            "优惠价 3999",
        )

    def test_compliance_scan_supports_package_and_flat_imports(self):
        package_module = importlib.import_module("scripts.compliance_scan")
        self.assertFalse(package_module.contains_price_text("省排名 3000 位"))

        flat_import = subprocess.run(
            [
                sys.executable,
                "-c",
                "import compliance_scan; "
                "assert compliance_scan.contains_price_text('仅需 99')",
            ],
            cwd=ROOT / "scripts",
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            flat_import.returncode,
            0,
            f"stdout={flat_import.stdout}\nstderr={flat_import.stderr}",
        )

    def test_compliance_scan_cli_does_not_echo_private_paths(self):
        from scripts import compliance_scan

        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "private-student-report.md"
            report.write_text("省排名 3000 位", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                success_code = compliance_scan.main([str(report)])
                missing_code = compliance_scan.main([str(report.with_name("missing.md"))])

        self.assertEqual(success_code, 0)
        self.assertEqual(missing_code, 2)
        self.assertNotIn(str(report.parent), stdout.getvalue())
        self.assertNotIn(str(report.parent), stderr.getvalue())

    def test_compliance_scan_cli_normalizes_all_input_read_failures(self):
        from scripts import compliance_scan

        def invoke(argument: str, patched_open=None):
            stdout = io.StringIO()
            stderr = io.StringIO()
            patcher = (
                mock.patch("builtins.open", patched_open)
                if patched_open is not None
                else contextlib.nullcontext()
            )
            with patcher, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                try:
                    code = compliance_scan.main([argument])
                except Exception as error:  # The assertion below makes this RED.
                    code = error
            return code, stdout.getvalue(), stderr.getvalue()

        with tempfile.TemporaryDirectory() as temporary:
            private_root = Path(temporary)
            existing = private_root / "private-report.md"
            existing.write_text("省排名 3000 位", encoding="utf-8")
            cases = [
                (str(private_root), None),
                (str(private_root / "missing-private.md"), None),
                (str(existing), mock.Mock(side_effect=PermissionError("PRIVATE"))),
                (str(existing), mock.Mock(side_effect=FileNotFoundError("PRIVATE"))),
            ]
            for argument, patched_open in cases:
                with self.subTest(argument=Path(argument).name):
                    code, stdout, stderr = invoke(argument, patched_open)
                    self.assertEqual(code, 2)
                    self.assertEqual(stdout, "")
                    self.assertEqual(stderr, "错误：无法读取输入文件\n")
                    self.assertNotIn(str(private_root), stderr)
                    self.assertNotIn("PRIVATE", stderr)
                    self.assertNotIn("Traceback", stderr)

            for error in (
                OSError("PRIVATE read race"),
                UnicodeDecodeError("utf-8", b"\xff", 0, 1, "PRIVATE"),
            ):
                reader = mock.MagicMock()
                reader.__enter__.return_value = reader
                reader.read.side_effect = error
                with self.subTest(error=type(error).__name__):
                    code, stdout, stderr = invoke(
                        str(existing), mock.Mock(return_value=reader)
                    )
                    self.assertEqual(code, 2)
                    self.assertEqual(stdout, "")
                    self.assertEqual(stderr, "错误：无法读取输入文件\n")
                    self.assertNotIn(str(private_root), stderr)
                    self.assertNotIn("PRIVATE", stderr)
                    self.assertNotIn("Traceback", stderr)

    def test_legacy_school_keyword_adapter_is_not_reachable(self):
        from scripts.school_recommend import recommend_schools

        row = {
            "year": 2025,
            "province": "演示甲省",
            "school_name": "虚构甲大学",
            "school_code": "SYN-A01",
            "subject_group": "物理",
            "major_group_name": "虚构专业组",
            "min_score": 645,
            "min_rank": 1100,
        }
        with self.assertRaises(TypeError):
            recommend_schools(
                [row],
                year=2025,
                estimated_prov_rank=1100,
            )

    def test_fixed_default_pathway_adapter_is_not_reachable(self):
        from scripts import path_recommend

        legacy = getattr(path_recommend, "recommend_paths", None)
        if legacy is not None:
            result = legacy([], [], None, estimated_prov_rank=5000)
            self.fail(
                "legacy pathway adapter remains reachable with equivalent rank "
                f"{result['meta']['equivalent_rank']}"
            )

    def test_old_report_arguments_are_rejected_by_evidence_parser(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/generate_report.py"),
                "--province",
                "演示甲省",
                "--subject-group",
                "物理",
                "--grade",
                "高三",
                "--rank",
                "4000",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("--dataset", completed.stderr)
        self.assertIn("--profile", completed.stderr)
        self.assertIn("--evidence", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_rank_module_has_no_public_default_xibao_estimator(self):
        rank_module = importlib.import_module("scripts.rank_calc")
        self.assertFalse(hasattr(rank_module, "estimate_rank"))

        rank_tree = ast.parse((ROOT / "scripts/rank_calc.py").read_text("utf-8"))
        imported_names = {
            alias.name
            for node in ast.walk(rank_tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        function_names = {
            node.name for node in ast.walk(rank_tree) if isinstance(node, ast.FunctionDef)
        }
        self.assertNotIn("load_xibao", imported_names)
        self.assertNotIn("estimate_rank", function_names)

    def test_public_runtime_has_only_evidence_aware_recommendation_surfaces(self):
        from scripts.contracts import (
            EvidenceStatus,
            OrdinaryBatchPolicy,
            RecommendationProfile,
            RecommendationResult,
        )

        school_module = importlib.import_module("scripts.school_recommend")
        path_module = importlib.import_module("scripts.path_recommend")
        school_tree = ast.parse((ROOT / "scripts/school_recommend.py").read_text("utf-8"))
        path_tree = ast.parse((ROOT / "scripts/path_recommend.py").read_text("utf-8"))
        report_tree = ast.parse((ROOT / "scripts/generate_report.py").read_text("utf-8"))

        signature = inspect.signature(school_module.recommend_schools)
        self.assertEqual(
            list(signature.parameters),
            ["rows", "profile", "policy", "rank_scenario"],
        )
        for parameter in tuple(signature.parameters.values())[:3]:
            self.assertIs(parameter.kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            self.assertIs(parameter.default, inspect.Parameter.empty)
        scenario_parameter = signature.parameters["rank_scenario"]
        self.assertIs(scenario_parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(scenario_parameter.default)
        self.assertFalse(hasattr(school_module, "params_from_config"))
        self.assertFalse(hasattr(school_module, "_tier_threshold_labels"))

        result = school_module.recommend_schools(
            [{
                "year": 2025,
                "province": "演示甲省",
                "school_name": "虚构甲大学",
                "school_province": "演示甲省",
                "subject_group": "物理",
                "major_group_name": "虚构专业组",
                "min_score": 645,
                "min_rank": 1100,
                "evidence_status": EvidenceStatus.OFFICIAL.value,
                "source_ids": ("official-example",),
                "coverage_min_rank": 1,
                "coverage_max_rank": 5000,
            }],
            RecommendationProfile(
                rank=1100,
                target_province="演示甲省",
                subject_group="物理",
            ),
            OrdinaryBatchPolicy(
                schema_version="1.0",
                policy_id="synthetic-ordinary-batch-v1",
                basis_id="synthetic-policy-basis-v1",
                search_delta_min=-8000,
                search_delta_max=6000,
                challenge_delta_lt=-2000,
                stable_delta_le=2000,
                tier_caps={"冲": 3, "稳": 4, "保": 5},
            ),
        )
        self.assertIsInstance(result, RecommendationResult)
        with self.assertRaises(AttributeError):
            result.items = ()

        path_functions = {
            node.name for node in ast.walk(path_tree) if isinstance(node, ast.FunctionDef)
        }
        self.assertNotIn("equiv_adjust_from_config", path_functions)
        self.assertNotIn("recommend_paths", path_functions)
        self.assertFalse(hasattr(path_module, "EQUIV_RANK_ADJUST"))
        self.assertFalse(hasattr(path_module, "recommend_paths"))

        tracked_scripts = subprocess.run(
            ["git", "ls-files", "--", "scripts/*.py"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.splitlines()
        recommendation_findings = {
            relative: findings
            for relative in tracked_scripts
            if (
                findings := _recommendation_boundary_findings(
                    (ROOT / relative).read_text("utf-8"), relative
                )
            )
        }
        self.assertEqual(recommendation_findings, {})

        runtime_trees = {
            relative: ast.parse((ROOT / relative).read_text("utf-8"))
            for relative in tracked_scripts
        }
        forbidden_strings = {"legacy-local-dataset", "equiv_rank_adjust"}
        forbidden_symbols = {
            "equiv_adjust_from_config",
            "EQUIV_RANK_ADJUST",
            "recommend_paths",
        }
        for relative, tree in runtime_trees.items():
            strings = {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            symbols = {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))
            }
            symbols.update(
                target.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Assign)
                for target in node.targets
                if isinstance(target, ast.Name)
            )
            self.assertTrue(
                forbidden_strings.isdisjoint(strings),
                f"{relative}: {forbidden_strings & strings}",
            )
            self.assertTrue(
                forbidden_symbols.isdisjoint(symbols),
                f"{relative}: {forbidden_symbols & symbols}",
            )

        report_imports = {
            alias.name
            for node in ast.walk(report_tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue({
            "load_path_table",
            "load_province_config",
            "load_toudang",
            "load_yifenyiduan",
            "score_to_rank",
            "equiv_adjust_from_config",
            "recommend_paths",
            "params_from_config",
        }.isdisjoint(report_imports), report_imports)

        evaluate = next(
            node for node in path_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "evaluate_pathways"
        )
        self.assertEqual(evaluate.args.args[-1].arg, "model")
        self.assertIsInstance(evaluate.args.defaults[-1], ast.Constant)
        self.assertIsNone(evaluate.args.defaults[-1].value)

    def test_recommendation_boundary_mutation_canaries_are_semantic_and_finite(self):
        bad_sources = {
            "fixed subtract": "def f(rank):\n    return rank - 4000\n",
            "fixed negative add": "def f(rank):\n    return rank + (-4000)\n",
            "local positive offset": (
                "OFFSET = 4000\ndef f(rank):\n    return rank - OFFSET\n"
            ),
            "local negative delta": (
                "DELTA = -4000\ndef f(rank):\n    return rank + DELTA\n"
            ),
            "direct positive add": "def f(rank):\n    return rank + 4000\n",
            "direct negative subtract": "def f(rank):\n    return rank - (-4000)\n",
            "dynamic export": "__all__ = ['recommend_' + 'paths']\n",
            "aliased dynamic export": (
                "legacy_name = 'recommend_' + 'paths'\n__all__ = [legacy_name]\n"
            ),
            "dynamic globals access": (
                "legacy = globals()['recommend_' + 'paths']\n"
            ),
            "direct flat data call": (
                "from data_loader import load_toudang\nload_toudang()\n"
            ),
            "aliased flat data call": (
                "import data_loader as dl\ndl.load_yifenyiduan()\n"
            ),
            "module-aliased package data call": (
                "from scripts import data_loader as dl\ndl.load_toudang()\n"
            ),
            "module and callable alias chain": (
                "import scripts.data_loader as data_loader\n"
                "dl = data_loader\n"
                "lookup = dl.load_toudang\n"
                "lookup()\n"
            ),
            "direct relative data call": (
                "from .data_loader import score_to_rank as lookup\nlookup()\n"
            ),
            "folded getattr data call": (
                "import data_loader\n"
                "dl = data_loader\n"
                "lookup = getattr(dl, 'load_' + 'toudang')\n"
                "lookup()\n"
            ),
            "forbidden import local alias": (
                "from path_recommend import evaluate_pathways as recommend_paths\n"
            ),
            "forbidden module import local alias": (
                "import path_recommend as recommend_paths\n"
            ),
            "static forbidden alias": "legacy = recommend_paths\n",
            "static forbidden attribute alias": (
                "legacy = path_recommend.recommend_paths\n"
            ),
            "folded setattr": (
                "setattr(module, 'recommend_' + 'paths', handler)\n"
            ),
            "folded legacy source string": (
                "source_id = 'legacy-local-' + 'dataset'\n"
            ),
            "folded adjustment key string": (
                "config_key = 'equiv_rank_' + 'adjust'\n"
            ),
            "old function": "def recommend_paths():\n    pass\n",
            "old config function": "def params_from_config():\n    pass\n",
            "old constant": "EQUIV_RANK_ADJUST = 1\n",
        }
        for name, source in bad_sources.items():
            with self.subTest(name=name):
                self.assertTrue(
                    _recommendation_boundary_findings(source),
                    f"mutation escaped: {name}",
                )

        safe_source = """
page_limit = 4000
page_rank_limit = 9000
remaining = page_rank_limit - 4000
model = RankAdjustmentModel(rank_delta=-4000)
def evaluate(rank, model):
    return rank + model.rank_delta
from data_loader import load_admission_rows
labels = {'recommend_paths': 'historical prose only'}
notes = 'historical recommendation adapter prose'
"""
        self.assertEqual(_recommendation_boundary_findings(safe_source), ())

    def test_ast_boundary_folds_path_division(self):
        cases = {
            'legacy = Path("scripts") / "verify_province.py"\n': (
                "scripts/verify_province.py"
            ),
            'legacy = PurePath("data") / "hubei" / "province.json"\n': (
                "data/hubei/province.json"
            ),
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertIn(expected, self._probe_runtime_references(source))

    def test_ast_boundary_folds_standard_path_join(self):
        for module in ("os.path", "posixpath", "ntpath"):
            source = f'legacy = {module}.join("data", "hubei", "province.json")\n'
            with self.subTest(module=module):
                self.assertIn(
                    "data/hubei/province.json",
                    self._probe_runtime_references(source),
                )

    def test_ast_boundary_folds_split_dynamic_import(self):
        calls = (
            'importlib.import_module("scripts." + "estimate_rank")',
            '__import__("scripts." + "estimate_rank")',
        )
        for call in calls:
            with self.subTest(call=call):
                self.assertIn(
                    "scripts.estimate_rank",
                    self._probe_runtime_references(f"module = {call}\n"),
                )

    def test_ast_boundary_static_evaluator_keeps_safe_controls(self):
        source = '''"""Attribution: shengxue-system."""
safe_report = Path("reports") / "synthetic.docx"
safe_module = importlib.import_module("scripts.rank_calc")
'''
        self.assertEqual(self._probe_runtime_references(source), ())

    def test_fixture_policy_covers_and_validates_every_fixture_directory(self):
        fixtures = ROOT / "tests/fixtures/provinces"
        self.assertEqual(_fixture_policy_issues(fixtures), ())

    def test_fixture_policy_detects_an_unlisted_rogue_directory(self):
        fixtures = ROOT / "tests/fixtures/provinces"
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "provinces"
            shutil.copytree(fixtures, copied)
            (copied / "rogue-real-snapshot").mkdir()
            issues = _fixture_policy_issues(copied)

        self.assertTrue(
            any("目录全集" in issue for issue in issues),
            issues,
        )

    def test_readme_describes_host_retrieval_and_only_tracked_public_clis(self):
        readme = (ROOT / "README.md").read_text("utf-8")
        self.assertNotIn("零网络", readme)
        self.assertNotIn("零 LLM", readme)
        self.assertIn("Agent 宿主", readme)
        self.assertIn("本地确定性", readme)
        self.assertNotRegex(readme, r"#\s*\d+\s*个用例")

        command_scripts = set(re.findall(r"python (scripts/[A-Za-z0-9_.-]+)", readme))
        required = {
            "scripts/preflight.py",
            "scripts/validate_data.py",
            "scripts/validate_evidence.py",
            "scripts/generate_report.py",
            "scripts/docx_export.py",
        }
        self.assertEqual(command_scripts, required)
        for script in sorted(command_scripts):
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", "--", script],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(tracked.returncode, 0, f"untracked README CLI: {script}")

    @staticmethod
    def _probe_runtime_references(source: str) -> tuple[str, ...]:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "probe.py"
            path.write_text(source, encoding="utf-8")
            return _runtime_references(path)

    def test_ast_boundary_detects_package_imports_and_dynamic_legacy_paths(self):
        source = '''
import scripts.verify_province
from scripts import fetch_via_qr

rank_cli = "scripts/estimate_rank.py"
private_export = "scripts/export_from_system.py"
parity = "scripts/parity_check.py"
real_data = "data/hubei/province.json"
old_demo = "data/demo-xx/province.json"
generated = "output/report.md"
'''
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "probe.py"
            path.write_text(source, encoding="utf-8")
            findings = _runtime_references(path)

        for expected in (
            "scripts.verify_province",
            "fetch_via_qr",
            "scripts/estimate_rank.py",
            "scripts/export_from_system.py",
            "scripts/parity_check.py",
            "data/hubei/province.json",
            "data/demo-xx/province.json",
            "output/report.md",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, findings)

    def test_ast_boundary_allows_historical_attribution_docstrings(self):
        source = '''"""Migrated from shengxue-system for attribution only."""\n'''
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "probe.py"
            path.write_text(source, encoding="utf-8")
            findings = _runtime_references(path)

        self.assertEqual(findings, ())

    def test_private_and_unreviewed_runtime_artifacts_are_absent(self):
        present = [
            relative
            for relative in PRIVATE_OR_UNREVIEWED_PATHS
            if os.path.lexists(ROOT / relative)
        ]
        self.assertEqual(present, [], f"public tree still contains: {present}")

    def test_public_python_has_no_executable_private_or_template_lookup(self):
        findings = {
            path.relative_to(ROOT).as_posix(): references
            for path in _public_python_files()
            if (references := _runtime_references(path))
        }
        self.assertEqual(findings, {})

    def test_both_province_modes_remain_available_as_synthetic_fixtures(self):
        fixture_roots = (
            ROOT / "tests/fixtures/provinces/demo-312",
            ROOT / "tests/fixtures/provinces/demo-33",
        )
        modes = []
        for fixture_root in fixture_roots:
            metadata_path = fixture_root / "province.json"
            self.assertTrue(metadata_path.is_file(), str(metadata_path))
            metadata = json.loads(metadata_path.read_text("utf-8"))
            self.assertTrue(metadata["province"].startswith("演示"), metadata)
            self.assertTrue((fixture_root / "yifenyiduan.csv").is_file())
            self.assertTrue((fixture_root / "tou_dang.csv").is_file())
            with (fixture_root / "tou_dang.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(rows)
            self.assertTrue(
                all(row["school_code"].startswith("SYN") for row in rows), rows
            )
            self.assertTrue(
                all(row["school_name"].startswith("虚构") for row in rows), rows
            )
            modes.append(metadata["mode"])

        self.assertEqual(modes, ["3+1+2", "3+3"])


if __name__ == "__main__":
    unittest.main()
