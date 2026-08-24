import os
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge, SGDClassifier
from sklearn.metrics import accuracy_score, brier_score_loss, mean_absolute_error, r2_score, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("MODEL_DB_PATH", ROOT / "vectorized.db"))
ANALYTICS_DIR = Path(os.getenv("MODEL_ANALYTICS_DIR", ROOT / "analytics"))
MODEL_VERSION = os.getenv("MODEL_VERSION", "tournament_v1")
HORIZONS = (5, 20, 60)
SIMILARITY_ANN_MODEL = "similarity_ann_monte_carlo"
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
ANN_EXTRA_FEATURES = (
    "log_dollar_vol_20d",
    "volatility_ratio_20_60",
    "return_trend_alignment",
    "liquidity_rank_pct",
    "volatility_rank_pct",
    "trend_rank_pct",
    "return_rank_pct",
    "stock_type_code",
    "variant_family_size",
    "variant_is_chosen",
)
ANN_FEATURES = FEATURES + ANN_EXTRA_FEATURES
FEATURE_GROUPS = {
    "Short returns": ("pct_1d", "pct_2d", "pct_3d", "pct_5d"),
    "Momentum": ("ret_10d", "ret_20d", "ret_60d", "riskadj_mom_60d", "ma_crossover"),
    "Risk": (
        "volatility_5d",
        "volatility_10d",
        "vol_20d",
        "vol_60d",
        "max_dd_60d",
        "bb_width_20d",
    ),
    "Trend shape": ("trend_slope_60d", "trend_r2_60d", "z_ma20", "time_since_max_60d"),
    "Liquidity": ("dollar_vol_20d", "log_dollar_vol_20d"),
    "Stock type": (
        "liquidity_rank_pct",
        "volatility_rank_pct",
        "trend_rank_pct",
        "return_rank_pct",
        "stock_type_code",
    ),
    "Variant family": ("variant_family_size", "variant_is_chosen"),
}
LOOKBACK_DATES = int(os.getenv("MODEL_LOOKBACK_DATES", "756"))
TEST_DATES = int(os.getenv("MODEL_TEST_DATES", "126"))
MAX_TRAIN_ROWS = int(os.getenv("MODEL_MAX_TRAIN_ROWS", "350000"))
MAX_TEST_ROWS = int(os.getenv("MODEL_MAX_TEST_ROWS", "150000"))
RANDOM_SEED = int(os.getenv("MODEL_RANDOM_SEED", "17"))
DEFAULT_CANDIDATES = (
    "sgd_logistic,mlp_ann,hist_gradient_boosting,"
    f"{SIMILARITY_ANN_MODEL}"
)
SELECTION_THRESHOLD = float(os.getenv("MODEL_SELECTION_THRESHOLD", "0.60"))
WALK_FORWARD_SPLITS = int(os.getenv("MODEL_WALK_FORWARD_SPLITS", "5"))
WALK_FORWARD_TEST_DATES = int(os.getenv("MODEL_WALK_FORWARD_TEST_DATES", "63"))
WALK_FORWARD_MIN_TRAIN_DATES = int(os.getenv("MODEL_WALK_FORWARD_MIN_TRAIN_DATES", "252"))
HISTORY_PREDICTION_LIMIT = int(os.getenv("MODEL_HISTORY_PREDICTION_LIMIT", "100"))
MODEL_MEMORY_RETENTION_DAYS = int(os.getenv("MODEL_MEMORY_RETENTION_DAYS", "180"))
ANN_IMPORTANCE_SAMPLE_ROWS = int(os.getenv("MODEL_ANN_IMPORTANCE_SAMPLE_ROWS", "12000"))
RIDGE_IMPORTANCE_MAX_TRAIN_ROWS = int(
    os.getenv("MODEL_RIDGE_IMPORTANCE_MAX_TRAIN_ROWS", "150000")
)
RIDGE_IMPORTANCE_MAX_TEST_ROWS = int(
    os.getenv("MODEL_RIDGE_IMPORTANCE_MAX_TEST_ROWS", "50000")
)
RIDGE_ALPHA = float(os.getenv("MODEL_RIDGE_ALPHA", "10.0"))
MONTE_CARLO_LIMIT_PER_HORIZON = int(os.getenv("MODEL_MONTE_CARLO_LIMIT_PER_HORIZON", "60"))
MONTE_CARLO_SIMULATIONS = int(os.getenv("MODEL_MONTE_CARLO_SIMULATIONS", "1000"))
MONTE_CARLO_PATH_LIMIT_PER_HORIZON = int(os.getenv("MODEL_MONTE_CARLO_PATH_LIMIT_PER_HORIZON", "12"))
MONTE_CARLO_NEIGHBOR_LIMIT = int(os.getenv("MODEL_MONTE_CARLO_NEIGHBOR_LIMIT", "8"))
MONTE_CARLO_DRAWDOWN_THRESHOLD = float(os.getenv("MODEL_MONTE_CARLO_DRAWDOWN_THRESHOLD", "-0.05"))
MONTE_CARLO_TARGET_THRESHOLD = float(os.getenv("MODEL_MONTE_CARLO_TARGET_THRESHOLD", "0.05"))


