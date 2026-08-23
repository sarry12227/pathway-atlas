# -*- coding: utf-8 -*-
"""Province-neutral, evidence-aware ordinary-batch school matching.

The public API returns immutable contracts. A one-release compatibility
dispatch preserves the legacy ``year=`` / ``estimated_prov_rank=`` callers;
both paths execute the same filtering, ranking, de-duplication, and tiering
core.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

try:
    from contracts import (
        EvidenceStatus,
        RecommendationItem,
        RecommendationMajorGroup,
        RecommendationProfile,
        RecommendationResult,
    )
except ModuleNotFoundError:  # Package import compatibility.
    from scripts.contracts import (  # type: ignore
        EvidenceStatus,
        RecommendationItem,
        RecommendationMajorGroup,
        RecommendationProfile,
        RecommendationResult,
    )


CHONG_LT = -2000
WEN_LE = 2000
TIER_CAPS = {"冲": 3, "稳": 4, "保": 5}
DELTA_LO, DELTA_HI = -8000, 6000
LEVEL_ORDER = {"985": 0, "211": 1, "双一流": 2}
_UNSET = object()

_ACCEPTED_EXACT_STATUSES = {
    EvidenceStatus.OFFICIAL,
    EvidenceStatus.CORROBORATED,
    EvidenceStatus.REFERENCE,
    EvidenceStatus.PARTIAL,
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


def params_from_config(config: Optional[dict] = None) -> dict:
    """Translate optional province parameters to deterministic engine limits."""

    config = config or {}
    thresholds = config.get("tier_thresholds") or {}
    caps = config.get("tier_caps") or {}
    delta_range = config.get("delta_range") or []
    return {
        "chong_lt": int(thresholds.get("chong_lt", CHONG_LT)),
        "wen_le": int(thresholds.get("wen_le", WEN_LE)),
        "delta_lo": int(delta_range[0]) if len(delta_range) >= 2 else DELTA_LO,
        "delta_hi": int(delta_range[1]) if len(delta_range) >= 2 else DELTA_HI,
        "tier_caps": {**TIER_CAPS, **{key: int(value) for key, value in caps.items()}},
    }


def _tier_threshold_labels(parameters: dict) -> dict:
    return {
        "冲": f"Δ<{parameters['chong_lt']}",
        "稳": f"{parameters['chong_lt']}≤Δ≤+{parameters['wen_le']}",
        "保": f"Δ>+{parameters['wen_le']}",
    }


def _tier(delta: int, chong_lt: int = CHONG_LT, wen_le: int = WEN_LE) -> str:
    if delta < chong_lt:
        return "冲"
    return "稳" if delta <= wen_le else "保"


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
        )
    except (TypeError, ValueError) as error:
        raise SchoolRecommendError("REC_001", "推荐输入格式无效") from error


@dataclass(frozen=True)
class _CoreMetrics:
    total_985: int
    total_211: int
    total_in_province: int


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
    parameters: dict,
) -> tuple[RecommendationResult, _CoreMetrics]:
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
        lo = max(1, rank + parameters["delta_lo"])
        hi = rank + parameters["delta_hi"]
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
        delta = representative["min_rank"] - rank
        reasons: list[str] = []
        if intent:
            reasons.append("用户意向院校")
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
            city=str(representative.get("city_location") or ""),
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
            recommend_level="★★★" if (intent or matched) else "★★",
            strategy=_tier(delta, parameters["chong_lt"], parameters["wen_le"]),
            data_year=representative["year"],
            source_ids=item_sources,
            evidence_status=item_status,
        )
        sort_key = (
            0 if intent else 1,
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
        capped.extend(tier_items[:parameters["tier_caps"][tier]])
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
        items=items,
        excluded_by_subject_count=excluded_by_subject,
        zero_score_excluded_count=zero_score_excluded,
        input_years=input_years,
        usable_years=usable_years,
        verified_rank_coverage=verified_coverage,
        coverage_status=final_status,
        empty_reason=empty_reason,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    metrics = _CoreMetrics(
        total_985=sum(item.school_level == "985" for item in all_items),
        total_211=sum(item.school_level == "211" for item in all_items),
        total_in_province=sum(item.province_match for item in all_items),
    )
    return result, metrics


def _legacy_rows(
    rows: Sequence[Mapping[str, Any]], *, year: int, target_province: str,
) -> list[dict[str, Any]]:
    adapted: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        row.setdefault("year", year)
        if not row.get("province"):
            row["province"] = target_province
        if not row.get("school_province") and not row.get("province_location"):
            row["school_province"] = target_province if bool(row.get("is_inside_hubei")) else ""
        row.setdefault("evidence_status", EvidenceStatus.REFERENCE.value)
        row.setdefault("source_ids", ("legacy-local-dataset",))
        row.setdefault("coverage_min_rank", 1)
        row.setdefault("coverage_max_rank", 2_147_483_647)
        adapted.append(row)
    return adapted


def _legacy_dict(
    result: RecommendationResult,
    metrics: _CoreMetrics,
    *,
    profile: RecommendationProfile,
    year: int,
    parameters: dict,
) -> dict[str, Any]:
    recommendations: dict[str, list[dict[str, Any]]] = {"冲": [], "稳": [], "保": []}
    for item in result.items:
        recommendations[item.strategy].append({
            "school_name": item.school_name,
            "school_level": item.school_level,
            "city": item.city,
            "is_in_province": item.province_match,
            "min_score": item.min_score,
            "min_rank": item.min_rank,
            "delta": item.delta,
            "related_majors": item.related_majors,
            "remark": item.remarks,
            "major_groups": [group.to_dict() for group in item.major_groups],
            "match_reason": item.match_reason,
            "recommend_level": item.recommend_level,
            "strategy": item.strategy,
        })
    return {
        "recommendations": recommendations,
        "statistics": {
            "total_985_in_range": metrics.total_985,
            "total_211_in_range": metrics.total_211,
            "total_in_province_in_range": metrics.total_in_province,
            "zero_score_excluded_count": result.zero_score_excluded_count,
        },
        "meta": {
            "reference_rank": profile.rank,
            "data_year": year,
            "subject_group": profile.subject_group,
            "delta_range": [parameters["delta_lo"], parameters["delta_hi"]],
            "tier_thresholds": _tier_threshold_labels(parameters),
            "tier_caps": parameters["tier_caps"],
            "target_city": list(profile.target_cities),
            "secondary_subjects": sorted(profile.secondary_subjects),
            "filtered_by_subject": result.excluded_by_subject_count,
        },
    }


def recommend_schools(
    rows: Sequence[Mapping[str, Any]],
    profile: RecommendationProfile | Mapping[str, Any] | None | object = _UNSET,
    *,
    year: int | None | object = _UNSET,
    estimated_prov_rank: object = _UNSET,
    subject_group: str | object = _UNSET,
    target_province: str | None | object = _UNSET,
    target_major_category: Optional[list[str]] | object = _UNSET,
    target_city: Optional[list[str]] | object = _UNSET,
    target_schools_preference: Optional[list[str]] | object = _UNSET,
    secondary_subjects: Optional[list[str]] | object = _UNSET,
    params: Optional[dict] | object = _UNSET,
) -> RecommendationResult | dict[str, Any]:
    """Run the immutable API, or dispatch old keywords to a one-release bridge."""

    legacy_only_values = (
        year, estimated_prov_rank, subject_group, target_province,
        target_major_category, target_city, target_schools_preference,
        secondary_subjects, params,
    )
    if profile is not _UNSET:
        if any(value is not _UNSET for value in legacy_only_values):
            raise SchoolRecommendError("REC_003", "新接口不能混用旧接口关键字")
        result, _metrics = _recommend_core(
            rows, _profile(profile), params_from_config()
        )
        return result

    if year is _UNSET or estimated_prov_rank is _UNSET or _strict_int(year) is None:
        raise SchoolRecommendError("REC_001", "旧接口缺少有效数据年份")
    parameters = (
        params_from_config()
        if params is _UNSET or params is None
        else params
    )
    target = None if target_province is _UNSET else target_province
    if not target:
        target = next((
            str(row.get("province")) for row in rows
            if isinstance(row, Mapping) and _canonical_province(row.get("province"))
        ), "湖北")
    try:
        normalized_profile = RecommendationProfile(
            rank=estimated_prov_rank,
            target_province=target,
            subject_group=(
                "物理" if subject_group is _UNSET or not subject_group else subject_group
            ),
            secondary_subjects=(
                () if secondary_subjects is _UNSET or secondary_subjects is None
                else secondary_subjects
            ),
            target_major_categories=(
                () if target_major_category is _UNSET or target_major_category is None
                else target_major_category
            ),
            target_cities=(
                () if target_city is _UNSET or target_city is None else target_city
            ),
            target_schools=(
                () if target_schools_preference is _UNSET or target_schools_preference is None
                else target_schools_preference
            ),
        )
    except (TypeError, ValueError) as error:
        raise SchoolRecommendError("REC_001", "旧接口推荐参数无效") from error
    adapted = _legacy_rows(rows, year=year, target_province=target)
    result, metrics = _recommend_core(adapted, normalized_profile, parameters)
    return _legacy_dict(result, metrics, profile=normalized_profile,
                        year=year, parameters=parameters)


__all__ = [
    "SchoolRecommendError",
    "is_in_province",
    "params_from_config",
    "parse_secondary_subjects",
    "recommend_schools",
]
