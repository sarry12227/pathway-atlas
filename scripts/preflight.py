"""Deterministic runtime capability preflight.

Host tools are deliberately not discovered here.  An agent must pass the
capabilities it actually has (``search``, ``browse``, and ``vision``) so a
report cannot accidentally claim access to a host integration.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

if __package__ in (None, ""):
    # ``python scripts/preflight.py`` puts ``scripts/`` rather than the
    # repository root on sys.path.  Add the root only for this direct CLI
    # invocation; module execution already has the correct import path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.contracts import CapabilityReport, CapabilityTier


HOST_CAPABILITIES = ("search", "browse", "vision")
OPTIONAL_MODULES = ("docx", "openpyxl", "pdfplumber")
MINIMUM_PYTHON = (3, 10)


def _default_module_probe(module_name: str) -> bool:
    """Return whether *module_name* has an import spec.

    ``find_spec`` intentionally checks availability without importing or
    executing the optional package.  The exception handling also makes the
    probe safe for a malformed or partially installed distribution.
    """

    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _python_version() -> tuple[str, bool]:
    """Return a stable version string and whether this runtime is supported."""

    version_info = sys.version_info
    major = int(version_info[0])
    minor = int(version_info[1])
    micro = int(version_info[2]) if len(version_info) > 2 else 0
    return f"{major}.{minor}.{micro}", (major, minor) >= MINIMUM_PYTHON


def _degradation_messages(
    missing: Iterable[str], *, offline: bool, unsupported_python: bool
) -> tuple[str, ...]:
    messages = [f"missing capability: {name}" for name in sorted(missing)]
    if offline:
        messages.append("offline mode: search and browse are unavailable")
    if unsupported_python:
        messages.append("python<3.10: runtime is below the supported minimum")
    return tuple(messages)


def detect_capabilities(
    host_capabilities: set[str],
    module_probe: Callable[[str], bool] | None = None,
) -> CapabilityReport:
    """Detect optional parsers and select a full, standard, or offline tier.

    ``host_capabilities`` is an explicit declaration from the calling agent;
    this function never tries to infer network or vision access.  ``module_probe``
    is injectable so tests and hosts can provide a deterministic import check.
    """

    probe = _default_module_probe if module_probe is None else module_probe
    # Ignore unknown declarations rather than allowing them to affect tier
    # selection.  Sorting all serialized collections keeps output identical
    # across platforms and set iteration orders.
    declared = {name for name in host_capabilities if name in HOST_CAPABILITIES}
    host = tuple(sorted(declared))

    available_modules = tuple(
        sorted(name for name in OPTIONAL_MODULES if bool(probe(name)))
    )
    missing_modules = tuple(
        sorted(name for name in OPTIONAL_MODULES if name not in available_modules)
    )
    missing_host = tuple(sorted(set(HOST_CAPABILITIES) - declared))
    missing = tuple(sorted((*missing_host, *missing_modules)))
    python_version, supported_python = _python_version()
    if not supported_python:
        missing = tuple(sorted((*missing, "python>=3.10")))

    has_network = {"search", "browse"}.issubset(declared)
    has_parsers = not missing_modules
    has_vision = "vision" in declared
    if has_network and has_vision and has_parsers and supported_python:
        tier = CapabilityTier.FULL
    elif has_network and supported_python:
        tier = CapabilityTier.STANDARD
    else:
        tier = CapabilityTier.OFFLINE

    degradations = _degradation_messages(
        missing,
        offline=tier is CapabilityTier.OFFLINE,
        unsupported_python=not supported_python,
    )
    return CapabilityReport(
        tier=tier,
        host_capabilities=host,
        available_capabilities=host,
        missing_capabilities=missing,
        degradations=degradations,
        python_version=python_version,
        optional_modules=available_modules,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host-capability",
        action="append",
        default=[],
        metavar="NAME",
        help="explicit host capability (repeat for search, browse, or vision)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = detect_capabilities(set(args.host_capability))
    print(json.dumps(report.to_dict(), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
