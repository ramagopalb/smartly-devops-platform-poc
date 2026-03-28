"""
Smartly Namespace-as-a-Service Controller
Provides self-service namespace provisioning for engineering teams.
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class NamespaceConfig:
    """Configuration for a managed namespace."""
    team: str
    service: str
    environment: str
    ttl_hours: Optional[int] = None  # None = permanent, int = ephemeral
    resource_quota_cpu: str = "4"
    resource_quota_memory: str = "8Gi"
    resource_quota_pods: int = 50
    labels: dict = field(default_factory=dict)
    annotations: dict = field(default_factory=dict)

    def __post_init__(self):
        valid_envs = {"dev", "staging", "prod", "preview"}
        if self.environment not in valid_envs:
            raise ValueError(f"environment must be one of {valid_envs}, got '{self.environment}'")
        if not self.team:
            raise ValueError("team cannot be empty")
        if not self.service:
            raise ValueError("service cannot be empty")
        if self.ttl_hours is not None and self.ttl_hours <= 0:
            raise ValueError("ttl_hours must be positive if set")

    @property
    def namespace_name(self) -> str:
        return f"{self.team}-{self.service}-{self.environment}"

    @property
    def is_ephemeral(self) -> bool:
        return self.ttl_hours is not None


@dataclass
class NamespaceProvisionResult:
    """Result of a namespace provisioning operation."""
    namespace_name: str
    action: str  # created, updated, already_exists, deleted
    success: bool
    message: str
    resources_created: list = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class NamespaceController:
    """
    Namespace-as-a-Service controller for Smartly platform.
    Manages namespace lifecycle, RBAC, resource quotas, and network policies.
    """

    ALLOWED_LABEL_PREFIXES = ("app.kubernetes.io/", "smartly.io/", "team/", "env/")
    MAX_NAMESPACE_NAME_LENGTH = 63  # Kubernetes DNS label limit

    def __init__(self):
        self._provisioned_namespaces: dict[str, NamespaceConfig] = {}
        self._deleted_namespaces: list[str] = []

    def provision(self, config: NamespaceConfig) -> NamespaceProvisionResult:
        """Provision a new namespace with all required resources."""
        ns_name = config.namespace_name

        if len(ns_name) > self.MAX_NAMESPACE_NAME_LENGTH:
            return NamespaceProvisionResult(
                namespace_name=ns_name,
                action="rejected",
                success=False,
                message=f"Namespace name '{ns_name}' exceeds {self.MAX_NAMESPACE_NAME_LENGTH} characters"
            )

        if ns_name in self._provisioned_namespaces:
            return NamespaceProvisionResult(
                namespace_name=ns_name,
                action="already_exists",
                success=True,
                message=f"Namespace {ns_name} already exists"
            )

        resources = self._build_resources(config)
        self._provisioned_namespaces[ns_name] = config

        logger.info(f"Provisioned namespace {ns_name} for team={config.team}, env={config.environment}")

        return NamespaceProvisionResult(
            namespace_name=ns_name,
            action="created",
            success=True,
            message=f"Namespace {ns_name} provisioned successfully",
            resources_created=resources
        )

    def deprovision(self, namespace_name: str) -> NamespaceProvisionResult:
        """Remove a namespace and all its resources."""
        if namespace_name not in self._provisioned_namespaces:
            return NamespaceProvisionResult(
                namespace_name=namespace_name,
                action="not_found",
                success=False,
                message=f"Namespace {namespace_name} not found"
            )

        config = self._provisioned_namespaces.pop(namespace_name)
        self._deleted_namespaces.append(namespace_name)

        return NamespaceProvisionResult(
            namespace_name=namespace_name,
            action="deleted",
            success=True,
            message=f"Namespace {namespace_name} deprovisioned"
        )

    def get_namespace(self, namespace_name: str) -> Optional[NamespaceConfig]:
        return self._provisioned_namespaces.get(namespace_name)

    def list_namespaces(self, team: Optional[str] = None,
                        environment: Optional[str] = None) -> list[str]:
        """List provisioned namespaces with optional filtering."""
        namespaces = []
        for ns_name, config in self._provisioned_namespaces.items():
            if team and config.team != team:
                continue
            if environment and config.environment != environment:
                continue
            namespaces.append(ns_name)
        return sorted(namespaces)

    def list_ephemeral_expired(self, current_hour_offset: int = 0) -> list[str]:
        """Return ephemeral namespaces that have exceeded their TTL."""
        # Simplified: assume all ephemeral with ttl_hours <= current_hour_offset are expired
        expired = []
        for ns_name, config in self._provisioned_namespaces.items():
            if config.is_ephemeral and config.ttl_hours is not None:
                if config.ttl_hours <= current_hour_offset:
                    expired.append(ns_name)
        return expired

    def validate_labels(self, labels: dict) -> tuple[bool, str]:
        """Validate that labels follow Smartly naming conventions."""
        for key in labels:
            if "/" in key:
                prefix = key.split("/")[0] + "/"
                if not any(key.startswith(p) for p in self.ALLOWED_LABEL_PREFIXES):
                    return False, f"Label prefix '{prefix}' not in allowed list"
        return True, ""

    def generate_resource_quota_manifest(self, config: NamespaceConfig) -> dict:
        """Generate a Kubernetes ResourceQuota manifest."""
        return {
            "apiVersion": "v1",
            "kind": "ResourceQuota",
            "metadata": {
                "name": "default-quota",
                "namespace": config.namespace_name,
                "labels": {
                    "app.kubernetes.io/managed-by": "namespace-controller",
                    "smartly.io/team": config.team,
                }
            },
            "spec": {
                "hard": {
                    "requests.cpu": config.resource_quota_cpu,
                    "requests.memory": config.resource_quota_memory,
                    "limits.cpu": str(int(config.resource_quota_cpu) * 2),
                    "limits.memory": config.resource_quota_memory,
                    "pods": str(config.resource_quota_pods),
                    "services": "10",
                    "configmaps": "50",
                    "secrets": "20",
                }
            }
        }

    def generate_network_policy_manifest(self, config: NamespaceConfig) -> dict:
        """Generate a default-deny NetworkPolicy manifest."""
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "default-deny-ingress",
                "namespace": config.namespace_name,
            },
            "spec": {
                "podSelector": {},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    {
                        "from": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "smartly.io/team": config.team
                                    }
                                }
                            },
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "monitoring"
                                    }
                                }
                            }
                        ]
                    }
                ],
                "egress": [
                    {"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}
                ]
            }
        }

    def _build_resources(self, config: NamespaceConfig) -> list[str]:
        """Return list of resources that would be created."""
        resources = ["Namespace", "ResourceQuota", "LimitRange", "NetworkPolicy"]
        if config.environment != "dev":
            resources.append("RBAC/RoleBinding")
        if config.is_ephemeral:
            resources.append("TTLController/ExpiryAnnotation")
        return resources
