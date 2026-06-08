import os
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from dashboard import automatic_paper_decisions as paper_decisions
from dashboard import paper_outcomes


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "dashboard_data.db"
PAPER_SNAPSHOT = ROOT / "dashboard" / "paper_learning_snapshot.json"


def database_path():
    return Path(os.getenv("DASHBOARD_DB_PATH", DEFAULT_DB))


@contextmanager
def connect():
    path = database_path()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run: python scripts/export_dashboard_data.py"
        )
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        yield conn
    finally:
        conn.close()


def query(sql, params=()):
    with connect() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def table_exists(table):
    with connect() as conn:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None


def table_columns(table):
    if not table_exists(table):
        return set()
    with connect() as conn:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def health():
    frame = query("SELECT metric, value FROM PipelineHealth ORDER BY metric")
    return dict(zip(frame["metric"], frame["value"]))


def shortlist():
    return query(
        """
        SELECT rank, ticker, begins_at, trend_slope_60d, ret_60d, vol_60d,
               AvgDollarVol, Days
        FROM LatestShortlist
        ORDER BY rank
        """
    )


def watchlist():
    return query(
        """
        SELECT rank, ticker, confidence, recommendation, suggested_horizon,
               is_persistent, leader_score, trend_score, trend_slope_60d,
               trend_r2_60d, vol_60d, dollar_vol_20d, total_return,
               entry_price
        FROM LatestWatchlist
        ORDER BY rank
        """
    )


def watchlist_performance_summary():
    return query(
        """
        SELECT horizon, evaluated_picks, average_return, win_rate
        FROM WatchlistPerformanceSummary
        ORDER BY CASE horizon WHEN '1d' THEN 1 WHEN '5d' THEN 2
                              WHEN '20d' THEN 3 WHEN '60d' THEN 4 END
        """
    )


def performance_summary():
    return query(
        """
        SELECT horizon, evaluated_picks, average_return, win_rate
        FROM PerformanceSummary
        ORDER BY CASE horizon WHEN '1d' THEN 1 WHEN '5d' THEN 2
                              WHEN '20d' THEN 3 WHEN '60d' THEN 4 END
        """
    )


def automatic_paper_decisions():
    if PAPER_SNAPSHOT.exists():
        payload = json.loads(PAPER_SNAPSHOT.read_text(encoding="utf-8"))
        return pd.DataFrame(
            payload.get("decisions", []), columns=paper_decisions.LEDGER_COLUMNS
        )
    if not table_exists("AutomaticPaperDecisions"):
        return pd.DataFrame()
    return query(
        """
        SELECT *
        FROM AutomaticPaperDecisions
        ORDER BY source_date DESC, watchlist_rank, ticker
        """
    )


def automatic_paper_outcomes():
    if PAPER_SNAPSHOT.exists():
        payload = json.loads(PAPER_SNAPSHOT.read_text(encoding="utf-8"))
        return pd.DataFrame(
            payload.get("outcomes", []), columns=paper_outcomes.OUTCOME_COLUMNS
        )
    if not table_exists("AutomaticPaperOutcomeEvents"):
        return pd.DataFrame()
    return query(
        """
        SELECT *
        FROM AutomaticPaperOutcomeEvents
        ORDER BY evaluation_date DESC, evaluation_horizon_days, ticker
        """
    )


def paper_learning_status():
    decisions = automatic_paper_decisions()
    outcomes = automatic_paper_outcomes()
    if decisions.empty:
        return {
            "decisions": 0,
            "open": 0,
            "matured": 0,
            "outcome_events": 0,
            "unavailable": 0,
            "unavailable_decisions": 0,
        }
    declared = decisions[["decision_id", "horizon_days"]].copy()
    declared["horizon_days"] = pd.to_numeric(declared["horizon_days"], errors="coerce")
    matured_ids = set()
    unavailable_ids = set()
    if not outcomes.empty:
        event_horizon = pd.to_numeric(
            outcomes["evaluation_horizon_days"], errors="coerce"
        )
        decision_horizon = pd.to_numeric(
            outcomes["decision_horizon_days"], errors="coerce"
        )
        matured_ids = set(
            outcomes.loc[
                (event_horizon == decision_horizon)
                & outcomes["status"].isin(["matured", "stopped", "targeted"]),
                "decision_id",
            ].astype(str)
        )
        unavailable_ids = set(
            outcomes.loc[
                (event_horizon == decision_horizon)
                & outcomes["status"].eq("unavailable"),
                "decision_id",
            ].astype(str)
        )
        unavailable_ids -= matured_ids
    resolved_ids = matured_ids | unavailable_ids
    return {
        "decisions": len(decisions),
        "open": int((~declared["decision_id"].astype(str).isin(resolved_ids)).sum()),
        "matured": len(matured_ids),
        "outcome_events": len(outcomes),
        "unavailable_decisions": len(unavailable_ids),
        "unavailable": (
            int((outcomes["status"] == "unavailable").sum()) if not outcomes.empty else 0
        ),
    }


