#!/usr/bin/env python3
"""Backfill persistent missing entities and materialize staged daily membership.

This source-repair job uses Massive ticker events and adjusted daily aggregates,
then ranks candidates together with the existing Robinhood source using the
project's past-only 20-date mean-dollar-volume top-1000 rule. It does not alter
the source database, train a model, read targets, or access the sealed holdout.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
import urllib.parse
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from build_massive_recent_universe_overlap_audit import request_json, write_csv, write_json


BACKFILL_ID = "massive_persistent_candidate_backfill_v1"
TRAILING_DATES = 20
TOP_LIQUIDITY = 1000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--candidate-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--start-date", default="2024-09-03")
    parser.add_argument("--cutoff-exclusive", default="2026-05-29")
    parser.add_argument("--request-delay-seconds", type=float, default=15.0)
    parser.add_argument("--rate-limit-wait-seconds", type=float, default=65.0)
    parser.add_argument("--max-rate-limit-retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def safe_cache_name(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    readable = "".join(character for character in value if character.isalnum())[:18]
    return f"{readable or 'entity'}_{digest}"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_registry(candidate_dir: Path) -> dict[str, dict[str, Any]]:
    persistent_path = candidate_dir / "persistent_conservative_candidates.csv"
    candidates_path = candidate_dir / "eligible_missing_candidates.csv"
    persistent_ids = {
        row["entity_id"] for row in read_csv(persistent_path) if row.get("entity_id")
    }
    candidate_rows = [
        row
        for row in read_csv(candidates_path)
        if row.get("entity_id") in persistent_ids
        and row.get("candidate_set") == "conservative_all_market_top1000"
    ]
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidate_rows:
        grouped[row["entity_id"]].append(row)

    registry: dict[str, dict[str, Any]] = {}
    for entity_id in sorted(persistent_ids):
        rows = grouped.get(entity_id, [])
        if not rows:
            raise RuntimeError(f"Persistent entity lacks conservative rows: {entity_id}")
        latest = max(rows, key=lambda row: row["anchor_date"])
        tickers = sorted({normalize_ticker(row["ticker"]) for row in rows if row.get("ticker")})
        registry[entity_id] = {
            "entity_id": entity_id,
            "name": latest.get("name"),
            "type": latest.get("type"),
            "primary_exchange": latest.get("primary_exchange"),
            "cik": latest.get("cik"),
            "composite_figi": latest.get("composite_figi"),
            "share_class_figi": latest.get("share_class_figi"),
            "anchor_tickers": tickers,
            "anchor_dates": sorted({row["anchor_date"] for row in rows}),
        }
    return registry


def events_url(identifier: str) -> str:
    encoded = urllib.parse.quote(identifier, safe="")
    return f"https://api.massive.com/vX/reference/tickers/{encoded}/events?types=ticker_change"


def bars_url(ticker: str, start_date: str, end_date: str) -> str:
    encoded = urllib.parse.quote(ticker, safe="")
    return (
        f"https://api.massive.com/v2/aggs/ticker/{encoded}/range/1/day/"
        f"{start_date}/{end_date}?adjusted=true&sort=asc&limit=50000"
    )


def fetch_cached(
    cache_path: Path,
    url: str,
    api_key: str,
    args: argparse.Namespace,
    allow_error: bool,
) -> tuple[dict[str, Any], bool]:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8")), False
    try:
        payload = request_json(
            url,
            api_key,
            args.request_delay_seconds,
            args.rate_limit_wait_seconds,
            args.max_rate_limit_retries,
        )
        cached = {"status": "ok", "payload": payload}
    except Exception as exc:
        if not allow_error:
            raise
        cached = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    write_json(cache_path, cached)
    return cached, True


def event_identifier(entity: dict[str, Any]) -> str:
    return str(
        entity.get("composite_figi")
        or entity.get("share_class_figi")
        or entity["anchor_tickers"][-1]
    )


def extract_events(cached: dict[str, Any]) -> list[dict[str, Any]]:
    if cached.get("status") != "ok":
        return []
    payload = cached.get("payload") or {}
    results = payload.get("results") or {}
    events = results.get("events") or [] if isinstance(results, dict) else []
    normalized = []
    for event in events:
        ticker_change = event.get("ticker_change") or {}
        ticker = normalize_ticker(ticker_change.get("ticker"))
        event_date = event.get("date")
        if event.get("type") == "ticker_change" and ticker and event_date:
            normalized.append(
                {"date": str(event_date), "ticker": ticker, "type": "ticker_change"}
            )
    return sorted(normalized, key=lambda row: (row["date"], row["ticker"]))


def build_alias_rows(
    entity_id: str,
    anchor_tickers: list[str],
    events: list[dict[str, Any]],
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    event_tickers = [event["ticker"] for event in events]
    all_tickers = sorted(set(anchor_tickers) | set(event_tickers))
    effective = {event["ticker"]: event["date"] for event in events}
    rows = []
    ordered_events = sorted(events, key=lambda row: row["date"])
    next_dates = {
        event["ticker"]: ordered_events[index + 1]["date"]
        for index, event in enumerate(ordered_events[:-1])
    }
    for ticker in all_tickers:
        valid_from = effective.get(ticker)
        next_date = next_dates.get(ticker)
        valid_to = (
            (date.fromisoformat(next_date) - timedelta(days=1)).isoformat()
            if next_date
            else None
        )
        rows.append(
            {
                "entity_id": entity_id,
                "ticker": ticker,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "window_start": start_date,
                "window_end": end_date,
                "validity_source": "massive_ticker_event" if ticker in effective else "anchor_snapshot",
            }
        )
    return rows


def timestamp_date(value: Any) -> str:
    return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).date().isoformat()


def extract_bars(cached: dict[str, Any], ticker: str) -> list[dict[str, Any]]:
    if cached.get("status") != "ok":
        return []
    payload = cached.get("payload") or {}
    rows = []
    for bar in payload.get("results") or []:
        if bar.get("t") is None:
            continue
        rows.append(
            {
                "ticker": ticker,
                "date": timestamp_date(bar["t"]),
                "open": bar.get("o"),
                "high": bar.get("h"),
                "low": bar.get("l"),
                "close": bar.get("c"),
                "volume": bar.get("v"),
                "vwap": bar.get("vw"),
                "transactions": bar.get("n"),
            }
        )
    return rows


def choose_bar(existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    old_key = (float(existing.get("transactions") or -1), float(existing.get("volume") or -1))
    new_key = (float(candidate.get("transactions") or -1), float(candidate.get("volume") or -1))
    return candidate if new_key > old_key else existing


def source_trading_dates(db_path: Path, cutoff: str) -> list[str]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT substr(begins_at, 1, 10) AS trading_date
            FROM ResearchPrices
            WHERE substr(begins_at, 1, 10) < ?
            ORDER BY trading_date
            """,
            (cutoff,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def source_rows(
    db_path: Path,
    warmup_start: str,
    cutoff: str,
) -> dict[str, dict[str, tuple[float, float]]]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    by_date: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            """
            SELECT substr(begins_at, 1, 10), ticker, close_price, volume
            FROM ResearchPrices
            WHERE substr(begins_at, 1, 10) >= ?
              AND substr(begins_at, 1, 10) < ?
            ORDER BY begins_at, ticker
            """,
            (warmup_start, cutoff),
        )
        for trading_date, ticker, close, volume in rows:
            if close is None or volume is None:
                continue
            by_date[str(trading_date)][normalize_ticker(ticker)] = (
                float(close),
                max(float(volume), 0.0),
            )
    return by_date


