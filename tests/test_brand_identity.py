"""Current public brand identity and release-surface contracts."""

from __future__ import annotations

import subprocess
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"
PROMPT = (
    "> **复制给 AI：**「请使用你当前环境的 Skill 安装能力，从 GitHub "
    "`https://github.com/sarry12227/pathway-atlas` 安装 `pathway-atlas`（多元星途）；"
    "如果 GitHub 无法访问，请改用 Gitee 镜像 `https://gitee.com/sarry1/pathway-atlas`。"
    "若环境没有专用安装工具，请将仓库克隆或下载到当前 Agent 的 Skills 目录，确认根目录存在 "
    "`SKILL.md` 且其中 `name` 为 `pathway-atlas`，然后重新加载并调用它。"
    "调用后先询问我的省份、选科、分数或位次和升学意向；不要索取姓名、电话、身份证、住址或本地文件路径。"
    "请基于可验证的公开来源，先验证证据再计算，为我分析普通批冲稳保及适合的多元升学路径，"
    "并在每项建议旁标注来源、证据状态、覆盖范围和不确定性。」"
)
CURRENT_OLD_BRAND = "多元星图"
OLD_SLUG = "shengxue" + "-skill"
OLD_DIST = "shengxue" + "_skill"
HISTORICAL_PREFIXES = (
    ".scratch/",
    "docs/superpowers/plans/",
)
SELF = "tests/test_brand_identity.py"


def _tracked_text_files() -> dict[str, str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = result.stdout.decode("utf-8").split("\0")
    texts: dict[str, str] = {}
    for relative in paths:
        if not relative or relative == SELF or relative.startswith(HISTORICAL_PREFIXES):
            continue
        try:
            text = (ROOT / relative).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        texts[relative] = text.replace(
            ".scratch/" + OLD_SLUG + "-open-source/spec.md",
            ".scratch/<historical-open-source-spec>/spec.md",
        )
    return texts


class BrandIdentityTest(unittest.TestCase):
    def test_readme_starts_with_copyable_prompt(self) -> None:
        first_line = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first_line, PROMPT)

    def test_readme_uses_primary_brand_lockup(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("assets/brand/pathway-atlas-horizontal.svg", readme)
        self.assertIn("# 多元星途 · PathwayAtlas", readme)
        self.assertIn("点亮多种升学路径，走出个性升学星途。", readme)

    def test_brand_assets_are_safe_and_parseable(self) -> None:
        svg_names = (
            "pathway-atlas-mark.svg",
            "pathway-atlas-horizontal.svg",
            "pathway-atlas-monochrome.svg",
        )
        for name in svg_names:
            with self.subTest(name=name):
                data = (BRAND / name).read_bytes()
                root = ElementTree.fromstring(data)
                self.assertTrue(root.tag.endswith("svg"))
                self.assertNotIn(b"<script", data.lower())
                self.assertNotIn(CURRENT_OLD_BRAND.encode("utf-8"), data)
                external_values = [
                    value
                    for element in root.iter()
                    for value in element.attrib.values()
                    if value.lower().startswith(("http://", "https://"))
                ]
                self.assertEqual(external_values, [])
                ids = {element.attrib.get("id") for element in root.iter()}
                self.assertTrue(
                    {"path-origin", "evidence-node", "guiding-star"}.issubset(ids),
                    f"{name} must encode the progression semantics",
                )

                if name != "pathway-atlas-monochrome.svg":
                    for color in (b"#94070A", b"#14213D", b"#C9A227"):
                        self.assertIn(color, data)

        for name in ("pathway-atlas-mark.png", "pathway-atlas-horizontal.png"):
            with self.subTest(name=name):
                data = (BRAND / name).read_bytes()
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(data[25], 6, "PNG must use RGBA color type")

    def test_current_public_surfaces_have_no_legacy_identifier(self) -> None:
        findings: list[str] = []
        for relative, text in _tracked_text_files().items():
            if OLD_SLUG in text or OLD_DIST in text:
                findings.append(relative)
        self.assertEqual(findings, [])

    def test_current_public_surfaces_have_no_retired_chinese_brand(self) -> None:
        findings = [
            relative
            for relative, text in _tracked_text_files().items()
            if CURRENT_OLD_BRAND in text
        ]
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
