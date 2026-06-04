import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from dashboard import data


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "data" / "robinhood_portfolio_snapshot.csv"


@dataclass(frozen=True)
class ReplayConfig:
    lookback_days: int = 60
    replay_days: int = 30
    rebalance_days: int = 5
    max_positions: int = 5
    minimum_dollar_volume: float = 2_000_000.0
    maximum_volatility: float = 0.08
    cash: float = 0.0


def parse_holdings(text):
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in re.split(r"[,\s]+", line) if part.strip()]
        if len(parts) < 2:
            raise ValueError(f"Could not parse holding line: {line!r}")
        ticker = parts[0].upper()
        try:
            quantity = float(parts[1])
        except ValueError as exc:
            raise ValueError(f"Quantity must be numeric for {ticker}") from exc
        if quantity < 0:
            raise ValueError(f"Quantity cannot be negative for {ticker}")
        if quantity:
            rows.append({"ticker": ticker, "quantity": quantity})
    return pd.DataFrame(rows, columns=["ticker", "quantity"])


def latest_snapshot_text(path=DEFAULT_SNAPSHOT):
    if not path.exists():
        return ""
    frame = pd.read_csv(path)
    if frame.empty or not {"ticker", "quantity"}.issubset(frame.columns):
        return ""
    if "snapshot_at" in frame.columns:
        latest = frame["snapshot_at"].dropna().max()
        if latest:
            frame = frame[frame["snapshot_at"] == latest]
    frame = frame.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce")
    frame = frame.dropna(subset=["ticker", "quantity"])
    frame = frame[frame["quantity"] > 0]
    return "\n".join(f"{row.ticker}, {row.quantity:g}" for row in frame.itertuples())


def latest_snapshot_cash(path=DEFAULT_SNAPSHOT):
    if not path.exists():
        return 0.0
    frame = pd.read_csv(path)
    if frame.empty or "cash" not in frame.columns:
        return 0.0
    if "snapshot_at" in frame.columns:
        latest = frame["snapshot_at"].dropna().max()
        if latest:
            frame = frame[frame["snapshot_at"] == latest]
    cash = pd.to_numeric(frame["cash"], errors="coerce").dropna()
    return float(cash.iloc[0]) if not cash.empty else 0.0


