"""Natural-language-to-report acceptance over public host and planning APIs."""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass, replace
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest import mock
from uuid import uuid4

from scripts.adapters import CellStatus, ExtractedCoverage, ExtractedRow, ExtractedTable
from scripts.adapters.admission_bridge import bridge_admission_evidence
from scripts.adapters.pathway_bridge import bridge_pathway_policy_evidence
from scripts.adapters.pathway_extraction import extract_pathway_policy
from scripts.adapters.rank_bridge import bridge_rank_evidence
from scripts.adapters.school_fit_bridge import bridge_school_fit_evidence
from scripts.contracts import CapabilityReport, CapabilityTier, EvidenceStatus, SourceCandidate, SourceTier
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.evidence import EvidenceStore
from scripts.generate_report import build_pathway_atlas_model
from scripts.planning_session import (
    PlanningSession,
    SessionStage,
    SessionTransitionError,
    build_calculation_outcome,
    build_evidence_manifest_outcome,
    build_report_publication_outcome,
    build_task_evidence_outcome,
)
from scripts.questionnaire_intake import build_profile_from_questionnaire
from scripts.path_recommend import (
    PATHWAY_DISPLAY_EVIDENCE_FIELDS,
    evaluate_pathways,
)
from scripts.query_plan import build_query_plan, load_province_catalog
from scripts.report_model import ReportRecommendation, render_markdown
from scripts.research_snapshot import build_research_snapshot
from scripts.validate_data import validate_runtime_admission_row
from scripts.validate_evidence import validate_bundle_snapshot


_PATHWAY_FIELDS = (
    "institution", "province", "subject_mode", "year",
    "eligibility_requirements", "grade_requirements", "subject_requirements",
    "award_requirements", "activity_requirements", "disqualifying_facts",
    "professional_options", "training_arrangements", "transition_rules",
    "outcomes", "service_employment_obligations", "penalty_exit_rules",
    "fees_and_subsidies", "dates_and_deadlines", "application_materials",
    "preparation_actions",
)
def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def anonymous_twenty_answer_payload(
    *,
    subject_mode: str = "3+1+2",
    risk: str = "balanced",
    rank_scope: str = "province_official",
):
    subjects = ("历史", ["地理", "政治"]) if subject_mode == "3+1+2" else ("物理", ["化学", "生物"])
    province = "湖北" if subject_mode == "3+1+2" else "上海"
    city = "武汉" if subject_mode == "3+1+2" else "上海"
    school = "武汉市示例中学" if subject_mode == "3+1+2" else "上海市示例中学"
    canonical_subject = "历史+政治+地理" if subject_mode == "3+1+2" else "物理+化学+生物"
    return {
        1: "不便回答",
        2: province,
        3: {"city": city, "high_school": school},
        4: {"grade": "高二", "exam_year": 2028},
        5: "重点班",
        6: {"mode": subject_mode, "primary": subjects[0], "secondary": subjects[1], "score_basis": "原始分"},
        7: {
            "exam": "市级联考",
            "date": "2026-06-01",
            "scope": rank_scope,
            "score": 610,
            "max_score": 750,
            "source": "school_report",
        },
        8: {
            "rank": 120 if rank_scope == "school" else 18000,
            "cohort_size": 1000 if rank_scope == "school" else 200000,
        },
        9: {"best_rank": 80, "usual_rank": 140},
        10: ["合成学科奖项"],
        11: ["合成研究性学习", "合成志愿活动"],
        12: ["示例甲大学"],
        13: ["专业实力强"],
        14: ["历史学" if subject_mode == "3+1+2" else "计算机科学与技术"],
        15: ["自己感兴趣"],
        16: {"targets": [city], "excluded": []},
        17: "postgraduate",
        18: ["院校定位", "多元路径"],
        19: ["院校范围", "多元路径", "当前行动"],
        20: {
            "budget": "flexible", "institution_types": ["public", "cooperative"],
            "service": "reject", "adjustment": "consider", "risk": risk,
            "health": [], "school_vs_major": "major_first",
            "pathways": {
                "strong_foundation": "interested",
                "comprehensive_evaluation": "interested",
                "special_program": "unknown",
                "service_oriented": "interested",
                "uniformed_service": "not_applicable",
                "cross_border": "unknown",
                "arts_sports": "not_applicable",
            },
            "eligibility": [
                "完成高考报名", "接受异地就读", "高二", canonical_subject,
                "历史或物理", "合成学科奖项", "合成志愿活动",
            ],
        },
    }


def _normalize_answers(answers):
    normalized = dict(answers)
    normalized[7] = {
        key: value for key, value in answers[7].items() if key != "exam"
    }
    return build_profile_from_questionnaire(normalized)


def _candidate(
    source_id: str,
    *,
    year: int,
    content_hash: str,
    tier: SourceTier = SourceTier.A,
    publisher: str = "湖北省教育考试院",
):
    host = source_id.replace("_", "-") + ".example.cn"
    return SourceCandidate(
        source_id=source_id, url=f"https://{host}/{source_id}.html", publisher=publisher,
        tier=tier, published_at=f"{year}-06-25", retrieved_at="2026-08-30T00:00:00Z",
        content_hash=content_hash, citation_root=f"https://{host}/",
        summary="合成公开资料，仅用于端到端测试",
    )


def _table(caption: str, rows: tuple[dict[str, Any], ...], *, rank_bounds=None, score_bounds=None):
    extracted = tuple(
        ExtractedRow(
            values=row,
            cell_status={name: CellStatus.EXACT for name in row},
            location=f"table[1]/tbody/tr[{index}]", confidence=1,
        )
        for index, row in enumerate(rows, 1)
    )
    lower, upper = rank_bounds or (None, None)
    lower_score, upper_score = score_bounds or (None, None)
    return ExtractedTable(
        table_id="table[1]", caption=caption, sheet=None, rows=extracted,
        coverage=ExtractedCoverage(
            lower_score=lower_score, upper_score=upper_score,
            lower_rank=lower, upper_rank=upper,
        ),
        warnings=(), extraction_method="html-table",
    )


@dataclass(frozen=True)
class DiscoveredCandidate:
    source_id: str
    publisher: str
    tier: SourceTier
    year: int
    kind: str
    caption: str
    rows: tuple[dict[str, Any], ...]
    rank_bounds: tuple[int, int] | None = None
    score_bounds: tuple[int, int] | None = None


@dataclass(frozen=True)
class OpenedBody:
    discovered: DiscoveredCandidate
    body: bytes
    source: SourceCandidate


@dataclass(frozen=True)
class AdaptedCandidate:
    sources: tuple[SourceCandidate, ...]
    tables: tuple[ExtractedTable, ...]
    kind: str


