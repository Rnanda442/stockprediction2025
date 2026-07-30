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

The large warehouse keeps the evidence. GitHub stays easy to upload into ChatGPT projects.
