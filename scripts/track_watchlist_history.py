import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
VEC_DB = ROOT / "vectorized.db"
HIST_DB = ROOT / "historicals.db"
LATEST_CSV = ROOT / "analytics" / "latest_watchlist.csv"
HISTORY_CSV = ROOT / "analytics" / "watchlist_history.csv"
SUMMARY_CSV = ROOT / "analytics" / "watchlist_performance_summary.csv"
SPAN = "5year"
WATCHLIST_LIMIT = 50
HORIZONS = (1, 5, 20, 60)
PERSISTENCE_BONUS = 0.04


def create_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS WatchlistHistory (
          as_of_date TEXT NOT NULL, ticker TEXT NOT NULL, rank INTEGER NOT NULL,
          raw_score REAL NOT NULL, adjusted_score REAL NOT NULL,
          confidence REAL NOT NULL, recommendation TEXT NOT NULL,
          suggested_horizon TEXT NOT NULL, is_persistent INTEGER NOT NULL,
          leader_score REAL, trend_score REAL, trend_slope_60d REAL,
          trend_r2_60d REAL, vol_60d REAL, riskadj_mom_60d REAL,
          dollar_vol_20d REAL, total_return REAL, entry_price REAL,
          fwd_return_1d REAL, fwd_return_5d REAL,
          fwd_return_20d REAL, fwd_return_60d REAL,
          evaluated_at TEXT NOT NULL,
          PRIMARY KEY (as_of_date, ticker)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_watchlist_history_ticker "
        "ON WatchlistHistory(ticker, as_of_date)"
    )


def percentile(series, ascending=True):
    return series.rank(pct=True, ascending=ascending, na_option="bottom").fillna(0.0)


def previous_watchlist(conn):
    latest = conn.execute("SELECT MAX(as_of_date) FROM WatchlistHistory").fetchone()[0]
    if not latest:
        return set()
    return {
        row[0]
        for row in conn.execute(
            "SELECT ticker FROM WatchlistHistory WHERE as_of_date=?", (latest,)
        )
    }


def suggested_horizon(row):
    if row["confidence"] >= 80 and row["trend_r2_60d"] >= 0.35 and row["vol_60d"] <= 0.05:
        return "20d"
    if row["confidence"] >= 65:
        return "5d"
    return "watch"


def recommendation(row):
    if row["rank"] <= 10 and row["confidence"] >= 75:
        return "consider entry"
    if row["rank"] <= 25:
        return "research"
    return "watch"


def build_watchlist(conn):
    frame = pd.read_sql_query(
        """
        SELECT ticker, Leader_Score AS leader_score, Trend_Score AS trend_score,
               Trend_Slope_60d AS trend_slope_60d, Trend_R2_60d AS trend_r2_60d,
               Vol_60d AS vol_60d, RiskAdj_Mom_60d AS riskadj_mom_60d,
               DollarVol_20d AS dollar_vol_20d, Total_Return AS total_return
        FROM FeatureSummary
        WHERE Leader_Score IS NOT NULL
          AND Trend_Score IS NOT NULL
          AND Trend_Slope_60d IS NOT NULL
          AND Vol_60d IS NOT NULL
          AND DollarVol_20d IS NOT NULL
        """,
        conn,
    )
    if frame.empty:
        raise RuntimeError("FeatureSummary has no scoreable watchlist candidates")

    frame["raw_score"] = (
        0.30 * percentile(frame["leader_score"])
        + 0.20 * percentile(frame["trend_score"])
        + 0.15 * percentile(frame["trend_slope_60d"])
        + 0.15 * percentile(frame["riskadj_mom_60d"])
        + 0.10 * percentile(frame["trend_r2_60d"])
        + 0.05 * percentile(frame["dollar_vol_20d"])
        + 0.05 * percentile(frame["vol_60d"], ascending=False)
    )
    prior = previous_watchlist(conn)
    frame["is_persistent"] = frame["ticker"].isin(prior).astype(int)
    frame["adjusted_score"] = (
        frame["raw_score"] + PERSISTENCE_BONUS * frame["is_persistent"]
    ).clip(upper=1.0)
    frame["confidence"] = frame["adjusted_score"] * 100.0
    frame = frame.sort_values(
        ["adjusted_score", "leader_score", "dollar_vol_20d"],
        ascending=False,
    ).head(WATCHLIST_LIMIT)
    frame = frame.reset_index(drop=True)
    frame["rank"] = np.arange(1, len(frame) + 1)
    frame["suggested_horizon"] = frame.apply(suggested_horizon, axis=1)
    frame["recommendation"] = frame.apply(recommendation, axis=1)
    return frame


def latest_prices(conn, ticker, as_of_date):
    return conn.execute(
        """
        SELECT close_price
        FROM HistoricalPrices
        WHERE ticker=? AND span=? AND begins_at >= ?
          AND close_price IS NOT NULL
        ORDER BY begins_at
        """,
        (ticker, SPAN, as_of_date),
    ).fetchall()


def returns_for_prices(prices):
    values = {f"fwd_return_{days}d": None for days in HORIZONS}
    if not prices:
        return None, values
    entry = float(prices[0][0])
    if not entry:
        return None, values
    for days in HORIZONS:
        if len(prices) > days:
            values[f"fwd_return_{days}d"] = float(prices[days][0]) / entry - 1.0
    return entry, values


