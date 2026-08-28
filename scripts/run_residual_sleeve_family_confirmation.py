#!/usr/bin/env python3
"""Confirm residual ANN evidence with five sleeves, regimes, and family ablations."""

from __future__ import annotations

import argparse
import json
import sqlite3
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import run_purged_residual_baselines as baseline


EXPERIMENT_ID = "residual_sleeve_family_confirmation_v1"
DESIGN_SIGNATURE = (
    "residual-sleeve-family-confirmation-v1:"
    "h5:4x63d:5sleeves:top50:tanh:families:regimes:cost10"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--context-gate", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--holdout-start", default="2026-05-29")
    parser.add_argument("--splits", type=int, default=4)
    parser.add_argument("--test-dates", type=int, default=63)
    parser.add_argument("--minimum-training-dates", type=int, default=504)
    parser.add_argument("--maximum-training-rows", type=int, default=300000)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--sleeves", type=int, default=5)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    return parser.parse_args()


def read_json(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def validate_design(gate: dict, spec: dict, args: argparse.Namespace) -> None:
    if spec.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("Frozen experiment ID mismatch")
    if spec.get("design_signature") != DESIGN_SIGNATURE:
        raise ValueError("Frozen design signature mismatch")
    evaluation = spec["evaluation"]
    frozen = {
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
        "sleeves": (args.sleeves, evaluation["active_sleeves"]),
        "cost_bps": (args.cost_bps, evaluation["primary_one_way_cost_bps"]),
        "bootstrap_samples": (
            args.bootstrap_samples,
            evaluation["bootstrap_samples"],
        ),
    }
    for name, (actual, expected) in frozen.items():
        if actual != expected:
            raise ValueError(f"{name}={actual} differs from frozen value {expected}")
    for entry in gate.get("next_experiments", []):
        if entry.get("experiment_id", entry.get("id")) == EXPERIMENT_ID:
            if entry.get("design_signature") != DESIGN_SIGNATURE:
                raise ValueError("Context-gate signature mismatch")
            if entry.get("status") not in {"approved_next", "approved_after_dependency"}:
                raise ValueError("Experiment is not approved by the context gate")
            return
    raise ValueError("Experiment is not registered in context_gate.json")


def attach_market_context(
    frame: pd.DataFrame,
    regime_features: list[str],
) -> pd.DataFrame:
    grouped = frame.groupby("date", sort=False)
    frame["market_ret20_median"] = grouped["ret_20d"].transform("median")
    frame["market_vol60_median"] = grouped["vol_60d"].transform("median")
    frame["breadth_ret20_positive"] = grouped["ret_20d"].transform(
        lambda values: float((values > 0).mean())
    )
    frame["market_ret20_dispersion"] = grouped["ret_20d"].transform("std")
    if frame[regime_features].isna().any().any():
        raise RuntimeError("As-of market context contains missing values")
    return frame


def feature_configurations(
    families: dict[str, list[str]],
) -> dict[str, list[str]]:
    momentum = families["momentum"]
    volatility = families["volatility"]
    liquidity = families["liquidity"]
    regime = families["regime"]
    all_features = momentum + volatility + liquidity + regime
    return {
        "baseline_momentum": momentum,
        "baseline_plus_volatility": momentum + volatility,
        "baseline_plus_liquidity": momentum + liquidity,
        "baseline_plus_regime": momentum + regime,
        "all_families": all_features,
        "all_minus_momentum": volatility + liquidity + regime,
        "all_minus_volatility": momentum + liquidity + regime,
        "all_minus_liquidity": momentum + volatility + regime,
        "all_minus_regime": momentum + volatility + liquidity,
    }


def build_tanh(seed: int) -> object:
    return make_pipeline(
        StandardScaler(),
        MLPClassifier(
            hidden_layer_sizes=(32, 16),
            activation="tanh",
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


def build_logistic(seed: int) -> object:
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=0.5,
            max_iter=400,
            solver="lbfgs",
            random_state=seed,
        ),
    )


def regime_labels(
    training: pd.DataFrame,
    evaluation: pd.DataFrame,
) -> tuple[pd.Series, dict[str, float]]:
    training_dates = training.groupby("date", as_index=False).first()
    evaluation_dates = evaluation.groupby("date", as_index=False).first()
    volatility_cutoff = float(training_dates["market_vol60_median"].quantile(0.75))
    high_breadth = 0.60
    low_breadth = 0.40
    labels = np.select(
        [
            evaluation_dates["market_vol60_median"] > volatility_cutoff,
            evaluation_dates["breadth_ret20_positive"] >= high_breadth,
            evaluation_dates["breadth_ret20_positive"] <= low_breadth,
        ],
        ["stress", "trend_up", "trend_down"],
        default="rotation_quiet",
    )
    mapping = pd.Series(labels, index=evaluation_dates["date"])
    return mapping, {
        "volatility_cutoff": volatility_cutoff,
        "high_breadth": high_breadth,
        "low_breadth": low_breadth,
    }


def shuffled_inputs(
    evaluation: pd.DataFrame,
    all_features: list[str],
    family_features: list[str],
    family: str,
    seed: int,
) -> pd.DataFrame:
    inputs = evaluation[all_features].copy()
    generator = np.random.default_rng(seed)
    if family == "regime":
        date_values = np.array(sorted(evaluation["date"].unique()))
        source_dates = generator.permutation(date_values)
        date_map = dict(zip(date_values, source_dates))
        source = evaluation.groupby("date", as_index=True)[family_features].first()
        for date, indexes in evaluation.groupby("date").groups.items():
            inputs.loc[indexes, family_features] = source.loc[
                date_map[np.datetime64(date)]
            ].to_numpy()
        return inputs
    for _, indexes in evaluation.groupby("date").groups.items():
        indexes_array = np.asarray(list(indexes))
        source = generator.permutation(indexes_array)
        inputs.loc[indexes_array, family_features] = inputs.loc[
            source, family_features
        ].to_numpy()
    return inputs


def scored_rows(
    evaluation: pd.DataFrame,
    scores: np.ndarray,
    model: str,
    split: int,
    seed: int,
    regime_map: pd.Series,
) -> pd.DataFrame:
    result = evaluation[
        [
            "ticker",
            "date",
            "evaluation_date",
            "primary_residual_return",
            "primary_residual_top_decile",
        ]
    ].copy()
    result["score"] = scores
    result["model"] = model
    result["split"] = split
    result["seed"] = seed
    result["regime"] = result["date"].map(regime_map)
    return result


def load_one_day_residuals(
    db_path: str,
    panel: pd.DataFrame,
    start_date: pd.Timestamp,
    holdout_start: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, object]]:
    query = """
        SELECT ticker, begins_at AS date, close_price
        FROM ResearchPrices
        WHERE begins_at >= ? AND begins_at < ?
        ORDER BY ticker, begins_at
    """
    with sqlite3.connect(db_path) as connection:
        prices = pd.read_sql_query(
            query,
            connection,
            params=[
                start_date.strftime("%Y-%m-%d"),
                holdout_start.strftime("%Y-%m-%d"),
            ],
        )
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce").dt.normalize()
    prices["ticker"] = prices["ticker"].astype(str).str.strip().str.upper()
    prices["close_price"] = pd.to_numeric(prices["close_price"], errors="coerce")
    prices = prices.dropna().drop_duplicates(["ticker", "date"], keep="last")
    prices = prices.sort_values(["ticker", "date"])
    grouped = prices.groupby("ticker", sort=False)
    prices["next_date"] = grouped["date"].shift(-1)
    prices["next_close"] = grouped["close_price"].shift(-1)
    prices["one_day_return"] = prices["next_close"] / prices["close_price"] - 1.0
    global_dates = [pd.Timestamp(value) for value in sorted(prices["date"].unique())]
    next_date_map = {
        date: global_dates[index + 1]
        for index, date in enumerate(global_dates[:-1])
    }
    prices["expected_next_date"] = prices["date"].map(next_date_map)
    prices = prices[
        (prices["next_date"] == prices["expected_next_date"])
        & np.isfinite(prices["one_day_return"])
    ][["ticker", "date", "next_date", "one_day_return"]]

    panel_keys = panel[panel["date"] >= start_date][["ticker", "date"]].drop_duplicates()
    returns = panel_keys.merge(prices, on=["ticker", "date"], how="left")
    grouped_returns = returns.groupby("date")["one_day_return"]
    group_sum = grouped_returns.transform("sum")
    group_count = grouped_returns.transform("count")
    returns["market_return_loo_1d"] = (
        group_sum - returns["one_day_return"]
    ) / (group_count - 1).where(group_count > 1)
    returns["residual_return_1d"] = (
        returns["one_day_return"] - returns["market_return_loo_1d"]
    )
    coverage = float(returns["residual_return_1d"].notna().mean())
    return returns, {
        "one_day_rows": int(len(returns)),
        "one_day_residual_coverage": coverage,
        "one_day_start": returns["date"].min().date().isoformat(),
        "one_day_end": returns["date"].max().date().isoformat(),
    }


