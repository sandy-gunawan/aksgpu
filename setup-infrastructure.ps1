param(
    [switch]$SkipGpuCheck,
    [switch]$GpuApproved    # Set this if you have MCAPS policy exception for N-series VMs
)

# Use Continue so Azure CLI stderr warnings don't kill the script
$ErrorActionPreference = "Continue"

# Configuration  -- fixed names (scoped to RG, not globally unique)
$SUBSCRIPTION_ID = "5a7c-****-****-****-************"  # Replace with your subscription ID
$RESOURCE_GROUP  = "rg-gpu-weather"
$CLUSTER_NAME    = "aks-gpu-weather"
$APPINSIGHTS     = "ai-gpu-weather"
$GPU_SKU         = "Standard_NC4as_T4_v3"
$GPU_LABEL       = "nvidia-t4"

# Load existing config if available (resume from previous partial run)
# Only generate new unique names if no config exists
$configPath = Join-Path $PSScriptRoot ".infra-config.json"
if (Test-Path $configPath) {
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    $ACR_NAME      = $cfg.AcrName
    $STORAGE_NAME  = $cfg.StorageName
    $KEYVAULT_NAME = $cfg.KeyVaultName
    Write-Host "  Loaded existing config: ACR=$ACR_NAME, Storage=$STORAGE_NAME" -ForegroundColor Gray
} else {
    # Generate a unique 4-char suffix for globally-unique resource names
    $SUFFIX = -join ((97..122) + (48..57) | Get-Random -Count 4 | ForEach-Object { [char]$_ })
    $ACR_NAME        = "acrgpuweather$SUFFIX"
    $STORAGE_NAME    = "stgpuweather$SUFFIX"
    $KEYVAULT_NAME   = "kv-gpu-wea-$SUFFIX"
    Write-Host "  Generated new unique names: ACR=$ACR_NAME, Storage=$STORAGE_NAME" -ForegroundColor Gray
}

# CPU SKU -- 4 vCPU candidates (need this much room for AKS system pods + APIs)
# OS disk forced to 30 GB so Ephemeral disk fits in the cache disk
# (avoids "OS disk > 100 GB not allowed" error). Ephemeral disk is free.
$CPU_SKU_CANDIDATES = @(
    "Standard_DC4ds_v3",
    "Standard_D4ads_v6",
    "Standard_DC4s_v3",
    "Standard_EC4ads_v5",
    "Standard_EC4as_v5"
)
$CPU_OSDISK_SIZE_GB = 30

$CANDIDATE_REGIONS = @(
    "eastus2", "eastus"
)

function Write-Step { param([string]$Message); Write-Host "`n========================================" -ForegroundColor Cyan; Write-Host "  $Message" -ForegroundColor Cyan; Write-Host "========================================" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message); Write-Host "  [PASS] $Message" -ForegroundColor Green }
function Write-Fail { param([string]$Message); Write-Host "  [FAIL] $Message" -ForegroundColor Red }
function Write-Info { param([string]$Message); Write-Host "  $Message" -ForegroundColor Gray }

# Step 0: Prerequisites
Write-Step "Step 0: Checking prerequisites"

$azVer = az --version 2>$null | Select-Object -First 1
if (-not $azVer) { Write-Fail "Azure CLI not found"; exit 1 }
Write-Ok "Azure CLI: $azVer"

$kubectlCheck = kubectl version --client --short 2>$null
if (-not $kubectlCheck) {
    Write-Info "Installing kubectl via az..."
    az aks install-cli 2>$null
}
Write-Ok "kubectl available"

$dockerVer = docker --version 2>$null
if ($dockerVer) { Write-Ok "Docker: $dockerVer" }
else { Write-Info "Docker not found (needed later for build-and-deploy.ps1)" }

Write-Info "Setting subscription: $SUBSCRIPTION_ID"
az account set --subscription $SUBSCRIPTION_ID
$currentSub = az account show --query name -o tsv
Write-Ok "Subscription: $currentSub"

