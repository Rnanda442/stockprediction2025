# Workspace Layout

Last updated: 2026-07-27

The repository now has a Streamlit frontend over a shared backend/data contract.

## App Code

| Path | Role |
|---|---|
| `dashboard/` | Streamlit frontend and shared domain/data helpers. |
| `backend/` | Service/API layer over `dashboard_data.db` for shared backend logic. |
| `scripts/` | Pipeline, sync, validation, and local launch commands. |
| `tests/` | Unit and smoke tests for contracts that should not drift. |
| `docs/` | Product, architecture, runbook, and workspace notes. |

## Generated And Local Data

The current pipeline expects several generated artifacts at the repository root.
Do not move these without also updating the pipeline scripts:

| Path | Role |
|---|---|
| `dashboard_data.db` | Compact approved dashboard/API database. |
| `analytics/` | Uploaded analysis CSVs from the workflow artifact. |
| `vector_analysis_results.csv` | Generated feature summary export. |
| `checkpoint_filtered.csv` / `checkpoint_rejected.csv` | Generated universe gate outputs. |
| `historicals.db` / `vectorized.db` / `filtered_tickers.db` | Large ignored local build databases. |
| `.dashboard-sync/` | Local GitHub Actions artifact downloads. |
| `logs/` | Local or artifact notebook execution logs. |

## Launch Commands

Use Streamlit as the frontend:

```powershell
.\scripts\start_dashboard.ps1 -SkipSync
```

Use the backend API when testing service endpoints or future integrations:

```powershell
python -m pip install -r requirements-backend.txt
.\scripts\start_backend.ps1
```

Useful backend endpoints include `/api/readiness`, `/api/daily-decisions`,
`/api/model/tournament`, and `/api/ticker/{ticker}`. Streamlit reads the same
backend service code directly.

Validate an approved dashboard artifact without requiring fresh ignored local
raw databases:

```powershell
python scripts\validate_pipeline_outputs.py --dashboard-artifact-only
```

Check the Streamlit/backend service contract without starting a server:

```powershell
python scripts\check_app_services_smoke.py
```

Run all local app-stack checks:

```powershell
.\scripts\check_app_stack.ps1
```
