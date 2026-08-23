# -*- coding: utf-8 -*-
"""M5 多元路径引擎（强基/综评/港澳）——从 shengxue-system/backend/app/
services/path_recommend.py 原样移植，规则逻辑一字未改，仅做三处接口适配：

1. 数据源由 sqlite3 连接改为内存 list[dict]（来自 data_loader 的 CSV 行），
   年份选择（各表 MAX(year)）与搜索区间过滤随之上移到 Python 代码，
   过滤条件与原 SQL 完全一致：rank BETWEEN max(1, ref+ΔLO) AND ref+ΔHI，
   ORDER BY rank ASC（位次为空的行不参与，同 SQL 中 NULL 不匹配 BETWEEN）；
2. AI 文案环节整体移除（spec §4.5 确定性原则）：描述字段只走
   「CSV 描述列 > 模板文案」，模板文案与现有系统 Null 降级路径逐字一致；
3. 等效位次修正值并入省份配置 province.json（equiv_rank_adjust，票 06）；
   缺省回退本模块常量 EQUIV_RANK_ADJUST（湖北经验值 4000）。

规则（与现有系统一致）：
- 三条路径统一等效位次 = 估算省排 − 4000，复用 M4 的 Δ 阈值与搜索区间
  （冲 Δ<−2000；稳 Δ∈[−2000,+2000]；保 Δ>+2000；Δ∈[−8000,+6000]），
  各档取 1 所（位次升序首个），留空不硬凑；
  （等效位次修正值、Δ 阈值、搜索区间均为省份可调参数，文中数值为湖北默认值）
- 强基：无奖项/活动 → 每条推荐标注"需补充背景材料"；所有强基推荐附
  限报三选一政策提示；
- 港澳：意愿"不考虑"或未填 → 整体不推 + skip_reason（两种原因分开表述）；
  "考虑/可了解"才推；
- 年级语义：高一/高二 → 规划建议；高三 → 当年申报（meta.grade_note）。
"""
import math
import re
import unicodedata
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Optional

if __package__:
    from .school_recommend import (  # noqa: F401
        DELTA_HI,
        DELTA_LO,
        _tier,
        _tier_threshold_labels,
        params_from_config,
    )
    from .contracts import EvidenceStatus
else:  # flat ``scripts`` path used by the legacy CLIs/tests
    from school_recommend import DELTA_HI, DELTA_LO, _tier  # noqa: F401
    from school_recommend import _tier_threshold_labels, params_from_config
    from contracts import EvidenceStatus

EQUIV_RANK_ADJUST = 4000  # 等效位次 = 估算省排 − 4000（湖北默认值；
                          # province.json 的 equiv_rank_adjust 可覆盖，见票 06）

POLICY_NOTE_QIANGJI = (
    "强基计划每生限报1所高校，以上冲/稳/保为候选清单，正式申报时三选一。")
BACKGROUND_NOTE = "需补充背景材料"
GANGAO_SKIP_REASON = "画像中港澳意愿为「不考虑」，为避免向无预算家庭推荐高学费院校，港澳路径不推荐。"
GANGAO_SKIP_REASON_UNFILLED = "画像未填港澳意愿，默认不推荐港澳路径。"
GRADE_NOTE_PLAN = "规划建议：当前为{grade}规划建议，还有时间补充背景材料，正式申报以高三当年招生政策为准。"
GRADE_NOTE_APPLY = "当年申报：高三当年申报路径，请以当年招生简章与报名截止时间为准。"
HKMO_POSITIVE = {"考虑", "可了解"}

# "无"类措辞词表（来源：shengxue-system/backend/app/services/parser.py NONE_WORDS）
NONE_WORDS = {"没有获奖", "没有", "还没有", "无获奖", "无获奖经历", "无",
              "暂时无", "暂无"}

_DISCLAIMER = ("以上推荐基于{year}年数据，次年招生政策可能有调整，"
               "以当年官方发布为准。")


