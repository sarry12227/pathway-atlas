# -*- coding: utf-8 -*-
"""Generate an evidence-aware deterministic Markdown admission report."""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

if __package__:
    from .compliance_scan import scan_text
    from .contracts import EvidenceStatus, RecommendationProfile
    from .data_loader import DataError
    from .path_recommend import PathwayProfile, evaluate_pathways
    from .report_model import (
        StudentProfile,
        build_report_model,
        render_markdown,
        validate_profile_text,
    )
    from .school_recommend import SchoolRecommendError, recommend_schools
    from .validate_data import (
        ValidatedAdmissionRow,
        admission_row_hash,
        canonical_subject_selection_key,
        validate_dataset_snapshot,
    )
    from .validate_evidence import validate_bundle_snapshot
else:
    from compliance_scan import scan_text
    from contracts import EvidenceStatus, RecommendationProfile
    from data_loader import DataError
    from path_recommend import PathwayProfile, evaluate_pathways
    from report_model import (
        StudentProfile,
        build_report_model,
        render_markdown,
        validate_profile_text,
    )
    from school_recommend import SchoolRecommendError, recommend_schools
    from validate_data import (
        ValidatedAdmissionRow,
        admission_row_hash,
        canonical_subject_selection_key,
        validate_dataset_snapshot,
    )
    from validate_evidence import validate_bundle_snapshot


class EvidenceReportInputError(ValueError):
    """The public report CLI received invalid or unauthenticated input."""


class EvidenceReportCapabilityError(RuntimeError):
    """A caller-required optional report capability is unavailable."""


def _reconfigure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceReportInputError("JSON 包含重复字段")
        value[key] = item
    return value


def _reject_json_constant(_value):
    raise EvidenceReportInputError("JSON 包含非有限数值")


def _strict_json_text(text: str, label: str):
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, EvidenceReportInputError) as error:
        raise EvidenceReportInputError(f"{label} 不是严格 JSON") from error


def _strict_json_file(path: Path, label: str):
    try:
        before = path.stat()
        if not path.is_file() or path.is_symlink() or before.st_size > 1024 * 1024:
            raise EvidenceReportInputError(f"{label} 文件不安全")
        payload = path.read_bytes()
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            raise EvidenceReportInputError(f"{label} 文件读取期间发生变化")
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvidenceReportInputError(f"{label} 不是 UTF-8") from error
    except OSError as error:
        raise EvidenceReportInputError(f"{label} 无法安全读取") from error
    return _strict_json_text(text, label)


def _validated_evidence_snapshot(bundle: Path):
    """Return only validate_evidence's public authenticated bundle snapshot."""

    result = validate_bundle_snapshot(bundle)
    if result.snapshot is None or result.issues:
        raise EvidenceReportInputError("证据包未通过完整性与来源门禁")
    return result.snapshot


def _profile_collection(payload: dict, name: str) -> tuple[str, ...]:
    value = payload[name]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvidenceReportInputError(f"画像字段 {name} 必须是字符串数组")
    try:
        return tuple(validate_profile_text(item, name) for item in value)
    except (TypeError, ValueError) as error:
        raise EvidenceReportInputError(f"画像字段 {name} 包含隐私或不安全文本") from error


