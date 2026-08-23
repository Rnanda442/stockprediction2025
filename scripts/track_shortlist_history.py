import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHORTLIST_CSV = ROOT / "analytics" / "winners_shortlist.csv"
HISTORY_CSV = ROOT / "analytics" / "shortlist_history.csv"
SUMMARY_CSV = ROOT / "analytics" / "shortlist_performance_summary.csv"
HIST_DB = ROOT / "historicals.db"
VEC_DB = ROOT / "vectorized.db"
SPAN = "5year"
HORIZONS = (1, 5, 20, 60)


def create_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ShortlistHistory (
          as_of_date TEXT NOT NULL,
          ticker TEXT NOT NULL,
          rank INTEGER NOT NULL,
          entry_price REAL,
          trend_slope_60d REAL,
          ret_60d REAL,
          vol_60d REAL,
          avg_dollar_vol REAL,
          fwd_return_1d REAL,
          fwd_return_5d REAL,
          fwd_return_20d REAL,
          fwd_return_60d REAL,
          evaluated_at TEXT NOT NULL,
          PRIMARY KEY (as_of_date, ticker)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shortlist_history_ticker "
        "ON ShortlistHistory(ticker, as_of_date)"
    )


def parse_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def latest_prices(conn, ticker, as_of_date):
    return conn.execute(
        """
        SELECT begins_at, close_price
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
    entry = float(prices[0][1])
    if not entry:
        return None, values
    for days in HORIZONS:
        if len(prices) > days:
            values[f"fwd_return_{days}d"] = float(prices[days][1]) / entry - 1.0
    return entry, values


def upsert_current_shortlist(vconn, hconn, now):
    with SHORTLIST_CSV.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"{SHORTLIST_CSV} is empty")

    as_of_date = max(str(row["begins_at"])[:10] for row in rows)
    for rank, row in enumerate(rows, start=1):
        ticker = str(row["Ticker"]).strip().upper()
        entry_price, returns = returns_for_prices(latest_prices(hconn, ticker, as_of_date))
        vconn.execute(
            """
            INSERT INTO ShortlistHistory (
              as_of_date, ticker, rank, entry_price, trend_slope_60d, ret_60d,
              vol_60d, avg_dollar_vol, fwd_return_1d, fwd_return_5d,
              fwd_return_20d, fwd_return_60d, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(as_of_date, ticker) DO UPDATE SET
              rank=excluded.rank,
              entry_price=COALESCE(ShortlistHistory.entry_price, excluded.entry_price),
              trend_slope_60d=excluded.trend_slope_60d,
              ret_60d=excluded.ret_60d,
              vol_60d=excluded.vol_60d,
              avg_dollar_vol=excluded.avg_dollar_vol,
              fwd_return_1d=COALESCE(excluded.fwd_return_1d, ShortlistHistory.fwd_return_1d),
              fwd_return_5d=COALESCE(excluded.fwd_return_5d, ShortlistHistory.fwd_return_5d),
              fwd_return_20d=COALESCE(excluded.fwd_return_20d, ShortlistHistory.fwd_return_20d),
              fwd_return_60d=COALESCE(excluded.fwd_return_60d, ShortlistHistory.fwd_return_60d),
              evaluated_at=excluded.evaluated_at
            """,
            (
                as_of_date,
                ticker,
                rank,
                entry_price,
                parse_float(row.get("trend_slope_60d")),
                parse_float(row.get("ret_60d")),
                parse_float(row.get("vol_60d")),
                parse_float(row.get("AvgDollarVol")),
                returns["fwd_return_1d"],
                returns["fwd_return_5d"],
                returns["fwd_return_20d"],
                returns["fwd_return_60d"],
                now,
            ),
        )
    return as_of_date, len(rows)


def refresh_forward_returns(vconn, hconn, now):
    rows = vconn.execute(
        "SELECT as_of_date, ticker FROM ShortlistHistory ORDER BY as_of_date, rank"
    ).fetchall()
    for as_of_date, ticker in rows:
        entry_price, returns = returns_for_prices(latest_prices(hconn, ticker, as_of_date))
        vconn.execute(
            """
            UPDATE ShortlistHistory SET
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


def export_history(vconn):
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    columns = [row[1] for row in vconn.execute("PRAGMA table_info(ShortlistHistory)")]
    rows = vconn.execute(
        "SELECT * FROM ShortlistHistory ORDER BY as_of_date DESC, rank, ticker"
    ).fetchall()
    with HISTORY_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def export_summary(vconn):
    return_columns = [f"fwd_return_{days}d" for days in HORIZONS]
    rows = vconn.execute("SELECT * FROM ShortlistHistory").fetchall()
    columns = [row[1] for row in vconn.execute("PRAGMA table_info(ShortlistHistory)")]
    records = [dict(zip(columns, row)) for row in rows]
    with SUMMARY_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["horizon", "evaluated_picks", "average_return", "win_rate"])
        for column in return_columns:
            values = [float(row[column]) for row in records if row[column] is not None]
            avg = sum(values) / len(values) if values else None
            wins = sum(1 for value in values if value > 0)
            writer.writerow(
                [column.removeprefix("fwd_return_"), len(values), avg, wins / len(values) if values else None]
            )


def main():
    if not SHORTLIST_CSV.exists():
        raise RuntimeError(f"{SHORTLIST_CSV} is missing")
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(VEC_DB) as vconn, sqlite3.connect(HIST_DB) as hconn:
        create_table(vconn)
        as_of_date, shortlist_rows = upsert_current_shortlist(vconn, hconn, now)
        refresh_forward_returns(vconn, hconn, now)
        export_history(vconn)
        export_summary(vconn)
        history_rows = vconn.execute("SELECT COUNT(*) FROM ShortlistHistory").fetchone()[0]
    print(
        f"Tracked shortlist snapshot for {as_of_date}: "
        f"current_rows={shortlist_rows} history_rows={history_rows}"
    )


if __name__ == "__main__":
    main()
