import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.auth import require_login
from dashboard import data


st.set_page_config(
    page_title="Stock Research Dashboard",
    page_icon="📈",
    layout="wide",
)


def percent(value):
    return "—" if pd.isna(value) else f"{value:.1%}"


def money(value):
    return "—" if pd.isna(value) else f"${value:,.0f}"


def render_overview():
    health = data.health()
    short = data.shortlist()
    st.title("Stock Research Dashboard")
    st.caption("Research signals only. This is not investment advice.")

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

    universe = data.stock_universe()
    if universe.empty:
        st.info("No stock-universe export is available. Run the dashboard export script.")
        return

    universe = universe.copy()
    universe["Status"] = universe["status"].str.title()
    universe["Dot size"] = universe["DollarVol_20d"].fillna(1).clip(lower=1)
    universe["60d volatility"] = universe["Vol_60d"] * 100
    universe["60d return"] = universe["Total_Return"] * 100

    controls = st.columns(3)
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

    fig = px.scatter_3d(
        visible,
        x="x",
        y="y",
        z="z",
        color="Status",
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
            "Dot size": False,
            "x": ":.2f",
            "y": ":.2f",
            "z": ":.2f",
        },
        color_discrete_map={"Passed": "#00cc96", "Rejected": "#ef553b"},
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
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("How to read this map")
    st.markdown(
        """
        - Passing stocks are positioned from compressed technical behavior features.
        - Nearby passing stocks have more similar feature profiles.
        - Rejected stocks remain visible in separate reason-based clouds.
        - Coordinates are exploratory measurements, not trade predictions yet.
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
    cols = st.columns(3)
    cols[0].metric("Latest market date", health.get("latest_market_date", "—")[:10])
    cols[1].metric("Latest shortlist date", health.get("latest_shortlist_date", "—")[:10])
    cols[2].metric("Dashboard exported", health.get("exported_at", "—")[:19])

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
