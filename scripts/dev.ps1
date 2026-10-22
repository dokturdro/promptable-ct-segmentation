<#
.SYNOPSIS
  Common dev tasks (Windows PowerShell 5.1+). Run from anywhere:
    .\scripts\dev.ps1 setup     # install backend (uv) + frontend (npm) dependencies
    .\scripts\dev.ps1 weights   # download MedSAM2 (149 MB) + TotalSegmentator (1.24 GB) weights
    .\scripts\dev.ps1 up        # start Orthanc (Docker)
    .\scripts\dev.ps1 seed      # download PANCREAS_0001 from IDC (128 MB) and upload to Orthanc
    .\scripts\dev.ps1 backend   # FastAPI on http://127.0.0.1:8000
    .\scripts\dev.ps1 frontend  # Vite on http://localhost:5173
    .\scripts\dev.ps1 check     # ruff + pytest + eslint + tsc + vitest
    .\scripts\dev.ps1 eval      # simulated-click evaluation of MedSAM2
    .\scripts\dev.ps1 down      # stop Orthanc (data volume is kept)
#>
param([Parameter(Mandatory = $true)][ValidateSet('setup', 'weights', 'up', 'seed', 'backend', 'frontend', 'check', 'eval', 'down')][string]$Task)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'

function Invoke-Step([string]$Label, [scriptblock]$Block) {
  Write-Host "==> $Label" -ForegroundColor Cyan
  # PS 5.1 turns native stderr (e.g. uv warnings) into errors under 'Stop'; judge by exit code.
  $ErrorActionPreference = 'Continue'
  & $Block 2>&1 | ForEach-Object { "$_" }
  if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE)" }
}

switch ($Task) {
  'setup' {
    Invoke-Step 'uv sync (backend)' { uv sync --project $Backend }
    Invoke-Step 'npm install (frontend)' { npm install --prefix $Frontend }
  }
  'weights' { Invoke-Step 'download weights' { uv run --project $Backend python (Join-Path $PSScriptRoot 'download_weights.py') } }
  'up' {
    Invoke-Step 'docker compose up' { docker compose -f (Join-Path $Root 'docker-compose.yml') up -d }
    Write-Host 'Orthanc: http://127.0.0.1:8042  (OHIF: http://127.0.0.1:8042/ohif/)'
  }
  'seed' { Invoke-Step 'seed data' { uv run --project $Backend python (Join-Path $PSScriptRoot 'seed_data.py') } }
  'backend' {
    Push-Location $Backend
    try { uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 } finally { Pop-Location }
  }
  'frontend' { Invoke-Step 'vite' { npm run dev --prefix $Frontend } }
  'check' {
    Push-Location $Backend
    try {
      Invoke-Step 'ruff check' { uv run ruff check . ../scripts }
      Invoke-Step 'ruff format --check' { uv run ruff format --check . ../scripts }
      Invoke-Step 'pytest' { uv run pytest -q }
    } finally { Pop-Location }
    Invoke-Step 'eslint' { npm run lint --prefix $Frontend }
    Invoke-Step 'tsc' { npm run typecheck --prefix $Frontend }
    Invoke-Step 'vitest' { npm test --prefix $Frontend }
  }
  'eval' { Invoke-Step 'eval' { uv run --project $Backend python (Join-Path $PSScriptRoot 'eval_prompt.py') } }
  'down' { Invoke-Step 'docker compose down' { docker compose -f (Join-Path $Root 'docker-compose.yml') down } }
}
