import pandas as pd

from dashboard import data
from dashboard import decision_policy
from dashboard import portfolio_replay
from dashboard import trading_constraints


def latest_portfolio_frame():
    snapshot_text = portfolio_replay.latest_snapshot_text()
    cash = portfolio_replay.latest_snapshot_cash()
    if not snapshot_text.strip():
        return pd.DataFrame(), cash, 0.0

    holdings = portfolio_replay.parse_holdings(snapshot_text)
    if holdings.empty:
        return holdings, cash, cash

    latest_prices = data.query(
        """
        WITH latest AS (
            SELECT ticker, MAX(begins_at) AS begins_at
            FROM RecentPrices
            GROUP BY ticker
        )
        SELECT prices.ticker, prices.close_price
        FROM RecentPrices AS prices
        INNER JOIN latest
          ON latest.ticker = prices.ticker
         AND latest.begins_at = prices.begins_at
        """
    )
    holdings = holdings.merge(latest_prices, on="ticker", how="left")
    holdings["close_price"] = pd.to_numeric(holdings["close_price"], errors="coerce")
    holdings["market_value"] = holdings["quantity"] * holdings["close_price"]
    invested = holdings["market_value"].fillna(0.0).sum()
    portfolio_value = float(invested + cash)
    holdings["portfolio_weight"] = (
        holdings["market_value"] / portfolio_value if portfolio_value > 0 else 0.0
    )
    return holdings, cash, portfolio_value


def model_queue_summary():
    frames = []
    for horizon in (5, 20, 60):
        queue = data.trade_research_queue(horizon)
        if queue.empty:
            continue
        queue = queue.copy()
        queue["model_horizon_days"] = horizon
        frames.append(queue)
    if not frames:
        return pd.DataFrame(
            columns=[
                "ticker",
                "model_name",
                "model_label",
                "model_version",
                "model_probability_up",
                "model_rank",
                "model_horizon_days",
                "top_positive_drivers",
                "top_negative_drivers",
            ]
        )

    queue = pd.concat(frames, ignore_index=True)
    queue = queue.sort_values(["ticker", "probability_up"], ascending=[True, False])
    best = queue.groupby("ticker", as_index=False).first()
    return best.rename(
        columns={
            "probability_up": "model_probability_up",
            "model_rank": "model_rank",
            "model_horizon_days": "model_horizon_days",
        }
    )[
        [
            "ticker",
            "model_name",
            "model_label",
            "model_version",
            "model_probability_up",
            "model_rank",
            "model_horizon_days",
            "top_positive_drivers",
            "top_negative_drivers",
        ]
    ]


def estimated_trade_notional(row):
    quantity = pd.to_numeric(row.get("paper_quantity_1pct_risk"), errors="coerce")
    entry = pd.to_numeric(row.get("entry_price"), errors="coerce")
    if pd.isna(quantity) or pd.isna(entry) or quantity <= 0 or entry <= 0:
        return None
    return float(quantity * entry)


def daily_decision_context(limit=25):
    health = data.health()
    watch = data.watchlist()
    if watch.empty:
        constraints = trading_constraints.latest_constraints()
        return {
            "health": health,
            "watch": watch,
            "board": pd.DataFrame(),
            "ranked_decisions": pd.DataFrame(),
            "holdings": pd.DataFrame(),
            "cash": 0.0,
            "portfolio_value": 0.0,
            "constraints": constraints,
            "constraint_status": "unknown",
            "constraint_message": "No ranked watchlist is available.",
        }

    holdings, cash, portfolio_value = latest_portfolio_frame()
    model_summary = model_queue_summary()
    shortlist = data.shortlist()
    shortlist_tickers = set(shortlist["ticker"].str.upper()) if not shortlist.empty else set()
    constraints = trading_constraints.latest_constraints()
    constraint_status, constraint_message = trading_constraints.status(constraints)

    board = watch.head(50).copy()
    board["ticker"] = board["ticker"].str.upper()
    if "entry_price" not in board.columns:
        board["entry_price"] = None
    board = board.merge(model_summary, on="ticker", how="left")
    if not holdings.empty:
        board = board.merge(
            holdings[["ticker", "quantity", "market_value", "portfolio_weight"]],
            on="ticker",
            how="left",
        )
    else:
        board["quantity"] = 0.0
        board["market_value"] = 0.0
        board["portfolio_weight"] = 0.0

    board["quantity"] = pd.to_numeric(board["quantity"], errors="coerce").fillna(0.0)
    board["market_value"] = pd.to_numeric(board["market_value"], errors="coerce").fillna(0.0)
    board["portfolio_weight"] = pd.to_numeric(
        board["portfolio_weight"], errors="coerce"
    ).fillna(0.0)
    board["is_holding"] = board["quantity"] > 0
    board["in_shortlist"] = board["ticker"].isin(shortlist_tickers)
    board = decision_policy.apply_policy(board, portfolio_value)
    constraint_rows = board.apply(
        lambda row: pd.Series(
            trading_constraints.action_status(
                row.get("decision"),
                constraints,
                estimated_notional=estimated_trade_notional(row),
            )
        ),
        axis=1,
    )
    board = pd.concat([board, constraint_rows], axis=1)
    ranked_decisions = board.sort_values(
        ["decision_priority", "rank", "model_probability_up"],
        ascending=[True, True, False],
    ).head(limit)

    return {
        "health": health,
        "watch": watch,
        "board": board,
        "ranked_decisions": ranked_decisions,
        "holdings": holdings,
        "cash": cash,
        "portfolio_value": portfolio_value,
        "constraints": constraints,
        "constraint_status": constraint_status,
        "constraint_message": constraint_message,
    }


