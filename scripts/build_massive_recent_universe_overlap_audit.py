#!/usr/bin/env python3
"""Audit recent Robinhood-vs-Massive point-in-time universe coverage.

This is a source-quality audit, not a model run. It reads ResearchPrices in
SQLite, retrieves pre-holdout Massive reference snapshots and grouped daily
bars, checkpoints every API page, and writes sanitized comparison artifacts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


AUDIT_ID = "massive_recent_universe_overlap_audit_v1"
DEFAULT_ANCHORS = ("2024-09-03", "2025-09-02", "2026-05-28")
REFERENCE_FIELDS = (
    "ticker",
    "name",
    "type",
    "primary_exchange",
    "active",
    "currency_name",
    "cik",
    "composite_figi",
    "share_class_figi",
    "last_updated_utc",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path)
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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp_path.replace(path)


def without_api_key(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "apikey"
    ]
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


def with_api_key(url: str, api_key: str) -> str:
    parsed = urllib.parse.urlsplit(without_api_key(url))
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("apiKey", api_key))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


def request_json(
    url: str,
    api_key: str,
    delay: float,
    rate_limit_wait: float,
    retries: int,
) -> dict[str, Any]:
    for attempt in range(retries + 1):
        if delay > 0:
            time.sleep(delay)
        request = urllib.request.Request(
            with_api_key(url, api_key),
            headers={"User-Agent": "stockprediction2025-research/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = {}
            if exc.code == 429 and attempt < retries:
                time.sleep(rate_limit_wait)
                continue
            message = detail.get("error") or detail.get("message") or f"HTTP {exc.code}"
            raise RuntimeError(f"Massive HTTP {exc.code}: {message}") from exc
    raise AssertionError("Unreachable request loop")


def source_tickers(db_path: Path, anchor_date: str) -> set[str]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT ticker
            FROM ResearchPrices
            WHERE substr(begins_at, 1, 10) = ?
            ORDER BY ticker
            """,
            (anchor_date,),
        ).fetchall()
    return {normalize_ticker(row[0]) for row in rows if normalize_ticker(row[0])}


def reference_start_url(anchor_date: str) -> str:
    query = urllib.parse.urlencode(
        {
            "market": "stocks",
            "date": anchor_date,
            "active": "true",
            "order": "asc",
            "limit": "1000",
            "sort": "ticker",
        }
    )
    return "https://api.massive.com/v3/reference/tickers?" + query


def grouped_url(anchor_date: str) -> str:
    return (
        "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/"
        f"{anchor_date}?adjusted=false&include_otc=false"
    )


def fetch_reference_snapshot(
    anchor_date: str,
    cache_dir: Path,
    api_key: str,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], int]:
    page_dir = cache_dir / anchor_date / "reference_pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    next_url: str | None = reference_start_url(anchor_date)
    page_number = 1

    while next_url:
        page_path = page_dir / f"page_{page_number:04d}.json"
        if page_path.exists():
            cached = json.loads(page_path.read_text(encoding="utf-8"))
        else:
            payload = request_json(
                next_url,
                api_key,
                args.request_delay_seconds,
                args.rate_limit_wait_seconds,
                args.max_rate_limit_retries,
            )
            cached = {
                "request_id": payload.get("request_id"),
                "results": payload.get("results") or [],
                "next_url": without_api_key(payload["next_url"]) if payload.get("next_url") else None,
            }
            write_json(page_path, cached)
        records.extend(cached.get("results") or [])
        next_url = cached.get("next_url")
        page_number += 1

    return records, page_number - 1


def fetch_grouped_snapshot(
    anchor_date: str,
    cache_dir: Path,
    api_key: str,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    cache_path = cache_dir / anchor_date / "grouped_bars.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))["results"]
    payload = request_json(
        grouped_url(anchor_date),
        api_key,
        args.request_delay_seconds,
        args.rate_limit_wait_seconds,
        args.max_rate_limit_retries,
    )
    cached = {
        "request_id": payload.get("request_id"),
        "results_count": len(payload.get("results") or []),
        "results": payload.get("results") or [],
    }
    write_json(cache_path, cached)
    return cached["results"]


def normalized_reference(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record.get(field) for field in REFERENCE_FIELDS} | {
        "ticker": normalize_ticker(record.get("ticker"))
    }


def reference_index(records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], int]:
    index: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for raw in records:
        record = normalized_reference(raw)
        ticker = record["ticker"]
        if not ticker:
            continue
        if ticker in index:
            duplicates += 1
        index[ticker] = record
    return index, duplicates


