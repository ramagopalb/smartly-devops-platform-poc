"""
Tests for ArgoCD ApplicationSet configuration.
Validates GitOps deployment configs for Smartly multi-cluster platform.
"""
import os
import pytest
import yaml


ARGOCD_DIR = os.path.join(os.path.dirname(__file__), "..", "argocd")


def load_yaml(filename):
    path = os.path.join(ARGOCD_DIR, filename)
    with open(path) as f:
        return list(yaml.safe_load_all(f))


# --- ApplicationSet EKS Tests ---

class TestApplicationSetEKS:
    def setup_method(self):
        self.docs = load_yaml("applicationset-eks.yaml")
        self.appset = self.docs[0]

    def test_api_version_correct(self):
        assert self.appset["apiVersion"] == "argoproj.io/v1alpha1"

    def test_kind_is_application_set(self):
        assert self.appset["kind"] == "ApplicationSet"

    def test_name_is_set(self):
        assert self.appset["metadata"]["name"] == "smartly-ad-platform-eks"

    def test_namespace_is_argocd(self):
        assert self.appset["metadata"]["namespace"] == "argocd"

    def test_has_list_generator(self):
        generators = self.appset["spec"]["generators"]
        assert len(generators) > 0
        assert "list" in generators[0]

    def test_has_three_environments(self):
        elements = self.appset["spec"]["generators"][0]["list"]["elements"]
        envs = {e["env"] for e in elements}
        assert envs == {"prod", "staging", "dev"}

    def test_prod_has_highest_replicas(self):
        elements = self.appset["spec"]["generators"][0]["list"]["elements"]
        prod = next(e for e in elements if e["env"] == "prod")
        dev = next(e for e in elements if e["env"] == "dev")
        assert int(prod["replicaCount"]) > int(dev["replicaCount"])

    def test_prod_has_most_kafka_partitions(self):
        elements = self.appset["spec"]["generators"][0]["list"]["elements"]
        prod = next(e for e in elements if e["env"] == "prod")
        dev = next(e for e in elements if e["env"] == "dev")
        assert int(prod["kafkaPartitions"]) > int(dev["kafkaPartitions"])

    def test_automated_sync_enabled(self):
        sync = self.appset["spec"]["template"]["spec"]["syncPolicy"]["automated"]
        assert sync["prune"] is True
        assert sync["selfHeal"] is True

    def test_create_namespace_sync_option(self):
        opts = self.appset["spec"]["template"]["spec"]["syncPolicy"]["syncOptions"]
        assert "CreateNamespace=true" in opts

    def test_retry_limit_configured(self):
        retry = self.appset["spec"]["template"]["spec"]["syncPolicy"]["retry"]
        assert retry["limit"] >= 3

    def test_destination_namespace_set(self):
        dest = self.appset["spec"]["template"]["spec"]["destination"]
        assert dest["namespace"] == "smartly-platform"

    def test_helm_value_files_include_env_override(self):
        helm = self.appset["spec"]["template"]["spec"]["source"]["helm"]
        assert any("{{env}}" in vf for vf in helm["valueFiles"])

    def test_ignore_differences_on_replicas(self):
        diffs = self.appset["spec"]["template"]["spec"]["ignoreDifferences"]
        replica_diff = next((d for d in diffs if "/spec/replicas" in d.get("jsonPointers", [])), None)
        assert replica_diff is not None

    def test_all_clusters_have_region(self):
        elements = self.appset["spec"]["generators"][0]["list"]["elements"]
        for elem in elements:
            assert "region" in elem


# --- ApplicationSet GKE Tests ---

class TestApplicationSetGKE:
    def setup_method(self):
        self.docs = load_yaml("applicationset-gke.yaml")
        self.appset = self.docs[0]

    def test_api_version_correct(self):
        assert self.appset["apiVersion"] == "argoproj.io/v1alpha1"

    def test_kind_is_application_set(self):
        assert self.appset["kind"] == "ApplicationSet"

    def test_name_is_gke(self):
        assert "gke" in self.appset["metadata"]["name"]

    def test_has_list_generator(self):
        generators = self.appset["spec"]["generators"]
        assert "list" in generators[0]

    def test_has_prod_and_staging(self):
        elements = self.appset["spec"]["generators"][0]["list"]["elements"]
        envs = {e["env"] for e in elements}
        assert "prod" in envs
        assert "staging" in envs

    def test_gcp_cloud_label(self):
        labels = self.appset["spec"]["template"]["metadata"]["labels"]
        assert labels["cloud"] == "gcp"

    def test_server_side_apply_option(self):
        opts = self.appset["spec"]["template"]["spec"]["syncPolicy"]["syncOptions"]
        assert "ServerSideApply=true" in opts


# --- Rollout Canary Tests ---

class TestRolloutCanary:
    def setup_method(self):
        self.docs = load_yaml("rollout-canary.yaml")
        self.rollout = self.docs[0]
        self.error_analysis = self.docs[1]
        self.latency_analysis = self.docs[2]
        self.throughput_analysis = self.docs[3]

    def test_rollout_kind(self):
        assert self.rollout["kind"] == "Rollout"

    def test_canary_strategy_configured(self):
        strategy = self.rollout["spec"]["strategy"]
        assert "canary" in strategy

    def test_canary_starts_at_5_percent(self):
        steps = self.rollout["spec"]["strategy"]["canary"]["steps"]
        first_weight = steps[0]["setWeight"]
        assert first_weight == 5

    def test_canary_ends_at_100_percent(self):
        steps = self.rollout["spec"]["strategy"]["canary"]["steps"]
        weights = [s["setWeight"] for s in steps if "setWeight" in s]
        assert max(weights) == 100

    def test_analysis_templates_used(self):
        steps = self.rollout["spec"]["strategy"]["canary"]["steps"]
        analysis_steps = [s for s in steps if "analysis" in s]
        assert len(analysis_steps) >= 2

    def test_istio_traffic_routing(self):
        traffic = self.rollout["spec"]["strategy"]["canary"]["trafficRouting"]
        assert "istio" in traffic

    def test_error_rate_analysis_template(self):
        assert self.error_analysis["kind"] == "AnalysisTemplate"
        assert self.error_analysis["metadata"]["name"] == "error-rate-check"

    def test_latency_analysis_template(self):
        assert self.latency_analysis["kind"] == "AnalysisTemplate"
        assert self.latency_analysis["metadata"]["name"] == "latency-p99-check"

    def test_throughput_analysis_template(self):
        assert self.throughput_analysis["kind"] == "AnalysisTemplate"

    def test_slo_tier_label(self):
        labels = self.rollout["metadata"]["labels"]
        assert labels.get("slo-tier") == "critical"
