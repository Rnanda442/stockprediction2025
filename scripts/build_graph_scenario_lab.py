#!/usr/bin/env python3
"""Leakage-safe 3D stock graph and blind paper-scenario experiment.

This challenger lab intentionally stays outside the production tournament. It learns
one stock graph per chronological split using training-window information only,
adds graph context to an ANN, and replays decisions before revealing future prices.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix, diags
from scipy.sparse.linalg import eigsh
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    mean_absolute_error,
    r2_score,
    roc_auc_score,
)
from sklearn.neighbors import NearestNeighbors
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
BASE_FEATURES = (
    "pct_1d",
    "pct_2d",
    "pct_3d",
    "pct_5d",
    "volatility_5d",
    "volatility_10d",
    "momentum_slope_5d",
    "ma_crossover",
    "ret_10d",
    "ret_20d",
    "ret_60d",
    "riskadj_mom_60d",
    "vol_20d",
    "vol_60d",
    "trend_slope_60d",
    "trend_r2_60d",
    "z_ma20",
    "bb_width_20d",
    "dollar_vol_20d",
    "ac1_5d",
    "max_dd_60d",
    "time_since_max_60d",
)
NODE_FEATURES = (
    "ret_20d",
    "ret_60d",
    "vol_20d",
    "vol_60d",
    "trend_slope_60d",
    "trend_r2_60d",
    "max_dd_60d",
    "dollar_vol_20d",
)
NEIGHBOR_SIGNALS = {
    "ret_20d": "graph_neighbor_momentum",
    "vol_20d": "graph_neighbor_volatility",
    "trend_slope_60d": "graph_neighbor_trend",
    "max_dd_60d": "graph_neighbor_drawdown",
}
GRAPH_FEATURES = (
    "graph_x",
    "graph_y",
    "graph_z",
    "graph_strength_rank",
    "graph_degree_rank",
    "graph_neighbor_momentum",
    "graph_neighbor_volatility",
    "graph_neighbor_trend",
    "graph_neighbor_drawdown",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "vectorized.db")
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "analytics" / "graph_scenario_lab"
    )
    parser.add_argument("--horizons", default="5,20,60")
    parser.add_argument("--lookback-dates", type=int, default=756)
    parser.add_argument("--train-window-dates", type=int, default=504)
    parser.add_argument("--graph-window-dates", type=int, default=126)
    parser.add_argument("--test-dates", type=int, default=126)
    parser.add_argument("--splits", type=int, default=3)
    parser.add_argument("--neighbors", type=int, default=10)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--rebalance-every", type=int, default=5)
    parser.add_argument("--transaction-cost-bps", type=float, default=10.0)
    parser.add_argument("--max-train-rows", type=int, default=120_000)
    parser.add_argument("--seed", type=int, default=442)
    return parser.parse_args()


def safe_auc(y_true: np.ndarray, score: np.ndarray) -> float:
    return float(roc_auc_score(y_true, score)) if np.unique(y_true).size > 1 else np.nan


def safe_rank(values: pd.Series) -> pd.Series:
    return values.rank(method="average", pct=True).fillna(0.5)


def sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def price_feature_frame(
    prices: pd.DataFrame,
    horizons: tuple[int, ...],
) -> pd.DataFrame:
    """Rebuild a compact feature panel from the archived RecentPrices table."""
    prices["begins_at"] = pd.to_datetime(prices["begins_at"], errors="coerce")
    prices = prices.dropna(subset=["begins_at", "ticker", "close_price"])
    prices = prices.sort_values(["ticker", "begins_at"]).reset_index(drop=True)
    prices["ticker"] = prices["ticker"].astype(str)
    prices["close_price"] = pd.to_numeric(prices["close_price"], errors="coerce")
    prices["volume"] = pd.to_numeric(prices["volume"], errors="coerce").fillna(0.0)

    grouped_close = prices.groupby("ticker", sort=False)["close_price"]
    prices["pct_1d"] = grouped_close.pct_change(1, fill_method=None)
    prices["pct_2d"] = grouped_close.pct_change(2, fill_method=None)
    prices["pct_3d"] = grouped_close.pct_change(3, fill_method=None)
    prices["pct_5d"] = grouped_close.pct_change(5, fill_method=None)
    prices["ret_10d"] = grouped_close.pct_change(10, fill_method=None)
    prices["ret_20d"] = grouped_close.pct_change(20, fill_method=None)
    prices["ret_60d"] = grouped_close.pct_change(60, fill_method=None)

    return_group = prices.groupby("ticker", sort=False)["pct_1d"]
    prices["volatility_5d"] = return_group.transform(
        lambda values: values.rolling(5, min_periods=4).std()
    )
    prices["volatility_10d"] = return_group.transform(
        lambda values: values.rolling(10, min_periods=6).std()
    )
    prices["vol_20d"] = return_group.transform(
        lambda values: values.rolling(20, min_periods=12).std()
    )
    prices["vol_60d"] = return_group.transform(
        lambda values: values.rolling(60, min_periods=30).std()
    )
    prices["ac1_5d"] = return_group.transform(
        lambda values: values.rolling(5, min_periods=4).corr(values.shift(1))
    )

    shift_4 = grouped_close.shift(4)
    shift_59 = grouped_close.shift(59)
    prices["momentum_slope_5d"] = (prices["close_price"] / shift_4 - 1.0) / 4.0
    prices["trend_slope_60d"] = (prices["close_price"] / shift_59 - 1.0) / 59.0

    moving_5 = grouped_close.transform(lambda values: values.rolling(5, min_periods=4).mean())
    moving_20 = grouped_close.transform(lambda values: values.rolling(20, min_periods=12).mean())
    moving_60 = grouped_close.transform(lambda values: values.rolling(60, min_periods=30).mean())
    price_std_20 = grouped_close.transform(lambda values: values.rolling(20, min_periods=12).std())
    rolling_high_60 = grouped_close.transform(lambda values: values.rolling(60, min_periods=30).max())
    path_distance_60 = prices.groupby("ticker", sort=False)["close_price"].transform(
        lambda values: values.diff().abs().rolling(60, min_periods=30).sum()
    )
    direct_distance_60 = (prices["close_price"] - shift_59).abs()

    prices["ma_crossover"] = moving_5 / moving_20 - 1.0
    prices["z_ma20"] = (prices["close_price"] - moving_20) / price_std_20.replace(0.0, np.nan)
    prices["bb_width_20d"] = 4.0 * price_std_20 / moving_20.replace(0.0, np.nan)
    prices["max_dd_60d"] = prices["close_price"] / rolling_high_60 - 1.0
    prices["trend_r2_60d"] = (
        direct_distance_60 / path_distance_60.replace(0.0, np.nan)
    ).clip(0.0, 1.0)
    prices["riskadj_mom_60d"] = prices["ret_60d"] / (
        prices["vol_60d"].replace(0.0, np.nan) * math.sqrt(60.0)
    )
    prices["dollar_volume"] = prices["close_price"] * prices["volume"]
    prices["dollar_vol_20d"] = prices.groupby("ticker", sort=False)["dollar_volume"].transform(
        lambda values: values.rolling(20, min_periods=12).mean()
    )
    prices["time_since_max_60d"] = prices.groupby("ticker", sort=False)["close_price"].transform(
        lambda values: values.rolling(60, min_periods=30).apply(
            lambda window: float(len(window) - 1 - np.argmax(window)), raw=True
        )
    )

    for horizon in horizons:
        future_price = grouped_close.shift(-horizon)
        future_date = prices.groupby("ticker", sort=False)["begins_at"].shift(-horizon)
        prices[f"future_price_{horizon}d"] = future_price
        prices[f"future_date_{horizon}d"] = future_date
        prices[f"future_return_{horizon}d"] = future_price / prices["close_price"] - 1.0
    return prices.drop(columns=["dollar_volume"])


def load_frame(db_path: Path, horizons: tuple[int, ...], lookback_dates: int) -> pd.DataFrame:
    if not db_path.exists():
        raise FileNotFoundError(f"Vectorized database not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "VectorizedFeatures" in tables:
            columns = ["begins_at", "ticker", "close_price", *BASE_FEATURES]
            date_rows = conn.execute(
                """
                SELECT DISTINCT begins_at
                FROM VectorizedFeatures
                WHERE span = '5year'
                ORDER BY begins_at DESC
                LIMIT ?
                """,
                (lookback_dates,),
            ).fetchall()
            if not date_rows:
                raise RuntimeError("VectorizedFeatures has no 5year rows.")
            cutoff = min(row[0] for row in date_rows)
            query = f"""
                SELECT {', '.join(columns)}
                FROM VectorizedFeatures
                WHERE span = '5year' AND begins_at >= ?
                ORDER BY ticker, begins_at
            """
            frame = pd.read_sql_query(query, conn, params=(cutoff,))
        elif "RecentPrices" in tables:
            date_rows = conn.execute(
                """
                SELECT DISTINCT begins_at
                FROM RecentPrices
                ORDER BY begins_at DESC
                LIMIT ?
                """,
                (lookback_dates,),
            ).fetchall()
            if not date_rows:
                raise RuntimeError("RecentPrices has no rows.")
            cutoff = min(row[0] for row in date_rows)
            frame = pd.read_sql_query(
                """
                SELECT ticker, begins_at, close_price, volume
                FROM RecentPrices
                WHERE begins_at >= ?
                ORDER BY ticker, begins_at
                """,
                conn,
                params=(cutoff,),
            )
            return price_feature_frame(frame, horizons)
        else:
            raise RuntimeError("Database has neither VectorizedFeatures nor RecentPrices.")

    frame["begins_at"] = pd.to_datetime(frame["begins_at"], errors="coerce")
    frame = frame.dropna(subset=["begins_at", "ticker", "close_price"])
    frame = frame.sort_values(["ticker", "begins_at"]).reset_index(drop=True)
    frame["ticker"] = frame["ticker"].astype(str)
    for horizon in horizons:
        future_price = frame.groupby("ticker", sort=False)["close_price"].shift(-horizon)
        future_date = frame.groupby("ticker", sort=False)["begins_at"].shift(-horizon)
        frame[f"future_price_{horizon}d"] = future_price
        frame[f"future_date_{horizon}d"] = future_date
        frame[f"future_return_{horizon}d"] = future_price / frame["close_price"] - 1.0
    return frame


def chronological_splits(
    frame: pd.DataFrame,
    horizon: int,
    test_dates: int,
    split_count: int,
    train_window_dates: int,
) -> list[dict[str, object]]:
    target = f"future_return_{horizon}d"
    usable = frame.loc[frame[target].notna(), "begins_at"].drop_duplicates().sort_values()
    all_dates = frame["begins_at"].drop_duplicates().sort_values().tolist()
    if len(usable) < 80:
        return []

    pool = usable.iloc[-min(test_dates, max(20, len(usable) // 3)) :].tolist()
    chunks = [list(chunk) for chunk in np.array_split(pool, split_count) if len(chunk)]
    date_position = {date: index for index, date in enumerate(all_dates)}
    splits: list[dict[str, object]] = []
    for split_number, chunk in enumerate(chunks, start=1):
        test_start = chunk[0]
        embargo_end = date_position[test_start] - horizon
        if embargo_end <= 0:
            continue
        eligible_train = all_dates[:embargo_end]
        train_dates = eligible_train[-train_window_dates:]
        if len(train_dates) < min(126, train_window_dates // 2):
            continue
        splits.append(
            {
                "split": split_number,
                "train_dates": train_dates,
                "test_dates": chunk,
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "test_start": chunk[0],
                "test_end": chunk[-1],
            }
        )
    return splits


def fit_training_graph(
    train: pd.DataFrame,
    graph_window_dates: int,
    neighbor_count: int,
) -> tuple[pd.DataFrame, csr_matrix, list[str]]:
    graph_dates = train["begins_at"].drop_duplicates().sort_values().iloc[-graph_window_dates:]
    recent = train[train["begins_at"].isin(graph_dates)].copy()
    latest = recent.sort_values("begins_at").groupby("ticker", as_index=False).tail(1)
    latest = latest.drop_duplicates("ticker").sort_values("ticker").reset_index(drop=True)
    nodes = latest["ticker"].tolist()
    if len(nodes) < 5:
        raise RuntimeError("Not enough tickers to build a graph.")

    node_matrix = latest.loc[:, NODE_FEATURES].replace([np.inf, -np.inf], np.nan)
    node_matrix = SimpleImputer(strategy="median").fit_transform(node_matrix)
    node_matrix = StandardScaler().fit_transform(node_matrix)

    neighbors = min(max(2, neighbor_count), len(nodes) - 1)
    search = NearestNeighbors(n_neighbors=neighbors + 1, metric="euclidean")
    distances, indices = search.fit(node_matrix).kneighbors(node_matrix)
    distances = distances[:, 1:]
    indices = indices[:, 1:]
    positive_distances = distances[distances > 0]
    scale = float(np.median(positive_distances)) if positive_distances.size else 1.0
    weights = np.exp(-np.square(distances) / (2.0 * scale * scale))

    rows = np.repeat(np.arange(len(nodes)), neighbors)
    cols = indices.reshape(-1)
    data = weights.reshape(-1)
    adjacency = csr_matrix((data, (rows, cols)), shape=(len(nodes), len(nodes)))
    adjacency = adjacency.maximum(adjacency.T)
    adjacency.setdiag(0.0)
    adjacency.eliminate_zeros()

    strength = np.asarray(adjacency.sum(axis=1)).ravel()
    degree = np.diff(adjacency.indptr).astype(float)
    inverse_root = np.zeros_like(strength)
    positive = strength > 0
    inverse_root[positive] = 1.0 / np.sqrt(strength[positive])
    normalized = diags(inverse_root) @ adjacency @ diags(inverse_root)

    coordinate_count = min(4, len(nodes) - 1)
    try:
        eigenvalues, eigenvectors = eigsh(normalized, k=coordinate_count, which="LA")
        order = np.argsort(eigenvalues)[::-1]
        coordinates = eigenvectors[:, order[1:4]]
    except Exception:
        coordinates = np.zeros((len(nodes), 3), dtype=float)
    if coordinates.shape[1] < 3:
        coordinates = np.pad(coordinates, ((0, 0), (0, 3 - coordinates.shape[1])))
    for column in range(3):
        vector = coordinates[:, column]
        if vector.size and vector[np.argmax(np.abs(vector))] < 0:
            coordinates[:, column] *= -1.0
        spread = float(np.std(coordinates[:, column]))
        if spread > 0:
            coordinates[:, column] /= spread

    graph = pd.DataFrame(
        {
            "ticker": nodes,
            "graph_x": coordinates[:, 0],
            "graph_y": coordinates[:, 1],
            "graph_z": coordinates[:, 2],
            "graph_strength": strength,
            "graph_degree": degree,
        }
    )
    graph["graph_strength_rank"] = safe_rank(graph["graph_strength"])
    graph["graph_degree_rank"] = safe_rank(graph["graph_degree"])
    liquidity = np.log1p(pd.to_numeric(latest["dollar_vol_20d"], errors="coerce").clip(lower=0))
    graph["liquidity_rank"] = safe_rank(liquidity)
    graph["bubble_size"] = 8.0 + 32.0 * (
        0.60 * graph["liquidity_rank"] + 0.40 * graph["graph_strength_rank"]
    )

    row_normalizer = np.zeros_like(strength)
    row_normalizer[positive] = 1.0 / strength[positive]
    transition = diags(row_normalizer) @ adjacency
    return graph, transition.tocsr(), nodes


def attach_graph_context(
    frame: pd.DataFrame,
    graph: pd.DataFrame,
    transition: csr_matrix,
    nodes: list[str],
) -> pd.DataFrame:
    enriched = frame.merge(graph, on="ticker", how="left")
    neighbor_parts: list[pd.DataFrame] = []
    for date, group in frame.groupby("begins_at", sort=False):
        values = group.drop_duplicates("ticker").set_index("ticker")
        matrix = values.reindex(nodes)[list(NEIGHBOR_SIGNALS)].apply(pd.to_numeric, errors="coerce")
        matrix = matrix.replace([np.inf, -np.inf], np.nan)
        for column in matrix.columns:
            median = matrix[column].median()
            matrix[column] = matrix[column].fillna(0.0 if pd.isna(median) else median)
        neighbor_values = transition @ matrix.to_numpy(dtype=float)
        part = pd.DataFrame(neighbor_values, columns=list(NEIGHBOR_SIGNALS.values()))
        part.insert(0, "ticker", nodes)
        part.insert(0, "begins_at", date)
        neighbor_parts.append(part)
    neighbor_frame = pd.concat(neighbor_parts, ignore_index=True)
    enriched = enriched.merge(neighbor_frame, on=["begins_at", "ticker"], how="left")
    for column in GRAPH_FEATURES:
        enriched[column] = pd.to_numeric(enriched[column], errors="coerce").fillna(0.0)
    return enriched


def bounded_training_sample(frame: pd.DataFrame, max_rows: int, seed: int) -> pd.DataFrame:
    if len(frame) <= max_rows:
        return frame
    return frame.sample(n=max_rows, random_state=seed).sort_values(["begins_at", "ticker"])


def fit_predict_models(
    train: pd.DataFrame,
    test: pd.DataFrame,
    horizon: int,
    max_train_rows: int,
    seed: int,
) -> dict[str, dict[str, np.ndarray]]:
    target_return = f"future_return_{horizon}d"
    sampled = bounded_training_sample(train.dropna(subset=[target_return]), max_train_rows, seed)
    y_return = sampled[target_return].to_numpy(dtype=float)
    y_up = (y_return > 0.0).astype(int)
    if np.unique(y_up).size < 2:
        raise RuntimeError("Training target contains only one class.")

    base_columns = list(BASE_FEATURES)
    graph_columns = [*BASE_FEATURES, *GRAPH_FEATURES]
    ridge = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=12.0))
    ann = make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        MLPClassifier(
            hidden_layer_sizes=(48, 24),
            alpha=0.003,
            batch_size=512,
            learning_rate_init=0.001,
            max_iter=90,
            early_stopping=True,
            validation_fraction=0.12,
            n_iter_no_change=8,
            random_state=seed,
        ),
    )
    graph_ann = make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        MLPClassifier(
            hidden_layer_sizes=(64, 32, 12),
            alpha=0.005,
            batch_size=512,
            learning_rate_init=0.0008,
            max_iter=110,
            early_stopping=True,
            validation_fraction=0.12,
            n_iter_no_change=10,
            random_state=seed + 1,
        ),
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ridge.fit(sampled[base_columns], y_return)
        ann.fit(sampled[base_columns], y_up)
        graph_ann.fit(sampled[graph_columns], y_up)

    ridge_train = ridge.predict(sampled[base_columns])
    ridge_scale = float(np.std(ridge_train)) or 1.0
    ridge_center = float(np.median(ridge_train))
    ridge_score = ridge.predict(test[base_columns])
    return {
        "ridge_return": {
            "score": ridge_score,
            "probability": sigmoid((ridge_score - ridge_center) / ridge_scale),
            "prediction": (ridge_score > 0.0).astype(int),
        },
        "ann_base": {
            "score": ann.predict_proba(test[base_columns])[:, 1],
            "probability": ann.predict_proba(test[base_columns])[:, 1],
            "prediction": ann.predict(test[base_columns]),
        },
        "graph_ann": {
            "score": graph_ann.predict_proba(test[graph_columns])[:, 1],
            "probability": graph_ann.predict_proba(test[graph_columns])[:, 1],
            "prediction": graph_ann.predict(test[graph_columns]),
        },
    }


def evaluation_rows(
    predictions: dict[str, dict[str, np.ndarray]],
    train: pd.DataFrame,
    test: pd.DataFrame,
    horizon: int,
    split_meta: dict[str, object],
) -> list[dict[str, object]]:
    target_return = f"future_return_{horizon}d"
    y_return = test[target_return].to_numpy(dtype=float)
    y_up = (y_return > 0.0).astype(int)
    train_rate = float((train[target_return] > 0.0).mean())
    null_brier = float(np.mean(np.square(y_up - train_rate)))
    rows: list[dict[str, object]] = []
    for model, result in predictions.items():
        probability = np.asarray(result["probability"], dtype=float)
        prediction = np.asarray(result["prediction"], dtype=int)
        brier = float(brier_score_loss(y_up, probability))
        record: dict[str, object] = {
            "horizon": horizon,
            "split": split_meta["split"],
            "model": model,
            "train_start": split_meta["train_start"],
            "train_end": split_meta["train_end"],
            "test_start": split_meta["test_start"],
            "test_end": split_meta["test_end"],
            "train_rows": len(train),
            "test_rows": len(test),
            "positive_rate": float(y_up.mean()),
            "auc": safe_auc(y_up, np.asarray(result["score"], dtype=float)),
            "accuracy": float(accuracy_score(y_up, prediction)),
            "brier": brier,
            "brier_skill": 1.0 - brier / null_brier if null_brier > 0 else np.nan,
            "mae_return": np.nan,
            "r2_return": np.nan,
        }
        if model == "ridge_return":
            record["mae_return"] = float(mean_absolute_error(y_return, result["score"]))
            record["r2_return"] = float(r2_score(y_return, result["score"]))
        rows.append(record)
    return rows


def blind_scenario_rows(
    predictions: dict[str, dict[str, np.ndarray]],
    test: pd.DataFrame,
    horizon: int,
    split_meta: dict[str, object],
    top_k: int,
    rebalance_every: int,
    transaction_cost_bps: float,
    seed: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    target_return = f"future_return_{horizon}d"
    future_price = f"future_price_{horizon}d"
    future_date = f"future_date_{horizon}d"
    scored = test.copy().reset_index(drop=True)
    for model, result in predictions.items():
        scored[f"score_{model}"] = result["score"]

    available_dates = scored["begins_at"].drop_duplicates().sort_values().tolist()
    decision_dates = available_dates[:: max(1, rebalance_every)]
    round_trip_cost = 2.0 * transaction_cost_bps / 10_000.0
    selections: list[dict[str, object]] = []
    cohorts: list[dict[str, object]] = []

    for date_number, date in enumerate(decision_dates):
        day = scored[scored["begins_at"] == date].dropna(subset=[target_return]).copy()
        if day.empty:
            continue
        universe_return = float(day[target_return].mean() - round_trip_cost)
        cohorts.append(
            {
                "horizon": horizon,
                "split": split_meta["split"],
                "decision_date": date,
                "strategy": "equal_weight_universe",
                "holdings": len(day),
                "net_return": universe_return,
                "benchmark_return": universe_return,
                "excess_return": 0.0,
            }
        )

        random_day = day.sample(n=min(top_k, len(day)), random_state=seed + date_number + horizon)
        random_return = float(random_day[target_return].mean() - round_trip_cost)
        cohorts.append(
            {
                "horizon": horizon,
                "split": split_meta["split"],
                "decision_date": date,
                "strategy": "random_top_k",
                "holdings": len(random_day),
                "net_return": random_return,
                "benchmark_return": universe_return,
                "excess_return": random_return - universe_return,
            }
        )

        for model in predictions:
            score_column = f"score_{model}"
            chosen = day.nlargest(min(top_k, len(day)), score_column).copy()
            cohort_return = float(chosen[target_return].mean() - round_trip_cost)
            cohorts.append(
                {
                    "horizon": horizon,
                    "split": split_meta["split"],
                    "decision_date": date,
                    "strategy": model,
                    "holdings": len(chosen),
                    "net_return": cohort_return,
                    "benchmark_return": universe_return,
                    "excess_return": cohort_return - universe_return,
                }
            )
            chosen = chosen.sort_values(score_column, ascending=False)
            for rank, row in enumerate(chosen.itertuples(index=False), start=1):
                selections.append(
                    {
                        "horizon": horizon,
                        "split": split_meta["split"],
                        "strategy": model,
                        "decision_date": date,
                        "training_cutoff": split_meta["train_end"],
                        "ticker": row.ticker,
                        "rank": rank,
                        "score": getattr(row, score_column),
                        "entry_price": row.close_price,
                        "exit_date": getattr(row, future_date),
                        "exit_price": getattr(row, future_price),
                        "gross_return": getattr(row, target_return),
                        "net_return": getattr(row, target_return) - round_trip_cost,
                        "graph_x": getattr(row, "graph_x", np.nan),
                        "graph_y": getattr(row, "graph_y", np.nan),
                        "graph_z": getattr(row, "graph_z", np.nan),
                        "bubble_size": getattr(row, "bubble_size", np.nan),
                    }
                )
    return selections, cohorts


def max_drawdown(returns: pd.Series) -> float:
    wealth = (1.0 + returns.fillna(0.0)).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0
    return float(drawdown.min()) if len(drawdown) else np.nan


def summarize_cohorts(cohorts: pd.DataFrame, rebalance_every: int) -> pd.DataFrame:
    summaries: list[dict[str, object]] = []
    for (horizon, strategy), group in cohorts.groupby(["horizon", "strategy"], sort=True):
        ordered = group.sort_values(["decision_date", "split"]).reset_index(drop=True)
        sleeve_count = max(1, math.ceil(int(horizon) / max(1, rebalance_every)))
        sleeve_compounds: list[float] = []
        sleeve_drawdowns: list[float] = []
        for offset in range(sleeve_count):
            sleeve = ordered.iloc[offset::sleeve_count]["net_return"]
            if len(sleeve):
                sleeve_compounds.append(float((1.0 + sleeve).prod() - 1.0))
                sleeve_drawdowns.append(max_drawdown(sleeve))
        summaries.append(
            {
                "horizon": horizon,
                "strategy": strategy,
                "cohorts": len(ordered),
                "holdings_evaluated": int(ordered["holdings"].sum()),
                "mean_net_return": float(ordered["net_return"].mean()),
                "median_net_return": float(ordered["net_return"].median()),
                "win_rate": float((ordered["net_return"] > 0.0).mean()),
                "mean_excess_vs_universe": float(ordered["excess_return"].mean()),
                "worst_cohort_return": float(ordered["net_return"].min()),
                "average_sleeve_compound_return": float(np.mean(sleeve_compounds)),
                "worst_sleeve_max_drawdown": float(np.min(sleeve_drawdowns)),
                "rebalance_every_trading_days": rebalance_every,
                "capital_sleeves": sleeve_count,
            }
        )
    return pd.DataFrame(summaries)


def write_readout(
    output_path: Path,
    evaluation: pd.DataFrame,
    summary: pd.DataFrame,
    audit: pd.DataFrame,
) -> None:
    mean_eval = (
        evaluation.groupby(["horizon", "model"], as_index=False)[["auc", "brier_skill", "accuracy"]]
        .mean()
        .sort_values(["horizon", "auc"], ascending=[True, False])
    )
    lines = [
        "# 3D Graph + Blind Scenario Lab",
        "",
        "## Guardrail",
        "",
        "Exploratory and paper-only. Every graph was fitted before its test window, and training labels were embargoed by the forecast horizon.",
        "",
        "## Mean out-of-sample model results",
        "",
        mean_eval.to_string(index=False, float_format=lambda value: f"{value:.4f}"),
        "",
        "## Blind top-k scenario results",
        "",
        summary.to_string(index=False, float_format=lambda value: f"{value:.4f}"),
        "",
        "## Leakage audit",
        "",
        audit.to_string(index=False),
        "",
        "Bubble size is visualization context only: 60% prediction-time liquidity rank and 40% graph-strength rank. It is not treated as a future-return label.",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    horizons = tuple(int(value.strip()) for value in args.horizons.split(",") if value.strip())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame = load_frame(args.db, horizons, args.lookback_dates)

    evaluations: list[dict[str, object]] = []
    selections: list[dict[str, object]] = []
    cohorts: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    embeddings: list[pd.DataFrame] = []

    for horizon in horizons:
        target = f"future_return_{horizon}d"
        for split_meta in chronological_splits(
            frame,
            horizon,
            args.test_dates,
            args.splits,
            args.train_window_dates,
        ):
            train = frame[frame["begins_at"].isin(split_meta["train_dates"])].dropna(subset=[target])
            test = frame[frame["begins_at"].isin(split_meta["test_dates"])].dropna(subset=[target])
            graph, transition, nodes = fit_training_graph(
                train, args.graph_window_dates, args.neighbors
            )
            train_enriched = attach_graph_context(train, graph, transition, nodes)
            test_enriched = attach_graph_context(test, graph, transition, nodes)
            predictions = fit_predict_models(
                train_enriched,
                test_enriched,
                horizon,
                args.max_train_rows,
                args.seed + 100 * horizon + int(split_meta["split"]),
            )
            evaluations.extend(
                evaluation_rows(predictions, train_enriched, test_enriched, horizon, split_meta)
            )
            split_selections, split_cohorts = blind_scenario_rows(
                predictions,
                test_enriched,
                horizon,
                split_meta,
                args.top_k,
                args.rebalance_every,
                args.transaction_cost_bps,
                args.seed,
            )
            selections.extend(split_selections)
            cohorts.extend(split_cohorts)
            audits.append(
                {
                    "horizon": horizon,
                    "split": split_meta["split"],
                    "graph_cutoff": split_meta["train_end"],
                    "test_start": split_meta["test_start"],
                    "test_end": split_meta["test_end"],
                    "embargo_trading_days": horizon,
                    "graph_uses_test_or_future_rows": False,
                    "selection_uses_future_return": False,
                }
            )
            graph_output = graph.copy()
            graph_output.insert(0, "split", split_meta["split"])
            graph_output.insert(0, "horizon", horizon)
            graph_output["as_of_date"] = split_meta["train_end"]
            embeddings.append(graph_output)

    if not evaluations:
        raise RuntimeError("No chronological split had enough data to run the experiment.")

    evaluation_frame = pd.DataFrame(evaluations)
    selection_frame = pd.DataFrame(selections)
    cohort_frame = pd.DataFrame(cohorts)
    audit_frame = pd.DataFrame(audits)
    embedding_frame = pd.concat(embeddings, ignore_index=True)
    summary_frame = summarize_cohorts(cohort_frame, args.rebalance_every)

    evaluation_frame.to_csv(args.output_dir / "graph_challenger_evaluation.csv", index=False)
    selection_frame.to_csv(args.output_dir / "blind_portfolio_selections.csv", index=False)
    cohort_frame.to_csv(args.output_dir / "blind_portfolio_cohorts.csv", index=False)
    summary_frame.to_csv(args.output_dir / "blind_portfolio_summary.csv", index=False)
    audit_frame.to_csv(args.output_dir / "leakage_audit.csv", index=False)
    embedding_frame.to_csv(args.output_dir / "graph_3d_stock_embedding.csv", index=False)
    write_readout(args.output_dir / "graph_scenario_readout.md", evaluation_frame, summary_frame, audit_frame)

    manifest = {
        "database": str(args.db),
        "horizons": horizons,
        "models": ["ridge_return", "ann_base", "graph_ann"],
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
    print(summary_frame.to_string(index=False))
    print(f"\nOutputs written to {args.output_dir}")


if __name__ == "__main__":
    main()
