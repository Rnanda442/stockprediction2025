import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SPANS = ("year", "5year")
MAX_DATA_AGE_DAYS = 10
MIN_LATEST_DATE_COVERAGE = 0.80
MIN_FEATURE_SUMMARY_COVERAGE = 0.50
MODEL_FEATURES = (
    "pct_1d", "pct_2d", "pct_3d", "pct_5d", "volatility_5d",
    "volatility_10d", "momentum_slope_5d", "ma_crossover", "ret_10d",
    "ret_20d", "ret_60d", "riskadj_mom_60d", "vol_20d", "vol_60d",
    "trend_slope_60d", "trend_r2_60d", "z_ma20", "bb_width_20d",
    "dollar_vol_20d", "ac1_5d", "max_dd_60d", "time_since_max_60d",
)


def fail(message):
    print(f"ERROR: {message}")
    return False


def warn(message):
    print(f"WARNING: {message}")


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


def check_table_columns(relative_db, table, required_columns):
    path = ROOT / relative_db
    if not has_tables(path, table):
        return False
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    missing = [column for column in required_columns if column not in columns]
    if missing:
        return fail(f"{relative_db}:{table} is missing columns: {', '.join(missing)}")
    print(f"{relative_db}:{table}: required columns present={', '.join(required_columns)}")
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


def has_tables(path, *tables):
    if not path.exists() or path.stat().st_size == 0:
        return fail(f"{path.relative_to(ROOT)} is missing or empty")
    with sqlite3.connect(path) as conn:
        existing = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    missing = [table for table in tables if table not in existing]
    if missing:
        return fail(
            f"{path.relative_to(ROOT)} is missing required tables: {', '.join(missing)}"
        )
    return True


def check_historical_quality():
    path = ROOT / "historicals.db"
    if not has_tables(path, "HistoricalPrices"):
        return False
    with sqlite3.connect(path) as conn:
        duplicate_keys = conn.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT ticker, span, begins_at
              FROM HistoricalPrices
              GROUP BY ticker, span, begins_at
              HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        invalid_raw_prices = conn.execute(
            """
            SELECT COUNT(*)
            FROM HistoricalPrices
            WHERE close_price IS NULL OR close_price <= 0
               OR open_price < 0 OR high_price < 0 OR low_price < 0
               OR volume < 0
            """
        ).fetchone()[0]
        interpolated_invalid_prices = conn.execute(
            """
            SELECT COUNT(*)
            FROM HistoricalPrices
            WHERE interpolated=1
              AND (
                close_price IS NULL OR close_price <= 0
                OR open_price < 0 OR high_price < 0 OR low_price < 0
                OR volume < 0
              )
            """
        ).fetchone()[0]
        invalid_raw_prices -= interpolated_invalid_prices
        total_tickers, latest_date = conn.execute(
            """
            SELECT COUNT(DISTINCT ticker), MAX(begins_at)
            FROM HistoricalPrices
            WHERE span='5year'
            """
        ).fetchone()
        latest_tickers = conn.execute(
            """
            SELECT COUNT(DISTINCT ticker)
            FROM HistoricalPrices
            WHERE span='5year' AND begins_at=?
            """,
            (latest_date,),
        ).fetchone()[0]
        recent_invalid_raw_prices = conn.execute(
            """
            SELECT COUNT(*)
            FROM HistoricalPrices
            WHERE begins_at >= datetime(?, '-400 days')
              AND COALESCE(interpolated, 0) != 1
              AND (
                close_price IS NULL OR close_price <= 0
                OR open_price < 0 OR high_price < 0 OR low_price < 0
                OR volume < 0
              )
            """,
            (latest_date,),
        ).fetchone()[0]

    coverage = latest_tickers / total_tickers if total_tickers else 0.0
    print(
        "historicals.db:HistoricalPrices:5year: "
        f"latest_date={latest_date} ticker_coverage={latest_tickers}/{total_tickers} "
        f"({coverage:.1%}) duplicate_keys={duplicate_keys} "
        f"recent_invalid_raw_prices={recent_invalid_raw_prices} "
        f"legacy_invalid_raw_prices={invalid_raw_prices - recent_invalid_raw_prices} "
        f"invalid_interpolated_prices={interpolated_invalid_prices}"
    )
    ok = True
    if duplicate_keys:
        ok = fail(f"historicals.db:HistoricalPrices has {duplicate_keys} duplicate keys") and ok
    if recent_invalid_raw_prices:
        ok = fail(
            "historicals.db:HistoricalPrices has "
            f"{recent_invalid_raw_prices} invalid recent raw price rows"
        ) and ok
    legacy_invalid_raw_prices = invalid_raw_prices - recent_invalid_raw_prices
    if legacy_invalid_raw_prices:
        warn(
            "historicals.db:HistoricalPrices has "
            f"{legacy_invalid_raw_prices} legacy invalid raw price rows"
        )
    if interpolated_invalid_prices:
        warn(
            "historicals.db:HistoricalPrices has "
            f"{interpolated_invalid_prices} invalid interpolated placeholder rows"
        )
    if coverage < MIN_LATEST_DATE_COVERAGE:
        ok = fail(
            "historicals.db:HistoricalPrices latest 5year date covers only "
            f"{coverage:.1%} of tracked tickers; expected at least {MIN_LATEST_DATE_COVERAGE:.0%}"
        ) and ok
    return ok