def materialize_membership(
    db_path: Path,
    candidate_bars: list[dict[str, Any]],
    start_date: str,
    cutoff: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trading_dates = source_trading_dates(db_path, cutoff)
    start_index = trading_dates.index(start_date)
    warmup_start = trading_dates[max(0, start_index - (TRAILING_DATES - 1))]
    source_by_date = source_rows(db_path, warmup_start, cutoff)
    candidates_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidate_bars:
        if start_date <= row["date"] < cutoff:
            candidates_by_date[row["date"]].append(row)

    rolling: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=TRAILING_DATES))
    history_count: dict[str, int] = defaultdict(int)
    membership: list[dict[str, Any]] = []
    source_conflicts = 0

    for trading_date in [value for value in trading_dates if warmup_start <= value < cutoff]:
        values_today: dict[str, float] = {}
        source_tickers_today = set(source_by_date.get(trading_date, {}))
        for ticker, (close, volume) in source_by_date.get(trading_date, {}).items():
            key = "SRC:" + ticker
            rolling[key].append(close * volume)
            history_count[key] += 1
            if len(rolling[key]) == TRAILING_DATES:
                values_today[key] = sum(rolling[key]) / TRAILING_DATES

        candidate_today: dict[str, dict[str, Any]] = {}
        for row in candidates_by_date.get(trading_date, []):
            key = "ENT:" + row["entity_id"]
            candidate_today[key] = row
            if row["close"] is None or row["volume"] is None:
                continue
            rolling[key].append(float(row["close"]) * max(float(row["volume"]), 0.0))
            history_count[key] += 1
            if len(rolling[key]) == TRAILING_DATES:
                values_today[key] = sum(rolling[key]) / TRAILING_DATES

        ranks = {
            key: index + 1
            for index, key in enumerate(
                sorted(values_today, key=lambda item: (-values_today[item], item))
            )
        }
        if trading_date < start_date:
            continue
        for key, row in sorted(candidate_today.items()):
            ticker_conflict = row["ticker"] in source_tickers_today
            if ticker_conflict:
                source_conflicts += 1
            rank = ranks.get(key)
            if ticker_conflict:
                eligible = False
                reason = "already_present_in_source"
            elif history_count[key] < TRAILING_DATES:
                eligible = False
                reason = "insufficient_20_date_history"
            elif rank is None:
                eligible = False
                reason = "missing_liquidity_rank"
            elif rank > TOP_LIQUIDITY:
                eligible = False
                reason = "outside_top_1000_liquidity"
            else:
                eligible = True
                reason = "eligible_missing_past_only"
            membership.append(
                {
                    "date": trading_date,
                    "entity_id": row["entity_id"],
                    "ticker": row["ticker"],
                    "history_observations": history_count[key],
                    "mean_dollar_volume_20d": values_today.get(key),
                    "combined_liquidity_rank": rank,
                    "universe_eligible": int(eligible),
                    "eligibility_reason": reason,
                }
            )
    return membership, {
        "warmup_start": warmup_start,
        "source_conflict_rows": source_conflicts,
    }


