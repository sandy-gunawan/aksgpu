<#
.SYNOPSIS
    Destroy all Azure resources created by setup-infrastructure.ps1.
    Safe to run multiple times -- skips resources that don't exist.

.DESCRIPTION
    Supports three modes:
    - Full:    Delete entire resource group (all resources gone)
    - Partial: Stop AKS cluster but keep everything (resume later with az aks start)
    - GPU:     Only remove GPU node pool (keep cluster running, save GPU costs)

.EXAMPLE
    .\teardown.ps1                    # Interactive -- asks which mode
    .\teardown.ps1 -Mode full         # Delete everything
    .\teardown.ps1 -Mode partial      # Stop cluster, keep resources
    .\teardown.ps1 -Mode gpu          # Remove GPU pool only
    .\teardown.ps1 -Mode full -Force  # No confirmation prompt
#>
param(
    [ValidateSet("full", "partial", "gpu", "")]
    [string]$Mode = "",
    [switch]$Force
)

$ErrorActionPreference = "Continue"

# Configuration -- must match setup-infrastructure.ps1
$SUBSCRIPTION_ID = "5a7c-****-****-****-************"  # Replace with your subscription ID
$RESOURCE_GROUP  = "rg-gpu-weather"
$CLUSTER_NAME    = "aks-gpu-weather"
$ACR_NAME        = "acrgpuweather"
$STORAGE_NAME    = "stgpuweather"
$KEYVAULT_NAME   = ""
$REGION          = ""

# Try to load from .infra-config.json if it exists
$configFile = Join-Path $PSScriptRoot ".infra-config.json"
if (Test-Path $configFile) {
    $cfg = Get-Content $configFile -Raw | ConvertFrom-Json
    if ($cfg.ResourceGroup) { $RESOURCE_GROUP = $cfg.ResourceGroup }
    if ($cfg.ClusterName)   { $CLUSTER_NAME = $cfg.ClusterName }
    if ($cfg.AcrName)       { $ACR_NAME = $cfg.AcrName }
    if ($cfg.StorageName)   { $STORAGE_NAME = $cfg.StorageName }
    if ($cfg.KeyVaultName)  { $KEYVAULT_NAME = $cfg.KeyVaultName }
    if ($cfg.Region)        { $REGION = $cfg.Region }
}

function Write-Step { param([string]$Message); Write-Host "`n=== $Message ===" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message); Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Skip { param([string]$Message); Write-Host "  [SKIP] $Message" -ForegroundColor Gray }
function Write-Info { param([string]$Message); Write-Host "  $Message" -ForegroundColor Gray }

# Set subscription
az account set --subscription $SUBSCRIPTION_ID 2>$null

# Interactive mode selection
if (-not $Mode) {
    Write-Host ""
    Write-Host "  Teardown Modes:" -ForegroundColor Cyan
    Write-Host "    1. full    - Delete EVERYTHING (resource group + all resources)" -ForegroundColor White
    Write-Host "    2. partial - Stop AKS cluster (keep all resources, resume later)" -ForegroundColor White
    Write-Host "    3. gpu     - Remove GPU node pool only (keep cluster running)" -ForegroundColor White
    Write-Host ""
    $choice = Read-Host "  Select mode (1/2/3)"
    switch ($choice) {
        "1" { $Mode = "full" }
        "2" { $Mode = "partial" }
        "3" { $Mode = "gpu" }
        "full" { $Mode = "full" }
        "partial" { $Mode = "partial" }
        "gpu" { $Mode = "gpu" }
        default {
            Write-Host "  Invalid choice. Exiting." -ForegroundColor Red
            exit 1
        }
    }
}

Write-Host ""
Write-Host "  Mode: $Mode" -ForegroundColor Yellow

# ============================================================
# Check what exists before doing anything
# ============================================================
Write-Step "Checking existing resources"

$rgExists = az group exists --name $RESOURCE_GROUP -o tsv 2>$null
if ($rgExists -eq "true") { Write-Info "Resource group: EXISTS" }
else { Write-Skip "Resource group $RESOURCE_GROUP does not exist -- nothing to tear down"; exit 0 }

$aksExists = az aks show --name $CLUSTER_NAME --resource-group $RESOURCE_GROUP --query provisioningState -o tsv 2>$null
if ($aksExists) { Write-Info "AKS cluster: EXISTS ($aksExists)" }
else { Write-Info "AKS cluster: does not exist" }

$gpuPoolExists = $false
$gpuPoolName = $null
if ($aksExists) {
    foreach ($pn in @("gpupool1", "gpupool", "gpu")) {
        $gpuCheck = az aks nodepool show --resource-group $RESOURCE_GROUP --cluster-name $CLUSTER_NAME --name $pn --query provisioningState -o tsv 2>$null
        if ($gpuCheck) { Write-Info "GPU node pool: EXISTS ($pn)"; $gpuPoolExists = $true; $gpuPoolName = $pn; break }
    }
    if (-not $gpuPoolExists) { Write-Info "GPU node pool: does not exist" }
}

