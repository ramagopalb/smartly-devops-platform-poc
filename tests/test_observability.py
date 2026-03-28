"""
Tests for SLO Calculator and Observability configuration.
Validates burn-rate calculations, alert thresholds, and Prometheus queries.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "platform"))

from slo_calculator import SLOConfig, BurnRateWindow, SLOCalculator


class TestSLOConfig:
    def test_valid_slo_config(self):
        slo = SLOConfig(service_name="creative-delivery", availability_target=0.9997)
        assert slo.service_name == "creative-delivery"
        assert slo.availability_target == 0.9997

    def test_error_rate_threshold_calculation(self):
        slo = SLOConfig(service_name="test", availability_target=0.999)
        assert abs(slo.error_rate_threshold - 0.001) < 1e-10

    def test_error_budget_calculated_automatically(self):
        slo = SLOConfig(service_name="test", availability_target=0.999)
        # 30 days * 24h * 60min * 0.001 = 43.2 minutes
        assert abs(slo.error_budget_minutes_per_month - 43.2) < 0.1

    def test_availability_above_1_raises(self):
        with pytest.raises(ValueError):
            SLOConfig(service_name="test", availability_target=1.1)

    def test_availability_zero_raises(self):
        with pytest.raises(ValueError):
            SLOConfig(service_name="test", availability_target=0)

    def test_availability_below_90_raises(self):
        with pytest.raises(ValueError, match="90%"):
            SLOConfig(service_name="test", availability_target=0.89)

    def test_error_budget_hours(self):
        slo = SLOConfig(service_name="test", availability_target=0.999)
        assert slo.error_budget_hours > 0


class TestSLOCalculator:
    def setup_method(self):
        self.slo = SLOConfig(service_name="creative-delivery", availability_target=0.9997)
        self.calc = SLOCalculator(self.slo)

    def test_burn_rate_zero_for_zero_errors(self):
        br = self.calc.calculate_burn_rate(0.0, window_hours=1)
        assert br == 0.0

    def test_burn_rate_one_at_slo_threshold(self):
        br = self.calc.calculate_burn_rate(self.slo.error_rate_threshold, window_hours=1)
        assert abs(br - 1.0) < 1e-6

    def test_burn_rate_above_one_triggers_alert(self):
        # 10x the error rate = burn rate of 10
        br = self.calc.calculate_burn_rate(self.slo.error_rate_threshold * 10, window_hours=1)
        assert br > 1.0

    def test_negative_error_rate_raises(self):
        with pytest.raises(ValueError):
            self.calc.calculate_burn_rate(-0.01, window_hours=1)

    def test_error_rate_above_one_raises(self):
        with pytest.raises(ValueError):
            self.calc.calculate_burn_rate(1.1, window_hours=1)

    def test_zero_window_raises(self):
        with pytest.raises(ValueError):
            self.calc.calculate_burn_rate(0.001, window_hours=0)

    def test_high_burn_rate_triggers_page(self):
        # Burn rate of 15 should trigger page for 1h window (threshold 14.4)
        window = BurnRateWindow(window_hours=1, burn_rate_threshold=14.4, severity="page")
        assert self.calc.should_alert(15.0, window) is True

    def test_low_burn_rate_no_alert(self):
        window = BurnRateWindow(window_hours=1, burn_rate_threshold=14.4, severity="page")
        assert self.calc.should_alert(5.0, window) is False

    def test_time_to_exhaustion_decreases_with_higher_burn(self):
        tte_low = self.calc.get_time_to_budget_exhaustion(1.0)
        tte_high = self.calc.get_time_to_budget_exhaustion(10.0)
        assert tte_high < tte_low

    def test_time_to_exhaustion_none_for_zero_burn(self):
        tte = self.calc.get_time_to_budget_exhaustion(0.0)
        assert tte is None

    def test_evaluate_all_windows_returns_four_results(self):
        results = self.calc.evaluate_all_windows(0.001)
        assert len(results) == 4

    def test_evaluate_high_error_rate_triggers_page_alerts(self):
        # Very high error rate should trigger page-level alerts
        results = self.calc.evaluate_all_windows(0.1)
        page_alerts = [r for r in results if r["severity"] == "page" and r["alerting"]]
        assert len(page_alerts) > 0

    def test_prometheus_query_contains_service_name(self):
        window = BurnRateWindow(window_hours=1, burn_rate_threshold=14.4, severity="page")
        query = self.calc.get_prometheus_alert_query(window)
        assert "creative-delivery" in query

    def test_error_budget_full_when_zero_consumed(self):
        pct = self.calc.error_budget_remaining_percent(0)
        assert pct == 100.0

    def test_error_budget_zero_when_fully_consumed(self):
        pct = self.calc.error_budget_remaining_percent(self.slo.error_budget_minutes_per_month)
        assert pct == 0.0
