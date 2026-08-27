# -*- coding: utf-8 -*-
"""Province-neutral, evidence-aware ordinary-batch school matching.

The public API accepts normalized rows plus an explicit profile and always
returns an immutable recommendation result. Evidence coverage and provenance
must already be present on the rows; this module never fabricates either.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Optional

if __package__:
    from .contracts import (
        EvidenceStatus,
        OrdinaryBatchPolicy,
        RecommendationItem,
        RecommendationMajorGroup,
        RecommendationProfile,
        RecommendationResult,
    )
    from .rank_locator import RankScenario
else:
    from contracts import (
        EvidenceStatus,
        OrdinaryBatchPolicy,
        RecommendationItem,
        RecommendationMajorGroup,
        RecommendationProfile,
        RecommendationResult,
    )
    from rank_locator import RankScenario


LEVEL_ORDER = {"985": 0, "211": 1, "双一流": 2}

_ACCEPTED_EXACT_STATUSES = {
    EvidenceStatus.OFFICIAL,
    EvidenceStatus.CORROBORATED,
    EvidenceStatus.REFERENCE,
}
_ISSUE_PRECEDENCE = (
    EvidenceStatus.CONFLICT,
    EvidenceStatus.MASKED,
    EvidenceStatus.PARTIAL,
    EvidenceStatus.MISSING,
)
_ACCEPTED_PRECEDENCE = (
    EvidenceStatus.REFERENCE,
    EvidenceStatus.CORROBORATED,
    EvidenceStatus.OFFICIAL,
)

_PROVINCE_ALIASES = {
    "北京": "北京", "北京市": "北京", "天津": "天津", "天津市": "天津",
    "上海": "上海", "上海市": "上海", "重庆": "重庆", "重庆市": "重庆",
    "河北": "河北", "河北省": "河北", "山西": "山西", "山西省": "山西",
    "辽宁": "辽宁", "辽宁省": "辽宁", "吉林": "吉林", "吉林省": "吉林",
    "黑龙江": "黑龙江", "黑龙江省": "黑龙江", "江苏": "江苏", "江苏省": "江苏",
    "浙江": "浙江", "浙江省": "浙江", "安徽": "安徽", "安徽省": "安徽",
    "福建": "福建", "福建省": "福建", "江西": "江西", "江西省": "江西",
    "山东": "山东", "山东省": "山东", "河南": "河南", "河南省": "河南",
    "湖北": "湖北", "湖北省": "湖北", "湖南": "湖南", "湖南省": "湖南",
    "广东": "广东", "广东省": "广东", "海南": "海南", "海南省": "海南",
    "四川": "四川", "四川省": "四川", "贵州": "贵州", "贵州省": "贵州",
    "云南": "云南", "云南省": "云南", "陕西": "陕西", "陕西省": "陕西",
    "甘肃": "甘肃", "甘肃省": "甘肃", "青海": "青海", "青海省": "青海",
    "内蒙古": "内蒙古", "内蒙古自治区": "内蒙古",
    "广西": "广西", "广西壮族自治区": "广西",
    "西藏": "西藏", "西藏自治区": "西藏",
    "宁夏": "宁夏", "宁夏回族自治区": "宁夏",
    "新疆": "新疆", "新疆维吾尔自治区": "新疆",
    "香港": "香港", "香港特别行政区": "香港",
    "澳门": "澳门", "澳门特别行政区": "澳门",
    "台湾": "台湾", "台湾省": "台湾",
}
_OFFICIAL_PROVINCE_NAMES = frozenset(_PROVINCE_ALIASES) | frozenset(
    _PROVINCE_ALIASES.values()
)

_SUBJECT_REQ_RE = re.compile(r"再选科目：([^；]+)")
_SUBJECT_SPLIT_RE = re.compile(r"(?:和|、|/|，|,|\s)+")
_SELECTION_SPLIT_RE = re.compile(r"(?:和|或|、|/|，|,|\s)+")


class SchoolRecommendError(Exception):
    """Controlled recommendation error carrying a stable public code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _tier(delta: int, challenge_delta_lt: int, stable_delta_le: int) -> str:
    if delta < challenge_delta_lt:
        return "冲"
    return "稳" if delta <= stable_delta_le else "保"