@dataclass
class FakeHost:
    capabilities: frozenset[str]
    source_tier: SourceTier = SourceTier.A
    source_count: int = 1
    offline: bool = False
    conflict: bool = False
    collect_school_history: bool = False
    availability_offsets: dict[str, int] | None = None
    available_offsets: dict[str, tuple[int, ...]] | None = None
    conflict_offsets: dict[str, tuple[int, ...]] | None = None
    attempts: list[tuple[str, str, int]] | None = None
    opened_task_ids: list[str] | None = None
    opened_registry: dict[str, tuple[str, str]] | None = None
    research_year: int | None = None
    profile_subject_mode: str | None = None
    profile_province: str | None = None
    profile_school: str | None = None
    admission_coverage_status: EvidenceStatus | None = None
    admission_coverage_by_offset: dict[int, EvidenceStatus] | None = None
    source_tier_by_offset: dict[int, SourceTier] | None = None
    source_count_by_offset: dict[int, int] | None = None

    @classmethod
    def official_current_year(cls):
        return cls(frozenset({"search", "browse", "vision", "local_exec", "file_output"}))

    @classmethod
    def historical_official(cls):
        return cls(
            frozenset({"search", "browse", "vision", "local_exec", "file_output"}),
            availability_offsets={
                "score_table": 0,
                "joy_report": 1,
                "batch_admission": 2,
                "pathway": 3,
            },
        )

    @classmethod
    def two_independent_b(cls):
        return cls(
            frozenset({"search", "browse", "local_exec", "file_output"}),
            source_tier=SourceTier.B, source_count=2,
        )

    @classmethod
    def three_independent_c(cls):
        return cls(
            frozenset({"search", "browse", "local_exec", "file_output"}),
            source_tier=SourceTier.C, source_count=3,
        )

    @classmethod
    def offline_only(cls):
        return cls(
            frozenset({"local_exec", "file_output"}),
            offline=True,
            availability_offsets={
                "score_table": 1,
                "joy_report": 1,
                "batch_admission": 1,
                "pathway": 1,
            },
        )

    @classmethod
    def conflicting_sources(cls):
        return cls(
            frozenset({"search", "browse", "local_exec", "file_output"}),
            source_tier=SourceTier.B,
            source_count=2,
            conflict=True,
            available_offsets={"pathway": (0, 1, 2, 3)},
            conflict_offsets={"pathway": (0, 1, 2, 3)},
        )

    @classmethod
    def current_conflict_then_historical(cls):
        return cls(
            frozenset({"search", "browse", "local_exec", "file_output"}),
            source_tier=SourceTier.B,
            source_count=2,
            conflict=True,
            available_offsets={"pathway": (0, 1)},
            conflict_offsets={"pathway": (0,)},
        )

    @classmethod
    def current_reference_then_historical_official(cls):
        return cls(
            frozenset({"search", "browse", "vision", "local_exec", "file_output"}),
            source_tier=SourceTier.B,
            source_count=2,
            available_offsets={
                "score_table": (0, 1),
                "joy_report": (0, 1),
                "batch_admission": (0, 1),
                "pathway": (0, 1),
            },
            source_tier_by_offset={0: SourceTier.B, 1: SourceTier.A},
            source_count_by_offset={0: 2, 1: 1},
        )

    def __post_init__(self):
        if self.availability_offsets is None:
            self.availability_offsets = {}
        if self.available_offsets is None:
            self.available_offsets = {}
        if self.conflict_offsets is None:
            self.conflict_offsets = {}
        if self.attempts is None:
            self.attempts = []
        if self.opened_task_ids is None:
            self.opened_task_ids = []
        if self.opened_registry is None:
            self.opened_registry = {}
        if self.source_tier_by_offset is None:
            self.source_tier_by_offset = {}
        if self.source_count_by_offset is None:
            self.source_count_by_offset = {}

    @staticmethod
    def availability_family(task) -> str:
        if task.kind in {
            "strong_foundation", "comprehensive_evaluation", "special_pathway"
        }:
            return "pathway"
        return task.kind

    @staticmethod
    def task_family(task) -> str:
        return (
            task.kind
            if task.target_name is None
            else f"{task.kind}:{task.target_name}"
        )

    def selected_year(self, task) -> int:
        if self.research_year is None:
            raise AssertionError("host research year was not bound")
        assert self.availability_offsets is not None
        offset = self.availability_offsets.get(
            self.task_family(task),
            self.availability_offsets.get(self.availability_family(task), 0),
        )
        return self.research_year - offset

    def source_tier_for(self, task) -> SourceTier:
        if self.research_year is None:
            raise AssertionError("host research year was not bound")
        assert self.source_tier_by_offset is not None
        return self.source_tier_by_offset.get(
            self.research_year - task.year, self.source_tier
        )

    def source_count_for(self, task) -> int:
        if self.research_year is None:
            raise AssertionError("host research year was not bound")
        assert self.source_count_by_offset is not None
        return self.source_count_by_offset.get(
            self.research_year - task.year, self.source_count
        )

    def _fixture(
        self, task, source_index: int
    ) -> tuple[str, tuple[dict[str, Any], ...], tuple[int, int] | None, tuple[int, int] | None, str] | None:
        if task.kind == "score_table":
            return (
                "一分一段表",
                ({"score": 610, "rank": 18000, "cumulative_count": 18000},),
                (1, 200000),
                (100, 750),
                "rank",
            )
        if task.kind == "joy_report":
            return (
                "学校成绩锚点",
                ({
                    "scope": "school_anchor", "school_name": self.profile_school,
                    "class_level": "重点班", "school_rank": 120,
                    "province_rank": 18000, "school_score": 610,
                    "max_score": 750, "cohort_size": 1000,
                },),
                (1, 1000),
                (0, 750),
                "rank",
            )
        if task.kind == "batch_admission" and task.target_name == "普通批":
            rows = tuple(
                {
                    "school_code": code, "school_name": name, "program_group": "第01组",
                    "min_score": score, "min_rank": rank,
                }
                for code, name, score, rank in (
                    ("SYN-CH", "合成公办历史冲刺大学", 617, 16500),
                    ("SYN-CL", "合成上海高费调剂法学冲刺大学", 615, 17000),
                    ("SYN-SH", "合成公办历史稳妥大学", 605, 18500),
                    ("SYN-SL", "合成上海高费调剂法学稳妥大学", 602, 19000),
                    ("SYN-BH", "合成公办历史保底大学", 592, 22000),
                    ("SYN-BL", "合成上海高费调剂法学保底大学", 590, 23000),
                )
            )
            return (
                "普通批投档",
                rows,
                (15000, 23000),
                (590, 620),
                "admission",
            )
        if task.kind == "province_policy":
            return (
                "省级高考与批次政策",
                ({
                    "province": task.province,
                    "year": task.year,
                    "exam_mode": self.profile_subject_mode,
                    "subject_structure": self.profile_subject_mode,
                    "batch_structure": "本科普通批按院校专业组投档",
                    "effective_date": f"{task.year}-01-01",
                },),
                None,
                None,
                "school_fit",
            )
        if task.kind == "enrollment_plan":
            rows = tuple(
                {
                    "province": task.province,
                    "year": task.year,
                    "subject_group": task.subject_group,
                    "institution": name,
                    "institution_code": code,
                    "program_group": "第01组",
                    "majors": (major,),
                    "school_province": province,
                    "school_city": city,
                    "institution_type": institution_type,
                }
                for (
                    code,
                    name,
                    major,
                    province,
                    city,
                    institution_type,
                ) in (
                    (
                        "SYN-CH", "合成公办历史冲刺大学", "历史学",
                        "湖北", "武汉", "public",
                    ),
                    (
                        "SYN-CL", "合成上海高费调剂法学冲刺大学", "法学",
                        "上海", "上海", "cooperative",
                    ),
                    (
                        "SYN-SH", "合成公办历史稳妥大学", "历史学",
                        "湖北", "武汉", "public",
                    ),
                    (
                        "SYN-SL", "合成上海高费调剂法学稳妥大学", "法学",
                        "上海", "上海", "cooperative",
                    ),
                    (
                        "SYN-BH", "合成公办历史保底大学", "历史学",
                        "湖北", "武汉", "public",
                    ),
                    (
                        "SYN-BL", "合成上海高费调剂法学保底大学", "法学",
                        "上海", "上海", "cooperative",
                    ),
                )
            )
            return "普通批招生计划", rows, None, None, "school_fit"
        if task.kind == "admission_charter":
            rows = tuple(
                {
                    "province": task.province,
                    "year": task.year,
                    "institution": name,
                    "institution_code": code,
                    "admission_rules": "按投档成绩择优录取",
                    "adjustment_rules": (
                        "同一院校专业组内按章程调剂"
                        if adjustment_required
                        else "该专业组不要求接受调剂"
                    ),
                    "adjustment_required": adjustment_required,
                    "health_restrictions": "按招生体检指导意见执行",
                    "language_restrictions": "不限外语语种",
                    "single_subject_restrictions": "无单科成绩限制",
                    "special_conditions": "无其他已公开特殊条件",
                }
                for code, name, adjustment_required in (
                    ("SYN-CH", "合成公办历史冲刺大学", False),
                    ("SYN-CL", "合成上海高费调剂法学冲刺大学", True),
                    ("SYN-SH", "合成公办历史稳妥大学", False),
                    ("SYN-SL", "合成上海高费调剂法学稳妥大学", True),
                    ("SYN-BH", "合成公办历史保底大学", False),
                    ("SYN-BL", "合成上海高费调剂法学保底大学", True),
                )
            )
            return "本科招生章程", rows, None, None, "school_fit"
        if task.kind == "tuition_fee":
            rows = tuple(
                {
                    "province": task.province,
                    "year": task.year,
                    "institution": name,
                    "institution_code": code,
                    "program_group": "第01组",
                    "majors": (major,),
                    "annual_fee_amount": annual_fee_amount,
                    "fee_currency": "CNY",
                    "fee_period": "academic_year",
                    "accommodation_fee": 1800,
                    "other_required_fees": "教材费据实结算",
                    "financial_aid": "国家奖助学金和校内助学金",
                }
                for code, name, major, annual_fee_amount in (
                    ("SYN-CH", "合成公办历史冲刺大学", "历史学", 8000),
                    ("SYN-CL", "合成上海高费调剂法学冲刺大学", "法学", 40000),
                    ("SYN-SH", "合成公办历史稳妥大学", "历史学", 8000),
                    ("SYN-SL", "合成上海高费调剂法学稳妥大学", "法学", 40000),
                    ("SYN-BH", "合成公办历史保底大学", "历史学", 8000),
                    ("SYN-BL", "合成上海高费调剂法学保底大学", "法学", 40000),
                )
            )
            return "学费与必要费用", rows, None, None, "school_fit"
        if task.kind == "subject_requirement":
            required_subject = (
                "政治" if self.profile_subject_mode == "3+1+2" else "化学"
            )
            rows = tuple(
                {
                    "province": task.province,
                    "year": task.year,
                    "subject_group": task.subject_group,
                    "institution": name,
                    "institution_code": code,
                    "program_group": "第01组",
                    "required_secondary_subjects": (required_subject,),
                    "secondary_subject_rule": "all",
                    "special_conditions": "无其他已公开限制",
                }
                for code, name in (
                    ("SYN-CH", "合成公办历史冲刺大学"),
                    ("SYN-CL", "合成上海高费调剂法学冲刺大学"),
                    ("SYN-SH", "合成公办历史稳妥大学"),
                    ("SYN-SL", "合成上海高费调剂法学稳妥大学"),
                    ("SYN-BH", "合成公办历史保底大学"),
                    ("SYN-BL", "合成上海高费调剂法学保底大学"),
                )
            )
            return "专业组选科要求", rows, None, None, "school_fit"
        if task.target_name in {"强基计划", "综合评价", "高校专项", "公费师范"}:
            eligibility = (
                "完成高考报名；专项资格待核验"
                if task.target_name == "高校专项"
                else "完成高考报名"
            )
            values = {
                "institution": "合成示例高校", "province": self.profile_province, "subject_mode": self.profile_subject_mode,
                "year": task.year, "eligibility_requirements": eligibility,
                "grade_requirements": "高三" if task.target_name == "综合评价" else "高二",
                "subject_requirements": "历史或物理",
                "award_requirements": "合成学科奖项", "activity_requirements": "合成志愿活动",
                "disqualifying_facts": "受到处分", "professional_options": "合成示例专业",
                "training_arrangements": "校内培养", "transition_rules": "按公开规则考核",
                "outcomes": "按公开规则毕业",
                "service_employment_obligations": "无额外服务期",
                "penalty_exit_rules": "按公开规则退出", "fees_and_subsidies": "按公开标准执行",
                "dates_and_deadlines": "2028-04-30", "application_materials": "成绩与活动材料",
                "preparation_actions": "跟踪官方报名通知",
            }
            conflict_offsets = self.conflict_offsets.get(
                self.task_family(task),
                self.conflict_offsets.get(self.availability_family(task), ()),
            )
            should_conflict = (
                self.conflict
                and self.research_year is not None
                and self.research_year - task.year in conflict_offsets
            )
            if should_conflict and source_index == 2:
                values["professional_options"] = "相互冲突的专业范围"
            return "多元路径政策", (values,), None, None, "pathway"
        return None

    def discover(self, task) -> tuple[DiscoveredCandidate, ...]:
        assert self.attempts is not None
        self.attempts.append(("discover", self.task_family(task), task.year))
        selected_year = self.selected_year(task)
        assert self.available_offsets is not None
        explicit_offsets = self.available_offsets.get(
            self.task_family(task),
            self.available_offsets.get(self.availability_family(task), ()),
        )
        available_years = (
            {self.research_year - offset for offset in explicit_offsets}
            if explicit_offsets and self.research_year is not None
            else {selected_year}
        )
        is_requested_school_history = (
            self.collect_school_history
            and task.kind == "joy_report"
            and task.year == selected_year - 1
        )
        if task.year not in available_years and not is_requested_school_history:
            return ()
        discovered = []
        for index in range(1, self.source_count_for(task) + 1):
            fixture = self._fixture(task, index)
            if fixture is None:
                return ()
            caption, rows, rank_bounds, score_bounds, kind = fixture
            source_digest = hashlib.sha256(
                f"{task.task_id}:{index}".encode("utf-8")
            ).hexdigest()[:24]
            # Machine fixture IDs must not accidentally resemble phone numbers.
            source_id = "source-" + source_digest.translate(
                str.maketrans("0123456789", "ghijklmnop")
            )
            discovered.append(
                DiscoveredCandidate(
                    source_id=source_id,
                    publisher=f"独立发布方 {source_id}",
                    tier=self.source_tier_for(task),
                    year=task.year,
                    kind=kind,
                    caption=caption,
                    rows=rows,
                    rank_bounds=rank_bounds,
                    score_bounds=score_bounds,
                )
            )
        return tuple(discovered)

    def open_body(self, task, candidate: DiscoveredCandidate) -> OpenedBody:
        assert (
            self.attempts is not None
            and self.opened_task_ids is not None
            and self.opened_registry is not None
        )
        payload = {
            "source_marker": candidate.source_id,
            "data": {
                "caption": candidate.caption,
                "rows": candidate.rows,
                "rank_bounds": candidate.rank_bounds,
                "score_bounds": candidate.score_bounds,
                "kind": candidate.kind,
            },
        }
        body = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        body_hash = _digest(payload)
        family = self.task_family(task)
        self.attempts.append(("open", family, task.year))
        if task.task_id not in self.opened_task_ids:
            self.opened_task_ids.append(task.task_id)
        self.opened_registry[candidate.source_id] = (task.task_id, body_hash)
        return OpenedBody(
            discovered=candidate,
            body=body,
            source=_candidate(
                candidate.source_id,
                year=candidate.year,
                content_hash=body_hash,
                tier=candidate.tier,
                publisher=candidate.publisher,
            ),
        )

    def adapt(self, task, opened: tuple[OpenedBody, ...]) -> AdaptedCandidate:
        tables = []
        for item in opened:
            payload = json.loads(item.body.decode("utf-8"))
            assert self.opened_registry is not None
            if self.opened_registry.get(item.discovered.source_id) != (
                task.task_id,
                item.source.content_hash,
            ):
                raise AssertionError("adapter candidate was not opened for this task")
            if item.source.content_hash != _digest(payload):
                raise AssertionError("adapter input is not bound to the opened body")
            data = payload["data"]
            assert self.attempts is not None
            self.attempts.append(("adapt", self.task_family(task), task.year))
            tables.append(
                _table(
                    data["caption"],
                    tuple(data["rows"]),
                    rank_bounds=(
                        tuple(data["rank_bounds"])
                        if data["rank_bounds"] is not None else None
                    ),
                    score_bounds=(
                        tuple(data["score_bounds"])
                        if data["score_bounds"] is not None else None
                    ),
                )
            )
        return AdaptedCandidate(
            sources=tuple(item.source for item in opened),
            tables=tuple(tables),
            kind=opened[0].discovered.kind,
        )


