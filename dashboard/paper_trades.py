import csv
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "data" / "paper_trade_ledger.csv"
LEDGER_COLUMNS = [
    "created_at",
    "updated_at",
    "ticker",
    "horizon_days",
    "review_status",
    "direction",
    "watchlist_rank",
    "model_rank",
    "probability_up",
    "confidence",
    "reference_price",
    "planned_entry",
    "stop_loss",
    "target_price",
    "paper_quantity",
    "risk_dollars",
    "notes",
]
STATUS_OPTIONS = (
    "watching",
    "needs more research",
    "paper opened",
    "paper closed",
    "rejected",
)
DIRECTION_OPTIONS = ("long", "short", "watch only")


def ledger_path():
    return Path(os.getenv("PAPER_TRADE_LEDGER_PATH", DEFAULT_LEDGER))


def empty_ledger():
    return pd.DataFrame(columns=LEDGER_COLUMNS)


def load_ledger():
    path = ledger_path()
    if not path.exists():
        return empty_ledger()
    frame = pd.read_csv(path)
    for column in LEDGER_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[LEDGER_COLUMNS].sort_values("updated_at", ascending=False)


def save_review(review):
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = {column: review.get(column, "") for column in LEDGER_COLUMNS}
    row["created_at"] = row.get("created_at") or now
    row["updated_at"] = now
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)
    return path
