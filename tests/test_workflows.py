from __future__ import annotations

import copy
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
PROJECT_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SOURCE_HEALTH_WORKFLOW = ROOT / ".github" / "workflows" / "source-health.yml"
SOURCE_HEALTH_TEMPLATE = ROOT / ".github" / "ISSUE_TEMPLATE" / "source-health.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
EXPECTED_CONCURRENCY_GROUP = "deterministic-ci-${{ github.workflow }}-${{ github.ref }}"
EXPECTED_SOURCE_HEALTH_CONCURRENCY_GROUP = "source-health-${{ github.repository }}"
EXPECTED_RELEASE_CONCURRENCY_GROUP = "release-tags"


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
    if not isinstance(timeout, int) or not 1 <= timeout <= 90:
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
        if step.get("run") == f"python scripts/release_check.py --ci --expected-version {PROJECT_VERSION}"
    ]
    if len(release_steps) != 1:
        errors.append("release-gate")
    elif any(key in release_steps[0] for key in ("continue-on-error", "if")):
        errors.append("release-gate-bypass")
    return errors


def _source_health_errors(document: dict[str, Any]) -> list[str]:
    """Return contract breaks in the non-authoritative live monitor."""

    errors: list[str] = []
    triggers = document.get("on")
    if not isinstance(triggers, dict) or set(triggers) != {"schedule", "workflow_dispatch"}:
        errors.append("triggers")
    else:
        schedule = triggers.get("schedule")
        if (
            not isinstance(schedule, list)
            or len(schedule) != 1
            or schedule[0] != {"cron": "17 3 * * 1"}
        ):
            errors.append("weekly-schedule")
        if triggers.get("workflow_dispatch") is not None:
            errors.append("manual-dispatch")

    if document.get("permissions") != {"contents": "read", "issues": "write"}:
        errors.append("permissions")

    concurrency = document.get("concurrency")
    if not isinstance(concurrency, dict):
        errors.append("concurrency")
    else:
        if concurrency.get("group") != EXPECTED_SOURCE_HEALTH_CONCURRENCY_GROUP:
            errors.append("concurrency-group")
        if concurrency.get("cancel-in-progress") is not True:
            errors.append("concurrency")

    jobs = document.get("jobs")
    job = jobs.get("monitor") if isinstance(jobs, dict) and set(jobs) == {"monitor"} else None
    if not isinstance(job, dict):
        return errors + ["monitor-job"]
    if "permissions" in job:
        errors.append("job-permissions")
    if job.get("runs-on") != "ubuntu-latest":
        errors.append("runs-on")
    timeout = job.get("timeout-minutes")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 15:
        errors.append("timeout")

    steps = job.get("steps")
    if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
        return errors + ["steps"]
    if any("continue-on-error" in step for step in steps):
        errors.append("failure-masking")
    if any("if" in step for step in steps if step.get("uses") != "actions/github-script@v8"):
        errors.append("failure-masking")

    actions = [step.get("uses") for step in steps if "uses" in step]
    if actions != [
        "actions/checkout@v7",
        "actions/setup-python@v7",
        "actions/cache/restore@v4",
        "actions/upload-artifact@v6",
        "actions/cache/save@v4",
        "actions/github-script@v8",
    ]:
        errors.append("official-actions")

    checkout = next((step for step in steps if step.get("uses") == "actions/checkout@v7"), {})
    if checkout.get("with") != {"persist-credentials": False}:
        errors.append("checkout-credentials")
    setup = next((step for step in steps if step.get("uses") == "actions/setup-python@v7"), {})
    if setup.get("with") != {"python-version": "3.10"}:
        errors.append("supported-python")

    cache_path = "source-health-cache/state.json"
    cache_key = "source-health-state-${{ github.run_id }}"
    restore = next(
        (step for step in steps if step.get("uses") == "actions/cache/restore@v4"),
        {},
    )
    restore_with = restore.get("with")
    if (
        not isinstance(restore_with, dict)
        or restore_with.get("path") != cache_path
        or restore_with.get("key") != cache_key
        or str(restore_with.get("restore-keys", "")).strip() != "source-health-state-"
    ):
        errors.append("cache-restore")
    save = next(
        (step for step in steps if step.get("uses") == "actions/cache/save@v4"),
        {},
    )
    if save.get("with") != {"path": cache_path, "key": cache_key} or "if" in save:
        errors.append("cache-save")

    run_steps = [step for step in steps if "run" in step]
    install_steps = [step for step in run_steps if step.get("run") == "python -m pip install -e ."]
    if len(install_steps) != 1:
        errors.append("minimal-install")
    collect_steps = [step for step in run_steps if step.get("id") == "collect"]
    if len(collect_steps) != 1 or not isinstance(collect_steps[0].get("run"), str):
        errors.append("collector")
        collector = ""
    else:
        collector = collect_steps[0]["run"]

    required_collector_contract = (
        "MAX_PROVINCE_ALIASES",
        "load_province_catalog",
        "MAX_CATALOG_PROVINCES = 29",
        "PROCESS_TIMEOUT_SECONDS = 6",
        "COLLECTION_BUDGET_SECONDS = 600",
        "max_calls = MAX_CATALOG_PROVINCES * MAX_PROVINCE_ALIASES",
        "max_calls * PROCESS_TIMEOUT_SECONDS > COLLECTION_BUDGET_SECONDS",
        "TOTAL_TIMEOUT_SECONDS != 5.0",
        "MAX_RESPONSE_BYTES != 1_048_576",
        '"scripts/live_smoke.py"',
        '"--province"',
        "subprocess.run",
        "timeout=PROCESS_TIMEOUT_SECONDS",
        '"source-health/results.json"',
    )
    if (
        any(token not in collector for token in required_collector_contract)
        or re.search(r"(?m)^\s*for alias in discovery\.aliases:\s*$", collector) is None
    ):
        errors.append("catalog-coverage")
    lowered_commands = "\n".join(
        str(step.get("run", "")) for step in run_steps
    ).casefold()
    forbidden_network_or_mutation = (
        "curl ",
        "wget ",
        "invoke-webrequest",
        "requests.",
        "urllib.",
        "httpx.",
        "aiohttp.",
        "socket.",
        "git push",
        "git commit",
        "write_text(\"references/provinces/index.json",
        "scripts/validate_data.py",
        "scripts/validate_evidence.py",
        "scripts/release_check.py",
    )
    if any(token in lowered_commands for token in forbidden_network_or_mutation):
        errors.append("unsafe-command")
    live_entrypoints = re.findall(r"scripts[/\\][A-Za-z0-9_.-]+\.py", lowered_commands)
    if set(live_entrypoints) != {"scripts/live_smoke.py"}:
        errors.append("live-entrypoint")

    state_steps = [step for step in run_steps if step.get("id") == "state"]
    if len(state_steps) != 1 or not isinstance(state_steps[0].get("run"), str):
        errors.append("state-wiring")
        state_runner = ""
    else:
        state_runner = state_steps[0]["run"]
    required_state_wiring = (
        "HealthObservation",
        "state_from_payload",
        "state_to_payload",
        "transition_source_health",
        'Path("source-health-cache/state.json")',
        'Path("source-health/review.json")',
        'os.environ["GITHUB_OUTPUT"]',
        '"review=true\\n" if transition.review else "review=false\\n"',
    )
    if any(token not in state_runner for token in required_state_wiring):
        errors.append("state-wiring")

    upload = next(
        (step for step in steps if step.get("uses") == "actions/upload-artifact@v6"),
        {},
    )
    upload_with = upload.get("with")
    if not isinstance(upload_with, dict):
        errors.append("artifact")
    else:
        artifact_path = upload_with.get("path")
        if (
            upload_with.get("name") != "source-health-${{ github.run_id }}"
            or artifact_path != "source-health/results.json"
            or upload_with.get("if-no-files-found") != "error"
            or upload_with.get("retention-days") != 14
            or not isinstance(artifact_path, str)
            or artifact_path.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", artifact_path)
        ):
            errors.append("artifact")

    issue_step = next(
        (step for step in steps if step.get("uses") == "actions/github-script@v8"),
        {},
    )
    issue_with = issue_step.get("with")
    issue_script = issue_with.get("script") if isinstance(issue_with, dict) else None
    if issue_step.get("if") != "${{ steps.state.outputs.review == 'true' }}":
        errors.append("issue-gating")
    if not isinstance(issue_script, str):
        errors.append("issue-gating")
    else:
        required_issue_contract = (
            'fs.readFileSync("source-health/review.json", "utf8")',
            "if (!Array.isArray(review) || review.length === 0)",
            "matching.length > 1",
            "github.rest.issues.create",
            "github.rest.issues.update",
            'labels: [label]',
        )
        if any(token not in issue_script for token in required_issue_contract):
            errors.append("issue-gating")
        gate_position = issue_script.find("if (!Array.isArray(review) || review.length === 0)")
        mutation_positions = [
            issue_script.find("github.rest.issues.create"),
            issue_script.find("github.rest.issues.update"),
        ]
        if gate_position < 0 or any(position <= gate_position for position in mutation_positions):
            errors.append("issue-gating")
        forbidden_payload_fields = (
            "result.requested_domain",
            "result.final_domain",
            "result.redirect_domains",
            "result.content_type",
            "result.size_bytes",
            "http://",
            "https://",
            "priorBody",
            "unavailableMarker",
        )
        if any(token in issue_script for token in forbidden_payload_fields):
            errors.append("unsafe-issue-payload")
    if save and issue_step and steps.index(save) >= steps.index(issue_step):
        errors.append("cache-save-order")
    return errors


