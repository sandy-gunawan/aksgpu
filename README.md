# GPU Weather & Crop Health Prediction on AKS

A full-stack ML application that predicts weather and crop health using GPU-accelerated training on Azure Kubernetes Service (AKS). React frontend, FastAPI backend, NVIDIA T4 GPU training that **auto-scales to zero** when idle (so the GPU costs $0 the rest of the time).

> **Live URL after the most recent deploy:** see the output of `build-and-deploy.ps1` (search for "Ingress IP") or run
> `kubectl get ingress -n gpu-weather`. The IP changes whenever you delete + recreate the cluster.
> Current run: **http://20.7.236.50/**

---

## Naming convention

`setup-infrastructure.ps1` generates a **random 4-character suffix** for resources that must be globally unique (ACR, Storage, Key Vault). The suffix is saved to `.infra-config.json` and reused on subsequent runs.

| Resource | Pattern | Current run |
|---|---|---|
| Resource group | `rg-gpu-weather` | `rg-gpu-weather` |
| AKS cluster | `aks-gpu-weather` | `aks-gpu-weather` |
| ACR | `acrgpuweather<suffix>` | `acrgpuweatherro6n` |
| Storage account | `stgpuweather<suffix>` | `stgpuweatherro6n` |
| Key Vault | `kv-gpu-wea-<suffix>` | `kv-gpu-wea-ro6n` |
| App Insights | `ai-gpu-weather` | `ai-gpu-weather` |

To get the live values any time:

```powershell
Get-Content C:\labs\tech\aksgpu-temp\.infra-config.json | ConvertFrom-Json
```

The samples below use the current suffix (`ro6n`). **Substitute your own suffix if it differs.**

---

## Quick Start — How to Access the Demo

The AKS cluster can be **stopped** to save money (~$170/mo → ~$5/mo while stopped). You can start/stop it via the Azure Portal or CLI.

### Option A: Start via Azure Portal (No CLI Needed)

1. Go to **<https://portal.azure.com>**
2. Search **`aks-gpu-weather`** → open the AKS resource
3. Top toolbar → click **Start** (waits ~3–5 min, status flips to `Running`)
4. Open the app at the ingress URL (e.g. `http://20.7.236.50/`)
5. When done → top toolbar → click **Stop**

> If the app returns 500s after start, see the **Storage public access** note in *Troubleshooting* below.

### Option B: Start via CLI

```powershell
# 1) Start cluster (~3-5 min)
az aks start --resource-group rg-gpu-weather --name aks-gpu-weather

# 2) Get current ingress IP
az aks get-credentials --resource-group rg-gpu-weather --name aks-gpu-weather --overwrite-existing
kubectl get ingress -n gpu-weather

# 3) When done, stop the cluster
az aks stop --resource-group rg-gpu-weather --name aks-gpu-weather
```

### Option C: Tear down everything

```powershell
.\teardown.ps1 -Mode full      # delete RG + everything (no recurring cost at all)
```

Recreate later with `.\setup-infrastructure.ps1` + `.\build-and-deploy.ps1` (~15 min total).

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Resource Group: rg-gpu-weather   (East US 2)                              │
│                                                                             │
│  AKS Cluster: aks-gpu-weather                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ nodepool1   (CPU pool, always on)                                      │ │
│  │   Standard_DC4ds_v3  ×1   4 vCPU / 32 GB RAM                           │ │
│  │   30 GB Ephemeral OS disk  (free, uses VM local SSD)                   │ │
│  │                                                                          │ │
│  │   Hosts:   weather-ui  (React/nginx, port 80)                          │ │
│  │            weather-api (FastAPI, port 8000)                             │ │
│  │            crop-api    (FastAPI, port 8001)                             │ │
│  │            system pods + ingress-nginx + NVIDIA device plugin (DS)      │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ gpupool1   (GPU pool, autoscale 0 ↔ 1)                                 │ │
│  │   Standard_NC4as_T4_v3   4 vCPU / 28 GB RAM / NVIDIA T4 16 GB           │ │
│  │   30 GB Ephemeral OS disk                                                │ │
│  │   Taint:  sku=gpu:NoSchedule    (only training jobs tolerate it)        │ │
│  │                                                                          │ │
│  │   Idle:    0 nodes,  $0 / hr                                            │ │
│  │   Active:  1 node only while a training Job is pending or running       │ │
│  │   Cooldown: scales back to 0 about 10 min after the Job finishes        │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  Ingress (nginx via App Routing add-on)   Public IP from Load Balancer     │
│    /             → weather-ui                                              │
│    /api          → weather-api                                              │
│    /api/crop     → crop-api                                                 │
│                                                                             │
│  Supporting resources in the same RG:                                       │
│    • acrgpuweather<suffix>  (Container Registry, Basic)                    │
│    • stgpuweather<suffix>   (Blob: weather-data, models, predictions,      │
│                              crop-data, crop-models — MI-only auth)         │
│    • kv-gpu-wea-<suffix>    (Key Vault)                                     │
│    • ai-gpu-weather         (Application Insights)                          │
└───────────────────────────────────────────────────────────────────────────┘

