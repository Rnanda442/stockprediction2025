#!/usr/bin/env python3
"""Evaluate saved residual-ANN predictions under conservative exposure policies."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

LEAN_MODELS = ("baseline_plus_volatility", "baseline_momentum", "all_families", "logistic_all_families", "random_top_50")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260828)
    parser.add_argument("--cost-bps", type=int, nargs="+", default=[0, 10, 20, 40])
    return parser.parse_args()


def path_metrics(frame: pd.DataFrame) -> pd.Series:
    returns = frame["policy_net_residual_return"].astype(float)
    wealth = (1.0 + returns).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0
    return pd.Series({
        "days": len(frame), "active_day_share": float(frame["gate"].mean()),
        "mean_daily_net_residual_return": float(returns.mean()),
        "cumulative_net_residual_return": float(wealth.iloc[-1] - 1.0),
        "maximum_drawdown": float(drawdown.min()), "positive_day_rate": float((returns > 0).mean()),
        "mean_turnover": float(frame["policy_turnover"].mean()), "worst_day": float(returns.min()),
    })


def build_daily(daily: pd.DataFrame, costs: list[int]) -> pd.DataFrame:
    base = daily.loc[daily["model"].eq("all_families")].sort_values(["split", "seed", "date"]).reset_index(drop=True).copy()
    base["effective_regime"] = base.groupby(["split", "seed"])["new_signal_regime"].ffill().fillna("unknown")
    masks = {
        "ann_all_regimes": np.ones(len(base), dtype=float),
        "ann_stress_only": base["effective_regime"].eq("stress").astype(float).to_numpy(),
        "ann_trend_down_cash": (~base["effective_regime"].eq("trend_down")).astype(float).to_numpy(),
    }
    frames = []
    for policy, mask in masks.items():
        for cost_bps in costs:
            frame = base.copy()
            frame["policy"], frame["cost_bps"], frame["gate"] = policy, cost_bps, mask
            previous = frame.groupby(["split", "seed"])["gate"].shift(1).fillna(frame["gate"])
            frame["gate_transition"] = (frame["gate"] - previous).abs()
            frame["policy_turnover"] = frame["turnover"] * frame["gate"] + frame["gate_transition"] * frame["invested_weight"]
            frame["policy_gross_residual_return"] = frame["gross_residual_return"] * frame["gate"]
            frame["policy_cost"] = frame["policy_turnover"] * cost_bps / 10000.0
            frame["policy_net_residual_return"] = frame["policy_gross_residual_return"] - frame["policy_cost"]
            frames.append(frame[["policy", "cost_bps", "split", "seed", "date", "effective_regime", "gate", "gate_transition", "active_sleeves", "invested_weight", "positions", "policy_turnover", "policy_gross_residual_return", "policy_cost", "policy_net_residual_return", "missing_return_weight"]])
    return pd.concat(frames, ignore_index=True)


def bootstrap_interval(deltas: pd.DataFrame, block_cols: list[str], samples: int, rng: np.random.Generator) -> tuple[float, float, int]:
    deltas = deltas.copy()
    deltas["week"] = deltas["date"].dt.to_period("W").astype(str)
    blocks = deltas.groupby(block_cols + ["week"])["delta"].mean().to_numpy()
    draws = rng.choice(blocks, size=(samples, len(blocks)), replace=True).mean(axis=1)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975)), int(len(blocks))


def policy_interval(frame: pd.DataFrame, challenger: str, samples: int, rng: np.random.Generator) -> dict[str, object]:
    cols = ["split", "seed", "date", "policy_net_residual_return"]
    merged = frame.loc[frame["policy"].eq(challenger), cols].merge(frame.loc[frame["policy"].eq("ann_all_regimes"), cols], on=["split", "seed", "date"], suffixes=("_challenger", "_baseline"), validate="one_to_one")
    merged["delta"] = merged["policy_net_residual_return_challenger"] - merged["policy_net_residual_return_baseline"]
    lower, upper, blocks = bootstrap_interval(merged, ["split", "seed"], samples, rng)
    return {"challenger": challenger, "baseline": "ann_all_regimes", "metric": "policy_net_residual_return", "mean_delta": float(merged["delta"].mean()), "ci_2_5": lower, "ci_97_5": upper, "weekly_blocks": blocks, "positive_lower_bound": lower > 0}


def lean_interval(lean: pd.DataFrame, baseline: str, metric: str, samples: int, rng: np.random.Generator) -> dict[str, object]:
    challenger = "baseline_plus_volatility"
    left = lean.loc[(lean["model"].eq(challenger)) & (lean["seed"].eq(17)), ["split", "date", metric]]
    right = lean.loc[(lean["model"].eq(baseline)) & (lean["seed"].eq(17)), ["split", "date", metric]]
    merged = left.merge(right, on=["split", "date"], suffixes=("_challenger", "_baseline"), validate="one_to_one")
    merged["delta"] = merged[f"{metric}_challenger"] - merged[f"{metric}_baseline"]
    lower, upper, blocks = bootstrap_interval(merged, ["split"], samples, rng)
    return {"challenger": challenger, "baseline": baseline, "metric": metric, "mean_delta": float(merged["delta"].mean()), "ci_2_5": lower, "ci_97_5": upper, "weekly_blocks": blocks, "positive_lower_bound": lower > 0}


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.bootstrap_seed)
    daily = pd.read_csv(args.source_dir / "five_sleeve_daily.csv.gz", parse_dates=["date"])
    cohort = pd.read_csv(args.source_dir / "cohort_metrics.csv.gz", parse_dates=["date"])
    policy_daily = build_daily(daily, args.cost_bps)
    paths = policy_daily.groupby(["policy", "cost_bps", "split", "seed"], group_keys=False).apply(path_metrics, include_groups=False).reset_index()
    summary = paths.groupby(["policy", "cost_bps"], as_index=False).agg(paths=("split", "size"), active_day_share=("active_day_share", "mean"), mean_daily_net_residual_return=("mean_daily_net_residual_return", "mean"), median_daily_net_residual_return=("mean_daily_net_residual_return", "median"), mean_path_cumulative_net_residual_return=("cumulative_net_residual_return", "mean"), worst_path_cumulative_net_residual_return=("cumulative_net_residual_return", "min"), worst_maximum_drawdown=("maximum_drawdown", "min"), mean_positive_day_rate=("positive_day_rate", "mean"), mean_turnover=("mean_turnover", "mean"), worst_day=("worst_day", "min"))
    policy_10 = policy_daily.loc[policy_daily["cost_bps"].eq(10)].copy()
    policy_intervals = pd.DataFrame([policy_interval(policy_10, candidate, args.bootstrap_samples, rng) for candidate in ("ann_stress_only", "ann_trend_down_cash")])
    lean = cohort.loc[cohort["model"].isin(LEAN_MODELS)].copy()
    lean_summary = lean.groupby("model", as_index=False).agg(rows=("date", "size"), splits=("split", "nunique"), seeds=("seed", "nunique"), mean_auc=("auc", "mean"), mean_rank_ic=("rank_ic", "mean"), mean_net_residual_return=("net_residual_return", "mean"), win_rate=("win", "mean"), mean_turnover=("turnover", "mean"), worst_cohort=("net_residual_return", "min"))
    lean_intervals = pd.DataFrame([lean_interval(lean, baseline, metric, args.bootstrap_samples, rng) for baseline in ("baseline_momentum", "all_families", "logistic_all_families") for metric in ("net_residual_return", "auc", "rank_ic")])
    original = daily.loc[daily["model"].eq("all_families"), ["split", "seed", "date", "net_residual_return"]]
    replay = policy_10.loc[policy_10["policy"].eq("ann_all_regimes"), ["split", "seed", "date", "policy_net_residual_return"]]
    audit_rows = replay.merge(original, on=["split", "seed", "date"], validate="one_to_one")
    audit = {"source_experiment": "residual_sleeve_family_confirmation_v1", "post_selection_bias": True, "reason": "Policies were specified after inspecting the source experiment's regime diagnostics.", "promotion_allowed": False, "sealed_holdout_opened": False, "universe_point_in_time_verified": False, "base_replay_max_absolute_error_at_10bps": float((audit_rows["policy_net_residual_return"] - audit_rows["net_residual_return"]).abs().max()), "source_daily_rows": int(len(daily)), "source_cohort_rows": int(len(cohort)), "generated_at_utc": datetime.now(timezone.utc).isoformat()}
    result = {"experiment_id": "regime_exposure_policy_v1", "design_signature": "regime-exposure-policy-v1:saved-predictions:5sleeves:stress-only:trend-down-cash:lean-volatility:cost0-40:posthoc", "status": "completed_exploratory_not_promotable", "audit": audit, "policy_summary_10bps": summary.loc[summary["cost_bps"].eq(10)].to_dict("records"), "policy_paired_intervals_10bps": policy_intervals.to_dict("records"), "lean_volatility_summary": lean_summary.to_dict("records"), "lean_volatility_intervals": lean_intervals.to_dict("records")}
    policy_daily.to_csv(args.output_dir / "policy_daily.csv.gz", index=False, compression="gzip")
    paths.to_csv(args.output_dir / "policy_path_summary.csv", index=False)
    summary.to_csv(args.output_dir / "policy_summary.csv", index=False)
    policy_intervals.to_csv(args.output_dir / "policy_paired_intervals.csv", index=False)
    lean_summary.to_csv(args.output_dir / "lean_volatility_summary.csv", index=False)
    lean_intervals.to_csv(args.output_dir / "lean_volatility_intervals.csv", index=False)
    (args.output_dir / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    (args.output_dir / "result_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    readout = "\n\n".join(["# Regime exposure policy v1", "Exploratory post-selection test. No promotion is allowed and the sealed holdout remains closed.", "## 10 bps policy summary\n\n" + summary.loc[summary["cost_bps"].eq(10)].to_string(index=False), "## Paired weekly-block intervals\n\n" + policy_intervals.to_string(index=False), "## Lean volatility comparison\n\n" + lean_summary.to_string(index=False), "## Lean volatility paired intervals\n\n" + lean_intervals.to_string(index=False)])
    (args.output_dir / "readout.md").write_text(readout, encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
