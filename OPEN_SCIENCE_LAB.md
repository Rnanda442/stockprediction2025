# Open Science Lab Warehouse

Use Open Science Lab as the large-data memory for the project, while GitHub stays small and code-focused.

## What GitHub Stores

- Source code and workflow files.
- Streamlit dashboard code.
- Small project docs and setup scripts.
- Compact dashboard outputs only when explicitly needed.

GitHub should not store the large warehouse data.

## What Open Science Lab Stores

The local `warehouse/` folder is created by:

```bash
python scripts/setup_open_science_lab.py
```

The same script also manages the warehouse:

```bash
python scripts/setup_open_science_lab.py status
python scripts/setup_open_science_lab.py export-run --source /path/to/stock-analysis-outputs
python scripts/setup_open_science_lab.py summarize
```

After GitHub CLI is installed and authenticated in Open Science Lab, sync all
new successful run artifacts with one command:

```bash
python scripts/run_open_science_lab_workflow.py --limit 10 --to your_email@gmail.com
```

Runs whose artifacts are expired or missing are reported and skipped; successful
downloads still get summarized, converted into Gmail-ready reports, and uploaded
to a tiny Google Drive analysis pack when `rclone` is configured. The default
workflow also publishes the compact website snapshot through the authenticated
GitHub CLI; raw archives are never part of that publication.

The workflow also writes a Drive-ready digest from the compact CSVs:

```bash
python scripts/build_osl_analysis_digest.py
```

That creates `warehouse/drive_pack/` with `analysis_digest.md`,
`analysis_digest.json`, `site_snapshot.json`, `csv/model_action_plan.csv`,
`csv/recommended_charts.csv`, and the small CSV inputs needed for leakage,
calibration, model-gate, probability-shape, paper-outcome, feature-stability,
and artifact-health charts.

Render every chart supported by the available evidence:

```bash
python scripts/render_osl_analysis_charts.py
```

Charts are written under `warehouse/drive_pack/charts/`. A chart is skipped
with an explicit reason in `chart_status.json` when the evidence is too sparse.
The whole rendered chart set is designed to stay small; raw run archives remain
in OSL.

Publish the exact same compact snapshot for the Sites dashboard:

```bash
python scripts/publish_osl_site_snapshot.py
```

This updates only `public/data/latest-analysis.json` in GitHub. The live Sites
dashboard fetches that file when it opens, so routine OSL runs do not require a
new site build. Use `--skip-site-publish` on the full workflow for a local-only
analysis pass.

Build a Gmail-ready summary report from the warehouse:

```bash
python scripts/email_warehouse_summary.py --to your_email@gmail.com
```

That writes Markdown, HTML, and `.eml` files under
`warehouse/summaries/email/`. To send directly through Gmail SMTP, set secrets
only in Open Science Lab:

```bash
export GMAIL_SMTP_USER="your_email@gmail.com"
export GMAIL_APP_PASSWORD="your_gmail_app_password"
python scripts/email_warehouse_summary.py --to your_email@gmail.com --send
```

Upload compact summaries and manifests to Google Drive from Open Science Lab:

```bash
mamba install -c conda-forge rclone -y
rclone config
python scripts/upload_warehouse_to_drive.py
```

The uploader expects a Google Drive rclone remote named `gdrive` and writes to
`gdrive:stockprediction2025/warehouse` by default. Its default `digest` profile
uploads only `warehouse/drive_pack/` plus the local warehouse README. Add
`--profile compact` to upload all compact summary folders, manifests, and
paper-outcome analysis. Add `--include-run-archives` only when you want larger
archived run folders in Drive too.

The script creates this local-only structure:

```text
warehouse/
  prices/
    raw/
    clean/
  features/
    vectorized/
  model_runs/
    evaluations/
    predictions/
  monte_carlo/
    latest/
    history/
  paper_outcomes/
  summaries/
    daily/
    weekly/
    analysis/
    email/
  drive_pack/
  manifests/
  logs/
  scratch/
```

It also adds local Git exclude rules so `warehouse/` does not show up as untracked data.

## Intended Flow

1. Pull the repo in Open Science Lab.
2. Run `python scripts/setup_open_science_lab.py`.
3. Download or place a GitHub Actions artifact folder in Open Science Lab.
4. Run `python scripts/setup_open_science_lab.py export-run --source PATH_TO_ARTIFACT`.
5. Run `python scripts/setup_open_science_lab.py summarize`.
6. Keep large raw data, Parquet files, DuckDB files, and run archives in `warehouse/`.
7. Push only code changes or compact summary logic back to GitHub.
8. Let Streamlit read compact dashboard summaries instead of full raw datasets.

Once `gh` is authenticated, replace steps 3-5 with:

```bash
python scripts/run_open_science_lab_workflow.py --limit 10 --to your_email@gmail.com
```

By default this uploads the tiny Drive pack, not the large run archives. Use
`--drive-profile compact` when you want every compact summary folder in Drive.
Use `--send-email` only after Gmail SMTP secrets are configured in Open Science Lab.
Do not commit Gmail tokens, Google Drive tokens, app passwords, downloaded
artifacts, or warehouse contents.

## Commands

Check the warehouse:

```bash
python scripts/setup_open_science_lab.py status
```

Export one run from a GitHub Actions artifact folder:

```bash
python scripts/setup_open_science_lab.py export-run --source ~/stock-analysis-outputs
```

Build summary CSVs from all saved run archives:

```bash
python scripts/setup_open_science_lab.py summarize
```

Use a different large storage location:

```bash
python scripts/setup_open_science_lab.py setup --warehouse /path/to/big/storage
python scripts/setup_open_science_lab.py export-run --warehouse /path/to/big/storage --source ~/stock-analysis-outputs
```

## Why This Helps

The dashboard can show clean summaries such as:

- Which model/horizon is improving.
- Which stock types are working.
- Whether Monte Carlo bands are too optimistic or too conservative.
- Which feature groups stay useful across runs.
- Which paper decisions actually matured into profitable outcomes.
- Which latest successful runs are already archived and which were skipped.
- Which model quality, leakage, probability, and paper-calibration checks need attention next.
- Which model or analysis change should be reviewed next and the acceptance test it must pass.
- Which charts were rendered and which were honestly skipped for insufficient evidence.

The large warehouse keeps the evidence. GitHub stays easy to upload into ChatGPT projects.
