#!/usr/bin/env python3
"""Run chronological loss, activation, and motion-feature ablations."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


EXPERIMENT_ID = "temporal_3d_mc_loss_activation_v1"
STAGE_ID = "temporal_motion_predictive_ablation_v1"
BASE_FEATURES = [
    "ret_5d",
    "ret_20d",
    "ret_60d",
    "vol_20d",
    "vol_60d",
    "drawdown_60d",
    "dollar_vol_20d_log",
    "graph_degree",
    "graph_similarity_mean",
    "neighbor_ret_20d",
    "neighbor_divergence",
    "empirical_upside_probability_5d",
    "latent_x",
    "latent_y",
    "latent_z",
]
KINEMATIC_FEATURES = [
    "latent_velocity",
    "latent_acceleration",
    "latent_path_curvature",
    "latent_radial_expansion",
]
GRAPH_MOTION_FEATURES = [
    "neighbor_convergence_velocity",
    "graph_cluster_switch_count_20d",
    "graph_regime_residence_days",
    "crowding_change_5d",
]
MOTION_FEATURES = KINEMATIC_FEATURES + GRAPH_MOTION_FEATURES
ACTIVATIONS = ["relu", "tanh", "leaky_relu", "gelu"]
LOSSES = ["binary_cross_entropy", "brier_mse", "focal_gamma_1", "focal_gamma_2", "return_aware_bce"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--motion-features", required=True)
    parser.add_argument("--context-gate", default="research_context/context_gate.json")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--sealed-holdout-start", default="2026-05-29")
    parser.add_argument("--splits", type=int, default=3)
    parser.add_argument("--test-dates", type=int, default=15)
    parser.add_argument("--embargo-dates", type=int, default=5)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--seeds", default="442,2025,9001")
    parser.add_argument("--deep-seeds", default="442,2025,9001,117,8080")
    parser.add_argument("--portfolio-size", type=int, default=10)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    return parser.parse_args()


def assert_approved(path: Path) -> dict:
    gate = json.loads(path.read_text(encoding="utf-8"))
    rows = gate.get("next_experiments", [])
    match = next((row for row in rows if row.get("id") == EXPERIMENT_ID), None)
    if not match or match.get("status") not in {"approved_next", "completed_reviewed_provisional"}:
        raise RuntimeError(f"Context gate does not approve {EXPERIMENT_ID}")
    return match


def load_analysis_frame(motion_path: Path, db_path: Path, holdout_start: str) -> pd.DataFrame:
    motion = pd.read_csv(motion_path)
    motion["date"] = pd.to_datetime(motion["date"], errors="coerce").dt.normalize()
    motion["ticker"] = motion["ticker"].astype(str).str.upper().str.strip()
    tickers = sorted(motion["ticker"].dropna().unique())
    first_date = motion["date"].min().strftime("%Y-%m-%d")
    placeholders = ",".join("?" for _ in tickers)
    query = f"""
        SELECT ticker, begins_at, close_price
        FROM ResearchPrices
        WHERE begins_at >= ? AND begins_at < ? AND ticker IN ({placeholders})
        ORDER BY begins_at, ticker
    """
    with sqlite3.connect(db_path) as connection:
        prices = pd.read_sql_query(query, connection, params=[first_date, holdout_start, *tickers])
    prices["date"] = pd.to_datetime(prices["begins_at"], utc=True, errors="coerce").dt.tz_localize(None).dt.normalize()
    prices["ticker"] = prices["ticker"].astype(str).str.upper().str.strip()
    prices["close_price"] = pd.to_numeric(prices["close_price"], errors="coerce")
    prices = prices.dropna(subset=["date", "ticker", "close_price"])
    prices = prices.groupby(["ticker", "date"], as_index=False)["close_price"].last()
    prices = prices.sort_values(["ticker", "date"])
    prices["future_return_5d"] = prices.groupby("ticker")["close_price"].shift(-5) / prices["close_price"] - 1.0
    targets = prices[["ticker", "date", "future_return_5d"]]
    frame = motion.merge(targets, on=["ticker", "date"], how="left")
    frame["future_return_5d"] = pd.to_numeric(frame["future_return_5d"], errors="coerce").clip(-0.8, 3.0)
    frame = frame.dropna(subset=["future_return_5d"]).copy()
    frame["target_up"] = (frame["future_return_5d"] > 0).astype(float)
    required = BASE_FEATURES + MOTION_FEATURES
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values(["date", "ticker"]).reset_index(drop=True)


def chronological_splits(frame: pd.DataFrame, count: int, test_dates: int, embargo: int) -> list[dict]:
    dates = np.array(sorted(frame["date"].unique()))
    required = count * test_dates + embargo + 20
    if len(dates) < required:
        raise RuntimeError(f"Need at least {required} labeled dates, found {len(dates)}")
    first_test = len(dates) - count * test_dates
    output = []
    for split in range(count):
        test_start_index = first_test + split * test_dates
        test = dates[test_start_index : test_start_index + test_dates]
        train = dates[: max(0, test_start_index - embargo)]
        validation_count = max(4, min(8, len(train) // 5))
        validation = train[-validation_count:]
        fit = train[:-validation_count]
        output.append({"split": split + 1, "fit_dates": fit, "validation_dates": validation, "test_dates": test})
    return output


def prepare_arrays(frame: pd.DataFrame, split: dict, features: list[str]) -> dict:
    fit = frame[frame["date"].isin(split["fit_dates"])].copy()
    validation = frame[frame["date"].isin(split["validation_dates"])].copy()
    test = frame[frame["date"].isin(split["test_dates"])].copy()
    medians = fit[features].median().fillna(0.0)
    means = fit[features].fillna(medians).mean()
    scales = fit[features].fillna(medians).std().replace(0, 1.0).fillna(1.0)

    def matrix(rows: pd.DataFrame) -> np.ndarray:
        return ((rows[features].fillna(medians) - means) / scales).clip(-6, 6).to_numpy(dtype=np.float64)

    return {
        "x_fit": matrix(fit),
        "y_fit": fit["target_up"].to_numpy(dtype=np.float64),
        "r_fit": fit["future_return_5d"].to_numpy(dtype=np.float64),
        "x_validation": matrix(validation),
        "y_validation": validation["target_up"].to_numpy(dtype=np.float64),
        "r_validation": validation["future_return_5d"].to_numpy(dtype=np.float64),
        "x_test": matrix(test),
        "y_test": test["target_up"].to_numpy(dtype=np.float64),
        "r_test": test["future_return_5d"].to_numpy(dtype=np.float64),
        "test_frame": test,
    }


def sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def activation(values: np.ndarray, name: str) -> np.ndarray:
    if name == "relu":
        return np.maximum(values, 0.0)
    if name == "tanh":
        return np.tanh(values)
    if name == "leaky_relu":
        return np.where(values >= 0.0, values, 0.1 * values)
    coefficient = math.sqrt(2.0 / math.pi)
    return 0.5 * values * (1.0 + np.tanh(coefficient * (values + 0.044715 * values**3)))


def activation_derivative(values: np.ndarray, name: str) -> np.ndarray:
    if name == "relu":
        return (values > 0.0).astype(float)
    if name == "tanh":
        tanh = np.tanh(values)
        return 1.0 - tanh**2
    if name == "leaky_relu":
        return np.where(values >= 0.0, 1.0, 0.1)
    coefficient = math.sqrt(2.0 / math.pi)
    inner = coefficient * (values + 0.044715 * values**3)
    tanh = np.tanh(inner)
    return 0.5 * (1.0 + tanh) + 0.5 * values * (1.0 - tanh**2) * coefficient * (1.0 + 3.0 * 0.044715 * values**2)


def loss_gradient(probability: np.ndarray, target: np.ndarray, returns: np.ndarray, name: str) -> np.ndarray:
    probability = np.clip(probability, 1e-6, 1.0 - 1e-6)
    if name == "binary_cross_entropy":
        return probability - target
    if name == "brier_mse":
        return 2.0 * (probability - target) * probability * (1.0 - probability)
    if name.startswith("focal_gamma_"):
        gamma = float(name.rsplit("_", 1)[-1])
        positive = gamma * probability * (1.0 - probability) ** gamma * np.log(probability) - (1.0 - probability) ** (gamma + 1.0)
        negative = -gamma * probability**gamma * (1.0 - probability) * np.log(1.0 - probability) + probability ** (gamma + 1.0)
        return np.where(target > 0.5, positive, negative)
    return_scale = np.nanstd(returns)
    scaled_return = np.clip(returns / max(return_scale, 1e-6), -4.0, 4.0)
    utility_gradient = -0.08 * scaled_return * probability * (1.0 - probability)
    downside_gradient = 0.04 * np.maximum(-scaled_return, 0.0) * probability * (1.0 - probability)
    return probability - target + utility_gradient + downside_gradient


class TinyMLP:
    def __init__(self, inputs: int, activation_name: str, loss_name: str, seed: int):
        self.activation_name = activation_name
        self.loss_name = loss_name
        rng = np.random.default_rng(seed)
        scale1 = math.sqrt(2.0 / max(inputs, 1))
        scale2 = math.sqrt(2.0 / 32)
        self.parameters = {
            "w1": rng.normal(0.0, scale1, size=(inputs, 32)),
            "b1": np.zeros((1, 32)),
            "w2": rng.normal(0.0, scale2, size=(32, 16)),
            "b2": np.zeros((1, 16)),
            "w3": rng.normal(0.0, math.sqrt(1.0 / 16), size=(16, 1)),
            "b3": np.zeros((1, 1)),
        }
        self.m = {name: np.zeros_like(value) for name, value in self.parameters.items()}
        self.v = {name: np.zeros_like(value) for name, value in self.parameters.items()}
        self.step = 0

    def forward(self, x: np.ndarray) -> tuple[np.ndarray, tuple]:
        z1 = x @ self.parameters["w1"] + self.parameters["b1"]
        a1 = activation(z1, self.activation_name)
        z2 = a1 @ self.parameters["w2"] + self.parameters["b2"]
        a2 = activation(z2, self.activation_name)
        logits = a2 @ self.parameters["w3"] + self.parameters["b3"]
        probability = sigmoid(logits).reshape(-1)
        return probability, (x, z1, a1, z2, a2)

    def train_batch(self, x: np.ndarray, y: np.ndarray, returns: np.ndarray, learning_rate: float = 0.002) -> None:
        probability, cache = self.forward(x)
        x0, z1, a1, z2, a2 = cache
        dz3 = loss_gradient(probability, y, returns, self.loss_name).reshape(-1, 1) / len(x)
        gradients = {}
        gradients["w3"] = a2.T @ dz3 + 1e-4 * self.parameters["w3"]
        gradients["b3"] = dz3.sum(axis=0, keepdims=True)
        dz2 = (dz3 @ self.parameters["w3"].T) * activation_derivative(z2, self.activation_name)
        gradients["w2"] = a1.T @ dz2 + 1e-4 * self.parameters["w2"]
        gradients["b2"] = dz2.sum(axis=0, keepdims=True)
        dz1 = (dz2 @ self.parameters["w2"].T) * activation_derivative(z1, self.activation_name)
        gradients["w1"] = x0.T @ dz1 + 1e-4 * self.parameters["w1"]
        gradients["b1"] = dz1.sum(axis=0, keepdims=True)
        self.step += 1
        for name, gradient in gradients.items():
            self.m[name] = 0.9 * self.m[name] + 0.1 * gradient
            self.v[name] = 0.999 * self.v[name] + 0.001 * gradient**2
            adjusted_m = self.m[name] / (1.0 - 0.9**self.step)
            adjusted_v = self.v[name] / (1.0 - 0.999**self.step)
            self.parameters[name] -= learning_rate * adjusted_m / (np.sqrt(adjusted_v) + 1e-8)

    def fit(self, arrays: dict, epochs: int, batch_size: int, seed: int) -> int:
        rng = np.random.default_rng(seed)
        best_parameters = {name: value.copy() for name, value in self.parameters.items()}
        best_validation = float("inf")
        patience = 0
        best_epoch = 0
        for epoch in range(1, epochs + 1):
            order = rng.permutation(len(arrays["x_fit"]))
            for start in range(0, len(order), batch_size):
                index = order[start : start + batch_size]
                self.train_batch(arrays["x_fit"][index], arrays["y_fit"][index], arrays["r_fit"][index])
            validation_probability, _ = self.forward(arrays["x_validation"])
            validation_loss = log_loss(arrays["y_validation"], validation_probability, labels=[0, 1])
            if validation_loss < best_validation - 1e-5:
                best_validation = validation_loss
                best_parameters = {name: value.copy() for name, value in self.parameters.items()}
                best_epoch = epoch
                patience = 0
            else:
                patience += 1
            if patience >= 10:
                break
        self.parameters = best_parameters
        return best_epoch


def ece_10(target: np.ndarray, probability: np.ndarray) -> float:
    edges = np.linspace(0.0, 1.0, 11)
    total = len(target)
    result = 0.0
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (probability >= low) & (probability < high if high < 1.0 else probability <= high)
        if mask.any():
            result += mask.mean() * abs(float(probability[mask].mean()) - float(target[mask].mean()))
    return float(result if total else np.nan)


def portfolio_metrics(test: pd.DataFrame, probability: np.ndarray, portfolio_size: int, cost_bps: float) -> dict:
    scored = test[["date", "ticker", "future_return_5d"]].copy()
    scored["probability"] = probability
    dates = sorted(scored["date"].unique())[::5]
    previous: set[str] = set()
    rows = []
    for date in dates:
        dated = scored[scored["date"] == date].dropna(subset=["future_return_5d", "probability"])
        if len(dated) < portfolio_size:
            continue
        selected = dated.nlargest(portfolio_size, "probability")
        names = set(selected["ticker"])
        turnover = 1.0 if not previous else 1.0 - len(names & previous) / portfolio_size
        gross = float(selected["future_return_5d"].mean())
        universe = float(dated["future_return_5d"].mean())
        net = gross - turnover * cost_bps / 10000.0
        rows.append({"net": net, "universe": universe, "excess": net - universe, "turnover": turnover})
        previous = names
    if not rows:
        return {"mean_net_return": np.nan, "mean_excess_return": np.nan, "win_rate": np.nan, "turnover": np.nan, "worst_net_return": np.nan}
    result = pd.DataFrame(rows)
    return {
        "mean_net_return": float(result["net"].mean()),
        "mean_excess_return": float(result["excess"].mean()),
        "win_rate": float((result["excess"] > 0).mean()),
        "turnover": float(result["turnover"].mean()),
        "worst_net_return": float(result["net"].min()),
    }


def evaluate_fit(arrays: dict, activation_name: str, loss_name: str, seed: int, args: argparse.Namespace) -> dict:
    model = TinyMLP(arrays["x_fit"].shape[1], activation_name, loss_name, seed)
    best_epoch = model.fit(arrays, args.epochs, args.batch_size, seed)
    probability, _ = model.forward(arrays["x_test"])
    target = arrays["y_test"]
    auc = roc_auc_score(target, probability) if len(np.unique(target)) > 1 else np.nan
    result = {
        "auc": float(auc),
        "brier": float(brier_score_loss(target, probability)),
        "log_loss": float(log_loss(target, probability, labels=[0, 1])),
        "ece_10": ece_10(target, probability),
        "best_epoch": best_epoch,
    }
    result.update(portfolio_metrics(arrays["test_frame"], probability, args.portfolio_size, args.cost_bps))
    return result


def summarize(rows: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    metrics = ["auc", "brier", "log_loss", "ece_10", "mean_net_return", "mean_excess_return", "win_rate", "turnover", "worst_net_return"]
    output = rows.groupby(groups, as_index=False)[metrics].agg(["mean", "std"])
    output.columns = ["_".join(value).rstrip("_") for value in output.columns.to_flat_index()]
    output = output.reset_index(drop=True)
    output["auc_rank"] = output["auc_mean"].rank(pct=True)
    output["brier_rank"] = (-output["brier_mean"]).rank(pct=True)
    output["ece_rank"] = (-output["ece_10_mean"]).rank(pct=True)
    output["excess_rank"] = output["mean_excess_return_mean"].rank(pct=True)
    output["selection_score"] = 0.35 * output["auc_rank"] + 0.25 * output["brier_rank"] + 0.15 * output["ece_rank"] + 0.25 * output["excess_rank"]
    return output.sort_values("selection_score", ascending=False).reset_index(drop=True)


def run_matrix(frame: pd.DataFrame, splits: list[dict], args: argparse.Namespace, seeds: list[int]) -> pd.DataFrame:
    rows = []
    total = len(splits) * len(ACTIVATIONS) * len(LOSSES) * len(seeds)
    count = 0
    features = BASE_FEATURES + MOTION_FEATURES
    for split in splits:
        arrays = prepare_arrays(frame, split, features)
        for activation_name in ACTIVATIONS:
            for loss_name in LOSSES:
                for seed in seeds:
                    count += 1
                    metrics = evaluate_fit(arrays, activation_name, loss_name, seed, args)
                    rows.append({"split": split["split"], "activation": activation_name, "loss": loss_name, "seed": seed, "feature_set": "all_motion", **metrics})
                    if count == 1 or count % 20 == 0 or count == total:
                        print(f"Loss/activation fit {count}/{total}: split={split['split']} {activation_name} {loss_name} seed={seed}", flush=True)
    return pd.DataFrame(rows)


def run_feature_sets(frame: pd.DataFrame, splits: list[dict], activation_name: str, loss_name: str, args: argparse.Namespace, seeds: list[int]) -> pd.DataFrame:
    feature_sets = {
        "base_only": BASE_FEATURES,
        "base_plus_kinematic": BASE_FEATURES + KINEMATIC_FEATURES,
        "base_plus_graph_motion": BASE_FEATURES + GRAPH_MOTION_FEATURES,
        "base_plus_all_motion": BASE_FEATURES + MOTION_FEATURES,
    }
    rows = []
    total = len(splits) * len(feature_sets) * len(seeds)
    count = 0
    for split in splits:
        for set_name, features in feature_sets.items():
            arrays = prepare_arrays(frame, split, features)
            for seed in seeds:
                count += 1
                metrics = evaluate_fit(arrays, activation_name, loss_name, seed, args)
                rows.append({"split": split["split"], "feature_set": set_name, "activation": activation_name, "loss": loss_name, "seed": seed, **metrics})
                if count == 1 or count % 15 == 0 or count == total:
                    print(f"Feature-set fit {count}/{total}: split={split['split']} {set_name} seed={seed}", flush=True)
    return pd.DataFrame(rows)


def run_lofo(frame: pd.DataFrame, splits: list[dict], activation_name: str, loss_name: str, args: argparse.Namespace, seeds: list[int]) -> pd.DataFrame:
    rows = []
    all_features = BASE_FEATURES + MOTION_FEATURES
    total = len(splits) * len(MOTION_FEATURES) * len(seeds)
    count = 0
    for split in splits:
        for dropped in MOTION_FEATURES:
            arrays = prepare_arrays(frame, split, [feature for feature in all_features if feature != dropped])
            for seed in seeds:
                count += 1
                metrics = evaluate_fit(arrays, activation_name, loss_name, seed, args)
                rows.append({"split": split["split"], "dropped_feature": dropped, "activation": activation_name, "loss": loss_name, "seed": seed, **metrics})
                if count == 1 or count % 18 == 0 or count == total:
                    print(f"LOFO fit {count}/{total}: split={split['split']} drop={dropped} seed={seed}", flush=True)
    return pd.DataFrame(rows)


def run_placebo(frame: pd.DataFrame, splits: list[dict], activation_name: str, loss_name: str, args: argparse.Namespace, seeds: list[int]) -> pd.DataFrame:
    rows = []
    features = BASE_FEATURES + MOTION_FEATURES
    for split in splits:
        arrays = prepare_arrays(frame, split, features)
        for seed in seeds:
            shuffled = dict(arrays)
            rng = np.random.default_rng(seed + split["split"] * 10000)
            shuffled["y_fit"] = rng.permutation(arrays["y_fit"])
            shuffled["r_fit"] = rng.permutation(arrays["r_fit"])
            shuffled["y_validation"] = rng.permutation(arrays["y_validation"])
            shuffled["r_validation"] = rng.permutation(arrays["r_validation"])
            metrics = evaluate_fit(shuffled, activation_name, loss_name, seed, args)
            rows.append({"split": split["split"], "seed": seed, "activation": activation_name, "loss": loss_name, **metrics})
            print(f"Placebo complete: split={split['split']} seed={seed}", flush=True)
    return pd.DataFrame(rows)


def markdown_table(frame: pd.DataFrame, columns: list[str], limit: int = 20) -> str:
    visible = frame[columns].head(limit)
    header = "| " + " | ".join(columns) + " |"
    divider = "|" + "|".join("---:" if pd.api.types.is_numeric_dtype(visible[column]) else "---" for column in columns) + "|"
    rows = []
    for item in visible.itertuples(index=False, name=None):
        values = [f"{value:.5f}" if isinstance(value, (float, np.floating)) else str(value) for value in item]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, divider, *rows])


def main() -> None:
    args = parse_args()
    approval = assert_approved(Path(args.context_gate))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    seeds = [int(value) for value in args.seeds.split(",")]
    deep_seeds = [int(value) for value in args.deep_seeds.split(",")]
    frame = load_analysis_frame(Path(args.motion_features), Path(args.db), args.sealed_holdout_start)
    splits = chronological_splits(frame, args.splits, args.test_dates, args.embargo_dates)
    leakage_rows = []
    for split in splits:
        leakage_rows.append({
            "split": split["split"],
            "fit_end": str(pd.Timestamp(split["fit_dates"][-1]).date()),
            "validation_start": str(pd.Timestamp(split["validation_dates"][0]).date()),
            "validation_end": str(pd.Timestamp(split["validation_dates"][-1]).date()),
            "test_start": str(pd.Timestamp(split["test_dates"][0]).date()),
            "test_end": str(pd.Timestamp(split["test_dates"][-1]).date()),
            "embargo_dates": args.embargo_dates,
            "holdout_opened": False,
        })
    pd.DataFrame(leakage_rows).to_csv(output_dir / "leakage_audit.csv", index=False)
    matrix = run_matrix(frame, splits, args, seeds)
    matrix_summary = summarize(matrix, ["activation", "loss"])
    winner = matrix_summary.iloc[0]
    winning_activation = str(winner["activation"])
    winning_loss = str(winner["loss"])
    feature_sets = run_feature_sets(frame, splits, winning_activation, winning_loss, args, deep_seeds)
    feature_summary = summarize(feature_sets, ["feature_set"])
    lofo = run_lofo(frame, splits, winning_activation, winning_loss, args, seeds)
    lofo_summary = summarize(lofo, ["dropped_feature"])
    full_auc = float(matrix[(matrix["activation"] == winning_activation) & (matrix["loss"] == winning_loss)]["auc"].mean())
    full_brier = float(matrix[(matrix["activation"] == winning_activation) & (matrix["loss"] == winning_loss)]["brier"].mean())
    full_excess = float(matrix[(matrix["activation"] == winning_activation) & (matrix["loss"] == winning_loss)]["mean_excess_return"].mean())
    lofo_summary["auc_drop"] = full_auc - lofo_summary["auc_mean"]
    lofo_summary["brier_harm"] = lofo_summary["brier_mean"] - full_brier
    lofo_summary["excess_return_drop"] = full_excess - lofo_summary["mean_excess_return_mean"]
    lofo_summary = lofo_summary.sort_values(["auc_drop", "excess_return_drop"], ascending=False)
    placebo = run_placebo(frame, splits, winning_activation, winning_loss, args, deep_seeds)
    matrix.to_csv(output_dir / "loss_activation_metrics.csv", index=False)
    matrix_summary.to_csv(output_dir / "loss_activation_summary.csv", index=False)
    feature_sets.to_csv(output_dir / "feature_set_metrics.csv", index=False)
    feature_summary.to_csv(output_dir / "feature_set_summary.csv", index=False)
    lofo.to_csv(output_dir / "motion_lofo_metrics.csv", index=False)
    lofo_summary.to_csv(output_dir / "motion_lofo_summary.csv", index=False)
    placebo.to_csv(output_dir / "placebo_metrics.csv", index=False)
    readout = [
        "# Temporal Motion Predictive Ablation",
        "",
        f"- Winning activation: `{winning_activation}`",
        f"- Winning loss: `{winning_loss}`",
        f"- Winning mean AUC: {winner['auc_mean']:.5f}",
        f"- Winning mean Brier: {winner['brier_mean']:.5f}",
        f"- Winning mean after-cost excess return: {winner['mean_excess_return_mean']:.5f}",
        f"- Placebo mean AUC: {placebo['auc'].mean():.5f}",
        f"- Final holdout opened: false",
        "",
        "## Loss and activation ranking",
        "",
        markdown_table(matrix_summary, ["activation", "loss", "auc_mean", "brier_mean", "ece_10_mean", "mean_excess_return_mean", "selection_score"]),
        "",
        "## Feature-set ranking",
        "",
        markdown_table(feature_summary, ["feature_set", "auc_mean", "brier_mean", "ece_10_mean", "mean_excess_return_mean", "selection_score"]),
        "",
        "## Motion leave-one-feature-out",
        "",
        markdown_table(lofo_summary, ["dropped_feature", "auc_drop", "brier_harm", "excess_return_drop"], limit=20),
        "",
        "## Guardrails",
        "",
        "- All fits use chronological train, validation, embargo, and test windows.",
        "- Scaling and imputation are fit on training rows only.",
        "- Portfolio returns include the configured transaction cost and five-date rebalance spacing.",
        "- Monte Carlo paths are not used as independent training observations.",
        "- Results remain provisional until reviewed; the final 60-date holdout remains sealed.",
    ]
    (output_dir / "ablation_readout.md").write_text("\n".join(readout) + "\n", encoding="utf-8")
    candidate = {
        "action": "review_then_merge",
        "base_context_id": "stockprediction2025-research-gate",
        "completed_experiment": {
            "id": STAGE_ID,
            "parent_experiment": EXPERIMENT_ID,
            "status": "completed_pending_review",
            "winning_activation": winning_activation,
            "winning_loss": winning_loss,
            "winner_auc_mean": round(float(winner["auc_mean"]), 6),
            "winner_brier_mean": round(float(winner["brier_mean"]), 6),
            "winner_excess_return_mean": round(float(winner["mean_excess_return_mean"]), 6),
            "best_feature_set": str(feature_summary.iloc[0]["feature_set"]),
            "placebo_auc_mean": round(float(placebo["auc"].mean()), 6),
            "holdout_opened": False,
            "design_signature": approval.get("design_signature"),
        },
        "holdout_status": {"status": "sealed", "date_start": args.sealed_holdout_start, "trading_dates": 60},
    }
    (output_dir / "context_gate_candidate_update.json").write_text(json.dumps(candidate, indent=2), encoding="utf-8")
    manifest = {
        "experiment_id": STAGE_ID,
        "parent_experiment": EXPERIMENT_ID,
        "status": "completed_pending_review",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(frame),
        "tickers": int(frame["ticker"].nunique()),
        "dates": int(frame["date"].nunique()),
        "model_fits": int(len(matrix) + len(feature_sets) + len(lofo) + len(placebo)),
        "winning_activation": winning_activation,
        "winning_loss": winning_loss,
        "best_feature_set": str(feature_summary.iloc[0]["feature_set"]),
        "holdout_opened": False,
        "outputs": sorted(path.name for path in output_dir.iterdir() if path.is_file()),
    }
    (output_dir / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Outputs written to {output_dir}", flush=True)
    print(f"Winner: {winning_activation} / {winning_loss}", flush=True)
    print(f"Best feature set: {feature_summary.iloc[0]['feature_set']}", flush=True)
    print("Final holdout remains sealed.", flush=True)


if __name__ == "__main__":
    main()
