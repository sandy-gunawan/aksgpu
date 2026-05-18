# Azure Resources — GPU Weather Prediction App

Everything deployed in Azure for this application, organized by resource group.

> **Resource names with a `<suffix>` placeholder** are randomized per setup run for global uniqueness. The current run is recorded in `.infra-config.json`. As of the most recent setup, the suffix is **`ro6n`** — i.e. ACR is `acrgpuweatherro6n`, storage is `stgpuweatherro6n`, Key Vault is `kv-gpu-wea-ro6n`.

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
|----------|------|--------------|
| **acrgpuweather`<suffix>`** | Container Registry (Basic) | Private Docker image store. Holds `weather-api`, `weather-ui`, and `crop-api` images that AKS pulls from. |
| **aks-gpu-weather** | AKS Managed Cluster | The Kubernetes cluster. Two node pools: `nodepool1` (CPU, always on) and `gpupool1` (GPU, autoscale 0 ↔ 1, tainted `sku=gpu:NoSchedule`). |
| **stgpuweather`<suffix>`** | Storage Account (Standard_LRS) | Azure Blob Storage. Containers: `weather-data/`, `models/`, `predictions/`, `crop-data/`, `crop-models/`. **Auth is managed-identity only** (shared keys disabled by policy). |
| **kv-gpu-wea-`<suffix>`** | Key Vault | Holds secrets if/when needed. The k8s `weather-secrets` is intentionally empty because MI is used at runtime. |
| **ai-gpu-weather** | Application Insights | Telemetry and monitoring — tracks API response times, error rates, request counts, custom metrics. |
| **Failure Anomalies - ai-gpu-weather** | Smart Detector Alert | Auto-created by App Insights. Fires alerts when it detects abnormal spikes in failure rates. |
| **stgpuweather`<suffix>`-...** | Event Grid System Topic | Auto-created by the Storage Account. Enables event-driven notifications (e.g., "blob created" events). |

### Node pool details

| Pool | VM size | vCPU | RAM | OS disk | GPU | Count | Taints |
|------|---------|------|-----|---------|-----|-------|--------|
| `nodepool1` | Standard_DC4ds_v3 | 4 | 32 GB | 30 GB Ephemeral | — | 1 | — |
| `gpupool1` | Standard_NC4as_T4_v3 | 4 | 28 GB | 30 GB Ephemeral | T4 16 GB | 0–1 (autoscale) | `sku=gpu:NoSchedule` |

Ephemeral OS disks live on the VM's local SSD — they are **free** (no managed disk billing) and they fit inside the VM cache disk, which is why we cap the size at 30 GB.

---

## Resource Group 2: `MC_rg-gpu-weather_aks-gpu-weather_eastus2` (AKS-Managed)

**Auto-created by AKS.** Contains the actual infrastructure that runs the cluster. You should never modify these directly.

| Resource | What It Does |
|----------|--------------|
| **aks-nodepool1-...-vmss** | Virtual Machine Scale Set for the **CPU node pool**. Runs frontend, backend, ingress, NVIDIA device plugin (on GPU node only), and system pods. |
| **aks-gpupool1-...-vmss** | Virtual Machine Scale Set for the **GPU node pool**. NC4as_T4_v3 with an NVIDIA T4. Scales 0–1 automatically. |
| **kubernetes** (Load Balancer) | Azure Load Balancer (Standard SKU) that routes external internet traffic to Kubernetes services. |
| **2 × Public IP Addresses** | One for the cluster's outbound LB, one for the ingress controller (web app routing). The latter is the IP you hit in the browser. |
| **aks-vnet-...** (Virtual Network) | The VNet where all cluster nodes sit. Uses Azure CNI networking. |
| **3 × Network Security Groups** | Firewall rules controlling traffic for: agent pool subnet, virtual kubelet, and app gateway. |
| **2 × User-Assigned Managed Identities** | Kubelet identity (pulls images from ACR, manages disks, accesses blob storage) and the web app routing add-on identity. |

---

## Resource Group 3: `ai_ai-gpu-weather_..._managed` (App Insights-Managed)

**Auto-created by Application Insights.** Contains the data backend for monitoring.

| Resource | What It Does |
|----------|--------------|
| **managed-ai-gpu-weather-ws** | Log Analytics Workspace. The actual data store behind Application Insights — all telemetry logs, KQL queries, and diagnostics data live here. |

---

## Quick Reference Commands

```powershell
# Read the live resource names (with the random suffix)
Get-Content C:\labs\tech\aksgpu-temp\.infra-config.json | ConvertFrom-Json

# List all resource groups for this app
az group list --query "[?contains(name,'gpu-weather') || starts_with(name,'MC_rg-gpu-weather')].{Name:name, Location:location}" -o table

# List resources in your main group
az resource list --resource-group rg-gpu-weather --query "[].{Name:name, Type:type}" -o table

# List resources in the AKS-managed group
az resource list --resource-group "MC_rg-gpu-weather_aks-gpu-weather_eastus2" --query "[].{Name:name, Type:type}" -o table

# Node pool overview
az aks nodepool list --cluster-name aks-gpu-weather --resource-group rg-gpu-weather `
  --query "[].{Name:name, VmSize:vmSize, Count:count, OsDiskGB:osDiskSizeGb, OsDiskType:osDiskType, Min:minCount, Max:maxCount, Taints:nodeTaints}" -o table
```

---

## What Happens at Teardown?

When you run `teardown.ps1 -Mode full`, deleting `rg-gpu-weather` automatically deletes all three groups:
- Deleting the AKS cluster triggers Azure to delete the `MC_` group
- Deleting Application Insights triggers Azure to delete the `ai_..._managed` group

You only need to delete **one** resource group — Azure cleans up the rest.

Other teardown modes:

| Mode | Effect | Recurring cost |
|------|--------|----------------|
| `.\teardown.ps1 -Mode full` | Delete the resource group and every resource in it. | **$0** |
| `.\teardown.ps1 -Mode partial` | `az aks stop` — keeps everything in place, deallocates the node VMs. | **~$5–10 / mo** (storage + ACR + KV + App Insights) |
| `.\teardown.ps1 -Mode gpu` | Remove only the GPU node pool from the cluster. | Same as cluster running without a GPU pool (~$170/mo) |
