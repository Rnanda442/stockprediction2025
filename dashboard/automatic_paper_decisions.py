import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "data" / "automatic_paper_decisions.csv"
MODEL_NAME = "sgd_logistic_baseline"
MODEL_VERSION = "baseline_v1"

LEDGER_COLUMNS = [
    "decision_id",
    "created_at",
    "source_date",
    "ticker",
    "action",
    "reason",
    "horizon_days",
    "model_name",
    "model_version",
    "watchlist_rank",
    "model_rank",
    "probability_up",
    "confidence",
    "reference_price",
    "paper_quantity",
    "stop_loss",
    "target_price",
    "risk_dollars",
    "constraint_status",
    "constraint_reason",
    "portfolio_value",
    "portfolio_weight",
    "record_status",
    "top_positive_drivers",
    "top_negative_drivers",
]

REQUIRED_FIELDS = (
    "source_date",
    "ticker",
    "action",
    "reason",
    "constraint_status",
)


def _number(value, default=None):
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return default
    return float(numeric)


def _integer(value, default=None):
    numeric = _number(value)
    return default if numeric is None else int(numeric)


def _decision_id(record):
    identity = "|".join(
        [
            str(record["source_date"]),
            str(record["ticker"]),
            str(record["action"]),
            str(record.get("horizon_days") or ""),
            str(record.get("model_version") or ""),
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def build_record(values, created_at=None):
    record = {column: values.get(column, "") for column in LEDGER_COLUMNS}
    record["created_at"] = created_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    record["ticker"] = str(record["ticker"]).strip().upper()
    record["action"] = str(record["action"]).strip().lower()
    record["model_name"] = record["model_name"] or MODEL_NAME
    record["model_version"] = record["model_version"] or MODEL_VERSION
    record["record_status"] = record["record_status"] or "proposed"

    for field in REQUIRED_FIELDS:
        if not str(record.get(field, "")).strip():
            raise ValueError(f"Automatic paper decision is missing required field: {field}")

    record["horizon_days"] = _integer(record["horizon_days"])
    record["watchlist_rank"] = _integer(record["watchlist_rank"])
    record["model_rank"] = _integer(record["model_rank"])
    record["paper_quantity"] = _integer(record["paper_quantity"], 0)

    numeric_fields = (
        "probability_up",
        "confidence",
        "reference_price",
        "stop_loss",
        "target_price",
        "risk_dollars",
        "portfolio_value",
        "portfolio_weight",
    )
    for field in numeric_fields:
        record[field] = _number(record[field])

    if record["risk_dollars"] is None:
        entry = record["reference_price"]
        stop = record["stop_loss"]
        quantity = record["paper_quantity"]
        if entry is not None and stop is not None and quantity:
            record["risk_dollars"] = abs(entry - stop) * quantity

    record["decision_id"] = record["decision_id"] or _decision_id(record)
    return {column: record[column] for column in LEDGER_COLUMNS}


def record_from_board_row(
    row,
    source_date,
    constraint_status,
    constraint_reason,
    portfolio_value=0.0,
):
    entry = _number(row.get("entry_price"))
    volatility = _number(row.get("vol_60d"))
    quantity = _integer(row.get("paper_quantity_1pct_risk"), 0)
    action = row.get("decision", "")

    stop_loss = None
    target_price = None
    if entry is not None and entry > 0 and action == "paper buy candidate":
        stop_pct = 0.08 if volatility is None else min(0.18, max(0.05, volatility * 2))
        stop_loss = entry * (1 - stop_pct)
        target_price = entry * (1 + stop_pct * 2)
    else:
        quantity = 0

    return build_record(
        {
            "source_date": source_date,
            "ticker": row.get("ticker"),
            "action": action,
            "reason": row.get("why"),
            "horizon_days": row.get("model_horizon_days"),
            "watchlist_rank": row.get("rank"),
            "model_rank": row.get("model_rank"),
            "probability_up": row.get("model_probability_up"),
            "confidence": row.get("confidence"),
            "reference_price": entry,
            "paper_quantity": quantity,
            "stop_loss": stop_loss,
            "target_price": target_price,
            "constraint_status": constraint_status,
            "constraint_reason": constraint_reason,
            "portfolio_value": portfolio_value,
            "portfolio_weight": row.get("portfolio_weight"),
            "top_positive_drivers": row.get("top_positive_drivers"),
            "top_negative_drivers": row.get("top_negative_drivers"),
        }
    )


def load_ledger(path=DEFAULT_LEDGER):
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    frame = pd.read_csv(path)
    for column in LEDGER_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[LEDGER_COLUMNS].sort_values("created_at", ascending=False)


def append_records(records, path=DEFAULT_LEDGER):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_ledger(path)
    existing_ids = set(existing["decision_id"].dropna().astype(str))
    normalized = [build_record(record) for record in records]
    new_records = []
    seen_ids = set(existing_ids)
    for record in normalized:
        if record["decision_id"] in seen_ids:
            continue
        new_records.append(record)
        seen_ids.add(record["decision_id"])
    if not new_records:
        return 0

    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerows(new_records)
    return len(new_records)
