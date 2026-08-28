#!/usr/bin/env python3
"""Confirm graph persistence signals with split-specific training-fitted clusters."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import MiniBatchKMeans
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

import run_graph_signal_confirmatory as shared


EXPERIMENT_ID = "graph_signal_confirmatory_v1"
DESIGN_SIGNATURE = (
    "graph-signal-confirmatory-v1:h5:history400:trainfit-kmeans24:crowding+"
    "regime-residence+cluster-switch:elastic+ridge+tanh:6walkforward:"
    "date-block-bootstrap:label-placebo:holdout60"
)
RAW_BASE = [
    "ret_5d", "ret_20d", "ret_60d", "vol_20d", "vol_60d",
    "drawdown_60d", "dollar_vol_20d_log",
]
FEATURE_SETS = {
    "base_only": RAW_BASE + ["graph_degree"],
    "base_plus_crowding": RAW_BASE + ["graph_degree", "crowding_change_5d"],
    "base_plus_regime": RAW_BASE + ["graph_degree", "graph_regime_residence_days"],
    "graph_survivors": RAW_BASE + [
        "graph_degree", "crowding_change_5d", "graph_regime_residence_days",
        "graph_cluster_switch_count_20d",
    ],
}


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
    p.add_argument("--clusters", type=int, default=24)
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
        raise RuntimeError("Confirmatory design is not uniquely approved.")
    if matches[0].get("status") != "approved_next":
        raise RuntimeError("Confirmatory design is not approved for compute.")
    return gate


def load_raw(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    missing = [x for x in ["date", "ticker"] + RAW_BASE if x not in frame]
    if missing:
        raise RuntimeError(f"Raw feature panel is missing: {missing}")
    return frame[["date", "ticker"] + RAW_BASE].drop_duplicates(["date", "ticker"])


def add_training_fitted_graph_features(train: pd.DataFrame, test: pd.DataFrame,
                                       clusters: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    x_train = scaler.fit_transform(imputer.fit_transform(train[RAW_BASE]))
    x_test = scaler.transform(imputer.transform(test[RAW_BASE]))
    model = MiniBatchKMeans(
        n_clusters=clusters, random_state=seed, batch_size=4096,
        n_init=10, max_iter=200,
    ).fit(x_train)
    train_cluster = model.predict(x_train)
    test_cluster = model.predict(x_test)
    train_distance = np.linalg.norm(x_train - model.cluster_centers_[train_cluster], axis=1)
    test_distance = np.linalg.norm(x_test - model.cluster_centers_[test_cluster], axis=1)
    occupancy = np.bincount(train_cluster, minlength=clusters).astype(float)
    occupancy /= max(occupancy.max(), 1.0)

    train = train.copy()
    test = test.copy()
    train["_cluster"] = train_cluster
    test["_cluster"] = test_cluster
    train["_crowding"] = -train_distance
    test["_crowding"] = -test_distance
    train["graph_degree"] = occupancy[train_cluster]
    test["graph_degree"] = occupancy[test_cluster]
    combined = pd.concat([
        train.assign(_scope="train"), test.assign(_scope="test")
    ], ignore_index=True).sort_values(["ticker", "date"])
    grouped = combined.groupby("ticker", sort=False)
    combined["crowding_change_5d"] = combined["_crowding"] - grouped["_crowding"].shift(5)
    switched = combined["_cluster"].ne(grouped["_cluster"].shift(1)).astype(float)
    combined["graph_cluster_switch_count_20d"] = switched.groupby(combined["ticker"]).transform(
        lambda s: s.rolling(20, min_periods=5).sum()
    )
    run_id = combined["_cluster"].ne(grouped["_cluster"].shift(1)).groupby(combined["ticker"]).cumsum()
    combined["graph_regime_residence_days"] = combined.groupby(
        [combined["ticker"], run_id], sort=False
    ).cumcount() + 1
    train_out = combined[combined["_scope"] == "train"].drop(columns=["_scope", "_cluster", "_crowding"])
    test_out = combined[combined["_scope"] == "test"].drop(columns=["_scope", "_cluster", "_crowding"])
    return train_out, test_out


def main() -> None:
    args = parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    gate = validate_gate(Path(args.context_gate))
    holdout_start = pd.Timestamp(args.sealed_holdout_start)
    raw = load_raw(Path(args.raw_features))
    labels = shared.load_labels(Path(args.db), holdout_start)
    data = raw.merge(labels, on=["date", "ticker"], how="inner")
    data = data[(data["date"] < holdout_start) & (data["label_date"] < holdout_start)].copy()
    dates = sorted(data["date"].drop_duplicates().tolist())
    splits = shared.make_splits(dates, args)
    ann_seeds = [int(x) for x in args.ann_seeds.split(",")]
    placebo_seeds = [int(x) for x in args.placebo_seeds.split(",")]
    metric_rows, date_rows, portfolio_rows, placebo_rows, audit_rows = [], [], [], [], []

    for info in splits:
        split = info["split"]
        train_raw = data[data["date"].isin(info["train_dates"])].copy()
        test_raw = data[data["date"].isin(info["test_dates"])].copy()
        train, test = add_training_fitted_graph_features(train_raw, test_raw, args.clusters, 442 + split)
        audit_rows.append({
            "split": split, "train_start": train["date"].min(), "train_end": train["date"].max(),
            "test_start": test["date"].min(), "test_end": test["date"].max(),
            "max_test_label_date": test["label_date"].max(), "embargo_dates": args.embargo_dates,
            "graph_fit_scope": "training_rows_only", "clusters": args.clusters,
            "holdout_start": holdout_start, "holdout_opened": False,
        })
        for feature_set, features in FEATURE_SETS.items():
            for model_name in ["elastic_logistic_c1_l25", "ridge_logistic_c01", "tanh_mlp_16_8"]:
                seeds = ann_seeds if model_name == "tanh_mlp_16_8" else [0]
                for seed in seeds:
                    metrics, daily, portfolio = shared.evaluate_fit(
                        train, test, features, model_name, seed, split, feature_set, args
                    )
                    metric_rows.append(metrics)
                    date_rows.extend(daily)
                    portfolio_rows.extend(portfolio)
                    print(f"fit split={split} set={feature_set} model={model_name} seed={seed}", flush=True)
        for seed in placebo_seeds:
            shuffled = shared.shuffle_within_date(train, seed)
            for model_name in ["elastic_logistic_c1_l25", "tanh_mlp_16_8"]:
                metrics, _, _ = shared.evaluate_fit(
                    train, test, FEATURE_SETS["graph_survivors"], model_name, seed,
                    split, "graph_survivors_placebo", args, shuffled,
                )
                placebo_rows.append(metrics)
                print(f"placebo split={split} model={model_name} seed={seed}", flush=True)

    metrics = pd.DataFrame(metric_rows)
    daily = pd.DataFrame(date_rows)
    portfolio = pd.DataFrame(portfolio_rows)
    placebo = pd.DataFrame(placebo_rows)
    audit = pd.DataFrame(audit_rows)
    summary = metrics.groupby(["feature_set", "model"], as_index=False).agg(
        auc_mean=("auc", "mean"), auc_std=("auc", "std"), brier_mean=("brier", "mean"),
        log_loss_mean=("log_loss", "mean"), ece_10_mean=("ece_10", "mean"),
        mean_excess_return=("mean_excess_return", "mean"), win_rate=("win_rate", "mean"),
        worst_net_return=("worst_net_return", "min"), fits=("auc", "size"),
    ).sort_values(["auc_mean", "brier_mean"], ascending=[False, True])
    paired = shared.paired_deltas(daily, portfolio, args)
    metrics.to_csv(output / "confirmatory_metrics.csv", index=False)
    daily.to_csv(output / "confirmatory_daily_metrics.csv", index=False)
    portfolio.to_csv(output / "confirmatory_portfolio_daily.csv", index=False)
    placebo.to_csv(output / "placebo_metrics.csv", index=False)
    audit.to_csv(output / "leakage_audit.csv", index=False)
    summary.to_csv(output / "confirmatory_summary.csv", index=False)
    paired.to_csv(output / "paired_graph_deltas.csv", index=False)

    best = summary.iloc[0]
    placebo_auc = float(placebo["auc"].mean())
    linear = paired[paired["model"].str.contains("logistic")]
    ann = paired[paired["model"] == "tanh_mlp_16_8"]
    promotion = bool(
        not linear.empty and not ann.empty
        and (linear["auc_ci_2_5"] > 0).all()
        and (ann["auc_ci_2_5"] > 0).all()
        and best["auc_mean"] > placebo_auc
    )
    manifest = {
        "experiment_id": EXPERIMENT_ID, "design_signature": DESIGN_SIGNATURE,
        "status": "completed_pending_review", "generated_at": pd.Timestamp.utcnow().isoformat(),
        "rows": len(data), "dates": len(dates), "tickers": int(data["ticker"].nunique()),
        "splits": len(splits), "graph_fit_scope": "training_rows_only",
        "holdout_start": str(holdout_start.date()), "holdout_opened": False,
        "best_feature_set": best["feature_set"], "best_model": best["model"],
        "best_auc_mean": float(best["auc_mean"]), "placebo_auc_mean": placebo_auc,
        "promotion_candidate": promotion, "source_context_id": gate["context_id"],
    }
    (output / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2))
    (output / "context_gate_candidate_update.json").write_text(json.dumps({
        "action": "review_then_merge", "base_context_id": gate["context_id"],
        "completed_experiment": manifest,
        "holdout_status": {"status": "sealed", "date_start": args.sealed_holdout_start, "trading_dates": 60},
    }, indent=2))
    readout = [
        "# Training-Fitted Graph Signal Confirmatory Readout", "",
        f"- Best feature set: `{best['feature_set']}`",
        f"- Best model: `{best['model']}`",
        f"- Best mean AUC: {best['auc_mean']:.5f}",
        f"- Placebo mean AUC: {placebo_auc:.5f}",
        f"- Promotion candidate: `{str(promotion).lower()}`",
        "- Final 60-date holdout opened: `false`", "",
        "## Model and feature-set summary", "", shared.markdown_table(summary.round(5)), "",
        "## Paired graph-survivor deltas versus base", "", shared.markdown_table(paired.round(5)), "",
        "## Guardrails", "",
        "- Raw features and labels end before the sealed holdout.",
        "- The scaler, imputer, and 24-cluster similarity model are refit on training rows for every split.",
        "- Test rows are transformed but never used to fit cluster centers.",
        "- Raw 3D kinematic variables are excluded.",
        "- Uncertainty uses paired five-date blocks and placebos shuffle training labels within date.",
    ]
    (output / "confirmatory_readout.md").write_text("\n".join(readout))
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
