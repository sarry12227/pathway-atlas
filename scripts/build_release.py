# -*- coding: utf-8 -*-
"""Build deterministic release artifacts from one immutable Git index tree."""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
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
    from .compliance_scan import (
        PolicyError,
        ReleasePolicy,
        canonical_repository_path,
        git_environment,
        git_replacement_refs,
        missing_required_release_paths,
        parse_policy_bytes,
        safe_tracked_file,
        validate_binary_release_manifest,
    )
except ImportError:  # pragma: no cover - direct script execution
    from compliance_scan import (
        PolicyError,
        ReleasePolicy,
        canonical_repository_path,
        git_environment,
        git_replacement_refs,
        missing_required_release_paths,
        parse_policy_bytes,
        safe_tracked_file,
        validate_binary_release_manifest,
    )


_VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}", re.ASCII)
_OBJECT_ID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.ASCII)
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
            ["git", "--no-replace-objects", *arguments], cwd=root, check=False, capture_output=True,
            env=git_environment(), timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildReleaseError("git snapshot failed") from error
    if completed.returncode != 0:
        raise BuildReleaseError("git snapshot failed")
    return completed.stdout


def _ensure_no_replacement_refs(root: Path) -> None:
    try:
        replacements = git_replacement_refs(root)
    except RuntimeError as error:
        raise BuildReleaseError("git replacement inventory failed") from error
    if replacements:
        raise BuildReleaseError("git replacement refs are forbidden")


def _canonical_path(value: str) -> str:
    try:
        canonical = canonical_repository_path(value, "release path")
    except PolicyError as error:
        raise BuildReleaseError("unsafe tracked path") from error
    if unicodedata.normalize("NFKC", canonical) != canonical:
        raise BuildReleaseError("unsafe tracked path")
    return canonical


def _tree_oid(root: Path) -> str:
    try:
        value = _run_git(root, ("write-tree",)).decode("ascii", errors="strict").strip()
    except UnicodeError as error:
        raise BuildReleaseError("git snapshot failed") from error
    if _OBJECT_ID_RE.fullmatch(value) is None:
        raise BuildReleaseError("git snapshot failed")
    return value


def _tree_blobs(root: Path, tree_oid: str) -> tuple[IndexBlob, ...]:
    records = _run_git(root, ("ls-tree", "-rz", "--full-tree", tree_oid, "--")).split(b"\x00")
    blobs: list[IndexBlob] = []
    identities: set[str] = set()
    try:
        for record in records:
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            raw_mode, raw_type, raw_object_id = metadata.split(b" ", 2)
            mode = raw_mode.decode("ascii", errors="strict")
            object_type = raw_type.decode("ascii", errors="strict")
            object_id = raw_object_id.decode("ascii", errors="strict")
            path = _canonical_path(raw_path.decode("utf-8", errors="strict"))
            if mode not in _ORDINARY_MODES or object_type != "blob" or _OBJECT_ID_RE.fullmatch(object_id) is None:
                raise BuildReleaseError("unsupported tracked entry")
            identity = path.casefold()
            if identity in identities:
                raise BuildReleaseError("ambiguous tracked path")
            identities.add(identity)
            blobs.append(IndexBlob(path=path, mode=mode, object_id=object_id))
    except (UnicodeError, ValueError) as error:
        raise BuildReleaseError("malformed git snapshot") from error
    if not blobs:
        raise BuildReleaseError("empty git snapshot")
    return tuple(sorted(blobs, key=lambda item: item.path))


def _blob_bytes(root: Path, object_id: str) -> bytes:
    return _run_git(root, ("cat-file", "blob", object_id))


def _snapshot_bytes(root: Path, blobs: Sequence[IndexBlob], path: str) -> bytes:
    match = next((blob for blob in blobs if blob.path == path), None)
    if match is None:
        raise BuildReleaseError("incomplete release snapshot")
    return _blob_bytes(root, match.object_id)


def _project_version(raw: bytes) -> str:
    try:
        payload = tomllib.loads(raw.decode("utf-8", errors="strict"))
        name = payload["project"]["name"]
        version = payload["project"]["version"]
    except (UnicodeError, KeyError, TypeError, ValueError) as error:
        raise BuildReleaseError("invalid project metadata") from error
    if name != "pathway-atlas" or not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise BuildReleaseError("invalid project metadata")
    return version


def _path_is_beneath(path: str, directory: str) -> bool:
    folded_path = path.casefold()
    folded_directory = directory.casefold()
    return folded_path == folded_directory or folded_path.startswith(folded_directory + "/")


