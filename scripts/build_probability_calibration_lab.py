#!/usr/bin/env python3
"""Evaluate honest post-hoc probability calibration for the graph ANN challenger.

The experiment is paper-only. Each evaluation split uses three chronological
regions: core model training, calibration, and testing. Labels are embargoed by
the prediction horizon, graph neighbors are selected using core-training rows
only, and no test labels are used to fit or select a calibrator.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler


RANDOM_STATE = 442
EPSILON = 1e-6
BASE_FEATURES = (
    "ret_1d",
    "ret_5d",
    "ret_20d",
    "ret_60d",
    "vol_20d",
    "vol_60d",
    "dollar_vol_20d_log",
    "drawdown_60d",
    "z_ma20",
    "ma_crossover",
    "rsi_14",
)
GRAPH_SOURCE_FEATURES = ("ret_5d", "ret_20d", "vol_20d")
GRAPH_FEATURES = (
    "neighbor_ret_5d",
    "neighbor_ret_20d",
    "neighbor_vol_20d",
    "graph_similarity_mean",
    "graph_degree",
)

SPLITS = {
    5: (
        (1, "2026-03-30", "2026-04-08", "2026-05-19"),
        (2, "2026-05-12", "2026-05-20", "2026-07-02"),
        (3, "2026-06-25", "2026-07-06", "2026-08-14"),
    ),
    20: (
        (1, "2026-02-23", "2026-03-24", "2026-05-04"),
        (2, "2026-04-06", "2026-05-05", "2026-06-12"),
        (3, "2026-05-14", "2026-06-15", "2026-07-24"),
    ),
}


@dataclass
class FittedCalibrator:
    name: str
    predict: Callable[[np.ndarray], np.ndarray]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--calibration-days", type=int, default=30)
    parser.add_argument("--max-tickers", type=int, default=600)
    parser.add_argument("--graph-lookback-days", type=int, default=80)
    parser.add_argument("--graph-neighbors", type=int, default=8)
    parser.add_argument("--max-train-rows", type=int, default=150000)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    return parser.parse_args()


def load_prices(db_path: Path) -> pd.DataFrame:
    with sqlite3.connect(db_path) as connection:
        prices = pd.read_sql_query(
            "SELECT ticker, begins_at, close_price, volume FROM RecentPrices",
            connection,
        )
    prices["date"] = pd.to_datetime(prices["begins_at"], errors="coerce").dt.normalize()
    prices["close_price"] = pd.to_numeric(prices["close_price"], errors="coerce")
    prices["volume"] = pd.to_numeric(prices["volume"], errors="coerce")
    prices = prices.dropna(subset=["ticker", "date", "close_price", "volume"])
    prices = prices[(prices["close_price"] > 0) & (prices["volume"] >= 0)]
    prices = prices.sort_values(["ticker", "date"]).drop_duplicates(
        ["ticker", "date"], keep="last"
    )
    return prices.reset_index(drop=True)


def build_trailing_features(prices: pd.DataFrame) -> pd.DataFrame:
    frame = prices.copy()
    grouped = frame.groupby("ticker", sort=False)
    frame["ret_1d"] = grouped["close_price"].pct_change(fill_method=None)
    for days in (5, 20, 60):
        frame[f"ret_{days}d"] = grouped["close_price"].pct_change(
            periods=days, fill_method=None
        )
    frame["vol_20d"] = grouped["ret_1d"].transform(
        lambda values: values.rolling(20, min_periods=10).std()
    )
    frame["vol_60d"] = grouped["ret_1d"].transform(
        lambda values: values.rolling(60, min_periods=30).std()
    )
    dollar_volume = frame["close_price"] * frame["volume"]
    frame["dollar_vol_20d_log"] = np.log1p(
        dollar_volume.groupby(frame["ticker"]).transform(
            lambda values: values.rolling(20, min_periods=10).median()
        )
    )
    rolling_high = grouped["close_price"].transform(
        lambda values: values.rolling(60, min_periods=30).max()
    )
    frame["drawdown_60d"] = frame["close_price"] / rolling_high - 1.0
    ma5 = grouped["close_price"].transform(
        lambda values: values.rolling(5, min_periods=5).mean()
    )
    ma20 = grouped["close_price"].transform(
        lambda values: values.rolling(20, min_periods=10).mean()
    )
    std20 = grouped["close_price"].transform(
        lambda values: values.rolling(20, min_periods=10).std()
    )
    frame["z_ma20"] = (frame["close_price"] - ma20) / std20.replace(0, np.nan)
    frame["ma_crossover"] = ma5 / ma20 - 1.0
    gains = frame["ret_1d"].clip(lower=0)
    losses = -frame["ret_1d"].clip(upper=0)
    avg_gain = gains.groupby(frame["ticker"]).transform(
        lambda values: values.rolling(14, min_periods=7).mean()
    )
    avg_loss = losses.groupby(frame["ticker"]).transform(
        lambda values: values.rolling(14, min_periods=7).mean()
    )
    relative_strength = avg_gain / avg_loss.replace(0, np.nan)
    frame["rsi_14"] = 100.0 - 100.0 / (1.0 + relative_strength)
    frame["rsi_14"] = frame["rsi_14"].fillna(50.0) / 100.0
    return frame


def select_graph_neighbors(
    frame: pd.DataFrame,
    cutoff: pd.Timestamp,
    max_tickers: int,
    lookback_days: int,
    neighbor_count: int,
) -> pd.DataFrame:
    history = frame[frame["date"] <= cutoff]
    eligibility = history.groupby("ticker").agg(
        rows=("date", "size"),
        liquidity=("dollar_vol_20d_log", "median"),
    )
    eligibility = eligibility[eligibility["rows"] >= max(60, lookback_days)]
    universe = (
        eligibility.sort_values("liquidity", ascending=False)
        .head(max_tickers)
        .index.tolist()
    )
    if len(universe) <= neighbor_count:
        raise RuntimeError("Not enough eligible tickers to build the similarity graph")

    dates = sorted(history["date"].unique())[-lookback_days:]
    returns = history[
        history["ticker"].isin(universe) & history["date"].isin(dates)
    ].pivot(index="date", columns="ticker", values="ret_1d")
    minimum_observations = max(30, lookback_days // 2)
    returns = returns.loc[:, returns.notna().sum() >= minimum_observations]
    if returns.shape[1] <= neighbor_count:
        raise RuntimeError("Not enough complete ticker histories for graph fitting")

    values = returns.to_numpy(dtype=float)
    observed = np.isfinite(values).astype(float)
    means = np.nanmean(values, axis=0)
    standard_deviations = np.nanstd(values, axis=0)
    standard_deviations[standard_deviations < EPSILON] = 1.0
    standardized = (values - means) / standard_deviations
    standardized[~np.isfinite(standardized)] = 0.0
    overlap = observed.T @ observed
    similarity = (standardized.T @ standardized) / np.maximum(overlap, 1.0)
    similarity[overlap < minimum_observations] = -np.inf
    np.fill_diagonal(similarity, -np.inf)

    tickers = returns.columns.to_numpy()
    edges: list[dict[str, object]] = []
    for index, ticker in enumerate(tickers):
        row = similarity[index]
        candidate_indexes = np.argpartition(-row, neighbor_count - 1)[:neighbor_count]
        candidate_indexes = candidate_indexes[
            np.argsort(-row[candidate_indexes], kind="stable")
        ]
        for neighbor_index in candidate_indexes:
            score = float(row[neighbor_index])
            if not np.isfinite(score) or score <= 0:
                continue
            edges.append(
                {
                    "ticker": str(ticker),
                    "neighbor": str(tickers[neighbor_index]),
                    "similarity": score,
                }
            )
    edges_frame = pd.DataFrame(edges)
    if edges_frame.empty:
        raise RuntimeError("Similarity graph produced no positive edges")
    return edges_frame


def add_graph_context(frame: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    universe = set(edges["ticker"]).union(edges["neighbor"])
    selected = frame[frame["ticker"].isin(universe)].copy()
    neighbor_values = selected[["date", "ticker", *GRAPH_SOURCE_FEATURES]].rename(
        columns={
            "ticker": "neighbor",
            "ret_5d": "neighbor_ret_5d",
            "ret_20d": "neighbor_ret_20d",
            "vol_20d": "neighbor_vol_20d",
        }
    )
    expanded = edges.merge(neighbor_values, on="neighbor", how="inner")
    graph = expanded.groupby(["ticker", "date"], as_index=False).agg(
        neighbor_ret_5d=("neighbor_ret_5d", "mean"),
        neighbor_ret_20d=("neighbor_ret_20d", "mean"),
        neighbor_vol_20d=("neighbor_vol_20d", "mean"),
        graph_similarity_mean=("similarity", "mean"),
        graph_degree=("neighbor", "nunique"),
    )
    return selected.merge(graph, on=["ticker", "date"], how="left")


def clip_probabilities(probabilities: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(probabilities, dtype=float), EPSILON, 1.0 - EPSILON)


def logits(probabilities: np.ndarray) -> np.ndarray:
    probabilities = clip_probabilities(probabilities)
    return np.log(probabilities / (1.0 - probabilities))


def fit_calibrators(
    probabilities: np.ndarray, labels: np.ndarray
) -> list[FittedCalibrator]:
    probabilities = clip_probabilities(probabilities)
    labels = np.asarray(labels, dtype=int)
    raw_logits = logits(probabilities)
    prior = float(labels.mean())
    calibrators = [
        FittedCalibrator("uncalibrated", lambda values: clip_probabilities(values))
    ]

    platt = LogisticRegression(C=1.0, max_iter=500, random_state=RANDOM_STATE)
    platt.fit(raw_logits.reshape(-1, 1), labels)
    calibrators.append(
        FittedCalibrator(
            "platt",
            lambda values, model=platt: model.predict_proba(
                logits(values).reshape(-1, 1)
            )[:, 1],
        )
    )

    beta_inputs = np.column_stack(
        [np.log(probabilities), -np.log1p(-probabilities)]
    )
    beta = LogisticRegression(C=1.0, max_iter=500, random_state=RANDOM_STATE)
    beta.fit(beta_inputs, labels)
    calibrators.append(
        FittedCalibrator(
            "beta",
            lambda values, model=beta: model.predict_proba(
                np.column_stack(
                    [
                        np.log(clip_probabilities(values)),
                        -np.log1p(-clip_probabilities(values)),
                    ]
                )
            )[:, 1],
        )
    )

    isotonic = IsotonicRegression(out_of_bounds="clip", y_min=EPSILON, y_max=1 - EPSILON)
    isotonic.fit(probabilities, labels)
    calibrators.append(
        FittedCalibrator(
            "isotonic",
            lambda values, model=isotonic: clip_probabilities(model.predict(values)),
        )
    )

    temperature_result = minimize_scalar(
        lambda temperature: log_loss(
            labels,
            1.0 / (1.0 + np.exp(-raw_logits / temperature)),
            labels=[0, 1],
        ),
        bounds=(0.1, 10.0),
        method="bounded",
    )
    temperature = float(temperature_result.x)
    calibrators.append(
        FittedCalibrator(
            "temperature",
            lambda values, fitted_temperature=temperature: clip_probabilities(
                1.0 / (1.0 + np.exp(-logits(values) / fitted_temperature))
            ),
        )
    )

    shrinkage_result = minimize_scalar(
        lambda weight: log_loss(
            labels,
            clip_probabilities(weight * probabilities + (1.0 - weight) * prior),
            labels=[0, 1],
        ),
        bounds=(0.0, 1.0),
        method="bounded",
    )
    shrinkage_weight = float(shrinkage_result.x)
    calibrators.append(
        FittedCalibrator(
            "prior_shrinkage",
            lambda values, weight=shrinkage_weight, base_rate=prior: clip_probabilities(
                weight * np.asarray(values) + (1.0 - weight) * base_rate
            ),
        )
    )
    return calibrators


def expected_calibration_error(
    labels: np.ndarray, probabilities: np.ndarray, bins: int = 10
) -> tuple[float, float]:
    labels = np.asarray(labels, dtype=float)
    probabilities = clip_probabilities(probabilities)
    bin_indexes = np.minimum((probabilities * bins).astype(int), bins - 1)
    ece = 0.0
    maximum_error = 0.0
    for bin_index in range(bins):
        mask = bin_indexes == bin_index
        if not mask.any():
            continue
        gap = abs(float(labels[mask].mean()) - float(probabilities[mask].mean()))
        ece += float(mask.mean()) * gap
        maximum_error = max(maximum_error, gap)
    return ece, maximum_error


def calibration_regression(
    labels: np.ndarray, probabilities: np.ndarray
) -> tuple[float, float]:
    try:
        model = LogisticRegression(C=1e6, max_iter=500, random_state=RANDOM_STATE)
        model.fit(logits(probabilities).reshape(-1, 1), labels)
        return float(model.intercept_[0]), float(model.coef_[0, 0])
    except ValueError:
        return np.nan, np.nan


def ranking_metrics(
    evaluation: pd.DataFrame,
    probabilities: np.ndarray,
    top_k: int,
    cost_bps: float,
) -> tuple[float, float, float]:
    scored = evaluation[["date", "ticker", "future_return"]].copy()
    scored["probability"] = probabilities
    cost = cost_bps / 10000.0
    daily_rows: list[dict[str, float]] = []
    for _, group in scored.groupby("date"):
        selected = group.nlargest(min(top_k, len(group)), "probability")
        selected_return = float(selected["future_return"].mean()) - cost
        universe_return = float(group["future_return"].mean())
        daily_rows.append(
            {
                "net_return": selected_return,
                "excess_return": selected_return - universe_return,
            }
        )
    daily = pd.DataFrame(daily_rows)
    return (
        float(daily["net_return"].mean()),
        float((daily["net_return"] > 0).mean()),
        float(daily["excess_return"].mean()),
    )


def reliability_rows(
    horizon: int,
    split: int,
    method: str,
    labels: np.ndarray,
    probabilities: np.ndarray,
) -> list[dict[str, object]]:
    temporary = pd.DataFrame({"label": labels, "probability": probabilities})
    temporary["bin"] = pd.cut(
        temporary["probability"],
        bins=np.linspace(0.0, 1.0, 11),
        include_lowest=True,
        labels=False,
    )
    rows: list[dict[str, object]] = []
    for bin_index, group in temporary.dropna(subset=["bin"]).groupby("bin"):
        rows.append(
            {
                "horizon": horizon,
                "split": split,
                "method": method,
                "bin": int(bin_index),
                "count": int(len(group)),
                "mean_probability": float(group["probability"].mean()),
                "observed_frequency": float(group["label"].mean()),
            }
        )
    return rows


def evaluate_probabilities(
    evaluation: pd.DataFrame,
    probabilities: np.ndarray,
    calibration_prior: float,
    top_k: int,
    cost_bps: float,
) -> dict[str, float]:
    labels = evaluation["target_up"].to_numpy(dtype=int)
    probabilities = clip_probabilities(probabilities)
    brier = float(np.mean((probabilities - labels) ** 2))
    reference_brier = float(np.mean((calibration_prior - labels) ** 2))
    model_log_loss = float(log_loss(labels, probabilities, labels=[0, 1]))
    reference_log_loss = float(
        log_loss(
            labels,
            np.full(len(labels), calibration_prior),
            labels=[0, 1],
        )
    )
    ece, maximum_calibration_error = expected_calibration_error(labels, probabilities)
    intercept, slope = calibration_regression(labels, probabilities)
    mean_return, win_rate, excess_return = ranking_metrics(
        evaluation, probabilities, top_k, cost_bps
    )
    return {
        "auc": float(roc_auc_score(labels, probabilities)),
        "accuracy": float(accuracy_score(labels, probabilities >= 0.5)),
        "probability_std": float(np.std(probabilities)),
        "brier": brier,
        "brier_skill": 1.0 - brier / reference_brier,
        "log_loss": model_log_loss,
        "log_loss_skill": 1.0 - model_log_loss / reference_log_loss,
        "calibration_error": ece,
        "maximum_calibration_error": maximum_calibration_error,
        "calibration_intercept": intercept,
        "calibration_slope": slope,
        "mean_net_return": mean_return,
        "win_rate": win_rate,
        "mean_excess_vs_universe": excess_return,
    }


def run_split(
    features: pd.DataFrame,
    horizon: int,
    split: int,
    training_cutoff: str,
    test_start: str,
    test_end: str,
    args: argparse.Namespace,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    frame = features.copy()
    frame["future_return"] = (
        frame.groupby("ticker", sort=False)["close_price"].shift(-horizon)
        / frame["close_price"]
        - 1.0
    )
    frame["target_up"] = (frame["future_return"] > 0).astype(int)
    frame.loc[frame["future_return"].isna(), "target_up"] = np.nan

    cutoff = pd.Timestamp(training_cutoff)
    test_start_date = pd.Timestamp(test_start)
    test_end_date = pd.Timestamp(test_end)
    pretest_dates = sorted(frame.loc[frame["date"] <= cutoff, "date"].unique())
    if len(pretest_dates) <= args.calibration_days + horizon + 60:
        raise RuntimeError(f"Split {split} horizon {horizon} lacks training history")
    calibration_dates = pretest_dates[-args.calibration_days :]
    calibration_start = pd.Timestamp(calibration_dates[0])
    calibration_end = pd.Timestamp(calibration_dates[-1])
    calibration_start_index = len(pretest_dates) - args.calibration_days
    core_end_index = calibration_start_index - horizon - 1
    core_training_end = pd.Timestamp(pretest_dates[core_end_index])

    edges = select_graph_neighbors(
        frame,
        core_training_end,
        args.max_tickers,
        args.graph_lookback_days,
        args.graph_neighbors,
    )
    graph_frame = add_graph_context(
        frame[frame["date"] <= test_end_date],
        edges,
    )
    model_features = [*BASE_FEATURES, *GRAPH_FEATURES]
    usable = graph_frame.dropna(
        subset=[*model_features, "future_return", "target_up"]
    ).copy()
    core = usable[usable["date"] <= core_training_end]
    calibration = usable[
        (usable["date"] >= calibration_start)
        & (usable["date"] <= calibration_end)
    ]
    evaluation = usable[
        (usable["date"] >= test_start_date) & (usable["date"] <= test_end_date)
    ]
    if min(len(core), len(calibration), len(evaluation)) == 0:
        raise RuntimeError(f"Split {split} horizon {horizon} has an empty region")
    if core["target_up"].nunique() < 2 or calibration["target_up"].nunique() < 2:
        raise RuntimeError(f"Split {split} horizon {horizon} has one-class training data")
    if evaluation["target_up"].nunique() < 2:
        raise RuntimeError(f"Split {split} horizon {horizon} has one-class test data")

    if len(core) > args.max_train_rows:
        core = core.sample(args.max_train_rows, random_state=RANDOM_STATE).sort_values("date")
    scaler = StandardScaler()
    train_inputs = scaler.fit_transform(core[model_features])
    calibration_inputs = scaler.transform(calibration[model_features])
    evaluation_inputs = scaler.transform(evaluation[model_features])
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
        random_state=RANDOM_STATE,
    )
    model.fit(train_inputs, core["target_up"].astype(int))
    calibration_raw = model.predict_proba(calibration_inputs)[:, 1]
    evaluation_raw = model.predict_proba(evaluation_inputs)[:, 1]
    calibration_labels = calibration["target_up"].to_numpy(dtype=int)
    calibration_prior = float(calibration_labels.mean())

    metric_rows: list[dict[str, object]] = []
    reliability: list[dict[str, object]] = []
    for calibrator in fit_calibrators(calibration_raw, calibration_labels):
        calibrated = clip_probabilities(calibrator.predict(evaluation_raw))
        metrics = evaluate_probabilities(
            evaluation,
            calibrated,
            calibration_prior,
            args.top_k,
            args.cost_bps,
        )
        metric_rows.append(
            {
                "horizon": horizon,
                "split": split,
                "method": calibrator.name,
                "train_rows": int(len(core)),
                "calibration_rows": int(len(calibration)),
                "test_rows": int(len(evaluation)),
                "tickers": int(evaluation["ticker"].nunique()),
                "calibration_prior": calibration_prior,
                **metrics,
            }
        )
        reliability.extend(
            reliability_rows(
                horizon,
                split,
                calibrator.name,
                evaluation["target_up"].to_numpy(dtype=int),
                calibrated,
            )
        )

    audit = {
        "horizon": horizon,
        "split": split,
        "core_training_end": core_training_end.date().isoformat(),
        "graph_fit_cutoff": core_training_end.date().isoformat(),
        "calibration_start": calibration_start.date().isoformat(),
        "calibration_end": calibration_end.date().isoformat(),
        "test_start": test_start_date.date().isoformat(),
        "test_end": test_end_date.date().isoformat(),
        "embargo_trading_days": horizon,
        "graph_uses_calibration_or_test_rows": False,
        "calibrator_uses_test_labels": False,
        "method_selection_uses_test_returns": False,
    }
    return metric_rows, reliability, audit


def summarize(by_split: pd.DataFrame) -> pd.DataFrame:
    summary = by_split.groupby(["horizon", "method"], as_index=False).agg(
        auc=("auc", "mean"),
        minimum_split_auc=("auc", "min"),
        accuracy=("accuracy", "mean"),
        probability_std=("probability_std", "mean"),
        minimum_split_probability_std=("probability_std", "min"),
        brier=("brier", "mean"),
        brier_skill=("brier_skill", "mean"),
        minimum_split_brier_skill=("brier_skill", "min"),
        brier_skill_split_std=("brier_skill", "std"),
        log_loss=("log_loss", "mean"),
        log_loss_skill=("log_loss_skill", "mean"),
        calibration_error=("calibration_error", "mean"),
        maximum_calibration_error=("maximum_calibration_error", "mean"),
        calibration_intercept=("calibration_intercept", "mean"),
        calibration_slope=("calibration_slope", "mean"),
        mean_net_return=("mean_net_return", "mean"),
        win_rate=("win_rate", "mean"),
        mean_excess_vs_universe=("mean_excess_vs_universe", "mean"),
        minimum_split_excess_vs_universe=("mean_excess_vs_universe", "min"),
        excess_return_split_std=("mean_excess_vs_universe", "std"),
        minimum_split_calibration_slope=("calibration_slope", "min"),
        maximum_split_calibration_slope=("calibration_slope", "max"),
    )
    raw_ece = (
        summary[summary["method"] == "uncalibrated"]
        .set_index("horizon")["calibration_error"]
        .to_dict()
    )
    summary["improves_calibration_error"] = summary.apply(
        lambda row: row["calibration_error"] < raw_ece.get(row["horizon"], np.inf),
        axis=1,
    )
    summary["probability_ready"] = (
        (summary["minimum_split_brier_skill"] > 0)
        & (summary["log_loss_skill"] > 0)
        & summary["improves_calibration_error"]
        & (summary["auc"] >= 0.51)
        & (summary["minimum_split_auc"] >= 0.50)
        & (summary["calibration_error"] <= 0.05)
        & (summary["minimum_split_calibration_slope"] >= 0.50)
        & (summary["maximum_split_calibration_slope"] <= 1.50)
        & (summary["minimum_split_probability_std"] >= 0.02)
        & (summary["mean_excess_vs_universe"] > 0)
        & (summary["minimum_split_excess_vs_universe"] >= 0)
    )
    return summary.sort_values(
        ["horizon", "probability_ready", "brier_skill"],
        ascending=[True, False, False],
    )


def write_readout(output_path: Path, summary: pd.DataFrame) -> None:
    columns = [
        "horizon",
        "method",
        "auc",
        "minimum_split_auc",
        "probability_std",
        "brier_skill",
        "minimum_split_brier_skill",
        "log_loss_skill",
        "calibration_error",
        "calibration_slope",
        "mean_excess_vs_universe",
        "minimum_split_excess_vs_universe",
        "probability_ready",
    ]
    ready = summary[summary["probability_ready"]]
    lines = [
        "# Probability Calibration Lab",
        "",
        "Paper-only. Core training, calibration, and test regions are chronological.",
        "Graph fitting stops before calibration, and calibration never sees test labels.",
        "",
        summary[columns].round(4).to_string(index=False),
        "",
    ]
    if ready.empty:
        lines.extend(
            [
                "Decision: no method is ready for probability claims.",
                "Keep direction_relu_graph as a ranking score only.",
            ]
        )
    else:
        lines.append(
            "Probability candidates: "
            + ", ".join(
                f"{row.horizon}d/{row.method}" for row in ready.itertuples()
            )
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prices = load_prices(args.db)
    features = build_trailing_features(prices)

    metrics: list[dict[str, object]] = []
    reliability: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    for horizon, split_definitions in SPLITS.items():
        for split, cutoff, test_start, test_end in split_definitions:
            split_metrics, split_reliability, audit = run_split(
                features,
                horizon,
                split,
                cutoff,
                test_start,
                test_end,
                args,
            )
            metrics.extend(split_metrics)
            reliability.extend(split_reliability)
            audits.append(audit)
            print(f"Completed calibration horizon={horizon} split={split}", flush=True)

    by_split = pd.DataFrame(metrics)
    summary = summarize(by_split)
    by_split.to_csv(args.output_dir / "calibration_by_split.csv", index=False)
    summary.to_csv(args.output_dir / "calibration_summary.csv", index=False)
    pd.DataFrame(reliability).to_csv(
        args.output_dir / "calibration_reliability_bins.csv", index=False
    )
    pd.DataFrame(audits).to_csv(
        args.output_dir / "calibration_leakage_audit.csv", index=False
    )
    write_readout(args.output_dir / "calibration_readout.txt", summary)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": str(args.db),
        "architecture": "direction_relu_graph",
        "status": "paper_only",
        "methods": sorted(by_split["method"].unique().tolist()),
        "horizons": sorted(by_split["horizon"].unique().tolist()),
        "parameters": {
            "calibration_days": args.calibration_days,
            "max_tickers": args.max_tickers,
            "graph_lookback_days": args.graph_lookback_days,
            "graph_neighbors": args.graph_neighbors,
            "max_train_rows": args.max_train_rows,
            "top_k": args.top_k,
            "cost_bps": args.cost_bps,
        },
        "promotion_rule": (
            "Positive Brier skill in every split, positive log-loss skill, lower "
            "calibration error than the raw model, mean AUC at least 0.51 with no "
            "split below 0.50, calibration slopes between 0.50 and 1.50, meaningful "
            "probability spread, and positive excess return in every split."
        ),
    }
    (args.output_dir / "experiment_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote calibration lab to {args.output_dir}", flush=True)


if __name__ == "__main__":
    main()