def five_sleeve_replay(
    predictions: pd.DataFrame,
    one_day_returns: pd.DataFrame,
    model_name: str,
    split: int,
    seed: int,
    top_k: int,
    sleeves: int,
    cost_bps: float,
) -> pd.DataFrame:
    predictions = predictions.sort_values(["date", "score"], ascending=[True, False])
    signal_dates = [pd.Timestamp(value) for value in sorted(predictions["date"].unique())]
    selections = {}
    regimes = {}
    for date, day in predictions.groupby("date", sort=True):
        selected = day.nlargest(min(top_k, len(day)), "score")
        selections[pd.Timestamp(date)] = {
            ticker: 1.0 / (sleeves * len(selected))
            for ticker in selected["ticker"]
        }
        regimes[pd.Timestamp(date)] = str(selected["regime"].iloc[0])

    returns = one_day_returns.set_index(["date", "ticker"])["residual_return_1d"]
    available_dates = [
        pd.Timestamp(value)
        for value in sorted(one_day_returns["date"].dropna().unique())
        if pd.Timestamp(value) >= signal_dates[0]
    ]
    active: list[dict[str, object]] = []
    previous_weights: dict[str, float] = {}
    previous_cash = 1.0
    rows = []
    last_signal = signal_dates[-1]

    for date in available_dates:
        active = [cohort for cohort in active if int(cohort["remaining"]) > 0]
        if date in selections:
            active.append(
                {
                    "remaining": sleeves,
                    "weights": selections[date],
                    "regime": regimes[date],
                }
            )
        weights: dict[str, float] = defaultdict(float)
        for cohort in active:
            for ticker, weight in dict(cohort["weights"]).items():
                weights[ticker] += float(weight)
        invested = float(sum(weights.values()))
        cash = max(0.0, 1.0 - invested)
        tickers = set(previous_weights) | set(weights)
        turnover = 0.5 * (
            sum(
                abs(weights.get(ticker, 0.0) - previous_weights.get(ticker, 0.0))
                for ticker in tickers
            )
            + abs(cash - previous_cash)
        )
        gross = 0.0
        missing_weight = 0.0
        for ticker, weight in weights.items():
            key = (date, ticker)
            value = returns.get(key, np.nan)
            if pd.isna(value):
                missing_weight += weight
            else:
                gross += weight * float(value)
        cost = turnover * cost_bps / 10000.0
        rows.append(
            {
                "model": model_name,
                "split": split,
                "seed": seed,
                "date": date,
                "new_signal": date in selections,
                "new_signal_regime": regimes.get(date, "no_new_signal"),
                "active_sleeves": len(active),
                "invested_weight": invested,
                "positions": len(weights),
                "turnover": turnover,
                "gross_residual_return": gross,
                "cost": cost,
                "net_residual_return": gross - cost,
                "missing_return_weight": missing_weight,
            }
        )
        previous_weights = dict(weights)
        previous_cash = cash
        for cohort in active:
            cohort["remaining"] = int(cohort["remaining"]) - 1
        if date > last_signal and not active:
            break
    return pd.DataFrame(rows)


