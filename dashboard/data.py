import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "dashboard_data.db"


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
               trend_r2_60d, vol_60d, dollar_vol_20d, total_return
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
