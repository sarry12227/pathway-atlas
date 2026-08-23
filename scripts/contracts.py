"""Host-neutral contracts shared by retrieval and deterministic processing.

The contracts deliberately use only Python 3.10 standard-library features.  A
dataclass's ``to_dict`` method is the serialization seam used by the JSON
schemas and by evidence-bundle writers.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any
from uuid import uuid4


class SourceTier(str, Enum):
    """Reliability tier of a source publisher."""

    A = "A"
    B = "B"
    C = "C"


class EvidenceStatus(str, Enum):
    """Status assigned to a normalized fact."""

    OFFICIAL = "official"
    CORROBORATED = "corroborated"
    REFERENCE = "reference"
    INFERRED = "inferred"
    CONFLICT = "conflict"
    MISSING = "missing"
    MASKED = "masked"
    PARTIAL = "partial"


class CapabilityTier(str, Enum):
    """Execution tier selected by the environment preflight."""

    FULL = "full"
    STANDARD = "standard"
    OFFLINE = "offline"


def _json_safe(value: Any) -> Any:
    """Recursively convert contract values to values accepted by ``json``."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    return value


class _Serializable:
    """Mixin implementing stable recursive dataclass serialization."""

    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: _json_safe(getattr(self, item.name))
            for item in fields(self)
        }


@dataclass(frozen=True)
class SourceCandidate(_Serializable):
    source_id: str
    url: str
    publisher: str
    tier: SourceTier
    published_at: str | None
    retrieved_at: str
    content_hash: str
    citation_root: str
    summary: str


@dataclass(frozen=True)
class FactClaim(_Serializable):
    field: str
    value: Any
    unit: str | None
    source_id: str
    method: str


@dataclass(frozen=True)
class EvidenceFact(_Serializable):
    fact_id: str
    field: str
    value: Any
    unit: str | None
    status: EvidenceStatus
    source_ids: tuple[str, ...]
    method: str
    notes: str


@dataclass(frozen=True)
class CapabilityReport(_Serializable):
    """Machine-readable result of host capability preflight.

    The optional tuple fields intentionally default to empty tuples so a
    caller can construct a minimal report for an offline run while preserving
    one stable serialized shape for all tiers.
    """

    tier: CapabilityTier
    host_capabilities: tuple[str, ...] = ()
    available_capabilities: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    degradations: tuple[str, ...] = ()
    python_version: str = ""
    optional_modules: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceManifest(_Serializable):
    """Index for one reproducible evidence bundle."""

    schema_version: str = "1.0"
    session_id: str = field(default_factory=lambda: uuid4().hex)
    capability_tier: CapabilityTier = CapabilityTier.OFFLINE
    candidates_filename: str = "candidates.jsonl"
    facts_filename: str = "facts.jsonl"
    rejected_count: int = 0
    manifest_hash: str = ""


__all__ = [
    "CapabilityReport",
    "CapabilityTier",
    "EvidenceFact",
    "EvidenceManifest",
    "EvidenceStatus",
    "FactClaim",
    "SourceCandidate",
    "SourceTier",
]