def upsert_current_watchlist(vconn, hconn, watchlist, as_of_date, now):
    for row in watchlist.itertuples(index=False):
        entry_price, returns = returns_for_prices(latest_prices(hconn, row.ticker, as_of_date))
        vconn.execute(
            """
            INSERT INTO WatchlistHistory (
              as_of_date, ticker, rank, raw_score, adjusted_score, confidence,
              recommendation, suggested_horizon, is_persistent, leader_score,
              trend_score, trend_slope_60d, trend_r2_60d, vol_60d,
              riskadj_mom_60d, dollar_vol_20d, total_return, entry_price,
              fwd_return_1d, fwd_return_5d, fwd_return_20d, fwd_return_60d,
              evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(as_of_date, ticker) DO UPDATE SET
              rank=excluded.rank, raw_score=excluded.raw_score,
              adjusted_score=excluded.adjusted_score, confidence=excluded.confidence,
              recommendation=excluded.recommendation,
              suggested_horizon=excluded.suggested_horizon,
              is_persistent=excluded.is_persistent, leader_score=excluded.leader_score,
              trend_score=excluded.trend_score, trend_slope_60d=excluded.trend_slope_60d,
              trend_r2_60d=excluded.trend_r2_60d, vol_60d=excluded.vol_60d,
              riskadj_mom_60d=excluded.riskadj_mom_60d,
              dollar_vol_20d=excluded.dollar_vol_20d,
              total_return=excluded.total_return,
              entry_price=COALESCE(WatchlistHistory.entry_price, excluded.entry_price),
              fwd_return_1d=COALESCE(excluded.fwd_return_1d, WatchlistHistory.fwd_return_1d),
              fwd_return_5d=COALESCE(excluded.fwd_return_5d, WatchlistHistory.fwd_return_5d),
              fwd_return_20d=COALESCE(excluded.fwd_return_20d, WatchlistHistory.fwd_return_20d),
              fwd_return_60d=COALESCE(excluded.fwd_return_60d, WatchlistHistory.fwd_return_60d),
              evaluated_at=excluded.evaluated_at
            """,
            (
                as_of_date,
                row.ticker,
                row.rank,
                row.raw_score,
                row.adjusted_score,
                row.confidence,
                row.recommendation,
                row.suggested_horizon,
                row.is_persistent,
                row.leader_score,
                row.trend_score,
                row.trend_slope_60d,
                row.trend_r2_60d,
                row.vol_60d,
                row.riskadj_mom_60d,
                row.dollar_vol_20d,
                row.total_return,
                entry_price,
                returns["fwd_return_1d"],
                returns["fwd_return_5d"],
                returns["fwd_return_20d"],
                returns["fwd_return_60d"],
                now,
            ),
        )


def refresh_forward_returns(vconn, hconn, now):
    rows = vconn.execute(
        "SELECT as_of_date, ticker FROM WatchlistHistory ORDER BY as_of_date, rank"
    ).fetchall()
    for as_of_date, ticker in rows:
        entry_price, returns = returns_for_prices(latest_prices(hconn, ticker, as_of_date))
        vconn.execute(
            """
            UPDATE WatchlistHistory SET
              entry_price=COALESCE(entry_price, ?),
              fwd_return_1d=COALESCE(?, fwd_return_1d),
              fwd_return_5d=COALESCE(?, fwd_return_5d),
              fwd_return_20d=COALESCE(?, fwd_return_20d),
              fwd_return_60d=COALESCE(?, fwd_return_60d),
              evaluated_at=?
            WHERE as_of_date=? AND ticker=?
            """,
            (
                entry_price,
                returns["fwd_return_1d"],
                returns["fwd_return_5d"],
                returns["fwd_return_20d"],
                returns["fwd_return_60d"],
                now,
                as_of_date,
                ticker,
            ),
        )


def export_csvs(conn, as_of_date):
    LATEST_CSV.parent.mkdir(parents=True, exist_ok=True)
    history = pd.read_sql_query(
        "SELECT * FROM WatchlistHistory ORDER BY as_of_date DESC, rank, ticker", conn
    )
    latest = history[history["as_of_date"] == as_of_date].copy()
    latest.to_csv(LATEST_CSV, index=False)
    history.to_csv(HISTORY_CSV, index=False)
    rows = []
    for days in HORIZONS:
        values = history[f"fwd_return_{days}d"].dropna()
        rows.append(
            {
                "horizon": f"{days}d",
                "evaluated_picks": len(values),
                "average_return": values.mean() if len(values) else None,
                "win_rate": (values > 0).mean() if len(values) else None,
            }
        )
    pd.DataFrame(rows).to_csv(SUMMARY_CSV, index=False)


def main():
    if not VEC_DB.exists() or not HIST_DB.exists():
        raise RuntimeError("vectorized.db and historicals.db are required")
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(VEC_DB) as vconn, sqlite3.connect(HIST_DB) as hconn:
        create_table(vconn)
        watchlist = build_watchlist(vconn)
        as_of_date = vconn.execute(
            "SELECT MAX(begins_at) FROM VectorizedFeatures"
        ).fetchone()[0]
        if not as_of_date:
            raise RuntimeError("VectorizedFeatures has no market date")
        as_of_date = str(as_of_date)[:10]
        vconn.execute("DELETE FROM WatchlistHistory WHERE as_of_date=?", (as_of_date,))
        upsert_current_watchlist(vconn, hconn, watchlist, as_of_date, now)
        refresh_forward_returns(vconn, hconn, now)
        export_csvs(vconn, as_of_date)
        history_rows = vconn.execute("SELECT COUNT(*) FROM WatchlistHistory").fetchone()[0]
    print(
        f"Tracked watchlist snapshot for {as_of_date}: "
        f"current_rows={len(watchlist)} history_rows={history_rows}"
    )


if __name__ == "__main__":
    main()
