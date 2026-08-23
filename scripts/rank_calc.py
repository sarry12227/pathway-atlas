# -*- coding: utf-8 -*-
"""M3 校排名→省排名折算引擎（spec §4.1 / CONTEXT.md 锚点·折算）。

算法自 shengxue-system/backend/app/services/rank_calc.py 原样移植，
折算数值口径与现有系统完全一致；唯一有意改进：锚点线集合（名称/数量/
列映射）由省份配置 province.json 定义，不再写死四列。

纯确定性计算：
- 锚点插值：校排名落在相邻锚点间线性插值，区间外由最外侧两个锚点线性外推，
  省排钳位到 ≥1；
- 三档估计：正常 normal_rank / 最好 best_rank / 保守 normal+(normal−best)，
  先在校排名口径算出保守校排，再走同一锚点映射折成省排；
- 一分一段反查分数：取最小 rank≥目标位次的分数；位次超出表尾取表内最低分兜底；
- 降级链：本校当年 → 本校往年（数据质量降一级标注）→ 同城同档代理
  （同市同层次学校锚点均值，data_quality=sparse）→ 拒绝折算（RankCalcError DB_001，
  提示改用分数/省排输入）。
"""
import math
import os
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Iterable, Optional

if __package__:
    from .contracts import EvidenceStatus
    from .data_loader import (
        DEFAULT_DATA_ROOT,
        load_province_config,
        load_schools,
        load_xibao,
        load_yifenyiduan,
    )
else:  # Direct ``python scripts/*.py`` and flat scripts-path compatibility.
    from contracts import EvidenceStatus
    from data_loader import (
        DEFAULT_DATA_ROOT,
        load_province_config,
        load_schools,
        load_xibao,
        load_yifenyiduan,
    )

MIN_ANCHORS_DEFAULT = 2  # 少于 2 个锚点无法插值/外推，视为该年不可用

_DISCLAIMER = ("根据往年数据估算，该成绩折合今年高考省排约{prov}位，分数约{score}分。"
               "该成绩仅供参考，一切以实际高考成绩为准。")

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_-]{0,127}$")
_ACCEPTED_EXACT_STATUSES = frozenset(
    {
        EvidenceStatus.OFFICIAL,
        EvidenceStatus.CORROBORATED,
        EvidenceStatus.REFERENCE,
    }
)


class RankScope(str, Enum):
    """Comparable population represented by a joy-report anchor."""

    WHOLE_SCHOOL = "whole_school"
    CLASS = "class"
    SUBJECT_GROUP = "subject_group"
    NAMED_PROGRAM = "named_program"


