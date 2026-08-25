# -*- coding: utf-8 -*-
"""Build a deterministic release archive from the isolated Git index."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 workflow
    import tomli as tomllib

try:
    from .compliance_scan import git_environment
except ImportError:  # pragma: no cover - direct script execution
    from compliance_scan import git_environment


_VERSION_RE = re.compile(r"(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){2}")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_ORDINARY_MODES = frozenset({"100644", "100755"})
_CACHE_COMPONENTS = frozenset(
    {
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)
_FORBIDDEN_ROOTS = frozenset({".git", "output", "reports", "work"})
_SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pem", ".pfx"})
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class BuildReleaseError(RuntimeError):
    """A fixed, path-neutral release build failure."""


@dataclass(frozen=True)
class IndexBlob:
    path: str
    mode: str
    object_id: str


@dataclass(frozen=True)
class ReleaseArtifacts:
    archive: Path
    checksums: Path


def _run_git(root: Path, arguments: Sequence[str]) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            env=git_environment(),
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildReleaseError("git snapshot failed") from error
    if completed.returncode != 0:
        raise BuildReleaseError("git snapshot failed")
    return completed.stdout


def _canonical_path(value: str) -> str:
    if (
        not value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or unicodedata.normalize("NFKC", value) != value
    ):
        raise BuildReleaseError("unsafe tracked path")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or _DRIVE_RE.match(value)
        or pure.as_posix() != value
        or value.startswith("./")
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise BuildReleaseError("unsafe tracked path")
    return value


def _is_forbidden_release_path(value: str) -> bool:
    path = PurePosixPath(value)
    folded_parts = tuple(part.casefold() for part in path.parts)
    name = folded_parts[-1]
    return (
        folded_parts[0] in _FORBIDDEN_ROOTS
        or any(part in _CACHE_COMPONENTS or part.endswith(".egg-info") for part in folded_parts)
        or folded_parts[:2] == ("data", "hubei")
        or name == ".env"
        or name.startswith(".env.")
        or PurePosixPath(name).suffix in _SENSITIVE_SUFFIXES
    )


def _index_blobs(root: Path) -> tuple[IndexBlob, ...]:
    records = _run_git(root, ("ls-files", "--stage", "-z", "--")).split(b"\x00")
    blobs: list[IndexBlob] = []
    identities: set[str] = set()
    try:
        for record in records:
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            raw_mode, raw_object_id, raw_stage = metadata.split(b" ", 2)
            mode = raw_mode.decode("ascii", errors="strict")
            object_id = raw_object_id.decode("ascii", errors="strict")
            path = _canonical_path(raw_path.decode("utf-8", errors="strict"))
            if (
                mode not in _ORDINARY_MODES
                or raw_stage != b"0"
                or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", object_id) is None
            ):
                raise BuildReleaseError("unsupported tracked entry")
            identity = unicodedata.normalize("NFKC", path).casefold()
            if identity in identities:
                raise BuildReleaseError("ambiguous tracked path")
            identities.add(identity)
            if _is_forbidden_release_path(path):
                raise BuildReleaseError("forbidden tracked release path")
            blobs.append(IndexBlob(path=path, mode=mode, object_id=object_id))
    except (UnicodeError, ValueError) as error:
        raise BuildReleaseError("malformed git snapshot") from error
    if not blobs:
        raise BuildReleaseError("empty git snapshot")
    return tuple(sorted(blobs, key=lambda item: item.path))


def _project_version(root: Path) -> str:
    try:
        payload = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
        project = payload["project"]
        name = project["name"]
        version = project["version"]
    except (OSError, UnicodeError, KeyError, TypeError, ValueError) as error:
        raise BuildReleaseError("invalid project metadata") from error
    if name != "shengxue-skill" or not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise BuildReleaseError("invalid project metadata")
    return version


def _safe_output(root: Path, value: str) -> Path:
    canonical = _canonical_path(value)
    relative = PurePosixPath(canonical)
    output = root.joinpath(*relative.parts)
    try:
        root_resolved = root.resolve(strict=True)
        parent = output.parent.resolve(strict=True)
        parent.relative_to(root_resolved)
        metadata = output.parent.lstat()
    except (OSError, ValueError) as error:
        raise BuildReleaseError("unsafe output path") from error
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag) or output.exists():
        raise BuildReleaseError("unsafe output path")
    return output


def _release_environment() -> dict[str, str]:
    blocked = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    return {
        name: value
        for name, value in git_environment().items()
        if not name.upper().startswith("PYTHON") and name.casefold() not in blocked
    }


def _run_release_check(root: Path, version: str) -> None:
    checker = root / "scripts" / "release_check.py"
    if not checker.is_file():
        raise BuildReleaseError("release check failed")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(checker),
                "--root",
                str(root),
                "--expected-version",
                version,
                "--ci",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            env=_release_environment(),
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildReleaseError("release check failed") from error
    if completed.returncode != 0:
        raise BuildReleaseError("release check failed")


def _blob_bytes(root: Path, object_id: str) -> bytes:
    return _run_git(root, ("cat-file", "blob", object_id))


def _write_archive(root: Path, destination: Path, blobs: Sequence[IndexBlob]) -> None:
    try:
        with zipfile.ZipFile(
            destination,
            "x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for blob in blobs:
                info = zipfile.ZipInfo(f"shengxue-skill/{blob.path}", date_time=_ZIP_TIMESTAMP)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.flag_bits |= 0x800
                permissions = 0o755 if blob.mode == "100755" else 0o644
                info.external_attr = (stat.S_IFREG | permissions) << 16
                archive.writestr(info, _blob_bytes(root, blob.object_id), compresslevel=9)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
        raise BuildReleaseError("archive creation failed") from error


def build_release(root: Path, version: str, output: str) -> ReleaseArtifacts:
    root = root.absolute()
    if _VERSION_RE.fullmatch(version) is None:
        raise BuildReleaseError("invalid release version")
    _run_release_check(root, version)
    if _project_version(root) != version:
        raise BuildReleaseError("release version mismatch")
    output_path = _safe_output(root, output)
    blobs = _index_blobs(root)
    archive_name = f"shengxue-skill-{version}.zip"
    staging: Path | None = None
    try:
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{output_path.name}.release-tmp-",
                dir=output_path.parent,
            )
        )
        archive = staging / archive_name
        checksums = staging / "SHA256SUMS"
        _write_archive(root, archive, blobs)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksums.write_text(f"{digest}  {archive_name}\n", encoding="utf-8", newline="\n")
        staging.replace(output_path)
        staging = None
    except BuildReleaseError:
        raise
    except (OSError, UnicodeError) as error:
        raise BuildReleaseError("release publication failed") from error
    finally:
        if staging is not None:
            try:
                shutil.rmtree(staging)
            except OSError:
                pass
    return ReleaseArtifacts(output_path / archive_name, output_path / "SHA256SUMS")


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
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True)
    try:
        parsed = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
        artifacts = build_release(parsed.root, parsed.version, parsed.output)
    except BuildReleaseError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print("release build failed", file=sys.stderr)
        return 2
    print(artifacts.archive.name)
    print(artifacts.checksums.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
