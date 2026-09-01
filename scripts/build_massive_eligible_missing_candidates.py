#!/usr/bin/env python3
"""Build point-in-time eligible missing-stock candidates from Massive caches.

The project liquidity rule is reproduced exactly: require 20 trailing trading
dates, compute mean close times non-negative volume, and rank descending with a
stable ticker tie-break. No model, target, or sealed-holdout data is read.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from build_massive_recent_universe_overlap_audit import (
    fetch_grouped_snapshot,
    reference_index,
    source_tickers,
    write_csv,
    write_json,
)


CANDIDATE_ID = "massive_eligible_missing_candidates_v1"
DEFAULT_ANCHORS = ("2025-09-02", "2026-05-28")
TRAILING_DATES = 20
TOP_LIQUIDITY = 1000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--audit-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--anchor-date", action="append", dest="anchor_dates")
    parser.add_argument("--cutoff-exclusive", default="2026-05-29")
    parser.add_argument("--request-delay-seconds", type=float, default=15.0)
    parser.add_argument("--rate-limit-wait-seconds", type=float, default=65.0)
    parser.add_argument("--max-rate-limit-retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def trading_window(db_path: Path, anchor: str, length: int) -> list[str]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            """
            SELECT trading_date
            FROM (
                SELECT DISTINCT substr(begins_at, 1, 10) AS trading_date
                FROM ResearchPrices
                WHERE substr(begins_at, 1, 10) <= ?
                ORDER BY trading_date DESC
                LIMIT ?
            )
            ORDER BY trading_date
            """,
            (anchor, length),
        ).fetchall()
    dates = [str(row[0]) for row in rows]
    if len(dates) != length or dates[-1] != anchor:
        raise RuntimeError(f"Expected {length} source trading dates ending {anchor}; got {dates}")
    return dates


def load_reference(audit_dir: Path, anchor: str) -> tuple[dict[str, dict[str, Any]], int]:
    page_dir = audit_dir / "cache" / anchor / "reference_pages"
    pages = sorted(page_dir.glob("page_*.json"))
    if not pages:
        raise RuntimeError(f"No cached reference pages for {anchor}: {page_dir}")
    records: list[dict[str, Any]] = []
    for page in pages:
        records.extend(json.loads(page.read_text(encoding="utf-8")).get("results") or [])
    return reference_index(records)


def load_or_fetch_bars(
    trading_date: str,
    audit_dir: Path,
    output_dir: Path,
    api_key: str,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    audit_cache = audit_dir / "cache" / trading_date / "grouped_bars.json"
    if audit_cache.exists():
        return json.loads(audit_cache.read_text(encoding="utf-8"))["results"]
    return fetch_grouped_snapshot(trading_date, output_dir / "cache", api_key, args)


def deduplicate_bars(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], int]:
    chosen: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for row in rows:
        ticker = normalize_ticker(row.get("T"))
        if not ticker:
            continue
        if ticker in chosen:
            duplicates += 1
            old_key = (float(chosen[ticker].get("n") or -1), float(chosen[ticker].get("v") or -1))
            new_key = (float(row.get("n") or -1), float(row.get("v") or -1))
            if new_key > old_key:
                chosen[ticker] = row
        else:
            chosen[ticker] = row
    return chosen, duplicates


def stable_ranks(values: dict[str, float]) -> dict[str, int]:
    ordered = sorted(values, key=lambda ticker: (-values[ticker], ticker))
    return {ticker: index + 1 for index, ticker in enumerate(ordered)}


def entity_id(reference: dict[str, Any], ticker: str) -> str:
    if reference.get("share_class_figi"):
        return str(reference["share_class_figi"])
    if reference.get("composite_figi"):
        return str(reference["composite_figi"])
    if reference.get("cik"):
        return "CIK:" + str(reference["cik"])
    return "TICKER:" + ticker


def candidate_row(
    anchor: str,
    ticker: str,
    candidate_set: str,
    mean_dollar_volume: float,
    rank_all: int | None,
    rank_common_stock: int | None,
    anchor_bar: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    return {
        "anchor_date": anchor,
        "ticker": ticker,
        "entity_id": entity_id(reference, ticker),
        "candidate_set": candidate_set,
        "mean_dollar_volume_20d": mean_dollar_volume,
        "liquidity_rank_all_market": rank_all,
        "liquidity_rank_common_stock": rank_common_stock,
        "anchor_close": anchor_bar.get("c"),
        "anchor_volume": anchor_bar.get("v"),
        "name": reference.get("name"),
        "type": reference.get("type"),
        "primary_exchange": reference.get("primary_exchange"),
        "cik": reference.get("cik"),
        "composite_figi": reference.get("composite_figi"),
        "share_class_figi": reference.get("share_class_figi"),
    }


def write_readout(path: Path, summaries: list[dict[str, Any]], persistent: list[dict[str, Any]]) -> None:
    lines = [
        "# Massive eligible missing-stock candidates",
        "",
        "This audit reproduces the model panel's past-only liquidity rule: 20 complete trailing trading dates, mean close times volume, and a stable descending top-1,000 rank.",
        "",
        "| Anchor | Complete-history symbols | Source overlap in all-market top 1,000 | Missing CS in all-market top 1,000 | Missing CS in CS-only top 1,000 | Duplicate rows removed |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            "| {anchor_date} | {complete_history_count} | {source_overlap_top_1000_all} | {missing_cs_top_1000_all} | {missing_cs_top_1000_cs} | {duplicate_bar_rows_removed} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            f"Persistent conservative candidates across every anchor: **{len(persistent)}**.",
            "",
            "## Guardrails",
            "",
            "- Conservative candidates are common stocks inside the all-security top-1,000 liquidity rank and absent from the stored Robinhood source on that date.",
            "- The CS-only top-1,000 set is a sensitivity analysis, not the default repair universe.",
            "- Stable FIGI/CIK identifiers must be used before joining historical prices across ticker changes.",
            "- These candidates require downstream listing-event and return-label review before model use.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY is not set")
    if not args.source_db.exists():
        raise FileNotFoundError(args.source_db)
    if not args.audit_dir.exists():
        raise FileNotFoundError(args.audit_dir)

    anchors = sorted(set(args.anchor_dates or DEFAULT_ANCHORS))
    cutoff = parse_iso_date(args.cutoff_exclusive)
    if any(parse_iso_date(anchor) >= cutoff for anchor in anchors):
        raise RuntimeError(f"Every anchor must be before sealed holdout cutoff {cutoff}")

    progress_path = args.output_dir / "progress.json"
    if args.resume and progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    else:
        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise RuntimeError("Output directory is non-empty; use --resume")
        progress = {
            "candidate_id": CANDIDATE_ID,
            "status": "running",
            "started_at_utc": utc_now(),
            "updated_at_utc": utc_now(),
            "anchors": anchors,
            "completed_anchors": [],
            "trailing_dates": TRAILING_DATES,
            "top_liquidity": TOP_LIQUIDITY,
            "api_key_stored": False,
            "sealed_holdout_read": False,
        }
        write_json(progress_path, progress)

    summaries: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    conservative_entities_by_anchor: dict[str, set[str]] = {}

    try:
        for anchor in anchors:
            reference, duplicate_reference_tickers = load_reference(args.audit_dir, anchor)
            source = source_tickers(args.source_db, anchor)
            window = trading_window(args.source_db, anchor, TRAILING_DATES)
            dollar_volume_history: dict[str, list[float]] = defaultdict(list)
            anchor_bars: dict[str, dict[str, Any]] = {}
            duplicate_bar_rows = 0

            for trading_date in window:
                raw_bars = load_or_fetch_bars(
                    trading_date, args.audit_dir, args.output_dir, api_key, args
                )
                bars, duplicates = deduplicate_bars(raw_bars)
                duplicate_bar_rows += duplicates
                if trading_date == anchor:
                    anchor_bars = bars
                for ticker, row in bars.items():
                    close = row.get("c")
                    volume = row.get("v")
                    if close is None or volume is None:
                        continue
                    dollar_volume_history[ticker].append(
                        float(close) * max(float(volume), 0.0)
                    )

            mean_dollar_volume = {
                ticker: sum(values) / TRAILING_DATES
                for ticker, values in dollar_volume_history.items()
                if len(values) == TRAILING_DATES
            }
            rank_all = stable_ranks(mean_dollar_volume)
            common_stock_values = {
                ticker: value
                for ticker, value in mean_dollar_volume.items()
                if reference.get(ticker, {}).get("type") == "CS"
            }
            rank_cs = stable_ranks(common_stock_values)

            conservative = sorted(
                ticker
                for ticker, rank in rank_all.items()
                if rank <= TOP_LIQUIDITY
                and ticker not in source
                and reference.get(ticker, {}).get("type") == "CS"
            )
            cs_sensitivity = sorted(
                ticker
                for ticker, rank in rank_cs.items()
                if rank <= TOP_LIQUIDITY and ticker not in source
            )
            conservative_set = set(conservative)
            for ticker in conservative:
                candidates.append(
                    candidate_row(
                        anchor,
                        ticker,
                        "conservative_all_market_top1000",
                        mean_dollar_volume[ticker],
                        rank_all[ticker],
                        rank_cs.get(ticker),
                        anchor_bars.get(ticker, {}),
                        reference[ticker],
                    )
                )
            for ticker in cs_sensitivity:
                if ticker in conservative_set:
                    continue
                candidates.append(
                    candidate_row(
                        anchor,
                        ticker,
                        "common_stock_top1000_sensitivity",
                        mean_dollar_volume[ticker],
                        rank_all.get(ticker),
                        rank_cs[ticker],
                        anchor_bars.get(ticker, {}),
                        reference[ticker],
                    )
                )

            conservative_entities_by_anchor[anchor] = {
                entity_id(reference[ticker], ticker) for ticker in conservative
            }
            top_all = {ticker for ticker, rank in rank_all.items() if rank <= TOP_LIQUIDITY}
            summary = {
                "anchor_date": anchor,
                "window_start": window[0],
                "window_end": window[-1],
                "complete_history_count": len(mean_dollar_volume),
                "source_overlap_top_1000_all": len(top_all & source),
                "missing_cs_top_1000_all": len(conservative),
                "missing_cs_top_1000_cs": len(cs_sensitivity),
                "duplicate_bar_rows_removed": duplicate_bar_rows,
                "duplicate_reference_tickers": duplicate_reference_tickers,
            }
            summaries.append(summary)
            if anchor not in progress["completed_anchors"]:
                progress["completed_anchors"].append(anchor)
            progress["last_summary"] = summary
            progress["updated_at_utc"] = utc_now()
            write_json(progress_path, progress)

        persistent_entities = set.intersection(*conservative_entities_by_anchor.values())
        candidate_by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in candidates:
            if row["candidate_set"] == "conservative_all_market_top1000":
                candidate_by_entity[row["entity_id"]].append(row)
        persistent = []
        for entity in sorted(persistent_entities):
            rows = candidate_by_entity[entity]
            persistent.append(
                {
                    "entity_id": entity,
                    "anchor_count": len(rows),
                    "tickers": "|".join(sorted({str(row["ticker"]) for row in rows})),
                    "names": "|".join(sorted({str(row["name"]) for row in rows if row.get("name")})),
                    "min_mean_dollar_volume_20d": min(float(row["mean_dollar_volume_20d"]) for row in rows),
                    "max_mean_dollar_volume_20d": max(float(row["mean_dollar_volume_20d"]) for row in rows),
                    "best_all_market_rank": min(int(row["liquidity_rank_all_market"]) for row in rows),
                    "worst_all_market_rank": max(int(row["liquidity_rank_all_market"]) for row in rows),
                }
            )

        candidate_fields = [
            "anchor_date", "ticker", "entity_id", "candidate_set",
            "mean_dollar_volume_20d", "liquidity_rank_all_market",
            "liquidity_rank_common_stock", "anchor_close", "anchor_volume", "name",
            "type", "primary_exchange", "cik", "composite_figi", "share_class_figi",
        ]
        summary_fields = list(summaries[0]) if summaries else []
        persistent_fields = [
            "entity_id", "anchor_count", "tickers", "names",
            "min_mean_dollar_volume_20d", "max_mean_dollar_volume_20d",
            "best_all_market_rank", "worst_all_market_rank",
        ]
        write_csv(args.output_dir / "anchor_candidate_summary.csv", summaries, summary_fields)
        write_csv(args.output_dir / "eligible_missing_candidates.csv", candidates, candidate_fields)
        write_csv(args.output_dir / "persistent_conservative_candidates.csv", persistent, persistent_fields)
        write_readout(args.output_dir / "candidate_readout.md", summaries, persistent)

        progress.update(
            {
                "status": "complete",
                "updated_at_utc": utc_now(),
                "completed_at_utc": utc_now(),
                "summary": summaries,
                "candidate_row_count": len(candidates),
                "persistent_conservative_entity_count": len(persistent),
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
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        write_json(progress_path, progress)
        raise


if __name__ == "__main__":
    sys.exit(main())
