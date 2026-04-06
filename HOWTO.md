# How This App Works -- Developer Guide

A practical guide for working with the GPU Weather & Crop Health Prediction apps on AKS.

---

## How the App is Structured

```
YOUR PC (Windows)                           AZURE CLOUD
=================                           ===========

aks/
 backend/      -- Weather Python (FastAPI)  --> Docker image --> ACR --> weather-api Pod
 crop/         -- Crop Python (FastAPI)     --> Docker image --> ACR --> crop-api Pod
 frontend/     -- React (TypeScript)        --> Docker image --> ACR --> weather-ui Pod
 k8s/          -- Kubernetes YAML configs   --> Applied to AKS cluster
 scripts/      -- PowerShell helper scripts
```

**Key concept**: You edit code locally on your PC, but the app runs in Azure.
To see your changes, you must: **Build** (create Docker image) -> **Push** (to ACR) -> **Deploy** (restart AKS pod).

---

## The Build-Deploy Pipeline (What Happens Step by Step)

```
 [1] Edit code locally     You change a .py or .tsx file on your PC
         |
         v
 [2] ACR Cloud Build       Azure builds a Docker image from your code
         |                  (sends only source code, builds in the cloud)
         v
 [3] Image in ACR           The Docker image is stored in Azure Container Registry
         |                  (like a private Docker Hub)
         v
 [4] Restart Pod            Tell Kubernetes to pull the new image
         |
         v
 [5] New code running       Your changes are live at http://20.65.30.149/
```

---

## Command Reference

### Building Images

**Backend (Python/FastAPI):**
```powershell
cd c:\labs\tech\gpu\aks
az acr build --registry acrgpuweather --image weather-api:v1 .\backend --no-logs -o none
```
- **What it does**: Sends your `backend/` source code to Azure, builds a Docker image in the cloud (~7 min)
- **When to run**: After changing ANY file in `backend/` (Python code, requirements.txt)
- **Why cloud build?**: The PyTorch Docker image is ~5GB. Building locally and pushing takes hours. Cloud build sends only your ~25KB source code.

**Frontend (React/TypeScript):**
```powershell
cd c:\labs\tech\gpu\aks
az acr build --registry acrgpuweather --image weather-ui:v1 .\frontend --no-logs -o none
```
- **What it does**: Sends your `frontend/` source code to Azure, builds a Docker image (~1-2 min)
- **When to run**: After changing ANY file in `frontend/` (TSX, CSS, api.ts, crop components)

**Crop Backend (Python/FastAPI):**
```powershell
cd c:\labs\tech\gpu\aks
az acr build --registry acrgpuweather --image crop-api:v1 .\crop --no-logs -o none
```
- **What it does**: Sends your `crop/` source code to Azure, builds a Docker image (~7 min)
- **When to run**: After changing ANY file in `crop/` (Python code, requirements.txt)

### Deploying (Restarting Pods)

**Restart backend only:**
```powershell
kubectl rollout restart deployment weather-api -n gpu-weather
```

**Restart crop backend only:**
```powershell
kubectl rollout restart deployment crop-api -n gpu-weather
```

**Restart frontend only:**
```powershell
kubectl rollout restart deployment weather-ui -n gpu-weather
```

**Restart all three:**
```powershell
kubectl rollout restart deployment weather-api crop-api weather-ui -n gpu-weather
```

**Restart both:**
```powershell
kubectl rollout restart deployment weather-api weather-ui -n gpu-weather
```

**Wait and verify pods are running:**
```powershell
kubectl get pods -n gpu-weather
```
- Look for `1/1 Running` and `0` restarts
- If restarts > 0, something crashed (check logs)

### Checking Status

**See pod status:**
```powershell
kubectl get pods -n gpu-weather
```

**See backend logs (last 50 lines):**
```powershell
kubectl logs -n gpu-weather -l app=weather-api --tail=50
```

**See if pod crashed (OOM, errors):**
```powershell
kubectl describe pod -n gpu-weather -l app=weather-api | Select-String "OOM|Restart|Last State|Reason"
```

**Check API is responding:**
```powershell
Invoke-RestMethod -Uri "http://20.65.30.149/api/health"
```

### Applying Kubernetes Config Changes

