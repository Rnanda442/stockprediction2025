#!/usr/bin/env python3
"""Confirm a fixed training-only similarity adjustment for linear portfolios."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

import run_graph_signal_confirmatory as shared


EXPERIMENT_ID = "similarity_weighted_linear_400d_v1"
DESIGN_SIGNATURE = (
    "similarity-weighted-linear-400d-v1:h5:elastic-c1-l25:train-knn20:"
    "alpha025:p10:r5:cost10:6walkforward:block-bootstrap:neighbor-placebo:holdout60"
)
FEATURES = [
    "ret_5d", "ret_20d", "ret_60d", "vol_20d", "vol_60d",
    "drawdown_60d", "dollar_vol_20d_log",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--raw-features", required=True)
    p.add_argument("--context-gate", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--sealed-holdout-start", default="2026-05-29")
    p.add_argument("--splits", type=int, default=6)
    p.add_argument("--test-dates", type=int, default=20)
    p.add_argument("--embargo-dates", type=int, default=5)
    p.add_argument("--min-train-dates", type=int, default=120)
    p.add_argument("--neighbors", type=int, default=20)
    p.add_argument("--max-reference-rows", type=int, default=80000)
    p.add_argument("--alpha", type=float, default=0.25)
    p.add_argument("--portfolio-size", type=int, default=10)
    p.add_argument("--rebalance-dates", type=int, default=5)
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--cost-grid", default="0,10,25,50")
    p.add_argument("--placebo-seeds", default="117,442,2025,8080,9001")
    p.add_argument("--bootstrap-reps", type=int, default=5000)
    p.add_argument("--block-dates", type=int, default=5)
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
        raise RuntimeError("Similarity experiment is not uniquely approved.")
    if matches[0].get("status") != "approved_next":
        raise RuntimeError("Similarity experiment is not approved for compute.")
    return gate


def load_data(raw_path: Path, db_path: Path, holdout_start: pd.Timestamp) -> pd.DataFrame:
    raw = pd.read_csv(raw_path)
    raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
    missing = [x for x in ["date", "ticker"] + FEATURES if x not in raw]
    if missing:
        raise RuntimeError(f"Raw feature panel is missing: {missing}")
    labels = shared.load_labels(db_path, holdout_start)
    data = raw[["date", "ticker"] + FEATURES].merge(labels, on=["date", "ticker"], how="inner")
    return data[(data["date"] < holdout_start) & (data["label_date"] < holdout_start)].copy()


def fit_base(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, SimpleImputer, StandardScaler]:
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    x_train = scaler.fit_transform(imputer.fit_transform(train[FEATURES]))
    x_test = scaler.transform(imputer.transform(test[FEATURES]))
    model = LogisticRegression(
        penalty="elasticnet", solver="saga", C=1.0, l1_ratio=0.25,
        max_iter=1500, random_state=442,
    )
    model.fit(x_train, train["target"])
    probability = np.clip(model.predict_proba(x_test)[:, 1], 1e-6, 1 - 1e-6)
    return probability, x_train, x_test, imputer, scaler


def sample_reference(train: pd.DataFrame, x_train: np.ndarray, maximum: int) -> tuple[pd.DataFrame, np.ndarray]:
    if len(train) <= maximum:
        idx = np.arange(len(train))
    else:
        rng = np.random.default_rng(442)
        idx = np.sort(rng.choice(len(train), size=maximum, replace=False))
    return train.iloc[idx].reset_index(drop=True), x_train[idx]


def fit_similarity(reference_x: np.ndarray, neighbors: int) -> tuple[NearestNeighbors, float]:
    model = NearestNeighbors(n_neighbors=neighbors, metric="euclidean", algorithm="auto", n_jobs=-1)
    model.fit(reference_x)
    rng = np.random.default_rng(442)
    probe_idx = rng.choice(len(reference_x), size=min(4000, len(reference_x)), replace=False)
    probe_dist, _ = model.kneighbors(reference_x[probe_idx], n_neighbors=min(neighbors + 1, len(reference_x)))
    scale = float(np.median(probe_dist[:, 1:].mean(axis=1)))
    return model, max(scale, 1e-6)


def neighbor_adjustment(base_probability: np.ndarray, distances: np.ndarray, indices: np.ndarray,
                        reference_target: np.ndarray, scale: float, alpha: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    weights = np.exp(-distances / scale)
    consensus = (weights * reference_target[indices]).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-12)
    confidence = np.exp(-distances.mean(axis=1) / scale)
    effective_alpha = alpha * confidence
    adjusted = np.clip(base_probability + effective_alpha * (consensus - base_probability), 1e-6, 1 - 1e-6)
    return adjusted, consensus, confidence


def shuffle_reference_within_date(reference: pd.DataFrame, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    target = reference["target"].to_numpy().copy()
    for idx in reference.groupby("date", sort=False).indices.values():
        target[idx] = rng.permutation(target[idx])
    return target


def predictive_metrics(scored: pd.DataFrame, split: int, strategy: str, placebo_seed: int | None = None) -> tuple[dict, list[dict]]:
    p = scored["probability"].to_numpy()
    y = scored["target"].to_numpy()
    overall = {
        "split": split, "strategy": strategy, "placebo_seed": placebo_seed,
        "auc": roc_auc_score(y, p), "brier": brier_score_loss(y, p),
        "log_loss": log_loss(y, p, labels=[0, 1]), "ece_10": shared.ece(y, p),
    }
    daily = []
    for date, g in scored.groupby("date", sort=True):
        daily.append({
            "split": split, "date": date, "strategy": strategy,
            "placebo_seed": placebo_seed,
            "auc": roc_auc_score(g["target"], g["probability"]) if g["target"].nunique() > 1 else np.nan,
            "brier": brier_score_loss(g["target"], g["probability"]),
        })
    return overall, daily


def portfolio(scored: pd.DataFrame, split: int, strategy: str, args: argparse.Namespace,
              placebo_seed: int | None = None) -> tuple[dict, list[dict]]:
    previous: set[str] = set()
    rows = []
    for date in sorted(scored["date"].unique())[::args.rebalance_dates]:
        frame = scored[scored["date"] == date].sort_values("probability", ascending=False)
        selected_frame = frame.head(args.portfolio_size)
        selected = set(selected_frame["ticker"])
        turnover = 1.0 if not previous else 1.0 - len(selected & previous) / max(len(selected), 1)
        gross = float(selected_frame["future_return_5d"].mean() - frame["future_return_5d"].mean())
        rows.append({
            "split": split, "date": pd.Timestamp(date), "strategy": strategy,
            "placebo_seed": placebo_seed, "gross_excess_return": gross,
            "turnover": turnover, "net_excess_return": gross - turnover * args.cost_bps / 10000.0,
        })
        previous = selected
    daily = pd.DataFrame(rows)
    return ({
        "mean_excess_return": float(daily["net_excess_return"].mean()),
        "win_rate": float((daily["net_excess_return"] > 0).mean()),
        "mean_turnover": float(daily["turnover"].mean()),
        "worst_net_return": float(daily["net_excess_return"].min()),
    }, rows)


def score_frame(test: pd.DataFrame, probability: np.ndarray) -> pd.DataFrame:
    scored = test[["date", "ticker", "target", "future_return_5d"]].copy()
    scored["probability"] = probability
    return scored


def paired_summary(daily_metrics: pd.DataFrame, portfolio_daily: pd.DataFrame,
                   args: argparse.Namespace) -> dict:
    prediction = daily_metrics[daily_metrics["placebo_seed"].isna()].pivot_table(
        index=["split", "date"], columns="strategy", values=["auc", "brier"]
    ).sort_index()
    auc_delta = (prediction[("auc", "similarity_weighted")] - prediction[("auc", "plain")]).dropna().to_numpy()
    brier_gain = (prediction[("brier", "plain")] - prediction[("brier", "similarity_weighted")]).dropna().to_numpy()
    returns = portfolio_daily[portfolio_daily["placebo_seed"].isna()].pivot_table(
        index=["split", "date"], columns="strategy", values="net_excess_return"
    ).sort_index()
    return_delta = (returns["similarity_weighted"] - returns["plain"]).dropna().to_numpy()
    auc_ci = shared.block_bootstrap(auc_delta, args.block_dates, args.bootstrap_reps, 443)
    brier_ci = shared.block_bootstrap(brier_gain, args.block_dates, args.bootstrap_reps, 444)
    return_ci = shared.block_bootstrap(return_delta, args.block_dates, args.bootstrap_reps, 445)
    return {
        "auc_delta_mean": float(auc_delta.mean()), "auc_ci_2_5": auc_ci[0], "auc_ci_97_5": auc_ci[1],
        "brier_gain_mean": float(brier_gain.mean()), "brier_ci_2_5": brier_ci[0], "brier_ci_97_5": brier_ci[1],
        "excess_return_delta_mean": float(return_delta.mean()),
        "return_ci_2_5": return_ci[0], "return_ci_97_5": return_ci[1],
    }


def main() -> None:
    args = parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    gate = validate_gate(Path(args.context_gate))
    holdout_start = pd.Timestamp(args.sealed_holdout_start)
    data = load_data(Path(args.raw_features), Path(args.db), holdout_start)
    dates = sorted(data["date"].drop_duplicates().tolist())
    splits = shared.make_splits(dates, args)
    placebo_seeds = [int(x) for x in args.placebo_seeds.split(",")]
    metric_rows, daily_rows, portfolio_rows, audit_rows = [], [], [], []

    for info in splits:
        split = info["split"]
        train = data[data["date"].isin(info["train_dates"])].reset_index(drop=True)
        test = data[data["date"].isin(info["test_dates"])].reset_index(drop=True)
        base_probability, x_train, x_test, _, _ = fit_base(train, test)
        reference, reference_x = sample_reference(train, x_train, args.max_reference_rows)
        neighbor_model, scale = fit_similarity(reference_x, args.neighbors)
        distances, indices = neighbor_model.kneighbors(x_test, n_neighbors=args.neighbors)
        weighted_probability, consensus, confidence = neighbor_adjustment(
            base_probability, distances, indices, reference["target"].to_numpy(), scale, args.alpha
        )
        for strategy, probability in [("plain", base_probability), ("similarity_weighted", weighted_probability)]:
            scored = score_frame(test, probability)
            metrics, daily = predictive_metrics(scored, split, strategy)
            port_metrics, port_daily = portfolio(scored, split, strategy, args)
            metric_rows.append({**metrics, **port_metrics})
            daily_rows.extend(daily)
            portfolio_rows.extend(port_daily)
        for seed in placebo_seeds:
            shuffled_target = shuffle_reference_within_date(reference, seed)
            placebo_probability, _, _ = neighbor_adjustment(
                base_probability, distances, indices, shuffled_target, scale, args.alpha
            )
            scored = score_frame(test, placebo_probability)
            metrics, daily = predictive_metrics(scored, split, "neighbor_placebo", seed)
            port_metrics, port_daily = portfolio(scored, split, "neighbor_placebo", args, seed)
            metric_rows.append({**metrics, **port_metrics})
            daily_rows.extend(daily)
            portfolio_rows.extend(port_daily)
        audit_rows.append({
            "split": split, "train_start": train["date"].min(), "train_end": train["date"].max(),
            "test_start": test["date"].min(), "test_end": test["date"].max(),
            "max_test_label_date": test["label_date"].max(), "embargo_dates": args.embargo_dates,
            "reference_rows": len(reference), "neighbors": args.neighbors,
            "similarity_fit_scope": "training_rows_only", "holdout_start": holdout_start,
            "holdout_opened": False, "mean_neighbor_consensus": float(consensus.mean()),
            "mean_similarity_confidence": float(confidence.mean()),
        })
        print(f"completed split={split}", flush=True)

    metrics = pd.DataFrame(metric_rows)
    daily = pd.DataFrame(daily_rows)
    portfolios = pd.DataFrame(portfolio_rows)
    audit = pd.DataFrame(audit_rows)
    primary = metrics[metrics["placebo_seed"].isna()].groupby("strategy", as_index=False).agg(
        auc_mean=("auc", "mean"), brier_mean=("brier", "mean"), log_loss_mean=("log_loss", "mean"),
        ece_10_mean=("ece_10", "mean"), mean_excess_return=("mean_excess_return", "mean"),
        win_rate=("win_rate", "mean"), mean_turnover=("mean_turnover", "mean"),
        worst_net_return=("worst_net_return", "min"), splits=("split", "size"),
    )
    placebo = metrics[metrics["strategy"] == "neighbor_placebo"].groupby("strategy", as_index=False).agg(
        auc_mean=("auc", "mean"), brier_mean=("brier", "mean"),
        mean_excess_return=("mean_excess_return", "mean"), fits=("split", "size"),
    )
    paired = paired_summary(daily, portfolios, args)
    cost_rows = []
    for cost in [float(x) for x in args.cost_grid.split(",")]:
        subset = portfolios[portfolios["placebo_seed"].isna()].copy()
        subset["net_at_cost"] = subset["gross_excess_return"] - subset["turnover"] * cost / 10000.0
        for strategy, frame in subset.groupby("strategy"):
            cost_rows.append({"cost_bps": cost, "strategy": strategy, "mean_excess_return": frame["net_at_cost"].mean()})
    costs = pd.DataFrame(cost_rows)
    placebo_return_delta = float(
        metrics.loc[metrics["strategy"] == "neighbor_placebo", "mean_excess_return"].mean()
        - metrics.loc[metrics["strategy"] == "plain", "mean_excess_return"].mean()
    )
    promotion = bool(
        paired["return_ci_2_5"] > 0
        and paired["auc_ci_2_5"] > -0.002
        and paired["brier_ci_2_5"] > -0.001
        and paired["excess_return_delta_mean"] > placebo_return_delta
    )

    metrics.to_csv(output / "strategy_metrics.csv", index=False)
    daily.to_csv(output / "predictive_daily.csv", index=False)
    portfolios.to_csv(output / "portfolio_daily.csv", index=False)
    audit.to_csv(output / "leakage_audit.csv", index=False)
    primary.to_csv(output / "primary_summary.csv", index=False)
    placebo.to_csv(output / "placebo_summary.csv", index=False)
    costs.to_csv(output / "cost_sensitivity.csv", index=False)
    pd.DataFrame([paired]).to_csv(output / "paired_similarity_delta.csv", index=False)
    manifest = {
        "experiment_id": EXPERIMENT_ID, "design_signature": DESIGN_SIGNATURE,
        "status": "completed_pending_review", "generated_at": pd.Timestamp.utcnow().isoformat(),
        "rows": len(data), "dates": len(dates), "tickers": int(data["ticker"].nunique()),
        "splits": len(splits), "holdout_start": str(holdout_start.date()),
        "holdout_opened": False, "paired_result": paired,
        "placebo_return_delta_mean": placebo_return_delta,
        "promotion_candidate": promotion, "source_context_id": gate["context_id"],
    }
    (output / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2))
    (output / "context_gate_candidate_update.json").write_text(json.dumps({
        "action": "review_then_merge", "base_context_id": gate["context_id"],
        "completed_experiment": manifest,
        "holdout_status": {"status": "sealed", "date_start": args.sealed_holdout_start, "trading_dates": 60},
    }, indent=2))
    readout = [
        "# Similarity-Weighted Linear 400-Date Readout", "",
        f"- Paired after-cost return delta: {paired['excess_return_delta_mean']:.5f}",
        f"- Return interval: [{paired['return_ci_2_5']:.5f}, {paired['return_ci_97_5']:.5f}]",
        f"- Paired AUC delta: {paired['auc_delta_mean']:.5f}",
        f"- AUC interval: [{paired['auc_ci_2_5']:.5f}, {paired['auc_ci_97_5']:.5f}]",
        f"- Paired Brier gain: {paired['brier_gain_mean']:.5f}",
        f"- Neighbor-placebo return delta: {placebo_return_delta:.5f}",
        f"- Promotion candidate: `{str(promotion).lower()}`",
        "- Final 60-date holdout opened: `false`", "",
        "## Primary strategies", "", shared.markdown_table(primary.round(5)), "",
        "## Cost sensitivity", "", shared.markdown_table(costs.round(5)), "",
        "## Guardrails", "",
        "- One fixed elastic model and one fixed similarity rule were used.",
        "- Imputation, scaling, the classifier, distance scale, and neighbor reference were fit on training rows only.",
        "- Placebos shuffled neighbor labels within training date without refitting the base model.",
        "- Paired uncertainty used five-date blocks.",
        "- Portfolio size, rebalance interval, cost, neighbor count, and alpha were not tuned.",
    ]
    (output / "similarity_readout.md").write_text("\n".join(readout))
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
