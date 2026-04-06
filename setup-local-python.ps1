<#
.SYNOPSIS
    Set up Python virtual environment for local development and testing.
    Run this from the aks/ directory.

.DESCRIPTION
    Creates a Python venv, installs all backend dependencies,
    and prints instructions for activation.

.EXAMPLE
    .\setup-local-python.ps1
    .\setup-local-python.ps1 -PythonPath "C:\Python311\python.exe"
#>
param(
    [string]$PythonPath = "python"
)

$ErrorActionPreference = "Continue"
$VenvDir = Join-Path $PSScriptRoot "backend\.venv"

function Write-Step { param([string]$Message); Write-Host "`n=== $Message ===" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message); Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Info { param([string]$Message); Write-Host "  $Message" -ForegroundColor Gray }

# Step 1: Check Python
Write-Step "Checking Python installation"

$pyVer = & $PythonPath --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] Python not found at: $PythonPath" -ForegroundColor Red
    Write-Host "  Install Python 3.10+: winget install Python.Python.3.11" -ForegroundColor Yellow
    exit 1
}
Write-Ok "$pyVer"

# Check version is 3.10+
$verNum = ($pyVer -replace 'Python ', '') -split '\.'
$major = [int]$verNum[0]
$minor = [int]$verNum[1]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
    Write-Host "  [FAIL] Python 3.10+ required, got $pyVer" -ForegroundColor Red
    exit 1
}

# Step 2: Create virtual environment
Write-Step "Creating virtual environment"

if (Test-Path $VenvDir) {
    Write-Ok "Virtual environment already exists at: $VenvDir"
} else {
    Write-Info "Creating venv at: $VenvDir"
    & $PythonPath -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] Failed to create virtual environment" -ForegroundColor Red
        exit 1
    }
    Write-Ok "Virtual environment created"
}

# Step 3: Activate and install dependencies
Write-Step "Installing dependencies"

$activateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
if (-not (Test-Path $activateScript)) {
    Write-Host "  [FAIL] Activate script not found at: $activateScript" -ForegroundColor Red
    exit 1
}

# Activate venv
& $activateScript

# Upgrade pip
Write-Info "Upgrading pip..."
& (Join-Path $VenvDir "Scripts\python.exe") -m pip install --upgrade pip 2>&1 | Out-Null

# Install requirements
$reqFile = Join-Path $PSScriptRoot "backend\requirements.txt"
Write-Info "Installing from: $reqFile"
& (Join-Path $VenvDir "Scripts\pip.exe") install -r $reqFile

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] pip install failed" -ForegroundColor Red
    Write-Host "  Note: PyTorch with CUDA requires specific install command." -ForegroundColor Yellow
    Write-Host "  For CPU-only local testing, this is fine." -ForegroundColor Yellow
} else {
    Write-Ok "All dependencies installed"
}

# Step 4: Verify key packages
Write-Step "Verifying installation"

$venvPython = Join-Path $VenvDir "Scripts\python.exe"

$checks = @(
    "import torch; print(f'PyTorch {torch.__version__} (CUDA: {torch.cuda.is_available()})')",
    "import fastapi; print(f'FastAPI {fastapi.__version__}')",
    "import pandas; print(f'Pandas {pandas.__version__}')",
    "import sklearn; print(f'scikit-learn {sklearn.__version__}')"
)

foreach ($check in $checks) {
    $result = & $venvPython -c $check 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "$result"
    } else {
        Write-Host "  [WARN] $result" -ForegroundColor Yellow
    }
}

# Step 5: Print usage instructions
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Python environment ready!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  To activate the venv:" -ForegroundColor White
Write-Host "    .\backend\.venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "  To run the API server locally:" -ForegroundColor White
Write-Host "    cd backend" -ForegroundColor Cyan
Write-Host "    python -m uvicorn app.main:app --reload --port 8000" -ForegroundColor Cyan
Write-Host ""
Write-Host "  To run training locally (CPU, slow but works):" -ForegroundColor White
Write-Host "    cd backend" -ForegroundColor Cyan
Write-Host "    python -m scripts.train --city new-york --lat 40.71 --lon -74.01" -ForegroundColor Cyan
Write-Host ""
Write-Host "  To test health endpoint:" -ForegroundColor White
Write-Host "    curl http://localhost:8000/api/health" -ForegroundColor Cyan
Write-Host ""
Write-Host "  To deactivate the venv:" -ForegroundColor White
Write-Host "    deactivate" -ForegroundColor Cyan
Write-Host ""
