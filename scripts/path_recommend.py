# -*- coding: utf-8 -*-
"""Evidence- and policy-backed deterministic pathway evaluation.

Callers provide an explicit profile, accepted policy records, and optionally a
documented rank-adjustment model. Without that model no numeric target rank is
produced.
"""
import math
import re
import unicodedata
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any

if __package__:
    from .contracts import EvidenceStatus
else:
    from contracts import EvidenceStatus


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SUBJECT_MODES = frozenset({"3+1+2", "3+3"})
_PATHWAY_TYPES = frozenset(
    {
        "strong_foundation",
        "comprehensive_evaluation",
        "special_program",
        "public_funded_or_directed",
        "hong_kong_macao",
        "other",
    }
)
_EXACT_EVIDENCE_MINIMUMS = {
    EvidenceStatus.OFFICIAL: 1,
    EvidenceStatus.CORROBORATED: 2,
    EvidenceStatus.REFERENCE: 3,
}
_MODEL_METHODS = frozenset({"documented_rank_delta"})
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_LANDLINE = re.compile(r"(?<!\d)0(?:10|\d{3})\d{7,8}(?!\d)")
_PHONE_SEPARATORS = re.compile(r"[\s.\-·‐‑–—_]+")
_IDENTITY = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
_EMAIL = re.compile(r"(?i)(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_URL_OR_LOCAL_PATH = re.compile(
    r"(?i)(?:"
    r"https?://|file://|www\.[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:[\\/]|\b)|"
    r"\$(?:home|userprofile|appdata|codex_home)[\\/]|"
    r"%(?:home|userprofile|appdata|codex_home)%[\\/]|"
    r"(?:^|[\s(])\.\.?\\(?:[^\\\s]+\\)*[^\\\s]+|"
    r"[a-z]:[\\/]|\\\\[^\\\s]+[\\/]|//[^/\s]+/|~/|"
    r"/(?:home|users|tmp|var|etc|opt|srv|mnt|root)(?:/|$)|"
    r"/(?!/)(?:[a-z0-9._~-]+/)+[a-z0-9._~-]+|(?:^|\s)/[^/\s]+"
    r")"
)
_SCHEMELESS_DOMAIN = re.compile(
    r"(?i)(?<![\w@.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,}(?:[/:?#][^\s]*)?"
)
_PRIVATE_OUTPUT_MARKERS = (
    "姓名", "studentname", "wechat", "weixin", "微信", "手机号",
    "联系电话", "电话", "就读学校", "currentschool", "studentschool",
    "班级", "住址",
)
_PRIVATE_LABEL = re.compile(
    r"(?i)(?<!\w)(?:student\s+name|name|wechat|weixin|phone|telephone|"
    r"current\s+school|student\s+school|address|api[\s_-]*key|secret|token|"
    r"private[\s_-]*key)\s*[:=]"
)
_WECHAT_ID = re.compile(r"(?i)(?<![a-z0-9])wxid[_-][a-z0-9_-]+")
_STRUCTURED_SECRET = re.compile(
    r"(?i)(?<![a-z0-9])(?:"
    r"gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|"
    r"(?:akia|asia)[a-z0-9]{16}|"
    r"sk-(?:proj-)?[a-z0-9_-]{20,}|sk_(?:live|test)_[a-z0-9]{16,}|"
    r"glpat-[a-z0-9_-]{20,}|xox[baprs]-[a-z0-9-]{20,}|"
    r"aiza[a-z0-9_-]{30,}"
    r")"
)
_JWT_SECRET = re.compile(
    r"(?i)(?<![a-z0-9_-])eyj[a-z0-9_-]{8,}\.eyj[a-z0-9_-]{8,}\."
    r"[a-z0-9_-]{8,}(?![a-z0-9_-])"
)
_CHINESE_OUTPUT_CLAIMS = frozenset(
    {
        "保录",
        "保证录取",
        "包录",
        "确保录取",
        "录取概率",
        "录取几率",
        "录取成功率",
        "成功率",
        "百分比承诺",
        "投资回报",
        "投资收益",
        "收益率",
        "回报率",
        "预计收益",
        "预计回报",
        "承诺回报",
    }
)
_PROMISE_ERROR = "output text contains unsupported promise language"
_SOURCE_ID_PROMISE_ERROR = "source ID contains unsupported claim language"
_ENGLISH_CLAIM_PHRASES = (
    ("admission", "guarantee"),
    ("guarantee", "admission"),
    ("guaranteed", "admission"),
    ("admission", "guaranteed"),
    ("admission", "is", "guarantee"),
    ("admission", "is", "guaranteed"),
    ("admission", "rate"),
    ("rate", "admission"),
    ("rate", "of", "admission"),
    ("probability",),
    ("admission", "probability"),
    ("probability", "admission"),
    ("probability", "of", "admission"),
    ("admission", "chance"),
    ("chance", "admission"),
    ("chance", "of", "admission"),
    ("admission", "likelihood"),
    ("likelihood", "admission"),
    ("likelihood", "of", "admission"),
    ("success", "rate"),
    ("return", "on", "investment"),
    ("investment", "return"),
    ("r", "o", "i"),
)
_ENGLISH_CLAIM_COMPACT = frozenset(
    "".join(phrase) for phrase in _ENGLISH_CLAIM_PHRASES
)
_ROI_CLAIM_TOKENS = next(
    phrase for phrase in _ENGLISH_CLAIM_PHRASES if "".join(phrase) == "roi"
)
_ENGLISH_OUTPUT_COMPACT_CLAIMS = frozenset(
    claim for claim in _ENGLISH_CLAIM_COMPACT if claim != "roi"
)
_ROI_PATTERN_BODY = r"\s*".join(re.escape(token) for token in _ROI_CLAIM_TOKENS)
_ROI_OUTPUT_PATTERN = re.compile(
    rf"(?<![a-z0-9]){_ROI_PATTERN_BODY}(?=[0-9%]|[^a-z0-9]|$)"
)
_UNICODE_PERCENT_SIGNS = frozenset({"%", "\u066a", "\ufe6a", "\uff05"})
_CHINESE_NUMBER = r"[0-9零〇一二三四五六七八九十百千万两]+(?:\.[0-9]+)?"
_PERCENT_OF_FORM = rf"百分之{_CHINESE_NUMBER}"
_PERCENT_SYMBOL_FORM = r"[0-9]+(?:\.[0-9]+)?%"
_CHENG_FORM = rf"{_CHINESE_NUMBER}成"
_RATE_FORM = rf"(?:{_PERCENT_OF_FORM}|{_PERCENT_SYMBOL_FORM}|{_CHENG_FORM})"
_NUMERIC_ADMISSION_PATTERNS = (
    re.compile(rf"(?:预计|预估|预测)?录取(?:is)?{_RATE_FORM}"),
    re.compile(rf"{_RATE_FORM}录取(?:把握|概率|几率|可能性)?"),
    re.compile(rf"admission(?:is)?{_RATE_FORM}"),
    re.compile(rf"{_RATE_FORM}admission(?:rate|probability|chance|likelihood)?"),
)
_CHINESE_ADMISSION_RATE_TERMS = (
    "录取率",
    "录取比例",
    "录取概率",
    "录取几率",
    "录取可能性",
    "录取把握",
)
_CHINESE_REVERSE_ADMISSION_RATE_TERMS = (
    "比例录取",
    "概率录取",
    "几率录取",
    "可能性录取",
    "把握录取",
)
def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: _json_safe(item) for key, item in value.items()}
    if is_dataclass(value) and hasattr(value, "to_dict"):
        return _json_safe(value.to_dict())
    raise TypeError(f"Value of type {type(value).__name__} is not JSON serializable")


