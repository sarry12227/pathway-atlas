from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from scripts.source_health_state import (
    HealthObservation,
    HealthStateEntry,
    state_from_payload,
    state_to_payload,
    transition_source_health,
)


class SourceHealthStateTest(unittest.TestCase):
    def observation(self, status: str, province: str = "湖北") -> HealthObservation:
        return HealthObservation(province=province, status=status)

    def test_second_consecutive_unavailable_alerts_and_recovery_restarts_count(self) -> None:
        first = transition_source_health(
            (),
            (self.observation("unavailable"), self.observation("unavailable")),
        )
        self.assertEqual(first.state, (HealthStateEntry("湖北", "unavailable", 1),))
        self.assertEqual(first.review, ())

        second = transition_source_health(first.state, (self.observation("unavailable"),))
        self.assertEqual(second.state, (HealthStateEntry("湖北", "unavailable", 2),))
        self.assertEqual(second.review, (self.observation("unavailable"),))

        repeated = transition_source_health(second.state, (self.observation("unavailable"),))
        self.assertEqual(repeated.state, (HealthStateEntry("湖北", "unavailable", 2),))
        self.assertEqual(repeated.review, (self.observation("unavailable"),))

        recovered = transition_source_health(repeated.state, (self.observation("healthy"),))
        self.assertEqual(recovered.state, ())
        self.assertEqual(recovered.review, ())
        restarted = transition_source_health(recovered.state, (self.observation("unavailable"),))
        self.assertEqual(restarted.state, (HealthStateEntry("湖北", "unavailable", 1),))
        self.assertEqual(restarted.review, ())

    def test_redirect_alerts_immediately_and_next_unavailable_starts_at_one(self) -> None:
        prior = (HealthStateEntry("湖北", "unavailable", 1),)
        redirected = transition_source_health(prior, (self.observation("redirect_review"),))
        self.assertEqual(redirected.state, ())
        self.assertEqual(redirected.review, (self.observation("redirect_review"),))

        after_redirect = transition_source_health(
            redirected.state,
            (self.observation("unavailable"),),
        )
        self.assertEqual(after_redirect.state, (HealthStateEntry("湖北", "unavailable", 1),))
        self.assertEqual(after_redirect.review, ())

    def test_alias_observations_are_one_run_and_healthy_prevents_false_persistence(self) -> None:
        prior = (
            HealthStateEntry("北京", "unavailable", 1),
            HealthStateEntry("湖北", "unavailable", 1),
        )
        transition = transition_source_health(
            prior,
            (
                self.observation("unavailable", "北京"),
                self.observation("unavailable", "北京"),
                self.observation("unavailable", "湖北"),
                self.observation("healthy", "湖北"),
            ),
        )
        self.assertEqual(transition.state, (HealthStateEntry("北京", "unavailable", 2),))
        self.assertEqual(transition.review, (self.observation("unavailable", "北京"),))

    def test_redirect_precedes_other_alias_results_and_output_order_is_deterministic(self) -> None:
        transition = transition_source_health(
            (),
            (
                self.observation("unavailable", "湖北"),
                self.observation("redirect_review", "北京"),
                self.observation("healthy", "北京"),
                self.observation("unavailable", "湖北"),
            ),
        )
        self.assertEqual(transition.state, (HealthStateEntry("湖北", "unavailable", 1),))
        self.assertEqual(transition.review, (self.observation("redirect_review", "北京"),))

    def test_cached_state_payload_is_minimal_strict_and_immutable(self) -> None:
        payload = [
            {"province": "北京", "status": "unavailable", "count": 2},
            {"province": "湖北", "status": "unavailable", "count": 1},
        ]
        state = state_from_payload(payload)
        self.assertEqual(state_to_payload(state), payload)
        with self.assertRaises(FrozenInstanceError):
            state[0].count = 1  # type: ignore[misc]

        invalid_payloads = (
            {"records": payload},
            [{"province": "湖北", "status": "unavailable", "count": 1, "url": "forbidden"}],
            [{"province": "湖北/学生", "status": "unavailable", "count": 1}],
            [{"province": "湖北", "status": "redirect_review", "count": 1}],
            [{"province": "湖北", "status": "unavailable", "count": 3}],
            [payload[1], payload[1]],
        )
        for invalid in invalid_payloads:
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                state_from_payload(invalid)

    def test_observation_and_state_constructors_reject_unsafe_or_unknown_values(self) -> None:
        invalid_observations = (
            ("湖北/学生", "healthy"),
            ("湖北", "unknown"),
            ("", "unavailable"),
        )
        for province, status in invalid_observations:
            with self.subTest(province=province, status=status), self.assertRaises(ValueError):
                HealthObservation(province, status)
        with self.assertRaises(ValueError):
            HealthStateEntry("湖北", "unavailable", 0)


if __name__ == "__main__":
    unittest.main()
