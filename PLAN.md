# Plan: GPU Weather & Crop Health Prediction on AKS — Full Production Guide

## TL;DR

Build end-to-end GPU-accelerated prediction applications on Azure AKS:

1. **Weather Prediction** — Three ML models (LSTM, XGBoost, ARIMA) train on hourly weather data from Open-Meteo. Serves forecasts, backtesting, model comparison, and natural language weather reports via FastAPI.

2. **Crop Health Prediction** — Same three ML models train on multi-source daily data: Open-Meteo weather+soil, Open-Meteo air quality, and NASA MODIS satellite vegetation indices (NDVI/EVI). Predicts vegetation health and stress levels 32 days ahead. Includes data preview with source grouping and XLSX export.

Both apps share the same AKS cluster, ACR, Blob Storage, and GPU node pool (T4 autoscale 0-1). A unified React frontend with app switcher lets users toggle between modules. GPU nodes only run when training — saving ~$300/month when idle.

### Key Additions (since initial weather-only version):
- `aks/crop/` — Separate backend for crop health (21 features from 3 API sources)
- `/api/report` — Template-based weather report generator (daily breakdown, alerts, recommendations)
- Frontend app switcher: **Weather Prediction | Crop Health**
- Training activity monitor with live pod logs
- Data preview table (color-coded by source) + XLSX download
- Blob containers: `crop-models/`, `crop-data/` (in addition to `models/`, `weather-data/`)

This document is the **complete reference** for any agent implementing this project.

---

## SOP: Step-by-Step Deployment Flow

This is the **exact order of operations** to get the system running. Some steps are automated (script), some require manual Portal action.

### Flow Diagram

```
START
  |
  v
[1] Run .\setup-infrastructure.ps1          <-- AUTOMATED
  |   Creates: Resource Group, ACR, AKS (CPU pool), Storage, Key Vault
  |   May FAIL at GPU node pool step (Azure Policy blocks CLI creation)
  |
  v
[2] Add GPU Node Pool via Azure Portal       <-- MANUAL (if step 1 failed on GPU)
  |   Portal > AKS > Node pools > + Add
  |   See detailed steps below
  |
  v
[3] Run .\build-and-deploy.ps1               <-- AUTOMATED
  |   Builds Docker images, pushes to ACR, deploys K8s manifests
  |   Triggers initial model training on GPU
  |
  v
[4] Open browser > http://<INGRESS_IP>/      <-- VERIFY
  |   Dashboard should show forecast charts
  |
  v
[5] After demo: Run .\teardown.ps1           <-- AUTOMATED
    Cleans up all resources
```

### Step 2 Details: Adding GPU Node Pool via Azure Portal

**When to do this**: If `setup-infrastructure.ps1` shows `[FAIL] Failed to add GPU node pool` or `RequestDisallowedByPolicy` error.

