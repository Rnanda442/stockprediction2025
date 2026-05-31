import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_NAMES = ("filtered_tickers.db", "historicals.db", "vectorized.db", "full_features.db")
SPAN_TABLES = {
    "historicals.db": ("HistoricalPrices",),
    "vectorized.db": ("VectorizedFeatures",),
}
EXPECTED_SPANS = ("week", "month", "3month", "year", "5year")
DB_ROOTS = (
    ROOT,
    ROOT / "data",
    ROOT / "notebook" / "stockprediction2025" / "data",
)
CSV_PATHS = (
    "checkpoint_filtered.csv",
    "checkpoint_rejected.csv",
    "vector_analysis_results.csv",
    "analytics/winners_shortlist.csv",
    "analytics/shortlist_history.csv",
    "analytics/shortlist_performance_summary.csv",
    "analytics/flipcorr_winners_5y.csv",
)


def fmt_size(size):
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024


def table_summaries(path):
    try:
        with sqlite3.connect(path) as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            summaries = []
            for table in tables:
                try:
                    row = conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
                    status = "has rows" if row else "empty"
                    summaries.append(f"{table}={status}")
                except sqlite3.Error as exc:
                    summaries.append(f"{table}=unreadable({exc})")
            return summaries
    except sqlite3.Error as exc:
        return [f"unreadable({exc})"]


def span_summaries(path, db_name):
    summaries = []
    for table in SPAN_TABLES.get(db_name, ()):
        try:
            with sqlite3.connect(path) as conn:
                rows = conn.execute(
                    f"""SELECT span, COUNT(*) AS rows, MIN(begins_at), MAX(begins_at)
                        FROM "{table}"
                        GROUP BY span
                        ORDER BY span"""
                ).fetchall()
        except sqlite3.Error as exc:
            summaries.append(f"{table}: span check failed ({exc})")
            continue

        by_span = {row[0]: row for row in rows}
        missing = [span for span in EXPECTED_SPANS if span not in by_span or by_span[span][1] == 0]
        if missing:
            summaries.append(f"{table}: missing spans={','.join(missing)}")
        for span in EXPECTED_SPANS:
            row = by_span.get(span)
            if not row:
                continue
            _, count, min_dt, max_dt = row
            summaries.append(
                f"{table}: {span} rows={count} min={min_dt} max={max_dt}"
            )
    return summaries


def print_db_state():
    print("Database state:")
    for root in DB_ROOTS:
        print(f"  root: {root}")
        if not root.exists():
            print("    missing directory")
            continue
        for name in DB_NAMES:
            path = root / name
            if not path.exists():
                print(f"    {name}: missing")
                continue
            if path.stat().st_size == 0:
                print(f"    {name}: empty file")
                continue
            summaries = "; ".join(table_summaries(path))
            print(f"    {name}: {fmt_size(path.stat().st_size)}; {summaries}")
            for summary in span_summaries(path, name):
                print(f"      {summary}")


def print_csv_state():
    print("Generated CSV/artifact state:")
    for relative in CSV_PATHS:
        path = ROOT / relative
        if not path.exists():
            print(f"  {relative}: missing")
            continue
        print(f"  {relative}: {fmt_size(path.stat().st_size)}")


def main():
    print(f"Workspace root: {ROOT}")
    print(f"Python: {sys.version.split()[0]}")
    print_db_state()
    print_csv_state()
    return 0


if __name__ == "__main__":
    sys.exit(main())
