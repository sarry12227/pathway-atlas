import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"
STAGES = (
    "画像确认",
    "会话初始化",
    "研究循环",
    "证据最终化",
    "计算发布",
    "恢复与降级",
)
QUESTION_LABELS = (
    "性别",
    "高考报名省份",
    "当前城市与所在高中",
    "年级与预计高考年份",
    "班型/培养层次",
    "选科组合",
    "最近一次大考总分",
    "最近一次大考校排名（年级排名）",
    "过往最高与正常水平校排",
    "获奖经历",
    "特殊活动经历",
    "理想大学",
    "为什么想去这些学校",
    "想学什么专业（大类）",
    "为什么想学这个专业",
    "想去哪个城市或地区读大学",
    "对大学毕业后的想象",
    "目前最焦虑或最担心",
    "最希望规划方案解决什么问题",
    "规划条件、限制与特殊升学方向",
)


def parse_skill():
    raw = SKILL.read_bytes()
    text = raw.decode("utf-8", errors="strict")
    assert text.encode("utf-8") == raw
    match = re.fullmatch(r"---\n(?P<frontmatter>.*?)\n---\n(?P<body>.*)", text, re.S)
    if match is None:
        raise AssertionError("one YAML frontmatter block is required")
    frontmatter = {}
    for line in match.group("frontmatter").splitlines():
        key, separator, value = line.partition(":")
        if not separator or key in frontmatter:
            raise AssertionError("simple unique frontmatter fields are required")
        frontmatter[key] = value.strip()
    return frontmatter, match.group("body")


def section(body, heading):
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n(?P<value>.*?)(?=^## |\Z)",
        body,
        re.M | re.S,
    )
    if match is None:
        raise AssertionError(f"missing stage: {heading}")
    return match.group("value")


def questions(intake):
    return tuple(
        (int(number), label.strip())
        for number, label in re.findall(
            r"^([0-9]+)\.\s+\*\*([^*]+)\*\*", intake, re.M
        )
    )


