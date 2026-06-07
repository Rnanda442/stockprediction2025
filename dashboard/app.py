from datetime import datetime, timezone
from pathlib import Path
import sys

# Streamlit Cloud launches this nested entry point with dashboard/ on sys.path.
# Add the repository root so package imports work in both cloud and local runs.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard import actions
from dashboard.auth import require_login
from dashboard import data
from dashboard import paper_trades
from dashboard import portfolio_replay
from dashboard import research
from dashboard import trading_constraints


st.set_page_config(
    page_title="Stock Research Dashboard",
    page_icon="📈",
    layout="wide",
)


def percent(value):
    return "—" if pd.isna(value) else f"{value:.1%}"


def money(value):
    return "—" if pd.isna(value) else f"${value:,.0f}"


VARIABLE_GUIDE = {
    "Leader score": {
        "group": "Opportunity",
        "question": "Is the stock moving strongly enough to stand out after accounting for noise?",
        "plain": "A combined ranking signal that rewards a strong trend and penalizes a rough, volatile path.",
        "formula": "trend strength relative to volatility",
        "high": "Stronger, cleaner leadership versus the rest of the universe.",
        "low": "Weak movement, noisy movement, or both.",
        "watch": "A high score can arrive late after a large run. Check drawdown, earnings, and current price location.",
    },
    "Trend score": {
        "group": "Opportunity",
        "question": "How much directional movement are we getting for the risk taken?",
        "plain": "Trend slope divided by volatility. It is a signal-to-noise ratio for the recent price path.",
        "formula": "trend slope / recent volatility",
        "high": "Price has been rising with relatively little noise.",
        "low": "The trend is flat, falling, or too erratic to trust.",
        "watch": "A smooth historical trend can break suddenly when the market regime or company story changes.",
    },
    "Trend slope": {
        "group": "Direction",
        "question": "Which way has the fitted price path been pointing, and how steeply?",
        "plain": "The direction and steepness of a fitted line through recent prices.",
        "formula": "change in fitted price per trading day",
        "high": "A steeper upward recent path.",
        "low": "A flat or downward recent path.",
        "watch": "Slope depends on the stock price scale and selected window. Compare it with return and volatility.",
    },
    "Trend fit": {
        "group": "Consistency",
        "question": "How closely did prices follow the fitted trend instead of wandering around it?",
        "plain": "A consistency score, often expressed like R-squared, for the recent fitted trend.",
        "formula": "share of price variation explained by the fitted trend",
        "high": "A steadier, more orderly path around the trend line.",
        "low": "A choppy path with frequent deviations from the trend.",
        "watch": "A perfect fit describes the past shape; it does not guarantee that the trend continues.",
    },
    "60d volatility": {
        "group": "Risk",
        "question": "How bumpy has the stock's daily path been?",
        "plain": "The typical variation in daily returns over roughly 60 trading sessions.",
        "formula": "standard deviation of recent daily returns",
        "high": "Wider daily swings and greater position-sizing risk.",
        "low": "A more stable recent path.",
        "watch": "Low historical volatility can jump quickly around earnings, news, or market stress.",
    },
    "Dollar volume": {
        "group": "Tradability",
        "question": "How much money typically changes hands in this stock?",
        "plain": "Average share volume multiplied by price, used as a practical liquidity proxy.",
        "formula": "average shares traded x price",
        "high": "Usually easier to enter or exit without moving the market.",
        "low": "Potentially wider spreads and more execution friction.",
        "watch": "Dollar volume does not show the full order book, spread, or liquidity during a shock.",
    },
    "Total return": {
        "group": "Direction",
        "question": "How much did price change over the measured window?",
        "plain": "The percentage change from the beginning to the end of the selected period.",
        "formula": "(ending price / starting price) - 1",
        "high": "Strong historical appreciation over the window.",
        "low": "Weak or negative historical performance.",
        "watch": "Return alone ignores how rough the path was and whether the move is already extended.",
    },
    "Confidence": {
        "group": "Decision aid",
        "question": "How strongly do the watchlist rules agree on this idea?",
        "plain": "A transparent heuristic score assembled from ranking signals. It is not a probability.",
        "formula": "rule-based score from the watchlist pipeline",
        "high": "More of the heuristic conditions agree.",
        "low": "The evidence is mixed or weaker relative to other candidates.",
        "watch": "Do not read 80 confidence as an 80% chance of profit. Only model probability uses probability units.",
    },
    "Baseline probability up": {
        "group": "Model",
        "question": "How does the baseline model rank the chance of a positive later return?",
        "plain": "A logistic-model estimate trained on earlier dates and evaluated on a later held-out period.",
        "formula": "logistic transformation of weighted standardized features",
        "high": "The model sees a feature combination historically associated with more positive outcomes.",
        "low": "The model sees weaker historical evidence for an upward outcome.",
        "watch": "Probabilities can be miscalibrated and market regimes change. Compare them with held-out accuracy and Brier score.",
    },
}


def render_variable_card(name):
    item = VARIABLE_GUIDE[name]
    st.markdown(f"#### {name}")
    st.caption(item["group"])
    st.markdown(f"**Question it answers:** {item['question']}")
    st.write(item["plain"])
    st.code(item["formula"], language=None)
    left, right = st.columns(2)
    left.success(f"Higher: {item['high']}")
    right.info(f"Lower: {item['low']}")
    st.warning(f"Watch out: {item['watch']}")