def _load_public_profile(path: Path):
    payload = _strict_json_file(path, "用户画像")
    fields = {
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
    if not isinstance(payload, dict) or set(payload) != fields:
        raise EvidenceReportInputError("用户画像字段不符合公开契约")
    if payload["schema_version"] != "1.0":
        raise EvidenceReportInputError("用户画像版本不受支持")
    try:
        report_profile = StudentProfile(
            province=payload["province"],
            subject_mode=payload["subject_mode"],
            subject_group=payload["subject_group"],
            secondary_subjects=_profile_collection(payload, "secondary_subjects"),
            rank=payload["rank"],
            grade=payload["grade"],
            current_year=payload["current_year"],
        )
        recommendation_profile = RecommendationProfile(
            rank=payload["rank"],
            target_province=payload["province"],
            subject_group=payload["subject_group"],
            secondary_subjects=frozenset(_profile_collection(payload, "secondary_subjects")),
            target_major_categories=_profile_collection(payload, "target_major_categories"),
            target_cities=_profile_collection(payload, "target_cities"),
            target_schools=_profile_collection(payload, "target_schools"),
        )
        pathway_profile = PathwayProfile(
            rank=payload["rank"],
            province=payload["province"],
            subject_mode=payload["subject_mode"],
            current_year=payload["current_year"],
            eligibility_facts=_profile_collection(payload, "eligibility_facts"),
        )
    except (TypeError, ValueError) as error:
        raise EvidenceReportInputError("用户画像值不符合公开契约") from error
    return report_profile, recommendation_profile, pathway_profile


def _resolve_public_dataset(dataset: Path, profile: StudentProfile):
    try:
        resolved = dataset.resolve(strict=True)
    except OSError as error:
        raise EvidenceReportInputError("数据目录不存在") from error
    validation = validate_dataset_snapshot(resolved)
    if validation.issues or validation.snapshot is None:
        raise EvidenceReportInputError("数据目录未通过省份数据校验")
    try:
        config = validation.snapshot.config
        if config.province != profile.province or config.mode != profile.subject_mode:
            raise EvidenceReportInputError("用户画像与省份数据配置不匹配")
        validation.snapshot.validate_subjects(
            profile.subject_group,
            profile.secondary_subjects,
        )
    except EvidenceReportInputError:
        raise
    except Exception as error:
        raise EvidenceReportInputError("省份或选科配置无效") from error
    return validation.snapshot


def _profiles_with_canonical_subject_key(
    dataset,
    report_profile: StudentProfile,
    recommendation_profile: RecommendationProfile,
):
    key = canonical_subject_selection_key(
        dataset.config,
        report_profile.subject_group,
        list(report_profile.secondary_subjects),
    )
    return (
        StudentProfile(
            province=report_profile.province,
            subject_mode=report_profile.subject_mode,
            subject_group=report_profile.subject_group,
            secondary_subjects=report_profile.secondary_subjects,
            rank=report_profile.rank,
            grade=report_profile.grade,
            current_year=report_profile.current_year,
            subject_selection_key=key,
        ),
        RecommendationProfile(
            rank=recommendation_profile.rank,
            target_province=recommendation_profile.target_province,
            subject_group=key,
            secondary_subjects=recommendation_profile.secondary_subjects,
            target_major_categories=recommendation_profile.target_major_categories,
            target_cities=recommendation_profile.target_cities,
            target_schools=recommendation_profile.target_schools,
        ),
    )


_ADMISSION_FACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ADMISSION_VALUE_FIELDS = {
    "year",
    "province",
    "subject_group",
    "school_code",
    "program_group",
    "remarks",
    "min_score",
    "min_rank",
    "coverage_min_rank",
    "coverage_max_rank",
    "coverage_status",
    "row_hash",
}


def _admission_fixed_projection(record):
    return (
        record.get("year"),
        record.get("province"),
        record.get("subject_group"),
        record.get("school_code"),
        record.get("program_group") or record.get("major_group_name"),
        record.get("remarks") or "",
        record.get("min_score"),
        record.get("min_rank"),
    )


def _strict_admission_fact(record):
    if not isinstance(record, dict):
        return None
    field = record.get("field")
    if not isinstance(field, str) or not field.startswith("admission_record:"):
        return None
    suffix = field.removeprefix("admission_record:")
    if _ADMISSION_FACT_ID.fullmatch(suffix) is None:
        return None
    value = record.get("value")
    if not isinstance(value, dict) or set(value) != _ADMISSION_VALUE_FIELDS:
        return None
    status = record.get("status")
    if status not in {
        EvidenceStatus.OFFICIAL.value,
        EvidenceStatus.CORROBORATED.value,
        EvidenceStatus.REFERENCE.value,
    }:
        return None
    for name in ("year", "min_score", "min_rank", "coverage_min_rank", "coverage_max_rank"):
        if not isinstance(value[name], int) or isinstance(value[name], bool) or value[name] < 1:
            return None
    if value["year"] < 2000 or value["year"] > 2100:
        return None
    if value["coverage_min_rank"] > value["coverage_max_rank"]:
        return None
    if not value["coverage_min_rank"] <= value["min_rank"] <= value["coverage_max_rank"]:
        return None
    if value["coverage_status"] not in {
        EvidenceStatus.OFFICIAL.value,
        EvidenceStatus.CORROBORATED.value,
        EvidenceStatus.REFERENCE.value,
        EvidenceStatus.PARTIAL.value,
    }:
        return None
    if not isinstance(value["row_hash"], str) or re.fullmatch(
        r"sha256:[0-9a-f]{64}", value["row_hash"]
    ) is None:
        return None
    for name in ("province", "subject_group", "school_code", "program_group", "remarks"):
        if not isinstance(value[name], str):
            return None
        if name != "remarks" and (not value[name] or value[name] != value[name].strip()):
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in value[name]):
            return None
    source_ids = record.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids:
        return None
    if len(source_ids) != len(set(source_ids)) or any(
        not isinstance(source_id, str) or _ADMISSION_FACT_ID.fullmatch(source_id) is None
        for source_id in source_ids
    ):
        return None
    return value["row_hash"], value, status, tuple(sorted(source_ids))


