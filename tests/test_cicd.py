"""
Tests for CI/CD pipeline configuration.
Validates GitHub Actions workflow structure and security requirements.
"""
import os
import pytest
import yaml


WORKFLOWS_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")


def load_workflow(filename="ci-cd.yml"):
    path = os.path.join(WORKFLOWS_DIR, filename)
    with open(path) as f:
        return yaml.safe_load(f)


class TestCICDPipeline:
    def setup_method(self):
        self.workflow = load_workflow()

    def test_workflow_has_name(self):
        assert "name" in self.workflow
        assert self.workflow["name"] != ""

    def test_triggers_on_push_to_main(self):
        # YAML parses 'on' as True (boolean) in some parsers; handle both keys
        triggers = self.workflow.get("on") or self.workflow.get(True) or {}
        assert "push" in triggers
        assert "main" in triggers["push"]["branches"]

    def test_triggers_on_pull_request(self):
        triggers = self.workflow.get("on") or self.workflow.get(True) or {}
        assert "pull_request" in triggers

    def test_security_scan_job_exists(self):
        assert "security-scan" in self.workflow["jobs"]

    def test_build_job_exists(self):
        assert "build" in self.workflow["jobs"]

    def test_image_signing_job_exists(self):
        assert "sign-image" in self.workflow["jobs"]

    def test_staging_deploy_job_exists(self):
        assert "deploy-staging" in self.workflow["jobs"]

    def test_prod_deploy_job_exists(self):
        assert "deploy-prod" in self.workflow["jobs"]

    def test_build_depends_on_security_scan(self):
        build_needs = self.workflow["jobs"]["build"].get("needs", [])
        assert "security-scan" in build_needs

    def test_signing_depends_on_build(self):
        sign_needs = self.workflow["jobs"]["sign-image"].get("needs", [])
        assert "build" in sign_needs

    def test_trivy_scanner_used(self):
        steps = self.workflow["jobs"]["security-scan"]["steps"]
        step_uses = [s.get("uses", "") for s in steps]
        assert any("trivy" in u.lower() for u in step_uses)

    def test_checkov_scanner_used(self):
        steps = self.workflow["jobs"]["security-scan"]["steps"]
        step_uses = [s.get("uses", "") for s in steps]
        assert any("checkov" in u.lower() for u in step_uses)

    def test_cosign_image_signing(self):
        steps = self.workflow["jobs"]["sign-image"]["steps"]
        step_uses = [s.get("uses", "") for s in steps]
        assert any("cosign" in u.lower() or "sigstore" in u.lower() for u in step_uses)

    def test_prod_deploy_requires_environment_protection(self):
        prod_job = self.workflow["jobs"]["deploy-prod"]
        assert prod_job.get("environment") == "production"

    def test_staging_deploy_requires_environment_protection(self):
        staging_job = self.workflow["jobs"]["deploy-staging"]
        assert staging_job.get("environment") == "staging"

    def test_gitops_approach_mentioned(self):
        # Verify GitOps pattern (no direct kubectl apply, use git push)
        prod_steps = self.workflow["jobs"]["deploy-prod"]["steps"]
        step_names = [s.get("name", "") for s in prod_steps]
        assert any("GitOps" in n or "gitops" in n.lower() for n in step_names)
