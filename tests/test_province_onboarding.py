# -*- coding: utf-8 -*-
"""票 06 多省份接入测试：配置覆盖/缺省回退、demo-xx 端到端、validate_data。"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(os.path.dirname(TESTS_DIR), "scripts")
SKILL_ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from school_recommend import params_from_config  # noqa: E402
from path_recommend import equiv_adjust_from_config  # noqa: E402
from verify_province import verify  # noqa: E402


def run_script(path, *args, cwd=SKILL_ROOT):
    return subprocess.run([sys.executable, path, *args], capture_output=True,
                          text=True, encoding="utf-8", cwd=cwd)


class ConfigParamsTest(unittest.TestCase):
    """省份配置 → 推荐参数：覆盖生效、缺省回退湖北默认。"""

    def test_empty_config_falls_back_to_hubei_defaults(self):
        p = params_from_config({})
        self.assertEqual((p["chong_lt"], p["wen_le"]), (-2000, 2000))
        self.assertEqual((p["delta_lo"], p["delta_hi"]), (-8000, 6000))
        self.assertEqual(p["tier_caps"], {"冲": 3, "稳": 4, "保": 5})
        self.assertEqual(equiv_adjust_from_config({}), 4000)

    def test_override_takes_effect(self):
        config = {"tier_thresholds": {"chong_lt": -3000, "wen_le": 1000},
                  "delta_range": [-5000, 4000],
                  "tier_caps": {"冲": 2},
                  "equiv_rank_adjust": 2000}
        p = params_from_config(config)
        self.assertEqual((p["chong_lt"], p["wen_le"]), (-3000, 1000))
        self.assertEqual((p["delta_lo"], p["delta_hi"]), (-5000, 4000))
        # 部分覆盖：未指定的档保留默认
        self.assertEqual(p["tier_caps"], {"冲": 2, "稳": 4, "保": 5})
        self.assertEqual(equiv_adjust_from_config(config), 2000)


class VerifyProvinceTest(unittest.TestCase):
    """端到端自查：随包 demo-xx / 湖北全部 ✅（AC8）。"""

    def test_demo_xx_passes(self):
        self.assertTrue(verify("XX省"))

    def test_hubei_passes(self):
        self.assertTrue(verify("湖北"))

    def test_unknown_province_fails_cleanly(self):
        self.assertFalse(verify(" Atlantis "))


class ValidateDataTest(unittest.TestCase):
    """tools/validate_data.py：合法通过、逐行报错（真实行号）。"""

    TOOL = os.path.join(SKILL_ROOT, "scripts", "validate_data.py")

    def _metadata(self, pdir):
        with open(os.path.join(pdir, "province.json"), "w", encoding="utf-8") as f:
            json.dump({
                "province": "测试省",
                "mode": "3+1+2",
                "primary_subjects": ["物理", "历史"],
                "secondary_subjects": ["化学", "生物", "思想政治", "地理"],
                "score_scale": 750,
                "schema_version": "1.0",
            }, f, ensure_ascii=False)

    def _write(self, pdir, name, header, rows):
        with open(os.path.join(pdir, name), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    def test_valid_package_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._metadata(tmp)
            self._write(tmp, "yifenyiduan.csv",
                        ["year", "score", "rank", "cumulative_count", "subject_group"],
                        [[2025, 600, 1000, 1000, "物理"]])
            p = run_script(self.TOOL, "--province-dir", tmp)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_row_errors_reported_with_real_line_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._metadata(tmp)
            self._write(tmp, "yifenyiduan.csv",
                        ["year", "score", "rank", "cumulative_count", "subject_group"],
                        [[2025, 600, 1000, 1000, "物理"],      # 行2 合法
                         [2025, 999, 1000, 1000, "物理"],      # 行3 分数超界
                         [2025, 590, 0, 0, "综评"],           # 行4 位次<1 + 非法枚举
                         [2025, 600, 1200, 1200, "物理"]])    # 行5 唯一键重复
            p = run_script(self.TOOL, "--province-dir", tmp)
            self.assertEqual(p.returncode, 2)
            self.assertIn("行3", p.stdout)
            self.assertIn("行4", p.stdout)
            self.assertIn("行5", p.stdout)
            self.assertIn("唯一键重复", p.stdout)

    def test_missing_required_column_is_file_level_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._metadata(tmp)
            self._write(tmp, "yifenyiduan.csv", ["year", "score"], [[2025, 600]])
            p = run_script(self.TOOL, "--province-dir", tmp)
            self.assertEqual(p.returncode, 2)
            self.assertIn("文件级错误", p.stdout)


if __name__ == "__main__":
    unittest.main()
