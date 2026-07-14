"""Tests for backend.guardrails: scenario allowlist, rate limiting, budget tracking.

Time is controlled by monkeypatching `time.monotonic` (guardrails.py does
`import time` and calls `time.monotonic()`), never by real sleeping.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.guardrails as guardrails


class FakeMonotonic:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def fake_time(monkeypatch: pytest.MonkeyPatch) -> FakeMonotonic:
    clock = FakeMonotonic()
    monkeypatch.setattr(guardrails.time, "monotonic", clock)
    return clock


class TestScenarioAllowlist:
    def test_known_scenario_ids_are_valid(self) -> None:
        for scenario_id in guardrails.ALLOWED_SCENARIO_IDS:
            assert guardrails.is_valid_scenario_id(scenario_id)

    def test_unknown_scenario_id_rejected(self) -> None:
        assert not guardrails.is_valid_scenario_id("not-a-real-scenario")

    def test_case_sensitive(self) -> None:
        assert not guardrails.is_valid_scenario_id("Checkout-Deploy")


class TestRateLimiter:
    def test_allows_up_to_short_window_max(self, fake_time: FakeMonotonic) -> None:
        limiter = guardrails.RateLimiter()

        for _ in range(guardrails.RateLimiter.SHORT_WINDOW_MAX):
            result = limiter.check("1.2.3.4")
            assert result.allowed

    def test_denies_after_short_window_max(self, fake_time: FakeMonotonic) -> None:
        limiter = guardrails.RateLimiter()

        for _ in range(guardrails.RateLimiter.SHORT_WINDOW_MAX):
            limiter.check("1.2.3.4")

        result = limiter.check("1.2.3.4")

        assert not result.allowed
        assert result.retry_after_seconds > 0
        assert "10 minutes" in result.reason

    def test_short_window_resets_after_window_elapses(self, fake_time: FakeMonotonic) -> None:
        limiter = guardrails.RateLimiter()

        for _ in range(guardrails.RateLimiter.SHORT_WINDOW_MAX):
            limiter.check("1.2.3.4")
        assert not limiter.check("1.2.3.4").allowed

        fake_time.advance(guardrails.RateLimiter.SHORT_WINDOW_SECONDS + 1)

        assert limiter.check("1.2.3.4").allowed

    def test_denies_after_long_window_max_even_when_spaced_out(
        self, fake_time: FakeMonotonic
    ) -> None:
        """20 requests spaced far enough apart to never trip the short-window
        limiter individually should still trip the 24h ceiling."""
        limiter = guardrails.RateLimiter()
        gap = 700  # > SHORT_WINDOW_SECONDS (600), so short window never fills

        for _ in range(guardrails.RateLimiter.LONG_WINDOW_MAX):
            fake_time.advance(gap)
            result = limiter.check("5.5.5.5")
            assert result.allowed

        result = limiter.check("5.5.5.5")

        assert not result.allowed
        assert "24 hours" in result.reason

    def test_different_ips_tracked_independently(self, fake_time: FakeMonotonic) -> None:
        limiter = guardrails.RateLimiter()

        for _ in range(guardrails.RateLimiter.SHORT_WINDOW_MAX):
            limiter.check("1.1.1.1")
        assert not limiter.check("1.1.1.1").allowed

        assert limiter.check("2.2.2.2").allowed

    def test_exempt_ips_always_allowed(self, fake_time: FakeMonotonic) -> None:
        limiter = guardrails.RateLimiter(exempt_ips=frozenset({"9.9.9.9"}))

        for _ in range(guardrails.RateLimiter.LONG_WINDOW_MAX + 10):
            assert limiter.check("9.9.9.9").allowed

    def test_entries_older_than_long_window_are_pruned(self, fake_time: FakeMonotonic) -> None:
        limiter = guardrails.RateLimiter()

        for _ in range(guardrails.RateLimiter.SHORT_WINDOW_MAX):
            limiter.check("1.2.3.4")
        assert not limiter.check("1.2.3.4").allowed

        fake_time.advance(guardrails.RateLimiter.LONG_WINDOW_SECONDS + 1)

        # All prior history is now outside the 24h window and gets pruned,
        # so this IP should have a full quota again.
        for _ in range(guardrails.RateLimiter.SHORT_WINDOW_MAX):
            assert limiter.check("1.2.3.4").allowed

    def test_retry_after_seconds_matches_remaining_short_window(
        self, fake_time: FakeMonotonic
    ) -> None:
        limiter = guardrails.RateLimiter()

        for _ in range(guardrails.RateLimiter.SHORT_WINDOW_MAX):
            limiter.check("1.2.3.4")

        result = limiter.check("1.2.3.4")

        # All 5 requests happened at t=0, so retry_after should be
        # ~SHORT_WINDOW_SECONDS (+1 for the ceiling in the implementation).
        assert result.retry_after_seconds == guardrails.RateLimiter.SHORT_WINDOW_SECONDS + 1


class TestBudgetTracker:
    def test_record_call_computes_cost_from_pricing_constants(self, tmp_path: Path) -> None:
        tracker = guardrails.BudgetTracker(path=tmp_path / "budget.json")

        total = tracker.record_call(prompt_tokens=1000, completion_tokens=1000)

        expected = (
            1000 * guardrails._PROMPT_COST_PER_1K + 1000 * guardrails._COMPLETION_COST_PER_1K
        ) / 1000.0
        assert total == pytest.approx(expected)

    def test_cost_persists_across_tracker_instances_sharing_a_file(self, tmp_path: Path) -> None:
        path = tmp_path / "budget.json"
        tracker_a = guardrails.BudgetTracker(path=path)
        tracker_a.record_call(prompt_tokens=100_000, completion_tokens=100_000)

        tracker_b = guardrails.BudgetTracker(path=path)

        assert tracker_b.used_pct() == tracker_a.used_pct() > 0.0

    def test_corrupted_budget_file_degrades_to_zero(self, tmp_path: Path) -> None:
        path = tmp_path / "budget.json"
        path.write_text("not valid json")

        tracker = guardrails.BudgetTracker(path=path)

        assert tracker.used_pct() == 0.0

    def test_missing_budget_file_degrades_to_zero(self, tmp_path: Path) -> None:
        tracker = guardrails.BudgetTracker(path=tmp_path / "does-not-exist.json")

        assert tracker.used_pct() == 0.0

    def test_stale_month_resets_cost(self, tmp_path: Path) -> None:
        path = tmp_path / "budget.json"
        path.write_text(json.dumps({"month": "2000-01", "cost_usd": 40.0}))

        tracker = guardrails.BudgetTracker(path=path)

        assert tracker.used_pct() == 0.0

    def test_is_exhausted_at_100_percent(self, tmp_path: Path) -> None:
        path = tmp_path / "budget.json"
        tracker = guardrails.BudgetTracker(path=path)
        path.write_text(
            json.dumps(
                {"month": tracker._current_month(), "cost_usd": guardrails.BUDGET_MONTHLY_CAP_USD}
            )
        )

        assert tracker.is_exhausted() is True

    def test_is_not_exhausted_below_cap(self, tmp_path: Path) -> None:
        path = tmp_path / "budget.json"
        tracker = guardrails.BudgetTracker(path=path)
        path.write_text(
            json.dumps(
                {
                    "month": tracker._current_month(),
                    "cost_usd": guardrails.BUDGET_MONTHLY_CAP_USD - 1,
                }
            )
        )

        assert tracker.is_exhausted() is False

    def test_force_enable_env_var_overrides_exhaustion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "budget.json"
        tracker = guardrails.BudgetTracker(path=path)
        path.write_text(
            json.dumps(
                {"month": tracker._current_month(), "cost_usd": guardrails.BUDGET_MONTHLY_CAP_USD}
            )
        )
        monkeypatch.setenv("FORCE_ENABLE", "true")

        assert tracker.is_exhausted() is False

    def test_is_warning_at_threshold(self, tmp_path: Path) -> None:
        path = tmp_path / "budget.json"
        tracker = guardrails.BudgetTracker(path=path)
        warn_cost = (
            guardrails.BUDGET_WARN_THRESHOLD_PCT / 100.0
        ) * guardrails.BUDGET_MONTHLY_CAP_USD
        path.write_text(json.dumps({"month": tracker._current_month(), "cost_usd": warn_cost}))

        assert tracker.is_warning() is True

    def test_is_not_warning_below_threshold(self, tmp_path: Path) -> None:
        path = tmp_path / "budget.json"
        tracker = guardrails.BudgetTracker(path=path)
        path.write_text(json.dumps({"month": tracker._current_month(), "cost_usd": 0.0}))

        assert tracker.is_warning() is False