def _source_health_template_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(document) != {"name", "description", "title", "labels", "body"}:
        errors.append("template-shape")
    if document.get("labels") != ["source-health"]:
        errors.append("template-label")
    if document.get("title") != "[source-health] Manual official-root review":
        errors.append("template-title")
    body = document.get("body")
    if not isinstance(body, list) or not all(isinstance(item, dict) for item in body):
        return errors + ["template-body"]
    rendered = str(document).casefold()
    for phrase in (
        "non-authoritative",
        "manually verify",
        "personal student data",
        "must not update catalog facts",
        "must not be used as evidence or release approval",
    ):
        if phrase not in rendered:
            errors.append("template-safety-notice")
            break
    return errors


def _release_workflow_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    triggers = document.get("on")
    if triggers != {"push": {"tags": ["v*"]}}:
        errors.append("tag-trigger")
    if document.get("permissions") != {"contents": "write"}:
        errors.append("permissions")
    concurrency = document.get("concurrency")
    if not isinstance(concurrency, dict):
        errors.append("concurrency")
    else:
        if concurrency.get("group") != EXPECTED_RELEASE_CONCURRENCY_GROUP:
            errors.append("concurrency-group")
        if concurrency.get("cancel-in-progress") is not False:
            errors.append("concurrency")

    jobs = document.get("jobs")
    job = jobs.get("release") if isinstance(jobs, dict) and set(jobs) == {"release"} else None
    if not isinstance(job, dict):
        return errors + ["release-job"]
    if job.get("runs-on") != "ubuntu-latest":
        errors.append("runs-on")
    timeout = job.get("timeout-minutes")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 90:
        errors.append("timeout")
    steps = job.get("steps")
    if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
        return errors + ["steps"]
    if any("continue-on-error" in step or "if" in step for step in steps):
        errors.append("critical-step-bypass")
    expected_ids = [
        "checkout",
        "setup",
        "install",
        "metadata",
        "tests",
        "release_gate",
        "build",
        "checksum",
        "notes",
        "publish",
    ]
    if [step.get("id") for step in steps] != expected_ids:
        errors.append("step-order")
    by_id = {
        step.get("id"): step
        for step in steps
        if isinstance(step.get("id"), str)
    }
    actions = [step.get("uses") for step in steps if "uses" in step]
    if actions != ["actions/checkout@v7", "actions/setup-python@v7"]:
        errors.append("official-actions")
    checkout = next((step for step in steps if step.get("uses") == "actions/checkout@v7"), {})
    if checkout.get("with") != {"fetch-depth": 0, "persist-credentials": False}:
        errors.append("checkout-history")
    setup = next((step for step in steps if step.get("uses") == "actions/setup-python@v7"), {})
    if setup.get("with") != {"python-version": "3.10", "cache": "pip", "cache-dependency-path": "pyproject.toml"}:
        errors.append("supported-python")

    run_steps = [step for step in steps if "run" in step]
    rendered = "\n".join(str(step.get("run", "")) for step in run_steps)
    if any("${{" in str(step.get("run", "")) for step in run_steps):
        errors.append("expression-in-command")
    def contains_expression(value: object) -> bool:
        if isinstance(value, str):
            return "${{" in value
        if isinstance(value, dict):
            return any(contains_expression(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_expression(item) for item in value)
        return False

    expression_outside_env = any(
        contains_expression(value)
        for step in steps
        for key, value in step.items()
        if key != "env"
    ) or contains_expression(
        {key: value for key, value in document.items() if key != "jobs"}
    ) or contains_expression({key: value for key, value in job.items() if key != "steps"})
    if expression_outside_env:
        errors.append("expression-outside-step-env")

    expected_argv = {
        "install": ["python", "-m", "pip", "install", "-e", ".[all,test]"],
        "metadata": ["python", "scripts/build_release.py", "--verify-ref"],
        "tests": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
        "release_gate": [
            "python", "scripts/release_check.py", "--ci", "--expected-version",
            "$RELEASE_VERSION", "--tag", "$RELEASE_TAG",
        ],
        "build": [
            "python", "scripts/build_release.py", "--version", "$RELEASE_VERSION",
            "--output", "$OUTPUT_DIRECTORY",
        ],
        "checksum": ["sha256sum", "--check", "$CHECKSUM_FILE"],
        "notes": ["python", "scripts/build_release.py", "--extract-notes"],
        "publish": [
            "gh", "release", "create", "$RELEASE_TAG", "$ARCHIVE_PATH", "$CHECKSUM_PATH",
            "--verify-tag", "--title", "$RELEASE_TAG", "--notes-file", "$NOTES_PATH",
        ],
    }
    for step_id, argv in expected_argv.items():
        command = by_id.get(step_id, {}).get("run")
        try:
            actual = shlex.split(command, posix=True) if isinstance(command, str) and "\n" not in command else []
        except ValueError:
            actual = []
        if actual != argv:
            errors.append(f"command-{step_id}")

    expected_env = {
        "metadata": {
            "RELEASE_TAG": "${{ github.ref_name }}",
            "EXPECTED_COMMIT": "${{ github.sha }}",
        },
        "release_gate": {
            "RELEASE_VERSION": "${{ steps.metadata.outputs.version }}",
            "RELEASE_TAG": "${{ github.ref_name }}",
        },
        "build": {
            "RELEASE_VERSION": "${{ steps.metadata.outputs.version }}",
            "OUTPUT_DIRECTORY": "dist",
        },
        "checksum": {"CHECKSUM_FILE": "SHA256SUMS"},
        "notes": {
            "RELEASE_VERSION": "${{ steps.metadata.outputs.version }}",
            "CHANGELOG_PATH": "CHANGELOG.md",
            "NOTES_PATH": "dist/release-notes.md",
        },
        "publish": {
            "GH_TOKEN": "${{ github.token }}",
            "RELEASE_TAG": "${{ github.ref_name }}",
            "ARCHIVE_PATH": "dist/pathway-atlas-${{ steps.metadata.outputs.version }}.zip",
            "CHECKSUM_PATH": "dist/SHA256SUMS",
            "NOTES_PATH": "dist/release-notes.md",
        },
    }
    for step_id, environment in expected_env.items():
        if by_id.get(step_id, {}).get("env") != environment:
            errors.append(f"environment-{step_id}")

    lowered = rendered.casefold()
    if any(token in lowered for token in ("pypi", "twine", "--draft", "secrets.")):
        errors.append("forbidden-release-behavior")
    token_steps = [step.get("id") for step in steps if "GH_TOKEN" in step.get("env", {})]
    if token_steps != ["publish"]:
        errors.append("release-token")
    return errors


def _execute_release_notes_step(step: dict[str, Any]) -> tuple[int, str | None]:
    """Execute the workflow command against a real temporary changelog."""

    command = step.get("run")
    if not isinstance(command, str):
        return 2, None
    try:
        arguments = shlex.split(command, comments=True, posix=True)
    except ValueError:
        return 2, None
    if not arguments or arguments[0] != "python":
        return 2, None
    arguments[0] = sys.executable
    if len(arguments) > 1 and arguments[1] == "scripts/build_release.py":
        arguments[1] = str(ROOT / arguments[1])
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        changelog = root / "CHANGELOG.md"
        notes = root / "release-notes.md"
        changelog.write_text(
            "# Changes\n\n## v0.2.0 — Later\n\nLater.\n\n"
            "## v0.1.0 — Preview\n\nExact published notes.\n\n"
            "## v0.0.1 — Earlier\n\nEarlier.\n",
            encoding="utf-8",
        )
        environment = {
            **os.environ,
            "RELEASE_VERSION": "0.1.0",
            "CHANGELOG_PATH": str(changelog),
            "NOTES_PATH": str(notes),
        }
        completed = subprocess.run(
            arguments,
            cwd=root,
            check=False,
            capture_output=True,
            env=environment,
            timeout=30,
        )
        content = notes.read_text("utf-8") if notes.is_file() else None
        return completed.returncode, content


class WorkflowTest(unittest.TestCase):
    def load_ci(self) -> dict[str, Any]:
        self.assertTrue(CI_WORKFLOW.is_file(), ".github/workflows/ci.yml is missing")
        return _load_yaml_subset(CI_WORKFLOW.read_text(encoding="utf-8"))

    def load_source_health(self) -> dict[str, Any]:
        self.assertTrue(
            SOURCE_HEALTH_WORKFLOW.is_file(),
            ".github/workflows/source-health.yml is missing",
        )
        return _load_yaml_subset(SOURCE_HEALTH_WORKFLOW.read_text(encoding="utf-8"))

    def load_source_health_template(self) -> dict[str, Any]:
        self.assertTrue(
            SOURCE_HEALTH_TEMPLATE.is_file(),
            ".github/ISSUE_TEMPLATE/source-health.yml is missing",
        )
        return _load_yaml_subset(SOURCE_HEALTH_TEMPLATE.read_text(encoding="utf-8"))

    def load_release(self) -> dict[str, Any]:
        self.assertTrue(RELEASE_WORKFLOW.is_file(), ".github/workflows/release.yml is missing")
        return _load_yaml_subset(RELEASE_WORKFLOW.read_text(encoding="utf-8"))

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
                f"python scripts/release_check.py --ci --expected-version {PROJECT_VERSION}",
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

    def test_source_health_workflow_and_issue_template_are_strict_and_safe(self) -> None:
        self.assertEqual(_source_health_errors(self.load_source_health()), [])
        self.assertEqual(
            _source_health_template_errors(self.load_source_health_template()),
            [],
        )

    def test_source_health_schedule_permission_timeout_and_concurrency_mutations_fail(self) -> None:
        workflow = self.load_source_health()
        mutations = (
            ("triggers", lambda item: item["on"].pop("workflow_dispatch")),
            ("weekly-schedule", lambda item: item["on"]["schedule"][0].update(cron="* * * * *")),
            ("permissions", lambda item: item["permissions"].update(contents="write")),
            ("timeout", lambda item: item["jobs"]["monitor"].update({"timeout-minutes": 16})),
            ("concurrency-group", lambda item: item["concurrency"].update(group="fixed")),
            ("concurrency", lambda item: item["concurrency"].update({"cancel-in-progress": False})),
        )
        for expected, mutate in mutations:
            with self.subTest(expected=expected):
                changed = copy.deepcopy(workflow)
                mutate(changed)
                self.assertIn(expected, _source_health_errors(changed))

    def test_source_health_execution_and_artifact_mutations_fail_closed(self) -> None:
        workflow = self.load_source_health()
        steps = workflow["jobs"]["monitor"]["steps"]

        credentials = copy.deepcopy(workflow)
        credentials["jobs"]["monitor"]["steps"][0]["with"]["persist-credentials"] = True
        self.assertIn("checkout-credentials", _source_health_errors(credentials))

        masked = copy.deepcopy(workflow)
        masked["jobs"]["monitor"]["steps"][4]["continue-on-error"] = True
        self.assertIn("failure-masking", _source_health_errors(masked))

        direct_network = copy.deepcopy(workflow)
        direct_network["jobs"]["monitor"]["steps"].insert(3, {"run": "curl example.invalid"})
        self.assertIn("unsafe-command", _source_health_errors(direct_network))

        incomplete_catalog = copy.deepcopy(workflow)
        collector = next(
            step for step in incomplete_catalog["jobs"]["monitor"]["steps"]
            if step.get("id") == "collect"
        )
        collector["run"] = collector["run"].replace(
            "for alias in discovery.aliases",
            "for alias in discovery.aliases[:1]",
        )
        self.assertIn("catalog-coverage", _source_health_errors(incomplete_catalog))

        unbounded_catalog = copy.deepcopy(workflow)
        collector = next(
            step for step in unbounded_catalog["jobs"]["monitor"]["steps"]
            if step.get("id") == "collect"
        )
        collector["run"] = collector["run"].replace(
            "max_calls * PROCESS_TIMEOUT_SECONDS > COLLECTION_BUDGET_SECONDS",
            "False",
        )
        self.assertIn("catalog-coverage", _source_health_errors(unbounded_catalog))

        absolute_artifact = copy.deepcopy(workflow)
        upload = next(
            step for step in absolute_artifact["jobs"]["monitor"]["steps"]
            if step.get("uses") == "actions/upload-artifact@v6"
        )
        upload["with"]["path"] = "/tmp/results.json"
        self.assertIn("artifact", _source_health_errors(absolute_artifact))

        missing_save = copy.deepcopy(workflow)
        missing_save["jobs"]["monitor"]["steps"] = [
            step
            for step in missing_save["jobs"]["monitor"]["steps"]
            if step.get("uses") != "actions/cache/save@v4"
        ]
        self.assertIn("cache-save", _source_health_errors(missing_save))

        self.assertEqual(len(steps), 9)

    def test_source_health_issue_gating_and_template_notice_mutations_fail_closed(self) -> None:
        workflow = self.load_source_health()
        ungated = copy.deepcopy(workflow)
        issue_step = next(
            step for step in ungated["jobs"]["monitor"]["steps"]
            if step.get("uses") == "actions/github-script@v8"
        )
        issue_step["if"] = "${{ always() }}"
        self.assertIn("issue-gating", _source_health_errors(ungated))

        leaked_domain = copy.deepcopy(workflow)
        issue_step = next(
            step for step in leaked_domain["jobs"]["monitor"]["steps"]
            if step.get("uses") == "actions/github-script@v8"
        )
        issue_step["with"]["script"] += "\ncore.info(result.requested_domain);"
        self.assertIn("unsafe-issue-payload", _source_health_errors(leaked_domain))

        template = self.load_source_health_template()
        unsafe_template = copy.deepcopy(template)
        unsafe_template["body"] = []
        self.assertIn("template-safety-notice", _source_health_template_errors(unsafe_template))

        divergent_title = copy.deepcopy(template)
        divergent_title["title"] = "[source-health] Another thread"
        self.assertIn("template-title", _source_health_template_errors(divergent_title))

    def test_release_workflow_is_strict_annotated_tag_to_non_draft_artifacts(self) -> None:
        self.assertEqual(_release_workflow_errors(self.load_release()), [])

    def test_release_workflow_mutation_canaries_fail_closed(self) -> None:
        workflow = self.load_release()
        mutations = (
            ("tag-trigger", lambda item: item["on"]["push"].update(tags=["*"])),
            ("permissions", lambda item: item["permissions"].update(contents="read")),
            ("checkout-history", lambda item: item["jobs"]["release"]["steps"][0]["with"].update({"fetch-depth": 1})),
            ("concurrency", lambda item: item["concurrency"].update({"cancel-in-progress": True})),
        )
        for expected, mutate in mutations:
            with self.subTest(expected=expected):
                changed = copy.deepcopy(workflow)
                mutate(changed)
                self.assertIn(expected, _release_workflow_errors(changed))

        third_party = copy.deepcopy(workflow)
        third_party["jobs"]["release"]["steps"][0]["uses"] = "vendor/checkout@v7"
        self.assertIn("official-actions", _release_workflow_errors(third_party))

        draft = copy.deepcopy(workflow)
        publish = next(step for step in draft["jobs"]["release"]["steps"] if "gh release create" in str(step.get("run")))
        publish["run"] += " --draft"
        self.assertIn("forbidden-release-behavior", _release_workflow_errors(draft))

    def test_release_workflow_behavior_canaries_enforce_order_argv_and_token_seam(self) -> None:
        workflow = self.load_release()

        early_publish = copy.deepcopy(workflow)
        steps = early_publish["jobs"]["release"]["steps"]
        publish = steps.pop()
        steps.insert(4, publish)
        self.assertIn("step-order", _release_workflow_errors(early_publish))

        echoed_tests = copy.deepcopy(workflow)
        tests_step = next(
            step for step in echoed_tests["jobs"]["release"]["steps"]
            if step.get("id") == "tests"
        )
        tests_step["run"] = "echo python -m unittest discover -s tests -v"
        self.assertIn("command-tests", _release_workflow_errors(echoed_tests))

        interpolated = copy.deepcopy(workflow)
        gate = next(
            step for step in interpolated["jobs"]["release"]["steps"]
            if step.get("id") == "release_gate"
        )
        gate["run"] = 'python scripts/release_check.py --ci --expected-version "${{ steps.metadata.outputs.version }}"'
        self.assertIn("expression-in-command", _release_workflow_errors(interpolated))

        early_credentials = copy.deepcopy(workflow)
        metadata = next(
            step for step in early_credentials["jobs"]["release"]["steps"]
            if step.get("id") == "metadata"
        )
        token_key = "GH" + "_TOKEN"
        token_value = "${{" + " github.token }}"
        metadata.setdefault("env", {})[token_key] = token_value
        self.assertIn("release-token", _release_workflow_errors(early_credentials))

    def test_release_notes_step_executes_the_real_cli_and_cannot_be_faked_by_dead_code(self) -> None:
        workflow = self.load_release()
        notes = next(
            step for step in workflow["jobs"]["release"]["steps"]
            if step.get("id") == "notes"
        )

        exit_code, content = _execute_release_notes_step(notes)

        self.assertEqual(exit_code, 0)
        self.assertEqual(content, "Exact published notes.\n")

        mutations = (
            'python -c "raise SystemExit(0)" # python scripts/build_release.py --extract-notes',
            'python -c "import os; raise SystemExit(0); open(os.environ[\'NOTES_PATH\'], \'w\').write(\'unreachable\')"',
        )
        for command in mutations:
            with self.subTest(command=command):
                changed = copy.deepcopy(notes)
                changed["run"] = command
                changed_exit, changed_content = _execute_release_notes_step(changed)
                self.assertEqual(changed_exit, 0)
                self.assertIsNone(changed_content)


if __name__ == "__main__":
    unittest.main()
