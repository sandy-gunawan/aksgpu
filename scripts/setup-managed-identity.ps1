param(
    [string]$ResourceGroup = "rg-gpu-weather",
    [string]$ClusterName   = "aks-gpu-weather",
    [string]$StorageName   = "stgpuweather"
)

<#
.SYNOPSIS
    Grants AKS kubelet managed identity "Storage Blob Data Contributor" on the storage account.
    Required when Azure policy blocks key-based storage authentication.

.DESCRIPTION
    This script:
    1. Gets the AKS kubelet managed identity object ID
    2. Gets the storage account resource ID
    3. Checks if the role assignment already exists
    4. Creates the role assignment if missing

.EXAMPLE
    .\scripts\setup-managed-identity.ps1
    .\scripts\setup-managed-identity.ps1 -ResourceGroup "my-rg" -ClusterName "my-aks" -StorageName "mystg"
#>

$ErrorActionPreference = "Continue"

function Write-Ok   { param([string]$M); Write-Host "  [PASS] $M" -ForegroundColor Green }
function Write-Fail { param([string]$M); Write-Host "  [FAIL] $M" -ForegroundColor Red }
function Write-Info { param([string]$M); Write-Host "  $M" -ForegroundColor Gray }

# Load config if available
$configFile = Join-Path $PSScriptRoot "..\.infra-config.json"
if (Test-Path $configFile) {
    $cfg = Get-Content $configFile -Raw | ConvertFrom-Json
    if (-not $PSBoundParameters.ContainsKey('ResourceGroup')) { $ResourceGroup = $cfg.ResourceGroup }
    if (-not $PSBoundParameters.ContainsKey('ClusterName'))   { $ClusterName   = $cfg.ClusterName }
    if (-not $PSBoundParameters.ContainsKey('StorageName'))   { $StorageName   = $cfg.StorageName }
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Setting up Managed Identity for Blob Storage" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Step 1: Get kubelet identity (both objectId and clientId)
Write-Info "Getting AKS kubelet managed identity..."
$kubeletId = az aks show --resource-group $ResourceGroup --name $ClusterName `
    --query "identityProfile.kubeletidentity.objectId" -o tsv 2>$null
$kubeletClientId = az aks show --resource-group $ResourceGroup --name $ClusterName `
    --query "identityProfile.kubeletidentity.clientId" -o tsv 2>$null

if ([string]::IsNullOrEmpty($kubeletId)) {
    Write-Fail "Could not get kubelet identity from AKS cluster '$ClusterName'"
    exit 1
}
Write-Ok "Kubelet object ID: $kubeletId"
Write-Ok "Kubelet client ID: $kubeletClientId"

# Step 2: Get storage account resource ID
Write-Info "Getting storage account resource ID..."
$storageId = az storage account show --name $StorageName --resource-group $ResourceGroup `
    --query "id" -o tsv 2>$null

if ([string]::IsNullOrEmpty($storageId)) {
    Write-Fail "Could not find storage account '$StorageName'"
    exit 1
}
Write-Ok "Storage account: $storageId"

# Step 3: Check if role already assigned
Write-Info "Checking existing role assignments..."
$existing = az role assignment list --assignee $kubeletId --scope $storageId `
    --role "Storage Blob Data Contributor" --query "[].id" -o tsv 2>$null

if (-not [string]::IsNullOrEmpty($existing)) {
    Write-Ok "Storage Blob Data Contributor role already assigned  -- nothing to do"
    exit 0
}

# Step 4: Create role assignment
Write-Info "Assigning 'Storage Blob Data Contributor' role..."
az role assignment create `
    --assignee $kubeletId `
    --role "Storage Blob Data Contributor" `
    --scope $storageId `
    -o none 2>$null

if ($LASTEXITCODE -eq 0) {
    Write-Ok "Role assigned: Storage Blob Data Contributor"
    Write-Info "Note: Role propagation may take 1-2 minutes"
} else {
    Write-Fail "Role assignment failed. You may need Owner or User Access Administrator permissions."
    exit 1
}

# Step 5: Set AZURE_CLIENT_ID in K8s configmap so pods know which identity to use
Write-Info "Patching configmap with AZURE_CLIENT_ID..."
$ns = kubectl get namespace gpu-weather --no-headers 2>$null
if ($ns) {
    kubectl patch configmap weather-config -n gpu-weather `
        --type merge -p "{`"data`":{`"AZURE_CLIENT_ID`":`"$kubeletClientId`"}}" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "ConfigMap patched: AZURE_CLIENT_ID=$kubeletClientId"
    } else {
        Write-Info "ConfigMap patch failed (namespace may not exist yet - will be set at deploy)"
    }
} else {
    Write-Info "Namespace gpu-weather not found yet - AZURE_CLIENT_ID will be set at deploy time"
}

# Also update the local configmap.yaml so future deploys include it
$configmapFile = Join-Path $PSScriptRoot "..\k8s\configmap.yaml"
if (Test-Path $configmapFile) {
    $content = Get-Content $configmapFile -Raw
    if ($content -match 'AZURE_CLIENT_ID:\s*"[^"]*"') {
        $content = $content -replace 'AZURE_CLIENT_ID:\s*"[^"]*"', "AZURE_CLIENT_ID: `"$kubeletClientId`""
    }
    Set-Content $configmapFile -Value $content -NoNewline
    Write-Ok "Updated configmap.yaml: AZURE_CLIENT_ID=$kubeletClientId"
}

# Step 6: Ensure storage containers exist (using login auth, not key auth)
Write-Info "Ensuring blob containers exist (using login auth)..."
foreach ($container in @("weather-data", "models", "predictions")) {
    az storage container create --name $container --account-name $StorageName --auth-mode login -o none 2>$null
    Write-Ok "Container verified: $container"
}

Write-Host ""
Write-Ok "Managed identity setup complete"
Write-Host ""
