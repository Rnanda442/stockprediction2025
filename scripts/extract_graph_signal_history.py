#!/usr/bin/env python3
"""Build a causal pre-holdout raw feature panel for graph confirmation."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


EXPERIMENT_ID = "graph_signal_history400_v1"
DESIGN_SIGNATURE = "graph-signal-history400-v1:raw-causal-features:top600:preholdout-only"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--context-gate", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--holdout-start", default="2026-05-29")
    p.add_argument("--history-dates", type=int, default=400)
    p.add_argument("--top-liquidity", type=int, default=600)
    return p.parse_args()


def validate_gate(path: Path) -> dict:
    gate = json.loads(path.read_text())
    holdout = gate["guardrails"]["sealed_holdout"]
    if holdout["status"] != "sealed" or holdout["opened_for_evaluation"]:
        raise RuntimeError("The final holdout is not sealed.")
    matches = [
        x for x in gate["next_experiments"]
        if x.get("experiment_id", x.get("id")) == EXPERIMENT_ID
    ]
    if len(matches) != 1 or matches[0].get("design_signature") != DESIGN_SIGNATURE:
        raise RuntimeError("History extraction is not uniquely approved in the context gate.")
    if matches[0].get("status") != "approved_next":
        raise RuntimeError("History extraction is not approved for compute.")
    return gate


def choose(columns: set[str], candidates: list[str], label: str) -> str:
    match = next((x for x in candidates if x in columns), None)
    if not match:
        raise RuntimeError(f"ResearchPrices has no supported {label} column.")
    return match


def load_prices(db_path: Path, holdout_start: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(ResearchPrices)")}
        ticker = choose(columns, ["ticker", "symbol"], "ticker")
        date = choose(columns, ["begins_at", "date", "source_date"], "date")
        close = choose(columns, ["close_price", "close", "adjusted_close"], "close")
        volume = choose(columns, ["volume", "share_volume"], "volume")
        query = (
            f'SELECT "{ticker}" AS ticker, "{date}" AS date, '
            f'"{close}" AS close_price, "{volume}" AS volume '
            f'FROM ResearchPrices WHERE "{date}" < ? ORDER BY "{ticker}", "{date}"'
        )
        frame = pd.read_sql_query(query, con, params=[holdout_start])
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["close_price"] = pd.to_numeric(frame["close_price"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    return frame.dropna(subset=["ticker", "date", "close_price", "volume"])


def build_features(prices: pd.DataFrame, history_dates: int, top_liquidity: int) -> pd.DataFrame:
    prices = prices.sort_values(["ticker", "date"]).copy()
    g = prices.groupby("ticker", sort=False)
    prices["ret_1d"] = g["close_price"].pct_change(fill_method=None)
    for window in [5, 20, 60]:
        prices[f"ret_{window}d"] = g["close_price"].pct_change(window, fill_method=None)
    prices["vol_20d"] = g["ret_1d"].transform(lambda s: s.rolling(20, min_periods=15).std()) * np.sqrt(252)
    prices["vol_60d"] = g["ret_1d"].transform(lambda s: s.rolling(60, min_periods=40).std()) * np.sqrt(252)
    rolling_high = g["close_price"].transform(lambda s: s.rolling(60, min_periods=40).max())
    prices["drawdown_60d"] = prices["close_price"] / rolling_high - 1.0
    prices["dollar_volume"] = prices["close_price"] * prices["volume"].clip(lower=0)
    trailing_dollar = prices.groupby("ticker", sort=False)["dollar_volume"].transform(
        lambda s: s.rolling(20, min_periods=15).mean()
    )
    prices["dollar_vol_20d_log"] = np.log1p(trailing_dollar.clip(lower=0))
    feature_cols = [
        "ret_5d", "ret_20d", "ret_60d", "vol_20d", "vol_60d",
        "drawdown_60d", "dollar_vol_20d_log",
    ]
    prices = prices.dropna(subset=feature_cols).copy()
    dates = sorted(prices["date"].unique())[-history_dates:]
    prices = prices[prices["date"].isin(dates)].copy()
    prices["liquidity_rank"] = prices.groupby("date")["dollar_vol_20d_log"].rank(
        method="first", ascending=False
    )
    prices = prices[prices["liquidity_rank"] <= top_liquidity]
    return prices[["date", "ticker"] + feature_cols].sort_values(["date", "ticker"])


def main() -> None:
    args = parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    gate = validate_gate(Path(args.context_gate))
    prices = load_prices(Path(args.db), args.holdout_start)
    features = build_features(prices, args.history_dates, args.top_liquidity)
    if features["date"].max() >= pd.Timestamp(args.holdout_start):
        raise RuntimeError("Extracted panel crossed the sealed holdout boundary.")
    features.to_csv(output / "raw_features.csv", index=False)
    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "status": "completed_pending_review",
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "rows": len(features),
        "dates": int(features["date"].nunique()),
        "tickers": int(features["ticker"].nunique()),
        "date_min": str(features["date"].min().date()),
        "date_max": str(features["date"].max().date()),
        "holdout_start": args.holdout_start,
        "holdout_opened": False,
        "source_context_id": gate["context_id"],
    }
    (output / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