def sleeve_summary(frame: pd.DataFrame) -> dict[str, float]:
    wealth = (1.0 + frame["net_residual_return"]).cumprod()
    running_high = wealth.cummax()
    drawdown = wealth / running_high - 1.0
    return {
        "days": int(len(frame)),
        "mean_daily_net_residual_return": float(
            frame["net_residual_return"].mean()
        ),
        "cumulative_net_residual_return": float(wealth.iloc[-1] - 1.0),
        "maximum_drawdown": float(drawdown.min()),
        "mean_turnover": float(frame["turnover"].mean()),
        "mean_invested_weight": float(frame["invested_weight"].mean()),
        "maximum_missing_return_weight": float(
            frame["missing_return_weight"].max()
        ),
        "positive_day_rate": float((frame["net_residual_return"] > 0).mean()),
    }


def nonoverlap_phases(
    predictions: pd.DataFrame,
    model_name: str,
    split: int,
    seed: int,
    top_k: int,
    horizon: int,
    cost_bps: float,
) -> pd.DataFrame:
    dates = [pd.Timestamp(value) for value in sorted(predictions["date"].unique())]
    rows = []
    for phase in range(horizon):
        phase_returns = []
        for date in dates[phase::horizon]:
            day = predictions[predictions["date"] == date]
            selected = day.nlargest(min(top_k, len(day)), "score")
            gross = float(selected["primary_residual_return"].mean())
            net = gross - 2.0 * cost_bps / 10000.0
            phase_returns.append(net)
        values = np.asarray(phase_returns, dtype=float)
        rows.append(
            {
                "model": model_name,
                "split": split,
                "seed": seed,
                "phase": phase,
                "cohorts": len(values),
                "mean_net_residual_return": float(values.mean()),
                "win_rate": float((values > 0).mean()),
                "worst_cohort": float(values.min()),
            }
        )
    return pd.DataFrame(rows)


