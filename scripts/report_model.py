"""Immutable evidence-aware report model and pure Markdown renderer.

This module is deliberately file- and network-free.  It snapshots only the
privacy-minimal public contracts emitted by the deterministic engine; report
renderers project those decisions and never recalculate them.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import re
from typing import Any, Iterable

if __package__:
    from .contracts import (
        CapabilityReport,
        CapabilityTier,
        EvidenceManifest,
        EvidenceStatus,
        RecommendationItem,
        RecommendationResult,
    )
    from .path_recommend import PathwayItem, PathwayResult
    from .rank_calc import RankEstimate
else:  # pragma: no cover - direct scripts-path compatibility
    from contracts import (
        CapabilityReport,
        CapabilityTier,
        EvidenceManifest,
        EvidenceStatus,
        RecommendationItem,
        RecommendationResult,
    )
    from path_recommend import PathwayItem, PathwayResult
    from rank_calc import RankEstimate


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z][a-z0-9_-]{0,127}$")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDENTITY = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
_EMAIL = re.compile(r"(?i)(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_URL_OR_PATH = re.compile(r"(?i)(?:https?://|file://|(?:^|\s)[A-Za-z]:[\\/])")
_PII_LABELS = ("手机号", "联系电话", "微信", "身份证", "姓名：", "姓名:", "班级")
_SUBJECT_MODES = frozenset({"3+1+2", "3+3"})
_GRADES = frozenset({"高一", "高二", "高三"})
_ACCEPTED_EXACT = frozenset(
    {
        EvidenceStatus.OFFICIAL,
        EvidenceStatus.CORROBORATED,
        EvidenceStatus.REFERENCE,
    }
)
_STATUS_PRECEDENCE = (
    EvidenceStatus.CONFLICT,
    EvidenceStatus.MASKED,
    EvidenceStatus.PARTIAL,
    EvidenceStatus.MISSING,
    EvidenceStatus.INFERRED,
    EvidenceStatus.REFERENCE,
    EvidenceStatus.CORROBORATED,
    EvidenceStatus.OFFICIAL,
)
_STATUS_LABEL = {
    EvidenceStatus.OFFICIAL: "官方",
    EvidenceStatus.CORROBORATED: "多源核验",
    EvidenceStatus.REFERENCE: "多源参考",
    EvidenceStatus.INFERRED: "推断",
    EvidenceStatus.CONFLICT: "冲突",
    EvidenceStatus.MISSING: "缺失",
    EvidenceStatus.MASKED: "屏蔽",
    EvidenceStatus.PARTIAL: "部分覆盖",
}
_TIER_LABEL = {
    CapabilityTier.FULL: "完整档",
    CapabilityTier.STANDARD: "标准档",
    CapabilityTier.OFFLINE: "离线档",
}
_CONFIDENCE_LABEL = {
    EvidenceStatus.OFFICIAL: "高",
    EvidenceStatus.CORROBORATED: "高",
    EvidenceStatus.REFERENCE: "中",
    EvidenceStatus.INFERRED: "中",
    EvidenceStatus.PARTIAL: "低",
    EvidenceStatus.MISSING: "无",
    EvidenceStatus.MASKED: "无",
    EvidenceStatus.CONFLICT: "无",
}
_QUERY_COVERAGE = {
    CapabilityTier.FULL: "联网检索、网页读取与视觉识别能力可用",
    CapabilityTier.STANDARD: "联网文本与结构化附件可用，图片表格可能未覆盖",
    CapabilityTier.OFFLINE: "仅使用本地或用户提供的已验证证据包",
}
_HOST_CAPABILITIES = frozenset({"search", "browse", "vision"})
_OPTIONAL_MODULES = frozenset({"docx", "openpyxl", "pdfplumber"})


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if is_dataclass(value) and hasattr(value, "to_dict"):
        return value.to_dict()
    raise TypeError(f"unsupported report value: {type(value).__name__}")


class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: _json_safe(getattr(self, item.name))
            for item in fields(self)
        }


def _positive_int(value: Any, name: str, *, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} is below its minimum")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    return _positive_int(value, name, minimum=0)


def _privacy_safe(value: str) -> bool:
    return not (
        _PHONE.search(value)
        or _IDENTITY.search(value)
        or _EMAIL.search(value)
        or _URL_OR_PATH.search(value)
        or any(label in value for label in _PII_LABELS)
    )


def _text(value: Any, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
    if len(value) > 2048 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must be a bounded single-line string")
    if not _privacy_safe(value):
        raise ValueError(f"{name} contains private or non-public content")
    return value


def _text_tuple(
    value: Any,
    name: str,
    *,
    safe_ids: bool = False,
    unique: bool = False,
    sort: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be a collection of strings")
    try:
        items = tuple(value)
    except TypeError as error:
        raise TypeError(f"{name} must be a collection of strings") from error
    normalized: list[str] = []
    for item in items:
        text = _text(item, f"{name} item")
        assert text is not None
        if safe_ids and _SAFE_ID.fullmatch(text) is None:
            raise ValueError(f"{name} contains an unsafe ID")
        normalized.append(text)
    if unique and len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} must contain unique values")
    if sort:
        normalized.sort()
    return tuple(normalized)


def _year_tuple(value: Any, name: str) -> tuple[int, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be a collection of years")
    try:
        years = tuple(value)
    except TypeError as error:
        raise TypeError(f"{name} must be a collection of years") from error
    for year in years:
        _positive_int(year, f"{name} item", minimum=2000)
        if year > 2100:
            raise ValueError(f"{name} item exceeds 2100")
    if len(years) != len(set(years)):
        raise ValueError(f"{name} must contain unique years")
    return tuple(sorted(years))


def _status(value: Any, name: str = "status") -> EvidenceStatus:
    if isinstance(value, EvidenceStatus):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{name} must be an EvidenceStatus")
    try:
        return EvidenceStatus(value)
    except ValueError as error:
        raise ValueError(f"{name} is unsupported") from error


def _capability_tier(value: Any) -> CapabilityTier:
    if isinstance(value, CapabilityTier):
        return value
    if not isinstance(value, str):
        raise TypeError("capability tier must be a CapabilityTier")
    try:
        return CapabilityTier(value)
    except ValueError as error:
        raise ValueError("unsupported capability tier") from error


def _aggregate_status(values: Iterable[EvidenceStatus]) -> EvidenceStatus:
    present = set(values)
    for item in _STATUS_PRECEDENCE:
        if item in present:
            return item
    return EvidenceStatus.MISSING


@dataclass(frozen=True)
class StudentProfile(_Serializable):
    """Privacy-minimal report profile; no name, contact, school, or class."""

    province: str
    subject_mode: str
    subject_group: str
    secondary_subjects: tuple[str, ...]
    rank: int
    grade: str
    current_year: int

    def __post_init__(self) -> None:
        for name in ("province", "subject_mode", "subject_group", "grade"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.subject_mode not in _SUBJECT_MODES:
            raise ValueError("subject_mode must be 3+1+2 or 3+3")
        if self.grade not in _GRADES:
            raise ValueError("grade must be 高一, 高二, or 高三")
        object.__setattr__(
            self,
            "secondary_subjects",
            _text_tuple(
                self.secondary_subjects,
                "secondary_subjects",
                unique=True,
                sort=True,
            ),
        )
        object.__setattr__(self, "rank", _positive_int(self.rank, "rank"))
        year = _positive_int(self.current_year, "current_year", minimum=2000)
        if year > 2100:
            raise ValueError("current_year must not exceed 2100")
        object.__setattr__(self, "current_year", year)


@dataclass(frozen=True)
class ReportRecommendation(_Serializable):
    strategy: str
    school_name: str
    school_level: str
    city: str
    min_score: int
    min_rank: int
    delta: int
    remarks: str
    match_reason: str
    data_year: int
    source_ids: tuple[str, ...]
    evidence_status: EvidenceStatus
    calculation_basis: str

    def __post_init__(self) -> None:
        if self.strategy not in {"冲", "稳", "保"}:
            raise ValueError("strategy must be 冲, 稳, or 保")
        for name in (
            "school_name",
            "school_level",
            "city",
            "remarks",
            "match_reason",
            "calculation_basis",
        ):
            value = getattr(self, name)
            if value == "" and name in {"school_level", "city", "remarks"}:
                continue
            object.__setattr__(self, name, _text(value, name))
        object.__setattr__(self, "min_score", _positive_int(self.min_score, "min_score"))
        object.__setattr__(self, "min_rank", _positive_int(self.min_rank, "min_rank"))
        if not isinstance(self.delta, int) or isinstance(self.delta, bool):
            raise TypeError("delta must be an integer")
        object.__setattr__(self, "data_year", _positive_int(self.data_year, "data_year", minimum=2000))
        if self.data_year > 2100:
            raise ValueError("data_year must not exceed 2100")
        object.__setattr__(
            self,
            "source_ids",
            _text_tuple(self.source_ids, "source_ids", safe_ids=True, unique=True, sort=True),
        )
        if not self.source_ids:
            raise ValueError("report recommendations require source IDs")
        object.__setattr__(self, "evidence_status", _status(self.evidence_status))
        if self.evidence_status not in _ACCEPTED_EXACT:
            raise ValueError("numeric recommendations require accepted exact evidence")


@dataclass(frozen=True)
class ReportPathway(_Serializable):
    policy_id: str
    pathway_type: str
    title: str
    institution: str
    status: str
    eligibility: str
    missing_constraints: tuple[str, ...]
    source_ids: tuple[str, ...]
    evidence_status: EvidenceStatus
    calculation_basis: str
    target_rank: int | None

    def __post_init__(self) -> None:
        for name in ("policy_id", "pathway_type", "title", "institution", "status", "eligibility", "calculation_basis"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if _SAFE_ID.fullmatch(self.policy_id) is None:
            raise ValueError("policy_id must use safe-ID syntax")
        if self.status not in {"formal", "pending_verification", "excluded"}:
            raise ValueError("unsupported pathway status")
        expected_eligibility = {
            "formal": "eligible",
            "pending_verification": "pending_verification",
            "excluded": "ineligible",
        }[self.status]
        if self.eligibility != expected_eligibility:
            raise ValueError("pathway status and eligibility are inconsistent")
        object.__setattr__(
            self,
            "missing_constraints",
            _text_tuple(self.missing_constraints, "missing_constraints"),
        )
        if self.status == "formal" and self.missing_constraints:
            raise ValueError("formal pathways cannot carry missing constraints")
        if self.status != "formal" and not self.missing_constraints:
            raise ValueError("non-formal pathways require an explicit reason")
        object.__setattr__(
            self,
            "source_ids",
            _text_tuple(self.source_ids, "source_ids", safe_ids=True, unique=True, sort=True),
        )
        if not self.source_ids:
            raise ValueError("pathways require policy source IDs")
        object.__setattr__(self, "evidence_status", _status(self.evidence_status))
        minimum_sources = {
            EvidenceStatus.OFFICIAL: 1,
            EvidenceStatus.CORROBORATED: 2,
            EvidenceStatus.REFERENCE: 3,
        }.get(self.evidence_status)
        if self.status == "formal" and (
            minimum_sources is None or len(self.source_ids) < minimum_sources
        ):
            raise ValueError("formal pathways require accepted exact evidence")
        if self.target_rank is not None:
            object.__setattr__(self, "target_rank", _positive_int(self.target_rank, "target_rank"))
            if self.status != "formal":
                raise ValueError("only formal pathways may carry a target rank")


@dataclass(frozen=True)
class ReportModel(_Serializable):
    profile: StudentProfile
    capability_tier: CapabilityTier
    query_coverage: str
    available_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    degradations: tuple[str, ...]
    recommendations: tuple[ReportRecommendation, ...]
    recommendation_coverage_status: EvidenceStatus
    verified_rank_coverage: tuple[int, int] | None
    recommendation_empty_reason: str | None
    recommendation_warnings: tuple[str, ...]
    excluded_by_subject_count: int
    zero_score_excluded_count: int
    input_years: tuple[int, ...]
    usable_years: tuple[int, ...]
    rank: RankEstimate | None
    pathways_available: bool
    pathways: tuple[ReportPathway, ...]
    pathway_warnings: tuple[str, ...]
    pathway_target_rank: int | None
    pathway_transformation: str | None
    model_source_ids: tuple[str, ...]
    manifest_session_id: str
    manifest_hash: str
    source_ids: tuple[str, ...]
    evidence_status: EvidenceStatus
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.profile, StudentProfile):
            raise TypeError("profile must be a StudentProfile")
        object.__setattr__(self, "profile", StudentProfile(**self.profile.to_dict()))
        tier = _capability_tier(self.capability_tier)
        object.__setattr__(self, "capability_tier", tier)
        expected_query = _QUERY_COVERAGE[tier]
        if self.query_coverage != expected_query:
            raise ValueError("query coverage must be derived from capability tier")
        object.__setattr__(self, "query_coverage", _text(self.query_coverage, "query_coverage"))
        for name in ("available_capabilities", "missing_capabilities", "degradations"):
            object.__setattr__(
                self,
                name,
                _text_tuple(getattr(self, name), name, unique=True, sort=True),
            )

        if isinstance(self.recommendations, (str, bytes, bytearray)):
            raise TypeError("recommendations must be a collection")
        try:
            recommendations = tuple(self.recommendations)
        except TypeError as error:
            raise TypeError("recommendations must be a collection") from error
        if not all(isinstance(item, ReportRecommendation) for item in recommendations):
            raise TypeError("recommendations must contain report recommendation records")
        if len({(item.school_name, item.strategy) for item in recommendations}) != len(recommendations):
            raise ValueError("recommendations must be unique by school and strategy")
        object.__setattr__(self, "recommendations", recommendations)
        coverage_status = _status(self.recommendation_coverage_status)
        object.__setattr__(self, "recommendation_coverage_status", coverage_status)
        if self.verified_rank_coverage is not None:
            if not isinstance(self.verified_rank_coverage, (tuple, list)) or len(self.verified_rank_coverage) != 2:
                raise TypeError("verified_rank_coverage must be a pair")
            lower = _positive_int(self.verified_rank_coverage[0], "coverage lower")
            upper = _positive_int(self.verified_rank_coverage[1], "coverage upper")
            if lower > upper:
                raise ValueError("verified rank coverage is reversed")
            object.__setattr__(self, "verified_rank_coverage", (lower, upper))
        if self.recommendation_empty_reason is not None:
            reason = _text(self.recommendation_empty_reason, "recommendation_empty_reason")
            assert reason is not None
            if _REASON.fullmatch(reason) is None:
                raise ValueError("recommendation_empty_reason must be a stable code")
            object.__setattr__(self, "recommendation_empty_reason", reason)
        object.__setattr__(
            self,
            "recommendation_warnings",
            _text_tuple(self.recommendation_warnings, "recommendation_warnings"),
        )
        for name in ("excluded_by_subject_count", "zero_score_excluded_count"):
            object.__setattr__(self, name, _nonnegative_int(getattr(self, name), name))
        object.__setattr__(self, "input_years", _year_tuple(self.input_years, "input_years"))
        object.__setattr__(self, "usable_years", _year_tuple(self.usable_years, "usable_years"))
        if set(self.usable_years).difference(self.input_years):
            raise ValueError("usable years must be a subset of input years")

        if self.rank is not None:
            if not isinstance(self.rank, RankEstimate):
                raise TypeError("rank must be a RankEstimate or None")
            object.__setattr__(self, "rank", RankEstimate(**self.rank.to_dict()))
        if not isinstance(self.pathways_available, bool):
            raise TypeError("pathways_available must be boolean")
        if isinstance(self.pathways, (str, bytes, bytearray)):
            raise TypeError("pathways must be a collection")
        try:
            pathways = tuple(self.pathways)
        except TypeError as error:
            raise TypeError("pathways must be a collection") from error
        if not all(isinstance(item, ReportPathway) for item in pathways):
            raise TypeError("pathways must contain report pathway records")
        if len({item.policy_id for item in pathways}) != len(pathways):
            raise ValueError("pathways must have unique policy IDs")
        if not self.pathways_available and pathways:
            raise ValueError("unavailable pathways cannot contain items")
        object.__setattr__(self, "pathways", pathways)
        object.__setattr__(self, "pathway_warnings", _text_tuple(self.pathway_warnings, "pathway_warnings"))
        if self.pathway_target_rank is not None:
            object.__setattr__(self, "pathway_target_rank", _positive_int(self.pathway_target_rank, "pathway_target_rank"))
        if self.pathway_transformation is not None:
            object.__setattr__(self, "pathway_transformation", _text(self.pathway_transformation, "pathway_transformation"))
            if self.pathway_target_rank is None:
                raise ValueError("pathway transformation requires a target rank")
        object.__setattr__(
            self,
            "model_source_ids",
            _text_tuple(self.model_source_ids, "model_source_ids", safe_ids=True, unique=True, sort=True),
        )
        if self.pathway_target_rank is not None and not self.model_source_ids:
            raise ValueError("pathway target rank requires model sources")
        formal_pathways = tuple(item for item in pathways if item.status == "formal")
        if self.pathway_target_rank is None:
            if self.pathway_transformation is not None or self.model_source_ids:
                raise ValueError("pathway model output requires a target rank")
            if any(item.target_rank is not None for item in pathways):
                raise ValueError("pathway item targets require a result target rank")
        else:
            if self.pathway_transformation is None or not formal_pathways:
                raise ValueError("pathway target rank requires a documented formal pathway")
            if any(item.target_rank != self.pathway_target_rank for item in formal_pathways):
                raise ValueError("formal pathway targets must match the result target")
        if not self.pathways_available and (
            self.pathway_warnings
            or self.pathway_target_rank is not None
            or self.pathway_transformation is not None
            or self.model_source_ids
        ):
            raise ValueError("unavailable pathways cannot carry pathway output")

        session = _text(self.manifest_session_id, "manifest_session_id")
        assert session is not None
        if _SAFE_ID.fullmatch(session) is None:
            raise ValueError("manifest session id is unsafe")
        object.__setattr__(self, "manifest_session_id", session)
        manifest_hash = _text(self.manifest_hash, "manifest_hash")
        assert manifest_hash is not None
        if _HASH.fullmatch(manifest_hash) is None:
            raise ValueError("manifest hash is invalid")
        object.__setattr__(self, "manifest_hash", manifest_hash)

        source_ids = _text_tuple(self.source_ids, "source_ids", safe_ids=True, unique=True, sort=True)
        expected_sources = {
            source_id
            for item in recommendations
            for source_id in item.source_ids
        }
        if self.rank is not None:
            expected_sources.update(self.rank.contributing_source_ids)
        for item in pathways:
            expected_sources.update(item.source_ids)
        expected_sources.update(self.model_source_ids)
        if source_ids != tuple(sorted(expected_sources)):
            raise ValueError("source_ids must be the exact cross-result source union")
        object.__setattr__(self, "source_ids", source_ids)

        statuses = [coverage_status]
        statuses.append(self.rank.status if self.rank is not None else EvidenceStatus.MISSING)
        if pathways:
            statuses.extend(item.evidence_status for item in pathways)
        else:
            statuses.append(EvidenceStatus.MISSING)
        expected_status = _aggregate_status(statuses)
        if _status(self.evidence_status) != expected_status:
            raise ValueError("evidence_status must follow conservative precedence")
        object.__setattr__(self, "evidence_status", expected_status)
        object.__setattr__(self, "warnings", _text_tuple(self.warnings, "warnings"))


def _snapshot_capability(capability: CapabilityReport) -> tuple[
    CapabilityTier, tuple[str, ...], tuple[str, ...], tuple[str, ...]
]:
    if not isinstance(capability, CapabilityReport):
        raise TypeError("capability must be a CapabilityReport")
    tier = _capability_tier(capability.tier)
    host = _text_tuple(capability.host_capabilities, "host_capabilities", unique=True, sort=True)
    available = _text_tuple(capability.available_capabilities, "available_capabilities", unique=True, sort=True)
    missing = _text_tuple(capability.missing_capabilities, "missing_capabilities", unique=True, sort=True)
    degradations = _text_tuple(capability.degradations, "degradations", unique=True, sort=True)
    optional = _text_tuple(capability.optional_modules, "optional_modules", unique=True, sort=True)
    version = _text(capability.python_version, "python_version")
    assert version is not None
    if set(host).difference(_HOST_CAPABILITIES) or available != host:
        raise ValueError("capability host declarations are inconsistent")
    if set(optional).difference(_OPTIONAL_MODULES):
        raise ValueError("capability optional-module declaration is unsupported")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        raise ValueError("capability python version is invalid")
    supported_python = (int(match.group(1)), int(match.group(2))) >= (3, 10)
    has_network = {"search", "browse"}.issubset(host)
    is_full = (
        has_network
        and "vision" in host
        and set(optional) == _OPTIONAL_MODULES
        and supported_python
    )
    expected_tier = (
        CapabilityTier.FULL
        if is_full
        else CapabilityTier.STANDARD
        if has_network and supported_python
        else CapabilityTier.OFFLINE
    )
    if tier is not expected_tier:
        raise ValueError("capability tier does not match the declared capabilities")
    return tier, available, missing, degradations


def _snapshot_manifest(manifest: EvidenceManifest, tier: CapabilityTier) -> tuple[str, str]:
    if not isinstance(manifest, EvidenceManifest):
        raise TypeError("manifest must be a validated EvidenceManifest")
    if manifest.schema_version != "1.0":
        raise ValueError("only evidence manifest schema 1.0 is supported")
    if _capability_tier(manifest.capability_tier) != tier:
        raise ValueError("manifest and capability tiers disagree")
    if manifest.candidates_filename != "candidates.jsonl" or manifest.facts_filename != "normalized/facts.jsonl":
        raise ValueError("manifest artifact names are not canonical")
    _nonnegative_int(manifest.rejected_count, "manifest rejected_count")
    session = _text(manifest.session_id, "manifest session_id")
    digest = _text(manifest.manifest_hash, "manifest hash")
    assert session is not None and digest is not None
    if _SAFE_ID.fullmatch(session) is None or _HASH.fullmatch(digest) is None:
        raise ValueError("manifest public identity is invalid")
    return session, digest


def _project_recommendation(item: RecommendationItem) -> ReportRecommendation:
    if not isinstance(item, RecommendationItem):
        raise TypeError("recommendation items must be RecommendationItem records")
    if not isinstance(item.province_match, bool) or not isinstance(item.subject_match, bool):
        raise TypeError("recommendation match flags must be boolean")
    if not item.subject_match:
        raise ValueError("rendered recommendations must pass subject filters")
    return ReportRecommendation(
        strategy=item.strategy,
        school_name=item.school_name,
        school_level=item.school_level,
        city=item.city,
        min_score=item.min_score,
        min_rank=item.min_rank,
        delta=item.delta,
        remarks=item.remarks,
        match_reason=item.match_reason,
        data_year=item.data_year,
        source_ids=item.source_ids,
        evidence_status=item.evidence_status,
        calculation_basis=f"{item.data_year} 年已验证投档记录；最低位次与用户位次差 Δ={item.delta:+d}",
    )


def _project_pathway(item: PathwayItem) -> ReportPathway:
    if not isinstance(item, PathwayItem):
        raise TypeError("pathway items must be PathwayItem records")
    return ReportPathway(
        policy_id=item.policy_id,
        pathway_type=item.pathway_type,
        title=item.title,
        institution=item.institution,
        status=item.status,
        eligibility=item.eligibility,
        missing_constraints=item.missing_constraints,
        source_ids=item.policy_source_ids,
        evidence_status=item.evidence_status,
        calculation_basis=item.calculation_basis,
        target_rank=item.target_rank,
    )


def build_report_model(
    profile: StudentProfile,
    capability: CapabilityReport,
    recommendations: RecommendationResult,
    rank: RankEstimate | None,
    pathways: PathwayResult | None,
    manifest: EvidenceManifest,
) -> ReportModel:
    """Snapshot validated engine decisions into one renderer-neutral model."""

    if not isinstance(profile, StudentProfile):
        raise TypeError("profile must be a StudentProfile")
    profile_snapshot = StudentProfile(**profile.to_dict())
    tier, available, missing, degradations = _snapshot_capability(capability)
    session_id, manifest_hash = _snapshot_manifest(manifest, tier)
    if not isinstance(recommendations, RecommendationResult):
        raise TypeError("recommendations must be a RecommendationResult")
    projected_recommendations = tuple(
        _project_recommendation(item) for item in tuple(recommendations.items)
    )
    recommendation_status = _status(recommendations.coverage_status, "recommendation coverage status")
    recommendation_warnings = _text_tuple(recommendations.warnings, "recommendation warnings")
    input_years = _year_tuple(recommendations.input_years, "recommendation input years")
    usable_years = _year_tuple(recommendations.usable_years, "recommendation usable years")
    if rank is not None:
        if not isinstance(rank, RankEstimate):
            raise TypeError("rank must be a RankEstimate or None")
        rank_snapshot = RankEstimate(**rank.to_dict())
    else:
        rank_snapshot = None

    if pathways is None:
        pathways_available = False
        projected_pathways: tuple[ReportPathway, ...] = ()
        pathway_warnings: tuple[str, ...] = ()
        pathway_target_rank = None
        pathway_transformation = None
        model_source_ids: tuple[str, ...] = ()
    else:
        if not isinstance(pathways, PathwayResult):
            raise TypeError("pathways must be a PathwayResult or None")
        pathways_available = True
        projected_pathways = tuple(_project_pathway(item) for item in tuple(pathways.items))
        pathway_warnings = _text_tuple(pathways.warnings, "pathway warnings")
        pathway_target_rank = pathways.target_rank
        pathway_transformation = pathways.transformation
        model_source_ids = _text_tuple(pathways.model_source_ids, "model source IDs", safe_ids=True, unique=True, sort=True)

    source_ids = {
        source_id
        for item in projected_recommendations
        for source_id in item.source_ids
    }
    if rank_snapshot is not None:
        source_ids.update(rank_snapshot.contributing_source_ids)
    for item in projected_pathways:
        source_ids.update(item.source_ids)
    source_ids.update(model_source_ids)
    statuses = [recommendation_status]
    statuses.append(rank_snapshot.status if rank_snapshot is not None else EvidenceStatus.MISSING)
    if projected_pathways:
        statuses.extend(item.evidence_status for item in projected_pathways)
    else:
        statuses.append(EvidenceStatus.MISSING)
    warnings = tuple(dict.fromkeys((*degradations, *recommendation_warnings, *pathway_warnings)))
    return ReportModel(
        profile=profile_snapshot,
        capability_tier=tier,
        query_coverage=_QUERY_COVERAGE[tier],
        available_capabilities=available,
        missing_capabilities=missing,
        degradations=degradations,
        recommendations=projected_recommendations,
        recommendation_coverage_status=recommendation_status,
        verified_rank_coverage=recommendations.verified_rank_coverage,
        recommendation_empty_reason=recommendations.empty_reason,
        recommendation_warnings=recommendation_warnings,
        excluded_by_subject_count=recommendations.excluded_by_subject_count,
        zero_score_excluded_count=recommendations.zero_score_excluded_count,
        input_years=input_years,
        usable_years=usable_years,
        rank=rank_snapshot,
        pathways_available=pathways_available,
        pathways=projected_pathways,
        pathway_warnings=pathway_warnings,
        pathway_target_rank=pathway_target_rank,
        pathway_transformation=pathway_transformation,
        model_source_ids=model_source_ids,
        manifest_session_id=session_id,
        manifest_hash=manifest_hash,
        source_ids=tuple(sorted(source_ids)),
        evidence_status=_aggregate_status(statuses),
        warnings=warnings,
    )


def _md(value: Any) -> str:
    text = str(value)
    replacements = (
        ("\\", "\\\\"),
        ("|", "\\|"),
        ("`", "\\`"),
        ("*", "\\*"),
        ("_", "\\_"),
        ("[", "\\["),
        ("]", "\\]"),
        ("<", "&lt;"),
        (">", "&gt;"),
    )
    for original, escaped in replacements:
        text = text.replace(original, escaped)
    return text


def _ids(values: tuple[str, ...]) -> str:
    return "、".join(_md(item) for item in values) if values else "无可公开来源编号"


def _table(headers: tuple[str, ...], rows: Iterable[tuple[Any, ...]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(_md(cell) for cell in row) + " |" for row in rows)
    return lines


def _empty_recommendation_text(model: ReportModel) -> str:
    reason = model.recommendation_empty_reason
    status = model.recommendation_coverage_status
    if reason == "no_match_within_verified_coverage" and status in _ACCEPTED_EXACT:
        return "经验证覆盖范围内未找到匹配院校；未硬凑冲稳保数量。"
    if reason == "no_match_within_verified_coverage":
        return "数据仅部分覆盖：当前已验证覆盖范围内未找到匹配院校，不能解释为没有符合院校。"
    if reason == "rank_outside_verified_coverage":
        return "用户位次超出已验证数据覆盖范围，未生成精确推荐。"
    if reason == "unusable_evidence":
        return "输入包含屏蔽、冲突或不可精确使用的证据，未生成数值边界。"
    if reason == "missing_verified_coverage":
        return "缺少可验证覆盖范围，未生成精确推荐。"
    return "当前证据未形成可展示的普通批推荐。"


def render_markdown(model: ReportModel) -> str:
    """Pure projection of a :class:`ReportModel`; no decision is recomputed."""

    if not isinstance(model, ReportModel):
        raise TypeError("model must be a ReportModel")
    profile = model.profile
    reminder = "> ⚠️ AI 生成，仅供参考；不构成录取承诺，最终以当年官方发布为准。"
    lines = [
        f"# 匿名升学规划报告（{_md(profile.province)}）",
        "",
        reminder,
        "",
        "## 一、输入与证据边界",
        "",
        f"- 年级：{_md(profile.grade)}",
        f"- 选科模式：{_md(profile.subject_mode)}；科目组：{_md(profile.subject_group)}；再选科目：{_ids(profile.secondary_subjects)}",
        f"- 用户提供省位次：{profile.rank}",
        f"- 能力档位：{_TIER_LABEL[model.capability_tier]}",
        f"- 查询覆盖：{_md(model.query_coverage)}",
        f"- 证据状态：{_STATUS_LABEL[model.evidence_status]}",
        f"- 证据置信度：{_CONFIDENCE_LABEL[model.evidence_status]}",
        f"- 数据覆盖：{_STATUS_LABEL[model.recommendation_coverage_status]}",
        f"- 证据包标识：{_md(model.manifest_session_id)}",
        f"- 清单哈希：{_md(model.manifest_hash)}",
        f"- 来源编号：{_ids(model.source_ids)}",
    ]
    if model.verified_rank_coverage is not None:
        lines.append(
            f"- 普通批已验证位次覆盖：{model.verified_rank_coverage[0]}–{model.verified_rank_coverage[1]}"
        )
    if model.warnings:
        lines.extend(["", "### 风险与缺失", ""])
        lines.extend(f"- {_md(item)}" for item in model.warnings)

    lines.extend(["", "## 二、成绩定位", ""])
    if model.rank is None:
        lines.append("喜报位次证据不足：本次直接采用用户提供的省位次，不执行校排名折算。")
    elif model.rank.status is EvidenceStatus.INFERRED:
        rank = model.rank
        lines.extend(
            [
                f"- 推断位次区间：{rank.lower_rank}–{rank.upper_rank}",
                f"- 区间中位描述：{rank.median_rank} 位（推断，不是官方位次）",
                f"- 容差：±{rank.tolerance_rank} 位",
                f"- 置信度：{_md(rank.confidence)}",
                f"- 计算依据：{_md(rank.method)}",
                f"- 贡献年份：{'、'.join(str(year) for year in rank.contributing_years)}",
                f"- 贡献锚点：{_ids(rank.contributing_anchor_ids)}",
                f"- 锚点来源编号：{_ids(rank.contributing_source_ids)}",
            ]
        )
    else:
        lines.append(
            f"喜报位次证据{_STATUS_LABEL[model.rank.status]}：{_md(model.rank.reason_code or '未形成可用区间')}；未输出代理数值。"
        )

    lines.extend(["", "## 三、普通批冲稳保", ""])
    if model.recommendations:
        lines.extend(
            _table(
                ("档位", "院校", "最低分", "最低位次", "证据状态", "来源编号", "计算依据"),
                (
                    (
                        item.strategy,
                        item.school_name,
                        item.min_score,
                        item.min_rank,
                        _STATUS_LABEL[item.evidence_status],
                        "、".join(item.source_ids),
                        item.calculation_basis,
                    )
                    for item in model.recommendations
                ),
            )
        )
    else:
        lines.append(_empty_recommendation_text(model))
    for warning in model.recommendation_warnings:
        lines.append(f"- 风险提示：{_md(warning)}")
    lines.extend(["", reminder, "", "## 四、多元升学路径", ""])
    if not model.pathways_available:
        lines.append("多元升学数据不足：未提供经验证的政策结果，本章节不作正式推荐。")
    elif not model.pathways:
        lines.append("多元升学数据不足：未形成正式或待核实路径；不套用无依据的位次修正。")
    else:
        lines.extend(
            _table(
                ("路径", "院校", "状态", "证据状态", "来源编号", "待核实约束", "计算依据"),
                (
                    (
                        item.title,
                        item.institution,
                        "正式候选" if item.status == "formal" else ("待核实" if item.status == "pending_verification" else "不符合"),
                        _STATUS_LABEL[item.evidence_status],
                        "、".join(item.source_ids),
                        "；".join(item.missing_constraints) or "无",
                        item.calculation_basis,
                    )
                    for item in model.pathways
                ),
            )
        )
    if model.pathway_target_rank is not None:
        lines.append(f"- 有依据的路径目标位次：{model.pathway_target_rank}")
    for warning in model.pathway_warnings:
        lines.append(f"- 风险提示：{_md(warning)}")

    lines.extend(
        [
            "",
            "## 五、证据清单与免责声明",
            "",
            f"- 来源编号：{_ids(model.source_ids)}",
            f"- 证据包清单哈希：{_md(model.manifest_hash)}",
            "- 报告只展示安全来源编号，不展示原始 URL 或本机路径。",
            "",
            reminder,
            "> 屏蔽值、冲突、部分覆盖与缺失数据均未被补成精确边界；请以省教育考试院和高校当年正式信息为准。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "ReportModel",
    "ReportPathway",
    "ReportRecommendation",
    "StudentProfile",
    "build_report_model",
    "render_markdown",
]