AKS auto-creates a second RG (MC_rg-gpu-weather_aks-gpu-weather_eastus2)
holding the VMSS, load balancer, NICs, NSGs, and 2 managed identities.
Do not modify it directly — it is deleted automatically when the cluster is deleted.
```

### How "GPU on demand" actually works

1. UI calls `POST /api/training/trigger` (or the weekly CronJob fires).
2. Backend creates a Kubernetes `Job` that requests `nvidia.com/gpu: 1`, tolerates `sku=gpu:NoSchedule`, and pins to `kubernetes.azure.com/accelerator=nvidia`.
3. The pod stays **Pending** — no GPU node exists yet.
4. **Cluster autoscaler** sees the pending GPU pod and spins up a GPU VM (`gpupool1` 0 → 1). This takes ~3–5 min.
5. The NVIDIA device plugin DaemonSet starts on the new node and advertises `nvidia.com/gpu: 1`.
6. The training pod is scheduled, runs (XGBoost ~12 min on T4 for our workload), saves the model to blob storage, exits.
7. ~10 min after the last GPU pod completes, the autoscaler scales `gpupool1` back to **0** → GPU cost returns to $0.

End-to-end smoke test we ran (see commit history): scale-up took ~5 min, training took ~12.6 min, scale-down completed ~9m 46s after the job finished. ✅

---

## Cost (current shape, approximate, East US 2 PAYG)

| State | Hourly | Monthly | Notes |
|---|---|---|---|
| **Cluster stopped** (`az aks stop`) | ~$0.01 | **~$5–10** | Storage + ACR + Key Vault + App Insights only |
| **Cluster running, GPU idle** | ~$0.24 | **~$170** | CPU node (`DC4ds_v3`) + LB + small services |
| **+ One training run (~13 min on GPU)** | — | **+ ~$0.25** each | GPU billed only during ~28 min (5 min spin-up + train + 10 min cooldown) |
| **GPU pinned on 24/7** (don't do this) | ~$0.53 + base | **~$554** | What you'd pay without the autoscale-to-0 setup |

The two main savers in this repo:

- **GPU pool autoscales 0 ↔ 1** with a matching taint, so only training jobs can ever land on it.
- **OS disks are 30 GB Ephemeral** on both pools (uses the VM's local SSD, costs $0 vs. ~$19/mo each for managed disks).

To pause completely (e.g. overnight, weekends): click **Stop** on the AKS resource in the portal, or `az aks stop ...`.

---

## Repository scripts

| Script | What it does | When to run |
|---|---|---|
| `setup-infrastructure.ps1` | Creates RG, ACR, AKS (CPU pool + tainted GPU pool with autoscale 0–1), 30 GB Ephemeral OS disks, storage + containers, Key Vault, App Insights. Patches the NVIDIA device plugin DaemonSet to tolerate the GPU taint and run only on GPU nodes. Writes resource names to `.infra-config.json`. | Once per fresh environment |
| `build-and-deploy.ps1` | `az acr build` for backend / frontend / crop images, applies all `k8s/` manifests, waits for pods, prints ingress IP. | After any code or k8s YAML change |
| `teardown.ps1` | Three modes: `full` (delete RG), `partial` (`az aks stop`), `gpu` (remove GPU node pool only). | When you want to pause or remove |
| `scripts/setup-managed-identity.ps1` | Re-runs the blob-storage RBAC + AZURE_CLIENT_ID patching step (called by the setup script; usable standalone if MI role assignments drift). | If managed identity access breaks |
| `scripts/gpu-activate.ps1` | Helper to manually toggle the GPU node pool count without waiting for the autoscaler. | Manual on/off for demos |

---

## Project Structure

```
aksgpu-temp/
├── backend/                    # FastAPI weather prediction API (Python + PyTorch)
│   ├── app/
│   │   ├── routers/            # predict, validate, training, data
│   │   ├── services/           # predictor, trainer, validator, blob, data_fetcher
│   │   └── models/             # LSTM, XGBoost, ARIMA
│   ├── scripts/train.py        # CLI entry point used by training Jobs
│   └── Dockerfile
├── crop/                       # FastAPI crop health API (21 features, NDVI/EVI)
├── frontend/                   # React + TypeScript + Vite + nginx
├── k8s/
│   ├── namespace.yaml
│   ├── configmap.yaml          # Auto-patched by setup with AZURE_CLIENT_ID + storage name
│   ├── crop-configmap.yaml
│   ├── secrets.yaml            # Empty by policy; MI auth is used at runtime
│   ├── backend-deployment-cpu.yaml
│   ├── crop-deployment.yaml
│   ├── frontend-deployment.yaml
│   ├── ingress.yaml
│   ├── training-cronjob.yaml   # Weekly retrain (Sun 02:00 UTC)
│   └── training-rbac.yaml
├── scripts/
├── setup-infrastructure.ps1
├── build-and-deploy.ps1
├── teardown.ps1
└── .infra-config.json          # Generated; holds the random suffix + resource names
```

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `Can't reach http://<ip>` | AKS is stopped | `az aks start --resource-group rg-gpu-weather --name aks-gpu-weather` |
| Pods stay `Pending` for >5 min after start | Node still booting | Wait 1–2 min after `provisioningState: Succeeded` |
| Training job `Pending` for 3–5 min | GPU pool scaling 0 → 1 — **normal** | Watch with `kubectl get nodes -w` |
| Training pod `Pending` with `Insufficient nvidia.com/gpu` after GPU node is Ready | NVIDIA device plugin not running on the GPU node | Make sure the DS has the `sku=gpu` toleration + `nodeSelector: kubernetes.azure.com/accelerator=nvidia` (setup script does this). Recheck with `kubectl get pods -n kube-system -l name=nvidia-device-plugin-ds -o wide` |
| App pods landed on the GPU node | GPU node missing the `sku=gpu:NoSchedule` taint | Setup script applies the taint at create time. To fix an existing pool: `az aks nodepool update ... --node-taints sku=gpu:NoSchedule` then restart deployments |
| 500 on predictions, log says blob storage access denied | Subscription policy disabled public network access on the storage account | `az storage account update --name stgpuweather<suffix> --resource-group rg-gpu-weather --public-network-access Enabled` then `kubectl rollout restart deploy weather-api crop-api -n gpu-weather` |
| Cluster create failed with *"OS disk of Ephemeral VM with size greater than 100 GB is not allowed"* | OS disk size exceeds the VM's cache disk | Setup script forces 30 GB Ephemeral on both pools to avoid this |