def _is_forbidden_release_path(path: str, policy: ReleasePolicy) -> bool:
    folded_parts = tuple(part.casefold() for part in PurePosixPath(path).parts)
    name = folded_parts[-1]
    return (
        any(_path_is_beneath(path, directory) for directory in policy.forbidden_tracked_directories)
        or any(part in _CACHE_COMPONENTS or part.endswith(".egg-info") for part in folded_parts)
        or folded_parts[0] == ".git"
        or name == ".env"
        or name.startswith(".env.")
        or PurePosixPath(name).suffix in _SENSITIVE_SUFFIXES
    )


def _validate_release_contract(
    root: Path,
    blobs: Sequence[IndexBlob],
    policy: ReleasePolicy,
) -> None:
    paths = {blob.path for blob in blobs}
    if any(_is_forbidden_release_path(path, policy) for path in paths):
        raise BuildReleaseError("forbidden tracked release path")
    if missing_required_release_paths(policy, paths):
        raise BuildReleaseError("incomplete release snapshot")
    binary_validation = validate_binary_release_manifest(
        policy,
        blobs,
        lambda blob: _blob_bytes(root, blob.object_id),
    )
    if not binary_validation.ok:
        raise BuildReleaseError("invalid binary release manifest")


def _validate_worktree(root: Path, blobs: Sequence[IndexBlob]) -> None:
    for blob in blobs:
        _candidate, finding = safe_tracked_file(root, blob.path)
        if finding is not None:
            raise BuildReleaseError("worktree does not match release snapshot")
    try:
        completed = subprocess.run(
            ["git", "--no-replace-objects", "diff-files", "--quiet", "--"], cwd=root, check=False,
            capture_output=True, env=git_environment(), timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildReleaseError("worktree does not match release snapshot") from error
    if completed.returncode != 0:
        raise BuildReleaseError("worktree does not match release snapshot")


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _safe_output(root: Path, value: str) -> Path:
    try:
        canonical = canonical_repository_path(value, "output path")
    except PolicyError as error:
        raise BuildReleaseError("unsafe output path") from error
    output = root.joinpath(*PurePosixPath(canonical).parts)
    current = root
    try:
        metadata = current.lstat()
        if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError
        for part in PurePosixPath(canonical).parts[:-1]:
            current = current / part
            metadata = current.lstat()
            if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
                raise OSError
    except OSError as error:
        raise BuildReleaseError("unsafe output path") from error
    if output.exists() or output.is_symlink():
        raise BuildReleaseError("unsafe output path")
    return output


def _release_environment() -> dict[str, str]:
    blocked = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    return {
        name: value for name, value in git_environment().items()
        if not name.upper().startswith("PYTHON") and name.casefold() not in blocked
    }


def _run_release_check(root: Path, version: str) -> None:
    checker = root / "scripts" / "release_check.py"
    if not checker.is_file():
        raise BuildReleaseError("release check failed")
    try:
        completed = subprocess.run(
            [sys.executable, str(checker), "--root", str(root), "--expected-version", version, "--ci"],
            cwd=root, check=False, capture_output=True, env=_release_environment(), timeout=4800,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildReleaseError("release check failed") from error
    if completed.returncode != 0:
        identifiers: list[str] = []
        try:
            payload = json.loads(completed.stdout)
            results = payload.get("results", []) if isinstance(payload, dict) else []
            for result in results if isinstance(results, list) else []:
                if not isinstance(result, dict) or result.get("ok") is not False:
                    continue
                name = result.get("name")
                if isinstance(name, str) and re.fullmatch(r"[a-z_]{1,64}", name):
                    identifiers.append(name)
                details = result.get("details", [])
                for detail in details if isinstance(details, list) else []:
                    if isinstance(detail, str) and re.fullmatch(r"failed-test:[A-Za-z0-9_.]{1,256}", detail):
                        identifiers.append(detail)
        except (TypeError, ValueError):
            pass
        suffix = ": " + "; ".join(identifiers[:20]) if identifiers else ""
        raise BuildReleaseError("release check failed" + suffix)


def _write_archive(root: Path, destination: Path, blobs: Sequence[IndexBlob]) -> None:
    try:
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9, strict_timestamps=True) as archive:
            for blob in blobs:
                info = zipfile.ZipInfo(f"pathway-atlas/{blob.path}", date_time=_ZIP_TIMESTAMP)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.flag_bits |= 0x800
                permissions = 0o755 if blob.mode == "100755" else 0o644
                info.external_attr = (stat.S_IFREG | permissions) << 16
                archive.writestr(info, _blob_bytes(root, blob.object_id), compresslevel=9)
        destination.chmod(0o600)
        with destination.open("r+b") as handle:
            os.fsync(handle.fileno())
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
        raise BuildReleaseError("archive creation failed") from error


def _write_checksums(path: Path, archive_name: str, digest: str) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(f"{digest}  {archive_name}\n".encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise BuildReleaseError("checksum creation failed") from error


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_posix_library() -> object:
    return ctypes.CDLL(None, use_errno=True)


def _load_windows_library() -> object:
    try:
        return ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as error:
        raise BuildReleaseError("atomic publication unsupported") from error


def _linux_rename_noreplace(ready: Path, output: Path) -> None:
    try:
        function = getattr(_load_posix_library(), "renameat2")
    except (AttributeError, OSError) as error:
        raise BuildReleaseError("atomic publication unsupported") from error
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(-100, os.fsencode(ready), -100, os.fsencode(output), 1) == 0:
        return
    code = ctypes.get_errno()
    if code in {errno.ENOSYS, errno.EINVAL, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
        raise BuildReleaseError("atomic publication unsupported")
    raise BuildReleaseError("release publication failed")


def _macos_rename_exclusive(ready: Path, output: Path) -> None:
    try:
        function = getattr(_load_posix_library(), "renamex_np")
    except (AttributeError, OSError) as error:
        raise BuildReleaseError("atomic publication unsupported") from error
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(os.fsencode(ready), os.fsencode(output), 4) == 0:
        return
    code = ctypes.get_errno()
    if code in {errno.ENOSYS, errno.EINVAL, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
        raise BuildReleaseError("atomic publication unsupported")
    raise BuildReleaseError("release publication failed")


def _windows_rename_noreplace(ready: Path, output: Path) -> None:
    function = getattr(_load_windows_library(), "MoveFileExW", None)
    if function is None:
        raise BuildReleaseError("atomic publication unsupported")
    function.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(str(ready), str(output), 8):
        return
    code = ctypes.get_last_error()
    if code == 120:
        raise BuildReleaseError("atomic publication unsupported")
    raise BuildReleaseError("release publication failed")


def _atomic_publish_directory(ready: Path, output: Path) -> None:
    if sys.platform.startswith("linux"):
        _linux_rename_noreplace(ready, output)
    elif sys.platform == "darwin":
        _macos_rename_exclusive(ready, output)
    elif sys.platform == "win32":
        _windows_rename_noreplace(ready, output)
    else:
        raise BuildReleaseError("atomic publication unsupported")


def verify_release_ref(root: Path, tag: str, expected_commit: str, output: Path) -> str:
    """Verify one exact annotated release tag and emit its snapshot version."""

    try:
        root = root.resolve(strict=True)
    except OSError as error:
        raise BuildReleaseError("invalid repository root") from error
    if not isinstance(tag, str) or not tag.startswith("v") or _VERSION_RE.fullmatch(tag[1:]) is None:
        raise BuildReleaseError("invalid release tag")
    if not isinstance(expected_commit, str) or _OBJECT_ID_RE.fullmatch(expected_commit) is None:
        raise BuildReleaseError("invalid release commit")
    _ensure_no_replacement_refs(root)
    tree_oid = _tree_oid(root)
    blobs = _tree_blobs(root, tree_oid)
    version = _project_version(_snapshot_bytes(root, blobs, "pyproject.toml"))
    if tag != f"v{version}":
        raise BuildReleaseError("release tag version mismatch")
    try:
        object_type = _run_git(root, ("cat-file", "-t", tag)).decode("ascii", errors="strict").strip()
        target = _run_git(root, ("rev-list", "-n", "1", tag)).decode("ascii", errors="strict").strip()
        head = _run_git(root, ("rev-parse", "HEAD")).decode("ascii", errors="strict").strip()
    except UnicodeError as error:
        raise BuildReleaseError("release tag verification failed") from error
    if object_type != "tag" or target != expected_commit or head != expected_commit:
        raise BuildReleaseError("release tag verification failed")
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(f"version={version}\n".encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise BuildReleaseError("release metadata output failed") from error
    return version


def extract_release_notes(changelog: str, version: str) -> str:
    """Return the one nonempty changelog section for an exact release version."""

    if not isinstance(changelog, str) or _VERSION_RE.fullmatch(version) is None:
        raise BuildReleaseError("invalid release notes input")
    lines = changelog.splitlines()
    heading = f"## v{version} — "
    matches = [index for index, line in enumerate(lines) if line.startswith(heading)]
    if len(matches) != 1:
        raise BuildReleaseError("release notes section is not unique")
    start = matches[0]
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    notes = "\n".join(lines[start + 1 : end]).strip()
    if not notes:
        raise BuildReleaseError("release notes section is empty")
    return notes + "\n"


def _extract_release_notes_file(changelog_path: Path, notes_path: Path, version: str) -> None:
    try:
        raw = changelog_path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise BuildReleaseError("release notes input is too large")
        notes = extract_release_notes(raw.decode("utf-8", errors="strict"), version)
        descriptor = os.open(notes_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(notes.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
    except BuildReleaseError:
        raise
    except (OSError, UnicodeError) as error:
        raise BuildReleaseError("release notes extraction failed") from error


def build_release(root: Path, version: str, output: str) -> ReleaseArtifacts:
    try:
        root = root.resolve(strict=True)
    except OSError as error:
        raise BuildReleaseError("invalid repository root") from error
    if _VERSION_RE.fullmatch(version) is None:
        raise BuildReleaseError("invalid release version")

    _ensure_no_replacement_refs(root)
    tree_oid = _tree_oid(root)
    blobs = _tree_blobs(root, tree_oid)
    try:
        policy = parse_policy_bytes(_snapshot_bytes(root, blobs, "release-policy.json"))
    except PolicyError as error:
        raise BuildReleaseError("invalid release policy") from error
    project_version = _project_version(_snapshot_bytes(root, blobs, "pyproject.toml"))
    if project_version != version:
        raise BuildReleaseError("release version mismatch")
    _validate_release_contract(root, blobs, policy)
    _validate_worktree(root, blobs)
    output_path = _safe_output(root, output)

    _run_release_check(root, version)
    _ensure_no_replacement_refs(root)
    if _tree_oid(root) != tree_oid:
        raise BuildReleaseError("release snapshot changed during gate")
    _validate_worktree(root, blobs)
    output_path = _safe_output(root, output)

    archive_name = f"pathway-atlas-{version}.zip"
    checksum_name = "SHA256SUMS"
    ready: Path | None = None
    try:
        ready = Path(tempfile.mkdtemp(prefix=f".{output_path.name}.release-ready-", dir=output_path.parent))
        ready.chmod(0o700)
        archive = ready / archive_name
        checksums = ready / checksum_name
        _write_archive(root, archive, blobs)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        _write_checksums(checksums, archive_name, digest)
        _fsync_directory(ready)
        if _safe_output(root, output) != output_path:
            raise BuildReleaseError("unsafe output path")
        _ensure_no_replacement_refs(root)
        _atomic_publish_directory(ready, output_path)
        ready = None
        _fsync_directory(output_path.parent)
    except BuildReleaseError:
        raise
    except (OSError, UnicodeError) as error:
        raise BuildReleaseError("release publication failed") from error
    finally:
        if ready is not None:
            try:
                shutil.rmtree(ready)
            except OSError:
                pass
    return ReleaseArtifacts(output_path / archive_name, output_path / checksum_name)


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
    parser.add_argument("--version")
    parser.add_argument("--output")
    parser.add_argument("--verify-ref", action="store_true")
    parser.add_argument("--extract-notes", action="store_true")
    try:
        parsed = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
        if parsed.verify_ref and parsed.extract_notes:
            raise BuildReleaseError("invalid release command")
        if parsed.extract_notes:
            if parsed.version is not None or parsed.output is not None:
                raise BuildReleaseError("invalid release command")
            version = os.environ.get("RELEASE_VERSION")
            changelog_path = os.environ.get("CHANGELOG_PATH")
            notes_path = os.environ.get("NOTES_PATH")
            if version is None or changelog_path is None or notes_path is None:
                raise BuildReleaseError("missing release notes metadata")
            _extract_release_notes_file(Path(changelog_path), Path(notes_path), version)
            return 0
        if parsed.verify_ref:
            if parsed.version is not None or parsed.output is not None:
                raise BuildReleaseError("invalid release command")
            tag = os.environ.get("RELEASE_TAG")
            commit = os.environ.get("EXPECTED_COMMIT")
            metadata_output = os.environ.get("GITHUB_OUTPUT")
            if tag is None or commit is None or metadata_output is None:
                raise BuildReleaseError("missing release metadata")
            verify_release_ref(parsed.root, tag, commit, Path(metadata_output))
            return 0
        if parsed.version is None or parsed.output is None:
            raise BuildReleaseError("invalid release command")
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
