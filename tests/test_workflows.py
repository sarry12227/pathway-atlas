from __future__ import annotations

import copy
import re
import unittest
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
EXPECTED_CONCURRENCY_GROUP = "deterministic-ci-${{ github.workflow }}-${{ github.ref }}"


class _StrictWorkflowLoader(yaml.SafeLoader):
    pass


_StrictWorkflowLoader.yaml_implicit_resolvers = {
    initial: list(resolvers)
    for initial, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for _initial, _resolvers in tuple(_StrictWorkflowLoader.yaml_implicit_resolvers.items()):
    _StrictWorkflowLoader.yaml_implicit_resolvers[_initial] = [
        resolver
        for resolver in _resolvers
        if resolver[0] != "tag:yaml.org,2002:bool"
    ]
_StrictWorkflowLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def _construct_unique_mapping(
    loader: _StrictWorkflowLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictWorkflowLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_yaml_subset(text: str) -> dict[str, Any]:
    """Parse workflow YAML with real syntax and strict duplicate-key checks."""

    try:
        yaml.safe_load(text)
        document = yaml.load(text, Loader=_StrictWorkflowLoader)
    except yaml.YAMLError as error:
        raise ValueError("invalid workflow YAML") from error
    if not isinstance(document, dict):
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
    if not isinstance(concurrency, dict):
        errors.append("concurrency")
    else:
        if concurrency.get("group") != EXPECTED_CONCURRENCY_GROUP:
            errors.append("concurrency-group")
        if concurrency.get("cancel-in-progress") is not True:
            errors.append("concurrency")

    jobs = document.get("jobs")
    job = jobs.get("test") if isinstance(jobs, dict) else None
    if not isinstance(job, dict):
        return errors + ["test-job"]
    if "permissions" in job:
        errors.append("job-permissions")
    if job.get("runs-on") != "${{ matrix.os }}":
        errors.append("runs-on")
    timeout = job.get("timeout-minutes")
    if not isinstance(timeout, int) or not 1 <= timeout <= 30:
        errors.append("timeout")

    strategy = job.get("strategy")
    if not isinstance(strategy, dict) or strategy.get("fail-fast") is not False:
        errors.append("fail-fast")
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
    if any("if" in step or "continue-on-error" in step for step in steps):
        errors.append("critical-step-bypass")

    default_shells: list[Any] = []
    for container in (document, job):
        defaults = container.get("defaults")
        run_defaults = defaults.get("run") if isinstance(defaults, dict) else None
        if isinstance(run_defaults, dict) and "shell" in run_defaults:
            default_shells.append(run_defaults["shell"])
    default_shells.extend(step.get("shell") for step in steps if "run" in step and "shell" in step)
    if any(
        isinstance(shell, str)
        and re.match(r"^cmd(?:\.exe)?(?:\s|$)", shell.strip(), re.IGNORECASE)
        for shell in default_shells
    ):
        errors.append("portable-shell")

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

    def test_yaml_parser_rejects_unterminated_quotes_and_duplicate_keys(self) -> None:
        with self.assertRaises(ValueError):
            _load_yaml_subset("name: 'unterminated\n")
        with self.assertRaises(ValueError):
            _load_yaml_subset("name: first\nname: second\n")

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

    def test_execution_structure_mutation_canaries_reject_matrix_drift(self) -> None:
        ci = self.load_ci()

        wrong_runner = copy.deepcopy(ci)
        wrong_runner["jobs"]["test"]["runs-on"] = "ubuntu-latest"
        self.assertIn("runs-on", _workflow_errors(wrong_runner))

        fail_fast = copy.deepcopy(ci)
        fail_fast["jobs"]["test"]["strategy"]["fail-fast"] = True
        self.assertIn("fail-fast", _workflow_errors(fail_fast))

    def test_concurrency_mutation_canaries_reject_unstable_or_uncancellable_groups(self) -> None:
        ci = self.load_ci()

        for unsafe_group in ("", "fixed-group"):
            with self.subTest(group=unsafe_group):
                mutated = copy.deepcopy(ci)
                mutated["concurrency"]["group"] = unsafe_group
                self.assertIn("concurrency-group", _workflow_errors(mutated))

        uncancellable = copy.deepcopy(ci)
        uncancellable["concurrency"]["cancel-in-progress"] = False
        self.assertIn("concurrency", _workflow_errors(uncancellable))

    def test_critical_step_mutation_canaries_reject_bypasses_and_cmd_shell(self) -> None:
        ci = self.load_ci()

        skipped_step = copy.deepcopy(ci)
        skipped_step["jobs"]["test"]["steps"][2]["if"] = False
        self.assertIn("critical-step-bypass", _workflow_errors(skipped_step))

        tolerated_failure = copy.deepcopy(ci)
        tolerated_failure["jobs"]["test"]["steps"][2]["continue-on-error"] = True
        self.assertIn("critical-step-bypass", _workflow_errors(tolerated_failure))

        cmd_shell = copy.deepcopy(ci)
        cmd_shell["jobs"]["test"]["steps"][2]["shell"] = "cmd"
        self.assertIn("portable-shell", _workflow_errors(cmd_shell))


if __name__ == "__main__":
    unittest.main()
