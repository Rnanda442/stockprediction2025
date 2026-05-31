import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DB = ROOT / "vectorized.db"
HIST_DB = ROOT / "historicals.db"
OUTPUT_DB = ROOT / "dashboard_data.db"
BUILD_DB = ROOT / "dashboard_data.tmp.db"
RECENT_PRICE_DAYS = 400


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
    latest_shortlist_date = source.execute(
        "SELECT MAX(begins_at) FROM WinnerUniverse"
    ).fetchone()[0] if table_exists(source, "WinnerUniverse") else None
    values = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "latest_market_date": latest_market_date or "",
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
    if not SOURCE_DB.exists() or not HIST_DB.exists():
        raise RuntimeError("vectorized.db and historicals.db are required")
    if BUILD_DB.exists():
        BUILD_DB.unlink()
    with (
        closing(sqlite3.connect(SOURCE_DB)) as source,
        closing(sqlite3.connect(HIST_DB)) as history,
        closing(sqlite3.connect(BUILD_DB)) as destination,
    ):
        counts = {
            "feature_summary": export_feature_summary(source, destination),
            "latest_shortlist": export_latest_shortlist(source, destination),
            "shortlist_history": export_shortlist_history(source, destination),
            "performance_summary": export_performance_summary(source, destination),
            "recent_prices": export_recent_prices(history, destination),
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
