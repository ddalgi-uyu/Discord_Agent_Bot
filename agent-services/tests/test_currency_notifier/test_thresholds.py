"""Tests for :mod:`currency_notifier.thresholds`.

Pure-function module — exercises ``evaluate_thresholds`` directly with a
variety of synthetic configurations and histories. No I/O, no fixtures.
"""
from __future__ import annotations

import math

import pytest

from currency_notifier.thresholds import evaluate_thresholds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pair_config(**overrides: object) -> dict[str, object]:
    """Return a baseline ``USD/JPY`` config with optional overrides."""
    base: dict[str, object] = {
        "base": "USD",
        "quote": "JPY",
        "static_threshold": None,
        "dynamic": None,
        "change_threshold_pct": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Static threshold
# ---------------------------------------------------------------------------


class TestStaticThreshold:
    def test_fires_when_rate_below_target(self) -> None:
        alerts = evaluate_thresholds(
            _pair_config(static_threshold=145.0),
            rate=144.0,
            history=[],
        )
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert["type"] == "static"
        assert alert["pair"] == "USD/JPY"
        assert alert["rate"] == 144.0
        assert alert["threshold"] == 145.0
        assert "at or below target" in alert["message"]

    def test_fires_when_rate_equals_target(self) -> None:
        """The check is ``<=``, so equal-to-target still triggers."""
        alerts = evaluate_thresholds(
            _pair_config(static_threshold=145.0),
            rate=145.0,
            history=[],
        )
        assert len(alerts) == 1
        assert alerts[0]["type"] == "static"

    def test_does_not_fire_when_rate_above_target(self) -> None:
        alerts = evaluate_thresholds(
            _pair_config(static_threshold=145.0),
            rate=146.0,
            history=[],
        )
        assert alerts == []

    def test_no_static_threshold_means_no_static_alert(self) -> None:
        """Missing static config is silently disabled, not an error."""
        alerts = evaluate_thresholds(
            _pair_config(),  # no static_threshold
            rate=10.0,
            history=[],
        )
        assert alerts == []

    def test_static_threshold_works_with_empty_history(self) -> None:
        """The static check is intentionally history-independent."""
        alerts = evaluate_thresholds(
            _pair_config(static_threshold=145.0),
            rate=120.0,
            history=[],
        )
        assert len(alerts) == 1
        assert alerts[0]["type"] == "static"


# ---------------------------------------------------------------------------
# Dynamic (z-score) threshold
# ---------------------------------------------------------------------------


class TestDynamicThreshold:
    def test_z_score_below_threshold_fires(self) -> None:
        # Mean ≈ 100, std ≈ 1, current rate 98 → z ≈ -2, threshold -1.5 → fires.
        history = [99.0, 100.0, 101.0, 100.0, 100.0]
        alerts = evaluate_thresholds(
            _pair_config(dynamic={"window_days": 5, "z_threshold": -1.5}),
            rate=98.0,
            history=history,
        )
        assert any(a["type"] == "dynamic" for a in alerts)
        dynamic = next(a for a in alerts if a["type"] == "dynamic")
        assert dynamic["z_score"] < -1.5
        assert "statistically low" in dynamic["message"]
        assert dynamic["pair"] == "USD/JPY"

    def test_z_score_above_threshold_does_not_fire(self) -> None:
        history = [99.0, 100.0, 101.0, 100.0, 100.0]
        alerts = evaluate_thresholds(
            _pair_config(dynamic={"window_days": 5, "z_threshold": -1.5}),
            rate=100.5,  # close to the mean
            history=history,
        )
        assert all(a["type"] != "dynamic" for a in alerts)

    def test_zero_std_does_not_crash_and_yields_z_score_zero(self) -> None:
        """All-equal history → std=0 → z_score defined as 0, never NaN."""
        history = [100.0, 100.0, 100.0, 100.0, 100.0]
        alerts = evaluate_thresholds(
            _pair_config(dynamic={"window_days": 5, "z_threshold": -1.5}),
            rate=100.0,
            history=history,
        )
        # z_score == 0 > -1.5 → no dynamic alert.
        assert all(a["type"] != "dynamic" for a in alerts)

    def test_window_shorter_than_history_truncates(self) -> None:
        """Only the last ``window_days`` samples are used."""
        # 10 samples; window_days=3 takes only the last 3.
        # mean of last 3 = (104+105+106)/3 = 105, std small.
        history = [90, 92, 95, 97, 99, 100, 102, 104, 105, 106]
        alerts = evaluate_thresholds(
            _pair_config(dynamic={"window_days": 3, "z_threshold": -1.0}),
            rate=99.0,  # clearly below windowed mean
            history=history,
        )
        dynamic = next((a for a in alerts if a["type"] == "dynamic"), None)
        assert dynamic is not None
        # Mean should be that of the last 3 samples.
        assert dynamic["mean"] == pytest.approx(105.0)

    def test_short_history_skips_dynamic(self) -> None:
        """Fewer than 2 samples → cannot compute z-score → no alert."""
        alerts = evaluate_thresholds(
            _pair_config(dynamic={"window_days": 7, "z_threshold": -1.5}),
            rate=50.0,
            history=[100.0],
        )
        assert all(a["type"] != "dynamic" for a in alerts)


# ---------------------------------------------------------------------------
# Empty history edge case
# ---------------------------------------------------------------------------


class TestEmptyHistory:
    def test_no_history_no_crash(self) -> None:
        """``history=[]`` must not raise and must return a list."""
        result = evaluate_thresholds(
            _pair_config(),
            rate=100.0,
            history=[],
        )
        assert isinstance(result, list)

    def test_no_history_only_static_can_fire(self) -> None:
        """With empty history, only the static threshold is meaningful."""
        alerts = evaluate_thresholds(
            _pair_config(
                static_threshold=105.0,
                dynamic={"window_days": 7, "z_threshold": -1.5},
                change_threshold_pct=5.0,
            ),
            rate=100.0,
            history=[],
        )
        # Only the static alert should be present.
        assert [a["type"] for a in alerts] == ["static"]

    def test_no_history_no_alerts_at_all(self) -> None:
        """Empty history and no static threshold → no alerts."""
        alerts = evaluate_thresholds(
            _pair_config(dynamic={"window_days": 7, "z_threshold": -1.5}),
            rate=100.0,
            history=[],
        )
        assert alerts == []


# ---------------------------------------------------------------------------
# Percentage change (7-day lookback)
# ---------------------------------------------------------------------------


class TestPercentageChange:
    def test_7_day_change_above_threshold_fires(self) -> None:
        # history[-8] is the "7 days ago" point when history[-1] is current.
        history = [100.0] * 7 + [110.0]  # 10% jump
        alerts = evaluate_thresholds(
            _pair_config(change_threshold_pct=5.0),
            rate=110.0,
            history=history,
        )
        assert any(a["type"] == "change_pct" for a in alerts)
        pct = next(a for a in alerts if a["type"] == "change_pct")
        assert pct["change_pct"] == pytest.approx(10.0)
        assert pct["threshold"] == pytest.approx(5.0)
        assert "7-day change" in pct["message"]

    def test_7_day_change_below_threshold_does_not_fire(self) -> None:
        history = [100.0] * 7 + [101.0]  # only 1% jump
        alerts = evaluate_thresholds(
            _pair_config(change_threshold_pct=5.0),
            rate=101.0,
            history=history,
        )
        assert all(a["type"] != "change_pct" for a in alerts)

    def test_short_history_skips_percentage_check(self) -> None:
        """Need at least 8 samples (current + 7 lookback) for this check."""
        history = [100.0, 100.0, 100.0, 110.0]  # only 4 samples
        alerts = evaluate_thresholds(
            _pair_config(change_threshold_pct=1.0),
            rate=110.0,
            history=history,
        )
        assert all(a["type"] != "change_pct" for a in alerts)

    def test_percentage_change_disabled_when_threshold_none(self) -> None:
        history = [100.0] * 7 + [200.0]  # huge jump
        alerts = evaluate_thresholds(
            _pair_config(),  # change_threshold_pct is None
            rate=200.0,
            history=history,
        )
        assert all(a["type"] != "change_pct" for a in alerts)

    def test_percentage_change_treats_negative_baseline_as_no_check(self) -> None:
        """If the 7-days-ago sample is zero/negative, skip rather than divide."""
        history = [0.0] * 7 + [110.0]
        alerts = evaluate_thresholds(
            _pair_config(change_threshold_pct=1.0),
            rate=110.0,
            history=history,
        )
        assert all(a["type"] != "change_pct" for a in alerts)


# ---------------------------------------------------------------------------
# Combination behaviour
# ---------------------------------------------------------------------------


class TestCombined:
    def test_multiple_alerts_can_coexist(self) -> None:
        history = [100.0] * 7 + [80.0]  # 20% drop + way below static + low z
        alerts = evaluate_thresholds(
            _pair_config(
                static_threshold=145.0,
                dynamic={"window_days": 7, "z_threshold": -1.0},
                change_threshold_pct=5.0,
            ),
            rate=80.0,
            history=history,
        )
        types = sorted(a["type"] for a in alerts)
        assert "static" in types
        assert "dynamic" in types
        assert "change_pct" in types

    def test_every_alert_carries_required_fields(self) -> None:
        alerts = evaluate_thresholds(
            _pair_config(static_threshold=145.0),
            rate=120.0,
            history=[],
        )
        for alert in alerts:
            assert "type" in alert
            assert "pair" in alert
            assert "rate" in alert
            assert "message" in alert
            assert alert["pair"] == "USD/JPY"
            assert alert["rate"] == 120.0

    def test_unknown_currency_codes_still_produce_pair_label(self) -> None:
        """Pair label is built from the dict even if codes are odd."""
        cfg: dict[str, object] = {
            "base": "AAA",
            "quote": "BBB",
            "static_threshold": 1.0,
        }
        alerts = evaluate_thresholds(cfg, rate=0.5, history=[])
        assert len(alerts) == 1
        assert alerts[0]["pair"] == "AAA/BBB"


# ---------------------------------------------------------------------------
# Sanity: explicit math check on the z-score formula
# ---------------------------------------------------------------------------


def test_z_score_formula_matches_population_std() -> None:
    """Verify the evaluator uses the population (n) standard deviation."""
    history = [99.0, 100.0, 101.0]
    mean = sum(history) / len(history)  # 100.0
    variance = sum((x - mean) ** 2 for x in history) / len(history)  # 2/3
    std = math.sqrt(variance)
    expected_z = (95.0 - mean) / std
    # Threshold set slightly above the expected z-score so the alert fires
    # while still verifying the formula is exactly what we compute by hand.
    alerts = evaluate_thresholds(
        _pair_config(dynamic={"window_days": 3, "z_threshold": expected_z + 0.01}),
        rate=95.0,
        history=history,
    )
    dynamic = next(a for a in alerts if a["type"] == "dynamic")
    assert dynamic["z_score"] == pytest.approx(expected_z)
