from __future__ import annotations

import copy
import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


@dataclass(frozen=True)
class _YamlLine:
    indent: int
    content: str


def _scalar(value: str) -> Any:
    if value.startswith('"'):
        return json.loads(value)
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _key_value(content: str) -> tuple[str, str]:
    if ":" not in content:
        raise ValueError(f"expected mapping entry: {content}")
    key, value = content.split(":", 1)
    if not key or key != key.strip():
        raise ValueError(f"invalid mapping key: {content}")
    return key, value.strip()


def _load_yaml_subset(text: str) -> dict[str, Any]:
    """Parse the conservative YAML subset used by deterministic CI.

    The test suite deliberately has no YAML runtime dependency. This parser
    handles nested mappings, sequences, quoted scalars, booleans, integers,
    and null values, and rejects aliases, tags, tabs, flow collections, and
    multiline scalars that could conceal a materially different workflow.
    """

    lines: list[_YamlLine] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw:
            raise ValueError(f"tabs are not allowed on line {number}")
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise ValueError(f"indentation must use two-space levels on line {number}")
        content = raw[indent:]
        if content.startswith(("&", "*", "!", "|", ">", "[", "{")):
            raise ValueError(f"unsupported YAML feature on line {number}")
        lines.append(_YamlLine(indent, content))
    if not lines:
        raise ValueError("workflow is empty")

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(lines) or lines[index].indent != indent:
            raise ValueError("invalid block indentation")
        if lines[index].content.startswith("- "):
            return parse_sequence(index, indent)
        return parse_mapping(index, indent)

    def parse_mapping(index: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while index < len(lines) and lines[index].indent == indent:
            line = lines[index]
            if line.content.startswith("- "):
                break
            key, value = _key_value(line.content)
            if key in result:
                raise ValueError(f"duplicate mapping key: {key}")
            index += 1
            if value:
                result[key] = _scalar(value)
            elif index < len(lines) and lines[index].indent > indent:
                if lines[index].indent != indent + 2:
                    raise ValueError(f"invalid child indentation for {key}")
                result[key], index = parse_block(index, indent + 2)
            else:
                result[key] = None
        return result, index

    def parse_sequence(index: int, indent: int) -> tuple[list[Any], int]:
        result: list[Any] = []
        while index < len(lines) and lines[index].indent == indent:
            line = lines[index]
            if not line.content.startswith("- "):
                break
            item = line.content[2:].strip()
            if not item:
                raise ValueError("empty sequence entries are not supported")
            index += 1
            if ":" not in item:
                result.append(_scalar(item))
                continue
            key, value = _key_value(item)
            mapping: dict[str, Any] = {key: _scalar(value) if value else None}
            if index < len(lines) and lines[index].indent > indent:
                if lines[index].indent != indent + 2:
                    raise ValueError(f"invalid sequence mapping indentation for {key}")
                continuation, index = parse_mapping(index, indent + 2)
                overlap = set(mapping).intersection(continuation)
                if overlap:
                    raise ValueError(f"duplicate sequence mapping key: {sorted(overlap)[0]}")
                mapping.update(continuation)
            result.append(mapping)
        return result, index

    document, consumed = parse_block(0, lines[0].indent)
    if lines[0].indent != 0 or consumed != len(lines) or not isinstance(document, dict):
        raise ValueError("workflow must be one top-level mapping")
    return document


def _workflow_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    triggers = document.get("on")
    if not isinstance(triggers, dict) or set(triggers) != {"push", "pull_request"}:
        errors.append("triggers")
    if document.get("permissions") != {"contents": "read"}:
        errors.append("permissions")
    if document.get("env") != {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}:
        errors.append("utf8-environment")

    concurrency = document.get("concurrency")
    if not isinstance(concurrency, dict) or concurrency.get("cancel-in-progress") is not True:
        errors.append("concurrency")

    jobs = document.get("jobs")
    job = jobs.get("test") if isinstance(jobs, dict) else None
    if not isinstance(job, dict):
        return errors + ["test-job"]
    if "permissions" in job:
        errors.append("job-permissions")
    timeout = job.get("timeout-minutes")
    if not isinstance(timeout, int) or not 1 <= timeout <= 30:
        errors.append("timeout")

    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, dict) or set(matrix) != {"os", "python-version"}:
        errors.append("matrix-shape")
    else:
        if matrix["os"] != ["ubuntu-latest", "windows-latest", "macos-latest"]:
            errors.append("operating-systems")
        if matrix["python-version"] != ["3.10", "3.13"]:
            errors.append("python-versions")

    steps = job.get("steps")
    if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
        return errors + ["steps"]
    actions = [step.get("uses") for step in steps if "uses" in step]
    if actions != ["actions/checkout@v7", "actions/setup-python@v7"]:
        errors.append("official-actions")

    release_steps = [
        step
        for step in steps
        if step.get("run") == "python scripts/release_check.py --ci --expected-version 0.1.0"
    ]
    if len(release_steps) != 1:
        errors.append("release-gate")
    elif any(key in release_steps[0] for key in ("continue-on-error", "if")):
        errors.append("release-gate-bypass")
    return errors


