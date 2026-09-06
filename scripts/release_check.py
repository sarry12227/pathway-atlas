# -*- coding: utf-8 -*-
"""Privacy-safe, deterministic release gate for the public repository."""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence
from urllib.parse import unquote, urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the Python 3.10 gate
    import tomli as tomllib

try:
    from .compliance_scan import (
        GitTrackedEntry,
        PolicyError,
        ReleasePolicy,
        git_environment,
        git_blob_bytes,
        git_paths,
        git_replacement_refs,
        git_top_level,
        git_tracked_entries,
        git_tree_entries,
        git_write_tree,
        load_policy,
        missing_required_release_paths,
        parse_policy_bytes,
        safe_tracked_file,
        scan_git_snapshot,
        scan_text,
        scan_tracked,
        validate_binary_release_manifest,
    )
except ImportError:  # pragma: no cover - direct script execution
    from compliance_scan import (
        GitTrackedEntry,
        PolicyError,
        ReleasePolicy,
        git_environment,
        git_blob_bytes,
        git_paths,
        git_replacement_refs,
        git_top_level,
        git_tracked_entries,
        git_tree_entries,
        git_write_tree,
        load_policy,
        missing_required_release_paths,
        parse_policy_bytes,
        safe_tracked_file,
        scan_git_snapshot,
        scan_text,
        scan_tracked,
        validate_binary_release_manifest,
    )


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
_SENSITIVE_SUFFIXES = frozenset({".env", ".key", ".pem", ".p12", ".pfx", ".crt", ".sqlite", ".sqlite3", ".docx"})
_SENSITIVE_BASENAMES = frozenset(
    {
        ".env",
        "credentials",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        "secrets.json",
    }
)
_SENSITIVE_PREFIXES = ("private/", "reports/", "output/", "data/", "work/", "evidence/raw-downloads/")
_BENIGN_IGNORED_COMPONENTS = frozenset({".superpowers", ".venv", "__pycache__", "node_modules"})
_EXACT_CACHE_BINARY_SUFFIXES = frozenset(
    {".class", ".dll", ".dylib", ".exe", ".jar", ".node", ".pyc", ".pyd", ".pyo", ".so"}
)
_MAX_UNTRACKED_SCAN_FILES = 512
_MAX_UNTRACKED_SCAN_BYTES = 16 * 1024 * 1024


def _is_regular_nonreparse(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISREG(metadata.st_mode) and not bool(attributes & reparse_flag)


def _same_file_identity(first: os.stat_result, *others: os.stat_result) -> bool:
    return all(os.path.samestat(first, other) for other in others)


def _read_state(metadata: os.stat_result) -> tuple[int, int, int]:
    return (
        metadata.st_size,
        getattr(metadata, "st_mtime_ns", int(metadata.st_mtime * 1_000_000_000)),
        getattr(metadata, "st_ctime_ns", int(metadata.st_ctime * 1_000_000_000)),
    )


def _open_nofollow(path: str, flags: int) -> int:
    return os.open(
        path,
        flags
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0),
    )


def _verified_untracked_metadata(
    root: Path,
    canonical: str,
    candidate: Path,
) -> os.stat_result:
    verified, finding = safe_tracked_file(root, canonical)
    if finding is not None or verified != candidate:
        raise OSError("untracked path identity changed")
    metadata = candidate.lstat()
    if not _is_regular_nonreparse(metadata):
        raise OSError("untracked path identity changed")
    return metadata


def _read_untracked_bytes_bounded(
    root: Path,
    canonical: str,
    candidate: Path,
    limit: int,
) -> bytes:
    """Read at most ``limit + 1`` bytes from one identity-stable regular file."""

    before_path = candidate.lstat()
    if not _is_regular_nonreparse(before_path):
        raise OSError("unsafe untracked file")
    with io.open(candidate, "rb", buffering=0, opener=_open_nofollow) as stream:
        opened = os.fstat(stream.fileno())
        after_open_path = _verified_untracked_metadata(root, canonical, candidate)
        if (
            not _is_regular_nonreparse(opened)
            or not _same_file_identity(before_path, opened, after_open_path)
        ):
            raise OSError("untracked path identity changed")

        content = bytearray()
        while len(content) <= limit:
            requested = limit + 1 - len(content)
            chunk = stream.read(requested)
            if not isinstance(chunk, bytes) or len(chunk) > requested:
                raise OSError("invalid untracked file read")
            if not chunk:
                break
            content.extend(chunk)

        after_read = os.fstat(stream.fileno())
        after_read_path = _verified_untracked_metadata(root, canonical, candidate)
        if (
            not _is_regular_nonreparse(after_read)
            or not _same_file_identity(opened, after_read, after_read_path)
            or _read_state(opened) != _read_state(after_read)
        ):
            raise OSError("untracked file changed during read")
    return bytes(content)


