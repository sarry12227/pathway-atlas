"""Safe, deterministic persistence for one local evidence bundle.

``EvidenceStore.create`` is deliberately the sole session constructor.  It
accepts one existing absolute local workspace root and creates an unchosen
random directory below ``root/evidence``.  All later paths are derived from
that directory.

The manifest hash is ``sha256:`` plus SHA-256 of canonical UTF-8 JSON.  Its
input includes the schema version, complete capability report, rejected count,
and the exact canonical content of every persisted non-manifest record file.
It explicitly excludes the random session id and ``manifest_hash`` itself, so
equivalent bundles have equal hashes across sessions and insertion order.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from scripts.contracts import CapabilityReport, EvidenceFact, EvidenceManifest, SourceCandidate


class EvidenceError(Exception):
    """Base class for evidence-bundle failures."""


class EvidencePathError(EvidenceError):
    """Raised when a workspace or derived path is unsafe."""


class EvidencePrivacyError(EvidenceError):
    """Raised when evidence data contains a forbidden personal-data key."""


class EvidenceStateError(EvidenceError):
    """Raised when an operation violates the bundle lifecycle."""


_SCHEMA_VERSION = "1.0"
_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PII_KEYS = frozenset(
    {
        "name",
        "student_name",
        "phone",
        "mobile",
        "id_card",
        "address",
        "姓名",
        "学生姓名",
        "手机号",
        "身份证",
        "身份证号",
        "地址",
    }
)


def _canonical_json(value: Any) -> str:
    """Return one portable JSON representation, rejecting non-finite values."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _normalize_key(key: str) -> str:
    """Normalize spelling variants without turning compound keys into ``name``."""

    return re.sub(r"[\s-]+", "_", key.strip().lower())