def shortlist_history():
    return query(
        """
        SELECT as_of_date, rank, ticker, entry_price, trend_slope_60d, ret_60d,
               vol_60d, avg_dollar_vol, fwd_return_1d, fwd_return_5d,
               fwd_return_20d, fwd_return_60d
        FROM ShortlistHistory
        ORDER BY as_of_date DESC, rank
        """
    )


def tickers():
    frame = query("SELECT ticker FROM FeatureSummary ORDER BY ticker")
    return frame["ticker"].tolist()


def ticker_summary(ticker):
    return query("SELECT * FROM FeatureSummary WHERE ticker=?", (ticker,))


def ticker_prices(ticker):
    return query(
        """
        SELECT begins_at, close_price, volume
        FROM RecentPrices
        WHERE ticker=?
        ORDER BY begins_at
        """,
        (ticker,),
    )


def opportunity_map(limit=150):
    return query(
        """
        SELECT ticker, Leader_Score, Trend_Score, Trend_Slope_60d,
               Vol_60d, DollarVol_20d, Total_Return
        FROM FeatureSummary
        WHERE Leader_Score IS NOT NULL
          AND Trend_Score IS NOT NULL
          AND Vol_60d IS NOT NULL
          AND DollarVol_20d IS NOT NULL
        ORDER BY Leader_Score DESC
        LIMIT ?
        """,
        (limit,),
    )


def shortlist_prices():
    return query(
        """
        SELECT prices.ticker, prices.begins_at, prices.close_price
        FROM RecentPrices AS prices
        INNER JOIN LatestShortlist AS shortlist
                ON shortlist.ticker = prices.ticker
        ORDER BY prices.begins_at, prices.ticker
        """
    )


def stock_universe():
    return query(
        """
        SELECT ticker, status, reason, coordinate_mode, x, y, z,
               Leader_Score, Trend_Score, Vol_60d, DollarVol_20d, Total_Return
        FROM StockUniverse
        ORDER BY status, ticker
        """
    )


def stock_universe_dates():
    frame = query(
        """
        SELECT DISTINCT as_of_date
        FROM StockUniverseSnapshot
        ORDER BY as_of_date
        """
    )
    return frame["as_of_date"].tolist()


def stock_universe_snapshot(as_of_date):
    return query(
        """
        WITH dates AS (
          SELECT
            ? AS selected_date,
            (SELECT MAX(as_of_date) FROM StockUniverseSnapshot WHERE as_of_date < ?) AS previous_date,
            (SELECT MAX(as_of_date) FROM StockUniverseSnapshot
             WHERE as_of_date < (
               SELECT MAX(as_of_date) FROM StockUniverseSnapshot WHERE as_of_date < ?
             )) AS earlier_date
        ),
        current AS (
          SELECT * FROM StockUniverseSnapshot WHERE as_of_date=(SELECT selected_date FROM dates)
        ),
        previous AS (
          SELECT * FROM StockUniverseSnapshot WHERE as_of_date=(SELECT previous_date FROM dates)
        ),
        earlier AS (
          SELECT * FROM StockUniverseSnapshot WHERE as_of_date=(SELECT earlier_date FROM dates)
        )
        SELECT current.ticker, current.status, current.reason, current.coordinate_mode,
               current.x, current.y, current.z, current.Leader_Score,
               current.Trend_Score, current.Vol_60d, current.DollarVol_20d,
               current.Total_Return,
               CASE WHEN previous.ticker IS NOT NULL THEN
                 sqrt(
                   (current.x - previous.x) * (current.x - previous.x) +
                   (current.y - previous.y) * (current.y - previous.y) +
                   (current.z - previous.z) * (current.z - previous.z)
                 )
               END AS movement_speed,
               CASE WHEN previous.ticker IS NOT NULL AND earlier.ticker IS NOT NULL THEN
                 sqrt(
                   (current.x - previous.x) * (current.x - previous.x) +
                   (current.y - previous.y) * (current.y - previous.y) +
                   (current.z - previous.z) * (current.z - previous.z)
                 ) -
                 sqrt(
                   (previous.x - earlier.x) * (previous.x - earlier.x) +
                   (previous.y - earlier.y) * (previous.y - earlier.y) +
                   (previous.z - earlier.z) * (previous.z - earlier.z)
                 )
               END AS movement_acceleration
        FROM current
        LEFT JOIN previous ON previous.ticker=current.ticker
        LEFT JOIN earlier ON earlier.ticker=current.ticker
        ORDER BY current.status, current.ticker
        """,
        (as_of_date, as_of_date, as_of_date),
    )


