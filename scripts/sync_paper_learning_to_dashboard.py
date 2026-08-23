import argparse
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import automatic_paper_decisions
from dashboard import paper_outcomes


def _json_records(frame):
    return frame.astype(object).where(frame.notna(), None).to_dict("records")


def sync(database, decisions_path, outcomes_path, snapshot_path):
    decisions = automatic_paper_decisions.load_ledger(decisions_path)
    outcomes = paper_outcomes.load_outcomes(outcomes_path)
    declared = decisions[["decision_id", "horizon_days"]].copy()
    declared["horizon_days"] = declared["horizon_days"].astype("Int64")
    declared_horizon = (
        outcomes["evaluation_horizon_days"].astype("Int64")
        == outcomes["decision_horizon_days"].astype("Int64")
    )
    matured_ids = set(
        outcomes.loc[
            declared_horizon & outcomes["status"].isin(["matured", "stopped", "targeted"]),
            "decision_id",
        ].astype(str)
    )
    unavailable_ids = set(
        outcomes.loc[
            declared_horizon & outcomes["status"].eq("unavailable"), "decision_id"
        ].astype(str)
    )
    unavailable_ids -= matured_ids
    resolved_ids = matured_ids | unavailable_ids
    open_count = int((~declared["decision_id"].astype(str).isin(resolved_ids)).sum())

    with sqlite3.connect(database) as conn:
        decisions.to_sql("AutomaticPaperDecisions", conn, if_exists="replace", index=False)
        outcomes.to_sql("AutomaticPaperOutcomeEvents", conn, if_exists="replace", index=False)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS PipelineHealth (
              metric TEXT PRIMARY KEY, value TEXT NOT NULL
            )
            """
        )
        metrics = {
            "automatic_paper_decisions_rows": len(decisions),
            "automatic_paper_outcome_events_rows": len(outcomes),
            "automatic_paper_open_decisions": open_count,
            "automatic_paper_matured_decisions": len(matured_ids),
            "automatic_paper_unavailable_decisions": len(unavailable_ids),
            "automatic_paper_unavailable_events": int((outcomes["status"] == "unavailable").sum()),
        }
        conn.executemany(
            """
            INSERT INTO PipelineHealth(metric, value) VALUES (?, ?)
            ON CONFLICT(metric) DO UPDATE SET value=excluded.value
            """,
            [(key, str(value)) for key, value in metrics.items()],
        )
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "decisions": _json_records(decisions),
                "outcomes": _json_records(outcomes),
                "metrics": {
                    "decisions": len(decisions),
                    "outcome_events": len(outcomes),
                    "open": open_count,
                    "matured": len(matured_ids),
                    "unavailable": len(unavailable_ids),
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return len(decisions), len(outcomes), open_count, len(matured_ids), len(unavailable_ids)


def main():
    parser = argparse.ArgumentParser(
        description="Publish paper decisions, outcomes, and health metrics to the dashboard DB."
    )
    parser.add_argument("--database", type=Path, default=ROOT / "dashboard_data.db")
    parser.add_argument("--decisions", type=Path, default=paper_outcomes.DEFAULT_DECISIONS)
    parser.add_argument("--outcomes", type=Path, default=paper_outcomes.DEFAULT_OUTCOMES)
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "dashboard" / "paper_learning_snapshot.json",
    )
    args = parser.parse_args()
    counts = sync(args.database, args.decisions, args.outcomes, args.snapshot)
    print(
        "Paper learning dashboard sync: "
        f"decisions={counts[0]} outcomes={counts[1]} open={counts[2]} "
        f"matured={counts[3]} unavailable={counts[4]}"
    )


if __name__ == "__main__":
    main()