def render_signal_anatomy(watch, key_prefix):
    if watch.empty:
        return

    chart = watch.copy()
    chart["Risk: 60d volatility"] = pd.to_numeric(chart["vol_60d"], errors="coerce") * 100
    chart["Opportunity: trend score"] = pd.to_numeric(chart["trend_score"], errors="coerce")
    chart["Liquidity: dollar volume"] = pd.to_numeric(
        chart["dollar_vol_20d"], errors="coerce"
    ).clip(lower=1)
    chart["Watchlist confidence"] = pd.to_numeric(chart["confidence"], errors="coerce")
    chart["Ticker"] = chart["ticker"]
    chart["Guidance"] = chart["recommendation"]
    chart = chart.dropna(
        subset=[
            "Risk: 60d volatility",
            "Opportunity: trend score",
            "Liquidity: dollar volume",
            "Watchlist confidence",
        ]
    )
    if chart.empty:
        return

    risk_mid = chart["Risk: 60d volatility"].median()
    signal_mid = chart["Opportunity: trend score"].median()
    fig = px.scatter(
        chart,
        x="Risk: 60d volatility",
        y="Opportunity: trend score",
        size="Liquidity: dollar volume",
        color="Watchlist confidence",
        text="Ticker",
        hover_name="Ticker",
        hover_data={
            "Guidance": True,
            "Risk: 60d volatility": ":.2f",
            "Opportunity: trend score": ":.2f",
            "Liquidity: dollar volume": ":$,.0f",
            "Watchlist confidence": ":.1f",
        },
        color_continuous_scale="Viridis",
        size_max=42,
        title="Signal anatomy: opportunity versus risk",
    )
    fig.add_vline(x=risk_mid, line_dash="dot", line_color="#7f8c8d")
    fig.add_hline(y=signal_mid, line_dash="dot", line_color="#7f8c8d")
    fig.add_annotation(
        x=0.01,
        y=0.99,
        xref="paper",
        yref="paper",
        text="Cleaner opportunity zone",
        showarrow=False,
        bgcolor="rgba(39,174,96,0.12)",
    )
    fig.update_traces(textposition="top center")
    fig.update_layout(
        height=610,
        coloraxis_colorbar_title="Rule<br>agreement",
        margin=dict(l=30, r=30, t=70, b=30),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_signal_anatomy")
    st.caption(
        "Up is stronger risk-adjusted trend. Left is lower recent volatility. "
        "Larger circles are more liquid. Color shows heuristic rule agreement, not probability."
    )


def health_warnings(health):
    warnings = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    latest_market_date = pd.to_datetime(health.get("latest_market_date"), errors="coerce")
    exported_at = pd.to_datetime(health.get("exported_at"), errors="coerce", utc=True)
    latest_shortlist_date = str(health.get("latest_shortlist_date", ""))[:10]
    market_day = str(health.get("latest_market_date", ""))[:10]

    if pd.isna(latest_market_date):
        warnings.append("The dashboard does not report a latest market date.")
    elif (now - latest_market_date.to_pydatetime()).days > 4:
        warnings.append(f"Market data is stale. The latest stored trading date is {market_day}.")

    if pd.isna(exported_at):
        warnings.append("The dashboard does not report when its local export was built.")
    elif (datetime.now(timezone.utc) - exported_at.to_pydatetime()).days > 2:
        warnings.append(f"The local dashboard export is stale. It was built at {str(exported_at)[:19]}.")

    coverage = pd.to_numeric(health.get("latest_market_coverage"), errors="coerce")
    if not pd.isna(coverage) and coverage < 0.80:
        updated = health.get("latest_market_tickers", "0")
        tracked = health.get("tracked_market_tickers", "0")
        warnings.append(
            f"The latest market date covers only {coverage:.1%} of tracked tickers "
            f"({updated}/{tracked}). Treat rankings as incomplete."
        )

    if latest_shortlist_date and market_day and latest_shortlist_date != market_day:
        warnings.append(
            f"The shortlist date ({latest_shortlist_date}) does not match "
            f"the latest market date ({market_day})."
        )
    return warnings


def render_health_warnings(health):
    for message in health_warnings(health):
        st.warning(message)


def model_health_warnings(health):
    warnings = []
    status = data.model_status()
    missing = status[status["status"] != "ready"]
    if not missing.empty:
        parts = [f"{row.table} ({row.status})" for row in missing.itertuples()]
        warnings.append(
            "Model outputs are incomplete: "
            + ", ".join(parts)
            + ". Run or sync the latest successful pipeline before trusting model-backed decisions."
        )

    horizons = data.model_horizon_status()
    if horizons.empty:
        warnings.append("No latest model prediction horizons are available.")
    else:
        available = {int(value) for value in horizons["horizon_days"].dropna()}
        expected = {5, 20, 60}
        missing_horizons = sorted(expected - available)
        if missing_horizons:
            warnings.append(
                "Latest model predictions are missing horizon(s): "
                + ", ".join(f"{horizon}d" for horizon in missing_horizons)
                + "."
            )
        market_day = str(health.get("latest_market_date", ""))[:10]
        latest_prediction = str(horizons["latest_prediction_date"].dropna().max())[:10]
        if market_day and latest_prediction and market_day != latest_prediction:
            warnings.append(
                f"Model predictions are dated {latest_prediction}, but market data is dated {market_day}."
            )
    return warnings


def render_model_health_warnings(health):
    for message in model_health_warnings(health):
        st.warning(message)


def render_overview():
    health = data.health()
    short = data.shortlist()
    watch = data.watchlist()
    st.title("Stock Research Dashboard")
    st.caption("Research signals only. This is not investment advice.")
    render_health_warnings(health)
    st.info(
        "Start with Ranked Watchlist for the daily research funnel, use Research Lab "
        "to test one ticker across historical slices, and use 3D Stock Universe to "
        "explore similarities and filter outcomes."
    )

    cols = st.columns(4)
    cols[0].metric("Latest market date", health.get("latest_market_date", "—")[:10])
    cols[1].metric("Shortlist date", health.get("latest_shortlist_date", "—")[:10])
    cols[2].metric("Tracked candidates", health.get("feature_summary_rows", "0"))
    cols[3].metric("Shortlist picks", health.get("latest_shortlist_rows", "0"))

    st.subheader("Where the strongest ideas sit")
    st.write(
        "This map separates **opportunity** from **risk** instead of compressing every "
        "variable into one rank. Look first toward the upper-left, then inspect circle "
        "size and color before opening the detailed watchlist."
    )
    render_signal_anatomy(watch.head(30), "overview")

    st.subheader("Latest shortlist")
    if short.empty:
        st.info("No shortlist is available yet.")
        return
    display = short.rename(
        columns={
            "ticker": "Ticker",
            "rank": "Rank",
            "begins_at": "As of",
            "trend_slope_60d": "Trend slope",
            "ret_60d": "60d return",
            "vol_60d": "60d volatility",
            "AvgDollarVol": "Avg dollar volume",
            "Days": "Liquidity days",
        }
    )
    for column in ("60d return", "60d volatility"):
        display[column] = display[column] * 100
    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "60d return": st.column_config.NumberColumn(format="%.1f%%"),
            "60d volatility": st.column_config.NumberColumn(format="%.2f%%"),
            "Avg dollar volume": st.column_config.NumberColumn(format="$%.0f"),
        },
    )


def _latest_portfolio_frame():
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
    if portfolio_value > 0:
        holdings["portfolio_weight"] = holdings["market_value"] / portfolio_value
    else:
        holdings["portfolio_weight"] = 0.0
    return holdings, cash, portfolio_value


def _model_queue_summary():
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
            "model_probability_up",
            "model_rank",
            "model_horizon_days",
            "top_positive_drivers",
            "top_negative_drivers",
        ]
    ]


def _decision_action(row):
    is_holding = bool(row.get("is_holding"))
    rank = pd.to_numeric(row.get("rank"), errors="coerce")
    probability = pd.to_numeric(row.get("model_probability_up"), errors="coerce")
    confidence = pd.to_numeric(row.get("confidence"), errors="coerce")

    has_strong_model = not pd.isna(probability) and probability >= 0.60
    has_good_watchlist = not pd.isna(rank) and rank <= 10
    has_confidence = pd.isna(confidence) or confidence >= 60

    if is_holding and has_strong_model and has_confidence:
        return "hold / consider add"
    if is_holding and has_good_watchlist:
        return "hold"
    if is_holding:
        return "review reduce"
    if has_strong_model and has_good_watchlist and has_confidence:
        return "paper buy candidate"
    if has_good_watchlist:
        return "watch"
    return "avoid for now"


def _decision_reason(row):
    parts = []
    rank = pd.to_numeric(row.get("rank"), errors="coerce")
    probability = pd.to_numeric(row.get("model_probability_up"), errors="coerce")
    if not pd.isna(rank):
        parts.append(f"watchlist rank {int(rank)}")
    if not pd.isna(probability):
        horizon = row.get("model_horizon_days")
        parts.append(f"{probability:.1%} model probability over {int(horizon)}d")
    if row.get("is_holding"):
        weight = pd.to_numeric(row.get("portfolio_weight"), errors="coerce")
        if not pd.isna(weight):
            parts.append(f"already held at {weight:.1%} of portfolio")
    return "; ".join(parts) if parts else "not enough signal yet"


