# Azure Resources — GPU Weather Prediction App

Everything deployed in Azure for this application, organized by resource group.

---

## Resource Groups — Why 3 Instead of 1?

When you run `setup-infrastructure.ps1`, it creates **one** resource group: `rg-gpu-weather`. But when you check Azure, you'll see **three** resource groups. This is normal — the other two are **auto-created by Azure services**, not by you.

### How This Happens

```
You create ONE resource group
         |
         v
  rg-gpu-weather                        <-- YOU created this (setup-infrastructure.ps1)
         |
         ├── AKS cluster created
         |       |
         |       └── Azure auto-creates ──> MC_rg-gpu-weather_aks-gpu-weather_eastus2
         |                                  (AKS needs VMs, networking, load balancers —
         |                                   it puts them in a separate group it manages)
         |
         └── Application Insights created
                 |
                 └── Azure auto-creates ──> ai_ai-gpu-weather_..._managed
                                            (App Insights needs a Log Analytics workspace —
                                             Azure creates a managed group to hold it)
```

**Why doesn't Azure put everything in one group?**

AKS manages its own infrastructure (VMs, disks, NICs, load balancers). If those lived in your resource group, you might accidentally delete a VM that AKS needs, or modify a network rule that breaks the cluster. By putting them in a separate `MC_` group, Azure keeps "your stuff" and "AKS's stuff" isolated. You manage `rg-gpu-weather`; AKS manages `MC_rg-gpu-weather_...`. Same logic for App Insights — the Log Analytics workspace is a managed dependency, so Azure puts it in its own managed group.

**Rule of thumb**: Only interact with `rg-gpu-weather`. Never manually modify resources in the `MC_` or `ai_` groups — Azure manages those for you.

---

## Resource Group 1: `rg-gpu-weather` (Your Resources)

These are the resources **you** created via `setup-infrastructure.ps1`:

| Resource | Type | What It Does |
|----------|------|-------------|
| **acrgpuweather** | Container Registry | Private Docker image store. Holds `weather-api` and `weather-ui` images that AKS pulls from. |
| **aks-gpu-weather** | AKS Managed Cluster | The Kubernetes cluster — orchestrates all pods across CPU and GPU node pools. The "brain" of the system. |
| **stgpuweather** | Storage Account | Azure Blob Storage with 3 containers: `weather-data/` (raw CSV), `models/` (trained .pt/.json files), `predictions/` (forecast output). |
| **ai-gpu-weather** | Application Insights | Telemetry and monitoring — tracks API response times, error rates, request counts, custom metrics. |
| **Failure Anomalies - ai-gpu-weather** | Smart Detector Alert | Auto-created by App Insights. Fires alerts when it detects abnormal spikes in failure rates. |
| **stgpuweather-...** | Event Grid System Topic | Auto-created by the Storage Account. Enables event-driven notifications (e.g., "blob created" events). |

---

## Resource Group 2: `MC_rg-gpu-weather_aks-gpu-weather_eastus2` (AKS-Managed)

**Auto-created by AKS.** Contains the actual infrastructure that runs the cluster. You should never modify these directly.

| Resource | What It Does |
|----------|-------------|
| **aks-gpupool1-...-vmss** | Virtual Machine Scale Set for the **GPU node pool**. Each VM has an NVIDIA T4 GPU. Scales 0-1. |
| **aks-cpupool2-...-vmss** | Virtual Machine Scale Set for the **CPU node pool**. Runs frontend, backend, ingress, and system pods. |
| **kubernetes** (Load Balancer) | Azure Load Balancer that routes external internet traffic to the correct Kubernetes services. |
| **2 × Public IP Addresses** | One for the Load Balancer (external access), one for the ingress controller (web app routing). |
| **aks-vnet-...** (Virtual Network) | The VNet where all cluster nodes sit. Uses Azure CNI networking. |
| **3 × Network Security Groups** | Firewall rules controlling traffic for: agent pool subnet, virtual kubelet, and app gateway. |
| **2 × User-Assigned Managed Identities** | Identity for AKS agent pool (pulls images from ACR, manages disks) and for the web app routing add-on. |

---

## Resource Group 3: `ai_ai-gpu-weather_..._managed` (App Insights-Managed)

**Auto-created by Application Insights.** Contains the data backend for monitoring.

| Resource | What It Does |
|----------|-------------|
| **managed-ai-gpu-weather-ws** | Log Analytics Workspace. The actual data store behind Application Insights — all telemetry logs, KQL queries, and diagnostics data live here. |

---

## Quick Reference Commands

```powershell
# List all resource groups for this app
az group list --query "[?contains(name,'gpu') || contains(name,'weather')].{Name:name, Location:location}" -o table

# List resources in your main group
az resource list --resource-group rg-gpu-weather --query "[].{Name:name, Type:type}" -o table

# List resources in the AKS-managed group
az resource list --resource-group "MC_rg-gpu-weather_aks-gpu-weather_eastus2" --query "[].{Name:name, Type:type}" -o table
```

---

## What Happens at Teardown?

When you run `teardown.ps1`, deleting `rg-gpu-weather` automatically deletes all three groups:
- Deleting the AKS cluster triggers Azure to delete the `MC_` group
- Deleting Application Insights triggers Azure to delete the `ai_..._managed` group

You only need to delete **one** resource group — Azure cleans up the rest.