class WorkflowTest(unittest.TestCase):
    def load_ci(self) -> dict[str, Any]:
        self.assertTrue(CI_WORKFLOW.is_file(), ".github/workflows/ci.yml is missing")
        return _load_yaml_subset(CI_WORKFLOW.read_text(encoding="utf-8"))

    def test_ci_is_parseable_and_has_no_contract_errors(self) -> None:
        self.assertEqual(_workflow_errors(self.load_ci()), [])

    def test_ci_matrix_is_exactly_six_supported_environments(self) -> None:
        ci = self.load_ci()
        matrix = ci["jobs"]["test"]["strategy"]["matrix"]
        combinations = {
            (operating_system, python_version)
            for operating_system in matrix["os"]
            for python_version in matrix["python-version"]
        }
        self.assertEqual(
            combinations,
            {
                ("ubuntu-latest", "3.10"),
                ("ubuntu-latest", "3.13"),
                ("windows-latest", "3.10"),
                ("windows-latest", "3.13"),
                ("macos-latest", "3.10"),
                ("macos-latest", "3.13"),
            },
        )

    def test_ci_uses_official_actions_and_pyproject_keyed_pip_cache(self) -> None:
        steps = self.load_ci()["jobs"]["test"]["steps"]
        checkout, setup = [step for step in steps if "uses" in step]
        self.assertEqual(checkout["uses"], "actions/checkout@v7")
        self.assertEqual(checkout.get("with"), {"persist-credentials": False})
        self.assertEqual(setup["uses"], "actions/setup-python@v7")
        self.assertEqual(
            setup.get("with"),
            {
                "python-version": "${{ matrix.python-version }}",
                "cache": "pip",
                "cache-dependency-path": "pyproject.toml",
            },
        )

    def test_ci_runs_every_deterministic_gate_without_release_bypass(self) -> None:
        steps = self.load_ci()["jobs"]["test"]["steps"]
        run_steps = [step for step in steps if "run" in step]
        self.assertEqual(
            [step["run"] for step in run_steps],
            [
                'python -m pip install -e ".[all,test]"',
                "python -m unittest discover -s tests -v",
                "python scripts/validate_data.py tests/fixtures/provinces/demo-312",
                "python scripts/validate_data.py tests/fixtures/provinces/demo-33",
                "python scripts/validate_evidence.py tests/fixtures/evidence/three-source-consensus",
                "python scripts/compliance_scan.py --tracked",
                "python scripts/release_check.py --ci --expected-version 0.1.0",
            ],
        )
        release_step = run_steps[-1]
        self.assertNotIn("continue-on-error", release_step)
        self.assertNotIn("if", release_step)

    def test_ci_commands_are_cross_platform_and_never_run_live_web_checks(self) -> None:
        steps = self.load_ci()["jobs"]["test"]["steps"]
        commands = [step["run"] for step in steps if "run" in step]
        for command in commands:
            self.assertTrue(command.startswith("python "), command)
            for shell_specific in ("&&", "||", ";", "|", ">", "$env:", "export ", "\\"):
                self.assertNotIn(shell_specific, command)
        rendered = "\n".join(commands).casefold()
        for live_entrypoint in ("live_smoke", "curl", "wget", "invoke-webrequest", "http://", "https://"):
            self.assertNotIn(live_entrypoint, rendered)

    def test_yaml_parser_rejects_a_malformed_mapping(self) -> None:
        with self.assertRaises(ValueError):
            _load_yaml_subset("name: CI\njobs\n  test:\n")

    def test_structure_mutation_canaries_catch_unsafe_workflow_changes(self) -> None:
        ci = self.load_ci()

        missing_platform = copy.deepcopy(ci)
        missing_platform["jobs"]["test"]["strategy"]["matrix"]["os"].pop()
        self.assertIn("operating-systems", _workflow_errors(missing_platform))

        third_party_action = copy.deepcopy(ci)
        third_party_action["jobs"]["test"]["steps"][0]["uses"] = "vendor/checkout@v7"
        self.assertIn("official-actions", _workflow_errors(third_party_action))

        bypassed_release = copy.deepcopy(ci)
        bypassed_release["jobs"]["test"]["steps"][-1]["continue-on-error"] = True
        self.assertIn("release-gate-bypass", _workflow_errors(bypassed_release))


if __name__ == "__main__":
    unittest.main()