def _admission_fact_index(facts):
    index = {}
    projections = set()
    for record in facts:
        parsed = _strict_admission_fact(record)
        if parsed is None:
            continue
        row_hash, value, status, source_ids = parsed
        projections.add(_admission_fixed_projection(value))
        snapshot = (value, status, source_ids)
        if row_hash not in index:
            index[row_hash] = snapshot
        elif index[row_hash] != snapshot:
            index[row_hash] = None
    return index, frozenset(projections)


def _public_recommendations(
    admission_rows: tuple[ValidatedAdmissionRow, ...],
    profile: RecommendationProfile,
    policy,
    facts,
):
    """Run Task 3 without assigning unscoped facts to admission rows.

    v1 evidence facts must name the exact normalized admission field before a
    row can carry numeric provenance.  The current public replay fixture has a
    deliberately generic fact, so rows degrade to missing rather than gaining
    fabricated source coverage.
    """

    try:
        if not isinstance(admission_rows, tuple) or not all(
            isinstance(row, ValidatedAdmissionRow) for row in admission_rows
        ):
            raise TypeError("admission rows must come from validated snapshot")
        authenticated_rows = tuple(
            (row.to_dict(), admission_row_hash(row)) for row in admission_rows
        )
        matching_years = [
            row["year"]
            for row, _row_hash in authenticated_rows
            if row.get("subject_group") == profile.subject_group
        ]
        if not matching_years:
            raise DataError("已验证投档数据没有匹配的科目组")
        latest_year = max(matching_years)
        rows = [
            (row, row_hash) for row, row_hash in authenticated_rows
            if row.get("subject_group") == profile.subject_group
            and row.get("year") == latest_year
        ]
        evidence_by_hash, evidence_projections = _admission_fact_index(facts)
        bounded_rows = []
        for original, expected_row_hash in rows:
            row = dict(original)
            if expected_row_hash not in evidence_by_hash:
                status = (
                    EvidenceStatus.CONFLICT
                    if _admission_fixed_projection(row) in evidence_projections
                    else EvidenceStatus.MISSING
                )
                row.update(
                    {
                        "evidence_status": status.value,
                        "source_ids": (),
                        "coverage_min_rank": None,
                        "coverage_max_rank": None,
                        "coverage_status": status.value,
                    }
                )
            else:
                accepted = evidence_by_hash[expected_row_hash]
                if accepted is None:
                    row.update(
                        {
                            "evidence_status": EvidenceStatus.CONFLICT.value,
                            "source_ids": (),
                            "coverage_min_rank": None,
                            "coverage_max_rank": None,
                            "coverage_status": EvidenceStatus.CONFLICT.value,
                        }
                    )
                    bounded_rows.append(row)
                    continue
                value, status, source_ids = accepted
                if (
                    value["row_hash"] != expected_row_hash
                    or _admission_fixed_projection(value)
                    != _admission_fixed_projection(row)
                ):
                    row.update(
                        {
                            "evidence_status": EvidenceStatus.CONFLICT.value,
                            "source_ids": (),
                            "coverage_min_rank": None,
                            "coverage_max_rank": None,
                            "coverage_status": EvidenceStatus.CONFLICT.value,
                        }
                    )
                else:
                    row.update(
                        {
                            "evidence_status": status,
                            "source_ids": source_ids,
                            "coverage_min_rank": value["coverage_min_rank"],
                            "coverage_max_rank": value["coverage_max_rank"],
                            "coverage_status": value["coverage_status"],
                        }
                    )
            bounded_rows.append(row)
        return recommend_schools(bounded_rows, profile, policy)
    except (DataError, SchoolRecommendError, TypeError, ValueError) as error:
        raise EvidenceReportInputError("普通批数据无法形成安全推荐结果") from error


