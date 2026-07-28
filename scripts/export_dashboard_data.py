import os
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DB = ROOT / "vectorized.db"
HIST_DB = ROOT / "historicals.db"
FILTER_DB = ROOT / "filtered_tickers.db"
OUTPUT_DB = ROOT / "dashboard_data.db"
BUILD_DB = ROOT / "dashboard_data.tmp.db"
RECENT_PRICE_DAYS = 400
MAP_FEATURES = (
    "Total_Return",
    "Trend_Slope_60d",
    "Trend_R2_60d",
    "Vol_60d",
    "RiskAdj_Mom_60d",
    "DollarVol_20d",
    "MaxDD_60d",
    "AC1_5d",
    "Z_MA20",
    "BB_Width_20d",
)


def table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def recreate_table(conn, table, create_sql):
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.execute(create_sql)


def copy_rows(source, destination, select_sql, insert_sql, params=()):
    rows = source.execute(select_sql, params).fetchall()
    if rows:
        destination.executemany(insert_sql, rows)
    return len(rows)


def export_feature_summary(source, destination):
    recreate_table(
        destination,
        "FeatureSummary",
        """
        CREATE TABLE FeatureSummary (
          ticker TEXT PRIMARY KEY,
          Start TEXT, End TEXT, Rows INTEGER, Total_Return REAL,
          Trend_Slope_60d REAL, Trend_R2_60d REAL, Vol_60d REAL,
          RiskAdj_Mom_60d REAL, DollarVol_20d REAL, MaxDD_60d REAL,
          AC1_5d REAL, Z_MA20 REAL, BB_Width_20d REAL,
          MA_Crossover INTEGER, Trend_Score REAL, Leader_Score REAL
        )
        """,
    )
    return copy_rows(
        source,
        destination,
        """
        SELECT ticker, Start, End, Rows, Total_Return, Trend_Slope_60d,
               Trend_R2_60d, Vol_60d, RiskAdj_Mom_60d, DollarVol_20d,
               MaxDD_60d, AC1_5d, Z_MA20, BB_Width_20d, MA_Crossover,
               Trend_Score, Leader_Score
        FROM FeatureSummary
        """,
        """
        INSERT INTO FeatureSummary VALUES (
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
    )


def normalized_pca(rows):
    if not rows:
        return {}
    matrix = np.asarray([[value for value in row[1:]] for row in rows], dtype=float)
    medians = np.nanmedian(matrix, axis=0)
    medians[~np.isfinite(medians)] = 0.0
    matrix = np.where(np.isfinite(matrix), matrix, medians)
    scale = np.nanstd(matrix, axis=0)
    scale[~np.isfinite(scale) | (scale == 0)] = 1.0
    matrix = (matrix - np.nanmean(matrix, axis=0)) / scale
    _, _, right = np.linalg.svd(matrix, full_matrices=False)
    components = right[:3].T
    for column in range(components.shape[1]):
        anchor = np.argmax(np.abs(components[:, column]))
        if components[anchor, column] < 0:
            components[:, column] *= -1
    coords = matrix @ components
    coord_scale = np.nanstd(coords, axis=0)
    coord_scale[~np.isfinite(coord_scale) | (coord_scale == 0)] = 1.0
    coords = coords / coord_scale
    if coords.shape[1] < 3:
        coords = np.pad(coords, ((0, 0), (0, 3 - coords.shape[1])))
    return {row[0]: tuple(float(value) for value in xyz) for row, xyz in zip(rows, coords)}


def rejected_coordinates(ticker, reason, reason_rank):
    digest = hashlib.sha256(ticker.encode("utf-8")).digest()
    y = (int.from_bytes(digest[:4], "big") / 2**32 - 0.5) * 2.2
    z = (int.from_bytes(digest[4:8], "big") / 2**32 - 0.5) * 2.2
    return -4.0 - reason_rank * 1.5, y, z


def persist_stock_universe_snapshot(source, output):
    source.execute(
        """
        CREATE TABLE IF NOT EXISTS StockUniverseSnapshot (
          as_of_date TEXT NOT NULL, ticker TEXT NOT NULL,
          status TEXT NOT NULL, reason TEXT NOT NULL,
          coordinate_mode TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
          z REAL NOT NULL, Leader_Score REAL, Trend_Score REAL,
          Vol_60d REAL, DollarVol_20d REAL, Total_Return REAL,
          PRIMARY KEY (as_of_date, ticker)
        )
        """
    )
    as_of_date = source.execute("SELECT MAX(begins_at) FROM VectorizedFeatures").fetchone()[0]
    if not as_of_date:
        as_of_date = datetime.now(timezone.utc).date().isoformat()
    rows = [(str(as_of_date)[:10], *row) for row in output]
    source.executemany(
        """
        INSERT OR REPLACE INTO StockUniverseSnapshot
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    source.execute(
        "CREATE INDEX IF NOT EXISTS idx_stock_universe_snapshot_ticker "
        "ON StockUniverseSnapshot(ticker, as_of_date)"
    )
    source.commit()


def export_stock_universe(source, filters, destination):
    recreate_table(
        destination,
        "StockUniverse",
        """
        CREATE TABLE StockUniverse (
          ticker TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT NOT NULL,
          coordinate_mode TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
          z REAL NOT NULL, Leader_Score REAL, Trend_Score REAL,
          Vol_60d REAL, DollarVol_20d REAL, Total_Return REAL
        )
        """,
    )
    features = ", ".join(MAP_FEATURES)
    rows = source.execute(
        f"""
        SELECT ticker, {features}
        FROM FeatureSummary
        ORDER BY ticker
        """
    ).fetchall()
    coordinates = normalized_pca(rows)
    summaries = {
        row[0]: row
        for row in source.execute(
            """
            SELECT ticker, Leader_Score, Trend_Score, Vol_60d,
                   DollarVol_20d, Total_Return
            FROM FeatureSummary
            """
        )
    }
    output = []
    for ticker, xyz in coordinates.items():
        summary = summaries[ticker]
        output.append(
            (ticker, "passed", "Passed baseline filters", "behavior", *xyz, *summary[1:])
        )

    rejected = filters.execute(
        "SELECT Ticker, COALESCE(Reason, 'Rejected') FROM RejectedTickers ORDER BY Ticker"
    ).fetchall()
    reasons = sorted({reason for _, reason in rejected})
    reason_rank = {reason: rank for rank, reason in enumerate(reasons)}
    for ticker, reason in rejected:
        if ticker in coordinates:
            continue
        xyz = rejected_coordinates(ticker, reason, reason_rank[reason])
        output.append((ticker, "rejected", reason, "filter_reason", *xyz, None, None, None, None, None))

    destination.executemany(
        "INSERT OR REPLACE INTO StockUniverse VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        output,
    )
    destination.execute("CREATE INDEX idx_stock_universe_status ON StockUniverse(status)")
    destination.execute("CREATE INDEX idx_stock_universe_reason ON StockUniverse(reason)")
    persist_stock_universe_snapshot(source, output)
    return len(output)


def export_stock_universe_history(source, destination):
    recreate_table(
        destination,
        "StockUniverseSnapshot",
        """
        CREATE TABLE StockUniverseSnapshot (
          as_of_date TEXT NOT NULL, ticker TEXT NOT NULL,
          status TEXT NOT NULL, reason TEXT NOT NULL,
          coordinate_mode TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
          z REAL NOT NULL, Leader_Score REAL, Trend_Score REAL,
          Vol_60d REAL, DollarVol_20d REAL, Total_Return REAL,
          PRIMARY KEY (as_of_date, ticker)
        )
        """,
    )
    return copy_rows(
        source,
        destination,
        """
        SELECT as_of_date, ticker, status, reason, coordinate_mode,
               x, y, z, Leader_Score, Trend_Score, Vol_60d,
               DollarVol_20d, Total_Return
        FROM StockUniverseSnapshot
        WHERE as_of_date >= date('now', '-120 days')
        ORDER BY as_of_date, ticker
        """,
        "INSERT INTO StockUniverseSnapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )


def export_latest_shortlist(source, destination):
    recreate_table(
        destination,
        "LatestShortlist",
        """
        CREATE TABLE LatestShortlist (
          ticker TEXT PRIMARY KEY, rank INTEGER, begins_at TEXT,
          trend_slope_60d REAL, vol_60d REAL, dollar_vol_20d REAL,
          ret_60d REAL, AvgVolume REAL, AvgDollarVol REAL, Days INTEGER
        )
        """,
    )
    if not table_exists(source, "WinnerUniverse"):
        return 0
    columns = {row[1] for row in source.execute("PRAGMA table_info(WinnerUniverse)")}
    required = {
        "ticker", "begins_at", "trend_slope_60d", "vol_60d",
        "dollar_vol_20d", "ret_60d", "AvgVolume", "AvgDollarVol", "Days",
    }
    if not required.issubset(columns):
        return 0
    rows = source.execute(
        """
        SELECT ticker, begins_at, trend_slope_60d, vol_60d, dollar_vol_20d,
               ret_60d, AvgVolume, AvgDollarVol, Days
        FROM WinnerUniverse
        ORDER BY trend_slope_60d DESC, ret_60d DESC, AvgDollarVol DESC
        """
    ).fetchall()
    destination.executemany(
        "INSERT INTO LatestShortlist VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(row[0], rank, *row[1:]) for rank, row in enumerate(rows, start=1)],
    )
    return len(rows)


def export_shortlist_history(source, destination):
    recreate_table(
        destination,
        "ShortlistHistory",
        """
        CREATE TABLE ShortlistHistory (
          as_of_date TEXT NOT NULL, ticker TEXT NOT NULL, rank INTEGER NOT NULL,
          entry_price REAL, trend_slope_60d REAL, ret_60d REAL, vol_60d REAL,
          avg_dollar_vol REAL, fwd_return_1d REAL, fwd_return_5d REAL,
          fwd_return_20d REAL, fwd_return_60d REAL, evaluated_at TEXT,
          PRIMARY KEY (as_of_date, ticker)
        )
        """,
    )
    if not table_exists(source, "ShortlistHistory"):
        return 0
    return copy_rows(
        source,
        destination,
        "SELECT * FROM ShortlistHistory",
        "INSERT INTO ShortlistHistory VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )


def export_latest_watchlist(source, destination):
    recreate_table(
        destination,
        "LatestWatchlist",
        """
        CREATE TABLE LatestWatchlist (
          as_of_date TEXT NOT NULL, ticker TEXT PRIMARY KEY, rank INTEGER NOT NULL,
          raw_score REAL NOT NULL, adjusted_score REAL NOT NULL,
          confidence REAL NOT NULL, recommendation TEXT NOT NULL,
          suggested_horizon TEXT NOT NULL, is_persistent INTEGER NOT NULL,
          leader_score REAL, trend_score REAL, trend_slope_60d REAL,
          trend_r2_60d REAL, vol_60d REAL, riskadj_mom_60d REAL,
          dollar_vol_20d REAL, total_return REAL, entry_price REAL
        )
        """,
    )
    if not table_exists(source, "WatchlistHistory"):
        return 0
    return copy_rows(
        source,
        destination,
        """
        SELECT as_of_date, ticker, rank, raw_score, adjusted_score, confidence,
               recommendation, suggested_horizon, is_persistent, leader_score,
               trend_score, trend_slope_60d, trend_r2_60d, vol_60d,
               riskadj_mom_60d, dollar_vol_20d, total_return, entry_price
        FROM WatchlistHistory
        WHERE as_of_date=(SELECT MAX(as_of_date) FROM WatchlistHistory)
        ORDER BY rank
        """,
        "INSERT INTO LatestWatchlist VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )


def export_watchlist_history(source, destination):
    recreate_table(
        destination,
        "WatchlistHistory",
        """
        CREATE TABLE WatchlistHistory (
          as_of_date TEXT NOT NULL, ticker TEXT NOT NULL, rank INTEGER NOT NULL,
          raw_score REAL NOT NULL, adjusted_score REAL NOT NULL,
          confidence REAL NOT NULL, recommendation TEXT NOT NULL,
          suggested_horizon TEXT NOT NULL, is_persistent INTEGER NOT NULL,
          leader_score REAL, trend_score REAL, trend_slope_60d REAL,
          trend_r2_60d REAL, vol_60d REAL, riskadj_mom_60d REAL,
          dollar_vol_20d REAL, total_return REAL, entry_price REAL,
          fwd_return_1d REAL, fwd_return_5d REAL,
          fwd_return_20d REAL, fwd_return_60d REAL, evaluated_at TEXT NOT NULL,
          PRIMARY KEY (as_of_date, ticker)
        )
        """,
    )
    if not table_exists(source, "WatchlistHistory"):
        return 0
    return copy_rows(
        source,
        destination,
        "SELECT * FROM WatchlistHistory",
        "INSERT INTO WatchlistHistory VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )


def export_watchlist_performance_summary(source, destination):
    recreate_table(
        destination,
        "WatchlistPerformanceSummary",
        """
        CREATE TABLE WatchlistPerformanceSummary (
          horizon TEXT PRIMARY KEY, evaluated_picks INTEGER,
          average_return REAL, win_rate REAL
        )
        """,
    )
    if not table_exists(source, "WatchlistHistory"):
        return 0
    rows = []
    for days in (1, 5, 20, 60):
        column = f"fwd_return_{days}d"
        count, average, wins = source.execute(
            f"""
            SELECT COUNT({column}), AVG({column}),
                   SUM(CASE WHEN {column} > 0 THEN 1 ELSE 0 END)
            FROM WatchlistHistory
            """
        ).fetchone()
        rows.append((f"{days}d", count, average, (wins / count) if count else None))
    destination.executemany("INSERT INTO WatchlistPerformanceSummary VALUES (?, ?, ?, ?)", rows)
    return len(rows)


def export_performance_summary(source, destination):
    recreate_table(
        destination,
        "PerformanceSummary",
        """
        CREATE TABLE PerformanceSummary (
          horizon TEXT PRIMARY KEY, evaluated_picks INTEGER,
          average_return REAL, win_rate REAL
        )
        """,
    )
    if not table_exists(source, "ShortlistHistory"):
        return 0
    rows = []
    for days in (1, 5, 20, 60):
        column = f"fwd_return_{days}d"
        count, average, wins = source.execute(
            f"""
            SELECT COUNT({column}), AVG({column}),
                   SUM(CASE WHEN {column} > 0 THEN 1 ELSE 0 END)
            FROM ShortlistHistory
            """
        ).fetchone()
        rows.append((f"{days}d", count, average, (wins / count) if count else None))
    destination.executemany("INSERT INTO PerformanceSummary VALUES (?, ?, ?, ?)", rows)
    return len(rows)


def export_recent_prices(history, destination):
    recreate_table(
        destination,
        "RecentPrices",
        """
        CREATE TABLE RecentPrices (
          ticker TEXT NOT NULL, begins_at TEXT NOT NULL, close_price REAL,
          volume INTEGER, PRIMARY KEY (ticker, begins_at)
        )
        """,
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_PRICE_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    count = copy_rows(
        history,
        destination,
        """
        SELECT ticker, begins_at, close_price, volume
        FROM HistoricalPrices
        WHERE span='5year' AND begins_at >= ?
          AND close_price IS NOT NULL
        ORDER BY ticker, begins_at
        """,
        "INSERT OR REPLACE INTO RecentPrices VALUES (?, ?, ?, ?)",
        (cutoff,),
    )
    destination.execute(
        "CREATE INDEX idx_recent_prices_date ON RecentPrices(begins_at)"
    )
    return count


def export_optional_table(source, destination, table):
    if not table_exists(source, table):
        return 0
    columns = source.execute(f'PRAGMA table_info("{table}")').fetchall()
    definitions = ", ".join(
        f'"{column[1]}" {column[2] or "TEXT"}'
        for column in columns
    )
    recreate_table(destination, table, f'CREATE TABLE "{table}" ({definitions})')
    rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
    if rows:
        placeholders = ", ".join("?" for _ in rows[0])
        destination.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})', rows)
    return len(rows)


def export_health(history, source, destination, counts):
    recreate_table(
        destination,
        "PipelineHealth",
        """
        CREATE TABLE PipelineHealth (
          metric TEXT PRIMARY KEY, value TEXT NOT NULL
        )
        """,
    )
    latest_market_date = history.execute(
        "SELECT MAX(begins_at) FROM HistoricalPrices WHERE span='5year'"
    ).fetchone()[0]
    tracked_tickers = history.execute(
        "SELECT COUNT(DISTINCT ticker) FROM HistoricalPrices WHERE span='5year'"
    ).fetchone()[0]
    latest_market_tickers = history.execute(
        """
        SELECT COUNT(DISTINCT ticker)
        FROM HistoricalPrices
        WHERE span='5year' AND begins_at=?
        """,
        (latest_market_date,),
    ).fetchone()[0] if latest_market_date else 0
    latest_market_coverage = (
        latest_market_tickers / tracked_tickers if tracked_tickers else 0.0
    )
    latest_shortlist_date = source.execute(
        "SELECT MAX(begins_at) FROM WinnerUniverse"
    ).fetchone()[0] if table_exists(source, "WinnerUniverse") else None
    values = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "latest_market_date": latest_market_date or "",
        "latest_market_tickers": str(latest_market_tickers),
        "tracked_market_tickers": str(tracked_tickers),
        "latest_market_coverage": f"{latest_market_coverage:.6f}",
        "latest_shortlist_date": latest_shortlist_date or "",
        **{f"{key}_rows": str(value) for key, value in counts.items()},
    }
    destination.executemany(
        "INSERT INTO PipelineHealth VALUES (?, ?)",
        sorted(values.items()),
    )


def publish_build():
    try:
        os.replace(BUILD_DB, OUTPUT_DB)
    except PermissionError:
        with (
            closing(sqlite3.connect(BUILD_DB)) as source,
            closing(sqlite3.connect(OUTPUT_DB)) as destination,
        ):
            source.backup(destination)
        BUILD_DB.unlink()


def main():
    if not SOURCE_DB.exists() or not HIST_DB.exists() or not FILTER_DB.exists():
        raise RuntimeError("vectorized.db, historicals.db, and filtered_tickers.db are required")
    if BUILD_DB.exists():
        BUILD_DB.unlink()
    with (
        closing(sqlite3.connect(SOURCE_DB)) as source,
        closing(sqlite3.connect(HIST_DB)) as history,
        closing(sqlite3.connect(FILTER_DB)) as filters,
        closing(sqlite3.connect(BUILD_DB)) as destination,
    ):
        counts = {
            "feature_summary": export_feature_summary(source, destination),
            "stock_universe": export_stock_universe(source, filters, destination),
            "stock_universe_history": export_stock_universe_history(source, destination),
            "latest_shortlist": export_latest_shortlist(source, destination),
            "shortlist_history": export_shortlist_history(source, destination),
            "performance_summary": export_performance_summary(source, destination),
            "latest_watchlist": export_latest_watchlist(source, destination),
            "watchlist_history": export_watchlist_history(source, destination),
            "watchlist_performance_summary": export_watchlist_performance_summary(
                source, destination
            ),
            "recent_prices": export_recent_prices(history, destination),
            "model_evaluation": export_optional_table(source, destination, "ModelEvaluation"),
            "model_tournament_evaluation": export_optional_table(
                source, destination, "ModelTournamentEvaluation"
            ),
            "model_feature_importance": export_optional_table(
                source, destination, "ModelFeatureImportance"
            ),
            "model_tournament_feature_importance": export_optional_table(
                source, destination, "ModelTournamentFeatureImportance"
            ),
            "latest_model_predictions": export_optional_table(
                source, destination, "LatestModelPredictions"
            ),
            "latest_model_candidate_predictions": export_optional_table(
                source, destination, "LatestModelCandidatePredictions"
            ),
        }
        export_health(history, source, destination, counts)
        destination.commit()
    publish_build()
    size_mb = OUTPUT_DB.stat().st_size / (1024 * 1024)
    print(f"Exported compact dashboard database: {OUTPUT_DB} ({size_mb:.1f} MB)")
    for name, count in counts.items():
        print(f"  {name}: rows={count}")


if __name__ == "__main__":
    main()
