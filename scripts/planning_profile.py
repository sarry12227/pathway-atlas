"""Immutable anonymous planning profiles for PathwayAtlas.

The module is the single normalization seam between the twenty-question
conversation and deterministic planning code.  It performs no file or network
I/O at import time.  The CLI exists for automation; interactive users never
need to create JSON themselves.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from types import MappingProxyType
from typing import Any
import unicodedata


_MISSING_LOCAL_CAPABILITY: str | None = None
if __package__:
    try:
        from .path_recommend import validate_public_output_text
    except ModuleNotFoundError as error:  # pragma: no cover - isolated CLI probe
        if error.name != f"{__package__}.path_recommend":
            raise
        _MISSING_LOCAL_CAPABILITY = "path_recommend"
else:  # pragma: no cover - exercised by the real flat CLI
    try:
        from path_recommend import validate_public_output_text
    except ModuleNotFoundError as error:
        if error.name != "path_recommend":
            raise
        _MISSING_LOCAL_CAPABILITY = "path_recommend"

if _MISSING_LOCAL_CAPABILITY is not None:
    validate_public_output_text = None


_SCHEMA_VERSION = "2.0"
_MAX_INPUT_BYTES = 1024 * 1024
_GRADES = frozenset({"高一", "高二", "高三"})
_SUBJECT_MODES = frozenset({"3+1+2", "3+3"})
_GENDERS = frozenset({"男", "女", "不便回答"})
_SCORE_BASES = frozenset({"原始分", "赋分", "原始分与赋分", "不确定"})
_OBSERVATION_SCOPES = frozenset(
    {"school", "city_joint", "province_joint", "province_official"}
)
_OBSERVATION_SOURCES = frozenset(
    {"user_reported", "school_report", "joint_exam_report", "official_score"}
)
_ENV_REFERENCE = re.compile(r"(?i)(?:%[A-Z_][A-Z0-9_]*%|\$[A-Z_][A-Z0-9_]*)")
_DRIVE_REFERENCE = re.compile(r"(?i)(?:^|[\s(\[/_-])[A-Z]:")
_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:^|\s)(?:\\\\|//|~/|/(?:home|users|tmp|var|etc|root)(?:/|$))"
)

_V2_FIELDS = frozenset(
    {
        "schema_version",
        "gender",
        "province",
        "city",
        "high_school",
        "grade",
        "exam_year",
        "class_level",
        "subject_mode",
        "subject_group",
        "secondary_subjects",
        "score_basis",
        "rank_observations",
        "best_rank",
        "usual_rank",
        "awards",
        "activities",
        "target_schools",
        "target_school_reasons",
        "target_majors",
        "target_major_reasons",
        "target_regions",
        "excluded_regions",
        "future_plan",
        "concerns",
        "desired_outcomes",
        "eligibility_facts",
    }
)
_V1_FIELDS = frozenset(
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
_OBSERVATION_FIELDS = frozenset(
    {
        "exam_date",
        "scope",
        "score",
        "max_score",
        "rank",
        "cohort_size",
        "subject_group",
        "high_school",
        "class_level",
        "source",
    }
)
_QUESTION_FIELD_GROUPS = MappingProxyType(
    {
        1: ("gender",),
        2: ("province", "city", "high_school"),
        3: ("grade", "exam_year"),
        4: ("class_level",),
        5: ("subject_mode", "subject_group", "secondary_subjects", "score_basis"),
        # One observation binds the answers to questions 6 and 7 atomically.
        6: ("rank_observations",),
        7: (),
        8: ("best_rank",),
        9: ("usual_rank",),
        10: ("awards",),
        11: ("activities",),
        12: ("target_schools",),
        13: ("target_school_reasons",),
        14: ("target_majors",),
        15: ("target_major_reasons",),
        16: ("target_regions", "excluded_regions"),
        17: ("future_plan",),
        18: ("concerns",),
        19: ("desired_outcomes",),
        20: ("eligibility_facts",),
    }
)


class PlanningProfileInputError(ValueError):
    """A profile payload does not match the anonymous public contract."""


class PlanningProfileCapabilityError(RuntimeError):
    """A local module required by the profile boundary is unavailable."""


class PlanningMode(str, Enum):
    REFERENCE = "reference"
    OFFICIAL = "official"
    LOW_INFORMATION = "low_information"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if is_dataclass(value) and hasattr(value, "to_dict"):
        return value.to_dict()
    raise TypeError(f"unsupported planning-profile value: {type(value).__name__}")


def _mathematical_int(
    value: Any,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    optional: bool = False,
) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a mathematical integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        result = int(value)
    else:
        raise TypeError(f"{name} must be a mathematical integer")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} is below its minimum")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} exceeds its maximum")
    return result


def _public_text(
    value: Any,
    name: str,
    *,
    optional: bool = False,
    maximum: int = 512,
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if value != value.strip() or not value:
        raise ValueError(f"{name} must be non-empty trimmed text")
    if any(unicodedata.category(character) == "Cf" for character in value):
        raise ValueError(f"{name} contains format controls")
    normalized = unicodedata.normalize("NFKC", value)
    if (
        not normalized
        or normalized != normalized.strip()
        or len(normalized) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError(f"{name} must be bounded single-line text")
    if (
        "\\" in normalized
        or _ENV_REFERENCE.search(normalized) is not None
        or _DRIVE_REFERENCE.search(normalized) is not None
        or _ABSOLUTE_PATH.search(normalized) is not None
    ):
        raise ValueError(f"{name} contains path-like text")
    if validate_public_output_text is None:
        raise PlanningProfileCapabilityError("profile privacy gate is unavailable")
    try:
        validate_public_output_text(normalized)
    except ValueError as error:
        raise ValueError(f"{name} contains private or non-public text") from error
    return normalized


def _text_tuple(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be an array of strings")
    try:
        items = tuple(value)
    except TypeError as error:
        raise TypeError(f"{name} must be an array of strings") from error
    normalized = tuple(_public_text(item, f"{name} item") for item in items)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} must contain unique normalized strings")
    return tuple(item for item in normalized if item is not None)


def _calendar_date(value: Any, name: str) -> str:
    text = _public_text(value, name, maximum=10)
    assert text is not None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{name} must be an ISO calendar date") from None
    if parsed.isoformat() != text:
        raise ValueError(f"{name} must be an ISO calendar date")
    return text


@dataclass(frozen=True, init=False)
class RankObservation:
    exam_date: str
    scope: str
    score: int | None
    max_score: int | None
    rank: int | None
    cohort_size: int | None
    subject_group: str
    high_school: str | None
    class_level: str | None
    source: str

    def __init__(self) -> None:
        raise TypeError("RankObservation is factory-only")

    @classmethod
    def _create(
        cls,
        payload: Mapping[str, Any],
        *,
        subject_group: str,
        high_school: str | None,
        class_level: str | None,
    ) -> "RankObservation":
        if not isinstance(payload, Mapping) or not set(payload) <= _OBSERVATION_FIELDS:
            raise ValueError("rank observation fields do not match the contract")
        required = {"exam_date", "scope", "score", "max_score", "rank", "cohort_size"}
        if not required <= set(payload):
            raise ValueError("rank observation is missing required fields")
        exam_date = _calendar_date(payload["exam_date"], "exam_date")
        scope = _public_text(payload["scope"], "scope", maximum=32)
        if scope not in _OBSERVATION_SCOPES:
            raise ValueError("unsupported rank observation scope")
        score = _mathematical_int(
            payload["score"], "score", minimum=0, maximum=1500, optional=True
        )
        max_score = _mathematical_int(
            payload["max_score"], "max_score", minimum=1, maximum=1500, optional=True
        )
        if (score is None) != (max_score is None):
            raise ValueError("score and max_score must be supplied together")
        if score is not None and max_score is not None and score > max_score:
            raise ValueError("score cannot exceed max_score")
        rank = _mathematical_int(payload["rank"], "rank", minimum=1, optional=True)
        cohort = _mathematical_int(
            payload["cohort_size"], "cohort_size", minimum=1, optional=True
        )
        if rank is not None and cohort is not None and rank > cohort:
            raise ValueError("rank cannot exceed cohort_size")
        bound_subject = _public_text(
            payload.get("subject_group", subject_group), "observation subject_group"
        )
        bound_school = _public_text(
            payload.get("high_school", high_school),
            "observation high_school",
            optional=True,
        )
        bound_class = _public_text(
            payload.get("class_level", class_level),
            "observation class_level",
            optional=True,
        )
        if (
            bound_subject != subject_group
            or bound_school != high_school
            or bound_class != class_level
        ):
            raise ValueError("rank observation context does not match the profile")
        source = _public_text(
            payload.get("source", "user_reported"), "observation source", maximum=32
        )
        if source not in _OBSERVATION_SOURCES:
            raise ValueError("unsupported rank observation source")
        instance = object.__new__(cls)
        for name, item in (
            ("exam_date", exam_date),
            ("scope", scope),
            ("score", score),
            ("max_score", max_score),
            ("rank", rank),
            ("cohort_size", cohort),
            ("subject_group", bound_subject),
            ("high_school", bound_school),
            ("class_level", bound_class),
            ("source", source),
        ):
            object.__setattr__(instance, name, item)
        return instance

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _json_safe(getattr(self, item.name)) for item in fields(self)}


@dataclass(frozen=True, init=False)
class PlanningProfile:
    schema_version: str
    gender: str
    province: str
    city: str | None
    high_school: str | None
    grade: str
    exam_year: int
    class_level: str | None
    subject_mode: str
    subject_group: str
    secondary_subjects: tuple[str, ...]
    score_basis: str | None
    rank_observations: tuple[RankObservation, ...]
    best_rank: int | None
    usual_rank: int | None
    awards: tuple[str, ...]
    activities: tuple[str, ...]
    target_schools: tuple[str, ...]
    target_school_reasons: tuple[str, ...]
    target_majors: tuple[str, ...]
    target_major_reasons: tuple[str, ...]
    target_regions: tuple[str, ...]
    excluded_regions: tuple[str, ...]
    future_plan: str
    concerns: tuple[str, ...]
    desired_outcomes: tuple[str, ...]
    eligibility_facts: tuple[str, ...]
    mode: PlanningMode
    digest: str

    def __init__(self) -> None:
        raise TypeError("PlanningProfile is factory-only")

    @classmethod
    def create(cls, payload: Mapping[str, Any]) -> "PlanningProfile":
        if not isinstance(payload, Mapping) or set(payload) != _V2_FIELDS:
            raise ValueError("planning profile fields do not match the v2 contract")
        if payload["schema_version"] != _SCHEMA_VERSION:
            raise ValueError("unsupported planning profile schema version")

        gender = _public_text(payload["gender"], "gender", maximum=16)
        if gender not in _GENDERS:
            raise ValueError("unsupported gender response")
        province = _public_text(payload["province"], "province", maximum=32)
        city = _public_text(payload["city"], "city", optional=True, maximum=64)
        high_school = _public_text(
            payload["high_school"], "high_school", optional=True, maximum=128
        )
        grade = _public_text(payload["grade"], "grade", maximum=16)
        if grade not in _GRADES:
            raise ValueError("unsupported grade")
        exam_year = _mathematical_int(
            payload["exam_year"], "exam_year", minimum=2000, maximum=2100
        )
        assert exam_year is not None
        class_level = _public_text(
            payload["class_level"], "class_level", optional=True, maximum=64
        )
        subject_mode = _public_text(payload["subject_mode"], "subject_mode", maximum=16)
        if subject_mode not in _SUBJECT_MODES:
            raise ValueError("unsupported subject mode")
        subject_group = _public_text(
            payload["subject_group"], "subject_group", maximum=64
        )
        secondary_subjects = _text_tuple(payload["secondary_subjects"], "secondary_subjects")
        if subject_mode == "3+1+2" and len(secondary_subjects) != 2:
            raise ValueError("3+1+2 profiles require exactly two secondary subjects")
        if subject_mode == "3+3" and len(secondary_subjects) != 2:
            raise ValueError("3+3 profiles require one primary and two secondary subjects")
        if subject_group in secondary_subjects:
            raise ValueError("subject selections must be unique")
        score_basis = _public_text(
            payload["score_basis"], "score_basis", optional=True, maximum=32
        )
        if score_basis is not None and score_basis not in _SCORE_BASES:
            raise ValueError("unsupported score basis")

        raw_observations = payload["rank_observations"]
        if not isinstance(raw_observations, list):
            raise TypeError("rank_observations must be an array")
        observations = tuple(
            RankObservation._create(
                item,
                subject_group=subject_group,
                high_school=high_school,
                class_level=class_level,
            )
            for item in raw_observations
        )
        if len(observations) > 24:
            raise ValueError("rank_observations exceeds the bounded history")
        identities = tuple(
            (item.exam_date, item.scope, item.source) for item in observations
        )
        if len(identities) != len(set(identities)):
            raise ValueError("rank_observations contains duplicates")

        best_rank = _mathematical_int(
            payload["best_rank"], "best_rank", minimum=1, optional=True
        )
        usual_rank = _mathematical_int(
            payload["usual_rank"], "usual_rank", minimum=1, optional=True
        )
        collections = {
            name: _text_tuple(payload[name], name)
            for name in (
                "awards",
                "activities",
                "target_schools",
                "target_school_reasons",
                "target_majors",
                "target_major_reasons",
                "target_regions",
                "excluded_regions",
                "concerns",
                "desired_outcomes",
                "eligibility_facts",
            )
        }
        if set(collections["target_regions"]) & set(collections["excluded_regions"]):
            raise ValueError("target and excluded regions must not overlap")
        future_plan = _public_text(payload["future_plan"], "future_plan", maximum=512)
        assert future_plan is not None

        if any(
            item.scope == "province_official" and (item.rank is not None or item.score is not None)
            for item in observations
        ):
            mode = PlanningMode.OFFICIAL
        elif any(item.rank is not None or item.score is not None for item in observations):
            mode = PlanningMode.REFERENCE
        else:
            mode = PlanningMode.LOW_INFORMATION

        values: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "gender": gender,
            "province": province,
            "city": city,
            "high_school": high_school,
            "grade": grade,
            "exam_year": exam_year,
            "class_level": class_level,
            "subject_mode": subject_mode,
            "subject_group": subject_group,
            "secondary_subjects": secondary_subjects,
            "score_basis": score_basis,
            "rank_observations": observations,
            "best_rank": best_rank,
            "usual_rank": usual_rank,
            **collections,
            "future_plan": future_plan,
            "mode": mode,
        }
        digest_payload = {
            name: _json_safe(item) for name, item in values.items()
        }
        digest_bytes = json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        values["digest"] = f"sha256:{hashlib.sha256(digest_bytes).hexdigest()}"
        instance = object.__new__(cls)
        for name, item in values.items():
            object.__setattr__(instance, name, item)
        return instance

    @classmethod
    def questionnaire_field_groups(cls) -> Mapping[int, tuple[str, ...]]:
        return _QUESTION_FIELD_GROUPS

    @property
    def official_rank(self) -> int | None:
        ranks = tuple(
            item.rank
            for item in self.rank_observations
            if item.scope == "province_official" and item.rank is not None
        )
        return ranks[-1] if ranks else None

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _json_safe(getattr(self, item.name)) for item in fields(self)}


def _legacy_v1_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != _V1_FIELDS or payload.get("schema_version") != "1.0":
        raise ValueError("legacy profile fields do not match the v1 contract")
    year = _mathematical_int(payload["current_year"], "current_year", minimum=2000, maximum=2100)
    rank = _mathematical_int(payload["rank"], "rank", minimum=1)
    assert year is not None and rank is not None
    return {
        "schema_version": _SCHEMA_VERSION,
        "gender": "不便回答",
        "province": payload["province"],
        "city": None,
        "high_school": None,
        "grade": payload["grade"],
        "exam_year": year,
        "class_level": None,
        "subject_mode": payload["subject_mode"],
        "subject_group": payload["subject_group"],
        "secondary_subjects": payload["secondary_subjects"],
        "score_basis": None,
        "rank_observations": [
            {
                "exam_date": f"{year:04d}-06-01",
                "scope": "province_official",
                "score": None,
                "max_score": None,
                "rank": rank,
                "cohort_size": None,
                "source": "official_score",
            }
        ],
        "best_rank": rank,
        "usual_rank": rank,
        "awards": [],
        "activities": [],
        "target_schools": payload["target_schools"],
        "target_school_reasons": [],
        "target_majors": payload["target_major_categories"],
        "target_major_reasons": [],
        "target_regions": payload["target_cities"],
        "excluded_regions": [],
        "future_plan": "不确定",
        "concerns": [],
        "desired_outcomes": ["院校范围", "多元路径"],
        "eligibility_facts": payload["eligibility_facts"],
    }


def load_planning_profile(payload: Mapping[str, Any]) -> PlanningProfile:
    """Validate v2 or migrate the exact public v1 profile shape."""

    if not isinstance(payload, Mapping):
        raise TypeError("planning profile payload must be an object")
    version = payload.get("schema_version")
    if version == _SCHEMA_VERSION:
        return PlanningProfile.create(payload)
    if version == "1.0":
        return PlanningProfile.create(_legacy_v1_payload(payload))
    raise ValueError("unsupported planning profile schema version")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlanningProfileInputError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise PlanningProfileInputError("non-finite JSON number")


def _strict_json(data: bytes) -> Any:
    if len(data) > _MAX_INPUT_BYTES:
        raise PlanningProfileInputError("input exceeds size limit")
    try:
        text = data.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, PlanningProfileInputError) as error:
        raise PlanningProfileInputError("input is not strict UTF-8 JSON") from error


def _read_input(source: str) -> Any:
    if source == "-":
        return _strict_json(sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1))
    path = Path(source)
    try:
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_size > _MAX_INPUT_BYTES
        ):
            raise PlanningProfileInputError("unsafe input file")
        data = path.read_bytes()
        after = os.lstat(path)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
        )
        if identity(before) != identity(after):
            raise PlanningProfileInputError("input changed while reading")
    except (OSError, PlanningProfileInputError) as error:
        raise PlanningProfileInputError("unable to read input") from error
    return _strict_json(data)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize an anonymous planning profile")
    parser.add_argument("input", help="UTF-8 JSON file or - for stdin")
    return parser


def _reconfigure_utf8() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _reconfigure_utf8()
    if sys.version_info < (3, 10) or _MISSING_LOCAL_CAPABILITY is not None:
        sys.stderr.write("planning-profile: missing capability\n")
        return 3
    try:
        arguments = _parser().parse_args(argv)
        profile = load_planning_profile(_read_input(arguments.input))
        encoded = json.dumps(
            profile.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        sys.stdout.write(encoded + "\n")
        return 0
    except (TypeError, ValueError, OSError, UnicodeError):
        sys.stderr.write("planning-profile: invalid input\n")
        return 2


__all__ = [
    "PlanningMode",
    "PlanningProfile",
    "PlanningProfileCapabilityError",
    "PlanningProfileInputError",
    "RankObservation",
    "load_planning_profile",
]


if __name__ == "__main__":
    raise SystemExit(main())
