#!/usr/bin/env python3
"""Build an expanded pre-holdout panel with residual-return targets."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


EXPERIMENT_ID = "point_in_time_residual_panel_5y_v1"
DESIGN_SIGNATURE = (
    "point-in-time-residual-panel-5y-v1:"
    "h5:1250d:top1000:loo-market:optional-pit-sector:holdout60"
)
FEATURES = [
    "ret_5d",
    "ret_20d",
    "ret_60d",
    "vol_20d",
    "vol_60d",
    "drawdown_60d",
    "dollar_vol_20d_log",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite database containing ResearchPrices")
    parser.add_argument(
        "--universe-db",
        help="Optional point_in_time_universe.db; eligible rows are joined by ticker and date",
    )
    parser.add_argument("--context-gate", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--sector-map",
        help="Optional CSV with ticker, sector, valid_from, valid_to columns",
    )
    parser.add_argument("--holdout-start", default="2026-05-29")
    parser.add_argument("--history-dates", type=int, default=1250)
    parser.add_argument("--top-liquidity", type=int, default=1000)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--min-sector-members", type=int, default=6)
    return parser.parse_args()


def read_json(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def validate_registration(gate: dict, spec: dict, args: argparse.Namespace) -> None:
    if spec.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("Spec experiment_id does not match the frozen builder")
    if spec.get("design_signature") != DESIGN_SIGNATURE:
        raise ValueError("Spec design_signature does not match the frozen builder")
    holdout = spec.get("sealed_holdout", {}).get("start_date")
    if holdout != args.holdout_start:
        raise ValueError("CLI holdout start differs from the frozen specification")
    target = spec.get("target", {})
    panel = spec.get("panel", {})
    frozen_values = {
        "horizon": (args.horizon, target.get("horizon_trading_days")),
        "history_dates": (args.history_dates, panel.get("requested_history_dates")),
        "top_liquidity": (args.top_liquidity, panel.get("daily_liquidity_cap")),
        "min_sector_members": (
            args.min_sector_members,
            target.get("minimum_sector_members_including_self"),
        ),
    }
    for name, (actual, frozen) in frozen_values.items():
        if actual != frozen:
            raise ValueError(f"{name}={actual} differs from frozen value {frozen}")

    registered = False
    for entry in gate.get("next_experiments", []):
        entry_id = entry.get("experiment_id", entry.get("id"))
        if entry_id == EXPERIMENT_ID:
            if entry.get("design_signature") != DESIGN_SIGNATURE:
                raise ValueError("Context-gate design signature mismatch")
            if entry.get("status") not in {"approved_next", "approved_after_dependency"}:
                raise ValueError("Context gate has not approved this experiment")
            registered = True
            break
    if not registered:
        raise ValueError("Experiment is not registered in context_gate.json")


def load_prices(
    db_path: str,
    holdout_start: pd.Timestamp,
    universe_db: str | None,
) -> pd.DataFrame:
    if universe_db:
        query = """
            SELECT
                prices.ticker,
                prices.begins_at AS date,
                prices.close_price,
                prices.volume
            FROM ResearchPrices AS prices
            INNER JOIN universe.point_in_time_universe AS membership
                ON membership.ticker = UPPER(TRIM(prices.ticker))
               AND membership.as_of_date = date(prices.begins_at)
               AND membership.universe_eligible = 1
            WHERE prices.begins_at < ?
            ORDER BY prices.ticker, prices.begins_at
        """
    else:
        query = """
            SELECT
                ticker,
                begins_at AS date,
                close_price,
                volume
            FROM ResearchPrices
            WHERE begins_at < ?
            ORDER BY ticker, begins_at
        """
    with sqlite3.connect(db_path) as connection:
        if universe_db:
            connection.execute("ATTACH DATABASE ? AS universe", (universe_db,))
        prices = pd.read_sql_query(
            query,
            connection,
            params=[holdout_start.strftime("%Y-%m-%d")],
        )
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce").dt.normalize()
    prices["ticker"] = prices["ticker"].astype(str).str.strip().str.upper()
    prices["close_price"] = pd.to_numeric(prices["close_price"], errors="coerce")
    prices["volume"] = pd.to_numeric(prices["volume"], errors="coerce")
    prices = prices.dropna(subset=["ticker", "date", "close_price", "volume"])
    prices = prices[(prices["ticker"] != "") & (prices["close_price"] > 0)]
    prices = prices.drop_duplicates(["ticker", "date"], keep="last")
    return prices.sort_values(["ticker", "date"]).reset_index(drop=True)


def add_trailing_features(prices: pd.DataFrame) -> pd.DataFrame:
    grouped = prices.groupby("ticker", sort=False)
    prices["ret_1d"] = grouped["close_price"].pct_change()
    prices["ret_5d"] = grouped["close_price"].pct_change(5)
    prices["ret_20d"] = grouped["close_price"].pct_change(20)
    prices["ret_60d"] = grouped["close_price"].pct_change(60)
    prices["vol_20d"] = (
        prices.groupby("ticker", sort=False)["ret_1d"]
        .rolling(20, min_periods=20)
        .std()
        .reset_index(level=0, drop=True)
    )
    prices["vol_60d"] = (
        prices.groupby("ticker", sort=False)["ret_1d"]
        .rolling(60, min_periods=60)
        .std()
        .reset_index(level=0, drop=True)
    )
    rolling_high = (
        prices.groupby("ticker", sort=False)["close_price"]
        .rolling(60, min_periods=60)
        .max()
        .reset_index(level=0, drop=True)
    )
    prices["drawdown_60d"] = prices["close_price"] / rolling_high - 1.0
    prices["dollar_volume"] = prices["close_price"] * prices["volume"].clip(lower=0)
    dollar_volume_20d = (
        prices.groupby("ticker", sort=False)["dollar_volume"]
        .rolling(20, min_periods=20)
        .mean()
        .reset_index(level=0, drop=True)
    )
    prices["dollar_vol_20d_log"] = np.log1p(dollar_volume_20d)
    return prices


def add_forward_target(
    prices: pd.DataFrame,
    horizon: int,
    holdout_start: pd.Timestamp,
) -> pd.DataFrame:
    grouped = prices.groupby("ticker", sort=False)
    prices["evaluation_date"] = grouped["date"].shift(-horizon)
    prices["future_close"] = grouped["close_price"].shift(-horizon)
    prices["future_return"] = prices["future_close"] / prices["close_price"] - 1.0
    required = FEATURES + ["evaluation_date", "future_return"]
    prices = prices.dropna(subset=required)
    prices = prices[prices["evaluation_date"] < holdout_start].copy()
    finite = np.isfinite(prices[FEATURES + ["future_return"]]).all(axis=1)
    return prices.loc[finite].copy()


def select_panel(
    prices: pd.DataFrame,
    history_dates: int,
    top_liquidity: int,
) -> pd.DataFrame:
    dates = np.sort(prices["date"].unique())
    if len(dates) > history_dates:
        dates = dates[-history_dates:]
    prices = prices[prices["date"].isin(dates)].copy()
    prices["liquidity_rank"] = prices.groupby("date")[
        "dollar_vol_20d_log"
    ].rank(method="first", ascending=False)
    prices = prices[prices["liquidity_rank"] <= top_liquidity].copy()
    return prices.sort_values(["date", "ticker"]).reset_index(drop=True)


def attach_point_in_time_sector(
    prices: pd.DataFrame,
    sector_map_path: str | None,
) -> tuple[pd.DataFrame, dict]:
    if not sector_map_path:
        prices["sector"] = pd.NA
        return prices, {
            "sector_map_supplied": False,
            "sector_rows_mapped": 0,
            "sector_mapping_coverage": 0.0,
        }

    mapping = pd.read_csv(sector_map_path)
    required = ["ticker", "sector", "valid_from", "valid_to"]
    missing = [column for column in required if column not in mapping.columns]
    if missing:
        raise ValueError(f"Sector mapping is missing columns: {missing}")
    mapping = mapping[required].copy()
    mapping["ticker"] = mapping["ticker"].astype(str).str.strip().str.upper()
    mapping["sector"] = mapping["sector"].astype(str).str.strip()
    mapping["valid_from"] = pd.to_datetime(
        mapping["valid_from"], errors="coerce"
    ).dt.normalize()
    mapping["valid_to"] = pd.to_datetime(
        mapping["valid_to"], errors="coerce"
    ).dt.normalize()
    if mapping[["ticker", "sector", "valid_from"]].isna().any().any():
        raise ValueError("Sector mapping has invalid ticker, sector, or valid_from values")
    invalid_intervals = mapping["valid_to"].notna() & (
        mapping["valid_to"] <= mapping["valid_from"]
    )
    if invalid_intervals.any():
        raise ValueError("Sector mapping contains non-positive validity intervals")

    left = prices.sort_values(["date", "ticker"]).copy()
    right = mapping.sort_values(["valid_from", "ticker"]).copy()
    merged = pd.merge_asof(
        left,
        right,
        left_on="date",
        right_on="valid_from",
        by="ticker",
        direction="backward",
        allow_exact_matches=True,
    )
    valid = merged["sector"].notna() & (
        merged["valid_to"].isna() | (merged["date"] < merged["valid_to"])
    )
    merged.loc[~valid, "sector"] = pd.NA
    merged = merged.drop(columns=["valid_from", "valid_to"])
    mapped_rows = int(merged["sector"].notna().sum())
    return merged, {
        "sector_map_supplied": True,
        "sector_rows_mapped": mapped_rows,
        "sector_mapping_coverage": mapped_rows / len(merged) if len(merged) else 0.0,
    }


def leave_one_out_mean(
    frame: pd.DataFrame,
    group_columns: list[str],
    value_column: str,
) -> tuple[pd.Series, pd.Series]:
    grouped = frame.groupby(group_columns, dropna=False)[value_column]
    group_sum = grouped.transform("sum")
    group_count = grouped.transform("count")
    denominator = group_count - 1
    benchmark = (group_sum - frame[value_column]) / denominator.where(
        denominator > 0
    )
    return benchmark, group_count


def add_residual_targets(
    prices: pd.DataFrame,
    min_sector_members: int,
) -> pd.DataFrame:
    market_benchmark, market_members = leave_one_out_mean(
        prices, ["date"], "future_return"
    )
    prices["market_members"] = market_members.astype("int32")
    prices["market_return_loo"] = market_benchmark
    prices["market_residual_return"] = (
        prices["future_return"] - prices["market_return_loo"]
    )

    prices["sector_members"] = 0
    prices["sector_return_loo"] = np.nan
    has_sector = prices["sector"].notna()
    if has_sector.any():
        sector_slice = prices.loc[has_sector].copy()
        sector_benchmark, sector_members = leave_one_out_mean(
            sector_slice, ["date", "sector"], "future_return"
        )
        prices.loc[has_sector, "sector_members"] = sector_members.astype("int32")
        prices.loc[has_sector, "sector_return_loo"] = sector_benchmark

    valid_sector_target = (
        prices["sector"].notna()
        & (prices["sector_members"] >= min_sector_members)
        & prices["sector_return_loo"].notna()
    )
    prices["sector_residual_return"] = (
        prices["future_return"] - prices["sector_return_loo"]
    )
    prices["primary_benchmark"] = np.where(
        valid_sector_target, "sector_leave_one_out", "market_leave_one_out"
    )
    prices["primary_residual_return"] = np.where(
        valid_sector_target,
        prices["sector_residual_return"],
        prices["market_residual_return"],
    )
    prices["primary_residual_rank_pct"] = prices.groupby("date")[
        "primary_residual_return"
    ].rank(method="average", pct=True)
    prices["primary_residual_positive"] = (
        prices["primary_residual_return"] > 0
    ).astype("int8")
    prices["primary_residual_top_decile"] = (
        prices["primary_residual_rank_pct"] >= 0.9
    ).astype("int8")
    return prices


def metric_row(metric: str, value: object, passed: bool | None = None) -> dict:
    row = {"metric": metric, "value": value}
    if passed is not None:
        row["passed"] = bool(passed)
    return row


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    holdout_start = pd.Timestamp(args.holdout_start)

    gate = read_json(args.context_gate)
    spec = read_json(args.spec)
    validate_registration(gate, spec, args)

    prices = load_prices(args.db, holdout_start, args.universe_db)
    source_summary = {
        "source_rows_pre_holdout": int(len(prices)),
        "source_min_date": prices["date"].min().strftime("%Y-%m-%d"),
        "source_max_date": prices["date"].max().strftime("%Y-%m-%d"),
        "source_dates_pre_holdout": int(prices["date"].nunique()),
        "source_tickers_pre_holdout": int(prices["ticker"].nunique()),
        "point_in_time_membership_filter_enabled": bool(args.universe_db),
        "point_in_time_membership_database": args.universe_db or "",
    }
    duplicate_count = int(prices.duplicated(["ticker", "date"]).sum())

    prices = add_trailing_features(prices)
    prices = add_forward_target(prices, args.horizon, holdout_start)
    prices = select_panel(prices, args.history_dates, args.top_liquidity)
    prices, sector_summary = attach_point_in_time_sector(prices, args.sector_map)
    prices = add_residual_targets(prices, args.min_sector_members)

    decision_holdout_violations = int((prices["date"] >= holdout_start).sum())
    evaluation_holdout_violations = int(
        (prices["evaluation_date"] >= holdout_start).sum()
    )
    self_benchmark_failures = int(
        (prices["market_members"] <= 1).sum()
    )
    if decision_holdout_violations or evaluation_holdout_violations:
        raise RuntimeError("Sealed-holdout boundary violation detected")
    if self_benchmark_failures:
        raise RuntimeError("Leave-one-out market benchmark lacks peers")

    output_columns = [
        "ticker",
        "date",
        "evaluation_date",
        "close_price",
        "future_close",
        "future_return",
        *FEATURES,
        "liquidity_rank",
        "sector",
        "market_members",
        "market_return_loo",
        "market_residual_return",
        "sector_members",
        "sector_return_loo",
        "sector_residual_return",
        "primary_benchmark",
        "primary_residual_return",
        "primary_residual_rank_pct",
        "primary_residual_positive",
        "primary_residual_top_decile",
    ]
    prices[output_columns].to_csv(
        output_dir / "target_panel.csv.gz",
        index=False,
        compression="gzip",
        date_format="%Y-%m-%d",
    )

    ticker_coverage = (
        prices.groupby("ticker")
        .agg(
            first_decision_date=("date", "min"),
            last_decision_date=("date", "max"),
            rows=("date", "size"),
            mean_liquidity_rank=("liquidity_rank", "mean"),
            sector_coverage=("sector", lambda values: values.notna().mean()),
        )
        .reset_index()
        .sort_values(["rows", "ticker"], ascending=[False, True])
    )
    ticker_coverage.to_csv(
        output_dir / "ticker_coverage.csv", index=False, date_format="%Y-%m-%d"
    )

    panel_dates = int(prices["date"].nunique())
    panel_tickers = int(prices["ticker"].nunique())
    panel_summary = {
        "panel_rows": int(len(prices)),
        "panel_dates": panel_dates,
        "panel_tickers": panel_tickers,
        "panel_min_decision_date": prices["date"].min().strftime("%Y-%m-%d"),
        "panel_max_decision_date": prices["date"].max().strftime("%Y-%m-%d"),
        "panel_max_evaluation_date": prices["evaluation_date"].max().strftime(
            "%Y-%m-%d"
        ),
        "market_residual_mean": float(prices["market_residual_return"].mean()),
        "primary_residual_mean": float(prices["primary_residual_return"].mean()),
        "sector_target_rows": int(
            (prices["primary_benchmark"] == "sector_leave_one_out").sum()
        ),
    }
    quality_rows = [
        metric_row(key, value) for key, value in {**source_summary, **panel_summary}.items()
    ]
    quality_rows.extend(
        metric_row(key, value) for key, value in sector_summary.items()
    )
    pd.DataFrame(quality_rows).to_csv(
        output_dir / "data_quality_summary.csv", index=False
    )

    minimum_dates = int(spec["promotion"]["minimum_pre_holdout_dates"])
    minimum_tickers = int(spec["promotion"]["minimum_distinct_tickers"])
    audit_rows = [
        metric_row("duplicate_ticker_dates_after_cleaning", duplicate_count, duplicate_count == 0),
        metric_row("decision_holdout_violations", decision_holdout_violations, decision_holdout_violations == 0),
        metric_row("evaluation_holdout_violations", evaluation_holdout_violations, evaluation_holdout_violations == 0),
        metric_row("market_leave_one_out_rows_without_peers", self_benchmark_failures, self_benchmark_failures == 0),
        metric_row("minimum_pre_holdout_dates", panel_dates, panel_dates >= minimum_dates),
        metric_row("minimum_distinct_tickers", panel_tickers, panel_tickers >= minimum_tickers),
        metric_row(
            "past_only_universe_membership_enforced",
            bool(args.universe_db),
            bool(args.universe_db),
        ),
        metric_row("historical_security_master_verified", False, False),
        metric_row("universe_point_in_time_verified", False, False),
        metric_row("model_promotion_allowed", False, True),
    ]
    pd.DataFrame(audit_rows).to_csv(output_dir / "leakage_audit.csv", index=False)

    target_definition = {
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "horizon_trading_days": args.horizon,
        "raw_return": "future_close / close_price - 1",
        "market_benchmark": "cross-sectional leave-one-out mean future return",
        "market_residual": "future_return - market_return_loo",
        "sector_benchmark": (
            "dated-sector leave-one-out mean when a valid mapping is supplied "
            f"and sector_members >= {args.min_sector_members}"
        ),
        "primary_residual": (
            "sector_residual_return when dated sector coverage is valid, "
            "otherwise market_residual_return"
        ),
        "validity_interval": "[valid_from, valid_to)",
        "sealed_holdout_start": args.holdout_start,
    }
    write_json(output_dir / "target_definition.json", target_definition)

    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "status": "panel_built",
        "source": source_summary,
        "panel": panel_summary,
        "sector": sector_summary,
        "guardrails": {
            "sealed_holdout_start": args.holdout_start,
            "sealed_holdout_opened": False,
            "decision_and_evaluation_dates_pre_holdout": True,
            "market_benchmark_leave_one_out": True,
            "sector_mapping_requires_dated_intervals": True,
            "past_only_universe_membership_enforced": bool(args.universe_db),
            "historical_security_master_verified": False,
            "universe_point_in_time_verified": False,
            "model_promotion_allowed": False,
        },
        "known_limitations": [
            "Historical universe membership and delistings are not independently verified.",
            (
                "Sector-neutral targets fall back to market-neutral targets unless "
                "a dated valid_from/valid_to sector map is supplied."
            ),
            "Overlapping five-day targets require purged or embargoed model evaluation.",
        ],
    }
    write_json(output_dir / "experiment_manifest.json", manifest)

    candidate_update = {
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "candidate_status": "completed_data_stage_pending_review",
        "promotion": False,
        "summary": {
            **panel_summary,
            **sector_summary,
            "past_only_universe_membership_enforced": bool(args.universe_db),
            "historical_security_master_verified": False,
            "universe_point_in_time_verified": False,
            "sealed_holdout_opened": False,
        },
        "next_experiment": (
            "purged_walk_forward_residual_baselines_v1 after panel and leakage "
            "audit review"
        ),
    }
    write_json(output_dir / "context_gate_candidate_update.json", candidate_update)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
