import argparse
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import automatic_paper_decisions
from dashboard import data
from dashboard import decision_policy


DEFAULT_OUTPUT = Path("analytics") / "automatic_paper_decisions.csv"


def model_summary():
    frames = []
    for horizon in (5, 20, 60):
        predictions = data.latest_model_predictions(horizon, limit=10000)
        if predictions.empty:
            continue
        predictions = predictions.copy()
        predictions["model_horizon_days"] = horizon
        frames.append(predictions)
    if not frames:
        return pd.DataFrame(
            columns=[
                "ticker",
                "model_probability_up",
                "model_rank",
                "model_horizon_days",
                "top_positive_drivers",
                "top_negative_drivers",
            ]
        )

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(
        ["ticker", "probability_up"], ascending=[True, False]
    )
    best = combined.groupby("ticker", as_index=False).first()
    return best.rename(
        columns={
            "probability_up": "model_probability_up",
            "model_rank": "model_rank",
        }
    )[
        [
            "ticker",
            "model_probability_up",
            "model_rank",
            "model_horizon_days",
            "top_positive_drivers",
            "top_negative_drivers",
        ]
    ]


def build_board(portfolio_value):
    watch = data.watchlist()
    if watch.empty:
        raise RuntimeError("LatestWatchlist is missing or empty.")
    watch = watch.copy()
    watch["ticker"] = watch["ticker"].str.upper()
    board = watch.merge(model_summary(), on="ticker", how="left")
    board["is_holding"] = False
    board["portfolio_weight"] = 0.0
    return decision_policy.apply_policy(board, portfolio_value)


def generate_records(portfolio_value, constraint_status, constraint_reason):
    health = data.health()
    source_date = str(health.get("latest_market_date", ""))[:10]
    if not source_date:
        raise RuntimeError("PipelineHealth is missing latest_market_date.")

    board = build_board(portfolio_value)
    return [
        automatic_paper_decisions.record_from_board_row(
            row,
            source_date=source_date,
            constraint_status=constraint_status,
            constraint_reason=constraint_reason,
            portfolio_value=portfolio_value,
        )
        for row in board.to_dict("records")
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Generate one automatic paper-decision record per watchlist ticker."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--portfolio-value", type=float, default=0.0)
    parser.add_argument(
        "--constraint-status",
        default="unknown",
        choices=("safe", "caution", "blocked", "unknown", "pdt cushion"),
    )
    parser.add_argument(
        "--constraint-reason",
        default="Cloud pipeline does not yet have a verified broker constraint snapshot.",
    )
    args = parser.parse_args()

    records = generate_records(
        portfolio_value=max(args.portfolio_value, 0.0),
        constraint_status=args.constraint_status,
        constraint_reason=args.constraint_reason,
    )
    written = automatic_paper_decisions.append_records(records, args.output)
    print(
        f"Automatic paper decisions: generated={len(records)} "
        f"written={written} output={args.output}"
    )


if __name__ == "__main__":
    main()
