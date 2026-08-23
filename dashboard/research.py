import numpy as np
import pandas as pd


TRADING_DAYS = 252


def prepare_prices(frame, history_days):
    prices = frame.copy()
    prices["begins_at"] = pd.to_datetime(prices["begins_at"])
    prices["close_price"] = pd.to_numeric(prices["close_price"], errors="coerce")
    prices = prices.dropna(subset=["begins_at", "close_price"])
    prices = prices.sort_values("begins_at").drop_duplicates("begins_at")
    return prices.tail(history_days).reset_index(drop=True)


def return_series(prices):
    return prices["close_price"].pct_change().dropna()


def max_drawdown(prices):
    if prices.empty:
        return np.nan
    cumulative_max = prices["close_price"].cummax()
    drawdown = prices["close_price"] / cumulative_max - 1.0
    return float(drawdown.min())


def historical_metrics(prices, risk_free_rate=0.0):
    returns = return_series(prices)
    if prices.empty or returns.empty:
        return {}
    annual_return = float(returns.mean() * TRADING_DAYS)
    annual_volatility = float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS))
    sharpe = (
        (annual_return - risk_free_rate) / annual_volatility
        if annual_volatility
        else np.nan
    )
    return {
        "observations": len(prices),
        "total_return": float(prices["close_price"].iloc[-1] / prices["close_price"].iloc[0] - 1.0),
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe": float(sharpe),
        "max_drawdown": max_drawdown(prices),
    }


def monte_carlo_paths(prices, horizon_days, simulations, seed=7):
    returns = np.log(prices["close_price"]).diff().dropna()
    if returns.empty:
        return pd.DataFrame(), {}
    rng = np.random.default_rng(seed)
    simulated_returns = rng.normal(
        loc=float(returns.mean()),
        scale=float(returns.std(ddof=1)),
        size=(horizon_days, simulations),
    )
    paths = float(prices["close_price"].iloc[-1]) * np.exp(simulated_returns.cumsum(axis=0))
    quantiles = pd.DataFrame(
        {
            "day": np.arange(1, horizon_days + 1),
            "p10": np.quantile(paths, 0.10, axis=1),
            "p25": np.quantile(paths, 0.25, axis=1),
            "median": np.quantile(paths, 0.50, axis=1),
            "p75": np.quantile(paths, 0.75, axis=1),
            "p90": np.quantile(paths, 0.90, axis=1),
        }
    )
    last = paths[-1]
    terminal = {
        "current_price": float(prices["close_price"].iloc[-1]),
        "median_price": float(np.quantile(last, 0.50)),
        "p10_price": float(np.quantile(last, 0.10)),
        "p90_price": float(np.quantile(last, 0.90)),
        "probability_up": float(np.mean(last > prices["close_price"].iloc[-1])),
    }
    return quantiles, terminal


def walk_forward_signals(
    prices,
    training_days,
    holding_days,
    momentum_days,
    minimum_momentum,
    minimum_sharpe,
):
    frame = prices[["begins_at", "close_price"]].copy()
    frame["return_1d"] = frame["close_price"].pct_change()
    rows = []
    first = max(training_days, momentum_days)
    for index in range(first, len(frame) - holding_days):
        history = frame.iloc[index - training_days:index]
        returns = history["return_1d"].dropna()
        volatility = float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(returns) > 1 else np.nan
        annual_return = float(returns.mean() * TRADING_DAYS) if len(returns) else np.nan
        sharpe = annual_return / volatility if volatility else np.nan
        momentum = float(
            frame["close_price"].iloc[index] / frame["close_price"].iloc[index - momentum_days] - 1.0
        )
        future_return = float(
            frame["close_price"].iloc[index + holding_days] / frame["close_price"].iloc[index] - 1.0
        )
        signal = bool(momentum >= minimum_momentum and sharpe >= minimum_sharpe)
        rows.append(
            {
                "date": frame["begins_at"].iloc[index],
                "price": float(frame["close_price"].iloc[index]),
                "momentum": momentum,
                "trailing_sharpe": sharpe,
                "forward_return": future_return,
                "signal": signal,
            }
        )
    return pd.DataFrame(rows)


def signal_summary(signals):
    selected = signals[signals["signal"]]
    if selected.empty:
        return {
            "signals": 0,
            "average_forward_return": np.nan,
            "win_rate": np.nan,
            "median_forward_return": np.nan,
        }
    return {
        "signals": len(selected),
        "average_forward_return": float(selected["forward_return"].mean()),
        "win_rate": float((selected["forward_return"] > 0).mean()),
        "median_forward_return": float(selected["forward_return"].median()),
    }
