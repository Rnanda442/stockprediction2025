[CmdletBinding()]
param(
    [int]$MaxSources = 35,
    [string]$OutputName = "chatgpt_project_upload"
)

$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = [System.IO.Path]::GetFullPath((Join-Path $ScriptRoot ".."))
$OutputDir = [System.IO.Path]::GetFullPath((Join-Path $Root $OutputName))
$ZipPath = [System.IO.Path]::GetFullPath((Join-Path $Root "$OutputName.zip"))

function Assert-UnderRoot {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to write outside repository: $fullPath"
    }
}

Assert-UnderRoot $OutputDir
Assert-UnderRoot $ZipPath

$SelectedSources = @(
    "README.md",
    "requirements.txt",
    "requirements-dashboard.txt",
    "requirements-backend.txt",
    ".github/workflows/stock-run.yml",
    "dashboard/app.py",
    "dashboard/actions.py",
    "dashboard/auth.py",
    "dashboard/data.py",
    "dashboard/automatic_paper_decisions.py",
    "dashboard/decision_policy.py",
    "dashboard/paper_outcomes.py",
    "dashboard/paper_trades.py",
    "dashboard/portfolio_replay.py",
    "dashboard/research.py",
    "dashboard/trading_constraints.py",
    "backend/main.py",
    "backend/services.py",
    "scripts/build_model_baseline.py",
    "scripts/export_dashboard_data.py",
    "scripts/generate_daily_paper_decisions.py",
    "scripts/update_automatic_paper_outcomes.py",
    "scripts/validate_pipeline_outputs.py",
    "scripts/check_model_export_smoke.py",
    "scripts/check_app_stack.ps1",
    "scripts/sync_and_run_stock_pipeline.ps1",
    "docs/WORKSPACE_LAYOUT.md",
    "docs/SYSTEM_ARCHITECTURE.md",
    "docs/PREDICTION_VISUAL_OUTPUT_PLAN.md",
    "docs/ROBINHOOD_ACTION_RUNBOOK.md",
    "tests/test_backend_api.py",
    "tests/test_model_tournament.py",
    "tests/test_decision_policy.py"
)

$GeneratedSources = @(
    "PROJECT_BRIEF.md",
    "MANIFEST.md"
)

$TotalSources = $SelectedSources.Count + $GeneratedSources.Count
if ($TotalSources -gt $MaxSources) {
    throw "Bundle source list has $TotalSources files, above MaxSources=$MaxSources."
}

if (Test-Path -LiteralPath $OutputDir) {
    Remove-Item -LiteralPath $OutputDir -Recurse -Force
}
if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

foreach ($source in $SelectedSources) {
    $sourcePath = Join-Path $Root $source
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Selected source is missing: $source"
    }
    $destination = Join-Path $OutputDir $source
    $destinationParent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $destination -Force
}

$dbPath = Join-Path $Root "dashboard_data.db"
$briefPath = Join-Path $OutputDir "PROJECT_BRIEF.md"
$briefScript = @'
import sqlite3
import sys
from pathlib import Path

db_path = Path(sys.argv[1])
brief_path = Path(sys.argv[2])

def pct(value):
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "unknown"

