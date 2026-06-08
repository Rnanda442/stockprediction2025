import pandas as pd


ACTION_PRIORITY = {
    "hold / consider add": 0,
    "paper buy candidate": 1,
    "hold": 2,
    "watch": 3,
    "review reduce": 4,
    "avoid for now": 5,
}


def decision_action(row):
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


def decision_reason(row):
    parts = []
    rank = pd.to_numeric(row.get("rank"), errors="coerce")
    probability = pd.to_numeric(row.get("model_probability_up"), errors="coerce")
    if not pd.isna(rank):
        parts.append(f"watchlist rank {int(rank)}")
    if not pd.isna(probability):
        horizon = pd.to_numeric(row.get("model_horizon_days"), errors="coerce")
        if pd.isna(horizon):
            parts.append(f"{probability:.1%} model probability")
        else:
            parts.append(f"{probability:.1%} model probability over {int(horizon)}d")
    if row.get("is_holding"):
        weight = pd.to_numeric(row.get("portfolio_weight"), errors="coerce")
        if not pd.isna(weight):
            parts.append(f"already held at {weight:.1%} of portfolio")
    return "; ".join(parts) if parts else "not enough signal yet"


def paper_quantity(row, portfolio_value):
    if portfolio_value <= 0:
        return 0
    entry = pd.to_numeric(row.get("entry_price"), errors="coerce")
    volatility = pd.to_numeric(row.get("vol_60d"), errors="coerce")
    if pd.isna(entry) or entry <= 0:
        return 0
    stop_pct = 0.08
    if not pd.isna(volatility) and volatility > 0:
        stop_pct = min(0.18, max(0.05, volatility * 2.0))
    risk_budget = portfolio_value * 0.01
    risk_per_share = entry * stop_pct
    if risk_per_share <= 0:
        return 0
    return max(int(risk_budget // risk_per_share), 0)


def apply_policy(board, portfolio_value):
    result = board.copy()
    result["decision"] = result.apply(decision_action, axis=1)
    result["why"] = result.apply(decision_reason, axis=1)
    result["paper_quantity_1pct_risk"] = result.apply(
        lambda row: paper_quantity(row, portfolio_value), axis=1
    )
    result["decision_priority"] = (
        result["decision"].map(ACTION_PRIORITY).fillna(9).astype(int)
    )
    return result

