#!/usr/bin/env python3
"""Build a leakage-safe, pre-holdout ticker-universe audit.

The source database is opened read-only. Every query is bounded by the
exclusive cutoff, so the sealed holdout is never read. Disappearance flags are
post-hoc data-quality proxies, not claims that a security was delisted.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable


DESIGN_SIGNATURE = (
    "point-in-time-universe-audit-v1:researchprices:"
    "past-only-membership+lifecycle+sector-lineage:preholdout-only:holdout60"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--cutoff-exclusive", default="2026-05-29")
    parser.add_argument("--minimum-history-observations", type=int, default=60)
    parser.add_argument("--terminal-absence-days", type=int, default=60)
    parser.add_argument(
        "--sector-profiles",
        type=Path,
        default=Path("research_context/ticker_profiles_nasdaq_v1.json"),
    )
    parser.add_argument("--materialize-membership", action="store_true")
    return parser.parse_args()


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def scalar_field(record: dict[str, Any], names: set[str]) -> str:
    for key, value in record.items():
        if key.lower() in names and isinstance(value, (str, int, float)):
            return str(value).strip()
    return ""


def extract_current_profiles(payload: Any) -> dict[str, dict[str, str]]:
    """Tolerate list, keyed-map, and nested profile snapshot shapes."""
    profiles: dict[str, dict[str, str]] = {}

    def walk(value: Any, inherited_ticker: str = "") -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return

        ticker = scalar_field(value, {"ticker", "symbol", "stock_symbol"}) or inherited_ticker
        sector = scalar_field(value, {"sector", "sector_name"})
        industry = scalar_field(value, {"industry", "industry_name"})
        if ticker and (sector or industry):
            profiles[ticker.upper()] = {"sector": sector, "industry": industry}

        for key, item in value.items():
            child_ticker = ""
            if isinstance(item, dict) and key.isupper() and 1 <= len(key) <= 8:
                child_ticker = key
            walk(item, child_ticker)

    walk(payload)
    return profiles


def top_level_as_of(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("as_of", "as_of_date", "generated_at", "snapshot_date"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def materialize_membership(
    source_db: Path,
    target_db: Path,
    cutoff: str,
    minimum_history: int,
) -> int:
    connection = sqlite3.connect(target_db)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("ATTACH DATABASE ? AS source", (str(source_db.resolve()),))
    connection.executescript(
        """
        CREATE TABLE point_in_time_universe (
            as_of_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            history_observations INTEGER NOT NULL,
            source_rows_on_date INTEGER NOT NULL,
            has_close INTEGER NOT NULL,
            has_volume INTEGER NOT NULL,
            universe_eligible INTEGER NOT NULL,
            eligibility_reason TEXT NOT NULL,
            PRIMARY KEY (as_of_date, ticker)
        );
        """
    )
    connection.execute(
        """
        INSERT INTO point_in_time_universe
        WITH daily AS (
            SELECT
                date(begins_at) AS as_of_date,
                ticker,
                COUNT(*) AS source_rows_on_date,
                MAX(CASE WHEN close_price IS NOT NULL THEN 1 ELSE 0 END) AS has_close,
                MAX(CASE WHEN volume IS NOT NULL THEN 1 ELSE 0 END) AS has_volume
            FROM source.ResearchPrices
            WHERE begins_at < :cutoff
            GROUP BY date(begins_at), ticker
        ), history AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY ticker ORDER BY as_of_date
                ) AS history_observations
            FROM daily
        )
        SELECT
            as_of_date,
            ticker,
            history_observations,
            source_rows_on_date,
            has_close,
            has_volume,
            CASE
                WHEN source_rows_on_date != 1 THEN 0
                WHEN has_close = 0 THEN 0
                WHEN history_observations < :minimum_history THEN 0
                ELSE 1
            END AS universe_eligible,
            CASE
                WHEN source_rows_on_date != 1 THEN 'duplicate_source_rows'
                WHEN has_close = 0 THEN 'missing_close'
                WHEN history_observations < :minimum_history THEN 'insufficient_history'
                ELSE 'eligible_past_only'
            END AS eligibility_reason
        FROM history
        """,
        {"cutoff": cutoff, "minimum_history": minimum_history},
    )
    connection.executescript(
        """
        CREATE INDEX idx_pitu_ticker_date
            ON point_in_time_universe(ticker, as_of_date);
        CREATE INDEX idx_pitu_eligible_date
            ON point_in_time_universe(universe_eligible, as_of_date);
        CREATE VIEW eligible_universe AS
            SELECT * FROM point_in_time_universe WHERE universe_eligible = 1;
        """
    )
    row_count = connection.execute(
        "SELECT COUNT(*) FROM point_in_time_universe"
    ).fetchone()[0]
    connection.commit()
    connection.close()
    return int(row_count)


def main() -> None:
    args = parse_args()
    cutoff_date = date.fromisoformat(args.cutoff_exclusive)
    source_db = args.db.resolve()
    if not source_db.is_file():
        raise FileNotFoundError(source_db)
    if args.minimum_history_observations < 1:
        raise ValueError("minimum history must be positive")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(ResearchPrices)")
    }
    required = {"ticker", "begins_at", "close_price", "volume"}
    if not required.issubset(columns):
        raise RuntimeError(f"ResearchPrices is missing columns: {sorted(required - columns)}")

    summary = connection.execute(
        """
        SELECT
            MIN(date(begins_at)),
            MAX(date(begins_at)),
            COUNT(*),
            COUNT(DISTINCT ticker),
            COUNT(DISTINCT date(begins_at))
        FROM ResearchPrices
        WHERE begins_at < ?
        """,
        (args.cutoff_exclusive,),
    ).fetchone()
    start_text, end_text, source_rows, ticker_count, trading_date_count = summary
    if not start_text or not end_text:
        raise RuntimeError("no pre-holdout ResearchPrices rows found")
    start_date = date.fromisoformat(start_text)
    end_date = date.fromisoformat(end_text)

    market_dates = [
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT date(begins_at) AS d
            FROM ResearchPrices
            WHERE begins_at < ?
            ORDER BY d
            """,
            (args.cutoff_exclusive,),
        )
    ]
    lifecycle_raw = connection.execute(
        """
        WITH daily AS (
            SELECT ticker, date(begins_at) AS d, COUNT(*) AS source_rows
            FROM ResearchPrices
            WHERE begins_at < ?
            GROUP BY ticker, date(begins_at)
        )
        SELECT ticker, MIN(d), MAX(d), COUNT(*), SUM(source_rows)
        FROM daily
        GROUP BY ticker
        ORDER BY ticker
        """,
        (args.cutoff_exclusive,),
    ).fetchall()
    duplicate_groups, duplicate_excess_rows = connection.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(source_rows - 1), 0)
        FROM (
            SELECT COUNT(*) AS source_rows
            FROM ResearchPrices
            WHERE begins_at < ?
            GROUP BY ticker, date(begins_at)
            HAVING COUNT(*) > 1
        )
        """,
        (args.cutoff_exclusive,),
    ).fetchone()
    missing_close_rows, missing_volume_rows = connection.execute(
        """
        SELECT
            SUM(CASE WHEN close_price IS NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN volume IS NULL THEN 1 ELSE 0 END)
        FROM ResearchPrices
        WHERE begins_at < ?
        """,
        (args.cutoff_exclusive,),
    ).fetchone()
    connection.close()

    lifecycle_rows: list[dict[str, Any]] = []
    entry_months: Counter[str] = Counter()
    exit_months: Counter[str] = Counter()
    for ticker, first_text, last_text, observed_dates, ticker_source_rows in lifecycle_raw:
        first_date = date.fromisoformat(first_text)
        last_date = date.fromisoformat(last_text)
        first_index = bisect.bisect_left(market_dates, first_text)
        last_index = bisect.bisect_right(market_dates, last_text)
        market_dates_in_span = max(1, last_index - first_index)
        terminal_absence = (end_date - last_date).days
        terminal_absence_proxy = int(
            terminal_absence >= args.terminal_absence_days and last_date < end_date
        )
        row = {
            "ticker": ticker,
            "first_seen": first_text,
            "last_seen": last_text,
            "observed_dates": int(observed_dates),
            "source_rows": int(ticker_source_rows),
            "market_dates_in_observed_span": market_dates_in_span,
            "coverage_ratio": round(observed_dates / market_dates_in_span, 6),
            "left_censored_at_dataset_start": int(first_date == start_date),
            "right_censored_at_training_end": int(last_date == end_date),
            "terminal_absence_calendar_days": terminal_absence,
            "terminal_absence_proxy": terminal_absence_proxy,
            "proxy_interpretation": (
                "possible_delisting_or_coverage_exit_requires_external_confirmation"
                if terminal_absence_proxy
                else "no_terminal_absence_signal"
            ),
        }
        lifecycle_rows.append(row)
        entry_months[first_text[:7]] += 1
        exit_months[last_text[:7]] += 1

    write_csv(
        args.output_dir / "ticker_lifecycle_audit.csv",
        list(lifecycle_rows[0]),
        lifecycle_rows,
    )

    month_counts: dict[str, set[str]] = {}
    read_connection = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    for month, ticker in read_connection.execute(
        """
        SELECT DISTINCT substr(date(begins_at), 1, 7), ticker
        FROM ResearchPrices
        WHERE begins_at < ?
        ORDER BY 1, 2
        """,
        (args.cutoff_exclusive,),
    ):
        month_counts.setdefault(month, set()).add(ticker)
    read_connection.close()
    monthly_rows = [
        {
            "month": month,
            "observed_tickers": len(tickers),
            "first_seen_tickers": entry_months[month],
            "last_seen_tickers": exit_months[month],
        }
        for month, tickers in sorted(month_counts.items())
    ]
    write_csv(
        args.output_dir / "monthly_universe_audit.csv",
        list(monthly_rows[0]),
        monthly_rows,
    )

    profile_payload: Any = {}
    profile_path = args.sector_profiles.resolve()
    if profile_path.is_file():
        profile_payload = json.loads(profile_path.read_text(encoding="utf-8"))
    current_profiles = extract_current_profiles(profile_payload)
    sector_rows = []
    for row in lifecycle_rows:
        profile = current_profiles.get(row["ticker"], {})
        has_profile = bool(profile)
        sector_rows.append(
            {
                "ticker": row["ticker"],
                "current_sector": profile.get("sector", ""),
                "current_industry": profile.get("industry", ""),
                "current_profile_available": int(has_profile),
                "point_in_time_sector_safe": 0,
                "lineage_status": (
                    "current_snapshot_only_not_valid_for_historical_features"
                    if has_profile
                    else "no_sector_metadata"
                ),
            }
        )
    write_csv(
        args.output_dir / "sector_lineage_audit.csv",
        list(sector_rows[0]),
        sector_rows,
    )

    membership_rows = 0
    membership_path = args.output_dir / "point_in_time_universe.db"
    if args.materialize_membership:
        membership_rows = materialize_membership(
            source_db,
            membership_path,
            args.cutoff_exclusive,
            args.minimum_history_observations,
        )

    disappearance_count = sum(row["terminal_absence_proxy"] for row in lifecycle_rows)
    profile_count = sum(row["current_profile_available"] for row in sector_rows)
    manifest = {
        "audit_id": "point_in_time_universe_audit_v1",
        "design_signature": DESIGN_SIGNATURE,
        "status": "completed",
        "source_database": str(source_db),
        "source_table": "ResearchPrices",
        "holdout_policy": {
            "cutoff_exclusive": args.cutoff_exclusive,
            "sealed_holdout_read": False,
            "query_guard": "every source query uses begins_at < cutoff_exclusive",
        },
        "pre_holdout_window": {
            "first_date": start_text,
            "last_date": end_text,
            "source_rows": int(source_rows),
            "tickers": int(ticker_count),
            "trading_dates": int(trading_date_count),
        },
        "data_quality": {
            "duplicate_ticker_date_groups": int(duplicate_groups),
            "duplicate_excess_rows": int(duplicate_excess_rows),
            "missing_close_rows": int(missing_close_rows or 0),
            "missing_volume_rows": int(missing_volume_rows or 0),
        },
        "point_in_time_membership": {
            "materialized": bool(args.materialize_membership),
            "rows": membership_rows,
            "minimum_history_observations": args.minimum_history_observations,
            "uses_future_information_for_eligibility": False,
        },
        "disappearance_audit": {
            "terminal_absence_days": args.terminal_absence_days,
            "proxy_tickers": disappearance_count,
            "confirmed_delistings": 0,
            "warning": "proxy requires an external dated security-master source",
        },
        "sector_lineage": {
            "profile_source": str(profile_path) if profile_path.is_file() else "missing",
            "profile_snapshot_as_of": top_level_as_of(profile_payload),
            "current_profiles_matched": profile_count,
            "point_in_time_sector_ready": False,
            "warning": "current sector labels must not be used as historical truth",
        },
        "artifacts": [
            "ticker_lifecycle_audit.csv",
            "monthly_universe_audit.csv",
            "sector_lineage_audit.csv",
            "point_in_time_universe.db" if args.materialize_membership else None,
            "readout.md",
        ],
    }
    manifest["artifacts"] = [item for item in manifest["artifacts"] if item]
    (args.output_dir / "audit_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    readout = f"""# Point-in-time universe and lineage audit v1

## Safe scope

- Source rows were restricted to `begins_at < {args.cutoff_exclusive}`.
- The sealed holdout was not read.
- Universe eligibility uses only observations available on or before each date.

## Pre-holdout inventory

- Window: {start_text} through {end_text}
- Rows: {int(source_rows):,}
- Tickers: {int(ticker_count):,}
- Trading dates: {int(trading_date_count):,}
- Duplicate ticker-date groups: {int(duplicate_groups):,}
- Missing closes: {int(missing_close_rows or 0):,}
- Missing volumes: {int(missing_volume_rows or 0):,}

## Lifecycle and sector conclusions

- {disappearance_count:,} tickers meet the {args.terminal_absence_days}-calendar-day disappearance proxy.
- These are not confirmed delistings; they can also reflect symbol changes or source coverage exits.
- Current sector profiles matched {profile_count:,} tickers.
- Point-in-time sector lineage is not ready because the available profile snapshot is not historically dated.
- Historical sector features remain blocked until a dated external security master is added.

## Reusable output

- `point_in_time_universe.db` contains past-only daily eligibility when materialization is enabled.
- Post-hoc disappearance flags are isolated in the lifecycle audit and must not be model features.
"""
    (args.output_dir / "readout.md").write_text(readout, encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
