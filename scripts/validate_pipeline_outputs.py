import csv
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message):
    print(f"ERROR: {message}")
    return False


def check_csv(relative_path, required_columns, min_rows=1, nonblank_columns=None):
    path = ROOT / relative_path
    if not path.exists():
        return fail(f"{relative_path} is missing")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    print(f"{relative_path}: rows={len(rows)} size={path.stat().st_size}")
    if len(rows) < min_rows:
        return fail(f"{relative_path} has {len(rows)} rows; expected at least {min_rows}")

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
            nonblank_columns=["Ticker", "trend_slope_60d", "ret_60d", "AvgDollarVol"],
        ),
        check_csv(
            "checkpoint_filtered.csv",
            required_columns=["Ticker", "Name", "Sector", "Price"],
            min_rows=1,
            nonblank_columns=["Ticker", "Name", "Sector"],
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
    ]

    if not all(checks):
        print("Pipeline output validation failed.")
        return 1

    print("Pipeline output validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
