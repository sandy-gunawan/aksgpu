<#
.SYNOPSIS
    Activate or deactivate the GPU node pool on AKS to control costs.

.EXAMPLE
    .\gpu-activate.ps1 -Action activate
    .\gpu-activate.ps1 -Action deactivate
#>
param(
    [Parameter(Mandatory)]
    [ValidateSet("activate", "deactivate")]
    [string]$Action
)

$ResourceGroup = "rg-gpu-weather"
$ClusterName   = "aks-gpu-weather"
$NodePool      = "gpupool1"

if ($Action -eq "activate") {
    Write-Host "`n=== Activating GPU Node Pool ===" -ForegroundColor Cyan

    Write-Host "Scaling GPU pool to 1 node..."
    az aks nodepool scale `
        --resource-group $ResourceGroup `
        --cluster-name $ClusterName `
        --name $NodePool `
        --node-count 1

    Write-Host "Waiting for GPU node to become Ready (up to 5 min)..."
    $timeout = 300
    $elapsed = 0
    while ($elapsed -lt $timeout) {
        $ready = kubectl get nodes -l accelerator=nvidia-t4 --no-headers 2>$null | Select-String "Ready"
        if ($ready) {
            Write-Host "GPU node is Ready!" -ForegroundColor Green
            break
        }
        Start-Sleep -Seconds 15
        $elapsed += 15
        Write-Host "  Waiting... ($elapsed s)"
    }

    # Verify NVIDIA plugin
    kubectl get pods -n kube-system -l app=nvidia-device-plugin --no-headers

    # Switch backend to GPU mode
    Write-Host "Switching backend to GPU deployment..."
    kubectl apply -f k8s/backend-deployment.yaml
    kubectl rollout status deployment/weather-api -n gpu-weather --timeout=120s

    Write-Host "`nGPU activated. Fast inference available." -ForegroundColor Green
}
elseif ($Action -eq "deactivate") {
    Write-Host "`n=== Deactivating GPU Node Pool ===" -ForegroundColor Cyan

    # Switch backend to CPU mode first
    Write-Host "Switching backend to CPU deployment..."
    kubectl apply -f k8s/backend-deployment-cpu.yaml
    kubectl rollout status deployment/weather-api -n gpu-weather --timeout=120s

    Start-Sleep -Seconds 10

    Write-Host "Scaling GPU pool to 0 nodes..."
    az aks nodepool scale `
        --resource-group $ResourceGroup `
        --cluster-name $ClusterName `
        --name $NodePool `
        --node-count 0

    Write-Host "`nGPU deactivated. Saving ~`$15/day." -ForegroundColor Green
}

