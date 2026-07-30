import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("MODEL_DB_PATH", ROOT / "vectorized.db"))
ANALYTICS_DIR = Path(os.getenv("MODEL_ANALYTICS_DIR", ROOT / "analytics"))
MODEL_VERSION = os.getenv("MODEL_VERSION", "tournament_v1")
HORIZONS = (5, 20, 60)
FEATURES = (
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
LOOKBACK_DATES = int(os.getenv("MODEL_LOOKBACK_DATES", "756"))
TEST_DATES = int(os.getenv("MODEL_TEST_DATES", "126"))
MAX_TRAIN_ROWS = int(os.getenv("MODEL_MAX_TRAIN_ROWS", "350000"))
MAX_TEST_ROWS = int(os.getenv("MODEL_MAX_TEST_ROWS", "150000"))
RANDOM_SEED = int(os.getenv("MODEL_RANDOM_SEED", "17"))
DEFAULT_CANDIDATES = "sgd_logistic,mlp_ann,hist_gradient_boosting"
SELECTION_THRESHOLD = float(os.getenv("MODEL_SELECTION_THRESHOLD", "0.60"))
WALK_FORWARD_SPLITS = int(os.getenv("MODEL_WALK_FORWARD_SPLITS", "5"))
WALK_FORWARD_TEST_DATES = int(os.getenv("MODEL_WALK_FORWARD_TEST_DATES", "63"))
WALK_FORWARD_MIN_TRAIN_DATES = int(os.getenv("MODEL_WALK_FORWARD_MIN_TRAIN_DATES", "252"))
HISTORY_PREDICTION_LIMIT = int(os.getenv("MODEL_HISTORY_PREDICTION_LIMIT", "100"))


MODEL_LABELS = {
    "sgd_logistic": "SGD logistic baseline",
    "mlp_ann": "ANN MLP classifier",
    "hist_gradient_boosting": "Histogram gradient boosting",
}


def parse_candidates():
    raw = os.getenv("MODEL_CANDIDATES", DEFAULT_CANDIDATES)
    candidates = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    unknown = [candidate for candidate in candidates if candidate not in MODEL_LABELS]
    if unknown:
        raise RuntimeError(f"Unknown model candidates: {', '.join(unknown)}")
    if not candidates:
        raise RuntimeError("MODEL_CANDIDATES did not include any known model")
    return candidates


MODEL_CANDIDATES = parse_candidates()


def table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def build_run_metadata(frame, created_at):
    explicit_run = os.getenv("MODEL_RUN_ID") or os.getenv("GITHUB_RUN_ID")
    run_id = explicit_run or created_at.replace("-", "").replace(":", "").replace("+", "_")
    return {
        "run_id": str(run_id),
        "created_at": created_at,
        "as_of_date": str(frame["begins_at"].max())[:10],
        "model_version": MODEL_VERSION,
        "model_candidates": ",".join(MODEL_CANDIDATES),
        "horizons": ",".join(str(horizon) for horizon in HORIZONS),
        "lookback_dates": LOOKBACK_DATES,
        "test_dates": TEST_DATES,
        "selection_threshold": SELECTION_THRESHOLD,
        "walk_forward_splits_requested": WALK_FORWARD_SPLITS,
        "walk_forward_test_dates": WALK_FORWARD_TEST_DATES,
        "walk_forward_min_train_dates": WALK_FORWARD_MIN_TRAIN_DATES,
        "random_seed": RANDOM_SEED,
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
        "github_sha": os.getenv("GITHUB_SHA", ""),
    }


def load_frame(conn):
    dates = [
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT begins_at
            FROM VectorizedFeatures
            WHERE span='5year'
            ORDER BY begins_at DESC
            LIMIT ?
            """,
            (LOOKBACK_DATES,),
        )
    ]
    if len(dates) < TEST_DATES + max(HORIZONS) + 60:
        raise RuntimeError("Not enough five-year feature dates to build leakage-controlled models")
    cutoff = min(dates)
    features = ", ".join(FEATURES)
    frame = pd.read_sql_query(
        f"""
        SELECT begins_at, ticker, close_price, {features}
        FROM VectorizedFeatures
        WHERE span='5year' AND begins_at >= ?
        ORDER BY begins_at, ticker
        """,
        conn,
        params=(cutoff,),
    )
    frame = frame.sort_values(["ticker", "begins_at"]).reset_index(drop=True)
    frame[list(FEATURES)] = frame[list(FEATURES)].replace([np.inf, -np.inf], np.nan)
    grouped_prices = frame.groupby("ticker", sort=False)["close_price"]
    for horizon in HORIZONS:
        frame[f"future_price_{horizon}d"] = grouped_prices.shift(-horizon)
    return frame


def sample_rows(frame, limit):
    if len(frame) <= limit:
        return frame
    return frame.sample(limit, random_state=RANDOM_SEED)


def model_row_limit(model_name, kind, default):
    env_name = f"MODEL_{model_name.upper()}_MAX_{kind.upper()}_ROWS"
    return int(os.getenv(env_name, str(default)))


def safe_auc(labels, probabilities):
    return float(roc_auc_score(labels, probabilities)) if labels.nunique() > 1 else np.nan


def describe_probability(probability):
    if probability >= 0.70:
        return "strong bullish research signal"
    if probability >= 0.60:
        return "bullish research signal"
    if probability >= 0.55:
        return "modest bullish lean"
    if probability <= 0.40:
        return "bearish/avoid signal"
    return "neutral / watch only"


def feature_driver_text(features, contributions, positive=True, limit=3):
    rows = sorted(zip(features, contributions), key=lambda row: row[1], reverse=positive)
    if positive:
        rows = [row for row in rows if row[1] > 0]
    else:
        rows = [row for row in rows if row[1] < 0]
    return "; ".join(f"{feature} ({value:+.2f})" for feature, value in rows[:limit])


def build_estimator(model_name):
    if model_name == "sgd_logistic":
        balanced_classes = os.getenv("MODEL_BALANCED_CLASSES", "true").lower() not in {
            "0",
            "false",
            "no",
        }
        return make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            SGDClassifier(
                loss="log_loss",
                alpha=float(os.getenv("MODEL_SGD_ALPHA", "0.0005")),
                max_iter=int(os.getenv("MODEL_SGD_MAX_ITER", "1500")),
                class_weight="balanced" if balanced_classes else None,
                random_state=RANDOM_SEED,
                early_stopping=True,
                validation_fraction=0.1,
                n_iter_no_change=10,
            ),
        )
    if model_name == "mlp_ann":
        hidden_layers = tuple(
            int(part)
            for part in os.getenv("MODEL_MLP_HIDDEN_LAYERS", "32,16").split(",")
            if part.strip()
        )
        return make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            MLPClassifier(
                hidden_layer_sizes=hidden_layers,
                alpha=float(os.getenv("MODEL_MLP_ALPHA", "0.0005")),
                learning_rate_init=float(os.getenv("MODEL_MLP_LEARNING_RATE", "0.001")),
                max_iter=int(os.getenv("MODEL_MLP_MAX_ITER", "80")),
                early_stopping=True,
                validation_fraction=0.1,
                n_iter_no_change=8,
                random_state=RANDOM_SEED,
            ),
        )
    if model_name == "hist_gradient_boosting":
        return make_pipeline(
            SimpleImputer(strategy="median"),
            HistGradientBoostingClassifier(
                max_iter=int(os.getenv("MODEL_HGB_MAX_ITER", "120")),
                learning_rate=float(os.getenv("MODEL_HGB_LEARNING_RATE", "0.06")),
                l2_regularization=float(os.getenv("MODEL_HGB_L2", "0.01")),
                max_leaf_nodes=int(os.getenv("MODEL_HGB_MAX_LEAF_NODES", "31")),
                early_stopping=True,
                random_state=RANDOM_SEED,
            ),
        )
    raise RuntimeError(f"Unknown model candidate: {model_name}")


def _pipeline_step_with_attr(model, attr):
    for step in getattr(model, "named_steps", {}).values():
        if hasattr(step, attr):
            return step
    return None


def linear_contributions(model, feature_frame):
    imputer = getattr(model, "named_steps", {}).get("simpleimputer")
    scaler = getattr(model, "named_steps", {}).get("standardscaler")
    classifier = _pipeline_step_with_attr(model, "coef_")
    if imputer is None or scaler is None or classifier is None:
        return None
    standardized = scaler.transform(imputer.transform(feature_frame))
    return standardized * classifier.coef_[0]


def latest_predictions(model, frame, horizon, features, model_name):
    latest = (
        frame.sort_values(["ticker", "begins_at"])
        .groupby("ticker", as_index=False)
        .tail(1)[["begins_at", "ticker", *features]]
        .copy()
    )
    feature_frame = latest[list(features)]
    probabilities = model.predict_proba(feature_frame)[:, 1]
    contributions = linear_contributions(model, feature_frame)

    latest = latest.rename(columns={"begins_at": "as_of_date"})
    latest["model_name"] = model_name
    latest["model_label"] = MODEL_LABELS[model_name]
    latest["model_version"] = MODEL_VERSION
    latest["horizon_days"] = horizon
    latest["probability_up"] = probabilities
    latest["probability_bucket"] = latest["probability_up"].map(describe_probability)
    latest["model_rank"] = latest["probability_up"].rank(method="first", ascending=False).astype(int)
    if contributions is None:
        latest["top_positive_drivers"] = "nonlinear model; driver attribution pending"
        latest["top_negative_drivers"] = "nonlinear model; driver attribution pending"
    else:
        latest["top_positive_drivers"] = [
            feature_driver_text(features, row, positive=True) for row in contributions
        ]
        latest["top_negative_drivers"] = [
            feature_driver_text(features, row, positive=False) for row in contributions
        ]
    return latest[
        [
            "as_of_date",
            "ticker",
            "model_name",
            "model_label",
            "model_version",
            "horizon_days",
            "model_rank",
            "probability_up",
            "probability_bucket",
            "top_positive_drivers",
            "top_negative_drivers",
        ]
    ]


def feature_importance(model, horizon, features, model_name):
    classifier = _pipeline_step_with_attr(model, "coef_")
    if classifier is None:
        return pd.DataFrame()
    importance = pd.DataFrame(
        {
            "horizon_days": horizon,
            "model_name": model_name,
            "model_label": MODEL_LABELS[model_name],
            "model_version": MODEL_VERSION,
            "feature": features,
            "coefficient": classifier.coef_[0],
        }
    )
    importance["absolute_coefficient"] = importance["coefficient"].abs()
    return importance.sort_values("absolute_coefficient", ascending=False)


def champion_score(evaluation):
    if evaluation.get("fit_status") != "ok":
        return -1_000_000_000.0
    score = 0.0
    auc = evaluation.get("roc_auc")
    if not pd.isna(auc):
        score += float(auc) - 0.5
    accuracy_lift = evaluation.get("accuracy_lift")
    if not pd.isna(accuracy_lift):
        score += float(np.clip(accuracy_lift, -0.05, 0.05)) * 0.75
    selected_return_edge = evaluation.get("selected_return_edge")
    if not pd.isna(selected_return_edge):
        score += float(np.clip(selected_return_edge, -0.05, 0.05)) * 1.5
    selected_win_lift = evaluation.get("selected_win_lift")
    if not pd.isna(selected_win_lift):
        score += float(np.clip(selected_win_lift, -0.2, 0.2)) * 0.25
    brier_skill = evaluation.get("brier_skill")
    if not pd.isna(brier_skill):
        score += float(np.clip(brier_skill, -0.2, 0.2)) * 0.25
    return score


def evaluated_metric_block(test, y_test, probabilities, train_positive_rate):
    predictions = (probabilities >= 0.5).astype(int)
    selected = test.loc[probabilities >= SELECTION_THRESHOLD, "forward_return"]
    positive_rate = float(y_test.mean())
    majority_accuracy = max(positive_rate, 1.0 - positive_rate)
    benchmark = float(test["forward_return"].mean())
    selected_average_return = float(selected.mean()) if len(selected) else np.nan
    selected_win_rate = float((selected > 0).mean()) if len(selected) else np.nan
    brier = float(brier_score_loss(y_test, probabilities))
    baseline_brier = float(
        brier_score_loss(y_test, np.full(len(probabilities), train_positive_rate))
    )
    brier_skill = (
        np.nan if baseline_brier <= 0 else float(1.0 - (brier / baseline_brier))
    )
    accuracy = float(accuracy_score(y_test, predictions))
    return {
        "train_positive_rate": train_positive_rate,
        "positive_rate": positive_rate,
        "majority_accuracy": majority_accuracy,
        "accuracy": accuracy,
        "accuracy_lift": accuracy - majority_accuracy,
        "roc_auc": safe_auc(y_test, probabilities),
        "brier_score": brier,
        "baseline_brier_score": baseline_brier,
        "brier_skill": brier_skill,
        "benchmark_average_return": benchmark,
        "selected_threshold": SELECTION_THRESHOLD,
        "selected_rows": len(selected),
        "selected_average_return": selected_average_return,
        "selected_return_edge": selected_average_return - benchmark,
        "selected_win_rate": selected_win_rate,
        "selected_win_lift": selected_win_rate - positive_rate,
    }


def evaluate_model(model, model_name, horizon, train, test, train_dates, test_dates, features):
    x_train = train[list(features)]
    y_train = (train["forward_return"] > 0).astype(int)
    x_test = test[list(features)]
    y_test = (test["forward_return"] > 0).astype(int)

    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_test)[:, 1]
    train_positive_rate = float(y_train.mean())
    evaluation = {
        "horizon_days": horizon,
        "model_name": model_name,
        "model_label": MODEL_LABELS[model_name],
        "model_version": MODEL_VERSION,
        "fit_status": "ok",
        "fit_error": "",
        "training_start": min(train_dates),
        "training_end": max(train_dates),
        "embargo_dates": horizon,
        "test_start": min(test_dates),
        "test_end": max(test_dates),
        "training_rows": len(train),
        "test_rows": len(test),
        "retained_features": len(features),
        "dropped_features": "",
    }
    evaluation.update(evaluated_metric_block(test, y_test, probabilities, train_positive_rate))
    evaluation["champion_score"] = champion_score(evaluation)
    evaluation["holdout_score"] = evaluation["champion_score"]
    return evaluation


def failed_evaluation(model_name, horizon, error, train_dates, test_dates, features, dropped_features):
    evaluation = {
        "horizon_days": horizon,
        "model_name": model_name,
        "model_label": MODEL_LABELS[model_name],
        "model_version": MODEL_VERSION,
        "fit_status": "failed",
        "fit_error": str(error)[:500],
        "training_start": min(train_dates) if train_dates else "",
        "training_end": max(train_dates) if train_dates else "",
        "embargo_dates": horizon,
        "test_start": min(test_dates) if test_dates else "",
        "test_end": max(test_dates) if test_dates else "",
        "training_rows": 0,
        "test_rows": 0,
        "train_positive_rate": np.nan,
        "positive_rate": np.nan,
        "majority_accuracy": np.nan,
        "accuracy": np.nan,
        "accuracy_lift": np.nan,
        "roc_auc": np.nan,
        "brier_score": np.nan,
        "baseline_brier_score": np.nan,
        "brier_skill": np.nan,
        "benchmark_average_return": np.nan,
        "selected_threshold": SELECTION_THRESHOLD,
        "selected_rows": 0,
        "selected_average_return": np.nan,
        "selected_return_edge": np.nan,
        "selected_win_rate": np.nan,
        "selected_win_lift": np.nan,
        "retained_features": len(features),
        "dropped_features": ", ".join(dropped_features),
        "champion_score": -1_000_000_000.0,
        "holdout_score": -1_000_000_000.0,
        "walk_forward_splits": 0,
        "walk_forward_positive_splits": 0,
        "walk_forward_avg_score": np.nan,
        "walk_forward_score_std": np.nan,
        "walk_forward_avg_return_edge": np.nan,
        "walk_forward_avg_brier_skill": np.nan,
        "walk_forward_avg_auc": np.nan,
    }
    return evaluation


def walk_forward_windows(dates, horizon):
    if WALK_FORWARD_SPLITS <= 0 or WALK_FORWARD_TEST_DATES <= 0:
        return []
    windows = []
    step = WALK_FORWARD_TEST_DATES
    for offset in range(WALK_FORWARD_SPLITS):
        test_end_index = len(dates) - 1 - offset * step
        test_start_index = test_end_index - step + 1
        if test_start_index < 0:
            break
        embargo_start_index = max(0, test_start_index - horizon)
        train_dates = dates[:embargo_start_index]
        test_dates = dates[test_start_index : test_end_index + 1]
        if len(train_dates) < WALK_FORWARD_MIN_TRAIN_DATES or not test_dates:
            continue
        windows.append((train_dates, test_dates))
    return list(reversed(windows))


def failed_walk_forward_evaluation(
    model_name, horizon, split_id, error, train_dates, test_dates, features
):
    return {
        "horizon_days": horizon,
        "split_id": split_id,
        "model_name": model_name,
        "model_label": MODEL_LABELS[model_name],
        "model_version": MODEL_VERSION,
        "fit_status": "failed",
        "fit_error": str(error)[:500],
        "training_start": min(train_dates) if train_dates else "",
        "training_end": max(train_dates) if train_dates else "",
        "embargo_dates": horizon,
        "test_start": min(test_dates) if test_dates else "",
        "test_end": max(test_dates) if test_dates else "",
        "training_rows": 0,
        "test_rows": 0,
        "train_positive_rate": np.nan,
        "positive_rate": np.nan,
        "majority_accuracy": np.nan,
        "accuracy": np.nan,
        "accuracy_lift": np.nan,
        "roc_auc": np.nan,
        "brier_score": np.nan,
        "baseline_brier_score": np.nan,
        "brier_skill": np.nan,
        "benchmark_average_return": np.nan,
        "selected_threshold": SELECTION_THRESHOLD,
        "selected_rows": 0,
        "selected_average_return": np.nan,
        "selected_return_edge": np.nan,
        "selected_win_rate": np.nan,
        "selected_win_lift": np.nan,
        "retained_features": len(features),
        "champion_score": -1_000_000_000.0,
    }


def evaluate_walk_forward_split(model_name, horizon, split_id, train, test, train_dates, test_dates, features):
    x_train = train[list(features)]
    y_train = (train["forward_return"] > 0).astype(int)
    x_test = test[list(features)]
    y_test = (test["forward_return"] > 0).astype(int)
    if y_train.nunique() < 2:
        raise RuntimeError("training labels contain only one class")
    if y_test.empty:
        raise RuntimeError("test split has no labels")

    model = build_estimator(model_name)
    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_test)[:, 1]
    train_positive_rate = float(y_train.mean())
    evaluation = {
        "horizon_days": horizon,
        "split_id": split_id,
        "model_name": model_name,
        "model_label": MODEL_LABELS[model_name],
        "model_version": MODEL_VERSION,
        "fit_status": "ok",
        "fit_error": "",
        "training_start": min(train_dates),
        "training_end": max(train_dates),
        "embargo_dates": horizon,
        "test_start": min(test_dates),
        "test_end": max(test_dates),
        "training_rows": len(train),
        "test_rows": len(test),
        "retained_features": len(features),
    }
    evaluation.update(evaluated_metric_block(test, y_test, probabilities, train_positive_rate))
    evaluation["champion_score"] = champion_score(evaluation)
    return evaluation


def walk_forward_horizon(labeled, horizon, features):
    dates = sorted(labeled["begins_at"].unique())
    rows = []
    for split_id, (train_dates, test_dates) in enumerate(
        walk_forward_windows(dates, horizon), start=1
    ):
        train_pool = labeled[labeled["begins_at"].isin(train_dates)]
        test_pool = labeled[labeled["begins_at"].isin(test_dates)]
        for model_name in MODEL_CANDIDATES:
            train = sample_rows(
                train_pool, model_row_limit(model_name, "train", MAX_TRAIN_ROWS)
            )
            test = sample_rows(
                test_pool, model_row_limit(model_name, "test", MAX_TEST_ROWS)
            )
            try:
                rows.append(
                    evaluate_walk_forward_split(
                        model_name,
                        horizon,
                        split_id,
                        train,
                        test,
                        train_dates,
                        test_dates,
                        features,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - keep other candidates/splits alive.
                rows.append(
                    failed_walk_forward_evaluation(
                        model_name, horizon, split_id, exc, train_dates, test_dates, features
                    )
                )
                print(f"{horizon}d split {split_id} {model_name} failed: {exc}")
    return rows


def attach_walk_forward_summary(evaluations, walk_forward_rows):
    if not evaluations:
        return evaluations
    ok_rows = [
        row for row in walk_forward_rows
        if row.get("fit_status") == "ok" and not pd.isna(row.get("champion_score"))
    ]
    summary = {}
    if ok_rows:
        frame = pd.DataFrame(ok_rows)
        grouped = frame.groupby(["horizon_days", "model_name"], dropna=False)
        for key, group in grouped:
            scores = pd.to_numeric(group["champion_score"], errors="coerce").dropna()
            summary[key] = {
                "walk_forward_splits": int(len(group)),
                "walk_forward_positive_splits": int((scores > 0).sum()),
                "walk_forward_avg_score": float(scores.mean()) if len(scores) else np.nan,
                "walk_forward_score_std": float(scores.std(ddof=0)) if len(scores) else np.nan,
                "walk_forward_avg_return_edge": float(
                    pd.to_numeric(group["selected_return_edge"], errors="coerce").mean()
                ),
                "walk_forward_avg_brier_skill": float(
                    pd.to_numeric(group["brier_skill"], errors="coerce").mean()
                ),
                "walk_forward_avg_auc": float(
                    pd.to_numeric(group["roc_auc"], errors="coerce").mean()
                ),
            }

    for evaluation in evaluations:
        key = (evaluation["horizon_days"], evaluation["model_name"])
        values = summary.get(
            key,
            {
                "walk_forward_splits": 0,
                "walk_forward_positive_splits": 0,
                "walk_forward_avg_score": np.nan,
                "walk_forward_score_std": np.nan,
                "walk_forward_avg_return_edge": np.nan,
                "walk_forward_avg_brier_skill": np.nan,
                "walk_forward_avg_auc": np.nan,
            },
        )
        evaluation.update(values)
        holdout_score = evaluation.get("holdout_score", evaluation.get("champion_score"))
        if (
            evaluation.get("fit_status") == "ok"
            and values["walk_forward_splits"] > 0
            and not pd.isna(values["walk_forward_avg_score"])
        ):
            consistency = values["walk_forward_positive_splits"] / values["walk_forward_splits"]
            volatility_penalty = 0.0 if pd.isna(values["walk_forward_score_std"]) else values["walk_forward_score_std"]
            evaluation["champion_score"] = (
                0.55 * float(holdout_score)
                + 0.45 * float(values["walk_forward_avg_score"])
                + 0.02 * (consistency - 0.5)
                - 0.10 * float(volatility_penalty)
            )
    return evaluations


def build_horizon(frame, horizon):
    labeled = frame.copy()
    labeled["forward_return"] = labeled[f"future_price_{horizon}d"] / labeled["close_price"] - 1.0
    labeled = labeled.dropna(subset=["forward_return"])
    dates = sorted(labeled["begins_at"].unique())
    test_dates = dates[-TEST_DATES:]
    test_start_index = len(dates) - TEST_DATES
    embargo_start_index = max(0, test_start_index - horizon)
    train_dates = dates[:embargo_start_index]
    if not train_dates:
        raise RuntimeError(f"No training dates remain for the {horizon}d model")

    train_pool = labeled[labeled["begins_at"].isin(train_dates)]
    test_pool = labeled[labeled["begins_at"].isin(test_dates)]
    usable_features = tuple(feature for feature in FEATURES if train_pool[feature].notna().any())
    dropped_features = tuple(feature for feature in FEATURES if feature not in usable_features)
    if not usable_features:
        raise RuntimeError(f"No usable model features remain for the {horizon}d model")
    if dropped_features:
        print(
            f"{horizon}d model dropped features without training values: "
            f"{', '.join(dropped_features)}"
        )

    evaluations = []
    importances = []
    predictions = []
    for model_name in MODEL_CANDIDATES:
        train = sample_rows(
            train_pool, model_row_limit(model_name, "train", MAX_TRAIN_ROWS)
        )
        test = sample_rows(
            test_pool, model_row_limit(model_name, "test", MAX_TEST_ROWS)
        )
        model = build_estimator(model_name)
        try:
            evaluation = evaluate_model(
                model, model_name, horizon, train, test, train_dates, test_dates, usable_features
            )
            latest = latest_predictions(model, frame, horizon, usable_features, model_name)
            importance = feature_importance(model, horizon, usable_features, model_name)
            predictions.append(latest)
            if not importance.empty:
                importances.append(importance)
        except Exception as exc:  # noqa: BLE001 - keep pipeline alive if one candidate fails.
            evaluation = failed_evaluation(
                model_name, horizon, exc, train_dates, test_dates, usable_features, dropped_features
            )
            print(f"{horizon}d {model_name} failed: {exc}")
        evaluation["dropped_features"] = ", ".join(dropped_features)
        evaluations.append(evaluation)
    walk_forward_rows = walk_forward_horizon(labeled, horizon, usable_features)
    evaluations = attach_walk_forward_summary(evaluations, walk_forward_rows)
    return evaluations, importances, predictions, walk_forward_rows


def mark_champions(tournament):
    tournament = tournament.copy()
    tournament["is_champion"] = False
    for horizon, group in tournament.groupby("horizon_days"):
        eligible = group[group["fit_status"] == "ok"].copy()
        if eligible.empty:
            raise RuntimeError(f"No model candidate succeeded for {horizon}d")
        champion_index = eligible["champion_score"].astype(float).idxmax()
        tournament.loc[champion_index, "is_champion"] = True
    return tournament.sort_values(
        ["horizon_days", "is_champion", "champion_score"],
        ascending=[True, False, False],
    )


def filter_champion_rows(frame, champions):
    if frame.empty:
        return frame
    pairs = set(zip(champions["horizon_days"], champions["model_name"]))
    mask = [
        (row.horizon_days, row.model_name) in pairs for row in frame.itertuples(index=False)
    ]
    return frame.loc[mask].copy()


def with_run_columns(frame, run_metadata):
    output = frame.copy()
    output.insert(0, "run_as_of_date", run_metadata["as_of_date"])
    output.insert(0, "run_created_at", run_metadata["created_at"])
    output.insert(0, "run_id", run_metadata["run_id"])
    return output


def delete_existing_run(conn, table, run_id):
    if table_exists(conn, table):
        conn.execute(f'DELETE FROM "{table}" WHERE run_id=?', (run_id,))


def append_run_frame(conn, table, frame, run_id):
    if frame.empty:
        return 0
    delete_existing_run(conn, table, run_id)
    frame.to_sql(table, conn, if_exists="append", index=False)
    return len(frame)


def prediction_history_rows(all_predictions, champions, run_metadata):
    if all_predictions.empty:
        return all_predictions
    rows = all_predictions.sort_values(
        ["horizon_days", "model_name", "model_rank"]
    ).copy()
    rows = (
        rows.groupby(["horizon_days", "model_name"], group_keys=False)
        .head(HISTORY_PREDICTION_LIMIT)
        .copy()
    )
    champion_pairs = set(zip(champions["horizon_days"], champions["model_name"]))
    rows["is_champion"] = [
        (row.horizon_days, row.model_name) in champion_pairs
        for row in rows.itertuples(index=False)
    ]
    return with_run_columns(rows, run_metadata)


def champion_text(champions):
    parts = []
    for row in champions.sort_values("horizon_days").itertuples(index=False):
        parts.append(f"{int(row.horizon_days)}d {row.model_name}")
    return ", ".join(parts)


def save_outputs(conn, evaluations, importances, predictions, walk_forward_rows, run_metadata):
    tournament = mark_champions(pd.DataFrame(evaluations))
    champions = tournament[tournament["is_champion"]].copy()
    all_predictions = pd.concat(predictions, ignore_index=True)
    latest = filter_champion_rows(all_predictions, champions)
    walk_forward = (
        pd.DataFrame(walk_forward_rows)
        if walk_forward_rows
        else pd.DataFrame()
    )

    if importances:
        all_importance = pd.concat(importances, ignore_index=True)
        importance = filter_champion_rows(all_importance, champions)
        if importance.empty:
            importance = all_importance[all_importance["model_name"].eq("sgd_logistic")].copy()
    else:
        all_importance = pd.DataFrame(
            columns=[
                "horizon_days",
                "model_name",
                "model_label",
                "model_version",
                "feature",
                "coefficient",
                "absolute_coefficient",
            ]
        )
        importance = all_importance.copy()

    champions.to_sql("ModelEvaluation", conn, if_exists="replace", index=False)
    tournament.to_sql("ModelTournamentEvaluation", conn, if_exists="replace", index=False)
    importance.to_sql("ModelFeatureImportance", conn, if_exists="replace", index=False)
    all_importance.to_sql("ModelTournamentFeatureImportance", conn, if_exists="replace", index=False)
    latest.to_sql("LatestModelPredictions", conn, if_exists="replace", index=False)
    all_predictions.to_sql("LatestModelCandidatePredictions", conn, if_exists="replace", index=False)
    if not walk_forward.empty:
        walk_forward.to_sql("ModelWalkForwardEvaluation", conn, if_exists="replace", index=False)
    else:
        pd.DataFrame(
            columns=[
                "horizon_days",
                "split_id",
                "model_name",
                "fit_status",
                "champion_score",
            ]
        ).to_sql("ModelWalkForwardEvaluation", conn, if_exists="replace", index=False)

    run_frame = pd.DataFrame(
        [
            {
                **run_metadata,
                "champions": champion_text(champions),
                "tournament_rows": len(tournament),
                "walk_forward_rows": len(walk_forward),
                "candidate_prediction_rows": len(all_predictions),
                "champion_prediction_rows": len(latest),
            }
        ]
    )
    append_run_frame(conn, "MLRunHistory", run_frame, run_metadata["run_id"])
    append_run_frame(
        conn,
        "ModelEvaluationHistory",
        with_run_columns(tournament, run_metadata),
        run_metadata["run_id"],
    )
    if not walk_forward.empty:
        append_run_frame(
            conn,
            "ModelWalkForwardEvaluationHistory",
            with_run_columns(walk_forward, run_metadata),
            run_metadata["run_id"],
        )
    prediction_history = prediction_history_rows(all_predictions, champions, run_metadata)
    append_run_frame(conn, "ModelPredictionHistory", prediction_history, run_metadata["run_id"])
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_latest_model_predictions "
        "ON LatestModelPredictions(horizon_days, model_rank)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_latest_model_candidate_predictions "
        "ON LatestModelCandidatePredictions(horizon_days, model_name, model_rank)"
    )
    for table in (
        "MLRunHistory",
        "ModelEvaluationHistory",
        "ModelWalkForwardEvaluationHistory",
        "ModelPredictionHistory",
    ):
        if table_exists(conn, table):
            conn.execute(
                f'CREATE INDEX IF NOT EXISTS idx_{table.lower()}_run '
                f'ON "{table}"(run_id)'
            )
    conn.commit()

    ANALYTICS_DIR.mkdir(exist_ok=True)
    run_frame.to_csv(ANALYTICS_DIR / "model_run_summary.csv", index=False)
    champions.to_csv(ANALYTICS_DIR / "model_evaluation.csv", index=False)
    tournament.to_csv(ANALYTICS_DIR / "model_tournament_evaluation.csv", index=False)
    walk_forward.to_csv(ANALYTICS_DIR / "model_walk_forward_evaluation.csv", index=False)
    importance.to_csv(ANALYTICS_DIR / "model_feature_importance.csv", index=False)
    all_importance.to_csv(ANALYTICS_DIR / "model_tournament_feature_importance.csv", index=False)
    latest.to_csv(ANALYTICS_DIR / "latest_model_predictions.csv", index=False)
    all_predictions.to_csv(ANALYTICS_DIR / "latest_model_candidate_predictions.csv", index=False)
    prediction_history.to_csv(ANALYTICS_DIR / "model_prediction_history_latest.csv", index=False)
    return champions, tournament


def main():
    if not DB_PATH.exists():
        raise RuntimeError(f"{DB_PATH} is required")
    with closing(sqlite3.connect(DB_PATH)) as conn:
        frame = load_frame(conn)
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run_metadata = build_run_metadata(frame, created_at)
        evaluations = []
        importances = []
        predictions = []
        walk_forward_rows = []
        for horizon in HORIZONS:
            (
                horizon_evaluations,
                horizon_importances,
                horizon_predictions,
                horizon_walk_forward,
            ) = build_horizon(frame, horizon)
            evaluations.extend(horizon_evaluations)
            importances.extend(horizon_importances)
            predictions.extend(horizon_predictions)
            walk_forward_rows.extend(horizon_walk_forward)
        champions, tournament = save_outputs(
            conn, evaluations, importances, predictions, walk_forward_rows, run_metadata
        )
    print("Built leakage-controlled model tournament:")
    print(
        tournament[
            [
                "horizon_days",
                "model_name",
                "fit_status",
                "is_champion",
                "accuracy",
                "accuracy_lift",
                "roc_auc",
                "brier_skill",
                "selected_return_edge",
                "selected_average_return",
                "selected_win_rate",
                "holdout_score",
                "walk_forward_splits",
                "walk_forward_avg_score",
                "champion_score",
            ]
        ].to_string(index=False)
    )
    print("Champion model outputs:")
    print(champions.to_string(index=False))


if __name__ == "__main__":
    main()
