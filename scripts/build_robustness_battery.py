#!/usr/bin/env python3
"""Comprehensive pre-holdout robustness battery for the compact five-day model."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

import build_combination_similarity_lab as base
import build_compact_consensus_lab as compact
from context_gate import assert_experiment_allowed, candidate_update, load_gate


EXPERIMENT_ID = "robustness_battery_5d_v1"
DESIGN_SIGNATURE = (
    "robustness-battery-v1:h5:challengers+lofo+placebo+segments+costs+bootstrap:holdout60"
)

CHALLENGERS = {
    "ridge_c0_01": {"kind": "ridge", "c": 0.01},
    "ridge_c0_1": {"kind": "ridge", "c": 0.1},
    "ridge_c1": {"kind": "ridge", "c": 1.0},
    "ridge_c10": {"kind": "ridge", "c": 10.0},
    "elastic_c0_1_l25": {"kind": "elastic", "c": 0.1, "l1_ratio": 0.25},
    "elastic_c0_1_l50": {"kind": "elastic", "c": 0.1, "l1_ratio": 0.50},
    "elastic_c0_1_l75": {"kind": "elastic", "c": 0.1, "l1_ratio": 0.75},
    "elastic_c1_l25": {"kind": "elastic", "c": 1.0, "l1_ratio": 0.25},
    "elastic_c1_l50": {"kind": "elastic", "c": 1.0, "l1_ratio": 0.50},
    "elastic_c1_l75": {"kind": "elastic", "c": 1.0, "l1_ratio": 0.75},
    "hist_depth3": {"kind": "hist", "depth": 3, "learning_rate": 0.05},
    "hist_depth5": {"kind": "hist", "depth": 5, "learning_rate": 0.05},
    "extra_depth6": {"kind": "extra", "depth": 6},
    "extra_unbounded": {"kind": "extra", "depth": None},
    "ann_relu_48_24": {"kind": "ann", "architecture": "relu_48_24"},
    "ann_tanh_48_24": {"kind": "ann", "architecture": "tanh_48_24"},
    "ann_relu_64_32_16": {"kind": "ann", "architecture": "relu_64_32_16"},
}

SEGMENTS = (
    "all",
    "liquidity_high",
    "liquidity_low",
    "graph_degree_high",
    "graph_degree_low",
    "momentum_positive",
    "momentum_negative",
)
STRATEGIES = ("plain_probability", "similarity_weighted", "consensus_gate")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--context-gate", required=True)
    parser.add_argument("--compact-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--holdout-dates", type=int, default=60)
    parser.add_argument("--splits", type=int, default=3)
    parser.add_argument("--test-dates", type=int, default=30)
    parser.add_argument("--train-dates", type=int, default=252)
    parser.add_argument("--graph-lookback-dates", type=int, default=120)
    parser.add_argument("--top-k", type=int, default=600)
    parser.add_argument("--neighbors", type=int, default=10)
    parser.add_argument("--max-iter", type=int, default=100)
    parser.add_argument("--seeds", default="442,2025,9001")
    parser.add_argument("--portfolio-sizes", default="5,10,20,40,60")
    parser.add_argument("--rebalance-days", default="1,5")
    parser.add_argument("--cost-bps", default="0,5,10,25,50")
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    return parser.parse_args()


def require_compact_dependency(compact_dir: Path) -> dict[str, object]:
    manifest_path = compact_dir / "experiment_manifest.json"
    summary_path = compact_dir / "compact_model_summary.csv"
    candidate_path = compact_dir / "context_gate_candidate_update.json"
    missing = [str(path) for path in (manifest_path, summary_path, candidate_path) if not path.exists()]
    if missing:
        raise RuntimeError(f"Compact dependency is incomplete; missing: {missing}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("experiment_id") != compact.EXPERIMENT_ID:
        raise RuntimeError("Compact dependency experiment_id does not match")
    return manifest


def challenger_seed_list(spec: dict[str, object], seeds: list[int]) -> list[int]:
    return seeds if spec["kind"] == "ann" else [0]


def fit_challenger(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    name: str,
    seed: int,
    max_iter: int,
) -> tuple[np.ndarray, dict[str, object]]:
    spec = CHALLENGERS[name]
    kind = str(spec["kind"])
    if kind == "ann":
        probability, metadata, _, _ = base.fit_ann(
            train,
            test,
            features,
            architecture=str(spec["architecture"]),
            seed=seed,
            max_iter=max_iter,
        )
        return probability, metadata

    train_values = train[features].to_numpy(dtype=float)
    test_values = test[features].to_numpy(dtype=float)
    if kind in {"ridge", "elastic"}:
        scaler = StandardScaler()
        train_values = scaler.fit_transform(train_values)
        test_values = scaler.transform(test_values)
        if kind == "ridge":
            model = LogisticRegression(
                penalty="l2",
                C=float(spec["c"]),
                solver="lbfgs",
                max_iter=1000,
                random_state=seed,
            )
        else:
            model = LogisticRegression(
                penalty="elasticnet",
                C=float(spec["c"]),
                l1_ratio=float(spec["l1_ratio"]),
                solver="saga",
                max_iter=1000,
                tol=1e-4,
                random_state=seed,
            )
    elif kind == "hist":
        model = HistGradientBoostingClassifier(
            learning_rate=float(spec["learning_rate"]),
            max_depth=int(spec["depth"]),
            max_iter=200,
            min_samples_leaf=40,
            l2_regularization=1.0,
            random_state=seed,
        )
    elif kind == "extra":
        model = ExtraTreesClassifier(
            n_estimators=300,
            max_depth=spec["depth"],
            min_samples_leaf=20,
            max_features="sqrt",
            n_jobs=-1,
            random_state=seed,
        )
    else:
        raise ValueError(f"Unsupported challenger kind: {kind}")

    model.fit(train_values, train["target"].astype(int).to_numpy())
    probability = model.predict_proba(test_values)[:, 1]
    iterations = int(np.max(getattr(model, "n_iter_", [0])))
    return probability, {"iterations": iterations, "converged": iterations < 1000 or iterations == 0}


def calibration_metrics(target: pd.Series, probability: np.ndarray, bins: int = 10) -> dict[str, float]:
    target_array = target.astype(int).to_numpy()
    clipped = np.clip(probability, 1e-6, 1.0 - 1e-6)
    bin_ids = np.minimum((clipped * bins).astype(int), bins - 1)
    expected_error = 0.0
    for bin_id in range(bins):
        mask = bin_ids == bin_id
        if mask.any():
            expected_error += mask.mean() * abs(target_array[mask].mean() - clipped[mask].mean())
    return {
        "auc": float(roc_auc_score(target_array, clipped)),
        "brier": float(brier_score_loss(target_array, clipped)),
        "log_loss": float(log_loss(target_array, clipped)),
        "ece_10": float(expected_error),
        "mean_probability": float(clipped.mean()),
        "positive_rate": float(target_array.mean()),
    }


def summarize_challengers(frame: pd.DataFrame) -> pd.DataFrame:
    values = ["auc", "brier", "log_loss", "ece_10", "mean_excess_return", "win_rate"]
    summary = frame.groupby("challenger")[values].agg(["mean", "std", "count"]).reset_index()
    summary.columns = [
        "_".join(part for part in column if part) if isinstance(column, tuple) else column
        for column in summary.columns
    ]
    summary["auc_rank"] = summary["auc_mean"].rank(ascending=False, pct=True)
    summary["excess_rank"] = summary["mean_excess_return_mean"].rank(ascending=False, pct=True)
    summary["brier_rank"] = summary["brier_mean"].rank(ascending=True, pct=True)
    summary["selection_score"] = (
        0.50 * (1.0 - summary["auc_rank"])
        + 0.35 * (1.0 - summary["excess_rank"])
        + 0.15 * (1.0 - summary["brier_rank"])
    )
    return summary.sort_values(["selection_score", "auc_mean"], ascending=False).reset_index(drop=True)


def segment_day(day: pd.DataFrame, segment: str) -> pd.DataFrame:
    if segment == "all":
        return day
    if segment == "liquidity_high":
        return day.loc[day["dollar_vol_20d_log"].ge(day["dollar_vol_20d_log"].median())]
    if segment == "liquidity_low":
        return day.loc[day["dollar_vol_20d_log"].lt(day["dollar_vol_20d_log"].median())]
    if segment == "graph_degree_high":
        return day.loc[day["graph_degree"].ge(day["graph_degree"].median())]
    if segment == "graph_degree_low":
        return day.loc[day["graph_degree"].lt(day["graph_degree"].median())]
    if segment == "momentum_positive":
        return day.loc[day["ret_20d"].ge(0.0)]
    if segment == "momentum_negative":
        return day.loc[day["ret_20d"].lt(0.0)]
    raise ValueError(f"Unknown segment: {segment}")


def scenario_daily_rows(
    test: pd.DataFrame,
    probability: np.ndarray,
    portfolio_sizes: list[int],
    rebalance_days: list[int],
    costs_bps: list[float],
) -> list[dict[str, object]]:
    columns = [
        "date", "ticker", "forward_return", "probability", "dollar_vol_20d_log",
        "graph_degree", "ret_20d", "graph_similarity_mean", "neighbor_confirmation",
    ]
    scored = test.drop(columns=["probability"], errors="ignore").copy()
    scored["probability"] = probability
    rows: list[dict[str, object]] = []
    dates = sorted(scored["date"].unique())
    for segment in SEGMENTS:
        for strategy in STRATEGIES:
            for portfolio_size in portfolio_sizes:
                for rebalance in rebalance_days:
                    previous: set[str] = set()
                    for date_index, date in enumerate(dates):
                        day_all = scored.loc[scored["date"].eq(date), columns]
                        day = segment_day(day_all, segment)
                        if day.empty:
                            continue
                        should_rebalance = date_index % rebalance == 0 or not previous
                        if should_rebalance:
                            selected = compact.select_strategy(day, strategy, portfolio_size)
                            current = set(selected["ticker"].astype(str))
                            turnover = 1.0 if not previous else 1.0 - len(current & previous) / max(1, len(current))
                            previous = current
                        else:
                            selected = day.loc[day["ticker"].astype(str).isin(previous)]
                            if selected.empty:
                                selected = compact.select_strategy(day, strategy, portfolio_size)
                                previous = set(selected["ticker"].astype(str))
                            turnover = 0.0
                        gross = float(selected["forward_return"].mean())
                        universe = float(day["forward_return"].mean())
                        market_return = float(day_all["forward_return"].mean())
                        market_volatility = float(day_all["ret_20d"].std(ddof=0))
                        for cost_bps in costs_bps:
                            net = gross - turnover * cost_bps / 10000.0
                            rows.append({
                                "date": date,
                                "segment": segment,
                                "strategy": strategy,
                                "portfolio_size": portfolio_size,
                                "rebalance_days": rebalance,
                                "cost_bps": cost_bps,
                                "gross_return": gross,
                                "net_return": net,
                                "universe_return": universe,
                                "excess_return": net - universe,
                                "turnover": turnover,
                                "market_return": market_return,
                                "market_volatility": market_volatility,
                            })
    return rows


def scenario_summary(frame: pd.DataFrame) -> pd.DataFrame:
    keys = ["segment", "strategy", "portfolio_size", "rebalance_days", "cost_bps"]
    result = frame.groupby(keys).agg(
        dates=("date", "count"),
        mean_net_return=("net_return", "mean"),
        mean_excess_return=("excess_return", "mean"),
        excess_std=("excess_return", "std"),
        win_rate=("excess_return", lambda values: float((values > 0.0).mean())),
        mean_turnover=("turnover", "mean"),
        worst_net_return=("net_return", "min"),
    ).reset_index()
    return result.sort_values(["mean_excess_return", "win_rate"], ascending=False).reset_index(drop=True)


def regime_summary(frame: pd.DataFrame) -> pd.DataFrame:
    baseline = frame.loc[
        frame["segment"].eq("all")
        & frame["portfolio_size"].eq(20)
        & frame["rebalance_days"].eq(5)
        & frame["cost_bps"].eq(10.0)
    ].copy()
    baseline["return_regime"] = np.where(baseline["market_return"].ge(0.0), "market_up", "market_down")
    volatility_cutoff = baseline["market_volatility"].median()
    baseline["volatility_regime"] = np.where(
        baseline["market_volatility"].ge(volatility_cutoff), "high_volatility", "low_volatility"
    )
    long = pd.concat([
        baseline.assign(regime=baseline["return_regime"], regime_type="return"),
        baseline.assign(regime=baseline["volatility_regime"], regime_type="volatility"),
    ], ignore_index=True)
    return long.groupby(["regime_type", "regime", "strategy"]).agg(
        dates=("date", "count"),
        mean_excess_return=("excess_return", "mean"),
        win_rate=("excess_return", lambda values: float((values > 0.0).mean())),
        worst_net_return=("net_return", "min"),
    ).reset_index()


def weekly_block_bootstrap(
    daily: pd.DataFrame,
    samples: int,
    seed: int = 2026,
) -> pd.DataFrame:
    focus = daily.loc[
        daily["segment"].eq("all")
        & daily["portfolio_size"].eq(20)
        & daily["rebalance_days"].eq(5)
        & daily["cost_bps"].eq(10.0)
    ].copy()
    pivot = focus.pivot_table(index=["split", "seed", "date"], columns="strategy", values="excess_return")
    pivot = pivot.dropna(subset=["plain_probability"])
    pivot = pivot.reset_index()
    pivot["week"] = pd.to_datetime(pivot["date"]).dt.to_period("W").astype(str)
    generator = np.random.default_rng(seed)
    weeks = pivot["week"].unique()
    rows: list[dict[str, object]] = []
    for strategy in ("consensus_gate", "similarity_weighted"):
        observed = float((pivot[strategy] - pivot["plain_probability"]).mean())
        estimates = []
        for _ in range(samples):
            sampled_weeks = generator.choice(weeks, size=len(weeks), replace=True)
            sampled = pd.concat([pivot.loc[pivot["week"].eq(week)] for week in sampled_weeks])
            estimates.append(float((sampled[strategy] - sampled["plain_probability"]).mean()))
        rows.append({
            "strategy": strategy,
            "portfolio_size": 20,
            "rebalance_days": 5,
            "cost_bps": 10.0,
            "observed_delta": observed,
            "ci_2_5": float(np.quantile(estimates, 0.025)),
            "ci_97_5": float(np.quantile(estimates, 0.975)),
            "positive_probability": float(np.mean(np.asarray(estimates) > 0.0)),
            "bootstrap_samples": samples,
        })
    return pd.DataFrame(rows)


def markdown_table(frame: pd.DataFrame, columns: list[str], rows: int = 20) -> str:
    view = frame[columns].head(rows).copy()
    for column in view.select_dtypes(include=["number"]).columns:
        view[column] = view[column].map(lambda value: f"{value:.5f}" if pd.notna(value) else "")
    return "\n".join([
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
        *["| " + " | ".join(map(str, row)) + " |" for row in view.to_numpy()],
    ])


def main() -> None:
    args = parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    compact_dir = Path(args.compact_dir)
    compact_manifest = require_compact_dependency(compact_dir)
    selected_combination = str(compact_manifest["selected_combination"])
    if selected_combination not in compact.COMPACT_COMBINATIONS:
        raise RuntimeError(f"Unknown compact combination: {selected_combination}")
    features = compact.COMPACT_COMBINATIONS[selected_combination]
    seeds = [int(item) for item in args.seeds.split(",") if item.strip()]
    portfolio_sizes = [int(item) for item in args.portfolio_sizes.split(",") if item.strip()]
    rebalance_days = [int(item) for item in args.rebalance_days.split(",") if item.strip()]
    costs_bps = [float(item) for item in args.cost_bps.split(",") if item.strip()]
    design = {
        "dependency": compact.EXPERIMENT_ID,
        "dependency_fingerprint": compact_manifest["design_fingerprint"],
        "selected_compact_combination": selected_combination,
        "features": features,
        "challengers": CHALLENGERS,
        "segments": SEGMENTS,
        "strategies": STRATEGIES,
        "portfolio_sizes": portfolio_sizes,
        "rebalance_days": rebalance_days,
        "costs_bps": costs_bps,
        "bootstrap_samples": args.bootstrap_samples,
        "horizon": args.horizon,
        "holdout_dates": args.holdout_dates,
        "splits": args.splits,
        "test_dates": args.test_dates,
        "train_dates": args.train_dates,
        "graph_lookback_dates": args.graph_lookback_dates,
        "top_k": args.top_k,
        "neighbors": args.neighbors,
        "seeds": seeds,
    }
    gate = load_gate(args.context_gate)
    fingerprint = assert_experiment_allowed(gate, EXPERIMENT_ID, DESIGN_SIGNATURE, design)
    print(f"Context gate approved {EXPERIMENT_ID} fingerprint={fingerprint}", flush=True)

    base.COMBINATIONS = {selected_combination: features}
    prices = base.load_prices(Path(args.db))
    feature_frame = base.build_base_features(prices, args.horizon, args.top_k)
    del prices
    prepared, sealed_manifest, leakage_rows, latent_manifests = base.build_splits(feature_frame, args)
    del feature_frame

    challenger_rows: list[dict[str, object]] = []
    total = sum(
        len(challenger_seed_list(spec, seeds)) for spec in CHALLENGERS.values()
    ) * len(prepared)
    completed = 0
    prediction_cache: dict[tuple[int, str, int], np.ndarray] = {}
    for split in prepared:
        for name, spec in CHALLENGERS.items():
            for seed in challenger_seed_list(spec, seeds):
                probability, metadata = fit_challenger(
                    split.train, split.test, features, name, seed, args.max_iter
                )
                prediction_cache[(split.split, name, seed)] = probability
                calibration = calibration_metrics(split.test["target"], probability)
                paper = base.portfolio_metrics(split.test, probability, 20)
                challenger_rows.append({
                    "split": split.split,
                    "seed": seed,
                    "challenger": name,
                    **metadata,
                    **calibration,
                    **paper,
                })
                completed += 1
                print(f"Challenger fit {completed}/{total}: split={split.split} {name} seed={seed}", flush=True)

    challenger_metrics = pd.DataFrame(challenger_rows)
    challenger_summary = summarize_challengers(challenger_metrics)
    winner = str(challenger_summary.iloc[0]["challenger"])
    winner_spec = CHALLENGERS[winner]
    winner_seeds = challenger_seed_list(winner_spec, seeds)

    lofo_rows: list[dict[str, object]] = []
    for split in prepared:
        for seed in winner_seeds:
            baseline_probability = prediction_cache[(split.split, winner, seed)]
            baseline = calibration_metrics(split.test["target"], baseline_probability)
            baseline.update(base.portfolio_metrics(split.test, baseline_probability, 20))
            for feature in features:
                reduced = [column for column in features if column != feature]
                probability, _ = fit_challenger(
                    split.train, split.test, reduced, winner, seed, args.max_iter
                )
                metrics = calibration_metrics(split.test["target"], probability)
                metrics.update(base.portfolio_metrics(split.test, probability, 20))
                lofo_rows.append({
                    "split": split.split,
                    "seed": seed,
                    "challenger": winner,
                    "dropped_feature": feature,
                    "auc_drop": baseline["auc"] - metrics["auc"],
                    "brier_harm": metrics["brier"] - baseline["brier"],
                    "excess_return_drop": baseline["mean_excess_return"] - metrics["mean_excess_return"],
                })
            print(f"LOFO complete: split={split.split} seed={seed}", flush=True)
    lofo = pd.DataFrame(lofo_rows)
    lofo_summary = lofo.groupby("dropped_feature")[["auc_drop", "brier_harm", "excess_return_drop"]].agg(
        ["mean", "std", "count"]
    ).reset_index()
    lofo_summary.columns = [
        "_".join(part for part in column if part) if isinstance(column, tuple) else column
        for column in lofo_summary.columns
    ]
    lofo_summary = lofo_summary.sort_values("auc_drop_mean", ascending=False).reset_index(drop=True)

    placebo_rows: list[dict[str, object]] = []
    for split in prepared:
        for seed in seeds:
            shuffled = split.train.copy()
            generator = np.random.default_rng(seed + split.split * 10000)
            shuffled["target"] = shuffled.groupby("date", sort=False)["target"].transform(
                lambda values: generator.permutation(values.to_numpy())
            )
            probability, _ = fit_challenger(
                shuffled, split.test, features, winner, seed, args.max_iter
            )
            placebo_rows.append({
                "split": split.split,
                "seed": seed,
                "challenger": winner,
                "auc": float(roc_auc_score(split.test["target"].astype(int), probability)),
                "brier": float(brier_score_loss(split.test["target"].astype(int), probability)),
            })
            print(f"Placebo complete: split={split.split} seed={seed}", flush=True)
    placebo = pd.DataFrame(placebo_rows)

    scenario_parts: list[pd.DataFrame] = []
    for split in prepared:
        for seed in winner_seeds:
            probability = prediction_cache[(split.split, winner, seed)]
            daily = pd.DataFrame(scenario_daily_rows(
                split.test, probability, portfolio_sizes, rebalance_days, costs_bps
            ))
            daily.insert(0, "seed", seed)
            daily.insert(0, "split", split.split)
            scenario_parts.append(daily)
            print(f"Scenario grid complete: split={split.split} seed={seed}", flush=True)
    scenario_daily = pd.concat(scenario_parts, ignore_index=True)
    scenarios = scenario_summary(scenario_daily)
    regimes = regime_summary(scenario_daily)
    bootstrap = weekly_block_bootstrap(scenario_daily, args.bootstrap_samples)

    challenger_metrics.to_csv(output / "challenger_metrics_by_run.csv", index=False)
    challenger_summary.to_csv(output / "challenger_summary.csv", index=False)
    lofo.to_csv(output / "leave_one_feature_out_by_run.csv", index=False)
    lofo_summary.to_csv(output / "leave_one_feature_out_summary.csv", index=False)
    placebo.to_csv(output / "placebo_label_metrics.csv", index=False)
    scenario_daily.to_csv(output / "scenario_daily.csv", index=False)
    scenarios.to_csv(output / "scenario_summary.csv", index=False)
    regimes.to_csv(output / "regime_summary.csv", index=False)
    bootstrap.to_csv(output / "weekly_block_bootstrap_ci.csv", index=False)
    pd.DataFrame(leakage_rows).to_csv(output / "leakage_audit.csv", index=False)
    (output / "sealed_holdout_manifest.json").write_text(
        json.dumps(sealed_manifest, indent=2), encoding="utf-8"
    )
    best_scenario = scenarios.iloc[0]
    results_for_gate = {
        "dependency": compact.EXPERIMENT_ID,
        "selected_compact_combination": selected_combination,
        "winning_challenger": winner,
        "winner_auc_mean": float(challenger_summary.iloc[0]["auc_mean"]),
        "winner_brier_mean": float(challenger_summary.iloc[0]["brier_mean"]),
        "winner_excess_return_mean": float(challenger_summary.iloc[0]["mean_excess_return_mean"]),
        "best_scenario": {
            "segment": str(best_scenario["segment"]),
            "strategy": str(best_scenario["strategy"]),
            "portfolio_size": int(best_scenario["portfolio_size"]),
            "rebalance_days": int(best_scenario["rebalance_days"]),
            "cost_bps": float(best_scenario["cost_bps"]),
            "mean_excess_return": float(best_scenario["mean_excess_return"]),
        },
        "placebo_auc_mean": float(placebo["auc"].mean()),
        "holdout_opened": False,
        "review_status": "pending",
    }
    (output / "context_gate_candidate_update.json").write_text(
        json.dumps(candidate_update(
            gate, EXPERIMENT_ID, DESIGN_SIGNATURE, design, results_for_gate
        ), indent=2),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "design_fingerprint": fingerprint,
        "paper_only": True,
        "selected_compact_combination": selected_combination,
        "winning_challenger": winner,
        "design": design,
        "latent_manifests": latent_manifests,
    }
    (output / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    readout = "\n".join([
        "# Comprehensive Five-Day Robustness Battery",
        "",
        "Paper-only. The 60-date final holdout remained sealed.",
        f"Dependency: `{compact.EXPERIMENT_ID}`",
        f"Winning challenger: `{winner}` on `{selected_combination}`.",
        "",
        "## Challenger models",
        "",
        markdown_table(challenger_summary, [
            "challenger", "auc_mean", "brier_mean", "log_loss_mean", "ece_10_mean",
            "mean_excess_return_mean", "win_rate_mean", "selection_score",
        ], rows=len(challenger_summary)),
        "",
        "## Leave-one-feature-out stability",
        "",
        markdown_table(lofo_summary, [
            "dropped_feature", "auc_drop_mean", "brier_harm_mean", "excess_return_drop_mean",
        ], rows=len(lofo_summary)),
        "",
        "## Weekly block-bootstrap strategy deltas",
        "",
        markdown_table(bootstrap, [
            "strategy", "observed_delta", "ci_2_5", "ci_97_5", "positive_probability",
        ], rows=len(bootstrap)),
        "",
        "## Best paper scenarios",
        "",
        markdown_table(scenarios, [
            "segment", "strategy", "portfolio_size", "rebalance_days", "cost_bps",
            "mean_excess_return", "win_rate", "mean_turnover", "worst_net_return",
        ], rows=25),
        "",
        "## Guardrails",
        "",
        "- Results are pre-holdout and require review before context-gate merge.",
        "- The scenario grid is exploratory and carries multiple-testing risk.",
        "- Placebo, calibration, regimes, costs, turnover, and block uncertainty must all be reviewed.",
        "- No brokerage or website behavior was changed.",
    ])
    (output / "robustness_readout.md").write_text(readout + "\n", encoding="utf-8")
    print(f"Outputs written to {output}", flush=True)
    print(f"Winning challenger: {winner}", flush=True)


if __name__ == "__main__":
    main()
