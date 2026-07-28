# stockprediction2025

Automated stock research pipeline with a private local Streamlit dashboard.

Permanent Streamlit app:

https://stockprediction2025-j9r5rlvr6meymlsdsuvxqq.streamlit.app/

## Product Roadmap

The product direction and next-step checklist are tracked in:

- `docs/PROJECT_CHARTER.md`
- `docs/SYSTEM_ARCHITECTURE.md`
- `docs/PRODUCT_DECISIONS.md`
- `docs/ROADMAP_CHECKLIST.md`
- `docs/NEXT_STEPS_PLAN.md`
- `docs/LOCAL_DATA_LAYOUT.md`
- `docs/ROBINHOOD_ACTION_RUNBOOK.md`

Current direction: build this into a personal Robinhood-aware trading assistant,
starting with automatic paper trading and serious backtesting before any real
trade execution.


## Sync Code And Run The Cloud Pipeline

From PowerShell in the repository, use the root launcher to push the branch you
are actually on and dispatch the workflow:

```powershell
.\sync_and_run_stock_pipeline.cmd -Watch
```

You can also call the PowerShell script directly:

```powershell
.\scripts\sync_and_run_stock_pipeline.ps1 -Watch
```

The script detects the current branch with `git branch --show-current`, pushes
that branch to `origin`, then runs `stock-run.yml` with GitHub CLI. This avoids
the common `src refspec work does not match any` error that happens when the
local branch is not named `work`. If you intentionally want to run a specific
branch, pass it explicitly:

```powershell
.\sync_and_run_stock_pipeline.cmd -Branch main -Watch
```

Scheduled workflow runs publish artifacts and caches but do not commit generated
snapshot updates back to `main`. If you intentionally want a manual run to commit
refreshed tracked snapshot files, dispatch it with
`commit_generated_updates=true`.

If PowerShell says the script is not recognized, this helper file is not in your
current checkout yet. Use the manual commands below from the repository root;
they do not require the helper script and they avoid hard-coding a branch name:

```powershell
$branch = git branch --show-current
if (-not $branch) { throw "No current branch is checked out." }
git status --short --branch
git fetch origin --prune
git push -u origin HEAD
& "C:\Program Files\GitHub CLI\gh.exe" workflow run stock-run.yml --repo Rnanda442/stockprediction2025 --ref $branch
Start-Sleep -Seconds 5
$runId = & "C:\Program Files\GitHub CLI\gh.exe" run list --repo Rnanda442/stockprediction2025 --workflow stock-run.yml --branch $branch --limit 1 --json databaseId --jq '.[0].databaseId'
& "C:\Program Files\GitHub CLI\gh.exe" run watch $runId --repo Rnanda442/stockprediction2025
```

`gh run watch` needs a run id to avoid the interactive "Select a workflow run"
prompt. The commands above look up the newest `stock-run.yml` run for your
current branch and watch that exact run.


### Check A Completed Action And Dashboard Output

After a workflow run starts, this helper can verify the run status, download the
`stock-analysis-outputs` artifact, summarize `dashboard_data.db`, and compare
the new app data against your existing local `dashboard_data.db` when present:

```powershell
.\scripts\check_stock_action_outputs.ps1 -RunId 26853601559 -Wait
```

If you omit `-RunId`, the helper checks the latest `stock-run.yml` run:

```powershell
.\scripts\check_stock_action_outputs.ps1
```

The summary includes pipeline health, latest market and shortlist dates, exported
row counts, shortlist contents, top watchlist rows, model-tournament evaluation,
and shortlist/watchlist changes compared with the prior local dashboard DB.

## Dashboard

### Streamlit Community Cloud

Deploy the private repository with these settings:

- Repository: `Rnanda442/stockprediction2025`
- Branch: `main`
- Main file path: `dashboard/app.py`
- Secret: `DASHBOARD_PASSWORD = "choose-a-private-password"`

The repository includes a read-only `dashboard_data.db` snapshot for the hosted
app. Routine pipeline runs continue publishing fresher databases as workflow
artifacts without committing a large database revision on every run.

Generate the compact read-only dashboard database:

```powershell
python -m pip install -r requirements-dashboard.txt
python scripts/export_dashboard_data.py
```

Sync the latest successful GitHub Actions output and start the private
dashboard:

```powershell
.\start_dashboard.cmd
```

The launcher prompts for a password, downloads the latest
`stock-analysis-outputs` artifact, refreshes `dashboard_data.db`, and prints the
local and LAN URLs. Keep that terminal window open while using the dashboard.
You can also double-click `start_dashboard.cmd` in File Explorer.

To start from the existing local database without downloading an artifact:

```powershell
.\scripts\start_dashboard.ps1 -SkipSync
```

