"""Build a host-neutral, deterministic research query plan.

The module is deliberately file- and network-free except for its CLI input
boundary.  Query execution belongs to the host; this contract only describes
what must be researched and how missing current-year material is represented.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

if __package__:
    from .contracts import RecommendationProfile
    from .path_recommend import validate_public_output_text
    from .province_registry import (
        ProvinceConfig,
        _parse_config,
        canonical_subject_selection_key,
    )
else:  # pragma: no cover - exercised by the real CLI and flat-import tests
    from contracts import RecommendationProfile
    from path_recommend import validate_public_output_text
    from province_registry import (
        ProvinceConfig,
        _parse_config,
        canonical_subject_selection_key,
    )


_SCHEMA_VERSION = "1.0"
_KINDS = frozenset({"score_table", "admission", "joy_report", "pathway_policy"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FIELD_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EXPECTATIONS = frozenset(
    {"current_year_not_yet_expected", "explicit_older_year"}
)
_FRESHNESS_BY_EXPECTATION = {
    "current_year_not_yet_expected": "query_exact_year_and_record_unpublished",
    "explicit_older_year": "query_exact_historical_year_no_silent_substitution",
}
_TASK_FIELDS = frozenset(
    {
        "task_id",
        "kind",
        "province",
        "year",
        "subject_group",
        "query_variants",
        "preferred_source_tiers",
        "max_candidates",
        "freshness_rule",
        "required_extraction_fields",
        "availability_expectation",
    }
)
_PLAN_FIELDS = frozenset(
    {"schema_version", "province", "exam_year", "subject_group", "tasks"}
)
_PROFILE_FIELDS = frozenset(
    {
        "schema_version",
        "province",
        "subject_mode",
        "subject_group",
        "secondary_subjects",
        "rank",
        "grade",
        "current_year",
        "target_major_categories",
        "target_cities",
        "target_schools",
        "eligibility_facts",
    }
)
_MAX_INPUT_BYTES = 1024 * 1024


class QueryPlanCapabilityError(RuntimeError):
    """A capability required to emit the plan is unavailable."""


def _normalize_exam_year(value: Any) -> int:
    if isinstance(value, (bool, str, bytes, bytearray)):
        raise TypeError("exam_year must be a finite mathematical integer")
    try:
        finite = math.isfinite(value)
        normalized = int(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise TypeError("exam_year must be a finite mathematical integer") from error
    if not finite or value != normalized:
        raise ValueError("exam_year must be a finite mathematical integer")
    if not 2000 <= normalized <= 2100:
        raise ValueError("exam_year must be between 2000 and 2100")
    return normalized


def _public_text(value: Any, name: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
    if len(value) > maximum or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must be bounded single-line public text")
    try:
        validate_public_output_text(value)
    except ValueError as error:
        raise ValueError(f"{name} contains private or non-public text") from error
    return value


def _text_collection(
    value: Any,
    name: str,
    *,
    nonempty: bool = False,
    public: bool = True,
    sort_values: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be a collection of strings")
    try:
        items = tuple(value)
    except TypeError as error:
        raise TypeError(f"{name} must be a collection of strings") from error
    normalized: list[str] = []
    for item in items:
        if public:
            normalized.append(_public_text(item, f"{name} item", maximum=512))
        else:
            if not isinstance(item, str) or _FIELD_ID.fullmatch(item) is None:
                raise ValueError(f"{name} must contain safe field identifiers")
            normalized.append(item)
    if nonempty and not normalized:
        raise ValueError(f"{name} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} must not contain duplicates")
    if sort_values:
        normalized.sort()
    return tuple(normalized)


def _task_identity_payload(task: "QueryTask") -> dict[str, Any]:
    return {
        "kind": task.kind,
        "province": task.province,
        "year": task.year,
        "subject_group": task.subject_group,
        "query_variants": list(task.query_variants),
        "preferred_source_tiers": list(task.preferred_source_tiers),
        "max_candidates": task.max_candidates,
        "freshness_rule": task.freshness_rule,
        "required_extraction_fields": list(task.required_extraction_fields),
        "availability_expectation": task.availability_expectation,
    }


def _expected_task_id(task: "QueryTask") -> str:
    normalized = json.dumps(
        _task_identity_payload(task),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(normalized).hexdigest()[:24]
    return f"{task.kind.replace('_', '-')}:{digest}"


@dataclass(frozen=True)
class QueryTask:
    task_id: str
    kind: str
    province: str
    year: int
    subject_group: str
    query_variants: tuple[str, ...]
    preferred_source_tiers: tuple[str, ...]
    max_candidates: int
    freshness_rule: str
    required_extraction_fields: tuple[str, ...]
    availability_expectation: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in _KINDS:
            raise ValueError("unsupported query kind")
        object.__setattr__(self, "province", _public_text(self.province, "province"))
        if not isinstance(self.year, int) or isinstance(self.year, bool):
            raise TypeError("task year must be an integer")
        if not 1998 <= self.year <= 2100:
            raise ValueError("task year is outside the supported query window")
        object.__setattr__(
            self, "subject_group", _public_text(self.subject_group, "subject_group")
        )
        object.__setattr__(
            self,
            "query_variants",
            _text_collection(self.query_variants, "query_variants", nonempty=True),
        )
        tiers = _text_collection(
            self.preferred_source_tiers,
            "preferred_source_tiers",
            nonempty=True,
        )
        if tiers != ("A", "B", "C"):
            raise ValueError("preferred_source_tiers must be exactly A, B, C")
        object.__setattr__(self, "preferred_source_tiers", tiers)
        if self.max_candidates != 10 or isinstance(self.max_candidates, bool):
            raise ValueError("max_candidates must be exactly 10")
        if self.availability_expectation not in _EXPECTATIONS:
            raise ValueError("unsupported availability expectation")
        expected_freshness = _FRESHNESS_BY_EXPECTATION[self.availability_expectation]
        if self.freshness_rule != expected_freshness:
            raise ValueError("freshness rule and availability expectation disagree")
        object.__setattr__(
            self,
            "required_extraction_fields",
            _text_collection(
                self.required_extraction_fields,
                "required_extraction_fields",
                nonempty=True,
                public=False,
            ),
        )
        if not isinstance(self.task_id, str) or _SAFE_ID.fullmatch(self.task_id) is None:
            raise ValueError("task_id must use the evidence safe-ID syntax")
        if self.task_id != _expected_task_id(self):
            raise ValueError("task_id does not match normalized task content")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "province": self.province,
            "year": self.year,
            "subject_group": self.subject_group,
            "query_variants": list(self.query_variants),
            "preferred_source_tiers": list(self.preferred_source_tiers),
            "max_candidates": self.max_candidates,
            "freshness_rule": self.freshness_rule,
            "required_extraction_fields": list(self.required_extraction_fields),
            "availability_expectation": self.availability_expectation,
        }


@dataclass(frozen=True)
class QueryPlan:
    schema_version: str
    province: str
    exam_year: int
    subject_group: str
    tasks: tuple[QueryTask, ...]

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError("unsupported query-plan schema version")
        object.__setattr__(self, "province", _public_text(self.province, "province"))
        if not isinstance(self.exam_year, int) or isinstance(self.exam_year, bool):
            raise TypeError("exam_year must be a normalized integer")
        if not 2000 <= self.exam_year <= 2100:
            raise ValueError("exam_year must be between 2000 and 2100")
        object.__setattr__(
            self, "subject_group", _public_text(self.subject_group, "subject_group")
        )
        if isinstance(self.tasks, (str, bytes, bytearray)):
            raise TypeError("tasks must be a collection of QueryTask records")
        try:
            tasks = tuple(self.tasks)
        except TypeError as error:
            raise TypeError("tasks must be a collection of QueryTask records") from error
        if not tasks or any(not isinstance(task, QueryTask) for task in tasks):
            raise TypeError("tasks must contain QueryTask records")
        object.__setattr__(self, "tasks", tasks)
        if len({task.task_id for task in tasks}) != len(tasks):
            raise ValueError("query plan contains duplicate task IDs")
        window = {self.exam_year - 2, self.exam_year - 1, self.exam_year}
        for task in tasks:
            if task.province != self.province or task.subject_group != self.subject_group:
                raise ValueError("task context does not match query plan")
            if task.year not in window:
                raise ValueError("task year is outside the explicit three-year window")
            expectation = (
                "current_year_not_yet_expected"
                if task.year == self.exam_year
                else "explicit_older_year"
            )
            if task.availability_expectation != expectation:
                raise ValueError("task availability does not match its explicit year")
        by_kind = {
            kind: tuple(task for task in tasks if task.kind == kind) for kind in _KINDS
        }
        for required_kind in ("score_table", "admission"):
            if {task.year for task in by_kind[required_kind]} != window:
                raise ValueError(f"{required_kind} tasks must cover the exact three-year window")
        joy_years = tuple(task.year for task in by_kind["joy_report"])
        if len(joy_years) > 3 or len(set(joy_years)) != len(joy_years):
            raise ValueError("joy-report tasks must use at most three explicit years")
        if any(task.year != self.exam_year for task in by_kind["pathway_policy"]):
            raise ValueError("pathway-policy tasks must target the explicit exam year")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "province": self.province,
            "exam_year": self.exam_year,
            "subject_group": self.subject_group,
            "tasks": [task.to_dict() for task in self.tasks],
        }


_EXTRACTION_FIELDS = {
    "score_table": (
        "province",
        "year",
        "subject_group",
        "score",
        "cumulative_rank",
        "source_url",
        "publisher",
        "publication_date",
    ),
    "admission": (
        "province",
        "year",
        "subject_group",
        "institution",
        "program_group",
        "min_score",
        "min_rank",
        "source_url",
        "publisher",
        "publication_date",
    ),
    "joy_report": (
        "province",
        "year",
        "high_school",
        "metric",
        "value",
        "source_url",
        "publisher",
        "publication_date",
    ),
    "pathway_policy": (
        "province",
        "year",
        "pathway",
        "eligibility",
        "institutions",
        "application_window",
        "constraints",
        "source_url",
        "publisher",
        "publication_date",
    ),
}


def _make_task(
    *,
    kind: str,
    province: str,
    year: int,
    subject_group: str,
    query_variants: tuple[str, ...],
    exam_year: int,
) -> QueryTask:
    expectation = (
        "current_year_not_yet_expected"
        if year == exam_year
        else "explicit_older_year"
    )
    values = {
        "task_id": "temporary",
        "kind": kind,
        "province": province,
        "year": year,
        "subject_group": subject_group,
        "query_variants": query_variants,
        "preferred_source_tiers": ("A", "B", "C"),
        "max_candidates": 10,
        "freshness_rule": _FRESHNESS_BY_EXPECTATION[expectation],
        "required_extraction_fields": _EXTRACTION_FIELDS[kind],
        "availability_expectation": expectation,
    }
    prototype = object.__new__(QueryTask)
    for name, value in values.items():
        object.__setattr__(prototype, name, value)
    values["task_id"] = _expected_task_id(prototype)
    return QueryTask(**values)


def build_query_plan(
    profile: RecommendationProfile,
    province: ProvinceConfig,
    exam_year: Any,
    *,
    high_school_name: str | None = None,
    requested_pathways: Any = (),
) -> QueryPlan:
    """Return an immutable plan for one anonymous recommendation profile."""

    if not isinstance(profile, RecommendationProfile):
        raise TypeError("profile must be a RecommendationProfile")
    if not isinstance(province, ProvinceConfig):
        raise TypeError("province must be a strict ProvinceConfig")
    year = _normalize_exam_year(exam_year)
    province_name = _public_text(province.province, "province")
    if profile.target_province != province_name:
        raise ValueError("profile and province configuration do not match")
    primary = _public_text(profile.subject_group, "profile subject_group")
    secondary = tuple(
        _public_text(item, "profile secondary_subject")
        for item in profile.secondary_subjects
    )
    subject_group = canonical_subject_selection_key(province, primary, list(secondary))
    _public_text(subject_group, "canonical subject_group")

    school = None
    if high_school_name is not None:
        school = _public_text(high_school_name, "high_school_name")
    pathways = _text_collection(
        requested_pathways,
        "requested_pathways",
        sort_values=True,
    )

    authority = f"{province_name}教育考试院"
    years = (year - 2, year - 1, year)
    tasks: list[QueryTask] = []
    for task_year in years:
        tasks.append(
            _make_task(
                kind="score_table",
                province=province_name,
                year=task_year,
                subject_group=subject_group,
                exam_year=year,
                query_variants=(
                    f"{authority} {task_year} {subject_group} 一分一段表",
                    f"{province_name} {task_year} {subject_group} 一分一段",
                ),
            )
        )
    for task_year in years:
        tasks.append(
            _make_task(
                kind="admission",
                province=province_name,
                year=task_year,
                subject_group=subject_group,
                exam_year=year,
                query_variants=(
                    f"{authority} {task_year} {subject_group} 普通批 投档线",
                    f"{province_name} {task_year} {subject_group} 院校专业组 录取",
                ),
            )
        )
    if school is not None:
        for task_year in years:
            tasks.append(
                _make_task(
                    kind="joy_report",
                    province=province_name,
                    year=task_year,
                    subject_group=subject_group,
                    exam_year=year,
                    query_variants=(
                        f"{province_name} {school} {task_year} {subject_group} 高考喜报",
                        f"{school} {task_year} {subject_group} 高考成绩 光荣榜",
                        f"{authority} {school} {task_year} {subject_group} 升学成果",
                    ),
                )
            )
    for pathway in pathways:
        tasks.append(
            _make_task(
                kind="pathway_policy",
                province=province_name,
                year=year,
                subject_group=subject_group,
                exam_year=year,
                query_variants=(
                    f"{authority} {year} {pathway} 政策",
                    f"{province_name} {year} {subject_group} {pathway} 招生",
                ),
            )
        )
    return QueryPlan(
        schema_version=_SCHEMA_VERSION,
        province=province_name,
        exam_year=year,
        subject_group=subject_group,
        tasks=tuple(tasks),
    )


def validate_query_plan_payload(payload: Any) -> QueryPlan:
    """Validate JSON structure plus year, context, and task-ID semantics."""

    if not isinstance(payload, dict) or set(payload) != _PLAN_FIELDS:
        raise ValueError("query-plan object fields do not match the contract")
    raw_tasks = payload["tasks"]
    if not isinstance(raw_tasks, list):
        raise TypeError("query-plan tasks must be an array")
    tasks: list[QueryTask] = []
    for raw_task in raw_tasks:
        if not isinstance(raw_task, dict) or set(raw_task) != _TASK_FIELDS:
            raise ValueError("query-task object fields do not match the contract")
        tasks.append(QueryTask(**raw_task))
    return QueryPlan(
        schema_version=payload["schema_version"],
        province=payload["province"],
        exam_year=payload["exam_year"],
        subject_group=payload["subject_group"],
        tasks=tuple(tasks),
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _strict_json_file(path_value: Any) -> Any:
    try:
        path = Path(path_value)
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_size > _MAX_INPUT_BYTES
        ):
            raise ValueError("unsafe input file")
        data = path.read_bytes()
        after = os.lstat(path)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("input file changed while reading")
        text = data.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("input is not strict UTF-8 JSON") from error


def _profile_array(payload: dict[str, Any], name: str) -> tuple[str, ...]:
    value = payload[name]
    if not isinstance(value, list):
        raise TypeError("profile collection must be an array")
    return _text_collection(value, f"profile {name}")


def _load_profile(path: Any) -> tuple[RecommendationProfile, str, int]:
    payload = _strict_json_file(path)
    if not isinstance(payload, dict) or set(payload) != _PROFILE_FIELDS:
        raise ValueError("profile fields do not match the strict anonymous contract")
    if payload["schema_version"] != "1.0":
        raise ValueError("unsupported profile schema version")
    province = _public_text(payload["province"], "profile province")
    subject_mode = _public_text(payload["subject_mode"], "profile subject_mode")
    subject_group = _public_text(payload["subject_group"], "profile subject_group")
    _public_text(payload["grade"], "profile grade")
    current_year = _normalize_exam_year(payload["current_year"])
    secondary = _profile_array(payload, "secondary_subjects")
    majors = _profile_array(payload, "target_major_categories")
    cities = _profile_array(payload, "target_cities")
    schools = _profile_array(payload, "target_schools")
    _profile_array(payload, "eligibility_facts")
    profile = RecommendationProfile(
        rank=payload["rank"],
        target_province=province,
        subject_group=subject_group,
        secondary_subjects=frozenset(secondary),
        target_major_categories=majors,
        target_cities=cities,
        target_schools=schools,
    )
    return profile, subject_mode, current_year


def _load_province(path: Any) -> ProvinceConfig:
    payload = _strict_json_file(path)
    if not isinstance(payload, dict):
        raise ValueError("province input must be an object")
    return _parse_config(payload, Path(path).parent)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic research query plan")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--province", required=True)
    parser.add_argument("--exam-year", required=True)
    parser.add_argument("--pathway", action="append", default=[])
    parser.add_argument("--high-school")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        try:
            raw_exam_year: Any = int(arguments.exam_year)
        except (TypeError, ValueError) as error:
            raise ValueError("exam-year CLI value must be an integer") from error
        exam_year = _normalize_exam_year(raw_exam_year)
        profile, subject_mode, profile_year = _load_profile(arguments.profile)
        province = _load_province(arguments.province)
        if subject_mode != province.mode or profile_year != exam_year:
            raise ValueError("profile context does not match requested plan")
        plan = build_query_plan(
            profile,
            province,
            exam_year,
            high_school_name=arguments.high_school,
            requested_pathways=arguments.pathway,
        )
        payload = plan.to_dict()
        validate_query_plan_payload(payload)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stdout.write(encoded + "\n")
        return 0
    except QueryPlanCapabilityError:
        sys.stderr.write("query-plan: missing capability\n")
        return 3
    except (TypeError, ValueError, OSError, UnicodeError):
        sys.stderr.write("query-plan: invalid input\n")
        return 2


def _reconfigure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


__all__ = [
    "QueryPlan",
    "QueryPlanCapabilityError",
    "QueryTask",
    "build_query_plan",
    "validate_query_plan_payload",
]


if __name__ == "__main__":
    _reconfigure_utf8()
    raise SystemExit(main())
