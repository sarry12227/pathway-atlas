"""Natural-language questionnaire boundary and no-fabrication profile tests."""

from __future__ import annotations

from copy import deepcopy
import unittest

from scripts.planning_profile import PlanningMode
from scripts.questionnaire_intake import (
    QuestionnaireIntakeError,
    build_profile_from_questionnaire,
    parse_numbered_questionnaire,
)


def transcript() -> str:
    return "\n".join(
        (
            "1. 不便回答",
            "2. 湖北",
            "3. 武汉，武汉市示例中学",
            "4. 高二，预计 2028 年高考",
            "5. 重点班",
            "6. 历史+政治+地理，原始分",
            "7. 2026-06-01 市级联考，610/750",
            "8. 年级第120名，同口径1000人",
            "9. 最好80名，通常140名左右",
            "10. 合成学科奖项",
            "11. 合成志愿活动；没有研究经历",
            "12. 示例甲大学",
            "13. 专业实力强",
            "14. 历史学",
            "15. 自己感兴趣",
            "16. 优先武汉，无不能接受地区",
            "17. 倾向继续深造",
            "18. 担心院校定位和多元路径",
            "19. 希望得到院校范围、多元路径和当前行动",
            "20. 公办优先；预算适中；服务期不接受；调剂可考虑；风险平衡；强基和综评有兴趣；其余不确定",
        )
    )


def structured_answers() -> dict[int, object]:
    return {
        1: "不便回答",
        2: "湖北",
        3: {"city": "武汉", "high_school": "武汉市示例中学"},
        4: {"grade": "高二", "exam_year": 2028},
        5: "重点班",
        6: {
            "mode": "3+1+2",
            "primary": "历史",
            "secondary": ["政治", "地理"],
            "score_basis": "原始分",
        },
        7: {
            "date": "2026-06-01",
            "scope": "school",
            "score": 610,
            "max_score": 750,
            "source": "school_report",
        },
        8: {"rank": 120, "cohort_size": 1000},
        9: {"best_rank": 80, "usual_rank": 140},
        10: ["合成学科奖项"],
        11: ["合成志愿活动"],
        12: ["示例甲大学"],
        13: ["专业实力强"],
        14: ["历史学"],
        15: ["自己感兴趣"],
        16: {"targets": ["武汉"], "excluded": []},
        17: "postgraduate",
        18: ["院校定位", "多元路径"],
        19: ["院校范围", "多元路径", "当前行动"],
        20: {
            "budget": "moderate",
            "institution_types": ["public"],
            "service": "reject",
            "adjustment": "consider",
            "risk": "balanced",
            "health": [],
            "school_vs_major": "balanced",
            "pathways": {
                "strong_foundation": "interested",
                "comprehensive_evaluation": "interested",
                "special_program": "unknown",
                "service_oriented": "unknown",
                "uniformed_service": "not_applicable",
                "cross_border": "unknown",
                "arts_sports": "not_applicable",
            },
            "eligibility": ["完成高考报名"],
        },
    }


class QuestionnaireIntakeTest(unittest.TestCase):
    def test_numbered_natural_language_transcript_is_the_public_starting_boundary(self):
        answers = parse_numbered_questionnaire(transcript())

        self.assertEqual(tuple(answers), tuple(range(1, 21)))
        self.assertEqual(answers[2], "湖北")
        self.assertIn("其余不确定", answers[20])

    def test_structured_host_normalization_never_fabricates_unasked_readiness(self):
        source = structured_answers()
        before = deepcopy(source)

        profile = build_profile_from_questionnaire(source)

        self.assertEqual(source, before)
        self.assertEqual(profile.mode, PlanningMode.REFERENCE)
        self.assertEqual(profile.rank_observations[0].source, "school_report")
        self.assertEqual(profile.preparation_assets.subject_strengths, ())
        self.assertEqual(profile.preparation_assets.research_experiences, ())
        self.assertEqual(profile.preparation_assets.activities, ("合成志愿活动",))
        self.assertEqual(profile.preparation_assets.english_readiness, "unknown")
        self.assertEqual(profile.preparation_assets.interview_readiness, "unknown")
        self.assertEqual(profile.preparation_assets.physical_readiness, "unknown")

    def test_question_eight_keeps_school_and_joint_exam_ranks_as_distinct_observations(self):
        answers = structured_answers()
        answers[8] = {
            "rank": 120,
            "cohort_size": 1000,
            "additional_observations": [
                {
                    "scope": "province_joint",
                    "rank": 18200,
                    "cohort_size": 210000,
                    "source": "joint_exam_report",
                }
            ],
        }

        profile = build_profile_from_questionnaire(answers)

        self.assertEqual(
            tuple(
                (
                    item.scope,
                    item.rank,
                    item.cohort_size,
                    item.source,
                    item.score,
                    item.exam_date,
                )
                for item in profile.rank_observations
            ),
            (
                ("school", 120, 1000, "school_report", 610, "2026-06-01"),
                (
                    "province_joint",
                    18200,
                    210000,
                    "joint_exam_report",
                    610,
                    "2026-06-01",
                ),
            ),
        )

    def test_missing_duplicate_or_extra_questions_fail_before_profile_creation(self):
        missing = transcript().replace("19. 希望得到院校范围、多元路径和当前行动\n", "")
        duplicate = transcript() + "\n20. 重复答案"
        with self.assertRaises(QuestionnaireIntakeError):
            parse_numbered_questionnaire(missing)
        with self.assertRaises(QuestionnaireIntakeError):
            parse_numbered_questionnaire(duplicate)

        answers = structured_answers()
        answers.pop(19)
        with self.assertRaises(QuestionnaireIntakeError):
            build_profile_from_questionnaire(answers)


if __name__ == "__main__":
    unittest.main()
