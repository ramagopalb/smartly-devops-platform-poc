"""
Tests for GitOpsPipeline — Smartly DevOps Platform POC.
Covers ApplicationSet registration, sync lifecycle, rollout strategies,
progressive delivery, image update automation, and pipeline summary.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "platform"))
import pytest
from gitops_pipeline import (
    GitOpsPipeline,
    ApplicationSet,
    SyncPolicy,
    RolloutStrategy,
    SyncStatus,
    HealthStatus,
    RolloutPhase,
    DeliveryStrategy,
)


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def pipeline():
    return GitOpsPipeline()


@pytest.fixture
def sync_policy():
    return SyncPolicy(
        automated=True,
        prune=True,
        self_heal=True,
        retry_limit=5,
        retry_backoff_seconds=5,
    )


@pytest.fixture
def canary_rollout():
    return RolloutStrategy.default_canary("ad-delivery")


@pytest.fixture
def ad_delivery_appset(sync_policy, canary_rollout):
    return ApplicationSet(
        name="ad-delivery",
        repo_url="https://github.com/ramagopalb/smartly-devops-platform-poc",
        chart_path="helm/ad-platform",
        clusters=["eks-eu-west-1", "gke-europe-west1"],
        environments=["dev", "staging", "prod"],
        target_revision="HEAD",
        sync_policy=sync_policy,
        rollout=canary_rollout,
        namespace_prefix="adplatform",
        project="smartly",
    )


@pytest.fixture
def simple_appset():
    return ApplicationSet(
        name="campaign-api",
        repo_url="https://github.com/ramagopalb/smartly-devops-platform-poc",
        chart_path="helm/campaign-api",
        clusters=["eks-eu-west-1"],
        environments=["dev", "prod"],
    )


# ------------------------------------------------------------------ #
#  SyncPolicy tests (8 tests)
# ------------------------------------------------------------------ #

class TestSyncPolicy:

    def test_argocd_dict_has_automated(self, sync_policy):
        d = sync_policy.to_argocd_dict()
        assert "automated" in d

    def test_automated_prune_enabled(self, sync_policy):
        d = sync_policy.to_argocd_dict()
        assert d["automated"]["prune"] is True

    def test_automated_self_heal_enabled(self, sync_policy):
        d = sync_policy.to_argocd_dict()
        assert d["automated"]["selfHeal"] is True

    def test_retry_limit(self, sync_policy):
        d = sync_policy.to_argocd_dict()
        assert d["retry"]["limit"] == 5

    def test_retry_backoff_duration(self, sync_policy):
        d = sync_policy.to_argocd_dict()
        assert d["retry"]["backoff"]["duration"] == "5s"

    def test_manual_sync_policy(self):
        policy = SyncPolicy(automated=False)
        d = policy.to_argocd_dict()
        assert "automated" not in d
        assert "retry" in d

    def test_invalid_retry_limit_raises(self):
        with pytest.raises(ValueError, match="retry_limit must be"):
            SyncPolicy(retry_limit=-1)

    def test_invalid_backoff_raises(self):
        with pytest.raises(ValueError, match="retry_backoff_seconds must be"):
            SyncPolicy(retry_backoff_seconds=0)


# ------------------------------------------------------------------ #
#  RolloutStrategy tests (8 tests)
# ------------------------------------------------------------------ #

class TestRolloutStrategy:

    def test_default_canary_strategy(self, canary_rollout):
        assert canary_rollout.strategy == DeliveryStrategy.CANARY

    def test_default_canary_has_steps(self, canary_rollout):
        assert canary_rollout.step_count > 0

    def test_default_canary_has_prometheus_gate(self, canary_rollout):
        assert canary_rollout.has_prometheus_gate() is True

    def test_default_canary_prometheus_query_contains_service(self, canary_rollout):
        assert "ad-delivery" in canary_rollout.prometheus_query

    def test_canary_step_count(self, canary_rollout):
        # default_canary creates 5 steps
        assert canary_rollout.step_count == 5

    def test_rolling_strategy(self):
        r = RolloutStrategy(strategy=DeliveryStrategy.ROLLING)
        assert r.strategy == DeliveryStrategy.ROLLING
        assert r.has_prometheus_gate() is False

    def test_invalid_error_rate_threshold_raises(self):
        with pytest.raises(ValueError, match="error_rate_threshold must be"):
            RolloutStrategy(error_rate_threshold=0.0)

    def test_invalid_analysis_interval_raises(self):
        with pytest.raises(ValueError, match="analysis_interval_seconds must be"):
            RolloutStrategy(analysis_interval_seconds=5)


# ------------------------------------------------------------------ #
#  ApplicationSet tests (10 tests)
# ------------------------------------------------------------------ #

class TestApplicationSet:

    def test_application_count(self, ad_delivery_appset):
        # 2 clusters × 3 environments = 6 apps
        assert ad_delivery_appset.application_count == 6

    def test_has_progressive_delivery(self, ad_delivery_appset):
        assert ad_delivery_appset.has_progressive_delivery is True

    def test_no_progressive_delivery(self, simple_appset):
        assert simple_appset.has_progressive_delivery is False

    def test_argocd_manifest_kind(self, ad_delivery_appset):
        manifest = ad_delivery_appset.to_argocd_manifest()
        assert manifest["kind"] == "ApplicationSet"

    def test_argocd_manifest_name(self, ad_delivery_appset):
        manifest = ad_delivery_appset.to_argocd_manifest()
        assert manifest["metadata"]["name"] == "ad-delivery"

    def test_argocd_manifest_namespace(self, ad_delivery_appset):
        manifest = ad_delivery_appset.to_argocd_manifest()
        assert manifest["metadata"]["namespace"] == "argocd"

    def test_argocd_manifest_repo_url(self, ad_delivery_appset):
        manifest = ad_delivery_appset.to_argocd_manifest()
        source = manifest["spec"]["template"]["spec"]["source"]
        assert source["repoURL"] == "https://github.com/ramagopalb/smartly-devops-platform-poc"

    def test_argocd_manifest_has_generators(self, ad_delivery_appset):
        manifest = ad_delivery_appset.to_argocd_manifest()
        assert len(manifest["spec"]["generators"]) > 0

    def test_invalid_empty_name_raises(self):
        with pytest.raises(ValueError, match="ApplicationSet name cannot be empty"):
            ApplicationSet(name="", repo_url="https://repo", chart_path="helm/app",
                           clusters=["cluster1"])

    def test_invalid_no_clusters_raises(self):
        with pytest.raises(ValueError, match="At least one cluster"):
            ApplicationSet(name="app", repo_url="https://repo", chart_path="helm/app",
                           clusters=[])


# ------------------------------------------------------------------ #
#  GitOpsPipeline — registration tests (8 tests)
# ------------------------------------------------------------------ #

class TestGitOpsPipelineRegistration:

    def test_register_appset(self, pipeline, ad_delivery_appset):
        result = pipeline.register_application_set(ad_delivery_appset)
        assert result["name"] == "ad-delivery"
        assert result["application_count"] == 6

    def test_pipeline_appset_count(self, pipeline, ad_delivery_appset, simple_appset):
        pipeline.register_application_set(ad_delivery_appset)
        pipeline.register_application_set(simple_appset)
        assert pipeline.application_set_count() == 2

    def test_list_application_sets(self, pipeline, ad_delivery_appset, simple_appset):
        pipeline.register_application_set(ad_delivery_appset)
        pipeline.register_application_set(simple_appset)
        names = pipeline.list_application_sets()
        assert "ad-delivery" in names
        assert "campaign-api" in names

    def test_initial_sync_status_out_of_sync(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        status = pipeline.get_sync_status("ad-delivery", "eks-eu-west-1", "dev")
        assert status == SyncStatus.OUT_OF_SYNC

    def test_initial_health_status_progressing(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        status = pipeline.get_health_status("ad-delivery", "eks-eu-west-1", "dev")
        assert status == HealthStatus.PROGRESSING

    def test_initial_rollout_phase_canary(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        phase = pipeline.get_rollout_phase("ad-delivery", "eks-eu-west-1", "prod")
        assert phase == RolloutPhase.CANARY

    def test_get_application_set(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        fetched = pipeline.get_application_set("ad-delivery")
        assert fetched is not None
        assert fetched.name == "ad-delivery"

    def test_get_nonexistent_appset(self, pipeline):
        assert pipeline.get_application_set("nonexistent") is None


# ------------------------------------------------------------------ #
#  GitOpsPipeline — sync & health tests (10 tests)
# ------------------------------------------------------------------ #

class TestGitOpsPipelineSync:

    def test_sync_application_success(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        result = pipeline.sync_application("ad-delivery", "eks-eu-west-1", "dev")
        assert result["success"] is True
        assert result["sync_status"] == SyncStatus.SYNCED.value

    def test_sync_sets_healthy_status(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        pipeline.sync_application("ad-delivery", "eks-eu-west-1", "dev")
        health = pipeline.get_health_status("ad-delivery", "eks-eu-west-1", "dev")
        assert health == HealthStatus.HEALTHY

    def test_sync_sets_synced_status(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        pipeline.sync_application("ad-delivery", "eks-eu-west-1", "dev")
        status = pipeline.get_sync_status("ad-delivery", "eks-eu-west-1", "dev")
        assert status == SyncStatus.SYNCED

    def test_sync_nonexistent_appset(self, pipeline):
        result = pipeline.sync_application("nonexistent", "cluster", "dev")
        assert result["success"] is False

    def test_out_of_sync_apps_initially_all(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        out_of_sync = pipeline.get_out_of_sync_apps()
        assert len(out_of_sync) == 6

    def test_out_of_sync_decreases_after_sync(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        pipeline.sync_application("ad-delivery", "eks-eu-west-1", "dev")
        out_of_sync = pipeline.get_out_of_sync_apps()
        assert len(out_of_sync) == 5

    def test_abort_rollout(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        result = pipeline.abort_rollout("ad-delivery", "eks-eu-west-1", "prod")
        assert result["success"] is True
        assert result["phase"] == RolloutPhase.ABORTED.value

    def test_promote_rollout(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        result = pipeline.promote_rollout("ad-delivery", "eks-eu-west-1", "prod")
        assert result["success"] is True
        assert result["phase"] == RolloutPhase.STABLE.value

    def test_promote_aborted_rollout_fails(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        pipeline.abort_rollout("ad-delivery", "eks-eu-west-1", "prod")
        result = pipeline.promote_rollout("ad-delivery", "eks-eu-west-1", "prod")
        assert result["success"] is False

    def test_promote_nonexistent_rollout_fails(self, pipeline):
        result = pipeline.promote_rollout("nonexistent", "cluster", "prod")
        assert result["success"] is False


# ------------------------------------------------------------------ #
#  GitOpsPipeline — image update & summary (7 tests)
# ------------------------------------------------------------------ #

class TestGitOpsPipelineImageUpdate:

    def test_image_update_triggers_out_of_sync(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        # Sync all first
        for cluster in ad_delivery_appset.clusters:
            for env in ad_delivery_appset.environments:
                pipeline.sync_application("ad-delivery", cluster, env)
        # Now trigger image update
        result = pipeline.update_image_tag("ad-delivery", "v2.0.0")
        assert result["success"] is True
        assert result["apps_triggered"] == 6

    def test_image_update_new_tag(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        result = pipeline.update_image_tag("ad-delivery", "v2.0.0")
        assert result["new_tag"] == "v2.0.0"

    def test_image_update_nonexistent_appset(self, pipeline):
        result = pipeline.update_image_tag("nonexistent", "v1.0.0")
        assert result["success"] is False

    def test_pipeline_summary_structure(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        summary = pipeline.pipeline_summary()
        assert "total_application_sets" in summary
        assert "total_applications" in summary
        assert "synced" in summary
        assert "healthy" in summary

    def test_pipeline_summary_counts(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        summary = pipeline.pipeline_summary()
        assert summary["total_application_sets"] == 1
        assert summary["total_applications"] == 6
        assert summary["synced"] == 0

    def test_unhealthy_apps_initially_all(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        unhealthy = pipeline.get_unhealthy_apps()
        assert len(unhealthy) == 6

    def test_unhealthy_decreases_after_sync(self, pipeline, ad_delivery_appset):
        pipeline.register_application_set(ad_delivery_appset)
        pipeline.sync_application("ad-delivery", "eks-eu-west-1", "dev")
        unhealthy = pipeline.get_unhealthy_apps()
        assert len(unhealthy) == 5