def _paper_quantity(row, portfolio_value):
    if portfolio_value <= 0:
        return None
    entry = pd.to_numeric(row.get("entry_price"), errors="coerce")
    volatility = pd.to_numeric(row.get("vol_60d"), errors="coerce")
    if pd.isna(entry) or entry <= 0:
        return None
    stop_pct = 0.08
    if not pd.isna(volatility) and volatility > 0:
        stop_pct = min(0.18, max(0.05, volatility * 2.0))
    risk_budget = portfolio_value * 0.01
    risk_per_share = entry * stop_pct
    if risk_per_share <= 0:
        return None
    quantity = int(risk_budget // risk_per_share)
    return max(quantity, 0)


def render_daily_decision_board():
    st.title("Daily Decision Board")
    st.caption("Paper decisions first. Live trading stays disabled until backtests, paper results, and trading-limit guards are strong.")
    health = data.health()
    render_health_warnings(health)
    render_model_health_warnings(health)
    st.warning(
        "Real trade execution is not enabled here. The board is a review and paper-trading surface, "
        "especially while PDT, buying-power, and account-type constraints are still being built."
    )

    watch = data.watchlist()
    if watch.empty:
        st.info("No ranked watchlist is available. Run the pipeline to initialize daily decisions.")
        return

    holdings, cash, portfolio_value = _latest_portfolio_frame()
    model_summary = _model_queue_summary()
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
    board["portfolio_weight"] = pd.to_numeric(board["portfolio_weight"], errors="coerce").fillna(0.0)
    board["is_holding"] = board["quantity"] > 0
    board["in_shortlist"] = board["ticker"].isin(shortlist_tickers)
    board["decision"] = board.apply(_decision_action, axis=1)
    board["why"] = board.apply(_decision_reason, axis=1)
    board["paper_quantity_1pct_risk"] = board.apply(
        lambda row: _paper_quantity(row, portfolio_value), axis=1
    )

    cols = st.columns(5)
    cols[0].metric("Portfolio value", money(portfolio_value) if portfolio_value else "no snapshot")
    cols[1].metric("Cash snapshot", money(cash))
    cols[2].metric("Held tickers found", f"{int(board['is_holding'].sum()):,}")
    cols[3].metric("Paper buy candidates", f"{int((board['decision'] == 'paper buy candidate').sum()):,}")
    cols[4].metric("Hold / add reviews", f"{int((board['decision'] == 'hold / consider add').sum()):,}")

    st.subheader("Trading constraints")
    if constraint_status in ("blocked", "unknown"):
        st.warning(constraint_message)
    elif constraint_status == "caution":
        st.warning(constraint_message)
    else:
        st.info(constraint_message)
    st.dataframe(
        trading_constraints.as_display_rows(constraints),
        hide_index=True,
        use_container_width=True,
    )
    with st.expander("Create a local constraint snapshot template"):
        st.code(trading_constraints.sample_snapshot_text(), language="csv")
        st.caption(
            "Save this as data/trading_constraints_snapshot.csv and update it manually "
            "until broker-derived constraints are implemented. The data folder is ignored by git."
        )

    st.subheader("Best next actions")
    priority = {
        "hold / consider add": 0,
        "paper buy candidate": 1,
        "hold": 2,
        "watch": 3,
        "review reduce": 4,
        "avoid for now": 5,
    }
    board["decision_priority"] = board["decision"].map(priority).fillna(9)
    display = board.sort_values(
        ["decision_priority", "rank", "model_probability_up"],
        ascending=[True, True, False],
    ).head(25)
    display = display.rename(
        columns={
            "rank": "Watchlist rank",
            "ticker": "Ticker",
            "decision": "Action",
            "why": "Why",
            "confidence": "Watchlist confidence",
            "model_probability_up": "Model probability",
            "model_horizon_days": "Model horizon",
            "entry_price": "Reference price",
            "paper_quantity_1pct_risk": "Paper qty at 1% risk",
            "portfolio_weight": "Portfolio weight",
            "in_shortlist": "In shortlist",
        }
    )
    st.dataframe(
        display[
            [
                "Ticker",
                "Action",
                "Why",
                "Watchlist rank",
                "Watchlist confidence",
                "Model probability",
                "Model horizon",
                "Reference price",
                "Paper qty at 1% risk",
                "Portfolio weight",
                "In shortlist",
            ]
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            "Model probability": st.column_config.NumberColumn(format="%.1f%%"),
            "Reference price": st.column_config.NumberColumn(format="$%.2f"),
            "Portfolio weight": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

    st.subheader("What this board does next")
    st.markdown(
        """
        - Use these actions as the input queue for automatic paper trading.
        - Add PDT, buying-power, and account-type checks before live trading.
        - Keep every hold, buy, reduce, and rejected idea in an audit trail.
        - Backtest this decision logic before trusting it with real orders.
        """
    )


def render_performance():
    st.title("Shortlist Performance")
    st.caption("Forward returns populate as future trading sessions arrive.")
    summary = data.performance_summary()
    history = data.shortlist_history()

    if summary.empty:
        st.info("No performance history exists yet. Run the pipeline once to initialize it.")
    else:
        cols = st.columns(len(summary))
        for col, row in zip(cols, summary.itertuples(index=False)):
            col.metric(
                row.horizon,
                percent(row.average_return),
                f"{int(row.evaluated_picks)} evaluated picks",
            )
            col.caption(f"Win rate: {percent(row.win_rate)}")

    st.subheader("Historical snapshots")
    if history.empty:
        st.info("The first snapshot will appear after the next successful pipeline run.")
        return
    display = history.copy()
    for column in (
        "ret_60d",
        "vol_60d",
        "fwd_return_1d",
        "fwd_return_5d",
        "fwd_return_20d",
        "fwd_return_60d",
    ):
        display[column] = display[column] * 100
    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "entry_price": st.column_config.NumberColumn(format="$%.2f"),
            "ret_60d": st.column_config.NumberColumn(format="%.1f%%"),
            "vol_60d": st.column_config.NumberColumn(format="%.2f%%"),
            "avg_dollar_vol": st.column_config.NumberColumn(format="$%.0f"),
            "fwd_return_1d": st.column_config.NumberColumn(format="%.1f%%"),
            "fwd_return_5d": st.column_config.NumberColumn(format="%.1f%%"),
            "fwd_return_20d": st.column_config.NumberColumn(format="%.1f%%"),
            "fwd_return_60d": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )


def render_watchlist():
    st.title("Ranked Watchlist")
    st.caption(
        "A broader swing-trade research set. Confidence is an interpretable heuristic "
        "score, not a probability or automatic order."
    )
    watch = data.watchlist()
    if watch.empty:
        st.info("No ranked watchlist is available. Run the pipeline to initialize it.")
        return

    counts = watch["recommendation"].value_counts()
    metrics = st.columns(4)
    metrics[0].metric("Tracked ideas", f"{len(watch):,}")
    metrics[1].metric("Consider entry", f"{int(counts.get('consider entry', 0)):,}")
    metrics[2].metric("Research", f"{int(counts.get('research', 0)):,}")
    metrics[3].metric("Persistent ideas", f"{int(watch['is_persistent'].sum()):,}")

    summary = data.watchlist_performance_summary()
    if not summary.empty:
        st.subheader("Watchlist feedback")
        cols = st.columns(len(summary))
        for col, row in zip(cols, summary.itertuples(index=False)):
            col.metric(
                row.horizon,
                percent(row.average_return),
                f"{int(row.evaluated_picks)} evaluated ideas",
            )
            col.caption(f"Win rate: {percent(row.win_rate)}")

    st.subheader("Read the ranking as a system")
    render_signal_anatomy(watch, "watchlist")
    with st.expander("Decode the four visual channels", expanded=False):
        st.markdown(
            """
            - **Vertical position - opportunity:** trend strength after accounting for noise.
            - **Horizontal position - risk:** recent price variability; farther right means a bumpier path.
            - **Circle size - tradability:** average dollar volume; larger usually means easier execution.
            - **Color - rule agreement:** how strongly the heuristic watchlist rules agree, not the chance of profit.

            A stock is not automatically attractive because it is high on one dimension.
            The useful question is whether the combination fits the intended holding period
            and the amount of risk the portfolio can absorb.
            """
        )

    st.subheader("Latest ranked ideas")
    display = watch.rename(
        columns={
            "rank": "Rank",
            "ticker": "Ticker",
            "confidence": "Confidence",
            "recommendation": "Guidance",
            "suggested_horizon": "Holding window",
            "is_persistent": "Stayed ranked",
            "leader_score": "Leader score",
            "trend_score": "Trend score",
            "trend_slope_60d": "Trend slope",
            "trend_r2_60d": "Trend fit",
            "vol_60d": "60d volatility",
            "dollar_vol_20d": "Dollar volume",
            "total_return": "Total return",
        }
    )
    display["Stayed ranked"] = display["Stayed ranked"].map({1: "yes", 0: "new"})
    for column in ("60d volatility", "Total return"):
        display[column] = display[column] * 100
    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Confidence": st.column_config.ProgressColumn(format="%.1f", min_value=0, max_value=100),
            "60d volatility": st.column_config.NumberColumn(format="%.2f%%"),
            "Total return": st.column_config.NumberColumn(format="%.1f%%"),
            "Dollar volume": st.column_config.NumberColumn(format="$%.0f"),
        },
    )


