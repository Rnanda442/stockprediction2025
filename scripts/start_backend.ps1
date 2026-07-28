param(
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:PYTHONPATH = if ($env:PYTHONPATH) {
    "$root$([System.IO.Path]::PathSeparator)$env:PYTHONPATH"
} else {
    $root
}

Write-Host "Backend API starting."
Write-Host "API root: http://${HostAddress}:${Port}"
Write-Host "API docs: http://${HostAddress}:${Port}/docs"

python -m uvicorn backend.main:app --host $HostAddress --port $Port