def relation_row(
    anchor_date: str,
    ticker: str,
    relation: str,
    reference: dict[str, Any] | None,
) -> dict[str, Any]:
    record = reference or {}
    return {
        "anchor_date": anchor_date,
        "ticker": ticker,
        "relation": relation,
        "name": record.get("name"),
        "type": record.get("type") or "UNMAPPED",
        "primary_exchange": record.get("primary_exchange"),
        "active": record.get("active"),
        "cik": record.get("cik"),
        "composite_figi": record.get("composite_figi"),
        "share_class_figi": record.get("share_class_figi"),
    }


def entity_key(record: dict[str, Any]) -> str:
    return str(
        record.get("share_class_figi")
        or record.get("composite_figi")
        or ("CIK:" + str(record["cik"]) if record.get("cik") else "TICKER:" + record["ticker"])
    )


def write_readout(path: Path, summaries: list[dict[str, Any]], output_dir: Path) -> None:
    lines = [
        "# Massive recent universe overlap audit",
        "",
        "This source-quality audit compares the stored Robinhood research universe with Massive point-in-time active tickers and non-OTC traded symbols. It does not train a model or read the sealed holdout.",
        "",
        "| Anchor | Robinhood source | Massive traded | Overlap | Source coverage | Massive-only CS candidates | Source-only |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            "| {anchor_date} | {source_count} | {massive_traded_count} | {intersection_count} | {source_coverage_rate:.2%} | {provider_only_common_stock_count} | {source_only_count} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            "- A Massive-only common stock is a repair candidate, not automatically a confirmed omission; identifier and listing-history review is still required.",
            "- A source-only symbol can reflect a no-trade day, symbol formatting difference, unsupported venue, or stale Robinhood instrument rather than an error.",
            "- The three anchors measure structural overlap and churn; they are not yet a daily point-in-time universe.",
            "- Stable FIGI and CIK identifiers should drive symbol-change reconciliation before any backfill joins.",
            "",
            f"Artifacts are rooted at `{output_dir}`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY is not set")
    if not args.source_db.exists():
        raise FileNotFoundError(f"Source database not found: {args.source_db}")

    anchors = sorted(set(args.anchor_dates or DEFAULT_ANCHORS))
    cutoff = parse_iso_date(args.cutoff_exclusive)
    for anchor in anchors:
        if parse_iso_date(anchor) >= cutoff:
            raise RuntimeError(f"Anchor {anchor} is not before sealed holdout cutoff {cutoff}")

    progress_path = args.output_dir / "progress.json"
    if args.resume and progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    else:
        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise RuntimeError("Output directory is non-empty; use --resume")
        progress = {
            "audit_id": AUDIT_ID,
            "status": "running",
            "started_at_utc": utc_now(),
            "updated_at_utc": utc_now(),
            "anchors": anchors,
            "completed_anchors": [],
            "api_key_stored": False,
            "sealed_holdout_read": False,
        }
        write_json(progress_path, progress)

    cache_dir = args.output_dir / "cache"
    summaries: list[dict[str, Any]] = []
    relation_rows: list[dict[str, Any]] = []
    type_rows: list[dict[str, Any]] = []
    reference_by_date: dict[str, dict[str, dict[str, Any]]] = {}

    try:
        for anchor in anchors:
            source = source_tickers(args.source_db, anchor)
            references, page_count = fetch_reference_snapshot(anchor, cache_dir, api_key, args)
            bars = fetch_grouped_snapshot(anchor, cache_dir, api_key, args)
            reference, duplicate_reference_tickers = reference_index(references)
            reference_by_date[anchor] = reference
            massive_traded = {
                normalize_ticker(record.get("T"))
                for record in bars
                if normalize_ticker(record.get("T"))
            }
            overlap = source & massive_traded
            provider_only = massive_traded - source
            source_only = source - massive_traded
            provider_only_cs = {ticker for ticker in provider_only if reference.get(ticker, {}).get("type") == "CS"}

            for ticker in sorted(provider_only):
                relation_rows.append(relation_row(anchor, ticker, "massive_traded_only", reference.get(ticker)))
            for ticker in sorted(source_only):
                relation_rows.append(relation_row(anchor, ticker, "robinhood_source_only", reference.get(ticker)))

            relation_type_counts = Counter(
                (row["relation"], row["type"])
                for row in relation_rows
                if row["anchor_date"] == anchor
            )
            for (relation, ticker_type), count in sorted(relation_type_counts.items()):
                type_rows.append(
                    {
                        "anchor_date": anchor,
                        "relation": relation,
                        "type": ticker_type,
                        "count": count,
                    }
                )

            union = source | massive_traded
            summary = {
                "anchor_date": anchor,
                "source_count": len(source),
                "massive_reference_active_count": len(reference),
                "massive_traded_count": len(massive_traded),
                "intersection_count": len(overlap),
                "provider_only_count": len(provider_only),
                "provider_only_common_stock_count": len(provider_only_cs),
                "source_only_count": len(source_only),
                "source_in_active_reference_count": len(source & set(reference)),
                "source_coverage_rate": len(overlap) / len(source) if source else 0.0,
                "jaccard_rate": len(overlap) / len(union) if union else 0.0,
                "reference_page_count": page_count,
                "duplicate_reference_ticker_count": duplicate_reference_tickers,
            }
            summaries.append(summary)
            if anchor not in progress["completed_anchors"]:
                progress["completed_anchors"].append(anchor)
            progress["updated_at_utc"] = utc_now()
            progress["last_summary"] = summary
            write_json(progress_path, progress)

        churn_index: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"dates": set(), "tickers": set(), "types": set(), "names": set()}
        )
        for anchor, reference in reference_by_date.items():
            for record in reference.values():
                entity = churn_index[entity_key(record)]
                entity["dates"].add(anchor)
                entity["tickers"].add(record["ticker"])
                entity["types"].add(record.get("type") or "UNMAPPED")
                entity["names"].add(record.get("name") or "")
        first_anchor, last_anchor = anchors[0], anchors[-1]
        churn_rows = []
        for entity, values in churn_index.items():
            dates_seen = sorted(values["dates"])
            tickers = sorted(values["tickers"])
            churn_rows.append(
                {
                    "entity_id": entity,
                    "first_anchor_seen": dates_seen[0],
                    "last_anchor_seen": dates_seen[-1],
                    "present_first_anchor": first_anchor in values["dates"],
                    "present_last_anchor": last_anchor in values["dates"],
                    "ticker_count": len(tickers),
                    "tickers": "|".join(tickers),
                    "types": "|".join(sorted(values["types"])),
                    "names": "|".join(sorted(name for name in values["names"] if name)),
                    "candidate_departure": first_anchor in values["dates"] and last_anchor not in values["dates"],
                    "candidate_entry": first_anchor not in values["dates"] and last_anchor in values["dates"],
                    "candidate_symbol_change": len(tickers) > 1,
                }
            )

        summary_fields = list(summaries[0]) if summaries else []
        relation_fields = [
            "anchor_date", "ticker", "relation", "name", "type", "primary_exchange",
            "active", "cik", "composite_figi", "share_class_figi",
        ]
        churn_fields = [
            "entity_id", "first_anchor_seen", "last_anchor_seen", "present_first_anchor",
            "present_last_anchor", "ticker_count", "tickers", "types", "names",
            "candidate_departure", "candidate_entry", "candidate_symbol_change",
        ]
        write_csv(args.output_dir / "anchor_summary.csv", summaries, summary_fields)
        write_csv(args.output_dir / "universe_mismatches.csv", relation_rows, relation_fields)
        write_csv(args.output_dir / "mismatch_type_summary.csv", type_rows, ["anchor_date", "relation", "type", "count"])
        write_csv(args.output_dir / "reference_entity_churn.csv", churn_rows, churn_fields)
        write_readout(args.output_dir / "audit_readout.md", summaries, args.output_dir)

        progress.update(
            {
                "status": "complete",
                "updated_at_utc": utc_now(),
                "completed_at_utc": utc_now(),
                "summary": summaries,
                "mismatch_row_count": len(relation_rows),
                "entity_churn_row_count": len(churn_rows),
                "candidate_departure_count": sum(bool(row["candidate_departure"]) for row in churn_rows),
                "candidate_entry_count": sum(bool(row["candidate_entry"]) for row in churn_rows),
                "candidate_symbol_change_count": sum(bool(row["candidate_symbol_change"]) for row in churn_rows),
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
