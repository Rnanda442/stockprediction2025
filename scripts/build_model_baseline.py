import os
import sqlite3
from contextlib import closing
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


def evaluate_model(model, model_name, horizon, train, test, train_dates, test_dates, features):
    x_train = train[list(features)]
    y_train = (train["forward_return"] > 0).astype(int)
    x_test = test[list(features)]
    y_test = (test["forward_return"] > 0).astype(int)

    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_test)[:, 1]
    predictions = (probabilities >= 0.5).astype(int)
    selected = test.loc[probabilities >= 0.60, "forward_return"]
    positive_rate = float(y_test.mean())
    majority_accuracy = max(positive_rate, 1.0 - positive_rate)
    benchmark = float(test["forward_return"].mean())
    selected_average_return = float(selected.mean()) if len(selected) else np.nan
    selected_win_rate = float((selected > 0).mean()) if len(selected) else np.nan
    brier = float(brier_score_loss(y_test, probabilities))
    baseline_brier = float(
        brier_score_loss(y_test, np.full(len(probabilities), positive_rate))
    )
    brier_skill = (
        np.nan if baseline_brier <= 0 else float(1.0 - (brier / baseline_brier))
    )
    accuracy = float(accuracy_score(y_test, predictions))
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
        "positive_rate": positive_rate,
        "majority_accuracy": majority_accuracy,
        "accuracy": accuracy,
        "accuracy_lift": accuracy - majority_accuracy,
        "roc_auc": safe_auc(y_test, probabilities),
        "brier_score": brier,
        "baseline_brier_score": baseline_brier,
        "brier_skill": brier_skill,
        "benchmark_average_return": benchmark,
        "selected_rows": len(selected),
        "selected_average_return": selected_average_return,
        "selected_return_edge": selected_average_return - benchmark,
        "selected_win_rate": selected_win_rate,
        "selected_win_lift": selected_win_rate - positive_rate,
        "retained_features": len(features),
        "dropped_features": "",
    }
    evaluation["champion_score"] = champion_score(evaluation)
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
        "positive_rate": np.nan,
        "majority_accuracy": np.nan,
        "accuracy": np.nan,
        "accuracy_lift": np.nan,
        "roc_auc": np.nan,
        "brier_score": np.nan,
        "baseline_brier_score": np.nan,
        "brier_skill": np.nan,
        "benchmark_average_return": np.nan,
        "selected_rows": 0,
        "selected_average_return": np.nan,
        "selected_return_edge": np.nan,
        "selected_win_rate": np.nan,
        "selected_win_lift": np.nan,
        "retained_features": len(features),
        "dropped_features": ", ".join(dropped_features),
        "champion_score": -1_000_000_000.0,
    }
    return evaluation


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
    return evaluations, importances, predictions


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


def save_outputs(conn, evaluations, importances, predictions):
    tournament = mark_champions(pd.DataFrame(evaluations))
    champions = tournament[tournament["is_champion"]].copy()
    all_predictions = pd.concat(predictions, ignore_index=True)
    latest = filter_champion_rows(all_predictions, champions)

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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_latest_model_predictions "
        "ON LatestModelPredictions(horizon_days, model_rank)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_latest_model_candidate_predictions "
        "ON LatestModelCandidatePredictions(horizon_days, model_name, model_rank)"
    )
    conn.commit()

    ANALYTICS_DIR.mkdir(exist_ok=True)
    champions.to_csv(ANALYTICS_DIR / "model_evaluation.csv", index=False)
    tournament.to_csv(ANALYTICS_DIR / "model_tournament_evaluation.csv", index=False)
    importance.to_csv(ANALYTICS_DIR / "model_feature_importance.csv", index=False)
    all_importance.to_csv(ANALYTICS_DIR / "model_tournament_feature_importance.csv", index=False)
    latest.to_csv(ANALYTICS_DIR / "latest_model_predictions.csv", index=False)
    all_predictions.to_csv(ANALYTICS_DIR / "latest_model_candidate_predictions.csv", index=False)
    return champions, tournament


def main():
    if not DB_PATH.exists():
        raise RuntimeError(f"{DB_PATH} is required")
    with closing(sqlite3.connect(DB_PATH)) as conn:
        frame = load_frame(conn)
        evaluations = []
        importances = []
        predictions = []
        for horizon in HORIZONS:
            horizon_evaluations, horizon_importances, horizon_predictions = build_horizon(
                frame, horizon
            )
            evaluations.extend(horizon_evaluations)
            importances.extend(horizon_importances)
            predictions.extend(horizon_predictions)
        champions, tournament = save_outputs(conn, evaluations, importances, predictions)
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
                "champion_score",
            ]
        ].to_string(index=False)
    )
    print("Champion model outputs:")
    print(champions.to_string(index=False))


if __name__ == "__main__":
    main()