### Useful commands

```powershell
# Pod status / logs
kubectl get pods -n gpu-weather -o wide
kubectl logs -n gpu-weather deployment/weather-api --tail=80
kubectl logs -n gpu-weather deployment/crop-api    --tail=80

# Node pools
kubectl get nodes -o custom-columns="NAME:.metadata.name,POOL:.metadata.labels.agentpool"
az aks nodepool list --cluster-name aks-gpu-weather --resource-group rg-gpu-weather `
  --query "[].{Name:name, VmSize:vmSize, Count:count, OsDiskGB:osDiskSizeGb, Min:minCount, Max:maxCount, Taints:nodeTaints}" -o table

# Force GPU pool to 0 now (don't wait for autoscaler)
az aks nodepool update --cluster-name aks-gpu-weather --resource-group rg-gpu-weather --name gpupool1 --disable-cluster-autoscaler -o none
az aks nodepool scale  --cluster-name aks-gpu-weather --resource-group rg-gpu-weather --name gpupool1 --node-count 0       -o none
az aks nodepool update --cluster-name aks-gpu-weather --resource-group rg-gpu-weather --name gpupool1 --enable-cluster-autoscaler --min-count 0 --max-count 1 -o none
```

---

## Documentation map

| Document | When to read |
|---|---|
| [README.md](README.md) | You are here — start, stop, architecture, costs |
| [AZURE-RESOURCES.md](AZURE-RESOURCES.md) | What Azure resources exist and why the 3 resource groups |
| [APP-FLOW.md](APP-FLOW.md) | End-to-end request flow & component-level architecture |
| [AKS-GUIDE.md](AKS-GUIDE.md) | Tutorial on AKS / Kubernetes concepts using this project as the working example |
| [HOWTO.md](HOWTO.md) | Day-to-day build / deploy / debug commands |
| [PLAN.md](PLAN.md) | Historical design notes (not maintained for the current cluster shape) |
