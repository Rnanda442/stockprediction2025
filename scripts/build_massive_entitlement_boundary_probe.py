#!/usr/bin/env python3
"""Locate the earliest Massive daily-bar date available to the current API key.

The probe uses one-symbol requests during a binary search, checkpoints after
every request, and optionally confirms the discovered boundary with one grouped
all-market request. It never writes the API key or reads model/holdout data.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROBE_ID = "massive_entitlement_boundary_probe_v1"
PROVIDER = "Massive (Polygon API compatibility)"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--lower-date", required=True)
    parser.add_argument("--upper-date", required=True)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--request-delay-seconds", type=float, default=15.0)
    parser.add_argument("--rate-limit-wait-seconds", type=float, default=65.0)
    parser.add_argument("--max-rate-limit-retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify-grouped", action="store_true")
    return parser.parse_args()


def load_trading_dates(db_path: Path, lower: str, upper: str) -> list[str]:
    if not db_path.exists():
        raise FileNotFoundError(f"Source database not found: {db_path}")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT substr(begins_at, 1, 10) AS trading_date
            FROM ResearchPrices
            WHERE substr(begins_at, 1, 10) >= ?
              AND substr(begins_at, 1, 10) <= ?
            ORDER BY trading_date
            """,
            (lower, upper),
        ).fetchall()
    dates = [str(row[0]) for row in rows if row[0]]
    if len(dates) < 2:
        raise RuntimeError("Need at least two source trading dates in the requested range")
    return dates


def sanitized_error(payload: dict[str, Any], fallback: str) -> str:
    return str(payload.get("error") or payload.get("message") or fallback)


def request_json(url: str, delay: float, wait: float, retries: int) -> dict[str, Any]:
    for attempt in range(retries + 1):
        if delay > 0:
            time.sleep(delay)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "stockprediction2025-research/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return {
                "http_status": int(response.status),
                "provider_status": payload.get("status"),
                "request_id": payload.get("request_id"),
                "rows": len(payload.get("results") or []),
            }
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
            if exc.code == 429 and attempt < retries:
                time.sleep(wait)
                continue
            return {
                "http_status": int(exc.code),
                "provider_status": payload.get("status"),
                "request_id": payload.get("request_id"),
                "error": sanitized_error(payload, f"HTTP {exc.code}"),
                "rows": 0,
            }
        except Exception as exc:  # Network failures remain resumable evidence.
            return {
                "http_status": None,
                "provider_status": None,
                "error": f"{type(exc).__name__}: {exc}",
                "rows": 0,
            }
    raise AssertionError("Unreachable request loop")


def classify(result: dict[str, Any]) -> str:
    status = result.get("http_status")
    message = str(result.get("error") or "").lower()
    if status == 200:
        return "available"
    if status == 403 and (
        "historical entitlement" in message
        or "not_authorized" in message
        or "not authorized" in message
        or "doesn't include this data timeframe" in message
        or "does not include this data timeframe" in message
    ):
        return "unavailable"
    if status == 429:
        return "rate_limited"
    return "inconclusive"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def single_symbol_url(symbol: str, trading_date: str, api_key: str) -> str:
    ticker = urllib.parse.quote(symbol.upper(), safe="")
    return (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{trading_date}/{trading_date}?adjusted=false&sort=asc&limit=1&apiKey="
        + urllib.parse.quote(api_key, safe="")
    )


def grouped_url(trading_date: str, api_key: str) -> str:
    return (
        "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/"
        f"{trading_date}?adjusted=false&include_otc=false&apiKey="
        + urllib.parse.quote(api_key, safe="")
    )


def initial_state(args: argparse.Namespace, dates: list[str]) -> dict[str, Any]:
    return {
        "probe_id": PROBE_ID,
        "provider": PROVIDER,
        "started_at_utc": utc_now(),
        "updated_at_utc": utc_now(),
        "status": "running",
        "source_db": str(args.source_db),
        "lower_date": dates[0],
        "upper_date": dates[-1],
        "symbol": args.symbol.upper(),
        "trading_date_count": len(dates),
        "sealed_holdout_read": False,
        "api_key_stored": False,
        "assumption": "Historical daily-bar entitlement is monotonic by date.",
        "seed_evidence": [
            {"date": dates[0], "classification": "unavailable", "source": "prior grouped probe"},
            {"date": dates[-1], "classification": "available", "source": "prior grouped probe"},
        ],
        "requests": [],
    }


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY is not set")

    dates = load_trading_dates(args.source_db, args.lower_date, args.upper_date)
    progress_path = args.output_dir / "progress.json"
    manifest_path = args.output_dir / "manifest.json"
    if args.resume and progress_path.exists():
        state = json.loads(progress_path.read_text(encoding="utf-8"))
    else:
        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise RuntimeError("Output directory is non-empty; use --resume")
        state = initial_state(args, dates)
        write_json(progress_path, state)

    prior = {}
    for item in state.get("requests", []):
        recovered_classification = classify(item)
        if recovered_classification in {"available", "unavailable"}:
            item["classification"] = recovered_classification
            prior[item["date"]] = recovered_classification
    lower_index = 0
    upper_index = len(dates) - 1
    for index, trading_date in enumerate(dates):
        if prior.get(trading_date) == "unavailable":
            lower_index = max(lower_index, index)
        elif prior.get(trading_date) == "available":
            upper_index = min(upper_index, index)

    while upper_index - lower_index > 1:
        middle_index = (lower_index + upper_index) // 2
        trading_date = dates[middle_index]
        result = request_json(
            single_symbol_url(args.symbol, trading_date, api_key),
            args.request_delay_seconds,
            args.rate_limit_wait_seconds,
            args.max_rate_limit_retries,
        )
        result.update(
            {
                "date": trading_date,
                "endpoint": "single_symbol_daily_bar",
                "symbol": args.symbol.upper(),
                "classification": classify(result),
                "checked_at_utc": utc_now(),
            }
        )
        state["requests"].append(result)
        state["updated_at_utc"] = utc_now()
        state["current_unavailable_date"] = dates[lower_index]
        state["current_available_date"] = dates[upper_index]
        write_json(progress_path, state)

        if result["classification"] == "available":
            upper_index = middle_index
        elif result["classification"] == "unavailable":
            lower_index = middle_index
        else:
            state["status"] = "inconclusive"
            state["blocking_result"] = result
            write_json(progress_path, state)
            print(json.dumps(state, indent=2))
            return 2

    boundary_date = dates[upper_index]
    previous_date = dates[lower_index]
    grouped_verification = None
    if args.verify_grouped:
        grouped_verification = request_json(
            grouped_url(boundary_date, api_key),
            args.request_delay_seconds,
            args.rate_limit_wait_seconds,
            args.max_rate_limit_retries,
        )
        grouped_verification.update(
            {
                "date": boundary_date,
                "endpoint": "grouped_daily_bars",
                "classification": classify(grouped_verification),
                "checked_at_utc": utc_now(),
            }
        )
        state["requests"].append(grouped_verification)

    verified = grouped_verification is None or grouped_verification["classification"] == "available"
    state.update(
        {
            "updated_at_utc": utc_now(),
            "completed_at_utc": utc_now(),
            "status": "complete" if verified else "inconclusive",
            "latest_unavailable_trading_date": previous_date,
            "earliest_available_trading_date": boundary_date,
            "grouped_verification": grouped_verification,
            "request_count_this_run": len(state.get("requests", [])),
        }
    )
    write_json(progress_path, state)
    write_json(manifest_path, state)
    print(json.dumps(state, indent=2))
    return 0 if verified else 2


if __name__ == "__main__":
    sys.exit(main())