MODEL_LABELS = {
    "sgd_logistic": "SGD logistic baseline",
    "mlp_ann": "ANN MLP classifier",
    "hist_gradient_boosting": "Histogram gradient boosting",
    SIMILARITY_ANN_MODEL: "ANN similarity + Monte Carlo",
}


@contextmanager
def timed_stage(name):
    started = time.monotonic()
    print(f"[model-stage] {name} started", flush=True)
    try:
        yield
    finally:
        elapsed = time.monotonic() - started
        print(f"[model-stage] {name} finished in {elapsed:.1f}s", flush=True)


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


def read_csv_if_exists(path):
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001 - optional artifacts should not kill modeling.
        print(f"Could not read optional artifact {path}: {exc}")
        return pd.DataFrame()


def normalize_ticker(value):
    if pd.isna(value):
        return ""
    return str(value).strip().upper()


def load_similarity_pairs():
    pairs = read_csv_if_exists(ANALYTICS_DIR / "flipcorr_pairs_5y.csv")
    columns = [
        "A",
        "B",
        "similarity",
        "A_slope",
        "A_ret60",
        "A_vol60",
        "A_dv20",
        "B_slope",
        "B_ret60",
        "B_vol60",
        "B_dv20",
        "winner",
    ]
    if pairs.empty or not {"A", "B", "similarity"}.issubset(pairs.columns):
        return pd.DataFrame(columns=columns)
    pairs = pairs.copy()
    for column in columns:
        if column not in pairs.columns:
            pairs[column] = np.nan
    pairs["A"] = pairs["A"].map(normalize_ticker)
    pairs["B"] = pairs["B"].map(normalize_ticker)
    pairs["winner"] = pairs["winner"].map(normalize_ticker)
    pairs["similarity"] = pd.to_numeric(pairs["similarity"], errors="coerce")
    pairs = pairs.dropna(subset=["similarity"])
    pairs = pairs[(pairs["A"] != "") & (pairs["B"] != "") & (pairs["A"] != pairs["B"])]
    return pairs[columns].drop_duplicates(["A", "B"]).reset_index(drop=True)


def variant_family_features():
    families = read_csv_if_exists(ANALYTICS_DIR / "variant_families.csv")
    columns = [
        "ticker",
        "variant_family_size",
        "variant_is_chosen",
    ]
    if families.empty or not {"Family", "Member"}.issubset(families.columns):
        return pd.DataFrame(columns=columns)
    frame = families.copy()
    frame["ticker"] = frame["Member"].astype(str).str.upper()
    frame["variant_family_size"] = frame.groupby("Family")["Member"].transform("count")
    if "Chosen" in frame.columns:
        frame["variant_is_chosen"] = frame["Chosen"].astype(str).str.lower().isin(
            {"true", "1", "yes"}
        ).astype(int)
    else:
        frame["variant_is_chosen"] = 0
    return frame[columns].drop_duplicates("ticker")


def _rank_pct(frame, source_column, output_column):
    frame[output_column] = (
        frame.groupby("begins_at")[source_column]
        .rank(pct=True, method="average")
        .fillna(0.5)
    )


def attach_ann_features(frame):
    frame = frame.copy()
    _rank_pct(frame, "dollar_vol_20d", "liquidity_rank_pct")
    _rank_pct(frame, "vol_60d", "volatility_rank_pct")
    _rank_pct(frame, "trend_slope_60d", "trend_rank_pct")
    _rank_pct(frame, "ret_60d", "return_rank_pct")

    dollar_volume = pd.to_numeric(frame["dollar_vol_20d"], errors="coerce").clip(lower=0)
    frame["log_dollar_vol_20d"] = np.log1p(dollar_volume)
    frame["volatility_ratio_20_60"] = (
        pd.to_numeric(frame["vol_20d"], errors="coerce")
        / (pd.to_numeric(frame["vol_60d"], errors="coerce").abs() + 1e-9)
    )
    frame["return_trend_alignment"] = (
        pd.to_numeric(frame["ret_20d"], errors="coerce")
        * np.sign(pd.to_numeric(frame["trend_slope_60d"], errors="coerce").fillna(0))
    )
    labels = np.select(
        [
            frame["volatility_rank_pct"] >= 0.75,
            (frame["liquidity_rank_pct"] >= 0.75) & (frame["trend_rank_pct"] >= 0.60),
            frame["trend_rank_pct"] >= 0.75,
            frame["return_rank_pct"] <= 0.25,
        ],
        [
            "High volatility",
            "Liquid leader",
            "Trend leader",
            "Recent laggard",
        ],
        default="Core",
    )
    stock_type_order = {
        "Core": 0,
        "High volatility": 1,
        "Liquid leader": 2,
        "Trend leader": 3,
        "Recent laggard": 4,
    }
    frame["stock_type"] = labels
    frame["stock_type_code"] = pd.Series(labels, index=frame.index).map(stock_type_order)

    families = variant_family_features()
    if not families.empty:
        frame = frame.merge(families, on="ticker", how="left")
    else:
        frame["variant_family_size"] = 1
        frame["variant_is_chosen"] = 1
    frame["variant_family_size"] = pd.to_numeric(
        frame["variant_family_size"], errors="coerce"
    ).fillna(1)
    frame["variant_is_chosen"] = pd.to_numeric(
        frame["variant_is_chosen"], errors="coerce"
    ).fillna(1)
    frame[list(ANN_EXTRA_FEATURES)] = frame[list(ANN_EXTRA_FEATURES)].replace(
        [np.inf, -np.inf], np.nan
    )
    return frame