def _positive_int(value, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be a positive integer")
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _nonempty_string(value, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a non-empty string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
    return value


def _enum_value(value, enum_type, name: str):
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a {enum_type.__name__}")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"unsupported {name}: {value!r}") from exc


@dataclass(frozen=True)
class RankAnchor:
    """One exact, field-sourced school-rank to province-rank mapping.

    Construction snapshots ``source_ids`` into a tuple. ``to_dict`` returns a
    new JSON-safe tree, so neither caller mutation nor serialization can alter
    the accepted anchor used by the deterministic engine.
    """

    anchor_id: str
    year: int
    school_name: str
    scope_type: RankScope
    scope_value: str
    school_rank: int
    province_rank: int
    school_score: int | float | None
    source_ids: tuple[str, ...]
    evidence_status: EvidenceStatus
    coverage_status: EvidenceStatus
    coverage_min_school_rank: int | None = None
    coverage_max_school_rank: int | None = None

    def __post_init__(self) -> None:
        _nonempty_string(self.anchor_id, "anchor_id")
        if not _SAFE_ID.fullmatch(self.anchor_id):
            raise ValueError("anchor_id must use the public safe-ID syntax")
        _positive_int(self.year, "year")
        if self.year < 2000 or self.year > 2100:
            raise ValueError("year must be between 2000 and 2100")
        _nonempty_string(self.school_name, "school_name")
        _nonempty_string(self.scope_value, "scope_value")
        object.__setattr__(
            self, "scope_type", _enum_value(self.scope_type, RankScope, "scope_type")
        )
        _positive_int(self.school_rank, "school_rank")
        _positive_int(self.province_rank, "province_rank")
        if self.school_score is not None:
            if (
                not isinstance(self.school_score, (int, float))
                or isinstance(self.school_score, bool)
            ):
                raise TypeError("school_score must be a finite positive number")
            if not math.isfinite(self.school_score) or self.school_score <= 0:
                raise ValueError("school_score must be a finite positive number")

        if isinstance(self.source_ids, (str, bytes)):
            raise TypeError("source_ids must be a collection of safe IDs")
        try:
            source_ids = tuple(self.source_ids)
        except TypeError as exc:
            raise TypeError("source_ids must be a collection of safe IDs") from exc
        if not source_ids:
            raise ValueError("source_ids must not be empty")
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_ids must be unique")
        for source_id in source_ids:
            if not isinstance(source_id, str):
                raise TypeError("source_ids must contain only strings")
            if not _SAFE_ID.fullmatch(source_id):
                raise ValueError("source_ids must use the public safe-ID syntax")
        object.__setattr__(self, "source_ids", tuple(sorted(source_ids)))
        object.__setattr__(
            self,
            "evidence_status",
            _enum_value(self.evidence_status, EvidenceStatus, "evidence_status"),
        )
        object.__setattr__(
            self,
            "coverage_status",
            _enum_value(self.coverage_status, EvidenceStatus, "coverage_status"),
        )

        minimum = self.coverage_min_school_rank
        maximum = self.coverage_max_school_rank
        if (minimum is None) != (maximum is None):
            raise ValueError("coverage rank bounds must be both present or both absent")
        if self.coverage_status in _ACCEPTED_EXACT_STATUSES and minimum is None:
            raise ValueError("accepted exact coverage requires explicit rank bounds")
        if minimum is not None and maximum is not None:
            _positive_int(minimum, "coverage_min_school_rank")
            _positive_int(maximum, "coverage_max_school_rank")
            if minimum > maximum:
                raise ValueError("coverage rank bounds must be ordered")
            if not minimum <= self.school_rank <= maximum:
                raise ValueError("school_rank must lie inside declared coverage")

    def to_dict(self) -> dict:
        return {
            "anchor_id": self.anchor_id,
            "year": self.year,
            "school_name": self.school_name,
            "scope_type": self.scope_type.value,
            "scope_value": self.scope_value,
            "school_rank": self.school_rank,
            "province_rank": self.province_rank,
            "school_score": self.school_score,
            "source_ids": list(self.source_ids),
            "evidence_status": self.evidence_status.value,
            "coverage_status": self.coverage_status.value,
            "coverage_min_school_rank": self.coverage_min_school_rank,
            "coverage_max_school_rank": self.coverage_max_school_rank,
        }


@dataclass(frozen=True)
class RankEstimate:
    """Immutable result of evidence-gated rank estimation."""

    status: EvidenceStatus
    lower_rank: int | None
    upper_rank: int | None
    median_rank: int | None
    method: str
    confidence: str
    input_anchor_count: int
    usable_anchor_count: int
    rejected_anchor_count: int
    rejection_reasons: tuple[str, ...]
    reason_code: str | None
    reasons: tuple[str, ...]
    contributing_anchor_ids: tuple[str, ...]
    contributing_years: tuple[int, ...]
    contributing_source_ids: tuple[str, ...]
    tolerance_rank: int | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "status", _enum_value(self.status, EvidenceStatus, "status")
        )
        if self.status not in {
            EvidenceStatus.INFERRED,
            EvidenceStatus.MISSING,
            EvidenceStatus.CONFLICT,
        }:
            raise ValueError("rank estimate status must be inferred, missing, or conflict")
        _nonempty_string(self.method, "method")
        _nonempty_string(self.confidence, "confidence")
        _nonnegative_int(self.input_anchor_count, "input_anchor_count")
        _nonnegative_int(self.usable_anchor_count, "usable_anchor_count")
        _nonnegative_int(self.rejected_anchor_count, "rejected_anchor_count")
        if self.usable_anchor_count + self.rejected_anchor_count != self.input_anchor_count:
            raise ValueError("anchor counts must reconcile")

        for field_name in (
            "rejection_reasons",
            "reasons",
            "contributing_anchor_ids",
            "contributing_years",
            "contributing_source_ids",
        ):
            value = getattr(self, field_name)
            if isinstance(value, (str, bytes)):
                raise TypeError(f"{field_name} must be a collection")
            try:
                snapshot = tuple(value)
            except TypeError as exc:
                raise TypeError(f"{field_name} must be a collection") from exc
            object.__setattr__(self, field_name, snapshot)

        for field_name in ("rejection_reasons", "reasons"):
            for item in getattr(self, field_name):
                if not isinstance(item, str):
                    raise TypeError(f"{field_name} must contain only strings")
                if not _REASON_CODE.fullmatch(item):
                    raise ValueError(f"{field_name} must contain stable reason codes")
        for field_name in ("contributing_anchor_ids", "contributing_source_ids"):
            identifiers = getattr(self, field_name)
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{field_name} must contain unique IDs")
            for item in identifiers:
                if not isinstance(item, str):
                    raise TypeError(f"{field_name} must contain only strings")
                if not _SAFE_ID.fullmatch(item):
                    raise ValueError(f"{field_name} must contain only safe IDs")
        if len(self.contributing_years) != len(set(self.contributing_years)):
            raise ValueError("contributing_years must be unique")
        for year in self.contributing_years:
            _positive_int(year, "contributing_year")
            if year < 2000 or year > 2100:
                raise ValueError("contributing years must be between 2000 and 2100")

        if self.status == EvidenceStatus.INFERRED:
            for name in ("lower_rank", "upper_rank", "median_rank"):
                _positive_int(getattr(self, name), name)
            if not self.lower_rank <= self.median_rank <= self.upper_rank:
                raise ValueError("rank bounds must contain the median")
            _nonnegative_int(self.tolerance_rank, "tolerance_rank")
            expected_tolerance = max(
                self.median_rank - self.lower_rank,
                self.upper_rank - self.median_rank,
            )
            if self.tolerance_rank != expected_tolerance:
                raise ValueError("tolerance_rank must match the observed bounds")
            if self.reason_code is not None:
                raise ValueError("successful estimates cannot have a reason_code")
            if self.confidence == "none":
                raise ValueError("successful estimates require confidence")
            if not (
                self.contributing_anchor_ids
                and self.contributing_years
                and self.contributing_source_ids
            ):
                raise ValueError("successful estimates require contributing evidence")
        else:
            if any(
                value is not None
                for value in (
                    self.lower_rank,
                    self.upper_rank,
                    self.median_rank,
                    self.tolerance_rank,
                )
            ):
                raise ValueError("missing or conflict estimates cannot contain ranks")
            if not isinstance(self.reason_code, str):
                raise TypeError("missing or conflict estimates require a reason_code")
            if not _REASON_CODE.fullmatch(self.reason_code):
                raise ValueError("reason_code must use stable code syntax")
            if self.confidence != "none":
                raise ValueError("missing or conflict estimates must have no confidence")
            if (
                self.contributing_anchor_ids
                or self.contributing_years
                or self.contributing_source_ids
            ):
                raise ValueError("missing or conflict estimates cannot cite contributors")

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "lower_rank": self.lower_rank,
            "upper_rank": self.upper_rank,
            "median_rank": self.median_rank,
            "method": self.method,
            "confidence": self.confidence,
            "input_anchor_count": self.input_anchor_count,
            "usable_anchor_count": self.usable_anchor_count,
            "rejected_anchor_count": self.rejected_anchor_count,
            "rejection_reasons": list(self.rejection_reasons),
            "reason_code": self.reason_code,
            "reasons": list(self.reasons),
            "contributing_anchor_ids": list(self.contributing_anchor_ids),
            "contributing_years": list(self.contributing_years),
            "contributing_source_ids": list(self.contributing_source_ids),
            "tolerance_rank": self.tolerance_rank,
        }