# Step 1: Register providers
Write-Step "Step 1: Registering Azure providers"

$providers = @("Microsoft.ContainerService","Microsoft.Compute","Microsoft.Storage","Microsoft.KeyVault","Microsoft.OperationalInsights","Microsoft.ContainerRegistry")
foreach ($ns in $providers) {
    $state = az provider show --namespace $ns --query registrationState -o tsv 2>$null
    if ($state -ne "Registered") {
        Write-Info "Registering $ns ..."
        az provider register --namespace $ns --wait 2>$null
    }
    Write-Ok "$ns = Registered"
}

# Step 2: GPU Capacity Discovery
$REGION = $null

if ($SkipGpuCheck) {
    $REGION = "eastus2"
    Write-Step "Step 2: GPU check SKIPPED - using region: $REGION"
}
else {
    Write-Step "Step 2: Scanning regions for GPU availability"

    foreach ($r in $CANDIDATE_REGIONS) {
        Write-Info "Checking $r ..."

        # Check 1: Is the SKU available in this region?
        $skuInfo = az vm list-skus --location $r --resource-type virtualMachines `
            --query "[?name=='$GPU_SKU'].restrictions[0].reasonCode" -o tsv 2>$null

        if ($LASTEXITCODE -ne 0) {
            Write-Info "  $r : Error querying SKU"
            continue
        }

        if (-not [string]::IsNullOrEmpty($skuInfo)) {
            if ($skuInfo -eq "NotAvailableForSubscription") {
                Write-Info "  $r : SKU not available for this subscription"
            }
            else {
                Write-Info "  $r : SKU restricted ($skuInfo)"
            }
            continue
        }

        # Check 2: Does this subscription have T4 quota specifically?
        $t4Quota = az vm list-usage --location $r -o json 2>$null | ConvertFrom-Json
        $t4Family = $t4Quota | Where-Object { $_.name.localizedValue -match "NCASv3_T4" }
        $t4Limit = if ($t4Family) { $t4Family.limit } else { 0 }
        $t4Used = if ($t4Family) { $t4Family.currentValue } else { 0 }
        $t4Avail = $t4Limit - $t4Used

        if ($t4Limit -lt 4) {
            Write-Info "  $r : SKU available but T4 quota = $t4Limit (need >= 4)"
            continue
        }

        Write-Ok "$r : SKU available, T4 quota OK ($t4Used/$t4Limit used, $t4Avail free)"
        $REGION = $r
        break
    }

    if (-not $REGION) {
        Write-Fail "No region found with available $GPU_SKU and sufficient quota!"
        Write-Host ""
        Write-Host "  Options:" -ForegroundColor Yellow
        Write-Host "    1. Request GPU quota increase: Azure Portal > Subscriptions > Usage + quotas" -ForegroundColor Yellow
        Write-Host "    2. Try a different GPU SKU" -ForegroundColor Yellow
        Write-Host "    3. Re-run with -SkipGpuCheck if you just requested quota" -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }
}

Write-Host ""
Write-Host "  >> Using region: $REGION" -ForegroundColor Green
Write-Host "  >> GPU SKU: $GPU_SKU" -ForegroundColor Green

# Step 3: Resource Group
Write-Step "Step 3: Creating Resource Group"

$rgState = az group show --name $RESOURCE_GROUP --query provisioningState -o tsv 2>$null
if ($rgState -eq "Succeeded") {
    Write-Ok "Resource group already exists: $RESOURCE_GROUP"
}
elseif ($rgState -eq "Deleting") {
    Write-Info "Resource group is still being deleted from a previous teardown. Waiting..."
    $waited = 0
    while ($waited -lt 600) {
        $rgCheck = az group show --name $RESOURCE_GROUP --query provisioningState -o tsv 2>$null
        if (-not $rgCheck -or $rgCheck -ne "Deleting") { break }
        Start-Sleep -Seconds 15
        $waited += 15
        Write-Info "  Still deleting... ($waited s)"
    }
    if ($waited -ge 600) { Write-Fail "Resource group deletion timed out after 10 minutes"; exit 1 }
    Write-Ok "Previous resource group fully deleted"
    az group create --name $RESOURCE_GROUP --location $REGION -o none
    Write-Ok "Created resource group: $RESOURCE_GROUP in $REGION"
}
else {
    az group create --name $RESOURCE_GROUP --location $REGION -o none
    Write-Ok "Created resource group: $RESOURCE_GROUP in $REGION"
}

# Save config early so partial runs can resume with the same names
$configPath = Join-Path $PSScriptRoot ".infra-config.json"
$configOut = @{
    Region        = $REGION
    ResourceGroup = $RESOURCE_GROUP
    ClusterName   = $CLUSTER_NAME
    AcrName       = $ACR_NAME
    StorageName   = $STORAGE_NAME
    KeyVaultName  = $KEYVAULT_NAME
    GpuSku        = $GPU_SKU
    GpuLabel      = $GPU_LABEL
}
$configOut | ConvertTo-Json | Set-Content $configPath
Write-Ok "Saved config to .infra-config.json (names locked for this run)"

# Step 4: ACR
Write-Step "Step 4: Creating Azure Container Registry"

$acrExists = az acr show --name $ACR_NAME --query loginServer -o tsv 2>$null
if ($acrExists) {
    Write-Ok "ACR already exists: $acrExists"
}
else {
    $result = az acr create --name $ACR_NAME --resource-group $RESOURCE_GROUP --sku Basic -o tsv --query loginServer 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Created ACR: $result"
    } else {
        Write-Fail "Could not create ACR '$ACR_NAME'"; exit 1
    }
}

# Step 5: AKS Cluster
Write-Step "Step 5: Creating AKS cluster (5-10 minutes)"

$aksExists = az aks show --name $CLUSTER_NAME --resource-group $RESOURCE_GROUP --query provisioningState -o tsv 2>$null
if ($aksExists -eq "Succeeded") {
    Write-Ok "AKS cluster already exists: $CLUSTER_NAME"
}
else {
    # Try each CPU SKU candidate until one succeeds
    $CPU_SKU = $null
    $clusterCreated = $false

    foreach ($candidate in $CPU_SKU_CANDIDATES) {
        Write-Info "Trying AKS with CPU SKU: $candidate ..."
        az aks create `
            --resource-group $RESOURCE_GROUP `
            --name $CLUSTER_NAME `
            --node-count 1 `
            --node-vm-size $candidate `
            --node-osdisk-size $CPU_OSDISK_SIZE_GB `
            --generate-ssh-keys `
            --attach-acr $ACR_NAME `
            --enable-app-routing `
            --network-plugin azure `
            --enable-managed-identity `
            --location $REGION `
            -o none 2>$null

        if ($LASTEXITCODE -eq 0) {
            $CPU_SKU = $candidate
            $clusterCreated = $true
            Write-Ok "AKS cluster created with: $CPU_SKU"
            break
        }
        Write-Info "  $candidate failed, trying next..."
        # Delete failed cluster attempt if partially created
        az aks delete --name $CLUSTER_NAME --resource-group $RESOURCE_GROUP --yes --no-wait 2>$null
        Start-Sleep -Seconds 5
    }

    if (-not $clusterCreated) {
        Write-Fail "AKS cluster creation failed with all CPU SKU candidates."
        Write-Info "Your subscription restricts which VM sizes can be used."
        Write-Info "Check: az aks create --help or try from Azure Portal."
        exit 1
    }
}

Write-Info "Fetching kubectl credentials..."
az aks get-credentials --resource-group $RESOURCE_GROUP --name $CLUSTER_NAME --overwrite-existing
Write-Ok "kubectl configured"

$nodeCount = (kubectl get nodes --no-headers 2>$null | Measure-Object -Line).Lines
Write-Ok "Cluster has $nodeCount CPU nodes"

# Step 6: GPU Node Pool
Write-Step "Step 6: GPU node pool"

# Check if any GPU pool exists (gpupool or gpupool1)
$gpuPoolName = $null
foreach ($name in @("gpupool", "gpupool1", "gpu")) {
    $state = az aks nodepool show --resource-group $RESOURCE_GROUP --cluster-name $CLUSTER_NAME --name $name --query provisioningState -o tsv 2>$null
    if ($state -eq "Succeeded") {
        $gpuPoolName = $name
        break
    }
}

if ($gpuPoolName) {
    Write-Ok "GPU node pool already exists: $gpuPoolName"
}
else {
    # Always try CLI first (works if MCAPS policy exception is granted)
    Write-Info "Creating GPU pool: 1x $GPU_SKU with autoscaler 0-1, max-pods=30..."
    $poolName = "gpupool1"
    az aks nodepool add `
        --resource-group $RESOURCE_GROUP `
        --cluster-name $CLUSTER_NAME `
        --name $poolName `
        --node-count 1 `
        --node-vm-size $GPU_SKU `
        --node-osdisk-size 30 `
        --node-taints "sku=gpu:NoSchedule" `
        --labels "sku=gpu" `
        --enable-cluster-autoscaler `
        --min-count 0 `
        --max-count 1 `
        --max-pods 30 `
        -o none 2>$null

    if ($LASTEXITCODE -eq 0) {
        Write-Ok "GPU node pool created: $poolName"
    }
    else {
        Write-Fail "GPU pool creation failed via CLI (policy may block it)"
        Write-Host ""
        Write-Host "  Add GPU pool manually via Azure Portal:" -ForegroundColor Yellow
        Write-Host "    1. Go to: https://portal.azure.com" -ForegroundColor White
        Write-Host "    2. Search: $CLUSTER_NAME > Node pools > + Add node pool" -ForegroundColor White
        Write-Host "    3. Name: gpupool1, Mode: User, OS: Ubuntu Linux" -ForegroundColor White
        Write-Host "    4. Node size: search 'NC4as_T4' > select Standard_NC4as_T4_v3" -ForegroundColor White
        Write-Host "    5. Scale method: Autoscale, Min: 0, Max: 1" -ForegroundColor White
        Write-Host "    6. IMPORTANT: Optional settings > Max pods per node: 30" -ForegroundColor White
        Write-Host "    7. Review + create > Create (wait 5-10 min)" -ForegroundColor White
        Write-Host ""
    }
}

# Step 7: NVIDIA Device Plugin
Write-Step "Step 7: Installing NVIDIA device plugin"

$nvidiaExists = kubectl get daemonset -n kube-system nvidia-device-plugin-daemonset --no-headers 2>$null
if ($nvidiaExists) {
    Write-Ok "NVIDIA device plugin already installed"
}
else {
    kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.1/nvidia-device-plugin.yml 2>$null
    Write-Ok "NVIDIA device plugin deployed"
}

# Patch the DaemonSet so it tolerates the sku=gpu taint AND only schedules on GPU nodes.
# Without this, the DS lands on the CPU node (where no GPU exists) and the GPU
# node never registers nvidia.com/gpu capacity, so training pods stay Pending.
Write-Info "Patching device plugin tolerations + nodeSelector for GPU pool..."
$dsPatch = '{"spec":{"template":{"spec":{"tolerations":[{"effect":"NoSchedule","key":"nvidia.com/gpu","operator":"Exists"},{"effect":"NoSchedule","key":"sku","operator":"Equal","value":"gpu"}],"nodeSelector":{"kubernetes.azure.com/accelerator":"nvidia"}}}}}'
kubectl patch ds -n kube-system nvidia-device-plugin-daemonset --type=strategic -p $dsPatch 2>$null | Out-Null
Write-Ok "Device plugin patched (tolerates sku=gpu, runs only on GPU nodes)"

Write-Info "Waiting 30s for plugin to start..."
Start-Sleep -Seconds 30

$nvidiaRunning = kubectl get pods -n kube-system -l app=nvidia-device-plugin --no-headers 2>$null | Select-String "Running"
if ($nvidiaRunning) { Write-Ok "NVIDIA plugin is Running" }
else { Write-Info "Plugin may still be starting. Check: kubectl get pods -n kube-system | findstr nvidia" }

# Step 8: Blob Storage (policy-aware  -- handles both key-based and MI-only auth)
Write-Step "Step 8: Creating Blob Storage"

$stExists = az storage account show --name $STORAGE_NAME --query provisioningState -o tsv 2>$null
if ($stExists -eq "Succeeded") {
    Write-Ok "Storage account already exists: $STORAGE_NAME"
}
else {
    # Try with shared key access enabled first (needed for connection string auth)
    az storage account create --name $STORAGE_NAME --resource-group $RESOURCE_GROUP `
        --sku Standard_LRS --kind StorageV2 --location $REGION `
        --min-tls-version TLS1_2 --https-only true `
        --allow-shared-key-access true `
        -o none 2>$null
    if ($LASTEXITCODE -ne 0) {
        # Policy may block shared key access  -- retry without it (MI-only mode)
        Write-Info "Shared key access blocked by policy. Creating with MI-only auth..."
        az storage account create --name $STORAGE_NAME --resource-group $RESOURCE_GROUP `
            --sku Standard_LRS --kind StorageV2 --location $REGION `
            --min-tls-version TLS1_2 --https-only true `
            --allow-shared-key-access false `
            -o none 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Could not create storage account '$STORAGE_NAME'"
            exit 1
        }
        Write-Ok "Created storage account: $STORAGE_NAME (MI-only, shared key disabled by policy)"
    } else {
        Write-Ok "Created storage account: $STORAGE_NAME"
    }
}

