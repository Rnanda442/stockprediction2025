#!/usr/bin/env python3
"""Build a leakage-safe animated 3D stock-history and Monte Carlo research lab."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from plotly.offline import get_plotlyjs
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA


DEFAULT_DB = "warehouse/model_runs/runs/32915939359/artifact/research_history.db"
DEFAULT_OUTPUT = "artifacts/temporal_3d_mc_loss_activation_v1"
EXPERIMENT_ID = "temporal_3d_mc_loss_activation_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--context-gate", default="research_context/context_gate.json")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    parser.add_argument("--history-start", default="2024-01-01")
    parser.add_argument("--sealed-holdout-start", default="2026-05-29")
    parser.add_argument("--stocks", type=int, default=120)
    parser.add_argument("--frames", type=int, default=90)
    parser.add_argument("--graph-lookback", type=int, default=60)
    parser.add_argument("--neighbors", type=int, default=10)
    parser.add_argument("--mc-paths", type=int, default=2000)
    parser.add_argument("--mc-horizon", type=int, default=60)
    parser.add_argument("--mc-block", type=int, default=5)
    parser.add_argument("--seed", type=int, default=442)
    return parser.parse_args()


def assert_context_approval(path: Path) -> dict:
    gate = json.loads(path.read_text(encoding="utf-8"))
    candidates = gate.get("next_experiments", [])
    match = next((row for row in candidates if row.get("id") == EXPERIMENT_ID), None)
    if not match or match.get("status") != "approved_next":
        raise RuntimeError(f"Context gate has not approved {EXPERIMENT_ID}")
    return gate


def load_prices(db_path: Path, history_start: str, holdout_start: str) -> pd.DataFrame:
    query = """
        SELECT ticker, begins_at, close_price, volume
        FROM ResearchPrices
        WHERE begins_at >= ? AND begins_at < ?
        ORDER BY begins_at, ticker
    """
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(query, connection, params=[history_start, holdout_start])
    if frame.empty:
        raise RuntimeError("ResearchPrices returned no pre-holdout rows")
    frame["date"] = pd.to_datetime(frame["begins_at"], utc=True, errors="coerce").dt.tz_localize(None).dt.normalize()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame["close_price"] = pd.to_numeric(frame["close_price"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").clip(lower=0)
    frame = frame.dropna(subset=["date", "ticker", "close_price"])
    frame = frame[frame["close_price"] > 0]
    return (
        frame.groupby(["date", "ticker"], as_index=False)
        .agg(close_price=("close_price", "last"), volume=("volume", "sum"))
        .sort_values(["date", "ticker"])
    )


def select_universe(prices: pd.DataFrame, requested: int) -> list[str]:
    dates = np.sort(prices["date"].unique())
    recent_dates = set(dates[-60:])
    recent = prices[prices["date"].isin(recent_dates)].copy()
    recent["dollar_volume"] = recent["close_price"] * recent["volume"]
    observations = prices.groupby("ticker")["date"].nunique()
    eligible = observations[observations >= max(180, int(len(dates) * 0.55))].index
    liquidity = recent[recent["ticker"].isin(eligible)].groupby("ticker")["dollar_volume"].median()
    tickers = liquidity.nlargest(requested).index.tolist()
    if len(tickers) < 20:
        raise RuntimeError("Fewer than 20 liquid tickers have sufficient history")
    return tickers


def robust_z(series: pd.Series) -> pd.Series:
    median = series.median()
    mad = (series - median).abs().median()
    scale = 1.4826 * mad
    if not np.isfinite(scale) or scale < 1e-9:
        scale = series.std()
    if not np.isfinite(scale) or scale < 1e-9:
        return pd.Series(0.0, index=series.index)
    return ((series - median) / scale).clip(-4.0, 4.0)


def price_matrices(prices: pd.DataFrame, tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = prices[prices["ticker"].isin(tickers)]
    close = selected.pivot(index="date", columns="ticker", values="close_price").sort_index()
    volume = selected.pivot(index="date", columns="ticker", values="volume").reindex(close.index)
    close = close.reindex(columns=tickers).ffill(limit=3)
    volume = volume.reindex(columns=tickers).fillna(0)
    return close, volume


def calculate_base_features(close: pd.DataFrame, volume: pd.DataFrame) -> dict[str, pd.DataFrame]:
    daily_log_return = np.log(close).diff().clip(-0.35, 0.35)
    ret_5d = (close / close.shift(5) - 1.0).clip(-0.8, 3.0)
    ret_20d = (close / close.shift(20) - 1.0).clip(-0.9, 5.0)
    ret_60d = (close / close.shift(60) - 1.0).clip(-0.95, 8.0)
    vol_20d = daily_log_return.rolling(20, min_periods=14).std() * math.sqrt(252)
    vol_60d = daily_log_return.rolling(60, min_periods=40).std() * math.sqrt(252)
    drawdown_60d = close / close.rolling(60, min_periods=40).max() - 1.0
    dollar_volume = close * volume
    dollar_vol_20d_log = np.log1p(dollar_volume.rolling(20, min_periods=10).median())
    valid_five_day = (ret_5d > 0).where(ret_5d.notna())
    empirical_upside = valid_five_day.rolling(126, min_periods=40).mean()
    return {
        "daily_log_return": daily_log_return,
        "ret_5d": ret_5d,
        "ret_20d": ret_20d,
        "ret_60d": ret_60d,
        "vol_20d": vol_20d,
        "vol_60d": vol_60d,
        "drawdown_60d": drawdown_60d,
        "dollar_vol_20d_log": dollar_vol_20d_log,
        "empirical_upside_probability_5d": empirical_upside,
    }


def choose_animation_dates(close: pd.DataFrame, frames: int, graph_lookback: int) -> list[pd.Timestamp]:
    enough_history = close.index[graph_lookback + 20 :]
    coverage = close.notna().mean(axis=1)
    valid = [date for date in enough_history if coverage.loc[date] >= 0.80]
    if not valid:
        raise RuntimeError("No dates have enough cross-sectional coverage")
    return valid[-frames:]


def graph_features_for_date(
    date: pd.Timestamp,
    features: dict[str, pd.DataFrame],
    tickers: list[str],
    lookback: int,
    neighbors: int,
) -> pd.DataFrame:
    daily = features["daily_log_return"]
    position = daily.index.get_loc(date)
    history = daily.iloc[max(0, position - lookback + 1) : position + 1]
    corr = history.corr(min_periods=max(20, lookback // 2)).reindex(index=tickers, columns=tickers).fillna(0.0)
    matrix = corr.to_numpy(dtype=float, copy=True)
    np.fill_diagonal(matrix, 0.0)
    positive = np.clip(matrix, 0.0, 1.0)
    degree = (positive >= 0.35).sum(axis=1).astype(float)
    count = min(neighbors, max(1, len(tickers) - 1))
    top_indices = np.argsort(positive, axis=1)[:, -count:]
    ret_20 = features["ret_20d"].loc[date].reindex(tickers).to_numpy(dtype=float)
    similarity_mean = np.zeros(len(tickers), dtype=float)
    neighbor_return = np.full(len(tickers), np.nan, dtype=float)
    for row_index, neighbor_index in enumerate(top_indices):
        weights = positive[row_index, neighbor_index]
        values = ret_20[neighbor_index]
        usable = np.isfinite(values) & (weights > 0)
        if usable.any():
            usable_weights = weights[usable]
            similarity_mean[row_index] = float(usable_weights.mean())
            neighbor_return[row_index] = float(np.average(values[usable], weights=usable_weights))
    result = pd.DataFrame(index=tickers)
    result["graph_degree"] = degree
    result["graph_similarity_mean"] = similarity_mean
    result["neighbor_ret_20d"] = neighbor_return
    result["neighbor_divergence"] = result["neighbor_ret_20d"] - ret_20
    return result


def build_visual_frame_rows(
    dates: list[pd.Timestamp],
    tickers: list[str],
    features: dict[str, pd.DataFrame],
    graph_lookback: int,
    neighbors: int,
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    base_names = [
        "ret_5d",
        "ret_20d",
        "ret_60d",
        "vol_20d",
        "vol_60d",
        "drawdown_60d",
        "dollar_vol_20d_log",
        "empirical_upside_probability_5d",
    ]
    for index, date in enumerate(dates, start=1):
        frame = pd.DataFrame(index=tickers)
        for name in base_names:
            frame[name] = features[name].loc[date].reindex(tickers)
        frame = frame.join(graph_features_for_date(date, features, tickers, graph_lookback, neighbors))
        frame["date"] = date
        frame["ticker"] = frame.index
        rows.append(frame.reset_index(drop=True))
        if index == 1 or index % 15 == 0 or index == len(dates):
            print(f"Prepared graph frame {index}/{len(dates)}: {date.date()}", flush=True)
    return pd.concat(rows, ignore_index=True)


def add_latent_and_motion_features(frame: pd.DataFrame, seed: int) -> tuple[pd.DataFrame, dict]:
    latent_inputs = [
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
    ]
    standardized = []
    for column in latent_inputs:
        transformed = frame.groupby("date", group_keys=False)[column].transform(robust_z)
        standardized.append(transformed.fillna(0.0).to_numpy())
    matrix = np.column_stack(standardized)
    pca = PCA(n_components=3, random_state=seed)
    coordinates = pca.fit_transform(matrix)
    frame = frame.copy()
    frame[["latent_x", "latent_y", "latent_z"]] = coordinates
    cluster_count = min(6, max(3, len(frame["ticker"].unique()) // 20))
    frame["state_cluster"] = KMeans(n_clusters=cluster_count, n_init=20, random_state=seed).fit_predict(coordinates)
    frame = frame.sort_values(["ticker", "date"]).reset_index(drop=True)
    grouped = frame.groupby("ticker", group_keys=False)
    for axis in ["latent_x", "latent_y", "latent_z"]:
        frame[f"delta_{axis}"] = grouped[axis].diff()
    delta_columns = ["delta_latent_x", "delta_latent_y", "delta_latent_z"]
    frame["latent_velocity"] = np.sqrt(sum(frame[column].pow(2) for column in delta_columns))
    frame["latent_acceleration"] = grouped["latent_velocity"].diff()
    previous_delta = grouped[delta_columns].shift(1)
    current_delta = frame[delta_columns]
    dot = (current_delta.to_numpy() * previous_delta.to_numpy()).sum(axis=1)
    current_norm = np.linalg.norm(current_delta.to_numpy(), axis=1)
    previous_norm = np.linalg.norm(previous_delta.to_numpy(), axis=1)
    denominator = current_norm * previous_norm
    cosine = np.divide(dot, denominator, out=np.ones_like(dot), where=denominator > 1e-12)
    frame["latent_path_curvature"] = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
    frame["latent_radius"] = np.linalg.norm(frame[["latent_x", "latent_y", "latent_z"]].to_numpy(), axis=1)
    frame["latent_radial_expansion"] = grouped["latent_radius"].diff()
    absolute_neighbor_gap = frame["neighbor_divergence"].abs()
    frame["neighbor_convergence_velocity"] = -absolute_neighbor_gap.groupby(frame["ticker"]).diff()
    previous_cluster = grouped["state_cluster"].shift(1)
    switched = ((frame["state_cluster"] != previous_cluster) & previous_cluster.notna()).astype(float)
    frame["graph_cluster_switch_count_20d"] = switched.groupby(frame["ticker"]).transform(
        lambda values: values.rolling(20, min_periods=1).sum()
    )

    def residence(values: pd.Series) -> pd.Series:
        output = []
        prior = None
        run = 0
        for value in values:
            run = run + 1 if value == prior else 1
            output.append(run)
            prior = value
        return pd.Series(output, index=values.index, dtype=float)

    frame["graph_regime_residence_days"] = grouped["state_cluster"].transform(residence)
    frame["crowding_change_5d"] = grouped["graph_degree"].diff(5)
    frame["prediction_confidence_proxy"] = (frame["empirical_upside_probability_5d"] - 0.5).abs() * 2.0
    frame = frame.sort_values(["date", "ticker"]).reset_index(drop=True)
    metadata = {
        "inputs": latent_inputs,
        "explained_variance_ratio": [round(float(value), 6) for value in pca.explained_variance_ratio_],
        "cluster_count": cluster_count,
        "note": "PCA and clustering use only pre-holdout animation rows; coordinates are descriptive, not causal.",
    }
    return frame, metadata


def conditional_monte_carlo(
    ticker: str,
    daily_return: pd.Series,
    market_trend: pd.Series,
    paths: int,
    horizon: int,
    block: int,
    seed: int,
) -> dict:
    series = daily_return.dropna().tail(504).clip(-0.20, 0.20)
    if len(series) < 100:
        raise RuntimeError(f"Insufficient Monte Carlo history for {ticker}")
    values = series.to_numpy(dtype=float)
    rolling_vol = series.rolling(20, min_periods=15).std().to_numpy()
    current_vol = float(np.nanmedian(rolling_vol[-20:]))
    aligned_market = market_trend.reindex(series.index).fillna(0.0).to_numpy(dtype=float)
    current_regime = 1 if float(aligned_market[-1]) >= 0 else -1
    candidates = []
    for position in range(20, len(values) - block):
        vol = rolling_vol[position]
        regime = 1 if aligned_market[position] >= 0 else -1
        if np.isfinite(vol) and current_vol > 0 and 0.65 <= vol / current_vol <= 1.35 and regime == current_regime:
            candidates.append(position)
    if len(candidates) < 15:
        candidates = list(range(max(20, len(values) - 252), len(values) - block))
    rng = np.random.default_rng(seed + zlib.crc32(ticker.encode("utf-8")))
    simulated = np.empty((paths, horizon), dtype=float)
    blocks = math.ceil(horizon / block)
    offset = 0
    for _ in range(blocks):
        starts = rng.choice(candidates, size=paths, replace=True)
        width = min(block, horizon - offset)
        for step in range(width):
            simulated[:, offset + step] = values[starts + step]
        offset += width
    cumulative = np.expm1(np.cumsum(simulated, axis=1))
    quantiles = np.quantile(cumulative, [0.05, 0.25, 0.50, 0.75, 0.95], axis=0)
    final = cumulative[:, -1]
    target = cumulative >= 0.05
    stop = cumulative <= -0.05
    target_first = np.where(target.any(axis=1), target.argmax(axis=1), horizon + 1)
    stop_first = np.where(stop.any(axis=1), stop.argmax(axis=1), horizon + 1)
    threshold = np.quantile(final, 0.05)
    expected_shortfall = float(final[final <= threshold].mean())
    return {
        "ticker": ticker,
        "days": list(range(1, horizon + 1)),
        "q05": np.round(quantiles[0], 6).tolist(),
        "q25": np.round(quantiles[1], 6).tolist(),
        "q50": np.round(quantiles[2], 6).tolist(),
        "q75": np.round(quantiles[3], 6).tolist(),
        "q95": np.round(quantiles[4], 6).tolist(),
        "summary": {
            "expected_return": round(float(final.mean()), 6),
            "upside_probability": round(float((final > 0).mean()), 6),
            "target_before_stop_probability": round(float((target_first < stop_first).mean()), 6),
            "stop_before_target_probability": round(float((stop_first < target_first).mean()), 6),
            "expected_shortfall_5pct": round(expected_shortfall, 6),
            "conditioned_block_count": len(candidates),
            "current_regime": "positive" if current_regime > 0 else "negative",
        },
    }


def build_monte_carlo_payload(
    tickers: list[str],
    daily_return: pd.DataFrame,
    paths: int,
    horizon: int,
    block: int,
    seed: int,
) -> dict[str, dict]:
    market_trend = daily_return.mean(axis=1).rolling(20, min_periods=10).sum()
    output = {}
    for index, ticker in enumerate(tickers, start=1):
        output[ticker] = conditional_monte_carlo(
            ticker, daily_return[ticker], market_trend, paths, horizon, block, seed
        )
        if index == 1 or index % 20 == 0 or index == len(tickers):
            print(f"Prepared Monte Carlo tube {index}/{len(tickers)}: {ticker}", flush=True)
    return output


VARIABLE_LABELS = {
    "ret_5d": "5-day return",
    "ret_20d": "20-day momentum",
    "ret_60d": "60-day return",
    "vol_20d": "20-day annualized volatility",
    "vol_60d": "60-day annualized volatility",
    "drawdown_60d": "60-day drawdown",
    "dollar_vol_20d_log": "Log median dollar volume",
    "graph_degree": "Graph degree",
    "graph_similarity_mean": "Mean neighbor similarity",
    "neighbor_ret_20d": "Neighbor 20-day return",
    "neighbor_divergence": "Neighbor divergence",
    "empirical_upside_probability_5d": "Empirical 5-day upside probability",
    "prediction_confidence_proxy": "Empirical confidence proxy",
    "latent_x": "Latent coordinate X",
    "latent_y": "Latent coordinate Y",
    "latent_z": "Latent coordinate Z",
    "latent_velocity": "Latent velocity",
    "latent_acceleration": "Latent acceleration",
    "latent_path_curvature": "Latent path curvature",
    "latent_radial_expansion": "Latent radial expansion",
    "neighbor_convergence_velocity": "Neighbor convergence velocity",
    "graph_cluster_switch_count_20d": "Cluster switches over 20 dates",
    "graph_regime_residence_days": "Cluster residence dates",
    "crowding_change_5d": "Five-date crowding change",
}


def safe_values(series: pd.Series) -> list:
    output = []
    for value in series:
        if pd.isna(value) or not np.isfinite(float(value)):
            output.append(None)
        else:
            output.append(round(float(value), 6))
    return output


def columnar_frames(frame: pd.DataFrame) -> list[dict]:
    variables = list(VARIABLE_LABELS)
    frames = []
    for date, dated in frame.groupby("date", sort=True):
        frames.append(
            {
                "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                "tickers": dated["ticker"].tolist(),
                "clusters": dated["state_cluster"].astype(int).tolist(),
                "values": {name: safe_values(dated[name]) for name in variables},
            }
        )
    return frames


HTML_TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Temporal Stock Universe Lab</title>
  <style>
    :root{--ink:#102f31;--teal:#0d4a4a;--deep:#082f32;--cream:#f4eddf;--paper:#fffaf0;--copper:#d6753f;--gold:#e9b85a;--mint:#8ec5b0;--muted:#6c7c78;--line:rgba(16,47,49,.16);--shadow:0 24px 70px rgba(8,47,50,.15)}
    *{box-sizing:border-box} body{margin:0;color:var(--ink);font-family:"Avenir Next","Trebuchet MS",sans-serif;background:radial-gradient(circle at 8% 4%,rgba(233,184,90,.3),transparent 28%),radial-gradient(circle at 92% 16%,rgba(142,197,176,.28),transparent 30%),linear-gradient(145deg,#efe5d2,#f8f3e8 55%,#e6efe8);min-height:100vh}
    body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.25;background-image:linear-gradient(rgba(13,74,74,.08) 1px,transparent 1px),linear-gradient(90deg,rgba(13,74,74,.08) 1px,transparent 1px);background-size:34px 34px;mask-image:linear-gradient(to bottom,#000,transparent 75%)}
    .shell{width:min(1580px,calc(100% - 32px));margin:24px auto 50px;position:relative}.hero{display:grid;grid-template-columns:1.5fr .7fr;gap:28px;align-items:end;padding:34px 38px;background:linear-gradient(125deg,rgba(8,47,50,.97),rgba(13,74,74,.94));border-radius:28px;color:var(--cream);box-shadow:var(--shadow);overflow:hidden;position:relative}.hero:after{content:"";position:absolute;width:460px;height:460px;border:1px solid rgba(233,184,90,.35);border-radius:50%;right:-170px;top:-290px;box-shadow:0 0 0 48px rgba(233,184,90,.05),0 0 0 96px rgba(233,184,90,.035)}
    .eyebrow{font:700 11px/1 "IBM Plex Mono","Courier New",monospace;letter-spacing:.2em;text-transform:uppercase;color:var(--gold)}h1{font-family:"Palatino Linotype",Palatino,serif;font-size:clamp(38px,5vw,72px);line-height:.94;letter-spacing:-.04em;margin:13px 0 17px;max-width:900px}.lede{font-size:16px;line-height:1.65;max-width:850px;color:#d9e5df;margin:0}.seal{justify-self:end;text-align:right;position:relative;z-index:2}.seal strong{display:block;font:700 31px/1 "IBM Plex Mono","Courier New",monospace;color:var(--gold)}.seal span{display:block;margin-top:8px;color:#d5e5df;font-size:13px}.badge{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border:1px solid rgba(255,255,255,.18);border-radius:999px;background:rgba(255,255,255,.07);font-size:12px;margin-bottom:14px}.badge:before{content:"";width:8px;height:8px;border-radius:50%;background:var(--mint);box-shadow:0 0 0 5px rgba(142,197,176,.12)}
    .control-deck{margin-top:18px;background:rgba(255,250,240,.88);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,.7);border-radius:22px;padding:18px;box-shadow:var(--shadow)}.controls{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:12px}.field label{display:block;font:700 10px/1.2 "IBM Plex Mono","Courier New",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 7px}.field select,.field input[type=text]{width:100%;height:41px;border:1px solid var(--line);background:#fffdf8;border-radius:11px;padding:0 11px;color:var(--ink);font:600 13px/1 "Avenir Next","Trebuchet MS",sans-serif;outline:none}.field select:focus,.field input:focus{border-color:var(--copper);box-shadow:0 0 0 3px rgba(214,117,63,.12)}
    .timeline{display:grid;grid-template-columns:auto 1fr auto auto;gap:12px;align-items:center;margin-top:16px;padding-top:16px;border-top:1px solid var(--line)}button{border:0;border-radius:11px;padding:11px 16px;background:var(--deep);color:#fff;font-weight:800;cursor:pointer}button:hover{background:var(--copper)}input[type=range]{accent-color:var(--copper);width:100%}.date-readout{font:800 13px/1 "IBM Plex Mono","Courier New",monospace;min-width:92px}.check{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--muted)}
    .main-grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(360px,.75fr);gap:18px;margin-top:18px}.panel{background:rgba(255,250,240,.94);border:1px solid rgba(255,255,255,.8);border-radius:24px;box-shadow:var(--shadow);overflow:hidden}.panel-head{display:flex;justify-content:space-between;gap:18px;align-items:start;padding:20px 22px 0}.panel-head h2{font-family:"Palatino Linotype",Palatino,serif;font-size:24px;margin:0}.panel-head p{font-size:12px;line-height:1.45;color:var(--muted);margin:4px 0 0;max-width:580px}.micro{font:700 10px/1.3 "IBM Plex Mono","Courier New",monospace;color:var(--copper);text-transform:uppercase;letter-spacing:.08em}.plot{height:690px}.side-plot{height:450px}.metrics{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;padding:0 18px 18px}.metric{padding:15px;border-radius:15px;background:#f2eadb;border:1px solid rgba(16,47,49,.08)}.metric span{display:block;font:700 9px/1.2 "IBM Plex Mono","Courier New",monospace;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}.metric strong{display:block;margin-top:7px;font-size:20px;color:var(--teal)}
    .explain-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}.response{height:390px}.copy{padding:5px 22px 24px;color:var(--muted);font-size:13px;line-height:1.6}.callout{margin:18px 22px 22px;padding:16px;border-left:4px solid var(--copper);background:#f1e4ce;border-radius:0 12px 12px 0;font-size:13px;line-height:1.55}.feature-list{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;padding:18px 22px 25px}.feature-pill{padding:10px 12px;border:1px solid var(--line);border-radius:12px;background:#fffdf8;font:700 11px/1.35 "IBM Plex Mono","Courier New",monospace;color:var(--teal)}.footer{display:flex;justify-content:space-between;gap:20px;color:var(--muted);font-size:11px;padding:22px 8px}.footer a{color:var(--copper);font-weight:800}
    @media(max-width:1100px){.controls{grid-template-columns:repeat(3,1fr)}.main-grid,.explain-grid{grid-template-columns:1fr}.plot{height:580px}.side-plot{height:500px}.hero{grid-template-columns:1fr}.seal{justify-self:start;text-align:left}.feature-list{grid-template-columns:repeat(2,1fr)}}
    @media(max-width:650px){.shell{width:min(100% - 16px,1580px);margin-top:8px}.hero{padding:27px 22px;border-radius:20px}.controls{grid-template-columns:1fr 1fr}.timeline{grid-template-columns:auto 1fr}.date-readout,.check{grid-column:span 1}.plot{height:480px}.side-plot{height:420px}.feature-list{grid-template-columns:1fr}.panel-head{padding:18px 17px 0}.control-deck{padding:13px}.footer{display:block}.footer span{display:block;margin-top:8px}}
  </style>
  <script>__PLOTLY_JS__</script>
</head>
<body>
<main class="shell">
  <section class="hero">
    <div><div class="eyebrow">Research desk / temporal geometry</div><h1>The market is a moving shape.</h1><p class="lede">Explore momentum, risk, liquidity, graph structure, and latent motion through pre-holdout history. Click a stock to open its regime-conditioned Monte Carlo probability tube.</p></div>
    <div class="seal"><div class="badge">Paper-only research</div><strong>60 dates</strong><span>final holdout remains sealed</span></div>
  </section>

  <section class="control-deck">
    <div class="controls">
      <div class="field"><label>X coordinate</label><select id="xSelect"></select></div>
      <div class="field"><label>Y coordinate</label><select id="ySelect"></select></div>
      <div class="field"><label>Z coordinate</label><select id="zSelect"></select></div>
      <div class="field"><label>Bubble size</label><select id="sizeSelect"></select></div>
      <div class="field"><label>Bubble color</label><select id="colorSelect"></select></div>
      <div class="field"><label>Find ticker</label><input id="tickerSearch" type="text" list="tickerList" placeholder="Type ticker"><datalist id="tickerList"></datalist></div>
    </div>
    <div class="timeline"><button id="playButton">Play history</button><input id="dateSlider" type="range" min="0" step="1"><span id="dateReadout" class="date-readout"></span><label class="check"><input id="trailToggle" type="checkbox" checked> selected-stock trail</label></div>
  </section>

  <section class="main-grid">
    <article class="panel"><header class="panel-head"><div><div class="micro">Animated universe</div><h2>Variable geometry</h2><p>Coordinates are selectable. Spatial proximity is descriptive and never treated as causal evidence.</p></div><div id="universeCount" class="micro"></div></header><div id="universePlot" class="plot"></div></article>
    <aside class="panel"><header class="panel-head"><div><div class="micro">Conditional path distribution</div><h2 id="mcTitle">Monte Carlo tube</h2><p>Moving blocks preserve short-run dependence and are conditioned on volatility and broad-market regime.</p></div></header><div id="mcPlot" class="side-plot"></div><div class="metrics"><div class="metric"><span>Expected return</span><strong id="mExpected">-</strong></div><div class="metric"><span>Upside probability</span><strong id="mUpside">-</strong></div><div class="metric"><span>Target before stop</span><strong id="mTarget">-</strong></div><div class="metric"><span>Expected shortfall</span><strong id="mShortfall">-</strong></div></div></aside>
  </section>

  <section class="explain-grid">
    <article class="panel"><header class="panel-head"><div><div class="micro">Mechanism explorer</div><h2>Activation response</h2></div><div class="field"><label>Activation</label><select id="activationSelect"><option>relu</option><option>tanh</option><option>leaky_relu</option><option>gelu</option></select></div></header><div id="activationPlot" class="response"></div><p class="copy">Activations shape how the ANN combines momentum, graph, and motion variables. This panel shows their geometry, not performance. Performance requires the approved chronological ablation.</p></article>
    <article class="panel"><header class="panel-head"><div><div class="micro">Probability objective</div><h2>Loss response</h2></div><div class="field"><label>Loss</label><select id="lossSelect"><option value="bce">binary_cross_entropy</option><option value="brier">brier_mse</option><option value="focal1">focal_gamma_1</option><option value="focal2">focal_gamma_2</option></select></div></header><div id="lossPlot" class="response"></div><p class="copy">Loss functions train the probability model; Monte Carlo then translates historical return dependence into a distribution of paths. Simulated paths are never treated as independent training rows.</p></article>
  </section>

  <section class="panel" style="margin-top:18px"><header class="panel-head"><div><div class="micro">Candidate feature factory</div><h2>Motion variables created by the map</h2><p>Each feature must improve ranking, calibration, or after-cost return across chronological splits before promotion.</p></div></header><div class="feature-list" id="featureList"></div><div class="callout"><strong>Research guardrail:</strong> a persuasive animation is a hypothesis generator, not evidence. The final holdout remains sealed until the visual variables and model family are frozen.</div></section>
  <footer class="footer"><span>Generated __GENERATED_AT__ from pre-holdout ResearchPrices.</span><span><a href="motion_features.csv" download>Download motion features</a> / No brokerage actions / No investment advice</span></footer>
</main>
<script>
const DATA=__PAYLOAD__;
const labels=DATA.variable_labels;
const variables=Object.keys(labels);
let frameIndex=DATA.frames.length-1;
let selectedTicker=DATA.meta.default_ticker;
let timer=null;
const byId=id=>document.getElementById(id);
const formatPercent=v=>v==null?"-":`${(100*v).toFixed(2)}%`;
const selectDefaults={xSelect:"ret_20d",ySelect:"graph_degree",zSelect:"vol_60d",sizeSelect:"empirical_upside_probability_5d",colorSelect:"neighbor_divergence"};
for(const id of Object.keys(selectDefaults)){const element=byId(id);for(const key of variables){const option=document.createElement("option");option.value=key;option.textContent=labels[key];element.appendChild(option)}element.value=selectDefaults[id];element.addEventListener("change",drawUniverse)}
const tickers=DATA.frames[DATA.frames.length-1].tickers;for(const ticker of tickers){const option=document.createElement("option");option.value=ticker;byId("tickerList").appendChild(option)}
byId("dateSlider").max=DATA.frames.length-1;byId("dateSlider").value=frameIndex;byId("dateSlider").addEventListener("input",event=>{frameIndex=Number(event.target.value);drawUniverse()});
byId("trailToggle").addEventListener("change",drawUniverse);
byId("tickerSearch").addEventListener("change",event=>{const value=event.target.value.toUpperCase();if(tickers.includes(value)){selectedTicker=value;drawUniverse();drawMonteCarlo()}});
function finite(values){return values.filter(value=>Number.isFinite(value)).sort((a,b)=>a-b)}
function quantile(sorted,q){if(!sorted.length)return 0;const p=(sorted.length-1)*q;const b=Math.floor(p),r=p-b;return sorted[b+1]===undefined?sorted[b]:sorted[b]+r*(sorted[b+1]-sorted[b])}
function sizes(values){const clean=finite(values),lo=quantile(clean,.05),hi=quantile(clean,.95),span=Math.max(hi-lo,1e-9);return values.map(value=>Number.isFinite(value)?7+17*Math.max(0,Math.min(1,(value-lo)/span)):6)}
function axisTitle(key){return labels[key]||key}
function hoverText(frame,index){const value=key=>frame.values[key][index];return `<b>${frame.tickers[index]}</b><br>${frame.date}<br>20d return: ${formatPercent(value("ret_20d"))}<br>60d vol: ${formatPercent(value("vol_60d"))}<br>Graph degree: ${value("graph_degree")?.toFixed(0)??"-"}<br>Latent speed: ${value("latent_velocity")?.toFixed(3)??"-"}`}
function drawUniverse(){const frame=DATA.frames[frameIndex],xKey=byId("xSelect").value,yKey=byId("ySelect").value,zKey=byId("zSelect").value,sizeKey=byId("sizeSelect").value,colorKey=byId("colorSelect").value;const traces=[{type:"scatter3d",mode:"markers",x:frame.values[xKey],y:frame.values[yKey],z:frame.values[zKey],customdata:frame.tickers,text:frame.tickers.map((_,i)=>hoverText(frame,i)),hovertemplate:"%{text}<extra></extra>",marker:{size:sizes(frame.values[sizeKey]),color:frame.values[colorKey],colorscale:[[0,"#c95c3f"],[.48,"#e9b85a"],[.52,"#dce8d7"],[1,"#0d736c"]],opacity:.82,colorbar:{title:axisTitle(colorKey),thickness:10,len:.55,x:1.02},line:{color:"rgba(255,255,255,.55)",width:.5}}}];
if(selectedTicker&&byId("trailToggle").checked){const trail={x:[],y:[],z:[],text:[]};for(let i=Math.max(0,frameIndex-20);i<=frameIndex;i++){const past=DATA.frames[i],position=past.tickers.indexOf(selectedTicker);if(position>=0){trail.x.push(past.values[xKey][position]);trail.y.push(past.values[yKey][position]);trail.z.push(past.values[zKey][position]);trail.text.push(past.date)}}traces.push({type:"scatter3d",mode:"lines+markers",x:trail.x,y:trail.y,z:trail.z,text:trail.text,hovertemplate:`<b>${selectedTicker}</b><br>%{text}<extra></extra>`,line:{color:"#fff4d2",width:7},marker:{color:"#d6753f",size:4}})}
const layout={margin:{l:0,r:18,t:10,b:0},paper_bgcolor:"rgba(0,0,0,0)",plot_bgcolor:"rgba(0,0,0,0)",showlegend:false,scene:{bgcolor:"#082f32",xaxis:{title:axisTitle(xKey),gridcolor:"rgba(255,255,255,.12)",color:"#dce8df"},yaxis:{title:axisTitle(yKey),gridcolor:"rgba(255,255,255,.12)",color:"#dce8df"},zaxis:{title:axisTitle(zKey),gridcolor:"rgba(255,255,255,.12)",color:"#dce8df"},camera:{eye:{x:1.38,y:1.35,z:.9}}}};Plotly.react("universePlot",traces,layout,{responsive:true,displaylogo:false});byId("dateReadout").textContent=frame.date;byId("universeCount").textContent=`${frame.tickers.length} stocks / frame ${frameIndex+1}`;const plot=byId("universePlot");if(!plot.dataset.bound){plot.on("plotly_click",event=>{const ticker=event.points[0]?.customdata;if(typeof ticker==="string"){selectedTicker=ticker;byId("tickerSearch").value=ticker;drawUniverse();drawMonteCarlo()}});plot.dataset.bound="1"}}
function drawMonteCarlo(){const tube=DATA.monte_carlo[selectedTicker]||DATA.monte_carlo[DATA.meta.default_ticker],levels=[5,25,50,75,95],curves=[tube.q05,tube.q25,tube.q50,tube.q75,tube.q95],x=levels.map(()=>tube.days),y=levels.map(level=>tube.days.map(()=>level));const traces=[{type:"surface",x:x,y:y,z:curves,colorscale:[[0,"#c95c3f"],[.5,"#e9b85a"],[1,"#0d736c"]],opacity:.9,showscale:false,hovertemplate:"Day %{x}<br>Percentile %{y}<br>Return %{z:.2%}<extra></extra>"},{type:"scatter3d",mode:"lines",x:tube.days,y:tube.days.map(()=>50),z:tube.q50,line:{color:"#fff4d2",width:8},hovertemplate:"Median day %{x}<br>%{z:.2%}<extra></extra>"}];const layout={margin:{l:0,r:0,t:4,b:0},paper_bgcolor:"rgba(0,0,0,0)",showlegend:false,scene:{bgcolor:"#102f31",xaxis:{title:"Forecast day",color:"#dce8df",gridcolor:"rgba(255,255,255,.1)"},yaxis:{title:"Path percentile",color:"#dce8df",gridcolor:"rgba(255,255,255,.1)"},zaxis:{title:"Cumulative return",tickformat:".0%",color:"#dce8df",gridcolor:"rgba(255,255,255,.1)"},camera:{eye:{x:1.35,y:1.25,z:.85}}}};Plotly.react("mcPlot",traces,layout,{responsive:true,displaylogo:false});byId("mcTitle").textContent=`${tube.ticker} probability tube`;byId("mExpected").textContent=formatPercent(tube.summary.expected_return);byId("mUpside").textContent=formatPercent(tube.summary.upside_probability);byId("mTarget").textContent=formatPercent(tube.summary.target_before_stop_probability);byId("mShortfall").textContent=formatPercent(tube.summary.expected_shortfall_5pct)}
function activation(name,x){if(name==="relu")return Math.max(0,x);if(name==="tanh")return Math.tanh(x);if(name==="leaky_relu")return x>=0?x:.1*x;return .5*x*(1+Math.tanh(Math.sqrt(2/Math.PI)*(x+.044715*x*x*x)))}
function drawActivation(){const name=byId("activationSelect").value,x=Array.from({length:181},(_,i)=>-4.5+i*.05),y=x.map(value=>activation(name,value));Plotly.react("activationPlot",[{x,y,type:"scatter",mode:"lines",line:{color:"#d6753f",width:4},fill:"tozeroy",fillcolor:"rgba(214,117,63,.12)"}],{margin:{l:52,r:20,t:20,b:48},paper_bgcolor:"rgba(0,0,0,0)",plot_bgcolor:"rgba(255,255,255,.4)",xaxis:{title:"pre-activation signal",zerolinecolor:"#102f31",gridcolor:"rgba(16,47,49,.1)"},yaxis:{title:name,zerolinecolor:"#102f31",gridcolor:"rgba(16,47,49,.1)"}},{responsive:true,displayModeBar:false})}
function lossValue(name,p,y){p=Math.max(1e-5,Math.min(1-1e-5,p));if(name==="brier")return (p-y)*(p-y);const base=-(y*Math.log(p)+(1-y)*Math.log(1-p));if(name==="focal1")return (y?(1-p):p)*base;if(name==="focal2")return Math.pow(y?(1-p):p,2)*base;return base}
function drawLoss(){const name=byId("lossSelect").value,p=Array.from({length:199},(_,i)=>(i+1)/200),positive=p.map(value=>lossValue(name,value,1)),negative=p.map(value=>lossValue(name,value,0));Plotly.react("lossPlot",[{x:p,y:positive,type:"scatter",mode:"lines",name:"Outcome up",line:{color:"#0d736c",width:4}},{x:p,y:negative,type:"scatter",mode:"lines",name:"Outcome down",line:{color:"#c95c3f",width:4}}],{margin:{l:52,r:20,t:20,b:48},paper_bgcolor:"rgba(0,0,0,0)",plot_bgcolor:"rgba(255,255,255,.4)",legend:{orientation:"h",y:1.12},xaxis:{title:"predicted upside probability",gridcolor:"rgba(16,47,49,.1)"},yaxis:{title:"loss",rangemode:"tozero",gridcolor:"rgba(16,47,49,.1)"}},{responsive:true,displayModeBar:false})}
byId("activationSelect").addEventListener("change",drawActivation);byId("lossSelect").addEventListener("change",drawLoss);byId("playButton").addEventListener("click",()=>{if(timer){clearInterval(timer);timer=null;byId("playButton").textContent="Play history";return}byId("playButton").textContent="Pause";if(frameIndex>=DATA.frames.length-1)frameIndex=0;timer=setInterval(()=>{frameIndex=(frameIndex+1)%DATA.frames.length;byId("dateSlider").value=frameIndex;drawUniverse();if(frameIndex===DATA.frames.length-1){clearInterval(timer);timer=null;byId("playButton").textContent="Play history"}},650)});
for(const name of DATA.motion_features){const pill=document.createElement("div");pill.className="feature-pill";pill.textContent=labels[name]||name;byId("featureList").appendChild(pill)}
drawUniverse();drawMonteCarlo();drawActivation();drawLoss();
</script>
</body>
</html>'''


def build_readout(
    output_dir: Path,
    frame: pd.DataFrame,
    mc_payload: dict[str, dict],
    latent_metadata: dict,
    args: argparse.Namespace,
) -> None:
    latest_date = frame["date"].max()
    latest = frame[frame["date"] == latest_date]
    fastest = latest.nlargest(10, "latent_velocity")[["ticker", "latent_velocity", "ret_20d", "graph_degree"]]
    fastest_table = [
        "| ticker | latent_velocity | ret_20d | graph_degree |",
        "|---|---:|---:|---:|",
    ]
    fastest_table.extend(
        f"| {row.ticker} | {row.latent_velocity:.4f} | {row.ret_20d:.4f} | {row.graph_degree:.4f} |"
        for row in fastest.itertuples(index=False)
    )
    mc_rank = sorted(
        ((ticker, values["summary"]["target_before_stop_probability"], values["summary"]["expected_shortfall_5pct"]) for ticker, values in mc_payload.items()),
        key=lambda row: row[1],
        reverse=True,
    )[:10]
    lines = [
        "# Temporal 3D and Monte Carlo Lab",
        "",
        f"- Status: visual lab complete; predictive ablation pending",
        f"- Pre-holdout cutoff: {pd.Timestamp(latest_date).date()}",
        f"- Stocks: {frame['ticker'].nunique()}",
        f"- Animation frames: {frame['date'].nunique()}",
        f"- Monte Carlo paths per stock: {args.mc_paths}",
        f"- PCA explained variance: {latent_metadata['explained_variance_ratio']}",
        "- Final 60-date holdout: sealed",
        "",
        "## Fastest latent movers on the final visual frame",
        "",
        "\n".join(fastest_table),
        "",
        "## Highest target-before-stop Monte Carlo probabilities",
        "",
        "| ticker | target_before_stop_probability | expected_shortfall_5pct |",
        "|---|---:|---:|",
    ]
    lines.extend(f"| {ticker} | {probability:.4f} | {shortfall:.4f} |" for ticker, probability, shortfall in mc_rank)
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            "- The 3D map creates hypotheses; it does not establish causal relationships.",
            "- Monte Carlo paths are conditional historical resamples, not independent training observations.",
            "- Loss and activation curves are an interactive mechanism explorer, not ablation results.",
            "- Motion features require chronological ablation before context-gate promotion.",
            "- No final holdout, brokerage, or live-trading behavior was opened or changed.",
        ]
    )
    (output_dir / "scenario_and_calibration_readout.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    gate = assert_context_approval(Path(args.context_gate))
    prices = load_prices(Path(args.db), args.history_start, args.sealed_holdout_start)
    tickers = select_universe(prices, args.stocks)
    close, volume = price_matrices(prices, tickers)
    features = calculate_base_features(close, volume)
    animation_dates = choose_animation_dates(close, args.frames, args.graph_lookback)
    frame_rows = build_visual_frame_rows(
        animation_dates, tickers, features, args.graph_lookback, args.neighbors
    )
    frame_rows, latent_metadata = add_latent_and_motion_features(frame_rows, args.seed)
    monte_carlo = build_monte_carlo_payload(
        tickers,
        features["daily_log_return"],
        args.mc_paths,
        args.mc_horizon,
        args.mc_block,
        args.seed,
    )
    latest_date = frame_rows["date"].max()
    latest_rows = frame_rows[frame_rows["date"] == latest_date]
    default_ticker = latest_rows.sort_values("dollar_vol_20d_log", ascending=False)["ticker"].iloc[0]
    motion_features = [
        "latent_velocity",
        "latent_acceleration",
        "latent_path_curvature",
        "latent_radial_expansion",
        "neighbor_convergence_velocity",
        "graph_cluster_switch_count_20d",
        "graph_regime_residence_days",
        "crowding_change_5d",
    ]
    payload = {
        "meta": {
            "experiment_id": EXPERIMENT_ID,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "history_start": args.history_start,
            "pre_holdout_cutoff": pd.Timestamp(latest_date).strftime("%Y-%m-%d"),
            "sealed_holdout_start": args.sealed_holdout_start,
            "holdout_opened": False,
            "stock_count": len(tickers),
            "frame_count": len(animation_dates),
            "default_ticker": default_ticker,
            "monte_carlo_paths_per_stock": args.mc_paths,
            "paper_only": True,
        },
        "variable_labels": VARIABLE_LABELS,
        "motion_features": motion_features,
        "latent_model": latent_metadata,
        "frames": columnar_frames(frame_rows),
        "monte_carlo": monte_carlo,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (
        HTML_TEMPLATE.replace("__PLOTLY_JS__", get_plotlyjs())
        .replace("__PAYLOAD__", payload_json)
        .replace("__GENERATED_AT__", generated_at)
    )
    (output_dir / "interactive_market_universe_3d.html").write_text(html, encoding="utf-8")
    (output_dir / "website_payload.json").write_text(payload_json, encoding="utf-8")
    (output_dir / "monte_carlo_probability_tubes.json").write_text(
        json.dumps(monte_carlo, indent=2, allow_nan=False), encoding="utf-8"
    )
    frame_rows.to_csv(output_dir / "motion_features.csv", index=False)
    feature_dictionary = {
        "experiment_id": EXPERIMENT_ID,
        "variables": VARIABLE_LABELS,
        "motion_features": motion_features,
        "acceptance_rule": gate.get("next_experiments", [{}])[-1].get("design_signature"),
        "holdout_status": "sealed",
    }
    (output_dir / "visual_feature_dictionary.json").write_text(
        json.dumps(feature_dictionary, indent=2), encoding="utf-8"
    )
    build_readout(output_dir, frame_rows, monte_carlo, latent_metadata, args)
    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "status": "visual_lab_completed_ablation_pending",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {"database": str(args.db), "context_gate": str(args.context_gate)},
        "outputs": sorted(path.name for path in output_dir.iterdir() if path.is_file()),
        "rows": int(len(frame_rows)),
        "stocks": int(frame_rows["ticker"].nunique()),
        "frames": int(frame_rows["date"].nunique()),
        "holdout_opened": False,
        "loss_activation_status": "interactive_response_explorer_built_ablation_pending",
    }
    (output_dir / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Outputs written to {output_dir}", flush=True)
    print(f"Interactive lab: {output_dir / 'interactive_market_universe_3d.html'}", flush=True)
    print("Final holdout remains sealed.", flush=True)


if __name__ == "__main__":
    main()
