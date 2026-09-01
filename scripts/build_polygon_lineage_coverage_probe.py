#!/usr/bin/env python3
"""Probe Polygon point-in-time coverage without opening the sealed holdout.

The probe reads only trading dates from the existing OSL database, requests a
small deterministic sample of full-market bars and dated ticker snapshots, and
stores all provider data on OSL. It never trains or scores a model and never
writes an API key to an artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPERIMENT_ID = "polygon_lineage_coverage_probe_v1"
DESIGN_SIGNATURE = (
    "polygon-lineage-coverage-probe-v1:3dates:grouped-daily+dated-tickers:"
    "source-comparison:preholdout-only:no-model"
)
API_ROOT = "https://api.polygon.io"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--start-date", default="2021-08-23")
    parser.add_argument("--cutoff-exclusive", default="2026-05-29")
    parser.add_argument("--max-dates", type=int, default=3)
    parser.add_argument("--api-key-env", default="POLYGON_API_KEY")
    parser.add_argument("--request-delay-seconds", type=float, default=0.25)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def select_probe_dates(dates: list[str], maximum: int) -> list[str]:
    if maximum < 1:
        raise ValueError("--max-dates must be positive")
    if len(dates) <= maximum:
        return dates
    if maximum == 1:
        return [dates[-1]]
    indexes = {
        round(position * (len(dates) - 1) / (maximum - 1))
        for position in range(maximum)
    }
    return [dates[index] for index in sorted(indexes)]


def source_inventory(
    source_db: Path,
    start_date: str,
    cutoff_exclusive: str,
) -> tuple[list[str], dict[str, set[str]]]:
    uri = f"file:{source_db.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(ResearchPrices)")
        }
        if not {"ticker", "begins_at"}.issubset(columns):
            raise RuntimeError("ResearchPrices lacks ticker or begins_at")
        dates = [
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT date(begins_at)
                FROM ResearchPrices
                WHERE begins_at >= ? AND begins_at < ?
                ORDER BY 1
                """,
                (start_date, cutoff_exclusive),
            )
        ]
    if not dates:
        raise RuntimeError("No source dates found inside the pre-holdout window")
    return dates, {}


def source_tickers(source_db: Path, dates: list[str]) -> dict[str, set[str]]:
    placeholders = ",".join("?" for _ in dates)
    uri = f"file:{source_db.resolve()}?mode=ro"
    output = {date: set() for date in dates}
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            f"""
            SELECT date(begins_at), UPPER(TRIM(ticker))
            FROM ResearchPrices
            WHERE date(begins_at) IN ({placeholders})
            GROUP BY date(begins_at), UPPER(TRIM(ticker))
            """,
            dates,
        )
        for as_of_date, ticker in rows:
            output[as_of_date].add(ticker)
    return output