def _reject_pii_keys(value: Any) -> None:
    """Reject forbidden keys recursively without including user values in errors."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and _normalize_key(key) in _PII_KEYS:
                raise EvidencePrivacyError("Personal-data keys are not allowed in evidence data")
            _reject_pii_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_pii_keys(item)


def _is_below(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


class EvidenceStore:
    """Mutable evidence collection which becomes immutable once finalized."""

    def __init__(self, root: Path, session_path: Path, capability_report: CapabilityReport):
        self._root = root
        self.session_path = session_path
        self._capability_report = capability_report
        self._candidates: dict[str, SourceCandidate] = {}
        self._facts: dict[str, EvidenceFact] = {}
        self._rejections: dict[str, str] = {}
        self._contexts: list[dict[str, Any]] = []
        self._manifest: EvidenceManifest | None = None

    @classmethod
    def create(cls, root: str | os.PathLike[str], capability_report: CapabilityReport) -> "EvidenceStore":
        """Create a session below one caller-selected, existing local directory."""

        if not isinstance(capability_report, CapabilityReport):
            raise TypeError("capability_report must be a CapabilityReport")
        try:
            requested_root = Path(root)
        except TypeError as error:
            raise EvidencePathError("Evidence workspace must be a local path") from error
        if not requested_root.is_absolute() or not requested_root.is_dir():
            raise EvidencePathError("Evidence workspace must be an existing absolute directory")
        try:
            resolved_root = requested_root.resolve(strict=True)
        except OSError as error:
            raise EvidencePathError("Evidence workspace could not be resolved") from error

        evidence_parent = resolved_root / "evidence"
        try:
            evidence_parent.mkdir()
            resolved_parent = evidence_parent.resolve(strict=True)
        except FileExistsError:
            resolved_parent = evidence_parent.resolve(strict=True)
        except OSError as error:
            raise EvidencePathError("Evidence workspace could not create session storage") from error
        if not resolved_parent.is_dir() or not _is_below(resolved_parent, resolved_root):
            raise EvidencePathError("Evidence session directory escaped the workspace")

        for _ in range(8):
            session_id = uuid4().hex
            candidate_session = resolved_parent / session_id
            try:
                candidate_session.mkdir()
            except FileExistsError:
                continue
            except OSError as error:
                raise EvidencePathError("Evidence workspace could not create a session") from error
            try:
                session_path = candidate_session.resolve(strict=True)
                if not _is_below(session_path, resolved_parent):
                    raise EvidencePathError("Evidence session directory escaped the workspace")
                (session_path / "raw").mkdir()
                (session_path / "normalized").mkdir()
                return cls(resolved_root, session_path, capability_report)
            except EvidencePathError:
                raise
            except OSError as error:
                raise EvidencePathError("Evidence workspace could not prepare session storage") from error
        raise EvidencePathError("Could not allocate a unique evidence session")

    def add_candidate(self, candidate: SourceCandidate) -> None:
        self._require_open()
        if not isinstance(candidate, SourceCandidate):
            raise TypeError("candidate must be a SourceCandidate")
        self._validate_source_id(candidate.source_id)
        _reject_pii_keys(candidate.to_dict())
        if candidate.source_id in self._candidates or candidate.source_id in self._rejections:
            raise EvidenceStateError("Duplicate evidence source id")
        self._candidates[candidate.source_id] = candidate

    def add_fact(self, fact: EvidenceFact) -> None:
        self._require_open()
        if not isinstance(fact, EvidenceFact):
            raise TypeError("fact must be an EvidenceFact")
        _reject_pii_keys(fact.to_dict())
        if fact.fact_id in self._facts:
            raise EvidenceStateError("Duplicate evidence fact id")
        unknown_sources = set(fact.source_ids).difference(self._candidates)
        if unknown_sources:
            raise EvidenceStateError("Evidence fact references an unregistered source")
        self._facts[fact.fact_id] = fact

    def reject_candidate(self, candidate: SourceCandidate | str, reason: str) -> None:
        """Record a source considered but rejected, keyed by its source ID."""

        self._require_open()
        if isinstance(candidate, SourceCandidate):
            _reject_pii_keys(candidate.to_dict())
            source_id = candidate.source_id
        elif isinstance(candidate, str):
            source_id = candidate
        else:
            raise TypeError("candidate must be a SourceCandidate or source id")
        self._validate_source_id(source_id)
        if not isinstance(reason, str):
            raise TypeError("rejection reason must be a string")
        if source_id in self._candidates or source_id in self._rejections:
            raise EvidenceStateError("Duplicate evidence source id")
        self._rejections[source_id] = reason

    def add_context(self, context: Mapping[str, Any]) -> None:
        self._require_open()
        if not isinstance(context, Mapping):
            raise TypeError("context must be a mapping")
        context_dict = dict(context)
        _reject_pii_keys(context_dict)
        # Validate serializability at the write boundary before changing state.
        _canonical_json(context_dict)
        self._contexts.append(context_dict)

    def raw_path_for(self, source_id: str) -> Path:
        """Return a generated per-source raw-download directory, never a caller path."""

        self._require_open()
        self._validate_source_id(source_id)
        if source_id not in self._candidates:
            raise EvidencePathError("Raw storage requires a registered source id")
        raw_root = (self.session_path / "raw").resolve(strict=True)
        if not _is_below(raw_root, self.session_path.resolve(strict=True)):
            raise EvidencePathError("Raw storage escaped the evidence session")
        raw_path = raw_root / source_id
        try:
            raw_path.mkdir(exist_ok=True)
            resolved_path = raw_path.resolve(strict=True)
        except OSError as error:
            raise EvidencePathError("Raw storage path could not be created") from error
        if not _is_below(resolved_path, raw_root):
            raise EvidencePathError("Raw storage path escaped the evidence session")
        return resolved_path

    def finalize(self) -> EvidenceManifest:
        """Persist deterministic artifacts and atomically publish the manifest last."""

        if self._manifest is not None:
            return self._manifest
        records = self._artifact_records()
        manifest = EvidenceManifest(
            schema_version=_SCHEMA_VERSION,
            session_id=self.session_path.name,
            capability_tier=self._capability_report.tier,
            candidates_filename="candidates.jsonl",
            facts_filename="normalized/facts.jsonl",
            rejected_count=len(self._rejections),
            manifest_hash=self._manifest_hash(records),
        )
        try:
            for relative_name, content in records.items():
                self._atomic_write(relative_name, content)
            self._atomic_write("manifest.json", _canonical_json(manifest.to_dict()) + "\n")
        except Exception:
            # The manifest is the publication marker.  It is only written last.
            raise
        self._manifest = manifest
        return manifest

    def _artifact_records(self) -> dict[str, str]:
        candidates = [self._candidates[source_id].to_dict() for source_id in sorted(self._candidates)]
        facts = []
        for fact_id in sorted(self._facts):
            fact = self._facts[fact_id].to_dict()
            # A fact's supporting sources form a set for provenance purposes;
            # persist them in stable order rather than caller insertion order.
            fact["source_ids"] = sorted(fact["source_ids"])
            facts.append(fact)
        rejections = [
            {"source_id": source_id, "reason": self._rejections[source_id]}
            for source_id in sorted(self._rejections)
        ]
        contexts = sorted(self._contexts, key=_canonical_json)
        return {
            "capability.json": _canonical_json(self._capability_report.to_dict()) + "\n",
            "candidates.jsonl": self._jsonl(candidates),
            "context.jsonl": self._jsonl(contexts),
            "normalized/facts.jsonl": self._jsonl(facts),
            "rejections.jsonl": self._jsonl(rejections),
        }

    @staticmethod
    def _jsonl(records: list[dict[str, Any]]) -> str:
        return "".join(_canonical_json(record) + "\n" for record in records)

    def _manifest_hash(self, records: Mapping[str, str]) -> str:
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "capability": self._capability_report.to_dict(),
            "rejected_count": len(self._rejections),
            "files": {name: records[name] for name in sorted(records)},
        }
        digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def _atomic_write(self, relative_name: str, content: str) -> None:
        destination = self._safe_session_path(relative_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def _safe_session_path(self, relative_name: str) -> Path:
        if Path(relative_name).is_absolute():
            raise EvidencePathError("Evidence artifact path must be relative")
        session = self.session_path.resolve(strict=True)
        destination = session / relative_name
        parent = destination.parent.resolve(strict=True)
        if not _is_below(parent, session):
            raise EvidencePathError("Evidence artifact path escaped the session")
        return destination

    def _require_open(self) -> None:
        if self._manifest is not None:
            raise EvidenceStateError("Evidence bundle is already finalized")

    @staticmethod
    def _validate_source_id(source_id: str) -> None:
        if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
            raise EvidencePathError("Evidence source id is unsafe")


__all__ = [
    "EvidenceError",
    "EvidencePathError",
    "EvidencePrivacyError",
    "EvidenceStateError",
    "EvidenceStore",
]
