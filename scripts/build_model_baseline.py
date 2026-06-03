import os
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "vectorized.db"
ANALYTICS_DIR = ROOT / "analytics"
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
RANDOM_SEED = 17


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


def latest_predictions(model, frame, horizon, features):
    latest = (
        frame.sort_values(["ticker", "begins_at"])
        .groupby("ticker", as_index=False)
        .tail(1)[["begins_at", "ticker", *features]]
        .copy()
    )
    feature_frame = latest[list(features)]
    probabilities = model.predict_proba(feature_frame)[:, 1]
    imputer = model.named_steps["simpleimputer"]
    scaler = model.named_steps["standardscaler"]
    classifier = model.named_steps["sgdclassifier"]
    standardized = scaler.transform(imputer.transform(feature_frame))
    contributions = standardized * classifier.coef_[0]

    latest = latest.rename(columns={"begins_at": "as_of_date"})
    latest["horizon_days"] = horizon
    latest["probability_up"] = probabilities
    latest["probability_bucket"] = latest["probability_up"].map(describe_probability)
    latest["model_rank"] = latest["probability_up"].rank(method="first", ascending=False).astype(int)
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
            "horizon_days",
            "model_rank",
            "probability_up",
            "probability_bucket",
            "top_positive_drivers",
            "top_negative_drivers",
        ]
    ]


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

    train = sample_rows(labeled[labeled["begins_at"].isin(train_dates)], MAX_TRAIN_ROWS)
    test = sample_rows(labeled[labeled["begins_at"].isin(test_dates)], MAX_TEST_ROWS)
    usable_features = tuple(feature for feature in FEATURES if train[feature].notna().any())
    dropped_features = tuple(feature for feature in FEATURES if feature not in usable_features)
    if not usable_features:
        raise RuntimeError(f"No usable model features remain for the {horizon}d model")
    if dropped_features:
        print(
            f"{horizon}d model dropped features without training values: "
            f"{', '.join(dropped_features)}"
        )

    x_train = train[list(usable_features)]
    y_train = (train["forward_return"] > 0).astype(int)
    x_test = test[list(usable_features)]
    y_test = (test["forward_return"] > 0).astype(int)

    model = make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        SGDClassifier(
            loss="log_loss",
            alpha=0.0005,
            max_iter=1500,
            random_state=RANDOM_SEED,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=10,
        ),
    )
    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_test)[:, 1]
    predictions = (probabilities >= 0.5).astype(int)
    selected = test.loc[probabilities >= 0.60, "forward_return"]
    benchmark = float(test["forward_return"].mean())
    evaluation = {
        "horizon_days": horizon,
        "training_start": min(train_dates),
        "training_end": max(train_dates),
        "embargo_dates": horizon,
        "test_start": min(test_dates),
        "test_end": max(test_dates),
        "training_rows": len(train),
        "test_rows": len(test),
        "accuracy": float(accuracy_score(y_test, predictions)),
        "roc_auc": safe_auc(y_test, probabilities),
        "brier_score": float(brier_score_loss(y_test, probabilities)),
        "benchmark_average_return": benchmark,
        "selected_rows": len(selected),
        "selected_average_return": float(selected.mean()) if len(selected) else np.nan,
        "selected_win_rate": float((selected > 0).mean()) if len(selected) else np.nan,
        "retained_features": len(usable_features),
        "dropped_features": ", ".join(dropped_features),
    }
    classifier = model.named_steps["sgdclassifier"]
    importance = pd.DataFrame(
        {
            "horizon_days": horizon,
            "feature": usable_features,
            "coefficient": classifier.coef_[0],
        }
    )
    importance["absolute_coefficient"] = importance["coefficient"].abs()
    importance = importance.sort_values("absolute_coefficient", ascending=False)
    return evaluation, importance, latest_predictions(model, frame, horizon, usable_features)


def save_outputs(conn, evaluations, importances, predictions):
    evaluation = pd.DataFrame(evaluations)
    importance = pd.concat(importances, ignore_index=True)
    latest = pd.concat(predictions, ignore_index=True)
    evaluation.to_sql("ModelEvaluation", conn, if_exists="replace", index=False)
    importance.to_sql("ModelFeatureImportance", conn, if_exists="replace", index=False)
    latest.to_sql("LatestModelPredictions", conn, if_exists="replace", index=False)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_latest_model_predictions "
        "ON LatestModelPredictions(horizon_days, model_rank)"
    )
    conn.commit()
    ANALYTICS_DIR.mkdir(exist_ok=True)
    evaluation.to_csv(ANALYTICS_DIR / "model_evaluation.csv", index=False)
    importance.to_csv(ANALYTICS_DIR / "model_feature_importance.csv", index=False)
    latest.to_csv(ANALYTICS_DIR / "latest_model_predictions.csv", index=False)
    return evaluation


def main():
    if not DB_PATH.exists():
        raise RuntimeError("vectorized.db is required")
    with closing(sqlite3.connect(DB_PATH)) as conn:
        frame = load_frame(conn)
        evaluations = []
        importances = []
        predictions = []
        for horizon in HORIZONS:
            evaluation, importance, latest = build_horizon(frame, horizon)
            evaluations.append(evaluation)
            importances.append(importance)
            predictions.append(latest)
        summary = save_outputs(conn, evaluations, importances, predictions)
    print("Built leakage-controlled model baselines:")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
