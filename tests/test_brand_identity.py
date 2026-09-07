"""Current public brand identity and release-surface contracts."""

from __future__ import annotations

import hashlib
import subprocess
import unittest
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
        self.assertIn("assets/brand/pathway-atlas-logo.png", readme)
        self.assertIn("# 多元星途 · PathwayAtlas", readme)
        self.assertIn("陪你看清选择", readme)
        self.assertIn("点亮多种升学路径，走出个性升学星途。", readme)

    def test_brand_assets_are_safe_and_parseable(self) -> None:
        data = (BRAND / "pathway-atlas-logo.png").read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(data[25], 2, "Keep the approved opaque RGB PNG")
        self.assertEqual(
            hashlib.sha256(data).hexdigest(),
            "ec14043003e517138e19b97f2d46c186af7cf6afd01bc1079358557e0429b2c7",
            "Keep the owner-approved warm book-and-paths artwork unchanged",
        )

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
