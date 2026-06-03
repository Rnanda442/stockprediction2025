from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard import actions
from dashboard.auth import require_login
from dashboard import data
from dashboard import paper_trades
from dashboard import research


st.set_page_config(
    page_title="Stock Research Dashboard",
    page_icon="📈",
    layout="wide",
)


def percent(value):
    return "—" if pd.isna(value) else f"{value:.1%}"


def money(value):
    return "—" if pd.isna(value) else f"${value:,.0f}"


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


def render_overview():
    health = data.health()
    short = data.shortlist()
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
    st.caption("A readable map of the pipeline, the variables, and the limits of the analysis.")

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

    st.subheader("Core variables")
    variables = pd.DataFrame(
        [
            ("Leader score", "Current ranking alias built from trend strength relative to volatility."),
            ("Trend score", "Trend slope divided by volatility. Higher means stronger movement relative to noise."),
            ("Trend slope", "Direction and steepness of the recent fitted price trend."),
            ("Trend fit", "How closely prices follow that fitted trend. Higher means a steadier trend."),
            ("60d volatility", "Standard deviation of recent daily returns. Higher means a bumpier path."),
            ("Risk-adjusted momentum", "Recent return divided by recent volatility."),
            ("Dollar volume", "Average traded dollar value. Used as a liquidity proxy."),
            ("Max drawdown", "Largest decline from a prior peak inside the measured window."),
            ("Sharpe-style ratio", "Annualized average return divided by annualized volatility, with a configurable risk-free rate."),
            ("3D coordinates", "Compressed feature-space coordinates for exploration. They are not predictions by themselves."),
            ("3D movement speed", "Distance traveled between saved feature-space snapshots."),
            ("3D movement acceleration", "Change in feature-space speed between saved snapshots."),
            ("Baseline probability up", "Logistic model estimate trained on earlier dates only. Treat it as a research ranking, not a promise."),
            ("Model drivers", "Largest standardized feature contributions behind a model probability for the selected horizon."),
            ("Trade research queue", "Overlap between the heuristic watchlist and model probabilities. It is a due-diligence list, not an order ticket."),
        ],
        columns=["Variable", "What it means"],
    )
    st.dataframe(variables, hide_index=True, use_container_width=True)

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
    st.link_button(
        "Open latest GitHub Actions runs",
        "https://github.com/Rnanda442/stockprediction2025/actions/workflows/stock-run.yml",
    )


require_login()

page = st.sidebar.radio(
    "Navigate",
    (
        "Overview",
        "How It Works",
        "Research Lab",
        "Model Lab",
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
    elif page == "How It Works":
        render_guide()
    elif page == "Research Lab":
        render_research_lab()
    elif page == "Model Lab":
        render_model_lab()
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