class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: _json_safe(getattr(self, item.name))
            for item in fields(self)
        }


def _strict_positive_int(value: Any, name: str, *, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _strict_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    return value


def _schema_integer(value: Any, name: str) -> int:
    """Normalize a Draft 2020-12 mathematical integer to Python ``int``."""

    if isinstance(value, bool):
        raise TypeError(f"{name} must be a JSON Schema integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    raise TypeError(f"{name} must be a JSON Schema integer")


def _text(value: Any, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be blank")
    if normalized != value:
        raise ValueError(f"{name} must not have surrounding whitespace")
    return normalized


def _normalize_claim_text(value: str) -> str:
    """Canonicalize claim text without merging distinct semantic tokens."""

    characters: list[str] = []
    for character in unicodedata.normalize("NFKC", value).casefold():
        try:
            characters.append(str(unicodedata.decimal(character)))
            continue
        except (TypeError, ValueError):
            pass
        if character in _UNICODE_PERCENT_SIGNS:
            characters.append("%")
        elif character.isalnum():
            characters.append(character)
        else:
            characters.append(" ")
    return " ".join("".join(characters).split())


def _compact_claim_text(normalized: str) -> str:
    return "".join(
        character for character in normalized
        if character.isalnum() or character == "%"
    )


def _contains_admission_rate_claim(compact: str) -> bool:
    if any(term in compact for term in _CHINESE_ADMISSION_RATE_TERMS):
        return True
    if any(term in compact for term in _CHINESE_REVERSE_ADMISSION_RATE_TERMS):
        return True
    return any(pattern.search(compact) for pattern in _NUMERIC_ADMISSION_PATTERNS)


def _validate_output_text(value: str) -> None:
    normalized = _normalize_claim_text(value)
    compact = _compact_claim_text(normalized)
    if (
        any(token in compact for token in _CHINESE_OUTPUT_CLAIMS)
        or any(token in compact for token in _ENGLISH_OUTPUT_COMPACT_CLAIMS)
        or _contains_admission_rate_claim(compact)
        or _ROI_OUTPUT_PATTERN.search(normalized) is not None
    ):
        raise ValueError(_PROMISE_ERROR)


def validate_public_output_text(value: str) -> None:
    """Reject PII, secrets, URLs, and local paths from persisted output text."""

    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKC", value).casefold()
        if unicodedata.category(character) != "Cf"
    )
    compact = re.sub(r"[\s_\-:：=]+", "", normalized)
    phone_view = _PHONE_SEPARATORS.sub("", normalized)
    if (
        _PHONE.search(phone_view)
        or _LANDLINE.search(phone_view)
        or _IDENTITY.search(normalized)
        or _EMAIL.search(normalized)
        or _URL_OR_LOCAL_PATH.search(normalized)
        or _SCHEMELESS_DOMAIN.search(normalized)
        or any(marker in compact for marker in _PRIVATE_OUTPUT_MARKERS)
        or _PRIVATE_LABEL.search(normalized)
        or _WECHAT_ID.search(normalized)
        or _STRUCTURED_SECRET.search(normalized)
        or _JWT_SECRET.search(normalized)
        or re.search(r"高三[（(]?\d+[)）]?班", compact)
    ):
        raise ValueError("output text contains private or non-public content")


def _validate_source_id_claim(value: str) -> None:
    segments = tuple(
        segment.casefold()
        for segment in re.split(r"[-._:]+", value)
        if segment
    )
    if any(segment in _ENGLISH_CLAIM_COMPACT for segment in segments):
        raise ValueError(_SOURCE_ID_PROMISE_ERROR)
    for phrase in _ENGLISH_CLAIM_PHRASES:
        width = len(phrase)
        if any(
            segments[index:index + width] == phrase
            for index in range(len(segments) - width + 1)
        ):
            raise ValueError(_SOURCE_ID_PROMISE_ERROR)


def _output_text(value: Any, name: str, *, optional: bool = False) -> str | None:
    normalized = _text(value, name, optional=optional)
    if normalized is not None:
        validate_public_output_text(normalized)
        _validate_output_text(normalized)
    return normalized


def _string_tuple(
    value: Any,
    name: str,
    *,
    allow_empty: bool = True,
    safe_ids: bool = False,
    sort: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be a collection of strings")
    try:
        items = tuple(value)
    except TypeError as error:
        raise TypeError(f"{name} must be a collection of strings") from error
    if not allow_empty and not items:
        raise ValueError(f"{name} must not be empty")
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise TypeError(f"{name} must contain only strings")
        stripped = item.strip()
        if not stripped:
            raise ValueError(f"{name} must not contain blank strings")
        if stripped != item:
            raise ValueError(f"{name} must not contain surrounding whitespace")
        if safe_ids and _SAFE_ID.fullmatch(stripped) is None:
            raise ValueError(f"{name} must use the public safe-ID syntax")
        normalized.append(stripped)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} must contain unique values")
    if sort:
        normalized.sort()
    return tuple(normalized)


def _output_string_tuple(
    value: Any, name: str, *, allow_empty: bool = True
) -> tuple[str, ...]:
    normalized = _string_tuple(value, name, allow_empty=allow_empty)
    for item in normalized:
        validate_public_output_text(item)
        _validate_output_text(item)
    return normalized


def _source_id_tuple(value: Any, name: str) -> tuple[str, ...]:
    normalized = _string_tuple(
        value,
        name,
        allow_empty=False,
        safe_ids=True,
        sort=True,
    )
    for item in normalized:
        _validate_source_id_claim(item)
    return normalized


def _status(value: Any, name: str = "evidence_status") -> EvidenceStatus:
    if isinstance(value, EvidenceStatus):
        return value
    if isinstance(value, str):
        try:
            return EvidenceStatus(value)
        except ValueError as error:
            raise ValueError(f"{name} is not a supported evidence status") from error
    raise TypeError(f"{name} must be an EvidenceStatus or status string")


def _pathway_type(value: Any, name: str = "pathway_type") -> str:
    normalized = _text(value, name)
    assert normalized is not None
    if normalized not in _PATHWAY_TYPES:
        raise ValueError(f"{name} is not a supported pathway type")
    return normalized


@dataclass(frozen=True)
class PathwayProfile(_Serializable):
    """Privacy-minimal inputs for deterministic pathway evaluation."""

    rank: int
    province: str
    subject_mode: str
    current_year: int
    eligibility_facts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rank", _strict_positive_int(self.rank, "rank"))
        object.__setattr__(self, "province", _output_text(self.province, "province"))
        object.__setattr__(self, "subject_mode", _text(self.subject_mode, "subject_mode"))
        if self.subject_mode not in _SUBJECT_MODES:
            raise ValueError("subject_mode must be 3+1+2 or 3+3")
        object.__setattr__(
            self,
            "current_year",
            _strict_positive_int(self.current_year, "current_year", minimum=2000),
        )
        if self.current_year > 2100:
            raise ValueError("current_year must not exceed 2100")
        object.__setattr__(
            self,
            "eligibility_facts",
            tuple(sorted(_output_string_tuple(self.eligibility_facts, "eligibility_facts"))),
        )


@dataclass(frozen=True)
class PathwayPolicy(_Serializable):
    """One accepted, year- and province-scoped pathway policy record."""

    policy_id: str
    pathway_type: str
    title: str
    institution: str
    province: str
    subject_mode: str
    valid_year: int | None
    eligibility_requirements: tuple[str, ...]
    disqualifying_facts: tuple[str, ...]
    professional_options: tuple[str, ...]
    training_arrangements: str | None
    transition_rules: str | None
    outcomes: str | None
    service_employment_obligations: str | None
    penalty_exit_rules: str | None
    fees_and_subsidies: str | None
    policy_source_ids: tuple[str, ...]
    evidence_status: EvidenceStatus
    calculation_basis: str

    def __post_init__(self) -> None:
        policy_id = _text(self.policy_id, "policy_id")
        assert policy_id is not None
        if _SAFE_ID.fullmatch(policy_id) is None:
            raise ValueError("policy_id must use the public safe-ID syntax")
        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "pathway_type", _pathway_type(self.pathway_type))
        for name in ("title", "institution", "province"):
            object.__setattr__(self, name, _output_text(getattr(self, name), name))
        object.__setattr__(self, "subject_mode", _text(self.subject_mode, "subject_mode"))
        if self.subject_mode not in _SUBJECT_MODES:
            raise ValueError("subject_mode must be 3+1+2 or 3+3")
        if self.valid_year is not None:
            valid_year = _schema_integer(self.valid_year, "valid_year")
            if valid_year < 2000:
                raise ValueError("valid_year must be at least 2000")
            if valid_year > 2100:
                raise ValueError("valid_year must not exceed 2100")
            object.__setattr__(self, "valid_year", valid_year)
        for name in ("eligibility_requirements", "disqualifying_facts"):
            object.__setattr__(
                self,
                name,
                tuple(sorted(_output_string_tuple(getattr(self, name), name))),
            )
        object.__setattr__(
            self,
            "professional_options",
            tuple(sorted(_output_string_tuple(self.professional_options, "professional_options"))),
        )
        for name in (
            "training_arrangements",
            "transition_rules",
            "outcomes",
            "service_employment_obligations",
            "penalty_exit_rules",
            "fees_and_subsidies",
        ):
            object.__setattr__(
                self,
                name,
                _output_text(getattr(self, name), name, optional=True),
            )
        object.__setattr__(
            self,
            "policy_source_ids",
            _source_id_tuple(self.policy_source_ids, "policy_source_ids"),
        )
        object.__setattr__(self, "evidence_status", _status(self.evidence_status))
        basis = _output_text(self.calculation_basis, "calculation_basis")
        assert basis is not None
        object.__setattr__(self, "calculation_basis", basis)


@dataclass(frozen=True)
class RankAdjustmentModel(_Serializable):
    """Explicit, evidence-backed rank transformation with bounded applicability."""

    model_id: str
    province: str
    subject_mode: str
    cohort_years: tuple[int, ...]
    source_ids: tuple[str, ...]
    evidence_status: EvidenceStatus
    method: str
    pathway_types: tuple[str, ...]
    applicability_rank_min: int
    applicability_rank_max: int
    score_table_rank_min: int
    score_table_rank_max: int
    rank_delta: int

    def __post_init__(self) -> None:
        model_id = _text(self.model_id, "model_id")
        assert model_id is not None
        if _SAFE_ID.fullmatch(model_id) is None:
            raise ValueError("model_id must use the public safe-ID syntax")
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "province", _output_text(self.province, "province"))
        object.__setattr__(self, "subject_mode", _text(self.subject_mode, "subject_mode"))
        method = _text(self.method, "method")
        if method not in _MODEL_METHODS:
            raise ValueError("unsupported model method")
        object.__setattr__(self, "method", method)
        if self.subject_mode not in _SUBJECT_MODES:
            raise ValueError("subject_mode must be 3+1+2 or 3+3")
        if isinstance(self.cohort_years, (str, bytes, bytearray)):
            raise TypeError("cohort_years must be a collection of integers")
        try:
            cohort_years = tuple(self.cohort_years)
        except TypeError as error:
            raise TypeError("cohort_years must be a collection of integers") from error
        if not cohort_years:
            raise ValueError("cohort_years must not be empty")
        for year in cohort_years:
            _strict_positive_int(year, "cohort_year", minimum=2000)
            if year > 2100:
                raise ValueError("cohort_year must not exceed 2100")
        if len(cohort_years) != len(set(cohort_years)):
            raise ValueError("cohort_years must be unique")
        object.__setattr__(self, "cohort_years", tuple(sorted(cohort_years)))
        object.__setattr__(
            self,
            "source_ids",
            _source_id_tuple(self.source_ids, "source_ids"),
        )
        object.__setattr__(self, "evidence_status", _status(self.evidence_status))
        pathway_types = _string_tuple(
            self.pathway_types, "pathway_types", allow_empty=False, sort=True
        )
        for item in pathway_types:
            _pathway_type(item, "pathway_types item")
        object.__setattr__(self, "pathway_types", pathway_types)
        for name in (
            "applicability_rank_min",
            "applicability_rank_max",
            "score_table_rank_min",
            "score_table_rank_max",
        ):
            object.__setattr__(
                self, name, _strict_positive_int(getattr(self, name), name)
            )
        if self.applicability_rank_min > self.applicability_rank_max:
            raise ValueError("applicability rank bounds are reversed")
        if self.score_table_rank_min > self.score_table_rank_max:
            raise ValueError("score-table rank bounds are reversed")
        object.__setattr__(self, "rank_delta", _strict_int(self.rank_delta, "rank_delta"))