If you change any file in `k8s/` (YAML configs):
```powershell
kubectl apply -f k8s\<filename>.yaml
```

Examples:
```powershell
# Changed memory limits in CPU deployment
kubectl apply -f k8s\backend-deployment-cpu.yaml

# Changed configmap values
kubectl apply -f k8s\configmap.yaml

# Changed ingress routing
kubectl apply -f k8s\ingress.yaml

# Changed RBAC permissions
kubectl apply -f k8s\training-rbac.yaml
```
- **Note**: After applying a deployment change, the pod restarts automatically
- After applying a configmap change, you must manually restart the pod

---

## Common Workflows

### "I changed backend Python code"

```powershell
# 1. Build new image (~7 min)
cd c:\labs\tech\gpu\aks
az acr build --registry acrgpuweather --image weather-api:v1 .\backend --no-logs -o none

# 2. Restart pod to use new image (~90 sec)
kubectl rollout restart deployment weather-api -n gpu-weather

# 3. Wait and verify
Start-Sleep -Seconds 90
kubectl get pods -n gpu-weather -l app=weather-api
# Should show: 1/1 Running, 0 restarts
```

### "I changed frontend React code"

```powershell
# 1. Build new image (~1-2 min)
cd c:\labs\tech\gpu\aks
az acr build --registry acrgpuweather --image weather-ui:v1 .\frontend --no-logs -o none

# 2. Restart pod (~30 sec)
kubectl rollout restart deployment weather-ui -n gpu-weather

# 3. Verify
Start-Sleep -Seconds 30
kubectl get pods -n gpu-weather -l app=weather-ui
```

### "I changed both backend and frontend"

```powershell
cd c:\labs\tech\gpu\aks

# Build both (can't run in parallel -- ACR queues them)
az acr build --registry acrgpuweather --image weather-api:v1 .\backend --no-logs -o none
az acr build --registry acrgpuweather --image weather-ui:v1 .\frontend --no-logs -o none

# Restart both
kubectl rollout restart deployment weather-api weather-ui -n gpu-weather

# Wait and verify
Start-Sleep -Seconds 90
kubectl get pods -n gpu-weather
```

### "I changed k8s/configmap.yaml"

```powershell
kubectl apply -f k8s\configmap.yaml
# Then restart the pods that use it:
kubectl rollout restart deployment weather-api -n gpu-weather
```

### "Pod keeps crashing (CrashLoopBackOff)"

```powershell
# Step 1: See what's wrong
kubectl logs -n gpu-weather -l app=weather-api --tail=50

# Step 2: Check if OOM killed
kubectl describe pod -n gpu-weather -l app=weather-api | Select-String "OOM|Reason|Last State"

# Step 3: If OOM, increase memory in k8s/backend-deployment-cpu.yaml, then:
kubectl apply -f k8s\backend-deployment-cpu.yaml
```

### "Something is wrong, roll back to previous version"

```powershell
kubectl rollout undo deployment weather-api -n gpu-weather
# This reverts to the previous pod configuration
```

---

## How the Application Components Connect

```
Browser (you)
    |
    | http://20.65.30.149/
    v
 Ingress (nginx)                    Routes requests
    |         |
    |         |-- /       -->  Frontend Pod (nginx serving React static files)
    |         |-- /api/*  -->  Backend Pod (FastAPI Python server)
    |
    v
 Backend Pod
    |
    |-- Loads ML models from Azure Blob Storage (on first request per city)
    |-- Fetches weather data from Open-Meteo API (or Blob cache)
    |-- Creates K8s training Jobs (via Kubernetes API)
    |
    v
 Training Job Pods (temporary, run on GPU node)
    |-- Train LSTM / XGBoost / ARIMA models
    |-- Save trained models to Azure Blob Storage
    |-- Die after completion (GPU node scales to 0)
```

---

## File Map: What Code Does What

### Weather Backend (`backend/`)

