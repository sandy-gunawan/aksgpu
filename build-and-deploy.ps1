param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Continue"

# Load config from Phase 1
$configFile = Join-Path $PSScriptRoot ".infra-config.json"
if (Test-Path $configFile) {
    $cfg = Get-Content $configFile -Raw | ConvertFrom-Json
    $RESOURCE_GROUP = $cfg.ResourceGroup
    $CLUSTER_NAME   = $cfg.ClusterName
    $ACR_NAME       = $cfg.AcrName
    $ACR_LOGIN      = $cfg.AcrLogin
    $STORAGE_NAME   = $cfg.StorageName
} else {
    $RESOURCE_GROUP = "rg-gpu-weather"
    $CLUSTER_NAME   = "aks-gpu-weather"
    $ACR_NAME       = "acrgpuweather"
    $ACR_LOGIN      = "acrgpuweather.azurecr.io"
    $STORAGE_NAME   = "stgpuweather"
    Write-Host "  [WARN] .infra-config.json not found - using defaults" -ForegroundColor Yellow
}

$BACKEND_IMAGE  = "$ACR_LOGIN/weather-api:v1"
$FRONTEND_IMAGE = "$ACR_LOGIN/weather-ui:v1"
$CROP_IMAGE     = "$ACR_LOGIN/crop-api:v1"

function Write-Step { param([string]$M); Write-Host "`n========================================" -ForegroundColor Cyan; Write-Host "  $M" -ForegroundColor Cyan; Write-Host "========================================" -ForegroundColor Cyan }
function Write-Ok   { param([string]$M); Write-Host "  [PASS] $M" -ForegroundColor Green }
function Write-Fail { param([string]$M); Write-Host "  [FAIL] $M" -ForegroundColor Red }
function Write-Info { param([string]$M); Write-Host "  $M" -ForegroundColor Gray }

# Step 0: Pre-flight
Write-Step "Step 0: Pre-flight checks"

$currentCtx = kubectl config current-context 2>$null
if (-not $currentCtx) {
    Write-Info "Fetching kubectl credentials..."
    az aks get-credentials --resource-group $RESOURCE_GROUP --name $CLUSTER_NAME --overwrite-existing 2>$null
}
Write-Ok "kubectl context: $(kubectl config current-context 2>$null)"

$nodeCount = (kubectl get nodes --no-headers 2>$null | Measure-Object -Line).Lines
Write-Ok "AKS nodes: $nodeCount"

if (-not $SkipBuild) {
    # Step 1: Build backend via ACR Build (cloud build - no large upload)
    Write-Step "Step 1: Building backend image (ACR Cloud Build)"
    Write-Info "Building in Azure cloud - sends only source code, builds remotely..."

    $backendDir = Join-Path $PSScriptRoot "backend"
    az acr build --registry $ACR_NAME --image weather-api:v1 $backendDir --no-logs -o none 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Backend ACR build failed"
        Write-Info "Try manually: az acr build --registry $ACR_NAME --image weather-api:v1 $backendDir"
        exit 1
    }
    Write-Ok "Backend image built and pushed: $ACR_LOGIN/weather-api:v1"

    # Step 2: Build frontend via ACR Build
    Write-Step "Step 2: Building frontend image (ACR Cloud Build)"

    $frontendDir = Join-Path $PSScriptRoot "frontend"
    az acr build --registry $ACR_NAME --image weather-ui:v1 $frontendDir --no-logs -o none 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Frontend ACR build failed"
        Write-Info "Check: az acr build --registry $ACR_NAME --image weather-ui:v1 $frontendDir"
        exit 1
    }
    Write-Ok "Frontend image built and pushed: $ACR_LOGIN/weather-ui:v1"

    # Step 2b: Build crop-api via ACR Build
    Write-Step "Step 2b: Building crop-api image (ACR Cloud Build)"

    $cropDir = Join-Path $PSScriptRoot "crop"
    if (Test-Path $cropDir) {
        az acr build --registry $ACR_NAME --image crop-api:v1 $cropDir --no-logs -o none 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Crop API ACR build failed"
            Write-Info "Try manually: az acr build --registry $ACR_NAME --image crop-api:v1 $cropDir"
            exit 1
        }
        Write-Ok "Crop API image built and pushed: $ACR_LOGIN/crop-api:v1"
    } else {
        Write-Info "Crop directory not found — skipping crop-api build"
    }

    Write-Info "Verifying images in ACR..."
    $repos = az acr repository list --name $ACR_NAME -o tsv 2>$null
    Write-Ok "ACR repositories: $repos"
} else {
    Write-Step "Steps 1-2: SKIPPED (SkipBuild flag set)"
}

# Step 2.5: Ensure AKS managed identity has Blob Storage access
Write-Step "Step 2.5: Managed Identity — Blob Storage RBAC"
$miScript = Join-Path $PSScriptRoot "scripts\setup-managed-identity.ps1"
if (Test-Path $miScript) {
    & $miScript -ResourceGroup $RESOURCE_GROUP -ClusterName $CLUSTER_NAME -StorageName $STORAGE_NAME
} else {
    Write-Info "scripts\setup-managed-identity.ps1 not found — skipping"
}

# Step 2.6: Ensure crop blob containers exist
Write-Step "Step 2.6: Verifying crop blob containers"
foreach ($container in @("crop-data", "crop-models")) {
    az storage container create --name $container --account-name $STORAGE_NAME --auth-mode login -o none 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Ok "Container: $container" }
    else { Write-Info "Container '$container' may already exist or needs manual creation" }
}