def _empty_rank_estimate(
    status: EvidenceStatus,
    reason_code: str,
    *,
    input_count: int,
    usable_count: int,
    rejection_reasons: tuple[str, ...],
    reasons: tuple[str, ...] = (),
) -> RankEstimate:
    return RankEstimate(
        status=status,
        lower_rank=None,
        upper_rank=None,
        median_rank=None,
        method="school_rank_offset_median_observed_spread",
        confidence="none",
        input_anchor_count=input_count,
        usable_anchor_count=usable_count,
        rejected_anchor_count=input_count - usable_count,
        rejection_reasons=rejection_reasons,
        reason_code=reason_code,
        reasons=reasons,
        contributing_anchor_ids=(),
        contributing_years=(),
        contributing_source_ids=(),
        tolerance_rank=None,
    )


def _three_independent_anchors(anchors: list[RankAnchor]) -> tuple[RankAnchor, ...]:
    """Return the first deterministic disjoint-source triple, if one exists."""

    ordered = sorted(anchors, key=lambda item: (len(item.source_ids), item.source_ids, item.anchor_id))
    for left_index, left in enumerate(ordered):
        left_sources = set(left.source_ids)
        for middle_index in range(left_index + 1, len(ordered)):
            middle = ordered[middle_index]
            middle_sources = set(middle.source_ids)
            if left_sources & middle_sources:
                continue
            for right in ordered[middle_index + 1 :]:
                right_sources = set(right.source_ids)
                if not (left_sources & right_sources or middle_sources & right_sources):
                    return left, middle, right
    return ()