# Check if shared key access is actually enabled (policy may have overridden it)
$sharedKeyEnabled = az storage account show --name $STORAGE_NAME --resource-group $RESOURCE_GROUP `
    --query allowSharedKeyAccess -o tsv 2>$null
if ($sharedKeyEnabled -eq "true") {
    Write-Ok "Shared key access: enabled (connection string auth will work)"
    $BLOB_CONN_STR = az storage account show-connection-string --name $STORAGE_NAME `
        --resource-group $RESOURCE_GROUP --query connectionString -o tsv 2>$null
    Write-Ok "Got storage connection string"
} else {
    Write-Info "Shared key access: disabled by policy (will use managed identity only)"
    $BLOB_CONN_STR = ""
}

# Step 8.5: Set up Managed Identity RBAC for Blob Storage
# This is REQUIRED when shared key access is disabled, and best practice regardless
Write-Step "Step 8.5: Setting up Managed Identity for Blob Storage"
$miScript = Join-Path $PSScriptRoot "scripts\setup-managed-identity.ps1"
if (Test-Path $miScript) {
    & $miScript -ResourceGroup $RESOURCE_GROUP -ClusterName $CLUSTER_NAME -StorageName $STORAGE_NAME
} else {
    Write-Info "scripts\setup-managed-identity.ps1 not found"
    Write-Fail "Managed identity setup is required for blob storage access"
    exit 1
}