def _canonical_province(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    known = _PROVINCE_ALIASES.get(normalized)
    if known is not None:
        return known
    if not re.fullmatch(r"[A-Za-z0-9一-鿿]{2,20}", normalized):
        return None
    for suffix in ("特别行政区", "自治区", "省", "市"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            base = normalized[:-len(suffix)]
            if base in _OFFICIAL_PROVINCE_NAMES:
                return None
            return base
    return normalized


def is_in_province(school_province: object, target_province: object) -> bool:
    """Compare exact canonical administrative names without fuzzy matching."""

    school = _canonical_province(school_province)
    target = _canonical_province(target_province)
    return school is not None and target is not None and school == target


def parse_secondary_subjects(text: object) -> frozenset[str]:
    """Parse an explicit selection from a string or a sequence of strings."""

    if text is None:
        return frozenset()
    if isinstance(text, str):
        values = _SELECTION_SPLIT_RE.split(text.strip()) if text.strip() else []
    elif isinstance(text, Sequence) and not isinstance(text, (bytes, bytearray)):
        if not all(isinstance(value, str) for value in text):
            return frozenset()
        values = [part for value in text for part in _SELECTION_SPLIT_RE.split(value.strip())]
    elif isinstance(text, frozenset):
        if not all(isinstance(value, str) for value in text):
            return frozenset()
        values = list(text)
    else:
        return frozenset()
    return frozenset(value for value in values if value)


def _subject_required(remarks: str) -> Optional[list[set[str]]]:
    """One-release parser for legacy ``再选科目：`` remarks."""

    match = _SUBJECT_REQ_RE.search(remarks or "")
    if not match:
        return None
    text = match.group(1).strip()
    if text in ("不限", "", "无"):
        return []
    return [
        {token for token in _SUBJECT_SPLIT_RE.split(alternative) if token}
        for alternative in re.split(r"或", text)
        if alternative.strip()
    ]


def _subject_match(row: Mapping[str, Any], selected: frozenset[str]) -> bool:
    if "required_secondary_subjects" in row or "secondary_subject_rule" in row:
        rule = row.get("secondary_subject_rule")
        required = parse_secondary_subjects(row.get("required_secondary_subjects"))
        if rule not in {"any", "all"}:
            return False
        if not required:
            return True
        if not selected:
            return False
        return bool(required & selected) if rule == "any" else required <= selected

    legacy = _subject_required(str(row.get("remarks") or row.get("remark") or ""))
    if legacy is None or not legacy:
        return True
    if not selected:
        return True  # Legacy omission meant "do not filter" for one release.
    return any(requirement <= selected for requirement in legacy)


def _strict_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _status(value: object) -> EvidenceStatus | None:
    if isinstance(value, EvidenceStatus):
        return value
    if isinstance(value, str):
        try:
            return EvidenceStatus(value)
        except ValueError:
            return None
    return None


def _source_ids(row: Mapping[str, Any]) -> tuple[str, ...]:
    raw = row.get("source_ids")
    if raw is None and isinstance(row.get("source_id"), str):
        raw = (row["source_id"],)
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (tuple, list)):
        return ()
    values = {value.strip() for value in raw if isinstance(value, str) and value.strip()}
    return tuple(sorted(values))


def _major_tokens(categories: Iterable[str]) -> list[str]:
    tokens: list[str] = []
    for category in categories:
        tokens.extend(
            token for token in re.split(r"[^一-鿿A-Za-z0-9]+", category or "")
            if len(token) >= 2
        )
    return tokens


def _matched_tokens(majors_text: str, tokens: list[str]) -> list[str]:
    return [token for token in tokens if token and token in majors_text]


def _majors_text(raw: object) -> str:
    if raw in (None, ""):
        return ""
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(items, list):
            return "、".join(str(item) for item in items)
    except (ValueError, TypeError):
        pass
    return str(raw)


def _is_intent(school_name: str, preferences: Iterable[str]) -> bool:
    for preference in preferences:
        value = (preference or "").strip()
        if value == school_name or (
            len(value) >= 4 and (value in school_name or school_name in value)
        ):
            return True
    return False


def _profile(value: RecommendationProfile | Mapping[str, Any]) -> RecommendationProfile:
    if isinstance(value, RecommendationProfile):
        return RecommendationProfile(
            rank=value.rank,
            target_province=value.target_province,
            subject_group=value.subject_group,
            secondary_subjects=frozenset(value.secondary_subjects),
            target_major_categories=tuple(value.target_major_categories),
            target_cities=tuple(value.target_cities),
            target_schools=tuple(value.target_schools),
            rank_basis=value.rank_basis,
            optimistic_rank=value.optimistic_rank,
            conservative_rank=value.conservative_rank,
            rank_confidence=value.rank_confidence,
            rank_source_ids=value.rank_source_ids,
        )
    if not isinstance(value, Mapping):
        raise SchoolRecommendError("REC_001", "推荐输入缺少有效参考位次")
    try:
        raw_secondary = value.get("secondary_subjects", ())
        return RecommendationProfile(
            rank=value.get("rank"),
            target_province=value.get("target_province", ""),
            subject_group=value.get("subject_group", ""),
            secondary_subjects=(
                parse_secondary_subjects(raw_secondary)
                if isinstance(raw_secondary, str)
                else raw_secondary
            ),
            target_major_categories=value.get("target_major_categories", ()),
            target_cities=value.get("target_cities", ()),
            target_schools=value.get("target_schools", ()),
            rank_basis=value.get("rank_basis", "official"),
            optimistic_rank=value.get("optimistic_rank"),
            conservative_rank=value.get("conservative_rank"),
            rank_confidence=value.get("rank_confidence", "official"),
            rank_source_ids=value.get("rank_source_ids", ()),
        )
    except (TypeError, ValueError) as error:
        raise SchoolRecommendError("REC_001", "推荐输入格式无效") from error


def _coverage_status(statuses: set[EvidenceStatus]) -> EvidenceStatus:
    for status in _ISSUE_PRECEDENCE:
        if status in statuses:
            return status
    for status in _ACCEPTED_PRECEDENCE:
        if status in statuses:
            return status
    return EvidenceStatus.MISSING


def _warning_for_status(status: EvidenceStatus) -> str | None:
    return {
        EvidenceStatus.CONFLICT: "证据存在冲突，冲突行未进入精确推荐。",
        EvidenceStatus.MASKED: "数据包含屏蔽值、OCR 不确定值或非严格整数，相关行未进入精确推荐。",
        EvidenceStatus.PARTIAL: "数据覆盖不完整；结论仅适用于当前已验证覆盖范围内。",
        EvidenceStatus.MISSING: "部分数据缺少可验证状态、来源或覆盖元数据。",
    }.get(status)


def _recommend_core(
    rows: Sequence[Mapping[str, Any]],
    profile: RecommendationProfile,
    policy: OrdinaryBatchPolicy,
) -> RecommendationResult:
    rank = _strict_int(profile.rank)
    if rank is None or rank < 1:
        raise SchoolRecommendError("REC_001", "参考位次缺失或超出合理范围，请先完成折算")
    target = _canonical_province(profile.target_province)
    if target is None:
        raise SchoolRecommendError("REC_002", "目标省份缺失或不是受支持的行政区名称")

    selected = frozenset(profile.secondary_subjects)
    context_rows: list[dict[str, Any]] = []
    for original in rows:
        if not isinstance(original, Mapping):
            continue
        row = dict(original)
        admission_province = row.get("province")
        if not is_in_province(admission_province, target):
            continue
        row_group = row.get("subject_group")
        if profile.subject_group and row_group and row_group != profile.subject_group:
            continue
        context_rows.append(row)

    input_years = tuple(sorted({
        year for row in context_rows
        if (year := _strict_int(row.get("year"))) is not None
    }))
    excluded_by_subject = 0
    subject_rows: list[dict[str, Any]] = []
    for row in context_rows:
        if _subject_match(row, selected):
            subject_rows.append(row)
        else:
            excluded_by_subject += 1

    statuses: set[EvidenceStatus] = set()
    verified_ranges: list[tuple[int, int]] = []
    usable: list[dict[str, Any]] = []
    zero_score_excluded = 0
    for row in subject_rows:
        status = _status(row.get("evidence_status"))
        statuses.add(status or EvidenceStatus.MISSING)
        coverage_status = _status(row.get("coverage_status"))
        statuses.add(coverage_status or EvidenceStatus.MISSING)

        coverage_min = _strict_int(row.get("coverage_min_rank"))
        coverage_max = _strict_int(row.get("coverage_max_rank"))
        if coverage_min is None or coverage_max is None:
            statuses.add(EvidenceStatus.MISSING)
        elif coverage_min < 1 or coverage_max < coverage_min:
            statuses.add(EvidenceStatus.CONFLICT)
        else:
            verified_ranges.append((coverage_min, coverage_max))

        min_score = _strict_int(row.get("min_score"))
        zero_score = min_score is not None and min_score <= 0
        if zero_score:
            zero_score_excluded += 1
            statuses.add(EvidenceStatus.PARTIAL)

        if any(bool(row.get(flag)) for flag in (
            "masked", "is_masked", "ocr_uncertain", "value_uncertain",
        )):
            statuses.add(EvidenceStatus.MASKED)
            continue
        min_rank = _strict_int(row.get("min_rank"))
        year = _strict_int(row.get("year"))
        if min_rank is None or min_score is None:
            statuses.add(EvidenceStatus.MASKED)
            continue
        if zero_score:
            if min_rank < 1 or year is None:
                statuses.add(EvidenceStatus.MISSING)
            continue
        if min_rank < 1 or year is None:
            statuses.add(EvidenceStatus.MISSING)
            continue
        sources = _source_ids(row)
        if not sources:
            statuses.add(EvidenceStatus.MISSING)
            continue
        if status not in _ACCEPTED_EXACT_STATUSES:
            continue
        if coverage_status not in _ACCEPTED_EXACT_STATUSES | {EvidenceStatus.PARTIAL}:
            continue
        if coverage_min is None or coverage_max is None or coverage_max < coverage_min:
            continue
        snapshot = dict(row)
        snapshot.update({
            "min_rank": min_rank,
            "min_score": min_score,
            "year": year,
            "_status": status,
            "_source_ids": sources,
        })
        usable.append(snapshot)

    verified_coverage: tuple[int, int] | None = None
    if verified_ranges:
        intersection = (
            max(value[0] for value in verified_ranges),
            min(value[1] for value in verified_ranges),
        )
        if intersection[0] <= intersection[1]:
            verified_coverage = intersection
        else:
            statuses.add(EvidenceStatus.CONFLICT)
    else:
        statuses.add(EvidenceStatus.MISSING)

    usable_years = tuple(sorted({row["year"] for row in usable}))
    empty_reason: str | None = None
    candidate_rows: list[dict[str, Any]] = []
    if verified_coverage is None:
        empty_reason = "missing_verified_coverage"
    elif not (verified_coverage[0] <= rank <= verified_coverage[1]):
        empty_reason = "rank_outside_verified_coverage"
    elif usable:
        latest_year = max(row["year"] for row in usable)
        lo = max(1, rank + policy.search_delta_min)
        hi = rank + policy.search_delta_max
        candidate_rows = [
            row for row in usable
            if row["year"] == latest_year and lo <= row["min_rank"] <= hi
        ]

    tokens = _major_tokens(profile.target_major_categories)
    by_school: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(candidate_rows, key=lambda value: (
        value["min_rank"], str(value.get("school_name") or ""),
        str(value.get("major_group_code") or ""),
    )):
        school_name = str(row.get("school_name") or "").strip()
        if not school_name:
            statuses.add(EvidenceStatus.MISSING)
            continue
        by_school.setdefault(school_name, []).append(row)

    sortable: list[tuple[tuple[Any, ...], RecommendationItem]] = []
    for school_name, groups in by_school.items():
        representative = min(groups, key=lambda group: (
            group["min_score"], group["min_rank"],
            str(group.get("major_group_code") or ""),
        ))
        representative_text = _majors_text(representative.get("majors_in_group"))
        representative_matches = _matched_tokens(representative_text, tokens)
        shown = [representative]
        extra_matches: list[str] = []
        for group in groups:
            if group is representative:
                continue
            matches = _matched_tokens(_majors_text(group.get("majors_in_group")), tokens)
            if matches:
                shown.append(group)
                extra_matches.extend(matches)
        matched = list(dict.fromkeys(representative_matches + extra_matches))
        intent = _is_intent(school_name, profile.target_schools)
        city = str(representative.get("city_location") or "")
        city_match = bool(city and city in profile.target_cities)
        delta = representative["min_rank"] - rank
        reasons: list[str] = []
        if intent:
            reasons.append("用户意向院校")
        if city_match:
            reasons.append("用户意向城市")
        if matched:
            reasons.append(f"专业倾向匹配：{'、'.join(matched)}")
        if not reasons:
            reasons.append(
                f"{representative.get('school_level') or '普通高校'}，位次差Δ{delta:+d}"
            )
        school_province = str(
            representative.get("school_province")
            or representative.get("province_location")
            or ""
        )
        province_match = is_in_province(school_province, target)
        item_status = _coverage_status({group["_status"] for group in shown})
        item_sources = tuple(sorted({
            source for group in shown for source in group["_source_ids"]
        }))
        major_groups = tuple(
            RecommendationMajorGroup(
                major_group_name=str(group.get("major_group_name") or ""),
                major_group_code=str(group.get("major_group_code") or ""),
                min_score=group["min_score"],
                min_rank=group["min_rank"],
                majors=_majors_text(group.get("majors_in_group")),
            )
            for group in shown
        )
        item = RecommendationItem(
            school_name=school_name,
            school_level=str(representative.get("school_level") or ""),
            city=city,
            school_province=school_province,
            province_match=province_match,
            subject_match=True,
            min_score=representative["min_score"],
            min_rank=representative["min_rank"],
            delta=delta,
            related_majors=representative_text,
            remarks=str(representative.get("remarks") or ""),
            major_groups=major_groups,
            match_reason="；".join(reasons),
            recommend_level="★★★" if (intent or city_match or matched) else "★★",
            strategy=_tier(delta, policy.challenge_delta_lt, policy.stable_delta_le),
            data_year=representative["year"],
            source_ids=item_sources,
            evidence_status=item_status,
        )
        sort_key = (
            0 if intent else 1,
            0 if city_match else 1,
            LEVEL_ORDER.get(item.school_level, 9),
            0 if province_match else 1,
            item.min_rank,
            item.school_name,
        )
        sortable.append((sort_key, item))

    all_items = tuple(item for _key, item in sorted(sortable, key=lambda value: value[0]))
    capped: list[RecommendationItem] = []
    for tier in ("冲", "稳", "保"):
        tier_items = [item for item in all_items if item.strategy == tier]
        capped.extend(tier_items[:policy.tier_caps[tier]])
    items = tuple(capped)
    if verified_coverage is not None and verified_coverage[0] <= rank <= verified_coverage[1] and not items:
        empty_reason = (
            "unusable_evidence"
            if subject_rows and not usable
            else "no_match_within_verified_coverage"
        )

    final_status = _coverage_status(statuses)
    warnings = [
        warning
        for status in _ISSUE_PRECEDENCE
        if status in statuses
        if (warning := _warning_for_status(status)) is not None
    ]
    if len(input_years) == 1:
        warnings.append(f"仅覆盖 {input_years[0]}")
    if zero_score_excluded:
        warnings.append(f"0分占位已剔除：{zero_score_excluded} 行")
    result = RecommendationResult(
        ordinary_batch_policy=policy,
        items=items,
        excluded_by_subject_count=excluded_by_subject,
        zero_score_excluded_count=zero_score_excluded,
        input_years=input_years,
        usable_years=usable_years,
        verified_rank_coverage=verified_coverage,
        coverage_status=final_status,
        empty_reason=empty_reason,
        warnings=tuple(dict.fromkeys(warnings)),
        rank_basis=profile.rank_basis,
        rank_bounds=(profile.optimistic_rank, profile.rank, profile.conservative_rank),
        rank_confidence=profile.rank_confidence,
        rank_source_ids=profile.rank_source_ids,
    )
    return result


def _scenario_item(
    group: list[dict[str, Any]],
    profile: RecommendationProfile,
    scenario: RankScenario,
) -> RecommendationItem | None:
    by_year: dict[int, dict[str, Any]] = {}
    for row in group:
        year = _strict_int(row.get("year"))
        min_rank = _strict_int(row.get("min_rank"))
        min_score = _strict_int(row.get("min_score"))
        status = _status(row.get("evidence_status"))
        coverage_status = _status(row.get("coverage_status"))
        coverage_min = _strict_int(row.get("coverage_min_rank"))
        coverage_max = _strict_int(row.get("coverage_max_rank"))
        sources = _source_ids(row)
        if (
            year is None
            or min_rank is None
            or min_rank < 1
            or min_score is None
            or min_score <= 0
            or status not in _ACCEPTED_EXACT_STATUSES
            or coverage_status not in _ACCEPTED_EXACT_STATUSES | {EvidenceStatus.PARTIAL}
            or not sources
            or coverage_min is None
            or coverage_max is None
            or not coverage_min <= scenario.optimistic_rank
            or not scenario.conservative_rank <= coverage_max
        ):
            continue
        existing = by_year.get(year)
        if existing is not None and existing["min_rank"] != min_rank:
            return None
        snapshot = dict(row)
        snapshot["_status"] = status
        snapshot["_sources"] = sources
        by_year[year] = snapshot
    years = tuple(sorted(by_year, reverse=True)[:3])
    if not years:
        return None
    selected = tuple(by_year[year] for year in years)
    majority = math.ceil(2 * len(selected) / 3)
    bounds = (
        scenario.optimistic_rank,
        scenario.central_rank,
        scenario.conservative_rank,
    )
    counts = tuple(
        sum(1 for row in selected if rank <= row["min_rank"])
        for rank in bounds
    )
    if counts[2] >= majority:
        strategy = "保"
    elif counts[1] >= majority:
        strategy = "稳"
    elif counts[0] >= 1:
        strategy = "冲"
    else:
        strategy = "观察"
    representative = selected[0]
    school_name = str(representative.get("school_name") or "").strip()
    if not school_name:
        return None
    majors = _majors_text(representative.get("majors_in_group"))
    city = str(representative.get("city_location") or "")
    school_province = str(
        representative.get("school_province")
        or representative.get("province_location")
        or ""
    )
    statuses = {row["_status"] for row in selected}
    source_ids = tuple(
        sorted({source for row in selected for source in row["_sources"]})
    )
    major_group = RecommendationMajorGroup(
        major_group_name=str(representative.get("major_group_name") or ""),
        major_group_code=str(representative.get("major_group_code") or ""),
        min_score=representative["min_score"],
        min_rank=representative["min_rank"],
        majors=majors,
    )
    reasons = [
        f"近{len(years)}年情景覆盖：乐观{counts[0]}、中性{counts[1]}、保守{counts[2]}"
    ]
    intent = _is_intent(school_name, profile.target_schools)
    city_match = bool(city and city in profile.target_cities)
    matched = _matched_tokens(majors, _major_tokens(profile.target_major_categories))
    if intent:
        reasons.append("用户意向院校")
    if city_match:
        reasons.append("用户意向城市")
    if matched:
        reasons.append(f"专业倾向匹配：{'、'.join(matched)}")

    scenario_confidence = profile.rank_confidence if len(years) >= 2 else "low"
    plan_counts = tuple(
        _strict_int(row.get("plan_count")) for row in selected
    )
    known_plans = tuple(value for value in plan_counts if value is not None and value > 0)
    if len(known_plans) >= 2:
        latest_plan = _strict_int(representative.get("plan_count"))
        previous_plans = tuple(
            _strict_int(row.get("plan_count"))
            for row in selected[1:]
            if _strict_int(row.get("plan_count")) is not None
        )
        if (
            latest_plan is not None
            and latest_plan > 0
            and previous_plans
            and latest_plan * 5 < max(previous_plans) * 4
        ):
            scenario_confidence = "low"
            reasons.append("招生计划明显缩减")
    return RecommendationItem(
        school_name=school_name,
        school_level=str(representative.get("school_level") or ""),
        city=city,
        school_province=school_province,
        province_match=is_in_province(school_province, profile.target_province),
        subject_match=True,
        min_score=representative["min_score"],
        min_rank=representative["min_rank"],
        delta=representative["min_rank"] - profile.rank,
        related_majors=majors,
        remarks=str(representative.get("remarks") or ""),
        major_groups=(major_group,),
        match_reason="；".join(reasons),
        recommend_level=(
            "★★★" if len(years) >= 2 and (intent or city_match or matched) else "★★"
        ),
        strategy=strategy,
        data_year=max(years),
        source_ids=source_ids,
        evidence_status=_coverage_status(statuses),
        supporting_years=tuple(sorted(years)),
        required_year_majority=majority,
        scenario_reach_counts=counts,
        scenario_confidence=scenario_confidence,
    )


def _recommend_scenarios(
    rows: Sequence[Mapping[str, Any]],
    profile: RecommendationProfile,
    policy: OrdinaryBatchPolicy,
    scenario: RankScenario,
) -> RecommendationResult:
    if scenario.status not in {EvidenceStatus.OFFICIAL, EvidenceStatus.INFERRED}:
        raise SchoolRecommendError("REC_001", "位次情景缺少可计算边界")
    expected_basis = scenario.status.value
    if (
        profile.rank_basis != expected_basis
        or profile.rank != scenario.central_rank
        or profile.optimistic_rank != scenario.optimistic_rank
        or profile.conservative_rank != scenario.conservative_rank
        or profile.rank_confidence != scenario.confidence
        or tuple(profile.rank_source_ids) != tuple(scenario.source_ids)
    ):
        raise SchoolRecommendError("REC_001", "推荐画像与位次情景不一致")
    target = _canonical_province(profile.target_province)
    if target is None:
        raise SchoolRecommendError("REC_002", "目标省份无效")
    selected_subjects = frozenset(profile.secondary_subjects)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    input_years: set[int] = set()
    excluded = 0
    for original in rows:
        if not isinstance(original, Mapping):
            continue
        row = dict(original)
        if not is_in_province(row.get("province"), target):
            continue
        if profile.subject_group and row.get("subject_group") != profile.subject_group:
            continue
        year = _strict_int(row.get("year"))
        if year is not None:
            input_years.add(year)
        if not _subject_match(row, selected_subjects):
            excluded += 1
            continue
        key = (
            str(row.get("school_code") or row.get("school_name") or ""),
            str(row.get("major_group_code") or row.get("major_group_name") or ""),
            str(row.get("remarks") or ""),
        )
        groups.setdefault(key, []).append(row)
    items = tuple(
        item
        for key in sorted(groups)
        if (item := _scenario_item(groups[key], profile, scenario)) is not None
    )
    search_min = max(1, profile.rank + policy.search_delta_min)
    search_max = profile.rank + policy.search_delta_max
    items = tuple(
        item for item in items if search_min <= item.min_rank <= search_max
    )
    strategy_order = {"冲": 0, "稳": 1, "保": 2, "观察": 3}

    def preference_key(item: RecommendationItem) -> tuple[Any, ...]:
        intent = _is_intent(item.school_name, profile.target_schools)
        city_match = bool(item.city and item.city in profile.target_cities)
        major_match = bool(
            _matched_tokens(
                item.related_majors,
                _major_tokens(profile.target_major_categories),
            )
        )
        return (
            strategy_order[item.strategy],
            0 if intent else 1,
            0 if city_match else 1,
            0 if major_match else 1,
            LEVEL_ORDER.get(item.school_level, 9),
            item.school_name,
        )

    ordered = tuple(
        sorted(items, key=preference_key)
    )
    capped: list[RecommendationItem] = []
    for strategy in ("冲", "稳", "保"):
        matches = [item for item in ordered if item.strategy == strategy]
        capped.extend(matches[: policy.tier_caps[strategy]])
    capped.extend(item for item in ordered if item.strategy == "观察")
    warnings: list[str] = []
    if any(len(item.supporting_years) == 1 for item in capped):
        one_years = sorted(
            {item.supporting_years[0] for item in capped if len(item.supporting_years) == 1}
        )
        warnings.extend(f"仅覆盖 {year}" for year in one_years)
    statuses = {item.evidence_status for item in capped}
    return RecommendationResult(
        ordinary_batch_policy=policy,
        items=tuple(capped),
        excluded_by_subject_count=excluded,
        zero_score_excluded_count=0,
        input_years=tuple(sorted(input_years)),
        usable_years=tuple(sorted({year for item in capped for year in item.supporting_years})),
        verified_rank_coverage=(scenario.optimistic_rank, scenario.conservative_rank),
        coverage_status=_coverage_status(statuses),
        empty_reason=None if capped else "no_match_within_rank_scenarios",
        warnings=tuple(warnings),
        rank_basis=expected_basis,
        rank_bounds=(
            scenario.optimistic_rank,
            scenario.central_rank,
            scenario.conservative_rank,
        ),
        rank_confidence=scenario.confidence,
        rank_source_ids=scenario.source_ids,
    )


def recommend_schools(
    rows: Sequence[Mapping[str, Any]],
    profile: RecommendationProfile | Mapping[str, Any],
    policy: OrdinaryBatchPolicy,
    *,
    rank_scenario: RankScenario | None = None,
) -> RecommendationResult:
    """Return recommendations using one explicit authenticated province policy."""

    if not isinstance(policy, OrdinaryBatchPolicy):
        raise SchoolRecommendError("REC_003", "普通批策略缺失或无效")
    policy_snapshot = OrdinaryBatchPolicy(**policy.to_dict())
    profile_snapshot = _profile(profile)
    if rank_scenario is not None:
        if not isinstance(rank_scenario, RankScenario):
            raise TypeError("rank_scenario must be a RankScenario")
        return _recommend_scenarios(
            rows, profile_snapshot, policy_snapshot, rank_scenario
        )
    return _recommend_core(rows, profile_snapshot, policy_snapshot)


__all__ = [
    "SchoolRecommendError",
    "is_in_province",
    "parse_secondary_subjects",
    "recommend_schools",
]