$acrExists = az acr show --name $ACR_NAME --query loginServer -o tsv 2>$null
if ($acrExists) { Write-Info "ACR: EXISTS ($acrExists)" }
else { Write-Info "ACR: does not exist" }

$stExists = az storage account show --name $STORAGE_NAME --query provisioningState -o tsv 2>$null
if ($stExists) { Write-Info "Storage: EXISTS" }
else { Write-Info "Storage: does not exist" }

# ============================================================
# Confirmation
# ============================================================
if (-not $Force) {
    Write-Host ""
    switch ($Mode) {
        "full" {
            Write-Host "  WARNING: This will DELETE the entire resource group:" -ForegroundColor Red
            Write-Host "    - AKS cluster ($CLUSTER_NAME)" -ForegroundColor Red
            Write-Host "    - Container Registry ($ACR_NAME)" -ForegroundColor Red
            Write-Host "    - Storage Account ($STORAGE_NAME) -- including trained models" -ForegroundColor Red
            Write-Host "    - Key Vault, App Insights, and all other resources" -ForegroundColor Red
            Write-Host "  This is NOT reversible." -ForegroundColor Red
        }
        "partial" {
            Write-Host "  This will STOP the AKS cluster (no compute cost)." -ForegroundColor Yellow
            Write-Host "  All resources, data, and models are preserved." -ForegroundColor Yellow
            Write-Host "  Resume later with: az aks start --name $CLUSTER_NAME --resource-group $RESOURCE_GROUP" -ForegroundColor Yellow
        }
        "gpu" {
            Write-Host "  This will DELETE the GPU node pool only." -ForegroundColor Yellow
            Write-Host "  AKS cluster, frontend, and CPU backend continue running." -ForegroundColor Yellow
            Write-Host "  Re-add later with setup-infrastructure.ps1 (Step 6 will re-create it)." -ForegroundColor Yellow
        }
    }
    Write-Host ""
    $confirm = Read-Host "  Type 'yes' to proceed"
    if ($confirm -ne "yes") {
        Write-Host "  Cancelled." -ForegroundColor Gray
        exit 0
    }
}

# ============================================================
# Execute teardown
# ============================================================

