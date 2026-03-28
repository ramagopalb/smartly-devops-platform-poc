"""
SLO Burn-Rate Calculator for Smartly Ad-Tech Platform.
Implements Google SRE Book multi-window burn-rate alerting.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class SLOConfig:
    """Service Level Objective configuration."""
    service_name: str
    availability_target: float  # e.g. 0.9997 for 99.97%
    error_budget_minutes_per_month: float = 0.0

    def __post_init__(self):
        if not (0 < self.availability_target < 1):
            raise ValueError(f"availability_target must be between 0 and 1, got {self.availability_target}")
        if self.availability_target < 0.9:
            raise ValueError("availability_target below 90% is not supported")
        # Calculate error budget if not provided
        if self.error_budget_minutes_per_month == 0.0:
            month_minutes = 30 * 24 * 60
            self.error_budget_minutes_per_month = month_minutes * (1 - self.availability_target)

    @property
    def error_rate_threshold(self) -> float:
        """Maximum allowed error rate."""
        return 1 - self.availability_target

    @property
    def error_budget_hours(self) -> float:
        return self.error_budget_minutes_per_month / 60


@dataclass
class BurnRateWindow:
    """Burn-rate alert window configuration."""
    window_hours: float
    burn_rate_threshold: float
    severity: str  # page, ticket

    def __post_init__(self):
        valid_severities = {"page", "ticket", "warning"}
        if self.severity not in valid_severities:
            raise ValueError(f"severity must be one of {valid_severities}")


class SLOCalculator:
    """
    Multi-window burn-rate SLO calculator.
    Based on Google SRE Workbook Chapter 5 alerting methodology.
    """

    # Standard multi-window burn rate windows (hours, burn_rate, severity)
    STANDARD_WINDOWS = [
        BurnRateWindow(window_hours=1, burn_rate_threshold=14.4, severity="page"),
        BurnRateWindow(window_hours=6, burn_rate_threshold=6.0, severity="page"),
        BurnRateWindow(window_hours=24, burn_rate_threshold=3.0, severity="ticket"),
        BurnRateWindow(window_hours=72, burn_rate_threshold=1.0, severity="ticket"),
    ]

    def __init__(self, slo: SLOConfig):
        self.slo = slo

    def calculate_burn_rate(self, observed_error_rate: float,
                            window_hours: float) -> float:
        """
        Calculate the burn rate for a given error rate and window.
        Burn rate = observed_error_rate / slo_error_rate_threshold
        """
        if observed_error_rate < 0 or observed_error_rate > 1:
            raise ValueError("observed_error_rate must be between 0 and 1")
        if window_hours <= 0:
            raise ValueError("window_hours must be positive")
        if self.slo.error_rate_threshold == 0:
            return float("inf") if observed_error_rate > 0 else 0.0
        return observed_error_rate / self.slo.error_rate_threshold

    def should_alert(self, burn_rate: float, window: BurnRateWindow) -> bool:
        """Determine if burn rate exceeds threshold for alert window."""
        return burn_rate >= window.burn_rate_threshold

    def get_time_to_budget_exhaustion(self, burn_rate: float) -> Optional[float]:
        """
        Calculate hours until error budget is exhausted at current burn rate.
        Returns None if burn rate <= 0.
        """
        if burn_rate <= 0:
            return None
        return self.slo.error_budget_hours / burn_rate

    def evaluate_all_windows(self, observed_error_rate: float) -> list[dict]:
        """
        Evaluate burn rate across all standard windows.
        Returns list of alert states.
        """
        results = []
        for window in self.STANDARD_WINDOWS:
            burn_rate = self.calculate_burn_rate(observed_error_rate, window.window_hours)
            is_alerting = self.should_alert(burn_rate, window)
            tte = self.get_time_to_budget_exhaustion(burn_rate)
            results.append({
                "window_hours": window.window_hours,
                "burn_rate": round(burn_rate, 4),
                "threshold": window.burn_rate_threshold,
                "severity": window.severity,
                "alerting": is_alerting,
                "time_to_exhaustion_hours": round(tte, 2) if tte else None,
            })
        return results

    def get_prometheus_alert_query(self, window: BurnRateWindow) -> str:
        """Generate Prometheus alerting query for a burn rate window."""
        window_str = f"{int(window.window_hours)}h"
        return (
            f"(\n"
            f"  sum(rate(http_requests_total{{service='{self.slo.service_name}',status=~'5..'}}[{window_str}]))\n"
            f"  /\n"
            f"  sum(rate(http_requests_total{{service='{self.slo.service_name}'}}[{window_str}]))\n"
            f") > {self.slo.error_rate_threshold * window.burn_rate_threshold:.6f}"
        )

    def error_budget_remaining_percent(self, consumed_error_minutes: float) -> float:
        """Calculate percentage of error budget remaining."""
        if self.slo.error_budget_minutes_per_month == 0:
            return 0.0
        remaining = self.slo.error_budget_minutes_per_month - consumed_error_minutes
        return max(0.0, (remaining / self.slo.error_budget_minutes_per_month) * 100)