def _qualifies_comparable_anchor_set(anchors: Iterable[RankAnchor]) -> bool:
    """Return whether anchors pass every non-coverage estimation threshold."""

    items = tuple(anchors)
    if not items:
        return False
    if len(
        {(item.school_name, item.scope_type, item.scope_value) for item in items}
    ) != 1:
        return False
    by_year: dict[int, list[RankAnchor]] = {}
    for item in items:
        by_year.setdefault(item.year, []).append(item)
    if any(
        len(
            {
                (item.school_rank, item.province_rank, item.school_score)
                for item in year_items
            }
        )
        != 1
        for year_items in by_year.values()
    ):
        return False
    if len(by_year) >= 2:
        return True
    return bool(_three_independent_anchors(next(iter(by_year.values()))))


def _integer_median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def estimate_rank_from_anchors(
    anchors: Iterable[RankAnchor], student_score, student_rank
) -> RankEstimate:
    """Estimate a provincial-rank interval from comparable accepted anchors.

    Every contributing anchor applies the public rank-offset rule
    ``max(1, province_rank + student_rank - school_rank)``.  The integer median
    is the center description and the minimum/maximum implied values are the
    observed-spread interval. ``student_score`` is validated but explicitly
    reported as unused; without a versioned score mapping it never changes the
    rank result.

    The function performs no I/O and never enters the legacy bundled-xibao
    fallback used by :func:`estimate_rank`.
    """

    _positive_int(student_rank, "student_rank")
    if student_score is not None:
        if (
            not isinstance(student_score, (int, float))
            or isinstance(student_score, bool)
        ):
            raise TypeError("student_score must be a finite positive number")
        if not math.isfinite(student_score) or student_score <= 0:
            raise ValueError("student_score must be a finite positive number")
    if isinstance(anchors, (str, bytes)):
        raise TypeError("anchors must be an iterable of RankAnchor records")
    try:
        input_anchors = tuple(anchors)
    except TypeError as exc:
        raise TypeError("anchors must be an iterable of RankAnchor records") from exc
    if any(not isinstance(item, RankAnchor) for item in input_anchors):
        raise TypeError("anchors must contain only RankAnchor records")

    input_count = len(input_anchors)
    anchor_by_id: dict[str, RankAnchor] = {}
    for item in input_anchors:
        previous = anchor_by_id.get(item.anchor_id)
        if previous is not None and previous != item:
            return _empty_rank_estimate(
                EvidenceStatus.CONFLICT,
                "duplicate_anchor_id_conflict",
                input_count=input_count,
                usable_count=0,
                rejection_reasons=("duplicate_anchor_id_conflict",),
            )
        anchor_by_id[item.anchor_id] = item

    usable: list[RankAnchor] = []
    coverage_rejected: list[RankAnchor] = []
    rejection_reasons: list[str] = []
    seen_anchor_ids: set[str] = set()
    for item in sorted(input_anchors, key=lambda value: (value.year, value.anchor_id)):
        if item.anchor_id in seen_anchor_ids:
            rejection_reasons.append("duplicate_anchor")
            continue
        seen_anchor_ids.add(item.anchor_id)
        if item.evidence_status not in _ACCEPTED_EXACT_STATUSES:
            rejection_reasons.append("unaccepted_evidence_status")
            continue
        if item.coverage_status not in _ACCEPTED_EXACT_STATUSES:
            rejection_reasons.append("incomplete_coverage")
            continue
        if not (
            item.coverage_min_school_rank
            <= student_rank
            <= item.coverage_max_school_rank
        ):
            rejection_reasons.append("input_outside_verified_coverage")
            coverage_rejected.append(item)
            continue
        usable.append(item)

    usable_count = len(usable)
    rejected = tuple(sorted(rejection_reasons))
    insufficient_reason = (
        "input_outside_verified_coverage"
        if coverage_rejected
        and _qualifies_comparable_anchor_set((*usable, *coverage_rejected))
        else "insufficient_comparable_anchors"
    )
    if not usable:
        return _empty_rank_estimate(
            EvidenceStatus.MISSING,
            insufficient_reason,
            input_count=input_count,
            usable_count=usable_count,
            rejection_reasons=rejected,
        )

    comparability_keys = {
        (item.school_name, item.scope_type, item.scope_value) for item in usable
    }
    if len(comparability_keys) != 1:
        return _empty_rank_estimate(
            EvidenceStatus.CONFLICT,
            "mixed_comparability_groups",
            input_count=input_count,
            usable_count=usable_count,
            rejection_reasons=rejected,
        )

    by_year: dict[int, list[RankAnchor]] = {}
    for item in usable:
        by_year.setdefault(item.year, []).append(item)
    for items in by_year.values():
        exact_values = {
            (item.school_rank, item.province_rank, item.school_score)
            for item in items
        }
        if len(exact_values) != 1:
            return _empty_rank_estimate(
                EvidenceStatus.CONFLICT,
                "same_year_exact_disagreement",
                input_count=input_count,
                usable_count=usable_count,
                rejection_reasons=rejected,
            )

    if len(by_year) >= 2:
        contributing = tuple(
            min(by_year[year], key=lambda item: item.anchor_id) for year in sorted(by_year)
        )
        confidence = "high" if len(contributing) >= 3 else "moderate"
    else:
        only_year = next(iter(by_year))
        contributing = _three_independent_anchors(by_year[only_year])
        if not contributing:
            return _empty_rank_estimate(
                EvidenceStatus.MISSING,
                insufficient_reason,
                input_count=input_count,
                usable_count=usable_count,
                rejection_reasons=rejected,
            )
        confidence = "corroborated"

    implied_ranks = [
        max(1, item.province_rank + student_rank - item.school_rank)
        for item in contributing
    ]
    lower = min(implied_ranks)
    upper = max(implied_ranks)
    center = _integer_median(implied_ranks)
    reasons = ["observed_spread_interval"]
    if student_score is not None:
        reasons.append("student_score_not_used_no_versioned_model")
    return RankEstimate(
        status=EvidenceStatus.INFERRED,
        lower_rank=lower,
        upper_rank=upper,
        median_rank=center,
        method="school_rank_offset_median_observed_spread",
        confidence=confidence,
        input_anchor_count=input_count,
        usable_anchor_count=usable_count,
        rejected_anchor_count=input_count - usable_count,
        rejection_reasons=rejected,
        reason_code=None,
        reasons=tuple(reasons),
        contributing_anchor_ids=tuple(item.anchor_id for item in contributing),
        contributing_years=tuple(sorted({item.year for item in contributing})),
        contributing_source_ids=tuple(
            sorted({source_id for item in contributing for source_id in item.source_ids})
        ),
        tolerance_rank=max(center - lower, upper - center),
    )