def features_for_model(model_name, usable_features):
    if model_name == SIMILARITY_ANN_MODEL:
        return tuple(feature for feature in ANN_FEATURES if feature in usable_features)
    return tuple(feature for feature in FEATURES if feature in usable_features)


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
    return attach_ann_features(frame)


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
    if model_name == SIMILARITY_ANN_MODEL:
        hidden_layers = tuple(
            int(part)
            for part in os.getenv("MODEL_SIMILARITY_ANN_HIDDEN_LAYERS", "64,32").split(",")
            if part.strip()
        )
        return make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            MLPClassifier(
                hidden_layer_sizes=hidden_layers,
                alpha=float(os.getenv("MODEL_SIMILARITY_ANN_ALPHA", "0.0008")),
                learning_rate_init=float(
                    os.getenv("MODEL_SIMILARITY_ANN_LEARNING_RATE", "0.001")
                ),
                max_iter=int(os.getenv("MODEL_SIMILARITY_ANN_MAX_ITER", "100")),
                early_stopping=True,
                validation_fraction=0.1,
                n_iter_no_change=8,
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


def ann_feature_group_importance(model, model_name, horizon, test, features):
    if model_name != SIMILARITY_ANN_MODEL or test.empty:
        return pd.DataFrame()
    available_groups = {
        group: [feature for feature in group_features if feature in features]
        for group, group_features in FEATURE_GROUPS.items()
    }
    available_groups = {
        group: group_features
        for group, group_features in available_groups.items()
        if group_features
    }
    if not available_groups:
        return pd.DataFrame()

    sample = test.copy()
    if len(sample) > ANN_IMPORTANCE_SAMPLE_ROWS:
        sample = sample.sample(ANN_IMPORTANCE_SAMPLE_ROWS, random_state=RANDOM_SEED + horizon)
    sample["__label"] = (sample["forward_return"] > 0).astype(int)

    rows = []
    rng = np.random.default_rng(RANDOM_SEED + horizon)
    segments = [("All", sample)]
    if "stock_type" in sample.columns:
        for stock_type, segment in sample.groupby("stock_type", dropna=False):
            if len(segment) >= 25:
                segments.append((str(stock_type), segment))

    for stock_type, segment in segments:
        labels = segment["__label"].astype(int)
        feature_frame = segment[list(features)].copy()
        baseline_probabilities = model.predict_proba(feature_frame)[:, 1]
        baseline_brier = float(brier_score_loss(labels, baseline_probabilities))
        for group, group_features in available_groups.items():
            permuted = feature_frame.copy()
            for feature in group_features:
                values = permuted[feature].to_numpy(copy=True)
                rng.shuffle(values)
                permuted[feature] = values
            permuted_probabilities = model.predict_proba(permuted)[:, 1]
            permuted_brier = float(brier_score_loss(labels, permuted_probabilities))
            rows.append(
                {
                    "horizon_days": horizon,
                    "model_name": model_name,
                    "model_label": MODEL_LABELS[model_name],
                    "model_version": MODEL_VERSION,
                    "stock_type": stock_type,
                    "feature_group": group,
                    "feature_count": len(group_features),
                    "sample_rows": len(segment),
                    "baseline_brier": baseline_brier,
                    "permuted_brier": permuted_brier,
                    "importance_delta": permuted_brier - baseline_brier,
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["horizon_days", "stock_type", "importance_delta"],
        ascending=[True, True, False],
    )


def ridge_target_feature_importance(horizon, train_pool, test_pool, features):
    """Explain continuous return, upside, and downside targets with a time-safe Ridge model."""
    if train_pool.empty or test_pool.empty or not features:
        return pd.DataFrame()
    train = sample_rows(train_pool, RIDGE_IMPORTANCE_MAX_TRAIN_ROWS).copy()
    test = sample_rows(test_pool, RIDGE_IMPORTANCE_MAX_TEST_ROWS).copy()
    train_return = pd.to_numeric(train["forward_return"], errors="coerce")
    test_return = pd.to_numeric(test["forward_return"], errors="coerce")
    targets = {
        "total_return": (train_return, test_return),
        "upside_capture": (train_return.clip(lower=0), test_return.clip(lower=0)),
        "downside_risk": (-train_return.clip(upper=0), -test_return.clip(upper=0)),
    }
    rows = []
    for target_name, (train_target, test_target) in targets.items():
        train_mask = train_target.notna()
        test_mask = test_target.notna()
        if train_mask.sum() < 100 or test_mask.sum() < 50:
            continue
        lower = float(train_target[train_mask].quantile(0.01))
        upper = float(train_target[train_mask].quantile(0.99))
        if not np.isfinite(lower) or not np.isfinite(upper) or lower >= upper:
            continue
        y_train = train_target.loc[train_mask].clip(lower=lower, upper=upper)
        y_test = test_target.loc[test_mask].clip(lower=lower, upper=upper)
        model = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            Ridge(alpha=RIDGE_ALPHA),
        )
        try:
            model.fit(train.loc[train_mask, list(features)], y_train)
            predictions = model.predict(test.loc[test_mask, list(features)])
            ridge = _pipeline_step_with_attr(model, "coef_")
            if ridge is None:
                continue
            test_r2 = float(r2_score(y_test, predictions))
            test_mae = float(mean_absolute_error(y_test, predictions))
            for feature, coefficient in zip(features, ridge.coef_):
                rows.append(
                    {
                        "horizon_days": horizon,
                        "target_name": target_name,
                        "feature": feature,
                        "coefficient": float(coefficient),
                        "absolute_coefficient": abs(float(coefficient)),
                        "ridge_alpha": RIDGE_ALPHA,
                        "train_rows": int(train_mask.sum()),
                        "test_rows": int(test_mask.sum()),
                        "target_clip_lower": lower,
                        "target_clip_upper": upper,
                        "test_r2": test_r2,
                        "test_mae": test_mae,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - explanatory output must not stop the tournament.
            print(f"{horizon}d Ridge importance for {target_name} failed: {exc}")
    output = pd.DataFrame(rows)
    if output.empty:
        return output
    output["importance_rank"] = output.groupby(
        ["horizon_days", "target_name"]
    )["absolute_coefficient"].rank(method="first", ascending=False).astype(int)
    return output.sort_values(["horizon_days", "target_name", "importance_rank"])


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


def walk_forward_horizon(labeled, horizon, usable_features):
    dates = sorted(labeled["begins_at"].unique())
    rows = []
    for split_id, (train_dates, test_dates) in enumerate(
        walk_forward_windows(dates, horizon), start=1
    ):
        train_pool = labeled[labeled["begins_at"].isin(train_dates)]
        test_pool = labeled[labeled["begins_at"].isin(test_dates)]
        for model_name in MODEL_CANDIDATES:
            features = features_for_model(model_name, usable_features)
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
    usable_features = tuple(
        feature for feature in ANN_FEATURES if train_pool[feature].notna().any()
    )
    dropped_features = tuple(feature for feature in ANN_FEATURES if feature not in usable_features)
    if not usable_features:
        raise RuntimeError(f"No usable model features remain for the {horizon}d model")
    if dropped_features:
        print(
            f"{horizon}d model dropped features without training values: "
            f"{', '.join(dropped_features)}"
        )

    evaluations = []
    importances = []
    ann_importances = []
    ridge_importances = []
    predictions = []
    ridge_importance = ridge_target_feature_importance(
        horizon, train_pool, test_pool, usable_features
    )
    if not ridge_importance.empty:
        ridge_importances.append(ridge_importance)
    for model_name in MODEL_CANDIDATES:
        model_features = features_for_model(model_name, usable_features)
        expected_features = ANN_FEATURES if model_name == SIMILARITY_ANN_MODEL else FEATURES
        model_dropped_features = tuple(
            feature for feature in expected_features if feature not in model_features
        )
        train = sample_rows(
            train_pool, model_row_limit(model_name, "train", MAX_TRAIN_ROWS)
        )
        test = sample_rows(
            test_pool, model_row_limit(model_name, "test", MAX_TEST_ROWS)
        )
        model = build_estimator(model_name)
        try:
            if not model_features:
                raise RuntimeError("no usable features for model candidate")
            evaluation = evaluate_model(
                model, model_name, horizon, train, test, train_dates, test_dates, model_features
            )
            latest = latest_predictions(model, frame, horizon, model_features, model_name)
            importance = feature_importance(model, horizon, model_features, model_name)
            ann_importance = ann_feature_group_importance(
                model, model_name, horizon, test, model_features
            )
            predictions.append(latest)
            if not importance.empty:
                importances.append(importance)
            if not ann_importance.empty:
                ann_importances.append(ann_importance)
        except Exception as exc:  # noqa: BLE001 - keep pipeline alive if one candidate fails.
            evaluation = failed_evaluation(
                model_name,
                horizon,
                exc,
                train_dates,
                test_dates,
                model_features,
                model_dropped_features,
            )
            print(f"{horizon}d {model_name} failed: {exc}")
        evaluation["dropped_features"] = ", ".join(model_dropped_features)
        evaluations.append(evaluation)
    walk_forward_rows = walk_forward_horizon(labeled, horizon, usable_features)
    evaluations = attach_walk_forward_summary(evaluations, walk_forward_rows)
    return (
        evaluations,
        importances,
        ann_importances,
        ridge_importances,
        predictions,
        walk_forward_rows,
    )


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


def ensure_append_columns(conn, table, frame):
    if not table_exists(conn, table):
        return
    existing = {
        row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')
    }
    for column in frame.columns:
        if column not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" TEXT')


def append_run_frame(conn, table, frame, run_id):
    if frame.empty:
        return 0
    ensure_append_columns(conn, table, frame)
    delete_existing_run(conn, table, run_id)
    frame.to_sql(table, conn, if_exists="append", index=False)
    return len(frame)


def prune_history_table(conn, table, created_at):
    if MODEL_MEMORY_RETENTION_DAYS <= 0 or not table_exists(conn, table):
        return
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    if "run_created_at" not in columns:
        return
    parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    cutoff = parsed - timedelta(days=MODEL_MEMORY_RETENTION_DAYS)
    conn.execute(
        f'DELETE FROM "{table}" WHERE run_created_at < ?',
        (cutoff.isoformat(timespec="seconds"),),
    )


def prune_model_memory(conn, run_metadata):
    for table in (
        "MLRunHistory",
        "ModelEvaluationHistory",
        "ModelWalkForwardEvaluationHistory",
        "ModelPredictionHistory",
        "ANNFeatureGroupImportanceHistory",
        "MonteCarloSimulationHistory",
    ):
        prune_history_table(conn, table, run_metadata["created_at"])


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


def similarity_family_table():
    families = read_csv_if_exists(ANALYTICS_DIR / "variant_families.csv")
    columns = [
        "Family",
        "ticker",
        "Chosen",
        "Reason",
        "variant_family_size",
        "variant_is_chosen",
    ]
    if families.empty or not {"Family", "Member"}.issubset(families.columns):
        return pd.DataFrame(columns=columns)
    frame = families.copy()
    for column in ("Chosen", "Reason"):
        if column not in frame.columns:
            frame[column] = ""
    frame["ticker"] = frame["Member"].map(normalize_ticker)
    frame["variant_family_size"] = frame.groupby("Family")["Member"].transform("count")
    frame["variant_is_chosen"] = frame["Chosen"].astype(str).str.lower().isin(
        {"true", "1", "yes"}
    ).astype(int)
    return frame[columns].drop_duplicates(["Family", "ticker"]).reset_index(drop=True)


def persist_similarity_artifacts(conn):
    pairs = load_similarity_pairs()
    families = similarity_family_table()
    pairs.to_sql("SimilarityPairs", conn, if_exists="replace", index=False)
    families.to_sql("SimilarityFamilies", conn, if_exists="replace", index=False)
    if table_exists(conn, "SimilarityPairs"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_similarity_pairs_a "
            "ON SimilarityPairs(A, similarity)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_similarity_pairs_b "
            "ON SimilarityPairs(B, similarity)"
        )
    if table_exists(conn, "SimilarityFamilies"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_similarity_families_ticker "
            "ON SimilarityFamilies(ticker)"
        )
    return {"similarity_pairs": len(pairs), "similarity_families": len(families)}


def neighbor_map_from_pairs(pairs):
    neighbors = {}
    if pairs.empty:
        return neighbors
    for row in pairs.sort_values("similarity", ascending=False).itertuples(index=False):
        a = normalize_ticker(getattr(row, "A"))
        b = normalize_ticker(getattr(row, "B"))
        similarity = pd.to_numeric(getattr(row, "similarity"), errors="coerce")
        if not a or not b or pd.isna(similarity):
            continue
        neighbors.setdefault(a, []).append((b, float(similarity)))
        neighbors.setdefault(b, []).append((a, float(similarity)))
    return {
        ticker: [
            neighbor
            for neighbor, _ in sorted(items, key=lambda item: item[1], reverse=True)[
                :MONTE_CARLO_NEIGHBOR_LIMIT
            ]
        ]
        for ticker, items in neighbors.items()
    }


def historical_forward_returns(frame, horizon):
    target_column = f"future_price_{horizon}d"
    returns = frame[["ticker", "close_price", target_column]].copy()
    returns["ticker"] = returns["ticker"].map(normalize_ticker)
    returns["forward_return"] = (
        pd.to_numeric(returns[target_column], errors="coerce")
        / pd.to_numeric(returns["close_price"], errors="coerce")
        - 1.0
    )
    returns["forward_return"] = returns["forward_return"].replace([np.inf, -np.inf], np.nan)
    return returns.dropna(subset=["forward_return"])


def monte_carlo_return_draws(rng, return_pool, probability_up):
    pool = np.asarray(return_pool, dtype=float)
    pool = pool[np.isfinite(pool)]
    if len(pool) == 0:
        return np.zeros(MONTE_CARLO_SIMULATIONS, dtype=float)

    probability = float(np.clip(probability_up, 0.0, 1.0))
    positive_pool = pool[pool > 0]
    negative_pool = pool[pool <= 0]
    if len(positive_pool) == 0 or len(negative_pool) == 0:
        return rng.choice(pool, size=MONTE_CARLO_SIMULATIONS, replace=True)

    positive_mask = rng.random(MONTE_CARLO_SIMULATIONS) < probability
    draws = np.empty(MONTE_CARLO_SIMULATIONS, dtype=float)
    draws[positive_mask] = rng.choice(
        positive_pool,
        size=int(positive_mask.sum()),
        replace=True,
    )
    draws[~positive_mask] = rng.choice(
        negative_pool,
        size=int((~positive_mask).sum()),
        replace=True,
    )
    return draws


def simulation_candidates(all_predictions, champions):
    preferred = all_predictions[all_predictions["model_name"].eq(SIMILARITY_ANN_MODEL)].copy()
    if preferred.empty:
        return filter_champion_rows(all_predictions, champions)
    return preferred


def monte_carlo_outputs(frame, all_predictions, champions):
    summary_columns = [
        "as_of_date",
        "ticker",
        "horizon_days",
        "model_name",
        "model_label",
        "model_version",
        "model_rank",
        "probability_up",
        "current_price",
        "stock_type",
        "simulations",
        "neighbor_count",
        "median_return",
        "p10_return",
        "p90_return",
        "expected_return",
        "simulated_probability_up",
        "drawdown_probability",
        "target_probability",
    ]
    path_columns = [
        "as_of_date",
        "ticker",
        "horizon_days",
        "model_name",
        "model_label",
        "model_version",
        "trading_day",
        "current_price",
        "p10_price",
        "median_price",
        "p90_price",
    ]
    if all_predictions.empty:
        return pd.DataFrame(columns=summary_columns), pd.DataFrame(columns=path_columns)

    latest_context = (
        frame.sort_values(["ticker", "begins_at"])
        .groupby("ticker", as_index=False)
        .tail(1)[["ticker", "close_price", "stock_type"]]
        .copy()
    )
    latest_context["ticker"] = latest_context["ticker"].map(normalize_ticker)
    latest_context = latest_context.rename(columns={"close_price": "current_price"})
    candidates = simulation_candidates(all_predictions, champions).copy()
    candidates["ticker"] = candidates["ticker"].map(normalize_ticker)
    candidates = candidates.merge(latest_context, on="ticker", how="left")

    pairs = load_similarity_pairs()
    neighbors = neighbor_map_from_pairs(pairs)
    rng = np.random.default_rng(RANDOM_SEED)
    summary_rows = []
    path_rows = []

    for horizon, horizon_candidates in candidates.groupby("horizon_days"):
        horizon = int(horizon)
        returns = historical_forward_returns(frame, horizon)
        by_ticker = {
            ticker: group["forward_return"].to_numpy(dtype=float)
            for ticker, group in returns.groupby("ticker")
        }
        market_pool = returns["forward_return"].to_numpy(dtype=float)
        shown = horizon_candidates.sort_values("model_rank").head(
            MONTE_CARLO_LIMIT_PER_HORIZON
        )
        for path_rank, row in enumerate(shown.itertuples(index=False), start=1):
            ticker = normalize_ticker(row.ticker)
            peer_tickers = [ticker, *neighbors.get(ticker, [])]
            pools = [by_ticker[peer] for peer in peer_tickers if peer in by_ticker]
            return_pool = np.concatenate(pools) if pools else market_pool
            probability = pd.to_numeric(getattr(row, "probability_up"), errors="coerce")
            if pd.isna(probability):
                probability = 0.5
            draws = monte_carlo_return_draws(rng, return_pool, probability)
            current_price = pd.to_numeric(getattr(row, "current_price"), errors="coerce")
            stock_type = getattr(row, "stock_type", "Unknown")
            stock_type = "Unknown" if pd.isna(stock_type) else str(stock_type)
            summary = {
                "as_of_date": getattr(row, "as_of_date"),
                "ticker": ticker,
                "horizon_days": horizon,
                "model_name": getattr(row, "model_name"),
                "model_label": getattr(row, "model_label"),
                "model_version": getattr(row, "model_version"),
                "model_rank": int(getattr(row, "model_rank")),
                "probability_up": float(probability),
                "current_price": np.nan if pd.isna(current_price) else float(current_price),
                "stock_type": stock_type,
                "simulations": MONTE_CARLO_SIMULATIONS,
                "neighbor_count": max(0, len(peer_tickers) - 1),
                "median_return": float(np.quantile(draws, 0.50)),
                "p10_return": float(np.quantile(draws, 0.10)),
                "p90_return": float(np.quantile(draws, 0.90)),
                "expected_return": float(np.mean(draws)),
                "simulated_probability_up": float(np.mean(draws > 0)),
                "drawdown_probability": float(np.mean(draws <= MONTE_CARLO_DRAWDOWN_THRESHOLD)),
                "target_probability": float(np.mean(draws >= MONTE_CARLO_TARGET_THRESHOLD)),
            }
            summary_rows.append(summary)

            if (
                path_rank <= MONTE_CARLO_PATH_LIMIT_PER_HORIZON
                and not pd.isna(current_price)
                and float(current_price) > 0
            ):
                for trading_day in range(horizon + 1):
                    fraction = trading_day / horizon
                    path_rows.append(
                        {
                            "as_of_date": summary["as_of_date"],
                            "ticker": ticker,
                            "horizon_days": horizon,
                            "model_name": summary["model_name"],
                            "model_label": summary["model_label"],
                            "model_version": summary["model_version"],
                            "trading_day": trading_day,
                            "current_price": float(current_price),
                            "p10_price": float(current_price)
                            * (1.0 + summary["p10_return"] * fraction),
                            "median_price": float(current_price)
                            * (1.0 + summary["median_return"] * fraction),
                            "p90_price": float(current_price)
                            * (1.0 + summary["p90_return"] * fraction),
                        }
                    )

    return (
        pd.DataFrame(summary_rows, columns=summary_columns),
        pd.DataFrame(path_rows, columns=path_columns),
    )


def save_outputs(
    conn,
    frame,
    evaluations,
    importances,
    ann_importances,
    ridge_importances,
    predictions,
    walk_forward_rows,
    run_metadata,
):
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

    if ann_importances:
        all_ann_importance = pd.concat(ann_importances, ignore_index=True)
    else:
        all_ann_importance = pd.DataFrame(
            columns=[
                "horizon_days",
                "model_name",
                "model_label",
                "model_version",
                "stock_type",
                "feature_group",
                "feature_count",
                "sample_rows",
                "baseline_brier",
                "permuted_brier",
                "importance_delta",
            ]
        )
    if ridge_importances:
        all_ridge_importance = pd.concat(ridge_importances, ignore_index=True)
    else:
        all_ridge_importance = pd.DataFrame(
            columns=[
                "horizon_days",
                "target_name",
                "feature",
                "coefficient",
                "absolute_coefficient",
                "importance_rank",
                "ridge_alpha",
                "train_rows",
                "test_rows",
                "target_clip_lower",
                "target_clip_upper",
                "test_r2",
                "test_mae",
            ]
        )
    monte_carlo_summary, monte_carlo_paths = monte_carlo_outputs(
        frame, all_predictions, champions
    )
    similarity_counts = persist_similarity_artifacts(conn)

    champions.to_sql("ModelEvaluation", conn, if_exists="replace", index=False)
    tournament.to_sql("ModelTournamentEvaluation", conn, if_exists="replace", index=False)
    importance.to_sql("ModelFeatureImportance", conn, if_exists="replace", index=False)
    all_importance.to_sql("ModelTournamentFeatureImportance", conn, if_exists="replace", index=False)
    all_ann_importance.to_sql(
        "ANNFeatureGroupImportance", conn, if_exists="replace", index=False
    )
    all_ridge_importance.to_sql(
        "RidgeTargetFeatureImportance", conn, if_exists="replace", index=False
    )
    latest.to_sql("LatestModelPredictions", conn, if_exists="replace", index=False)
    all_predictions.to_sql("LatestModelCandidatePredictions", conn, if_exists="replace", index=False)
    monte_carlo_summary.to_sql(
        "LatestMonteCarloSimulations", conn, if_exists="replace", index=False
    )
    monte_carlo_paths.to_sql("LatestMonteCarloPaths", conn, if_exists="replace", index=False)
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
                "ann_feature_group_rows": len(all_ann_importance),
                "ridge_target_feature_rows": len(all_ridge_importance),
                "monte_carlo_rows": len(monte_carlo_summary),
                **similarity_counts,
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
    ann_importance_history = with_run_columns(all_ann_importance, run_metadata)
    ridge_importance_history = with_run_columns(all_ridge_importance, run_metadata)
    monte_carlo_history = with_run_columns(monte_carlo_summary, run_metadata)
    append_run_frame(
        conn,
        "ANNFeatureGroupImportanceHistory",
        ann_importance_history,
        run_metadata["run_id"],
    )
    append_run_frame(
        conn,
        "RidgeTargetFeatureImportanceHistory",
        ridge_importance_history,
        run_metadata["run_id"],
    )
    append_run_frame(
        conn,
        "MonteCarloSimulationHistory",
        monte_carlo_history,
        run_metadata["run_id"],
    )
    prune_model_memory(conn, run_metadata)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_latest_model_predictions "
        "ON LatestModelPredictions(horizon_days, model_rank)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_latest_model_candidate_predictions "
        "ON LatestModelCandidatePredictions(horizon_days, model_name, model_rank)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ann_feature_group_importance "
        "ON ANNFeatureGroupImportance(horizon_days, stock_type, feature_group)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ridge_target_feature_importance "
        "ON RidgeTargetFeatureImportance(horizon_days, target_name, importance_rank)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_latest_monte_carlo_simulations "
        "ON LatestMonteCarloSimulations(horizon_days, model_rank)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_latest_monte_carlo_paths "
        "ON LatestMonteCarloPaths(ticker, horizon_days, trading_day)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ann_feature_group_importance_history "
        "ON ANNFeatureGroupImportanceHistory(run_created_at, horizon_days, stock_type, feature_group)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_monte_carlo_simulation_history "
        "ON MonteCarloSimulationHistory(run_created_at, horizon_days, ticker)"
    )
    for table in (
        "MLRunHistory",
        "ModelEvaluationHistory",
        "ModelWalkForwardEvaluationHistory",
        "ModelPredictionHistory",
        "ANNFeatureGroupImportanceHistory",
        "RidgeTargetFeatureImportanceHistory",
        "MonteCarloSimulationHistory",
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
    all_ann_importance.to_csv(ANALYTICS_DIR / "ann_feature_group_importance.csv", index=False)
    all_ridge_importance.to_csv(
        ANALYTICS_DIR / "ridge_target_feature_importance.csv", index=False
    )
    latest.to_csv(ANALYTICS_DIR / "latest_model_predictions.csv", index=False)
    all_predictions.to_csv(ANALYTICS_DIR / "latest_model_candidate_predictions.csv", index=False)
    monte_carlo_summary.to_csv(
        ANALYTICS_DIR / "latest_monte_carlo_simulations.csv", index=False
    )
    monte_carlo_paths.to_csv(ANALYTICS_DIR / "latest_monte_carlo_paths.csv", index=False)
    ann_importance_history.to_csv(
        ANALYTICS_DIR / "ann_feature_group_importance_history_latest.csv", index=False
    )
    ridge_importance_history.to_csv(
        ANALYTICS_DIR / "ridge_target_feature_importance_history_latest.csv", index=False
    )
    monte_carlo_history.to_csv(
        ANALYTICS_DIR / "monte_carlo_simulation_history_latest.csv", index=False
    )
    load_similarity_pairs().to_csv(ANALYTICS_DIR / "similarity_pairs_export.csv", index=False)
    similarity_family_table().to_csv(
        ANALYTICS_DIR / "similarity_families_export.csv", index=False
    )
    prediction_history.to_csv(ANALYTICS_DIR / "model_prediction_history_latest.csv", index=False)
    return champions, tournament


def main():
    if not DB_PATH.exists():
        raise RuntimeError(f"{DB_PATH} is required")
    with closing(sqlite3.connect(DB_PATH)) as conn:
        with timed_stage("load leakage-controlled frame"):
            frame = load_frame(conn)
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run_metadata = build_run_metadata(frame, created_at)
        evaluations = []
        importances = []
        ann_importances = []
        ridge_importances = []
        predictions = []
        walk_forward_rows = []
        for horizon in HORIZONS:
            with timed_stage(f"build {horizon}d model horizon"):
                (
                    horizon_evaluations,
                    horizon_importances,
                    horizon_ann_importances,
                    horizon_ridge_importances,
                    horizon_predictions,
                    horizon_walk_forward,
                ) = build_horizon(frame, horizon)
            evaluations.extend(horizon_evaluations)
            importances.extend(horizon_importances)
            ann_importances.extend(horizon_ann_importances)
            ridge_importances.extend(horizon_ridge_importances)
            predictions.extend(horizon_predictions)
            walk_forward_rows.extend(horizon_walk_forward)
        with timed_stage("save model outputs and compact history"):
            champions, tournament = save_outputs(
                conn,
                frame,
                evaluations,
                importances,
                ann_importances,
                ridge_importances,
                predictions,
                walk_forward_rows,
                run_metadata,
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