def weekly_paired_interval(
    cohort_metrics: pd.DataFrame,
    challenger: str,
    comparison: str,
    metric: str,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, object]:
    subset = cohort_metrics[
        cohort_metrics["model"].isin([challenger, comparison])
        & (cohort_metrics["seed"] == 17)
    ]
    averaged = subset.groupby(["model", "date"], as_index=False)[metric].mean()
    left = averaged[averaged["model"] == challenger][["date", metric]]
    right = averaged[averaged["model"] == comparison][["date", metric]]
    merged = left.merge(right, on="date", suffixes=("_challenger", "_comparison"))
    merged["delta"] = (
        merged[f"{metric}_challenger"] - merged[f"{metric}_comparison"]
    )
    merged["week"] = merged["date"].dt.to_period("W").dt.start_time
    weekly = merged.groupby("week", as_index=False)["delta"].mean()
    mean, lower, upper = baseline.bootstrap_interval(
        weekly["delta"].to_numpy(dtype=float),
        bootstrap_samples,
        seed,
    )
    return {
        "challenger": challenger,
        "comparison": comparison,
        "metric": metric,
        "mean_delta": mean,
        "ci_2_5": lower,
        "ci_97_5": upper,
        "weekly_blocks": len(weekly),
    }


def family_registry(
    comparisons: pd.DataFrame,
    spec: dict,
) -> pd.DataFrame:
    gate = spec["family_confirmation_gate"]
    rows = []
    definitions = {
        "momentum": [
            ("removal", "all_families", "all_minus_momentum"),
            ("shuffle", "all_families", "all_shuffled_momentum"),
        ],
        "volatility": [
            ("addition", "baseline_plus_volatility", "baseline_momentum"),
            ("removal", "all_families", "all_minus_volatility"),
            ("shuffle", "all_families", "all_shuffled_volatility"),
        ],
        "liquidity": [
            ("addition", "baseline_plus_liquidity", "baseline_momentum"),
            ("removal", "all_families", "all_minus_liquidity"),
            ("shuffle", "all_families", "all_shuffled_liquidity"),
        ],
        "regime": [
            ("addition", "baseline_plus_regime", "baseline_momentum"),
            ("removal", "all_families", "all_minus_regime"),
            ("shuffle", "all_families", "all_shuffled_regime"),
        ],
    }
    for family, tests in definitions.items():
        test_passes = []
        evidence = {}
        for test_name, challenger, comparison in tests:
            rows_for_test = comparisons[
                (comparisons["challenger"] == challenger)
                & (comparisons["comparison"] == comparison)
            ].set_index("metric")
            net_lower = float(rows_for_test.loc["net_residual_return", "ci_2_5"])
            auc_lower = float(rows_for_test.loc["auc", "ci_2_5"])
            net_pass = net_lower > 0.0
            auc_pass = auc_lower >= gate["paired_auc_delta_ci_lower_gte"]
            passed = net_pass and auc_pass
            test_passes.append(passed)
            evidence[f"{test_name}_net_ci_lower"] = net_lower
            evidence[f"{test_name}_auc_ci_lower"] = auc_lower
            evidence[f"{test_name}_pass"] = bool(passed)
        rows.append(
            {
                "family": family,
                "confirmed": bool(all(test_passes)),
                **evidence,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    spec = read_json(args.spec)
    gate = read_json(args.context_gate)
    validate_design(gate, spec, args)
    families = {
        name: list(features)
        for name, features in spec["feature_families"].items()
    }
    base_features = (
        families["momentum"] + families["volatility"] + families["liquidity"]
    )
    holdout_start = pd.Timestamp(args.holdout_start)
    panel, source_summary = baseline.load_panel(
        args.panel, base_features, holdout_start
    )
    panel = attach_market_context(panel, families["regime"])
    configurations = feature_configurations(families)
    all_features = configurations["all_families"]
    splits = baseline.build_splits(
        panel,
        args.splits,
        args.test_dates,
        args.minimum_training_dates,
    )
    one_day, one_day_summary = load_one_day_residuals(
        args.db,
        panel,
        pd.Timestamp(splits[0]["test_start"]),
        holdout_start,
    )

    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    cohort_frames = []
    prediction_frames = []
    sleeve_frames = []
    sleeve_summary_rows = []
    nonoverlap_frames = []
    audit_rows = []
    regime_threshold_rows = []

    for definition in splits:
        split = int(definition["split"])
        test_start = pd.Timestamp(definition["test_start"])
        test_end = pd.Timestamp(definition["test_end"])
        training_full = panel[panel["evaluation_date"] < test_start]
        training = baseline.sample_training(
            training_full, args.maximum_training_rows, 170 + split
        )
        evaluation = panel[
            (panel["date"] >= test_start) & (panel["date"] <= test_end)
        ].copy()
        regime_map, thresholds = regime_labels(training_full, evaluation)
        regime_threshold_rows.append({"split": split, **thresholds})
        if training_full["evaluation_date"].max() >= test_start:
            raise RuntimeError(f"Split {split} purge violation")

        full_models = {}
        for configuration, features in configurations.items():
            model = build_tanh(17)
            model.fit(
                training[features],
                training["primary_residual_top_decile"].astype(int),
            )
            scores = model.predict_proba(evaluation[features])[:, 1]
            model_name = configuration
            scored = scored_rows(
                evaluation, scores, model_name, split, 17, regime_map
            )
            prediction_frames.append(scored)
            daily = baseline.portfolio_daily_metrics(
                evaluation,
                scores,
                model_name,
                split,
                17,
                args.top_k,
                args.cost_bps,
                True,
            )
            daily["regime"] = daily["date"].map(regime_map)
            cohort_frames.append(daily)
            if configuration == "all_families":
                full_models[17] = model

        model_29 = build_tanh(29)
        model_29.fit(
            training[all_features],
            training["primary_residual_top_decile"].astype(int),
        )
        scores_29 = model_29.predict_proba(evaluation[all_features])[:, 1]
        scored_29 = scored_rows(
            evaluation, scores_29, "all_families", split, 29, regime_map
        )
        prediction_frames.append(scored_29)
        daily_29 = baseline.portfolio_daily_metrics(
            evaluation,
            scores_29,
            "all_families",
            split,
            29,
            args.top_k,
            args.cost_bps,
            True,
        )
        daily_29["regime"] = daily_29["date"].map(regime_map)
        cohort_frames.append(daily_29)
        full_models[29] = model_29

        full_model = full_models[17]
        for family, family_features in families.items():
            inputs = shuffled_inputs(
                evaluation,
                all_features,
                family_features,
                family,
                17000 + split,
            )
            scores = full_model.predict_proba(inputs)[:, 1]
            model_name = f"all_shuffled_{family}"
            scored = scored_rows(
                evaluation, scores, model_name, split, 17, regime_map
            )
            prediction_frames.append(scored)
            daily = baseline.portfolio_daily_metrics(
                evaluation,
                scores,
                model_name,
                split,
                17,
                args.top_k,
                args.cost_bps,
                True,
            )
            daily["regime"] = daily["date"].map(regime_map)
            cohort_frames.append(daily)

        logistic = build_logistic(17)
        logistic.fit(
            training[all_features],
            training["primary_residual_top_decile"].astype(int),
        )
        logistic_scores = logistic.predict_proba(evaluation[all_features])[:, 1]
        logistic_scored = scored_rows(
            evaluation,
            logistic_scores,
            "logistic_all_families",
            split,
            17,
            regime_map,
        )
        prediction_frames.append(logistic_scored)
        logistic_daily = baseline.portfolio_daily_metrics(
            evaluation,
            logistic_scores,
            "logistic_all_families",
            split,
            17,
            args.top_k,
            args.cost_bps,
            True,
        )
        logistic_daily["regime"] = logistic_daily["date"].map(regime_map)
        cohort_frames.append(logistic_daily)

        generator = np.random.default_rng(29000 + split)
        random_scores = generator.random(len(evaluation))
        random_scored = scored_rows(
            evaluation,
            random_scores,
            "random_top_50",
            split,
            29,
            regime_map,
        )
        prediction_frames.append(random_scored)
        random_daily = baseline.portfolio_daily_metrics(
            evaluation,
            random_scores,
            "random_top_50",
            split,
            29,
            args.top_k,
            args.cost_bps,
            False,
        )
        random_daily["regime"] = random_daily["date"].map(regime_map)
        cohort_frames.append(random_daily)

        replay_candidates = [
            frame
            for frame in prediction_frames
            if int(frame["split"].iloc[0]) == split
            and frame["model"].iloc[0]
            in {"all_families", "logistic_all_families", "random_top_50"}
        ]
        for scored in replay_candidates:
            model_name = str(scored["model"].iloc[0])
            seed = int(scored["seed"].iloc[0])
            sleeve = five_sleeve_replay(
                scored,
                one_day,
                model_name,
                split,
                seed,
                args.top_k,
                args.sleeves,
                args.cost_bps,
            )
            sleeve_frames.append(sleeve)
            sleeve_summary_rows.append(
                {
                    "model": model_name,
                    "split": split,
                    "seed": seed,
                    **sleeve_summary(sleeve),
                }
            )
            nonoverlap_frames.append(
                nonoverlap_phases(
                    scored,
                    model_name,
                    split,
                    seed,
                    args.top_k,
                    args.sleeves,
                    args.cost_bps,
                )
            )

        audit_rows.append(
            {
                "split": split,
                "train_rows_before_sampling": len(training_full),
                "train_rows_used": len(training),
                "training_dates": int(training_full["date"].nunique()),
                "train_evaluation_max": training_full["evaluation_date"].max(),
                "test_start": test_start,
                "test_end": test_end,
                "test_rows": len(evaluation),
                "purge_passed": bool(
                    training_full["evaluation_date"].max() < test_start
                ),
                "sealed_holdout_used": False,
            }
        )

    cohort_metrics = pd.concat(cohort_frames, ignore_index=True)
    predictions = pd.concat(prediction_frames, ignore_index=True)
    sleeve_daily = pd.concat(sleeve_frames, ignore_index=True)
    sleeve_summaries = pd.DataFrame(sleeve_summary_rows)
    nonoverlap = pd.concat(nonoverlap_frames, ignore_index=True)

    family_summary = (
        cohort_metrics.groupby("model", as_index=False)
        .agg(
            mean_auc=("auc", "mean"),
            mean_rank_ic=("rank_ic", "mean"),
            mean_brier=("brier", "mean"),
            mean_gross_residual_return=("gross_residual_return", "mean"),
            mean_net_residual_return=("net_residual_return", "mean"),
            mean_turnover=("turnover", "mean"),
            win_rate=("win", "mean"),
        )
        .sort_values("mean_net_residual_return", ascending=False)
    )

    comparison_definitions = []
    for family in families:
        if family != "momentum":
            comparison_definitions.append(
                (
                    f"baseline_plus_{family}",
                    "baseline_momentum",
                )
            )
        comparison_definitions.extend(
            [
                ("all_families", f"all_minus_{family}"),
                ("all_families", f"all_shuffled_{family}"),
            ]
        )
    comparison_rows = []
    for number, (challenger, comparison) in enumerate(comparison_definitions):
        for metric_number, metric in enumerate(
            ("net_residual_return", "auc", "rank_ic")
        ):
            comparison_rows.append(
                weekly_paired_interval(
                    cohort_metrics,
                    challenger,
                    comparison,
                    metric,
                    args.bootstrap_samples,
                    44000 + number * 10 + metric_number,
                )
            )
    comparisons = pd.DataFrame(comparison_rows)
    registry = family_registry(comparisons, spec)

    regime_diagnostics = (
        cohort_metrics[
            cohort_metrics["model"].isin(
                ["all_families", "logistic_all_families", "random_top_50"]
            )
        ]
        .groupby(["model", "seed", "regime"], as_index=False)
        .agg(
            dates=("date", "nunique"),
            mean_auc=("auc", "mean"),
            mean_rank_ic=("rank_ic", "mean"),
            mean_net_residual_return=("net_residual_return", "mean"),
            win_rate=("win", "mean"),
        )
    )

    cost_frames = []
    for cost_bps in spec["evaluation"]["cost_sensitivity_bps"]:
        cost_frame = sleeve_daily.copy()
        cost_frame["net_at_cost"] = (
            cost_frame["gross_residual_return"]
            - cost_frame["turnover"] * float(cost_bps) / 10000.0
        )
        summary = cost_frame.groupby("model", as_index=False).agg(
            mean_daily_net_residual_return=("net_at_cost", "mean"),
            cumulative_net_residual_return=(
                "net_at_cost",
                lambda values: float((1.0 + values).prod() - 1.0),
            ),
            mean_turnover=("turnover", "mean"),
            positive_day_rate=(
                "net_at_cost",
                lambda values: float((values > 0).mean()),
            ),
        )
        summary["cost_bps"] = cost_bps
        cost_frames.append(summary)
    cost_sensitivity = pd.concat(cost_frames, ignore_index=True)

    audit = pd.DataFrame(audit_rows)
    audit["one_day_residual_coverage"] = one_day_summary[
        "one_day_residual_coverage"
    ]
    audit["maximum_sleeve_missing_weight"] = float(
        sleeve_daily["missing_return_weight"].max()
    )
    audit["universe_point_in_time_verified"] = False

    cohort_metrics.to_csv(
        output_dir / "cohort_metrics.csv.gz",
        index=False,
        compression="gzip",
        date_format="%Y-%m-%d",
    )
    sleeve_daily.to_csv(
        output_dir / "five_sleeve_daily.csv.gz",
        index=False,
        compression="gzip",
        date_format="%Y-%m-%d",
    )
    sleeve_summaries.to_csv(output_dir / "five_sleeve_summary.csv", index=False)
    nonoverlap.to_csv(output_dir / "nonoverlap_phase_summary.csv", index=False)
    family_summary.to_csv(output_dir / "family_ablation_summary.csv", index=False)
    comparisons.to_csv(output_dir / "family_paired_intervals.csv", index=False)
    registry.to_csv(output_dir / "family_confirmation_registry.csv", index=False)
    regime_diagnostics.to_csv(output_dir / "regime_diagnostics.csv", index=False)
    cost_sensitivity.to_csv(output_dir / "cost_sensitivity.csv", index=False)
    audit.to_csv(output_dir / "leakage_audit.csv", index=False)
    pd.DataFrame(regime_threshold_rows).to_csv(
        output_dir / "regime_thresholds.csv", index=False
    )

    confirmed_families = registry.loc[registry["confirmed"], "family"].tolist()
    site_candidate = {
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "review_status": "pending_human_and_context_review",
        "confirmed_families": confirmed_families,
        "family_registry": registry.to_dict(orient="records"),
        "five_sleeve_summary": sleeve_summaries.to_dict(orient="records"),
        "regime_diagnostics": regime_diagnostics.to_dict(orient="records"),
        "failure_basins": [
            "Any family failing addition, removal, or shuffled paired bounds.",
            "Any regime with fewer than ten observed test dates.",
            "Any sleeve result relying on material missing one-day return weight.",
            "Any result requiring the unopened sealed holdout.",
        ],
        "publish_allowed": False,
        "model_promotion_allowed": False,
    }
    write_json(output_dir / "site_context_candidate.json", site_candidate)

    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "experiment_id": EXPERIMENT_ID,
        "design_signature": DESIGN_SIGNATURE,
        "status": "completed_pending_review",
        "source": source_summary,
        "one_day_returns": one_day_summary,
        "confirmed_families": confirmed_families,
        "guardrails": {
            "sealed_holdout_start": args.holdout_start,
            "sealed_holdout_opened": False,
            "brokerage_orders_enabled": False,
            "model_promotion_allowed": False,
            "universe_point_in_time_verified": False,
        },
    }
    write_json(output_dir / "experiment_manifest.json", manifest)

    lines = [
        "# Residual Sleeve and Family Confirmation v1",
        "",
        "Research-only. The sealed holdout remained unopened.",
        "",
        "## Confirmed feature families",
        "",
        ", ".join(confirmed_families) if confirmed_families else "None",
        "",
        "## Family ablation summary",
        "",
        family_summary.round(6).to_string(index=False),
        "",
        "## Family confirmation registry",
        "",
        registry.round(6).to_string(index=False),
        "",
        "## Five-sleeve summary",
        "",
        sleeve_summaries.round(6).to_string(index=False),
        "",
        "## Non-overlapping phase summary",
        "",
        nonoverlap.groupby(["model", "phase"], as_index=False)
        .agg(
            mean_net_residual_return=("mean_net_residual_return", "mean"),
            win_rate=("win_rate", "mean"),
            worst_cohort=("worst_cohort", "min"),
        )
        .round(6)
        .to_string(index=False),
        "",
        "## Regime diagnostics",
        "",
        regime_diagnostics.round(6).to_string(index=False),
        "",
        "No family or model is promoted by this runner. Reviewed evidence must be written to the canonical context gate first.",
    ]
    (output_dir / "confirmation_readout.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(family_summary.round(6).to_string(index=False))
    print("\nFamily confirmation registry")
    print(registry.round(6).to_string(index=False))
    print("\nFive-sleeve summary")
    print(sleeve_summaries.round(6).to_string(index=False))
    print(f"\nOutputs written to {output_dir}")


if __name__ == "__main__":
    main()