def render_guide():
    st.title("How This Research App Works")
    st.caption(
        "The product map: how observations become variables, predictions, guarded "
        "decisions, and measurable outcomes."
    )

    st.subheader("North star")
    st.info(
        "The app should answer: What deserves attention today, why, and what risk, "
        "portfolio, or account constraint could change the decision? A prediction is "
        "evidence inside that decision, not the decision by itself."
    )

    st.subheader("System architecture")
    stages = [
        ("1. Observe", "Prices, volume, market history, portfolio snapshot"),
        ("2. Measure", "Direction, consistency, risk, tradability, context"),
        ("3. Compare", "Heuristic ranking plus time-split model baselines"),
        ("4. Decide", "Hold, paper buy, watch, reduce, avoid, or blocked"),
        ("5. Learn", "Later returns, paper outcomes, backtests, model feedback"),
    ]
    stage_columns = st.columns(len(stages))
    for column, (name, detail) in zip(stage_columns, stages):
        with column.container(border=True):
            st.markdown(f"**{name}**")
            st.caption(detail)

    st.caption(
        "Quality, time-leakage, portfolio, trading-limit, and human-review gates sit "
        "between these stages. A candidate can stop at any gate."
    )

    st.subheader("Research flow")
    st.markdown(
        """
        1. **Baseline filters** remove symbols with invalid quotes, insufficient liquidity,
           excessive volatility, or other data-quality problems.
        2. **Feature engineering** calculates recent returns, trend slope, trend fit,
           volatility, momentum, liquidity, drawdown, and related measurements.
        3. **Similarity analysis** compares how stocks move and helps identify behaviorally
           related candidates.
        4. **Ranked watchlist** scores the broader candidate pool and keeps 50 ideas for
           forward-return tracking.
        5. **Focused shortlist** highlights a smaller research set after additional
           liquidity, momentum, and volatility checks.
        6. **Feedback loop** records future 1d, 5d, 20d, and 60d returns so later versions
           can compare heuristics against trained models.
        7. **Model Lab** trains only on earlier historical dates, applies a time embargo,
           and measures the baseline on a later window that was not used for fitting.
        """
    )

    st.subheader("Variable families")
    concept_cols = st.columns(5)
    concepts = [
        ("Direction", "Return, momentum, and trend slope"),
        ("Consistency", "Trend fit and persistence"),
        ("Risk", "Volatility and drawdown"),
        ("Tradability", "Dollar volume and price"),
        ("Context", "Horizon, holdings, concentration, constraints"),
    ]
    for column, (name, question) in zip(concept_cols, concepts):
        column.markdown(f"**{name}**")
        column.caption(question)

    st.info(
        "No single variable answers 'Should I buy?' Each variable answers one smaller "
        "question. The research process combines those answers and still requires a "
        "holding period, position size, exit plan, and review of current events."
    )

    st.subheader("Interactive variable decoder")
    variable = st.selectbox(
        "Choose a variable",
        list(VARIABLE_GUIDE),
        help="Select any score or measurement to see its meaning, rough formula, and failure modes.",
    )
    render_variable_card(variable)

    st.subheader("How the variables connect")
    relationship = pd.DataFrame(
        [
            ("Price history", "Trend slope", "Direction"),
            ("Price history", "Trend fit", "Consistency"),
            ("Daily returns", "60d volatility", "Risk"),
            ("Price x volume", "Dollar volume", "Tradability"),
            ("Slope + volatility", "Trend score", "Risk-adjusted opportunity"),
            ("Multiple rules", "Confidence", "Heuristic agreement"),
            ("Standardized features", "Baseline probability up", "Model ranking"),
        ],
        columns=["Raw input", "Processed variable", "Decision role"],
    )
    flow = go.Figure(
        go.Sankey(
            arrangement="snap",
            node=dict(
                label=list(
                    dict.fromkeys(
                        relationship["Raw input"].tolist()
                        + relationship["Processed variable"].tolist()
                        + relationship["Decision role"].tolist()
                    )
                ),
                pad=18,
                thickness=18,
                color="#4c78a8",
            ),
            link=dict(
                source=[],
                target=[],
                value=[],
            ),
        )
    )
    labels = flow.data[0].node.label
    label_index = {label: index for index, label in enumerate(labels)}
    flow.data[0].link.source = [label_index[value] for value in relationship["Raw input"]]
    flow.data[0].link.target = [
        label_index[value] for value in relationship["Processed variable"]
    ]
    flow.data[0].link.value = [1] * len(relationship)
    second_links = go.Sankey(
        node=dict(label=labels),
        link=dict(
            source=[label_index[value] for value in relationship["Processed variable"]],
            target=[label_index[value] for value in relationship["Decision role"]],
            value=[1] * len(relationship),
        ),
    )
    flow.data[0].link.source = list(flow.data[0].link.source) + list(second_links.link.source)
    flow.data[0].link.target = list(flow.data[0].link.target) + list(second_links.link.target)
    flow.data[0].link.value = list(flow.data[0].link.value) + list(second_links.link.value)
    flow.update_layout(
        title="From raw market data to a decision role",
        height=560,
        margin=dict(l=20, r=20, t=60, b=20),
    )
    st.plotly_chart(flow, use_container_width=True, key="variable_relationship_flow")
    st.caption(
        "This is a concept map, not a claim that one variable causes another. "
        "It shows the processing path used to turn market observations into research aids."
    )

    st.subheader("What we build next")
    roadmap = [
        ("Now", "Reliable daily decisions", "Make freshness, model health, reasons, and constraints obvious."),
        ("Next", "Automatic paper loop", "Record every buy, hold, reject, stop, target, and later outcome."),
        ("Then", "Full decision backtest", "Replay the same policy with sizing, turnover, and drawdown."),
        ("After proof", "Model tournament", "Compare logistic and tree models by 5d, 20d, and 60d horizon."),
        ("Safety gate", "Portfolio assistant", "Add allocation, alerts, audit logs, and review-gated proposals."),
    ]
    st.dataframe(
        pd.DataFrame(roadmap, columns=["Stage", "Deliverable", "Why it matters"]),
        hide_index=True,
        use_container_width=True,
    )

    with st.expander("Where Processing / Java visual design fits"):
        st.write(
            "Processing is useful for prototyping motion: observations becoming features, "
            "stocks moving through risk gates, rankings changing over time, and outcomes "
            "flowing back into evaluation. The production dashboard should stay web-based. "
            "Useful Processing studies can be exported as short videos, GIFs, or design "
            "references so the Streamlit app does not require a Java runtime."
        )

    st.subheader("Important limits")
    st.warning(
        "The confidence score is a transparent heuristic score, not a calibrated probability. "
        "Model probabilities are baseline research outputs and can be wrong. "
        "Monte Carlo scenarios describe what could happen if historical return behavior persisted; "
        "they do not know future news, earnings surprises, or market regime shifts. "
        "The dashboard does not place trades; review liquidity, position size, stops, news, earnings, and account risk before any manual order."
    )