@dataclass(frozen=True)
class PathwayItem(_Serializable):
    policy_id: str
    pathway_type: str
    title: str
    institution: str
    status: str
    eligibility: str
    missing_constraints: tuple[str, ...]
    professional_options: tuple[str, ...]
    training_arrangements: str | None
    transition_rules: str | None
    outcomes: str | None
    service_employment_obligations: str | None
    penalty_exit_rules: str | None
    fees_and_subsidies: str | None
    policy_source_ids: tuple[str, ...]
    evidence_status: EvidenceStatus
    calculation_basis: str
    target_rank: int | None = None

    def __post_init__(self) -> None:
        policy_id = _text(self.policy_id, "policy_id")
        assert policy_id is not None
        if _SAFE_ID.fullmatch(policy_id) is None:
            raise ValueError("policy_id must use the public safe-ID syntax")
        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "pathway_type", _pathway_type(self.pathway_type))
        for name in ("title", "institution", "calculation_basis"):
            object.__setattr__(self, name, _output_text(getattr(self, name), name))
        allowed_pairs = {
            "formal": "eligible",
            "pending_verification": "pending_verification",
            "excluded": "ineligible",
        }
        if self.status not in allowed_pairs:
            raise ValueError("status is not supported")
        if self.eligibility != allowed_pairs[self.status]:
            raise ValueError("eligibility is inconsistent with status")
        constraints = _output_string_tuple(
            self.missing_constraints, "missing_constraints"
        )
        if self.status == "formal" and constraints:
            raise ValueError("formal items cannot have missing constraints")
        if self.status != "formal" and not constraints:
            raise ValueError("non-formal items require an explicit constraint or reason")
        object.__setattr__(self, "missing_constraints", constraints)
        object.__setattr__(
            self,
            "professional_options",
            tuple(sorted(_output_string_tuple(self.professional_options, "professional_options"))),
        )
        for name in (
            "training_arrangements",
            "transition_rules",
            "outcomes",
            "service_employment_obligations",
            "penalty_exit_rules",
            "fees_and_subsidies",
        ):
            object.__setattr__(
                self,
                name,
                _output_text(getattr(self, name), name, optional=True),
            )
        complete_details = bool(self.professional_options) and all(
            getattr(self, name) is not None
            for name in (
                "training_arrangements",
                "transition_rules",
                "outcomes",
                "service_employment_obligations",
                "penalty_exit_rules",
                "fees_and_subsidies",
            )
        )
        if self.status == "formal" and not complete_details:
            raise ValueError("formal items require complete policy details")
        object.__setattr__(
            self,
            "policy_source_ids",
            _source_id_tuple(self.policy_source_ids, "policy_source_ids"),
        )
        object.__setattr__(self, "evidence_status", _status(self.evidence_status))
        if self.status == "formal":
            evidence_problem = _exact_evidence_problem(
                self.evidence_status, self.policy_source_ids, "政策"
            )
            if evidence_problem is not None:
                raise ValueError("formal items require accepted exact evidence")
        if self.target_rank is not None:
            object.__setattr__(
                self,
                "target_rank",
                _strict_positive_int(self.target_rank, "target_rank"),
            )
            if self.status != "formal":
                raise ValueError("only formal items may carry a target rank")