@dataclass(frozen=True)
class WorkflowResult:
    report: object
    markdown: str
    session_stages: tuple[str, ...]
    opened_task_ids: tuple[str, ...]
    attempts: tuple[tuple[str, str, int], ...] = ()
    conflict_fields: tuple[str, ...] = ()
    unavailable_reasons: tuple[tuple[str, str], ...] = ()
    user_visible_internal_paths: tuple[str, ...] = ()
    user_created_files: tuple[str, ...] = ()
    degradations: tuple[str, ...] = ()
    calculation_outcome: object | None = None
    publication_outcome: object | None = None
    rendered_bytes: bytes = b""
    task_receipts: tuple[object, ...] = ()


def school_fit_source_ids(result: WorkflowResult, kind: str) -> frozenset[str]:
    """Read source identity from authenticated receipts, not opaque ID spelling."""
    return frozenset(
        candidate.source_id
        for receipt in result.task_receipts
        if receipt.kind == kind
        for bridge in receipt._bridges
        for candidate in bridge.candidates
    )


def run_agent_workflow(
    *,
    user_answers,
    host: FakeHost,
    user_confirmed: bool = True,
    query_builder=build_query_plan,
    calculator=build_pathway_atlas_model,
    publication_format: str = "markdown",
):
    del calculator  # Calculation authority is the factory-only session outcome.
    student = _normalize_answers(user_answers)
    session = PlanningSession.create(uuid4().hex, student)
    stages = [session.stage.value]
    if not user_confirmed:
        return WorkflowResult(
            None,
            "",
            tuple(stages),
            tuple(host.opened_task_ids or ()),
            tuple(host.attempts or ()),
        )
    session = session.confirm_profile(student.digest)
    stages.append(session.stage.value)
    host.profile_subject_mode = student.subject_mode
    host.profile_province = student.province
    host.profile_school = student.high_school
    policy = DecisionPolicySnapshot.load_default()
    query_plan = query_builder(student, load_province_catalog(), policy)
    host.research_year = query_plan.research_year
    declared_host = tuple(sorted(host.capabilities & {"search", "browse", "vision"}))
    missing_host = tuple(sorted({"search", "browse", "vision"} - set(declared_host)))
    tier = (
        CapabilityTier.OFFLINE if host.offline else
        CapabilityTier.FULL if "vision" in declared_host else
        CapabilityTier.STANDARD
    )
    capability = CapabilityReport(
        tier,
        host_capabilities=declared_host,
        available_capabilities=declared_host,
        missing_capabilities=missing_host,
        degradations=() if tier is CapabilityTier.FULL else tuple(
            f"missing-{name}" for name in missing_host
        ),
        python_version="3.10.0",
        optional_modules=() if host.offline else ("docx", "openpyxl", "pdfplumber"),
    )
    session = session.with_preflight(capability)
    stages.append(session.stage.value)
    session = session.with_query_plan(query_plan, profile=student)
    stages.append(session.stage.value)

    with tempfile.TemporaryDirectory() as temporary:
        store = EvidenceStore.create(Path(temporary).resolve(), capability)
        accepted_families = {}
        task_receipts = {}
        conflict_fields = []
        unresolved_ids = {
            item.task_id for item in session.next_tasks(query_plan, profile=student)
        }
        for task in (item for item in query_plan.tasks if item.task_id in unresolved_ids):
            identity = (task.kind, task.target_name)
            collect_next_school_year = (
                identity in accepted_families
                and host.collect_school_history
                and task.kind == "joy_report"
                and task.year == accepted_families[identity].year - 1
            )
            if identity in accepted_families and not collect_next_school_year:
                session = session.ingest_task(
                    task.task_id,
                    query_plan_digest=session.query_plan_digest,
                    query_plan=query_plan,
                    profile=student,
                    outcome="unavailable",
                    newer_evidence_outcome=accepted_families[identity],
                    unavailable_reason="newer_comparable_year_accepted",
                )
                continue
            discovered = host.discover(task)
            if not discovered:
                session = session.ingest_task(
                    task.task_id, query_plan_digest=session.query_plan_digest,
                    query_plan=query_plan, profile=student,
                    outcome="unavailable",
                    unavailable_reason=(
                        "network_unavailable" if host.offline else
                        "current_year_not_published"
                        if task.year == query_plan.research_year else
                        "source_threshold_not_met"
                    ),
                )
                continue
            opened = tuple(host.open_body(task, item) for item in discovered)
            outcome = host.adapt(task, opened)
            for source in outcome.sources:
                store.add_candidate(source)
            persisted = []
            if outcome.kind == "rank":
                persisted.append(bridge_rank_evidence(
                    profile=student, plan=query_plan, task=task, table=outcome.tables[0],
                    extracted_row=outcome.tables[0].rows[0], candidates=outcome.sources,
                    coverage_status={SourceTier.A: EvidenceStatus.OFFICIAL, SourceTier.B: EvidenceStatus.CORROBORATED, SourceTier.C: EvidenceStatus.REFERENCE}[host.source_tier_for(task)],
                ))
            elif outcome.kind == "admission":
                for index, row in enumerate(outcome.tables[0].rows, 1):
                    persisted.append(bridge_admission_evidence(
                        table=outcome.tables[0], adapter_row=row, task=task,
                        dataset_row=validate_runtime_admission_row(
                            {
                                "year": task.year, "province": student.province,
                                "subject_group": query_plan.subject_group,
                                "school_code": row.values["school_code"],
                                "school_name": row.values["school_name"],
                                "program_group": row.values["program_group"],
                                "min_score": row.values["min_score"],
                                "min_rank": row.values["min_rank"], "remarks": "",
                            },
                            province=student.province,
                            subject_group=query_plan.subject_group,
                            score_scale=student.rank_observations[0].max_score,
                            allowed_years=tuple(range(query_plan.research_year - 3, query_plan.research_year + 1)),
                        ),
                        fact_id=f"admission-{task.year}-{index}", candidates=outcome.sources,
                        coverage_status=(
                            (
                                host.admission_coverage_by_offset or {}
                            ).get(query_plan.research_year - task.year)
                            or host.admission_coverage_status
                            or {
                                SourceTier.A: EvidenceStatus.OFFICIAL,
                                SourceTier.B: EvidenceStatus.CORROBORATED,
                                SourceTier.C: EvidenceStatus.REFERENCE,
                            }[host.source_tier_for(task)]
                        ),
                    ))
            elif outcome.kind == "school_fit":
                row_count = len(outcome.tables[0].rows)
                if any(len(table.rows) != row_count for table in outcome.tables):
                    raise AssertionError("school-fit sources are not row-aligned")
                for index in range(row_count):
                    persisted.append(
                        bridge_school_fit_evidence(
                            profile=student,
                            plan=query_plan,
                            task=task,
                            tables=outcome.tables,
                            adapter_rows=tuple(
                                table.rows[index] for table in outcome.tables
                            ),
                            candidates=outcome.sources,
                        )
                    )
            else:
                projection = extract_pathway_policy(
                    profile=student, plan=query_plan, task=task,
                    extraction=(
                        outcome.tables[0] if len(outcome.tables) == 1
                        else outcome.tables
                    ),
                    field_map=(
                        {name: name for name in _PATHWAY_FIELDS}
                        if len(outcome.sources) == 1
                        else tuple(
                            {name: name for name in _PATHWAY_FIELDS}
                            for _ in outcome.sources
                        )
                    ),
                    candidates=outcome.sources,
                )
                pathway_bridge = bridge_pathway_policy_evidence(projection)
                persisted.append(pathway_bridge)
                if pathway_bridge.evidence_status is EvidenceStatus.CONFLICT:
                    conflict_fields.append(pathway_bridge.fact.field)
            for bridge in persisted:
                bridge.persist(store)
            receipt = build_task_evidence_outcome(
                student, query_plan, task, tuple(persisted)
            )
            task_receipts[task.task_id] = receipt
            session = session.ingest_task(
                task.task_id, query_plan_digest=session.query_plan_digest,
                query_plan=query_plan, profile=student,
                outcome="completed", evidence_outcome=receipt,
            )
            if receipt.usable and "official" in receipt.evidence_statuses:
                accepted_families.setdefault(identity, receipt)
        stages.append(session.stage.value)
        store.finalize()
        validated = validate_bundle_snapshot(store.session_path, _allow_empty=True)
        if validated.issues:
            raise AssertionError(validated.issues)
        assert validated.snapshot is not None
        evidence_outcome = build_evidence_manifest_outcome(
            session,
            student,
            query_plan,
            bundle_path=store.session_path,
            task_outcomes=tuple(task_receipts.values()),
            capability_report=capability,
        )
        session = session.finalize_evidence(
            evidence_outcome,
            query_plan=query_plan,
            profile=student,
        )
        stages.append(session.stage.value)
        calculation_outcome = build_calculation_outcome(
            session,
            evidence_outcome,
            student,
            query_plan,
            decision_policy=policy,
        )
        model = calculation_outcome.model
        session = session.with_calculation(
            calculation_outcome,
            query_plan=query_plan,
            profile=student,
        )
        stages.append(session.stage.value)
        publication_outcome = build_report_publication_outcome(
            calculation_outcome,
            format=publication_format,
        )
        markdown = (
            publication_outcome.markdown
            if publication_format == "markdown"
            else ""
        )
        session = session.publish_report(
            publication_outcome,
            query_plan=query_plan,
            profile=student,
        )
        stages.append(session.stage.value)
        assert session.stage is SessionStage.REPORT_PUBLISHED
        return WorkflowResult(
            report=model,
            markdown=markdown,
            session_stages=tuple(stages),
            opened_task_ids=tuple(host.opened_task_ids or ()),
            attempts=tuple(host.attempts or ()),
            conflict_fields=tuple(sorted(conflict_fields)),
            unavailable_reasons=tuple(
                zip(session.unavailable_task_ids, session.unavailable_reason_codes)
            ),
            degradations=tuple(session.status()["degradations"]),
            calculation_outcome=calculation_outcome,
            publication_outcome=publication_outcome,
            rendered_bytes=publication_outcome.rendered_bytes,
            task_receipts=tuple(
                task_receipts[task_id] for task_id in sorted(task_receipts)
            ),
        )


