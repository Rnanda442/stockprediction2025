# stockprediction2025

Automated stock research pipeline with a private local Streamlit dashboard.

## Dashboard

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

The dashboard includes:

- latest five-stock shortlist
- ranked 50-stock swing-trade watchlist with confidence and holding-window guidance
- plain-English guide to the pipeline and core variables
- interactive historical Research Lab with adjustable slices and assumptions
- Monte Carlo scenario fan and walk-forward signal evaluation
- leakage-controlled 5d, 20d, and 60d logistic model baselines with a held-out time window
- Model Lab with visible training cutoff, embargo, test range, feature weights, and latest rankings
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

The scheduled GitHub Action exports `dashboard_data.db` as part of its
`stock-analysis-outputs` artifact. The dashboard reads this compact database
only; it never modifies pipeline databases.

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
