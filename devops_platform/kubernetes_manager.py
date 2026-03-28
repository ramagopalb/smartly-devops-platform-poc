"""
Kubernetes Manager for Smartly Ad-Tech Platform.
Manages EKS and GKE cluster lifecycle, deployments, autoscaling,
and RBAC for high-throughput marketing platform workloads.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CloudProvider(str, Enum):
    AWS = "aws"
    GCP = "gcp"


class DeploymentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


class ScalingMode(str, Enum):
    HPA = "hpa"
    KEDA = "keda"
    KARPENTER = "karpenter"


@dataclass
class ClusterConfig:
    """Configuration for a managed Kubernetes cluster."""
    name: str
    provider: CloudProvider
    region: str
    node_min: int = 3
    node_max: int = 100
    node_instance_type: str = "m5.xlarge"
    kubernetes_version: str = "1.29"
    enable_spot: bool = True
    tags: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.name:
            raise ValueError("cluster name cannot be empty")
        if self.node_min < 1:
            raise ValueError("node_min must be at least 1")
        if self.node_max < self.node_min:
            raise ValueError("node_max must be >= node_min")
        if not self.region:
            raise ValueError("region cannot be empty")

    @property
    def cluster_id(self) -> str:
        return f"{self.provider.value}-{self.region}-{self.name}"

    @property
    def is_multi_az(self) -> bool:
        """Clusters with >3 nodes assumed to be multi-AZ."""
        return self.node_max > 3


@dataclass
class ScalingPolicy:
    """Autoscaling policy for Kubernetes workloads."""
    min_replicas: int
    max_replicas: int
    mode: ScalingMode = ScalingMode.HPA
    cpu_threshold_percent: int = 70
    memory_threshold_percent: int = 80
    kafka_topic: Optional[str] = None
    kafka_consumer_lag_threshold: int = 1000
    scale_down_stabilization_seconds: int = 300

    def __post_init__(self):
        if self.min_replicas < 0:
            raise ValueError("min_replicas must be >= 0")
        if self.max_replicas < self.min_replicas:
            raise ValueError("max_replicas must be >= min_replicas")
        if not (0 < self.cpu_threshold_percent <= 100):
            raise ValueError("cpu_threshold_percent must be 1-100")
        if self.mode == ScalingMode.KEDA and not self.kafka_topic:
            raise ValueError("kafka_topic is required for KEDA scaling mode")

    @property
    def is_event_driven(self) -> bool:
        return self.mode == ScalingMode.KEDA

    def to_hpa_manifest(self, deployment_name: str, namespace: str) -> dict:
        """Generate a Kubernetes HPA manifest."""
        return {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": f"{deployment_name}-hpa", "namespace": namespace},
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": deployment_name,
                },
                "minReplicas": self.min_replicas,
                "maxReplicas": self.max_replicas,
                "metrics": [
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": self.cpu_threshold_percent,
                            },
                        },
                    },
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "memory",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": self.memory_threshold_percent,
                            },
                        },
                    },
                ],
                "behavior": {
                    "scaleDown": {
                        "stabilizationWindowSeconds": self.scale_down_stabilization_seconds,
                    }
                },
            },
        }


@dataclass
class DeploymentSpec:
    """Specification for a Kubernetes deployment."""
    name: str
    namespace: str
    image: str
    tag: str = "latest"
    replicas: int = 2
    cpu_request: str = "100m"
    cpu_limit: str = "500m"
    memory_request: str = "128Mi"
    memory_limit: str = "512Mi"
    port: int = 8080
    env_vars: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)
    scaling: Optional[ScalingPolicy] = None

    def __post_init__(self):
        if not self.name:
            raise ValueError("deployment name cannot be empty")
        if not self.namespace:
            raise ValueError("namespace cannot be empty")
        if not self.image:
            raise ValueError("image cannot be empty")
        if self.replicas < 0:
            raise ValueError("replicas must be >= 0")
        if not (1 <= self.port <= 65535):
            raise ValueError("port must be 1-65535")

    @property
    def full_image(self) -> str:
        return f"{self.image}:{self.tag}"

    @property
    def has_autoscaling(self) -> bool:
        return self.scaling is not None

    def to_manifest(self) -> dict:
        """Generate a Kubernetes Deployment manifest."""
        base_labels = {"app": self.name, "app.kubernetes.io/name": self.name}
        base_labels.update(self.labels)
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": self.name,
                "namespace": self.namespace,
                "labels": base_labels,
            },
            "spec": {
                "replicas": self.replicas,
                "selector": {"matchLabels": {"app": self.name}},
                "template": {
                    "metadata": {"labels": base_labels},
                    "spec": {
                        "containers": [
                            {
                                "name": self.name,
                                "image": self.full_image,
                                "ports": [{"containerPort": self.port}],
                                "resources": {
                                    "requests": {
                                        "cpu": self.cpu_request,
                                        "memory": self.memory_request,
                                    },
                                    "limits": {
                                        "cpu": self.cpu_limit,
                                        "memory": self.memory_limit,
                                    },
                                },
                                "env": [
                                    {"name": k, "value": v}
                                    for k, v in self.env_vars.items()
                                ],
                            }
                        ]
                    },
                },
            },
        }


class KubernetesManager:
    """
    Kubernetes cluster manager for Smartly's multi-cloud ad-tech platform.
    Manages EKS (AWS) and GKE (GCP) clusters with Helm, ArgoCD GitOps,
    Karpenter autoscaling, and Vault secrets integration.
    """

    SUPPORTED_K8S_VERSIONS = {"1.27", "1.28", "1.29", "1.30"}
    MAX_CLUSTERS = 20
    MAX_DEPLOYMENTS_PER_CLUSTER = 200

    def __init__(self):
        self._clusters: dict[str, ClusterConfig] = {}
        self._deployments: dict[str, list[DeploymentSpec]] = {}
        self._deployment_statuses: dict[str, DeploymentStatus] = {}

    # ------------------------------------------------------------------ #
    #  Cluster lifecycle
    # ------------------------------------------------------------------ #

    def register_cluster(self, config: ClusterConfig) -> dict:
        """Register a cluster for management."""
        if len(self._clusters) >= self.MAX_CLUSTERS:
            raise RuntimeError(f"Cannot manage more than {self.MAX_CLUSTERS} clusters")
        if config.kubernetes_version not in self.SUPPORTED_K8S_VERSIONS:
            raise ValueError(
                f"Kubernetes {config.kubernetes_version} not supported. "
                f"Supported: {self.SUPPORTED_K8S_VERSIONS}"
            )
        cluster_id = config.cluster_id
        self._clusters[cluster_id] = config
        self._deployments[cluster_id] = []
        logger.info("Registered cluster %s (%s/%s)", config.name, config.provider.value, config.region)
        return {"cluster_id": cluster_id, "status": "registered", "provider": config.provider.value}

    def deregister_cluster(self, cluster_id: str) -> bool:
        """Deregister a cluster (does not destroy cloud resources)."""
        if cluster_id not in self._clusters:
            return False
        del self._clusters[cluster_id]
        self._deployments.pop(cluster_id, None)
        return True

    def get_cluster(self, cluster_id: str) -> Optional[ClusterConfig]:
        return self._clusters.get(cluster_id)

    def list_clusters(self, provider: Optional[CloudProvider] = None) -> list[str]:
        """List registered cluster IDs, optionally filtered by provider."""
        if provider is None:
            return sorted(self._clusters.keys())
        return sorted(
            cid for cid, cfg in self._clusters.items() if cfg.provider == provider
        )

    def cluster_count(self) -> int:
        return len(self._clusters)

    # ------------------------------------------------------------------ #
    #  Deployment management
    # ------------------------------------------------------------------ #

    def deploy(self, cluster_id: str, spec: DeploymentSpec) -> dict:
        """Deploy a workload to a cluster."""
        if cluster_id not in self._clusters:
            raise ValueError(f"Cluster {cluster_id!r} not registered")
        if len(self._deployments[cluster_id]) >= self.MAX_DEPLOYMENTS_PER_CLUSTER:
            raise RuntimeError(f"Cluster {cluster_id} has reached max deployments")

        deploy_key = f"{cluster_id}/{spec.namespace}/{spec.name}"
        self._deployments[cluster_id].append(spec)
        self._deployment_statuses[deploy_key] = DeploymentStatus.RUNNING
        logger.info("Deployed %s to cluster %s (ns=%s)", spec.name, cluster_id, spec.namespace)
        return {
            "deploy_key": deploy_key,
            "status": DeploymentStatus.RUNNING.value,
            "image": spec.full_image,
            "replicas": spec.replicas,
        }

    def rollback(self, cluster_id: str, namespace: str, name: str) -> dict:
        """Mark a deployment as rolled back."""
        deploy_key = f"{cluster_id}/{namespace}/{name}"
        if deploy_key not in self._deployment_statuses:
            return {"success": False, "message": f"Deployment {deploy_key!r} not found"}
        self._deployment_statuses[deploy_key] = DeploymentStatus.ROLLED_BACK
        return {"success": True, "deploy_key": deploy_key, "status": DeploymentStatus.ROLLED_BACK.value}

    def get_deployment_status(self, cluster_id: str, namespace: str, name: str) -> Optional[DeploymentStatus]:
        deploy_key = f"{cluster_id}/{namespace}/{name}"
        return self._deployment_statuses.get(deploy_key)

    def list_deployments(self, cluster_id: str) -> list[DeploymentSpec]:
        if cluster_id not in self._deployments:
            raise ValueError(f"Cluster {cluster_id!r} not registered")
        return list(self._deployments[cluster_id])

    def deployment_count(self, cluster_id: str) -> int:
        if cluster_id not in self._deployments:
            return 0
        return len(self._deployments[cluster_id])

    # ------------------------------------------------------------------ #
    #  Helm chart helpers
    # ------------------------------------------------------------------ #

    def build_helm_values(self, spec: DeploymentSpec, extra: Optional[dict] = None) -> dict:
        """Build Helm values dict from a DeploymentSpec."""
        values = {
            "image": {"repository": spec.image, "tag": spec.tag},
            "replicaCount": spec.replicas,
            "resources": {
                "requests": {"cpu": spec.cpu_request, "memory": spec.memory_request},
                "limits": {"cpu": spec.cpu_limit, "memory": spec.memory_limit},
            },
            "service": {"port": spec.port},
            "env": spec.env_vars,
        }
        if spec.has_autoscaling and spec.scaling:
            values["autoscaling"] = {
                "enabled": True,
                "minReplicas": spec.scaling.min_replicas,
                "maxReplicas": spec.scaling.max_replicas,
                "targetCPUUtilizationPercentage": spec.scaling.cpu_threshold_percent,
            }
        if extra:
            values.update(extra)
        return values

    # ------------------------------------------------------------------ #
    #  RBAC helpers
    # ------------------------------------------------------------------ #

    def generate_rbac_manifest(self, team: str, namespace: str,
                                role: str = "edit") -> dict:
        """Generate a RoleBinding manifest for a team."""
        valid_roles = {"view", "edit", "admin"}
        if role not in valid_roles:
            raise ValueError(f"role must be one of {valid_roles}, got {role!r}")
        return {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {
                "name": f"{team}-{role}",
                "namespace": namespace,
                "labels": {"smartly.io/team": team},
            },
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": role,
            },
            "subjects": [
                {
                    "kind": "Group",
                    "name": f"smartly:{team}",
                    "apiGroup": "rbac.authorization.k8s.io",
                }
            ],
        }

    # ------------------------------------------------------------------ #
    #  Node pool helpers
    # ------------------------------------------------------------------ #

    def calculate_node_capacity(self, config: ClusterConfig,
                                  cpu_per_node: float = 4.0,
                                  memory_per_node_gb: float = 16.0) -> dict:
        """Estimate cluster capacity for planning."""
        return {
            "cluster_id": config.cluster_id,
            "max_nodes": config.node_max,
            "total_cpu_vcores": config.node_max * cpu_per_node,
            "total_memory_gb": config.node_max * memory_per_node_gb,
            "usable_cpu_vcores": config.node_max * cpu_per_node * 0.9,  # 10% system overhead
            "usable_memory_gb": config.node_max * memory_per_node_gb * 0.85,
        }

    def recommend_instance_type(self, workload_type: str) -> str:
        """Recommend EC2/GCP instance type based on workload profile."""
        recommendations = {
            "ad-impression-processing": "c5.4xlarge",
            "creative-rendering": "c5.2xlarge",
            "campaign-api": "m5.xlarge",
            "data-pipeline": "r5.2xlarge",
            "general": "m5.xlarge",
        }
        return recommendations.get(workload_type, recommendations["general"])
