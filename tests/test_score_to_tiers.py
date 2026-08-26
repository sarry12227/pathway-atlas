# -*- coding: utf-8 -*-
"""PathwayAtlas 分数直达查冲稳保切片测试（标准库 unittest，零依赖）。

测试数据为小型构造 CSV（临时目录），不含真实全量数据；
全量湖北数据的对等验证走 scripts/parity_check.py。
"""
import csv
import json
import os
import sys
import tempfile
import unittest

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))

from data_loader import DataError, load_toudang, load_yifenyiduan, score_to_rank  # noqa: E402
from contracts import OrdinaryBatchPolicy, RecommendationProfile  # noqa: E402
from school_recommend import SchoolRecommendError, recommend_schools  # noqa: E402

TOUDANG_COLUMNS = ["year", "province", "school_name", "school_code", "subject_group",
                   "major_group_name", "major_group_code", "min_score", "min_rank",
                   "majors_in_group", "school_level", "school_type", "province_location",
                   "city_location", "is_inside_hubei", "remarks"]
YFD_COLUMNS = ["year", "score", "rank", "cumulative_count", "subject_group"]


POLICY = OrdinaryBatchPolicy(
    schema_version="1.0",
    policy_id="synthetic-ordinary-batch-v1",
    basis_id="synthetic-policy-basis-v1",
    search_delta_min=-8000,
    search_delta_max=6000,
    challenge_delta_lt=-2000,
    stable_delta_le=2000,
    tier_caps={"冲": 3, "稳": 4, "保": 5},
)


def td_row(school, group, min_score, min_rank, level="", city="", in_hubei=0,
           majors="", year=2024, subject="物理"):
    return {"year": year, "province": "湖北", "school_name": school,
            "school_code": "", "subject_group": subject,
            "major_group_name": group, "major_group_code": "",
            "min_score": min_score, "min_rank": min_rank,
            "majors_in_group": majors, "school_level": level,
            "school_type": "", "province_location": "", "city_location": city,
            "is_inside_hubei": in_hubei, "remarks": ""}


def yfd_row(year, score, rank, subject="物理"):
    return {"year": year, "score": score, "rank": rank,
            "cumulative_count": rank, "subject_group": subject}


def evidence_recommend(rows, rank, *, majors=(), schools=(), secondary=()):
    bounded = []
    for original in rows:
        row = dict(original)
        row.update({
            "school_province": (
                "湖北" if bool(row.get("is_inside_hubei"))
                else (row.get("province_location") or "江苏")
            ),
            "evidence_status": "official",
            "coverage_status": "official",
            "source_ids": ("synthetic-tier-source",),
            "coverage_min_rank": 1,
            "coverage_max_rank": 100000,
        })
        bounded.append(row)
    return recommend_schools(
        bounded,
        RecommendationProfile(
            rank=rank,
            target_province="湖北",
            subject_group="物理",
            secondary_subjects=secondary,
            target_major_categories=majors,
            target_schools=schools,
        ),
        POLICY,
    )


def tier_items(result, tier):
    return tuple(item for item in result.items if item.strategy == tier)


