from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "data" / "trading_constraints_snapshot.csv"
PDT_EQUITY_THRESHOLD = 25_000.0
DEFAULT_MAX_DAY_TRADES_5D = 3
DEFAULT_MAX_WEEKLY_TRADES = 4
TRADE_ACTION_KEYWORDS = ("buy", "add", "reduce", "exit")
BUY_ACTION_KEYWORDS = ("buy", "add")
SELL_ACTION_KEYWORDS = ("reduce", "exit")


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
            "weekly_trades": None,
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
        "weekly_trades": _number(row.get("weekly_trades")),
        "max_day_trades_5d": int(
            _number(row.get("max_day_trades_5d")) or DEFAULT_MAX_DAY_TRADES_5D
        ),
        "max_weekly_trades": int(
            _number(row.get("max_weekly_trades")) or DEFAULT_MAX_WEEKLY_TRADES
        ),
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
    buying_power = constraints.get("buying_power")
    cash = constraints.get("cash")
    day_trades = constraints.get("day_trades_5d")
    weekly_trades = constraints.get("weekly_trades")
    max_day_trades = constraints.get("max_day_trades_5d") or DEFAULT_MAX_DAY_TRADES_5D
    max_weekly_trades = constraints.get("max_weekly_trades") or DEFAULT_MAX_WEEKLY_TRADES

    if weekly_trades is not None and weekly_trades >= max_weekly_trades:
        return "blocked", "Weekly trade budget is used up."

    if equity is not None and equity >= PDT_EQUITY_THRESHOLD:
        return "pdt cushion", "Equity snapshot is at or above the PDT threshold."
    if day_trades is None:
        return "caution", "Day-trade count is unknown for an account below or near the PDT threshold."
    if day_trades >= max_day_trades:
        return "blocked", "Rolling 5-trading-day day-trade budget is used up."
    if day_trades == max_day_trades - 1:
        return "caution", "Only one day trade remains in the rolling 5-trading-day budget."
    if buying_power is None and cash is None:
        return "caution", "Buying power and cash are unknown in the local constraint snapshot."
    return "safe", "Trading constraints snapshot does not show a current PDT block."


def action_requires_trade(action):
    action_text = str(action or "").lower()
    return any(keyword in action_text for keyword in TRADE_ACTION_KEYWORDS)


def action_is_buy_side(action):
    action_text = str(action or "").lower()
    return any(keyword in action_text for keyword in BUY_ACTION_KEYWORDS)


def action_is_sell_side(action):
    action_text = str(action or "").lower()
    return any(keyword in action_text for keyword in SELL_ACTION_KEYWORDS)


def action_status(action, constraints, estimated_notional=None):
    if not action_requires_trade(action):
        return {
            "constraint_status": "no trade",
            "constraint_reason": "No trade is implied by this action.",
        }

    if constraints.get("source") == "missing":
        return {
            "constraint_status": "blocked",
            "constraint_reason": "No local trading constraint snapshot exists; keep this as review or paper only.",
        }

    overall_status, overall_reason = status(constraints)
    level = _status_level(overall_status)
    reasons = [overall_reason]

    if action_is_buy_side(action):
        buying_power = constraints.get("buying_power")
        if buying_power is None:
            level = max(level, _status_level("caution"))
            reasons.append("Buying power is unknown.")
        elif buying_power <= 0:
            level = max(level, _status_level("blocked"))
            reasons.append("Buying power is zero or negative.")
        elif estimated_notional is not None and estimated_notional > buying_power:
            level = max(level, _status_level("blocked"))
            reasons.append(
                f"Estimated notional ${estimated_notional:,.2f} exceeds buying power ${buying_power:,.2f}."
            )

    if action_is_sell_side(action):
        equity = constraints.get("equity")
        day_trades = constraints.get("day_trades_5d")
        below_pdt_threshold = equity is None or equity < PDT_EQUITY_THRESHOLD
        if below_pdt_threshold and day_trades is None:
            level = max(level, _status_level("caution"))
            reasons.append(
                "Do not treat same-day exit or re-entry as executable until the rolling day-trade count is known."
            )

    weekly_trades = constraints.get("weekly_trades")
    max_weekly_trades = constraints.get("max_weekly_trades") or DEFAULT_MAX_WEEKLY_TRADES
    if weekly_trades is None:
        level = max(level, _status_level("caution"))
        reasons.append("Weekly trade count is unknown.")
    elif weekly_trades >= max_weekly_trades:
        level = max(level, _status_level("blocked"))
        reasons.append("Weekly trade budget is used up.")
    elif weekly_trades == max_weekly_trades - 1:
        level = max(level, _status_level("caution"))
        reasons.append("Only one trade remains in the weekly trade budget.")

    return {
        "constraint_status": _level_status(level),
        "constraint_reason": " ".join(_dedupe(reasons)),
    }


def _status_level(value):
    return {
        "no trade": 0,
        "safe": 1,
        "pdt cushion": 1,
        "caution": 2,
        "unknown": 3,
        "blocked": 3,
    }.get(str(value or "").lower(), 2)


def _level_status(level):
    if level >= 3:
        return "blocked"
    if level == 2:
        return "caution"
    return "safe"


def _dedupe(values):
    seen = set()
    output = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


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
                "field": "Weekly trades",
                "value": _count_or_unknown(constraints.get("weekly_trades")),
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
        "weekly_trades,max_day_trades_5d,max_weekly_trades,notes\n"
        f"{now},margin,0,0,0,0,0,{DEFAULT_MAX_DAY_TRADES_5D},{DEFAULT_MAX_WEEKLY_TRADES},manual placeholder\n"
    )


def _money_or_unknown(value):
    return "unknown" if value is None else f"${value:,.2f}"


def _count_or_unknown(value):
    return "unknown" if value is None else f"{int(value)}"
