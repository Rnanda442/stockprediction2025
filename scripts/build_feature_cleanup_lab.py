#!/usr/bin/env python3
"""Audit individual ANN inputs before removing or retaining feature groups.

This paper-only lab measures cross-sectional permutation importance across the
same embargoed 5-day and 20-day evaluation windows used by the model labs. It
also records missingness, drift, and collinearity. Recommendations are limited
to keep, watch, or confirmatory_drop; this script never edits the live model.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

from build_probability_calibration_lab import (
    BASE_FEATURES,
    GRAPH_FEATURES,
    RANDOM_STATE,
    SPLITS,
    add_graph_context,
    build_trailing_features,
    load_prices,
    ranking_metrics,
    select_graph_neighbors,
)


FEATURE_GROUPS = {
    "ret_1d": "momentum",
    "ret_5d": "momentum",
    "ret_20d": "momentum",
    "ret_60d": "momentum",
    "z_ma20": "momentum",
    "ma_crossover": "momentum",
    "rsi_14": "momentum",
    "vol_20d": "risk",
    "vol_60d": "risk",
    "drawdown_60d": "risk",
    "dollar_vol_20d_log": "liquidity",
    "neighbor_ret_5d": "graph",
    "neighbor_ret_20d": "graph",
    "neighbor_vol_20d": "graph",
    "graph_similarity_mean": "graph",
    "graph_degree": "graph",
}
MODEL_FEATURES = (*BASE_FEATURES, *GRAPH_FEATURES)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--max-tickers", type=int, default=600)
    parser.add_argument("--graph-lookback-days", type=int, default=80)
    parser.add_argument("--graph-neighbors", type=int, default=8)
    parser.add_argument("--max-train-rows", type=int, default=120000)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    return parser.parse_args()


def add_target(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    labelled = frame.copy()
    labelled["future_return"] = (
        labelled.groupby("ticker", sort=False)["close_price"].shift(-horizon)
        / labelled["close_price"]
        - 1.0
    )
    labelled["target_up"] = (labelled["future_return"] > 0).astype(float)
    labelled.loc[labelled["future_return"].isna(), "target_up"] = np.nan
    return labelled


def population_stability_index(
    training_values: pd.Series,
    test_values: pd.Series,
    bins: int = 10,
) -> float:
    training = pd.to_numeric(training_values, errors="coerce").dropna().to_numpy()
    test = pd.to_numeric(test_values, errors="coerce").dropna().to_numpy()
    if len(training) < 50 or len(test) < 50:
        return np.nan
    quantiles = np.unique(np.quantile(training, np.linspace(0.0, 1.0, bins + 1)))
    if len(quantiles) < 3:
        return 0.0
    quantiles[0] = -np.inf
    quantiles[-1] = np.inf
    training_counts = np.histogram(training, bins=quantiles)[0].astype(float)
    test_counts = np.histogram(test, bins=quantiles)[0].astype(float)
    training_share = np.clip(training_counts / training_counts.sum(), 1e-6, None)
    test_share = np.clip(test_counts / test_counts.sum(), 1e-6, None)
    return float(np.sum((test_share - training_share) * np.log(test_share / training_share)))


def feature_quality_rows(
    horizon: int,
    split: int,
    training: pd.DataFrame,
    evaluation: pd.DataFrame,
) -> list[dict[str, object]]:
    numeric_training = training[list(MODEL_FEATURES)].apply(pd.to_numeric, errors="coerce")
    correlations = numeric_training.corr().abs()
    rows: list[dict[str, object]] = []
    for feature in MODEL_FEATURES:
        other_correlations = correlations.loc[feature].drop(feature, errors="ignore")
        rows.append(
            {
                "horizon": horizon,
                "split": split,
                "feature": feature,
                "feature_group": FEATURE_GROUPS[feature],
                "train_missing_rate": float(training[feature].isna().mean()),
                "test_missing_rate": float(evaluation[feature].isna().mean()),
                "train_mean": float(numeric_training[feature].mean()),
                "train_std": float(numeric_training[feature].std()),
                "maximum_absolute_correlation": (
                    float(other_correlations.max()) if not other_correlations.empty else 0.0
                ),
                "population_stability_index": population_stability_index(
                    training[feature], evaluation[feature]
                ),
            }
        )
    return rows


def model_metrics(
    evaluation: pd.DataFrame,
    probabilities: np.ndarray,
    top_k: int,
    cost_bps: float,
) -> dict[str, float]:
    labels = evaluation["target_up"].to_numpy(dtype=int)
    mean_return, win_rate, excess_return = ranking_metrics(
        evaluation, probabilities, top_k, cost_bps
    )
    return {
        "auc": float(roc_auc_score(labels, probabilities)),
        "brier": float(np.mean((probabilities - labels) ** 2)),
        "mean_net_return": mean_return,
        "win_rate": win_rate,
        "mean_excess_vs_universe": excess_return,
    }


def permute_within_dates(
    inputs: np.ndarray,
    dates: np.ndarray,
    feature_index: int,
    random_state: int,
) -> np.ndarray:
    permuted = inputs.copy()
    generator = np.random.default_rng(random_state)
    for date in np.unique(dates):
        indexes = np.flatnonzero(dates == date)
        if len(indexes) > 1:
            permuted[indexes, feature_index] = generator.permutation(
                permuted[indexes, feature_index]
            )
    return permuted


def run_split(
    features: pd.DataFrame,
    horizon: int,
    split: int,
    training_cutoff: str,
    test_start: str,
    test_end: str,
    args: argparse.Namespace,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    cutoff = pd.Timestamp(training_cutoff)
    test_start_date = pd.Timestamp(test_start)
    test_end_date = pd.Timestamp(test_end)
    labelled = add_target(features, horizon)
    edges = select_graph_neighbors(
        labelled,
        cutoff,
        args.max_tickers,
        args.graph_lookback_days,
        args.graph_neighbors,
    )
    graph_frame = add_graph_context(labelled[labelled["date"] <= test_end_date], edges)
    raw_training = graph_frame[graph_frame["date"] <= cutoff]
    raw_evaluation = graph_frame[
        (graph_frame["date"] >= test_start_date)
        & (graph_frame["date"] <= test_end_date)
    ]
    quality = feature_quality_rows(
        horizon, split, raw_training, raw_evaluation
    )
    training = raw_training.dropna(
        subset=[*MODEL_FEATURES, "target_up", "future_return"]
    ).copy()
    evaluation = raw_evaluation.dropna(
        subset=[*MODEL_FEATURES, "target_up", "future_return"]
    ).copy()
    if training.empty or evaluation.empty:
        raise RuntimeError(f"Split {split} horizon {horizon} has an empty model region")
    if training["target_up"].nunique() < 2 or evaluation["target_up"].nunique() < 2:
        raise RuntimeError(f"Split {split} horizon {horizon} has one-class labels")
    if len(training) > args.max_train_rows:
        training = training.sample(
            args.max_train_rows, random_state=RANDOM_STATE
        ).sort_values("date")

    scaler = StandardScaler()
    training_inputs = scaler.fit_transform(training[list(MODEL_FEATURES)])
    evaluation_inputs = scaler.transform(evaluation[list(MODEL_FEATURES)])
    evaluation_dates = evaluation["date"].to_numpy()
    permutation_rows: list[dict[str, object]] = []
    for seed_offset in range(args.seeds):
        seed = RANDOM_STATE + seed_offset
        model = MLPClassifier(
            hidden_layer_sizes=(48, 24),
            activation="relu",
            solver="adam",
            alpha=0.001,
            batch_size=512,
            learning_rate_init=0.001,
            max_iter=80,
            early_stopping=True,
            validation_fraction=0.15,
            n_iter_no_change=8,
            random_state=seed,
        )
        model.fit(training_inputs, training["target_up"].astype(int))
        baseline_probabilities = model.predict_proba(evaluation_inputs)[:, 1]
        baseline = model_metrics(
            evaluation,
            baseline_probabilities,
            args.top_k,
            args.cost_bps,
        )
        for feature_index, feature in enumerate(MODEL_FEATURES):
            permuted_inputs = permute_within_dates(
                evaluation_inputs,
                evaluation_dates,
                feature_index,
                seed * 1000 + feature_index,
            )
            permuted_probabilities = model.predict_proba(permuted_inputs)[:, 1]
            permuted = model_metrics(
                evaluation,
                permuted_probabilities,
                args.top_k,
                args.cost_bps,
            )
            permutation_rows.append(
                {
                    "horizon": horizon,
                    "split": split,
                    "seed": seed,
                    "feature": feature,
                    "feature_group": FEATURE_GROUPS[feature],
                    "baseline_auc": baseline["auc"],
                    "permuted_auc": permuted["auc"],
                    "auc_importance": baseline["auc"] - permuted["auc"],
                    "baseline_brier": baseline["brier"],
                    "permuted_brier": permuted["brier"],
                    "brier_importance": permuted["brier"] - baseline["brier"],
                    "baseline_mean_net_return": baseline["mean_net_return"],
                    "baseline_win_rate": baseline["win_rate"],
                    "baseline_excess_vs_universe": baseline[
                        "mean_excess_vs_universe"
                    ],
                    "permuted_excess_vs_universe": permuted[
                        "mean_excess_vs_universe"
                    ],
                    "excess_return_importance": baseline[
                        "mean_excess_vs_universe"
                    ]
                    - permuted["mean_excess_vs_universe"],
                }
            )

    audit = {
        "horizon": horizon,
        "split": split,
        "training_and_graph_cutoff": cutoff.date().isoformat(),
        "test_start": test_start_date.date().isoformat(),
        "test_end": test_end_date.date().isoformat(),
        "embargo_trading_days": horizon,
        "graph_uses_test_or_future_rows": False,
        "permutation_uses_future_rows_for_training": False,
        "live_model_modified": False,
        "train_rows": int(len(training)),
        "test_rows": int(len(evaluation)),
        "tickers": int(evaluation["ticker"].nunique()),
    }
    return permutation_rows, quality, audit


def summarize_importance(permutation: pd.DataFrame) -> pd.DataFrame:
    return (
        permutation.groupby(["horizon", "feature", "feature_group"], as_index=False)
        .agg(
            auc_importance=("auc_importance", "mean"),
            minimum_auc_importance=("auc_importance", "min"),
            auc_importance_std=("auc_importance", "std"),
            auc_positive_fraction=("auc_importance", lambda values: float((values > 0).mean())),
            brier_importance=("brier_importance", "mean"),
            brier_positive_fraction=(
                "brier_importance", lambda values: float((values > 0).mean())
            ),
            excess_return_importance=("excess_return_importance", "mean"),
            minimum_excess_return_importance=("excess_return_importance", "min"),
            excess_positive_fraction=(
                "excess_return_importance", lambda values: float((values > 0).mean())
            ),
        )
        .sort_values(
            ["horizon", "excess_return_importance", "auc_importance"],
            ascending=[True, False, False],
        )
    )


def build_registry(
    permutation: pd.DataFrame,
    quality: pd.DataFrame,
) -> pd.DataFrame:
    importance = permutation.groupby(["feature", "feature_group"], as_index=False).agg(
        auc_importance=("auc_importance", "mean"),
        auc_positive_fraction=("auc_importance", lambda values: float((values > 0).mean())),
        brier_importance=("brier_importance", "mean"),
        brier_positive_fraction=(
            "brier_importance", lambda values: float((values > 0).mean())
        ),
        excess_return_importance=("excess_return_importance", "mean"),
        excess_positive_fraction=(
            "excess_return_importance", lambda values: float((values > 0).mean())
        ),
    )
    quality_summary = quality.groupby(["feature", "feature_group"], as_index=False).agg(
        maximum_train_missing_rate=("train_missing_rate", "max"),
        maximum_test_missing_rate=("test_missing_rate", "max"),
        minimum_train_std=("train_std", "min"),
        maximum_absolute_correlation=("maximum_absolute_correlation", "max"),
        maximum_population_stability_index=("population_stability_index", "max"),
    )
    registry = importance.merge(
        quality_summary, on=["feature", "feature_group"], how="left"
    )

    def classify(row: pd.Series) -> tuple[str, str]:
        if row["minimum_train_std"] <= 1e-8:
            return "confirmatory_drop", "near-zero variance in at least one split"
        if (
            row["auc_importance"] < 0
            and row["excess_return_importance"] < 0
            and row["auc_positive_fraction"] <= 0.33
            and row["excess_positive_fraction"] <= 0.33
        ):
            return "confirmatory_drop", "usually harms both discrimination and ranking"
        if (
            row["auc_importance"] > 0
            and row["brier_importance"] > 0
            and row["excess_return_importance"] > 0
            and row["auc_positive_fraction"] >= 0.67
            and row["excess_positive_fraction"] >= 0.67
        ):
            if row["maximum_population_stability_index"] >= 0.25:
                return "watch", "helpful but exhibits material distribution drift"
            return "keep", "helps discrimination, probability loss, and ranking consistently"
        return "watch", "mixed importance across seeds, horizons, or time splits"

    decisions = registry.apply(classify, axis=1, result_type="expand")
    registry["decision"] = decisions[0]
    registry["reason"] = decisions[1]
    order = pd.CategoricalDtype(["keep", "watch", "confirmatory_drop"], ordered=True)
    registry["decision"] = registry["decision"].astype(order)
    return registry.sort_values(
        ["decision", "excess_return_importance", "auc_importance"],
        ascending=[True, False, False],
    )


def write_readout(
    output_path: Path,
    registry: pd.DataFrame,
    summary: pd.DataFrame,
) -> None:
    registry_columns = [
        "feature",
        "feature_group",
        "decision",
        "auc_importance",
        "brier_importance",
        "excess_return_importance",
        "auc_positive_fraction",
        "excess_positive_fraction",
        "maximum_population_stability_index",
        "maximum_absolute_correlation",
        "reason",
    ]
    group_summary = summary.groupby(["horizon", "feature_group"], as_index=False).agg(
        auc_importance=("auc_importance", "mean"),
        brier_importance=("brier_importance", "mean"),
        excess_return_importance=("excess_return_importance", "mean"),
    )
    lines = [
        "# ANN Feature Cleanup Lab",
        "",
        "Paper-only. Positive importance means the feature helped the unpermuted model.",
        "No live feature is removed by this lab.",
        "",
        "## Variable registry",
        "",
        registry[registry_columns].round(5).to_string(index=False),
        "",
        "## Group-level context",
        "",
        group_summary.round(5).to_string(index=False),
        "",
        "Only confirmatory_drop features should advance to a leave-one-feature-out rerun.",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prices = load_prices(args.db)
    features = build_trailing_features(prices)
    permutation_rows: list[dict[str, object]] = []
    quality_rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []
    for horizon, split_definitions in SPLITS.items():
        for split, cutoff, test_start, test_end in split_definitions:
            split_permutation, split_quality, split_audit = run_split(
                features,
                horizon,
                split,
                cutoff,
                test_start,
                test_end,
                args,
            )
            permutation_rows.extend(split_permutation)
            quality_rows.extend(split_quality)
            audit_rows.append(split_audit)
            print(f"Completed feature audit horizon={horizon} split={split}", flush=True)

    permutation = pd.DataFrame(permutation_rows)
    quality = pd.DataFrame(quality_rows)
    summary = summarize_importance(permutation)
    registry = build_registry(permutation, quality)
    permutation.to_csv(args.output_dir / "feature_permutation_by_split.csv", index=False)
    summary.to_csv(args.output_dir / "feature_permutation_summary.csv", index=False)
    quality.to_csv(args.output_dir / "feature_quality.csv", index=False)
    registry.to_csv(args.output_dir / "feature_cleanup_registry.csv", index=False)
    pd.DataFrame(audit_rows).to_csv(
        args.output_dir / "feature_cleanup_leakage_audit.csv", index=False
    )
    write_readout(args.output_dir / "feature_cleanup_readout.txt", registry, summary)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": str(args.db),
        "architecture": "direction_relu_graph",
        "status": "paper_only",
        "method": "within-date permutation importance",
        "features": list(MODEL_FEATURES),
        "feature_groups": FEATURE_GROUPS,
        "horizons": sorted(SPLITS),
        "seeds": args.seeds,
        "recommendation_levels": ["keep", "watch", "confirmatory_drop"],
        "guardrail": "No feature is removed without a confirmatory leave-one-out rerun.",
    }
    (args.output_dir / "experiment_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote feature cleanup lab to {args.output_dir}", flush=True)


if __name__ == "__main__":
    main()