def write_database(
    path: Path,
    registry: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
    events: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    membership: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            DROP TABLE IF EXISTS CandidateEntity;
            DROP TABLE IF EXISTS TickerAlias;
            DROP TABLE IF EXISTS TickerEvent;
            DROP TABLE IF EXISTS CandidatePrice;
            DROP TABLE IF EXISTS CandidateMembership;
            CREATE TABLE CandidateEntity (
                entity_id TEXT PRIMARY KEY,
                name TEXT,
                security_type TEXT,
                primary_exchange TEXT,
                cik TEXT,
                composite_figi TEXT,
                share_class_figi TEXT
            );
            CREATE TABLE TickerAlias (
                entity_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                valid_from TEXT,
                valid_to TEXT,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                validity_source TEXT NOT NULL,
                PRIMARY KEY (entity_id, ticker)
            );
            CREATE TABLE TickerEvent (
                entity_id TEXT NOT NULL,
                event_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                event_type TEXT NOT NULL,
                PRIMARY KEY (entity_id, event_date, ticker)
            );
            CREATE TABLE CandidatePrice (
                entity_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                price_date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                vwap REAL,
                transactions REAL,
                adjusted INTEGER NOT NULL,
                PRIMARY KEY (entity_id, price_date)
            );
            CREATE TABLE CandidateMembership (
                price_date TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                history_observations INTEGER NOT NULL,
                mean_dollar_volume_20d REAL,
                combined_liquidity_rank INTEGER,
                universe_eligible INTEGER NOT NULL,
                eligibility_reason TEXT NOT NULL,
                PRIMARY KEY (price_date, entity_id)
            );
            """
        )
        connection.executemany(
            "INSERT INTO CandidateEntity VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["entity_id"], row.get("name"), row.get("type"),
                    row.get("primary_exchange"), row.get("cik"),
                    row.get("composite_figi"), row.get("share_class_figi"),
                )
                for row in registry
            ],
        )
        connection.executemany(
            "INSERT INTO TickerAlias VALUES (?, ?, ?, ?, ?, ?, ?)",
            [tuple(row[field] for field in ("entity_id", "ticker", "valid_from", "valid_to", "window_start", "window_end", "validity_source")) for row in aliases],
        )
        connection.executemany(
            "INSERT INTO TickerEvent VALUES (?, ?, ?, ?)",
            [(row["entity_id"], row["date"], row["ticker"], row["type"]) for row in events],
        )
        connection.executemany(
            "INSERT INTO CandidatePrice VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            [
                (
                    row["entity_id"], row["ticker"], row["date"], row.get("open"),
                    row.get("high"), row.get("low"), row.get("close"), row.get("volume"),
                    row.get("vwap"), row.get("transactions"),
                )
                for row in bars
            ],
        )
        connection.executemany(
            "INSERT INTO CandidateMembership VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["date"], row["entity_id"], row["ticker"],
                    row["history_observations"], row.get("mean_dollar_volume_20d"),
                    row.get("combined_liquidity_rank"), row["universe_eligible"],
                    row["eligibility_reason"],
                )
                for row in membership
            ],
        )
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_candidate_price_date ON CandidatePrice(price_date);
            CREATE INDEX IF NOT EXISTS idx_candidate_membership_date ON CandidateMembership(price_date, universe_eligible);
            CREATE INDEX IF NOT EXISTS idx_alias_ticker ON TickerAlias(ticker);
            """
        )


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY is not set")
    if not args.source_db.exists():
        raise FileNotFoundError(args.source_db)
    registry = load_registry(args.candidate_dir)
    end_date = (date.fromisoformat(args.cutoff_exclusive) - timedelta(days=1)).isoformat()
    if args.start_date >= args.cutoff_exclusive:
        raise RuntimeError("Start date must precede cutoff")

    progress_path = args.output_dir / "progress.json"
    if args.resume and progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    else:
        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise RuntimeError("Output directory is non-empty; use --resume")
        progress = {
            "backfill_id": BACKFILL_ID,
            "status": "running",
            "started_at_utc": utc_now(),
            "updated_at_utc": utc_now(),
            "start_date": args.start_date,
            "cutoff_exclusive": args.cutoff_exclusive,
            "entity_count": len(registry),
            "completed_entities": [],
            "api_key_stored": False,
            "sealed_holdout_read": False,
            "source_database_modified": False,
        }
        write_json(progress_path, progress)

    event_cache_dir = args.output_dir / "cache" / "events"
    bar_cache_dir = args.output_dir / "cache" / "bars"
    event_cache_dir.mkdir(parents=True, exist_ok=True)
    bar_cache_dir.mkdir(parents=True, exist_ok=True)
    api_requests = 0
    event_errors = 0
    alias_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    chosen_bars: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_entity_dates = 0

    try:
        for entity_id, entity in registry.items():
            cache_key = safe_cache_name(entity_id)
            event_cache, requested = fetch_cached(
                event_cache_dir / f"{cache_key}.json",
                events_url(event_identifier(entity)),
                api_key,
                args,
                allow_error=True,
            )
            api_requests += int(requested)
            event_errors += int(event_cache.get("status") != "ok")
            events = extract_events(event_cache)
            for event in events:
                event_rows.append({"entity_id": entity_id, **event})
            entity_aliases = build_alias_rows(
                entity_id,
                entity["anchor_tickers"],
                events,
                args.start_date,
                end_date,
            )
            alias_rows.extend(entity_aliases)
            for alias in entity_aliases:
                ticker = alias["ticker"]
                bar_cache, requested = fetch_cached(
                    bar_cache_dir / f"{safe_cache_name(entity_id + ':' + ticker)}.json",
                    bars_url(ticker, args.start_date, end_date),
                    api_key,
                    args,
                    allow_error=False,
                )
                api_requests += int(requested)
                for bar in extract_bars(bar_cache, ticker):
                    if not (args.start_date <= bar["date"] < args.cutoff_exclusive):
                        continue
                    bar["entity_id"] = entity_id
                    key = (entity_id, bar["date"])
                    if key in chosen_bars:
                        duplicate_entity_dates += 1
                        chosen_bars[key] = choose_bar(chosen_bars[key], bar)
                    else:
                        chosen_bars[key] = bar
            if entity_id not in progress["completed_entities"]:
                progress["completed_entities"].append(entity_id)
            progress["updated_at_utc"] = utc_now()
            progress["api_requests_this_run"] = api_requests
            progress["last_entity_id"] = entity_id
            write_json(progress_path, progress)

        bars = sorted(chosen_bars.values(), key=lambda row: (row["date"], row["entity_id"]))
        membership, membership_meta = materialize_membership(
            args.source_db, bars, args.start_date, args.cutoff_exclusive
        )
        registry_rows = list(registry.values())
        write_database(
            args.output_dir / "candidate_backfill.db",
            registry_rows,
            alias_rows,
            event_rows,
            bars,
            membership,
        )

        registry_fields = [
            "entity_id", "name", "type", "primary_exchange", "cik",
            "composite_figi", "share_class_figi", "anchor_tickers", "anchor_dates",
        ]
        registry_csv_rows = [
            {**row, "anchor_tickers": "|".join(row["anchor_tickers"]), "anchor_dates": "|".join(row["anchor_dates"])}
            for row in registry_rows
        ]
        membership_fields = [
            "date", "entity_id", "ticker", "history_observations",
            "mean_dollar_volume_20d", "combined_liquidity_rank",
            "universe_eligible", "eligibility_reason",
        ]
        write_csv(args.output_dir / "entity_registry.csv", registry_csv_rows, registry_fields)
        write_csv(
            args.output_dir / "ticker_aliases.csv",
            alias_rows,
            ["entity_id", "ticker", "valid_from", "valid_to", "window_start", "window_end", "validity_source"],
        )
        write_csv(args.output_dir / "daily_candidate_membership.csv", membership, membership_fields)

        entities_with_bars = {row["entity_id"] for row in bars}
        eligible_rows = [row for row in membership if row["universe_eligible"] == 1]
        quality = {
            "entity_count": len(registry),
            "entities_with_bars": len(entities_with_bars),
            "entities_without_bars": len(registry) - len(entities_with_bars),
            "alias_count": len(alias_rows),
            "ticker_event_count": len(event_rows),
            "ticker_event_request_errors": event_errors,
            "candidate_price_rows": len(bars),
            "duplicate_entity_date_rows_resolved": duplicate_entity_dates,
            "null_close_rows": sum(row.get("close") is None for row in bars),
            "null_volume_rows": sum(row.get("volume") is None for row in bars),
            "membership_rows": len(membership),
            "eligible_membership_rows": len(eligible_rows),
            "eligible_entities": len({row["entity_id"] for row in eligible_rows}),
            "source_conflict_rows": membership_meta["source_conflict_rows"],
            "warmup_start": membership_meta["warmup_start"],
            "rows_on_or_after_cutoff": sum(row["date"] >= args.cutoff_exclusive for row in bars),
        }
        write_json(args.output_dir / "quality_summary.json", quality)
        readout = [
            "# Persistent candidate backfill",
            "",
            f"- Persistent candidate entities: {len(registry)}",
            f"- Entities with adjusted daily bars: {quality['entities_with_bars']}",
            f"- Ticker aliases: {quality['alias_count']}",
            f"- Ticker-change events: {quality['ticker_event_count']}",
            f"- Candidate price rows: {quality['candidate_price_rows']}",
            f"- Eligible daily membership rows: {quality['eligible_membership_rows']}",
            f"- Eligible entities: {quality['eligible_entities']}",
            f"- Resolved duplicate entity-dates: {quality['duplicate_entity_date_rows_resolved']}",
            "",
            "This database is staged evidence only. It must pass identifier, corporate-action, return-label, and source-ablation review before model use.",
        ]
        (args.output_dir / "backfill_readout.md").write_text("\n".join(readout) + "\n", encoding="utf-8")

        progress.update(
            {
                "status": "complete",
                "updated_at_utc": utc_now(),
                "completed_at_utc": utc_now(),
                "api_requests_this_run": api_requests,
                "quality_summary": quality,
            }
        )
        progress.pop("error", None)
        write_json(progress_path, progress)
        write_json(args.output_dir / "manifest.json", progress)
        print(json.dumps(progress, indent=2))
        return 0
    except Exception as exc:
        progress.update(
            {
                "status": "failed",
                "updated_at_utc": utc_now(),
                "api_requests_this_run": api_requests,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        write_json(progress_path, progress)
        raise


if __name__ == "__main__":
    sys.exit(main())