def render_research_lab():
    st.title("Research Lab")
    st.caption(
        "Change the historical slice and assumptions locally. These controls rerun instantly "
        "against the dashboard database and do not place trades."
    )
    available = data.tickers()
    if not available:
        st.info("No ticker summaries are available.")
        return

    controls = st.columns(3)
    ticker = controls[0].selectbox("Ticker", available)
    history_days = controls[1].slider("History slice (trading days)", 80, 400, 252, 10)
    risk_free_rate = controls[2].slider("Risk-free rate", 0.0, 0.10, 0.0, 0.005)

    prices = research.prepare_prices(data.ticker_prices(ticker), history_days)
    if len(prices) < 30:
        st.info("Not enough recent price history is available for this ticker.")
        return

    metrics = research.historical_metrics(prices, risk_free_rate)
    cols = st.columns(5)
    cols[0].metric("Slice return", percent(metrics["total_return"]))
    cols[1].metric("Annualized return", percent(metrics["annual_return"]))
    cols[2].metric("Annualized volatility", percent(metrics["annual_volatility"]))
    cols[3].metric("Sharpe-style ratio", f"{metrics['sharpe']:.2f}")
    cols[4].metric("Max drawdown", percent(metrics["max_drawdown"]))

    st.subheader("Scrollable historical slice")
    st.caption("Move the history slider to test how conclusions change when the visible past changes.")
    st.line_chart(prices.set_index("begins_at")["close_price"])

    st.subheader("Monte Carlo scenario fan")
    simulation_controls = st.columns(2)
    horizon_days = simulation_controls[0].slider("Scenario horizon (trading days)", 5, 90, 20, 5)
    simulations = simulation_controls[1].slider("Simulation paths", 200, 3000, 1000, 100)
    quantiles, terminal = research.monte_carlo_paths(prices, horizon_days, simulations)
    if not quantiles.empty:
        scenario_lines = quantiles.melt("day", var_name="scenario", value_name="price")
        st.plotly_chart(
            px.line(
                scenario_lines,
                x="day",
                y="price",
                color="scenario",
                title=f"{ticker}: simulated price percentiles",
            ),
            use_container_width=True,
        )
        cols = st.columns(4)
        cols[0].metric("Current price", f"${terminal['current_price']:,.2f}")
        cols[1].metric("Median scenario", f"${terminal['median_price']:,.2f}")
        cols[2].metric("10%-90% range", f"${terminal['p10_price']:,.2f} - ${terminal['p90_price']:,.2f}")
        cols[3].metric("Scenarios above today", percent(terminal["probability_up"]))

    st.subheader("Walk-forward slice test")
    st.caption(
        "For each historical date, use only the trailing training window, generate a simple "
        "signal, and then inspect the later return. This avoids training on future information."
    )
    walk = st.columns(3)
    training_days = walk[0].slider("Training window", 40, min(252, max(40, len(prices) - 20)), 60, 10)
    holding_days = walk[1].slider("Forward holding window", 1, 60, 20, 1)
    momentum_days = walk[2].slider("Momentum lookback", 5, 60, 20, 5)
    thresholds = st.columns(2)
    minimum_momentum = thresholds[0].slider("Minimum trailing momentum", -0.20, 0.50, 0.05, 0.01)
    minimum_sharpe = thresholds[1].slider("Minimum trailing Sharpe-style ratio", -2.0, 4.0, 0.5, 0.1)

    signals = research.walk_forward_signals(
        prices,
        training_days,
        holding_days,
        momentum_days,
        minimum_momentum,
        minimum_sharpe,
    )
    summary = research.signal_summary(signals)
    cols = st.columns(4)
    cols[0].metric("Historical signals", f"{summary['signals']:,}")
    cols[1].metric("Average later return", percent(summary["average_forward_return"]))
    cols[2].metric("Median later return", percent(summary["median_forward_return"]))
    cols[3].metric("Historical win rate", percent(summary["win_rate"]))
    if not signals.empty:
        chart = signals.copy()
        chart["Signal"] = chart["signal"].map({True: "signal", False: "no signal"})
        st.plotly_chart(
            px.scatter(
                chart,
                x="date",
                y="forward_return",
                color="Signal",
                hover_data=["price", "momentum", "trailing_sharpe"],
                title=f"{ticker}: later {holding_days}d return from each historical slice",
            ),
            use_container_width=True,
        )


def render_pipeline_controls():
    st.title("Pipeline Controls")
    st.caption(
        "Start a full GitHub Actions research run with validated settings. This is slower "
        "than Research Lab because it refreshes data and rebuilds the cloud outputs."
    )
    st.warning(
        "A cloud rerun uses Robinhood and GitHub Actions resources. It does not place trades. "
        "Use Research Lab first for quick experimentation."
    )
    with st.expander("What these controls change", expanded=True):
        st.markdown(
            """
            - **Watchlist size:** number of ranked ideas saved for feedback tracking.
            - **Persistence bonus:** small score bonus for ideas that stayed ranked, reducing churn.
            - **Shortlist size:** maximum focused picks shown on the overview.
            - **Minimum dollar volume:** liquidity floor for focused shortlist candidates.
            - **Maximum 60d volatility:** risk ceiling for focused shortlist candidates.
            - **Similarity range:** correlation window used when comparing behavioral neighbors.
            """
        )

    with st.form("pipeline_controls"):
        left, right = st.columns(2)
        watchlist_limit = left.number_input("Watchlist size", 10, 200, 50, 5)
        persistence_bonus = left.number_input("Persistence bonus", 0.0, 0.25, 0.04, 0.01, format="%.2f")
        shortlist_limit = left.number_input("Shortlist size", 1, 30, 5, 1)
        min_avg_dollar_vol = left.number_input("Minimum dollar volume", 100000, 100000000, 2000000, 100000)
        max_vol_60d = right.number_input("Maximum 60d volatility", 0.01, 0.50, 0.08, 0.01, format="%.2f")
        sim_min = right.number_input("Similarity minimum", 0.0, 0.99, 0.60, 0.01, format="%.2f")
        sim_cap = right.number_input("Similarity cap", 0.01, 1.0, 0.88, 0.01, format="%.2f")
        top_n_per_ticker = right.number_input("Neighbors per ticker", 1, 20, 3, 1)
        confirmed = st.checkbox("I understand this starts a full GitHub Actions pipeline run.")
        submitted = st.form_submit_button("Run pipeline on GitHub", type="primary")

    if submitted:
        if not confirmed:
            st.error("Confirm the GitHub Actions rerun before dispatching it.")
            return
        if sim_min >= sim_cap:
            st.error("Similarity minimum must be lower than similarity cap.")
            return
        inputs = {
            "watchlist_limit": int(watchlist_limit),
            "persistence_bonus": f"{persistence_bonus:.2f}",
            "shortlist_limit": int(shortlist_limit),
            "min_avg_dollar_vol": int(min_avg_dollar_vol),
            "max_vol_60d": f"{max_vol_60d:.2f}",
            "sim_min": f"{sim_min:.2f}",
            "sim_cap": f"{sim_cap:.2f}",
            "top_n_per_ticker": int(top_n_per_ticker),
        }
        try:
            url = actions.dispatch_pipeline(inputs)
        except Exception as exc:
            st.error(f"GitHub Actions dispatch failed: {exc}")
            return
        st.success("Pipeline dispatched successfully.")
        if url:
            st.link_button("Open GitHub Actions run", url)


def render_paper_trade_review(queue, horizon):
    st.subheader("Proposed paper-trade review")
    st.caption(
        "Record manual review notes for queue ideas. This writes a local paper ledger "
        "only; it does not place trades or contact Robinhood."
    )
    if queue.empty:
        st.info("The paper-trade review unlocks when the queue has at least one idea.")
        return

    choices = queue["ticker"].tolist()
    selected_ticker = st.selectbox("Review ticker", choices)
    selected = queue[queue["ticker"] == selected_ticker].iloc[0]
    reference_price = pd.to_numeric(selected.get("entry_price"), errors="coerce")

    metrics = st.columns(4)
    metrics[0].metric("Watchlist rank", int(selected["watchlist_rank"]))
    metrics[1].metric("Model rank", int(selected["model_rank"]))
    metrics[2].metric("Probability up", percent(selected["probability_up"]))
    metrics[3].metric("Confidence", f"{selected['confidence']:.1f}")

    with st.form("paper_trade_review"):
        left, right = st.columns(2)
        status = left.selectbox("Review status", paper_trades.STATUS_OPTIONS)
        direction = right.selectbox("Paper direction", paper_trades.DIRECTION_OPTIONS)
        planned_entry = left.number_input(
            "Planned entry",
            min_value=0.0,
            value=0.0 if pd.isna(reference_price) else float(reference_price),
            step=0.01,
            format="%.2f",
        )
        stop_loss = right.number_input("Stop loss", min_value=0.0, value=0.0, step=0.01, format="%.2f")
        target_price = left.number_input("Target price", min_value=0.0, value=0.0, step=0.01, format="%.2f")
        paper_quantity = right.number_input("Paper quantity", min_value=0, value=0, step=1)
        risk_dollars = 0.0
        if planned_entry > 0 and stop_loss > 0 and paper_quantity > 0:
            risk_dollars = abs(planned_entry - stop_loss) * paper_quantity
        st.metric("Planned paper risk", f"${risk_dollars:,.2f}")
        notes = st.text_area("Review notes", height=100)
        submitted = st.form_submit_button("Save paper review", type="primary")

    if submitted:
        path = paper_trades.save_review(
            {
                "ticker": selected_ticker,
                "horizon_days": int(horizon),
                "review_status": status,
                "direction": direction,
                "watchlist_rank": int(selected["watchlist_rank"]),
                "model_rank": int(selected["model_rank"]),
                "probability_up": float(selected["probability_up"]),
                "confidence": float(selected["confidence"]),
                "reference_price": "" if pd.isna(reference_price) else float(reference_price),
                "planned_entry": float(planned_entry),
                "stop_loss": float(stop_loss),
                "target_price": float(target_price),
                "paper_quantity": int(paper_quantity),
                "risk_dollars": float(risk_dollars),
                "notes": notes,
            }
        )
        st.success(f"Saved paper review to {path}")

    ledger = paper_trades.load_ledger()
    if ledger.empty:
        return
    st.subheader("Paper review ledger")
    display = ledger.head(50).rename(
        columns={
            "updated_at": "Updated",
            "ticker": "Ticker",
            "horizon_days": "Horizon",
            "review_status": "Status",
            "direction": "Direction",
            "probability_up": "Probability up",
            "confidence": "Confidence",
            "planned_entry": "Planned entry",
            "stop_loss": "Stop loss",
            "target_price": "Target",
            "paper_quantity": "Qty",
            "risk_dollars": "Paper risk",
            "notes": "Notes",
        }
    )
    st.dataframe(
        display[
            [
                "Updated",
                "Ticker",
                "Horizon",
                "Status",
                "Direction",
                "Probability up",
                "Confidence",
                "Planned entry",
                "Stop loss",
                "Target",
                "Qty",
                "Paper risk",
                "Notes",
            ]
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            "Probability up": st.column_config.NumberColumn(format="%.3f"),
            "Confidence": st.column_config.NumberColumn(format="%.1f"),
            "Planned entry": st.column_config.NumberColumn(format="$%.2f"),
            "Stop loss": st.column_config.NumberColumn(format="$%.2f"),
            "Target": st.column_config.NumberColumn(format="$%.2f"),
            "Paper risk": st.column_config.NumberColumn(format="$%.2f"),
        },
    )


