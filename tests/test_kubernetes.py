"""
Tests for Kubernetes resource configurations.
Validates Helm chart values and OPA-style policy checks.
"""
import os
import pytest
import yaml


HELM_DIR = os.path.join(os.path.dirname(__file__), "..", "helm", "ad-platform")


def load_values(filename="values.yaml"):
    path = os.path.join(HELM_DIR, filename)
    with open(path) as f:
        return yaml.safe_load(f)


# --- Helm Values Tests ---

class TestHelmValues:
    def setup_method(self):
        self.values = load_values()

    def test_global_section_exists(self):
        assert "global" in self.values

    def test_global_cloud_set(self):
        assert self.values["global"]["cloud"] in ("aws", "gcp")

    def test_global_region_set(self):
        assert self.values["global"]["region"] != ""

    def test_global_environment_set(self):
        assert self.values["global"]["environment"] in ("dev", "staging", "prod")

    def test_replica_count_positive(self):
        assert self.values["replicaCount"] >= 1

    def test_image_tag_set(self):
        assert self.values["image"]["tag"] != ""
        assert self.values["image"]["tag"] is not None

    def test_resource_requests_set(self):
        requests = self.values["resources"]["requests"]
        assert "cpu" in requests
        assert "memory" in requests

    def test_resource_limits_set(self):
        limits = self.values["resources"]["limits"]
        assert "cpu" in limits
        assert "memory" in limits

    def test_autoscaling_enabled(self):
        assert self.values["autoscaling"]["enabled"] is True

    def test_autoscaling_max_greater_than_min(self):
        autos = self.values["autoscaling"]
        assert autos["maxReplicas"] > autos["minReplicas"]

    def test_keda_enabled(self):
        assert self.values["keda"]["enabled"] is True

    def test_keda_kafka_config(self):
        keda = self.values["keda"]
        assert keda["kafkaTopic"] != ""
        assert keda["kafkaConsumerGroup"] != ""
        assert keda["lagThreshold"] > 0

    def test_pdb_enabled(self):
        assert self.values["podDisruptionBudget"]["enabled"] is True

    def test_prometheus_scrape_annotation(self):
        annotations = self.values["podAnnotations"]
        assert annotations.get("prometheus.io/scrape") == "true"

    def test_network_policy_enabled(self):
        assert self.values["networkPolicy"]["enabled"] is True

    def test_slo_error_rate_threshold(self):
        assert self.values["slo"]["errorRateThreshold"] < 0.01

    def test_slo_availability_target_high(self):
        assert self.values["slo"]["availabilityTarget"] >= 99.9

    def test_istio_mtls_enabled(self):
        assert self.values["istio"]["enabled"] is True
        assert self.values["istio"]["mtls"] is True


# --- OPA Policy Simulation Tests ---

class TestOPAKubernetesPolicies:
    """Simulate OPA Gatekeeper admission control policies."""

    def _make_pod_spec(self, image="smartly/app:v1.0.0",
                       requests=None, limits=None,
                       read_only_root=True,
                       run_as_non_root=True,
                       run_as_user=1000):
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "test-pod", "namespace": "smartly-platform",
                         "labels": {"app": "test", "team": "platform"}},
            "spec": {
                "containers": [{
                    "name": "app",
                    "image": image,
                    "resources": {
                        "requests": requests or {"cpu": "100m", "memory": "128Mi"},
                        "limits": limits or {"cpu": "500m", "memory": "512Mi"},
                    },
                    "securityContext": {
                        "readOnlyRootFilesystem": read_only_root,
                        "runAsNonRoot": run_as_non_root,
                        "runAsUser": run_as_user,
                        "allowPrivilegeEscalation": False,
                    }
                }]
            }
        }

    def _has_required_labels(self, pod: dict) -> bool:
        required = {"app", "team"}
        labels = pod["metadata"].get("labels", {})
        return required.issubset(set(labels.keys()))

    def _has_resource_limits(self, pod: dict) -> bool:
        for container in pod["spec"]["containers"]:
            resources = container.get("resources", {})
            if "limits" not in resources:
                return False
            limits = resources["limits"]
            if "cpu" not in limits or "memory" not in limits:
                return False
        return True

    def _is_non_root(self, pod: dict) -> bool:
        for container in pod["spec"]["containers"]:
            sc = container.get("securityContext", {})
            if not sc.get("runAsNonRoot", False):
                return False
            if sc.get("runAsUser", 0) == 0:
                return False
        return True

    def _no_privilege_escalation(self, pod: dict) -> bool:
        for container in pod["spec"]["containers"]:
            sc = container.get("securityContext", {})
            if sc.get("allowPrivilegeEscalation", True):
                return False
        return True

    def test_valid_pod_passes_all_policies(self):
        pod = self._make_pod_spec()
        assert self._has_required_labels(pod)
        assert self._has_resource_limits(pod)
        assert self._is_non_root(pod)
        assert self._no_privilege_escalation(pod)

    def test_missing_team_label_fails(self):
        pod = self._make_pod_spec()
        pod["metadata"]["labels"] = {"app": "test"}  # no team
        assert not self._has_required_labels(pod)

    def test_missing_cpu_limit_fails(self):
        pod = self._make_pod_spec(limits={"memory": "512Mi"})
        assert not self._has_resource_limits(pod)

    def test_root_user_fails(self):
        pod = self._make_pod_spec(run_as_non_root=False, run_as_user=0)
        assert not self._is_non_root(pod)

    def test_privilege_escalation_fails(self):
        pod = self._make_pod_spec()
        pod["spec"]["containers"][0]["securityContext"]["allowPrivilegeEscalation"] = True
        assert not self._no_privilege_escalation(pod)

    def test_latest_tag_detection(self):
        """Images with :latest tag should be rejected."""
        pod = self._make_pod_spec(image="smartly/app:latest")
        image = pod["spec"]["containers"][0]["image"]
        assert image.endswith(":latest")  # This would trigger OPA deny
