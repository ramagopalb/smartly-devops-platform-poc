"""
Tests for MetricsCollector — Smartly DevOps Platform POC.
Covers metric recording, aggregation, ad-tech helpers,
SLO evaluation, alert rules, and Prometheus config generation.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "platform"))
import time
import pytest
from metrics_collector import (
    MetricsCollector,
    MetricPoint,
    AlertRule,
    SLOMetrics,
    MetricType,
    AlertSeverity,
    AlertState,
)


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def collector():
    return MetricsCollector()


@pytest.fixture
def healthy_slo():
    return SLOMetrics(
        service="ad-delivery",
        slo_target=0.999,
        request_count=100_000,
        error_count=50,
        p50_latency_ms=12.0,
        p99_latency_ms=45.0,
        p999_latency_ms=120.0,
    )


@pytest.fixture
def breaching_slo():
    return SLOMetrics(
        service="ad-delivery",
        slo_target=0.999,
        request_count=100_000,
        error_count=200,
        p50_latency_ms=15.0,
        p99_latency_ms=80.0,
    )


@pytest.fixture
def page_alert():
    return AlertRule(
        name="HighErrorRate",
        expr="error_rate > 0.01",
        severity=AlertSeverity.PAGE,
        summary="Error rate exceeded SLO",
        description="Service error rate is above the SLO threshold.",
        for_duration_seconds=120,
    )


# ------------------------------------------------------------------ #
#  MetricPoint tests (6 tests)
# ------------------------------------------------------------------ #

class TestMetricPoint:

    def test_valid_metric_point(self):
        pt = MetricPoint(name="http_requests_total", value=1234.0)
        assert pt.name == "http_requests_total"
        assert pt.value == 1234.0

    def test_metric_point_with_labels(self):
        pt = MetricPoint(
            name="ad_impressions_total",
            value=500.0,
            labels={"service": "ad-delivery", "env": "prod"},
        )
        assert pt.labels["service"] == "ad-delivery"

    def test_label_set_is_frozenset(self):
        pt = MetricPoint(name="metric_a", value=1.0, labels={"k": "v"})
        assert isinstance(pt.label_set, frozenset)

    def test_invalid_empty_name_raises(self):
        with pytest.raises(ValueError, match="metric name cannot be empty"):
            MetricPoint(name="", value=1.0)

    def test_default_metric_type(self):
        pt = MetricPoint(name="metric_a", value=1.0)
        assert pt.metric_type == MetricType.GAUGE

    def test_counter_metric_type(self):
        pt = MetricPoint(name="metric_a", value=1.0, metric_type=MetricType.COUNTER)
        assert pt.metric_type == MetricType.COUNTER


# ------------------------------------------------------------------ #
#  Metric recording & aggregation tests (15 tests)
# ------------------------------------------------------------------ #

class TestMetricsCollectorRecording:

    def test_record_single_point(self, collector):
        pt = MetricPoint(name="request_count", value=100.0)
        collector.record(pt)
        assert collector.point_count("request_count") == 1

    def test_record_multiple_points(self, collector):
        for i in range(10):
            collector.record(MetricPoint(name="request_count", value=float(i)))
        assert collector.point_count("request_count") == 10

    def test_latest_returns_last(self, collector):
        for i in range(5):
            collector.record(MetricPoint(name="metric_a", value=float(i)))
        latest = collector.latest("metric_a")
        assert latest.value == 4.0

    def test_latest_nonexistent_returns_none(self, collector):
        assert collector.latest("nonexistent") is None

    def test_average_calculation(self, collector):
        for v in [10.0, 20.0, 30.0]:
            collector.record(MetricPoint(name="latency_ms", value=v))
        avg = collector.average("latency_ms")
        assert abs(avg - 20.0) < 0.001

    def test_max_value(self, collector):
        for v in [1.0, 5.0, 3.0]:
            collector.record(MetricPoint(name="cpu_usage", value=v))
        assert collector.max_value("cpu_usage") == 5.0

    def test_min_value(self, collector):
        for v in [1.0, 5.0, 3.0]:
            collector.record(MetricPoint(name="cpu_usage", value=v))
        assert collector.min_value("cpu_usage") == 1.0

    def test_average_nonexistent_returns_none(self, collector):
        assert collector.average("nonexistent") is None

    def test_increment_counter(self, collector):
        collector.increment_counter("impressions_total", 1000.0)
        assert collector.get_counter("impressions_total") == 1000.0

    def test_increment_counter_accumulates(self, collector):
        collector.increment_counter("impressions_total", 500.0)
        collector.increment_counter("impressions_total", 500.0)
        assert collector.get_counter("impressions_total") == 1000.0

    def test_negative_counter_increment_raises(self, collector):
        with pytest.raises(ValueError, match="counter increment must be non-negative"):
            collector.increment_counter("metric", -1.0)

    def test_set_and_get_gauge(self, collector):
        collector.set_gauge("kafka_lag", 42.0)
        assert collector.get_gauge("kafka_lag") == 42.0

    def test_gauge_overwrites_previous(self, collector):
        collector.set_gauge("kafka_lag", 100.0)
        collector.set_gauge("kafka_lag", 50.0)
        assert collector.get_gauge("kafka_lag") == 50.0

    def test_percentile_median(self, collector):
        for v in range(1, 101):  # 1..100
            collector.record(MetricPoint(name="latency", value=float(v)))
        p50 = collector.percentile("latency", 50)
        assert 49.0 <= p50 <= 51.0

    def test_percentile_invalid_raises(self, collector):
        with pytest.raises(ValueError, match="percentile must be 0-100"):
            collector.percentile("latency", 101)


# ------------------------------------------------------------------ #
#  Ad-tech specific tests (10 tests)
# ------------------------------------------------------------------ #

class TestAdTechMetrics:

    def test_record_impression(self, collector):
        collector.record_impression(1_000_000, "campaign-123")
        count = collector.get_counter(
            "ad_impressions_total",
            labels={"campaign_id": "campaign-123", "creative_type": "display"},
        )
        assert count == 1_000_000

    def test_record_impression_accumulates(self, collector):
        collector.record_impression(500_000, "campaign-123")
        collector.record_impression(500_000, "campaign-123")
        count = collector.get_counter(
            "ad_impressions_total",
            labels={"campaign_id": "campaign-123", "creative_type": "display"},
        )
        assert count == 1_000_000

    def test_negative_impression_raises(self, collector):
        with pytest.raises(ValueError, match="impression count must be non-negative"):
            collector.record_impression(-1, "campaign-123")

    def test_record_bid_request(self, collector):
        collector.record_bid_request(bid_price_usd=0.05, won=True, dsp_id="dsp-1")
        requests = collector.get_counter("rtb_requests_total", labels={"dsp_id": "dsp-1"})
        assert requests == 1.0

    def test_bid_win_recorded(self, collector):
        collector.record_bid_request(bid_price_usd=0.05, won=True, dsp_id="dsp-1")
        wins = collector.get_counter("rtb_wins_total", labels={"dsp_id": "dsp-1"})
        assert wins == 1.0

    def test_bid_loss_not_counted_as_win(self, collector):
        collector.record_bid_request(bid_price_usd=0.03, won=False, dsp_id="dsp-1")
        wins = collector.get_counter("rtb_wins_total", labels={"dsp_id": "dsp-1"})
        assert wins == 0.0

    def test_negative_bid_price_raises(self, collector):
        with pytest.raises(ValueError, match="bid_price_usd must be non-negative"):
            collector.record_bid_request(bid_price_usd=-1.0, won=True, dsp_id="dsp-1")

    def test_kafka_consumer_lag_gauge(self, collector):
        collector.record_kafka_consumer_lag("ad-impressions", "impression-consumer", 5000)
        lag = collector.get_kafka_consumer_lag("ad-impressions", "impression-consumer")
        assert lag == 5000.0

    def test_kafka_lag_severity_info(self, collector):
        severity = collector.kafka_lag_severity(100)
        assert severity == AlertSeverity.INFO

    def test_kafka_lag_severity_critical(self, collector):
        severity = collector.kafka_lag_severity(100_000)
        assert severity == AlertSeverity.CRITICAL


# ------------------------------------------------------------------ #
#  SLO tests (10 tests)
# ------------------------------------------------------------------ #

class TestSLOMetrics:

    def test_error_rate_calculation(self, healthy_slo):
        assert abs(healthy_slo.error_rate - 0.0005) < 0.0001

    def test_availability_calculation(self, healthy_slo):
        assert abs(healthy_slo.availability - 0.9995) < 0.0001

    def test_within_slo_true(self, healthy_slo):
        assert healthy_slo.is_within_slo is True

    def test_within_slo_false(self, breaching_slo):
        assert breaching_slo.is_within_slo is False

    def test_slo_gap_positive_when_healthy(self, healthy_slo):
        assert healthy_slo.slo_gap > 0

    def test_slo_gap_negative_when_breaching(self, breaching_slo):
        assert breaching_slo.slo_gap < 0

    def test_evaluate_slo_result_structure(self, collector, healthy_slo):
        result = collector.evaluate_slo(healthy_slo)
        assert "service" in result
        assert "within_slo" in result
        assert "availability" in result
        assert "error_rate" in result

    def test_evaluate_slo_healthy(self, collector, healthy_slo):
        result = collector.evaluate_slo(healthy_slo)
        assert result["within_slo"] is True
        assert result["alert"] is False

    def test_evaluate_slo_breaching(self, collector, breaching_slo):
        result = collector.evaluate_slo(breaching_slo)
        assert result["within_slo"] is False
        assert result["alert"] is True

    def test_burn_rate_low_for_healthy(self, collector, healthy_slo):
        br = collector.burn_rate(healthy_slo)
        # error_rate=0.0005, error_budget_rate=0.001, burn_rate=0.5
        assert br < 1.0

    def test_invalid_slo_target_raises(self):
        with pytest.raises(ValueError, match="slo_target must be between 0 and 1"):
            SLOMetrics(
                service="x", slo_target=1.5,
                request_count=100, error_count=0,
                p50_latency_ms=10.0, p99_latency_ms=50.0,
            )

    def test_error_count_exceeds_requests_raises(self):
        with pytest.raises(ValueError, match="error_count cannot exceed"):
            SLOMetrics(
                service="x", slo_target=0.99,
                request_count=10, error_count=20,
                p50_latency_ms=10.0, p99_latency_ms=50.0,
            )

    def test_p99_less_than_p50_raises(self):
        with pytest.raises(ValueError, match="p99 must be >= p50"):
            SLOMetrics(
                service="x", slo_target=0.99,
                request_count=100, error_count=0,
                p50_latency_ms=100.0, p99_latency_ms=50.0,
            )


# ------------------------------------------------------------------ #
#  Alert rule tests (10 tests)
# ------------------------------------------------------------------ #

class TestAlertRules:

    def test_register_alert_rule(self, collector, page_alert):
        collector.register_alert_rule(page_alert)
        assert collector.alert_rule_count() == 1

    def test_list_alert_rules(self, collector, page_alert):
        collector.register_alert_rule(page_alert)
        rules = collector.list_alert_rules()
        assert "HighErrorRate" in rules

    def test_alert_initially_inactive(self, collector, page_alert):
        collector.register_alert_rule(page_alert)
        state = collector.get_alert_state("HighErrorRate")
        assert state == AlertState.INACTIVE

    def test_fire_alert(self, collector, page_alert):
        collector.register_alert_rule(page_alert)
        result = collector.fire_alert("HighErrorRate")
        assert result is True
        assert collector.get_alert_state("HighErrorRate") == AlertState.FIRING

    def test_resolve_alert(self, collector, page_alert):
        collector.register_alert_rule(page_alert)
        collector.fire_alert("HighErrorRate")
        result = collector.resolve_alert("HighErrorRate")
        assert result is True
        assert collector.get_alert_state("HighErrorRate") == AlertState.RESOLVED

    def test_firing_alerts_list(self, collector, page_alert):
        collector.register_alert_rule(page_alert)
        collector.fire_alert("HighErrorRate")
        firing = collector.firing_alerts()
        assert "HighErrorRate" in firing

    def test_fire_nonexistent_alert(self, collector):
        assert collector.fire_alert("nonexistent") is False

    def test_prometheus_rule_format(self, page_alert):
        rule = page_alert.to_prometheus_rule()
        assert rule["alert"] == "HighErrorRate"
        assert rule["labels"]["severity"] == "page"
        assert "for" in rule  # for_duration_seconds=120 -> "2m"

    def test_prometheus_rule_no_for_when_zero(self):
        r = AlertRule(
            name="InstantAlert",
            expr="metric > 0",
            severity=AlertSeverity.WARNING,
            summary="test",
            for_duration_seconds=0,
        )
        rule = r.to_prometheus_rule()
        assert "for" not in rule

    def test_smartly_standard_alerts_count(self, collector):
        alerts = MetricsCollector.smartly_standard_alerts()
        assert len(alerts) == 5

    def test_invalid_empty_alert_name_raises(self):
        with pytest.raises(ValueError, match="alert rule name cannot be empty"):
            AlertRule(name="", expr="x > 0", severity=AlertSeverity.INFO, summary="test")

    def test_invalid_empty_expr_raises(self):
        with pytest.raises(ValueError, match="alert expression cannot be empty"):
            AlertRule(name="Alert", expr="", severity=AlertSeverity.INFO, summary="test")


# ------------------------------------------------------------------ #
#  Prometheus manifest generation tests (5 tests)
# ------------------------------------------------------------------ #

class TestPrometheusManifest:

    def test_manifest_kind(self, collector, page_alert):
        collector.register_alert_rule(page_alert)
        manifest = collector.generate_prometheus_rules_manifest()
        assert manifest["kind"] == "PrometheusRule"

    def test_manifest_namespace(self, collector, page_alert):
        collector.register_alert_rule(page_alert)
        manifest = collector.generate_prometheus_rules_manifest()
        assert manifest["metadata"]["namespace"] == "monitoring"

    def test_manifest_has_groups(self, collector, page_alert):
        collector.register_alert_rule(page_alert)
        manifest = collector.generate_prometheus_rules_manifest()
        assert len(manifest["spec"]["groups"]) == 1

    def test_manifest_rules_count(self, collector, page_alert):
        collector.register_alert_rule(page_alert)
        manifest = collector.generate_prometheus_rules_manifest()
        rules = manifest["spec"]["groups"][0]["rules"]
        assert len(rules) == 1

    def test_manifest_with_standard_alerts(self, collector):
        for rule in MetricsCollector.smartly_standard_alerts():
            collector.register_alert_rule(rule)
        manifest = collector.generate_prometheus_rules_manifest()
        rules = manifest["spec"]["groups"][0]["rules"]
        assert len(rules) == 5