@dataclass(frozen=True)
class PathwayResult(_Serializable):
    """Policy evaluation result; numeric output is absent without a model."""

    items: tuple[PathwayItem, ...] = ()
    formal_shortlist: tuple[str, ...] = ()
    target_rank: int | None = None
    transformation: str | None = None
    model_source_ids: tuple[str, ...] = ()
    model_id: str | None = None
    model_method: str | None = None
    model_evidence_status: EvidenceStatus | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.items, (str, bytes, bytearray)):
            raise TypeError("items must be a collection of PathwayItem records")
        try:
            items = tuple(self.items)
        except TypeError as error:
            raise TypeError("items must be a collection of PathwayItem records") from error
        if not all(isinstance(item, PathwayItem) for item in items):
            raise TypeError("items must contain only PathwayItem records")
        if len({item.policy_id for item in items}) != len(items):
            raise ValueError("items must have unique policy IDs")
        items = tuple(sorted(items, key=lambda item: item.policy_id))
        object.__setattr__(self, "items", items)

        shortlist = _string_tuple(
            self.formal_shortlist,
            "formal_shortlist",
            safe_ids=True,
            sort=True,
        )
        expected = tuple(
            item.policy_id for item in items if item.status == "formal"
        )
        if shortlist != expected:
            raise ValueError("formal_shortlist must name every and only formal item")
        object.__setattr__(self, "formal_shortlist", shortlist)

        if self.target_rank is None:
            if self.transformation is not None:
                raise ValueError("transformation requires a target_rank")
            if any(item.target_rank is not None for item in items):
                raise ValueError("item target ranks require a result target_rank")
            if self.model_source_ids:
                raise ValueError("model_source_ids require a target_rank")
            if self.model_id is not None or self.model_method is not None or self.model_evidence_status is not None:
                raise ValueError("model metadata requires a target_rank")
        else:
            target_rank = _strict_positive_int(self.target_rank, "target_rank")
            object.__setattr__(self, "target_rank", target_rank)
            transformation = _output_text(self.transformation, "transformation")
            object.__setattr__(self, "transformation", transformation)
            if not any(item.status == "formal" for item in items):
                raise ValueError("target_rank requires at least one formal item")
            if any(
                item.status == "formal" and item.target_rank != target_rank
                for item in items
            ):
                raise ValueError("formal item target ranks must match the result")
        model_source_ids = (
            _source_id_tuple(self.model_source_ids, "model_source_ids")
            if self.model_source_ids else ()
        )
        if self.target_rank is not None and not model_source_ids:
            raise ValueError("target_rank requires model_source_ids")
        object.__setattr__(self, "model_source_ids", model_source_ids)
        if self.target_rank is not None:
            model_id = _text(self.model_id, "model_id")
            assert model_id is not None
            if _SAFE_ID.fullmatch(model_id) is None:
                raise ValueError("model_id must use the public safe-ID syntax")
            model_method = _text(self.model_method, "model_method")
            if model_method not in _MODEL_METHODS:
                raise ValueError("unsupported model method")
            model_status = _status(self.model_evidence_status, "model_evidence_status")
            if _exact_evidence_problem(model_status, model_source_ids, "位次模型") is not None:
                raise ValueError("target_rank requires exact sufficient model evidence")
            object.__setattr__(self, "model_id", model_id)
            object.__setattr__(self, "model_method", model_method)
            object.__setattr__(self, "model_evidence_status", model_status)
        object.__setattr__(
            self, "warnings", _output_string_tuple(self.warnings, "warnings")
        )


