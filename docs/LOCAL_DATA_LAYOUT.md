# Local Data Layout

Last updated: 2026-07-22

This repository keeps source code in git and keeps generated market data out of
git. The working data files are large, local, and reproducible from the pipeline.

## Canonical Local Files

- `historicals.db`: ignored root database with historical price rows.
- `vectorized.db`: ignored root database with features, watchlist history, model
  outputs, and stock-universe snapshots.
- `filtered_tickers.db`: ignored root database with accepted and rejected ticker
  universes.
- `dashboard_data.db`: ignored compact dashboard export built from the root
  databases by `python scripts/export_dashboard_data.py`.

The root databases are the canonical local source for the dashboard export. Do
not keep duplicate pipeline databases under `data/`.

## Local Snapshot Files

The `data/` folder is reserved for personal local snapshots and ledgers, such as:

- `data/robinhood_portfolio_snapshot.csv`
- `data/trading_constraints_snapshot.csv`
- future paper-trading ledgers

These files stay ignored because they may contain account-specific information.

## Generated Output Folders

- `analytics/`: ignored CSV/HTML pipeline outputs.
- `logs/`: ignored notebook and run logs.
- `.dashboard-sync/`: ignored temporary download folder used by
  `scripts/start_dashboard.ps1 -SyncOnly`.
- `notebook/stockprediction2025/data/`: legacy or notebook-local database cache
  location. Use `python scripts/promote_pipeline_databases.py` to promote the
  most complete local database copies back to the root when needed.

## Sync And Freshness

To download the latest unexpired GitHub Actions dashboard artifact without
starting Streamlit:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_dashboard.ps1 -SyncOnly
```

If all artifacts are expired, rebuild from local databases:

```powershell
python scripts\export_dashboard_data.py
```

Future workflow artifacts are retained for 90 days. A fresh cloud run may still
need Robinhood app approval before it can produce current market data.
