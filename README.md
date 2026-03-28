# Smartly DevOps Platform POC

A production-grade DevOps platform POC demonstrating Staff-level capabilities for Smartly's ad-tech infrastructure. This project showcases Kubernetes at scale (EKS + GKE), GitOps with ArgoCD, multi-cloud IaC with Terraform/Terragrunt, platform engineering patterns, and observability for high-throughput advertising workloads.

## Architecture

```
                          ┌─────────────────────────────────┐
                          │        GitHub Actions CI/CD       │
                          │  Security: Trivy, Checkov, OPA    │
                          │  Build: Docker BuildKit + ECR     │
                          └────────────┬────────────────────-┘
                                       │ GitOps push
                          ┌────────────▼────────────────────-┐
                          │       ArgoCD ApplicationSets      │
                          │  Multi-cluster progressive deploy  │
                          │  Argo Rollouts canary + Prometheus │
                          └────────┬───────────────┬─────────┘
                                   │               │
              ┌────────────────────▼─┐   ┌────────▼─────────────────────┐
              │  AWS EKS Cluster      │   │  GCP GKE Cluster              │
              │  Karpenter autoscaler │   │  GKE Autopilot                │
              │  KEDA (Kafka scaling) │   │  Cross-cloud Workload Identity│
              │  Istio service mesh   │   │  Anthos Config Management     │
              └──────────────────────┘   └──────────────────────────────┘
                          │
              ┌────────────────────────────────────────────┐
              │         Ad-Tech Platform Services           │
              │  - Creative Delivery (high-throughput)      │
              │  - Impression Tracker (Kafka consumers)     │
              │  - Campaign Manager API                     │
              │  - Bid Request Router                       │
              └────────────────────────────────────────────┘
                          │
              ┌────────────────────────────────────────────┐
              │         Observability Stack                  │
              │  Prometheus + Grafana + OpenTelemetry       │
              │  SLO burn-rate alerts + Kafka lag           │
              │  PagerDuty escalation automation            │
              └────────────────────────────────────────────┘
```

## Key Features

### 1. GitOps with ArgoCD ApplicationSets
- Multi-cluster deployment across EKS (AWS) and GKE (GCP)
- Argo Rollouts canary strategy with Prometheus error-rate rollback gates
- Automated image update via ArgoCD Image Updater
- ApplicationSet generator for dev/staging/prod environments

### 2. Kubernetes at Scale
- Karpenter node autoscaler with Spot interruption handling
- KEDA event-driven scaling for Kafka consumer workloads (ad impression processing)
- Istio service mesh with mTLS for inter-service security
- OPA Gatekeeper admission control policies

### 3. Multi-Cloud IaC (Terraform/Terragrunt)
- DRY Terragrunt module structure for AWS EKS + GCP GKE
- Remote state with S3+DynamoDB (AWS) and GCS (GCP)
- OPA/Rego policy-as-code for tag compliance and security controls
- Infracost integration for cost estimation

### 4. Platform Engineering
- Namespace-as-a-Service controller (Python + kubernetes-client)
- Backstage IDP component catalog integration
- Golden path Helm chart templates for ad-tech services
- FinOps per-team cost dashboards

### 5. Observability
- Prometheus metrics with SLO burn-rate alerting
- OpenTelemetry distributed tracing for ad request flows
- Kafka consumer lag dashboards (critical for impression tracking)
- Grafana dashboards with 15+ panels covering infrastructure and application signals

## Test Suite (80+ Tests)

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest tests/ -v

# Run by category
pytest tests/ -v -k "argocd"
pytest tests/ -v -k "terraform"
pytest tests/ -v -k "kubernetes"
pytest tests/ -v -k "platform"
pytest tests/ -v -k "observability"
```

### Test Categories
- ArgoCD ApplicationSet configuration tests (15 tests)
- Terraform module validation tests (18 tests)
- Kubernetes resource policy tests (14 tests)
- Platform engineering / Namespace controller tests (12 tests)
- Observability config / SLO validation tests (11 tests)
- CI/CD pipeline configuration tests (10 tests)
- FinOps cost policy tests (8 tests)

**Total: 88 tests**

## Prerequisites

- Python 3.9+
- pytest, pyyaml, jsonschema
- kubectl (for live tests, optional)
- terraform (for plan tests, optional)

## Project Structure

```
.
├── README.md
├── requirements.txt
├── argocd/
│   ├── applicationset-eks.yaml       # AWS EKS ApplicationSet
│   ├── applicationset-gke.yaml       # GCP GKE ApplicationSet
│   ├── rollout-canary.yaml           # Argo Rollouts canary config
│   └── image-updater-config.yaml     # ArgoCD Image Updater
├── terraform/
│   └── modules/
│       ├── eks/                      # AWS EKS Terragrunt module
│       └── gke/                      # GCP GKE Terragrunt module
├── helm/
│   └── ad-platform/                  # Golden path Helm chart
├── platform/
│   ├── namespace_controller.py       # Namespace-as-a-Service
│   ├── finops_dashboard.py           # FinOps cost tracker
│   └── slo_calculator.py            # SLO burn-rate calculator
├── .github/
│   └── workflows/
│       └── ci-cd.yml                 # GitHub Actions pipeline
└── tests/
    ├── test_argocd.py
    ├── test_terraform.py
    ├── test_kubernetes.py
    ├── test_platform.py
    ├── test_observability.py
    ├── test_cicd.py
    └── test_finops.py
```

## Relevance to Smartly

Smartly processes 800B+ ad impressions/year across 700+ brands. This POC directly addresses:

1. **Scale:** Kubernetes patterns for high-throughput ad delivery workloads
2. **GitOps:** ArgoCD-based deployment across 13+ country infrastructure
3. **Developer Experience:** Platform engineering patterns for 750+ engineers
4. **Reliability:** SLO-driven operations with automated incident response
5. **Multi-cloud:** AWS + GCP footprint matching Smartly's infrastructure

## Author

Ram Gopal Reddy Basireddy | [LinkedIn](https://www.linkedin.com/in/ram-ba-29b110261/) | [GitHub](https://github.com/ramagopalb)