def render_model_lab():
    st.title("Model Lab")
    st.caption(
        "Leakage-controlled historical baselines. Each model trains on earlier dates, "
        "skips an embargo window, and is scored on a later time window it never saw."
    )
    st.warning(
        "These probabilities are research signals from a baseline model, not guarantees "
        "or automatic trade instructions. Performance can change in a new market regime."
    )
    evaluation = data.model_evaluation()
    if evaluation.empty:
        st.info("No model export is available yet. Run the cloud pipeline to build it.")
        return

    horizon = st.selectbox(
        "Prediction horizon",
        evaluation["horizon_days"].astype(int).tolist(),
        format_func=lambda value: f"{value} trading days",
    )
    row = evaluation[evaluation["horizon_days"] == horizon].iloc[0]
    cols = st.columns(5)
    cols[0].metric("Held-out accuracy", percent(row["accuracy"]))
    cols[1].metric("ROC AUC", f"{row['roc_auc']:.3f}")
    cols[2].metric("Brier score", f"{row['brier_score']:.3f}")
    cols[3].metric("High-confidence ideas", f"{int(row['selected_rows']):,}")
    cols[4].metric("High-confidence win rate", percent(row["selected_win_rate"]))

    st.subheader("Time boundary")
    st.caption(
        "Training labels end before the embargo. The test period begins afterward, so "
        "future returns used as labels cannot cross from training into evaluation."
    )
    boundary = pd.DataFrame(
        [
            ("Training window", row["training_start"], row["training_end"], int(row["training_rows"])),
            ("Embargo", f"{int(row['embargo_dates'])} trading dates", "Excluded", 0),
            ("Untouched test window", row["test_start"], row["test_end"], int(row["test_rows"])),
        ],
        columns=["Segment", "Start", "End", "Rows"],
    )
    st.dataframe(boundary, hide_index=True, use_container_width=True)
    compare = st.columns(2)
    compare[0].metric("All test rows: average later return", percent(row["benchmark_average_return"]))
    compare[1].metric(
        "Probability >= 60%: average later return",
        percent(row["selected_average_return"]),
    )

    st.subheader("Most influential standardized features")
    importance = data.model_feature_importance(horizon).head(15).copy()
    if not importance.empty:
        chart = importance.set_index("feature")["coefficient"].sort_values()
        st.bar_chart(chart)
        st.caption(
            "Positive coefficients push the baseline toward a higher probability of an "
            "upward return; negative coefficients push it lower. This is descriptive, "
            "not proof that a feature causes future movement."
        )

    st.subheader("Model + watchlist trade research queue")
    st.caption(
        "These are tickers that appear in the ranked watchlist and have a model probability "
        "of at least 55% for the selected horizon. Use this as a manual due-diligence queue; "
        "the app does not size positions or submit orders."
    )
    queue = data.trade_research_queue(horizon)
    if queue.empty:
        st.info("No watchlist names currently clear the model-probability queue for this horizon.")
    else:
        queue_display = queue.rename(
            columns={
                "watchlist_rank": "Watchlist rank",
                "model_rank": "Model rank",
                "ticker": "Ticker",
                "probability_up": "Baseline probability up",
                "probability_bucket": "Model read",
                "confidence": "Heuristic confidence",
                "recommendation": "Watchlist guidance",
                "suggested_horizon": "Holding window",
                "is_persistent": "Stayed ranked",
                "trend_slope_60d": "Trend slope",
                "trend_r2_60d": "Trend fit",
                "vol_60d": "60d volatility",
                "dollar_vol_20d": "Dollar volume",
                "total_return": "Total return",
                "top_positive_drivers": "Positive model drivers",
                "top_negative_drivers": "Negative model drivers",
                "as_of_date": "As of",
            }
        )
        queue_display["Stayed ranked"] = queue_display["Stayed ranked"].map({1: "yes", 0: "new"})
        queue_display["Baseline probability up"] = queue_display["Baseline probability up"] * 100
        for column in ("60d volatility", "Total return"):
            queue_display[column] = queue_display[column] * 100
        st.dataframe(
            queue_display,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Baseline probability up": st.column_config.ProgressColumn(
                    format="%.1f%%",
                    min_value=0.0,
                    max_value=100.0,
                ),
                "Heuristic confidence": st.column_config.ProgressColumn(
                    format="%.1f",
                    min_value=0.0,
                    max_value=100.0,
                ),
                "60d volatility": st.column_config.NumberColumn(format="%.2f%%"),
                "Total return": st.column_config.NumberColumn(format="%.1f%%"),
                "Dollar volume": st.column_config.NumberColumn(format="$%.0f"),
            },
        )
    render_paper_trade_review(queue, horizon)

    st.subheader("Latest model research ranking")
    predictions = data.latest_model_predictions(horizon)
    if predictions.empty:
        st.info("No latest model predictions were exported.")
        return
    display = predictions.rename(
        columns={
            "model_rank": "Rank",
            "ticker": "Ticker",
            "probability_up": "Baseline probability up",
            "probability_bucket": "Model read",
            "top_positive_drivers": "Positive model drivers",
            "top_negative_drivers": "Negative model drivers",
            "as_of_date": "As of",
        }
    )
    display["Baseline probability up"] = display["Baseline probability up"] * 100
    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Baseline probability up": st.column_config.ProgressColumn(
                format="%.1f%%",
                min_value=0.0,
                max_value=100.0,
            ),
        },
    )


