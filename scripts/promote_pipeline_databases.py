import shutil
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_ROOTS = (
    ROOT,
    ROOT / "data",
    ROOT / "notebook" / "stockprediction2025" / "data",
)

SPAN_TABLES = {
    "historicals.db": "HistoricalPrices",
    "vectorized.db": "VectorizedFeatures",
}
ROW_TABLES = {
    "filtered_tickers.db": "FilteredTickers",
    "full_features.db": None,
}
EXPECTED_SPANS = ("year", "5year")


def table_exists(conn, table):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def score_span_db(path, table):
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with sqlite3.connect(path) as conn:
            if not table_exists(conn, table):
                return None
            rows = conn.execute(
                f"""SELECT span, COUNT(*) AS rows, MAX(begins_at) AS max_dt
                    FROM "{table}"
                    GROUP BY span"""
            ).fetchall()
    except sqlite3.Error:
        return None

    total_rows = sum(int(row_count or 0) for _, row_count, _ in rows)
    if total_rows == 0:
        return None

    spans = {str(span) for span, row_count, _ in rows if int(row_count or 0) > 0}
    expected_count = sum(1 for span in EXPECTED_SPANS if span in spans)
    max_dt = max((str(max_dt or "") for _, _, max_dt in rows), default="")
    return (expected_count, max_dt, total_rows, path)


def score_row_db(path, table):
    if not path.exists() or path.stat().st_size == 0:
        return None
    if table is None:
        return (0, "", path.stat().st_size, path)
    try:
        with sqlite3.connect(path) as conn:
            if not table_exists(conn, table):
                return None
            rows = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    except sqlite3.Error:
        return None
    if int(rows or 0) == 0:
        return None
    return (1, "", int(rows), path)


def best_candidate(db_name):
    table = SPAN_TABLES.get(db_name)
    candidates = []
    for root in DB_ROOTS:
        path = root / db_name
        score = score_span_db(path, table) if table else score_row_db(path, ROW_TABLES.get(db_name))
        if score is not None:
            candidates.append(score)
    if not candidates:
        return None
    return max(candidates, key=lambda score: score[:-1])


def promote(db_name):
    best = best_candidate(db_name)
    if best is None:
        print(f"{db_name}: no populated candidate found")
        return

    source = best[-1]
    best_score = best[:-1]
    target = ROOT / db_name
    current = score_span_db(target, SPAN_TABLES[db_name]) if db_name in SPAN_TABLES else score_row_db(target, ROW_TABLES.get(db_name))

    if source == target:
        print(f"{db_name}: root already selected ({target})")
        return
    if current is not None and current[:-1] >= best_score:
        print(f"{db_name}: root is already at least as complete as {source}")
        return

    print(f"{db_name}: promoting {source} -> {target}")
    shutil.copy2(source, target)


def main():
    for db_name in (*SPAN_TABLES.keys(), *ROW_TABLES.keys()):
        promote(db_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
