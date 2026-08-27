#!/usr/bin/env python3
"""Novel compact-feature, ridge-vs-ANN, consensus, and cost lab."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

import build_combination_similarity_lab as base
from context_gate import assert_experiment_allowed, candidate_update, load_gate


EXPERIMENT_ID = "compact_consensus_cost_5d_v1"
DESIGN_SIGNATURE = "compact-consensus-cost-v1:h5:novel-pruned-groups:ridge+relu:3splits:holdout60"

COMPACT_COMBINATIONS = {
    "compact_rank_core": ["drawdown_60d", "graph_degree", "ret_20d"],
    "compact_stable": [
        "drawdown_60d",
        "graph_degree",
        "ret_20d",
        "dollar_vol_20d_log",
        "vol_20d",
        "vol_60d",
    ],
    "compact_latent_z": [
        "drawdown_60d",
        "graph_degree",
        "ret_20d",
        "dollar_vol_20d_log",
        "vol_20d",
        "vol_60d",
        "latent_z",
    ],
    "compact_similarity": [
        "drawdown_60d",
        "graph_degree",
        "ret_20d",
        "dollar_vol_20d_log",
        "vol_20d",
        "vol_60d",
        "similarity_weighted_momentum",
    ],
    "compact_latent_similarity": [
        "drawdown_60d",
        "graph_degree",
        "ret_20d",
        "dollar_vol_20d_log",
        "vol_20d",
        "vol_60d",
        "latent_z",
        "similarity_weighted_momentum",
    ],
}

MODEL_SPECS = {
    "ann_relu_48_24": {"kind": "ann"},
    "ridge_logistic_c0_1": {"kind": "ridge", "c": 0.1},
    "ridge_logistic_c1_0": {"kind": "ridge", "c": 1.0},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--context-gate", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--holdout-dates", type=int, default=60)
    parser.add_argument("--splits", type=int, default=3)
    parser.add_argument("--test-dates", type=int, default=30)
    parser.add_argument("--train-dates", type=int, default=252)
    parser.add_argument("--graph-lookback-dates", type=int, default=120)
    parser.add_argument("--top-k", type=int, default=600)
    parser.add_argument("--neighbors", type=int, default=10)
    parser.add_argument("--portfolio-size", type=int, default=20)
    parser.add_argument("--max-iter", type=int, default=100)
    parser.add_argument("--seeds", default="442,2025,9001")
    parser.add_argument("--cost-bps", default="0,5,10,25")
    return parser.parse_args()


def fit_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    model_name: str,
    seed: int,
    max_iter: int,
) -> tuple[np.ndarray, dict[str, object]]:
    spec = MODEL_SPECS[model_name]
    if spec["kind"] == "ann":
        probability, metadata, _, _ = base.fit_ann(
            train,
            test,
            features,
            architecture="relu_48_24",
            seed=seed,
            max_iter=max_iter,
        )
        return probability, metadata

    scaler = StandardScaler()
    train_matrix = scaler.fit_transform(train[features].to_numpy(dtype=float))
    test_matrix = scaler.transform(test[features].to_numpy(dtype=float))
    model = LogisticRegression(
        penalty="l2",
        C=float(spec["c"]),
        solver="lbfgs",
        max_iter=500,
        random_state=seed,
    )
    model.fit(train_matrix, train["target"].astype(int).to_numpy())
    return model.predict_proba(test_matrix)[:, 1], {
        "iterations": int(np.max(model.n_iter_)),
        "converged": bool(np.max(model.n_iter_) < model.max_iter),
    }


def summarize_models(metrics: pd.DataFrame) -> pd.DataFrame:
    values = ["auc", "brier", "mean_net_return", "mean_excess_return", "win_rate"]
    summary = metrics.groupby(["combination", "model"])[values].agg(["mean", "std", "count"]).reset_index()
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


def day_zscore(values: pd.Series) -> pd.Series:
    deviation = values.std(ddof=0)
    if not np.isfinite(deviation) or deviation <= 1e-12:
        return pd.Series(0.0, index=values.index)
    return values.sub(values.mean()).div(deviation)


def select_strategy(day: pd.DataFrame, strategy: str, size: int) -> pd.DataFrame:
    working = day.copy()
    if strategy == "plain_probability":
        working["strategy_score"] = working["probability"]
    elif strategy == "similarity_weighted":
        working["strategy_score"] = (
            working["probability"]
            + 0.04 * day_zscore(working["graph_similarity_mean"])
            + 0.04 * day_zscore(working["neighbor_confirmation"])
        )
    elif strategy == "consensus_gate":
        eligible = working.loc[
            working["neighbor_confirmation"].ge(0.0)
            & working["graph_similarity_mean"].ge(working["graph_similarity_mean"].median())
        ].copy()
        if len(eligible) >= size:
            working = eligible
        working["strategy_score"] = working["probability"]
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
    return working.nlargest(min(size, len(working)), "strategy_score")


def strategy_cost_metrics(
    test: pd.DataFrame,
    probability: np.ndarray,
    portfolio_size: int,
    costs_bps: list[float],
) -> list[dict[str, object]]:
    columns = [
        "date",
        "ticker",
        "forward_return",
        "graph_similarity_mean",
        "neighbor_confirmation",
    ]
    scored = test[columns].copy()
    scored["probability"] = probability
    rows: list[dict[str, object]] = []
    for strategy in ("plain_probability", "similarity_weighted", "consensus_gate"):
        previous: set[str] = set()
        daily: list[dict[str, float]] = []
        for date, day in scored.groupby("date", sort=True):
            selected = select_strategy(day, strategy, portfolio_size)
            current = set(selected["ticker"].astype(str))
            turnover = 1.0 if not previous else 1.0 - len(current & previous) / max(1, len(current))
            daily.append({
                "date": date,
                "gross_return": float(selected["forward_return"].mean()),
                "universe_return": float(day["forward_return"].mean()),
                "turnover": float(turnover),
            })
            previous = current
        daily_frame = pd.DataFrame(daily)
        for cost_bps in costs_bps:
            cost = daily_frame["turnover"].mul(cost_bps / 10000.0)
            net = daily_frame["gross_return"].sub(cost)
            excess = net.sub(daily_frame["universe_return"])
            rows.append({
                "strategy": strategy,
                "cost_bps": cost_bps,
                "dates": len(daily_frame),
                "mean_gross_return": float(daily_frame["gross_return"].mean()),
                "mean_net_return": float(net.mean()),
                "mean_excess_return": float(excess.mean()),
                "win_rate": float(excess.gt(0.0).mean()),
                "mean_turnover": float(daily_frame["turnover"].mean()),
                "worst_net_return": float(net.min()),
            })
    return rows


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
    seeds = [int(item) for item in args.seeds.split(",") if item.strip()]
    costs_bps = [float(item) for item in args.cost_bps.split(",") if item.strip()]
    design = {
        "horizon": args.horizon,
        "holdout_dates": args.holdout_dates,
        "splits": args.splits,
        "test_dates": args.test_dates,
        "train_dates": args.train_dates,
        "graph_lookback_dates": args.graph_lookback_dates,
        "top_k": args.top_k,
        "neighbors": args.neighbors,
        "portfolio_size": args.portfolio_size,
        "costs_bps": costs_bps,
        "combinations": COMPACT_COMBINATIONS,
        "models": MODEL_SPECS,
        "seeds": seeds,
    }
    gate = load_gate(args.context_gate)
    fingerprint = assert_experiment_allowed(
        gate,
        EXPERIMENT_ID,
        DESIGN_SIGNATURE,
        design,
    )
    print(f"Context gate approved {EXPERIMENT_ID} fingerprint={fingerprint}", flush=True)

    base.COMBINATIONS = COMPACT_COMBINATIONS
    prices = base.load_prices(Path(args.db))
    features = base.build_base_features(prices, args.horizon, args.top_k)
    del prices
    prepared, sealed_manifest, leakage_rows, latent_manifests = base.build_splits(features, args)
    del features

    model_rows: list[dict[str, object]] = []
    total = len(prepared) * len(COMPACT_COMBINATIONS) * (len(seeds) + 2)
    completed = 0
    for split in prepared:
        for combination, columns in COMPACT_COMBINATIONS.items():
            for model_name, spec in MODEL_SPECS.items():
                model_seeds = seeds if spec["kind"] == "ann" else [0]
                for seed in model_seeds:
                    probability, metadata = fit_predict(
                        split.train,
                        split.test,
                        columns,
                        model_name,
                        seed,
                        args.max_iter,
                    )
                    model_rows.append({
                        "split": split.split,
                        "seed": seed,
                        "combination": combination,
                        "model": model_name,
                        "feature_count": len(columns),
                        **metadata,
                        **base.evaluate_predictions(split.test, probability, args.portfolio_size),
                    })
                    completed += 1
                    print(
                        f"Model fit {completed}/{total}: split={split.split} "
                        f"{combination} {model_name} seed={seed}",
                        flush=True,
                    )

    model_metrics = pd.DataFrame(model_rows)
    model_summary = summarize_models(model_metrics)
    selected = model_summary.iloc[0]
    selected_combination = str(selected["combination"])
    selected_model = str(selected["model"])
    selected_spec = MODEL_SPECS[selected_model]
    selected_seeds = seeds if selected_spec["kind"] == "ann" else [0]

    strategy_rows: list[dict[str, object]] = []
    for split in prepared:
        for seed in selected_seeds:
            probability, metadata = fit_predict(
                split.train,
                split.test,
                COMPACT_COMBINATIONS[selected_combination],
                selected_model,
                seed,
                args.max_iter,
            )
            for row in strategy_cost_metrics(
                split.test,
                probability,
                args.portfolio_size,
                costs_bps,
            ):
                strategy_rows.append({
                    "split": split.split,
                    "seed": seed,
                    "combination": selected_combination,
                    "model": selected_model,
                    **metadata,
                    **row,
                })
            print(f"Cost scenarios: split={split.split} seed={seed}", flush=True)

    strategies = pd.DataFrame(strategy_rows)
    strategy_summary = strategies.groupby(["strategy", "cost_bps"])[
        ["mean_gross_return", "mean_net_return", "mean_excess_return", "win_rate", "mean_turnover", "worst_net_return"]
    ].agg(["mean", "std", "count"]).reset_index()
    strategy_summary.columns = [
        "_".join(part for part in column if part) if isinstance(column, tuple) else column
        for column in strategy_summary.columns
    ]

    model_metrics.to_csv(output / "compact_model_metrics_by_run.csv", index=False)
    model_summary.to_csv(output / "compact_model_summary.csv", index=False)
    strategies.to_csv(output / "consensus_cost_scenarios_by_run.csv", index=False)
    strategy_summary.to_csv(output / "consensus_cost_summary.csv", index=False)
    pd.DataFrame(leakage_rows).to_csv(output / "leakage_audit.csv", index=False)
    (output / "sealed_holdout_manifest.json").write_text(
        json.dumps(sealed_manifest, indent=2), encoding="utf-8"
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "design_fingerprint": fingerprint,
        "paper_only": True,
        "selected_combination": selected_combination,
        "selected_model": selected_model,
        "design": design,
        "latent_manifests": latent_manifests,
    }
    (output / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    results_for_gate = {
        "selected_combination": selected_combination,
        "selected_model": selected_model,
        "selected_auc_mean": float(selected["auc_mean"]),
        "selected_brier_mean": float(selected["brier_mean"]),
        "selected_excess_return_mean": float(selected["mean_excess_return_mean"]),
        "holdout_opened": False,
        "review_status": "pending",
    }
    (output / "context_gate_candidate_update.json").write_text(
        json.dumps(candidate_update(
            gate,
            EXPERIMENT_ID,
            DESIGN_SIGNATURE,
            design,
            results_for_gate,
        ), indent=2),
        encoding="utf-8",
    )
    readout = "\n".join([
        "# Compact Consensus and Cost Lab",
        "",
        "Paper-only. The final holdout remained sealed.",
        f"Context fingerprint: `{fingerprint}`",
        "",
        "## Model comparison",
        "",
        markdown_table(model_summary, [
            "combination", "model", "auc_mean", "brier_mean",
            "mean_excess_return_mean", "win_rate_mean", "selection_score",
        ], rows=len(model_summary)),
        "",
        "## Consensus and transaction-cost sensitivity",
        "",
        markdown_table(strategy_summary, [
            "strategy", "cost_bps", "mean_net_return_mean",
            "mean_excess_return_mean", "win_rate_mean", "mean_turnover_mean",
            "worst_net_return_mean",
        ], rows=len(strategy_summary)),
        "",
        "## Guardrails",
        "",
        "- Results are pre-holdout and require review before being merged into the context gate.",
        "- Daily rebalancing with overlapping five-day labels is a paper approximation.",
        "- No brokerage, website probability, or live portfolio behavior was changed.",
    ])
    (output / "compact_consensus_readout.md").write_text(readout + "\n", encoding="utf-8")
    print(f"Outputs written to {output}", flush=True)
    print(f"Provisional selection: {selected_combination} / {selected_model}", flush=True)


if __name__ == "__main__":
    main()