def render_portfolio_replay():
    st.title("Portfolio Replay")
    st.caption(
        "Replay rule-based portfolio decisions over recent history. This is a backward-looking "
        "walk-forward test: each rebalance uses only price and volume data available before that date."
    )
    st.warning(
        "This is not a future-return prediction and it does not place trades. It compares what "
        "would have happened recently if the rule had rotated a simulated portfolio."
    )

    with st.expander("How this replay works", expanded=True):
        st.markdown(
            """
            1. Enter a starting portfolio as `TICKER, quantity`.
            2. The replay starts N trading days ago and computes the value of simply holding those shares.
            3. On each rebalance date, the strategy ranks liquid stocks by trailing return divided by volatility.
            4. The strategy rotates into the top ranked names and holds until the next rebalance.
            5. The chart compares the simulated strategy against your starting holdings over the same dates.

            The first version uses manual holdings. The next step is a read-only Robinhood snapshot table so this page can load your actual account positions automatically.
            """
        )

    snapshot_defaults = portfolio_replay.latest_snapshot_text()
    sample_defaults = "\n".join(f"{row.ticker}, 1" for row in data.shortlist().head(5).itertuples())
    defaults = snapshot_defaults or sample_defaults
    if snapshot_defaults:
        st.success("Loaded starting holdings from data/robinhood_portfolio_snapshot.csv.")
    else:
        st.info(
            "No Robinhood portfolio snapshot file was found yet. The starting holdings below "
            "are editable sample holdings from the latest shortlist."
        )
    holdings_text = st.text_area(
        "Starting holdings",
        value=defaults,
        height=140,
        help="Use one holding per line, for example: AAPL, 3",
    )

    controls = st.columns(3)
    replay_days = controls[0].slider("Replay window (trading days)", 10, 120, 30, 5)
    lookback_days = controls[1].slider("Signal lookback", 20, 120, 60, 5)
    rebalance_days = controls[2].slider("Rebalance every N trading days", 1, 20, 5, 1)
    controls = st.columns(4)
    snapshot_cash = portfolio_replay.latest_snapshot_cash() if snapshot_defaults else 0.0
    max_positions = controls[0].slider("Max strategy positions", 1, 20, 5, 1)
    minimum_dollar_volume = controls[1].number_input(
        "Minimum dollar volume",
        min_value=0,
        value=2_000_000,
        step=500_000,
    )
    maximum_volatility = controls[2].number_input(
        "Maximum daily volatility",
        min_value=0.001,
        max_value=0.500,
        value=0.080,
        step=0.005,
        format="%.3f",
    )
    cash = controls[3].number_input("Starting cash", min_value=0.0, value=float(snapshot_cash), step=100.0)

    config = portfolio_replay.ReplayConfig(
        lookback_days=int(lookback_days),
        replay_days=int(replay_days),
        rebalance_days=int(rebalance_days),
        max_positions=int(max_positions),
        minimum_dollar_volume=float(minimum_dollar_volume),
        maximum_volatility=float(maximum_volatility),
        cash=float(cash),
    )
    try:
        result = portfolio_replay.replay(holdings_text, config)
    except ValueError as exc:
        st.error(str(exc))
        return

    if result.get("error"):
        st.info(result["error"])
        return

    curve = result["curve"]
    trades = result["trades"]
    candidates = result["candidates"]
    final = curve.iloc[-1]
    actual_start = float(curve["actual_hold"].iloc[0])
    strategy_start = float(curve["strategy"].iloc[0])
    actual_end = float(final["actual_hold"])
    strategy_end = float(final["strategy"])
    edge = strategy_end - actual_end

    metrics = st.columns(5)
    metrics[0].metric("Replay dates", f"{str(result['start_date'])[:10]} to {str(result['end_date'])[:10]}")
    metrics[1].metric("Hold result", money(actual_end - actual_start), percent(actual_end / actual_start - 1.0))
    metrics[2].metric("Strategy result", money(strategy_end - strategy_start), percent(strategy_end / strategy_start - 1.0))
    metrics[3].metric("Strategy vs hold", money(edge), percent(final["strategy_edge_pct"]))
    metrics[4].metric("Rebalances", f"{len(trades):,}")

    chart = curve[["date", "actual_hold", "strategy"]].rename(
        columns={"actual_hold": "Actual hold", "strategy": "Strategy replay"}
    )
    chart = chart.melt("date", var_name="Portfolio", value_name="Value")
    st.plotly_chart(
        px.line(
            chart,
            x="date",
            y="Value",
            color="Portfolio",
            title="Actual hold vs strategy replay",
        ),
        use_container_width=True,
    )

    if not trades.empty:
        st.subheader("Replay trades")
        display_trades = trades.copy()
        display_trades["date"] = display_trades["date"].astype(str).str[:10]
        st.dataframe(
            display_trades.rename(
                columns={
                    "date": "Date",
                    "action": "Action",
                    "tickers": "Held after rebalance",
                    "entered": "Entered",
                    "exited": "Exited",
                    "portfolio_value": "Portfolio value",
                }
            ),
            hide_index=True,
            use_container_width=True,
            column_config={"Portfolio value": st.column_config.NumberColumn(format="$%.2f")},
        )

    st.subheader("Current rule candidates")
    if candidates.empty:
        st.info("No candidates pass the current replay filters.")
    else:
        display = candidates.rename(
            columns={
                "ticker": "Ticker",
                "score": "Rule score",
                "trailing_return": "Trailing return",
                "volatility": "Daily volatility",
                "dollar_volume": "Dollar volume",
            }
        )
        st.dataframe(
            display,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Trailing return": st.column_config.NumberColumn(format="%.1%"),
                "Daily volatility": st.column_config.NumberColumn(format="%.2%"),
                "Dollar volume": st.column_config.NumberColumn(format="$%.0f"),
            },
        )


def render_explorer():
    st.title("Ticker Explorer")
    available = data.tickers()
    if not available:
        st.info("No ticker summaries are available.")
        return
    ticker = st.selectbox("Ticker", available)
    summary = data.ticker_summary(ticker)
    prices = data.ticker_prices(ticker)
    if summary.empty:
        st.warning(f"No summary found for {ticker}.")
        return

    row = summary.iloc[0]
    cols = st.columns(4)
    cols[0].metric("Leader score", f"{row['Leader_Score']:.2f}")
    cols[1].metric("Trend score", f"{row['Trend_Score']:.2f}")
    cols[2].metric("60d volatility", percent(row["Vol_60d"]))
    cols[3].metric("Dollar volume", money(row["DollarVol_20d"]))

    if not prices.empty:
        chart = prices.copy()
        chart["begins_at"] = pd.to_datetime(chart["begins_at"])
        st.subheader("Recent close price")
        st.line_chart(chart.set_index("begins_at")["close_price"])

    st.subheader("Feature summary")
    st.dataframe(summary, hide_index=True, use_container_width=True)


def render_visual_lab():
    st.title("Visual Lab")
    st.caption("Compare the strongest signals before turning research into a trade idea.")

    candidates = data.opportunity_map()
    if candidates.empty:
        st.info("No candidate features are available.")
        return

    candidates = candidates.copy()
    candidates["Liquidity"] = candidates["DollarVol_20d"].clip(lower=1)
    candidates["60d volatility"] = candidates["Vol_60d"] * 100
    candidates["Leader score"] = candidates["Leader_Score"]
    candidates["Trend score"] = candidates["Trend_Score"]

    st.subheader("Opportunity constellation")
    st.caption(
        "Each point is a stock. Higher is a stronger leader signal; farther right "
        "means more volatility. Larger points have greater dollar-volume liquidity."
    )
    st.scatter_chart(
        candidates,
        x="60d volatility",
        y="Leader score",
        size="Liquidity",
        color="Trend score",
        use_container_width=True,
    )

    cols = st.columns(2)
    with cols[0]:
        st.subheader("Signal leaderboard")
        leaders = candidates[
            ["ticker", "Leader_Score", "Trend_Score", "Trend_Slope_60d", "Vol_60d"]
        ].head(20).set_index("ticker")
        st.bar_chart(leaders[["Leader_Score", "Trend_Score"]])
    with cols[1]:
        st.subheader("How to read the map")
        st.markdown(
            """
            - Look for stocks near the top with manageable volatility.
            - Larger points are generally easier to enter and exit.
            - Use the leaderboard to compare signal strength.
            - Treat the map as a research view, not an automatic trade order.
            """
        )

    prices = data.shortlist_prices()
    if prices.empty:
        return
    prices = prices.copy()
    prices["begins_at"] = pd.to_datetime(prices["begins_at"])
    paths = prices.pivot(index="begins_at", columns="ticker", values="close_price")
    paths = paths.ffill().dropna()
    if paths.empty:
        return
    rebased = paths.div(paths.iloc[0]).mul(100)
    st.subheader("Current shortlist race")
    st.caption("The latest shortlist rebased to 100 so different stock prices are comparable.")
    st.line_chart(rebased)


