"""Build a tiny Drive-ready analysis pack from Open Science Lab summaries.

The warehouse can keep large run archives locally in Open Science Lab. This
script turns the compact summary CSVs into a smaller bundle that is useful in
Google Drive: one markdown digest, one JSON digest, a chart manifest, and a few
small CSV inputs for charts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = ROOT / "warehouse"

SUMMARY_FILES = {
    "run_inventory": "summaries/daily/run_inventory.csv",
    "model_score_by_model": "summaries/daily/model_score_by_model.csv",
    "paper_outcome_summary": "summaries/daily/paper_outcome_summary.csv",
    "model_quality_gates": "summaries/analysis/model_quality_gates.csv",
    "model_quality_gate_summary": "summaries/analysis/model_quality_gate_summary.csv",
    "prediction_signal_shape": "summaries/analysis/prediction_signal_shape.csv",
    "prediction_probability_buckets": "summaries/analysis/prediction_probability_buckets.csv",
    "paper_decision_calibration_proxy": "summaries/analysis/paper_decision_calibration_proxy.csv",
    "artifact_health": "summaries/analysis/artifact_health.csv",
    "leakage_audit_summary": "summaries/analysis/leakage_audit_summary.csv",
    "analysis_priorities": "summaries/analysis/analysis_priorities.csv",
}

CHART_MANIFEST = [
    {
        "chart_name": "Model gate matrix",
        "source_csv": "csv/model_quality_gates.csv",
        "x": "horizon_days",
        "y": "model_name",
        "series": "auc_gate,brier_gate,return_edge_gate,walk_forward_gate",
        "purpose": "See exactly which trust gates fail by model and horizon.",
        "caution": "Gate thresholds are research guardrails, not trading approval.",
    },
    {
        "chart_name": "Calibration proxy",
        "source_csv": "csv/paper_decision_calibration_proxy.csv",
        "x": "probability_bucket",
        "y": "observed_win_rate,avg_probability_up",
        "series": "action,horizon_days",
        "purpose": "Compare predicted probability buckets with realized paper outcomes.",
        "caution": "This is a paper-decision proxy until row-level holdout predictions are exported.",
    },
    {
        "chart_name": "Probability signal shape",
        "source_csv": "csv/latest_probability_signal_shape.csv",
        "x": "model_name",
        "y": "high_confidence_share,extreme_share",
        "series": "horizon_days",
        "purpose": "Spot overconfident models before trusting high-probability ranks.",
        "caution": "Use latest rows with meaningful sample size; ignore one-row historical spikes.",
    },
    {
        "chart_name": "Paper outcomes",
        "source_csv": "csv/paper_outcome_summary.csv",
        "x": "evaluation_horizon_days",
        "y": "avg_return,win_rate,evaluated",
        "series": "action",
        "purpose": "Separate candidate performance from watch and avoid buckets.",
        "caution": "Paper buy candidate rows are currently too sparse for stable conclusions.",
    },
    {
        "chart_name": "Leakage timeline",
        "source_csv": "csv/leakage_audit_summary.csv",
        "x": "horizon_days",
        "y": "training_end,test_start,test_end",
        "series": "model_name",
        "purpose": "Verify train/test order, embargo, and walk-forward coverage visually.",
        "caution": "This compact audit checks windows and metric spikes, not every raw feature timestamp.",
    },
    {
        "chart_name": "Artifact health",
        "source_csv": "csv/artifact_health.csv",
        "x": "latest_market_date",
        "y": "megabytes_copied",
        "series": "analysis_ready,compact_only",
        "purpose": "Show which runs have enough outputs for analysis and which are partial.",
        "caution": "Old expired GitHub artifacts may be skipped and absent from the warehouse.",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warehouse",
        default=os.getenv("STOCKPREDICTION_WAREHOUSE", str(DEFAULT_WAREHOUSE)),
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Defaults to warehouse/drive_pack.",
    )
    parser.add_argument(
        "--min-shape-rows",
        type=int,
        default=100,
        help="Minimum rows before probability-shape alerts are treated as stable.",
    )
    return parser.parse_args()


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def load_summaries(warehouse: Path) -> dict[str, pd.DataFrame]:
    return {name: read_csv(warehouse / relative) for name, relative in SUMMARY_FILES.items()}


def numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    output = frame.copy()
    for column in columns:
        if column in output.columns:
            output[column] = pd.to_numeric(output[column], errors="coerce")
    return output


def bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return frame[column].astype(str).str.lower().isin({"true", "1", "yes"})


def first_valid_datetime(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    values = pd.Series(pd.NaT, index=frame.index)
    for column in columns:
        if column in frame.columns:
            parsed = pd.to_datetime(frame[column], errors="coerce", utc=True).dt.tz_convert(None)
            values = values.where(values.notna(), parsed)
    return values


def latest_inventory_row(inventory: pd.DataFrame) -> dict[str, object]:
    if inventory.empty:
        return {}
    frame = inventory.copy()
    frame["_context_sort"] = first_valid_datetime(
        frame, ["model_as_of_date", "latest_market_date", "warehouse_exported_at"]
    )
    frame["_export_sort"] = first_valid_datetime(frame, ["warehouse_exported_at"])
    if "bytes_copied" in frame.columns:
        frame["_bytes_sort"] = pd.to_numeric(frame["bytes_copied"], errors="coerce").fillna(0)
    else:
        frame["_bytes_sort"] = 0
    row = frame.sort_values(["_context_sort", "_bytes_sort", "_export_sort"]).iloc[-1]
    return {key: (None if pd.isna(value) else value) for key, value in row.to_dict().items()}


def latest_probability_shape(signal_shape: pd.DataFrame, min_rows: int) -> pd.DataFrame:
    if signal_shape.empty:
        return pd.DataFrame()
    frame = numeric(
        signal_shape,
        [
            "horizon_days",
            "rows",
            "avg_probability_up",
            "median_probability_up",
            "high_confidence_rows",
            "extreme_rows",
            "high_confidence_share",
            "extreme_share",
        ],
    )
    if "as_of_date" in frame.columns:
        frame["_as_of"] = pd.to_datetime(frame["as_of_date"], errors="coerce", utc=True)
        latest_date = frame["_as_of"].max()
        if pd.notna(latest_date):
            frame = frame[frame["_as_of"].eq(latest_date)].copy()
    if "rows" in frame.columns:
        stable = frame[frame["rows"].ge(min_rows)].copy()
        if not stable.empty:
            frame = stable
    return frame.drop(columns=[column for column in ["_as_of"] if column in frame.columns])


def compact_champion_gates(quality_gates: pd.DataFrame) -> pd.DataFrame:
    if quality_gates.empty:
        return pd.DataFrame()
    frame = numeric(
        quality_gates,
        [
            "horizon_days",
            "roc_auc",
            "brier_skill",
            "selected_return_edge",
            "walk_forward_avg_score",
            "test_rows",
        ],
    )
    champion_mask = bool_series(frame, "is_champion_gate")
    if champion_mask.any():
        frame = frame[champion_mask].copy()
    columns = [
        "horizon_days",
        "model_name",
        "trust_tier",
        "auc_gate",
        "brier_gate",
        "return_edge_gate",
        "walk_forward_gate",
        "sample_gate",
        "roc_auc",
        "brier_skill",
        "selected_return_edge",
        "walk_forward_avg_score",
        "test_rows",
    ]
    output = frame[[column for column in columns if column in frame.columns]]
    sort_columns = [column for column in ["horizon_days", "model_name"] if column in output.columns]
    return output.sort_values(sort_columns) if sort_columns else output


def finite_number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(number):
        return default
    return number


def build_key_metrics(
    summaries: dict[str, pd.DataFrame],
    latest_shape: pd.DataFrame,
    min_shape_rows: int,
) -> pd.DataFrame:
    inventory = summaries["run_inventory"]
    latest = latest_inventory_row(inventory)
    quality = summaries["model_quality_gates"]
    leakage = summaries["leakage_audit_summary"]
    outcomes = numeric(summaries["paper_outcome_summary"], ["evaluated"])

    champion_gates = compact_champion_gates(quality)
    paper_ready_count = 0
    if not champion_gates.empty and "trust_tier" in champion_gates.columns:
        paper_ready_count = int(champion_gates["trust_tier"].eq("paper_review").sum())

    leakage_issue_count = 0
    if not leakage.empty and "leakage_audit_status" in leakage.columns:
        leakage_issue_count = int(leakage["leakage_audit_status"].ne("ok").sum())

    buy_evaluated = 0
    if not outcomes.empty and "action" in outcomes.columns:
        buy_rows = outcomes[outcomes["action"].astype(str).eq("paper buy candidate")]
        buy_evaluated = int(buy_rows["evaluated"].sum())

    high_confidence_max = None
    extreme_max = None
    if not latest_shape.empty:
        shaped = numeric(latest_shape, ["high_confidence_share", "extreme_share"])
        high_confidence_max = shaped["high_confidence_share"].max()
        extreme_max = shaped["extreme_share"].max()

    return pd.DataFrame(
        [
            {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "latest_run_id": latest.get("run_id", ""),
                "latest_market_date": latest.get("latest_market_date", ""),
                "run_archives": len(inventory) if not inventory.empty else 0,
                "latest_archive_megabytes": (
                    float(latest.get("bytes_copied", 0) or 0) / 1_000_000 if latest else None
                ),
                "paper_ready_champions": paper_ready_count,
                "leakage_issue_rows": leakage_issue_count,
                "paper_buy_evaluated": buy_evaluated,
                "latest_max_high_confidence_share": high_confidence_max,
                "latest_max_extreme_share": extreme_max,
                "probability_shape_min_rows": min_shape_rows,
            }
        ]
    )


def finding(priority: str, area: str, finding_text: str, evidence: str, next_step: str) -> dict[str, str]:
    return {
        "priority": priority,
        "area": area,
        "finding": finding_text,
        "evidence": evidence,
        "next_step": next_step,
    }


def build_findings(
    summaries: dict[str, pd.DataFrame],
    latest_shape: pd.DataFrame,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    champion_gates = compact_champion_gates(summaries["model_quality_gates"])
    if champion_gates.empty:
        findings.append(
            finding(
                "P0",
                "model_trust",
                "No champion gate data is available yet.",
                "model_quality_gates.csv is missing or empty.",
                "Run the OSL workflow after a successful GitHub Actions artifact is available.",
            )
        )
    else:
        paper_ready = (
            champion_gates["trust_tier"].eq("paper_review")
            if "trust_tier" in champion_gates.columns
            else pd.Series(False, index=champion_gates.index)
        )
        if not paper_ready.any():
            worst_brier = finite_number(
                pd.to_numeric(champion_gates.get("brier_skill"), errors="coerce").min()
            )
            best_auc = finite_number(
                pd.to_numeric(champion_gates.get("roc_auc"), errors="coerce").max()
            )
            findings.append(
                finding(
                    "P0",
                    "model_trust",
                    "Champions are still research-only or watch-tier.",
                    f"Best champion ROC AUC is {best_auc:.4f}; worst champion Brier skill is {worst_brier:.4f}.",
                    "Improve calibration and walk-forward stability before trusting high-probability ranks.",
                )
            )

    leakage = summaries["leakage_audit_summary"]
    if not leakage.empty and "leakage_audit_status" in leakage.columns:
        issues = leakage[leakage["leakage_audit_status"].ne("ok")]
        if issues.empty:
            findings.append(
                finding(
                    "P1",
                    "leakage_audit",
                    "Compact leakage checks are passing.",
                    "Train/test order, embargo, walk-forward, and metric-spike checks are ok.",
                    "Add feature availability timestamps next, because compact checks do not prove every feature is timestamp-safe.",
                )
            )
        else:
            findings.append(
                finding(
                    "P0",
                    "leakage_audit",
                    "At least one leakage audit row needs review.",
                    f"{len(issues)} model rows are not marked ok.",
                    "Inspect leakage_audit_summary.csv before promoting any model.",
                )
            )

    outcomes = numeric(summaries["paper_outcome_summary"], ["evaluated", "avg_return", "win_rate"])
    if not outcomes.empty and "action" in outcomes.columns:
        buy_rows = outcomes[outcomes["action"].astype(str).eq("paper buy candidate")]
        buy_count = int(buy_rows["evaluated"].sum()) if not buy_rows.empty else 0
        if buy_count < 30:
            findings.append(
                finding(
                    "P1",
                    "paper_outcomes",
                    "Paper buy candidates do not have enough matured outcomes yet.",
                    f"Only {buy_count} paper buy candidate outcomes are evaluated.",
                    "Keep paper mode running and compare candidates against watch and avoid baselines.",
                )
            )

    if not latest_shape.empty:
        shaped = numeric(latest_shape, ["rows", "high_confidence_share", "extreme_share"])
        high_row = shaped.sort_values("high_confidence_share", ascending=False).iloc[0]
        extreme_row = shaped.sort_values("extreme_share", ascending=False).iloc[0]
        high_share = float(high_row.get("high_confidence_share", 0) or 0)
        extreme_share = float(extreme_row.get("extreme_share", 0) or 0)
        if extreme_share > 0.05:
            findings.append(
                finding(
                    "P2",
                    "probability_shape",
                    "A meaningful latest model slice has too many extreme probabilities.",
                    f"Max latest extreme share is {extreme_share:.2%}.",
                    "Review probability buckets against observed outcomes and consider calibration.",
                )
            )
        elif high_share > 0.10:
            findings.append(
                finding(
                    "P2",
                    "probability_shape",
                    "Some latest model outputs are high-confidence enough to require calibration checks.",
                    f"Max latest >=70% probability share is {high_share:.2%}.",
                    "Use calibration curves before treating high-confidence ranks as stronger signals.",
                )
            )

    if not findings:
        findings.append(
            finding(
                "P2",
                "monitoring",
                "No immediate compact-analysis blocker was detected.",
                "Available summary files passed the current lightweight checks.",
                "Keep syncing runs and review the chart inputs after each pipeline run.",
            )
        )
    return findings


def markdown_table(frame: pd.DataFrame, columns: list[str], limit: int = 10) -> str:
    if frame.empty:
        return "_No rows available._"
    available = [column for column in columns if column in frame.columns]
    if not available:
        return "_No requested columns available._"
    output = frame[available].head(limit).copy()
    headers = [column.replace("_", " ").title() for column in output.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for record in output.to_dict("records"):
        row = []
        for column in output.columns:
            value = record.get(column, "")
            if pd.isna(value):
                row.append("")
            elif isinstance(value, float):
                row.append(f"{value:.4g}")
            else:
                row.append(str(value))
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")
    return "\n".join(lines)


def build_markdown(
    key_metrics: pd.DataFrame,
    findings: list[dict[str, str]],
    champion_gates: pd.DataFrame,
    latest_shape: pd.DataFrame,
    priorities: pd.DataFrame,
) -> str:
    latest = key_metrics.iloc[0].to_dict() if not key_metrics.empty else {}
    latest_archive_mb = finite_number(latest.get("latest_archive_megabytes", 0))
    finding_frame = pd.DataFrame(findings)
    lines = [
        "# Stockprediction2025 OSL Analysis Digest",
        "",
        f"Generated: `{latest.get('generated_at', '')}`",
        "",
        "## Latest Context",
        "",
        f"- Latest run id: `{latest.get('latest_run_id', '')}`",
        f"- Latest market date: `{latest.get('latest_market_date', '')}`",
        f"- Run archives in OSL: `{latest.get('run_archives', '')}`",
        f"- Latest archive stored in OSL: `{latest_archive_mb:.1f} MB`",
        "",
        "## Automated Findings",
        "",
        markdown_table(finding_frame, ["priority", "area", "finding", "evidence", "next_step"], limit=12),
        "",
        "## Champion Gates",
        "",
        markdown_table(
            champion_gates,
            [
                "horizon_days",
                "model_name",
                "trust_tier",
                "auc_gate",
                "brier_gate",
                "return_edge_gate",
                "walk_forward_gate",
                "roc_auc",
                "brier_skill",
                "selected_return_edge",
            ],
            limit=12,
        ),
        "",
        "## Latest Probability Shape",
        "",
        markdown_table(
            latest_shape,
            [
                "horizon_days",
                "model_name",
                "rows",
                "avg_probability_up",
                "median_probability_up",
                "high_confidence_share",
                "extreme_share",
            ],
            limit=20,
        ),
        "",
        "## Priority Queue",
        "",
        markdown_table(priorities, ["priority", "area", "evidence", "next_step"], limit=10),
        "",
        "## Chart Inputs",
        "",
        markdown_table(pd.DataFrame(CHART_MANIFEST), ["chart_name", "source_csv", "purpose", "caution"], limit=10),
        "",
        "_Research and paper-decision review only. No live trading recommendation._",
        "",
    ]
    return "\n".join(lines)


def copy_if_exists(source: Path, destination: Path) -> bool:
    if not source.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def write_pack(args: argparse.Namespace) -> Path:
    warehouse = resolve_path(args.warehouse)
    output_dir = resolve_path(args.output_dir) if args.output_dir else warehouse / "drive_pack"
    csv_dir = output_dir / "csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_dir.mkdir(parents=True, exist_ok=True)

    summaries = load_summaries(warehouse)
    latest_shape = latest_probability_shape(summaries["prediction_signal_shape"], args.min_shape_rows)
    champion_gates = compact_champion_gates(summaries["model_quality_gates"])
    key_metrics = build_key_metrics(summaries, latest_shape, args.min_shape_rows)
    findings = build_findings(summaries, latest_shape)
    priorities = summaries["analysis_priorities"]

    key_metrics.to_csv(csv_dir / "key_metrics.csv", index=False)
    latest_shape.to_csv(csv_dir / "latest_probability_signal_shape.csv", index=False)
    champion_gates.to_csv(csv_dir / "champion_model_gates.csv", index=False)
    pd.DataFrame(CHART_MANIFEST).to_csv(csv_dir / "recommended_charts.csv", index=False)
    pd.DataFrame(findings).to_csv(csv_dir / "automated_findings.csv", index=False)

    for name, relative in SUMMARY_FILES.items():
        copy_if_exists(warehouse / relative, csv_dir / f"{name}.csv")

    markdown = build_markdown(key_metrics, findings, champion_gates, latest_shape, priorities)
    (output_dir / "analysis_digest.md").write_text(markdown, encoding="utf-8")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "key_metrics": key_metrics.to_dict("records"),
        "findings": findings,
        "chart_manifest": CHART_MANIFEST,
        "source_files": SUMMARY_FILES,
    }
    (output_dir / "analysis_digest.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        "\n".join(
            [
                "# Stockprediction2025 Drive Pack",
                "",
                "This folder is generated in Open Science Lab from compact warehouse summaries.",
                "Large run archives stay in Open Science Lab unless the workflow is run with",
                "`--include-run-archives`.",
                "",
                "Start with `analysis_digest.md`, then use `csv/recommended_charts.csv`",
                "to build the small monitoring charts.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Wrote Drive analysis pack: {output_dir}")
    return output_dir


def main() -> int:
    args = parse_args()
    write_pack(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
