# stockprediction2025

Lean source folder for the stock prediction project.

This repo is intentionally trimmed so the project folder can be uploaded into a
new ChatGPT Project without carrying old planning versions, generated data, or
large local artifacts.

## Current System

- Frontend: Streamlit in `dashboard/app.py`
- Backend helpers: `backend/services.py`
- Pipeline: `.github/workflows/stock-run.yml`
- Core model builder: `scripts/build_model_baseline.py`
- Main notebook: `notebook/2025summerstock-Copy6.ipynb`
- Weekly ML plan: `WEEKLY_ML_RUN_PLAN.md`
- Live trading: disabled; this is research and paper-decision review only

Latest successful full run used by the last committed dashboard snapshot before
the repo was trimmed:

- Run: https://github.com/Rnanda442/stockprediction2025/actions/runs/30394124656
- Exported: `2026-07-28T21:01:39Z`
- Market date: `2026-07-27`
- Coverage: `98.9%`
- Model tournament rows: `9`
- Candidate prediction rows: `22,833`
- Champions: `5d sgd_logistic`, `20d hist_gradient_boosting`,
  `60d hist_gradient_boosting`

## Source Layout

```text
.github/workflows/stock-run.yml       Cloud pipeline
backend/                              Read-only service helpers
dashboard/                            Streamlit frontend and domain helpers
notebook/2025summerstock-Copy6.ipynb  Main data-refresh notebook
scripts/                              Pipeline, model, export, and validation scripts
WEEKLY_ML_RUN_PLAN.md                 Daily model run and review plan
```

Generated files are deliberately not source:

- `dashboard_data.db`
- `dashboard/paper_learning_snapshot.json`
- `analytics/`
- `logs/`
- root CSV exports
- large local `*.db` build databases
- Robinhood tokens or session caches

## Run The Cloud Pipeline

Use the `Run Stock Pipeline` GitHub Actions workflow dispatch from the repo.

The workflow restores cached databases/session material, runs the notebook,
builds the model tournament, exports `dashboard_data.db`, records paper
decisions/outcomes, validates outputs, and uploads `stock-analysis-outputs`.

Default model candidates:

```text
sgd_logistic,mlp_ann,hist_gradient_boosting
```

## Run Locally

The lean repo does not commit `dashboard_data.db`. Sync or build it first.

To build from local databases:

```powershell
python -m pip install -r requirements.txt
python scripts\export_dashboard_data.py
```

To start Streamlit after `dashboard_data.db` exists:

```powershell
$env:DASHBOARD_PASSWORD = "choose-a-local-password"
python -m streamlit run dashboard/app.py --server.headless=true --server.address=127.0.0.1 --server.port=8501
```

## Validation

Use these checks when the required generated databases/artifacts are present:

```powershell
python scripts\check_pipeline_safety.py
python scripts\check_model_export_smoke.py
python scripts\validate_pipeline_outputs.py --dashboard-artifact-only
```