To sync only the latest unexpired dashboard artifact without starting Streamlit:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_dashboard.ps1 -SyncOnly
```

To validate a synced dashboard artifact without requiring fresh local
`historicals.db` and `vectorized.db` rebuilds:

```powershell
python scripts\validate_pipeline_outputs.py --dashboard-artifact-only
```

If no unexpired artifact is available, rebuild the compact local database from
the local root databases:

```powershell
python scripts\export_dashboard_data.py
```

The dashboard includes:

- mobile-friendly navigation grouped into Today, Activity, Architecture, Research, and More
- an Activity Board backed by `docs/activity_board.json` for goals, completed work, and next commit targets
- a tested automatic paper-decision record contract with a separate append-only local ledger
- scheduled automatic paper-decision generation published in the pipeline artifact

- latest five-stock shortlist
- ranked 50-stock swing-trade watchlist with confidence and holding-window guidance
- plain-English guide to the pipeline and core variables
- interactive historical Research Lab with adjustable slices and assumptions
- Monte Carlo scenario fan and walk-forward signal evaluation
- leakage-controlled 5d, 20d, and 60d model tournament with logistic, ANN, and tree candidates
- Model Lab with visible training cutoff, embargo, test range, feature weights, and latest rankings
- proposed paper-trade review notes saved to a local ignored ledger at `data/paper_trade_ledger.csv`
- Portfolio Replay for comparing recent walk-forward rotation rules against a starting portfolio
- guarded GitHub Actions rerun controls for cloud pipeline parameters
- interactive 3D stock-universe map with visible filter outcomes
- time-aware 3D map snapshots with date scrolling, trails, speed, and acceleration
- visual opportunity map for comparing signal strength, volatility, and liquidity
- rebased price race for the latest shortlist
- forward-return evaluation over 1, 5, 20, and 60 trading sessions
- historical shortlist snapshots
- searchable ticker feature summaries
- recent price charts
- pipeline freshness metadata

### Backend API For The Streamlit Frontend

Streamlit in `dashboard/app.py` is the frontend. The cleaner backend split lives
in `backend/`, where shared service/API code reads `dashboard_data.db` and
supports the Streamlit UI.

```powershell
python -m pip install -r requirements-backend.txt
.\scripts\start_backend.ps1
```

Open `http://127.0.0.1:8000/docs` for the API schema. Use Streamlit for the
frontend:

```powershell
.\scripts\start_dashboard.ps1 -SkipSync
```

Check the Streamlit/backend service contract without starting a server:

```powershell
python scripts\check_app_services_smoke.py
```

Run the complete local app-stack check:

```powershell
.\scripts\check_app_stack.ps1
```

The backend also exposes `/api/readiness`, `/api/daily-decisions`,
`/api/model/tournament`, and `/api/ticker/{ticker}` for integration checks. The
Streamlit Pipeline Health page surfaces the same readiness report.

The product plan for prediction guidance, visuals, outputs, and next-run ANN
model work is in `docs/PREDICTION_VISUAL_OUTPUT_PLAN.md`.

The backend API does not refresh artifacts by itself. Sync the approved artifact
first when needed:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_dashboard.ps1 -SyncOnly
```

The scheduled GitHub Action exports `dashboard_data.db` as part of its
`stock-analysis-outputs` artifact. The dashboard reads this compact database
only; it never modifies pipeline databases.

To save a read-only local Robinhood position snapshot for **Portfolio Replay**:

```powershell
python scripts/snapshot_robinhood_portfolio.py
```

The helper uses the existing cached Robinhood session when available, may ask
for app authorization when Robinhood requires it, and writes only to the ignored
local file `data/robinhood_portfolio_snapshot.csv`. It does not place orders.

## Robinhood Session Reuse

Set the `ROBINHOOD_SESSION_KEY` GitHub Actions secret to a Fernet key. The
workflow uses it to cache an encrypted Robinhood session between runs. Raw
Robinhood tokens stay inside the runner and are never committed or uploaded as
artifacts.

Generate a key:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The first run after setup may still require Robinhood device approval. Later
runs reuse the encrypted session until Robinhood expires or revokes it.

## Research Lab And Cloud Controls

Use **Research Lab** for fast local experimentation. Its history-slice,
Monte Carlo, Sharpe-style, and walk-forward controls recalculate immediately
from the compact dashboard database.

Use **Pipeline Controls** only when you want to start a full GitHub Actions run.
Those controls can change the ranked-watchlist size, persistence bonus,
focused-shortlist size, liquidity floor, volatility ceiling, and behavioral
similarity settings. A confirmed rerun refreshes data and rebuilds the cloud
artifact; it does not place trades.
