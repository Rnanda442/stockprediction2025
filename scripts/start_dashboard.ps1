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
        --limit 20 `
        --json databaseId,createdAt,url | ConvertFrom-Json
    if (-not $runs) {
        throw "No successful stock pipeline run was found."
    }

    $runs = @($runs)
    $resolvedRoot = [System.IO.Path]::GetFullPath($root)
    $resolvedSync = [System.IO.Path]::GetFullPath($syncDir)
    if (-not $resolvedSync.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean sync directory outside the repository."
    }

    $synced = $false
    $skippedRuns = New-Object System.Collections.Generic.List[string]
    foreach ($run in $runs) {
        $artifactResponse = gh api "repos/Rnanda442/stockprediction2025/actions/runs/$($run.databaseId)/artifacts" | ConvertFrom-Json
        $artifact = @($artifactResponse.artifacts) |
            Where-Object { $_.name -eq "stock-analysis-outputs" } |
            Select-Object -First 1

        if (-not $artifact) {
            $skippedRuns.Add("$($run.databaseId) had no stock-analysis-outputs artifact") | Out-Null
            continue
        }
        if ($artifact.expired) {
            $expiresAt = if ($artifact.expires_at) { $artifact.expires_at } else { "unknown expiry" }
            $skippedRuns.Add("$($run.databaseId) artifact expired at $expiresAt") | Out-Null
            continue
        }

        if (Test-Path $syncDir) {
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
            $skippedRuns.Add("$($run.databaseId) artifact did not include dashboard_data.db") | Out-Null
            continue
        }

        Copy-Item -LiteralPath $artifactDb -Destination $incomingDb -Force
        Copy-Item -LiteralPath $incomingDb -Destination $dashboardDb -Force
        Remove-Item -LiteralPath $incomingDb -Force
        Write-Host "Synced dashboard_data.db from $($run.url)"
        $synced = $true
        break
    }

    if (-not $synced) {
        $details = if ($skippedRuns.Count) { "`n  - " + ($skippedRuns -join "`n  - ") } else { "" }
        throw (
            "No unexpired stock-analysis-outputs artifact with dashboard_data.db was found " +
            "in the last $($runs.Count) successful stock pipeline runs.$details`n" +
            "Run .\sync_and_run_stock_pipeline.cmd -Watch after approving Robinhood, " +
            "or build from local databases with: python scripts\export_dashboard_data.py"
        )
    }

    $healthScript = @'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    health = dict(connection.execute("SELECT metric, value FROM PipelineHealth"))
coverage = health.get("latest_market_coverage")
coverage_text = f"{float(coverage):.1%}" if coverage else "unavailable"
print(
    f"Synced market date: {health.get('latest_market_date', 'unknown')[:10]}; "
    f"shortlist date: {health.get('latest_shortlist_date', 'unknown')[:10]}; "
    f"latest-date coverage: {coverage_text}"
)
'@
    $health = $healthScript | python - $dashboardDb
    Write-Host $health
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
