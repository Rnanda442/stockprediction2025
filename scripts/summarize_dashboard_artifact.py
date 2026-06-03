"""Summarize and compare dashboard_data.db exports from stock pipeline runs."""

import argparse
import sqlite3
from pathlib import Path


WATCHLIST_LIMIT = 10


def connect(path):
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    if path.stat().st_size == 0:
        raise RuntimeError(f"{path} is empty")
    return sqlite3.connect(path)


def table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def fetch_health(conn):
    if not table_exists(conn, "PipelineHealth"):
        return {}
    return dict(conn.execute("SELECT metric, value FROM PipelineHealth"))


def fetch_rows(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def fetch_shortlist(conn):
    if not table_exists(conn, "LatestShortlist"):
        return []
    return fetch_rows(
        conn,
        """
        SELECT rank, ticker, begins_at, trend_slope_60d, ret_60d, AvgDollarVol
        FROM LatestShortlist
        ORDER BY rank
        """,
    )


def fetch_watchlist(conn):
    if not table_exists(conn, "LatestWatchlist"):
        return []
    return fetch_rows(
        conn,
        """
        SELECT rank, ticker, confidence, recommendation, suggested_horizon
        FROM LatestWatchlist
        ORDER BY rank
        LIMIT ?
        """,
        (WATCHLIST_LIMIT,),
    )


def fetch_model_eval(conn):
    if not table_exists(conn, "ModelEvaluation"):
        return []
    columns = [row[1] for row in conn.execute('PRAGMA table_info("ModelEvaluation")')]
    rows = fetch_rows(conn, 'SELECT * FROM "ModelEvaluation" ORDER BY horizon_days')
    return columns, rows


def print_health(health):
    keys = (
        "exported_at",
        "latest_market_date",
        "latest_shortlist_date",
        "latest_market_tickers",
        "tracked_market_tickers",
        "latest_market_coverage",
        "latest_shortlist_rows",
        "latest_watchlist_rows",
        "model_evaluation_rows",
        "latest_model_predictions_rows",
    )
    print("Pipeline health:")
    for key in keys:
        if key in health:
            print(f"  {key}: {health[key]}")
    row_metrics = sorted((key, value) for key, value in health.items() if key.endswith("_rows"))
    if row_metrics:
        print("  exported row counts:")
        for key, value in row_metrics:
            print(f"    {key}: {value}")


def print_shortlist(rows):
    print("Latest shortlist:")
    if not rows:
        print("  unavailable")
        return
    for rank, ticker, begins_at, trend, ret_60d, avg_dollar_vol in rows:
        print(
            f"  {rank}. {ticker} date={str(begins_at)[:10]} "
            f"trend_slope_60d={trend} ret_60d={ret_60d} AvgDollarVol={avg_dollar_vol}"
        )


def print_watchlist(rows):
    print(f"Top {WATCHLIST_LIMIT} watchlist:")
    if not rows:
        print("  unavailable")
        return
    for rank, ticker, confidence, recommendation, horizon in rows:
        print(
            f"  {rank}. {ticker} confidence={confidence} "
            f"recommendation={recommendation} horizon={horizon}"
        )


def print_model_eval(model_eval):
    print("Model-baseline evaluation:")
    if not model_eval:
        print("  unavailable")
        return
    columns, rows = model_eval
    print("  " + ", ".join(columns))
    for row in rows:
        print("  " + ", ".join(str(value) for value in row))


def compare_lists(name, old_rows, new_rows, key_index=1):
    old_keys = [row[key_index] for row in old_rows]
    new_keys = [row[key_index] for row in new_rows]
    added = [key for key in new_keys if key not in old_keys]
    removed = [key for key in old_keys if key not in new_keys]
    moved = []
    old_positions = {key: index + 1 for index, key in enumerate(old_keys)}
    for index, key in enumerate(new_keys, start=1):
        if key in old_positions and old_positions[key] != index:
            moved.append(f"{key}:{old_positions[key]}->{index}")
    print(f"{name} changes:")
    print(f"  added: {', '.join(added) if added else 'none'}")
    print(f"  removed: {', '.join(removed) if removed else 'none'}")
    print(f"  rank changes: {', '.join(moved) if moved else 'none'}")


def compare(previous, current):
    with connect(previous) as old_conn, connect(current) as new_conn:
        old_health = fetch_health(old_conn)
        new_health = fetch_health(new_conn)
        print("Comparison against previous dashboard DB:")
        for key in ("latest_market_date", "latest_shortlist_date", "latest_market_coverage"):
            old = old_health.get(key, "")
            new = new_health.get(key, "")
            if old != new:
                print(f"  {key}: {old or 'missing'} -> {new or 'missing'}")
            else:
                print(f"  {key}: unchanged ({new or 'missing'})")
        compare_lists("Shortlist", fetch_shortlist(old_conn), fetch_shortlist(new_conn))
        compare_lists("Top watchlist", fetch_watchlist(old_conn), fetch_watchlist(new_conn))


def summarize(path, previous=None):
    with connect(path) as conn:
        print(f"Dashboard DB: {path}")
        print_health(fetch_health(conn))
        print_shortlist(fetch_shortlist(conn))
        print_watchlist(fetch_watchlist(conn))
        print_model_eval(fetch_model_eval(conn))
    if previous:
        print("")
        compare(previous, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dashboard_db", type=Path, help="Path to dashboard_data.db to inspect")
    parser.add_argument(
        "--previous-db",
        type=Path,
        help="Optional prior dashboard_data.db to compare against",
    )
    args = parser.parse_args()
    summarize(args.dashboard_db, args.previous_db)


if __name__ == "__main__":
    main()
