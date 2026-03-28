"""
Tests for FinOps Cost Dashboard.
Validates multi-cloud spend tracking, anomaly detection, and budget enforcement.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "platform"))

from finops_dashboard import TeamBudget, CloudSpendRecord, FinOpsDashboard, CostAnomaly


class TestTeamBudget:
    def test_valid_budget_creates(self):
        budget = TeamBudget(team="ads", monthly_budget_usd=5000.0)
        assert budget.team == "ads"
        assert budget.monthly_budget_usd == 5000.0

    def test_zero_budget_raises(self):
        with pytest.raises(ValueError):
            TeamBudget(team="ads", monthly_budget_usd=0)

    def test_negative_budget_raises(self):
        with pytest.raises(ValueError):
            TeamBudget(team="ads", monthly_budget_usd=-100)

    def test_alert_threshold_above_100_raises(self):
        with pytest.raises(ValueError):
            TeamBudget(team="ads", monthly_budget_usd=1000, alert_threshold_percent=110)

    def test_hard_limit_below_alert_threshold_raises(self):
        with pytest.raises(ValueError):
            TeamBudget(team="ads", monthly_budget_usd=1000,
                       alert_threshold_percent=90, hard_limit_percent=70)


class TestCloudSpendRecord:
    def test_valid_aws_record(self):
        record = CloudSpendRecord(team="ads", cloud="aws",
                                   service="EKS", amount_usd=1200.0, month="2026-03")
        assert record.cloud == "aws"

    def test_valid_gcp_record(self):
        record = CloudSpendRecord(team="ads", cloud="gcp",
                                   service="GKE", amount_usd=400.0, month="2026-03")
        assert record.cloud == "gcp"

    def test_invalid_cloud_raises(self):
        with pytest.raises(ValueError, match="cloud must be"):
            CloudSpendRecord(team="ads", cloud="azure",
                             service="AKS", amount_usd=100.0, month="2026-03")

    def test_negative_amount_raises(self):
        with pytest.raises(ValueError):
            CloudSpendRecord(team="ads", cloud="aws",
                             service="EC2", amount_usd=-50.0, month="2026-03")


class TestFinOpsDashboard:
    def setup_method(self):
        self.dashboard = FinOpsDashboard()
        self.dashboard.register_team_budget(
            TeamBudget(team="ads", monthly_budget_usd=5000.0, alert_threshold_percent=80.0)
        )
        self.dashboard.record_spend(
            CloudSpendRecord(team="ads", cloud="aws", service="EKS",
                             amount_usd=2000.0, month="2026-03")
        )
        self.dashboard.record_spend(
            CloudSpendRecord(team="ads", cloud="gcp", service="GKE",
                             amount_usd=500.0, month="2026-03")
        )

    def test_get_team_spend_total(self):
        spend = self.dashboard.get_team_spend("ads", "2026-03")
        assert abs(spend - 2500.0) < 0.01

    def test_get_team_spend_aws_only(self):
        spend = self.dashboard.get_team_spend("ads", "2026-03", cloud="aws")
        assert abs(spend - 2000.0) < 0.01

    def test_get_team_spend_gcp_only(self):
        spend = self.dashboard.get_team_spend("ads", "2026-03", cloud="gcp")
        assert abs(spend - 500.0) < 0.01

    def test_budget_utilization_ok_status(self):
        util = self.dashboard.get_budget_utilization("ads", "2026-03")
        assert util is not None
        assert util["status"] == "ok"  # 2500/5000 = 50%, below 80% threshold

    def test_budget_utilization_warning_status(self):
        self.dashboard.record_spend(
            CloudSpendRecord(team="ads", cloud="aws", service="RDS",
                             amount_usd=2100.0, month="2026-03")  # Total = 4600
        )
        util = self.dashboard.get_budget_utilization("ads", "2026-03")
        assert util["status"] == "warning"  # 4600/5000 = 92% > 80%

    def test_budget_utilization_over_budget_status(self):
        self.dashboard.record_spend(
            CloudSpendRecord(team="ads", cloud="aws", service="Data Transfer",
                             amount_usd=3000.0, month="2026-03")  # Total = 5500
        )
        util = self.dashboard.get_budget_utilization("ads", "2026-03")
        assert util["status"] == "over_budget"

    def test_anomaly_detection_critical(self):
        # Baseline: 2500, current: 2500*2 = 5000 = 100% increase
        for cloud, service, amount in [("aws", "EKS", 2500.0), ("gcp", "GKE", 2500.0)]:
            self.dashboard.record_spend(
                CloudSpendRecord(team="ads", cloud=cloud, service=service,
                                 amount_usd=amount, month="2026-04")
            )
        anomalies = self.dashboard.detect_anomalies("2026-04", "2026-03")
        assert any(a.severity == "critical" for a in anomalies)

    def test_no_anomaly_for_stable_spend(self):
        # Same spend as baseline
        self.dashboard.record_spend(
            CloudSpendRecord(team="ads", cloud="aws", service="EKS",
                             amount_usd=2000.0, month="2026-04")
        )
        self.dashboard.record_spend(
            CloudSpendRecord(team="ads", cloud="gcp", service="GKE",
                             amount_usd=500.0, month="2026-04")
        )
        anomalies = self.dashboard.detect_anomalies("2026-04", "2026-03")
        assert len(anomalies) == 0

    def test_cloud_split_shows_aws_and_gcp(self):
        split = self.dashboard.get_cloud_split("ads", "2026-03")
        assert split["aws_usd"] == 2000.0
        assert split["gcp_usd"] == 500.0
        assert split["total_usd"] == 2500.0

    def test_cloud_split_percentages_sum_to_100(self):
        split = self.dashboard.get_cloud_split("ads", "2026-03")
        assert abs(split["aws_percent"] + split["gcp_percent"] - 100) < 0.1

    def test_top_spenders_returns_correct_order(self):
        self.dashboard.register_team_budget(
            TeamBudget(team="data", monthly_budget_usd=3000.0)
        )
        self.dashboard.record_spend(
            CloudSpendRecord(team="data", cloud="gcp", service="BigQuery",
                             amount_usd=1000.0, month="2026-03")
        )
        top = self.dashboard.get_top_spenders("2026-03", n=2)
        assert len(top) == 2
        assert top[0]["team"] == "ads"  # 2500 > 1000
