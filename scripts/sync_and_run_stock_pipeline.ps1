[CmdletBinding()]
param(
    [string]$Branch,
    [string]$Remote = "origin",
    [string]$Repo = "Rnanda442/stockprediction2025",
    [string]$Workflow = "stock-run.yml",
    [string]$GitHubCli = "C:\Program Files\GitHub CLI\gh.exe",
    [switch]$Watch
)

$ErrorActionPreference = "Stop"

function Invoke-LoggedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    Write-Host "> $FilePath $($Arguments -join ' ')" -ForegroundColor Cyan
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}


function Get-LatestWorkflowRunId {
    param(
        [Parameter(Mandatory = $true)]
        [string]$GitHubCliPath,
        [Parameter(Mandatory = $true)]
        [string]$Repository,
        [Parameter(Mandatory = $true)]
        [string]$WorkflowName,
        [Parameter(Mandatory = $true)]
        [string]$BranchName
    )

    Start-Sleep -Seconds 5
    $RunId = (& $GitHubCliPath run list --repo $Repository --workflow $WorkflowName --branch $BranchName --limit 1 --json databaseId --jq '.[0].databaseId').Trim()
    if ($LASTEXITCODE -ne 0 -or -not $RunId) {
        throw "Workflow was dispatched, but the latest run id could not be resolved for '$WorkflowName' on '$BranchName'. Run 'gh run list --workflow $WorkflowName --repo $Repository' to pick the run manually."
    }
    return $RunId
}

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptRoot "..")
Set-Location $RepoRoot

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is not available on PATH. Install Git or open a Git-enabled shell, then retry."
}

if (-not (Test-Path $GitHubCli)) {
    $PathGh = Get-Command gh -ErrorAction SilentlyContinue
    if ($PathGh) {
        $GitHubCli = $PathGh.Source
    } else {
        throw "GitHub CLI was not found at '$GitHubCli' or on PATH. Install gh or pass -GitHubCli."
    }
}

if (-not $Branch) {
    $Branch = (& git branch --show-current).Trim()
}
if (-not $Branch) {
    throw "Could not determine the current branch. Check out a branch first, then retry."
}

$InsideWorkTree = (& git rev-parse --is-inside-work-tree).Trim()
if ($InsideWorkTree -ne "true") {
    throw "This script must be run from inside the stockprediction2025 Git repository."
}

$OriginUrl = "https://github.com/$Repo.git"
$RemoteUrl = (& git remote get-url $Remote 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $RemoteUrl) {
    Invoke-LoggedCommand git remote add $Remote $OriginUrl
} elseif ($RemoteUrl.Trim() -ne $OriginUrl) {
    Write-Host "Remote '$Remote' is currently '$($RemoteUrl.Trim())'. Leaving it unchanged." -ForegroundColor Yellow
}

Invoke-LoggedCommand git status --short --branch
Invoke-LoggedCommand git fetch $Remote --prune

Write-Host "Pushing local branch '$Branch' to '$Remote/$Branch'." -ForegroundColor Green
Invoke-LoggedCommand git push -u $Remote "${Branch}:${Branch}"

Write-Host "Starting workflow '$Workflow' on ref '$Branch' in $Repo." -ForegroundColor Green
Invoke-LoggedCommand $GitHubCli workflow run $Workflow --repo $Repo --ref $Branch

$LatestRunId = Get-LatestWorkflowRunId -GitHubCliPath $GitHubCli -Repository $Repo -WorkflowName $Workflow -BranchName $Branch
Write-Host "Latest dispatched run id: $LatestRunId" -ForegroundColor Green

if ($Watch) {
    Invoke-LoggedCommand $GitHubCli run watch $LatestRunId --repo $Repo
} else {
    Write-Host "Workflow dispatch sent. To watch it, run:" -ForegroundColor Green
    Write-Host "& `"$GitHubCli`" run watch $LatestRunId --repo $Repo"
}