class SkillContractTest(unittest.TestCase):
    def setUp(self):
        self.frontmatter, self.body = parse_skill()

    def test_frontmatter_implicitly_routes_real_parent_questions(self):
        self.assertEqual(set(self.frontmatter), {"name", "description"})
        self.assertEqual(self.frontmatter["name"], "pathway-atlas")
        description = self.frontmatter["description"]
        self.assertTrue(description.startswith("Use when "))
        self.assertLessEqual(len(description), 500)
        for trigger in (
            "这个分数能上哪个学校",
            "位次",
            "冲稳保",
            "升学路径",
            "强基怎么走",
            "综评怎么走",
            "选校",
            "选专业",
        ):
            self.assertIn(trigger, description)
        self.assertNotIn("pathway-atlas", description.casefold())
        for workflow_word in ("preflight", "QueryPlan", "JSON", "DOCX"):
            self.assertNotIn(workflow_word, description)

    def test_body_is_one_six_stage_state_machine_runbook(self):
        headings = tuple(re.findall(r"^## (.+)$", self.body, re.M))
        self.assertEqual(headings, STAGES)
        self.assertLessEqual(len(self.body.splitlines()), 220)
        self.assertEqual(self.body.count("scripts/planning_session.py"), 1)
        for command in ("init", "confirm", "next", "ingest", "finalize", "compute", "status"):
            self.assertIn(f"`{command}`", self.body)

    def test_internal_bank_preserves_twenty_anonymous_topics(self):
        intake = section(self.body, "画像确认")
        rows = questions((ROOT / "references/questionnaire.md").read_text(encoding="utf-8"))
        self.assertEqual(tuple(number for number, _ in rows), tuple(range(1, 21)))
        self.assertEqual(tuple(label for _, label in rows), QUESTION_LABELS)
        self.assertEqual(rows[4], (5, "班型/培养层次"))
        self.assertNotIn("学生姓名", tuple(label for _, label in rows))
        self.assertIn("拒绝收集学生姓名", intake)
        for prohibited in ("电话", "地址", "具体班级编号", "通信 ID", "凭证", "本地路径"):
            self.assertIn(prohibited, intake)
        self.assertIn("高中完整校名", intake)

    def test_first_response_prefills_then_waits_for_explicit_confirmation(self):
        intake = section(self.body, "画像确认")
        self.assertIn("[内部题库](references/questionnaire.md)", intake)
        self.assertEqual(questions(intake), ())
        self.assertIn("不得重复询问已提供的信息", intake)
        self.assertIn("画像确认前不得运行 preflight、查询计划或检索，也不得计算、推荐或判断", intake)
        self.assertIn("确认后的匿名画像", intake)
        self.assertIn("唯一完整上下文", intake)
        self.assertIn("parse_numbered_questionnaire", intake)
        self.assertIn("build_profile_from_questionnaire", intake)
        self.assertIn("additional_observations", intake)
        self.assertIn("city_joint", intake)
        self.assertIn("province_joint", intake)
        self.assertIn("不得从选科、活动或“没有限制”补造", intake)

    def test_user_never_authors_internal_state_or_paths(self):
        for phrase in (
            "用户只回答自然语言问题",
            "不得要求用户创建 JSON",
            "不得让用户提供内部 JSON、本地路径或文件路径",
        ):
            self.assertIn(phrase, self.body)
        self.assertIn("宿主内部", self.body)
        self.assertIn("v3 `PlanningProfile`", self.body)
        self.assertIn("canonical QueryPlan", self.body)
        self.assertIn("fresh evidence bundle", self.body)

    def test_research_loop_opens_sources_and_tracks_four_year_fallback(self):
        research = section(self.body, "研究循环")
        self.assertLess(research.index("`next`"), research.index("`ingest`"))
        self.assertLess(research.index("`ingest`"), research.index("`next` 循环"))
        self.assertIn("必须打开原网页或附件", research)
        self.assertIn("不能把搜索摘要当事实", research)
        self.assertIn("Y → Y-1 → Y-2 → Y-3", research)
        self.assertIn("每种数据类型独立", research)
        self.assertIn("当前年度只有第三方资料而上一年度有官方资料时同时保留", research)
        self.assertIn("当年参考", research)
        self.assertIn("历史基线", research)
        for family in ("一分一段表", "投档位次", "招生计划", "招生章程", "学费", "选科要求", "多元路径政策", "服务期"):
            self.assertIn(family, research)
        self.assertIn("虚构 `province.json`", research)

    def test_completed_results_cross_factory_outcomes_not_bare_digests(self):
        research = section(self.body, "研究循环")
        evidence = section(self.body, "证据最终化")
        report = section(self.body, "计算发布")

        self.assertIn("build_task_evidence_outcome", research)
        self.assertIn("evidence_outcome=", research)
        self.assertIn("build_evidence_manifest_outcome", evidence)
        self.assertIn("build_calculation_outcome", report)
        self.assertIn("build_report_publication_outcome", report)
        for stage in (research, evidence, report):
            self.assertIn("裸 digest", stage)
        self.assertIn("同一宿主进程", self.body)

    def test_evidence_tiers_degrade_without_abandoning_a_decision(self):
        evidence = section(self.body, "证据最终化")
        for status in ("official", "corroborated", "reference", "partial", "conflict", "missing"):
            self.assertIn(f"`{status}`", evidence)
        self.assertIn("两个独立 B", evidence)
        self.assertIn("三个独立 C", evidence)
        self.assertIn("冲突不得取平均", evidence)
        self.assertIn("仍继续检索 B/C", evidence)

    def test_report_makes_school_pathway_and_action_decisions(self):
        report = section(self.body, "计算发布")
        for phrase in (
            "冲、稳、保、观察",
            "典型学校",
            "主攻、重点准备、备选、观察、不建议",
            "当前最需要做的事",
            "按时间与价值排序",
            "来源、证据状态、覆盖范围和不确定性",
            "本结果由 AI 基于公开数据整理",
            "不构成录取承诺或正式升学建议",
        ):
            self.assertIn(phrase, report)

    def test_resume_and_offline_paths_remain_useful_and_path_neutral(self):
        recovery = section(self.body, "恢复与降级")
        self.assertIn("从最后一个有效快照继续", recovery)
        self.assertIn("partial 版本", recovery)
        self.assertIn("位次区间", recovery)
        self.assertIn("典型学校和路径", recovery)
        self.assertIn("受控 degradation 或 unavailable reason", recovery)
        self.assertIn("内部路径", recovery)

    def test_all_references_are_reachable_once(self):
        links = tuple(re.findall(r"\[[^]]+\]\(([^)]+)\)", self.body))
        expected = {
            "references/questionnaire.md",
            "references/source-policy.md",
            "references/retrieval-playbook.md",
            "references/hosts/generic.md",
            "references/hosts/codex.md",
            "references/hosts/claude-code.md",
            "references/hosts/kimi.md",
        }
        self.assertEqual(set(links), expected)
        self.assertEqual(len(links), len(set(links)))
        for link in links:
            self.assertTrue((ROOT / link).is_file(), link)


if __name__ == "__main__":
    unittest.main()
