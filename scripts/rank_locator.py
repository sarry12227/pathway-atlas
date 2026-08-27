"""Deterministic multi-evidence provincial-rank location."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import json
import math
import re
from statistics import median
from typing import Any, Iterable, Mapping

if __package__:
    from .contracts import EvidenceStatus
    from .planning_profile import PlanningProfile
    from .rank_calc import RankAnchor, RankScope, estimate_rank_from_anchors
    from .validate_data import ValidatedScoreRow
    from .year_fallback import year_window
else:  # pragma: no cover - flat scripts-path compatibility
    from contracts import EvidenceStatus
    from planning_profile import PlanningProfile
    from rank_calc import RankAnchor, RankScope, estimate_rank_from_anchors
    from validate_data import ValidatedScoreRow
    from year_fallback import year_window


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CHANNEL_KINDS = frozenset(
    {"joint_exam", "school_anchor", "score_distribution", "group_prior"}
)
_ACCEPTED = frozenset(
    {EvidenceStatus.OFFICIAL, EvidenceStatus.CORROBORATED, EvidenceStatus.REFERENCE}
)
_SOURCE_MINIMUM = {
    EvidenceStatus.OFFICIAL: 1,
    EvidenceStatus.CORROBORATED: 2,
    EvidenceStatus.REFERENCE: 3,
}
_CHANNEL_VALUE_FIELDS = frozenset(
    {
        "schema_version",
        "channel_id",
        "kind",
        "profile_digest",
        "province",
        "subject_group",
        "high_school",
        "class_level",
        "year",
        "lower_percentile",
        "central_percentile",
        "upper_percentile",
        "coverage",
        "comparability",
        "backtest_error",
    }
)
_ANCHOR_VALUE_FIELDS = frozenset(
    {
        "schema_version",
        "profile_digest",
        "province",
        "subject_group",
        "class_level",
        "anchor_id",
        "year",
        "school_name",
        "scope_type",
        "scope_value",
        "school_rank",
        "province_rank",
        "school_score",
        "source_ids",
        "evidence_status",
        "coverage_status",
        "coverage_min_school_rank",
        "coverage_max_school_rank",
    }
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    raise TypeError(f"unsupported rank-scenario value: {type(value).__name__}")


def _status(value: Any) -> EvidenceStatus:
    if isinstance(value, EvidenceStatus):
        return value
    if not isinstance(value, str):
        raise TypeError("evidence status must be text")
    return EvidenceStatus(value)


def _bounded_number(
    value: Any,
    name: str,
    *,
    minimum: float,
    maximum: float,
    optional: bool = False,
) -> float | None:
    if value is None and optional:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"{name} is outside its supported range")
    return result


def _safe_ids(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be an ID collection")
    items = tuple(value)
    if not items or len(items) != len(set(items)):
        raise ValueError(f"{name} must contain unique IDs")
    if any(not isinstance(item, str) or _SAFE_ID.fullmatch(item) is None for item in items):
        raise ValueError(f"{name} contains an unsafe ID")
    return tuple(sorted(items))


@dataclass(frozen=True)
class _Channel:
    channel_id: str
    kind: str
    year: int
    lower_percentile: float
    central_percentile: float
    upper_percentile: float
    coverage: float
    comparability: float
    backtest_error: float | None
    source_ids: tuple[str, ...]
    status: EvidenceStatus


@dataclass(frozen=True, init=False)
class RankScenario:
    status: EvidenceStatus
    basis: str
    optimistic_rank: int | None
    central_rank: int | None
    conservative_rank: int | None
    confidence: str
    source_ids: tuple[str, ...]
    contributing_years: tuple[int, ...]
    backtest_error: float | None
    reasons: tuple[str, ...]
    channel_kinds: tuple[str, ...]
    channel_statuses: tuple[str, ...]
    rejected_channel_count: int

    def __init__(self) -> None:
        raise TypeError("RankScenario is factory-only")

    @classmethod
    def _create(cls, **values: Any) -> "RankScenario":
        if set(values) != {item.name for item in fields(cls)}:
            raise TypeError("RankScenario factory fields do not match the contract")
        status = _status(values["status"])
        if status not in {
            EvidenceStatus.OFFICIAL,
            EvidenceStatus.INFERRED,
            EvidenceStatus.MISSING,
            EvidenceStatus.CONFLICT,
        }:
            raise ValueError("unsupported rank scenario status")
        values["status"] = status
        ranks = (
            values["optimistic_rank"],
            values["central_rank"],
            values["conservative_rank"],
        )
        if status in {EvidenceStatus.OFFICIAL, EvidenceStatus.INFERRED}:
            if any(
                not isinstance(rank, int) or isinstance(rank, bool) or rank < 1
                for rank in ranks
            ):
                raise TypeError("numeric rank scenarios require positive integer bounds")
            if not ranks[0] <= ranks[1] <= ranks[2]:
                raise ValueError("rank scenario bounds must be ordered")
        elif any(rank is not None for rank in ranks):
            raise ValueError("non-numeric rank scenarios cannot contain bounds")
        confidence = values["confidence"]
        if confidence not in {"high", "medium", "low", "none"}:
            raise ValueError("unsupported rank scenario confidence")
        if status in {EvidenceStatus.MISSING, EvidenceStatus.CONFLICT} and confidence != "none":
            raise ValueError("non-numeric scenarios require no confidence")
        values["source_ids"] = (
            _safe_ids(values["source_ids"], "source_ids")
            if values["source_ids"]
            else ()
        )
        years = tuple(values["contributing_years"])
        if len(years) != len(set(years)) or any(
            not isinstance(year, int)
            or isinstance(year, bool)
            or year < 2000
            or year > 2100
            for year in years
        ):
            raise ValueError("contributing_years must be unique supported years")
        values["contributing_years"] = tuple(sorted(years))
        for name in ("reasons", "channel_kinds", "channel_statuses"):
            items = tuple(values[name])
            if len(items) != len(set(items)) or any(
                not isinstance(item, str) or _SAFE_ID.fullmatch(item) is None
                for item in items
            ):
                raise ValueError(f"{name} must contain unique safe identifiers")
            values[name] = tuple(sorted(items))
        rejected = values["rejected_channel_count"]
        if not isinstance(rejected, int) or isinstance(rejected, bool) or rejected < 0:
            raise TypeError("rejected_channel_count must be a non-negative integer")
        error = values["backtest_error"]
        if error is not None:
            values["backtest_error"] = _bounded_number(
                error, "backtest_error", minimum=0, maximum=1
            )
        instance = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(instance, name, value)
        return instance

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _json_safe(getattr(self, item.name)) for item in fields(self)}


def _missing(reasons: Iterable[str], rejected: int = 0) -> RankScenario:
    return RankScenario._create(
        status=EvidenceStatus.MISSING,
        basis="unavailable",
        optimistic_rank=None,
        central_rank=None,
        conservative_rank=None,
        confidence="none",
        source_ids=(),
        contributing_years=(),
        backtest_error=None,
        reasons=tuple(sorted(set(reasons))),
        channel_kinds=(),
        channel_statuses=(),
        rejected_channel_count=rejected,
    )


def _score_payload(row: ValidatedScoreRow) -> dict[str, str | int]:
    if not isinstance(row, ValidatedScoreRow):
        raise TypeError("score_rows must contain ValidatedScoreRow records")
    return row.to_dict()


def _matching_score_rows(
    profile: PlanningProfile,
    score_rows: Iterable[ValidatedScoreRow],
) -> tuple[dict[str, str | int], ...]:
    window = set(year_window(profile.exam_year))
    rows = tuple(_score_payload(row) for row in score_rows)
    return tuple(
        sorted(
            (
                row
                for row in rows
                if row["subject_group"] == profile.subject_group and row["year"] in window
            ),
            key=lambda row: (-int(row["year"]), -int(row["score"])),
        )
    )


def _cohort_context(
    profile: PlanningProfile,
    score_rows: Iterable[ValidatedScoreRow],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    rows = _matching_score_rows(profile, score_rows)
    by_year: dict[int, int] = {}
    for row in rows:
        year = int(row["year"])
        by_year[year] = max(by_year.get(year, 0), int(row["cumulative_count"]))
    years = tuple(sorted(by_year, reverse=True)[:3])
    cohorts = tuple(by_year[year] for year in years)
    return years, cohorts


def _official_scenario(
    profile: PlanningProfile,
    rows: tuple[dict[str, str | int], ...],
) -> RankScenario | None:
    official = tuple(
        item for item in profile.rank_observations if item.scope == "province_official"
    )
    if not official:
        return None
    latest = max(official, key=lambda item: item.exam_date)
    if latest.rank is not None:
        return RankScenario._create(
            status=EvidenceStatus.OFFICIAL,
            basis="official_province_rank",
            optimistic_rank=latest.rank,
            central_rank=latest.rank,
            conservative_rank=latest.rank,
            confidence="high",
            source_ids=("profile-official-rank",),
            contributing_years=(int(latest.exam_date[:4]),),
            backtest_error=0.0,
            reasons=("user_supplied_official_rank",),
            channel_kinds=("official_rank",),
            channel_statuses=("official",),
            rejected_channel_count=0,
        )
    if latest.score is None:
        return None
    candidates = tuple(row for row in rows if int(row["score"]) == latest.score)
    if not candidates:
        return None
    selected = candidates[0]
    selected_year = int(selected["year"])
    rank = int(selected["rank"])
    fallback = profile.exam_year - selected_year
    return RankScenario._create(
        status=EvidenceStatus.OFFICIAL,
        basis="official_score_table",
        optimistic_rank=rank,
        central_rank=rank,
        conservative_rank=rank,
        confidence="high",
        source_ids=(f"score-table:{selected_year}",),
        contributing_years=(selected_year,),
        backtest_error=0.0,
        reasons=(f"year_fallback:{fallback}",),
        channel_kinds=("official_score_table",),
        channel_statuses=("official",),
        rejected_channel_count=0,
    )


def _fact_mapping(value: Any) -> Mapping[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("evidence facts must be mappings")
    return value


def _fact_channel(profile: PlanningProfile, raw: Any) -> _Channel:
    fact = _fact_mapping(raw)
    field = fact.get("field")
    value = fact.get("value")
    if not isinstance(field, str) or not field.startswith("rank_channel:"):
        raise ValueError("fact is not a rank channel")
    if not isinstance(value, Mapping) or set(value) != _CHANNEL_VALUE_FIELDS:
        raise ValueError("rank channel value fields do not match the contract")
    channel_id = value["channel_id"]
    if field != f"rank_channel:{channel_id}":
        raise ValueError("rank channel field does not match its ID")
    if not isinstance(channel_id, str) or _SAFE_ID.fullmatch(channel_id) is None:
        raise ValueError("rank channel ID is unsafe")
    kind = value["kind"]
    if kind not in _CHANNEL_KINDS - {"school_anchor"}:
        raise ValueError("unsupported authenticated rank channel kind")
    if value["schema_version"] != "1.0" or value["profile_digest"] != profile.digest:
        raise ValueError("rank channel is not bound to this profile")
    for field_name, expected in (
        ("province", profile.province),
        ("subject_group", profile.subject_group),
        ("high_school", profile.high_school),
        ("class_level", profile.class_level),
    ):
        if value[field_name] != expected:
            raise ValueError("rank channel context does not match the profile")
    year = value["year"]
    if (
        not isinstance(year, int)
        or isinstance(year, bool)
        or year not in year_window(profile.exam_year)
    ):
        raise ValueError("rank channel year is outside the fallback window")
    lower = _bounded_number(
        value["lower_percentile"], "lower_percentile", minimum=0, maximum=1
    )
    central = _bounded_number(
        value["central_percentile"], "central_percentile", minimum=0, maximum=1
    )
    upper = _bounded_number(
        value["upper_percentile"], "upper_percentile", minimum=0, maximum=1
    )
    assert lower is not None and central is not None and upper is not None
    if not lower <= central <= upper:
        raise ValueError("rank channel percentile bounds must be ordered")
    coverage = _bounded_number(value["coverage"], "coverage", minimum=0.01, maximum=1)
    comparability = _bounded_number(
        value["comparability"], "comparability", minimum=0.01, maximum=1
    )
    error = _bounded_number(
        value["backtest_error"], "backtest_error", minimum=0, maximum=1, optional=True
    )
    sources = _safe_ids(fact.get("source_ids"), "rank channel source_ids")
    status = _status(fact.get("status"))
    if status not in _ACCEPTED or len(sources) < _SOURCE_MINIMUM[status]:
        raise ValueError("rank channel does not meet the evidence threshold")
    return _Channel(
        channel_id=channel_id,
        kind=kind,
        year=year,
        lower_percentile=lower,
        central_percentile=central,
        upper_percentile=upper,
        coverage=coverage,
        comparability=comparability,
        backtest_error=error,
        source_ids=sources,
        status=status,
    )


def _fact_anchor(profile: PlanningProfile, raw: Any) -> RankAnchor:
    fact = _fact_mapping(raw)
    field = fact.get("field")
    value = fact.get("value")
    if not isinstance(field, str) or not field.startswith("rank_anchor:"):
        raise ValueError("fact is not a rank anchor")
    if not isinstance(value, Mapping) or set(value) != _ANCHOR_VALUE_FIELDS:
        raise ValueError("rank anchor value fields do not match the contract")
    anchor_id = value["anchor_id"]
    if field != f"rank_anchor:{anchor_id}":
        raise ValueError("rank anchor field does not match its ID")
    if value["schema_version"] != "1.0" or value["profile_digest"] != profile.digest:
        raise ValueError("rank anchor is not bound to this profile")
    if (
        value["province"] != profile.province
        or value["subject_group"] != profile.subject_group
        or value["class_level"] != profile.class_level
    ):
        raise ValueError("rank anchor context does not match the profile")
    if value["year"] not in year_window(profile.exam_year):
        raise ValueError("rank anchor year is outside the fallback window")
    outer_sources = _safe_ids(fact.get("source_ids"), "rank anchor source_ids")
    inner_sources = _safe_ids(value["source_ids"], "rank anchor value source_ids")
    outer_status = _status(fact.get("status"))
    inner_status = _status(value["evidence_status"])
    if outer_sources != inner_sources or outer_status != inner_status:
        raise ValueError("rank anchor projection conflicts with its evidence fact")
    if outer_status not in _ACCEPTED or len(outer_sources) < _SOURCE_MINIMUM[outer_status]:
        raise ValueError("rank anchor does not meet the evidence threshold")
    return RankAnchor(
        anchor_id=anchor_id,
        year=value["year"],
        school_name=value["school_name"],
        scope_type=value["scope_type"],
        scope_value=value["scope_value"],
        school_rank=value["school_rank"],
        province_rank=value["province_rank"],
        school_score=value["school_score"],
        source_ids=inner_sources,
        evidence_status=inner_status,
        coverage_status=value["coverage_status"],
        coverage_min_school_rank=value["coverage_min_school_rank"],
        coverage_max_school_rank=value["coverage_max_school_rank"],
    )


def _integer_median(values: tuple[int, ...]) -> int:
    return int(median(values))


def _school_channel(
    profile: PlanningProfile,
    anchors: Iterable[RankAnchor],
    cohorts: tuple[int, ...],
) -> tuple[_Channel | None, tuple[int, ...]]:
    if not cohorts or profile.high_school is None or profile.class_level is None:
        return None, ()
    observations = tuple(
        item
        for item in profile.rank_observations
        if item.scope == "school" and item.rank is not None
    )
    if not observations:
        return None, ()
    latest = max(observations, key=lambda item: item.exam_date)
    window = set(year_window(profile.exam_year))
    usable = tuple(
        item
        for item in anchors
        if isinstance(item, RankAnchor)
        and item.year in window
        and item.school_name == profile.high_school
        and item.scope_type == RankScope.NAMED_PROGRAM
        and item.scope_value == profile.class_level
        and item.evidence_status in _ACCEPTED
        and item.coverage_status in _ACCEPTED
    )
    estimate = estimate_rank_from_anchors(usable, latest.score, latest.rank)
    if estimate.status != EvidenceStatus.INFERRED:
        return None, ()
    cohort = _integer_median(cohorts)
    offsets = tuple(item.province_rank - item.school_rank for item in usable)
    errors: list[int] = []
    for index, item in enumerate(usable):
        other = offsets[:index] + offsets[index + 1 :]
        if other:
            errors.append(abs(item.school_rank + _integer_median(other) - item.province_rank))
    backtest = (float(median(errors)) / cohort) if errors else None
    status_order = {
        EvidenceStatus.REFERENCE: 1,
        EvidenceStatus.CORROBORATED: 2,
        EvidenceStatus.OFFICIAL: 3,
    }
    anchor_status = min(
        (item.evidence_status for item in usable),
        key=lambda item: status_order[item],
    )
    return (
        _Channel(
            channel_id="school-anchor",
            kind="school_anchor",
            year=max(estimate.contributing_years),
            lower_percentile=max(0.0, estimate.lower_rank / cohort),
            central_percentile=min(1.0, estimate.median_rank / cohort),
            upper_percentile=min(1.0, estimate.upper_rank / cohort),
            coverage=min(1.0, estimate.usable_anchor_count / 3),
            comparability=1.0,
            backtest_error=backtest,
            source_ids=estimate.contributing_source_ids,
            status=anchor_status,
        ),
        estimate.contributing_years,
    )


def _weighted_median(
    channels: tuple[_Channel, ...],
    weights: tuple[float, ...],
    name: str,
) -> float:
    ordered = sorted(
        zip(channels, weights),
        key=lambda pair: (getattr(pair[0], name), pair[0].channel_id),
    )
    threshold = sum(weights) / 2
    running = 0.0
    for channel, weight in ordered:
        running += weight
        if running >= threshold:
            return float(getattr(channel, name))
    return float(getattr(ordered[-1][0], name))


def _weights(
    channels: tuple[_Channel, ...],
    target_year: int,
    cohort: int,
) -> tuple[tuple[float, ...], bool]:
    tested_errors = tuple(
        item.backtest_error
        for item in channels
        if item.backtest_error is not None
    )
    floor = max(1 / cohort, float(median(tested_errors)) if tested_errors else 0.02)
    tested: list[float] = []
    untested: list[float] = []
    for item in channels:
        recency = 0.5 ** (max(0, target_year - item.year) / 2)
        error = max(item.backtest_error if item.backtest_error is not None else floor, floor)
        weight = item.coverage * item.comparability * recency / (error * error)
        (untested if item.backtest_error is None else tested).append(weight)
    if not untested:
        return tuple(tested), False
    # Rebuild in original order after capping all untested channels to at most
    # one quarter of the final combined weight.
    tested_total = sum(tested)
    untested_total = sum(untested)
    cap_total = tested_total / 3 if tested_total > 0 else untested_total
    scale = min(1.0, cap_total / untested_total) if untested_total else 1.0
    result: list[float] = []
    for item in channels:
        recency = 0.5 ** (max(0, target_year - item.year) / 2)
        error = max(item.backtest_error if item.backtest_error is not None else floor, floor)
        weight = item.coverage * item.comparability * recency / (error * error)
        result.append(weight if item.backtest_error is not None else weight * scale)
    return tuple(result), True


def _volatility(profile: PlanningProfile) -> float:
    percentiles = tuple(
        item.rank / item.cohort_size
        for item in profile.rank_observations
        if item.rank is not None and item.cohort_size is not None
    )
    if len(percentiles) < 2:
        return 0.0
    return (max(percentiles) - min(percentiles)) / 2


def locate_rank(
    profile: PlanningProfile,
    *,
    evidence_facts: Iterable[Any],
    score_rows: Iterable[ValidatedScoreRow],
    anchors: Iterable[RankAnchor] = (),
) -> RankScenario:
    """Return an official or explicitly inferred rank scenario."""

    if not isinstance(profile, PlanningProfile):
        raise TypeError("profile must be a PlanningProfile")
    rows = _matching_score_rows(profile, score_rows)
    official = _official_scenario(profile, rows)
    if official is not None:
        return official
    cohort_years, cohorts = _cohort_context(profile, score_rows)
    if not cohorts:
        return _missing(("official_cohort_size_missing",))

    channels: list[_Channel] = []
    authenticated_anchors: list[RankAnchor] = []
    for anchor in anchors:
        if not isinstance(anchor, RankAnchor):
            raise TypeError("anchors must contain RankAnchor records")
        authenticated_anchors.append(anchor)
    rejected = 0
    for fact in evidence_facts:
        try:
            raw = _fact_mapping(fact)
            field = raw.get("field")
            if isinstance(field, str) and field.startswith("rank_anchor:"):
                authenticated_anchors.append(_fact_anchor(profile, raw))
            else:
                channels.append(_fact_channel(profile, raw))
        except (TypeError, ValueError):
            rejected += 1
    school, school_years = _school_channel(profile, authenticated_anchors, cohorts)
    if school is not None:
        channels.append(school)
    if not channels:
        return _missing(("calibration_evidence_missing",), rejected)
    ordered = tuple(sorted(channels, key=lambda item: item.channel_id))
    central_cohort = _integer_median(cohorts)
    weights, capped = _weights(ordered, profile.exam_year, central_cohort)
    lower = _weighted_median(ordered, weights, "lower_percentile")
    central = _weighted_median(ordered, weights, "central_percentile")
    upper = _weighted_median(ordered, weights, "upper_percentile")
    volatility = _volatility(profile)
    lower = max(0.0, lower - volatility)
    upper = min(1.0, upper + volatility)
    if lower > central:
        lower = central
    if upper < central:
        upper = central
    optimistic = max(1, math.ceil(lower * min(cohorts)))
    central_rank = max(1, math.ceil(central * central_cohort))
    conservative = max(1, math.ceil(upper * max(cohorts)))
    optimistic = min(optimistic, central_rank)
    conservative = max(conservative, central_rank)
    errors = tuple(
        item.backtest_error
        for item in ordered
        if item.backtest_error is not None
    )
    reasons = ["deterministic_weighted_median"]
    if capped:
        reasons.append("untested_weight_capped")
    if volatility:
        reasons.append("recent_exam_volatility_applied")
    confidence = "low" if capped or len(ordered) == 1 else "medium"
    basis = (
        "school_anchor_ensemble"
        if len(ordered) == 1 and school is not None
        else "multi_channel_ensemble"
    )
    return RankScenario._create(
        status=EvidenceStatus.INFERRED,
        basis=basis,
        optimistic_rank=optimistic,
        central_rank=central_rank,
        conservative_rank=conservative,
        confidence=confidence,
        source_ids=tuple(sorted({source for item in ordered for source in item.source_ids})),
        contributing_years=tuple(
            sorted({item.year for item in ordered} | set(school_years))
        ),
        backtest_error=float(median(errors)) if errors else None,
        reasons=tuple(sorted(reasons)),
        channel_kinds=tuple(sorted({item.kind for item in ordered})),
        channel_statuses=tuple(sorted({item.status.value for item in ordered})),
        rejected_channel_count=rejected,
    )


__all__ = ["RankScenario", "locate_rank"]
