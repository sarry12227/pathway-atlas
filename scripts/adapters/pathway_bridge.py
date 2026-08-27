"""Project authenticated evidence facts into pathway-policy contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

if __package__ and "." in __package__:
    from ..contracts import EvidenceStatus
    from ..path_recommend import PathwayPolicy, exact_evidence_problem
    from ..validate_evidence import FrozenJsonRecord, ValidatedEvidenceSnapshot
else:
    from contracts import EvidenceStatus
    from path_recommend import PathwayPolicy, exact_evidence_problem
    from validate_evidence import FrozenJsonRecord, ValidatedEvidenceSnapshot


_FIELD = re.compile(r"pathway_policy:([A-Za-z0-9][A-Za-z0-9._:-]{0,127})\Z")
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
def _canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def bridge_pathway_policies(
    snapshot: ValidatedEvidenceSnapshot,
    *,
    province: str,
    subject_mode: str,
    target_year: int,
) -> tuple[PathwayPolicy, ...]:
    """Return only hash-bound, context-matching, threshold-sufficient policies."""

    if type(snapshot) is not ValidatedEvidenceSnapshot:
        raise TypeError("snapshot must be a validated evidence snapshot")
    if snapshot.manifest_hash != snapshot.manifest.manifest_hash:
        raise ValueError("evidence snapshot identity does not match its manifest")
    if not isinstance(province, str) or not province.strip():
        raise TypeError("province must be non-empty text")
    province = province.strip()
    if subject_mode not in {"3+1+2", "3+3"}:
        raise ValueError("subject_mode must be 3+1+2 or 3+3")
    if (
        not isinstance(target_year, int)
        or isinstance(target_year, bool)
        or not 2000 <= target_year <= 2100
    ):
        raise TypeError("target_year must be a supported integer year")

    projected: list[tuple[str, PathwayPolicy]] = []
    for frozen in snapshot.facts:
        if type(frozen) is not FrozenJsonRecord:
            raise TypeError("snapshot facts must be frozen JSON records")
        fact = frozen.to_dict()
        field = fact.get("field")
        match = _FIELD.fullmatch(field) if isinstance(field, str) else None
        if match is None:
            continue
        value = fact.get("value")
        if not isinstance(value, dict):
            continue
        projection_hash = value.get("projection_hash")
        if not isinstance(projection_hash, str) or _HASH.fullmatch(projection_hash) is None:
            continue
        projection = dict(value)
        projection.pop("projection_hash")
        if _canonical_hash(projection) != projection_hash:
            continue
        if projection.get("policy_id") != match.group(1):
            continue
        try:
            status = EvidenceStatus(fact.get("status"))
        except (TypeError, ValueError):
            continue
        source_ids = fact.get("source_ids")
        if not isinstance(source_ids, list) or any(
            not isinstance(source_id, str) for source_id in source_ids
        ):
            continue
        if len(source_ids) != len(set(source_ids)):
            continue
        if exact_evidence_problem(status, tuple(source_ids)) is not None:
            continue
        if projection.get("evidence_status") != status.value:
            continue
        if projection.get("policy_source_ids") != source_ids:
            continue
        if (
            projection.get("province") != province
            or projection.get("subject_mode") != subject_mode
            or projection.get("target_year") != target_year
        ):
            continue
        try:
            policy = PathwayPolicy(**projection)
        except (TypeError, ValueError):
            continue
        projected.append((projection_hash, policy))

    hash_counts = Counter(item[0] for item in projected)
    id_counts = Counter(item[1].policy_id for item in projected)
    return tuple(
        sorted(
            (
                policy
                for projection_hash, policy in projected
                if hash_counts[projection_hash] == 1 and id_counts[policy.policy_id] == 1
            ),
            key=lambda policy: policy.policy_id,
        )
    )


__all__ = ["bridge_pathway_policies"]
