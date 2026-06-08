import argparse
import sqlite3
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import automatic_paper_decisions
from dashboard import paper_outcomes


def load_prices(database):
    with sqlite3.connect(database) as conn:
        return pd.read_sql_query(
            """
            SELECT ticker, begins_at, close_price
            FROM RecentPrices
            ORDER BY ticker, begins_at
            """,
            conn,
        )


def main():
    parser = argparse.ArgumentParser(
        description="Append newly matured outcomes for automatic paper decisions."
    )
    parser.add_argument("--database", type=Path, default=ROOT / "dashboard_data.db")
    parser.add_argument("--decisions", type=Path, default=paper_outcomes.DEFAULT_DECISIONS)
    parser.add_argument("--outcomes", type=Path, default=paper_outcomes.DEFAULT_OUTCOMES)
    args = parser.parse_args()

    decisions = automatic_paper_decisions.load_ledger(args.decisions)
    existing = paper_outcomes.load_outcomes(args.outcomes)
    records = paper_outcomes.evaluate_decisions(
        decisions, load_prices(args.database), existing=existing
    )
    written = paper_outcomes.append_outcomes(records, args.outcomes)
    print(
        f"Automatic paper outcomes: decisions={len(decisions)} "
        f"eligible_events={len(records)} written={written} output={args.outcomes}"
    )


if __name__ == "__main__":
    main()