def evaluate_pathways(
    profile: PathwayProfile,
    policies: tuple[PathwayPolicy, ...],
    model: RankAdjustmentModel | None = None,
) -> PathwayResult:
    """Evaluate eligibility and, only when documented, a bounded rank model."""

    if not isinstance(profile, PathwayProfile):
        raise TypeError("profile must be a PathwayProfile")
    if isinstance(policies, (str, bytes, bytearray)):
        raise TypeError("policies must be a collection of PathwayPolicy records")
    try:
        records = tuple(policies)
    except TypeError as error:
        raise TypeError("policies must be a collection of PathwayPolicy records") from error
    if not all(isinstance(item, PathwayPolicy) for item in records):
        raise TypeError("policies must contain only PathwayPolicy records")
    if len({item.policy_id for item in records}) != len(records):
        raise ValueError("policy_id must be unique within one evaluation")
    records = tuple(sorted(records, key=lambda item: item.policy_id))
    if model is not None and not isinstance(model, RankAdjustmentModel):
        raise TypeError("model must be a RankAdjustmentModel or None")

    items = tuple(
        _evaluate_policy(profile, record, None, None)
        for record in records
    )
    formal_policy_ids = {
        item.policy_id for item in items if item.status == "formal"
    }
    formal_policies = tuple(
        record for record in records if record.policy_id in formal_policy_ids
    )
    warnings: list[str] = []
    target_rank: int | None = None
    transformation: str | None = None
    if model is None:
        warnings.append("未提供有依据的位次模型")
    else:
        model_problem = _model_problem(profile, formal_policies, model)
        if model_problem is not None:
            warnings.append(model_problem)
        else:
            raw_target = profile.rank + model.rank_delta
            target_rank = min(
                model.score_table_rank_max,
                max(model.score_table_rank_min, raw_target),
            )
            transformation = (
                f"模型 {model.model_id}：{model.method}；队列年份 "
                f"{','.join(str(year) for year in model.cohort_years)}；"
                f"{profile.rank} + "
                f"({model.rank_delta}) = {raw_target}；按一分一段位次域 "
                f"[{model.score_table_rank_min}, {model.score_table_rank_max}] "
                f"钳制为 {target_rank}"
            )
            if target_rank != raw_target:
                warnings.append("模型原始结果超出声明的一分一段位次域，已按边界钳制")
            items = tuple(
                _evaluate_policy(profile, record, target_rank, transformation)
                for record in records
            )
    return PathwayResult(
        items=items,
        formal_shortlist=tuple(
            item.policy_id for item in items if item.status == "formal"
        ),
        target_rank=target_rank,
        transformation=transformation,
        model_source_ids=model.source_ids if target_rank is not None else (),
        model_id=model.model_id if target_rank is not None else None,
        model_method=model.method if target_rank is not None else None,
        model_evidence_status=model.evidence_status if target_rank is not None else None,
        warnings=tuple(warnings),
    )


