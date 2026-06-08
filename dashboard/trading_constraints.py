from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "data" / "trading_constraints_snapshot.csv"
PDT_EQUITY_THRESHOLD = 25_000.0
DEFAULT_MAX_DAY_TRADES_5D = 3
DEFAULT_MAX_WEEKLY_TRADES = 4


def load_snapshot(path=DEFAULT_SNAPSHOT):
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    if frame.empty:
        return frame
    if "snapshot_at" in frame.columns:
        frame = frame.sort_values("snapshot_at", ascending=False)
    return frame


def latest_constraints(path=DEFAULT_SNAPSHOT):
    frame = load_snapshot(path)
    if frame.empty:
        return {
            "source": "missing",
            "snapshot_at": "",
            "account_type": "unknown",
            "equity": None,
            "buying_power": None,
            "cash": None,
            "day_trades_5d": None,
            "max_day_trades_5d": DEFAULT_MAX_DAY_TRADES_5D,
            "max_weekly_trades": DEFAULT_MAX_WEEKLY_TRADES,
            "notes": "No local trading constraint snapshot found.",
        }

    row = frame.iloc[0].to_dict()
    return {
        "source": "snapshot",
        "snapshot_at": row.get("snapshot_at", ""),
        "account_type": row.get("account_type", "unknown"),
        "equity": _number(row.get("equity")),
        "buying_power": _number(row.get("buying_power")),
        "cash": _number(row.get("cash")),
        "day_trades_5d": _number(row.get("day_trades_5d")),
        "max_day_trades_5d": int(_number(row.get("max_day_trades_5d")) or DEFAULT_MAX_DAY_TRADES_5D),
        "max_weekly_trades": int(_number(row.get("max_weekly_trades")) or DEFAULT_MAX_WEEKLY_TRADES),
        "notes": row.get("notes", ""),
    }


def _number(value):
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return None
    return float(numeric)


def status(constraints):
    if constraints["source"] == "missing":
        return "unknown", "Add a local trading constraint snapshot before treating decisions as executable."

    equity = constraints.get("equity")
    day_trades = constraints.get("day_trades_5d")
    max_day_trades = constraints.get("max_day_trades_5d") or DEFAULT_MAX_DAY_TRADES_5D

    if equity is not None and equity >= PDT_EQUITY_THRESHOLD:
        return "pdt cushion", "Equity snapshot is at or above the PDT threshold."
    if day_trades is None:
        return "caution", "Day-trade count is unknown for an account below or near the PDT threshold."
    if day_trades >= max_day_trades:
        return "blocked", "Rolling 5-trading-day day-trade budget is used up."
    if day_trades == max_day_trades - 1:
        return "caution", "Only one day trade remains in the rolling 5-trading-day budget."
    return "safe", "Trading constraints snapshot does not show a current PDT block."


def as_display_rows(constraints):
    state, message = status(constraints)
    return pd.DataFrame(
        [
            {"field": "Status", "value": state},
            {"field": "Reason", "value": message},
            {"field": "Snapshot at", "value": constraints.get("snapshot_at") or "not available"},
            {"field": "Account type", "value": constraints.get("account_type") or "unknown"},
            {"field": "Equity", "value": _money_or_unknown(constraints.get("equity"))},
            {"field": "Buying power", "value": _money_or_unknown(constraints.get("buying_power"))},
            {"field": "Cash", "value": _money_or_unknown(constraints.get("cash"))},
            {
                "field": "Day trades in 5 trading days",
                "value": _count_or_unknown(constraints.get("day_trades_5d")),
            },
            {
                "field": "Max day trades in 5 days",
                "value": str(constraints.get("max_day_trades_5d") or "unknown"),
            },
            {
                "field": "Max weekly trades",
                "value": str(constraints.get("max_weekly_trades") or "unknown"),
            },
            {"field": "Notes", "value": constraints.get("notes") or ""},
        ]
    )


def sample_snapshot_text():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        "snapshot_at,account_type,equity,buying_power,cash,day_trades_5d,"
        "max_day_trades_5d,max_weekly_trades,notes\n"
        f"{now},margin,0,0,0,0,{DEFAULT_MAX_DAY_TRADES_5D},{DEFAULT_MAX_WEEKLY_TRADES},manual placeholder\n"
    )


def _money_or_unknown(value):
    return "unknown" if value is None else f"${value:,.2f}"


def _count_or_unknown(value):
    return "unknown" if value is None else f"{int(value)}"

