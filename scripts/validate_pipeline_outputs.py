import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SPANS = ("year", "5year")
MAX_DATA_AGE_DAYS = 10


def fail(message):
    print(f"ERROR: {message}")
    return False


def check_csv(relative_path, required_columns, min_rows=1, max_rows=None, nonblank_columns=None):
    path = ROOT / relative_path
    if not path.exists():
        return fail(f"{relative_path} is missing")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    print(f"{relative_path}: rows={len(rows)} size={path.stat().st_size}")
    if len(rows) < min_rows:
        return fail(f"{relative_path} has {len(rows)} rows; expected at least {min_rows}")
    if max_rows is not None and len(rows) > max_rows:
        return fail(f"{relative_path} has {len(rows)} rows; expected at most {max_rows}")

    missing = [column for column in required_columns if column not in (rows[0].keys() if rows else [])]
    if missing:
        return fail(f"{relative_path} is missing columns: {', '.join(missing)}")

    ok = True
    for column in nonblank_columns or []:
        blank_count = sum(1 for row in rows if not str(row.get(column, "")).strip())
        print(f"  {column}: nonblank={len(rows) - blank_count}/{len(rows)}")
        if blank_count:
            ok = fail(f"{relative_path} has blank values in required column {column}") and ok

    return ok


def check_table(relative_db, table, min_rows=1):
    path = ROOT / relative_db
    if not path.exists():
        return fail(f"{relative_db} is missing")
    if path.stat().st_size == 0:
        return fail(f"{relative_db} is empty")

    with sqlite3.connect(path) as conn:
        try:
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except sqlite3.Error as exc:
            return fail(f"{relative_db}:{table} could not be read: {exc}")

    print(f"{relative_db}:{table}: rows={count} size={path.stat().st_size}")
    if count < min_rows:
        return fail(f"{relative_db}:{table} has {count} rows; expected at least {min_rows}")
    return True


def parse_db_datetime(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(str(value).split("+")[0], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def check_span_table(relative_db, table, required_spans=EXPECTED_SPANS):
    path = ROOT / relative_db
    if not path.exists():
        return fail(f"{relative_db} is missing")

    with sqlite3.connect(path) as conn:
        try:
            rows = conn.execute(
                f"""SELECT span, COUNT(*) AS rows, MAX(begins_at) AS max_dt
                    FROM "{table}"
                    GROUP BY span"""
            ).fetchall()
        except sqlite3.Error as exc:
            return fail(f"{relative_db}:{table} span check failed: {exc}")

    by_span = {row[0]: row for row in rows}
    ok = True
    for span, count, max_dt in rows:
        print(f"{relative_db}:{table}:{span}: rows={count} max={max_dt}")

    for span in required_spans:
        row = by_span.get(span)
        if not row or row[1] < 1:
            ok = fail(f"{relative_db}:{table} is missing populated span '{span}'") and ok
            continue
        latest = parse_db_datetime(row[2])
        if latest is None:
            ok = fail(f"{relative_db}:{table}:{span} has unreadable latest date {row[2]!r}") and ok
            continue
        age_days = (datetime.now(timezone.utc).replace(tzinfo=None) - latest).days
        if age_days > MAX_DATA_AGE_DAYS:
            ok = fail(
                f"{relative_db}:{table}:{span} latest date is {row[2]} "
                f"({age_days} days old; max {MAX_DATA_AGE_DAYS})"
            ) and ok
    return ok


def main():
    checks = [
        check_csv(
            "vector_analysis_results.csv",
            required_columns=["ticker", "Leader_Score", "Rows", "Total_Return", "Trend_Slope_60d"],
            min_rows=1,
            nonblank_columns=["ticker", "Leader_Score", "Rows", "Trend_Slope_60d"],
        ),
        check_csv(
            "analytics/winners_shortlist.csv",
            required_columns=["Ticker", "trend_slope_60d", "ret_60d", "AvgDollarVol"],
            min_rows=1,
            max_rows=5,
            nonblank_columns=["Ticker", "trend_slope_60d", "ret_60d", "AvgDollarVol"],
        ),
        check_csv(
            "analytics/latest_watchlist.csv",
            required_columns=[
                "ticker", "rank", "confidence", "recommendation", "suggested_horizon"
            ],
            min_rows=1,
            max_rows=50,
            nonblank_columns=["ticker", "rank", "confidence", "recommendation"],
        ),
        check_csv(
            "checkpoint_filtered.csv",
            required_columns=["Ticker", "Name", "Price"],
            min_rows=1,
            nonblank_columns=["Ticker", "Name"],
        ),
        check_csv(
            "checkpoint_rejected.csv",
            required_columns=["Ticker", "Reason"],
            min_rows=1,
            nonblank_columns=["Ticker", "Reason"],
        ),
        check_table("filtered_tickers.db", "FilteredTickers", min_rows=1),
        check_table("historicals.db", "HistoricalPrices", min_rows=1),
        check_table("vectorized.db", "VectorizedFeatures", min_rows=1),
        check_table("vectorized.db", "FeatureSummary", min_rows=1),
        check_table("vectorized.db", "WinnerUniverse", min_rows=1),
        check_table("vectorized.db", "ShortlistHistory", min_rows=1),
        check_table("vectorized.db", "WatchlistHistory", min_rows=1),
        check_table("vectorized.db", "StockUniverseSnapshot", min_rows=1),
        check_table("dashboard_data.db", "FeatureSummary", min_rows=1),
        check_table("dashboard_data.db", "StockUniverse", min_rows=1),
        check_table("dashboard_data.db", "StockUniverseSnapshot", min_rows=1),
        check_table("dashboard_data.db", "LatestShortlist", min_rows=1),
        check_table("dashboard_data.db", "LatestWatchlist", min_rows=1),
        check_table("dashboard_data.db", "WatchlistHistory", min_rows=1),
        check_table("dashboard_data.db", "WatchlistPerformanceSummary", min_rows=4),
        check_table("dashboard_data.db", "RecentPrices", min_rows=1),
        check_table("dashboard_data.db", "PipelineHealth", min_rows=1),
        check_csv(
            "analytics/shortlist_history.csv",
            required_columns=["as_of_date", "ticker", "rank", "entry_price"],
            min_rows=1,
            nonblank_columns=["as_of_date", "ticker", "rank", "entry_price"],
        ),
        check_csv(
            "analytics/shortlist_performance_summary.csv",
            required_columns=["horizon", "evaluated_picks", "average_return", "win_rate"],
            min_rows=4,
            nonblank_columns=["horizon", "evaluated_picks"],
        ),
        check_span_table("historicals.db", "HistoricalPrices"),
        check_span_table("vectorized.db", "VectorizedFeatures"),
    ]

    if not all(checks):
        print("Pipeline output validation failed.")
        return 1

    print("Pipeline output validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