def render_stock_universe():
    st.title("3D Stock Universe")
    st.caption(
        "Explore how stocks behave and see which symbols were filtered out before ranking."
    )

    dates = data.stock_universe_dates()
    if not dates:
        st.info("No saved stock-universe dates are available. Run the pipeline to initialize them.")
        return
    selected_date = st.select_slider("Map date", dates, value=dates[-1])
    universe = data.stock_universe_snapshot(selected_date)
    if universe.empty:
        st.info("No stock-universe export is available. Run the dashboard export script.")
        return

    universe = universe.copy()
    universe["Status"] = universe["status"].str.title()
    universe["Dot size"] = universe["DollarVol_20d"].fillna(1).clip(lower=1)
    universe["60d volatility"] = universe["Vol_60d"] * 100
    universe["60d return"] = universe["Total_Return"] * 100
    universe["Movement speed"] = pd.to_numeric(universe["movement_speed"], errors="coerce").fillna(0)
    universe["Movement acceleration"] = pd.to_numeric(
        universe["movement_acceleration"], errors="coerce"
    ).fillna(0)

    controls = st.columns(4)
    statuses = controls[0].multiselect(
        "Filter status",
        sorted(universe["Status"].unique()),
        default=sorted(universe["Status"].unique()),
    )
    reasons = sorted(universe.loc[universe["status"] == "rejected", "reason"].unique())
    selected_reasons = controls[1].multiselect(
        "Rejected reasons",
        reasons,
        default=reasons,
    )
    size_mode = controls[2].selectbox(
        "Dot size",
        ("Liquidity", "Uniform"),
    )
    color_mode = controls[3].selectbox(
        "Dot color",
        ("Filter status", "Leader score", "Movement speed"),
    )

    visible = universe[universe["Status"].isin(statuses)].copy()
    visible = visible[
        (visible["status"] != "rejected") | visible["reason"].isin(selected_reasons)
    ]
    if visible.empty:
        st.info("No stocks match the selected filters.")
        return
    if size_mode == "Uniform":
        visible["Dot size"] = 1

    passed = int((visible["status"] == "passed").sum())
    rejected = int((visible["status"] == "rejected").sum())
    metrics = st.columns(4)
    metrics[0].metric("Visible stocks", f"{len(visible):,}")
    metrics[1].metric("Passed filters", f"{passed:,}")
    metrics[2].metric("Rejected", f"{rejected:,}")
    metrics[3].metric("Saved map dates", f"{data.stock_universe_snapshot_count():,}")

    color_column = {
        "Filter status": "Status",
        "Leader score": "Leader_Score",
        "Movement speed": "Movement speed",
    }[color_mode]
    fig = px.scatter_3d(
        visible,
        x="x",
        y="y",
        z="z",
        color=color_column,
        size="Dot size",
        size_max=16,
        hover_name="ticker",
        hover_data={
            "reason": True,
            "coordinate_mode": True,
            "Leader_Score": ":.3f",
            "Trend_Score": ":.3f",
            "60d volatility": ":.2f",
            "60d return": ":.1f",
            "Movement speed": ":.3f",
            "Movement acceleration": ":.3f",
            "Dot size": False,
            "x": ":.2f",
            "y": ":.2f",
            "z": ":.2f",
        },
        color_discrete_map={"Passed": "#00cc96", "Rejected": "#ef553b"},
        color_continuous_scale="Viridis",
        opacity=0.72,
    )
    fig.update_layout(
        height=760,
        margin=dict(l=0, r=0, t=10, b=0),
        legend_title_text="Filter outcome",
        scene=dict(
            xaxis_title="Behavior axis X",
            yaxis_title="Behavior axis Y",
            zaxis_title="Behavior axis Z",
        ),
    )
    passed_tickers = (
        visible.loc[visible["status"] == "passed"]
        .sort_values("Leader_Score", ascending=False)["ticker"]
        .head(100)
        .tolist()
    )
    trail_tickers = st.multiselect(
        "Trail tickers",
        passed_tickers,
        default=passed_tickers[:5],
        help="Choose passing stocks to trace across saved map dates.",
    )
    trail_dates = 1
    if len(dates) > 1:
        trail_dates = st.slider(
            "Trail length (saved map dates)",
            1,
            min(30, len(dates)),
            min(10, len(dates)),
        )
    trails = data.stock_universe_trails(trail_tickers, selected_date, trail_dates)
    for ticker, trail in trails.groupby("ticker"):
        if len(trail) < 2:
            continue
        fig.add_trace(
            go.Scatter3d(
                x=trail["x"],
                y=trail["y"],
                z=trail["z"],
                mode="lines+markers",
                name=f"{ticker} trail",
                line=dict(width=5),
                marker=dict(size=3),
            )
        )
    st.plotly_chart(fig, use_container_width=True)
    if len(dates) < 2:
        st.info(
            "One saved map date is available. Trails, movement speed, and acceleration "
            "will populate automatically as future successful pipeline runs add snapshots."
        )

    st.subheader("How to read this map")
    st.markdown(
        """
        - Passing stocks are positioned from compressed technical behavior features.
        - Nearby passing stocks have more similar feature profiles.
        - Rejected stocks remain visible in separate reason-based clouds.
        - Date scrolling and trails show how compressed feature-space positions change.
        - Movement speed and acceleration are exploratory model inputs, not predictions yet.
        """
    )

    st.subheader("Filter outcomes")
    outcome_counts = (
        universe.groupby(["status", "reason"])
        .size()
        .reset_index(name="stocks")
        .sort_values("stocks", ascending=False)
    )
    st.dataframe(outcome_counts, hide_index=True, use_container_width=True)


def render_health():
    st.title("Pipeline Health")
    health = data.health()
    render_health_warnings(health)
    cols = st.columns(4)
    cols[0].metric("Latest market date", health.get("latest_market_date", "—")[:10])
    cols[1].metric("Latest shortlist date", health.get("latest_shortlist_date", "—")[:10])
    cols[2].metric("Dashboard exported", health.get("exported_at", "—")[:19])
    coverage = pd.to_numeric(health.get("latest_market_coverage"), errors="coerce")
    cols[3].metric("Latest-date coverage", "—" if pd.isna(coverage) else f"{coverage:.1%}")

    st.subheader("Compact database contents")
    st.dataframe(data.span_health(), hide_index=True, use_container_width=True)

    st.subheader("Model export status")
    st.dataframe(
        data.model_status().rename(
            columns={
                "table": "Table",
                "status": "Status",
                "rows": "Rows",
                "health_metric": "Health metric",
                "health_rows": "Health rows",
                "minimum_rows": "Minimum rows",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )
    horizon_status = data.model_horizon_status()
    if horizon_status.empty:
        st.warning(
            "LatestModelPredictions is missing or empty; Model Lab and Daily Decision Board "
            "will run without model-backed probabilities."
        )
    else:
        st.dataframe(
            horizon_status.rename(
                columns={
                    "horizon_days": "Horizon days",
                    "rows": "Rows",
                    "latest_prediction_date": "Latest prediction date",
                }
            ),
            hide_index=True,
            use_container_width=True,
        )
    render_model_health_warnings(health)
    st.link_button(
        "Open latest GitHub Actions runs",
        "https://github.com/Rnanda442/stockprediction2025/actions/workflows/stock-run.yml",
    )


require_login()

page = st.sidebar.radio(
    "Navigate",
    (
        "Overview",
        "Daily Decision Board",
        "How It Works",
        "Research Lab",
        "Model Lab",
        "Portfolio Replay",
        "Pipeline Controls",
        "Ranked Watchlist",
        "3D Stock Universe",
        "Visual Lab",
        "Performance",
        "Ticker Explorer",
        "Pipeline Health",
    ),
)
st.sidebar.caption("Private stock research workspace")

try:
    if page == "Overview":
        render_overview()
    elif page == "Daily Decision Board":
        render_daily_decision_board()
    elif page == "How It Works":
        render_guide()
    elif page == "Research Lab":
        render_research_lab()
    elif page == "Model Lab":
        render_model_lab()
    elif page == "Portfolio Replay":
        render_portfolio_replay()
    elif page == "Pipeline Controls":
        render_pipeline_controls()
    elif page == "Ranked Watchlist":
        render_watchlist()
    elif page == "3D Stock Universe":
        render_stock_universe()
    elif page == "Visual Lab":
        render_visual_lab()
    elif page == "Performance":
        render_performance()
    elif page == "Ticker Explorer":
        render_explorer()
    else:
        render_health()
except FileNotFoundError as exc:
    st.error(str(exc))
