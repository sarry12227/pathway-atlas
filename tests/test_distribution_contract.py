from __future__ import annotations

import ipaddress
import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "DISTRIBUTION.md"

EXPECTED_PLATFORMS = {
    "GitHub",
    "Gitee",
    "SkillsMP",
    "skills.sh",
    "skills.homes",
    "skillhub.club",
    "SkillHub.cn",
    "SkillsCat",
    "ClawHub",
}
EXPECTED_COLUMNS = (
    "Platform",
    "Official URL",
    "Method",
    "Version/Commit",
    "Status",
    "Listing URL",
    "Last verified",
    "Notes",
)
ALLOWED_STATUSES = {"pending", "submitted", "indexed", "rejected", "unavailable"}
EMPTY_VALUE = "—"


def _table_rows(text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        raise AssertionError("distribution table is missing")
    header = tuple(cell.strip() for cell in lines[0].strip("|").split("|"))
    if header != EXPECTED_COLUMNS:
        raise AssertionError(f"unexpected distribution columns: {header!r}")
    separator = tuple(cell.strip() for cell in lines[1].strip("|").split("|"))
    if len(separator) != len(header) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
        raise AssertionError("distribution table separator is invalid")
    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
        if len(cells) != len(header):
            raise AssertionError("distribution row has the wrong number of columns")
        rows.append(dict(zip(header, cells, strict=True)))
    return rows


def _assert_public_https(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AssertionError(f"URL is not public HTTPS: {value!r}")
    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise AssertionError(f"URL host is private: {value!r}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return
    raise AssertionError(f"literal IP URL is not allowed: {value!r}")


class DistributionContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = LEDGER.read_text(encoding="utf-8")
        self.rows = _table_rows(self.text)

    def test_has_one_row_for_every_distribution_surface(self) -> None:
        platforms = [row["Platform"] for row in self.rows]
        self.assertEqual(set(platforms), EXPECTED_PLATFORMS)
        self.assertEqual(len(platforms), len(set(platforms)))

    def test_states_and_public_urls_are_fail_closed(self) -> None:
        for row in self.rows:
            with self.subTest(platform=row["Platform"]):
                self.assertIn(row["Status"], ALLOWED_STATUSES)
                _assert_public_https(row["Official URL"])
                if row["Listing URL"] != EMPTY_VALUE:
                    _assert_public_https(row["Listing URL"])
                if row["Last verified"] != EMPTY_VALUE:
                    self.assertRegex(row["Last verified"], r"^\d{4}-\d{2}-\d{2}$")

    def test_indexed_rows_require_direct_verification(self) -> None:
        for row in self.rows:
            if row["Status"] == "indexed":
                self.assertNotEqual(row["Listing URL"], EMPTY_VALUE)
                self.assertNotEqual(row["Last verified"], EMPTY_VALUE)
                self.assertNotEqual(row["Version/Commit"], EMPTY_VALUE)

    def test_ledger_contains_no_private_or_sensitive_literals(self) -> None:
        forbidden = (
            r"(?i)\b(?:ghp|github_pat|sk-[a-z]+)-[a-z0-9_-]{8,}",
            r"(?i)(?:[a-z]:\\|\\\\|/(?:users|home)/)",
            r"(?<!\d)1[3-9]\d[ .·-]?\d{4}[ .·-]?\d{4}(?!\d)",
            r"(?i)\b(?:password|secret|token)\s*[:=]\s*\S+",
        )
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, self.text))

    def test_recorded_outcomes_require_dated_explanations(self) -> None:
        for row in self.rows:
            with self.subTest(platform=row["Platform"]):
                self.assertNotEqual(row["Notes"], EMPTY_VALUE)
                if row["Status"] != "pending":
                    self.assertNotEqual(row["Last verified"], EMPTY_VALUE)
                if row["Status"] in {"submitted", "indexed"}:
                    self.assertNotEqual(row["Version/Commit"], EMPTY_VALUE)


if __name__ == "__main__":
    unittest.main()