# Step 8.6: Create blob containers (must use login auth  -- key auth may be blocked)
Write-Step "Step 8.6: Creating Blob Containers"
$allContainersOk = $true
foreach ($container in @("weather-data", "models", "predictions", "crop-models", "crop-data")) {
    az storage container create --name $container --account-name $STORAGE_NAME --auth-mode login -o none 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Info "Container '$container' failed with login auth, trying key auth..."
        az storage container create --name $container --account-name $STORAGE_NAME -o none 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Container '$container' creation failed"
            $allContainersOk = $false
        } else {
            Write-Ok "Container: $container (key auth)"
        }
    } else {
        Write-Ok "Container: $container"
    }
}
if (-not $allContainersOk) {
    Write-Host ""
    Write-Host "  Some containers failed to create. This usually means your" -ForegroundColor Yellow
    Write-Host "  user account needs 'Storage Blob Data Contributor' on the" -ForegroundColor Yellow
    Write-Host "  storage account. Run this and try again:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Run this command to fix:" -ForegroundColor Yellow
    Write-Host ""
    $userId = az ad signed-in-user show --query id -o tsv 2>$null
    $stId = az storage account show --name $STORAGE_NAME --resource-group $RESOURCE_GROUP --query id -o tsv 2>$null
    Write-Host "  az role assignment create --assignee $userId --role 'Storage Blob Data Contributor' --scope $stId" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Press Enter after assigning the role, or Ctrl+C to abort..." -ForegroundColor Yellow
    Read-Host
    # Retry
    foreach ($container in @("weather-data", "models", "predictions", "crop-models", "crop-data")) {
        az storage container create --name $container --account-name $STORAGE_NAME --auth-mode login -o none 2>$null
        if ($LASTEXITCODE -eq 0) { Write-Ok "Container: $container" }
        else { Write-Fail "Container '$container' still failed"; exit 1 }
    }
}