def stock_universe_trails(tickers, end_date, trail_dates):
    if not tickers:
        return pd.DataFrame(columns=["as_of_date", "ticker", "x", "y", "z"])
    placeholders = ",".join("?" for _ in tickers)
    return query(
        f"""
        SELECT as_of_date, ticker, x, y, z
        FROM StockUniverseSnapshot
        WHERE ticker IN ({placeholders})
          AND as_of_date <= ?
          AND as_of_date IN (
            SELECT DISTINCT as_of_date
            FROM StockUniverseSnapshot
            WHERE as_of_date <= ?
            ORDER BY as_of_date DESC
            LIMIT ?
          )
        ORDER BY ticker, as_of_date
        """,
        (*tickers, end_date, end_date, trail_dates),
    )


def stock_universe_snapshot_count():
    frame = query("SELECT COUNT(DISTINCT as_of_date) AS snapshots FROM StockUniverseSnapshot")
    return int(frame.iloc[0]["snapshots"])


def span_health():
    return query(
        """
        SELECT metric, value
        FROM PipelineHealth
        WHERE metric LIKE '%_rows'
        ORDER BY metric
        """
    )


def model_status():
    required = [
        ("ModelEvaluation", "model_evaluation_rows", 3),
        ("ModelFeatureImportance", "model_feature_importance_rows", 1),
        ("LatestModelPredictions", "latest_model_predictions_rows", 1),
    ]
    health_values = health()
    rows = []
    with connect() as conn:
        for table, health_key, minimum_rows in required:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone() is not None
            actual_rows = 0
            if exists:
                actual_rows = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            health_rows = pd.to_numeric(health_values.get(health_key), errors="coerce")
            if not exists:
                status = "missing table"
            elif actual_rows < minimum_rows:
                status = "not enough rows"
            else:
                status = "ready"
            rows.append(
                {
                    "table": table,
                    "status": status,
                    "rows": int(actual_rows),
                    "health_metric": health_key,
                    "health_rows": None if pd.isna(health_rows) else int(health_rows),
                    "minimum_rows": minimum_rows,
                }
            )
    return pd.DataFrame(rows)


def model_horizon_status():
    if not table_exists("LatestModelPredictions"):
        return pd.DataFrame(columns=["horizon_days", "rows", "latest_prediction_date"])
    return query(
        """
        SELECT horizon_days,
               COUNT(*) AS rows,
               MAX(as_of_date) AS latest_prediction_date
        FROM LatestModelPredictions
        GROUP BY horizon_days
        ORDER BY horizon_days
        """
    )


def model_evaluation():
    if not table_exists("ModelEvaluation"):
        return pd.DataFrame()
    return query("SELECT * FROM ModelEvaluation ORDER BY horizon_days")


def model_feature_importance(horizon_days):
    if not table_exists("ModelFeatureImportance"):
        return pd.DataFrame()
    return query(
        """
        SELECT feature, coefficient, absolute_coefficient
        FROM ModelFeatureImportance
        WHERE horizon_days=?
        ORDER BY absolute_coefficient DESC
        """,
        (horizon_days,),
    )


def latest_model_predictions(horizon_days, limit=50):
    if not table_exists("LatestModelPredictions"):
        return pd.DataFrame()
    columns = table_columns("LatestModelPredictions")
    optional_columns = []
    for column in ("probability_bucket", "top_positive_drivers", "top_negative_drivers"):
        if column in columns:
            optional_columns.append(column)
        else:
            optional_columns.append(f"'' AS {column}")
    return query(
        f"""
        SELECT model_rank, ticker, probability_up,
               {', '.join(optional_columns)}, as_of_date
        FROM LatestModelPredictions
        WHERE horizon_days=?
        ORDER BY model_rank
        LIMIT ?
        """,
        (horizon_days, limit),
    )


def trade_research_queue(horizon_days, limit=25):
    if not table_exists("LatestModelPredictions"):
        return pd.DataFrame()
    columns = table_columns("LatestModelPredictions")
    probability_bucket = (
        "model.probability_bucket"
        if "probability_bucket" in columns
        else "'' AS probability_bucket"
    )
    positive_drivers = (
        "model.top_positive_drivers"
        if "top_positive_drivers" in columns
        else "'' AS top_positive_drivers"
    )
    negative_drivers = (
        "model.top_negative_drivers"
        if "top_negative_drivers" in columns
        else "'' AS top_negative_drivers"
    )
    return query(
        f"""
        SELECT watch.rank AS watchlist_rank,
               model.model_rank,
               watch.ticker,
               model.probability_up,
               {probability_bucket},
               watch.confidence,
               watch.recommendation,
               watch.suggested_horizon,
               watch.is_persistent,
               watch.trend_slope_60d,
               watch.trend_r2_60d,
               watch.vol_60d,
               watch.dollar_vol_20d,
               watch.total_return,
               watch.entry_price,
               {positive_drivers},
               {negative_drivers},
               model.as_of_date
        FROM LatestWatchlist AS watch
        INNER JOIN LatestModelPredictions AS model
          ON model.ticker = watch.ticker
         AND model.horizon_days = ?
        WHERE model.probability_up >= 0.55
        ORDER BY model.probability_up DESC, watch.confidence DESC, watch.rank
        LIMIT ?
        """,
        (horizon_days, limit),
    )
