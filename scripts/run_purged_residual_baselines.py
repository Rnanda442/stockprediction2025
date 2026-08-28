#!/usr/bin/env python3
"""Run purged walk-forward baselines on the frozen residual-return panel."""

from __future__ import annotations

import argparse
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


EXPERIMENT_ID = "purged_walk_forward_residual_baselines_v1"
DESIGN_SIGNATURE = (
    "purged-walk-forward-residual-baselines-v1:"
    "h5:4x63d:purge5:top50:cost10:ridge-logit-mlprelu-mlptanh"
)
LINEAR_MODELS = ("ridge_residual", "logistic_top_decile")
ANN_MODELS = ("mlp_relu_top_decile", "mlp_tanh_top_decile")
PLACEBOS = ("random_top_50", "equal_weight_universe")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True)
    parser.add_argument("--context-gate", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--holdout-start", default="2026-05-29")
    parser.add_argument("--splits", type=int, default=4)
    parser.add_argument("--test-dates", type=int, default=63)
    parser.add_argument("--minimum-training-dates", type=int, default=504)
    parser.add_argument("--maximum-training-rows", type=int, default=300000)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--permutation-max-rows", type=int, default=50000)
    return parser.parse_args()


def read_json(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def validate_frozen_design(gate: dict, spec: dict, args: argparse.Namespace) -> None:
    if spec.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("Unexpected experiment_id in frozen specification")
    if spec.get("design_signature") != DESIGN_SIGNATURE:
        raise ValueError("Unexpected design_signature in frozen specification")
    evaluation = spec["evaluation"]
    expected = {
        "holdout_start": (args.holdout_start, evaluation["sealed_holdout_start"]),
        "splits": (args.splits, evaluation["walk_forward_splits"]),
        "test_dates": (args.test_dates, evaluation["test_dates_per_split"]),
        "minimum_training_dates": (
            args.minimum_training_dates,
            evaluation["minimum_training_dates"],
        ),
        "maximum_training_rows": (
            args.maximum_training_rows,
            evaluation["maximum_training_rows"],
        ),
        "top_k": (args.top_k, evaluation["top_k"]),
        "cost_bps": (args.cost_bps, evaluation["primary_cost_bps"]),
        "bootstrap_samples": (
            args.bootstrap_samples,
            evaluation["bootstrap_samples"],
        ),
    }
    for name, (actual, frozen) in expected.items():
        if actual != frozen:
            raise ValueError(f"{name}={actual} differs from frozen value {frozen}")

    registered = False
    for entry in gate.get("next_experiments", []):
        entry_id = entry.get("experiment_id", entry.get("id"))
        if entry_id == EXPERIMENT_ID:
            if entry.get("design_signature") != DESIGN_SIGNATURE:
                raise ValueError("Context-gate design signature mismatch")
            if entry.get("status") not in {"approved_next", "approved_after_dependency"}:
                raise ValueError("Context gate has not approved this experiment")
            registered = True
            break
    if not registered:
        raise ValueError("Experiment is not registered in context_gate.json")


def load_panel(
    path: str | Path,
    features: list[str],
    holdout_start: pd.Timestamp,
) -> tuple[pd.DataFrame, dict]:
    columns = [
        "ticker",
        "date",
        "evaluation_date",
        *features,
        "primary_residual_return",
        "primary_residual_top_decile",
    ]
    frame = pd.read_csv(path, usecols=columns)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["evaluation_date"] = pd.to_datetime(
        frame["evaluation_date"], errors="coerce"
    )
    numeric = [
        *features,
        "primary_residual_return",
        "primary_residual_top_decile",
    ]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=columns)
    frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
    frame = frame[frame["ticker"] != ""].copy()
    frame = frame.sort_values(["date", "ticker"]).reset_index(drop=True)

    duplicate_rows = int(frame.duplicated(["ticker", "date"]).sum())
    decision_violations = int((frame["date"] >= holdout_start).sum())
    evaluation_violations = int((frame["evaluation_date"] >= holdout_start).sum())
    if duplicate_rows:
        raise RuntimeError("Duplicate ticker-date rows found in target panel")
    if decision_violations or evaluation_violations:
        raise RuntimeError("Sealed-holdout boundary violation in target panel")
    summary = {
        "rows": int(len(frame)),
        "dates": int(frame["date"].nunique()),
        "tickers": int(frame["ticker"].nunique()),
        "decision_min": frame["date"].min().date().isoformat(),
        "decision_max": frame["date"].max().date().isoformat(),
        "evaluation_max": frame["evaluation_date"].max().date().isoformat(),
        "duplicate_ticker_dates": duplicate_rows,
        "decision_holdout_violations": decision_violations,
        "evaluation_holdout_violations": evaluation_violations,
    }
    return frame, summary


def build_splits(
    frame: pd.DataFrame,
    split_count: int,
    test_dates: int,
    minimum_training_dates: int,
) -> list[dict[str, object]]:
    dates = [pd.Timestamp(value) for value in sorted(frame["date"].unique())]
    first_test_index = len(dates) - split_count * test_dates
    if first_test_index < minimum_training_dates:
        raise RuntimeError("Insufficient dates for the frozen walk-forward design")
    definitions = []
    for split in range(split_count):
        start = first_test_index + split * test_dates
        test_block = dates[start : start + test_dates]
        test_start = test_block[0]
        eligible_training = frame[frame["evaluation_date"] < test_start]
        training_dates = sorted(eligible_training["date"].unique())
        if len(training_dates) < minimum_training_dates:
            raise RuntimeError(f"Split {split + 1} lacks minimum training history")
        definitions.append(
            {
                "split": split + 1,
                "test_start": test_start,
                "test_end": test_block[-1],
                "test_dates": test_block,
                "train_decision_max": pd.Timestamp(training_dates[-1]),
                "train_evaluation_max": eligible_training["evaluation_date"].max(),
                "training_dates": len(training_dates),
            }
        )
    return definitions


def sample_training(frame: pd.DataFrame, limit: int, seed: int) -> pd.DataFrame:
    if len(frame) <= limit:
        return frame
    return frame.sample(n=limit, random_state=seed).sort_values(["date", "ticker"])


def model_definitions(spec: dict) -> list[dict[str, object]]:
    return list(spec["models"])


def build_model(definition: dict[str, object], seed: int):
    name = str(definition["name"])
    if name == "ridge_residual":
        return make_pipeline(
            StandardScaler(),
            Ridge(alpha=float(definition["alpha"])),
        )
    if name == "logistic_top_decile":
        return make_pipeline(
            StandardScaler(),
            LogisticRegression(
                C=float(definition["C"]),
                max_iter=400,
                solver="lbfgs",
                random_state=seed,
            ),
        )
    if name in ANN_MODELS:
        return make_pipeline(
            StandardScaler(),
            MLPClassifier(
                hidden_layer_sizes=tuple(definition["hidden_layers"]),
                activation=str(definition["activation"]),
                solver="adam",
                alpha=0.001,
                batch_size=512,
                learning_rate_init=0.001,
                max_iter=60,
                early_stopping=True,
                validation_fraction=0.1,
                n_iter_no_change=6,
                random_state=seed,
            ),
        )
    raise ValueError(f"Unknown model: {name}")


def predict_score(model, name: str, inputs: pd.DataFrame) -> np.ndarray:
    if name == "ridge_residual":
        return np.asarray(model.predict(inputs), dtype=float)
    return np.asarray(model.predict_proba(inputs)[:, 1], dtype=float)


def safe_auc(labels: pd.Series | np.ndarray, scores: np.ndarray) -> float:
    labels_array = np.asarray(labels)
    if np.unique(labels_array).size < 2:
        return np.nan
    return float(roc_auc_score(labels_array, scores))


def rank_ic(scores: np.ndarray, returns: pd.Series | np.ndarray) -> float:
    score_series = pd.Series(scores)
    return_series = pd.Series(np.asarray(returns))
    if score_series.nunique() < 2 or return_series.nunique() < 2:
        return np.nan
    return float(score_series.corr(return_series, method="spearman"))


def portfolio_daily_metrics(
    evaluation: pd.DataFrame,
    scores: np.ndarray,
    model_name: str,
    split: int,
    seed: int,
    top_k: int,
    cost_bps: float,
    probability: bool,
) -> pd.DataFrame:
    scored = evaluation[
        [
            "date",
            "ticker",
            "primary_residual_return",
            "primary_residual_top_decile",
        ]
    ].copy()
    scored["score"] = scores
    rows = []
    previous_tickers: set[str] = set()
    for date, day in scored.groupby("date", sort=True):
        selected = day.nlargest(min(top_k, len(day)), "score")
        selected_tickers = set(selected["ticker"])
        turnover = (
            1.0
            if not previous_tickers
            else 1.0
            - len(previous_tickers & selected_tickers)
            / max(1, min(len(previous_tickers), len(selected_tickers)))
        )
        gross = float(selected["primary_residual_return"].mean())
        universe = float(day["primary_residual_return"].mean())
        cost = turnover * cost_bps / 10000.0
        labels = day["primary_residual_top_decile"].astype(int)
        brier = (
            float(brier_score_loss(labels, day["score"]))
            if probability
            else np.nan
        )
        rows.append(
            {
                "model": model_name,
                "split": split,
                "seed": seed,
                "date": pd.Timestamp(date),
                "observations": len(day),
                "selected": len(selected),
                "auc": safe_auc(labels, day["score"].to_numpy()),
                "rank_ic": rank_ic(
                    day["score"].to_numpy(), day["primary_residual_return"]
                ),
                "brier": brier,
                "gross_residual_return": gross,
                "universe_residual_return": universe,
                "excess_vs_universe": gross - universe,
                "turnover": turnover,
                "cost": cost,
                "net_residual_return": gross - cost,
                "win": float(gross - cost > 0),
            }
        )
        previous_tickers = selected_tickers
    return pd.DataFrame(rows)


def equal_weight_daily(
    evaluation: pd.DataFrame,
    split: int,
) -> pd.DataFrame:
    rows = []
    for date, day in evaluation.groupby("date", sort=True):
        residual = float(day["primary_residual_return"].mean())
        rows.append(
            {
                "model": "equal_weight_universe",
                "split": split,
                "seed": 0,
                "date": pd.Timestamp(date),
                "observations": len(day),
                "selected": len(day),
                "auc": 0.5,
                "rank_ic": 0.0,
                "brier": np.nan,
                "gross_residual_return": residual,
                "universe_residual_return": residual,
                "excess_vs_universe": 0.0,
                "turnover": 0.0,
                "cost": 0.0,
                "net_residual_return": residual,
                "win": float(residual > 0),
            }
        )
    return pd.DataFrame(rows)


def aggregate_run_metrics(daily: pd.DataFrame) -> dict[str, float]:
    return {
        "mean_auc": float(daily["auc"].mean()),
        "mean_rank_ic": float(daily["rank_ic"].mean()),
        "mean_brier": float(daily["brier"].mean())
        if daily["brier"].notna().any()
        else np.nan,
        "mean_gross_residual_return": float(
            daily["gross_residual_return"].mean()
        ),
        "mean_net_residual_return": float(daily["net_residual_return"].mean()),
        "mean_excess_vs_universe": float(daily["excess_vs_universe"].mean()),
        "mean_turnover": float(daily["turnover"].mean()),
        "win_rate": float(daily["win"].mean()),
    }


def permute_within_dates(
    inputs: pd.DataFrame,
    dates: pd.Series,
    column: str,
    seed: int,
) -> pd.DataFrame:
    permuted = inputs.copy()
    generator = np.random.default_rng(seed)
    date_values = dates.to_numpy()
    column_index = permuted.columns.get_loc(column)
    values = permuted.to_numpy(copy=True)
    for date in np.unique(date_values):
        indexes = np.flatnonzero(date_values == date)
        values[indexes, column_index] = values[
            generator.permutation(indexes), column_index
        ]
    return pd.DataFrame(values, columns=permuted.columns, index=permuted.index)


def fitted_linear_coefficients(
    model,
    name: str,
    features: list[str],
    split: int,
    seed: int,
) -> list[dict[str, object]]:
    if name == "ridge_residual":
        coefficients = model.named_steps["ridge"].coef_
    elif name == "logistic_top_decile":
        coefficients = model.named_steps["logisticregression"].coef_[0]
    else:
        return []
    return [
        {
            "model": name,
            "split": split,
            "seed": seed,
            "method": "standardized_coefficient",
            "feature": feature,
            "importance": float(coefficient),
            "auc_drop": np.nan,
            "rank_ic_drop": np.nan,
            "net_return_drop": np.nan,
        }
        for feature, coefficient in zip(features, coefficients)
    ]


def permutation_importance(
    model,
    name: str,
    evaluation: pd.DataFrame,
    features: list[str],
    split: int,
    seed: int,
    top_k: int,
    cost_bps: float,
    maximum_rows: int,
) -> list[dict[str, object]]:
    if len(evaluation) > maximum_rows:
        sampled = (
            evaluation.groupby("date", group_keys=False)
            .apply(
                lambda group: group.sample(
                    n=max(1, int(maximum_rows / evaluation["date"].nunique())),
                    random_state=seed,
                )
                if len(group)
                > max(1, int(maximum_rows / evaluation["date"].nunique()))
                else group,
                include_groups=False,
            )
            .reset_index(drop=True)
        )
        sampled["date"] = pd.to_datetime(sampled["date"])
    else:
        sampled = evaluation.copy()
    inputs = sampled[features]
    probability = name != "ridge_residual"
    baseline_scores = predict_score(model, name, inputs)
    baseline_auc = safe_auc(
        sampled["primary_residual_top_decile"], baseline_scores
    )
    baseline_ic = rank_ic(baseline_scores, sampled["primary_residual_return"])
    baseline_daily = portfolio_daily_metrics(
        sampled,
        baseline_scores,
        name,
        split,
        seed,
        top_k,
        cost_bps,
        probability,
    )
    baseline_net = float(baseline_daily["net_residual_return"].mean())
    rows = []
    for number, feature in enumerate(features):
        permuted = permute_within_dates(
            inputs,
            sampled["date"],
            feature,
            seed * 1000 + split * 100 + number,
        )
        scores = predict_score(model, name, permuted)
        permuted_daily = portfolio_daily_metrics(
            sampled,
            scores,
            name,
            split,
            seed,
            top_k,
            cost_bps,
            probability,
        )
        rows.append(
            {
                "model": name,
                "split": split,
                "seed": seed,
                "method": "within_date_permutation",
                "feature": feature,
                "importance": np.nan,
                "auc_drop": baseline_auc
                - safe_auc(sampled["primary_residual_top_decile"], scores),
                "rank_ic_drop": baseline_ic
                - rank_ic(scores, sampled["primary_residual_return"]),
                "net_return_drop": baseline_net
                - float(permuted_daily["net_residual_return"].mean()),
            }
        )
    return rows


def bootstrap_interval(
    values: np.ndarray,
    samples: int,
    seed: int,
) -> tuple[float, float, float]:
    values = values[np.isfinite(values)]
    if not len(values):
        return np.nan, np.nan, np.nan
    generator = np.random.default_rng(seed)
    simulated = np.empty(samples, dtype=float)
    for index in range(samples):
        simulated[index] = generator.choice(
            values, size=len(values), replace=True
        ).mean()
    lower, upper = np.quantile(simulated, [0.025, 0.975])
    return float(values.mean()), float(lower), float(upper)


def absolute_bootstrap(
    daily: pd.DataFrame,
    samples: int,
) -> pd.DataFrame:
    rows = []
    metrics = ("net_residual_return", "auc", "rank_ic")
    averaged = daily.groupby(["model", "date"], as_index=False)[list(metrics)].mean()
    averaged["week"] = averaged["date"].dt.to_period("W").dt.start_time
    for model, model_rows in averaged.groupby("model"):
        weekly = model_rows.groupby("week", as_index=False)[list(metrics)].mean()
        for metric_number, metric in enumerate(metrics):
            mean, lower, upper = bootstrap_interval(
                weekly[metric].to_numpy(dtype=float),
                samples,
                17000 + metric_number + sum(ord(char) for char in model),
            )
            rows.append(
                {
                    "model": model,
                    "comparison": "absolute",
                    "metric": metric,
                    "mean": mean,
                    "ci_2_5": lower,
                    "ci_97_5": upper,
                    "weekly_blocks": len(weekly),
                    "bootstrap_samples": samples,
                }
            )
    return pd.DataFrame(rows)


def paired_comparisons(
    daily: pd.DataFrame,
    best_linear: str,
    samples: int,
) -> pd.DataFrame:
    metrics = ("net_residual_return", "auc", "rank_ic")
    averaged = daily.groupby(["model", "date"], as_index=False)[list(metrics)].mean()
    baseline = averaged[averaged["model"] == best_linear].drop(columns="model")
    rows = []
    for model in ANN_MODELS:
        challenger = averaged[averaged["model"] == model].drop(columns="model")
        merged = challenger.merge(
            baseline, on="date", suffixes=("_challenger", "_baseline")
        )
        merged["week"] = merged["date"].dt.to_period("W").dt.start_time
        for metric_number, metric in enumerate(metrics):
            merged["delta"] = (
                merged[f"{metric}_challenger"] - merged[f"{metric}_baseline"]
            )
            weekly = merged.groupby("week", as_index=False)["delta"].mean()
            mean, lower, upper = bootstrap_interval(
                weekly["delta"].to_numpy(dtype=float),
                samples,
                29000 + metric_number + sum(ord(char) for char in model),
            )
            rows.append(
                {
                    "model": model,
                    "baseline": best_linear,
                    "metric": metric,
                    "mean_delta": mean,
                    "ci_2_5": lower,
                    "ci_97_5": upper,
                    "weekly_blocks": len(weekly),
                    "bootstrap_samples": samples,
                }
            )
    return pd.DataFrame(rows)


def advantage_decisions(
    intervals: pd.DataFrame,
    comparisons: pd.DataFrame,
    spec: dict,
) -> list[dict[str, object]]:
    gate = spec["advantage_gate"]
    decisions = []
    for model in (*LINEAR_MODELS, *ANN_MODELS):
        model_intervals = intervals[intervals["model"] == model].set_index("metric")
        net_lower = float(model_intervals.loc["net_residual_return", "ci_2_5"])
        auc_lower = float(model_intervals.loc["auc", "ci_2_5"])
        ic_lower = float(model_intervals.loc["rank_ic", "ci_2_5"])
        absolute_pass = (
            net_lower > gate["after_cost_residual_return_ci_lower_gt"]
            and auc_lower > gate["auc_ci_lower_gt"]
            and ic_lower > gate["rank_ic_ci_lower_gt"]
        )
        paired_pass = True
        paired_net_lower = np.nan
        paired_auc_lower = np.nan
        if model in ANN_MODELS:
            paired = comparisons[comparisons["model"] == model].set_index("metric")
            paired_net_lower = float(
                paired.loc["net_residual_return", "ci_2_5"]
            )
            paired_auc_lower = float(paired.loc["auc", "ci_2_5"])
            paired_pass = (
                paired_net_lower
                > gate["ann_paired_net_delta_vs_best_linear_ci_lower_gt"]
                and paired_auc_lower
                >= gate["ann_paired_auc_delta_vs_best_linear_ci_lower_gte"]
            )
        decisions.append(
            {
                "model": model,
                "absolute_gate_pass": bool(absolute_pass),
                "paired_ann_gate_pass": bool(paired_pass),
                "advantage_confirmed": bool(absolute_pass and paired_pass),
                "net_return_ci_lower": net_lower,
                "auc_ci_lower": auc_lower,
                "rank_ic_ci_lower": ic_lower,
                "paired_net_delta_ci_lower": paired_net_lower,
                "paired_auc_delta_ci_lower": paired_auc_lower,
            }
        )
    return decisions


def write_readout(
    path: Path,
    overall: pd.DataFrame,
    intervals: pd.DataFrame,
    comparisons: pd.DataFrame,
    decisions: list[dict[str, object]],
    source: dict,
    best_linear: str,
) -> None:
    decision_frame = pd.DataFrame(decisions)
    lines = [
        "# Purged Walk-Forward Residual Baselines v1",
        "",
        "## Decision status",
        "",
        "Research-only. The sealed holdout remained unopened and no trading system was changed.",
        "",
        f"Best simple control by mean after-cost residual return: {best_linear}",
        "",
        "## Source panel",
        "",
        json.dumps(source, indent=2, sort_keys=True),
        "",
        "## Overall metrics",
        "",
        overall.round(6).to_string(index=False),
        "",
        "## Weekly block-bootstrap intervals",
        "",
        intervals.round(6).to_string(index=False),
        "",
        "## ANN paired comparisons against the best linear control",
        "",
        comparisons.round(6).to_string(index=False),
        "",
        "## Advantage gate",
        "",
        decision_frame.round(6).to_string(index=False),
        "",
        "A more complex architecture is advantageous only when its paired after-cost evidence survives uncertainty.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    spec = read_json(args.spec)
    gate = read_json(args.context_gate)
    validate_frozen_design(gate, spec, args)
    features = list(spec["input"]["features"])
    holdout_start = pd.Timestamp(args.holdout_start)
    panel, source_summary = load_panel(args.panel, features, holdout_start)
    splits = build_splits(
        panel,
        args.splits,
        args.test_dates,
        args.minimum_training_dates,
    )

    daily_frames = []
    split_metric_rows = []
    importance_rows = []
    audit_rows = []
    definitions = model_definitions(spec)
    warnings.filterwarnings("ignore", category=ConvergenceWarning)

    for definition in splits:
        split = int(definition["split"])
        test_start = pd.Timestamp(definition["test_start"])
        test_end = pd.Timestamp(definition["test_end"])
        training_full = panel[panel["evaluation_date"] < test_start]
        training = sample_training(
            training_full,
            args.maximum_training_rows,
            17 + split,
        )
        evaluation = panel[
            (panel["date"] >= test_start) & (panel["date"] <= test_end)
        ].copy()
        if training.empty or evaluation.empty:
            raise RuntimeError(f"Split {split} has an empty train or test region")
        if training["evaluation_date"].max() >= test_start:
            raise RuntimeError(f"Split {split} purge failure")

        audit_rows.append(
            {
                "split": split,
                "train_rows_before_sampling": len(training_full),
                "train_rows_used": len(training),
                "training_dates": int(training_full["date"].nunique()),
                "train_decision_max": training_full["date"].max(),
                "train_evaluation_max": training_full["evaluation_date"].max(),
                "test_start": test_start,
                "test_end": test_end,
                "test_rows": len(evaluation),
                "test_dates": int(evaluation["date"].nunique()),
                "purge_passed": bool(
                    training_full["evaluation_date"].max() < test_start
                ),
                "sealed_holdout_used": False,
            }
        )

        equal_daily = equal_weight_daily(evaluation, split)
        daily_frames.append(equal_daily)
        split_metric_rows.append(
            {
                "model": "equal_weight_universe",
                "split": split,
                "seed": 0,
                **aggregate_run_metrics(equal_daily),
            }
        )

        for model_definition in definitions:
            model_name = str(model_definition["name"])
            for seed in model_definition["seeds"]:
                seed = int(seed)
                model = build_model(model_definition, seed)
                target = (
                    training["primary_residual_return"]
                    if model_name == "ridge_residual"
                    else training["primary_residual_top_decile"].astype(int)
                )
                model.fit(training[features], target)
                scores = predict_score(model, model_name, evaluation[features])
                probability = model_name != "ridge_residual"
                daily = portfolio_daily_metrics(
                    evaluation,
                    scores,
                    model_name,
                    split,
                    seed,
                    args.top_k,
                    args.cost_bps,
                    probability,
                )
                daily_frames.append(daily)
                split_metric_rows.append(
                    {
                        "model": model_name,
                        "split": split,
                        "seed": seed,
                        **aggregate_run_metrics(daily),
                    }
                )
                importance_rows.extend(
                    fitted_linear_coefficients(
                        model, model_name, features, split, seed
                    )
                )
                importance_rows.extend(
                    permutation_importance(
                        model,
                        model_name,
                        evaluation,
                        features,
                        split,
                        seed,
                        args.top_k,
                        args.cost_bps,
                        args.permutation_max_rows,
                    )
                )

        for seed in (17, 29):
            generator = np.random.default_rng(seed + split * 100)
            random_scores = generator.random(len(evaluation))
            random_daily = portfolio_daily_metrics(
                evaluation,
                random_scores,
                "random_top_50",
                split,
                seed,
                args.top_k,
                args.cost_bps,
                False,
            )
            daily_frames.append(random_daily)
            split_metric_rows.append(
                {
                    "model": "random_top_50",
                    "split": split,
                    "seed": seed,
                    **aggregate_run_metrics(random_daily),
                }
            )

    daily_all = pd.concat(daily_frames, ignore_index=True)
    split_metrics = pd.DataFrame(split_metric_rows)
    feature_importance = pd.DataFrame(importance_rows)
    overall = (
        daily_all.groupby("model", as_index=False)
        .agg(
            mean_auc=("auc", "mean"),
            mean_rank_ic=("rank_ic", "mean"),
            mean_brier=("brier", "mean"),
            mean_gross_residual_return=("gross_residual_return", "mean"),
            mean_net_residual_return=("net_residual_return", "mean"),
            mean_excess_vs_universe=("excess_vs_universe", "mean"),
            mean_turnover=("turnover", "mean"),
            win_rate=("win", "mean"),
        )
        .sort_values("mean_net_residual_return", ascending=False)
    )
    best_linear = (
        overall[overall["model"].isin(LINEAR_MODELS)]
        .sort_values("mean_net_residual_return", ascending=False)
        .iloc[0]["model"]
    )
    intervals = absolute_bootstrap(daily_all, args.bootstrap_samples)
    comparisons = paired_comparisons(
        daily_all, str(best_linear), args.bootstrap_samples
    )
    decisions = advantage_decisions(intervals, comparisons, spec)

    cost_rows = []
    for cost_bps in spec["evaluation"]["cost_sensitivity_bps"]:
        sensitivity = daily_all.copy()
        sensitivity["net_at_cost"] = (
            sensitivity["gross_residual_return"]
            - sensitivity["turnover"] * float(cost_bps) / 10000.0
        )
        grouped = sensitivity.groupby("model", as_index=False).agg(
            mean_net_residual_return=("net_at_cost", "mean"),
            win_rate=("net_at_cost", lambda values: float((values > 0).mean())),
            mean_turnover=("turnover", "mean"),
        )
        grouped["cost_bps"] = cost_bps
        cost_rows.append(grouped)
    cost_sensitivity = pd.concat(cost_rows, ignore_index=True)

    split_metrics.to_csv(output_dir / "split_metrics.csv", index=False)
    daily_all.to_csv(
        output_dir / "daily_portfolio_metrics.csv.gz",
        index=False,
        compression="gzip",
        date_format="%Y-%m-%d",
    )
    cost_sensitivity.to_csv(output_dir / "cost_sensitivity.csv", index=False)
    feature_importance.to_csv(output_dir / "feature_importance.csv", index=False)
    intervals.to_csv(output_dir / "bootstrap_intervals.csv", index=False)
    comparisons.to_csv(output_dir / "model_comparisons.csv", index=False)
    pd.DataFrame(audit_rows).to_csv(
        output_dir / "leakage_audit.csv", index=False, date_format="%Y-%m-%d"
    )
    write_readout(
        output_dir / "residual_baseline_readout.md",
        overall,
        intervals,
        comparisons,
        decisions,
        source_summary,
        str(best_linear),
    )

    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "status": "completed",
        "source": source_summary,
        "walk_forward": {
            "splits": args.splits,
            "test_dates_per_split": args.test_dates,
            "minimum_training_dates": args.minimum_training_dates,
            "maximum_training_rows": args.maximum_training_rows,
            "purge_rule": "training evaluation_date < test_start",
        },
        "portfolio": {
            "top_k": args.top_k,
            "primary_cost_bps": args.cost_bps,
        },
        "best_linear_control": str(best_linear),
        "advantage_decisions": decisions,
        "guardrails": {
            "sealed_holdout_start": args.holdout_start,
            "sealed_holdout_opened": False,
            "brokerage_orders_enabled": False,
            "model_promotion_allowed": False,
            "universe_point_in_time_verified": False,
        },
    }
    write_json(output_dir / "experiment_manifest.json", manifest)
    candidate = {
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "candidate_status": "completed_pending_context_review",
        "promotion": False,
        "best_linear_control": str(best_linear),
        "advantage_decisions": decisions,
        "sealed_holdout_opened": False,
        "next_action": (
            "Promote only confirmed components into a separately frozen "
            "confirmatory experiment; otherwise diagnose feature and target limits."
        ),
    }
    write_json(output_dir / "context_gate_candidate_update.json", candidate)
    print(overall.round(6).to_string(index=False))
    print("\nAdvantage decisions")
    print(pd.DataFrame(decisions).round(6).to_string(index=False))
    print(f"\nOutputs written to {output_dir}")


if __name__ == "__main__":
    main()