def price_history():
    frame = data.query(
        """
        SELECT ticker, begins_at, close_price, volume
        FROM RecentPrices
        WHERE close_price IS NOT NULL AND close_price > 0
        ORDER BY begins_at, ticker
        """
    )
    if frame.empty:
        return frame
    frame["begins_at"] = pd.to_datetime(frame["begins_at"])
    frame["ticker"] = frame["ticker"].str.upper()
    frame["close_price"] = pd.to_numeric(frame["close_price"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    return frame.dropna(subset=["begins_at", "ticker", "close_price"])


def candidate_scores(history, signal_date, config):
    start = signal_date - pd.Timedelta(days=max(10, config.lookback_days * 2))
    window = history[(history["begins_at"] < signal_date) & (history["begins_at"] >= start)]
    if window.empty:
        return pd.DataFrame()

    rows = []
    for ticker, group in window.groupby("ticker"):
        group = group.sort_values("begins_at").tail(config.lookback_days)
        if len(group) < max(20, config.lookback_days // 2):
            continue
        prices = group["close_price"].to_numpy(dtype=float)
        returns = pd.Series(prices).pct_change().dropna()
        if returns.empty:
            continue
        total_return = prices[-1] / prices[0] - 1.0
        volatility = float(returns.std())
        dollar_volume = float((group["close_price"] * group["volume"]).tail(20).mean())
        if not np.isfinite(volatility) or not np.isfinite(dollar_volume):
            continue
        if dollar_volume < config.minimum_dollar_volume or volatility > config.maximum_volatility:
            continue
        score = total_return / max(volatility, 1e-6)
        rows.append(
            {
                "ticker": ticker,
                "score": score,
                "trailing_return": total_return,
                "volatility": volatility,
                "dollar_volume": dollar_volume,
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["score", "dollar_volume"], ascending=False
    ).reset_index(drop=True)


def _price_matrix(history):
    prices = history.pivot_table(
        index="begins_at",
        columns="ticker",
        values="close_price",
        aggfunc="last",
    ).sort_index()
    return prices.ffill()


def _start_date(prices, config):
    if len(prices.index) < config.replay_days + config.lookback_days + 2:
        return None
    return prices.index[-config.replay_days]


def actual_hold_curve(prices, holdings, start_date, cash):
    replay_prices = prices.loc[prices.index >= start_date].copy()
    tickers = [ticker for ticker in holdings["ticker"] if ticker in replay_prices.columns]
    if not tickers:
        return pd.DataFrame(), 0.0
    quantities = holdings.set_index("ticker").loc[tickers, "quantity"]
    values = replay_prices[tickers].mul(quantities, axis=1)
    curve = values.sum(axis=1).add(cash).rename("actual_hold").reset_index()
    curve = curve.rename(columns={"begins_at": "date"})
    return curve, float(curve["actual_hold"].iloc[0])


def strategy_replay(history, prices, start_date, starting_value, config):
    replay_dates = list(prices.loc[prices.index >= start_date].index)
    if not replay_dates or starting_value <= 0:
        return pd.DataFrame(), pd.DataFrame()

    cash = float(starting_value)
    shares = {}
    trades = []
    curve_rows = []
    current_tickers = []

    for index, current_date in enumerate(replay_dates):
        should_rebalance = index == 0 or index % config.rebalance_days == 0
        if should_rebalance:
            portfolio_value = cash
            for ticker, quantity in shares.items():
                price = prices.at[current_date, ticker] if ticker in prices.columns else np.nan
                if pd.notna(price):
                    portfolio_value += quantity * float(price)

            scores = candidate_scores(history, current_date, config)
            selected = [
                ticker
                for ticker in scores.head(config.max_positions)["ticker"].tolist()
                if ticker in prices.columns and pd.notna(prices.at[current_date, ticker])
            ]
            if selected:
                previous = set(current_tickers)
                current_tickers = selected
                cash = 0.0
                shares = {}
                allocation = portfolio_value / len(selected)
                for ticker in selected:
                    price = float(prices.at[current_date, ticker])
                    shares[ticker] = allocation / price
                trades.append(
                    {
                        "date": current_date,
                        "action": "rebalance",
                        "tickers": ", ".join(selected),
                        "entered": ", ".join(sorted(set(selected) - previous)),
                        "exited": ", ".join(sorted(previous - set(selected))),
                        "portfolio_value": portfolio_value,
                    }
                )

        value = cash
        for ticker, quantity in shares.items():
            price = prices.at[current_date, ticker] if ticker in prices.columns else np.nan
            if pd.notna(price):
                value += quantity * float(price)
        curve_rows.append({"date": current_date, "strategy": value})

    return pd.DataFrame(curve_rows), pd.DataFrame(trades)


def replay(holdings_text, config):
    holdings = parse_holdings(holdings_text)
    if holdings.empty:
        return {
            "error": "Enter at least one starting holding as TICKER, quantity.",
            "curve": pd.DataFrame(),
            "trades": pd.DataFrame(),
            "candidates": pd.DataFrame(),
        }

    history = price_history()
    if history.empty:
        return {
            "error": "No recent price history is available in dashboard_data.db.",
            "curve": pd.DataFrame(),
            "trades": pd.DataFrame(),
            "candidates": pd.DataFrame(),
        }

    prices = _price_matrix(history)
    start_date = _start_date(prices, config)
    if start_date is None:
        return {
            "error": "Not enough recent history to run the selected replay window.",
            "curve": pd.DataFrame(),
            "trades": pd.DataFrame(),
            "candidates": pd.DataFrame(),
        }

    actual, starting_value = actual_hold_curve(prices, holdings, start_date, config.cash)
    if actual.empty:
        return {
            "error": "None of the entered holdings have prices in the replay window.",
            "curve": pd.DataFrame(),
            "trades": pd.DataFrame(),
            "candidates": pd.DataFrame(),
        }

    strategy, trades = strategy_replay(history, prices, start_date, starting_value, config)
    curve = actual.merge(strategy, on="date", how="left")
    curve["strategy"] = curve["strategy"].ffill()
    curve["strategy_edge"] = curve["strategy"] - curve["actual_hold"]
    curve["actual_return"] = curve["actual_hold"] / curve["actual_hold"].iloc[0] - 1.0
    curve["strategy_return"] = curve["strategy"] / curve["strategy"].iloc[0] - 1.0
    curve["strategy_edge_pct"] = curve["strategy_return"] - curve["actual_return"]
    latest_candidates = candidate_scores(history, prices.index[-1], config).head(25)
    return {
        "error": "",
        "curve": curve,
        "trades": trades,
        "candidates": latest_candidates,
        "start_date": start_date,
        "end_date": prices.index[-1],
    }