def paper_performance_summary():
    outcomes = data.automatic_paper_outcomes().copy()
    status = data.paper_learning_status()
    if outcomes.empty:
        return status, pd.DataFrame()

    for column in ("return_pct", "evaluation_horizon_days", "decision_horizon_days"):
        outcomes[column] = pd.to_numeric(outcomes[column], errors="coerce")
    declared = outcomes[
        (outcomes["evaluation_horizon_days"] == outcomes["decision_horizon_days"])
        & outcomes["return_pct"].notna()
    ].copy()
    if declared.empty:
        return status, pd.DataFrame()

    grouped = (
        declared.groupby(["decision_horizon_days", "action"], dropna=False)
        .agg(
            decisions=("decision_id", "nunique"),
            outcome_events=("outcome_id", "count"),
            average_return=("return_pct", "mean"),
            median_return=("return_pct", "median"),
            win_rate=("return_pct", lambda values: (values > 0).mean()),
        )
        .reset_index()
        .sort_values(["decision_horizon_days", "action"])
    )
    return status, grouped


def ticker_detail(ticker, price_limit=120):
    ticker = str(ticker).strip().upper()
    summary = data.ticker_summary(ticker)
    prices = data.ticker_prices(ticker)
    if not prices.empty:
        prices = prices.tail(price_limit)
    predictions = []
    for horizon in (5, 20, 60):
        frame = data.latest_model_predictions(horizon, limit=500)
        if frame.empty:
            continue
        match = frame[frame["ticker"].astype(str).str.upper() == ticker].copy()
        if match.empty:
            continue
        match["horizon_days"] = horizon
        predictions.append(match)
    prediction_frame = (
        pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame()
    )
    return {
        "ticker": ticker,
        "summary": summary,
        "prices": prices,
        "predictions": prediction_frame,
    }


def readiness_report():
    health = data.health()
    paper_status = data.paper_learning_status()
    model_status = data.model_status()
    context = daily_decision_context(limit=10)

    coverage = pd.to_numeric(
        health.get("latest_market_coverage"), errors="coerce"
    )
    coverage_ready = not pd.isna(coverage) and float(coverage) >= 0.8
    model_ready = (
        not model_status.empty
        and set(model_status["status"].astype(str).str.lower()) == {"ready"}
    )
    decisions_ready = not context["ranked_decisions"].empty
    paper_ready = paper_status["decisions"] > 0 and paper_status["outcome_events"] > 0
    learning_ready = paper_status["matured"] > 0
    constraints_ready = context["constraint_status"] not in {"unknown", "blocked"}

    checks = [
        {
            "key": "approved_artifact",
            "label": "Approved dashboard artifact",
            "status": "ready" if coverage_ready else "attention",
            "detail": (
                f"Market date {str(health.get('latest_market_date', 'unknown'))[:10]} "
                f"with {float(coverage):.1%} coverage"
                if not pd.isna(coverage)
                else "Latest-market coverage is unavailable"
            ),
        },
        {
            "key": "model_baseline",
            "label": "Model tournament",
            "status": "ready" if model_ready else "attention",
            "detail": (
                "Model tables are present for the dashboard contract"
                if model_ready
                else "One or more model export tables needs attention"
            ),
        },
        {
            "key": "daily_decisions",
            "label": "Daily decision board",
            "status": "ready" if decisions_ready else "attention",
            "detail": (
                f"{len(context['ranked_decisions'])} ranked decision rows available"
                if decisions_ready
                else "No ranked decision rows are available"
            ),
        },
        {
            "key": "paper_loop",
            "label": "Paper-learning loop",
            "status": "ready" if paper_ready and learning_ready else "partial",
            "detail": (
                f"{paper_status['decisions']} decisions, "
                f"{paper_status['matured']} matured, "
                f"{paper_status['outcome_events']} outcome events"
            ),
        },
        {
            "key": "account_constraints",
            "label": "Account constraints",
            "status": "ready" if constraints_ready else "attention",
            "detail": context["constraint_message"],
        },
        {
            "key": "live_trading",
            "label": "Live trading",
            "status": "blocked",
            "detail": (
                "Keep real orders disabled until constraints, backtests, and "
                "paper results are stronger."
            ),
        },
    ]

    if all(check["status"] == "ready" for check in checks[:4]):
        overall = "usable for review and paper decisions"
    else:
        overall = "needs attention before review"

    return {
        "overall": overall,
        "latest_market_date": str(health.get("latest_market_date", ""))[:10],
        "latest_shortlist_date": str(health.get("latest_shortlist_date", ""))[:10],
        "checks": checks,
    }