# Step 3: Deploy K8s manifests
Write-Step "Step 3: Deploying to Kubernetes"

$k8sDir = Join-Path $PSScriptRoot "k8s"

# Apply namespace first (required before secrets can be created)
$nsFile = Join-Path $k8sDir "namespace.yaml"
if (Test-Path $nsFile) {
    kubectl apply -f $nsFile 2>$null
    Write-Ok "Applied: namespace.yaml"
}

$manifests = @(
    "configmap.yaml",
    "crop-configmap.yaml",
    "training-rbac.yaml",
    "backend-deployment-cpu.yaml",
    "crop-deployment.yaml",
    "frontend-deployment.yaml",
    "training-cronjob.yaml",
    "ingress.yaml"
)

# Handle secrets: try to fetch connection string, but gracefully handle policy
# blocking shared key access (in which case MI auth is used at runtime)
Write-Info "Fetching Blob Storage connection string from Azure..."
$blobConn = az storage account show-connection-string --name $STORAGE_NAME --resource-group $RESOURCE_GROUP --query connectionString -o tsv 2>$null

# Check if shared key access is actually enabled
$sharedKeyEnabled = az storage account show --name $STORAGE_NAME --resource-group $RESOURCE_GROUP `
    --query allowSharedKeyAccess -o tsv 2>$null

if ($sharedKeyEnabled -ne "true") {
    # Policy blocks key auth — pods will use managed identity at runtime
    Write-Info "Shared key access disabled by policy. Pods will use managed identity."
    # Create a secret with empty connection string so the env var exists but triggers MI fallback
    kubectl create secret generic weather-secrets -n gpu-weather `
        --from-literal=BLOB_CONNECTION_STRING="" `
        --dry-run=client -o yaml | kubectl apply -f - 2>$null
    Write-Ok "Applied: weather-secrets (empty — MI auth will be used)"
} elseif ([string]::IsNullOrEmpty($blobConn)) {
    Write-Fail "Could not fetch Blob connection string from Azure"
    Write-Info "Falling back to secrets.yaml (may contain placeholder)"
    $secretsFile = Join-Path $k8sDir "secrets.yaml"
    if (Test-Path $secretsFile) { kubectl apply -f $secretsFile 2>$null; Write-Ok "Applied: secrets.yaml (fallback)" }
} else {
    # Create/update the secret directly via kubectl
    kubectl create secret generic weather-secrets -n gpu-weather `
        --from-literal=BLOB_CONNECTION_STRING="$blobConn" `
        --dry-run=client -o yaml | kubectl apply -f - 2>$null
    Write-Ok "Applied: weather-secrets (live connection string from Azure)"
}

foreach ($m in $manifests) {
    $file = Join-Path $k8sDir $m
    if (Test-Path $file) {
        kubectl apply -f $file 2>$null
        Write-Ok "Applied: $m"
    } else {
        Write-Info "Skipped (not found): $m"
    }
}

# Wait for pods
Write-Info "Waiting for pods to start (up to 5 minutes)..."
$timeout = 300
$elapsed = 0
while ($elapsed -lt $timeout) {
    $pods = kubectl get pods -n gpu-weather --no-headers 2>$null
    $runningCount = ($pods | Select-String "Running" | Measure-Object).Count
    $totalCount = ($pods | Measure-Object -Line).Lines

    if ($totalCount -gt 0 -and $runningCount -eq $totalCount) {
        Write-Ok "All $runningCount pods are Running"
        break
    }

    $pendingCount = ($pods | Select-String "Pending" | Measure-Object).Count
    Write-Info "Pods: $runningCount running, $pendingCount pending ($elapsed s)"

    Start-Sleep -Seconds 15
    $elapsed += 15
}

if ($elapsed -ge $timeout) {
    Write-Info "Some pods may still be starting. Check: kubectl get pods -n gpu-weather"
}

Write-Host ""
kubectl get pods -n gpu-weather 2>$null
Write-Host ""

# Step 4: No automatic training -- user trains from the UI after selecting a city
Write-Step "Step 4: Training"
Write-Info "No automatic training. Use the UI to: select city > Download Data > Train All Models"

# Step 5: Get URL
Write-Step "Step 5: Application URL"

Start-Sleep -Seconds 5
$ingressIP = kubectl get ingress weather-ingress -n gpu-weather -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>$null

if ($ingressIP) {
    Write-Ok "Ingress IP: $ingressIP"
    Write-Host ""
    Write-Host "  Dashboard : http://$ingressIP/" -ForegroundColor Green
    Write-Host "  Health    : http://$ingressIP/api/health" -ForegroundColor Green
    Write-Host "  Predict   : http://$ingressIP/api/predict?city=new-york&days=3" -ForegroundColor Green
    Write-Host "  Validate  : http://$ingressIP/api/validate?city=new-york&lookback_days=14" -ForegroundColor Green
} else {
    Write-Info "Ingress IP not assigned yet (may take 2-3 minutes)"
    Write-Info "Check: kubectl get ingress -n gpu-weather"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Useful commands:" -ForegroundColor White
Write-Host "    kubectl get pods -n gpu-weather" -ForegroundColor Gray
Write-Host "    kubectl logs -f job/initial-training -n gpu-weather" -ForegroundColor Gray
Write-Host "    kubectl get ingress -n gpu-weather" -ForegroundColor Gray
Write-Host "    .\scripts\gpu-activate.ps1 -Action deactivate" -ForegroundColor Gray
Write-Host ""


