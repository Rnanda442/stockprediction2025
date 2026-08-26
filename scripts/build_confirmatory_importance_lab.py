#!/usr/bin/env python3
"""Confirm ANN feature importance without opening the sealed future holdout.

This paper-only lab uses the direction_relu_graph architecture and combines:

* joint within-date permutation for correlated feature clusters;
* individual within-date permutation for diagnostic attribution;
* leave-one-feature/group-out model retraining;
* multiple ANN random seeds and chronological, embargoed splits; and
* weekly block-bootstrap confidence intervals.

The final holdout is recorded but never trained on, calibrated on, scored, or
used to choose features. No live model or brokerage state is modified.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
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
    add_graph_context,
    build_trailing_features,
    select_graph_neighbors,
)


MODEL_FEATURES = (*BASE_FEATURES, *GRAPH_FEATURES)
FEATURE_GROUPS = {
    "momentum": (
        "ret_1d",
        "ret_5d",
        "ret_20d",
        "ret_60d",
        "z_ma20",
        "ma_crossover",
        "rsi_14",
    ),
    "risk": ("vol_20d", "vol_60d", "drawdown_60d"),
    "liquidity": ("dollar_vol_20d_log",),
    "graph": (
        "neighbor_ret_5d",
        "neighbor_ret_20d",
        "neighbor_vol_20d",
        "graph_similarity_mean",
        "graph_degree",
    ),
}
DEFAULT_DROP_CANDIDATES = (
    "graph_degree",
    "graph_similarity_mean",
    "vol_60d",
    "ret_60d",
    "neighbor_vol_20d",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--horizons", default="5,20")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--splits", type=int, default=3)
    parser.add_argument("--test-dates", type=int, default=30)
    parser.add_argument("--holdout-dates", type=int, default=60)
    parser.add_argument("--minimum-training-dates", type=int, default=100)
    parser.add_argument("--max-tickers", type=int, default=600)
    parser.add_argument("--graph-lookback-days", type=int, default=80)
    parser.add_argument("--graph-neighbors", type=int, default=8)
    parser.add_argument("--max-train-rows", type=int, default=150000)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument(
        "--drop-candidates", default=",".join(DEFAULT_DROP_CANDIDATES)
    )
    parser.add_argument(
        "--stage", choices=("screen", "confirm", "all"), default="all"
    )
    return parser.parse_args()


def load_prices(db_path: Path) -> tuple[pd.DataFrame, str]:
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "ResearchPrices" in tables:
            table = "ResearchPrices"
            query = "SELECT ticker, begins_at, close_price, volume FROM ResearchPrices"
            params: tuple[object, ...] = ()
        elif "HistoricalPrices" in tables:
            table = "HistoricalPrices:5year"
            query = """
                SELECT ticker, begins_at, close_price, volume
                FROM HistoricalPrices WHERE span='5year'
            """
            params = ()
        elif "RecentPrices" in tables:
            table = "RecentPrices"
            query = "SELECT ticker, begins_at, close_price, volume FROM RecentPrices"
            params = ()
        else:
            raise RuntimeError(
                "Database must contain ResearchPrices, HistoricalPrices, or RecentPrices"
            )
        prices = pd.read_sql_query(query, connection, params=params)

    prices["ticker"] = prices["ticker"].astype(str).str.upper().str.strip()
    prices["date"] = pd.to_datetime(prices["begins_at"], errors="coerce").dt.normalize()
    prices["close_price"] = pd.to_numeric(prices["close_price"], errors="coerce")
    prices["volume"] = pd.to_numeric(prices["volume"], errors="coerce")
    prices = prices.dropna(subset=["ticker", "date", "close_price", "volume"])
    prices = prices[
        (prices["ticker"] != "")
        & (prices["close_price"] > 0)
        & (prices["volume"] >= 0)
    ]
    prices = prices.sort_values(["ticker", "date"]).drop_duplicates(
        ["ticker", "date"], keep="last"
    )
    return prices.reset_index(drop=True), table


def add_target(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    labelled = frame.copy()
    grouped = labelled.groupby("ticker", sort=False)
    labelled["future_return"] = (
        grouped["close_price"].shift(-horizon) / labelled["close_price"] - 1.0
    )
    labelled["target_up"] = (labelled["future_return"] > 0).astype(float)
    labelled.loc[labelled["future_return"].isna(), "target_up"] = np.nan
    return labelled


def chronological_splits(
    dates: list[pd.Timestamp],
    horizon: int,
    holdout_dates: int,
    test_dates: int,
    split_count: int,
    minimum_training_dates: int,
) -> tuple[list[dict[str, object]], list[pd.Timestamp]]:
    if len(dates) <= holdout_dates + horizon:
        raise RuntimeError("Not enough dates to reserve the requested holdout")
    holdout = dates[-holdout_dates:]
    cursor = len(dates) - holdout_dates - horizon - 1
    newest_first: list[dict[str, object]] = []
    for _ in range(split_count):
        test_end_index = cursor
        test_start_index = test_end_index - test_dates + 1
        cutoff_index = test_start_index - horizon - 1
        if cutoff_index + 1 < minimum_training_dates:
            raise RuntimeError(
                "Not enough independent dates for the requested training, test, "
                "embargo, and sealed-holdout regions. Use the five-year export or "
                "reduce --holdout-dates/--test-dates/--splits."
            )
        newest_first.append(
            {
                "training_cutoff": dates[cutoff_index],
                "test_start": dates[test_start_index],
                "test_end": dates[test_end_index],
                "training_dates": cutoff_index + 1,
            }
        )
        cursor = test_start_index - horizon - 1
    definitions = list(reversed(newest_first))
    for split, definition in enumerate(definitions, start=1):
        definition["split"] = split
    return definitions, holdout


def fit_model(
    training: pd.DataFrame,
    evaluation: pd.DataFrame,
    features: tuple[str, ...],
    seed: int,
    max_train_rows: int,
) -> tuple[np.ndarray, StandardScaler, MLPClassifier]:
    train = training.dropna(subset=[*features, "target_up", "future_return"]).copy()
    if len(train) > max_train_rows:
        train = train.sample(max_train_rows, random_state=seed).sort_values("date")
    test = evaluation.dropna(subset=[*features, "target_up", "future_return"])
    if train.empty or test.empty or train["target_up"].nunique() < 2:
        raise RuntimeError("A model region is empty or has one-class training labels")
    scaler = StandardScaler()
    train_inputs = scaler.fit_transform(train[list(features)])
    test_inputs = scaler.transform(test[list(features)])
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
    model.fit(train_inputs, train["target_up"].astype(int))
    return model.predict_proba(test_inputs)[:, 1], scaler, model


def daily_metrics(
    evaluation: pd.DataFrame,
    scores: np.ndarray,
    top_k: int,
    cost_bps: float,
) -> pd.DataFrame:
    scored = evaluation.copy()
    scored["score"] = scores
    rows: list[dict[str, object]] = []
    cost = cost_bps / 10_000.0
    for date, day in scored.groupby("date", sort=True):
        labels = day["target_up"].astype(int).to_numpy()
        day_scores = day["score"].to_numpy(dtype=float)
        chosen = day.nlargest(min(top_k, len(day)), "score")
        universe_return = float(day["future_return"].mean())
        net_return = float(chosen["future_return"].mean() - cost)
        rows.append(
            {
                "date": date,
                "auc": (
                    float(roc_auc_score(labels, day_scores))
                    if np.unique(labels).size == 2
                    else np.nan
                ),
                "brier": float(np.mean((day_scores - labels) ** 2)),
                "mean_net_return": net_return,
                "excess_return": net_return - universe_return,
                "win": float(net_return > 0),
            }
        )
    return pd.DataFrame(rows)


def aggregate_metrics(daily: pd.DataFrame) -> dict[str, float]:
    return {
        "auc": float(daily["auc"].mean()),
        "brier": float(daily["brier"].mean()),
        "mean_net_return": float(daily["mean_net_return"].mean()),
        "mean_excess_return": float(daily["excess_return"].mean()),
        "win_rate": float(daily["win"].mean()),
    }


def permute_columns_within_dates(
    inputs: np.ndarray,
    dates: np.ndarray,
    columns: tuple[int, ...],
    seed: int,
) -> np.ndarray:
    permuted = inputs.copy()
    generator = np.random.default_rng(seed)
    for date in np.unique(dates):
        indexes = np.flatnonzero(dates == date)
        if len(indexes) > 1:
            source = generator.permutation(indexes)
            permuted[np.ix_(indexes, columns)] = inputs[np.ix_(source, columns)]
    return permuted


def comparison_rows(
    baseline_daily: pd.DataFrame,
    challenger_daily: pd.DataFrame,
    horizon: int,
    split: int,
    seed: int,
    method: str,
    component: str,
    direction: str,
) -> list[dict[str, object]]:
    merged = baseline_daily.merge(
        challenger_daily, on="date", suffixes=("_baseline", "_challenger")
    )
    rows: list[dict[str, object]] = []
    for row in merged.itertuples(index=False):
        if direction == "permutation":
            auc_delta = row.auc_baseline - row.auc_challenger
            brier_delta = row.brier_challenger - row.brier_baseline
            return_delta = row.excess_return_baseline - row.excess_return_challenger
        else:
            auc_delta = row.auc_challenger - row.auc_baseline
            brier_delta = row.brier_baseline - row.brier_challenger
            return_delta = row.excess_return_challenger - row.excess_return_baseline
        rows.append(
            {
                "horizon": horizon,
                "split": split,
                "seed": seed,
                "date": row.date,
                "week": pd.Timestamp(row.date).to_period("W").start_time,
                "method": method,
                "component": component,
                "auc_delta": auc_delta,
                "brier_improvement": brier_delta,
                "excess_return_delta": return_delta,
            }
        )
    return rows


def bootstrap_intervals(
    comparisons: pd.DataFrame, samples: int
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    metrics = ("auc_delta", "brier_improvement", "excess_return_delta")
    for (horizon, method, component), group in comparisons.groupby(
        ["horizon", "method", "component"], sort=True
    ):
        weekly = group.groupby("week", as_index=False)[list(metrics)].mean()
        generator = np.random.default_rng(
            RANDOM_STATE + int(horizon) + sum(ord(char) for char in component + method)
        )
        for metric in metrics:
            values = weekly[metric].dropna().to_numpy(dtype=float)
            if not len(values):
                mean = lower = upper = np.nan
            else:
                simulated = np.empty(samples, dtype=float)
                for index in range(samples):
                    simulated[index] = generator.choice(
                        values, size=len(values), replace=True
                    ).mean()
                mean = float(values.mean())
                lower, upper = np.quantile(simulated, [0.025, 0.975])
            rows.append(
                {
                    "horizon": horizon,
                    "method": method,
                    "component": component,
                    "metric": metric,
                    "mean": mean,
                    "ci_2_5": float(lower),
                    "ci_97_5": float(upper),
                    "weekly_blocks": int(len(values)),
                    "bootstrap_samples": samples,
                }
            )
    return pd.DataFrame(rows)


def build_registry(
    comparisons: pd.DataFrame, intervals: pd.DataFrame
) -> pd.DataFrame:
    removal = comparisons[comparisons["method"].str.startswith("drop_")]
    if removal.empty:
        return pd.DataFrame()
    summary = removal.groupby(["method", "component"], as_index=False).agg(
        auc_delta=("auc_delta", "mean"),
        brier_improvement=("brier_improvement", "mean"),
        excess_return_delta=("excess_return_delta", "mean"),
        auc_positive_fraction=("auc_delta", lambda values: float((values > 0).mean())),
        excess_positive_fraction=(
            "excess_return_delta", lambda values: float((values > 0).mean())
        ),
    )
    pivot = intervals[intervals["method"].str.startswith("drop_")].pivot_table(
        index=["method", "component"],
        columns="metric",
        values=["ci_2_5", "ci_97_5"],
    )
    pivot.columns = [f"{metric}_{bound}" for bound, metric in pivot.columns]
    pivot = pivot.reset_index()
    registry = summary.merge(pivot, on=["method", "component"], how="left")

    def decide(row: pd.Series) -> tuple[str, str]:
        return_low = row.get("excess_return_delta_ci_2_5", np.nan)
        auc_low = row.get("auc_delta_ci_2_5", np.nan)
        brier_low = row.get("brier_improvement_ci_2_5", np.nan)
        if (
            return_low > 0
            and auc_low >= -0.001
            and brier_low >= -0.001
            and row["excess_positive_fraction"] >= 0.67
        ):
            return "confirmed_drop", "removal improves ranking without material metric harm"
        if row["excess_return_delta"] < 0 and row["auc_delta"] < 0:
            return "retain", "removal harms both ranking and discrimination on average"
        return "watch", "confidence interval or metric directions remain mixed"

    decisions = registry.apply(decide, axis=1, result_type="expand")
    registry["decision"] = decisions[0]
    registry["reason"] = decisions[1]
    return registry.sort_values(
        ["decision", "excess_return_delta", "auc_delta"],
        ascending=[True, False, False],
    )


def write_readout(
    path: Path,
    registry: pd.DataFrame,
    intervals: pd.DataFrame,
    audits: pd.DataFrame,
    holdout_manifest: dict[str, object],
) -> None:
    interval_view = intervals[
        intervals["metric"].isin(("excess_return_delta", "auc_delta"))
    ].copy()
    lines = [
        "# Confirmatory ANN Importance Lab",
        "",
        "## Decision status",
        "",
        "Paper-only. No live model, probability display, portfolio, or brokerage order was modified.",
        "Positive permutation delta means the feature helped the full model. Positive drop delta means removing it helped.",
        "",
        "## Sealed future holdout",
        "",
        f"Dates: {holdout_manifest['date_start']} through {holdout_manifest['date_end']}",
        f"Trading dates: {holdout_manifest['trading_dates']}",
        "Status: sealed; excluded from training, graph fitting, feature selection, scoring, and metrics.",
        "",
        "## Confirmatory registry",
        "",
        registry.round(6).to_string(index=False) if not registry.empty else "Confirmatory retraining was not requested in this stage.",
        "",
        "## Weekly block-bootstrap intervals",
        "",
        interval_view.round(6).to_string(index=False),
        "",
        "## Leakage audit",
        "",
        audits.to_string(index=False),
        "",
        "A feature should only change the paper challenger after a confirmed result survives a newly opened holdout or a later untouched window.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    horizons = tuple(int(value) for value in args.horizons.split(",") if value.strip())
    candidates = tuple(
        value.strip() for value in args.drop_candidates.split(",") if value.strip()
    )
    invalid = sorted(set(candidates) - set(MODEL_FEATURES))
    if invalid:
        raise ValueError(f"Unknown drop candidates: {', '.join(invalid)}")

    prices, source_table = load_prices(args.db)
    features = build_trailing_features(prices)
    all_dates = sorted(pd.Timestamp(value) for value in features["date"].unique())
    holdout = all_dates[-args.holdout_dates :]
    holdout_manifest = {
        "schema_version": 1,
        "status": "sealed",
        "date_start": holdout[0].date().isoformat(),
        "date_end": holdout[-1].date().isoformat(),
        "trading_dates": len(holdout),
        "excluded_from_training": True,
        "excluded_from_graph_fitting": True,
        "excluded_from_feature_selection": True,
        "excluded_from_scoring_and_metrics": True,
        "opened_for_evaluation": False,
    }
    (args.output_dir / "sealed_holdout_manifest.json").write_text(
        json.dumps(holdout_manifest, indent=2), encoding="utf-8"
    )

    metric_rows: list[dict[str, object]] = []
    comparison_data: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []
    correlation_rows: list[dict[str, object]] = []

    for horizon in horizons:
        definitions, horizon_holdout = chronological_splits(
            all_dates,
            horizon,
            args.holdout_dates,
            args.test_dates,
            args.splits,
            args.minimum_training_dates,
        )
        if horizon_holdout != holdout:
            raise RuntimeError("Holdout definitions disagree across horizons")
        labelled = add_target(features, horizon)
        for definition in definitions:
            split = int(definition["split"])
            cutoff = pd.Timestamp(definition["training_cutoff"])
            test_start = pd.Timestamp(definition["test_start"])
            test_end = pd.Timestamp(definition["test_end"])
            edges = select_graph_neighbors(
                labelled,
                cutoff,
                args.max_tickers,
                args.graph_lookback_days,
                args.graph_neighbors,
            )
            graph_frame = add_graph_context(
                labelled[labelled["date"] <= test_end], edges
            )
            training = graph_frame[graph_frame["date"] <= cutoff].dropna(
                subset=[*MODEL_FEATURES, "target_up", "future_return"]
            )
            evaluation = graph_frame[
                (graph_frame["date"] >= test_start)
                & (graph_frame["date"] <= test_end)
            ].dropna(subset=[*MODEL_FEATURES, "target_up", "future_return"])
            if training.empty or evaluation.empty:
                raise RuntimeError(f"Empty horizon={horizon} split={split} region")

            correlations = training[list(MODEL_FEATURES)].corr().abs()
            for left_index, left in enumerate(MODEL_FEATURES):
                for right in MODEL_FEATURES[left_index + 1 :]:
                    correlation_rows.append(
                        {
                            "horizon": horizon,
                            "split": split,
                            "feature_left": left,
                            "feature_right": right,
                            "absolute_correlation": correlations.loc[left, right],
                        }
                    )

            audit_rows.append(
                {
                    "horizon": horizon,
                    "split": split,
                    "training_and_graph_cutoff": cutoff.date().isoformat(),
                    "test_start": test_start.date().isoformat(),
                    "test_end": test_end.date().isoformat(),
                    "embargo_trading_days": horizon,
                    "sealed_holdout_start": holdout[0].date().isoformat(),
                    "graph_uses_test_or_future_rows": False,
                    "holdout_used": False,
                    "train_rows": len(training),
                    "test_rows": len(evaluation),
                    "tickers": evaluation["ticker"].nunique(),
                }
            )

            for seed_offset in range(args.seeds):
                seed = RANDOM_STATE + seed_offset
                baseline_probabilities, scaler, model = fit_model(
                    training,
                    evaluation,
                    MODEL_FEATURES,
                    seed,
                    args.max_train_rows,
                )
                baseline_daily = daily_metrics(
                    evaluation,
                    baseline_probabilities,
                    args.top_k,
                    args.cost_bps,
                )
                metric_rows.append(
                    {
                        "horizon": horizon,
                        "split": split,
                        "seed": seed,
                        "method": "baseline",
                        "component": "all_features",
                        **aggregate_metrics(baseline_daily),
                    }
                )

                if args.stage in ("screen", "all"):
                    evaluation_inputs = scaler.transform(
                        evaluation[list(MODEL_FEATURES)]
                    )
                    evaluation_dates = evaluation["date"].to_numpy()
                    permutation_sets = {
                        **{f"feature:{name}": (name,) for name in MODEL_FEATURES},
                        **{
                            f"group:{name}": tuple(group_features)
                            for name, group_features in FEATURE_GROUPS.items()
                        },
                    }
                    for component_number, (component, columns) in enumerate(
                        permutation_sets.items()
                    ):
                        indexes = tuple(MODEL_FEATURES.index(name) for name in columns)
                        permuted_inputs = permute_columns_within_dates(
                            evaluation_inputs,
                            evaluation_dates,
                            indexes,
                            seed * 10_000 + component_number,
                        )
                        probabilities = model.predict_proba(permuted_inputs)[:, 1]
                        daily = daily_metrics(
                            evaluation, probabilities, args.top_k, args.cost_bps
                        )
                        metric_rows.append(
                            {
                                "horizon": horizon,
                                "split": split,
                                "seed": seed,
                                "method": "permutation",
                                "component": component,
                                **aggregate_metrics(daily),
                            }
                        )
                        comparison_data.extend(
                            comparison_rows(
                                baseline_daily,
                                daily,
                                horizon,
                                split,
                                seed,
                                "permutation",
                                component,
                                "permutation",
                            )
                        )

                if args.stage in ("confirm", "all"):
                    drop_sets = {
                        **{f"feature:{name}": (name,) for name in candidates},
                        **{
                            f"group:{name}": tuple(group_features)
                            for name, group_features in FEATURE_GROUPS.items()
                        },
                    }
                    for component, removed in drop_sets.items():
                        retained = tuple(
                            name for name in MODEL_FEATURES if name not in removed
                        )
                        probabilities, _, _ = fit_model(
                            training,
                            evaluation,
                            retained,
                            seed,
                            args.max_train_rows,
                        )
                        daily = daily_metrics(
                            evaluation, probabilities, args.top_k, args.cost_bps
                        )
                        method = (
                            "drop_feature" if component.startswith("feature:") else "drop_group"
                        )
                        metric_rows.append(
                            {
                                "horizon": horizon,
                                "split": split,
                                "seed": seed,
                                "method": method,
                                "component": component,
                                **aggregate_metrics(daily),
                            }
                        )
                        comparison_data.extend(
                            comparison_rows(
                                baseline_daily,
                                daily,
                                horizon,
                                split,
                                seed,
                                method,
                                component,
                                "removal",
                            )
                        )

    metrics = pd.DataFrame(metric_rows)
    comparisons = pd.DataFrame(comparison_data)
    audits = pd.DataFrame(audit_rows)
    correlations = pd.DataFrame(correlation_rows)
    intervals = bootstrap_intervals(comparisons, args.bootstrap_samples)
    registry = build_registry(comparisons, intervals)

    metrics.to_csv(args.output_dir / "model_metrics_by_run.csv", index=False)
    comparisons.to_csv(args.output_dir / "paired_daily_importance.csv", index=False)
    intervals.to_csv(args.output_dir / "weekly_block_bootstrap_ci.csv", index=False)
    registry.to_csv(args.output_dir / "confirmatory_feature_registry.csv", index=False)
    audits.to_csv(args.output_dir / "leakage_audit.csv", index=False)
    correlations.to_csv(args.output_dir / "feature_correlations.csv", index=False)
    write_readout(
        args.output_dir / "confirmatory_importance_readout.md",
        registry,
        intervals,
        audits,
        holdout_manifest,
    )

    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": str(args.db),
        "source_table": source_table,
        "rows_loaded": len(prices),
        "tickers_loaded": int(prices["ticker"].nunique()),
        "date_min": prices["date"].min().date().isoformat(),
        "date_max": prices["date"].max().date().isoformat(),
        "architecture": "direction_relu_graph",
        "hidden_layers": [48, 24],
        "activation": "relu",
        "loss": "log_loss",
        "horizons": horizons,
        "seeds": args.seeds,
        "stage": args.stage,
        "feature_groups": FEATURE_GROUPS,
        "drop_candidates": candidates,
        "holdout": holdout_manifest,
        "probability_enabled": False,
        "live_trading_enabled": False,
        "paper_only": True,
    }
    (args.output_dir / "experiment_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    if not registry.empty:
        print(registry.to_string(index=False))
    print(f"\nOutputs written to {args.output_dir}")


if __name__ == "__main__":
    main()
