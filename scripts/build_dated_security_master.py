#!/usr/bin/env python3
"""Build a source-aware security master without inventing historical lineage.

Observed price coverage and current classifications are retained as provisional
records. Only externally supplied, explicitly dated, confirmed SEC, FINRA, or
Nasdaq events may be marked point-in-time safe.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


DESIGN_SIGNATURE = (
    "dated-security-master-v1:observed-lifecycle+authoritative-events:"
    "source-confidence+effective-dates:no-inferred-delistings:no-current-sector-backfill"
)
TRUSTED_AUTHORITIES = {"SEC", "FINRA", "NASDAQ"}
EVENT_FIELDS = {
    "event_type",
    "effective_date",
    "ticker",
    "prior_ticker",
    "issuer_name",
    "exchange",
    "source_url",
    "source_authority",
    "match_confidence",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-audit", required=True, type=Path)
    parser.add_argument("--sector-audit", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--events",
        action="append",
        default=[],
        type=Path,
        help="Optional CSV using the authoritative event contract; repeatable.",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def stable_security_id(ticker: str, first_seen: str) -> str:
    digest = hashlib.sha256(f"{ticker}|{first_seen}".encode("utf-8")).hexdigest()[:16]
    return f"observed-{digest}"


def safe_event(row: dict[str, str]) -> tuple[int, str]:
    authority = row.get("source_authority", "").strip().upper()
    confidence = row.get("match_confidence", "").strip().lower()
    effective_date = row.get("effective_date", "").strip()
    try:
        date.fromisoformat(effective_date)
        dated = True
    except ValueError:
        dated = False
    if authority not in TRUSTED_AUTHORITIES:
        return 0, "untrusted_or_missing_authority"
    if confidence != "confirmed":
        return 0, "match_not_confirmed"
    if not dated:
        return 0, "missing_or_invalid_effective_date"
    return 1, "authoritative_dated_confirmed"


def main() -> None:
    args = parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    lifecycle = read_csv(args.lifecycle_audit)
    sectors = {row["ticker"]: row for row in read_csv(args.sector_audit)}
    if not lifecycle:
        raise RuntimeError("lifecycle audit is empty")

    event_rows: list[dict[str, str]] = []
    for event_path in args.events:
        rows = read_csv(event_path)
        if rows and not EVENT_FIELDS.issubset(rows[0]):
            raise ValueError(
                f"{event_path} is missing event fields: {sorted(EVENT_FIELDS - set(rows[0]))}"
            )
        for row in rows:
            row["input_file"] = str(event_path.resolve())
            event_rows.append(row)

    db_path = args.output_dir / "security_master.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE observed_security (
            security_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            observed_first_date TEXT NOT NULL,
            observed_last_date TEXT NOT NULL,
            observed_dates INTEGER NOT NULL,
            coverage_ratio REAL NOT NULL,
            left_censored INTEGER NOT NULL,
            right_censored INTEGER NOT NULL,
            confirmed_listing_date TEXT,
            confirmed_delisting_date TEXT,
            lifecycle_point_in_time_verified INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE symbol_lineage (
            security_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            effective_from TEXT,
            effective_to TEXT,
            source_authority TEXT NOT NULL,
            source_url TEXT,
            match_confidence TEXT NOT NULL,
            point_in_time_safe INTEGER NOT NULL,
            safety_reason TEXT NOT NULL
        );
        CREATE TABLE classification_lineage (
            security_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            sector TEXT,
            industry TEXT,
            effective_from TEXT,
            effective_to TEXT,
            source_authority TEXT NOT NULL,
            point_in_time_safe INTEGER NOT NULL,
            safety_reason TEXT NOT NULL
        );
        CREATE TABLE corporate_action_event (
            event_type TEXT NOT NULL,
            effective_date TEXT,
            ticker TEXT,
            prior_ticker TEXT,
            issuer_name TEXT,
            exchange_name TEXT,
            source_url TEXT,
            source_authority TEXT,
            match_confidence TEXT,
            point_in_time_safe INTEGER NOT NULL,
            safety_reason TEXT NOT NULL,
            input_file TEXT
        );
        CREATE TABLE coverage_gap (
            gap_type TEXT PRIMARY KEY,
            affected_tickers INTEGER NOT NULL,
            sample_tickers TEXT NOT NULL,
            model_feature_blocked INTEGER NOT NULL,
            unblock_condition TEXT NOT NULL
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )

    security_rows = []
    symbol_rows = []
    classification_rows = []
    ticker_to_id: dict[str, str] = {}
    for row in lifecycle:
        ticker = row["ticker"].strip().upper()
        security_id = stable_security_id(ticker, row["first_seen"])
        ticker_to_id[ticker] = security_id
        security_rows.append(
            (
                security_id,
                ticker,
                row["first_seen"],
                row["last_seen"],
                int(row["observed_dates"]),
                float(row["coverage_ratio"]),
                int(row["left_censored_at_dataset_start"]),
                int(row["right_censored_at_training_end"]),
                None,
                None,
                0,
            )
        )
        symbol_rows.append(
            (
                security_id,
                ticker,
                row["first_seen"],
                row["last_seen"],
                "OBSERVED_PRICE_COVERAGE",
                None,
                "provisional",
                0,
                "observed dates do not prove listing or delisting dates",
            )
        )
        sector = sectors.get(ticker, {})
        if sector.get("current_profile_available") == "1":
            classification_rows.append(
                (
                    security_id,
                    ticker,
                    sector.get("current_sector", ""),
                    sector.get("current_industry", ""),
                    None,
                    None,
                    "NASDAQ_CURRENT_SNAPSHOT",
                    0,
                    "undated current classification cannot be backfilled historically",
                )
            )

    connection.executemany(
        "INSERT INTO observed_security VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        security_rows,
    )
    connection.executemany(
        "INSERT INTO symbol_lineage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        symbol_rows,
    )
    connection.executemany(
        "INSERT INTO classification_lineage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        classification_rows,
    )

    safe_event_count = 0
    matched_safe_tickers: set[str] = set()
    event_type_counts: Counter[str] = Counter()
    for row in event_rows:
        point_in_time_safe, reason = safe_event(row)
        safe_event_count += point_in_time_safe
        ticker = row.get("ticker", "").strip().upper()
        prior_ticker = row.get("prior_ticker", "").strip().upper()
        event_type = row.get("event_type", "").strip().lower()
        event_type_counts[event_type] += 1
        if point_in_time_safe and (ticker in ticker_to_id or prior_ticker in ticker_to_id):
            matched_safe_tickers.update(
                value for value in (ticker, prior_ticker) if value in ticker_to_id
            )
        connection.execute(
            "INSERT INTO corporate_action_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_type,
                row.get("effective_date", "").strip(),
                ticker,
                prior_ticker,
                row.get("issuer_name", "").strip(),
                row.get("exchange", "").strip(),
                row.get("source_url", "").strip(),
                row.get("source_authority", "").strip().upper(),
                row.get("match_confidence", "").strip().lower(),
                point_in_time_safe,
                reason,
                row.get("input_file", ""),
            ),
        )

    tickers = sorted(ticker_to_id)
    classified = {row[1] for row in classification_rows}
    gaps = [
        (
            "historical_listing_authority_missing",
            len(tickers) - len(matched_safe_tickers),
            ",".join(tickers[:20]),
            1,
            "Ingest confirmed effective-dated SEC, FINRA, Nasdaq, or licensed exchange events.",
        ),
        (
            "historical_delisting_authority_missing",
            len(tickers) - len(matched_safe_tickers),
            ",".join(tickers[:20]),
            1,
            "Ingest confirmed Form 25-NSE, FINRA deletion, or licensed exchange deletion events.",
        ),
        (
            "historical_sector_lineage_missing",
            len(tickers),
            ",".join(tickers[:20]),
            1,
            "Ingest sector and industry values with effective_from and effective_to dates.",
        ),
        (
            "current_classification_missing",
            len(tickers) - len(classified),
            ",".join([ticker for ticker in tickers if ticker not in classified][:20]),
            0,
            "Refresh descriptive profiles; do not use them as historical features.",
        ),
    ]
    connection.executemany("INSERT INTO coverage_gap VALUES (?, ?, ?, ?, ?)", gaps)
    metadata = {
        "design_signature": DESIGN_SIGNATURE,
        "point_in_time_ready": "false",
        "observed_tickers": str(len(tickers)),
        "authoritative_events": str(safe_event_count),
        "matched_authoritative_tickers": str(len(matched_safe_tickers)),
    }
    connection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
    connection.executescript(
        """
        CREATE INDEX idx_symbol_lineage_ticker_dates
            ON symbol_lineage(ticker, effective_from, effective_to);
        CREATE INDEX idx_classification_ticker_dates
            ON classification_lineage(ticker, effective_from, effective_to);
        CREATE INDEX idx_events_ticker_date
            ON corporate_action_event(ticker, effective_date);
        """
    )
    connection.commit()
    connection.close()

    with (args.output_dir / "coverage_gaps.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "gap_type",
                "affected_tickers",
                "sample_tickers",
                "model_feature_blocked",
                "unblock_condition",
            ]
        )
        writer.writerows(gaps)

    contract = {
        "required_event_fields": sorted(EVENT_FIELDS),
        "trusted_authorities": sorted(TRUSTED_AUTHORITIES),
        "point_in_time_safe_requirements": [
            "source_authority is SEC, FINRA, or Nasdaq",
            "match_confidence is confirmed",
            "effective_date is valid ISO-8601",
        ],
        "warning": "Ticker-name fuzzy matches must remain unconfirmed.",
    }
    (args.output_dir / "event_input_contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    manifest: dict[str, Any] = {
        "security_master_id": "dated_security_master_v1",
        "design_signature": DESIGN_SIGNATURE,
        "status": "completed_with_required_source_gaps",
        "observed_tickers": len(tickers),
        "current_classifications": len(classified),
        "input_events": len(event_rows),
        "authoritative_dated_confirmed_events": safe_event_count,
        "matched_authoritative_tickers": len(matched_safe_tickers),
        "fully_point_in_time_ready": False,
        "event_type_counts": event_type_counts,
        "artifacts": [
            "security_master.db",
            "coverage_gaps.csv",
            "event_input_contract.json",
            "readout.md",
        ],
    }
    (args.output_dir / "security_master_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    readout = f"""# Dated security master v1

- Observed tickers represented: {len(tickers):,}
- Current descriptive classifications represented: {len(classified):,}
- Authoritative dated confirmed events ingested: {safe_event_count:,}
- Tickers matched to authoritative dated events: {len(matched_safe_tickers):,}
- Fully point-in-time ready: no

Observed first and last price dates are preserved as provisional coverage dates,
not listing or delisting facts. Current sector labels are descriptive only and
are blocked from historical model features. Add confirmed SEC Form 25-NSE,
FINRA Daily List, Nasdaq Daily List, or licensed security-master events through
the documented input contract to close these gaps.
"""
    (args.output_dir / "readout.md").write_text(readout, encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
