#!/usr/bin/env python3
"""Chronological feature-group, activation, calibration, and ranking-loss lab.

This experiment is intentionally paper-only. It reuses the leakage-safe price
panel and graph construction from build_graph_scenario_lab, then evaluates:

* ANN feature-group only/leave-one-group-out ablations.
* ReLU versus Tanh hidden activations.
* Binary direction, squared-return, and pairwise-ranking objectives.
* Platt calibration and a multi-objective ranking blend.

No result from this file automatically replaces a production model.
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from build_graph_scenario_lab import (
    BASE_FEATURES,
    GRAPH_FEATURES,
    attach_graph_context,
    bounded_training_sample,
    chronological_splits,
    fit_training_graph,
    load_frame,
)


ROOT = Path(__file__).resolve().parents[1]
MOMENTUM_FEATURES = (
    "pct_1d",
    "pct_2d",
    "pct_3d",
    "pct_5d",
    "momentum_slope_5d",
    "ma_crossover",
    "ret_10d",
    "ret_20d",
    "ret_60d",
    "riskadj_mom_60d",
    "trend_slope_60d",
    "trend_r2_60d",
    "z_ma20",
)
RISK_FEATURES = (
    "volatility_5d",
    "volatility_10d",
    "vol_20d",
    "vol_60d",
    "bb_width_20d",
    "ac1_5d",
    "max_dd_60d",
    "time_since_max_60d",
)
LIQUIDITY_FEATURES = ("dollar_vol_20d",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "vectorized.db")
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "analytics" / "feature_loss_lab"
    )
    parser.add_argument("--horizons", default="5,20")
    parser.add_argument("--lookback-dates", type=int, default=756)
    parser.add_argument("--train-window-dates", type=int, default=504)
    parser.add_argument("--graph-window-dates", type=int, default=126)
    parser.add_argument("--test-dates", type=int, default=90)
    parser.add_argument("--splits", type=int, default=3)
    parser.add_argument("--neighbors", type=int, default=10)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--rebalance-every", type=int, default=5)
    parser.add_argument("--transaction-cost-bps", type=float, default=10.0)
    parser.add_argument("--max-train-rows", type=int, default=50_000)
    parser.add_argument("--max-pairs", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=442)
    return parser.parse_args()


def feature_sets() -> dict[str, tuple[str, ...]]:
    momentum = set(MOMENTUM_FEATURES)
    risk = set(RISK_FEATURES)
    liquidity = set(LIQUIDITY_FEATURES)
    base = tuple(BASE_FEATURES)
    return {
        "all_base": base,
        "momentum_only": MOMENTUM_FEATURES,
        "risk_only": RISK_FEATURES,
        "liquidity_only": LIQUIDITY_FEATURES,
        "without_momentum": tuple(value for value in base if value not in momentum),
        "without_risk": tuple(value for value in base if value not in risk),
        "without_liquidity": tuple(value for value in base if value not in liquidity),
        "all_plus_graph": (*base, *GRAPH_FEATURES),
        "graph_only": tuple(GRAPH_FEATURES),
    }


def safe_auc(y_true: np.ndarray, score: np.ndarray) -> float:
    return float(roc_auc_score(y_true, score)) if np.unique(y_true).size > 1 else np.nan


def expected_calibration_error(
    y_true: np.ndarray,
    probability: np.ndarray,
    bins: int = 10,
) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(y_true)
    error = 0.0
    for lower, upper in zip(edges[:-1], edges[1:]):
        include = (probability >= lower) & (
            (probability <= upper) if upper == 1.0 else (probability < upper)
        )
        if include.any():
            error += include.mean() * abs(float(y_true[include].mean()) - float(probability[include].mean()))
    return float(error) if total else np.nan


def ann_classifier(activation: str, seed: int, hidden: tuple[int, ...] = (32, 16)):
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        MLPClassifier(
            hidden_layer_sizes=hidden,
            activation=activation,
            solver="adam",
            alpha=0.004,
            batch_size=512,
            learning_rate_init=0.001,
            max_iter=70,
            early_stopping=True,
            validation_fraction=0.12,
            n_iter_no_change=7,
            random_state=seed,
        ),
    )


def ann_regressor(activation: str, seed: int):
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        MLPRegressor(
            hidden_layer_sizes=(32, 16),
            activation=activation,
            solver="adam",
            alpha=0.004,
            batch_size=512,
            learning_rate_init=0.001,
            max_iter=70,
            early_stopping=True,
            validation_fraction=0.12,
            n_iter_no_change=7,
            random_state=seed,
        ),
    )


def portfolio_metrics(
    test: pd.DataFrame,
    score: np.ndarray,
    horizon: int,
    top_k: int,
    rebalance_every: int,
    transaction_cost_bps: float,
) -> dict[str, float | int]:
    target = f"future_return_{horizon}d"
    scored = test[["begins_at", "ticker", target]].copy()
    scored["score"] = np.asarray(score, dtype=float)
    dates = scored["begins_at"].drop_duplicates().sort_values().tolist()
    decision_dates = dates[:: max(1, rebalance_every)]
    round_trip_cost = 2.0 * transaction_cost_bps / 10_000.0
    cohort_returns: list[float] = []
    benchmark_returns: list[float] = []

    for date in decision_dates:
        day = scored[scored["begins_at"] == date].dropna(subset=[target, "score"])
        if day.empty:
            continue
        chosen = day.nlargest(min(top_k, len(day)), "score")
        cohort_returns.append(float(chosen[target].mean() - round_trip_cost))
        benchmark_returns.append(float(day[target].mean() - round_trip_cost))

    if not cohort_returns:
        return {
            "cohorts": 0,
            "mean_net_return": np.nan,
            "win_rate": np.nan,
            "mean_excess_vs_universe": np.nan,
            "average_sleeve_compound_return": np.nan,
            "worst_sleeve_max_drawdown": np.nan,
        }

    returns = pd.Series(cohort_returns, dtype=float)
    sleeve_count = max(1, math.ceil(horizon / max(1, rebalance_every)))
    compounds: list[float] = []
    drawdowns: list[float] = []
    for offset in range(sleeve_count):
        sleeve = returns.iloc[offset::sleeve_count]
        if sleeve.empty:
            continue
        wealth = (1.0 + sleeve).cumprod()
        drawdown = wealth / wealth.cummax() - 1.0
        compounds.append(float(wealth.iloc[-1] - 1.0))
        drawdowns.append(float(drawdown.min()))
    return {
        "cohorts": len(returns),
        "mean_net_return": float(returns.mean()),
        "win_rate": float((returns > 0.0).mean()),
        "mean_excess_vs_universe": float(
            np.mean(np.asarray(cohort_returns) - np.asarray(benchmark_returns))
        ),
        "average_sleeve_compound_return": float(np.mean(compounds)),
        "worst_sleeve_max_drawdown": float(np.min(drawdowns)),
    }


def probability_metrics(
    train: pd.DataFrame,
    test: pd.DataFrame,
    horizon: int,
    score: np.ndarray,
    probability: np.ndarray | None,
) -> dict[str, float]:
    target = f"future_return_{horizon}d"
    y_train = (train[target].to_numpy(dtype=float) > 0.0).astype(int)
    y_test = (test[target].to_numpy(dtype=float) > 0.0).astype(int)
    result = {
        "auc": safe_auc(y_test, np.asarray(score, dtype=float)),
        "accuracy": np.nan,
        "brier": np.nan,
        "brier_skill": np.nan,
        "calibration_error": np.nan,
    }
    if probability is None:
        return result
    probability = np.clip(np.asarray(probability, dtype=float), 0.0, 1.0)
    base_rate = float(y_train.mean())
    null_brier = float(np.mean(np.square(y_test - base_rate)))
    brier = float(brier_score_loss(y_test, probability))
    result.update(
        {
            "accuracy": float(accuracy_score(y_test, probability >= 0.5)),
            "brier": brier,
            "brier_skill": 1.0 - brier / null_brier if null_brier > 0 else np.nan,
            "calibration_error": expected_calibration_error(y_test, probability),
        }
    )
    return result


def evaluate_score(
    train: pd.DataFrame,
    test: pd.DataFrame,
    horizon: int,
    score: np.ndarray,
    probability: np.ndarray | None,
    top_k: int,
    rebalance_every: int,
    transaction_cost_bps: float,
) -> dict[str, float | int]:
    return {
        **probability_metrics(train, test, horizon, score, probability),
        **portfolio_metrics(
            test,
            score,
            horizon,
            top_k,
            rebalance_every,
            transaction_cost_bps,
        ),
    }


def chronological_fit_and_calibration(
    train: pd.DataFrame,
    horizon: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = train["begins_at"].drop_duplicates().sort_values().tolist()
    calibration_count = min(max(15, len(dates) // 6), max(15, len(dates) // 3))
    calibration_dates = dates[-calibration_count:]
    calibration_start = len(dates) - calibration_count
    fit_end = max(1, calibration_start - horizon)
    fit_dates = dates[:fit_end]
    fit = train[train["begins_at"].isin(fit_dates)]
    calibration = train[train["begins_at"].isin(calibration_dates)]
    if fit.empty or calibration.empty:
        midpoint = max(1, int(len(dates) * 0.8))
        fit = train[train["begins_at"].isin(dates[:midpoint])]
        calibration = train[train["begins_at"].isin(dates[midpoint:])]
    return fit, calibration


def filled_matrix(
    train: pd.DataFrame,
    test: pd.DataFrame,
    columns: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    train_values = train[columns].replace([np.inf, -np.inf], np.nan)
    medians = train_values.median().fillna(0.0).to_numpy(dtype=float)
    train_matrix = train_values.fillna(pd.Series(medians, index=columns)).to_numpy(dtype=float)
    test_matrix = (
        test[columns]
        .replace([np.inf, -np.inf], np.nan)
        .fillna(pd.Series(medians, index=columns))
        .to_numpy(dtype=float)
    )
    return train_matrix, test_matrix, medians


def make_pairwise_training(
    train: pd.DataFrame,
    feature_matrix: np.ndarray,
    target_column: str,
    max_pairs: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    pairs_per_date = max(10, max_pairs // max(1, train["begins_at"].nunique()) // 2)
    differences: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    position = pd.Series(np.arange(len(train)), index=train.index)

    for _, group in train.groupby("begins_at", sort=False):
        if len(group) < 2:
            continue
        group_positions = position.loc[group.index].to_numpy(dtype=int)
        returns = group[target_column].to_numpy(dtype=float)
        count = min(pairs_per_date, len(group) * 2)
        left = rng.integers(0, len(group), size=count * 2)
        right = rng.integers(0, len(group), size=count * 2)
        valid = (left != right) & np.isfinite(returns[left]) & np.isfinite(returns[right])
        left = left[valid][:count]
        right = right[valid][:count]
        if left.size == 0:
            continue
        diff = feature_matrix[group_positions[left]] - feature_matrix[group_positions[right]]
        label = (returns[left] > returns[right]).astype(int)
        differences.extend([diff, -diff])
        labels.extend([label, 1 - label])

    if not differences:
        raise RuntimeError("Not enough within-date observations for pairwise training.")
    x_pairs = np.vstack(differences)
    y_pairs = np.concatenate(labels)
    if len(y_pairs) > max_pairs:
        keep = rng.choice(len(y_pairs), size=max_pairs, replace=False)
        x_pairs = x_pairs[keep]
        y_pairs = y_pairs[keep]
    return x_pairs, y_pairs


def cross_sectional_rank(frame: pd.DataFrame, values: np.ndarray) -> np.ndarray:
    ranked = pd.Series(np.asarray(values, dtype=float), index=frame.index)
    return ranked.groupby(frame["begins_at"]).rank(method="average", pct=True).to_numpy()


def run_feature_ablation(
    train: pd.DataFrame,
    test: pd.DataFrame,
    horizon: int,
    split_number: int,
    args: argparse.Namespace,
) -> list[dict[str, object]]:
    target = f"future_return_{horizon}d"
    sampled = bounded_training_sample(train.dropna(subset=[target]), args.max_train_rows, args.seed)
    y_train = (sampled[target].to_numpy(dtype=float) > 0.0).astype(int)
    records: list[dict[str, object]] = []
    for group_number, (name, columns) in enumerate(feature_sets().items()):
        model = ann_classifier("relu", args.seed + horizon * 100 + split_number * 10 + group_number, (24,))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model.fit(sampled[list(columns)], y_train)
        probability = model.predict_proba(test[list(columns)])[:, 1]
        metrics = evaluate_score(
            sampled,
            test,
            horizon,
            probability,
            probability,
            args.top_k,
            args.rebalance_every,
            args.transaction_cost_bps,
        )
        records.append(
            {
                "horizon": horizon,
                "split": split_number,
                "feature_configuration": name,
                "feature_count": len(columns),
                **metrics,
            }
        )
    return records


def run_architecture_lab(
    train: pd.DataFrame,
    test: pd.DataFrame,
    horizon: int,
    split_number: int,
    args: argparse.Namespace,
) -> list[dict[str, object]]:
    target = f"future_return_{horizon}d"
    fit, calibration = chronological_fit_and_calibration(train, horizon)
    fit = bounded_training_sample(fit.dropna(subset=[target]), args.max_train_rows, args.seed + 1)
    calibration = calibration.dropna(subset=[target])
    y_fit = (fit[target].to_numpy(dtype=float) > 0.0).astype(int)
    y_calibration = (calibration[target].to_numpy(dtype=float) > 0.0).astype(int)
    base_columns = list(BASE_FEATURES)
    graph_columns = [*BASE_FEATURES, *GRAPH_FEATURES]
    seed = args.seed + horizon * 1000 + split_number * 100

    relu_base = ann_classifier("relu", seed)
    tanh_base = ann_classifier("tanh", seed + 1)
    relu_graph = ann_classifier("relu", seed + 2)
    return_graph = ann_regressor("relu", seed + 3)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        relu_base.fit(fit[base_columns], y_fit)
        tanh_base.fit(fit[base_columns], y_fit)
        relu_graph.fit(fit[graph_columns], y_fit)
        return_graph.fit(fit[graph_columns], fit[target].to_numpy(dtype=float))

    base_probability = relu_base.predict_proba(test[base_columns])[:, 1]
    tanh_probability = tanh_base.predict_proba(test[base_columns])[:, 1]
    graph_probability = relu_graph.predict_proba(test[graph_columns])[:, 1]
    predicted_return = return_graph.predict(test[graph_columns])

    calibration_probability = relu_base.predict_proba(calibration[base_columns])[:, 1]
    if np.unique(y_calibration).size > 1:
        platt = LogisticRegression(C=1.0, random_state=seed, max_iter=500)
        platt.fit(calibration_probability.reshape(-1, 1), y_calibration)
        platt_probability = platt.predict_proba(base_probability.reshape(-1, 1))[:, 1]
    else:
        platt_probability = base_probability

    pair_train_matrix, pair_test_matrix, median_vector = filled_matrix(fit, test, graph_columns)
    x_pairs, y_pairs = make_pairwise_training(
        fit,
        pair_train_matrix,
        target,
        args.max_pairs,
        seed + 4,
    )
    pair_model = make_pipeline(
        StandardScaler(),
        MLPClassifier(
            hidden_layer_sizes=(32, 16),
            activation="relu",
            alpha=0.005,
            batch_size=512,
            max_iter=70,
            early_stopping=True,
            validation_fraction=0.12,
            n_iter_no_change=7,
            random_state=seed + 4,
        ),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pair_model.fit(x_pairs, y_pairs)
    pairwise_score = pair_model.predict_proba(pair_test_matrix - median_vector)[:, 1]

    multi_objective_score = (
        0.40 * cross_sectional_rank(test, platt_probability)
        + 0.35 * cross_sectional_rank(test, pairwise_score)
        + 0.25 * cross_sectional_rank(test, predicted_return)
    )
    candidates: dict[str, tuple[np.ndarray, np.ndarray | None, str, str]] = {
        "direction_relu_base": (base_probability, base_probability, "log_loss", "relu"),
        "direction_tanh_base": (tanh_probability, tanh_probability, "log_loss", "tanh"),
        "direction_relu_graph": (graph_probability, graph_probability, "log_loss", "relu"),
        "direction_relu_platt": (platt_probability, platt_probability, "log_loss+platt", "relu"),
        "return_mse_graph": (predicted_return, None, "squared_return", "relu"),
        "pairwise_relu_graph": (pairwise_score, None, "pairwise_log_loss", "relu"),
        "multi_objective_graph": (multi_objective_score, None, "blended_ranking", "mixed"),
    }

    records: list[dict[str, object]] = []
    for name, (score, probability, objective, activation) in candidates.items():
        metrics = evaluate_score(
            fit,
            test,
            horizon,
            score,
            probability,
            args.top_k,
            args.rebalance_every,
            args.transaction_cost_bps,
        )
        records.append(
            {
                "horizon": horizon,
                "split": split_number,
                "architecture": name,
                "objective": objective,
                "activation": activation,
                "fit_rows": len(fit),
                "calibration_rows": len(calibration),
                "test_rows": len(test),
                **metrics,
            }
        )
    return records


def summarize(
    frame: pd.DataFrame,
    group_column: str,
) -> pd.DataFrame:
    metrics = [
        "auc",
        "accuracy",
        "brier_skill",
        "calibration_error",
        "mean_net_return",
        "win_rate",
        "mean_excess_vs_universe",
        "average_sleeve_compound_return",
        "worst_sleeve_max_drawdown",
    ]
    mean = frame.groupby(["horizon", group_column], as_index=False)[metrics].mean()
    stability = (
        frame.groupby(["horizon", group_column], as_index=False)[
            ["auc", "mean_excess_vs_universe"]
        ]
        .std()
        .rename(
            columns={
                "auc": "auc_split_std",
                "mean_excess_vs_universe": "excess_return_split_std",
            }
        )
    )
    return mean.merge(stability, on=["horizon", group_column], how="left")


def write_readout(
    path: Path,
    feature_summary: pd.DataFrame,
    architecture_summary: pd.DataFrame,
    audit: pd.DataFrame,
) -> None:
    feature_order = feature_summary.sort_values(
        ["horizon", "mean_excess_vs_universe", "brier_skill"],
        ascending=[True, False, False],
    )
    architecture_order = architecture_summary.sort_values(
        ["horizon", "mean_excess_vs_universe", "brier_skill"],
        ascending=[True, False, False],
    )
    lines = [
        "# Feature and Loss Lab",
        "",
        "Paper-only. All splits are chronological, targets are embargoed by horizon, and graph context is fitted from training rows only.",
        "",
        "## Feature-group ablations",
        "",
        feature_order.to_string(index=False, float_format=lambda value: f"{value:.4f}"),
        "",
        "## Activation and objective comparison",
        "",
        architecture_order.to_string(index=False, float_format=lambda value: f"{value:.4f}"),
        "",
        "## Leakage audit",
        "",
        audit.to_string(index=False),
        "",
        "Promotion requires positive Brier skill for probability claims and stable positive top-10 excess return after costs.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    horizons = tuple(int(value.strip()) for value in args.horizons.split(",") if value.strip())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame = load_frame(args.db, horizons, args.lookback_dates)
    feature_records: list[dict[str, object]] = []
    architecture_records: list[dict[str, object]] = []
    audit_records: list[dict[str, object]] = []

    for horizon in horizons:
        target = f"future_return_{horizon}d"
        splits = chronological_splits(
            frame,
            horizon,
            args.test_dates,
            args.splits,
            args.train_window_dates,
        )
        for split_meta in splits:
            train = frame[frame["begins_at"].isin(split_meta["train_dates"])].dropna(subset=[target])
            test = frame[frame["begins_at"].isin(split_meta["test_dates"])].dropna(subset=[target])
            graph, transition, nodes = fit_training_graph(
                train,
                args.graph_window_dates,
                args.neighbors,
            )
            train_enriched = attach_graph_context(train, graph, transition, nodes)
            test_enriched = attach_graph_context(test, graph, transition, nodes)
            split_number = int(split_meta["split"])
            feature_records.extend(
                run_feature_ablation(
                    train_enriched,
                    test_enriched,
                    horizon,
                    split_number,
                    args,
                )
            )
            architecture_records.extend(
                run_architecture_lab(
                    train_enriched,
                    test_enriched,
                    horizon,
                    split_number,
                    args,
                )
            )
            audit_records.append(
                {
                    "horizon": horizon,
                    "split": split_number,
                    "train_start": split_meta["train_start"],
                    "graph_and_training_cutoff": split_meta["train_end"],
                    "test_start": split_meta["test_start"],
                    "test_end": split_meta["test_end"],
                    "embargo_trading_days": horizon,
                    "graph_uses_test_or_future_rows": False,
                    "selection_uses_future_return": False,
                }
            )

    if not feature_records or not architecture_records:
        raise RuntimeError("No chronological split had enough data for the feature/loss lab.")

    feature_frame = pd.DataFrame(feature_records)
    architecture_frame = pd.DataFrame(architecture_records)
    audit_frame = pd.DataFrame(audit_records)
    feature_summary = summarize(feature_frame, "feature_configuration")
    architecture_summary = summarize(architecture_frame, "architecture")

    feature_frame.to_csv(args.output_dir / "feature_group_ablation_by_split.csv", index=False)
    feature_summary.to_csv(args.output_dir / "feature_group_ablation_summary.csv", index=False)
    architecture_frame.to_csv(args.output_dir / "architecture_loss_by_split.csv", index=False)
    architecture_summary.to_csv(args.output_dir / "architecture_loss_summary.csv", index=False)
    audit_frame.to_csv(args.output_dir / "leakage_audit.csv", index=False)
    write_readout(
        args.output_dir / "feature_loss_readout.txt",
        feature_summary,
        architecture_summary,
        audit_frame,
    )

    manifest = {
        "database": str(args.db),
        "horizons": horizons,
        "feature_configurations": list(feature_sets()),
        "architectures": sorted(architecture_frame["architecture"].unique().tolist()),
        "top_k": args.top_k,
        "rebalance_every_trading_days": args.rebalance_every,
        "round_trip_cost_bps": 2.0 * args.transaction_cost_bps,
        "rows_loaded": len(frame),
        "ticker_count": int(frame["ticker"].nunique()),
        "date_min": frame["begins_at"].min().date().isoformat(),
        "date_max": frame["begins_at"].max().date().isoformat(),
        "paper_only": True,
    }
    (args.output_dir / "experiment_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print("FEATURE_GROUP_WINNERS")
    print(
        feature_summary.sort_values(
            ["horizon", "mean_excess_vs_universe"], ascending=[True, False]
        )
        .groupby("horizon")
        .head(3)
        .to_string(index=False, float_format=lambda value: f"{value:.4f}")
    )
    print("\nARCHITECTURE_WINNERS")
    print(
        architecture_summary.sort_values(
            ["horizon", "mean_excess_vs_universe"], ascending=[True, False]
        )
        .groupby("horizon")
        .head(3)
        .to_string(index=False, float_format=lambda value: f"{value:.4f}")
    )
    print(f"\nOutputs written to {args.output_dir}")


if __name__ == "__main__":
    main()
