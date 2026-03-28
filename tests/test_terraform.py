"""
Tests for Terraform module configuration.
Validates EKS and GKE module structure and required settings.
"""
import os
import re
import pytest


TF_DIR = os.path.join(os.path.dirname(__file__), "..", "terraform", "modules")


def read_tf(module, filename="main.tf"):
    path = os.path.join(TF_DIR, module, filename)
    with open(path) as f:
        return f.read()


# --- EKS Module Tests ---

class TestEKSModule:
    def setup_method(self):
        self.content = read_tf("eks")

    def test_required_terraform_version(self):
        assert "required_version" in self.content
        assert ">= 1.5.0" in self.content

    def test_aws_provider_declared(self):
        assert '"hashicorp/aws"' in self.content

    def test_kubernetes_provider_declared(self):
        assert '"hashicorp/kubernetes"' in self.content

    def test_cluster_name_variable(self):
        assert 'variable "cluster_name"' in self.content

    def test_environment_variable_with_validation(self):
        assert 'variable "environment"' in self.content
        assert '"dev", "staging", "prod"' in self.content

    def test_kubernetes_version_variable(self):
        assert 'variable "kubernetes_version"' in self.content

    def test_vpc_id_variable(self):
        assert 'variable "vpc_id"' in self.content

    def test_subnet_ids_variable(self):
        assert 'variable "subnet_ids"' in self.content

    def test_eks_cluster_resource(self):
        assert 'resource "aws_eks_cluster" "main"' in self.content

    def test_private_endpoint_access(self):
        assert "endpoint_private_access = true" in self.content

    def test_public_endpoint_disabled(self):
        assert "endpoint_public_access  = false" in self.content

    def test_kms_encryption_for_secrets(self):
        assert "secrets" in self.content
        assert "aws_kms_key" in self.content

    def test_key_rotation_enabled(self):
        assert "enable_key_rotation     = true" in self.content

    def test_cloudwatch_logging_enabled(self):
        assert "enabled_cluster_log_types" in self.content
        assert '"api"' in self.content
        assert '"audit"' in self.content

    def test_common_tags_with_environment(self):
        assert "local.common_tags" in self.content
        assert "ManagedBy" in self.content

    def test_iam_role_for_cluster(self):
        assert 'resource "aws_iam_role" "cluster"' in self.content

    def test_cluster_endpoint_output(self):
        assert 'output "cluster_endpoint"' in self.content

    def test_cluster_oidc_output(self):
        assert 'output "cluster_oidc_issuer"' in self.content


# --- GKE Module Tests ---

class TestGKEModule:
    def setup_method(self):
        self.content = read_tf("gke")

    def test_required_terraform_version(self):
        assert "required_version" in self.content
        assert ">= 1.5.0" in self.content

    def test_google_provider_declared(self):
        assert '"hashicorp/google"' in self.content

    def test_google_beta_provider(self):
        assert '"hashicorp/google-beta"' in self.content

    def test_cluster_name_variable(self):
        assert 'variable "cluster_name"' in self.content

    def test_project_id_variable(self):
        assert 'variable "project_id"' in self.content

    def test_environment_validation(self):
        assert '"dev", "staging", "prod"' in self.content

    def test_region_defaults_to_europe(self):
        assert "europe-west1" in self.content

    def test_workload_identity_enabled(self):
        assert "workload_identity_config" in self.content
        assert "workload_pool" in self.content

    def test_network_policy_calico(self):
        assert "CALICO" in self.content

    def test_binary_authorization_prod(self):
        assert "binary_authorization" in self.content
        assert "PROJECT_SINGLETON_POLICY_ENFORCE" in self.content

    def test_node_pool_autoscaling(self):
        assert "autoscaling" in self.content
        assert "min_node_count" in self.content
        assert "max_node_count" in self.content

    def test_shielded_instance_config(self):
        assert "shielded_instance_config" in self.content
        assert "enable_secure_boot" in self.content

    def test_auto_repair_enabled(self):
        assert "auto_repair  = true" in self.content

    def test_auto_upgrade_enabled(self):
        assert "auto_upgrade = true" in self.content

    def test_cluster_endpoint_output(self):
        assert 'output "cluster_endpoint"' in self.content

    def test_sensitive_ca_certificate_output(self):
        assert "sensitive = true" in self.content

    def test_release_channel_by_env(self):
        assert "STABLE" in self.content
        assert "REGULAR" in self.content