def _is_sensitive_untracked_name(relative: str) -> bool:
    folded = relative.casefold()
    path = PurePosixPath(folded)
    return (
        path.name in _SENSITIVE_BASENAMES
        or path.name.startswith(".env.")
        or path.suffix in _SENSITIVE_SUFFIXES
        or any(folded.startswith(prefix) for prefix in _SENSITIVE_PREFIXES)
    )


def _is_exact_ignored_artifact(relative: str, policy: ReleasePolicy) -> bool:
    """Return whether one ignored path is an exact non-text cache/build artifact."""

    path = PurePosixPath(relative.casefold())
    parts = path.parts
    in_cache = any(
        part in _BENIGN_IGNORED_COMPONENTS or part.endswith(".egg-info")
        for part in parts
    )
    if in_cache and path.suffix in _EXACT_CACHE_BINARY_SUFFIXES:
        return True
    if not _is_under(relative, policy.ci_generated_paths):
        return False
    name = path.name
    return bool(
        name == "sha256sums"
        or re.fullmatch(r"pathway-atlas-[0-9]+(?:\.[0-9]+){2}\.zip", name)
        or re.fullmatch(r"pathway_atlas-[0-9]+(?:\.[0-9]+){2}-.+\.whl", name)
    )


def _safe_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    sensitive_kinds = {
        "secret",
        "student_pii",
        "phone",
        "identity_number",
        "absolute_local_path",
        "private_system_reference",
        "pricing_or_sales",
    }
    if (
        _CONTROL_RE.search(normalized)
        or len(normalized) > 180
        or any(finding.kind in sensitive_kinds for finding in scan_text(normalized))
    ):
        return "redacted-sensitive-path"
    return normalized


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    details: tuple[str, ...] = ()
    count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "count": self.count,
            "details": list(self.details),
        }


@dataclass(frozen=True)
class ReleaseContext:
    root: Path
    expected_version: str
    tag: str | None = None
    ci: bool = False
    run_tests: bool = True
    python_executable: str = sys.executable


@dataclass(frozen=True)
class ReleaseReport:
    results: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "check_count": len(self.results),
            "failed_check_count": sum(not result.ok for result in self.results),
            "results": [result.to_dict() for result in self.results],
        }


def _canonical_repo_path(value: str) -> str | None:
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    normalized = value.replace("\\", "/")
    if unicodedata.normalize("NFKC", normalized) != normalized:
        return None
    pure = PurePosixPath(normalized)
    if (
        pure.is_absolute()
        or _DRIVE_RE.match(normalized)
        or ".." in pure.parts
        or pure.as_posix() != normalized
        or normalized.startswith("./")
    ):
        return None
    return normalized


