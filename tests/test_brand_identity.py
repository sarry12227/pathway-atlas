"""Current public brand identity and release-surface contracts."""

from __future__ import annotations

import subprocess
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"
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
        lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "一句话让AI调用此skill：")
        self.assertEqual(lines[2], "```bash")
        self.assertIn("github.com/sarry12227/pathway-atlas", lines[3])
        self.assertIn("gitee.com/sarry1/pathway-atlas", lines[3])
        self.assertEqual(lines[4], "```")

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
