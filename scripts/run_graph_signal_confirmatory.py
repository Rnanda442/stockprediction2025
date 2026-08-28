#!/usr/bin/env python3
"""Leakage-aware confirmation of the surviving temporal graph signals."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


EXPERIMENT_ID = "graph_signal_confirmatory_v1"
DESIGN_SIGNATURE = (
    "graph-signal-confirmatory-v1:h5:history400:crowding+regime-residence+"
    "cluster-switch:elastic+ridge+tanh:6walkforward:date-block-bootstrap:"
    "label-placebo:holdout60"
)

BASE_FEATURES = [
    "ret_5d", "ret_20d", "ret_60d", "vol_20d", "vol_60d",
    "drawdown_60d", "dollar_vol_20d_log", "graph_degree",
]
FEATURE_SETS = {
    "base_only": BASE_FEATURES,
    "base_plus_crowding": BASE_FEATURES + ["crowding_change_5d"],
    "base_plus_regime": BASE_FEATURES + ["graph_regime_residence_days"],
    "graph_survivors": BASE_FEATURES + [
        "crowding_change_5d",
        "graph_regime_residence_days",
        "graph_cluster_switch_count_20d",
    ],
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--motion-features", required=True)
    p.add_argument("--context-gate", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--sealed-holdout-start", default="2026-05-29")
    p.add_argument("--splits", type=int, default=6)
    p.add_argument("--test-dates", type=int, default=20)
    p.add_argument("--embargo-dates", type=int, default=5)
    p.add_argument("--min-train-dates", type=int, default=120)
    p.add_argument("--ann-seeds", default="442,2025,9001")
    p.add_argument("--placebo-seeds", default="117,442,8080,9001,2025")
    p.add_argument("--bootstrap-reps", type=int, default=4000)
    p.add_argument("--block-dates", type=int, default=5)
    p.add_argument("--portfolio-size", type=int, default=10)
    p.add_argument("--rebalance-dates", type=int, default=5)
    p.add_argument("--cost-bps", type=float, default=10.0)
    return p.parse_args()


def validate_gate(path: Path) -> dict:
    gate = json.loads(path.read_text())
    holdout = gate["guardrails"]["sealed_holdout"]
    if holdout["status"] != "sealed" or holdout["opened_for_evaluation"]:
        raise RuntimeError("The final holdout is not sealed.")
    matches = [
        x for x in gate["next_experiments"]
        if x.get("experiment_id", x.get("id")) == EXPERIMENT_ID
    ]
    if len(matches) != 1 or matches[0].get("design_signature") != DESIGN_SIGNATURE:
        raise RuntimeError("Experiment ID or design signature is not approved in context gate.")
    if matches[0].get("status") != "approved_next":
        raise RuntimeError("Experiment is not approved for compute.")
    return gate


def normalize_motion(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    date_col = next((c for c in ["date", "source_date", "begins_at"] if c in df), None)
    ticker_col = next((c for c in ["ticker", "symbol"] if c in df), None)
    if not date_col or not ticker_col:
        raise ValueError("Motion file must contain date and ticker columns.")
    df = df.rename(columns={date_col: "date", ticker_col: "ticker"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["ticker"] = df["ticker"].astype(str)
    required = sorted(set(sum(FEATURE_SETS.values(), [])))
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Motion file is missing required columns: {missing}")
    return df[["date", "ticker"] + required].drop_duplicates(["date", "ticker"])


def load_labels(db_path: Path, holdout_start: pd.Timestamp) -> pd.DataFrame:
    with sqlite3.connect(db_path) as con:
        prices = pd.read_sql_query(
            "SELECT ticker, begins_at, close_price FROM ResearchPrices "
            "WHERE begins_at < ? ORDER BY ticker, begins_at",
            con,
            params=[holdout_start.strftime("%Y-%m-%d")],
        )
    prices["date"] = pd.to_datetime(prices["begins_at"]).dt.normalize()
    prices["close_price"] = pd.to_numeric(prices["close_price"], errors="coerce")
    prices = prices.dropna(subset=["ticker", "date", "close_price"]).sort_values(["ticker", "date"])
    grouped = prices.groupby("ticker", sort=False)
    prices["label_date"] = grouped["date"].shift(-5)
    prices["future_return_5d"] = grouped["close_price"].shift(-5) / prices["close_price"] - 1.0
    prices = prices[
        prices["label_date"].notna()
        & (prices["label_date"] < holdout_start)
        & np.isfinite(prices["future_return_5d"])
    ].copy()
    prices["target"] = (prices["future_return_5d"] > 0).astype(int)
    return prices[["date", "ticker", "label_date", "future_return_5d", "target"]]


def make_splits(dates: list[pd.Timestamp], args: argparse.Namespace) -> list[dict]:
    available = len(dates) - args.min_train_dates - args.embargo_dates
    test_size = min(args.test_dates, available // args.splits)
    if test_size < 5:
        raise RuntimeError(f"Only {len(dates)} dates are available for the requested confirmation.")
    first_test = len(dates) - args.splits * test_size
    splits = []
    for i in range(args.splits):
        test_start = first_test + i * test_size
        train_end = test_start - args.embargo_dates
        test_end = test_start + test_size
        splits.append({
            "split": i + 1,
            "train_dates": dates[:train_end],
            "test_dates": dates[test_start:test_end],
        })
    return splits


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = max(len(y), 1)
    score = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if mask.any():
            score += mask.sum() / total * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return score


def build_model(name: str, seed: int) -> Pipeline:
    if name == "elastic_logistic_c1_l25":
        model = LogisticRegression(
            penalty="elasticnet", solver="saga", C=1.0, l1_ratio=0.25,
            max_iter=1200, random_state=seed,
        )
    elif name == "ridge_logistic_c01":
        model = LogisticRegression(C=0.1, penalty="l2", solver="lbfgs", max_iter=1200)
    elif name == "tanh_mlp_16_8":
        model = MLPClassifier(
            hidden_layer_sizes=(16, 8), activation="tanh", solver="adam",
            alpha=0.001, batch_size=512, learning_rate_init=0.001,
            max_iter=250, shuffle=True, early_stopping=False, random_state=seed,
        )
    else:
        raise ValueError(name)
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("model", model),
    ])


def date_metrics(frame: pd.DataFrame, split: int, feature_set: str, model: str, seed: int) -> list[dict]:
    rows = []
    for date, g in frame.groupby("date", sort=True):
        auc = roc_auc_score(g["target"], g["probability"]) if g["target"].nunique() > 1 else np.nan
        rows.append({
            "split": split, "date": date, "feature_set": feature_set,
            "model": model, "seed": seed, "auc": auc,
            "brier": brier_score_loss(g["target"], g["probability"]),
        })
    return rows


def portfolio_metrics(frame: pd.DataFrame, split: int, feature_set: str, model: str, seed: int,
                      args: argparse.Namespace) -> tuple[dict, list[dict]]:
    selected_prev: set[str] = set()
    daily = []
    dates = sorted(frame["date"].unique())[::args.rebalance_dates]
    for date in dates:
        g = frame[frame["date"] == date].sort_values("probability", ascending=False)
        top = g.head(args.portfolio_size)
        selected = set(top["ticker"])
        turnover = 1.0 if not selected_prev else 1.0 - len(selected & selected_prev) / max(len(selected), 1)
        gross_excess = float(top["future_return_5d"].mean() - g["future_return_5d"].mean())
        net_excess = gross_excess - turnover * args.cost_bps / 10000.0
        daily.append({
            "split": split, "date": pd.Timestamp(date), "feature_set": feature_set,
            "model": model, "seed": seed, "gross_excess_return": gross_excess,
            "net_excess_return": net_excess, "turnover": turnover,
        })
        selected_prev = selected
    d = pd.DataFrame(daily)
    return ({
        "mean_excess_return": float(d["net_excess_return"].mean()),
        "win_rate": float((d["net_excess_return"] > 0).mean()),
        "mean_turnover": float(d["turnover"].mean()),
        "worst_net_return": float(d["net_excess_return"].min()),
    }, daily)


def evaluate_fit(train: pd.DataFrame, test: pd.DataFrame, features: list[str], model_name: str,
                 seed: int, split: int, feature_set: str, args: argparse.Namespace,
                 shuffled_train_labels: np.ndarray | None = None) -> tuple[dict, list[dict], list[dict]]:
    model = build_model(model_name, seed)
    y_train = train["target"].to_numpy() if shuffled_train_labels is None else shuffled_train_labels
    model.fit(train[features], y_train)
    p = np.clip(model.predict_proba(test[features])[:, 1], 1e-6, 1 - 1e-6)
    scored = test[["date", "ticker", "target", "future_return_5d"]].copy()
    scored["probability"] = p
    auc = roc_auc_score(scored["target"], p) if scored["target"].nunique() > 1 else np.nan
    portfolio, portfolio_rows = portfolio_metrics(scored, split, feature_set, model_name, seed, args)
    metrics = {
        "split": split, "feature_set": feature_set, "model": model_name, "seed": seed,
        "train_dates": train["date"].nunique(), "test_dates": test["date"].nunique(),
        "train_rows": len(train), "test_rows": len(test), "auc": auc,
        "brier": brier_score_loss(scored["target"], p),
        "log_loss": log_loss(scored["target"], p, labels=[0, 1]),
        "ece_10": ece(scored["target"].to_numpy(), p), **portfolio,
    }
    return metrics, date_metrics(scored, split, feature_set, model_name, seed), portfolio_rows


def shuffle_within_date(train: pd.DataFrame, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    shuffled = train["target"].copy()
    for idx in train.groupby("date").indices.values():
        shuffled.iloc[idx] = rng.permutation(shuffled.iloc[idx].to_numpy())
    return shuffled.to_numpy()


def block_bootstrap(values: np.ndarray, block: int, reps: int, seed: int = 442) -> tuple[float, float]:
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return np.nan, np.nan
    blocks = [values[i:i + block] for i in range(0, len(values), block)]
    rng = np.random.default_rng(seed)
    samples = np.empty(reps)
    for i in range(reps):
        draw = [blocks[j] for j in rng.integers(0, len(blocks), len(blocks))]
        samples[i] = np.concatenate(draw)[:len(values)].mean()
    return tuple(np.quantile(samples, [0.025, 0.975]))


def paired_deltas(date_df: pd.DataFrame, portfolio_df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    rows = []
    for model in sorted(date_df["model"].unique()):
        for seed in sorted(date_df.loc[date_df["model"] == model, "seed"].unique()):
            subset = date_df[(date_df["model"] == model) & (date_df["seed"] == seed)]
            wide_auc = subset.pivot_table(index=["split", "date"], columns="feature_set", values="auc")
            wide_brier = subset.pivot_table(index=["split", "date"], columns="feature_set", values="brier")
            if not {"base_only", "graph_survivors"}.issubset(wide_auc.columns):
                continue
            auc_delta = (wide_auc["graph_survivors"] - wide_auc["base_only"]).dropna().to_numpy()
            brier_gain = (wide_brier["base_only"] - wide_brier["graph_survivors"]).dropna().to_numpy()
            port = portfolio_df[(portfolio_df["model"] == model) & (portfolio_df["seed"] == seed)]
            wide_port = port.pivot_table(index=["split", "date"], columns="feature_set", values="net_excess_return")
            return_delta = (wide_port["graph_survivors"] - wide_port["base_only"]).dropna().to_numpy()
            auc_ci = block_bootstrap(auc_delta, args.block_dates, args.bootstrap_reps, seed + 1)
            brier_ci = block_bootstrap(brier_gain, args.block_dates, args.bootstrap_reps, seed + 2)
            return_ci = block_bootstrap(return_delta, args.block_dates, args.bootstrap_reps, seed + 3)
            rows.append({
                "model": model, "seed": seed,
                "auc_delta_mean": float(np.mean(auc_delta)), "auc_ci_2_5": auc_ci[0], "auc_ci_97_5": auc_ci[1],
                "brier_gain_mean": float(np.mean(brier_gain)), "brier_ci_2_5": brier_ci[0], "brier_ci_97_5": brier_ci[1],
                "excess_return_delta_mean": float(np.mean(return_delta)),
                "return_ci_2_5": return_ci[0], "return_ci_97_5": return_ci[1],
            })
    return pd.DataFrame(rows)


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "No rows."
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in df.itertuples(index=False):
        vals = [f"{v:.5f}" if isinstance(v, (float, np.floating)) else str(v) for v in row]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    gate = validate_gate(Path(args.context_gate))
    holdout_start = pd.Timestamp(args.sealed_holdout_start)
    motion = normalize_motion(Path(args.motion_features))
    labels = load_labels(Path(args.db), holdout_start)
    data = motion.merge(labels, on=["date", "ticker"], how="inner")
    data = data[(data["date"] < holdout_start) & (data["label_date"] < holdout_start)].copy()
    dates = sorted(data["date"].drop_duplicates().tolist())
    splits = make_splits(dates, args)
    ann_seeds = [int(x) for x in args.ann_seeds.split(",")]
    placebo_seeds = [int(x) for x in args.placebo_seeds.split(",")]

    metric_rows, date_rows, portfolio_rows, leakage_rows = [], [], [], []
    for split_info in splits:
        split = split_info["split"]
        train = data[data["date"].isin(split_info["train_dates"])].copy()
        test = data[data["date"].isin(split_info["test_dates"])].copy()
        leakage_rows.append({
            "split": split, "train_start": train["date"].min(), "train_end": train["date"].max(),
            "test_start": test["date"].min(), "test_end": test["date"].max(),
            "max_label_date": data.loc[data["date"].isin(split_info["test_dates"]), "label_date"].max(),
            "embargo_dates": args.embargo_dates, "holdout_start": holdout_start,
            "holdout_opened": False,
        })
        for feature_set, features in FEATURE_SETS.items():
            for model_name in ["elastic_logistic_c1_l25", "ridge_logistic_c01", "tanh_mlp_16_8"]:
                seeds = ann_seeds if model_name == "tanh_mlp_16_8" else [0]
                for seed in seeds:
                    metrics, daily, portfolio = evaluate_fit(
                        train, test, features, model_name, seed, split, feature_set, args
                    )
                    metric_rows.append(metrics)
                    date_rows.extend(daily)
                    portfolio_rows.extend(portfolio)
                    print(f"fit split={split} set={feature_set} model={model_name} seed={seed}", flush=True)

    placebo_rows = []
    for split_info in splits:
        split = split_info["split"]
        train = data[data["date"].isin(split_info["train_dates"])].copy()
        test = data[data["date"].isin(split_info["test_dates"])].copy()
        for seed in placebo_seeds:
            shuffled = shuffle_within_date(train, seed)
            for model_name in ["elastic_logistic_c1_l25", "tanh_mlp_16_8"]:
                metrics, _, _ = evaluate_fit(
                    train, test, FEATURE_SETS["graph_survivors"], model_name, seed,
                    split, "graph_survivors_placebo", args, shuffled,
                )
                placebo_rows.append(metrics)
                print(f"placebo split={split} model={model_name} seed={seed}", flush=True)

    metrics_df = pd.DataFrame(metric_rows)
    date_df = pd.DataFrame(date_rows)
    portfolio_df = pd.DataFrame(portfolio_rows)
    placebo_df = pd.DataFrame(placebo_rows)
    leakage_df = pd.DataFrame(leakage_rows)
    summary = metrics_df.groupby(["feature_set", "model"], as_index=False).agg(
        auc_mean=("auc", "mean"), auc_std=("auc", "std"),
        brier_mean=("brier", "mean"), log_loss_mean=("log_loss", "mean"),
        ece_10_mean=("ece_10", "mean"), mean_excess_return=("mean_excess_return", "mean"),
        win_rate=("win_rate", "mean"), worst_net_return=("worst_net_return", "min"),
        fits=("auc", "size"),
    ).sort_values(["auc_mean", "brier_mean"], ascending=[False, True])
    paired = paired_deltas(date_df, portfolio_df, args)

    metrics_df.to_csv(output / "confirmatory_metrics.csv", index=False)
    date_df.to_csv(output / "confirmatory_daily_metrics.csv", index=False)
    portfolio_df.to_csv(output / "confirmatory_portfolio_daily.csv", index=False)
    placebo_df.to_csv(output / "placebo_metrics.csv", index=False)
    leakage_df.to_csv(output / "leakage_audit.csv", index=False)
    summary.to_csv(output / "confirmatory_summary.csv", index=False)
    paired.to_csv(output / "paired_graph_deltas.csv", index=False)

    best = summary.iloc[0]
    placebo_auc = float(placebo_df["auc"].mean())
    linear_delta_positive = bool(
        not paired[paired["model"].str.contains("logistic")].empty
        and (paired.loc[paired["model"].str.contains("logistic"), "auc_ci_2_5"] > 0).all()
    )
    ann_delta_positive = bool(
        not paired[paired["model"] == "tanh_mlp_16_8"].empty
        and (paired.loc[paired["model"] == "tanh_mlp_16_8", "auc_ci_2_5"] > 0).all()
    )
    promotion_candidate = bool(linear_delta_positive and ann_delta_positive and best["auc_mean"] > placebo_auc)

    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "status": "completed_pending_review",
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "rows": len(data), "dates": len(dates), "tickers": int(data["ticker"].nunique()),
        "splits": len(splits), "holdout_start": str(holdout_start.date()),
        "holdout_opened": False, "best_feature_set": best["feature_set"],
        "best_model": best["model"], "best_auc_mean": float(best["auc_mean"]),
        "placebo_auc_mean": placebo_auc, "promotion_candidate": promotion_candidate,
        "source_context_id": gate["context_id"],
    }
    (output / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2))
    candidate = {
        "action": "review_then_merge", "base_context_id": gate["context_id"],
        "completed_experiment": manifest,
        "holdout_status": {"status": "sealed", "date_start": args.sealed_holdout_start, "trading_dates": 60},
    }
    (output / "context_gate_candidate_update.json").write_text(json.dumps(candidate, indent=2))
    readout = [
        "# Graph Signal Confirmatory Readout", "",
        f"- Best feature set: `{best['feature_set']}`",
        f"- Best model: `{best['model']}`",
        f"- Best mean AUC: {best['auc_mean']:.5f}",
        f"- Placebo mean AUC: {placebo_auc:.5f}",
        f"- Promotion candidate: `{str(promotion_candidate).lower()}`",
        "- Final 60-date holdout opened: `false`", "",
        "## Model and feature-set summary", "", markdown_table(summary.round(5)), "",
        "## Paired graph-survivor deltas versus base", "", markdown_table(paired.round(5)), "",
        "## Guardrails", "",
        "- All label endpoints precede the sealed holdout.",
        "- Chronological expanding training windows use a five-date embargo.",
        "- Imputation and scaling are fit on training rows only.",
        "- Placebo labels are shuffled within date in training only.",
        "- Uncertainty uses paired five-date blocks, not ticker-row independence.",
        "- Results remain paper-only and provisional until reviewed.",
    ]
    (output / "confirmatory_readout.md").write_text("\n".join(readout))
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