def _path_identity(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def check_tracked_paths(
    paths: Sequence[str],
    forbidden_directories: Sequence[str] = (),
) -> CheckResult:
    """Reject forbidden or ambiguous Git path identities without absolute output."""

    details: list[str] = []
    identities: set[str] = set()
    forbidden = tuple(item.strip("/").casefold() for item in forbidden_directories)
    for raw in paths:
        canonical = _canonical_repo_path(raw)
        if canonical is None:
            details.append("noncanonical-tracked-path")
            continue
        identity = _path_identity(canonical)
        if identity in identities:
            details.append("duplicate-path-identity")
            continue
        identities.add(identity)
        for prefix in forbidden:
            if identity == prefix or identity.startswith(prefix + "/"):
                details.append(f"forbidden-tracked-directory:{_safe_relative(canonical)}")
                break
    return CheckResult("tracked_paths", not details, tuple(details), len(details))


def _check_tracked_modes(entries: Sequence[GitTrackedEntry]) -> CheckResult:
    details = tuple(
        f"kind=tracked_path;rule=unsupported-tracked-mode;line=0;path={_safe_relative(entry.path)}"
        for entry in entries
        if re.fullmatch(r"100[0-7]{3}", entry.mode) is None
    )
    return CheckResult("tracked_modes", not details, details, len(details))


def check_replacement_refs(root: Path) -> CheckResult:
    """Fail closed when the repository contains any object replacement mapping."""

    try:
        replacements = git_replacement_refs(root)
    except RuntimeError:
        return CheckResult(
            "replacement_refs",
            False,
            ("replacement-ref-inventory-failed",),
            1,
        )
    if not replacements:
        return CheckResult("replacement_refs", True)
    return CheckResult(
        "replacement_refs",
        False,
        (f"replacement-ref-count={len(replacements)}",),
        len(replacements),
    )


def check_binary_release_manifest(
    root: Path,
    policy: ReleasePolicy,
    entries: Sequence[GitTrackedEntry],
) -> CheckResult:
    """Apply the shared exact-binary contract to one immutable Git tree."""

    validation = validate_binary_release_manifest(
        policy,
        entries,
        lambda entry: git_blob_bytes(root, entry.object_id),
    )
    details = tuple(
        f"{finding.rule_id}:{_safe_relative(finding.path or 'unknown')}"
        for finding in validation.findings[:200]
    )
    if len(validation.findings) > 200:
        return CheckResult(
            "binary_release_manifest",
            False,
            details + ("binary-manifest-output-truncated",),
            len(validation.findings),
        )
    return CheckResult(
        "binary_release_manifest",
        validation.ok,
        details,
        len(validation.findings),
    )


def check_path_identities(root: Path, paths: Sequence[str]) -> CheckResult:
    """Reject symlink/reparse escapes and duplicate filesystem identities."""

    repo = root.resolve()
    details: list[str] = []
    physical: set[tuple[int, int]] = set()
    for relative in paths:
        canonical = _canonical_repo_path(relative)
        if canonical is None:
            continue
        candidate, finding = safe_tracked_file(repo, canonical)
        if finding is not None:
            details.append(
                f"kind={finding.kind};rule={finding.rule_id};line={finding.line};"
                f"path={_safe_relative(finding.path or 'unknown')}"
            )
            continue
        assert candidate is not None
        metadata = candidate.stat()
        try:
            candidate.resolve(strict=True).relative_to(repo)
        except (OSError, ValueError):
            details.append(f"tracked-path-outside-root:{_safe_relative(canonical)}")
            continue
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in physical:
            details.append("duplicate-filesystem-identity")
        physical.add(identity)
    return CheckResult("path_identities", not details, tuple(details), len(details))


def check_project_version(root: Path, expected_version: str, tag: str | None) -> CheckResult:
    details: list[str] = []
    if re.fullmatch(r"(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){2}", expected_version) is None:
        return CheckResult("project_version", False, ("invalid-expected-version",), 1)
    try:
        payload = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
        project = payload["project"]
        version = project["version"]
        name = project["name"]
    except (OSError, UnicodeError, KeyError, TypeError, ValueError):
        return CheckResult("project_version", False, ("invalid-pyproject",), 1)
    if name != "pathway-atlas":
        details.append("project-name-mismatch")
    if version != expected_version:
        details.append("expected-version-mismatch")
    if tag is not None and (tag != f"v{version}" or tag != f"v{expected_version}"):
        details.append("tag-version-mismatch")
    return CheckResult("project_version", not details, tuple(details), len(details))


def check_markdown_links(root: Path, markdown_paths: Sequence[str]) -> CheckResult:
    repo = root.resolve()
    details: list[str] = []
    for relative in sorted(markdown_paths):
        canonical = _canonical_repo_path(relative)
        if canonical is None:
            continue
        document = repo.joinpath(*PurePosixPath(canonical).parts)
        try:
            text = document.read_text("utf-8")
        except (OSError, UnicodeError):
            details.append(f"unreadable-markdown:{_safe_relative(canonical)}")
            continue
        for match in _MARKDOWN_LINK_RE.finditer(text):
            raw_target = match.group(1).strip()
            if raw_target.startswith("<") and raw_target.endswith(">"):
                raw_target = raw_target[1:-1]
            raw_target = raw_target.split(maxsplit=1)[0]
            split = urlsplit(raw_target)
            if split.scheme or split.netloc or raw_target.startswith(("#", "mailto:")):
                continue
            decoded = unquote(split.path)
            if not decoded:
                continue
            line = text.count("\n", 0, match.start()) + 1
            candidate = (document.parent / decoded).resolve(strict=False)
            try:
                candidate.relative_to(repo)
            except ValueError:
                details.append(f"outside-relative-link:{_safe_relative(canonical)}:{line}")
                continue
            if not candidate.exists():
                details.append(f"missing-relative-link:{_safe_relative(canonical)}:{line}")
    return CheckResult("markdown_links", not details, tuple(details), len(details))


def check_untracked_sensitive_paths(
    paths: Sequence[str],
    *,
    root: Path | None = None,
    policy: ReleasePolicy | None = None,
    ignored_paths: Sequence[str] = (),
    max_scan_files: int = _MAX_UNTRACKED_SCAN_FILES,
    max_scan_bytes: int = _MAX_UNTRACKED_SCAN_BYTES,
) -> CheckResult:
    details: list[str] = []
    if (
        not isinstance(max_scan_files, int)
        or isinstance(max_scan_files, bool)
        or max_scan_files < 1
        or not isinstance(max_scan_bytes, int)
        or isinstance(max_scan_bytes, bool)
        or max_scan_bytes < 1
    ):
        return CheckResult(
            "untracked_sensitive",
            False,
            ("kind=untracked_budget;rule=invalid-content-budget;line=0",),
            1,
        )
    ignored_identities = {_path_identity(path.replace("\\", "/")) for path in ignored_paths}
    ordinary_set = set(paths)
    combined = tuple(paths) + tuple(path for path in ignored_paths if path not in ordinary_set)
    resolved_root = root.resolve() if root is not None else None
    scanned_files = 0
    scanned_bytes = 0
    for raw in combined:
        normalized_raw = raw.replace("\\", "/")
        ignored = _path_identity(normalized_raw) in ignored_identities
        canonical = _canonical_repo_path(raw)
        if canonical is None:
            details.append("untracked-noncanonical-path")
            continue
        sensitive_name = _is_sensitive_untracked_name(canonical)
        if sensitive_name:
            details.append(
                "kind=untracked_path;rule=sensitive-name;line=0;"
                f"path={_safe_relative(canonical)}"
            )
        if root is None or policy is None:
            continue
        if ignored and _is_exact_ignored_artifact(canonical, policy):
            continue
        assert resolved_root is not None
        candidate, path_finding = safe_tracked_file(resolved_root, canonical)
        if path_finding is not None:
            details.append(
                "kind=untracked_path;"
                f"rule={path_finding.rule_id};line=0;path={_safe_relative(canonical)}"
            )
            continue
        assert candidate is not None
        if scanned_files + 1 > max_scan_files:
            details.append("kind=untracked_budget;rule=content-file-budget-exceeded;line=0")
            break
        scanned_files += 1
        remaining_total = max_scan_bytes - scanned_bytes
        read_limit = min(policy.max_text_bytes, remaining_total)
        try:
            raw = _read_untracked_bytes_bounded(
                resolved_root,
                canonical,
                candidate,
                read_limit,
            )
        except (OSError, ValueError):
            details.append(
                "kind=untracked_path;rule=unreadable-untracked-file;line=0;"
                f"path={_safe_relative(canonical)}"
            )
            break
        if len(raw) > policy.max_text_bytes:
            details.append(
                "kind=untracked_path;rule=untracked-file-too-large;line=0;"
                f"path={_safe_relative(canonical)}"
            )
            break
        if len(raw) > remaining_total:
            details.append("kind=untracked_budget;rule=content-byte-budget-exceeded;line=0")
            break
        scanned_bytes += len(raw)
        if b"\x00" in raw:
            details.append(
                "kind=untracked_path;rule=undeclared-binary-content;line=0;"
                f"path={_safe_relative(canonical)}"
            )
            continue
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeError:
            details.append(
                "kind=untracked_path;rule=untracked-text-not-utf8;line=0;"
                f"path={_safe_relative(canonical)}"
            )
            continue
        details.extend(
            f"kind={finding.kind};rule={finding.rule_id};line={finding.line};path={_safe_relative(canonical)}"
            for finding in scan_text(text)
        )
    bounded = tuple(details[:200])
    if len(details) > 200:
        bounded += ("sensitive-untracked-output-truncated",)
    return CheckResult("untracked_sensitive", not details, bounded, len(details))


def _git_untracked_inventory(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ordinary and ignored untracked files from isolated Git queries."""

    ordinary = git_paths(root, ("ls-files", "--others", "--exclude-standard", "-z", "--"))
    ignored = git_paths(
        root,
        ("ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--"),
    )
    return ordinary, ignored


def _strict_json(path: Path) -> object:
    raw = path.read_bytes()
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("oversized JSON")
    text = raw.decode("utf-8", errors="strict")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_nonfinite(token: str) -> object:
        raise ValueError(f"non-finite JSON number: {token}")

    return json.loads(text, object_pairs_hook=reject_duplicates, parse_constant=reject_nonfinite)


def _validate_schema(instance: object, schema: object, depth: int = 0) -> None:
    if depth > 32 or not isinstance(schema, dict):
        raise ValueError("unsupported schema depth or shape")
    if "const" in schema and instance != schema["const"]:
        raise ValueError("const mismatch")
    if "enum" in schema and instance not in schema["enum"]:
        raise ValueError("enum mismatch")
    expected_type = schema.get("type")
    type_map: dict[str, Callable[[object], bool]] = {
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "null": lambda value: value is None,
    }
    if isinstance(expected_type, str) and (
        expected_type not in type_map or not type_map[expected_type](instance)
    ):
        raise ValueError("type mismatch")
    if isinstance(instance, dict):
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if not isinstance(required, list) or not isinstance(properties, dict):
            raise ValueError("invalid object schema")
        if any(key not in instance for key in required):
            raise ValueError("missing required property")
        if schema.get("additionalProperties") is False and any(key not in properties for key in instance):
            raise ValueError("additional property")
        for key, value in instance.items():
            if key in properties:
                _validate_schema(value, properties[key], depth + 1)
    if isinstance(instance, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(instance) < minimum:
            raise ValueError("too few items")
        if isinstance(maximum, int) and len(instance) > maximum:
            raise ValueError("too many items")
        if schema.get("uniqueItems") is True:
            fingerprints = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in instance]
            if len(fingerprints) != len(set(fingerprints)):
                raise ValueError("duplicate array item")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for item in instance:
                _validate_schema(item, item_schema, depth + 1)
    if isinstance(instance, str):
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, instance) is None:
            raise ValueError("pattern mismatch")
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(instance) < minimum:
            raise ValueError("string too short")


def check_province_catalog(root: Path) -> CheckResult:
    try:
        schema = _strict_json(root / "schemas" / "province-catalog.schema.json")
        catalog = _strict_json(root / "references" / "provinces" / "index.json")
        if not isinstance(schema, dict):
            raise ValueError("schema is not an object")
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ValueError("wrong schema dialect")
        _validate_schema(catalog, schema)
    except (OSError, UnicodeError, TypeError, ValueError):
        return CheckResult("province_catalog", False, ("province-catalog-schema-failed",), 1)
    return CheckResult("province_catalog", True)


def _check_repo_scope(root: Path) -> CheckResult:
    try:
        metadata = root.lstat()
    except OSError:
        return CheckResult("repository_scope", False, ("repository-root-unreadable",), 1)
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag):
        return CheckResult("repository_scope", False, ("repository-root-link-or-reparse",), 1)
    try:
        top = git_top_level(root)
    except RuntimeError:
        return CheckResult("repository_scope", False, ("not-a-git-repository",), 1)
    if top != root.resolve():
        return CheckResult("repository_scope", False, ("root-is-not-git-top-level",), 1)
    for required in ("pyproject.toml", "SKILL.md", "scripts", "tests"):
        if not (root / required).exists():
            return CheckResult("repository_scope", False, (f"missing-top-level:{required}",), 1)
    return CheckResult("repository_scope", True)


def _check_license_and_data_docs(root: Path, tracked: frozenset[str]) -> CheckResult:
    details: list[str] = []
    try:
        license_text = (root / "LICENSE").read_text("utf-8")
        data_text = (root / "DATA_SOURCES.md").read_text("utf-8")
    except (OSError, UnicodeError):
        return CheckResult("license_and_data_docs", False, tuple(details + ["unreadable-license-or-data-doc"]), len(details) + 1)
    if "MIT License" not in license_text or "Copyright (c) 2026 sarry12227" not in license_text:
        details.append("mit-license-identity-missing")
    if "MIT 不自动授予第三方数据的再分发权" not in data_text or "删除请求" not in data_text:
        details.append("data-rights-boundary-missing")
    return CheckResult("license_and_data_docs", not details, tuple(details), len(details))

def _status_path(entry: str) -> str | None:
    if len(entry) < 4 or entry[2] != " ":
        return None
    return _canonical_repo_path(entry[3:])


def _is_under(relative: str, prefixes: Sequence[str]) -> bool:
    identity = _path_identity(relative)
    return any(identity == _path_identity(prefix) or identity.startswith(_path_identity(prefix) + "/") for prefix in prefixes)


def _check_clean_worktree(root: Path, ci: bool, generated_paths: Sequence[str] = ()) -> CheckResult:
    try:
        entries = git_paths(root, ("status", "--porcelain=v1", "-z", "--untracked-files=all"))
    except RuntimeError:
        return CheckResult("clean_worktree", False, ("git-status-failed",), 1)
    unexpected = []
    for entry in entries:
        relative = _status_path(entry)
        if relative is None or not (ci and _is_under(relative, generated_paths)):
            unexpected.append(entry)
    if unexpected:
        return CheckResult("clean_worktree", False, ("worktree-has-unexpected-changes",), len(unexpected))
    return CheckResult("clean_worktree", True)


_UNITTEST_CHILD = r'''
import io, json, os, sys, unittest
root, *modules = sys.argv[1:]
sys.path.insert(0, root)
os.chdir(root)
suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
loaded = suite.countTestCases()
result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
print(json.dumps({"requested": len(modules), "loaded": loaded, "run": result.testsRun,
                  "skipped": len(result.skipped), "failures": len(result.failures),
                  "errors": len(result.errors)}, sort_keys=True))
'''


_NETWORK_CHILD = r'''
import http.client, io, json, os, socket, sys, unittest, urllib.request
root, *modules = sys.argv[1:]
sys.path.insert(0, root)
os.chdir(root)
attempts = 0
class NetworkBlocked(RuntimeError): pass
def blocked(*args, **kwargs):
    global attempts
    attempts += 1
    raise NetworkBlocked("offline-network-blocked")
OriginalSocket = socket.socket
sendmsg_available = hasattr(OriginalSocket, "sendmsg")
class GuardedSocket(OriginalSocket):
    connect = blocked
    connect_ex = blocked
    send = blocked
    sendall = blocked
    sendto = blocked
if sendmsg_available:
    GuardedSocket.sendmsg = blocked
socket.socket = GuardedSocket
socket.create_connection = blocked
socket.getaddrinfo = blocked
socket.gethostbyname = blocked
socket.gethostbyname_ex = blocked
socket.gethostbyaddr = blocked
socket.getnameinfo = blocked
http.client.HTTPConnection.connect = blocked
http.client.HTTPConnection.request = blocked
http.client.HTTPSConnection.connect = blocked
http.client.HTTPSConnection.request = blocked
urllib.request.urlopen = blocked
canaries = {
    "dns-getaddrinfo": lambda: socket.getaddrinfo("invalid.test", 443),
    "dns-gethostbyname": lambda: socket.gethostbyname("invalid.test"),
    "dns-gethostbyname-ex": lambda: socket.gethostbyname_ex("invalid.test"),
    "reverse-gethostbyaddr": lambda: socket.gethostbyaddr("192.0.2.1"),
    "reverse-getnameinfo": lambda: socket.getnameinfo(("192.0.2.1", 9), 0),
    "tcp-create-connection": lambda: socket.create_connection(("192.0.2.1", 9)),
    "tcp-connect": lambda: GuardedSocket().connect(("192.0.2.1", 9)),
    "tcp-connect-ex": lambda: GuardedSocket().connect_ex(("192.0.2.1", 9)),
    "udp": lambda: GuardedSocket(type=socket.SOCK_DGRAM).sendto(b"x", ("192.0.2.1", 9)),
    "send": lambda: GuardedSocket().send(b"x"),
    "sendall": lambda: GuardedSocket().sendall(b"x"),
    "http-urlopen": lambda: urllib.request.urlopen("http://invalid.test/"),
    "http-request": lambda: http.client.HTTPConnection("invalid.test").request("GET", "/"),
}
if sendmsg_available:
    canaries["sendmsg"] = lambda: GuardedSocket().sendmsg([b"x"])
armed = []
for name, canary in canaries.items():
    try: canary()
    except NetworkBlocked: armed.append(name)
attempts = 0
suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
loaded = suite.countTestCases()
result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
print(json.dumps({"armed": sorted(armed), "attempts": attempts, "requested": len(modules),
                  "sendmsg_available": sendmsg_available,
                  "loaded": loaded, "run": result.testsRun, "skipped": len(result.skipped),
                  "failures": len(result.failures), "errors": len(result.errors)}, sort_keys=True))
'''


def _isolated_child_environment() -> dict[str, str]:
    environment = git_environment()
    blocked_names = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    return {
        name: value
        for name, value in environment.items()
        if not name.upper().startswith("PYTHON") and name.casefold() not in blocked_names
    }


def _run_json_child(
    root: Path,
    python_executable: str,
    script: str,
    modules: Sequence[str],
) -> dict[str, object] | None:
    try:
        completed = subprocess.run(
            [python_executable, "-I", "-c", script, str(root), *modules],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_isolated_child_environment(),
            timeout=600,
        )
        if completed.returncode != 0:
            return None
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _valid_test_child(payload: dict[str, object] | None, requested: int) -> bool:
    if payload is None or set(payload) != {"requested", "loaded", "run", "skipped", "failures", "errors"}:
        return False
    return (
        payload["requested"] == requested
        and isinstance(payload["loaded"], int)
        and payload["loaded"] > 0
        and payload["run"] == payload["loaded"]
        and payload["skipped"] == 0
        and payload["failures"] == 0
        and payload["errors"] == 0
    )


def _check_docx_tests(context: ReleaseContext, policy: ReleasePolicy) -> CheckResult:
    if not context.run_tests:
        return CheckResult("docx_tests", False, ("docx-tests-not-run",), 1)
    payload = _run_json_child(
        context.root,
        context.python_executable,
        _UNITTEST_CHILD,
        policy.docx_test_modules,
    )
    if not _valid_test_child(payload, len(policy.docx_test_modules)):
        return CheckResult("docx_tests", False, ("docx-suite-failed-loaded-run-or-skip-contract",), 1)
    assert payload is not None
    return CheckResult("docx_tests", True, (f"loaded={payload['loaded']};run={payload['run']};skipped=0",), int(payload["run"]))


def _check_deterministic_boundaries(context: ReleaseContext, policy: ReleasePolicy) -> CheckResult:
    if not context.run_tests:
        return CheckResult("deterministic_boundaries", False, ("deterministic-tests-not-run",), 1)
    if not (context.root / "scripts" / "live_smoke.py").is_file():
        return CheckResult("deterministic_boundaries", False, ("live-network-boundary-missing",), 1)
    payload = _run_json_child(
        context.root,
        context.python_executable,
        _NETWORK_CHILD,
        policy.deterministic_test_modules,
    )
    required = {
        "dns-getaddrinfo",
        "dns-gethostbyname",
        "dns-gethostbyname-ex",
        "reverse-gethostbyaddr",
        "reverse-getnameinfo",
        "tcp-create-connection",
        "tcp-connect",
        "tcp-connect-ex",
        "udp",
        "send",
        "sendall",
        "http-urlopen",
        "http-request",
    }
    valid_shape = payload is not None and set(payload) == {
        "armed", "attempts", "requested", "sendmsg_available", "loaded", "run", "skipped", "failures", "errors"
    }
    sendmsg_available = bool(valid_shape and payload["sendmsg_available"] is True)
    if sendmsg_available:
        required.add("sendmsg")
    ok = bool(
        valid_shape
        and set(payload["armed"]) == required
        and payload["attempts"] == 0
        and payload["requested"] == len(policy.deterministic_test_modules)
        and isinstance(payload["loaded"], int)
        and payload["loaded"] > 0
        and payload["run"] == payload["loaded"]
        and isinstance(payload["skipped"], int)
        and 0 <= payload["skipped"] < payload["run"]
        and payload["failures"] == 0
        and payload["errors"] == 0
    )
    if not ok:
        return CheckResult("deterministic_boundaries", False, ("offline-sentinel-or-test-contract-failed",), 1)
    assert payload is not None
    return CheckResult(
        "deterministic_boundaries",
        True,
        (
            f"armed={len(required)};sendmsg={'armed' if sendmsg_available else 'unavailable'};"
            f"attempts=0;run={payload['run']};skipped={payload['skipped']}",
        ),
        int(payload["run"]),
    )


def _check_future_paths(
    root: Path,
    policy: ReleasePolicy,
    tracked: Sequence[str],
) -> CheckResult:
    details = tuple(
        f"missing-or-untracked:{path}"
        for path in missing_required_release_paths(policy, tracked)
    )
    return CheckResult("future_release_artifacts", not details, details, len(details))


def _check_full_tests(context: ReleaseContext) -> CheckResult:
    if not context.run_tests:
        return CheckResult("full_tests", False, ("tests-not-run",), 1)
    try:
        completed = subprocess.run(
            [context.python_executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=context.root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_isolated_child_environment(),
            # The complete receipt/replay suite exceeds ten minutes on Windows.
            # Keep a finite bound while allowing all tests to finish.
            timeout=3600,
        )
    except (OSError, subprocess.TimeoutExpired):
        return CheckResult("full_tests", False, ("test-runner-failed",), 1)
    output = completed.stdout + "\n" + completed.stderr
    count_matches = re.findall(r"Ran (\d+) tests?", output)
    test_count = int(count_matches[-1]) if count_matches else 0
    details: list[str] = []
    if completed.returncode != 0:
        details.append("test-suite-failed")
        failed = re.findall(
            r"^(?:FAIL|ERROR): ([A-Za-z0-9_]+) \(([A-Za-z0-9_.]+)\)",
            output, re.MULTILINE,
        )
        details.extend(
            f"failed-test:{owner}.{method}"
            for method, owner in sorted(set(failed))[:100]
        )
    if test_count == 0:
        details.append("test-count-unavailable")
    return CheckResult("full_tests", not details, tuple(details), test_count)


def _safe_run(name: str, function: Callable[[], CheckResult]) -> CheckResult:
    try:
        return function()
    except Exception:  # The JSON gate fails closed without serializing exception data.
        return CheckResult(name, False, ("internal-check-error",), 1)


def evaluate_release(context: ReleaseContext) -> ReleaseReport:
    """Evaluate every gate and return a bounded JSON-serializable report."""

    root = context.root.absolute()
    replacement_result = _safe_run(
        "replacement_refs",
        lambda: check_replacement_refs(root),
    )
    try:
        tree_oid = git_write_tree(root)
        tracked_entries = git_tree_entries(root, tree_oid)
        policy_entry = next(entry for entry in tracked_entries if entry.path == "release-policy.json")
        policy = parse_policy_bytes(git_blob_bytes(root, policy_entry.object_id))
        tracked = tuple(entry.path for entry in tracked_entries)
        tracked_inventory_result = CheckResult("tracked_inventory", True, count=len(tracked))
    except (PolicyError, RuntimeError, StopIteration):
        return ReleaseReport(
            (
                replacement_result,
                CheckResult("release_policy", False, ("invalid-release-policy",), 1),
            )
        )
    try:
        if not tracked_entries:
            raise RuntimeError("empty tracked inventory")
    except RuntimeError:
        tracked_entries = ()
        tracked = ()
        tracked_inventory_result = CheckResult("tracked_inventory", False, ("git-index-inventory-failed",), 1)
    try:
        untracked, ignored_untracked = _git_untracked_inventory(root)
        untracked_inventory_result = CheckResult(
            "untracked_inventory",
            True,
            (f"ordinary={len(untracked)};ignored={len(ignored_untracked)}",),
            len(untracked) + len(ignored_untracked),
        )
    except RuntimeError:
        untracked = ()
        ignored_untracked = ()
        untracked_inventory_result = CheckResult("untracked_inventory", False, ("git-untracked-inventory-failed",), 1)

    scan_result = _safe_run(
        "compliance_scan",
        lambda: _compliance_result(root, policy, tracked_entries),
    )
    markdown = tuple(path for path in tracked if path.casefold().endswith(".md"))
    tracked_set = frozenset(tracked)
    results = (
        _safe_run("repository_scope", lambda: _check_repo_scope(root)),
        CheckResult("release_policy", True),
        replacement_result,
        tracked_inventory_result,
        untracked_inventory_result,
        check_tracked_paths(tracked, policy.forbidden_tracked_directories),
        _check_tracked_modes(tracked_entries),
        _safe_run("path_identities", lambda: check_path_identities(root, tracked)),
        scan_result,
        _safe_run(
            "binary_release_manifest",
            lambda: check_binary_release_manifest(root, policy, tracked_entries),
        ),
        check_project_version(root, context.expected_version, context.tag),
        _safe_run("license_and_data_docs", lambda: _check_license_and_data_docs(root, tracked_set)),
        _safe_run(
            "clean_worktree",
            lambda: _check_clean_worktree(root, context.ci, policy.ci_generated_paths),
        ),
        check_untracked_sensitive_paths(
            untracked,
            root=root,
            policy=policy,
            ignored_paths=ignored_untracked,
        ),
        _safe_run("province_catalog", lambda: check_province_catalog(root)),
        _safe_run("markdown_links", lambda: check_markdown_links(root, markdown)),
        _safe_run("deterministic_boundaries", lambda: _check_deterministic_boundaries(context, policy)),
        _safe_run("future_release_artifacts", lambda: _check_future_paths(root, policy, tracked)),
        _safe_run("docx_tests", lambda: _check_docx_tests(context, policy)),
        _safe_run("full_tests", lambda: _check_full_tests(context)),
    )
    return ReleaseReport(results)


def _compliance_result(
    root: Path,
    policy: ReleasePolicy,
    entries: Sequence[GitTrackedEntry] | None = None,
) -> CheckResult:
    if entries is None:
        entries = git_tree_entries(root, git_write_tree(root))
    summary = scan_git_snapshot(root, policy, entries)
    details = tuple(
        f"kind={finding.kind};rule={finding.rule_id};line={finding.line};"
        f"path={_safe_relative(finding.path or 'unknown')}"
        for finding in summary.findings[:200]
    )
    if len(summary.findings) > 200:
        return CheckResult("compliance_scan", False, details + ("finding-output-truncated",), len(summary.findings))
    return CheckResult("compliance_scan", summary.ok, details, len(summary.findings))


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--tag", default=None)
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--internal-skip-tests", action="store_true", help=argparse.SUPPRESS)
    try:
        parsed = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as error:
        return int(error.code)
    testing_bypass = parsed.internal_skip_tests and os.environ.get("SHENGXUE_RELEASE_CHECK_TESTING") == "1"
    context = ReleaseContext(
        root=parsed.root,
        expected_version=parsed.expected_version,
        tag=parsed.tag,
        ci=parsed.ci,
        run_tests=not testing_bypass,
    )
    report = evaluate_release(context)
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