def _exact_evidence_problem(
    status: EvidenceStatus, source_ids: tuple[str, ...], noun: str
) -> str | None:
    minimum = _EXACT_EVIDENCE_MINIMUMS.get(status)
    if minimum is None:
        return f"{noun}证据状态不是可接受的精确状态"
    if len(source_ids) < minimum:
        return f"{noun}独立来源不足：{status.value} 至少需要 {minimum} 个来源"
    return None


def _model_problem(
    profile: PathwayProfile,
    policies: tuple[PathwayPolicy, ...],
    model: RankAdjustmentModel,
) -> str | None:
    evidence_problem = _exact_evidence_problem(
        model.evidence_status, model.source_ids, "位次模型"
    )
    if evidence_problem is not None:
        return evidence_problem
    if model.province != profile.province:
        return "位次模型省份与用户画像不匹配"
    if model.subject_mode != profile.subject_mode:
        return "位次模型选科模式与用户画像不匹配"
    if profile.current_year not in model.cohort_years:
        return "用户当前年份不在模型声明的队列年份中"
    if not (model.applicability_rank_min <= profile.rank <= model.applicability_rank_max):
        return "用户位次超出模型声明的适用范围"
    if not policies:
        return "无满足正式候选条件的政策，位次模型未执行"
    for record in policies:
        if record.province != model.province or record.subject_mode != model.subject_mode:
            return "政策记录与位次模型的省份或选科模式不匹配"
        if record.pathway_type not in model.pathway_types:
            return "政策路径类型不在位次模型声明的适用范围"
    return None