def _build_evidence_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从显式省份数据、匿名画像和已验证证据包生成确定性 Markdown"
    )
    parser.add_argument("--dataset", required=True, type=Path, help="显式省份数据目录")
    parser.add_argument("--profile", required=True, type=Path, help="匿名严格 JSON 用户画像")
    parser.add_argument("--evidence", required=True, type=Path, help="已完成的证据包目录")
    parser.add_argument("--output", type=Path, default=None, help="可选 Markdown 输出路径")
    return parser


def _publish_markdown(markdown: str, destination: Path) -> None:
    """Write privately, durably, then publish one complete file exclusively."""

    owned_path = None
    primary_error = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            suffix=".ready.md",
            delete=False,
        ) as handle:
            owned_path = Path(handle.name)
            handle.write(markdown)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(owned_path, destination)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_error = None
        if owned_path is not None:
            try:
                owned_path.unlink(missing_ok=True)
            except OSError as error:
                cleanup_error = error
        if primary_error is None and cleanup_error is not None:
            raise cleanup_error


def _evidence_main(argv) -> int:
    if sys.version_info < (3, 10):
        print("缺少能力：需要 Python 3.10 或更高版本", file=sys.stderr)
        return 3
    args = _build_evidence_parser().parse_args(argv)
    try:
        report_profile, recommendation_profile, pathway_profile = _load_public_profile(
            args.profile
        )
        dataset = _resolve_public_dataset(args.dataset, report_profile)
        report_profile, recommendation_profile = _profiles_with_canonical_subject_key(
            dataset, report_profile, recommendation_profile
        )
        evidence = _validated_evidence_snapshot(args.evidence)
        facts = tuple(record.to_dict() for record in evidence.facts)
        recommendations = _public_recommendations(
            dataset.admission_rows,
            recommendation_profile,
            dataset.config.ordinary_batch_policy,
            facts,
        )
        # The public replay fixture carries no policy records or versioned rank
        # anchors.  Task 5 is still entered through its new API; Task 4 remains
        # explicitly unavailable instead of falling back to bundled xibao data.
        pathways = evaluate_pathways(pathway_profile, (), model=None)
        model = build_report_model(
            report_profile,
            recommendations,
            rank=None,
            pathways=pathways,
            evidence=evidence,
        )
        markdown = render_markdown(model)
        if scan_text(markdown):
            raise EvidenceReportInputError("报告未通过合规扫描")
        if args.output is not None:
            output = args.output.resolve(strict=False)
            if (
                output.suffix.lower() != ".md"
                or not output.parent.is_dir()
                or output.exists()
            ):
                raise EvidenceReportInputError("输出路径必须位于现有目录且使用 .md 后缀")
            _publish_markdown(markdown, output)
    except EvidenceReportCapabilityError as error:
        print(f"缺少能力：{error}", file=sys.stderr)
        return 3
    except EvidenceReportInputError as error:
        print(f"错误[REPORT_002]：{error}", file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError):
        print("错误[REPORT_002]：报告生成或发布失败", file=sys.stderr)
        return 2
    print(markdown, end="")
    return 0


def main(argv=None) -> int:
    _reconfigure_utf8()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    return _evidence_main(raw_argv)


if __name__ == "__main__":
    sys.exit(main())