def check_vectorized_quality():
    path = ROOT / "vectorized.db"
    if not has_tables(path, "VectorizedFeatures", "FeatureSummary"):
        return False
    with sqlite3.connect(path) as conn:
        duplicate_keys = conn.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT ticker, span, begins_at
              FROM VectorizedFeatures
              GROUP BY ticker, span, begins_at
              HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        total_rows = conn.execute(
            "SELECT COUNT(*) FROM VectorizedFeatures WHERE span='5year'"
        ).fetchone()[0]
        feature_summary_rows = conn.execute(
            "SELECT COUNT(*) FROM FeatureSummary"
        ).fetchone()[0]
        tracked_tickers = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM VectorizedFeatures WHERE span='5year'"
        ).fetchone()[0]
        null_rates = []
        for feature in MODEL_FEATURES:
            nonnull = conn.execute(
                f'SELECT COUNT("{feature}") FROM VectorizedFeatures WHERE span=\'5year\''
            ).fetchone()[0]
            null_rates.append((feature, 1.0 - (nonnull / total_rows if total_rows else 0.0)))

    print(f"vectorized.db:VectorizedFeatures: duplicate_keys={duplicate_keys}")
    for feature, null_rate in null_rates:
        print(f"  model feature {feature}: null_rate={null_rate:.1%}")
        if null_rate == 1.0:
            warn(f"model feature {feature} has no usable values and will be dropped during training")
        elif null_rate > 0.25:
            warn(f"model feature {feature} has a high null rate: {null_rate:.1%}")

    ok = True
    if duplicate_keys:
        ok = fail(f"vectorized.db:VectorizedFeatures has {duplicate_keys} duplicate keys") and ok
    coverage = feature_summary_rows / tracked_tickers if tracked_tickers else 0.0
    print(
        f"vectorized.db:FeatureSummary: ticker_coverage={feature_summary_rows}/{tracked_tickers} "
        f"({coverage:.1%})"
    )
    if coverage < MIN_FEATURE_SUMMARY_COVERAGE:
        ok = fail(
            f"vectorized.db:FeatureSummary covers only {coverage:.1%} of tracked tickers; "
            f"expected at least {MIN_FEATURE_SUMMARY_COVERAGE:.0%}"
        ) and ok
    return ok