switch ($Mode) {
    # ----------------------------------------------------------
    "gpu" {
        Write-Step "Removing GPU node pool"
        if ($gpuPoolExists) {
            # First switch backend to CPU mode if kubectl is available
            $k8sCpuFile = Join-Path $PSScriptRoot "k8s\backend-deployment-cpu.yaml"
            if (Test-Path $k8sCpuFile) {
                Write-Info "Switching backend to CPU mode..."
                kubectl apply -f $k8sCpuFile 2>$null
                Start-Sleep -Seconds 10
            }

            Write-Info "Deleting GPU node pool (takes 2-5 minutes)..."
            az aks nodepool delete `
                --resource-group $RESOURCE_GROUP `
                --cluster-name $CLUSTER_NAME `
                --name $gpuPoolName `
                --no-wait `
                -o none 2>$null
            Write-Ok "GPU node pool deletion started"
            Write-Info "Saves ~`$15/day. Re-add with setup-infrastructure.ps1"
        }
        else {
            Write-Skip "GPU node pool does not exist"
        }
    }

    # ----------------------------------------------------------
    "partial" {
        Write-Step "Stopping AKS cluster"
        if ($aksExists -and $aksExists -ne "Stopping" -and $aksExists -ne "Stopped") {
            Write-Info "Stopping AKS cluster (takes 2-5 minutes)..."
            az aks stop --name $CLUSTER_NAME --resource-group $RESOURCE_GROUP --no-wait 2>$null
            Write-Ok "AKS cluster stop initiated"
            Write-Host ""
            Write-Host "  To resume later:" -ForegroundColor Green
            Write-Host "    az aks start --name $CLUSTER_NAME --resource-group $RESOURCE_GROUP" -ForegroundColor Cyan
            Write-Host "    az aks get-credentials --name $CLUSTER_NAME --resource-group $RESOURCE_GROUP" -ForegroundColor Cyan
        }
        elseif ($aksExists -eq "Stopped") {
            Write-Skip "AKS cluster is already stopped"
        }
        else {
            Write-Skip "AKS cluster does not exist"
        }
    }

    # ----------------------------------------------------------
    "full" {
        # Clean up K8s namespace first (graceful)
        if ($aksExists -and $aksExists -eq "Succeeded") {
            Write-Step "Cleaning up Kubernetes resources"
            $hasCredentials = kubectl config current-context 2>$null
            if ($hasCredentials) {
                kubectl delete namespace gpu-weather --ignore-not-found=true 2>$null
                Write-Ok "Deleted gpu-weather namespace"
            }
            else {
                Write-Skip "No kubectl credentials -- skipping K8s cleanup"
            }
        }

        # Purge Key Vault to avoid soft-delete blocking re-creation
        if ($KEYVAULT_NAME) {
            Write-Step "Purging Key Vault: $KEYVAULT_NAME"
            $kvCheck = az keyvault list --resource-group $RESOURCE_GROUP --query "[?name=='$KEYVAULT_NAME'].name" -o tsv 2>$null
            if ($kvCheck) {
                az keyvault delete --name $KEYVAULT_NAME --resource-group $RESOURCE_GROUP -o none 2>$null
                $purgeArgs = @("keyvault", "purge", "--name", $KEYVAULT_NAME, "-o", "none")
                if ($REGION) { $purgeArgs += "--location"; $purgeArgs += $REGION }
                az @purgeArgs 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Ok "Key Vault deleted and purged" }
                else { Write-Info "Key Vault purge failed (may need elevated permissions)" }
            } else {
                Write-Skip "Key Vault '$KEYVAULT_NAME' does not exist in resource group"
            }
        } else {
            Write-Skip "No Key Vault name in config  -- skipping"
        }

        Write-Step "Deleting resource group: $RESOURCE_GROUP"
        Write-Info "This deletes ALL resources inside it. Takes 5-10 minutes..."
        az group delete --name $RESOURCE_GROUP --yes 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Resource group deleted: $RESOURCE_GROUP"
        } else {
            Write-Info "Resource group delete may have timed out. Check status manually:"
            Write-Info "  az group show --name $RESOURCE_GROUP --query provisioningState -o tsv"
        }

        # Purge Key Vault again in case it was soft-deleted by the RG deletion
        if ($KEYVAULT_NAME) {
            $showArgs = @("keyvault", "show-deleted", "--name", $KEYVAULT_NAME, "--query", "name", "-o", "tsv")
            if ($REGION) { $showArgs += "--location"; $showArgs += $REGION }
            $softDeletedKv = az @showArgs 2>$null
            if ($softDeletedKv) {
                Write-Info "Purging soft-deleted Key Vault..."
                $purgeArgs = @("keyvault", "purge", "--name", $KEYVAULT_NAME, "-o", "none")
                if ($REGION) { $purgeArgs += "--location"; $purgeArgs += $REGION }
                az @purgeArgs 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Ok "Purged soft-deleted Key Vault" }
                else { Write-Info "Key Vault purge failed. Names are unique per run, so next setup won't conflict." }
            }
        }

        # Clean up local files
        Write-Step "Cleaning up local files"

        $infraConfig = Join-Path $PSScriptRoot ".infra-config.json"
        if (Test-Path $infraConfig) {
            Remove-Item $infraConfig -Force
            Write-Ok "Removed .infra-config.json"
        }
        else { Write-Skip ".infra-config.json not found" }

        # Reset secrets.yaml to placeholder
        $secretsFile = Join-Path $PSScriptRoot "k8s\secrets.yaml"
        if (Test-Path $secretsFile) {
            $resetSecrets = @"
apiVersion: v1
kind: Secret
metadata:
  name: weather-secrets
  namespace: gpu-weather
type: Opaque
# NOTE: This file is a template. The real connection string is injected at
# deploy time by build-and-deploy.ps1 (fetched live from Azure).
# Do NOT put real credentials here.
stringData:
  BLOB_CONNECTION_STRING: "PLACEHOLDER_DO_NOT_DEPLOY_DIRECTLY"
"@
            Set-Content $secretsFile -Value $resetSecrets
            Write-Ok "Reset secrets.yaml to placeholder"
        }

        # Remove kubectl context, cluster, and user entries
        kubectl config delete-context $CLUSTER_NAME 2>$null
        kubectl config delete-cluster $CLUSTER_NAME 2>$null
        kubectl config delete-user "clusterUser_${RESOURCE_GROUP}_${CLUSTER_NAME}" 2>$null
        Write-Ok "Cleaned up kubectl config"
    }
}

# ============================================================
# Summary
# ============================================================
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Teardown complete ($Mode mode)" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan

switch ($Mode) {
    "full" {
        Write-Host ""
        Write-Host "  All resources deleted. Key Vault purged." -ForegroundColor White
        Write-Host "  You can immediately re-deploy from scratch:" -ForegroundColor Green
        Write-Host "    .\setup-infrastructure.ps1" -ForegroundColor Cyan
        Write-Host "    .\build-and-deploy.ps1" -ForegroundColor Cyan
    }
    "partial" {
        Write-Host ""
        Write-Host "  Cluster is stopping. Cost: ~`$0/day (only storage fees remain ~`$2/mo)." -ForegroundColor White
        Write-Host ""
        Write-Host "  To resume:" -ForegroundColor Green
        Write-Host "    az aks start --name $CLUSTER_NAME --resource-group $RESOURCE_GROUP" -ForegroundColor Cyan
        Write-Host "    az aks get-credentials --name $CLUSTER_NAME --resource-group $RESOURCE_GROUP" -ForegroundColor Cyan
    }
    "gpu" {
        Write-Host ""
        Write-Host "  GPU pool removed. CPU cluster still running." -ForegroundColor White
        Write-Host ""
        Write-Host "  To re-add GPU:" -ForegroundColor Green
        Write-Host "    .\setup-infrastructure.ps1  (Step 6 will re-create GPU pool)" -ForegroundColor Cyan
    }
}
Write-Host ""
