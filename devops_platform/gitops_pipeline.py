"""
GitOps Pipeline Orchestrator for Smartly Ad-Tech Platform.
Manages ArgoCD ApplicationSets, sync policies, rollout strategies,
and progressive delivery for multi-cluster ad-tech deployments.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class SyncStatus(str, Enum):
    SYNCED = "Synced"
    OUT_OF_SYNC = "OutOfSync"
    UNKNOWN = "Unknown"
    PROGRESSING = "Progressing"


class HealthStatus(str, Enum):
    HEALTHY = "Healthy"
    DEGRADED = "Degraded"
    PROGRESSING = "Progressing"
    SUSPENDED = "Suspended"
    MISSING = "Missing"


class RolloutPhase(str, Enum):
    STABLE = "stable"
    CANARY = "canary"
    PAUSED = "paused"
    ABORTED = "aborted"
    COMPLETED = "completed"


class DeliveryStrategy(str, Enum):
    ROLLING = "rolling"
    CANARY = "canary"
    BLUE_GREEN = "blue-green"


@dataclass
class SyncPolicy:
    """ArgoCD sync policy configuration."""
    automated: bool = True
    prune: bool = True
    self_heal: bool = True
    retry_limit: int = 5
    retry_backoff_seconds: int = 5
    retry_max_duration_seconds: int = 300
    apply_out_of_sync_only: bool = True

    def __post_init__(self):
        if self.retry_limit < 0:
            raise ValueError("retry_limit must be >= 0")
        if self.retry_backoff_seconds < 1:
            raise ValueError("retry_backoff_seconds must be >= 1")

    def to_argocd_dict(self) -> dict:
        """Serialise to ArgoCD SyncPolicy spec."""
        policy: dict = {}
        if self.automated:
            policy["automated"] = {
                "prune": self.prune,
                "selfHeal": self.self_heal,
                "applyOutOfSyncOnly": self.apply_out_of_sync_only,
            }
        policy["retry"] = {
            "limit": self.retry_limit,
            "backoff": {
                "duration": f"{self.retry_backoff_seconds}s",
                "maxDuration": f"{self.retry_max_duration_seconds}s",
                "factor": 2,
            },
        }
        return policy


@dataclass
class RolloutStrategy:
    """Progressive delivery rollout strategy (Argo Rollouts)."""
    strategy: DeliveryStrategy = DeliveryStrategy.CANARY
    canary_steps: list[dict] = field(default_factory=list)
    max_surge: str = "25%"
    max_unavailable: int = 0
    prometheus_query: Optional[str] = None
    error_rate_threshold: float = 0.01
    analysis_interval_seconds: int = 60

    def __post_init__(self):
        if not (0 < self.error_rate_threshold <= 1):
            raise ValueError("error_rate_threshold must be between 0 and 1")
        if self.analysis_interval_seconds < 10:
            raise ValueError("analysis_interval_seconds must be >= 10")

    @classmethod
    def default_canary(cls, service_name: str) -> "RolloutStrategy":
        """Factory: standard 3-step canary rollout with Prometheus gate."""
        return cls(
            strategy=DeliveryStrategy.CANARY,
            canary_steps=[
                {"setWeight": 10},
                {"pause": {"duration": "2m"}},
                {"setWeight": 50},
                {"pause": {"duration": "5m"}},
                {"setWeight": 100},
            ],
            prometheus_query=(
                f"sum(rate(http_requests_total{{service='{service_name}',status=~'5..'}}[5m])) "
                f"/ sum(rate(http_requests_total{{service='{service_name}'}}[5m]))"
            ),
            error_rate_threshold=0.01,
        )

    @property
    def step_count(self) -> int:
        return len(self.canary_steps)

    def has_prometheus_gate(self) -> bool:
        return self.prometheus_query is not None


@dataclass
class ApplicationSet:
    """ArgoCD ApplicationSet for multi-cluster, multi-environment deployments."""
    name: str
    repo_url: str
    chart_path: str
    clusters: list[str]
    environments: list[str] = field(default_factory=lambda: ["dev", "staging", "prod"])
    target_revision: str = "HEAD"
    sync_policy: Optional[SyncPolicy] = None
    rollout: Optional[RolloutStrategy] = None
    namespace_prefix: str = ""
    project: str = "default"

    def __post_init__(self):
        if not self.name:
            raise ValueError("ApplicationSet name cannot be empty")
        if not self.repo_url:
            raise ValueError("repo_url cannot be empty")
        if not self.clusters:
            raise ValueError("At least one cluster must be specified")
        if not self.environments:
            raise ValueError("At least one environment must be specified")
        if not self.chart_path:
            raise ValueError("chart_path cannot be empty")

    @property
    def application_count(self) -> int:
        """Total applications generated (clusters × environments)."""
        return len(self.clusters) * len(self.environments)

    @property
    def has_progressive_delivery(self) -> bool:
        return self.rollout is not None

    def to_argocd_manifest(self) -> dict:
        """Generate the ArgoCD ApplicationSet manifest."""
        generators = [
            {
                "matrix": {
                    "generators": [
                        {
                            "list": {
                                "elements": [
                                    {"cluster": c} for c in self.clusters
                                ]
                            }
                        },
                        {
                            "list": {
                                "elements": [
                                    {"env": e} for e in self.environments
                                ]
                            }
                        },
                    ]
                }
            }
        ]

        sync_policy_dict = {}
        if self.sync_policy:
            sync_policy_dict = self.sync_policy.to_argocd_dict()

        ns_name = (
            f"{self.namespace_prefix}-{{{{env}}}}"
            if self.namespace_prefix
            else f"{self.name}-{{{{env}}}}"
        )

        return {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "ApplicationSet",
            "metadata": {
                "name": self.name,
                "namespace": "argocd",
            },
            "spec": {
                "generators": generators,
                "template": {
                    "metadata": {
                        "name": f"{self.name}-{{{{cluster}}}}-{{{{env}}}}",
                    },
                    "spec": {
                        "project": self.project,
                        "source": {
                            "repoURL": self.repo_url,
                            "targetRevision": self.target_revision,
                            "path": self.chart_path,
                            "helm": {
                                "valueFiles": ["values-{{env}}.yaml"],
                            },
                        },
                        "destination": {
                            "server": "https://{{cluster}}",
                            "namespace": ns_name,
                        },
                        "syncPolicy": sync_policy_dict,
                    },
                },
            },
        }


class GitOpsPipeline:
    """
    GitOps pipeline orchestrator for Smartly's multi-cluster platform.
    Manages ArgoCD ApplicationSets, progressive delivery, and sync lifecycle.
    """

    MAX_APPLICATION_SETS = 50

    def __init__(self):
        self._application_sets: dict[str, ApplicationSet] = {}
        self._sync_statuses: dict[str, SyncStatus] = {}
        self._health_statuses: dict[str, HealthStatus] = {}
        self._rollout_phases: dict[str, RolloutPhase] = {}

    # ------------------------------------------------------------------ #
    #  ApplicationSet management
    # ------------------------------------------------------------------ #

    def register_application_set(self, appset: ApplicationSet) -> dict:
        """Register an ApplicationSet for management."""
        if len(self._application_sets) >= self.MAX_APPLICATION_SETS:
            raise RuntimeError(f"Cannot manage more than {self.MAX_APPLICATION_SETS} ApplicationSets")
        self._application_sets[appset.name] = appset
        # Initialise all generated apps as OutOfSync until first sync
        for cluster in appset.clusters:
            for env in appset.environments:
                app_key = f"{appset.name}/{cluster}/{env}"
                self._sync_statuses[app_key] = SyncStatus.OUT_OF_SYNC
                self._health_statuses[app_key] = HealthStatus.PROGRESSING
                if appset.has_progressive_delivery:
                    self._rollout_phases[app_key] = RolloutPhase.CANARY
        logger.info(
            "Registered ApplicationSet %s: %d applications across %d clusters",
            appset.name, appset.application_count, len(appset.clusters),
        )
        return {
            "name": appset.name,
            "application_count": appset.application_count,
            "clusters": appset.clusters,
            "environments": appset.environments,
        }

    def sync_application(self, appset_name: str, cluster: str, env: str) -> dict:
        """Simulate syncing an application."""
        app_key = f"{appset_name}/{cluster}/{env}"
        if appset_name not in self._application_sets:
            return {"success": False, "message": f"ApplicationSet {appset_name!r} not found"}
        self._sync_statuses[app_key] = SyncStatus.SYNCED
        self._health_statuses[app_key] = HealthStatus.HEALTHY
        if app_key in self._rollout_phases:
            self._rollout_phases[app_key] = RolloutPhase.COMPLETED
        return {"success": True, "app_key": app_key, "sync_status": SyncStatus.SYNCED.value}

    def abort_rollout(self, appset_name: str, cluster: str, env: str) -> dict:
        """Abort a progressive delivery rollout."""
        app_key = f"{appset_name}/{cluster}/{env}"
        if app_key not in self._rollout_phases:
            return {"success": False, "message": f"No rollout found for {app_key!r}"}
        self._rollout_phases[app_key] = RolloutPhase.ABORTED
        self._health_statuses[app_key] = HealthStatus.DEGRADED
        return {"success": True, "app_key": app_key, "phase": RolloutPhase.ABORTED.value}

    def promote_rollout(self, appset_name: str, cluster: str, env: str) -> dict:
        """Promote a canary to full rollout."""
        app_key = f"{appset_name}/{cluster}/{env}"
        if app_key not in self._rollout_phases:
            return {"success": False, "message": f"No rollout found for {app_key!r}"}
        if self._rollout_phases[app_key] == RolloutPhase.ABORTED:
            return {"success": False, "message": "Cannot promote an aborted rollout"}
        self._rollout_phases[app_key] = RolloutPhase.STABLE
        self._sync_statuses[app_key] = SyncStatus.SYNCED
        self._health_statuses[app_key] = HealthStatus.HEALTHY
        return {"success": True, "app_key": app_key, "phase": RolloutPhase.STABLE.value}

    # ------------------------------------------------------------------ #
    #  Status queries
    # ------------------------------------------------------------------ #

    def get_sync_status(self, appset_name: str, cluster: str, env: str) -> Optional[SyncStatus]:
        return self._sync_statuses.get(f"{appset_name}/{cluster}/{env}")

    def get_health_status(self, appset_name: str, cluster: str, env: str) -> Optional[HealthStatus]:
        return self._health_statuses.get(f"{appset_name}/{cluster}/{env}")

    def get_rollout_phase(self, appset_name: str, cluster: str, env: str) -> Optional[RolloutPhase]:
        return self._rollout_phases.get(f"{appset_name}/{cluster}/{env}")

    def get_application_set(self, name: str) -> Optional[ApplicationSet]:
        return self._application_sets.get(name)

    def list_application_sets(self) -> list[str]:
        return sorted(self._application_sets.keys())

    def application_set_count(self) -> int:
        return len(self._application_sets)

    def get_unhealthy_apps(self) -> list[str]:
        """Return app keys where health is not HEALTHY."""
        return [
            k for k, v in self._health_statuses.items()
            if v != HealthStatus.HEALTHY
        ]

    def get_out_of_sync_apps(self) -> list[str]:
        """Return app keys that are not Synced."""
        return [
            k for k, v in self._sync_statuses.items()
            if v != SyncStatus.SYNCED
        ]

    # ------------------------------------------------------------------ #
    #  Image update automation
    # ------------------------------------------------------------------ #

    def update_image_tag(self, appset_name: str, new_tag: str) -> dict:
        """Simulate triggering an image update across all apps in an ApplicationSet."""
        if appset_name not in self._application_sets:
            return {"success": False, "message": f"ApplicationSet {appset_name!r} not found"}
        appset = self._application_sets[appset_name]
        updated = 0
        for cluster in appset.clusters:
            for env in appset.environments:
                app_key = f"{appset_name}/{cluster}/{env}"
                self._sync_statuses[app_key] = SyncStatus.OUT_OF_SYNC
                if appset.has_progressive_delivery and env == "prod":
                    self._rollout_phases[app_key] = RolloutPhase.CANARY
                updated += 1
        return {
            "success": True,
            "appset": appset_name,
            "new_tag": new_tag,
            "apps_triggered": updated,
        }

    # ------------------------------------------------------------------ #
    #  Pipeline summary
    # ------------------------------------------------------------------ #

    def pipeline_summary(self) -> dict:
        """Return high-level pipeline health summary."""
        total_apps = len(self._sync_statuses)
        synced = sum(1 for v in self._sync_statuses.values() if v == SyncStatus.SYNCED)
        healthy = sum(1 for v in self._health_statuses.values() if v == HealthStatus.HEALTHY)
        return {
            "total_application_sets": len(self._application_sets),
            "total_applications": total_apps,
            "synced": synced,
            "out_of_sync": total_apps - synced,
            "healthy": healthy,
            "unhealthy": total_apps - healthy,
        }
