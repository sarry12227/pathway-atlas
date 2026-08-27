from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
import unittest

from scripts.year_fallback import YearSelection, select_latest_comparable, year_window


def record(
    record_id: str,
    year: int,
    *,
    regime_id: str = "current",
    comparable: bool = True,
    evidence_status: str = "official",
) -> dict:
    return {
        "record_id": record_id,
        "year": year,
        "regime_id": regime_id,
        "comparable": comparable,
        "evidence_status": evidence_status,
    }


class YearFallbackTest(unittest.TestCase):
    def test_window_is_exactly_target_through_target_minus_three(self):
        self.assertEqual(year_window(2028), (2028, 2027, 2026, 2025))
        self.assertEqual(year_window(2028.0), (2028, 2027, 2026, 2025))
        for bad in (True, 2028.5, float("nan"), "2028"):
            with self.subTest(bad=repr(bad)), self.assertRaises((TypeError, ValueError)):
                year_window(bad)

    def test_latest_available_year_is_selected_with_explicit_fallback_distance(self):
        selection = select_latest_comparable(
            [record("r-2026", 2026)], target_year=2028
        )
        self.assertEqual(selection.primary_year, 2026)
        self.assertEqual(selection.trend_years, (2026,))
        self.assertEqual(selection.fallback_distance, 2)
        self.assertEqual(selection.selected_record_ids, ("r-2026",))
        self.assertIn("fallback_used", selection.reason_codes)

    def test_records_older_than_three_years_do_not_become_a_false_current_basis(self):
        selection = select_latest_comparable(
            [record("old", 2024)], target_year=2028
        )
        self.assertIsNone(selection.primary_year)
        self.assertEqual(selection.trend_years, ())
        self.assertIsNone(selection.fallback_distance)
        self.assertEqual(selection.rejected_years, (2024,))
        self.assertIn("no_comparable_year", selection.reason_codes)

    def test_each_annual_data_family_uses_the_same_window_independently(self):
        latest_by_kind = {
            "score_table": 2028,
            "batch_admission": 2027,
            "enrollment_plan": 2026,
            "charter": 2025,
            "fee": 2027,
            "subject_requirement": 2028,
            "pathway_policy": 2026,
            "service_obligation": 2025,
            "rank_anchor": 2027,
        }
        for kind, available_year in latest_by_kind.items():
            with self.subTest(kind=kind):
                selection = select_latest_comparable(
                    [record(f"{kind}:{available_year}", available_year)],
                    target_year=2028,
                )
                self.assertEqual(selection.primary_year, available_year)
                self.assertEqual(selection.fallback_distance, 2028 - available_year)

    def test_trend_uses_at_most_three_distinct_comparable_years(self):
        selection = select_latest_comparable(
            [
                record("a", 2028),
                record("b", 2028, evidence_status="reference"),
                record("c", 2027),
                record("d", 2026),
                record("e", 2025),
            ],
            target_year=2028,
        )
        self.assertEqual(selection.primary_year, 2028)
        self.assertEqual(selection.trend_years, (2028, 2027, 2026))
        self.assertEqual(selection.selected_record_ids, ("a", "b", "c", "d"))
        self.assertEqual(selection.fallback_distance, 0)
        self.assertIn("current_year_selected", selection.reason_codes)

    def test_regime_breaks_are_rejected_instead_of_aggregated(self):
        selection = select_latest_comparable(
            [
                record("new-2028", 2028, regime_id="new-exam"),
                record("new-2027", 2027, regime_id="new-exam"),
                record("old-2026", 2026, regime_id="old-exam"),
            ],
            target_year=2028,
        )
        self.assertEqual(selection.primary_year, 2028)
        self.assertEqual(selection.trend_years, (2028, 2027))
        self.assertEqual(selection.selected_record_ids, ("new-2028", "new-2027"))
        self.assertEqual(selection.rejected_years, (2026,))
        self.assertIn("regime_break_rejected", selection.reason_codes)

    def test_same_latest_year_with_conflicting_regimes_fails_closed(self):
        selection = select_latest_comparable(
            [
                record("one", 2028, regime_id="a"),
                record("two", 2028, regime_id="b"),
            ],
            target_year=2028,
        )
        self.assertIsNone(selection.primary_year)
        self.assertEqual(selection.selected_record_ids, ())
        self.assertIn("latest_year_regime_conflict", selection.reason_codes)

    def test_current_reference_and_previous_official_stay_separate_and_labeled(self):
        selection = select_latest_comparable(
            [
                record("current-c", 2028, evidence_status="reference"),
                record("previous-a", 2027, evidence_status="official"),
            ],
            target_year=2028,
        )
        self.assertEqual(selection.primary_year, 2028)
        self.assertEqual(selection.selected_record_ids, ("current-c", "previous-a"))
        self.assertEqual(selection.selected_evidence_statuses, ("reference", "official"))

    def test_noncomparable_records_are_explicitly_rejected(self):
        selection = select_latest_comparable(
            [record("bad", 2028, comparable=False), record("good", 2027)],
            target_year=2028,
        )
        self.assertEqual(selection.primary_year, 2027)
        self.assertEqual(selection.rejected_years, (2028,))
        self.assertIn("noncomparable_record_rejected", selection.reason_codes)

    def test_result_is_factory_only_frozen_and_json_safe(self):
        selection = select_latest_comparable([record("a", 2028)], target_year=2028)
        with self.assertRaises(TypeError):
            YearSelection()
        with self.assertRaises(TypeError):
            replace(selection, primary_year=2027)
        with self.assertRaises(FrozenInstanceError):
            selection.primary_year = 2027
        json.dumps(selection.to_dict(), ensure_ascii=False, allow_nan=False)

    def test_unknown_record_fields_duplicate_ids_and_invalid_limits_fail_closed(self):
        unknown = record("a", 2028)
        unknown["weight"] = 0.9
        with self.assertRaises(ValueError):
            select_latest_comparable([unknown], target_year=2028)
        with self.assertRaises(ValueError):
            select_latest_comparable(
                [record("same", 2028), record("same", 2027)], target_year=2028
            )
        for bad in (0, 4, True, 2.5):
            with self.subTest(bad=bad), self.assertRaises((TypeError, ValueError)):
                select_latest_comparable(
                    [record("a", 2028)],
                    target_year=2028,
                    maximum_trend_years=bad,
                )


if __name__ == "__main__":
    unittest.main()
