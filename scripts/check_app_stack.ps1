$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Checking approved dashboard artifact..."
python scripts\validate_pipeline_outputs.py --dashboard-artifact-only

Write-Host ""
Write-Host "Checking Streamlit/backend service contract..."
python scripts\check_app_services_smoke.py

Write-Host ""
Write-Host "Running tests..."
python -m pytest -q

Write-Host ""
Write-Host "App stack checks passed."