def request_json(url: str, api_key: str, delay: float) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key == "apiKey" for key, _ in query):
        query.append(("apiKey", api_key))
    authenticated = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )
    request = urllib.request.Request(
        authenticated,
        headers={"User-Agent": "stockprediction2025-research/1.0"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            time.sleep(max(0.0, delay))
            if payload.get("status") not in {None, "OK", "DELAYED"}:
                raise RuntimeError(f"Provider status: {payload.get('status')}")
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"Polygon HTTP {exc.code}: {detail}") from exc
            retry_after = float(exc.headers.get("Retry-After", 2 ** attempt))
            time.sleep(max(retry_after, delay))
        except urllib.error.URLError as exc:
            if attempt == 3:
                raise RuntimeError(f"Polygon request failed: {exc.reason}") from exc
            time.sleep(max(2 ** attempt, delay))
    raise RuntimeError("Polygon request retry loop exhausted")


def create_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS ingestion_date (
            as_of_date TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            bar_rows INTEGER NOT NULL DEFAULT 0,
            reference_rows INTEGER NOT NULL DEFAULT 0,
            bar_request_id TEXT,
            retrieved_at TEXT NOT NULL,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS daily_bar (
            as_of_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            open_price REAL,
            high_price REAL,
            low_price REAL,
            close_price REAL,
            volume REAL,
            vwap REAL,
            transactions INTEGER,
            otc INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (as_of_date, ticker)
        );
        CREATE TABLE IF NOT EXISTS ticker_snapshot (
            as_of_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT,
            active INTEGER,
            market TEXT,
            security_type TEXT,
            primary_exchange TEXT,
            currency_name TEXT,
            cik TEXT,
            composite_figi TEXT,
            share_class_figi TEXT,
            delisted_utc TEXT,
            last_updated_utc TEXT,
            PRIMARY KEY (as_of_date, ticker)
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    return connection


def fetch_reference_snapshot(
    as_of_date: str,
    api_key: str,
    delay: float,
) -> list[dict[str, Any]]:
    url = (
        f"{API_ROOT}/v3/reference/tickers?market=stocks&locale=us&active=true"
        f"&date={as_of_date}&limit=1000&sort=ticker&order=asc"
    )
    rows: list[dict[str, Any]] = []
    while url:
        payload = request_json(url, api_key, delay)
        rows.extend(payload.get("results") or [])
        url = payload.get("next_url") or ""
    return rows


def main() -> None:
    args = parse_args()
    api_key = os.getenv(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"Set {args.api_key_env} in the OSL environment before running")
    source_db = args.source_db.resolve(strict=True)
    if args.start_date >= args.cutoff_exclusive:
        raise ValueError("start date must precede the exclusive cutoff")
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.resume:
        raise FileExistsError(f"Output directory is not empty: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    all_dates, _ = source_inventory(source_db, args.start_date, args.cutoff_exclusive)
    probe_dates = select_probe_dates(all_dates, args.max_dates)
    existing_tickers = source_tickers(source_db, probe_dates)
    database_path = args.output_dir / "polygon_point_in_time_probe.db"
    connection = create_database(database_path)

    comparisons: list[dict[str, Any]] = []
    for as_of_date in probe_dates:
        completed = connection.execute(
            "SELECT status FROM ingestion_date WHERE as_of_date = ?",
            (as_of_date,),
        ).fetchone()
        if args.resume and completed and completed[0] == "completed":
            continue
        try:
            bars_payload = request_json(
                f"{API_ROOT}/v2/aggs/grouped/locale/us/market/stocks/{as_of_date}"
                "?adjusted=false&include_otc=false",
                api_key,
                args.request_delay_seconds,
            )
            bar_rows = bars_payload.get("results") or []
            reference_rows = fetch_reference_snapshot(
                as_of_date,
                api_key,
                args.request_delay_seconds,
            )
            connection.execute("DELETE FROM daily_bar WHERE as_of_date = ?", (as_of_date,))
            connection.execute("DELETE FROM ticker_snapshot WHERE as_of_date = ?", (as_of_date,))
            connection.executemany(
                """
                INSERT INTO daily_bar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        as_of_date,
                        str(row.get("T", "")).strip().upper(),
                        row.get("o"),
                        row.get("h"),
                        row.get("l"),
                        row.get("c"),
                        row.get("v"),
                        row.get("vw"),
                        row.get("n"),
                        int(bool(row.get("otc", False))),
                    )
                    for row in bar_rows
                    if str(row.get("T", "")).strip()
                ],
            )
            connection.executemany(
                """
                INSERT INTO ticker_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        as_of_date,
                        str(row.get("ticker", "")).strip().upper(),
                        row.get("name"),
                        int(bool(row.get("active"))) if row.get("active") is not None else None,
                        row.get("market"),
                        row.get("type"),
                        row.get("primary_exchange"),
                        row.get("currency_name"),
                        row.get("cik"),
                        row.get("composite_figi"),
                        row.get("share_class_figi"),
                        row.get("delisted_utc"),
                        row.get("last_updated_utc"),
                    )
                    for row in reference_rows
                    if str(row.get("ticker", "")).strip()
                ],
            )
            bar_tickers = {
                str(row.get("T", "")).strip().upper()
                for row in bar_rows
                if str(row.get("T", "")).strip()
            }
            reference_tickers = {
                str(row.get("ticker", "")).strip().upper()
                for row in reference_rows
                if str(row.get("ticker", "")).strip()
            }
            source_set = existing_tickers[as_of_date]
            comparison = {
                "as_of_date": as_of_date,
                "source_tickers": len(source_set),
                "polygon_bar_tickers": len(bar_tickers),
                "polygon_reference_tickers": len(reference_tickers),
                "polygon_bars_missing_from_source": len(bar_tickers - source_set),
                "source_tickers_missing_polygon_bars": len(source_set - bar_tickers),
                "polygon_reference_without_bar": len(reference_tickers - bar_tickers),
                "sample_polygon_bars_missing_from_source": sorted(bar_tickers - source_set)[:50],
            }
            comparisons.append(comparison)
            connection.execute(
                """
                INSERT OR REPLACE INTO ingestion_date
                VALUES (?, 'completed', ?, ?, ?, ?, NULL)
                """,
                (
                    as_of_date,
                    len(bar_tickers),
                    len(reference_tickers),
                    bars_payload.get("request_id"),
                    utc_now(),
                ),
            )
            connection.commit()
        except Exception as exc:
            connection.execute(
                """
                INSERT OR REPLACE INTO ingestion_date
                VALUES (?, 'failed', 0, 0, NULL, ?, ?)
                """,
                (as_of_date, utc_now(), str(exc)[:1000]),
            )
            connection.commit()
            raise

    completed_dates = connection.execute(
        "SELECT COUNT(*) FROM ingestion_date WHERE status = 'completed'"
    ).fetchone()[0]
    total_bars = connection.execute("SELECT COUNT(*) FROM daily_bar").fetchone()[0]
    total_snapshots = connection.execute("SELECT COUNT(*) FROM ticker_snapshot").fetchone()[0]
    metadata = {
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "source_database": str(source_db),
        "cutoff_exclusive": args.cutoff_exclusive,
        "sealed_holdout_read": False,
        "model_training_or_scoring_performed": False,
        "api_key_stored": False,
    }
    connection.executemany(
        "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
        [(key, json.dumps(value)) for key, value in metadata.items()],
    )
    connection.commit()
    connection.close()

    manifest = {
        **metadata,
        "status": "coverage_probe_complete",
        "provider": "Polygon/Massive",
        "probe_dates": probe_dates,
        "completed_dates": int(completed_dates),
        "daily_bar_rows": int(total_bars),
        "ticker_snapshot_rows": int(total_snapshots),
        "comparisons": comparisons,
        "fully_point_in_time_ready": False,
        "remaining_before_full_backfill": [
            "Confirm plan entitlements and historical depth from this probe.",
            "Backfill every pre-holdout trading date with resumable checkpoints.",
            "Fetch inactive tickers, entity events, and dated SIC classifications.",
            "Reconcile terminal corporate actions and unresolved delisting returns.",
        ],
    }
    write_json(args.output_dir / "coverage_probe_manifest.json", manifest)
    write_json(
        args.output_dir / "context_gate_candidate_update.json",
        {
            "experiment_id": EXPERIMENT_ID,
            "design_signature": DESIGN_SIGNATURE,
            "candidate_status": "completed_pending_review",
            "promotion": False,
            "summary": manifest,
            "next_experiment": "polygon_point_in_time_full_backfill_v1 after coverage and entitlement review",
        },
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