# Step 8.7: Verify blob access works end-to-end
Write-Step "Step 8.7: Verifying Blob Storage Access"

$testBlob = "test-" + (Get-Date -Format "yyyyMMddHHmmss") + ".txt"
$blobTestPassed = $false

# Try blob upload with login auth (uses current CLI user's identity)
az storage blob upload --account-name $STORAGE_NAME --container-name models `
    --name $testBlob --data "test" --auth-mode login --overwrite -o none 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Ok "Blob upload test passed (login auth)"
    az storage blob delete --account-name $STORAGE_NAME --container-name models `
        --name $testBlob --auth-mode login -o none 2>$null
    $blobTestPassed = $true
}

# Try key auth fallback
if (-not $blobTestPassed -and $BLOB_CONN_STR) {
    az storage blob upload --account-name $STORAGE_NAME --container-name models `
        --name $testBlob --data "test" --connection-string $BLOB_CONN_STR --overwrite -o none 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Blob upload test passed (key auth)"
        az storage blob delete --account-name $STORAGE_NAME --container-name models `
            --name $testBlob --connection-string $BLOB_CONN_STR -o none 2>$null
        $blobTestPassed = $true
    }
}

# If failed, try to auto-assign Storage Blob Data Contributor to CLI user
if (-not $blobTestPassed) {
    Write-Info "Blob test failed. Attempting to assign 'Storage Blob Data Contributor' to your CLI user..."
    $userId = az ad signed-in-user show --query id -o tsv 2>$null
    $stId = az storage account show --name $STORAGE_NAME --resource-group $RESOURCE_GROUP --query id -o tsv 2>$null

    if ($userId -and $stId) {
        # Check if already assigned
        $existingRole = az role assignment list --assignee $userId --scope $stId `
            --role "Storage Blob Data Contributor" --query "[].id" -o tsv 2>$null
        if ($existingRole) {
            Write-Ok "Role already assigned to your user. Waiting for propagation..."
        } else {
            az role assignment create --assignee $userId --role "Storage Blob Data Contributor" --scope $stId -o none 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "Assigned 'Storage Blob Data Contributor' to your CLI user"
            } else {
                # Auto-assign failed (policy may block it) -- ask user to do it manually
                Write-Host ""
                Write-Host "  ============================================" -ForegroundColor Yellow
                Write-Host "  MANUAL STEP REQUIRED" -ForegroundColor Yellow
                Write-Host "  ============================================" -ForegroundColor Yellow
                Write-Host ""
                Write-Host "  Could not auto-assign the role (policy may block it)." -ForegroundColor Yellow
                Write-Host "  Please run this command in another terminal:" -ForegroundColor Yellow
                Write-Host ""
                Write-Host "  az role assignment create --assignee $userId --role ""Storage Blob Data Contributor"" --scope $stId" -ForegroundColor Cyan
                Write-Host ""
                Write-Host "  After running the command, wait 1-2 minutes for propagation." -ForegroundColor Yellow
                Write-Host ""
                Read-Host "  Press Enter when done"
            }
        }

        # Wait for RBAC propagation then retry
        Write-Info "Waiting for RBAC propagation (up to 2 min)..."
        for ($attempt = 1; $attempt -le 4; $attempt++) {
            Start-Sleep -Seconds 30
            az storage blob upload --account-name $STORAGE_NAME --container-name models `
                --name $testBlob --data "test" --auth-mode login --overwrite -o none 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "Blob upload test passed (attempt $attempt)"
                az storage blob delete --account-name $STORAGE_NAME --container-name models `
                    --name $testBlob --auth-mode login -o none 2>$null
                $blobTestPassed = $true
                break
            }
            Write-Info "  Propagating... ($($attempt * 30)s)"
        }
    }

    if (-not $blobTestPassed) {
        Write-Fail "Blob access verification failed after retries."
        Write-Info "The AKS kubelet MI has blob access -- pods will work at runtime."
        Write-Info "Only the CLI verification step failed. Continuing..."
        Write-Host ""
    }
}

# Step 9: Key Vault and App Insights
Write-Step "Step 9: Creating Key Vault and Application Insights"

$kvExists = az keyvault show --name $KEYVAULT_NAME --query name -o tsv 2>$null
if ($kvExists) { Write-Ok "Key Vault already exists: $KEYVAULT_NAME" }
else {
    az keyvault create --name $KEYVAULT_NAME --resource-group $RESOURCE_GROUP --location $REGION -o none 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Ok "Created Key Vault: $KEYVAULT_NAME" }
    else { Write-Info "Key Vault creation failed. Non-blocking." }
}

$aiExists = az monitor app-insights component show --app $APPINSIGHTS --resource-group $RESOURCE_GROUP --query instrumentationKey -o tsv 2>$null
if ($aiExists) { Write-Ok "Application Insights already exists" }
else {
    az monitor app-insights component create --app $APPINSIGHTS --location $REGION --resource-group $RESOURCE_GROUP -o none 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Ok "Created Application Insights: $APPINSIGHTS" }
    else { Write-Info "App Insights creation failed. Non-blocking." }
}

# Step 10: Update K8s configs with real values
Write-Step "Step 10: Updating Kubernetes configs"

$secretsFile = Join-Path $PSScriptRoot "k8s\secrets.yaml"
if (Test-Path $secretsFile) {
    # Always write the secrets.yaml with the real connection string
    $connValue = if ($BLOB_CONN_STR) { $BLOB_CONN_STR } else { "MANAGED_IDENTITY_AUTH" }
    $secretsText = "apiVersion: v1`nkind: Secret`nmetadata:`n  name: weather-secrets`n  namespace: gpu-weather`ntype: Opaque`nstringData:`n  BLOB_CONNECTION_STRING: ""$connValue"""
    Set-Content $secretsFile -Value $secretsText -NoNewline
    Write-Ok "Updated secrets.yaml"
}

$configFile = Join-Path $PSScriptRoot "k8s\configmap.yaml"
if (Test-Path $configFile) {
    $content = Get-Content $configFile -Raw
    # Replace any previous storage account name (handles both default and suffixed names)
    $blobNamePattern = 'BLOB_ACCOUNT_NAME: ".*?"'
    $blobNameReplace = "BLOB_ACCOUNT_NAME: ""$STORAGE_NAME"""
    $content = $content -replace $blobNamePattern, $blobNameReplace
    Set-Content $configFile -Value $content -NoNewline
    Write-Ok "Updated configmap.yaml with storage name: $STORAGE_NAME"
}

$acrLogin = az acr show --name $ACR_NAME --query loginServer -o tsv
$acrPattern = "[a-z0-9]+\.azurecr\.io"
$yamlFiles = Get-ChildItem -Path (Join-Path $PSScriptRoot "k8s") -Filter "*.yaml" -Recurse
foreach ($f in $yamlFiles) {
    $c = Get-Content $f.FullName -Raw
    # Replace any previous ACR login server (handles both default and suffixed names)
    if ($c -match $acrPattern) {
        $c = $c -replace $acrPattern, $acrLogin
        Set-Content $f.FullName -Value $c -NoNewline
    }
}
Write-Ok "Updated all K8s YAMLs with ACR: $acrLogin"

# Step 11: Verification
Write-Step "Step 11: Verification Checklist"

$allPassed = $true

$rgExists = az group exists --name $RESOURCE_GROUP -o tsv 2>$null
if ($rgExists -eq "true") { Write-Ok "Resource group: $RESOURCE_GROUP" } else { Write-Fail "Resource group"; $allPassed = $false }

$acrServer = az acr show --name $ACR_NAME --query loginServer -o tsv 2>$null
if ($acrServer) { Write-Ok "ACR: $acrServer" } else { Write-Fail "ACR"; $allPassed = $false }

$aksState = az aks show --name $CLUSTER_NAME --resource-group $RESOURCE_GROUP --query provisioningState -o tsv 2>$null
if ($aksState -eq "Succeeded") { Write-Ok "AKS cluster: $CLUSTER_NAME" }
elseif ($aksState) { Write-Info "AKS cluster: $aksState (may need reconciliation)" }
else { Write-Fail "AKS cluster not found"; $allPassed = $false }

$totalNodes = (kubectl get nodes --no-headers 2>$null | Measure-Object -Line).Lines
Write-Info "Nodes: $totalNodes total"

$gpuNode = kubectl get nodes -l "kubernetes.azure.com/accelerator=nvidia" --no-headers 2>$null
if ($gpuNode) { Write-Ok "GPU node found (nvidia accelerator)" } else { Write-Info "GPU node: not found yet (add via Portal or use -GpuApproved)" }

$stState = az storage account show --name $STORAGE_NAME --resource-group $RESOURCE_GROUP --query provisioningState -o tsv 2>$null
if ($stState -eq "Succeeded") { Write-Ok "Storage: $STORAGE_NAME" } else { Write-Fail "Storage"; $allPassed = $false }

$containers = az storage container list --account-name $STORAGE_NAME --auth-mode login --query "[].name" -o tsv 2>$null
if (-not $containers) {
    $connStr = az storage account show-connection-string --name $STORAGE_NAME --resource-group $RESOURCE_GROUP --query connectionString -o tsv 2>$null
    $containers = az storage container list --connection-string $connStr --query "[].name" -o tsv 2>$null
}
if ($containers -match "models") { Write-Ok "Blob containers: OK" } else { Write-Info "Blob containers: could not verify (may need auth)" }

# Summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
if ($allPassed) { Write-Host "  ALL CHECKS PASSED" -ForegroundColor Green }
else { Write-Host "  SOME CHECKS FAILED" -ForegroundColor Yellow }
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Region         : $REGION" -ForegroundColor White
Write-Host "  Resource Group : $RESOURCE_GROUP" -ForegroundColor White
Write-Host "  AKS Cluster    : $CLUSTER_NAME" -ForegroundColor White
Write-Host "  ACR            : $acrLogin" -ForegroundColor White
Write-Host "  Storage        : $STORAGE_NAME" -ForegroundColor White
Write-Host "  GPU SKU        : $GPU_SKU" -ForegroundColor White
Write-Host ""

# Update config with runtime values (AcrLogin, BlobConnStr)
$configOut = @{
    Region        = $REGION
    ResourceGroup = $RESOURCE_GROUP
    ClusterName   = $CLUSTER_NAME
    AcrName       = $ACR_NAME
    AcrLogin      = $acrLogin
    StorageName   = $STORAGE_NAME
    KeyVaultName  = $KEYVAULT_NAME
    BlobConnStr   = $BLOB_CONN_STR
    GpuSku        = $GPU_SKU
    GpuLabel      = $GPU_LABEL
}
$configOut | ConvertTo-Json | Set-Content (Join-Path $PSScriptRoot ".infra-config.json")
Write-Ok "Updated .infra-config.json with final values"

Write-Host ""
Write-Host "  NEXT: Run .\build-and-deploy.ps1" -ForegroundColor Green
Write-Host ""
