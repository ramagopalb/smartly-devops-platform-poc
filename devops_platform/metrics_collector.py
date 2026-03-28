"""
Metrics Collector for Smartly Ad-Tech Platform.
Collects, aggregates, and evaluates Prometheus-compatible metrics
for ad impression throughput, Kafka consumer lag, and SLO burn-rate alerting.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    PAGE = "page"


class AlertState(str, Enum):
    INACTIVE = "inactive"
    PENDING = "pending"
    FIRING = "firing"
    RESOLVED = "resolved"


@dataclass
class MetricPoint:
    """A single metric observation."""
    name: str
    value: float
    labels: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    metric_type: MetricType = MetricType.GAUGE

    def __post_init__(self):
        if not self.name:
            raise ValueError("metric name cannot be empty")
        if not self.name.replace("_", "").replace(":", "").isalnum():
            raise ValueError(
                f"metric name {self.name!r} contains invalid characters"
            )

    @property
    def label_set(self) -> frozenset:
        return frozenset(self.labels.items())


@dataclass
class AlertRule:
    """Prometheus-style alerting rule."""
    name: str
    expr: str
    severity: AlertSeverity
    summary: str
    description: str = ""
    for_duration_seconds: int = 0
    labels: dict = field(default_factory=dict)
    annotations: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.name:
            raise ValueError("alert rule name cannot be empty")
        if not self.expr:
            raise ValueError("alert expression cannot be empty")
        if self.for_duration_seconds < 0:
            raise ValueError("for_duration_seconds must be >= 0")

    def to_prometheus_rule(self) -> dict:
        """Serialise to Prometheus PrometheusRule format."""
        rule = {
            "alert": self.name,
            "expr": self.expr,
            "labels": {"severity": self.severity.value, **self.labels},
            "annotations": {
                "summary": self.summary,
                "description": self.description,
                **self.annotations,
            },
        }
        if self.for_duration_seconds > 0:
            mins = self.for_duration_seconds // 60
            secs = self.for_duration_seconds % 60
            rule["for"] = f"{mins}m{secs}s" if secs else f"{mins}m"
        return rule


@dataclass
class SLOMetrics:
    """SLO health snapshot for a service."""
    service: str
    slo_target: float
    request_count: int
    error_count: int
    p50_latency_ms: float
    p99_latency_ms: float
    p999_latency_ms: float = 0.0

    def __post_init__(self):
        if not (0 < self.slo_target < 1):
            raise ValueError("slo_target must be between 0 and 1")
        if self.request_count < 0:
            raise ValueError("request_count must be >= 0")
        if self.error_count < 0:
            raise ValueError("error_count must be >= 0")
        if self.error_count > self.request_count:
            raise ValueError("error_count cannot exceed request_count")
        if self.p50_latency_ms < 0:
            raise ValueError("p50_latency_ms must be >= 0")
        if self.p99_latency_ms < self.p50_latency_ms:
            raise ValueError("p99 must be >= p50")

    @property
    def error_rate(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self.error_count / self.request_count

    @property
    def availability(self) -> float:
        return 1 - self.error_rate

    @property
    def is_within_slo(self) -> bool:
        return self.availability >= self.slo_target

    @property
    def slo_gap(self) -> float:
        """Positive = above target, negative = below target."""
        return self.availability - self.slo_target


class MetricsCollector:
    """
    Prometheus-compatible metrics collector for Smartly ad-tech platform.
    Handles ad impression metrics, Kafka consumer lag, SLO burn-rate evaluation,
    and alerting rule management.
    """

    MAX_METRICS_PER_NAME = 10_000
    KAFKA_LAG_WARNING_THRESHOLD = 5_000
    KAFKA_LAG_CRITICAL_THRESHOLD = 50_000

    def __init__(self):
        self._metrics: dict[str, list[MetricPoint]] = {}
        self._alert_rules: dict[str, AlertRule] = {}
        self._alert_states: dict[str, AlertState] = {}
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}

    # ------------------------------------------------------------------ #
    #  Metric recording
    # ------------------------------------------------------------------ #

    def record(self, point: MetricPoint) -> None:
        """Record a metric observation."""
        if point.name not in self._metrics:
            self._metrics[point.name] = []
        if len(self._metrics[point.name]) >= self.MAX_METRICS_PER_NAME:
            # Ring-buffer: drop oldest
            self._metrics[point.name].pop(0)
        self._metrics[point.name].append(point)

    def increment_counter(self, name: str, value: float = 1.0,
                           labels: Optional[dict] = None) -> None:
        """Increment a named counter."""
        if value < 0:
            raise ValueError("counter increment must be non-negative")
        key = f"{name}:{sorted((labels or {}).items())}"
        self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(self, name: str, value: float,
                  labels: Optional[dict] = None) -> None:
        """Set a gauge value."""
        key = f"{name}:{sorted((labels or {}).items())}"
        self._gauges[key] = value

    def get_gauge(self, name: str, labels: Optional[dict] = None) -> Optional[float]:
        key = f"{name}:{sorted((labels or {}).items())}"
        return self._gauges.get(key)

    def get_counter(self, name: str, labels: Optional[dict] = None) -> float:
        key = f"{name}:{sorted((labels or {}).items())}"
        return self._counters.get(key, 0.0)

    # ------------------------------------------------------------------ #
    #  Aggregation
    # ------------------------------------------------------------------ #

    def latest(self, metric_name: str) -> Optional[MetricPoint]:
        points = self._metrics.get(metric_name, [])
        return points[-1] if points else None

    def average(self, metric_name: str) -> Optional[float]:
        points = self._metrics.get(metric_name, [])
        if not points:
            return None
        return sum(p.value for p in points) / len(points)

    def max_value(self, metric_name: str) -> Optional[float]:
        points = self._metrics.get(metric_name, [])
        if not points:
            return None
        return max(p.value for p in points)

    def min_value(self, metric_name: str) -> Optional[float]:
        points = self._metrics.get(metric_name, [])
        if not points:
            return None
        return min(p.value for p in points)

    def rate(self, metric_name: str, window_seconds: float = 60.0) -> Optional[float]:
        """Approximate per-second rate over the last window_seconds."""
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        points = self._metrics.get(metric_name, [])
        if len(points) < 2:
            return None
        now = time.time()
        recent = [p for p in points if now - p.timestamp <= window_seconds]
        if len(recent) < 2:
            return None
        time_span = recent[-1].timestamp - recent[0].timestamp
        if time_span <= 0:
            return None
        value_delta = recent[-1].value - recent[0].value
        return value_delta / time_span

    def percentile(self, metric_name: str, p: float) -> Optional[float]:
        """Return the p-th percentile (0-100) of recorded values."""
        if not (0 <= p <= 100):
            raise ValueError("percentile must be 0-100")
        points = self._metrics.get(metric_name, [])
        if not points:
            return None
        sorted_vals = sorted(pt.value for pt in points)
        idx = (p / 100) * (len(sorted_vals) - 1)
        lower = int(idx)
        upper = min(lower + 1, len(sorted_vals) - 1)
        frac = idx - lower
        return sorted_vals[lower] * (1 - frac) + sorted_vals[upper] * frac

    def point_count(self, metric_name: str) -> int:
        return len(self._metrics.get(metric_name, []))

    # ------------------------------------------------------------------ #
    #  Ad-tech specific helpers
    # ------------------------------------------------------------------ #

    def record_impression(self, count: int, campaign_id: str,
                           creative_type: str = "display") -> None:
        """Record ad impressions served."""
        if count < 0:
            raise ValueError("impression count must be non-negative")
        self.increment_counter(
            "ad_impressions_total",
            float(count),
            labels={"campaign_id": campaign_id, "creative_type": creative_type},
        )

    def record_bid_request(self, bid_price_usd: float, won: bool,
                            dsp_id: str) -> None:
        """Record a real-time bidding request."""
        if bid_price_usd < 0:
            raise ValueError("bid_price_usd must be non-negative")
        self.increment_counter("rtb_requests_total", labels={"dsp_id": dsp_id})
        if won:
            self.increment_counter("rtb_wins_total", labels={"dsp_id": dsp_id})
            self.increment_counter(
                "rtb_spend_usd_total", bid_price_usd, labels={"dsp_id": dsp_id}
            )

    def record_kafka_consumer_lag(self, topic: str, consumer_group: str,
                                   lag: int) -> None:
        """Record Kafka consumer lag for impression tracking."""
        if lag < 0:
            raise ValueError("kafka lag must be non-negative")
        self.set_gauge(
            "kafka_consumer_lag",
            float(lag),
            labels={"topic": topic, "consumer_group": consumer_group},
        )

    def get_kafka_consumer_lag(self, topic: str, consumer_group: str) -> Optional[float]:
        return self.get_gauge(
            "kafka_consumer_lag",
            labels={"topic": topic, "consumer_group": consumer_group},
        )

    def kafka_lag_severity(self, lag: int) -> AlertSeverity:
        """Return alert severity based on Kafka consumer lag."""
        if lag >= self.KAFKA_LAG_CRITICAL_THRESHOLD:
            return AlertSeverity.CRITICAL
        if lag >= self.KAFKA_LAG_WARNING_THRESHOLD:
            return AlertSeverity.WARNING
        return AlertSeverity.INFO

    # ------------------------------------------------------------------ #
    #  SLO evaluation
    # ------------------------------------------------------------------ #

    def evaluate_slo(self, slo: SLOMetrics) -> dict:
        """Evaluate SLO health and return structured result."""
        return {
            "service": slo.service,
            "slo_target": slo.slo_target,
            "availability": round(slo.availability, 6),
            "error_rate": round(slo.error_rate, 6),
            "within_slo": slo.is_within_slo,
            "slo_gap": round(slo.slo_gap, 6),
            "p50_ms": slo.p50_latency_ms,
            "p99_ms": slo.p99_latency_ms,
            "alert": not slo.is_within_slo,
        }

    def burn_rate(self, slo: SLOMetrics) -> float:
        """Instantaneous burn rate relative to SLO error budget."""
        error_budget_rate = 1 - slo.slo_target
        if error_budget_rate == 0:
            return float("inf") if slo.error_rate > 0 else 0.0
        return slo.error_rate / error_budget_rate

    # ------------------------------------------------------------------ #
    #  Alert rules
    # ------------------------------------------------------------------ #

    def register_alert_rule(self, rule: AlertRule) -> None:
        """Register an alerting rule."""
        self._alert_rules[rule.name] = rule
        self._alert_states[rule.name] = AlertState.INACTIVE

    def fire_alert(self, rule_name: str) -> bool:
        """Transition an alert to FIRING state."""
        if rule_name not in self._alert_rules:
            return False
        self._alert_states[rule_name] = AlertState.FIRING
        return True

    def resolve_alert(self, rule_name: str) -> bool:
        """Resolve a firing alert."""
        if rule_name not in self._alert_states:
            return False
        self._alert_states[rule_name] = AlertState.RESOLVED
        return True

    def get_alert_state(self, rule_name: str) -> Optional[AlertState]:
        return self._alert_states.get(rule_name)

    def firing_alerts(self) -> list[str]:
        """Return names of currently firing alerts."""
        return [k for k, v in self._alert_states.items() if v == AlertState.FIRING]

    def list_alert_rules(self) -> list[str]:
        return sorted(self._alert_rules.keys())

    def alert_rule_count(self) -> int:
        return len(self._alert_rules)

    # ------------------------------------------------------------------ #
    #  Predefined Smartly alert rules
    # ------------------------------------------------------------------ #

    @staticmethod
    def smartly_standard_alerts() -> list[AlertRule]:
        """Return the standard alert ruleset for Smartly ad-tech platform."""
        return [
            AlertRule(
                name="HighAdImpressionErrorRate",
                expr=(
                    "sum(rate(ad_delivery_errors_total[5m])) "
                    "/ sum(rate(ad_impressions_total[5m])) > 0.01"
                ),
                severity=AlertSeverity.PAGE,
                summary="Ad impression error rate > 1%",
                description="Ad delivery error rate has exceeded SLO threshold.",
                for_duration_seconds=120,
            ),
            AlertRule(
                name="KafkaConsumerLagHigh",
                expr="kafka_consumer_lag{topic='ad-impressions'} > 50000",
                severity=AlertSeverity.CRITICAL,
                summary="Kafka consumer lag > 50k messages",
                description="Impression tracking pipeline is falling behind.",
                for_duration_seconds=300,
            ),
            AlertRule(
                name="KubernetesNodePressure",
                expr="kube_node_status_condition{condition='MemoryPressure',status='true'} == 1",
                severity=AlertSeverity.WARNING,
                summary="Node memory pressure detected",
                description="One or more nodes are under memory pressure.",
                for_duration_seconds=180,
            ),
            AlertRule(
                name="ArgoRolloutDegraded",
                expr="argo_rollout_phase{phase='Degraded'} > 0",
                severity=AlertSeverity.CRITICAL,
                summary="ArgoCD rollout is degraded",
                description="A progressive delivery rollout has entered a degraded state.",
                for_duration_seconds=60,
            ),
            AlertRule(
                name="SLOBurnRateHigh",
                expr=(
                    "sum(rate(http_requests_total{status=~'5..'}[1h])) "
                    "/ sum(rate(http_requests_total[1h])) "
                    "> 0.0003 * 14.4"
                ),
                severity=AlertSeverity.PAGE,
                summary="SLO burn rate > 14.4x",
                description="Error budget will be exhausted within 2 hours at current rate.",
                for_duration_seconds=120,
            ),
        ]

    # ------------------------------------------------------------------ #
    #  Prometheus config generation
    # ------------------------------------------------------------------ #

    def generate_prometheus_rules_manifest(self, group_name: str = "smartly.rules") -> dict:
        """Generate a PrometheusRule CRD manifest for all registered rules."""
        return {
            "apiVersion": "monitoring.coreos.com/v1",
            "kind": "PrometheusRule",
            "metadata": {
                "name": group_name.replace(".", "-"),
                "namespace": "monitoring",
                "labels": {
                    "app.kubernetes.io/part-of": "kube-prometheus",
                    "prometheus": "kube-prometheus",
                    "role": "alert-rules",
                },
            },
            "spec": {
                "groups": [
                    {
                        "name": group_name,
                        "rules": [
                            r.to_prometheus_rule()
                            for r in self._alert_rules.values()
                        ],
                    }
                ]
            },
        }