| File | What It Does |
|------|-------------|
| `app/main.py` | FastAPI app startup, loads models on boot |
| `app/config.py` | Reads env vars (city, blob account, GPU settings) |
| `app/routers/predict.py` | `/api/predict` - forecasts + `/api/report` - weather reports |
| `app/routers/validate.py` | `/api/validate` - backtests model vs actual data |
| `app/routers/training.py` | `/api/training/*` - creates K8s jobs, shows status |
| `app/routers/data.py` | `/api/data/*` - downloads weather data to Blob |
| `app/services/predictor.py` | Loads models from Blob, runs inference |
| `app/services/validator.py` | Rolling prediction + metrics (MAE, RMSE, R2) |
| `app/services/trainer.py` | Trains all 3 model types |
| `app/services/data_fetcher.py` | Gets weather data (Blob cache or Open-Meteo API) |
| `app/services/blob_storage.py` | Upload/download files to Azure Blob Storage |
| `app/models/lstm_model.py` | PyTorch LSTM neural network |
| `app/models/xgboost_model.py` | XGBoost gradient boosting model |
| `app/models/arima_model.py` | ARIMA time-series model |
| `scripts/train.py` | CLI entry point for training (used by K8s Jobs) |

### Crop Backend (`crop/`)

| File | What It Does |
|------|-------------|
| `app/main.py` | FastAPI app for crop health, loads models on boot |
| `app/config.py` | 21 features, 3 data sources, crop presets, hyperparams |
| `app/routers/predict.py` | `/api/crop/predict` - NDVI/EVI forecast + stress level |
| `app/routers/validate.py` | `/api/crop/validate` - backtest NDVI predictions |
| `app/routers/training.py` | `/api/crop/training/*` - K8s jobs + live logs |
| `app/routers/data.py` | `/api/crop/data/*` - multi-source download + preview + XLSX |
| `app/services/data_fetcher.py` | Open-Meteo weather+soil (daily+hourly) + air quality |
| `app/services/satellite_fetcher.py` | NASA MODIS NDVI/EVI (16-day → daily interpolation) |
| `app/services/predictor.py` | Loads crop models, runs inference, classifies stress |
| `app/services/trainer.py` | 21-feature training (60d input → 32d output) |
| `app/services/blob_storage.py` | Upload/download to crop-models/crop-data containers |
| `app/models/lstm_model.py` | CropLSTM (same architecture, different features) |
| `app/models/xgboost_model.py` | CropXGBoost with daily rolling features |
| `app/models/arima_model.py` | CropARIMA per-feature SARIMAX |
| `scripts/train.py` | CLI: `--location palm-riau --lat 1.5 --lon 102.1` |

### Frontend (`frontend/src/`)

| File | What It Does |
|------|-------------|
| `App.tsx` | App switcher (Weather/Crop), tabs, selectors |
| `components/ForecastChart.tsx` | 7-day weather forecast line chart |
| `components/ValidationChart.tsx` | Predicted vs actual weather comparison |
| `components/ComparisonChart.tsx` | All 3 weather models overlaid |
| `components/WeatherReportView.tsx` | Natural language weather report + daily cards + alerts |
| `components/TrainingStatus.tsx` | Weather training cards + retrain buttons |
| `components/DataManager.tsx` | Download weather data + Train All Models |
| `components/CitySelector.tsx` | City dropdown (10 presets + custom) |
| `components/ModelSelector.tsx` | LSTM / XGBoost / ARIMA toggle |
| `components/ActivityLog.tsx` | Global event log (shared between apps) |
| `components/crop/CropSelector.tsx` | Crop presets (Palm, Rice, Corn, Wheat) + custom |
| `components/crop/CropForecastChart.tsx` | NDVI/EVI prediction + stress timeline |
| `components/crop/CropValidationChart.tsx` | Predicted vs actual NDVI + metrics |
| `components/crop/CropTrainingStatus.tsx` | Data download + train + active job monitor + live logs |
| `components/crop/CropDataPreview.tsx` | Paginated data table (color-coded by source) + XLSX |
| `services/api.ts` | Weather API calls + weather report |
| `services/cropApi.ts` | Crop API calls (predict, validate, train, data, preview) |

### Kubernetes (`k8s/`)