def _evaluate_policy(
    profile: PathwayProfile,
    policy: PathwayPolicy,
    target_rank: int | None,
    transformation: str | None,
) -> PathwayItem:
    missing: list[str] = []
    ineligible: list[str] = []
    if policy.province != profile.province:
        ineligible.append("政策省份与用户省份不匹配")
    if policy.subject_mode != profile.subject_mode:
        ineligible.append("政策选科模式与用户选科模式不匹配")
    matched_disqualifiers = sorted(
        set(policy.disqualifying_facts).intersection(profile.eligibility_facts)
    )
    ineligible.extend(f"命中排除条件：{item}" for item in matched_disqualifiers)

    missing.extend(
        item
        for item in policy.eligibility_requirements
        if item not in profile.eligibility_facts
    )
    critical_fields = (
        ("training_arrangements", "培养安排未核实"),
        ("transition_rules", "转段规则未核实"),
        ("outcomes", "毕业或升学出口未核实"),
        ("service_employment_obligations", "服务期或就业义务未核实"),
        ("penalty_exit_rules", "违约或退出规则未核实"),
        ("fees_and_subsidies", "费用或补助未核实"),
    )
    missing.extend(
        label for field_name, label in critical_fields if getattr(policy, field_name) is None
    )
    if not policy.professional_options:
        missing.append("专业选项未核实")
    if policy.valid_year is None:
        missing.append("政策有效年份未核实")
    elif policy.valid_year != profile.current_year:
        missing.append("政策不是当前年份有效版本")
    evidence_problem = _exact_evidence_problem(
        policy.evidence_status, policy.policy_source_ids, "政策"
    )
    if evidence_problem is not None:
        missing.append(evidence_problem)

    if ineligible:
        status = "excluded"
        eligibility = "ineligible"
        constraints = tuple(ineligible + missing)
    elif missing:
        status = "pending_verification"
        eligibility = "pending_verification"
        constraints = tuple(missing)
    else:
        status = "formal"
        eligibility = "eligible"
        constraints = ()
    basis = policy.calculation_basis
    item_target_rank = target_rank if status == "formal" else None
    if transformation is not None and status == "formal":
        basis = f"{basis}；{transformation}"
    else:
        basis = f"{basis}；未执行位次换算"
    return PathwayItem(
        policy_id=policy.policy_id,
        pathway_type=policy.pathway_type,
        title=policy.title,
        institution=policy.institution,
        status=status,
        eligibility=eligibility,
        missing_constraints=constraints,
        professional_options=policy.professional_options,
        training_arrangements=policy.training_arrangements,
        transition_rules=policy.transition_rules,
        outcomes=policy.outcomes,
        service_employment_obligations=policy.service_employment_obligations,
        penalty_exit_rules=policy.penalty_exit_rules,
        fees_and_subsidies=policy.fees_and_subsidies,
        policy_source_ids=policy.policy_source_ids,
        evidence_status=policy.evidence_status,
        calculation_basis=basis,
        target_rank=item_target_rank,
    )
