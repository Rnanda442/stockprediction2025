#!/usr/bin/env python3
"""Stream pre-holdout prices and membership into a self-contained OSL database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from contextlib import closing
from datetime import date
from itertools import zip_longest
from pathlib import Path

from context_gate import assert_experiment_allowed, candidate_update, load_gate


EXPERIMENT_ID = "materialized_eligible_prices_v1"
DESIGN_SIGNATURE = (
    "materialized-eligible-prices-v1:researchprices+membership:"
    "streaming-merge:warmup-preserved:preholdout2026-05-29"
)
CUTOFF = "2026-05-29"


def readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def stamp(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--universe-db", type=Path, required=True)
    parser.add_argument("--context-gate", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-seconds", type=int, default=600)
    args = parser.parse_args()
    started = time.monotonic()
    gate = load_gate(args.context_gate)
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if spec.get("experiment_id") != EXPERIMENT_ID or spec.get("design_signature") != DESIGN_SIGNATURE:
        raise ValueError("The materialization specification is not the registered v1 design")
    if spec.get("cutoff_exclusive") != CUTOFF:
        raise ValueError("The sealed-holdout cutoff cannot be changed for this design")
    fingerprint = assert_experiment_allowed(gate, EXPERIMENT_ID, DESIGN_SIGNATURE, spec)
    source_path = args.db.resolve(strict=True)
    membership_path = args.universe_db.resolve(strict=True)
    audit = json.loads((membership_path.parent / "audit_manifest.json").read_text(encoding="utf-8"))
    if audit.get("status") != "completed":
        raise ValueError("A completed membership audit is required")
    if audit["holdout_policy"]["cutoff_exclusive"] != CUTOFF:
        raise ValueError("Membership and materialization cutoffs differ")
    if audit["holdout_policy"].get("sealed_holdout_read") is not False:
        raise ValueError("Membership audit does not attest that the holdout stayed sealed")
    if Path(audit["source_database"]).resolve() != source_path:
        raise ValueError("Membership belongs to a different source database")
    if audit["point_in_time_membership"].get("uses_future_information_for_eligibility") is not False:
        raise ValueError("Membership eligibility must use past information only")
    if not audit["point_in_time_membership"].get("materialized"):
        raise ValueError("Membership audit has no materialized membership")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError("Output directory is not empty; previous artifacts will not be overwritten")
    if args.max_seconds < 1:
        raise ValueError("max-seconds must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    partial_path = args.output_dir / "eligible_prices.partial.db"
    final_path = args.output_dir / "eligible_prices.db"
    source_stamp, membership_stamp = stamp(source_path), stamp(membership_path)
    expired = lambda: int(time.monotonic() - started > args.max_seconds)
    stats = {"rows_materialized": 0, "eligible_rows": 0, "warmup_or_ineligible_rows": 0, "tickers": 0}
    first_date = last_date = None
    previous_key = None
    print("Starting bounded streaming merge; no cross-database SQL join.", flush=True)

    with closing(readonly(source_path)) as source, closing(readonly(membership_path)) as universe, closing(sqlite3.connect(partial_path)) as output:
        for connection in (source, universe, output):
            connection.set_progress_handler(expired, 10000)
        output.executescript("""
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE ResearchPrices (
                ticker TEXT NOT NULL,
                begins_at TEXT NOT NULL,
                close_price REAL,
                volume REAL,
                universe_eligible INTEGER NOT NULL CHECK(universe_eligible IN (0, 1)),
                history_observations INTEGER NOT NULL,
                eligibility_reason TEXT NOT NULL,
                PRIMARY KEY(ticker, begins_at)
            ) WITHOUT ROWID;
            CREATE TABLE MaterializationMetadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        prices = source.execute("""
            SELECT UPPER(TRIM(ticker)) AS normalized_ticker,
                   date(begins_at) AS as_of_date, close_price, volume
            FROM ResearchPrices
            WHERE begins_at < ?
            ORDER BY normalized_ticker, as_of_date
        """, (CUTOFF,))
        memberships = universe.execute("""
            SELECT ticker, as_of_date, universe_eligible,
                   history_observations, eligibility_reason
            FROM point_in_time_universe
            WHERE as_of_date < ?
            ORDER BY ticker, as_of_date
        """, (CUTOFF,))
        batch = []
        for price, membership in zip_longest(prices, memberships):
            if price is None or membership is None:
                raise ValueError("Source and membership lengths differ; refusing a partial match")
            ticker, as_of_date, close, volume = price
            if not ticker or not as_of_date:
                raise ValueError("Invalid ticker or source date")
            date.fromisoformat(as_of_date)
            key = (ticker, as_of_date)
            if key != (membership[0], membership[1]):
                raise ValueError(f"Source/membership key mismatch at {key}")
            if previous_key is not None and key <= previous_key:
                raise ValueError("Duplicate or unordered source ticker-date")
            if as_of_date >= CUTOFF:
                raise ValueError("Sealed-holdout boundary violation")
            eligible, observations, reason = membership[2:]
            if eligible not in (0, 1) or observations < 1:
                raise ValueError("Invalid eligibility record")
            if previous_key is None or ticker != previous_key[0]:
                stats["tickers"] += 1
            previous_key = key
            first_date = as_of_date if first_date is None else min(first_date, as_of_date)
            last_date = as_of_date if last_date is None else max(last_date, as_of_date)
            batch.append((ticker, as_of_date, close, volume, eligible, observations, reason))
            stats["rows_materialized"] += 1
            stats["eligible_rows"] += eligible
            stats["warmup_or_ineligible_rows"] += 1 - eligible
            if len(batch) == 25000:
                if expired():
                    raise TimeoutError("Materialization time budget exceeded")
                output.executemany("INSERT INTO ResearchPrices VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
                output.commit()
                batch.clear()
                if stats["rows_materialized"] % 250000 == 0:
                    print(json.dumps({**stats, "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
        if batch:
            output.executemany("INSERT INTO ResearchPrices VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
        if not stats["rows_materialized"]:
            raise ValueError("No matched pre-holdout prices")
        if stats["rows_materialized"] != audit["point_in_time_membership"]["rows"]:
            raise ValueError("Materialized row count differs from the frozen membership audit")
        if stamp(source_path) != source_stamp or stamp(membership_path) != membership_stamp:
            raise RuntimeError("An input changed during materialization")
        output.executescript("""
            CREATE INDEX idx_materialized_date_eligibility
                ON ResearchPrices(begins_at, universe_eligible);
            CREATE VIEW EligiblePrices AS
                SELECT ticker, begins_at, close_price, volume,
                       history_observations, eligibility_reason
                FROM ResearchPrices WHERE universe_eligible = 1;
        """)
        manifest = {
            "experiment_id": EXPERIMENT_ID,
            "design_signature": DESIGN_SIGNATURE,
            "design_fingerprint": fingerprint,
            "status": "ready",
            "source_database": str(source_path),
            "membership_database": str(membership_path),
            "database": str(final_path.resolve()),
            "cutoff_exclusive": CUTOFF,
            "first_date": first_date,
            "last_date": last_date,
            **stats,
            "warmup_prices_retained": True,
            "eligibility_applied_after_trailing_features": True,
            "sealed_holdout_read": False,
            "source_databases_modified": False,
            "historical_security_master_verified": False,
            "model_training_or_scoring_performed": False,
            "source_file_size_and_mtime_ns": source_stamp,
            "membership_file_size_and_mtime_ns": membership_stamp,
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
        output.execute("INSERT INTO MaterializationMetadata VALUES ('manifest', ?)", (json.dumps(manifest),))
        output.commit()
    partial_path.rename(final_path)
    manifest["database_bytes"] = final_path.stat().st_size
    (args.output_dir / "materialization_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    update = candidate_update(gate, EXPERIMENT_ID, DESIGN_SIGNATURE, spec, manifest)
    (args.output_dir / "context_gate_candidate_update.json").write_text(json.dumps(update, indent=2) + "\n", encoding="utf-8")
    print("MATERIALIZATION_READY " + json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
