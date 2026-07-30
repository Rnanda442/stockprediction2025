"""Fast static regression checks for pipeline bootstrap and commit safety."""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebook" / "2025summerstock-Copy6.ipynb"
WORKFLOW = ROOT / ".github" / "workflows" / "stock-run.yml"


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

    print("Pipeline safety checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