class PathRecommendError(Exception):
    """多元路径推荐失败的业务错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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
_PROMISE_COMPACT_TOKENS = frozenset(
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
        "returnoninvestment",
        "investmentreturn",
        "admissionguarantee",
        "guaranteedadmission",
        "guaranteeadmission",
        "admissionisguaranteed",
        "admissionisguarantee",
        "successrate",
        "probability",
    }
)
_PROMISE_ERROR = "output text contains unsupported promise language"
_SOURCE_ID_PROMISE_ERROR = "source ID contains unsupported claim language"
_SOURCE_ID_CLAIM_TOKENS = frozenset(
    {
        "admissionguarantee",
        "guaranteedadmission",
        "successrate",
        "roi",
    }
)
_PERCENT_TRANSLATION = str.maketrans(
    {
        "\u066a": "%",  # Arabic percent sign
        "\ufe6a": "%",  # small percent sign
        "\uff05": "%",  # full-width percent sign (also handled by NFKC)
    }
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


def _claim_normal_forms(value: str) -> tuple[str, str, str]:
    """Return punctuation-insensitive and percent-aware claim text forms."""

    normalized = (
        unicodedata.normalize("NFKC", value)
        .casefold()
        .translate(_PERCENT_TRANSLATION)
    )
    compact = "".join(character for character in normalized if character.isalnum())
    claim_stream = "".join(
        character for character in normalized
        if character.isalnum() or character == "%"
    )
    return normalized, compact, claim_stream


def _validate_output_text(value: str) -> None:
    normalized, compact, claim_stream = _claim_normal_forms(value)
    chinese_admission_rate = re.search(
        r"(?:预计)?录取(?:百分之)?[0-9零一二三四五六七八九十百两.]+(?:%|成)",
        claim_stream,
    )
    english_roi = re.search(
        r"(?<![a-z0-9])r[\W_]*o[\W_]*i(?![a-z0-9])",
        normalized,
    )
    if (
        any(token in compact for token in _PROMISE_COMPACT_TOKENS)
        or chinese_admission_rate is not None
        or english_roi is not None
    ):
        raise ValueError(_PROMISE_ERROR)


def _validate_source_id_claim(value: str) -> None:
    _, compact, _ = _claim_normal_forms(value)
    if any(token in compact for token in _SOURCE_ID_CLAIM_TOKENS):
        raise ValueError(_SOURCE_ID_PROMISE_ERROR)


def _output_text(value: Any, name: str, *, optional: bool = False) -> str | None:
    normalized = _text(value, name, optional=optional)
    if normalized is not None:
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
        for name in ("province", "subject_mode"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
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
            _string_tuple(self.eligibility_facts, "eligibility_facts", sort=True),
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
        for name in (
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
        ("service_employment_obligations", "服务期或就业义务未核实"),
        ("penalty_exit_rules", "违约或退出规则未核实"),
        ("fees_and_subsidies", "费用或补助未核实"),
    )
    missing.extend(
        label for field_name, label in critical_fields if getattr(policy, field_name) is None
    )
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
        policy_source_ids=policy.policy_source_ids,
        evidence_status=policy.evidence_status,
        calculation_basis=basis,
        target_rank=item_target_rank,
    )


def equiv_adjust_from_config(config: Optional[dict] = None) -> int:
    """Legacy config bridge; remove after Tasks 6/9 migrate and before v0.2."""
    return int((config or {}).get("equiv_rank_adjust", EQUIV_RANK_ADJUST))


def _latest_year(rows: list[dict]) -> Optional[int]:
    years = [r["year"] for r in rows if r.get("year") is not None]
    return max(years) if years else None


def _rows_in_range(rows: list[dict], rank_col: str, year: int,
                   ref: int, p: Optional[dict] = None) -> list[dict]:
    """原 SQL：WHERE year=? AND rank BETWEEN max(1, ref+ΔLO) AND ref+ΔHI
    ORDER BY rank ASC（位次为空的行同 SQL NULL 语义，不匹配）。"""
    p = p or params_from_config()
    lo, hi = max(1, ref + p["delta_lo"]), ref + p["delta_hi"]
    return sorted(
        (r for r in rows
         if r["year"] == year and r[rank_col] is not None
         and lo <= r[rank_col] <= hi),
        key=lambda r: r[rank_col])


def _pick_tiers(rows: list[dict], rank_col: str, ref: int,
                p: Optional[dict] = None) -> dict:
    """按 Δ 阈值分档，各档取 1 所（位次升序首个）；无符合院校留空（AC10）。"""
    p = p or params_from_config()
    tiers = {"冲": [], "稳": [], "保": []}
    for r in rows:
        t = _tier(r[rank_col] - ref, p["chong_lt"], p["wen_le"])
        if not tiers[t]:
            tiers[t] = [r]
    return tiers


def _has_experience(text) -> bool:
    """奖项/活动文本 → 是否有经历（"无"/"暂无"等视为无）。"""
    t = (text or "").strip() if isinstance(text, str) else ""
    return bool(t) and t not in NONE_WORDS


def _grade_note(grade: Optional[str]) -> str:
    if grade in ("高一", "高二"):
        return GRADE_NOTE_PLAN.format(grade=grade)
    if grade == "高三":
        return GRADE_NOTE_APPLY
    return ""


def _apply_descriptions(entry: dict, db_strength=None, db_career=None,
                        db_transfer=None):
    """组装描述字段：CSV 描述列 > 模板文案（与现有系统 Null 降级逐字一致）。
    只写描述，不碰数据字段；无任何 AI 生成（spec §4.5）。"""
    name, major = entry["school_name"], entry.get("major_name") or "招生专业"
    entry["discipline_strength"] = (
        db_strength or f"{name}相关学科实力详见院校官方介绍。")
    entry["research_direction"] = (
        f"科研方向围绕{major}展开，详见院校官方介绍。")
    if "transfer_direction" in entry:
        entry["transfer_direction"] = (
            db_transfer or f"转段方向以{name}当年强基计划培养方案为准。")
    entry["career_prospect"] = (
        db_career or f"就业方向详见{name}官方就业质量报告。")


def _qiangji_entry(row: dict, ref: int, need_background: bool, p: dict) -> dict:
    delta = row["min_admission_rank"] - ref
    entry = {
        "school_name": row["school_name"],
        "major_name": row["major_name"],
        "major_category": row["major_category"],
        "enrollment_plan": row["enrollment_plan"],
        "min_admission_score": row["min_admission_score"],
        "min_admission_rank": row["min_admission_rank"],
        "delta": delta,
        "exam_method": row["exam_method"],
        "exam_subjects": row["exam_subjects"],
        "interview_focus": row["interview_focus"],
        "transfer_direction": row["transfer_direction"],
        "school_level": row["school_level"],
        "apply_deadline": row["apply_deadline"],
        "notes": row.get("notes") or "",  # 数据口径/来源（推断标注，随推荐展示）
        "strategy": _tier(delta, p["chong_lt"], p["wen_le"]),
        "_db_strength": row["research_strength"],  # CSV 描述列，优先于模板
        "_db_career": row["career_prospect"],
    }
    if need_background:
        entry["background_note"] = BACKGROUND_NOTE
    return entry


def _zongping_entry(row: dict, ref: int, p: dict) -> dict:
    delta = row["min_admission_rank"] - ref
    return {
        "school_name": row["school_name"],
        "major_name": row["recommended_majors"],
        "province_location": row["province_location"],
        "apply_condition": row["apply_condition"],
        "score_ratio": row["score_ratio"],
        "min_admission_score": row["min_admission_score"],
        "min_admission_rank": row["min_admission_rank"],
        "delta": delta,
        "exam_method": row["exam_method"],
        "recommended_majors": row["recommended_majors"],
        "application_window": row["application_window"],
        "notes": row.get("notes") or "",  # 数据口径/来源（推断标注，随推荐展示）
        "strategy": _tier(delta, p["chong_lt"], p["wen_le"]),
    }


def _gangao_entry(row: dict, ref: int, p: dict) -> dict:
    delta = row["estimated_rank"] - ref
    return {
        "school_name": row["school_name"],
        "major_name": row["majors_offered"],
        "region": row["region"],
        "school_level_cn": row["school_level_cn"],
        "majors_offered": row["majors_offered"],
        "language_requirement": row["language_requirement"],
        "estimated_score": row["estimated_score"],
        "estimated_rank": row["estimated_rank"],
        "delta": delta,
        "tuition_fee": row["tuition_fee"],
        "scholarship_info": row["scholarship_info"],
        "application_window": row["application_window"],
        "interview_required": bool(row["interview_required"]),
        "notes": row.get("notes") or "",  # 数据口径/来源（推断标注，随推荐展示）
        "strategy": _tier(delta, p["chong_lt"], p["wen_le"]),
    }


def recommend_paths(qiangji_rows: list[dict], zongping_rows: list[dict],
                    gangao_rows: Optional[list[dict]], *,
                    estimated_prov_rank,
                    grade: Optional[str] = None,
                    hkmo_willingness: Optional[str] = None,
                    awards: Optional[str] = None,
                    activities: Optional[str] = None,
                    has_awards: Optional[bool] = None,
                    has_activities: Optional[bool] = None,
                    params: Optional[dict] = None,
                    equiv_rank_adjust: Optional[int] = None) -> dict:
    """Deprecated one-release adapter; remove after Tasks 6/9 and before v0.2.

    M5 主入口。输入为三张路径表的 CSV 行（全部年份），
    输出三路径冲/稳/保 + meta（结构与现有系统一致）。

    params 为 school_recommend.params_from_config(province_config) 的结果，
    equiv_rank_adjust 为 equiv_adjust_from_config(province_config) 的结果；
    两者缺省（None）时使用湖北默认参数，行为与配置整合前完全一致。"""
    if estimated_prov_rank is None or int(estimated_prov_rank) < 1:
        raise PathRecommendError("REC_001", "参考位次缺失或超出合理范围，请先完成折算")
    rank = int(estimated_prov_rank)
    p = params or params_from_config()
    adjust = EQUIV_RANK_ADJUST if equiv_rank_adjust is None else int(equiv_rank_adjust)
    ref = rank - adjust  # 等效位次（三条路径统一修正）
    if has_awards is None:
        has_awards = _has_experience(awards)
    if has_activities is None:
        has_activities = _has_experience(activities)
    need_background = not (has_awards or has_activities)

    table_rows = {"qiangji": qiangji_rows, "zongping": zongping_rows,
                  "gangao": gangao_rows or []}
    years = {t: _latest_year(table_rows[t]) for t in table_rows}

    # Step 1/2：强基、综评（无表数据时整体留空，不硬凑）
    result = {}
    for path, rank_col, build in (
            ("qiangji", "min_admission_rank",
             lambda r: _qiangji_entry(r, ref, need_background, p)),
            ("zongping", "min_admission_rank",
             lambda r: _zongping_entry(r, ref, p))):
        tiers = {"冲": [], "稳": [], "保": []}
        if years[path] is not None:
            rows = _rows_in_range(table_rows[path], rank_col, years[path], ref, p)
            for tier, rows_t in _pick_tiers(rows, rank_col, ref, p).items():
                if rows_t:
                    tiers[tier] = [build(rows_t[0])]
        result[path] = tiers

    result["qiangji"]["policy_note"] = POLICY_NOTE_QIANGJI

    # Step 3：港澳（仅"考虑/可了解"才推，spec AC3）
    gangao = {"冲": [], "稳": [], "保": []}
    if hkmo_willingness in HKMO_POSITIVE and years["gangao"] is not None:
        rows = _rows_in_range(table_rows["gangao"], "estimated_rank",
                              years["gangao"], ref, p)
        for tier, rows_t in _pick_tiers(rows, "estimated_rank", ref, p).items():
            if rows_t:
                gangao[tier] = [_gangao_entry(rows_t[0], ref, p)]
    elif hkmo_willingness not in HKMO_POSITIVE:
        gangao["skip_reason"] = (GANGAO_SKIP_REASON_UNFILLED
                                 if hkmo_willingness is None else GANGAO_SKIP_REASON)
    result["gangao"] = gangao

    # Step 4：描述字段（CSV 描述列 > 模板文案；无 AI 环节）
    for path in ("qiangji", "zongping", "gangao"):
        for tier in ("冲", "稳", "保"):
            for e in result[path][tier]:
                if path == "qiangji":
                    _apply_descriptions(
                        e,
                        db_strength=e.pop("_db_strength", None),
                        db_career=e.pop("_db_career", None),
                        db_transfer=e.get("transfer_direction"))
                else:
                    _apply_descriptions(e)

    # Step 5：组装返回
    data_year = next((y for y in years.values() if y is not None), None)
    return {
        **result,
        "meta": {
            "reference_rank": rank,
            "equivalent_rank": ref,
            "grade": grade,
            "grade_note": _grade_note(grade),
            "hkmo_willingness": hkmo_willingness,
            "data_years": years,
            "delta_range": [p["delta_lo"], p["delta_hi"]],
            "tier_thresholds": _tier_threshold_labels(p),
            "disclaimer": _DISCLAIMER.format(year=data_year) if data_year else "",
        },
    }
