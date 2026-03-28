terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.0"
    }
  }
}

variable "cluster_name" {
  description = "GKE cluster name"
  type        = string
}

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "europe-west1"
}

variable "environment" {
  description = "Environment (dev/staging/prod)"
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.28"
}

variable "node_machine_type" {
  description = "GCE machine type for nodes"
  type        = string
  default     = "n2-standard-8"
}

variable "min_node_count" {
  description = "Minimum node count per zone"
  type        = number
  default     = 1
}

variable "max_node_count" {
  description = "Maximum node count per zone"
  type        = number
  default     = 30
}

variable "labels" {
  description = "Resource labels"
  type        = map(string)
  default     = {}
}

locals {
  common_labels = merge(var.labels, {
    environment = var.environment
    managed-by  = "terraform"
    platform    = "smartly"
    team        = "platform-engineering"
  })
}

resource "google_container_cluster" "main" {
  provider = google-beta

  name     = var.cluster_name
  project  = var.project_id
  location = var.region

  min_master_version = var.kubernetes_version
  release_channel {
    channel = var.environment == "prod" ? "STABLE" : "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  addons_config {
    http_load_balancing {
      disabled = false
    }
    horizontal_pod_autoscaling {
      disabled = false
    }
    network_policy_config {
      disabled = false
    }
  }

  network_policy {
    enabled  = true
    provider = "CALICO"
  }

  binary_authorization {
    evaluation_mode = var.environment == "prod" ? "PROJECT_SINGLETON_POLICY_ENFORCE" : "DISABLED"
  }

  resource_labels = local.common_labels

  initial_node_count       = 1
  remove_default_node_pool = true
}

resource "google_container_node_pool" "main" {
  name     = "${var.cluster_name}-node-pool"
  project  = var.project_id
  location = var.region
  cluster  = google_container_cluster.main.name

  autoscaling {
    min_node_count = var.min_node_count
    max_node_count = var.max_node_count
  }

  node_config {
    machine_type = var.node_machine_type
    disk_size_gb = 100
    disk_type    = "pd-ssd"

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    labels = local.common_labels
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

output "cluster_name" {
  value = google_container_cluster.main.name
}

output "cluster_endpoint" {
  value = google_container_cluster.main.endpoint
}

output "cluster_ca_certificate" {
  value     = google_container_cluster.main.master_auth[0].cluster_ca_certificate
  sensitive = true
}
