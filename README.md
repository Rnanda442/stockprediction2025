# stockprediction2025

Lean source folder for the stock prediction project.

This repo is intentionally trimmed so the project folder can be uploaded into a
new ChatGPT Project without carrying old planning versions, generated data, or
large local artifacts.

## Current System

- Frontend: Streamlit in `dashboard/app.py`
- Sites view: lightweight deployable dashboard in `app/`
- Backend helpers: `backend/services.py`
- Pipeline: `.github/workflows/stock-run.yml`
- Core model builder: `scripts/build_model_baseline.py`
- Main notebook: `notebook/2025summerstock-Copy6.ipynb`
- Weekly ML plan: `WEEKLY_ML_RUN_PLAN.md`
- Live trading: disabled; this is research and paper-decision review only

Latest successful full run used by the current dashboard snapshots:

- Run: https://github.com/Rnanda442/stockprediction2025/actions/runs/30587429387
- Exported: `2026-07-31T01:42:09Z`
- Market date: `2026-07-29`
- Coverage: `98.9%`
- Model prediction rows: `7,644`
- Monte Carlo rows: `180`
- Validation: `passed`
- Champions: `5d sgd_logistic`, `20d sgd_logistic`,
  `60d hist_gradient_boosting`

## Source Layout

```text
.github/workflows/stock-run.yml       Cloud pipeline
app/                                  Sites dashboard page
backend/                              Read-only service helpers
dashboard/                            Streamlit frontend and domain helpers
build/                                Sites packaging helper
notebook/2025summerstock-Copy6.ipynb  Main data-refresh notebook
scripts/                              Pipeline, model, export, and validation scripts
worker/                               Sites Cloudflare Worker entry
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

## ChatGPT Project Source Pack

ChatGPT Projects use uploaded files as reference material. Instead of uploading
this folder file by file, generate one source pack and upload that Markdown file
to the ChatGPT Project:

```powershell
python scripts\build_chatgpt_source_pack.py
```

By default this writes:

```text
C:\Users\gargi\Downloads\stockprediction2025_chatgpt_source_pack.md
```

Regenerate and re-upload that one file after commits that change code, model
logic, pipeline behavior, or project instructions. You do not need to refresh it
only because a GitHub Actions run produced new databases or dashboard artifacts.

If GitHub connection is available in your ChatGPT account, connecting the repo is
cleaner than manual uploads because ChatGPT can read from the repository you
authorize.

## Run The Cloud Pipeline

Use the `Run Stock Pipeline` GitHub Actions workflow dispatch from the repo.

The workflow restores cached databases/session material, runs the notebook,
builds the model tournament, exports `dashboard_data.db`, records paper
decisions/outcomes, validates outputs, and uploads `stock-analysis-outputs`.

Default model candidates:

```text
sgd_logistic,mlp_ann,hist_gradient_boosting
```

## Open Science Lab Analysis Loop

Open Science Lab should hold downloaded GitHub Actions artifacts and generated
analysis summaries. The laptop should only pull/push source code.

After `gh` is installed and authenticated in Open Science Lab:

```bash
cd /home/jovyan/stockprediction2025
git pull origin main
python scripts/run_open_science_lab_workflow.py --limit 10 --to your_email@gmail.com
```

The workflow command reports and skips older runs whose artifacts are expired or
missing, rebuilds compact warehouse summaries, writes Gmail-ready report files,
builds a tiny analysis digest, and uploads the digest pack to Google Drive.

Compact analysis outputs include model quality gates, leakage checks,
probability-bucket shape, paper-decision calibration proxies, artifact health,
and prioritized next actions.

The Drive pack is intentionally small:

```text
warehouse/drive_pack/
  analysis_digest.md
  analysis_digest.json
  csv/recommended_charts.csv
  csv/*.csv
```

It contains the automated read on what is working, what is failing, and which
charts to make next. The larger run archives stay in Open Science Lab.

The Drive upload command uses `rclone` and uploads compact warehouse outputs to
`gdrive:stockprediction2025/warehouse` by default. One-time setup in OSL:

```bash
mamba install -c conda-forge rclone -y
rclone config
```

Create a Google Drive remote named `gdrive`. To also push larger run archives:

```bash
python scripts/run_open_science_lab_workflow.py --skip-sync --skip-email --include-run-archives
```

To upload all compact summary folders instead of only the digest pack:

```bash
python scripts/run_open_science_lab_workflow.py --skip-sync --skip-email --drive-profile compact
```

To send the summary through Gmail from Open Science Lab, configure secrets in
the OSL shell only:

```bash
export GMAIL_SMTP_USER="your_email@gmail.com"
export GMAIL_APP_PASSWORD="your_gmail_app_password"
python scripts/email_warehouse_summary.py --to your_email@gmail.com --send
```

Without `--send`, the script writes Gmail-ready Markdown, HTML, and `.eml`
files into `warehouse/summaries/email/`.
With the full workflow wrapper, use `--send-email` after those same secrets are
set.

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

To preview the deployable Sites dashboard:

```powershell
pnpm install --ignore-scripts
pnpm run dev -- --host 127.0.0.1 --port 8601
```

To build the Sites dashboard for deployment:

```powershell
pnpm run build
```

## Validation

Use these checks when the required generated databases/artifacts are present:

```powershell
python scripts\check_pipeline_safety.py
python scripts\check_model_export_smoke.py
python scripts\validate_pipeline_outputs.py --dashboard-artifact-only
```
