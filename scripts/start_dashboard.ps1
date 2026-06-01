param(
    [string]$DashboardPassword = $env:DASHBOARD_PASSWORD,
    [switch]$SkipSync,
    [switch]$SyncOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$syncDir = Join-Path $root ".dashboard-sync"
$downloadDir = Join-Path $syncDir "artifact"
$incomingDb = Join-Path $root "dashboard_data.incoming.db"
$dashboardDb = Join-Path $root "dashboard_data.db"

Set-Location $root

if (-not $DashboardPassword -and -not $SyncOnly) {
    $securePassword = Read-Host "Dashboard password" -AsSecureString
    $DashboardPassword = [System.Net.NetworkCredential]::new("", $securePassword).Password
}
if (-not $DashboardPassword -and -not $SyncOnly) {
    throw "Dashboard password cannot be empty."
}

if (-not $SkipSync) {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI (gh) is required. Install it and run: gh auth login"
    }

    $runs = gh run list `
        --repo Rnanda442/stockprediction2025 `
        --workflow stock-run.yml `
        --status success `
        --limit 1 `
        --json databaseId,createdAt,url | ConvertFrom-Json
    if (-not $runs -or -not $runs[0].databaseId) {
        throw "No successful stock pipeline run was found."
    }

    $run = $runs[0]
    if (Test-Path $syncDir) {
        $resolvedRoot = [System.IO.Path]::GetFullPath($root)
        $resolvedSync = [System.IO.Path]::GetFullPath($syncDir)
        if (-not $resolvedSync.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean sync directory outside the repository."
        }
        Remove-Item -LiteralPath $syncDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null

    Write-Host "Downloading dashboard data from successful run $($run.databaseId)..."
    gh run download $run.databaseId `
        --repo Rnanda442/stockprediction2025 `
        --name stock-analysis-outputs `
        --dir $downloadDir

    $artifactDb = Join-Path $downloadDir "dashboard_data.db"
    if (-not (Test-Path $artifactDb)) {
        throw "The workflow artifact did not include dashboard_data.db."
    }

    Copy-Item -LiteralPath $artifactDb -Destination $incomingDb -Force
    Move-Item -LiteralPath $incomingDb -Destination $dashboardDb -Force
    Write-Host "Synced dashboard_data.db from $($run.url)"
}

if ($SyncOnly) {
    Write-Host "Dashboard data sync complete."
    exit 0
}

$lanAddress = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -notlike "127.*" -and
        $_.IPAddress -notlike "169.254.*" -and
        $_.AddressState -eq "Preferred"
    } |
    Select-Object -First 1 -ExpandProperty IPAddress

$env:DASHBOARD_PASSWORD = $DashboardPassword
Write-Host ""
Write-Host "Dashboard starting. Keep this terminal window open."
Write-Host "Local browser: http://127.0.0.1:8501"
if ($lanAddress) {
    Write-Host "LAN / Codex preview: http://${lanAddress}:8501"
}
Write-Host ""

python -m streamlit run dashboard/app.py --server.headless=true --server.address=0.0.0.0 --server.port=8501