def check_output_date_alignment():
    historicals = ROOT / "historicals.db"
    vectorized = ROOT / "vectorized.db"
    if not has_tables(historicals, "HistoricalPrices") or not has_tables(
        vectorized, "WinnerUniverse", "WatchlistHistory"
    ):
        return False
    with sqlite3.connect(historicals) as history, sqlite3.connect(vectorized) as vectorized:
        latest_market_date = history.execute(
            "SELECT MAX(begins_at) FROM HistoricalPrices WHERE span='5year'"
        ).fetchone()[0]
        latest_shortlist_date = vectorized.execute(
            "SELECT MAX(begins_at) FROM WinnerUniverse"
        ).fetchone()[0]
        latest_watchlist_date = vectorized.execute(
            "SELECT MAX(as_of_date) FROM WatchlistHistory"
        ).fetchone()[0]

    market_day = str(latest_market_date or "")[:10]
    shortlist_day = str(latest_shortlist_date or "")[:10]
    watchlist_day = str(latest_watchlist_date or "")[:10]
    print(
        f"output dates: market={market_day} shortlist={shortlist_day} watchlist={watchlist_day}"
    )
    ok = True
    if shortlist_day != market_day:
        ok = fail(f"shortlist date {shortlist_day} does not match market date {market_day}") and ok
    if watchlist_day != market_day:
        ok = fail(f"watchlist date {watchlist_day} does not match market date {market_day}") and ok
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
        check_table("vectorized.db", "ModelEvaluation", min_rows=3),
        check_table("vectorized.db", "ModelFeatureImportance", min_rows=1),
        check_table("vectorized.db", "LatestModelPredictions", min_rows=1),
        check_table("dashboard_data.db", "FeatureSummary", min_rows=1),
        check_table("dashboard_data.db", "StockUniverse", min_rows=1),
        check_table("dashboard_data.db", "StockUniverseSnapshot", min_rows=1),
        check_table("dashboard_data.db", "LatestShortlist", min_rows=1),
        check_table("dashboard_data.db", "LatestWatchlist", min_rows=1),
        check_table("dashboard_data.db", "WatchlistHistory", min_rows=1),
        check_table("dashboard_data.db", "WatchlistPerformanceSummary", min_rows=4),
        check_table("dashboard_data.db", "RecentPrices", min_rows=1),
        check_table("dashboard_data.db", "PipelineHealth", min_rows=1),
        check_table("dashboard_data.db", "ModelEvaluation", min_rows=3),
        check_table("dashboard_data.db", "ModelFeatureImportance", min_rows=1),
        check_table("dashboard_data.db", "LatestModelPredictions", min_rows=1),
        check_table_columns(
            "vectorized.db",
            "LatestModelPredictions",
            [
                "probability_bucket",
                "top_positive_drivers",
                "top_negative_drivers",
            ],
        ),
        check_table_columns(
            "dashboard_data.db",
            "LatestModelPredictions",
            [
                "probability_bucket",
                "top_positive_drivers",
                "top_negative_drivers",
            ],
        ),
        check_csv(
            "analytics/model_evaluation.csv",
            required_columns=[
                "horizon_days", "training_end", "embargo_dates", "test_start",
                "accuracy", "roc_auc", "selected_average_return",
            ],
            min_rows=3,
            nonblank_columns=["horizon_days", "training_end", "embargo_dates", "test_start"],
        ),
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
        check_csv(
            "analytics/latest_model_predictions.csv",
            required_columns=[
                "ticker", "horizon_days", "model_rank", "probability_up",
                "probability_bucket", "top_positive_drivers", "top_negative_drivers",
            ],
            min_rows=1,
            nonblank_columns=["ticker", "horizon_days", "model_rank", "probability_bucket"],
        ),
        check_span_table("historicals.db", "HistoricalPrices"),
        check_span_table("vectorized.db", "VectorizedFeatures"),
        check_historical_quality(),
        check_vectorized_quality(),
        check_output_date_alignment(),
    ]

    if not all(checks):
        print("Pipeline output validation failed.")
        return 1

    print("Pipeline output validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
