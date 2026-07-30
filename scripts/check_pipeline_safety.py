"""Fast static regression checks for pipeline bootstrap and commit safety."""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebook" / "2025summerstock-Copy6.ipynb"
WORKFLOW = ROOT / ".github" / "workflows" / "stock-run.yml"
MODEL_SCRIPT = ROOT / "scripts" / "build_model_baseline.py"
NOTEBOOK_RUNNER = ROOT / "scripts" / "run_notebook_with_progress.py"


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def notebook_source():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook.get("cells", [])
        if cell.get("cell_type") == "code"
    )


def main():
    source = notebook_source()
    workflow = WORKFLOW.read_text(encoding="utf-8")
    model_script = MODEL_SCRIPT.read_text(encoding="utf-8")
    notebook_runner = NOTEBOOK_RUNNER.read_text(encoding="utf-8")

    require(
        "if pd.notna(last_hist):\n                        df_new = df_new[df_new[\"begins_at\"] > last_hist]" in source,
        "fresh-database bootstrap does not filter downloaded rows against pandas NaT",
    )
    require(
        "EMPTY_HISTORY_ABORT_AFTER = 10" in source
        and "CONSECUTIVE_EMPTY_HISTORY_COUNT >= EMPTY_HISTORY_ABORT_AFTER" in source,
        "sustained empty Robinhood historical responses stop the notebook early",
    )
    require(
        "stored_history_is_fresh(last_hist, last_vectorized)" in source
        and "MAX_STORED_HISTORY_AGE_HOURS" in source,
        "fresh historical/vectorized rows skip redundant Robinhood downloads",
    )
    require(
        "flipcorr_cache_manifest.json" in source
        and "cached_similarity = _load_similarity_cache" in source,
        "similarity stage can reuse unchanged expensive pairwise outputs",
    )
    require(
        "frame[f\"future_price_{horizon}d\"] = grouped_prices.shift(-horizon)" in model_script,
        "model targets are future-shifted by horizon",
    )
    require(
        "train_dates = dates[:embargo_start_index]" in model_script
        and "test_dates = dates[test_start_index : test_end_index + 1]" in model_script,
        "walk-forward validation uses earlier train dates and later test dates",
    )
    require(
        "make_pipeline(" in model_script
        and "SimpleImputer(strategy=\"median\")" in model_script
        and "StandardScaler()" in model_script,
        "scalers and imputers are fitted inside train-only sklearn pipelines",
    )
    require(
        "notebook_stage_manifest.json" in notebook_runner
        and "notebook_checkpoints" in notebook_runner,
        "notebook runner writes stage timing manifests and checkpoint files",
    )
    require(
        workflow.count("uses: actions/cache/restore@v4") == 2,
        "workflow restores each database/session cache once",
    )
    require("git add -A" not in workflow, "workflow does not stage every untracked file")
    require("git add -u -- ." in workflow, "workflow stages updates to tracked files only")
    require("retention-days: 90" in workflow, "workflow keeps dashboard artifacts for 90 days")
    require(
        "git reset -q -- dashboard_data.db dashboard/paper_learning_snapshot.json || true" in workflow,
        "workflow unstages generated dashboard snapshots before committing",
    )
    require(
        "logs/notebook_stage_manifest.json" in workflow
        and "logs/notebook_checkpoints/*.json" in workflow,
        "workflow uploads notebook stage timing checkpoints",
    )

    print("Pipeline safety checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