def rows(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()

health = {}
shortlist = []
watchlist = []
champions = []
if db_path.exists():
    with sqlite3.connect(db_path) as conn:
        health = dict(conn.execute("SELECT metric, value FROM PipelineHealth"))
        shortlist = rows(
            conn,
            """
            SELECT rank, ticker, ret_60d, AvgDollarVol
            FROM LatestShortlist
            ORDER BY rank
            LIMIT 5
            """,
        )
        watchlist = rows(
            conn,
            """
            SELECT rank, ticker, confidence, suggested_horizon
            FROM LatestWatchlist
            ORDER BY rank
            LIMIT 10
            """,
        )
        champions = rows(
            conn,
            """
            SELECT horizon_days, model_name, accuracy, roc_auc,
                   selected_return_edge, selected_win_lift
            FROM ModelEvaluation
            ORDER BY horizon_days
            """,
        )

lines = [
    "# Stock Prediction Project Brief",
    "",
    "Purpose: compact context bundle for a fresh ChatGPT Project.",
    "",
    "## Current State",
    "",
    f"- Latest successful run: {health.get('github_run_url', 'not recorded')}",
    f"- Dashboard exported at: {health.get('exported_at', 'not recorded')}",
    f"- Latest market date: {str(health.get('latest_market_date', ''))[:10]}",
    f"- Latest shortlist date: {str(health.get('latest_shortlist_date', ''))[:10]}",
    f"- Market coverage: {pct(health.get('latest_market_coverage'))}",
    f"- Model tournament rows: {health.get('model_tournament_evaluation_rows', '0')}",
    f"- Candidate prediction rows: {health.get('latest_model_candidate_predictions_rows', '0')}",
    f"- Paper decisions: {health.get('automatic_paper_decisions_rows', '0')}",
    f"- Paper outcome events: {health.get('automatic_paper_outcome_events_rows', '0')}",
    "",
    "## Model Champions",
    "",
]
if champions:
    for horizon, model, accuracy, auc, return_edge, win_lift in champions:
        lines.append(
            f"- {horizon}d: {model}; accuracy={float(accuracy):.3f}; "
            f"auc={float(auc):.3f}; return_edge={pct(return_edge)}; "
            f"win_lift={pct(win_lift)}"
        )
else:
    lines.append("- No model champions found in dashboard_data.db.")

lines.extend(["", "## Latest Shortlist", ""])
if shortlist:
    for rank, ticker, ret_60d, dollar_vol in shortlist:
        lines.append(
            f"- {rank}. {ticker}: 60d_return={pct(ret_60d)}; "
            f"avg_dollar_volume=${float(dollar_vol):,.0f}"
        )
else:
    lines.append("- No shortlist found in dashboard_data.db.")

lines.extend(["", "## Top Watchlist", ""])
if watchlist:
    for rank, ticker, confidence, horizon in watchlist:
        lines.append(
            f"- {rank}. {ticker}: confidence={float(confidence):.1f}; horizon={horizon}"
        )
else:
    lines.append("- No watchlist found in dashboard_data.db.")

lines.extend(
    [
        "",
        "## Architecture Notes",
        "",
        "- Streamlit in `dashboard/app.py` is the frontend.",
        "- `backend/` is a read-only service/API layer over `dashboard_data.db`.",
        "- The workflow uploads fresh data artifacts; routine runs do not commit generated updates unless explicitly requested.",
        "- Live trading remains blocked. The app is for research and paper-decision review.",
        "",
    ]
)

brief_path.write_text("\n".join(lines), encoding="utf-8")
'@
$briefScript | python - $dbPath $briefPath
if ($LASTEXITCODE -ne 0) {
    throw "Failed to generate PROJECT_BRIEF.md"
}

$manifestPath = Join-Path $OutputDir "MANIFEST.md"
$manifest = @(
    "# ChatGPT Project Source Manifest",
    "",
    "File count: $TotalSources / $MaxSources",
    "",
    "Generated from: stockprediction2025",
    "",
    "## Included Files",
    ""
)
foreach ($source in $GeneratedSources + $SelectedSources) {
    $manifest += "- ``$source``"
}
$manifest += ""
$manifest | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$actualCount = (Get-ChildItem -LiteralPath $OutputDir -Recurse -File | Measure-Object).Count
if ($actualCount -gt $MaxSources) {
    throw "Generated bundle has $actualCount files, above MaxSources=$MaxSources."
}
if ($actualCount -ne $TotalSources) {
    throw "Expected $TotalSources files, generated $actualCount files."
}

Compress-Archive -Path (Join-Path $OutputDir "*") -DestinationPath $ZipPath -Force

Write-Host "Created $OutputDir"
Write-Host "Created $ZipPath"
Write-Host "Sources: $actualCount / $MaxSources"