| File | What It Does |
|------|-------------|
| `namespace.yaml` | Creates the `gpu-weather` namespace |
| `configmap.yaml` | Weather env vars (city, blob account, ML params) |
| `crop-configmap.yaml` | Crop env vars (MODEL_CONTAINER=crop-models, presets) |
| `secrets.yaml` | Blob connection string (shared by both apps) |
| `backend-deployment-cpu.yaml` | Weather backend pod config (CPU mode) |
| `crop-deployment.yaml` | Crop backend pod + service (port 8001) |
| `frontend-deployment.yaml` | Frontend pod (nginx, app switcher) |
| `ingress.yaml` | Routes: / → frontend, /api/crop/* → crop-api, /api/* → weather-api |
| `training-cronjob.yaml` | Weekly weather auto-training (Sunday 2 AM) |
| `training-rbac.yaml` | Permissions for pods to create training jobs |

---

## How Training Works

```
User clicks "Train All Models" (Jakarta selected)
    |
    v
Frontend calls: POST /api/training/trigger?model_type=all&city=Jakarta&lat=-6.21&lon=106.85
    |
    v
Backend creates 3 Kubernetes Jobs:
    train-lstm-20260402-111410     (runs: python -m scripts.train --model-type lstm --city Jakarta ...)
    train-xgboost-20260402-111410  (runs: python -m scripts.train --model-type xgboost --city Jakarta ...)
    train-arima-20260402-111410    (runs: python -m scripts.train --model-type arima --city Jakarta ...)
    |
    v
Each Job creates a Pod on the GPU node (T4):
    1. Downloads 1 year of weather data from Open-Meteo
    2. Trains the model on GPU
    3. Saves model file to Azure Blob Storage (e.g., Jakarta_20260402_102405.pt)
    4. Pod terminates, GPU node scales to 0
    |
    v
Next time you open Forecast/Validation for Jakarta:
    Backend loads Jakarta_20260402_102405.pt from Blob
    Uses it for predictions
```

---

## How Validation Works (Why R2 Matters)

```
Training data: 1 year of hourly weather (8,784 records)
                  Jan 2025 ──────────────────────── Mar 19

Validation window: last 14 days
                                                   Mar 19 ── Apr 1

The validator:
  1. Feeds the model the 7 days BEFORE Mar 19 as input
  2. Asks: "predict the next 24 hours"
  3. Feeds those predictions back as input
  4. Repeats 14 times (14 x 24h = 336 hours)
  5. Compares all predictions vs what ACTUALLY happened

Good model:  predicted ≈ actual  →  low MAE, high R2
Bad model:   predicted ≠ actual  →  high MAE, negative R2
```

**Why Jakarta R2 is lower than New York:**
- New York has big temperature swings (0-35C) → easy to explain variation → R2 = 0.8
- Jakarta barely varies (26-34C) → any small error looks big relative to the tiny variance → R2 = -0.4
- MAE = 2.6C is actually decent for Jakarta (only 2.6 degrees off on average)

---

## Quick Troubleshooting

| Problem | Command | What to Look For |
|---------|---------|-----------------|
| Page won't load | `kubectl get pods -n gpu-weather` | All pods `1/1 Running`? |
| API returns 502/503 | `kubectl logs -n gpu-weather -l app=weather-api --tail=50` | Python errors? OOM? |
| Pod restarting | `kubectl describe pod -n gpu-weather -l app=weather-api` | `OOMKilled`? `CrashLoopBackOff`? |
| Build failed | Check exit code of `az acr build` command | Syntax errors in code? |
| Training stuck | `kubectl get jobs -n gpu-weather` | Job status `Running`/`Pending`? |
| Training pending | `kubectl get nodes` | Is GPU node scaling up? Wait 3-5 min |
| Ingress no IP | `kubectl get ingress -n gpu-weather` | ADDRESS column empty? |
| Wrong model loaded | Check backend logs | Which `.pt` file loaded on startup? |

---

## Cost Awareness

| Resource | Status | Cost |
|----------|--------|------|
| CPU node pool (2 nodes) | Always on | ~$140/month |
| GPU node (T4) | Scales to 0 when idle | $0 idle, ~$15/day when training |
| Blob Storage | Always on | ~$2/month |
| ACR (container registry) | Always on | ~$5/month |

**To stop all costs:**
```powershell
# Stop the AKS cluster (keeps config, $0/day)
az aks stop --resource-group rg-gpu-weather --name aks-gpu-weather

# Restart later:
az aks start --resource-group rg-gpu-weather --name aks-gpu-weather
```

**To delete everything permanently:**
```powershell
az group delete --name rg-gpu-weather --yes --no-wait
```