class FixtureMixin:
    """在临时目录构造小型 data/hubei/ CSV。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, "hubei"))
        self.write_csv("tou_dang", TOUDANG_COLUMNS, self.toudang_rows())
        self.write_csv("yifenyiduan", YFD_COLUMNS, self.yfd_rows())
        # 元数据解析只接受公开 v1 契约，不再回退到缺少 mode/量表的旧格式。
        with open(os.path.join(self.root, "hubei", "province.json"), "w",
                  encoding="utf-8") as f:
            json.dump({
                "province": "湖北",
                "mode": "3+1+2",
                "primary_subjects": ["物理", "历史"],
                "secondary_subjects": ["化学", "生物", "思想政治", "地理"],
                "score_scale": 750,
                "schema_version": "1.0",
                "ordinary_batch_policy": POLICY.to_dict(),
            }, f, ensure_ascii=False)

    def tearDown(self):
        self._tmp.cleanup()

    def toudang_rows(self):
        return []

    def yfd_rows(self):
        return []

    def write_csv(self, name, columns, rows):
        path = os.path.join(self.root, "hubei", f"{name}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            for r in rows:
                w.writerow(r)


class TierBoundaryTest(FixtureMixin, unittest.TestCase):
    """Δ 分档边界：−2000/+2000 恰好落稳档。"""

    def toudang_rows(self):
        return [
            td_row("边界冲校", "第01组", 650, 7999),    # Δ=-2001 → 冲
            td_row("边界稳校A", "第01组", 640, 8000),   # Δ=-2000 → 稳
            td_row("边界稳校B", "第01组", 630, 12000),  # Δ=+2000 → 稳
            td_row("边界保校", "第01组", 620, 12001),   # Δ=+2001 → 保
        ]

    def test_boundary_tiers(self):
        year, rows = load_toudang("湖北", "物理", root=self.root)
        out = evidence_recommend(rows, 10000)
        self.assertEqual([e.school_name for e in tier_items(out, "冲")],
                         ["边界冲校"])
        self.assertEqual([e.school_name for e in tier_items(out, "稳")],
                         ["边界稳校A", "边界稳校B"])
        self.assertEqual([e.school_name for e in tier_items(out, "保")],
                         ["边界保校"])


class IntentAndLevelSortTest(FixtureMixin, unittest.TestCase):
    """档内排序：意向院校优先 > 院校层次(985>211) > 省内 > 最低位次。"""

    def toudang_rows(self):
        return [
            td_row("普通985", "第01组", 650, 9000, level="985"),
            td_row("意向双非", "第01组", 640, 9500, level=""),
            td_row("普通211", "第01组", 645, 9200, level="211"),
        ]

    def test_intent_first_then_level(self):
        year, rows = load_toudang("湖北", "物理", root=self.root)
        out = evidence_recommend(rows, 10000, schools=("意向双非",))
        names = [e.school_name for e in tier_items(out, "稳")]
        self.assertEqual(names, ["意向双非", "普通985", "普通211"])
        entry = tier_items(out, "稳")[0]
        self.assertEqual(entry.recommend_level, "★★★")
        self.assertIn("用户意向院校", entry.match_reason)


class MultiGroupDedupTest(FixtureMixin, unittest.TestCase):
    """同院校多专业组去重：最低分组为代表定档，含目标专业组一并展示。"""

    def toudang_rows(self):
        return [
            td_row("多组大学", "第01组", 600, 9500, majors='["英语", "日语"]'),
            td_row("多组大学", "第02组", 620, 9000,
                   majors='["计算机科学与技术", "软件工程"]'),
            td_row("多组大学", "第03组", 610, 9300, majors='["历史学"]'),
        ]

    def test_rep_is_lowest_score_group_and_matched_group_shown(self):
        year, rows = load_toudang("湖北", "物理", root=self.root)
        out = evidence_recommend(rows, 10000, majors=("计算机",))
        recs = tier_items(out, "稳")
        self.assertEqual(len(recs), 1)
        e = recs[0]
        # 代表组 = 最低分组（第01组，min_score 600），定档 Δ 按代表组位次
        self.assertEqual(e.min_score, 600)
        self.assertEqual(e.min_rank, 9500)
        self.assertEqual(e.delta, -500)
        # 展示 = 代表组 + 含目标专业组（第02组），不含第03组
        groups = [g.major_group_name for g in e.major_groups]
        self.assertEqual(groups, ["第01组", "第02组"])
        self.assertEqual(e.recommend_level, "★★★")
        self.assertIn("计算机", e.match_reason)


class EmptyTierTest(FixtureMixin, unittest.TestCase):
    """某档无符合条件院校 → 留空不硬凑。"""

    def toudang_rows(self):
        return [td_row("仅冲校", "第01组", 650, 5000)]  # Δ=-5000，只有冲

    def test_empty_tiers_stay_empty(self):
        year, rows = load_toudang("湖北", "物理", root=self.root)
        out = evidence_recommend(rows, 10000)
        self.assertEqual(len(tier_items(out, "冲")), 1)
        self.assertEqual(tier_items(out, "稳"), ())
        self.assertEqual(tier_items(out, "保"), ())


class ExtremeRankTest(FixtureMixin, unittest.TestCase):
    """极端位次：搜索区间外无结果不报错；非法位次明确报错。"""

    def toudang_rows(self):
        return [td_row("普通校", "第01组", 600, 10000)]

    def test_rank_outside_search_window(self):
        year, rows = load_toudang("湖北", "物理", root=self.root)
        # ref=20000：搜索区间 [12000, 26000]，院校 min_rank=10000 在区间外
        out = evidence_recommend(rows, 20000)
        self.assertEqual(out.items, ())

    def test_invalid_rank_raises(self):
        year, rows = load_toudang("湖北", "物理", root=self.root)
        with self.assertRaises(SchoolRecommendError) as ctx:
            recommend_schools(rows, {"rank": 0, "target_province": "湖北"}, POLICY)
        self.assertEqual(ctx.exception.code, "REC_001")


class TierCapTest(FixtureMixin, unittest.TestCase):
    """展示上限冲3/稳4/保5，截断保留档内排序最前者。"""

    def toudang_rows(self):
        return [td_row(f"保校{i:02d}", "第01组", 500 - i, 12100 + i * 10,
                       level="211") for i in range(8)]

    def test_cap_5(self):
        year, rows = load_toudang("湖北", "物理", root=self.root)
        out = evidence_recommend(rows, 10000)
        bao = tier_items(out, "保")
        self.assertEqual(len(bao), 5)
        # 同层次按最低位次升序，截断后保留前 5
        self.assertEqual([e.min_rank for e in bao],
                         [12100, 12110, 12120, 12130, 12140])


class ScoreToRankTest(FixtureMixin, unittest.TestCase):
    """分数→省排反查：精确命中；缺档取相邻低分累计；超出范围明确报错。"""

    def yfd_rows(self):
        return [
            yfd_row(2024, 605, 9000),
            yfd_row(2024, 600, 10000),
            yfd_row(2024, 599, 10200),
        ]

    def test_exact_score(self):
        info = score_to_rank("湖北", "物理", 600, root=self.root)
        self.assertEqual(info["rank"], 10000)
        self.assertEqual(info["year"], 2024)

    def test_missing_score_falls_back_to_lower(self):
        info = score_to_rank("湖北", "物理", 603, root=self.root)
        self.assertEqual(info["rank"], 10000)  # 取 600 分的累计

    def test_score_below_table_raises(self):
        with self.assertRaises(DataError):
            score_to_rank("湖北", "物理", 100, root=self.root)


class MissingDataTest(FixtureMixin, unittest.TestCase):
    """数据文件缺失/为空/缺科目组 → 明确报错指明缺哪份数据。"""

    def test_missing_toudang_file(self):
        os.remove(os.path.join(self.root, "hubei", "tou_dang.csv"))
        with self.assertRaises(DataError) as ctx:
            load_toudang("湖北", "物理", root=self.root)
        self.assertIn("tou_dang.csv", str(ctx.exception))

    def test_missing_yfd_file(self):
        os.remove(os.path.join(self.root, "hubei", "yifenyiduan.csv"))
        with self.assertRaises(DataError) as ctx:
            load_yifenyiduan("湖北", "物理", root=self.root)
        self.assertIn("yifenyiduan.csv", str(ctx.exception))

    def test_empty_file_raises(self):
        self.write_csv("tou_dang", TOUDANG_COLUMNS, [])
        with self.assertRaises(DataError) as ctx:
            load_toudang("湖北", "物理", root=self.root)
        self.assertIn("tou_dang.csv", str(ctx.exception))

    def test_missing_subject_group_raises(self):
        self.write_csv("tou_dang", TOUDANG_COLUMNS,
                       [td_row("历史校", "第01组", 600, 10000, subject="历史")])
        with self.assertRaises(DataError) as ctx:
            load_toudang("湖北", "物理", root=self.root)
        self.assertIn("物理", str(ctx.exception))

    def test_unknown_province_raises(self):
        with self.assertRaises(DataError) as ctx:
            load_toudang("火星省", "物理", root=self.root)
        self.assertIn("火星省", str(ctx.exception))


class SecondarySubjectFilterTest(FixtureMixin, unittest.TestCase):
    """票 08：再选科目过滤——不可报专业组不进推荐；缺省不过滤。"""

    def toudang_rows(self):
        rows = []
        for name, group, score, rank, req in [
                ("政治大学", "第01组", 606, 4400, "再选科目：思想政治"),
                ("特殊大学", "第01组", 605, 4500, "再选科目：地理；中外合作办学"),
                ("化学大学", "第01组", 604, 4600, "再选科目：化学"),
                ("化生大学", "第01组", 603, 4700, "再选科目：化学和生物"),
                ("不限大学", "第01组", 602, 4800, "再选科目：不限"),
                ("无注大学", "第01组", 601, 4900, ""),
                ("或选大学", "第01组", 600, 5000, "再选科目：化学或思想政治")]:
            r = td_row(name, group, score, rank)
            r["remarks"] = req
            rows.append(r)
        return rows

    def _run(self, secondary=None):
        return evidence_recommend(
            self.load_rows(), 5200, secondary=secondary or ()
        )

    def load_rows(self):
        from data_loader import load_toudang
        _, rows = load_toudang("湖北", "物理", root=self.root)
        return rows

    def test_filter_drops_uneligible_groups(self):
        # 7 行全落稳档、帽 4：过滤后政治/特殊让位给化学/化生/不限/无注
        out = self._run(["化学", "生物"])
        names = {item.school_name for item in out.items}
        self.assertIn("化学大学", names)
        self.assertIn("化生大学", names)      # 化学和生物 ⊆ 物化生
        self.assertIn("不限大学", names)      # 不限保留
        self.assertIn("无注大学", names)      # 无要求信息保留
        self.assertNotIn("政治大学", names)   # 思想政治不可报 → 过滤
        self.assertNotIn("特殊大学", names)   # 地理不可报 → 过滤
        self.assertEqual(out.excluded_by_subject_count, 2)

    def test_default_no_filter(self):
        out = self._run(None)
        names = {item.school_name for item in out.items}
        self.assertIn("政治大学", names)
        self.assertIn("特殊大学", names)
        self.assertEqual(out.excluded_by_subject_count, 0)

    def test_subject_required_parsing(self):
        from school_recommend import _subject_required
        self.assertIsNone(_subject_required(""))
        self.assertEqual(_subject_required("再选科目：不限"), [])
        self.assertEqual(_subject_required("再选科目：化学"), [{"化学"}])
        self.assertEqual(_subject_required("再选科目：化学和生物"), [{"化学", "生物"}])
        self.assertEqual(_subject_required("再选科目：化学或思想政治"),
                         [{"化学"}, {"思想政治"}])
        # "或"备选语义：物化生满足化学分支
        req = _subject_required("再选科目：化学或思想政治")
        have = {"化学", "生物"}
        self.assertTrue(any(alt <= have for alt in req))


if __name__ == "__main__":
    unittest.main()
