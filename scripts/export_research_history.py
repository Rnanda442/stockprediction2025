#!/usr/bin/env python3
"""Export a compact, full-span price database for research on OSL.

The dashboard intentionally carries only recent prices. This export keeps the
entire requested historical span while limiting the payload to fields used by
the leakage-controlled research labs. It never contains Robinhood credentials,
session data, holdings, or order functionality.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("historicals.db"))
    parser.add_argument("--output", type=Path, default=Path("research_history.db"))
    parser.add_argument("--span", default="5year")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.source.exists():
        raise FileNotFoundError(f"Historical database not found: {args.source}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.unlink(missing_ok=True)

    with sqlite3.connect(args.output) as destination:
        destination.execute("PRAGMA journal_mode=OFF")
        destination.execute("PRAGMA synchronous=OFF")
        destination.execute("PRAGMA temp_store=MEMORY")
        destination.execute("ATTACH DATABASE ? AS source", (str(args.source.resolve()),))
        tables = {
            row[0]
            for row in destination.execute(
                "SELECT name FROM source.sqlite_master WHERE type='table'"
            )
        }
        if "HistoricalPrices" not in tables:
            raise RuntimeError("historicals.db does not contain HistoricalPrices")

        destination.execute(
            """
            CREATE TABLE ResearchPrices (
              ticker TEXT NOT NULL,
              begins_at TEXT NOT NULL,
              close_price REAL NOT NULL,
              volume REAL NOT NULL,
              PRIMARY KEY (ticker, begins_at)
            ) WITHOUT ROWID
            """
        )
        destination.execute(
            """
            INSERT OR REPLACE INTO ResearchPrices
                (ticker, begins_at, close_price, volume)
            SELECT UPPER(TRIM(ticker)), begins_at, close_price, volume
            FROM source.HistoricalPrices
            WHERE span = ?
              AND ticker IS NOT NULL
              AND begins_at IS NOT NULL
              AND close_price IS NOT NULL
              AND close_price > 0
              AND volume IS NOT NULL
              AND volume >= 0
            ORDER BY ticker, begins_at
            """,
            (args.span,),
        )
        destination.execute(
            "CREATE INDEX idx_research_prices_date ON ResearchPrices(begins_at)"
        )
        rows, tickers, date_min, date_max = destination.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT ticker),
                   MIN(begins_at), MAX(begins_at)
            FROM ResearchPrices
            """
        ).fetchone()
        metadata = {
            "schema_version": 1,
            "source_database": args.source.name,
            "source_table": "HistoricalPrices",
            "source_span": args.span,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "rows": int(rows),
            "tickers": int(tickers),
            "date_min": date_min,
            "date_max": date_max,
            "contains_credentials": False,
            "contains_brokerage_orders": False,
            "purpose": "paper-only leakage-controlled historical research",
        }
        destination.execute(
            "CREATE TABLE ResearchMetadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        destination.executemany(
            "INSERT INTO ResearchMetadata VALUES (?, ?)",
            [(key, json.dumps(value)) for key, value in metadata.items()],
        )
        destination.commit()
        destination.execute("DETACH DATABASE source")
        destination.execute("VACUUM")

    print(json.dumps(metadata, indent=2))
    print(f"Research history written to {args.output}")


if __name__ == "__main__":
    main()
