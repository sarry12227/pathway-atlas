"""Pure deterministic persistence rules for scheduled source-health telemetry."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


_PROVINCE = re.compile(r"^[\u3400-\u9fff]{2,8}$")
_OBSERVATION_STATUSES = frozenset({"healthy", "redirect_review", "unavailable"})
_STATE_FIELDS = frozenset({"province", "status", "count"})


def _safe_province(value: Any) -> str:
    if not isinstance(value, str) or _PROVINCE.fullmatch(value) is None:
        raise ValueError("invalid source-health province")
    return value


@dataclass(frozen=True)
class HealthObservation:
    province: str
    status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "province", _safe_province(self.province))
        if self.status not in _OBSERVATION_STATUSES:
            raise ValueError("invalid source-health status")

    def to_dict(self) -> dict[str, str]:
        return {"province": self.province, "status": self.status}


@dataclass(frozen=True)
class HealthStateEntry:
    province: str
    status: str
    count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "province", _safe_province(self.province))
        if self.status != "unavailable":
            raise ValueError("cached source-health status must be unavailable")
        if not isinstance(self.count, int) or isinstance(self.count, bool) or self.count not in {1, 2}:
            raise ValueError("cached source-health count must be one or two")

    def to_dict(self) -> dict[str, object]:
        return {"province": self.province, "status": self.status, "count": self.count}


@dataclass(frozen=True)
class SourceHealthTransition:
    state: tuple[HealthStateEntry, ...]
    review: tuple[HealthObservation, ...]


def _typed_collection(value: Iterable[Any], expected: type, name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be an ordered collection")
    try:
        items = tuple(value)
    except TypeError as error:
        raise TypeError(f"{name} must be an ordered collection") from error
    if any(not isinstance(item, expected) for item in items):
        raise TypeError(f"{name} contains an invalid entry")
    return items


def state_from_payload(payload: Any) -> tuple[HealthStateEntry, ...]:
    """Parse strict cache data containing only province, status, and count."""

    if not isinstance(payload, list):
        raise TypeError("source-health cache must be an array")
    entries: list[HealthStateEntry] = []
    for raw in payload:
        if not isinstance(raw, dict) or set(raw) != _STATE_FIELDS:
            raise ValueError("source-health cache entry fields are invalid")
        entries.append(
            HealthStateEntry(
                province=raw["province"],
                status=raw["status"],
                count=raw["count"],
            )
        )
    provinces = [entry.province for entry in entries]
    if len(provinces) != len(set(provinces)):
        raise ValueError("source-health cache provinces must be unique")
    return tuple(sorted(entries, key=lambda entry: entry.province))


def state_to_payload(state: Iterable[HealthStateEntry]) -> list[dict[str, object]]:
    entries = _typed_collection(state, HealthStateEntry, "source-health state")
    provinces = [entry.province for entry in entries]
    if len(provinces) != len(set(provinces)):
        raise ValueError("source-health state provinces must be unique")
    return [entry.to_dict() for entry in sorted(entries, key=lambda entry: entry.province)]


def transition_source_health(
    previous_state: Iterable[HealthStateEntry],
    observations: Iterable[HealthObservation],
) -> SourceHealthTransition:
    """Advance one scheduled run; duplicate aliases count as one observation run."""

    previous = _typed_collection(previous_state, HealthStateEntry, "previous source-health state")
    previous_by_province = {entry.province: entry for entry in previous}
    if len(previous_by_province) != len(previous):
        raise ValueError("previous source-health state provinces must be unique")
    current = _typed_collection(observations, HealthObservation, "source-health observations")
    if not current:
        raise ValueError("source-health observations must not be empty")

    statuses_by_province: dict[str, set[str]] = {}
    for observation in current:
        statuses_by_province.setdefault(observation.province, set()).add(observation.status)

    next_state: list[HealthStateEntry] = []
    review: list[HealthObservation] = []
    for province in sorted(statuses_by_province):
        statuses = statuses_by_province[province]
        if "redirect_review" in statuses:
            review.append(HealthObservation(province, "redirect_review"))
            continue
        if "healthy" in statuses:
            continue

        prior = previous_by_province.get(province)
        count = min(2, (prior.count if prior is not None else 0) + 1)
        next_state.append(HealthStateEntry(province, "unavailable", count))
        if count == 2:
            review.append(HealthObservation(province, "unavailable"))

    return SourceHealthTransition(tuple(next_state), tuple(review))


__all__ = [
    "HealthObservation",
    "HealthStateEntry",
    "SourceHealthTransition",
    "state_from_payload",
    "state_to_payload",
    "transition_source_health",
]