1. Go to [Azure Portal](https://portal.azure.com)
2. Search for **"aks-gpu-weather"** in the top search bar
3. Click on the AKS cluster resource
4. In the left menu, click **Settings > Node pools**
5. Click **+ Add node pool** at the top
6. Fill in:

| Field | Value |
|-------|-------|
| Node pool name | `gpupool1` |
| Mode | User |
| OS SKU | Ubuntu Linux |
| Availability zones | None |
| Node size | Click "Choose a size" > search `NC4as_T4_v3` > select **Standard_NC4as_T4_v3** |
| Scale method | Autoscale |
| Minimum node count | `0` |
| Maximum node count | `1` |

7. Click **Optional settings** tab:
   - **Max pods per node**: Change from 10 to **30** (IMPORTANT - default 10 is too small, system pods fill it)

8. Click **Review + create** > **Create**
9. Wait 5-10 minutes for the GPU node to provision

**If you see a "Policy" error in Portal**: You need MCAPS policy exception. Email SCMTeam@microsoft.com (see SOP in this doc).

### Step 2 Verification

After adding the GPU node pool (either via script or Portal):

```powershell
# Verify GPU node exists
kubectl get nodes
# Should show 2 nodes (1 CPU + 1 GPU)

# Verify GPU label
kubectl get nodes -l gpu-type=nvidia-t4
# Should show 1 GPU node

# Verify NVIDIA plugin sees the GPU
kubectl describe node <gpu-node-name> | Select-String "nvidia.com/gpu"
# Should show: nvidia.com/gpu: 1
```

### Quick Reference: All Scripts

| Script | What It Does | When to Run |
|--------|-------------|-------------|
| `.\setup-infrastructure.ps1` | Creates all Azure resources (RG, ACR, AKS, Storage) | First time setup |
| `.\build-and-deploy.ps1` | Builds images, deploys to K8s, sets up MI, triggers training | After infrastructure is ready |
| `.\teardown.ps1` | Destroys resources (3 modes: full/partial/gpu) | After demo or to save costs |
| `.\scripts\gpu-activate.ps1 -Action activate` | Scales GPU pool from 0 to 1 | When you need GPU |
| `.\scripts\gpu-activate.ps1 -Action deactivate` | Scales GPU pool to 0, switches to CPU mode | To save ~$15/day |
| `.\scripts\setup-managed-identity.ps1` | Assigns Blob RBAC to AKS MI, patches configmap | After infra setup (auto-called by build-and-deploy) |
| `.\setup-local-python.ps1` | Creates Python venv for local testing | Optional, for local dev |

---

## Table of Contents

1. [Scenario & How It Works](#scenario--how-it-works)
2. [Architecture Overview](#architecture-overview)
3. [Azure Services & Cost Breakdown](#azure-services--cost-breakdown)
4. [Phase 1: Prerequisites & Azure Infrastructure](#phase-1-prerequisites--azure-infrastructure)
5. [Phase 2: Backend — Data Pipeline & ML Model (with Code)](#phase-2-backend--data-pipeline--ml-model)
6. [Phase 3: Frontend — React Dashboard](#phase-3-frontend--react-dashboard)
7. [Phase 4: Kubernetes Manifests](#phase-4-kubernetes-manifests)
8. [Phase 5: Build, Deploy & Initial Training](#phase-5-build-deploy--initial-training)
9. [Phase 6: GPU Lifecycle Management (Activate/Deactivate)](#phase-6-gpu-lifecycle-management)
10. [Phase 7: Day-to-Day Operations & Monitoring](#phase-7-day-to-day-operations--monitoring)
11. [Phase 8: UI Walkthrough — How Users Interact](#phase-8-ui-walkthrough)
12. [Phase 9: Updating the Model & Retraining](#phase-9-updating-the-model--retraining)
13. [Phase 10: Teardown & Cleanup](#phase-10-teardown--cleanup)
14. [Alternative Use Case: YOLO Object Detection](#alternative-use-case-yolo)
15. [Common Errors & Fixes (Reference)](#common-errors--fixes)

---

## Scenario & How It Works

### The Business Story
"We want to demonstrate GPU computing on Azure Kubernetes Service. Our demo predicts weather for New York City using a neural network trained on real historical data. The UI shows a 7-day forecast and a **validation view** that proves the model is accurate by comparing past predictions against what actually happened."

### How the ML Pipeline Works (Step by Step for Newbies)

**What is an LSTM?**
LSTM (Long Short-Term Memory) is a type of neural network designed for sequential data — like weather readings over time. It "remembers" patterns from the past (e.g., "temperature usually drops after humidity spikes") and uses those patterns to predict the future.

**The Training Process:**
1. **Fetch Data**: Download 2 years of hourly weather data from Open-Meteo API (free, no API key). Data includes temperature, humidity, wind speed, precipitation, and pressure for New York City.
2. **Prepare Data**: 
   - Normalize all values to 0-1 range (so temperature 0-40°C becomes 0.0-1.0). This helps the neural network learn faster.
   - Create "windows" — take 168 consecutive hours (7 days) of data as input, and the next 24 hours as the target to predict.
   - Split into 80% training data and 20% test data.
3. **Train the Model**: 
   - Feed windows through the LSTM network on the GPU.
   - The model makes predictions, compares them to actual values, and adjusts its internal weights (learning).
   - Repeat for 50 rounds (epochs) or until the model stops improving (early stopping).
   - **On T4 GPU**: ~30-60 minutes. On CPU: ~4-8 hours. This is the GPU value proposition.
4. **Save the Model**: Save the trained model file (`.pt`) and the normalization parameters (`.pkl`) to Azure Blob Storage.
5. **Inference**: When a user requests a forecast, load the saved model, feed in the last 7 days of real weather data, and output the next 7 days of predictions.

**The Validation/Backtesting Process (How We Prove It Works):**
1. Take all data but **exclude the last 14 days**
2. Train (or use a model trained) on that subset
3. Ask the model: "What do you predict for those 14 days?"
4. Compare predictions against the **actual observed weather** for those 14 days
5. Calculate error metrics:
   - **MAE** (Mean Absolute Error): "On average, how many degrees off are we?" → Target: < 2°C
   - **RMSE** (Root Mean Square Error): "How bad are the worst misses?" → Target: < 3°C
   - **R²** (R-squared): "What percentage of weather variation can we explain?" → Target: > 0.7 (70%)
6. Display both lines (predicted vs actual) on the same chart so users visually see accuracy

### Data Flow Diagram
```
Open-Meteo API ──(HTTP GET)──▶ data_fetcher.py ──▶ Raw JSON
                                                       │
                                                       ▼
                                               Preprocessing
                                         (normalize, create windows)
                                                       │
                                                       ▼
                                              ┌────────────────┐
                                              │  LSTM Training  │
                                              │  (GPU - T4)     │
                                              │  50 epochs      │
                                              │  ~30-60 min     │
                                              └────────┬───────┘
                                                       │
                                                       ▼
                                              model.pt + scaler.pkl
                                              saved to Blob Storage
                                                       │
                                                       ▼
                                              ┌────────────────┐
                                              │  FastAPI Server │
                                              │  loads model    │
                                              │  serves /api/*  │
                                              └────────┬───────┘
                                                       │
                                                       ▼
                                              React Dashboard
                                              (charts, metrics)
```

---

## Worldwide Coordinate Support

### How It Works

The system supports **any location on Earth** -- users can pick from 10 preset cities or enter custom latitude/longitude coordinates.

### Frontend: City Selector
- **10 preset cities**: New York, London, Tokyo, Sydney, Sao Paulo, Paris, Singapore, Dubai, Mumbai, Jakarta
- **Custom coordinates**: Select "Custom coordinates..." from the dropdown, enter a name + lat/lon, click "Go"
- Coordinates are validated: latitude -90 to 90, longitude -180 to 180
- Current coordinates always displayed next to the selector

### Backend: API Endpoints Accept Coordinates
All data endpoints accept optional `lat` and `lon` query parameters:

```
GET /api/predict?city=tokyo&days=7&lat=35.68&lon=139.69
GET /api/validate?city=jakarta&lookback_days=14&lat=-6.21&lon=106.85
POST /api/training/trigger?city=mumbai&lat=19.08&lon=72.88
```

- If `lat` and `lon` are provided, the backend fetches weather data for those exact coordinates from Open-Meteo
- If omitted, falls back to the default city from environment config (CITY_LAT/CITY_LON)
- Open-Meteo has global coverage at 0.1 degree resolution -- any coordinate works

### Training: Per-City Models

The training script accepts city and coordinates as CLI arguments:

```powershell
# Train for default city (from env vars)
python -m scripts.train

# Train for specific city with coordinates
python -m scripts.train --city tokyo --lat 35.68 --lon 139.69
python -m scripts.train --city jakarta --lat -6.21 --lon 106.85
python -m scripts.train --city dubai --lat 25.28 --lon 55.30

# Train with more historical data
python -m scripts.train --city london --lat 51.51 --lon -0.13 --years 5
```

Models are saved with the city name in the filename:
```
models/
  tokyo_20260327_021500.pt
  tokyo_20260327_021500_scaler.pkl
  tokyo_20260327_021500_metrics.json    # contains {"city":"tokyo","lat":35.68,"lon":139.69,...}
  jakarta_20260328_031200.pt
  jakarta_20260328_031200_scaler.pkl
```

### Important: Model Accuracy by Location

The LSTM model learns weather patterns (temperature cycles, humidity correlations, seasonal trends) from the **training data**. When used for a different city:

| Scenario | Accuracy | Explanation |
|----------|----------|-------------|
| Same city as training data | Best | Model learned this city's exact patterns |
| Similar climate (e.g., trained NYC, predict London) | Good | Temperate climate patterns transfer well |
| Very different climate (e.g., trained NYC, predict Dubai) | Fair | Model captures general trends but misses desert-specific patterns |
| Trained separately for each city | Best for all | Each city has its own model fine-tuned to local patterns |

**Recommendation**: For demo purposes, one model works for any coordinate. For production accuracy, train a model per city/region using `python -m scripts.train --city <name> --lat <lat> --lon <lon>`.

---

## Architecture Overview

```
┌───────────────────────────────────────────────────────────────────────┐
│                         Azure AKS Cluster                              │
│                                                                        │
│   CPU Node Pool (DC2ds_v3 x 2)            GPU Node Pool (T4 x 0-1)   │
│   ┌─────────────────┐                     ┌────────────────────┐      │
│   │  Frontend Pod    │                     │  Training CronJob   │      │
│   │  React+Nginx     │                     │  Weekly Sunday 2AM  │      │
│   │  Port 80         │                     │  GPU: nvidia.com/1  │      │
│   └─────────────────┘                     └────────────────────┘      │
│   ┌─────────────────┐                                                  │
│   │  Backend Pod     │    GPU node autoscales to 0 when no training    │
│   │  FastAPI+PyTorch │    is running. Cost = $0 when idle.             │
│   │  (CPU inference) │                                                  │
│   │  Port 8000       │                                                  │
│   └─────────────────┘                                                  │
│   │  Frontend Pod    │                     ┌────────────────────┐      │
│   │  (replica 2)     │                     │  Training CronJob   │      │
│   └─────────────────┘                     │  Weekly Sunday 2AM  │      │
│                                           │  GPU: nvidia.com/1  │      │
│   ┌─────────────────┐                     └────────────────────┘      │
│   │  NGINX Ingress   │                                                 │
│   │  /    → frontend │                                                 │
│   │  /api → backend  │                                                 │
│   └─────────────────┘                                                  │
│                                                                        │
│   ┌─────────────────┐                                                  │
│   │  NVIDIA Device   │  (DaemonSet on GPU nodes only)                  │
│   │  Plugin          │                                                  │
│   └─────────────────┘                                                  │
└───────────────────────────────────────────────────────────────────────┘
         │                                          │
         │ HTTPS                                    │ HTTPS
         ▼                                          ▼
    User Browser                           Azure Blob Storage
                                           ├── weather-data/
                                           ├── models/
                                           └── predictions/
                                                    │
                                                    │ HTTP
                                                    ▼
                                            Open-Meteo API
                                            (free, no key)
```

**Why two node pools?**
- **CPU pool** (~$140/month, always on): 2 nodes × 2 vCPU each (4 vCPU total). Runs frontend, backend, ingress, system pods.
- **GPU pool** ($350-450/month, autoscales to 0): only runs when GPU workloads are scheduled. When no training or inference is happening, the GPU node shuts down automatically. This is how you save money.

---

## Azure Services & Cost Breakdown

| Service | SKU/Tier | What It Does (Newbie Explanation) | Est. Monthly Cost |
|---------|----------|-----------------------------------|-------------------|
| **AKS** (system pool) | Standard_DC2ds_v3 × 2 nodes | The "brain" of the cluster. 2 vCPU per node × 2 = 4 vCPU total. Runs frontend, backend, ingress controller, core Kubernetes services. Always on. | ~$140 |
| **AKS** (GPU pool) | Standard_NC4as_T4_v3 × 0-1 nodes | The "muscle". Has an NVIDIA T4 GPU (16GB VRAM). Runs ML training and inference. **Scales to 0 when idle to save money**. | ~$0-450 |
| **ACR** (Container Registry) | Basic | A private Docker Hub just for you. Stores your container images so AKS can pull them. | ~$5 |
| **Blob Storage** | Standard LRS | File storage in the cloud. Stores weather data (CSV/Parquet), trained model files (.pt), and prediction logs. | ~$2 |
| **Application Insights** | Pay-as-you-go | Monitoring dashboard. Shows API response times, error rates, and custom metrics like prediction accuracy. | ~$1-5 |
| **Key Vault** | Standard | A secure safe for secrets (storage keys, API keys if any). Pods access secrets without hardcoding them. | ~$0.03 |
| **Log Analytics** | Pay-as-you-go | Collects all logs from your Kubernetes cluster — pod logs, node events, training output. Searchable. | ~$5 |

**Cost Scenarios:**
- **Demo mode** (GPU always on): ~$600/month
- **Smart mode** (GPU on only during training/inference): ~$200-250/month
- **Parked mode** (GPU pool scaled to 0, CPU pool still running frontend): ~$150/month
- **Full teardown** (delete everything): $0/month

---

## Phase 1: Prerequisites & Azure Infrastructure

### Step 1.0: Prerequisites Check

**What you need on your local machine before starting:**

```powershell
# Check Azure CLI is installed
az --version
# Expected: azure-cli 2.x.x or higher

# Check kubectl is installed
kubectl version --client
# Expected: Client Version: v1.x.x

# Check Docker is installed (needed to build images)
docker --version
# Expected: Docker version 24.x.x or higher

# Check Helm is installed (needed for NVIDIA plugin)
helm version
# Expected: version.BuildInfo{Version:"v3.x.x"}

# Check Node.js is installed (needed for frontend build)
node --version
# Expected: v20.x.x or higher

npm --version
# Expected: 10.x.x or higher
```

**If any tool is missing, install it:**
```powershell
# Azure CLI (Windows)
winget install Microsoft.AzureCLI

# kubectl (via Azure CLI)
az aks install-cli

# Docker Desktop (Windows)
winget install Docker.DockerDesktop

# Helm
winget install Helm.Helm

# Node.js (LTS)
winget install OpenJS.NodeJS.LTS
```

**Login to Azure:**
```powershell
az login
# A browser window opens — sign in with your Azure account

# Set your subscription (if you have multiple)
az account list --output table
az account set --subscription "YOUR_SUBSCRIPTION_ID"

# Verify:
az account show --query name -o tsv
```

### Step 1.1: Register Required Azure Providers

```powershell
# These providers must be registered for AKS and GPU to work
az provider register --namespace Microsoft.ContainerService
az provider register --namespace Microsoft.Compute
az provider register --namespace Microsoft.Storage
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.OperationalInsights

# Wait for all to show "Registered" status:
az provider show --namespace Microsoft.ContainerService --query registrationState -o tsv
```

### Step 1.2: GPU Capacity Discovery (BLOCKING — Must Pass Before Proceeding)

**This is a HARD GATE. Do NOT create any Azure resources until GPU availability is confirmed.**

GPU VMs are specialty hardware — not every region has them, and your subscription may not have quota allocated. This step finds a region where GPU is available AND you have quota.

#### Step 1.2.1: Scan Multiple Regions for GPU SKU Availability

```powershell
# Check T4 GPU VM availability across all candidate regions
# We check multiple regions because GPU capacity varies by region

$regions = @("eastus", "eastus2", "westus2", "westus3", "southcentralus", "northcentralus", "westeurope", "northeurope", "southeastasia", "australiaeast")

Write-Host "`n=== GPU SKU Availability Scan ===" -ForegroundColor Cyan
foreach ($region in $regions) {
    $sku = az vm list-skus --location $region --resource-type virtualMachines --query "[?name=='Standard_NC4as_T4_v3'].{Name:name, Restrictions:restrictions[0].reasonCode}" -o tsv 2>$null
    if ($sku) {
        $restriction = ($sku -split "`t")[1]
        if ($restriction -eq "NotAvailableForSubscription") {
            Write-Host "  $region : SKU exists but NOT AVAILABLE for your subscription" -ForegroundColor Yellow
        } else {
            Write-Host "  $region : AVAILABLE" -ForegroundColor Green
        }
    } else {
        Write-Host "  $region : SKU does not exist in this region" -ForegroundColor Red
    }
}
```

**Expected output example:**
```
=== GPU SKU Availability Scan ===
  eastus : AVAILABLE
  eastus2 : SKU does not exist in this region
  westus2 : AVAILABLE
  westus3 : SKU does not exist in this region
  southcentralus : AVAILABLE
  northcentralus : SKU exists but NOT AVAILABLE for your subscription
  westeurope : AVAILABLE
  northeurope : AVAILABLE
  southeastasia : SKU does not exist in this region
  australiaeast : SKU does not exist in this region
```

**Pick any region showing "AVAILABLE"**. If none show AVAILABLE, try alternative GPU SKUs (see Step 1.2.4 below).

#### Step 1.2.2: Check Your GPU vCPU Quota in the Chosen Region

Even if the SKU is available in a region, your subscription needs **quota** (vCPU allocation) to actually create the VM.

```powershell
# Replace "eastus" with YOUR chosen region from Step 1.2.1
$REGION = "eastus"

# Check NC-series vCPU quota
az vm list-usage --location $REGION --query "[?contains(name.value, 'NC')].{Name:name.localizedValue, Used:currentValue, Limit:limit}" -o table

# Also check the specific family quota
az vm list-usage --location $REGION --query "[?contains(name.value, 'standard_NC')].{Name:name.localizedValue, Used:currentValue, Limit:limit}" -o table
```

**Interpreting the output:**

| Scenario | Used | Limit | Can Deploy? | Action |
|----------|------|-------|-------------|--------|
| Limit = 0 | 0 | 0 | NO | Request quota increase (Step 1.2.3) |
| Limit >= 4, Used = 0 | 0 | 4+ | YES | Proceed to Step 1.3 |
| Limit >= 4, Used = Limit | 4 | 4 | NO | Free up existing GPUs or request increase |
| Limit < 4 | 0 | 2 | NO | Need at least 4 vCPUs for NC4as_T4_v3 |

**Standard_NC4as_T4_v3 requires 4 vCPUs** — your quota limit must be >= 4.

#### Step 1.2.3: Request GPU Quota Increase (If Needed)

If your quota limit is 0 or less than 4 vCPUs:

**Option A — Azure Portal (Recommended for newbies):**
1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to: **Subscriptions** → select your subscription → **Usage + quotas**
3. In the search filter type: `NC` or `Standard NCASv3_T4`
4. Find the row for your target region (e.g., eastus)
5. Click the **pencil icon** or **"Request increase"**
6. Set new limit to **4** (minimum) or **8** (recommended, gives room for scaling)
7. Add justification: "GPU compute for ML training demo on AKS"
8. Submit

**Option B — Azure CLI:**
```powershell
# Request quota increase via CLI
az quota create \
  --resource-name "Standard NCASv3_T4 Family vCPUs" \
  --scope "/subscriptions/$(az account show --query id -o tsv)/providers/Microsoft.Compute/locations/$REGION" \
  --limit-object value=4 \
  --resource-type dedicated
```

**Approval timeline:**
| Subscription Type | Typical Wait |
|-------------------|-------------|
| Enterprise Agreement (EA) | Instant to 1 hour |
| Pay-as-you-go | 1-3 business days |
| Free trial / Student | Usually denied — GPU not available on free tiers |
| MSDN / Visual Studio | 1-2 business days, limited to small quotas |

**IMPORTANT**: Do NOT proceed until quota is confirmed. Verify again:
```powershell
az vm list-usage --location $REGION --query "[?contains(name.value, 'NC')].{Name:name.localizedValue, Used:currentValue, Limit:limit}" -o table
# Limit column must show >= 4
```

#### Step 1.2.4: Alternative GPU SKUs (If T4 is Unavailable)

If Standard_NC4as_T4_v3 is not available in any region, try these alternatives:

```powershell
# Scan for ALL available GPU SKUs in a region
az vm list-skus --location eastus --resource-type virtualMachines --query "[?contains(name, 'Standard_NC') || contains(name, 'Standard_ND') || contains(name, 'Standard_NV')].{Name:name, vCPUs:capabilities[?name=='vCPUs'].value|[0], GPUs:capabilities[?name=='GPUs'].value|[0], Restrictions:restrictions[0].reasonCode}" -o table
```

| Alternative SKU | GPU | VRAM | vCPUs | ~Monthly Cost | Notes |
|----------------|-----|------|-------|---------------|-------|
| **Standard_NC4as_T4_v3** (primary) | 1× T4 | 16 GB | 4 | ~$350 | Best cost/performance for demo |
| Standard_NC8as_T4_v3 | 1× T4 | 16 GB | 8 | ~$700 | Same GPU, more CPU (overkill for demo) |
| Standard_NC6s_v3 | 1× V100 | 16 GB | 6 | ~$900 | Faster training, more expensive |
| Standard_NV6ads_A10_v5 | 1× A10 | 24 GB | 6 | ~$450 | Good for Stable Diffusion (more VRAM) |
| Standard_NC24ads_A100_v4 | 1× A100 | 80 GB | 24 | ~$3,500 | Overkill for demo — enterprise only |

**If using an alternative SKU**, update these values throughout the plan:
- `--node-vm-size Standard_NC4as_T4_v3` → your chosen SKU
- `--labels accelerator=nvidia-t4` → update label (e.g., `nvidia-v100`, `nvidia-a10`)
- `nodeSelector: accelerator: nvidia-t4` → update in all K8s manifests
- GPU node taint stays the same: `sku=gpu:NoSchedule`

#### Step 1.2.5: Final GPU Readiness Confirmation

**Run this script to confirm everything is ready before proceeding:**

```powershell
$REGION = "eastus"  # YOUR chosen region
$SKU = "Standard_NC4as_T4_v3"  # YOUR chosen SKU
$REQUIRED_VCPUS = 4

Write-Host "`n=== GPU Deployment Readiness Check ===" -ForegroundColor Cyan

# Check 1: SKU exists and is available
$skuCheck = az vm list-skus --location $REGION --resource-type virtualMachines --query "[?name=='$SKU'].restrictions[0].reasonCode" -o tsv 2>$null
if (-not $skuCheck) {
    Write-Host "  [PASS] SKU $SKU is available in $REGION" -ForegroundColor Green
} elseif ($skuCheck -eq "NotAvailableForSubscription") {
    Write-Host "  [FAIL] SKU $SKU is NOT available for your subscription in $REGION" -ForegroundColor Red
    Write-Host "         Try a different region or request access." -ForegroundColor Yellow
    exit 1
} else {
    Write-Host "  [WARN] SKU $SKU has restriction: $skuCheck" -ForegroundColor Yellow
}

# Check 2: Quota is sufficient
$quotaInfo = az vm list-usage --location $REGION --query "[?contains(name.value, 'NC')]" -o json | ConvertFrom-Json
$ncQuota = $quotaInfo | Where-Object { $_.name.localizedValue -like "*NC*" } | Select-Object -First 1
if ($ncQuota) {
    $available = $ncQuota.limit - $ncQuota.currentValue
    if ($available -ge $REQUIRED_VCPUS) {
        Write-Host "  [PASS] GPU quota: $($ncQuota.currentValue)/$($ncQuota.limit) used, $available available (need $REQUIRED_VCPUS)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] GPU quota: $($ncQuota.currentValue)/$($ncQuota.limit) used, only $available available (need $REQUIRED_VCPUS)" -ForegroundColor Red
        Write-Host "         Request quota increase via Azure Portal." -ForegroundColor Yellow
        exit 1
    }
} else {
    Write-Host "  [FAIL] No NC-series quota found. Request GPU quota first." -ForegroundColor Red
    exit 1
}

# Check 3: Azure provider is registered
$providerState = az provider show --namespace Microsoft.Compute --query registrationState -o tsv
if ($providerState -eq "Registered") {
    Write-Host "  [PASS] Microsoft.Compute provider is registered" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] Microsoft.Compute provider state: $providerState" -ForegroundColor Red
    exit 1
}

Write-Host "`n  ALL CHECKS PASSED — Ready to deploy GPU in $REGION" -ForegroundColor Green
Write-Host "  Selected SKU: $SKU" -ForegroundColor Green
Write-Host "  Save this region — use it for ALL subsequent commands.`n" -ForegroundColor Cyan
```

**All 3 checks must show [PASS] before proceeding to Step 1.3.**

If any check fails:
- **SKU not available**: Re-run Step 1.2.1 with different regions, or try alternative SKUs from Step 1.2.4
- **Quota insufficient**: Follow Step 1.2.3 to request increase, then re-run this check
- **Provider not registered**: Run `az provider register --namespace Microsoft.Compute` and wait 2 minutes

### Step 1.3: Create Resource Group

```powershell
# All resources go into one resource group for easy management
az group create --name rg-gpu-weather --location eastus

# Verify:
az group show --name rg-gpu-weather --query provisioningState -o tsv
# Expected: Succeeded
```

### Step 1.4: Create Azure Container Registry

```powershell
# ACR name must be globally unique — lowercase, no hyphens
az acr create \
  --name acrgpuweather \
  --resource-group rg-gpu-weather \
  --sku Basic

# Verify:
az acr show --name acrgpuweather --query loginServer -o tsv
# Expected: acrgpuweather.azurecr.io
```

**If name is taken**: try `acrgpuweatherXX` where XX is a random number.

### Step 1.5: Create AKS Cluster with System (CPU) Pool

```powershell
az aks create \
  --resource-group rg-gpu-weather \
  --name aks-gpu-weather \
  --node-count 2 \
  --node-vm-size Standard_DS2_v2 \
  --generate-ssh-keys \
  --attach-acr acrgpuweather \
  --enable-app-routing \
  --network-plugin azure \
  --enable-managed-identity \
  --location eastus

# This takes 5-10 minutes. Wait for it to complete.

# Get kubectl credentials:
az aks get-credentials --resource-group rg-gpu-weather --name aks-gpu-weather

# Verify cluster is running:
kubectl get nodes
# Expected: 2 nodes in "Ready" state
```

**Common errors:**
- `QuotaExceeded`: your subscription doesn't have enough vCPU quota for DS2_v2. Request increase or use a smaller VM.
- `SubnetIsFull`: the virtual network ran out of IP addresses. Delete the cluster and try again — Azure will create a new VNet.

### Step 1.6: Add GPU Node Pool

```powershell
az aks nodepool add \
  --resource-group rg-gpu-weather \
  --cluster-name aks-gpu-weather \
  --name gpupool \
  --node-count 1 \
  --node-vm-size Standard_NC4as_T4_v3 \
  --node-taints sku=gpu:NoSchedule \
  --labels accelerator=nvidia-t4 \
  --enable-cluster-autoscaler \
  --min-count 0 \
  --max-count 1

# This takes 5-10 minutes.
```

**What each flag means (for newbies):**
- `--node-taints sku=gpu:NoSchedule`: Tells Kubernetes "don't put regular pods on this expensive GPU node — only pods that specifically request GPU"
- `--labels accelerator=nvidia-t4`: A label so our pods can say "I want to run on a GPU node"
- `--enable-cluster-autoscaler --min-count 0 --max-count 1`: The node pool can shrink to 0 nodes (no cost!) when no GPU pods need it, and grow to 1 node when a GPU pod is scheduled

**Verify GPU node is running:**
```powershell
kubectl get nodes -l accelerator=nvidia-t4
# Expected: 1 node with status "Ready"

kubectl get nodes
# Expected: 3 total nodes (2 CPU + 1 GPU)
```

### Step 1.7: Install NVIDIA Device Plugin

The GPU hardware exists on the node, but Kubernetes doesn't know about it yet. The NVIDIA device plugin exposes GPUs as a schedulable resource.

```powershell
# Install NVIDIA device plugin DaemonSet
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.1/nvidia-device-plugin.yml

# Wait 30 seconds, then verify:
kubectl get pods -n kube-system | findstr nvidia
# Expected: nvidia-device-plugin-daemonset-XXXXX  1/1  Running

# Verify GPU is visible to Kubernetes:
kubectl get nodes -l accelerator=nvidia-t4 -o jsonpath='{.items[0].status.capacity.nvidia\.com/gpu}'
# Expected: 1
```

**If the plugin pod is CrashLoopBackOff:**
- The AKS node image may not have GPU drivers. Check with:
  ```powershell
  kubectl logs -n kube-system $(kubectl get pods -n kube-system -l app=nvidia-device-plugin -o jsonpath='{.items[0].metadata.name}')
  ```
- If you see "NVML not found": the node image needs GPU drivers. AKS Standard_NC* VMs should have them pre-installed. Try deleting and re-adding the GPU node pool.

### Step 1.8: Create Blob Storage

```powershell
# Storage account name must be globally unique, lowercase, no hyphens, 3-24 chars
az storage account create \
  --name stgpuweather \
  --resource-group rg-gpu-weather \
  --sku Standard_LRS \
  --kind StorageV2 \
  --location eastus

# Create containers for organizing data
az storage container create --name weather-data --account-name stgpuweather
az storage container create --name models --account-name stgpuweather
az storage container create --name predictions --account-name stgpuweather

# Get storage account key (needed for pod access)
az storage account keys list --account-name stgpuweather --query "[0].value" -o tsv
# SAVE THIS KEY — you'll need it for Kubernetes secrets
```

### Step 1.9: Create Key Vault and Application Insights

```powershell
az keyvault create \
  --name kv-gpu-weather \
  --resource-group rg-gpu-weather \
  --location eastus

az monitor app-insights component create \
  --app ai-gpu-weather \
  --location eastus \
  --resource-group rg-gpu-weather

# Get Application Insights instrumentation key:
az monitor app-insights component show --app ai-gpu-weather --resource-group rg-gpu-weather --query instrumentationKey -o tsv
# SAVE THIS KEY — useful for monitoring
```

### Phase 1 Verification Checklist

Run all of these — every one should pass before moving to Phase 2:

```powershell
# 0. GPU capacity was confirmed (Step 1.2.5)
#    This should have been done BEFORE creating any resources.
#    If you skipped it, go back to Step 1.2 NOW.

# 1. Resource group exists
az group show --name rg-gpu-weather --query provisioningState -o tsv
# ✓ Succeeded

# 2. ACR exists
az acr show --name acrgpuweather --query loginServer -o tsv
# ✓ acrgpuweather.azurecr.io

# 3. AKS cluster healthy
az aks show --name aks-gpu-weather --resource-group rg-gpu-weather --query provisioningState -o tsv
# ✓ Succeeded

# 4. 3 nodes (2 CPU + 1 GPU)
kubectl get nodes --no-headers | measure-object -line
# ✓ 3

# 5. GPU node labeled
kubectl get nodes -l accelerator=nvidia-t4 --no-headers
# ✓ 1 node shown

# 6. NVIDIA plugin running
kubectl get pods -n kube-system -l app=nvidia-device-plugin --no-headers
# ✓ 1 pod in Running state

# 7. GPU resource visible
kubectl get nodes -l accelerator=nvidia-t4 -o jsonpath='{.items[0].status.capacity.nvidia\.com/gpu}'
# ✓ 1

# 8. Storage account exists
az storage account show --name stgpuweather --query provisioningState -o tsv
# ✓ Succeeded

# 9. Blob containers exist
az storage container list --account-name stgpuweather --query "[].name" -o tsv
# ✓ weather-data, models, predictions
```

---

## Phase 2: Backend — Data Pipeline & ML Model

### Project Structure

```
aks/
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI entry point
│   │   ├── config.py               # Environment config
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── predict.py          # GET /api/predict, GET /api/compare
│   │   │   ├── validate.py         # GET /api/validate, GET /api/validate/compare
│   │   │   └── training.py         # GET /api/training/status[/all], POST /api/training/trigger
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── data_fetcher.py     # Open-Meteo API client
│   │   │   ├── trainer.py          # Model training logic (LSTM, XGBoost, ARIMA)
│   │   │   ├── predictor.py        # Inference logic (multi-model)
│   │   │   ├── validator.py        # Backtesting logic (multi-model)
│   │   │   └── blob_storage.py     # Azure Blob upload/download
│   │   └── models/
│   │       ├── __init__.py
│   │       ├── base.py             # Abstract base class for all models
│   │       ├── lstm_model.py       # PyTorch LSTM neural network (GPU)
│   │       ├── xgboost_model.py    # XGBoost ensemble model (CPU)
│   │       └── arima_model.py      # ARIMA/SARIMAX model (CPU)
│   └── scripts/
│       └── train.py                # Standalone training entry point (supports --model-type)
```

### Step 2.1: `backend/requirements.txt`

```
torch==2.1.0
numpy==1.24.4
pandas==2.1.4
scikit-learn==1.3.2
matplotlib==3.8.2
requests==2.31.0
fastapi==0.104.1
uvicorn[standard]==0.24.0
azure-storage-blob==12.19.0
python-dotenv==1.0.0
joblib==1.3.2
xgboost==2.0.3
statsmodels==0.14.1
```

**What each package does:**
- `torch`: PyTorch — the ML framework that runs on GPU. Used for LSTM model.
- `numpy` / `pandas`: Data manipulation. NumPy for arrays, Pandas for data tables.
- `scikit-learn`: Provides `MinMaxScaler` for normalizing data to 0-1 range.
- `matplotlib`: Generates training loss curve plots.
- `requests`: Makes HTTP calls to Open-Meteo API.
- `fastapi` / `uvicorn`: Web server framework — creates the REST API endpoints.
- `azure-storage-blob`: SDK to upload/download files from Azure Blob Storage.
- `joblib`: Saves/loads the scaler object (normalization parameters).
- `xgboost`: Gradient-boosted tree ensemble model. CPU-only, fast training.
- `statsmodels`: Provides SARIMAX for ARIMA time-series forecasting. CPU-only.

### Step 2.2: `backend/app/config.py`

The implementing agent should create this file with the following pattern:
- Read all configuration from environment variables with sensible defaults
- Provide: `CITY_NAME` (default: "new-york"), `CITY_LAT` (40.71), `CITY_LON` (-74.01)
- Provide: `BLOB_ACCOUNT_NAME`, `BLOB_ACCOUNT_KEY`, `BLOB_CONNECTION_STRING`
- Provide: `MODEL_CONTAINER` (default: "models"), `DATA_CONTAINER` (default: "weather-data")
- Provide: `DEVICE` — auto-detect GPU: use `"cuda"` if `torch.cuda.is_available()` else `"cpu"`
- Provide: `INPUT_WINDOW` (168 = 7 days of hourly data), `OUTPUT_WINDOW` (24 = 1 day ahead)
- Provide: `HIDDEN_SIZE` (128), `NUM_LAYERS` (2), `DROPOUT` (0.2)
- Provide: `BATCH_SIZE` (64), `EPOCHS` (50), `LEARNING_RATE` (0.001), `PATIENCE` (10)

### Step 2.3: `backend/app/models/lstm_model.py` — The Neural Network

The implementing agent should create a PyTorch `nn.Module` class called `WeatherLSTM` with:

**Architecture explanation for newbies:**
```
Input (168 hours × 5 features)
    │
    ▼
┌──────────────────┐
│ LSTM Layer 1      │  128 hidden units, processes sequence step by step
│                    │  "reads" the weather data from hour 1 to hour 168
│ LSTM Layer 2      │  128 hidden units, refines the patterns from layer 1
│ (dropout=0.2)     │  dropout randomly zeros 20% of connections to prevent overfitting
└────────┬─────────┘
         │
         ▼ (take output from last time step only)
┌──────────────────┐
│ Fully Connected   │  Linear layer: 128 → output_window × num_features
│ Layer             │  Maps LSTM output to actual predictions
└────────┬─────────┘
         │
         ▼
Output (24 hours × 5 features)
    predicted temperature, humidity, wind, precipitation, pressure
```

**Code pattern:**
- `__init__`: Create `nn.LSTM(input_size=num_features, hidden_size=128, num_layers=2, dropout=0.2, batch_first=True)` and `nn.Linear(128, output_window * num_features)`
- `forward(x)`: Run x through LSTM, take last hidden state `output[:, -1, :]`, pass through linear layer, reshape to `(batch, output_window, num_features)`
- `num_features` = 5 (temperature, humidity, wind_speed, precipitation, pressure)

### Step 2.4: `backend/app/services/data_fetcher.py` — Getting Weather Data

The implementing agent should create a `WeatherDataFetcher` class that:

**How Open-Meteo API works:**
- **Historical data**: `GET https://archive-api.open-meteo.com/v1/archive?latitude=40.71&longitude=-74.01&start_date=2024-01-01&end_date=2025-12-31&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,surface_pressure`
- **Recent/forecast data**: `GET https://api.open-meteo.com/v1/forecast?latitude=40.71&longitude=-74.01&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,surface_pressure&past_days=7&forecast_days=7`
- Returns JSON with `hourly.time[]` and `hourly.<variable>[]` arrays
- No API key needed, 10,000 requests/day free

**Implementation pattern:**
- `fetch_historical(start_date, end_date)` → calls archive API, returns Pandas DataFrame with columns: `time, temperature, humidity, wind_speed, precipitation, pressure`
- `fetch_recent(past_days=7)` → calls forecast API with `past_days` param, returns DataFrame
- Handle HTTP errors with retry (3 attempts, exponential backoff: 1s, 2s, 4s)
- Handle missing data: fill NaN with forward-fill then backfill
- Log how many rows fetched: `logger.info(f"Fetched {len(df)} hourly records")`

### Step 2.5: `backend/app/services/blob_storage.py` — Cloud Storage

The implementing agent should create a `BlobStorageService` class that:
- `upload_model(model_bytes, filename)` → uploads to `models/` container
- `download_model(filename)` → downloads from `models/` container, returns bytes
- `list_models()` → lists all `.pt` files, sorted by date descending
- `get_latest_model_name()` → returns filename of most recent model
- `upload_data(df, filename)` → uploads DataFrame as Parquet to `weather-data/` container
- Uses `azure.storage.blob.BlobServiceClient` with connection string from config

### Step 2.6: `backend/app/services/trainer.py` — Training Logic

The implementing agent should create a `ModelTrainer` class with:

**`prepare_data(df)` method:**
1. Extract feature columns: `['temperature', 'humidity', 'wind_speed', 'precipitation', 'pressure']`
2. Apply `MinMaxScaler()` — fits on the data, transforms to 0-1 range
3. Save the scaler object (needed later for inference to reverse the normalization)
4. Create sliding windows:
   - For each position `i` in the data:
     - Input X: rows `i` to `i + INPUT_WINDOW` (168 rows × 5 features)
     - Target Y: rows `i + INPUT_WINDOW` to `i + INPUT_WINDOW + OUTPUT_WINDOW` (24 rows × 5 features)
   - Skip positions where we don't have enough future data
5. Split 80/20 into train/test sets
6. Convert to PyTorch tensors
7. Create `DataLoader` with batch_size and shuffle=True for training

**`train(train_loader, test_loader)` method:**
1. Create `WeatherLSTM` model, move to GPU: `model.to(device)`
2. Create `Adam` optimizer with learning rate 0.001
3. Create `ReduceLROnPlateau` scheduler (reduces learning rate when loss plateaus)
4. Training loop for `EPOCHS` (50):
   ```
   for epoch in range(EPOCHS):
       model.train()
       for batch_x, batch_y in train_loader:
           batch_x, batch_y = batch_x.to(device), batch_y.to(device)  # Move data to GPU
           outputs = model(batch_x)                                     # Forward pass
           loss = MSELoss(outputs, batch_y)                            # Calculate error
           optimizer.zero_grad()                                        # Reset gradients
           loss.backward()                                              # Backpropagation
           optimizer.step()                                             # Update weights
       
       # Evaluate on test set
       model.eval()
       with torch.no_grad():
           test_loss = evaluate(model, test_loader)
       
       # Early stopping: if test_loss hasn't improved for PATIENCE epochs, stop
       scheduler.step(test_loss)
       print(f"Epoch {epoch}: train_loss={train_loss:.4f}, test_loss={test_loss:.4f}")
   ```
5. Return trained model, scaler, and training history (losses per epoch)

**`save_model(model, scaler, metrics)` method:**
1. Save model state dict: `torch.save(model.state_dict(), buffer)` → upload to Blob Storage as `{city}_{timestamp}.pt`
2. Save scaler: `joblib.dump(scaler, buffer)` → upload as `{city}_{timestamp}_scaler.pkl`
3. Save metrics JSON: `{city}_{timestamp}_metrics.json` (final loss, epochs, duration)

### Step 2.7: `backend/app/services/predictor.py` — Inference Logic

The implementing agent should create a `WeatherPredictor` class with:

**`load_model()` method:**
1. Download latest `.pt` and `.pkl` from Blob Storage
2. Create `WeatherLSTM` instance
3. Load state dict: `model.load_state_dict(torch.load(buffer))`
4. Move to GPU: `model.to(device)`
5. Set eval mode: `model.eval()`
6. Load scaler: `joblib.load(buffer)`
7. Cache in memory — don't reload on every request

**`predict(days=7)` method:**
1. Fetch recent 7 days (168 hours) of actual weather data from Open-Meteo
2. Normalize using saved scaler: `scaler.transform(data)`
3. Convert to tensor, move to GPU
4. Run model forward pass: `with torch.no_grad(): predictions = model(input_tensor)`
5. Reverse normalization: `scaler.inverse_transform(predictions)` to get real °C, m/s, etc.
6. For multi-day forecasts (>24h): use autoregressive approach — feed predictions back as input for next window
7. Return list of `{time, temperature, humidity, wind_speed, precipitation, pressure}` dicts

### Step 2.8: `backend/app/services/validator.py` — Backtesting Logic

The implementing agent should create a `ModelValidator` class with:

**`validate(lookback_days=14)` method:**
1. Determine date range: `cutoff = today - lookback_days`
2. Fetch ALL historical data up to `cutoff` (for training)
3. Fetch actual observed data from `cutoff` to `today` (ground truth)
4. Train a temporary model on the pre-cutoff data (or use existing model if it was trained before cutoff)
5. Generate predictions for the `lookback_days` window
6. Compute metrics comparing predicted vs actual:
   - `MAE = mean(abs(actual - predicted))`
   - `RMSE = sqrt(mean((actual - predicted)²))`
   - `R² = 1 - (sum((actual - predicted)²) / sum((actual - mean(actual))²))`
   - `Bias = mean(predicted - actual)` (positive = over-predicting, negative = under-predicting)
7. Return `{ metrics: {mae, rmse, r2, bias}, predicted: [...], actual: [...] }`

### Step 2.9: `backend/app/main.py` — FastAPI Application

The implementing agent should create the FastAPI app with:
- CORS middleware allowing all origins (for development; in production restrict to frontend domain)
- Include routers: `predict`, `validate`, `training`
- Health check endpoint: `GET /api/health` → returns `{"status": "healthy", "gpu_available": true/false}`
- On startup: load the latest trained model into memory
- Error handling: return proper HTTP error codes (404 if no model trained yet, 503 if GPU unavailable)

### Step 2.10: API Endpoints Detail

**`GET /api/predict?city=new-york&days=7&model_type=lstm`**
- Returns 7-day hourly forecast using specified model (lstm, xgboost, or arima)
- Response: `{ "city": "new-york", "model_type": "lstm", "generated_at": "...", "forecast": [{"time": "2026-03-28T00:00", "temperature": 12.3, "humidity": 65, "wind_speed": 4.2, "precipitation": 0.0, "pressure": 1013.2}, ...] }`

**`GET /api/compare?city=new-york&days=7`**
- Runs all loaded models and returns forecasts side-by-side
- Response: `{ "city": "new-york", "models": { "lstm": {"forecast": [...]}, "xgboost": {"forecast": [...]}, "arima": {"forecast": [...]} } }`

**`GET /api/validate?city=new-york&lookback_days=14&model_type=lstm`**
- Returns backtesting results for the specified model
- Response: `{ "model_type": "lstm", "metrics": {"mae": 1.8, "rmse": 2.3, "r2": 0.82, "bias": -0.3}, "predicted": [{...}], "actual": [{...}] }`

**`GET /api/validate/compare?city=new-york&lookback_days=14`**
- Runs validation for all loaded models and returns combined results with per-model metrics
- Response: `{ "models": { "lstm": {"metrics": {...}, "predicted": [...], "actual": [...]}, "xgboost": {...}, "arima": {...} } }`

**`GET /api/training/status?model_type=lstm`**
- Returns latest training info for the specified model type
- Response: `{ "status": "ready", "model_type": "lstm", "last_trained": "2026-03-25T02:15:00", "model_file": "new-york_20260325.pt", "duration_minutes": 42, "final_loss": 0.0032, "epochs_completed": 38 }`

**`GET /api/training/status/all`**
- Returns training status for all model types (lstm, xgboost, arima)

**`POST /api/training/trigger?model_type=lstm`**
- Returns kubectl command to train a specific model type (creates a K8s Job)
- Response: `{ "status": "training_trigger_received", "model_type": "lstm", "command": "kubectl create job ..." }`

**`GET /api/health`**
- Response: `{ "status": "healthy", "gpu_available": true, "model_loaded": true, "loaded_models": ["lstm", "xgboost", "arima"], "cuda_device": "NVIDIA T4" }`

### Step 2.11: `backend/scripts/train.py` — Standalone Training Script

This runs inside the Kubernetes CronJob. Supports `--model-type` argument (lstm, xgboost, arima):

```
Usage:
  python -m scripts.train                                    # default: LSTM
  python -m scripts.train --model-type xgboost               # train XGBoost
  python -m scripts.train --model-type arima --city tokyo     # train ARIMA for Tokyo

Entry point flow:
1. Parse --model-type (lstm|xgboost|arima), --city, --lat, --lon
2. Print GPU info (LSTM) or "CPU mode" (XGBoost/ARIMA)
3. Initialize DataFetcher, BlobStorage, Trainer
4. Fetch 2 years of historical data
5. If LSTM: prepare DataLoaders, train with early stopping (GPU)
   If XGBoost/ARIMA: prepare numpy arrays, train (CPU)
6. Print final metrics
7. Save model + scaler + metrics to Blob Storage
8. Print "Training complete" with duration
```

### Step 2.12: `backend/Dockerfile`

The implementing agent should create a Dockerfile with:
- Base image: `pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime`
- `WORKDIR /app`
- Copy `requirements.txt` first, run `pip install` (Docker layer caching)
- Copy all source code
- Expose port 8000
- Default CMD: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- The CronJob overrides CMD with `python -m scripts.train`

### Step 2.13: Local Testing (Before Deploying to AKS)

**Test the backend locally with Docker:**
```powershell
cd aks/backend

# Build
docker build -t weather-api:test .

# Run (without GPU — will use CPU, which is fine for testing)
docker run -p 8000:8000 `
  -e CITY_NAME=new-york `
  -e CITY_LAT=40.71 `
  -e CITY_LON=-74.01 `
  -e BLOB_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=stgpuweather;AccountKey=YOUR_KEY;EndpointSuffix=core.windows.net" `
  weather-api:test

# Test health endpoint:
curl http://localhost:8000/api/health
# Expected: {"status": "healthy", "gpu_available": false, "model_loaded": false}

# Test prediction (will fail if no model trained yet — that's expected):
curl http://localhost:8000/api/predict?city=new-york&days=1
# Expected: 404 or error about no model

# Run training locally (slow on CPU, but proves the code works):
docker run `
  -e BLOB_CONNECTION_STRING="..." `
  weather-api:test python -m scripts.train
# This will take 30+ minutes on CPU. On GPU node it takes ~10-15 minutes.
```

---

## Phase 3: Frontend — React Dashboard

### Step 3.1: Initialize Project

```powershell
cd aks
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install recharts axios
npm install -D tailwindcss @tailwindcss/vite
```

### Step 3.2: Project Structure

```
frontend/
├── Dockerfile
├── nginx.conf
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── index.html
└── src/
    ├── App.tsx                     # Main layout with 4 tabs + model selector
    ├── main.tsx                    # Entry point
    ├── index.css                   # Tailwind imports
    ├── components/
    │   ├── ForecastChart.tsx        # 7-day temperature forecast (per model)
    │   ├── ValidationChart.tsx     # Predicted vs actual overlay (per model)
    │   ├── ComparisonChart.tsx     # All models overlaid on one chart
    │   ├── ComparisonValidation.tsx # All models vs actual + metrics table
    │   ├── ModelSelector.tsx       # LSTM / XGBoost / ARIMA toggle
    │   ├── MetricsCard.tsx         # Single metric display (MAE, RMSE, R²)
    │   ├── MetricsDashboard.tsx    # Grid of MetricsCard components
    │   ├── TrainingStatus.tsx      # Training status for all model types
    │   ├── CitySelector.tsx        # Dropdown for city selection
    │   └── Header.tsx              # App header with title
    └── services/
        └── api.ts                  # Axios client for backend API
```

### Step 3.3: Component Details

**`App.tsx`** — Main layout:
- Header with app title "GPU Weather Prediction" and city selector
- **4 tab buttons**: "Forecast" | "Validation" | "**Compare Models**" | "Training"
- Model selector (LSTM / XGBoost / ARIMA) shown on Forecast and Validation tabs
- Renders corresponding component based on active tab

**`ModelSelector.tsx`** — Model type picker:
- Toggle buttons: LSTM | XGBoost | ARIMA
- Active model highlighted in blue
- Used on Forecast and Validation tabs to switch between models

**`ForecastChart.tsx`** — 7-day forecast:
- Accepts `modelType` prop to fetch from the selected model
- Uses Recharts `LineChart` with `ResponsiveContainer`
- X-axis: date/time labels
- Y-axis left: Temperature (°C) — primary blue line
- Y-axis right: Humidity (%) — secondary green line
- Tooltip shows all values on hover
- Below chart: wind speed and precipitation as a smaller secondary chart
- Shows active model name in header

**`ValidationChart.tsx`** — Backtesting proof:
- Accepts `modelType` prop
- Two `Line` components on same `LineChart`:
  - "Predicted" — blue dashed line
  - "Actual" — green solid line
- X-axis: last 14 days
- Y-axis: temperature (°C)
- Below chart: `MetricsDashboard` component
- Button: "Run Validation" to fetch fresh backtesting data
- Shows active model name in header

**`ComparisonChart.tsx`** — Multi-model forecast comparison:
- Calls `GET /api/compare` to fetch all models at once
- Overlays all available models on one chart (color-coded: LSTM=blue, XGBoost=amber, ARIMA=green)
- Downsampled to every 6 hours for readability

**`ComparisonValidation.tsx`** — Multi-model validation comparison:
- Calls `GET /api/validate/compare` to backtest all models
- Overlays all models + actual observed data on one chart
- Displays metrics comparison table with best values highlighted in green
- Columns: Model | MAE | RMSE | R² | Bias

**`MetricsCard.tsx`** — Single metric:
- Props: `name` (string), `value` (number), `unit` (string), `threshold` ({good, fair})
- Color coded: green if value < threshold.good, yellow if < threshold.fair, red otherwise
- Example: MAE card shows "1.8 °C" in green (threshold: good=2, fair=4)

**`MetricsDashboard.tsx`** — Grid of metrics:
- 4 cards in a row: MAE, RMSE, R², Bias
- Thresholds:
  - MAE: good < 2°C, fair < 4°C
  - RMSE: good < 3°C, fair < 5°C
  - R²: good > 0.8, fair > 0.6 (reversed — higher is better)
  - Bias: good < |0.5|°C, fair < |1.5|°C

**`TrainingStatus.tsx`** — Training info for all models:
- Shows status cards for each model type (LSTM, XGBoost, ARIMA)
- Each card displays: last trained date, model filename, training duration, final loss, epochs, device
- Green/red status indicator per model
- "Retrain" button per model → calls `POST /api/training/trigger?model_type=...`

**`CitySelector.tsx`** — City picker:
- Dropdown with preset cities: New York, London, Tokyo, Sydney, São Paulo
- Each option has lat/lon values
- On change: updates global state, re-fetches all data for new city
- For now: only New York is fully supported (model trained for it). Others show "No model available" message.

**`services/api.ts`** — API client:
- Base URL: empty string (relative URLs — ingress handles routing)
- `getForecast(city, days)` → `GET /api/predict?city=${city}&days=${days}`
- `getValidation(city, lookbackDays)` → `GET /api/validate?city=${city}&lookback_days=${lookbackDays}`
- `getTrainingStatus()` → `GET /api/training/status`
- `triggerTraining()` → `POST /api/training/trigger`
- `getHealth()` → `GET /api/health`
- Error handling: catch network errors, show user-friendly message

### Step 3.4: `frontend/nginx.conf`

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # Serve React app — all routes go to index.html (SPA routing)
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API calls are handled by Kubernetes ingress, not nginx
    # This file is only for serving the static frontend
}
```

### Step 3.5: `frontend/Dockerfile`

Multi-stage build:
- Stage 1 (`build`): `node:20-alpine` → copy package.json → `npm ci` → copy source → `npm run build`
- Stage 2 (`production`): `nginx:alpine` → copy `dist/` from build stage → copy `nginx.conf` → expose 80

### Step 3.6: Local Testing

```powershell
cd aks/frontend

# Test locally (development mode)
npm run dev
# Opens http://localhost:5173
# Note: API calls will fail (no backend running). That's normal for frontend-only testing.

# Test production build
npm run build
# Check dist/ folder is created with index.html

# Test Docker build
docker build -t weather-ui:test .
docker run -p 8080:80 weather-ui:test
# Open http://localhost:8080 — should show the React app (API calls will fail)
```

---

## Phase 4: Kubernetes Manifests

### Step 4.1: `k8s/namespace.yaml`

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: gpu-weather
  labels:
    app: gpu-weather
```

### Step 4.2: `k8s/configmap.yaml`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: weather-config
  namespace: gpu-weather
data:
  CITY_NAME: "new-york"
  CITY_LAT: "40.71"
  CITY_LON: "-74.01"
  BLOB_ACCOUNT_NAME: "stgpuweather"
  INPUT_WINDOW: "168"
  OUTPUT_WINDOW: "24"
  HIDDEN_SIZE: "128"
  NUM_LAYERS: "2"
  BATCH_SIZE: "64"
  EPOCHS: "50"
  LEARNING_RATE: "0.001"
```

### Step 4.3: `k8s/secrets.yaml`

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: weather-secrets
  namespace: gpu-weather
type: Opaque
stringData:
  BLOB_CONNECTION_STRING: "DefaultEndpointsProtocol=https;AccountName=stgpuweather;AccountKey=REPLACE_WITH_ACTUAL_KEY;EndpointSuffix=core.windows.net"
```

**IMPORTANT**: Replace `REPLACE_WITH_ACTUAL_KEY` with the actual storage account key from Step 1.8.

### Step 4.4: `k8s/backend-deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: weather-api
  namespace: gpu-weather
  labels:
    app: weather-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: weather-api
  template:
    metadata:
      labels:
        app: weather-api
    spec:
      tolerations:
        - key: "sku"
          operator: "Equal"
          value: "gpu"
          effect: "NoSchedule"
      nodeSelector:
        accelerator: nvidia-t4
      containers:
        - name: weather-api
          image: acrgpuweather.azurecr.io/weather-api:v1
          ports:
            - containerPort: 8000
          resources:
            requests:
              cpu: "1"
              memory: "4Gi"
              nvidia.com/gpu: 1
            limits:
              cpu: "2"
              memory: "8Gi"
              nvidia.com/gpu: 1
          envFrom:
            - configMapRef:
                name: weather-config
            - secretRef:
                name: weather-secrets
          readinessProbe:
            httpGet:
              path: /api/health
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /api/health
              port: 8000
            initialDelaySeconds: 60
            periodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: weather-api-svc
  namespace: gpu-weather
spec:
  selector:
    app: weather-api
  ports:
    - port: 8000
      targetPort: 8000
  type: ClusterIP
```

### Step 4.5: `k8s/frontend-deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: weather-ui
  namespace: gpu-weather
  labels:
    app: weather-ui
spec:
  replicas: 2
  selector:
    matchLabels:
      app: weather-ui
  template:
    metadata:
      labels:
        app: weather-ui
    spec:
      containers:
        - name: weather-ui
          image: acrgpuweather.azurecr.io/weather-ui:v1
          ports:
            - containerPort: 80
          resources:
            requests:
              cpu: "100m"
              memory: "128Mi"
            limits:
              cpu: "200m"
              memory: "256Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: weather-ui-svc
  namespace: gpu-weather
spec:
  selector:
    app: weather-ui
  ports:
    - port: 80
      targetPort: 80
  type: ClusterIP
```

### Step 4.6: `k8s/training-cronjob.yaml`

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: weather-training
  namespace: gpu-weather
spec:
  schedule: "0 2 * * 0"    # Every Sunday at 2:00 AM UTC
  concurrencyPolicy: Forbid  # Don't run if previous job still running
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 2       # Retry up to 2 times on failure
      activeDeadlineSeconds: 7200   # Kill if running more than 2 hours
      template:
        spec:
          tolerations:
            - key: "sku"
              operator: "Equal"
              value: "gpu"
              effect: "NoSchedule"
          nodeSelector:
            accelerator: nvidia-t4
          containers:
            - name: trainer
              image: acrgpuweather.azurecr.io/weather-api:v1
              command: ["python", "-m", "scripts.train"]
              resources:
                requests:
                  cpu: "2"
                  memory: "8Gi"
                  nvidia.com/gpu: 1
                limits:
                  cpu: "4"
                  memory: "16Gi"
                  nvidia.com/gpu: 1
              envFrom:
                - configMapRef:
                    name: weather-config
                - secretRef:
                    name: weather-secrets
          restartPolicy: OnFailure
```

### Step 4.7: `k8s/ingress.yaml`

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: weather-ingress
  namespace: gpu-weather
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  ingressClassName: webapprouting.kubernetes.azure.com
  rules:
    - http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: weather-api-svc
                port:
                  number: 8000
          - path: /
            pathType: Prefix
            backend:
              service:
                name: weather-ui-svc
                port:
                  number: 80
```

**Note on ingress annotations**: The `rewrite-target` may need adjustment. If API calls to `/api/predict` arrive at the backend as `/predict` (stripped prefix), remove the rewrite annotation. Test after deployment.

---

## Phase 5: Build, Deploy & Initial Training

### Step 5.1: Build and Push Docker Images (ACR Cloud Build)

**Use ACR Build (recommended)** -- builds in Azure cloud, no large upload from your machine:

```powershell
# Build backend in the cloud (~7 minutes, sends only source code)
az acr build --registry acrgpuweather --image weather-api:v1 backend --no-logs

# Build frontend in the cloud (~1 minute)
az acr build --registry acrgpuweather --image weather-ui:v1 frontend --no-logs

# Verify images are in ACR:
az acr repository list --name acrgpuweather -o table
# Expected: weather-api, weather-ui
```

**Why ACR Build instead of local Docker build+push?**
- Backend image is ~5GB (PyTorch). Local push takes 1-3 hours on slow upload.
- ACR Build sends only your source code (~50KB) and builds inside Azure (datacenter speed).
- Cost: ~$0.03 per build. No Docker Desktop required.

**Alternative: Local Docker build (if you prefer):**
```powershell
az acr login --name acrgpuweather
cd backend && docker build -t acrgpuweather.azurecr.io/weather-api:v1 . && docker push acrgpuweather.azurecr.io/weather-api:v1
cd ../frontend && docker build -t acrgpuweather.azurecr.io/weather-ui:v1 . && docker push acrgpuweather.azurecr.io/weather-ui:v1
```

**Common errors:**
- `unauthorized: authentication required`: run `az acr login --name acrgpuweather` again
- `denied: requested access to the resource is denied`: verify ACR is attached to AKS with `az aks show --name aks-gpu-weather --resource-group rg-gpu-weather --query "identityProfile"`

### Step 5.2: Deploy All Kubernetes Resources

```powershell
# Deploy in order (namespace first, then config, then apps)
cd aks/k8s

kubectl apply -f namespace.yaml
kubectl apply -f configmap.yaml
kubectl apply -f secrets.yaml
kubectl apply -f backend-deployment.yaml
kubectl apply -f frontend-deployment.yaml
kubectl apply -f training-cronjob.yaml
kubectl apply -f ingress.yaml

# Watch pods come up:
kubectl get pods -n gpu-weather -w
# Wait until all pods show "Running" and "1/1 READY"
# The backend pod may take 2-5 minutes because:
#   1. GPU node might need to scale from 0 to 1 (takes 3-5 min)
#   2. Docker image is large (~5GB for PyTorch)
#   3. Model loading on startup
```

**Expected output:**
```
NAME                           READY   STATUS    RESTARTS   AGE
weather-api-xxxxx-xxxxx        1/1     Running   0          5m
weather-ui-xxxxx-xxxxx         1/1     Running   0          2m
weather-ui-xxxxx-yyyyy         1/1     Running   0          2m
```

**If backend pod is stuck in Pending:**
```powershell
kubectl describe pod -n gpu-weather -l app=weather-api
# Look for Events section. Common issues:
# - "0/3 nodes are available: 3 Insufficient nvidia.com/gpu"
#   → GPU node hasn't scaled up yet. Wait 5 minutes. Check: kubectl get nodes
# - "0/3 nodes are available: 3 node(s) had untolerated taint"
#   → Backend deployment is missing GPU tolerations. Fix the YAML.
```

**If backend pod is in ImagePullBackOff:**
```powershell
# Verify ACR connection
az aks check-acr --name aks-gpu-weather --resource-group rg-gpu-weather --acr acrgpuweather.azurecr.io
```

### Step 5.3: Trigger Initial Training

The model needs to be trained before predictions work. The CronJob runs weekly, but we need the first training NOW.

```powershell
# Create a one-time Job from the CronJob template
kubectl create job initial-training --from=cronjob/weather-training -n gpu-weather

# Watch the training progress:
kubectl logs -f job/initial-training -n gpu-weather
# You should see output like:
# GPU available: NVIDIA T4
# Fetching historical data from 2024-01-01 to 2026-03-13...
# Fetched 17520 hourly records
# Creating sliding windows...
# Created 17328 training samples
# Starting training...
# Epoch 1/50: train_loss=0.0452, test_loss=0.0389
# Epoch 2/50: train_loss=0.0312, test_loss=0.0287
# ...
# Early stopping at epoch 38
# Saving model to Blob Storage: new-york_20260327.pt
# Training complete! Duration: 42 minutes

# Check job completed:
kubectl get jobs -n gpu-weather
# Expected: initial-training  1/1  42m
```

**If training fails:**
```powershell
# Check logs for errors
kubectl logs job/initial-training -n gpu-weather

# Common errors:
# "CUDA out of memory": reduce BATCH_SIZE in configmap from 64 to 32, re-apply configmap, delete and recreate job
# "Connection refused" (Open-Meteo): check pod has internet access. AKS with Azure CNI should have outbound by default.
# "BlobStorageError: AuthenticationFailed": check BLOB_CONNECTION_STRING in secrets.yaml is correct
```

### Step 5.4: Verify Model is Saved

```powershell
az storage blob list --container-name models --account-name stgpuweather --query "[].{Name:name, Size:properties.contentLength}" -o table
# Expected:
# Name                                   Size
# new-york_20260327_021500.pt           ~5MB
# new-york_20260327_021500_scaler.pkl   ~2KB
# new-york_20260327_021500_metrics.json  ~200B
```

### Step 5.5: Restart Backend to Load New Model

```powershell
# The backend needs to reload to pick up the newly trained model
kubectl rollout restart deployment weather-api -n gpu-weather

# Wait for new pod to be ready
kubectl rollout status deployment weather-api -n gpu-weather
```

### Step 5.6: Test the Full Application

```powershell
# Get the ingress external IP
kubectl get ingress -n gpu-weather
# Note the ADDRESS column — that's your app's public IP

# Set variable for convenience
$IP = "YOUR_INGRESS_IP"

# Test health
curl http://$IP/api/health
# Expected: {"status":"healthy","gpu_available":true,"model_loaded":true,"cuda_device":"NVIDIA T4"}

# Test prediction
curl "http://$IP/api/predict?city=new-york&days=3"
# Expected: JSON with forecast array containing hourly predictions

# Test validation
curl "http://$IP/api/validate?city=new-york&lookback_days=14"
# Expected: JSON with metrics and predicted/actual arrays

# Open in browser
Start-Process "http://$IP"
# Expected: React dashboard loads with charts
```

---

## Phase 6: GPU Lifecycle Management (Activate/Deactivate)

### Why This Matters
A GPU node (Standard_NC4as_T4_v3) costs ~$15/day. If you only need GPU for training (1 hour/week) and occasional inference, you're wasting ~$400/month leaving it on 24/7.

### Architecture for Cost-Optimized GPU Usage

**Two modes of operation:**

**Mode A: "Active" (GPU On)**
- GPU node pool: 1 node
- Backend pod: runs on GPU node with `nvidia.com/gpu: 1`
- Inference: sub-100ms on GPU
- Training: available immediately
- Cost: ~$15/day for GPU node

**Mode B: "Standby" (GPU Off)**
- GPU node pool: 0 nodes (autoscaled down)
- Backend pod: runs on CPU pool (fallback) OR is scaled to 0
- Inference: not available (or slow on CPU if fallback enabled)
- Training: needs GPU node to scale up first (~5 min delay)
- Cost: $0/day for GPU

### Option 1: Automatic Scaling with Cluster Autoscaler (Recommended)

This is already configured in Step 1.6 (`--min-count 0 --max-count 1`). The autoscaler works as follows:

```
No GPU pods scheduled → autoscaler removes GPU node after ~10 minutes → cost = $0
GPU pod scheduled (e.g., CronJob) → autoscaler adds GPU node (~5 min) → pod runs → GPU pod completes → autoscaler removes node after ~10 min → cost = $0
```

**To make the backend NOT require GPU (so it can run on CPU pool when GPU is off):**

The implementing agent should create two variants of the backend deployment:

1. **`k8s/backend-deployment-gpu.yaml`** — the current one (GPU required, fast inference)
2. **`k8s/backend-deployment-cpu.yaml`** — CPU-only version (no GPU tolerations, no nvidia resource, slower inference but always available)

The CPU version changes:
- Remove `tolerations` for GPU taint
- Remove `nodeSelector` for nvidia-t4
- Remove `nvidia.com/gpu` from resources
- The backend code auto-detects: `device = "cuda" if torch.cuda.is_available() else "cpu"`

**Switching between modes:**
```powershell
# Switch to GPU mode (fast inference, costs ~$15/day)
kubectl apply -f k8s/backend-deployment-gpu.yaml

# Switch to CPU mode (slower inference, saves ~$15/day on GPU)
kubectl apply -f k8s/backend-deployment-cpu.yaml

# The GPU node will automatically scale down after ~10 minutes of no GPU pods
```

### Option 2: Manual GPU Node Pool Control

For maximum cost control, manually start/stop the GPU node pool:

```powershell
# === DEACTIVATE GPU (stop paying for GPU node) ===
# Scale GPU pool to 0 nodes
az aks nodepool scale `
  --resource-group rg-gpu-weather `
  --cluster-name aks-gpu-weather `
  --name gpupool `
  --node-count 0

# Verify: no GPU nodes
kubectl get nodes -l accelerator=nvidia-t4
# Expected: No resources found

# Switch backend to CPU mode (so it still serves requests)
kubectl apply -f k8s/backend-deployment-cpu.yaml

echo "GPU deactivated. Saving ~$15/day."


# === ACTIVATE GPU (when you need training or fast inference) ===
# Scale GPU pool to 1 node
az aks nodepool scale `
  --resource-group rg-gpu-weather `
  --cluster-name aks-gpu-weather `
  --name gpupool `
  --node-count 1

# Wait for node to be ready (3-5 minutes)
kubectl get nodes -l accelerator=nvidia-t4 -w
# Wait until status shows "Ready"

# Verify NVIDIA plugin is running on the new node
kubectl get pods -n kube-system | findstr nvidia
# Expected: nvidia-device-plugin-daemonset-xxxxx  1/1  Running

# Switch backend to GPU mode
kubectl apply -f k8s/backend-deployment-gpu.yaml

# Wait for backend pod to be ready on GPU node
kubectl rollout status deployment weather-api -n gpu-weather

echo "GPU activated. Fast inference available."
```

### Option 3: Scheduled GPU Activation (Best for Production)

Create a script or use Azure Automation to:
- **Sunday 1:50 AM**: Scale GPU pool to 1 (before training CronJob at 2 AM)
- **Sunday 4:00 AM**: Scale GPU pool to 0 (after training completes)
- **On-demand**: Activate via API call or Azure Portal when needed

```powershell
# Example: Create scheduled scale-up script
# Save as: scripts/gpu-activate.ps1
# This could be triggered by Azure Automation Runbook or GitHub Actions

param(
    [ValidateSet("activate", "deactivate")]
    [string]$Action
)

$ResourceGroup = "rg-gpu-weather"
$ClusterName = "aks-gpu-weather"
$NodePool = "gpupool"

if ($Action -eq "activate") {
    Write-Host "Activating GPU node pool..."
    az aks nodepool scale --resource-group $ResourceGroup --cluster-name $ClusterName --name $NodePool --node-count 1
    Write-Host "Waiting for GPU node to be ready..."
    Start-Sleep -Seconds 300  # 5 minute wait
    kubectl apply -f k8s/backend-deployment-gpu.yaml
    Write-Host "GPU activated and backend switched to GPU mode."
}
elseif ($Action -eq "deactivate") {
    Write-Host "Switching backend to CPU mode..."
    kubectl apply -f k8s/backend-deployment-cpu.yaml
    Start-Sleep -Seconds 30
    Write-Host "Deactivating GPU node pool..."
    az aks nodepool scale --resource-group $ResourceGroup --cluster-name $ClusterName --name $NodePool --node-count 0
    Write-Host "GPU deactivated. Cost savings active."
}
```

### GPU Cost Summary

| Scenario | GPU Hours/Week | GPU Monthly Cost | Total Monthly Cost |
|----------|---------------|-----------------|-------------------|
| Always on | 720 hrs | ~$450 | ~$600 |
| Training only (2 hrs/week) | ~10 hrs | ~$7 | ~$157 |
| Training + daily inference (4 hrs/day) | ~130 hrs | ~$80 | ~$230 |
| Completely off | 0 hrs | $0 | ~$150 (CPU pool only) |

---

## Phase 7: Day-to-Day Operations & Monitoring

### Daily Health Checks

```powershell
# Check all pods are running
kubectl get pods -n gpu-weather
# All should show 1/1 Running

# Check recent logs for errors
kubectl logs -n gpu-weather -l app=weather-api --tail=50
# Look for: ERROR, WARN, Exception

# Check API is responding
curl http://$IP/api/health

# Check GPU node status (if GPU is active)
kubectl get nodes -l accelerator=nvidia-t4
kubectl describe node <gpu-node-name> | findstr -i "nvidia"
```

### Monitoring the Model Performance

**Via API:**
```powershell
# Run validation to check if model accuracy is degrading
curl "http://$IP/api/validate?city=new-york&lookback_days=14"
# Check the metrics:
# - MAE should be < 2°C (good), < 4°C (acceptable)
# - R² should be > 0.7 (good), > 0.5 (acceptable)
# If metrics are poor, retrain the model with more recent data
```

**Via UI:**
- Open the dashboard → "Model Validation" tab
- Green metrics = model is accurate
- Yellow/red metrics = model needs retraining

### Viewing Training Logs

```powershell
# List all training jobs (current and past)
kubectl get jobs -n gpu-weather
# Shows completion status and age

# View logs of the most recent training
kubectl logs -n gpu-weather job/<job-name>

# View logs of CronJob history
kubectl get cronjob weather-training -n gpu-weather
# Shows last schedule time, active jobs, etc.
```

### Log Analytics (Azure Portal)

```powershell
# Query pod logs in Azure Portal:
# Go to Log Analytics workspace → Logs → Run this KQL query:

# ContainerLogV2
# | where ContainerName == "weather-api"
# | where LogMessage contains "error" or LogMessage contains "ERROR"
# | order by TimeGenerated desc
# | take 50
```

### Restarting Services

```powershell
# Restart backend (e.g., after config change)
kubectl rollout restart deployment weather-api -n gpu-weather
kubectl rollout status deployment weather-api -n gpu-weather

# Restart frontend
kubectl rollout restart deployment weather-ui -n gpu-weather

# Force delete a stuck pod
kubectl delete pod <pod-name> -n gpu-weather --force --grace-period=0
```

### Updating the Application (New Code Version)

```powershell
# 1. Rebuild via ACR Cloud Build (sends source code only, ~7 min backend, ~1 min frontend)
cd c:\labs\tech\gpu\aks
az acr build --registry acrgpuweather --image weather-api:v1 backend --no-logs -o none
az acr build --registry acrgpuweather --image weather-ui:v1 frontend --no-logs -o none

# 2. Restart deployments to pull new images
kubectl rollout restart deployment weather-api -n gpu-weather
kubectl rollout restart deployment weather-ui -n gpu-weather

# 3. Watch rollout
kubectl rollout status deployment/weather-api -n gpu-weather
# If something goes wrong, rollback:
kubectl rollout undo deployment/weather-api -n gpu-weather
```

---

## Phase 8: UI Walkthrough — How Users Interact

### First-Time User Experience

1. **Open browser** → navigate to `http://<INGRESS_IP>/`
2. **Dashboard loads** with "GPU Weather Prediction" header and New York City selected
3. **Forecast tab** (default):
   - Shows 7-day temperature forecast as a line chart
   - Today's predicted vs actual temperature shown in a card
   - Wind speed and precipitation below the main chart
   - Chart updates automatically every 6 hours
4. **Click "Model Validation" tab**:
   - Shows two overlapping lines: blue dashed (predicted) vs green solid (actual)
   - 14-day backtesting window
   - 4 metric cards at the bottom: MAE, RMSE, R², Bias
   - Each card is color-coded (green/yellow/red)
   - This proves the model works — users can see exactly where predictions match and where they diverge
5. **Click "Training Status" tab**:
   - Shows when the model was last trained (e.g., "March 25, 2026 at 2:15 AM")
   - Model file name, training duration (42 minutes), final loss
   - Next scheduled training: "March 30, 2026 at 2:00 AM"
   - Green dot = model is loaded and serving predictions
   - "Retrain Now" button for manual retraining

### What the User Sees When Clicking "Retrain Now"

1. Button shows spinner + "Training started..."
2. Backend creates a Kubernetes Job (same as CronJob template)
3. If GPU is off: GPU node scales from 0 to 1 (takes 3-5 min — show "Provisioning GPU..." status)
4. Training starts — progress updates every 30 seconds via polling
5. Training completes (~30-60 min) — "Training complete! New model loaded."
6. All charts refresh with predictions from the new model
7. GPU node scales back to 0 after ~10 minutes of inactivity

### Interpreting the Validation Chart

```
Temperature (°C)
  25│
    │      ╱──╲    Predicted (blue dashed)
  20│    ╱      ╲──────────╱──╲
    │  ╱    ╱──╲              ╲
  15│╱    ╱      ╲──────────╱    ╲
    │   ╱          Actual (green solid)
  10│──╱
    │
   5│
    └─────────────────────────────────
     Mar 13  Mar 16  Mar 19  Mar 22  Mar 27

  MAE: 1.8°C ✅   RMSE: 2.3°C ✅   R²: 0.82 ✅   Bias: -0.3°C ✅
```

- **Lines close together** = model is accurate
- **Lines diverge** = model missed that pattern (e.g., unexpected cold front)
- **MAE 1.8°C** means "on average, we're off by 1.8 degrees" — good!
- **R² 0.82** means "we explain 82% of temperature variation" — good!
- **Bias -0.3°C** means "we slightly under-predict temperature" — acceptable

---

## Phase 9: Updating the Model & Retraining

### When to Retrain
- **Automatic**: CronJob runs every Sunday at 2 AM
- **Manual**: When validation metrics degrade (MAE > 3°C or R² < 0.6)
- **Seasonal**: Consider retraining with more recent data as seasons change

### How to Retrain Manually

**Via UI:**
- Click "Training Status" tab → "Retrain Now" button

**Via CLI:**
```powershell
# Make sure GPU is active
az aks nodepool show --resource-group rg-gpu-weather --cluster-name aks-gpu-weather --name gpupool --query count
# If 0, activate first (see Phase 6)

# Create manual training job
kubectl create job manual-retrain-$(Get-Date -Format 'yyyyMMdd') --from=cronjob/weather-training -n gpu-weather

# Watch progress
kubectl logs -f -n gpu-weather job/manual-retrain-$(Get-Date -Format 'yyyyMMdd')
```

### How to Add a New City

Training for a new city is now built-in via CLI arguments. No code changes needed:

```powershell
# Step 1: Train a model for the new city (inside the training pod or locally)
# Option A: Run as a K8s Job with custom args
kubectl run train-tokyo -n gpu-weather --rm -it --restart=Never \
  --image=acrgpuweather.azurecr.io/weather-api:v1 \
  --overrides='{"spec":{"tolerations":[{"key":"sku","operator":"Equal","value":"gpu","effect":"NoSchedule"}],"nodeSelector":{"accelerator":"nvidia-t4"},"containers":[{"name":"train-tokyo","image":"acrgpuweather.azurecr.io/weather-api:v1","command":["python","-m","scripts.train","--city","tokyo","--lat","35.68","--lon","139.69"],"resources":{"limits":{"nvidia.com/gpu":"1"}}}]}}'

# Option B: If running locally with Docker
docker run -e BLOB_CONNECTION_STRING="..." weather-api:v1 \
  python -m scripts.train --city tokyo --lat 35.68 --lon 139.69

# Step 2: Verify model saved
az storage blob list --container-name models --account-name stgpuweather \
  --query "[?contains(name,'tokyo')].name" -o tsv

# Step 3: The model is ready -- the frontend can now pick any city
# and the backend fetches weather data for those coordinates automatically
```

**Available training examples:**
```powershell
python -m scripts.train --city new-york --lat 40.71 --lon -74.01
python -m scripts.train --city london --lat 51.51 --lon -0.13
python -m scripts.train --city tokyo --lat 35.68 --lon 139.69
python -m scripts.train --city sydney --lat -33.87 --lon 151.21
python -m scripts.train --city paris --lat 48.86 --lon 2.35
python -m scripts.train --city singapore --lat 1.35 --lon 103.82
python -m scripts.train --city dubai --lat 25.28 --lon 55.30
python -m scripts.train --city mumbai --lat 19.08 --lon 72.88
python -m scripts.train --city jakarta --lat -6.21 --lon 106.85

# Or any custom coordinate:
python -m scripts.train --city "my-village" --lat 12.34 --lon 56.78 --years 3
```
5. Frontend CitySelector dropdown already supports multiple cities

### How to Improve Model Accuracy

If the model isn't performing well:

1. **Add more training data**: Increase from 2 years to 5 years of history
   - Update `data_fetcher.py` to use `start_date=2021-01-01`
2. **Increase model complexity**: Change HIDDEN_SIZE from 128 to 256 in configmap
3. **Use longer input window**: Change INPUT_WINDOW from 168 (7 days) to 336 (14 days)
4. **Add more features**: Include solar radiation, cloud cover from Open-Meteo
5. **Ensemble**: Train multiple models and average their predictions

### Model Versioning

Models are stored in Blob Storage with timestamps:
```
models/
├── new-york_20260301_020000.pt         # March 1 model
├── new-york_20260301_020000_scaler.pkl
├── new-york_20260308_020000.pt         # March 8 model (weekly retrain)
├── new-york_20260308_020000_scaler.pkl
├── new-york_20260315_020000.pt         # March 15 model
└── new-york_20260315_020000_scaler.pkl
```

**Rolling back to a previous model:**
```powershell
# List all models
az storage blob list --container-name models --account-name stgpuweather --query "[?ends_with(name, '.pt')].{Name:name, Date:properties.lastModified}" -o table

# To use an older model, rename/copy it to be the latest, then restart backend
# Or update the backend code to accept a MODEL_VERSION env var pointing to a specific file
```

---

## Phase 10: Teardown & Cleanup

### Partial Teardown (Stop Costs, Keep Resources)

```powershell
# Stop GPU costs — scale GPU pool to 0
az aks nodepool scale --resource-group rg-gpu-weather --cluster-name aks-gpu-weather --name gpupool --node-count 0

# Stop AKS costs — stop the entire cluster (keeps configuration)
az aks stop --resource-group rg-gpu-weather --name aks-gpu-weather
# To restart later:
az aks start --resource-group rg-gpu-weather --name aks-gpu-weather
```

### Full Teardown (Delete Everything)

```powershell
# WARNING: This deletes ALL resources and data. Not reversible.

# Delete the entire resource group (includes AKS, ACR, Storage, KeyVault, etc.)
az group delete --name rg-gpu-weather --yes --no-wait

# Verify deletion started
az group show --name rg-gpu-weather --query provisioningState -o tsv
# Expected: Deleting (or group not found after a few minutes)
```

---

## Alternative Use Case: YOLO Real-Time Object Detection

### Why Add This

Weather prediction shows GPU **training**. YOLO shows GPU **inference**. Together they demonstrate the full GPU value story.

### How to Add YOLO as a Second Tab

1. Add `ultralytics` to backend requirements.txt
2. Create `app/routers/detect.py` with `POST /api/detect` endpoint:
   - Accepts image file upload (multipart form)
   - Runs YOLO v8 nano model on GPU
   - Returns annotated image (base64) + detections JSON
3. Add `DetectionView.tsx` component to frontend:
   - Image upload dropzone
   - Annotated result image display
   - Detection list (object name, confidence %, bounding box)
   - Inference latency display (e.g., "52ms on GPU")
4. Add "Object Detection" as 4th tab in the dashboard

### YOLO Backend Code Pattern

The implementing agent should:
- Download YOLO v8n model on startup: `from ultralytics import YOLO; model = YOLO("yolov8n.pt")`
- `POST /api/detect`: receive image → `results = model(image)` → return annotated image + JSON
- No training needed — pre-trained on COCO dataset (80 classes: person, car, dog, etc.)
- Show GPU speedup: return `inference_time_ms` in response

---

## Common Errors & Fixes (Reference)

### Azure / Infrastructure Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `RequestDisallowedByPolicy` / MCAPS deny | Subscription has Azure Policy blocking GPU VM creation via CLI | **Add GPU node pool via Azure Portal instead** (see SOP Step 2 at top of this doc). Portal may allow it or let you request an exemption inline. |
| `QuotaExceeded for Standard_NC4as_T4_v3` | T4 GPU quota is 0 | Request increase: Azure Portal > Subscriptions > Usage + quotas > search "NCASv3_T4" > Request increase to 4 |
| `SkuNotAvailable` | T4 VMs not available in selected region | Try different region: eastus2, westus2, westeurope, southcentralus |
| `SubnetIsFull` | VNet ran out of IPs | Delete cluster, let Azure create new VNet, or specify larger subnet |
| `OperationNotAllowed: Scaling is not allowed` | Cluster is in failed state | Run `az aks update --resource-group rg-gpu-weather --name aks-gpu-weather` to reconcile |
| `Node pool gpupool already exists` | Previous failed GPU pool is stuck | Delete it: `az aks nodepool delete --name gpupool --resource-group rg-gpu-weather --cluster-name aks-gpu-weather --yes` then wait 5 min and retry. Or use a different name like `gpu`. |
| `Node label 'accelerator' not allowed` | AKS reserves the `accelerator` label | Use `gpu-type` as the label key instead (already fixed in our manifests) |
| CPU VM SKU `Standard_DS2_v2` not allowed | Subscription policy restricts VM families | Script auto-tries 4-vCPU alternatives: DC4ads_v5, DC4as_v5, DC4ds_v3, etc. (already handled) |

### Kubernetes Errors

| Error | Cause | Fix |
|-------|-------|-----|
| Pod stuck in `Pending` | No GPU node available | Check autoscaler: `kubectl describe pod <pod>`. Wait 5 min for scale-up, or manually scale: `az aks nodepool scale --name gpupool --node-count 1` |
| Pod in `ImagePullBackOff` | ACR not attached or image tag wrong | `az aks check-acr --name aks-gpu-weather --resource-group rg-gpu-weather --acr acrgpuweather.azurecr.io` |
| Pod in `CrashLoopBackOff` | Application error on startup | `kubectl logs <pod-name> -n gpu-weather` — check for Python errors |
| `nvidia.com/gpu` not in node capacity | NVIDIA device plugin not installed | `kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.1/nvidia-device-plugin.yml` |
| Ingress has no ADDRESS | App Routing not enabled or DNS propagation | `az aks approuting enable --resource-group rg-gpu-weather --name aks-gpu-weather`. Wait 5 minutes. |

### ML / Training Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `CUDA out of memory` | Batch size too large for 16GB T4 | Reduce BATCH_SIZE in configmap from 64 to 32. Reapply. |
| `RuntimeError: CUDA error: device-side assert` | Data shape mismatch or NaN in data | Check data preprocessing — ensure no NaN values, verify tensor shapes |
| Training loss doesn't decrease | Learning rate too high or data not normalized | Verify MinMaxScaler is applied. Try reducing LEARNING_RATE to 0.0001 |
| Model predicts same value for everything | All features normalized to same range, model collapsed | Check scaler saved correctly. Ensure features have different distributions. |
| Open-Meteo returns 429 | Rate limited (>10k requests/day) | Add retry with backoff. Cache data in Blob Storage. |

### Frontend Errors

| Error | Cause | Fix |
|-------|-------|-----|
| CORS error in console | Backend not allowing frontend origin | Add `CORSMiddleware(app, allow_origins=["*"])` in FastAPI (fine for demo) |
| Charts show no data | API response format doesn't match Recharts expected format | Log API response in browser console. Verify field names match component props. |
| API calls return 404 | Ingress path routing misconfigured | Check ingress: `kubectl describe ingress -n gpu-weather` |
| "No model available" | Initial training not done yet | Run initial training: `kubectl create job initial-training --from=cronjob/weather-training -n gpu-weather` |

---

## Files to Create (Complete List)

| # | Path | Description | Depends On |
|---|------|-------------|------------|
| 1 | `aks/backend/requirements.txt` | Python package dependencies | None |
| 2 | `aks/backend/Dockerfile` | Container image for backend | #1 |
| 3 | `aks/backend/app/__init__.py` | Empty init file | None |
| 4 | `aks/backend/app/config.py` | Environment variable config | None |
| 5 | `aks/backend/app/models/__init__.py` | Empty init file | None |
| 5a | `aks/backend/app/models/base.py` | Abstract base class for all models | #4 |
| 6 | `aks/backend/app/models/lstm_model.py` | PyTorch LSTM neural network (GPU) | #4, #5a |
| 6a | `aks/backend/app/models/xgboost_model.py` | XGBoost ensemble model (CPU) | #4, #5a |
| 6b | `aks/backend/app/models/arima_model.py` | ARIMA/SARIMAX model (CPU) | #4, #5a |
| 7 | `aks/backend/app/services/__init__.py` | Empty init file | None |
| 8 | `aks/backend/app/services/data_fetcher.py` | Open-Meteo API client | #4 |
| 9 | `aks/backend/app/services/blob_storage.py` | Azure Blob upload/download (model_type aware) | #4 |
| 10 | `aks/backend/app/services/trainer.py` | Training logic (LSTM/XGBoost/ARIMA) | #6, #6a, #6b, #8, #9 |
| 11 | `aks/backend/app/services/predictor.py` | Multi-model inference logic | #6, #6a, #6b, #8, #9 |
| 12 | `aks/backend/app/services/validator.py` | Multi-model backtesting logic | #10, #11 |
| 13 | `aks/backend/app/routers/__init__.py` | Empty init file | None |
| 14 | `aks/backend/app/routers/predict.py` | GET /api/predict + /api/compare | #11 |
| 15 | `aks/backend/app/routers/validate.py` | GET /api/validate + /api/validate/compare | #12 |
| 16 | `aks/backend/app/routers/training.py` | GET/POST /api/training/* (all model types) | #10 |
| 17 | `aks/backend/app/main.py` | FastAPI app — loads all models on startup | #14, #15, #16 |
| 18 | `aks/backend/scripts/__init__.py` | Empty init file | None |
| 19 | `aks/backend/scripts/train.py` | Training entry point (--model-type arg) | #10 |
| 20 | `aks/frontend/package.json` | React app dependencies | None |
| 21 | `aks/frontend/vite.config.ts` | Vite configuration | None |
| 22 | `aks/frontend/tailwind.config.js` | Tailwind CSS config | None |
| 23 | `aks/frontend/index.html` | HTML template | None |
| 24 | `aks/frontend/src/main.tsx` | React entry point | None |
| 25 | `aks/frontend/src/index.css` | Tailwind imports | None |
| 26 | `aks/frontend/src/App.tsx` | Main dashboard layout (4 tabs) | #27-#34a |
| 27 | `aks/frontend/src/components/Header.tsx` | App header | None |
| 28 | `aks/frontend/src/components/CitySelector.tsx` | City dropdown | None |
| 28a | `aks/frontend/src/components/ModelSelector.tsx` | LSTM/XGBoost/ARIMA toggle | None |
| 29 | `aks/frontend/src/components/ForecastChart.tsx` | 7-day forecast chart (per model) | #33 |
| 30 | `aks/frontend/src/components/ValidationChart.tsx` | Predicted vs actual (per model) | #33 |
| 30a | `aks/frontend/src/components/ComparisonChart.tsx` | All models forecast overlay | #33 |
| 30b | `aks/frontend/src/components/ComparisonValidation.tsx` | All models validation + metrics table | #33 |
| 31 | `aks/frontend/src/components/MetricsCard.tsx` | Single metric card | None |
| 32 | `aks/frontend/src/components/MetricsDashboard.tsx` | Metrics grid | #31 |
| 33 | `aks/frontend/src/services/api.ts` | API client (multi-model types) | None |
| 34 | `aks/frontend/src/components/TrainingStatus.tsx` | Training info (all models) | #33 |
| 35 | `aks/frontend/nginx.conf` | Nginx config for SPA | None |
| 36 | `aks/frontend/Dockerfile` | Multi-stage frontend build | #35 |
| 37 | `aks/k8s/namespace.yaml` | Kubernetes namespace | None |
| 38 | `aks/k8s/configmap.yaml` | Environment config | None |
| 39 | `aks/k8s/secrets.yaml` | Storage connection string | Phase 1 |
| 40 | `aks/k8s/backend-deployment.yaml` | Backend deploy + service (GPU) | #2 |
| 41 | `aks/k8s/backend-deployment-cpu.yaml` | Backend deploy (CPU fallback) | #2 |
| 42 | `aks/k8s/frontend-deployment.yaml` | Frontend deploy + service | #36 |
| 43 | `aks/k8s/training-cronjob.yaml` | Weekly training CronJob | #2 |
| 44 | `aks/k8s/ingress.yaml` | Ingress routing rules | None |
| 45 | `aks/scripts/gpu-activate.ps1` | GPU activate/deactivate script | Phase 1 |
| 46 | `aks/README.md` | Project documentation | All |

---

## Verification Checklist (End-to-End)

Run after Phase 5. Every item must pass.

```
[ ] 1.  az group show → Succeeded
[ ] 2.  az acr show → loginServer returned
[ ] 3.  az aks show → Succeeded
[ ] 4.  kubectl get nodes → 3 nodes (2 CPU + 1 GPU)
[ ] 5.  kubectl get nodes -l accelerator=nvidia-t4 → 1 node
[ ] 6.  kubectl get pods -n kube-system | grep nvidia → Running
[ ] 7.  kubectl describe node <gpu> | grep nvidia.com/gpu → Capacity: 1
[ ] 8.  az acr repository list → weather-api, weather-ui
[ ] 9.  kubectl get pods -n gpu-weather → all Running
[ ] 10. kubectl get jobs -n gpu-weather → initial-training Completed
[ ] 11. az storage blob list models → .pt file exists
[ ] 12. curl /api/health → gpu_available: true, model_loaded: true
[ ] 13. curl /api/predict → forecast JSON returned
[ ] 14. curl /api/validate → metrics + predicted + actual returned
[ ] 15. Browser http://<IP>/ → dashboard loads with charts
[ ] 16. Validation tab → metrics are green (MAE < 2, R² > 0.7)
```

---

## Decisions

- **GPU SKU**: Standard_NC4as_T4_v3 — $350/mo, 16GB VRAM, sufficient for LSTM and YOLO
- **Weather API**: Open-Meteo — free, no key, JSON, data since 1940
- **ML Model**: 2-layer LSTM — simple, proven, trains <60min on T4
- **Frontend**: React + Vite + Recharts + Tailwind CSS
- **Backend**: FastAPI + PyTorch (CUDA) + uvicorn
- **Base image**: `pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime`
- **Training**: Weekly CronJob + manual trigger via UI
- **GPU cost control**: Autoscaler 0-1 nodes + CPU fallback deployment
- **Scope included**: Weather training, inference, validation UI, GPU lifecycle, monitoring, YOLO alternative
- **Scope excluded**: Custom domain, TLS certs, CI/CD, multi-city simultaneous training, authentication

---

## Lessons Learned (From Actual Deployment)

Issues encountered and resolved during deployment. Documented here so future runs avoid the same problems.

### Infrastructure Issues

| Issue | Root Cause | Fix Applied |
|-------|-----------|-------------|
| `Standard_DS2_v2` rejected | Subscription policy restricts VM families | Script auto-tries DC4ads_v5, DC4as_v5, DC4ds_v3, etc. |
| GPU pool creation blocked by CLI | MCAPS policy blocks N-series via CLI | Script tries CLI first, falls back to Portal instructions |
| GPU pool max-pods=10 (default) | Portal creates NC-series with max 10 pods; system DaemonSets fill all 10 | Script adds `--max-pods 30` to CLI. Portal instructions updated. |
| `accelerator` label reserved | AKS reserves the `accelerator` label key | Use `agentpool: gpupool1` nodeSelector (auto-applied by AKS) |
| CPU node pool too small (1 node) | System pods use 99% of 2-vCPU node | Scale CPU pool to 2 nodes |

### Storage Authentication Issues

| Issue | Root Cause | Fix Applied |
|-------|-----------|-------------|
| `KeyBasedAuthenticationNotPermitted` | Azure Policy automatically disables shared key access on storage | Switched to `DefaultAzureCredential` (managed identity) |
| Multiple managed identities error | AKS pod has multiple identities, DefaultAzureCredential can't pick one | Set `AZURE_CLIENT_ID` env var to kubelet identity client ID |
| `ContainerNotFound` | Containers created with key auth; after policy disabled keys, containers invisible | Recreated containers with `--auth-mode login` |
| `BLOB_CONNECTION_STRING` placeholder not replaced | Setup script checked for wrong placeholder string | Script now always overwrites secrets.yaml with real values |

### Code & Build Issues

| Issue | Root Cause | Fix Applied |
|-------|-----------|-------------|
| `total_mem` AttributeError | PyTorch API is `total_memory` not `total_mem` | Fixed in train.py |
| Docker push takes 1-3 hours | PyTorch image is 5GB, local upload slow | Switched to ACR Cloud Build (~7 min) |
| Em-dash characters break PowerShell | Unicode em-dash (U+2014) in .ps1 files | Recreated scripts with ASCII only |
| Image tag mismatch (v1/v2/v3) | Debugging created v2, v3 tags; manifests got out of sync | Standardized all to v1, tagged v3 as v1 in ACR |
| Frontend build TS error `ChartRow` cast | TypeScript rejects direct cast to `Record<string, unknown>` | Double-cast via `unknown` first |
| `ReadTimeout` fetching 2-year data | Open-Meteo API times out on large date ranges | Increased timeout to 300s, chunked into 90-day segments |
| Validation shows 1 data point | OUTPUT_WINDOW=24 but validation needs 336 hours | Implemented rolling autoregressive prediction |
| ARIMA validation `Input X contains infinity` | Rolling predictions produce extreme values | Added `nan_to_num` + `clip` guards on chunks and sliding window |
| `imagePullPolicy: IfNotPresent` + same tag | New image not pulled after rebuild | Changed to `imagePullPolicy: Always` on all deployments |
| `AuthorizationPermissionMismatch` for MI | `get_account_information()` needs Storage Account Contributor | Changed validation to `list_blobs()` (works with Blob Data Contributor) |
| `Multiple user assigned identities` error | AKS has multiple MIs, DefaultAzureCredential can't choose | Use `ManagedIdentityCredential(client_id=AZURE_CLIENT_ID)` explicitly |
| Open-Meteo API timeout on 1-year download | Single API call for 365 days too large | Backend chunks into 90-day segments, frontend timeout increased to 600s |

### Architecture Decisions (Changed During Deployment)

| Original Plan | Changed To | Why |
|--------------|-----------|-----|
| Backend on GPU node | Backend on CPU node | GPU node costs $12/day even idle; CPU inference is fast enough |
| Connection string auth for Blob | Managed Identity (`ManagedIdentityCredential`) | Subscription policy blocks key-based auth; explicit `AZURE_CLIENT_ID` for multi-MI nodes |
| Local Docker build + push | ACR Cloud Build | 5GB PyTorch image takes hours to upload locally |
| XGBoost/ARIMA on CPU only | XGBoost on GPU (`device="cuda"`), ARIMA on CPU | XGBoost 2.0+ has native CUDA support; ARIMA lacks GPU library |
| Train one model at a time | Train all 3 models in one run (`--model-type all`) | Single training job produces all models; 3.3 min total on T4 |
| 2 years default training data | 1 year default (`--years 1 --months 0`) | Faster download, avoids API timeout; configurable |
| Live API calls on every tab switch | Server-side caching (10-15 min TTL) + Blob data pre-download | Instant tab switching; no waiting for Open-Meteo |
| No frontend progress feedback | Activity Log panel + progress bar on download | Users see what's happening during long operations |
| Manual secret management | `build-and-deploy.ps1` fetches conn string live from Azure | Avoids placeholder overwrite issue |
| `secrets.yaml` has real credentials | Placeholder only; real secret set via kubectl | Prevents accidental credential leak in source control |

### New Features Added During Deployment

| Feature | Description |
|---------|-------------|
| **Data Pre-download** | Training tab has "Download Data" button that saves Open-Meteo data to Blob Storage as Parquet. All tabs then read from Blob (instant) instead of live API (slow). |
| **Activity Log** | Collapsible bottom panel showing timestamped activity: API calls, completions, errors. Pulses blue dot for new entries. |
| **Model Selector** | LSTM / XGBoost / ARIMA toggle on Forecast and Validation tabs |
| **Compare Models tab** | Overlays all 3 model forecasts + validation metrics in one view |
| **Rolling Validation** | Autoregressive 24h prediction chunks cover full 14-day validation window |
| **Server-side Caching** | Two-layer cache: data fetcher (10 min) + response cache (10-15 min) |
| **Managed Identity Script** | `scripts/setup-managed-identity.ps1` auto-assigns RBAC and patches configmap |
| **Chunked Downloads** | Backend downloads historical data in 90-day chunks to avoid API timeout |
