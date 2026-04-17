# GPU Weather & Crop Health Prediction on AKS

A full-stack ML application that predicts weather and crop health using GPU-accelerated training on Azure Kubernetes Service (AKS). Features a React frontend, FastAPI backend, and automatic GPU scaling that costs $0 when idle.

```
http://48.211.182.37/
```

---

## Quick Start — How to Access the Demo

The AKS cluster is **stopped by default** to save costs. Follow these steps every time you want to use it.

### Step 1: Start the AKS Cluster

```powershell
az aks start --resource-group rg-gpu-weather --name aks-gpu-weather
```

Wait ~3-5 minutes. Check status:

```powershell
az aks show --resource-group rg-gpu-weather --name aks-gpu-weather `
  --query "provisioningState" --output tsv
# Wait until it says "Succeeded"
```

### Step 2: Fix Storage Access (Required Every Time)

> **Why?** Corporate Azure Policy disables public network access on the storage account daily. The API pods need network access to download ML models from blob storage.

```powershell
az storage account update `
  --name stgpuweatheropd5 `
  --resource-group rg-gpu-weather `
  --public-network-access Enabled
```

### Step 3: Restart the API Pods

The API pods need to be restarted to reconnect to storage after enabling access:

```powershell
az aks get-credentials --resource-group rg-gpu-weather --name aks-gpu-weather --overwrite-existing

kubectl rollout restart -n gpu-weather deployment/weather-api
kubectl rollout restart -n gpu-weather deployment/crop-api
```

Wait ~30 seconds for pods to restart:

```powershell
kubectl get pods -n gpu-weather
# All pods should show "Running" and "1/1 Ready"
```

### Step 4: Open the App

Open your browser:

```
http://48.211.182.37/
```

### Step 5: Stop When Done (Save Money!)

```powershell
az aks stop --resource-group rg-gpu-weather --name aks-gpu-weather
```

---

## One-Liner Startup (Copy-Paste)

```powershell
# Full startup sequence — copy and paste the entire block
az aks start --resource-group rg-gpu-weather --name aks-gpu-weather; `
az storage account update --name stgpuweatheropd5 --resource-group rg-gpu-weather --public-network-access Enabled; `
az aks get-credentials --resource-group rg-gpu-weather --name aks-gpu-weather --overwrite-existing; `
kubectl rollout restart -n gpu-weather deployment/weather-api; `
kubectl rollout restart -n gpu-weather deployment/crop-api; `
Write-Host "`nWaiting 30 seconds for pods to restart..."; `
Start-Sleep 30; `
kubectl get pods -n gpu-weather; `
Write-Host "`nApp ready at: http://48.211.182.37/"
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  AKS Cluster: aks-gpu-weather (East US 2)                               │
│  Resource Group: rg-gpu-weather                                          │
│                                                                           │
│  ┌─── CPU Node Pool (always on) ────────────────────────────────────┐  │
│  │  Standard_DC4ds_v3  (4 vCPU, 32 GB RAM)                          │  │
│  │                                                                    │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │  │
│  │  │  weather-ui   │  │  weather-api │  │  crop-api               │ │  │
│  │  │  React + nginx│  │  FastAPI     │  │  FastAPI                │ │  │
│  │  │  Port 80      │  │  Port 8000   │  │  Port 8001              │ │  │
│  │  └──────────────┘  └──────────────┘  └─────────────────────────┘ │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  ┌─── GPU Node Pool (scales to zero) ───────────────────────────────┐  │
│  │  NC4as_T4_v3  (4 vCPU, 28 GB RAM, NVIDIA T4 16 GB)              │  │
│  │  OFF by default — auto-starts when training is triggered         │  │
│  │  Cost: $0 when idle, ~$0.50/hr when training                     │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  Ingress Controller (nginx):                                              │
│    /            →  weather-ui   (Frontend)                                │
│    /api         →  weather-api  (Weather prediction API)                  │
│    /api/crop    →  crop-api     (Crop health API)                         │
│                                                                           │
│  External IP: 48.211.182.37                                               │
└─────────────────────────────────────────────────────────────────────────┘

