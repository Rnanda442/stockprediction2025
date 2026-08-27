#!/usr/bin/env python3
"""Paper-only ANN combination, similarity-filter, and 3D latent-feature lab.

The lab intentionally leaves the final holdout sealed. Similarity graphs,
scalers, PCA coordinates, feature selection, and models are fitted from
pre-test rows only. Nothing in this script connects to a broker or changes a
live prediction surface.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler


CORE_FEATURES = [
    "ret_5d",
    "ret_20d",
    "ret_60d",
    "z_ma20",
    "vol_20d",
    "vol_60d",
    "drawdown_60d",
]
LIQUIDITY_FEATURES = ["dollar_vol_20d_log"]
GRAPH_FEATURES = [
    "graph_degree",
    "graph_similarity_mean",
    "neighbor_ret_20d",
    "neighbor_vol_20d",
]
INTERACTION_FEATURES = [
    "neighbor_confirmation",
    "neighbor_divergence",
    "similarity_weighted_momentum",
    "risk_adjusted_momentum",
    "crowding_risk",
]
LATENT_FEATURES = ["latent_x", "latent_y", "latent_z", "latent_radius"]

COMBINATIONS = {
    "momentum_risk_core": CORE_FEATURES,
    "core_plus_liquidity": CORE_FEATURES + LIQUIDITY_FEATURES,
    "core_plus_graph_raw": CORE_FEATURES + GRAPH_FEATURES,
    "core_plus_graph_interactions": CORE_FEATURES + GRAPH_FEATURES + INTERACTION_FEATURES,
    "core_plus_latent3d": CORE_FEATURES + LATENT_FEATURES,
    "core_plus_graph_latent3d": (
        CORE_FEATURES + LIQUIDITY_FEATURES + GRAPH_FEATURES + INTERACTION_FEATURES + LATENT_FEATURES
    ),
    "all_without_neighbor_vol": [
        feature
        for feature in CORE_FEATURES
        + LIQUIDITY_FEATURES
        + GRAPH_FEATURES
        + INTERACTION_FEATURES
        + LATENT_FEATURES
        if feature != "neighbor_vol_20d"
    ],
}

ARCHITECTURES = {
    "relu_48_24": {"hidden_layer_sizes": (48, 24), "activation": "relu"},
    "tanh_48_24": {"hidden_layer_sizes": (48, 24), "activation": "tanh"},
    "relu_64_32_16": {"hidden_layer_sizes": (64, 32, 16), "activation": "relu"},
}


@dataclass
class GraphDefinition:
    tickers: list[str]
    weights: np.ndarray
    similarity_mean: dict[str, float]
    degree: dict[str, float]
    cluster: dict[str, int]


@dataclass
class PreparedSplit:
    split: int
    train: pd.DataFrame
    test: pd.DataFrame
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    graph_tickers: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to research_history.db")
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
    return parser.parse_args()


def safe_auc(target: pd.Series | np.ndarray, probability: np.ndarray) -> float:
    target_array = np.asarray(target)
    if np.unique(target_array).size < 2:
        return float("nan")
    return float(roc_auc_score(target_array, probability))


def load_prices(path: Path) -> pd.DataFrame:
    connection = sqlite3.connect(path)
    try:
        prices = pd.read_sql_query(
            "SELECT ticker, begins_at, close_price, volume FROM ResearchPrices",
            connection,
        )
        metadata = dict(connection.execute("SELECT key, value FROM ResearchMetadata").fetchall())
    finally:
        connection.close()

    prices["date"] = pd.to_datetime(prices.pop("begins_at"), utc=True).dt.tz_localize(None).dt.normalize()
    prices["ticker"] = prices["ticker"].astype(str)
    prices["close_price"] = pd.to_numeric(prices["close_price"], errors="coerce")
    prices["volume"] = pd.to_numeric(prices["volume"], errors="coerce").fillna(0.0)
    prices = prices.loc[prices["close_price"].gt(0)].copy()
    prices = prices.drop_duplicates(["date", "ticker"], keep="last")
    prices = prices.sort_values(["ticker", "date"]).reset_index(drop=True)
    print(
        "Loaded",
        f"{len(prices):,}",
        "price rows for",
        f"{prices['ticker'].nunique():,}",
        "tickers; source span",
        metadata.get("source_span", "unknown"),
        flush=True,
    )
    return prices


def build_base_features(prices: pd.DataFrame, horizon: int, top_k: int) -> pd.DataFrame:
    group = prices.groupby("ticker", sort=False, group_keys=False)
    for period in (1, 5, 20, 60):
        prices[f"ret_{period}d"] = group["close_price"].pct_change(period, fill_method=None)

    group = prices.groupby("ticker", sort=False, group_keys=False)
    prices["vol_20d"] = (
        group["ret_1d"].rolling(20, min_periods=15).std().reset_index(level=0, drop=True)
    )
    prices["vol_60d"] = (
        group["ret_1d"].rolling(60, min_periods=40).std().reset_index(level=0, drop=True)
    )
    rolling_high = (
        group["close_price"].rolling(60, min_periods=40).max().reset_index(level=0, drop=True)
    )
    ma20 = group["close_price"].rolling(20, min_periods=15).mean().reset_index(level=0, drop=True)
    sd20 = group["close_price"].rolling(20, min_periods=15).std().reset_index(level=0, drop=True)
    prices["drawdown_60d"] = prices["close_price"].div(rolling_high).sub(1.0)
    prices["z_ma20"] = prices["close_price"].sub(ma20).div(sd20.replace(0.0, np.nan))
    prices["dollar_volume"] = prices["close_price"].mul(prices["volume"].clip(lower=0.0))
    prices["dollar_vol_20d_log"] = np.log1p(
        group["dollar_volume"].rolling(20, min_periods=15).mean().reset_index(level=0, drop=True)
    )
    prices["forward_return"] = group["close_price"].shift(-horizon).div(prices["close_price"]).sub(1.0)
    prices["target"] = prices["forward_return"].gt(0.0).astype(float)
    prices.loc[prices["forward_return"].isna(), "target"] = np.nan

    prices["liquidity_rank"] = prices.groupby("date")["dollar_vol_20d_log"].rank(
        method="first", ascending=False
    )
    prices = prices.loc[prices["liquidity_rank"].le(top_k)].copy()
    required = CORE_FEATURES + LIQUIDITY_FEATURES
    prices[required] = prices[required].replace([np.inf, -np.inf], np.nan)
    return prices


def union_find_clusters(correlation: np.ndarray, tickers: list[str], threshold: float = 0.55) -> dict[str, int]:
    parent = list(range(len(tickers)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    rows, columns = np.where(np.triu(correlation >= threshold, k=1))
    for left, right in zip(rows.tolist(), columns.tolist()):
        union(left, right)

    roots = [find(index) for index in range(len(tickers))]
    labels = {root: label for label, root in enumerate(sorted(set(roots)))}
    return {ticker: labels[root] for ticker, root in zip(tickers, roots)}


def fit_similarity_graph(
    train: pd.DataFrame,
    lookback_dates: int,
    top_k: int,
    neighbors: int,
) -> GraphDefinition:
    recent_dates = sorted(train["date"].unique())[-lookback_dates:]
    recent = train.loc[train["date"].isin(recent_dates)].copy()
    universe = (
        recent.groupby("ticker")["dollar_vol_20d_log"]
        .median()
        .nlargest(top_k)
        .index.astype(str)
        .tolist()
    )
    returns = recent.loc[recent["ticker"].isin(universe)].pivot(
        index="date", columns="ticker", values="ret_1d"
    )
    returns = returns.reindex(columns=universe)
    minimum_periods = max(20, min(60, len(recent_dates) // 2))
    correlation_frame = returns.corr(min_periods=minimum_periods).reindex(index=universe, columns=universe)
    correlation = correlation_frame.fillna(0.0).clip(-1.0, 1.0).to_numpy(dtype=float, copy=True)
    np.fill_diagonal(correlation, 0.0)

    neighbor_count = max(1, min(neighbors, len(universe) - 1))
    weights = np.zeros_like(correlation)
    for row in range(len(universe)):
        candidates = np.argpartition(-correlation[row], neighbor_count - 1)[:neighbor_count]
        positive = candidates[correlation[row, candidates] > 0.0]
        if positive.size:
            selected_weights = correlation[row, positive]
            weights[row, positive] = selected_weights / selected_weights.sum()

    similarity_mean = {}
    degree = {}
    for index, ticker in enumerate(universe):
        selected = weights[index] > 0.0
        similarity_mean[ticker] = float(correlation[index, selected].mean()) if selected.any() else 0.0
        degree[ticker] = float((correlation[index] >= 0.40).sum()) / max(1, len(universe) - 1)

    return GraphDefinition(
        tickers=universe,
        weights=weights,
        similarity_mean=similarity_mean,
        degree=degree,
        cluster=union_find_clusters(correlation, universe),
    )


def weighted_neighbor_matrix(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    finite = np.isfinite(values)
    numerator = np.nan_to_num(values, nan=0.0).dot(weights.T)
    denominator = finite.astype(float).dot(weights.T)
    return np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator, dtype=float),
        where=denominator > 1e-12,
    )


def add_graph_features(frame: pd.DataFrame, graph: GraphDefinition) -> pd.DataFrame:
    dates = sorted(frame["date"].unique())
    graph_rows = frame.loc[frame["ticker"].isin(graph.tickers)]
    ret20 = graph_rows.pivot(index="date", columns="ticker", values="ret_20d").reindex(
        index=dates, columns=graph.tickers
    )
    vol20 = graph_rows.pivot(index="date", columns="ticker", values="vol_20d").reindex(
        index=dates, columns=graph.tickers
    )
    neighbor_return = weighted_neighbor_matrix(ret20.to_numpy(dtype=float), graph.weights)
    neighbor_volatility = weighted_neighbor_matrix(vol20.to_numpy(dtype=float), graph.weights)
    graph_index = pd.MultiIndex.from_product([dates, graph.tickers], names=["date", "ticker"])
    enriched = pd.DataFrame(
        {
            "neighbor_ret_20d": neighbor_return.reshape(-1),
            "neighbor_vol_20d": neighbor_volatility.reshape(-1),
        },
        index=graph_index,
    ).reset_index()
    enriched["graph_similarity_mean"] = enriched["ticker"].map(graph.similarity_mean).fillna(0.0)
    enriched["graph_degree"] = enriched["ticker"].map(graph.degree).fillna(0.0)
    enriched["graph_cluster"] = enriched["ticker"].map(graph.cluster).fillna(-1).astype(int)
    result = frame.merge(enriched, on=["date", "ticker"], how="left")
    result[GRAPH_FEATURES] = result[GRAPH_FEATURES].fillna(0.0)
    result["graph_cluster"] = result["graph_cluster"].fillna(-1).astype(int)
    return result


def add_interactions(frame: pd.DataFrame) -> pd.DataFrame:
    frame["neighbor_confirmation"] = frame["ret_20d"].mul(frame["neighbor_ret_20d"])
    frame["neighbor_divergence"] = frame["ret_20d"].sub(frame["neighbor_ret_20d"])
    frame["similarity_weighted_momentum"] = frame["ret_20d"].mul(
        frame["graph_similarity_mean"]
    )
    frame["risk_adjusted_momentum"] = frame["ret_20d"].div(frame["vol_20d"].abs().add(1e-4))
    frame["crowding_risk"] = frame["graph_degree"].mul(frame["vol_20d"])
    frame[INTERACTION_FEATURES] = frame[INTERACTION_FEATURES].replace([np.inf, -np.inf], np.nan)
    return frame


def add_train_only_latent_coordinates(
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    latent_inputs = CORE_FEATURES + LIQUIDITY_FEATURES + GRAPH_FEATURES + INTERACTION_FEATURES
    medians = train[latent_inputs].median().fillna(0.0)
    train_values = train[latent_inputs].fillna(medians).to_numpy(dtype=float)
    test_values = test[latent_inputs].fillna(medians).to_numpy(dtype=float)
    scaler = StandardScaler()
    train_scaled = scaler.fit_transform(train_values)
    test_scaled = scaler.transform(test_values)
    pca = PCA(n_components=3, random_state=0)
    train_coordinates = pca.fit_transform(train_scaled)
    test_coordinates = pca.transform(test_scaled)
    for column, index in zip(["latent_x", "latent_y", "latent_z"], range(3)):
        train[column] = train_coordinates[:, index]
        test[column] = test_coordinates[:, index]
    train["latent_radius"] = np.sqrt(np.square(train_coordinates).sum(axis=1))
    test["latent_radius"] = np.sqrt(np.square(test_coordinates).sum(axis=1))
    metadata = {
        "inputs": latent_inputs,
        "explained_variance_ratio": [float(value) for value in pca.explained_variance_ratio_],
        "note": "Scaler and PCA were fitted from this split's training rows only.",
    }
    return train, test, metadata


def build_splits(
    features: pd.DataFrame,
    args: argparse.Namespace,
) -> tuple[list[PreparedSplit], dict[str, object], list[dict[str, object]]]:
    all_dates = sorted(features["date"].dropna().unique())
    if len(all_dates) <= args.holdout_dates + args.splits * args.test_dates + args.horizon:
        raise ValueError("Not enough trading dates for requested splits and sealed holdout")
    holdout_dates = all_dates[-args.holdout_dates :]
    pre_holdout = features.loc[features["date"].lt(holdout_dates[0])].copy()
    pre_holdout = pre_holdout.loc[pre_holdout["target"].notna()].copy()
    pre_dates = sorted(pre_holdout["date"].unique())
    test_region = pre_dates[-args.splits * args.test_dates :]
    prepared: list[PreparedSplit] = []
    leakage_rows: list[dict[str, object]] = []
    latent_manifests: list[dict[str, object]] = []

    for split_index in range(args.splits):
        test_dates = test_region[
            split_index * args.test_dates : (split_index + 1) * args.test_dates
        ]
        test_start = pd.Timestamp(test_dates[0])
        test_end = pd.Timestamp(test_dates[-1])
        start_position = pre_dates.index(test_dates[0])
        train_end_position = start_position - args.horizon - 1
        train_start_position = max(0, train_end_position - args.train_dates + 1)
        train_dates = pre_dates[train_start_position : train_end_position + 1]
        if not train_dates:
            raise ValueError(f"Split {split_index + 1} has no training dates")
        train = pre_holdout.loc[pre_holdout["date"].isin(train_dates)].copy()
        test = pre_holdout.loc[pre_holdout["date"].isin(test_dates)].copy()
        graph = fit_similarity_graph(
            train,
            lookback_dates=args.graph_lookback_dates,
            top_k=args.top_k,
            neighbors=args.neighbors,
        )
        train = add_interactions(add_graph_features(train, graph))
        test = add_interactions(add_graph_features(test, graph))
        train, test, latent_metadata = add_train_only_latent_coordinates(train, test)
        required = sorted(set(sum(COMBINATIONS.values(), [])))
        train[required] = train[required].replace([np.inf, -np.inf], np.nan)
        test[required] = test[required].replace([np.inf, -np.inf], np.nan)
        train = train.dropna(subset=CORE_FEATURES + ["target", "forward_return"]).copy()
        test = test.dropna(subset=CORE_FEATURES + ["target", "forward_return"]).copy()
        medians = train[required].median().fillna(0.0)
        train[required] = train[required].fillna(medians)
        test[required] = test[required].fillna(medians)
        train_end = pd.Timestamp(train_dates[-1])
        prepared.append(
            PreparedSplit(
                split=split_index + 1,
                train=train,
                test=test,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                graph_tickers=len(graph.tickers),
            )
        )
        latent_manifests.append({"split": split_index + 1, **latent_metadata})
        leakage_rows.append(
            {
                "horizon": args.horizon,
                "split": split_index + 1,
                "train_end": train_end.date().isoformat(),
                "test_start": test_start.date().isoformat(),
                "test_end": test_end.date().isoformat(),
                "embargo_trading_days": args.horizon,
                "sealed_holdout_start": pd.Timestamp(holdout_dates[0]).date().isoformat(),
                "sealed_holdout_end": pd.Timestamp(holdout_dates[-1]).date().isoformat(),
                "graph_uses_test_or_future_rows": False,
                "latent_fit_uses_test_or_future_rows": False,
                "holdout_used": False,
                "train_rows": len(train),
                "test_rows": len(test),
                "test_tickers": int(test["ticker"].nunique()),
                "graph_tickers": len(graph.tickers),
            }
        )
        print(
            f"Prepared split {split_index + 1}: {len(train):,} train rows, "
            f"{len(test):,} test rows, graph fitted through {train_end.date()}",
            flush=True,
        )

    sealed_manifest = {
        "schema_version": 1,
        "status": "sealed",
        "date_start": pd.Timestamp(holdout_dates[0]).date().isoformat(),
        "date_end": pd.Timestamp(holdout_dates[-1]).date().isoformat(),
        "trading_dates": len(holdout_dates),
        "excluded_from_training": True,
        "excluded_from_graph_fitting": True,
        "excluded_from_pca_fitting": True,
        "excluded_from_feature_selection": True,
        "excluded_from_scoring_and_metrics": True,
        "opened_for_evaluation": False,
    }
    return prepared, sealed_manifest, leakage_rows, latent_manifests


def fit_ann(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    architecture: str,
    seed: int,
    max_iter: int,
) -> tuple[np.ndarray, dict[str, object], StandardScaler, MLPClassifier]:
    scaler = StandardScaler()
    train_matrix = scaler.fit_transform(train[features].to_numpy(dtype=float))
    test_matrix = scaler.transform(test[features].to_numpy(dtype=float))
    configuration = ARCHITECTURES[architecture]
    model = MLPClassifier(
        hidden_layer_sizes=configuration["hidden_layer_sizes"],
        activation=configuration["activation"],
        solver="adam",
        alpha=5e-4,
        batch_size=512,
        learning_rate_init=1e-3,
        max_iter=max_iter,
        shuffle=True,
        random_state=seed,
        tol=1e-4,
        n_iter_no_change=12,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        model.fit(train_matrix, train["target"].astype(int).to_numpy())
    probabilities = model.predict_proba(test_matrix)[:, 1]
    metadata = {
        "iterations": int(model.n_iter_),
        "converged": not any(issubclass(item.category, ConvergenceWarning) for item in caught),
    }
    return probabilities, metadata, scaler, model


def portfolio_metrics(
    test: pd.DataFrame,
    probability: np.ndarray,
    portfolio_size: int,
) -> dict[str, float]:
    scored = test[["date", "ticker", "forward_return"]].copy()
    scored["probability"] = probability
    portfolio_returns: list[float] = []
    universe_returns: list[float] = []
    for _, day in scored.groupby("date", sort=True):
        universe_return = float(day["forward_return"].mean())
        selected = day.nlargest(min(portfolio_size, len(day)), "probability")
        portfolio_returns.append(float(selected["forward_return"].mean()))
        universe_returns.append(universe_return)
    portfolio_array = np.asarray(portfolio_returns)
    universe_array = np.asarray(universe_returns)
    excess = portfolio_array - universe_array
    return {
        "mean_net_return": float(np.nanmean(portfolio_array)),
        "mean_excess_return": float(np.nanmean(excess)),
        "win_rate": float(np.nanmean(excess > 0.0)),
    }


def evaluate_predictions(
    test: pd.DataFrame,
    probability: np.ndarray,
    portfolio_size: int,
) -> dict[str, float]:
    metrics = {
        "auc": safe_auc(test["target"], probability),
        "brier": float(brier_score_loss(test["target"].astype(int), probability)),
    }
    metrics.update(portfolio_metrics(test, probability, portfolio_size))
    return metrics


def rank_candidates(summary: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    ranked = summary.copy()
    ranked["auc_rank"] = ranked["auc_mean"].rank(ascending=False, method="min", pct=True)
    ranked["excess_rank"] = ranked["mean_excess_return_mean"].rank(
        ascending=False, method="min", pct=True
    )
    ranked["brier_rank"] = ranked["brier_mean"].rank(ascending=True, method="min", pct=True)
    ranked["selection_score"] = (
        0.50 * (1.0 - ranked["auc_rank"])
        + 0.35 * (1.0 - ranked["excess_rank"])
        + 0.15 * (1.0 - ranked["brier_rank"])
    )
    return ranked.sort_values(["selection_score", "auc_mean"], ascending=False).reset_index(drop=True)


def summarize_metrics(metrics: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    values = ["auc", "brier", "mean_net_return", "mean_excess_return", "win_rate"]
    summary = metrics.groupby(keys)[values].agg(["mean", "std"]).reset_index()
    summary.columns = [
        "_".join(part for part in column if part) if isinstance(column, tuple) else column
        for column in summary.columns
    ]
    return rank_candidates(summary, keys)


def run_screen(
    prepared: list[PreparedSplit],
    seeds: list[int],
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    total = len(prepared) * len(seeds) * len(COMBINATIONS)
    completed = 0
    for split in prepared:
        for seed in seeds:
            for combination, features in COMBINATIONS.items():
                probability, metadata, _, _ = fit_ann(
                    split.train,
                    split.test,
                    features,
                    architecture="relu_48_24",
                    seed=seed,
                    max_iter=args.max_iter,
                )
                rows.append(
                    {
                        "stage": "combination_screen",
                        "horizon": args.horizon,
                        "split": split.split,
                        "seed": seed,
                        "combination": combination,
                        "architecture": "relu_48_24",
                        "feature_count": len(features),
                        **metadata,
                        **evaluate_predictions(split.test, probability, args.portfolio_size),
                    }
                )
                completed += 1
                print(f"Screen fit {completed}/{total}: split={split.split} seed={seed} {combination}", flush=True)
    metrics = pd.DataFrame(rows)
    summary = summarize_metrics(metrics, ["combination"])
    return metrics, summary


def run_architecture_challenge(
    prepared: list[PreparedSplit],
    seeds: list[int],
    combinations: list[str],
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    total = len(prepared) * len(seeds) * len(combinations) * len(ARCHITECTURES)
    completed = 0
    for split in prepared:
        for seed in seeds:
            for combination in combinations:
                features = COMBINATIONS[combination]
                for architecture in ARCHITECTURES:
                    probability, metadata, _, _ = fit_ann(
                        split.train,
                        split.test,
                        features,
                        architecture=architecture,
                        seed=seed,
                        max_iter=args.max_iter,
                    )
                    rows.append(
                        {
                            "stage": "architecture_challenge",
                            "horizon": args.horizon,
                            "split": split.split,
                            "seed": seed,
                            "combination": combination,
                            "architecture": architecture,
                            "feature_count": len(features),
                            **metadata,
                            **evaluate_predictions(split.test, probability, args.portfolio_size),
                        }
                    )
                    completed += 1
                    print(
                        f"Architecture fit {completed}/{total}: split={split.split} seed={seed} "
                        f"{combination} {architecture}",
                        flush=True,
                    )
    metrics = pd.DataFrame(rows)
    summary = summarize_metrics(metrics, ["combination", "architecture"])
    return metrics, summary


def zscore(values: pd.Series) -> pd.Series:
    standard_deviation = values.std(ddof=0)
    if not np.isfinite(standard_deviation) or standard_deviation <= 1e-12:
        return pd.Series(0.0, index=values.index)
    return values.sub(values.mean()).div(standard_deviation)


def diversified_selection(day: pd.DataFrame, portfolio_size: int, per_cluster: int = 2) -> pd.DataFrame:
    selected_indices: list[int] = []
    cluster_counts: dict[int, int] = {}
    for index, row in day.sort_values("strategy_score", ascending=False).iterrows():
        cluster = int(row["graph_cluster"])
        if cluster >= 0 and cluster_counts.get(cluster, 0) >= per_cluster:
            continue
        selected_indices.append(index)
        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
        if len(selected_indices) >= portfolio_size:
            break
    if len(selected_indices) < portfolio_size:
        remaining = day.drop(index=selected_indices).sort_values("strategy_score", ascending=False)
        selected_indices.extend(remaining.head(portfolio_size - len(selected_indices)).index.tolist())
    return day.loc[selected_indices]


def similarity_strategy_metrics(
    scored: pd.DataFrame,
    portfolio_size: int,
) -> list[dict[str, object]]:
    strategies = ["plain_probability", "similarity_weighted", "consensus_gate", "cluster_diversified"]
    daily: dict[str, list[tuple[float, float]]] = {strategy: [] for strategy in strategies}
    for _, day_source in scored.groupby("date", sort=True):
        day = day_source.copy()
        universe_return = float(day["forward_return"].mean())
        day["similarity_score"] = zscore(day["graph_similarity_mean"])
        day["confirmation_score"] = zscore(day["neighbor_confirmation"])

        day["strategy_score"] = day["probability"]
        selected = day.nlargest(min(portfolio_size, len(day)), "strategy_score")
        daily["plain_probability"].append((float(selected["forward_return"].mean()), universe_return))

        day["strategy_score"] = (
            day["probability"] + 0.04 * day["similarity_score"] + 0.04 * day["confirmation_score"]
        )
        selected = day.nlargest(min(portfolio_size, len(day)), "strategy_score")
        daily["similarity_weighted"].append((float(selected["forward_return"].mean()), universe_return))

        eligible = day.loc[
            day["neighbor_confirmation"].ge(0.0)
            & day["graph_similarity_mean"].ge(day["graph_similarity_mean"].median())
        ].copy()
        if len(eligible) < portfolio_size:
            eligible = day.copy()
        eligible["strategy_score"] = eligible["probability"]
        selected = eligible.nlargest(min(portfolio_size, len(eligible)), "strategy_score")
        daily["consensus_gate"].append((float(selected["forward_return"].mean()), universe_return))

        day["strategy_score"] = day["probability"]
        selected = diversified_selection(day, min(portfolio_size, len(day)))
        daily["cluster_diversified"].append((float(selected["forward_return"].mean()), universe_return))

    rows: list[dict[str, object]] = []
    for strategy, values in daily.items():
        portfolio = np.asarray([value[0] for value in values], dtype=float)
        universe = np.asarray([value[1] for value in values], dtype=float)
        excess = portfolio - universe
        rows.append(
            {
                "strategy": strategy,
                "dates": len(values),
                "mean_net_return": float(np.nanmean(portfolio)),
                "mean_excess_return": float(np.nanmean(excess)),
                "win_rate": float(np.nanmean(excess > 0.0)),
                "worst_date_return": float(np.nanmin(portfolio)),
            }
        )
    return rows


def permute_within_date(
    frame: pd.DataFrame,
    column: str,
    generator: np.random.Generator,
) -> pd.DataFrame:
    result = frame.copy()
    result[column] = result.groupby("date", sort=False)[column].transform(
        lambda values: generator.permutation(values.to_numpy())
    )
    return result


def run_finalist_analysis(
    prepared: list[PreparedSplit],
    seeds: list[int],
    combination: str,
    architecture: str,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    features = COMBINATIONS[combination]
    importance_rows: list[dict[str, object]] = []
    strategy_rows: list[dict[str, object]] = []
    latent_rows: list[pd.DataFrame] = []
    for split in prepared:
        for seed in seeds:
            probability, metadata, scaler, model = fit_ann(
                split.train,
                split.test,
                features,
                architecture=architecture,
                seed=seed,
                max_iter=args.max_iter,
            )
            baseline = evaluate_predictions(split.test, probability, args.portfolio_size)
            scored = split.test[
                [
                    "date",
                    "ticker",
                    "forward_return",
                    "target",
                    "graph_cluster",
                    "graph_similarity_mean",
                    "neighbor_confirmation",
                    "latent_x",
                    "latent_y",
                    "latent_z",
                    "latent_radius",
                ]
            ].copy()
            scored["probability"] = probability
            for row in similarity_strategy_metrics(scored, args.portfolio_size):
                strategy_rows.append(
                    {
                        "split": split.split,
                        "seed": seed,
                        "combination": combination,
                        "architecture": architecture,
                        **metadata,
                        **row,
                    }
                )
            if seed == seeds[0]:
                sample_size = min(20000, len(scored))
                latent_rows.append(
                    scored.sample(sample_size, random_state=split.split).assign(
                        split=split.split,
                        seed=seed,
                        combination=combination,
                        architecture=architecture,
                    )
                )

            for feature_index, feature in enumerate(features):
                generator = np.random.default_rng(seed * 1000 + split.split * 100 + feature_index)
                permuted = permute_within_date(split.test, feature, generator)
                matrix = scaler.transform(permuted[features].to_numpy(dtype=float))
                permuted_probability = model.predict_proba(matrix)[:, 1]
                permuted_metrics = evaluate_predictions(
                    permuted,
                    permuted_probability,
                    args.portfolio_size,
                )
                importance_rows.append(
                    {
                        "split": split.split,
                        "seed": seed,
                        "combination": combination,
                        "architecture": architecture,
                        "feature": feature,
                        "auc_importance": baseline["auc"] - permuted_metrics["auc"],
                        "brier_importance": permuted_metrics["brier"] - baseline["brier"],
                        "excess_return_importance": (
                            baseline["mean_excess_return"] - permuted_metrics["mean_excess_return"]
                        ),
                    }
                )
            print(
                f"Finalist analysis: split={split.split} seed={seed} {combination} {architecture}",
                flush=True,
            )

    importance = pd.DataFrame(importance_rows)
    strategy = pd.DataFrame(strategy_rows)
    latent = pd.concat(latent_rows, ignore_index=True) if latent_rows else pd.DataFrame()
    return importance, strategy, latent


def importance_summary(frame: pd.DataFrame) -> pd.DataFrame:
    metrics = ["auc_importance", "brier_importance", "excess_return_importance"]
    summary = frame.groupby("feature")[metrics].agg(["mean", "std", "count"]).reset_index()
    summary.columns = [
        "_".join(part for part in column if part) if isinstance(column, tuple) else column
        for column in summary.columns
    ]
    positive = frame.assign(
        auc_positive=frame["auc_importance"].gt(0.0),
        excess_positive=frame["excess_return_importance"].gt(0.0),
    ).groupby("feature")[["auc_positive", "excess_positive"]].mean()
    summary = summary.merge(positive, on="feature", how="left")
    return summary.sort_values(
        ["auc_importance_mean", "excess_return_importance_mean"], ascending=False
    ).reset_index(drop=True)


def markdown_table(frame: pd.DataFrame, columns: list[str], rows: int = 10) -> str:
    view = frame.loc[:, columns].head(rows).copy()
    for column in view.select_dtypes(include=["number"]).columns:
        view[column] = view[column].map(lambda value: f"{value:.4f}" if pd.notna(value) else "")
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = ["| " + " | ".join(str(value) for value in row) + " |" for row in view.to_numpy()]
    return "\n".join([header, divider, *body])


def write_readout(
    path: Path,
    screen_summary: pd.DataFrame,
    architecture_summary: pd.DataFrame,
    importance: pd.DataFrame,
    strategy: pd.DataFrame,
    selected_combination: str,
    selected_architecture: str,
    sealed_manifest: dict[str, object],
) -> None:
    strategy_summary = (
        strategy.groupby("strategy")[["mean_net_return", "mean_excess_return", "win_rate"]]
        .agg(["mean", "std"])
        .reset_index()
    )
    strategy_summary.columns = [
        "_".join(part for part in column if part) if isinstance(column, tuple) else column
        for column in strategy_summary.columns
    ]
    sections = [
        "# ANN Combination, Similarity, and 3D Latent Lab",
        "",
        "## Decision status",
        "",
        "Paper-only. No brokerage, live probability, portfolio, or website behavior was modified.",
        f"The {sealed_manifest['trading_dates']}-date holdout remains sealed from "
        f"{sealed_manifest['date_start']} through {sealed_manifest['date_end']}.",
        "Coordinates are experimental train-only PCA features, not evidence by themselves.",
        "",
        "## Combination screen",
        "",
        markdown_table(
            screen_summary,
            [
                "combination",
                "auc_mean",
                "brier_mean",
                "mean_excess_return_mean",
                "win_rate_mean",
                "selection_score",
            ],
            rows=len(screen_summary),
        ),
        "",
        "## Architecture challenge",
        "",
        f"Selected pre-holdout finalist: `{selected_combination}` with `{selected_architecture}`.",
        "Selection is provisional and does not open or score the sealed holdout.",
        "",
        markdown_table(
            architecture_summary,
            [
                "combination",
                "architecture",
                "auc_mean",
                "brier_mean",
                "mean_excess_return_mean",
                "selection_score",
            ],
            rows=len(architecture_summary),
        ),
        "",
        "## Within-date permutation importance",
        "",
        markdown_table(
            importance,
            [
                "feature",
                "auc_importance_mean",
                "brier_importance_mean",
                "excess_return_importance_mean",
                "auc_positive",
                "excess_positive",
            ],
            rows=len(importance),
        ),
        "",
        "Positive importance means performance deteriorated after that variable was shuffled within each date.",
        "",
        "## Similarity-filter paper scenarios",
        "",
        markdown_table(
            strategy_summary,
            [
                "strategy",
                "mean_net_return_mean",
                "mean_excess_return_mean",
                "win_rate_mean",
            ],
            rows=len(strategy_summary),
        ),
        "",
        "## Interpretation guardrails",
        "",
        "- 3D axes can rotate or flip between fits; radius and distances are more stable than axis names.",
        "- A useful visualization is not automatically a useful predictor.",
        "- Combination selection is multiple testing and must be confirmed before the holdout is opened.",
        "- Survivorship, universe selection, overlapping labels, and market-regime bias remain possible.",
        "- Similarity filtering is a paper scenario, not an order rule.",
    ]
    path.write_text("\n".join(sections) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    seeds = [int(value.strip()) for value in args.seeds.split(",") if value.strip()]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    prices = load_prices(Path(args.db))
    features = build_base_features(prices, args.horizon, args.top_k)
    del prices
    prepared, sealed_manifest, leakage_rows, latent_manifests = build_splits(features, args)
    del features

    screen_metrics, screen_summary = run_screen(prepared, seeds, args)
    finalists = screen_summary.head(3)["combination"].tolist()
    architecture_metrics, architecture_summary = run_architecture_challenge(
        prepared,
        seeds,
        finalists,
        args,
    )
    selected = architecture_summary.iloc[0]
    selected_combination = str(selected["combination"])
    selected_architecture = str(selected["architecture"])
    importance_runs, strategy_runs, latent_points = run_finalist_analysis(
        prepared,
        seeds,
        selected_combination,
        selected_architecture,
        args,
    )
    feature_importance = importance_summary(importance_runs)

    screen_metrics.to_csv(output_dir / "combination_screen_by_run.csv", index=False)
    screen_summary.to_csv(output_dir / "combination_screen_summary.csv", index=False)
    architecture_metrics.to_csv(output_dir / "architecture_challenge_by_run.csv", index=False)
    architecture_summary.to_csv(output_dir / "architecture_challenge_summary.csv", index=False)
    importance_runs.to_csv(output_dir / "within_date_permutation_by_run.csv", index=False)
    feature_importance.to_csv(output_dir / "within_date_permutation_summary.csv", index=False)
    strategy_runs.to_csv(output_dir / "similarity_filter_scenarios.csv", index=False)
    latent_points.to_csv(output_dir / "latent3d_points.csv", index=False)
    pd.DataFrame(leakage_rows).to_csv(output_dir / "leakage_audit.csv", index=False)
    (output_dir / "sealed_holdout_manifest.json").write_text(
        json.dumps(sealed_manifest, indent=2), encoding="utf-8"
    )
    manifest = {
        "schema_version": 1,
        "paper_only": True,
        "live_trading_enabled": False,
        "probability_display_enabled": False,
        "horizon": args.horizon,
        "seeds": seeds,
        "splits": args.splits,
        "test_dates": args.test_dates,
        "train_dates": args.train_dates,
        "graph_lookback_dates": args.graph_lookback_dates,
        "top_k": args.top_k,
        "neighbors": args.neighbors,
        "portfolio_size": args.portfolio_size,
        "combinations": COMBINATIONS,
        "architectures": ARCHITECTURES,
        "screen_finalists": finalists,
        "selected_combination": selected_combination,
        "selected_architecture": selected_architecture,
        "latent_manifests": latent_manifests,
        "selection_note": "Selected only from pre-holdout validation; holdout remains unopened.",
    }
    (output_dir / "experiment_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    write_readout(
        output_dir / "combination_similarity_readout.md",
        screen_summary,
        architecture_summary,
        feature_importance,
        strategy_runs,
        selected_combination,
        selected_architecture,
        sealed_manifest,
    )
    print(f"Outputs written to {output_dir}", flush=True)
    print(
        f"Provisional pre-holdout finalist: {selected_combination} / {selected_architecture}",
        flush=True,
    )


if __name__ == "__main__":
    main()
