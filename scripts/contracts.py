"""Host-neutral contracts shared by retrieval and deterministic processing.

The contracts deliberately use only Python 3.10 standard-library features.  A
dataclass's ``to_dict`` method is the serialization seam used by the JSON
schemas and by evidence-bundle writers.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
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
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, frozenset):
        if not all(isinstance(item, str) for item in value):
            raise TypeError("frozenset contract values must contain only strings")
        return sorted(value)
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        converted = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    "Value of type "
                    f"{type(key).__name__} is not a JSON object key"
                )
            converted[key] = _json_safe(item)
        return converted
    if is_dataclass(value) and hasattr(value, "to_dict"):
        return _json_safe(value.to_dict())
    raise TypeError(
        f"Value of type {type(value).__name__} is not JSON serializable"
    )


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


@dataclass(frozen=True)
class RecommendationProfile(_Serializable):
    """Normalized, privacy-minimal inputs for ordinary-batch matching."""

    rank: int
    target_province: str
    subject_group: str = ""
    secondary_subjects: frozenset[str] = field(default_factory=frozenset)
    target_major_categories: tuple[str, ...] = ()
    target_cities: tuple[str, ...] = ()
    target_schools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.secondary_subjects, str):
            raise TypeError("secondary_subjects must be a collection of strings")
        object.__setattr__(self, "secondary_subjects",
                           frozenset(self.secondary_subjects))
        for name in ("target_major_categories", "target_cities", "target_schools"):
            value = getattr(self, name)
            if isinstance(value, str):
                raise TypeError(f"{name} must be a collection of strings")
            object.__setattr__(self, name, tuple(value))


@dataclass(frozen=True)
class RecommendationMajorGroup(_Serializable):
    major_group_name: str
    major_group_code: str
    min_score: int
    min_rank: int
    majors: str


@dataclass(frozen=True)
class RecommendationItem(_Serializable):
    """One deterministic school recommendation with field-level provenance."""

    school_name: str
    school_level: str
    city: str
    school_province: str
    province_match: bool
    subject_match: bool
    min_score: int
    min_rank: int
    delta: int
    related_majors: str
    remarks: str
    major_groups: tuple[RecommendationMajorGroup, ...]
    match_reason: str
    recommend_level: str
    strategy: str
    data_year: int
    source_ids: tuple[str, ...]
    evidence_status: EvidenceStatus


@dataclass(frozen=True)
class RecommendationResult(_Serializable):
    """Immutable school-matching result with explicit evidence coverage."""

    items: tuple[RecommendationItem, ...] = ()
    excluded_by_subject_count: int = 0
    input_years: tuple[int, ...] = ()
    usable_years: tuple[int, ...] = ()
    verified_rank_coverage: tuple[int, int] | None = None
    coverage_status: EvidenceStatus = EvidenceStatus.MISSING
    empty_reason: str | None = None
    warnings: tuple[str, ...] = ()


__all__ = [
    "CapabilityReport",
    "CapabilityTier",
    "EvidenceFact",
    "EvidenceManifest",
    "EvidenceStatus",
    "FactClaim",
    "RecommendationItem",
    "RecommendationMajorGroup",
    "RecommendationProfile",
    "RecommendationResult",
    "SourceCandidate",
    "SourceTier",
]
