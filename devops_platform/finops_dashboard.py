"""
FinOps Cost Dashboard for Smartly Multi-Cloud Platform.
Tracks per-team AWS + GCP spend with anomaly detection and alerting.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TeamBudget:
    """Budget configuration for a team."""
    team: str
    monthly_budget_usd: float
    alert_threshold_percent: float = 80.0
    hard_limit_percent: float = 100.0

    def __post_init__(self):
        if self.monthly_budget_usd <= 0:
            raise ValueError("monthly_budget_usd must be positive")
        if not (0 < self.alert_threshold_percent <= 100):
            raise ValueError("alert_threshold_percent must be between 0 and 100")
        if self.hard_limit_percent < self.alert_threshold_percent:
            raise ValueError("hard_limit_percent must be >= alert_threshold_percent")


@dataclass
class CloudSpendRecord:
    """A cloud spend record for a team."""
    team: str
    cloud: str  # aws or gcp
    service: str
    amount_usd: float
    month: str  # YYYY-MM

    def __post_init__(self):
        if self.cloud not in ("aws", "gcp"):
            raise ValueError(f"cloud must be 'aws' or 'gcp', got '{self.cloud}'")
        if self.amount_usd < 0:
            raise ValueError("amount_usd cannot be negative")


@dataclass
class CostAnomaly:
    """Detected cost anomaly."""
    team: str
    cloud: str
    current_spend: float
    baseline_spend: float
    anomaly_percent: float
    severity: str  # warning, critical
    message: str


class FinOpsDashboard:
    """
    Multi-cloud FinOps cost tracking and anomaly detection.
    Supports AWS + GCP per-team spend with alerting and budget enforcement.
    """

    ANOMALY_WARNING_THRESHOLD = 20.0   # >20% above baseline
    ANOMALY_CRITICAL_THRESHOLD = 50.0  # >50% above baseline

    def __init__(self):
        self._budgets: dict[str, TeamBudget] = {}
        self._spend_records: list[CloudSpendRecord] = []

    def register_team_budget(self, budget: TeamBudget) -> None:
        """Register a team's monthly budget."""
        self._budgets[budget.team] = budget

    def record_spend(self, record: CloudSpendRecord) -> None:
        """Record a cloud spend entry."""
        self._spend_records.append(record)

    def get_team_spend(self, team: str, month: str,
                       cloud: Optional[str] = None) -> float:
        """Get total spend for a team in a given month."""
        total = 0.0
        for record in self._spend_records:
            if record.team != team or record.month != month:
                continue
            if cloud and record.cloud != cloud:
                continue
            total += record.amount_usd
        return total

    def get_budget_utilization(self, team: str, month: str) -> Optional[dict]:
        """Get budget utilization for a team."""
        if team not in self._budgets:
            return None
        budget = self._budgets[team]
        spent = self.get_team_spend(team, month)
        utilization_pct = (spent / budget.monthly_budget_usd) * 100

        return {
            "team": team,
            "month": month,
            "budget_usd": budget.monthly_budget_usd,
            "spent_usd": round(spent, 2),
            "utilization_percent": round(utilization_pct, 2),
            "alert_threshold_percent": budget.alert_threshold_percent,
            "hard_limit_percent": budget.hard_limit_percent,
            "status": self._budget_status(utilization_pct, budget),
        }

    def detect_anomalies(self, current_month: str,
                         baseline_month: str) -> list[CostAnomaly]:
        """Detect cost anomalies by comparing current vs baseline month."""
        anomalies = []
        teams = {r.team for r in self._spend_records}

        for team in teams:
            for cloud in ("aws", "gcp"):
                current = self.get_team_spend(team, current_month, cloud)
                baseline = self.get_team_spend(team, baseline_month, cloud)

                if baseline == 0:
                    continue  # Can't calculate anomaly without baseline

                change_pct = ((current - baseline) / baseline) * 100

                if change_pct >= self.ANOMALY_CRITICAL_THRESHOLD:
                    anomalies.append(CostAnomaly(
                        team=team,
                        cloud=cloud,
                        current_spend=current,
                        baseline_spend=baseline,
                        anomaly_percent=round(change_pct, 2),
                        severity="critical",
                        message=f"{team} {cloud.upper()} spend increased {change_pct:.1f}% vs baseline"
                    ))
                elif change_pct >= self.ANOMALY_WARNING_THRESHOLD:
                    anomalies.append(CostAnomaly(
                        team=team,
                        cloud=cloud,
                        current_spend=current,
                        baseline_spend=baseline,
                        anomaly_percent=round(change_pct, 2),
                        severity="warning",
                        message=f"{team} {cloud.upper()} spend increased {change_pct:.1f}% vs baseline"
                    ))

        return anomalies

    def get_top_spenders(self, month: str, n: int = 5) -> list[dict]:
        """Return top N teams by total spend for a given month."""
        teams = {r.team for r in self._spend_records}
        spend_by_team = [
            {"team": t, "spend_usd": round(self.get_team_spend(t, month), 2)}
            for t in teams
        ]
        return sorted(spend_by_team, key=lambda x: x["spend_usd"], reverse=True)[:n]

    def get_cloud_split(self, team: str, month: str) -> dict:
        """Get AWS vs GCP spend split for a team."""
        aws_spend = self.get_team_spend(team, month, "aws")
        gcp_spend = self.get_team_spend(team, month, "gcp")
        total = aws_spend + gcp_spend
        return {
            "team": team,
            "month": month,
            "aws_usd": round(aws_spend, 2),
            "gcp_usd": round(gcp_spend, 2),
            "total_usd": round(total, 2),
            "aws_percent": round((aws_spend / total) * 100, 1) if total > 0 else 0,
            "gcp_percent": round((gcp_spend / total) * 100, 1) if total > 0 else 0,
        }

    def _budget_status(self, utilization_pct: float, budget: TeamBudget) -> str:
        if utilization_pct >= budget.hard_limit_percent:
            return "over_budget"
        elif utilization_pct >= budget.alert_threshold_percent:
            return "warning"
        else:
            return "ok"