class RankCalcError(Exception):
    """折算失败的业务错误（code 沿用现有系统 v1.1 附录 B）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def resolve_current_year(config: dict) -> int:
    """"当年"判定年份（沿用 config.CURRENT_DATA_YEAR 语义）：
    环境变量 SHENGXUE_CURRENT_DATA_YEAR > 省份配置 current_data_year > 当前年份。"""
    env = os.getenv("SHENGXUE_CURRENT_DATA_YEAR")
    if env:
        return int(env)
    if config.get("current_data_year"):
        return int(config["current_data_year"])
    return date.today().year


def anchors_from_row(row: dict, anchor_defs: list[dict]) -> list[dict]:
    """从一条喜报记录提取可用锚点（两列均非空），按校排名升序。"""
    anchors = [
        {"name": a["name"], "school_rank": row.get(a["school_rank_col"]),
         "prov_rank": row.get(a["prov_rank_col"])}
        for a in anchor_defs
        if row.get(a["school_rank_col"]) is not None
        and row.get(a["prov_rank_col"]) is not None
    ]
    anchors.sort(key=lambda a: a["school_rank"])
    return anchors


def interp(anchors: list[dict], school_rank: int) -> int:
    """相邻锚点线性插值；区间外用最外侧两个锚点线性外推；结果钳位到 ≥1。"""
    if school_rank <= anchors[0]["school_rank"]:
        a, b = anchors[0], anchors[1]
    elif school_rank >= anchors[-1]["school_rank"]:
        a, b = anchors[-2], anchors[-1]
    else:
        a, b = anchors[0], anchors[1]
        for x, y in zip(anchors, anchors[1:]):
            if x["school_rank"] <= school_rank <= y["school_rank"]:
                a, b = x, y
                break
    span = b["school_rank"] - a["school_rank"]
    if span == 0:
        return max(1, a["prov_rank"])
    ratio = (school_rank - a["school_rank"]) / span
    prov = a["prov_rank"] + ratio * (b["prov_rank"] - a["prov_rank"])
    return max(1, round(prov))


def _downgrade(quality: Optional[str]) -> str:
    """往年回退时数据质量降一级标注。"""
    return {"full": "partial", "partial": "sparse"}.get(quality or "full", "sparse")


def _school_city_level(school_name: str, schools_rows: list[dict],
                       xibao_rows: list[dict]):
    """定位目标学校的 (城市, 层次)：优先高中名录精确匹配，其次本校喜报记录。"""
    for row in schools_rows:
        if row["school_name"] == school_name and row.get("city") and row.get("level"):
            return row["city"], row["level"]
    for row in xibao_rows:
        if (row["school_name"] == school_name
                and row.get("city") and row.get("school_level")):
            return row["city"], row["school_level"]
    return None, None


def _proxy_anchors(school_name: str, city: str, level: str,
                   xibao_rows: list[dict], anchor_defs: list[dict]):
    """同城同档代理：同市同层次其他学校最新年份锚点，按锚点名取均值。"""
    peers = [r for r in xibao_rows
             if r.get("city") == city and r.get("school_level") == level
             and r["school_name"] != school_name]
    if not peers:
        return [], [], 0
    year = max(r["year"] for r in peers)
    by_name: dict[str, list[dict]] = {}
    schools = []
    for r in peers:
        if r["year"] != year:
            continue
        anchors = anchors_from_row(r, anchor_defs)
        if not anchors:
            continue
        schools.append(r["school_name"])
        for a in anchors:
            by_name.setdefault(a["name"], []).append(a)
    merged = [
        {"name": name,
         "school_rank": round(sum(a["school_rank"] for a in lst) / len(lst)),
         "prov_rank": round(sum(a["prov_rank"] for a in lst) / len(lst))}
        for name, lst in by_name.items()
    ]
    merged.sort(key=lambda a: a["school_rank"])
    return merged, schools, year


def _load_anchors(school_name: str, xibao_rows: list[dict],
                  schools_rows: list[dict], anchor_defs: list[dict],
                  current_year: int, min_anchors: int):
    """降级链：本校当年 → 本校往年 → 同城同档代理 → 拒绝折算。"""
    rows = sorted((r for r in xibao_rows if r["school_name"] == school_name),
                  key=lambda r: r["year"], reverse=True)
    # "当年"以配置年份为准，而非全局 MAX(year)：个别学校灌入更新年份的喜报
    # 不应误伤其他学校的 anchor_source 标注

    # 1)/2) 本校当年 / 本校往年：取本校最新一条锚点足够的记录，
    # 年份等于当年 → 当年，否则往年回退（数据质量降一级）。
    for row in rows:
        anchors = anchors_from_row(row, anchor_defs)
        if len(anchors) < min_anchors:
            continue
        if row["year"] == current_year:
            info = {"data_year": row["year"], "anchor_source": "本校当年",
                    "data_quality": row.get("data_quality") or "full"}
        else:
            info = {"data_year": row["year"], "anchor_source": "本校往年",
                    "data_quality": _downgrade(row.get("data_quality"))}
        return anchors, info

    # 3) 同城同档代理
    city, level = _school_city_level(school_name, schools_rows, xibao_rows)
    if city and level:
        anchors, proxy_schools, year = _proxy_anchors(
            school_name, city, level, xibao_rows, anchor_defs)
        if len(anchors) >= min_anchors:
            return anchors, {"data_year": year, "anchor_source": "同城同档代理",
                             "data_quality": "sparse", "proxy_schools": proxy_schools}

    # 4) 拒绝折算：明确给出替代输入建议（spec §4.1）
    raise RankCalcError(
        "DB_001",
        f"「{school_name}」暂无喜报锚点数据，无法折算省排名，"
        f"建议改用分数/省排名输入（recommend.py --score/--rank）")


def _score_for_rank(yfd_rows: list[dict], prov_rank: int) -> int:
    """一分一段反查：最小 rank≥目标位次的分数；超出表尾取最低分。"""
    candidates = [r for r in yfd_rows if r["rank"] >= prov_rank]
    if candidates:
        return min(candidates, key=lambda r: r["rank"])["score"]
    return max(yfd_rows, key=lambda r: r["rank"])["score"]


def calculate_rank(anchors: list[dict], anchor_info: dict, yfd_rows: list[dict],
                   *, school_name: str, exam_rank, best_rank=None,
                   normal_rank=None, tolerance: str = "±2000位次") -> dict:
    """M3 主计算：输入已解析的锚点与一分一段行，输出三档估计 + 来源标注。"""
    school_name = (school_name or "").strip()
    if not school_name:
        raise RankCalcError("RANK_001", "学校名称缺失，无法折算")
    if exam_rank is None or int(exam_rank) < 1:
        raise RankCalcError("RANK_001", "校排名缺失或超出合理范围，请核实排名数据")
    normal = int(normal_rank) if normal_rank else int(exam_rank)
    best = int(best_rank) if best_rank else int(exam_rank)

    n_prov = interp(anchors, normal)
    b_prov = interp(anchors, best)
    # 保守估计：先在校排名口径 normal+(normal−best)，再走同一锚点映射
    c_prov = interp(anchors, normal + (normal - best))
    n_score = _score_for_rank(yfd_rows, n_prov)
    b_score = _score_for_rank(yfd_rows, b_prov)
    c_score = _score_for_rank(yfd_rows, c_prov)

    return {
        "normal_estimate": {
            "prov_rank": n_prov, "estimated_score": n_score,
            "description": f"正常水平：省排约{n_prov}位，对应约{n_score}分",
        },
        "best_estimate": {
            "prov_rank": b_prov, "estimated_score": b_score,
            "description": f"最好水平：省排约{b_prov}位，对应约{b_score}分",
        },
        "conservative_estimate": {
            "prov_rank": c_prov, "estimated_score": c_score,
            "description": f"保守估计：省排约{c_prov}位，对应约{c_score}分",
        },
        "interval": {
            "rank_range": f"{b_prov}-{c_prov}",
            "score_range": f"{c_score}-{b_score}",
        },
        "anchor_info": {
            "school_name": school_name,
            **anchor_info,
            "anchors_used": anchors,
        },
        "disclaimer": _DISCLAIMER.format(prov=n_prov, score=n_score),
        "tolerance": tolerance,
    }


def estimate_rank(province: str, school_name: str, subject_group: str = "物理",
                  exam_rank=None, best_rank=None, normal_rank=None,
                  root: str = DEFAULT_DATA_ROOT) -> dict:
    """M3 完整入口：加载省份配置与随包数据，走降级链折算省排名。"""
    school_name = (school_name or "").strip()
    if not school_name:
        raise RankCalcError("RANK_001", "学校名称缺失，无法折算")
    if exam_rank is None or int(exam_rank) < 1:
        raise RankCalcError("RANK_001", "校排名缺失或超出合理范围，请核实排名数据")
    subject_group = subject_group or "物理"

    config = load_province_config(province, root=root)
    anchor_defs = config["anchors"]
    display = config.get("display") or {}
    min_anchors = int(display.get("min_anchors") or MIN_ANCHORS_DEFAULT)
    tolerance = display.get("tolerance") or "±2000位次"
    current_year = resolve_current_year(config)

    xibao_rows = load_xibao(province, config, root=root)
    schools_rows = load_schools(province, root=root)
    _, yfd_rows = load_yifenyiduan(province, subject_group, root=root)

    anchors, info = _load_anchors(school_name, xibao_rows, schools_rows,
                                  anchor_defs, current_year, min_anchors)
    return calculate_rank(anchors, info, yfd_rows, school_name=school_name,
                          exam_rank=exam_rank, best_rank=best_rank,
                          normal_rank=normal_rank, tolerance=tolerance)
