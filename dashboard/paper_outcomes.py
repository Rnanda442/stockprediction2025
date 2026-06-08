import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DECISIONS = ROOT / "analytics" / "automatic_paper_decisions.csv"
DEFAULT_OUTCOMES = ROOT / "analytics" / "automatic_paper_decision_outcomes.csv"
HORIZONS = (1, 5, 20, 60)

OUTCOME_COLUMNS = [
    "outcome_id",
    "decision_id",
    "ticker",
    "source_date",
    "entry_price",
    "action",
    "model_version",
    "decision_horizon_days",
    "evaluation_horizon_days",
    "stop_loss",
    "target_price",
    "evaluation_date",
    "evaluation_price",
    "return_pct",
    "barrier_result",
    "barrier_date",
    "status",
    "evaluated_at",
]


def _number(value):
    numeric = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(numeric) else float(numeric)


def _outcome_id(decision_id, horizon, status, evaluation_date):
    identity = f"{decision_id}|{int(horizon)}|{status}|{evaluation_date}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def load_outcomes(path=DEFAULT_OUTCOMES):
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=OUTCOME_COLUMNS)
    frame = pd.read_csv(path)
    for column in OUTCOME_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[OUTCOME_COLUMNS]


def _normalize_prices(prices):
    frame = prices.copy()
    if frame.empty:
        return pd.DataFrame(columns=["ticker", "begins_at", "close_price"])
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    frame["begins_at"] = pd.to_datetime(frame["begins_at"], errors="coerce").dt.normalize()
    frame["close_price"] = pd.to_numeric(frame["close_price"], errors="coerce")
    return (
        frame.dropna(subset=["ticker", "begins_at", "close_price"])
        .query("close_price > 0")
        .sort_values(["ticker", "begins_at"])
        .drop_duplicates(["ticker", "begins_at"], keep="last")
    )


def _barrier_result(path, stop_loss, target_price):
    hits = []
    if stop_loss is not None:
        stopped = path[path["close_price"] <= stop_loss]
        if not stopped.empty:
            hits.append(("stopped", stopped.iloc[0]["begins_at"]))
    if target_price is not None:
        targeted = path[path["close_price"] >= target_price]
        if not targeted.empty:
            hits.append(("targeted", targeted.iloc[0]["begins_at"]))
    if not hits:
        return "none", None
    status, date = min(hits, key=lambda item: item[1])
    return status, pd.Timestamp(date).date().isoformat()


def evaluate_decisions(decisions, prices, existing=None, evaluated_at=None):
    if decisions.empty:
        return []
    prices = _normalize_prices(prices)
    existing_ids = set()
    if existing is not None and not existing.empty:
        existing_ids = set(existing["outcome_id"].dropna().astype(str))

    market_dates = sorted(prices["begins_at"].dropna().unique())
    evaluated_at = evaluated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []

    for decision in decisions.to_dict("records"):
        decision_id = str(decision.get("decision_id", "")).strip()
        ticker = str(decision.get("ticker", "")).strip().upper()
        source_date = pd.to_datetime(decision.get("source_date"), errors="coerce")
        if not decision_id or not ticker or pd.isna(source_date):
            continue
        source_date = source_date.normalize()
        ticker_prices = prices[
            (prices["ticker"] == ticker) & (prices["begins_at"] > source_date)
        ].reset_index(drop=True)
        global_future_dates = [date for date in market_dates if date > source_date]

        for horizon in HORIZONS:
            if len(global_future_dates) < horizon:
                continue

            entry_price = _number(decision.get("reference_price"))
            common = {
                "decision_id": decision_id,
                "ticker": ticker,
                "source_date": source_date.date().isoformat(),
                "entry_price": entry_price,
                "action": str(decision.get("action", "")),
                "model_version": str(decision.get("model_version", "")),
                "decision_horizon_days": decision.get("horizon_days"),
                "evaluation_horizon_days": horizon,
                "stop_loss": _number(decision.get("stop_loss")),
                "target_price": _number(decision.get("target_price")),
                "evaluated_at": evaluated_at,
            }
            if entry_price is None or entry_price <= 0 or len(ticker_prices) < horizon:
                evaluation_date = pd.Timestamp(
                    global_future_dates[horizon - 1]
                ).date().isoformat()
                outcome_id = _outcome_id(
                    decision_id, horizon, "unavailable", evaluation_date
                )
                if outcome_id in existing_ids:
                    continue
                records.append(
                    {
                        **common,
                        "outcome_id": outcome_id,
                        "evaluation_date": evaluation_date,
                        "evaluation_price": None,
                        "return_pct": None,
                        "barrier_result": "unavailable",
                        "barrier_date": None,
                        "status": "unavailable",
                    }
                )
                continue

            evaluation = ticker_prices.iloc[horizon - 1]
            evaluation_price = float(evaluation["close_price"])
            path = ticker_prices.iloc[:horizon]
            barrier_result, barrier_date = _barrier_result(
                path, common["stop_loss"], common["target_price"]
            )
            status = barrier_result if barrier_result in {"stopped", "targeted"} else "matured"
            evaluation_date = evaluation["begins_at"].date().isoformat()
            outcome_id = _outcome_id(decision_id, horizon, status, evaluation_date)
            if outcome_id in existing_ids:
                continue
            records.append(
                {
                    **common,
                    "outcome_id": outcome_id,
                    "evaluation_date": evaluation_date,
                    "evaluation_price": evaluation_price,
                    "return_pct": evaluation_price / entry_price - 1.0,
                    "barrier_result": barrier_result,
                    "barrier_date": barrier_date,
                    "status": status,
                }
            )
    return [{column: record.get(column) for column in OUTCOME_COLUMNS} for record in records]


def append_outcomes(records, path=DEFAULT_OUTCOMES):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_outcomes(path)
    existing_ids = set(existing["outcome_id"].dropna().astype(str))
    new_records = []
    for record in records:
        if record["outcome_id"] in existing_ids:
            continue
        new_records.append(record)
        existing_ids.add(record["outcome_id"])
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=OUTCOME_COLUMNS).writeheader()
    if not new_records:
        return 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTCOME_COLUMNS)
        writer.writerows(new_records)
    return len(new_records)
