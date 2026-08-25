#!/usr/bin/env python3
"""Select a stable, ranking-only paper challenger from Feature and Loss Lab output."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_HORIZONS = (5, 20)
SUMMARY_METRICS = (
    "auc",
    "accuracy",
    "brier_skill",
    "calibration_error",
    "mean_net_return",
    "win_rate",
    "mean_excess_vs_universe",
    "average_sleeve_compound_return",
    "worst_sleeve_max_drawdown",
    "auc_split_std",
    "excess_return_split_std",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Choose one architecture that has stable positive excess return across "
            "all required chronological splits and emit a guarded paper-only config."
        )
    )
    parser.add_argument("--experiment-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("paper_challenger.json"))
    parser.add_argument("--min-splits", type=int, default=3)
    parser.add_argument("--min-mean-excess", type=float, default=0.005)
    parser.add_argument("--max-excess-split-std", type=float, default=0.005)
    return parser.parse_args()


def require_columns(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def stable_candidates(
    by_split: pd.DataFrame,
    summary: pd.DataFrame,
    min_splits: int,
    min_mean_excess: float,
    max_excess_split_std: float,
) -> list[str]:
    candidates: list[str] = []
    for architecture in sorted(by_split["architecture"].dropna().unique()):
        architecture_ok = True
        for horizon in REQUIRED_HORIZONS:
            rows = by_split[
                (by_split["architecture"] == architecture)
                & (by_split["horizon"] == horizon)
            ]
            summary_rows = summary[
                (summary["architecture"] == architecture)
                & (summary["horizon"] == horizon)
            ]
            if len(rows) < min_splits or len(summary_rows) != 1:
                architecture_ok = False
                break
            result = summary_rows.iloc[0]
            if not bool((rows["mean_excess_vs_universe"] > 0).all()):
                architecture_ok = False
                break
            if float(result["mean_excess_vs_universe"]) < min_mean_excess:
                architecture_ok = False
                break
            if float(result["excess_return_split_std"]) > max_excess_split_std:
                architecture_ok = False
                break
        if architecture_ok:
            candidates.append(str(architecture))
    return candidates


def finite_or_none(value: object) -> float | None:
    numeric = float(value)
    return numeric if np.isfinite(numeric) else None


def build_config(
    architecture: str,
    by_split: pd.DataFrame,
    summary: pd.DataFrame,
    experiment_dir: Path,
    args: argparse.Namespace,
) -> dict[str, object]:
    selected_splits = by_split[by_split["architecture"] == architecture].copy()
    selected_summary = summary[summary["architecture"] == architecture].copy()

    brier_values = selected_splits["brier_skill"].to_numpy(dtype=float)
    probability_enabled = bool(
        np.isfinite(brier_values).all() and (brier_values > 0).all()
    )

    evidence: dict[str, object] = {}
    for horizon in REQUIRED_HORIZONS:
        row = selected_summary[selected_summary["horizon"] == horizon].iloc[0]
        split_rows = selected_splits[selected_splits["horizon"] == horizon].sort_values(
            "split"
        )
        horizon_evidence = {
            metric: finite_or_none(row[metric]) for metric in SUMMARY_METRICS
        }
        horizon_evidence["split_excess_returns"] = [
            finite_or_none(value)
            for value in split_rows["mean_excess_vs_universe"].tolist()
        ]
        horizon_evidence["split_brier_skills"] = [
            finite_or_none(value) for value in split_rows["brier_skill"].tolist()
        ]
        evidence[str(horizon)] = horizon_evidence

    experiment_id = experiment_dir.name
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "paper_only",
        "experiment_id": experiment_id,
        "architecture": architecture,
        "feature_input": "base_plus_similarity_graph",
        "prediction_semantics": (
            "probability" if probability_enabled else "ranking_score_only"
        ),
        "probability_enabled": probability_enabled,
        "live_trading_enabled": False,
        "selection_rule": {
            "required_horizons": list(REQUIRED_HORIZONS),
            "minimum_splits_per_horizon": args.min_splits,
            "minimum_mean_excess_return": args.min_mean_excess,
            "maximum_excess_return_split_std": args.max_excess_split_std,
            "require_positive_excess_in_every_split": True,
            "require_positive_brier_skill_for_probability": True,
        },
        "evidence": evidence,
        "guardrails": [
            "Use scores only to rank paper-trading candidates.",
            "Do not display the score as a probability or confidence percentage.",
            "Do not route outputs to Robinhood or another brokerage.",
            "Keep the existing production model unchanged.",
            "Re-evaluate after additional chronological windows before promotion.",
        ],
        "source_files": [
            str(experiment_dir / "architecture_loss_summary.csv"),
            str(experiment_dir / "architecture_loss_by_split.csv"),
            str(experiment_dir / "leakage_audit.csv"),
        ],
    }


def main() -> None:
    args = parse_args()
    summary_path = args.experiment_dir / "architecture_loss_summary.csv"
    by_split_path = args.experiment_dir / "architecture_loss_by_split.csv"
    summary = pd.read_csv(summary_path)
    by_split = pd.read_csv(by_split_path)

    require_columns(
        summary,
        {
            "horizon",
            "architecture",
            "mean_excess_vs_universe",
            "excess_return_split_std",
            *SUMMARY_METRICS,
        },
        "architecture summary",
    )
    require_columns(
        by_split,
        {
            "horizon",
            "split",
            "architecture",
            "brier_skill",
            "mean_excess_vs_universe",
        },
        "architecture split results",
    )

    candidates = stable_candidates(
        by_split,
        summary,
        args.min_splits,
        args.min_mean_excess,
        args.max_excess_split_std,
    )
    if not candidates:
        raise RuntimeError("No architecture passed the paper-challenger stability gates")

    scores = {
        architecture: float(
            summary[
                (summary["architecture"] == architecture)
                & (summary["horizon"].isin(REQUIRED_HORIZONS))
            ]["mean_excess_vs_universe"].mean()
        )
        for architecture in candidates
    }
    winner = max(scores, key=scores.get)
    config = build_config(winner, by_split, summary, args.experiment_dir, args)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(
        f"Selected {winner} as a paper-only challenger; "
        f"probability_enabled={config['probability_enabled']}"
    )


if __name__ == "__main__":
    main()