Supporting Azure Resources:
  ├── acrgpuweatheropd5    (Container Registry — Docker images)
  ├── stgpuweatheropd5     (Storage Account — ML models & data)
  ├── kv-gpu-wea-opd5      (Key Vault — secrets)
  └── ai-gpu-weather       (Application Insights — monitoring)
```

## What the App Does

| Feature | Description |
|---|---|
| **Weather Prediction** | Predicts temperature, humidity, and conditions for the next 24 hours using LSTM, XGBoost, and ARIMA models |
| **Crop Health** | Predicts crop health based on weather conditions |
| **GPU Training** | Train ML models on-demand with automatic GPU scaling (T4) |
| **Multi-City** | Support for multiple cities (New York, Jakarta, etc.) |
| **Live Weather** | Fetches real-time weather data from Open-Meteo API |

## Project Structure

```
aksgpu/
├── backend/           # FastAPI weather prediction API (Python)
│   ├── app/
│   │   ├── routers/   # API endpoints (predict, train, data, health)
│   │   ├── services/  # Business logic (predictor, blob storage, training)
│   │   └── models/    # ML models (LSTM, XGBoost, ARIMA)
│   └── Dockerfile
├── crop/              # FastAPI crop health API (Python)
├── frontend/          # React TypeScript UI
│   └── src/
│       ├── components/ # Dashboard, Training, CitySelector
│       └── services/   # API client
├── k8s/               # Kubernetes manifests
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── secrets.yaml
│   ├── backend-deployment-cpu.yaml
│   ├── crop-deployment.yaml
│   ├── frontend-deployment.yaml
│   ├── ingress.yaml
│   └── training-cronjob.yaml
├── scripts/           # Utility scripts
├── build-and-deploy.ps1       # Build images & deploy to AKS
├── setup-infrastructure.ps1   # Create all Azure resources
└── teardown.ps1               # Delete everything
```

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| **500 error on predictions** | Storage public access disabled by policy | Run: `az storage account update --name stgpuweatheropd5 --resource-group rg-gpu-weather --public-network-access Enabled` then restart pods |
| **Can't reach http://48.211.182.37** | AKS is stopped | Run: `az aks start --resource-group rg-gpu-weather --name aks-gpu-weather` |
| **Pods in "Pending"** | Node still booting after AKS start | Wait 1-2 minutes |
| **Training stuck in "Pending"** | GPU node scaling from 0 to 1 | Normal — takes 3-5 minutes |
| **"BlobStorageService not connected"** | Storage network access disabled | Fix storage access (Step 2 above), then restart API pods |

### Check Pod Logs

```powershell
kubectl logs -n gpu-weather deployment/weather-api --tail=50
kubectl logs -n gpu-weather deployment/crop-api --tail=50
kubectl logs -n gpu-weather deployment/weather-ui --tail=50
```

### Verify Storage Access

```powershell
# Check if storage is accessible
az storage account show --name stgpuweatheropd5 `
  --query "{publicNetworkAccess:publicNetworkAccess}" --output json

# Should show: "publicNetworkAccess": "Enabled"
# If "Disabled" → run the fix in Step 2
```

## Documentation

| Document | Description |
|---|---|
| [AKS-GUIDE.md](AKS-GUIDE.md) | Complete AKS/Kubernetes tutorial using this project as examples |
| [APP-FLOW.md](APP-FLOW.md) | How the application flow works |
| [AZURE-RESOURCES.md](AZURE-RESOURCES.md) | Azure resources and configuration |
| [HOWTO.md](HOWTO.md) | How-to guides for common tasks |
| [PLAN.md](PLAN.md) | Project plan and architecture decisions |

## Cost

| Resource | Monthly Cost | Notes |
|---|---|---|
| CPU Node Pool | ~$140 | Always on when cluster is running |
| GPU Node Pool | $0 - $15/day | Scales to 0 automatically |
| Container Registry | ~$5 | Basic tier |
| Storage | ~$2 | Standard LRS |
| **Total (cluster stopped)** | **~$7** | Storage + ACR only |
| **Total (cluster running, no training)** | **~$147** | Add CPU nodes |
| **Total (cluster running + daily training)** | **~$170** | Add occasional GPU |

> **Tip:** Stop the cluster when not in use: `az aks stop --resource-group rg-gpu-weather --name aks-gpu-weather`