class EndToEndPlanningTest(unittest.TestCase):
    def _assert_available_and_missing_pathways(self, report):
        available_titles = {"强基计划", "综合评价", "高校专项"}
        missing_titles = {"国家专项", "地方专项", "港澳招生", "中外合作办学"}
        self.assertEqual(
            {item.title for item in report.pathways},
            available_titles | missing_titles,
        )
        self.assertEqual(len(report.pathways), 7)
        available = tuple(
            item for item in report.pathways if item.title in available_titles
        )
        for item in available:
            self.assertTrue(item.source_ids)
            self.assertIsNotNone(item.data_year)
        for item in report.pathways:
            if item.title in missing_titles:
                with self.subTest(missing_pathway=item.title):
                    self.assertEqual(item.status, "pending_verification")
                    self.assertEqual(item.investment_decision, "观察")
                    self.assertEqual(item.qualification_status, "待核验")
                    self.assertIs(item.evidence_status, EvidenceStatus.MISSING)
                    self.assertEqual(item.source_ids, ())
                    self.assertIsNone(item.data_year)
                    self.assertIsNone(item.target_rank)
                    self.assertEqual(item.year_basis, "unverified")
        return available

    @staticmethod
    def _assert_rank_field_trails(rank, fields):
        for field in fields:
            if getattr(rank, field) is not None and not (
                rank.source_ids
                and rank.channel_statuses
                and rank.contributing_years
                and rank.basis
            ):
                raise AssertionError(f"rank.{field} has no evidence trail")

    @staticmethod
    def _assert_item_field_trails(item, fields, path, *, require_supporting_years=False):
        accepted = {"official", "corroborated", "reference", "inferred"}
        for field in fields:
            status = getattr(item.evidence_status, "value", item.evidence_status)
            has_trail = (
                bool(item.source_ids)
                and status in accepted
                and bool(item.data_year)
                and bool(item.calculation_basis)
            )
            if require_supporting_years:
                has_trail = has_trail and bool(item.supporting_years)
            elif hasattr(item, "year_basis"):
                has_trail = has_trail and item.year_basis in {
                    "current_year", "historical_fallback"
                }
            if not has_trail:
                raise AssertionError(f"{path}.{field} has no evidence trail")

    @classmethod
    def assert_field_level_trails(cls, report):
        rank = report.rank
        cls._assert_rank_field_trails(
            rank, ("optimistic_rank", "central_rank", "conservative_rank")
        )
        for index, school in enumerate(report.recommendations):
            cls._assert_item_field_trails(
                school,
                ("min_score", "min_rank"),
                f"recommendations[{index}]",
                require_supporting_years=True,
            )
        for index, pathway in enumerate(report.pathways):
            trails = {item.field: item for item in pathway.field_evidence}
            if tuple(sorted(trails)) != tuple(
                sorted(PATHWAY_DISPLAY_EVIDENCE_FIELDS)
            ):
                raise AssertionError(f"pathways[{index}] field evidence is incomplete")
            for field in PATHWAY_DISPLAY_EVIDENCE_FIELDS:
                trail = trails[field]
                if pathway.data_year is None:
                    valid = bool(trail.evidence_method) and (
                        (
                            trail.coverage == "complete"
                            and trail.status is EvidenceStatus.INFERRED
                        )
                        or (
                            trail.coverage in {"missing", "partial", "conflict"}
                            and trail.status
                            in {
                                EvidenceStatus.MISSING,
                                EvidenceStatus.MASKED,
                                EvidenceStatus.PARTIAL,
                                EvidenceStatus.CONFLICT,
                            }
                            and (
                                bool(trail.source_ids)
                                or trail.status
                                in {EvidenceStatus.MISSING, EvidenceStatus.MASKED}
                            )
                        )
                    )
                else:
                    valid = bool(
                        trail.source_ids
                        and trail.status.value
                        in {"official", "corroborated", "reference", "inferred"}
                        and trail.coverage == "complete"
                        and trail.evidence_method
                    )
                if not valid:
                    raise AssertionError(
                        f"pathways[{index}].{field} has no field evidence trail"
                    )

    def test_fake_host_intake_does_not_invent_unanswered_profile_fields(self):
        student = _normalize_answers(anonymous_twenty_answer_payload())

        self.assertEqual(student.preparation_assets.subject_strengths, ())
        self.assertEqual(student.preparation_assets.research_experiences, ())
        self.assertEqual(
            student.preparation_assets.activities,
            ("合成研究性学习", "合成志愿活动"),
        )
        self.assertEqual(student.preparation_assets.english_readiness, "unknown")
        self.assertEqual(student.preparation_assets.interview_readiness, "unknown")
        self.assertEqual(student.preparation_assets.physical_readiness, "unknown")

    def test_unconfirmed_intake_calls_no_query_host_or_calculation_boundary(self):
        boundary_calls = []

        def query_spy(*args, **kwargs):
            boundary_calls.append("query")
            return build_query_plan(*args, **kwargs)

        def calculation_spy(*args, **kwargs):
            boundary_calls.append("calculation")
            return build_pathway_atlas_model(*args, **kwargs)

        host = FakeHost.official_current_year()
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            user_confirmed=False,
            host=host,
            query_builder=query_spy,
            calculator=calculation_spy,
        )

        self.assertEqual(result.session_stages, ("intake",))
        self.assertEqual(boundary_calls, [])
        self.assertEqual(host.opened_task_ids, [])

    def test_user_answers_reach_report_without_internal_json(self):
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=FakeHost.official_current_year(),
        )
        self.assertTrue(result.report.priority_actions)
        self.assertTrue(result.report.recommendations)
        self.assertTrue(result.report.pathways)
        self.assertEqual(result.user_visible_internal_paths, ())
        self.assertEqual(result.user_created_files, ())
        self.assertEqual(
            result.session_stages,
            (
                "intake", "profile_confirmed", "preflight_complete",
                "query_plan_ready", "research_in_progress", "evidence_finalized",
                "calculation_complete", "report_published",
            ),
        )
        available_pathways = self._assert_available_and_missing_pathways(result.report)
        self.assertEqual(
            {item.investment_decision for item in available_pathways},
            {"主攻", "备选", "不建议"},
        )
        self.assertIn("不构成升学建议或录取承诺", result.markdown)
        self.assertTrue(result.calculation_outcome.degraded)

    def test_typed_docx_publication_closes_the_same_session_chain(self):
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=FakeHost.official_current_year(),
            publication_format="docx",
        )

        self.assertEqual(result.session_stages[-1], "report_published")
        self.assertEqual(result.publication_outcome.format, "docx")
        self.assertTrue(result.rendered_bytes.startswith(b"PK"))
        self.assertEqual(result.markdown, "")

    def test_profile_reported_rank_is_inferred_without_rank_research_receipt(self):
        host = FakeHost.official_current_year()
        host.available_offsets = {
            "score_table": (99,),
            "joy_report": (99,),
        }
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=host,
        )

        self.assertEqual(
            result.report.rank.basis,
            "profile_reported_province_rank",
        )
        self.assertEqual(result.report.rank.status.value, "inferred")
        self.assertLess(
            result.report.rank.optimistic_rank,
            result.report.rank.central_rank,
        )
        self.assertLess(
            result.report.rank.central_rank,
            result.report.rank.conservative_rank,
        )
        self.assertEqual(result.report.rank.central_rank, 18000)
        self.assertTrue(result.report.recommendations)
        self.assertEqual(result.report.rank.source_ids, ("profile-reported-rank",))

    def test_user_reported_rank_cannot_be_relabeled_as_official(self):
        real_builder = build_pathway_atlas_model

        def relabel_as_official(*args, **kwargs):
            model = real_builder(*args, **kwargs)
            object.__setattr__(model.rank, "status", EvidenceStatus.OFFICIAL)
            object.__setattr__(model.rank, "basis", "official_province_rank")
            object.__setattr__(model.rank, "source_ids", ("profile-official-rank",))
            object.__setattr__(model.rank, "channel_kinds", ("official_rank",))
            object.__setattr__(model.rank, "channel_statuses", ("official",))
            object.__setattr__(
                model,
                "source_ids",
                tuple(
                    sorted(
                        {
                            *(
                                item
                                for item in model.source_ids
                                if item != "profile-reported-rank"
                            ),
                            "profile-official-rank",
                        }
                    )
                ),
            )
            return model

        host = FakeHost.official_current_year()
        host.available_offsets = {"score_table": (99,), "joy_report": (99,)}
        with mock.patch(
            "scripts.generate_report.build_pathway_atlas_model",
            side_effect=relabel_as_official,
        ), self.assertRaisesRegex(
            SessionTransitionError,
            "outside completed typed evidence|canonical inference",
        ):
            run_agent_workflow(
                user_answers=anonymous_twenty_answer_payload(),
                host=host,
            )

    def test_score_conversion_without_rank_receipt_cannot_show_numeric_rank(self):
        host = FakeHost.official_current_year()
        host.available_offsets = {
            "score_table": (99,),
            "joy_report": (99,),
        }
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(rank_scope="school"),
            host=host,
        )

        self.assertIsNone(result.report.rank.central_rank)
        self.assertFalse(result.report.recommendations)

    def test_partial_admission_rows_remain_non_numeric_observations(self):
        host = FakeHost.official_current_year()
        host.admission_coverage_status = EvidenceStatus.PARTIAL
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=host,
        )

        admission_years = [
            year
            for action, family, year in result.attempts
            if action == "discover" and family == "batch_admission:普通批"
        ]
        self.assertEqual(admission_years, [2026, 2025, 2024, 2023])
        self.assertFalse(result.report.recommendations)
        self.assertTrue(result.report.school_observations)
        self.assertTrue(
            all(
                item.evidence_status is EvidenceStatus.PARTIAL
                and not hasattr(item, "min_score")
                and not hasattr(item, "min_rank")
                for item in result.report.school_observations
            )
        )
        self.assertEqual(
            result.report.recommendation_coverage_status.value,
            "partial",
        )
        self.assertEqual(result.report.usable_years, ())
        self.assertIsNone(result.report.verified_rank_coverage)
        self.assertTrue(result.calculation_outcome.degraded)
        self.assertIn("仅作方向性观察", result.markdown)
        self.assertIn("不进入冲稳保", result.markdown)
        model_payload = result.report.to_dict()
        self.assertEqual(
            result.calculation_outcome.recommendation_result_digest,
            _digest(
                {
                    key: model_payload[key]
                    for key in (
                        "recommendations",
                        "school_observations",
                        "school_decisions",
                        "recommendation_policy_status",
                        "recommendation_coverage_status",
                        "verified_rank_coverage",
                        "recommendation_empty_reason",
                        "recommendation_warnings",
                    )
                }
            ),
        )

    def test_partial_observation_cannot_be_forged_as_numeric_recommendation(self):
        host = FakeHost.official_current_year()
        host.admission_coverage_status = EvidenceStatus.PARTIAL
        real_builder = build_pathway_atlas_model

        def forge_partial_observation_as_numeric(*args, **kwargs):
            model = real_builder(*args, **kwargs)
            if not model.school_observations:
                raise AssertionError("attack fixture produced no partial observation")
            if model.recommendation_coverage_status is not EvidenceStatus.PARTIAL:
                raise AssertionError("attack fixture lacks a partial displayed source")
            observation = model.school_observations[0]
            forged = ReportRecommendation(
                strategy="稳",
                school_name=observation.school_name,
                school_level=observation.school_level,
                city=observation.city,
                min_score=610,
                min_rank=18000,
                delta=0,
                remarks="",
                match_reason="伪造的精确投档结论",
                data_year=observation.data_year,
                source_ids=observation.source_ids,
                evidence_status=EvidenceStatus.OFFICIAL,
                calculation_basis="伪造的冲稳保依据",
                supporting_years=(observation.data_year,),
            )
            object.__setattr__(
                model,
                "recommendations",
                (*model.recommendations, forged),
            )
            return model

        with mock.patch(
            "scripts.generate_report.build_pathway_atlas_model",
            side_effect=forge_partial_observation_as_numeric,
        ):
            with self.assertRaisesRegex(
                SessionTransitionError,
                "matching exact admission rows|exact accepted evidence",
            ):
                run_agent_workflow(
                    user_answers=anonymous_twenty_answer_payload(),
                    host=host,
                )

    def test_every_displayed_record_requires_nonempty_source_bindings(self):
        real_builder = build_pathway_atlas_model

        for target, message in (
            ("rank", "displayed numeric rank requires authenticated sources"),
            ("school", "displayed school requires authenticated sources"),
            ("pathway", "displayed pathway requires authenticated sources"),
        ):
            with self.subTest(target=target):
                def clear_sources(*args, **kwargs):
                    model = real_builder(*args, **kwargs)
                    if target == "rank":
                        object.__setattr__(model.rank, "source_ids", ())
                        object.__setattr__(
                            model,
                            "source_ids",
                            tuple(
                                source_id
                                for source_id in model.source_ids
                                if source_id != "profile-reported-rank"
                            ),
                        )
                    elif target == "school":
                        object.__setattr__(
                            model.recommendations[0],
                            "source_ids",
                            (),
                        )
                    else:
                        object.__setattr__(model.pathways[0], "source_ids", ())
                    return model

                with mock.patch(
                    "scripts.generate_report.build_pathway_atlas_model",
                    side_effect=clear_sources,
                ):
                    with self.assertRaisesRegex(SessionTransitionError, message):
                        run_agent_workflow(
                            user_answers=anonymous_twenty_answer_payload(),
                            host=FakeHost.official_current_year(),
                        )

    def test_every_displayed_school_rank_and_pathway_has_a_trail(self):
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=FakeHost.official_current_year(),
        )
        rank = result.report.rank
        self.assertTrue(rank.source_ids)
        self.assertTrue(rank.channel_statuses)
        self.assertTrue(rank.contributing_years)
        for school in result.report.recommendations:
            self.assertTrue(school.source_ids)
            self.assertTrue(school.evidence_status)
            self.assertTrue(school.data_year)
            self.assertTrue(school.calculation_basis)
        for pathway in result.report.pathways:
            self.assertTrue(pathway.evidence_status)
            self.assertTrue(pathway.calculation_basis)
            if pathway.data_year is None:
                self.assertEqual(pathway.status, "pending_verification")
                self.assertIsNone(pathway.target_year)
                self.assertEqual(pathway.year_basis, "unverified")
                if not pathway.source_ids:
                    self.assertIn(
                        pathway.evidence_status,
                        {EvidenceStatus.MISSING, EvidenceStatus.MASKED},
                    )
            else:
                self.assertTrue(pathway.source_ids)
        self.assert_field_level_trails(result.report)
        self.assertIn("逐字段证据", result.markdown)
        for field in PATHWAY_DISPLAY_EVIDENCE_FIELDS:
            self.assertIn(field, result.markdown)

        for field in ("optimistic_rank", "central_rank", "conservative_rank"):
            with self.subTest(canary=f"rank.{field}"):
                broken = copy(result.report.rank)
                object.__setattr__(broken, "source_ids", ())
                with self.assertRaisesRegex(
                    AssertionError, rf"rank\.{field} has no evidence trail"
                ):
                    self._assert_rank_field_trails(broken, (field,))
        for field in ("min_score", "min_rank"):
            with self.subTest(canary=f"school.{field}"):
                broken = copy(result.report.recommendations[0])
                object.__setattr__(broken, "source_ids", ())
                with self.assertRaisesRegex(
                    AssertionError, rf"recommendations\[0\]\.{field} has no evidence trail"
                ):
                    self._assert_item_field_trails(
                        broken,
                        (field,),
                        "recommendations[0]",
                        require_supporting_years=True,
                    )
        for field in PATHWAY_DISPLAY_EVIDENCE_FIELDS:
            with self.subTest(canary=f"pathway.{field}"):
                broken = copy(result.report.pathways[0])
                object.__setattr__(
                    broken,
                    "field_evidence",
                    tuple(
                        trail for trail in broken.field_evidence
                        if trail.field != field
                    ),
                )
                broken_model = copy(result.report)
                object.__setattr__(
                    broken_model,
                    "pathways",
                    (broken, *result.report.pathways[1:]),
                )
                with self.assertRaisesRegex(
                    ValueError, rf"pathway.*field evidence.*{field}"
                ):
                    render_markdown(broken_model)

    def test_full_profile_pathway_clock_has_no_raw_caller_owned_year(self):
        self.assertNotIn(
            "research_year", inspect.signature(evaluate_pathways).parameters
        )

    def test_each_family_falls_back_to_the_latest_available_historical_year(self):
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=FakeHost.historical_official(),
        )
        self.assertEqual({item.data_year for item in result.report.recommendations}, {2024})
        available_pathways = self._assert_available_and_missing_pathways(result.report)
        self.assertEqual({item.data_year for item in available_pathways}, {2023})
        self.assertTrue(all(item.fallback_distance == 3 for item in available_pathways))
        self.assertTrue(all(item.year_basis == "historical_fallback" for item in available_pathways))

        discovery_years = {}
        for action, family, year in result.attempts:
            if action == "discover":
                discovery_years.setdefault(family, []).append(year)
        self.assertEqual(discovery_years["score_table"], [2026])
        self.assertEqual(
            discovery_years["joy_report:武汉市示例中学"], [2026, 2025]
        )
        self.assertEqual(
            discovery_years["batch_admission:普通批"], [2026, 2025, 2024]
        )
        for family in (
            "strong_foundation:强基计划",
            "comprehensive_evaluation:综合评价",
            "special_pathway:高校专项",
        ):
            self.assertEqual(discovery_years[family], [2026, 2025, 2024, 2023])
        attempted = {(action, family, year) for action, family, year in result.attempts}
        for family, unavailable_years in (
            ("joy_report:武汉市示例中学", (2026,)),
            ("batch_admission:普通批", (2026, 2025)),
        ):
            for year in unavailable_years:
                self.assertNotIn(("open", family, year), attempted)
                self.assertNotIn(("adapt", family, year), attempted)

        plan = build_query_plan(
            _normalize_answers(anonymous_twenty_answer_payload()),
            load_province_catalog(),
            DecisionPolicySnapshot.load_default(),
        )
        reasons = dict(result.unavailable_reasons)
        for task in plan.tasks:
            identity = FakeHost.task_family(task)
            accepted_years = {
                "score_table": 2026,
                "joy_report:武汉市示例中学": 2025,
                "batch_admission:普通批": 2024,
                "strong_foundation:强基计划": 2023,
                "comprehensive_evaluation:综合评价": 2023,
                "special_pathway:高校专项": 2023,
            }
            accepted_year = accepted_years.get(identity)
            if accepted_year is not None and task.year < accepted_year:
                self.assertEqual(
                    reasons[task.task_id], "newer_comparable_year_accepted"
                )

    def test_adapter_is_bound_to_the_exact_opened_body(self):
        host = FakeHost.official_current_year()
        profile = _normalize_answers(anonymous_twenty_answer_payload())
        plan = build_query_plan(
            profile, load_province_catalog(), DecisionPolicySnapshot.load_default()
        )
        host.research_year = plan.research_year
        task = next(item for item in plan.tasks if item.kind == "score_table")
        opened = host.open_body(task, host.discover(task)[0])
        changed = replace(opened, body=opened.body.replace(b"18000", b"18001"))
        with self.assertRaisesRegex(AssertionError, "opened body"):
            host.adapt(task, (changed,))
        unopened_host = FakeHost.official_current_year()
        unopened_host.research_year = plan.research_year
        discovered = unopened_host.discover(task)[0]
        forged = replace(opened, discovered=discovered)
        with self.assertRaisesRegex(AssertionError, "not opened"):
            unopened_host.adapt(task, (forged,))

    def test_two_b_and_three_c_produce_labeled_reference_decisions(self):
        cases = (
            (FakeHost.two_independent_b(), "corroborated"),
            (FakeHost.three_independent_c(), "reference"),
        )
        for host, expected in cases:
            with self.subTest(expected=expected):
                result = run_agent_workflow(
                    user_answers=anonymous_twenty_answer_payload(), host=host
                )
                self.assertEqual(
                    {item.evidence_status.value for item in result.report.recommendations},
                    {expected},
                )
                available_pathways = self._assert_available_and_missing_pathways(result.report)
                self.assertEqual(
                    {item.evidence_status.value for item in available_pathways},
                    {expected},
                )
                self.assertGreaterEqual(len(host.opened_task_ids or ()), host.source_count)

    def test_current_reference_is_retained_while_lookup_continues_to_official_baseline(self):
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=FakeHost.current_reference_then_historical_official(),
        )
        expected_families = {
            ("score_table", None),
            ("joy_report", "武汉市示例中学"),
            ("batch_admission", "普通批"),
            ("strong_foundation", "强基计划"),
            ("comprehensive_evaluation", "综合评价"),
            ("special_pathway", "高校专项"),
        }
        for family in expected_families:
            receipts = sorted(
                (
                    receipt.year,
                    tuple(sorted(set(receipt.evidence_statuses))),
                    receipt.usable,
                )
                for receipt in result.task_receipts
                if (receipt.kind, receipt.target_name) == family
            )
            self.assertEqual(
                receipts,
                [
                    (2025, ("official",), True),
                    (2026, ("corroborated",), True),
                ],
                family,
            )
            attempted_years = [
                year
                for action, attempted_family, year in result.attempts
                if action == "discover"
                and attempted_family
                == (family[0] if family[1] is None else f"{family[0]}:{family[1]}")
            ]
            self.assertEqual(attempted_years, [2026, 2025], family)

    def test_school_anchor_inference_and_both_subject_modes_reach_reports(self):
        for mode in ("3+1+2", "3+3"):
            with self.subTest(mode=mode):
                result = run_agent_workflow(
                    user_answers=anonymous_twenty_answer_payload(
                        subject_mode=mode, rank_scope="school"
                    ),
                    host=FakeHost(
                        frozenset({
                            "search", "browse", "vision", "local_exec", "file_output"
                        }),
                        collect_school_history=True,
                    ),
                )
                self.assertIn("school_anchor", result.report.rank.channel_kinds)
                self.assertIsNotNone(result.report.rank.central_rank)
                self.assertTrue(result.report.recommendations)
                self.assertTrue(result.report.pathways)

    def test_rank_replay_distinguishes_encoded_publisher_digest_from_public_text(self):
        from scripts.adapters.rank_bridge import (
            RankBridgeError,
            _candidate_from_projection,
            _candidate_projection,
            _replay_rank_evidence_fact,
        )

        student = _normalize_answers(anonymous_twenty_answer_payload(rank_scope="school"))
        plan = build_query_plan(
            student, load_province_catalog(), DecisionPolicySnapshot.load_default()
        )
        host = FakeHost.official_current_year()
        host.research_year = plan.research_year
        host.profile_subject_mode = student.subject_mode
        host.profile_province = student.province
        host.profile_school = student.high_school
        task = next(task for task in plan.tasks if task.kind == "joy_report")
        discovered = replace(
            host.discover(task)[0],
            publisher="独立发布方 source-bjhdelhkfpgbndacblfjnnlg",
        )
        adapted = host.adapt(task, (host.open_body(task, discovered),))
        bridge = bridge_rank_evidence(
            profile=student, plan=plan, task=task,
            table=adapted.tables[0], extracted_row=adapted.tables[0].rows[0],
            candidates=adapted.sources, coverage_status=EvidenceStatus.OFFICIAL,
        )
        replayed = _replay_rank_evidence_fact(bridge.fact.to_dict(), student, plan)
        self.assertEqual(replayed.fact.to_dict(), bridge.fact.to_dict())

        encoded = _candidate_from_projection(_candidate_projection(adapted.sources[0]))
        for unencoded_publisher in (
            encoded.publisher + "x",
            "公开发布方 " + encoded.publisher.removeprefix("rank-publisher-sha256-"),
        ):
            with self.subTest(publisher=unencoded_publisher):
                with self.assertRaisesRegex(RankBridgeError, "publisher.*unsafe public text"):
                    _candidate_projection(replace(encoded, publisher=unencoded_publisher))

    def test_offline_authenticated_material_still_returns_a_labeled_partial_plan(self):
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=FakeHost.offline_only(),
        )
        self.assertTrue(result.report.recommendations)
        self.assertTrue(result.report.pathways)
        available_pathways = self._assert_available_and_missing_pathways(result.report)
        self.assertEqual({item.data_year for item in available_pathways}, {2025})
        self.assertGreaterEqual(
            set(result.degradations),
            {"missing_search", "missing_browse", "missing_vision", "network_unavailable"},
        )

    def test_conflicts_are_disclosed_and_never_averaged_into_a_pathway_fact(self):
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=FakeHost.conflicting_sources(),
        )
        self.assertTrue(result.conflict_fields)
        self.assertTrue(result.report.pathways)
        self.assertTrue(
            all(
                item.status == "pending_verification"
                and item.investment_decision == "观察"
                and item.qualification_status == "待核验"
                and item.target_rank is None
                and item.target_year is None
                and item.data_year is None
                and not item.timeline
                for item in result.report.pathways
            )
        )
        conflicted = tuple(
            item
            for item in result.report.pathways
            if item.evidence_status is EvidenceStatus.CONFLICT
        )
        self.assertTrue(conflicted)
        self.assertTrue(all(item.source_ids for item in conflicted))
        self.assertTrue(
            all(
                any("冲突" in gap for gap in item.missing_constraints)
                for item in conflicted
            )
        )
        missing = tuple(
            item
            for item in result.report.pathways
            if item.evidence_status is EvidenceStatus.MISSING
        )
        self.assertTrue(missing)
        self.assertTrue(all(not item.source_ids for item in missing))
        action_ids = {item.action_id for item in result.report.action_items}
        for item in result.report.pathways:
            self.assertIn(
                f"pathway-evidence-review:{item.policy_id}", action_ids
            )
            self.assertIn(
                f"qualification-blocker:{item.policy_id}", action_ids
            )
        self.assertEqual(result.report.rank.central_rank, 18000)
        conflict_warning = next(
            warning
            for warning in result.report.pathway_warnings
            if "路径政策证据冲突" in warning
        )
        for field in result.conflict_fields:
            self.assertIn(field.removeprefix("pathway_policy:"), conflict_warning)
        self.assertIn("路径政策证据冲突", result.markdown)
        self.assertNotIn("相互冲突的专业范围", result.markdown)
        self.assertNotIn("合成示例专业 / 相互冲突的专业范围", result.markdown)
        pathway_families = {
            "strong_foundation:强基计划",
            "comprehensive_evaluation:综合评价",
            "special_pathway:高校专项",
        }
        for family in pathway_families:
            self.assertEqual(
                [
                    year for action, attempted_family, year in result.attempts
                    if action == "discover" and attempted_family == family
                ],
                [2026, 2025, 2024, 2023],
            )
        plan = build_query_plan(
            _normalize_answers(anonymous_twenty_answer_payload()),
            load_province_catalog(),
            DecisionPolicySnapshot.load_default(),
        )
        pathway_task_ids = {
            task.task_id
            for task in plan.tasks
            if FakeHost.task_family(task) in pathway_families
        }
        self.assertFalse(
            any(
                task_id in pathway_task_ids
                and reason == "newer_comparable_year_accepted"
                for task_id, reason in result.unavailable_reasons
            )
        )

        clean = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=FakeHost.official_current_year(),
        )
        self.assertFalse(
            any(
                "路径政策证据冲突" in warning
                for warning in clean.report.pathway_warnings
            )
        )

    def test_conflicting_current_pathway_continues_to_usable_historical_year(self):
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=FakeHost.current_conflict_then_historical(),
        )
        self.assertTrue(result.report.pathways)
        evaluated = tuple(
            item for item in result.report.pathways if item.data_year is not None
        )
        observations = tuple(
            item for item in result.report.pathways if item.data_year is None
        )
        self.assertTrue(evaluated)
        self.assertEqual({item.data_year for item in evaluated}, {2025})
        self.assertTrue(observations)
        self.assertTrue(
            all(
                item.status == "pending_verification"
                and item.target_year is None
                and item.year_basis == "unverified"
                for item in observations
            )
        )
        for family in (
            "strong_foundation:强基计划",
            "comprehensive_evaluation:综合评价",
            "special_pathway:高校专项",
        ):
            self.assertEqual(
                [
                    year for action, attempted_family, year in result.attempts
                    if action == "discover" and attempted_family == family
                ],
                [2026, 2025, 2024, 2023],
            )

    def test_equal_rank_profiles_change_pathway_and_action_fingerprint(self):
        prepared = anonymous_twenty_answer_payload()
        unprepared = anonymous_twenty_answer_payload()
        unprepared[10] = []
        unprepared[20]["eligibility"].remove("合成学科奖项")
        left = run_agent_workflow(user_answers=prepared, host=FakeHost.official_current_year())
        right = run_agent_workflow(user_answers=unprepared, host=FakeHost.official_current_year())
        self.assertEqual(left.report.rank.central_rank, right.report.rank.central_rank)
        left_fingerprint = (
            tuple((item.title, item.investment_decision) for item in left.report.pathways),
            tuple(item.action_id for item in left.report.priority_actions),
        )
        right_fingerprint = (
            tuple((item.title, item.investment_decision) for item in right.report.pathways),
            tuple(item.action_id for item in right.report.priority_actions),
        )
        self.assertNotEqual(left_fingerprint, right_fingerprint)

    def test_school_fit_tasks_close_profile_sensitive_recommendation_evidence(self):
        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=FakeHost.official_current_year(),
        )
        fit_task_kinds = {
            receipt.kind for receipt in result.task_receipts
        }
        self.assertGreaterEqual(
            fit_task_kinds,
            {
                "province_policy",
                "enrollment_plan",
                "admission_charter",
                "tuition_fee",
                "subject_requirement",
            },
        )
        opened_families = {
            family for action, family, _year in result.attempts if action == "open"
        }
        self.assertGreaterEqual(
            opened_families,
            {
                "province_policy",
                "enrollment_plan",
                "admission_charter",
                "tuition_fee",
                "subject_requirement",
            },
        )
        self.assertTrue(result.report.recommendations)
        included_reasons = tuple(
            reason
            for decision in result.report.school_decisions
            if decision.outcome == "included"
            for reason in decision.reasons
        )
        for kind in ("enrollment_plan", "subject_requirement", "admission_charter", "tuition_fee"):
            with self.subTest(kind=kind):
                expected_sources = school_fit_source_ids(result, kind)
                self.assertTrue(expected_sources)
                self.assertTrue(any(expected_sources.intersection(reason.source_ids) for reason in included_reasons))

    def test_charter_and_tuition_year_fallback_are_independent(self):
        host = FakeHost.official_current_year()
        host.available_offsets = {
            "admission_charter": (1,),
            "tuition_fee": (0,),
        }

        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=host,
        )

        self.assertEqual(
            [
                year for action, family, year in result.attempts
                if action == "discover" and family == "admission_charter"
            ],
            [2026, 2025],
        )
        self.assertEqual(
            [
                year for action, family, year in result.attempts
                if action == "discover" and family == "tuition_fee"
            ],
            [2026],
        )
        receipt_years = {
            receipt.kind: receipt.year
            for receipt in result.task_receipts
            if receipt.kind in {"admission_charter", "tuition_fee"}
        }
        self.assertEqual(
            receipt_years,
            {"admission_charter": 2025, "tuition_fee": 2026},
        )

    def test_unavailable_school_fit_family_is_degraded_with_visible_reasons(self):
        host = FakeHost.official_current_year()
        host.available_offsets = {
            "province_policy": (99,),
            "enrollment_plan": (99,),
            "admission_charter": (99,),
            "tuition_fee": (99,),
            "subject_requirement": (99,),
        }

        result = run_agent_workflow(
            user_answers=anonymous_twenty_answer_payload(),
            host=host,
        )

        self.assertTrue(result.report.recommendations)
        self.assertTrue(result.calculation_outcome.degraded)
        for literal in (
            "省级招考政策缺少可回放 receipt",
            "招生计划/院校属性缺少可回放 receipt",
            "招生章程/录取与调剂规则缺少可回放 receipt",
            "学费/必要费用缺少可回放 receipt",
            "专业组选科要求缺少可回放 receipt",
        ):
            self.assertTrue(
                any(
                    literal in warning
                    for warning in result.report.recommendation_warnings
                )
            )
            self.assertIn(literal, result.markdown)
        decision_codes = {
            reason.code
            for decision in result.report.school_decisions
            for reason in decision.reasons
        }
        self.assertIn("SCHOOL_PROVINCE_POLICY_UNVERIFIED", decision_codes)
        self.assertIn("SCHOOL_SUBJECT_UNVERIFIED", decision_codes)

    def test_real_twenty_question_q14_q16_q20_each_changes_school_decision(self):
        baseline_answers = anonymous_twenty_answer_payload()
        baseline = run_agent_workflow(
            user_answers=baseline_answers,
            host=FakeHost.official_current_year(),
        )

        q14_answers = deepcopy(baseline_answers)
        q14_answers[14] = ["法学"]
        q14 = run_agent_workflow(
            user_answers=q14_answers,
            host=FakeHost.official_current_year(),
        )

        q16_answers = deepcopy(baseline_answers)
        q16_answers[16]["excluded"] = ["上海"]
        q16 = run_agent_workflow(
            user_answers=q16_answers,
            host=FakeHost.official_current_year(),
        )

        q20_answers = deepcopy(baseline_answers)
        q20_answers[20]["budget"] = "limited"
        q20 = run_agent_workflow(
            user_answers=q20_answers,
            host=FakeHost.official_current_year(),
        )

        baseline_stable = tuple(
            item.school_name
            for item in baseline.report.recommendations
            if item.strategy == "稳"
        )
        q14_stable = tuple(
            item.school_name
            for item in q14.report.recommendations
            if item.strategy == "稳"
        )
        self.assertIn("历史", baseline_stable[0])
        self.assertIn("法学", q14_stable[0])
        self.assertNotEqual(baseline_stable, q14_stable)
        baseline_names = {
            item.school_name for item in baseline.report.recommendations
        }
        self.assertTrue(any("上海" in name for name in baseline_names))
        for result, code in (
            (q16, "SCHOOL_EXCLUDED_REGION"),
            (q20, "SCHOOL_AFFORDABILITY_BLOCKED"),
        ):
            self.assertFalse(
                any("上海" in item.school_name for item in result.report.recommendations)
            )
            excluded = {
                reason.code
                for decision in result.report.school_decisions
                if decision.outcome == "excluded" and "上海" in decision.school_name
                for reason in decision.reasons
            }
            self.assertIn(code, excluded)
        for result in (baseline, q14, q16, q20):
            self.assertGreaterEqual(
                {receipt.kind for receipt in result.task_receipts},
                {
                    "province_policy",
                    "enrollment_plan",
                    "admission_charter",
                    "tuition_fee",
                    "subject_requirement",
                },
            )
            enrollment_sources = school_fit_source_ids(result, "enrollment_plan")
            self.assertTrue(enrollment_sources)
            self.assertTrue(enrollment_sources.intersection(result.report.source_ids))
        q20_fee_reasons = tuple(
            reason
            for decision in q20.report.school_decisions
            if decision.outcome == "excluded" and "上海" in decision.school_name
            for reason in decision.reasons
            if reason.code == "SCHOOL_AFFORDABILITY_BLOCKED"
        )
        self.assertTrue(q20_fee_reasons)
        tuition_sources = school_fit_source_ids(q20, "tuition_fee")
        enrollment_sources = school_fit_source_ids(q20, "enrollment_plan")
        self.assertTrue(tuition_sources)
        self.assertTrue(enrollment_sources)
        self.assertFalse(tuition_sources.intersection(enrollment_sources))
        self.assertTrue(
            all(
                tuition_sources.intersection(reason.source_ids)
                and not enrollment_sources.intersection(reason.source_ids)
                for reason in q20_fee_reasons
            )
        )


if __name__ == "__main__":
    unittest.main()
