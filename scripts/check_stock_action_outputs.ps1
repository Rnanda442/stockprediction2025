[CmdletBinding()]
param(
    [string]$RunId,
    [string]$Repo = "Rnanda442/stockprediction2025",
    [string]$Workflow = "stock-run.yml",
    [string]$ArtifactName = "stock-analysis-outputs",
    [string]$GitHubCli = "C:\Program Files\GitHub CLI\gh.exe",
    [string]$PreviousDb = "dashboard_data.db",
    [switch]$Wait
)

$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptRoot "..")
Set-Location $RepoRoot

if (-not (Test-Path $GitHubCli)) {
    $PathGh = Get-Command gh -ErrorAction SilentlyContinue
    if ($PathGh) {
        $GitHubCli = $PathGh.Source
    } else {
        throw "GitHub CLI was not found at '$GitHubCli' or on PATH. Install gh or pass -GitHubCli."
    }
}

if (-not $RunId) {
    $RunId = (& $GitHubCli run list --repo $Repo --workflow $Workflow --limit 1 --json databaseId --jq '.[0].databaseId').Trim()
    if ($LASTEXITCODE -ne 0 -or -not $RunId) {
        throw "Could not find a recent run for workflow '$Workflow' in $Repo."
    }
}

if ($Wait) {
    & $GitHubCli run watch $RunId --repo $Repo
    if ($LASTEXITCODE -ne 0) {
        throw "gh run watch failed for run $RunId."
    }
}

$RunJson = & $GitHubCli run view $RunId --repo $Repo --json databaseId,headBranch,headSha,status,conclusion,url,createdAt,updatedAt
if ($LASTEXITCODE -ne 0) {
    throw "Could not read GitHub Actions run $RunId."
}
$Run = $RunJson | ConvertFrom-Json

Write-Host "Run: $($Run.url)"
Write-Host "Branch/SHA: $($Run.headBranch) $($Run.headSha)"
Write-Host "Status: $($Run.status); conclusion: $($Run.conclusion)"
Write-Host "Created: $($Run.createdAt); updated: $($Run.updatedAt)"

if ($Run.status -ne "completed") {
    Write-Host "Run is not completed yet. Re-run with -Wait or run: & `"$GitHubCli`" run watch $RunId --repo $Repo" -ForegroundColor Yellow
    exit 2
}
if ($Run.conclusion -ne "success") {
    Write-Host "Run did not succeed. Inspect logs with: & `"$GitHubCli`" run view $RunId --repo $Repo --log-failed" -ForegroundColor Red
    exit 1
}

$DownloadRoot = Join-Path $RepoRoot ".dashboard-sync\action-check\$RunId"
if (Test-Path $DownloadRoot) {
    Remove-Item -LiteralPath $DownloadRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $DownloadRoot -Force | Out-Null

& $GitHubCli run download $RunId --repo $Repo --name $ArtifactName --dir $DownloadRoot
if ($LASTEXITCODE -ne 0) {
    throw "Could not download artifact '$ArtifactName' from run $RunId."
}

$DashboardDb = Join-Path $DownloadRoot "dashboard_data.db"
if (-not (Test-Path $DashboardDb)) {
    throw "Artifact '$ArtifactName' from run $RunId did not include dashboard_data.db."
}

$PythonArgs = @("scripts/summarize_dashboard_artifact.py", $DashboardDb)
if ($PreviousDb -and (Test-Path $PreviousDb)) {
    $PythonArgs += @("--previous-db", (Resolve-Path $PreviousDb))
} elseif ($PreviousDb) {
    Write-Host "Previous dashboard DB '$PreviousDb' was not found; printing current artifact only." -ForegroundColor Yellow
}
python @PythonArgs
if ($LASTEXITCODE -ne 0) {
    throw "Dashboard artifact summary failed."
}
