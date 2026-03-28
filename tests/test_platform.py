"""
Tests for Platform Engineering components.
Covers Namespace Controller, FinOps Dashboard, and SLO Calculator.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "platform"))

from namespace_controller import NamespaceConfig, NamespaceController, NamespaceProvisionResult
from finops_dashboard import TeamBudget, CloudSpendRecord, FinOpsDashboard
from slo_calculator import SLOConfig, BurnRateWindow, SLOCalculator


# --- Namespace Controller Tests ---

class TestNamespaceConfig:
    def test_valid_config_creates_correctly(self):
        cfg = NamespaceConfig(team="ads", service="creative-delivery", environment="prod")
        assert cfg.team == "ads"
        assert cfg.service == "creative-delivery"
        assert cfg.environment == "prod"

    def test_namespace_name_format(self):
        cfg = NamespaceConfig(team="ads", service="creative", environment="staging")
        assert cfg.namespace_name == "ads-creative-staging"

    def test_invalid_environment_raises(self):
        with pytest.raises(ValueError, match="environment must be one of"):
            NamespaceConfig(team="ads", service="svc", environment="qa")

    def test_empty_team_raises(self):
        with pytest.raises(ValueError, match="team cannot be empty"):
            NamespaceConfig(team="", service="svc", environment="dev")

    def test_empty_service_raises(self):
        with pytest.raises(ValueError, match="service cannot be empty"):
            NamespaceConfig(team="ads", service="", environment="dev")

    def test_ephemeral_namespace_detected(self):
        cfg = NamespaceConfig(team="ads", service="preview", environment="dev", ttl_hours=24)
        assert cfg.is_ephemeral is True

    def test_permanent_namespace_not_ephemeral(self):
        cfg = NamespaceConfig(team="ads", service="prod-svc", environment="prod")
        assert cfg.is_ephemeral is False

    def test_negative_ttl_raises(self):
        with pytest.raises(ValueError, match="ttl_hours must be positive"):
            NamespaceConfig(team="ads", service="svc", environment="dev", ttl_hours=-1)

    def test_zero_ttl_raises(self):
        with pytest.raises(ValueError, match="ttl_hours must be positive"):
            NamespaceConfig(team="ads", service="svc", environment="dev", ttl_hours=0)


class TestNamespaceController:
    def setup_method(self):
        self.controller = NamespaceController()
        self.config = NamespaceConfig(team="ads", service="creative", environment="prod")

    def test_provision_creates_namespace(self):
        result = self.controller.provision(self.config)
        assert result.success is True
        assert result.action == "created"

    def test_provision_returns_resources_created(self):
        result = self.controller.provision(self.config)
        assert len(result.resources_created) > 0
        assert "Namespace" in result.resources_created

    def test_duplicate_provision_returns_already_exists(self):
        self.controller.provision(self.config)
        result2 = self.controller.provision(self.config)
        assert result2.action == "already_exists"
        assert result2.success is True

    def test_get_namespace_after_provision(self):
        self.controller.provision(self.config)
        retrieved = self.controller.get_namespace(self.config.namespace_name)
        assert retrieved is not None
        assert retrieved.team == "ads"

    def test_deprovision_removes_namespace(self):
        self.controller.provision(self.config)
        result = self.controller.deprovision(self.config.namespace_name)
        assert result.success is True
        assert result.action == "deleted"
        assert self.controller.get_namespace(self.config.namespace_name) is None

    def test_deprovision_nonexistent_fails(self):
        result = self.controller.deprovision("nonexistent-ns")
        assert result.success is False
        assert result.action == "not_found"

    def test_list_namespaces_by_team(self):
        cfg1 = NamespaceConfig(team="ads", service="svc1", environment="dev")
        cfg2 = NamespaceConfig(team="data", service="svc2", environment="dev")
        self.controller.provision(cfg1)
        self.controller.provision(cfg2)
        ads_nss = self.controller.list_namespaces(team="ads")
        assert len(ads_nss) == 1
        assert "ads-svc1-dev" in ads_nss

    def test_list_namespaces_by_environment(self):
        cfg1 = NamespaceConfig(team="ads", service="svc1", environment="prod")
        cfg2 = NamespaceConfig(team="ads", service="svc2", environment="dev")
        self.controller.provision(cfg1)
        self.controller.provision(cfg2)
        prod_nss = self.controller.list_namespaces(environment="prod")
        assert "ads-svc1-prod" in prod_nss
        assert "ads-svc2-dev" not in prod_nss

    def test_resource_quota_manifest_structure(self):
        manifest = self.controller.generate_resource_quota_manifest(self.config)
        assert manifest["kind"] == "ResourceQuota"
        assert "hard" in manifest["spec"]
        assert "requests.cpu" in manifest["spec"]["hard"]

    def test_network_policy_manifest_structure(self):
        manifest = self.controller.generate_network_policy_manifest(self.config)
        assert manifest["kind"] == "NetworkPolicy"
        assert "policyTypes" in manifest["spec"]
        assert "Ingress" in manifest["spec"]["policyTypes"]

    def test_long_namespace_name_rejected(self):
        cfg = NamespaceConfig(
            team="a" * 30,
            service="b" * 30,
            environment="dev"
        )
        result = self.controller.provision(cfg)
        assert result.success is False
        assert result.action == "rejected"

    def test_ephemeral_namespace_gets_ttl_resource(self):
        cfg = NamespaceConfig(team="ads", service="preview", environment="dev", ttl_hours=2)
        result = self.controller.provision(cfg)
        assert "TTLController/ExpiryAnnotation" in result.resources_created

    def test_list_ephemeral_expired(self):
        cfg = NamespaceConfig(team="ads", service="preview", environment="dev", ttl_hours=1)
        self.controller.provision(cfg)
        expired = self.controller.list_ephemeral_expired(current_hour_offset=2)
        assert cfg.namespace_name in expired
