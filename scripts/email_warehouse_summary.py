"""Create a Gmail-ready report from Open Science Lab warehouse summaries.

By default this writes Markdown, HTML, and .eml files under
warehouse/summaries/email. Add --send with Gmail SMTP credentials in environment
variables to send the report from Open Science Lab.
"""

from __future__ import annotations

import argparse
import html
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = ROOT / "warehouse"


SUMMARY_FILES = {
    "inventory": "summaries/daily/run_inventory.csv",
    "model_history": "summaries/daily/model_score_history.csv",
    "model_by_model": "summaries/daily/model_score_by_model.csv",
    "prediction_recurrence": "summaries/daily/prediction_recurrence.csv",
    "monte_carlo": "summaries/daily/monte_carlo_stock_type_summary.csv",
    "feature_groups": "summaries/daily/feature_group_stability.csv",
    "paper_outcomes": "summaries/daily/paper_outcome_summary.csv",
    "weekly_scores": "summaries/weekly/model_score_weekly.csv",
    "artifact_health": "summaries/analysis/artifact_health.csv",
    "quality_gates": "summaries/analysis/model_quality_gates.csv",
    "quality_gate_summary": "summaries/analysis/model_quality_gate_summary.csv",
    "prediction_shape": "summaries/analysis/prediction_signal_shape.csv",
    "calibration_proxy": "summaries/analysis/paper_decision_calibration_proxy.csv",
    "leakage_audit": "summaries/analysis/leakage_audit_summary.csv",
    "analysis_priorities": "summaries/analysis/analysis_priorities.csv",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warehouse",
        default=os.getenv("STOCKPREDICTION_WAREHOUSE", str(DEFAULT_WAREHOUSE)),
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Defaults to warehouse/summaries/email.",
    )
    parser.add_argument(
        "--subject",
        default="Stockprediction2025 warehouse summary",
    )
    parser.add_argument("--to", default=os.getenv("GMAIL_TO", ""))
    parser.add_argument(
        "--from-email",
        default=os.getenv("GMAIL_SMTP_USER", os.getenv("GMAIL_USER", "")),
    )
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--smtp-host", default=os.getenv("GMAIL_SMTP_HOST", "smtp.gmail.com"))
    parser.add_argument("--smtp-port", type=int, default=int(os.getenv("GMAIL_SMTP_PORT", "587")))
    parser.add_argument(
        "--smtp-user",
        default=os.getenv("GMAIL_SMTP_USER", os.getenv("GMAIL_USER", "")),
    )
    parser.add_argument(
        "--smtp-password",
        default=os.getenv("GMAIL_APP_PASSWORD", os.getenv("GMAIL_SMTP_PASSWORD", "")),
    )
    parser.add_argument(
        "--attach-summaries",
        action="store_true",
        help="Attach small summary CSVs. The default keeps the email inline only.",
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


def to_numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    output = frame.copy()
    for column in columns:
        if column in output.columns:
            output[column] = pd.to_numeric(output[column], errors="coerce")
    return output


def percent(value: object, digits: int = 1) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if pd.isna(number):
        return "--"
    return f"{number * 100:.{digits}f}%"


def signed_percent(value: object, digits: int = 1) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if pd.isna(number):
        return "--"
    sign = "+" if number >= 0 else "-"
    return f"{sign}{abs(number) * 100:.{digits}f}%"


def number(value: object) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "--"


def coalesced_datetime(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    values = pd.Series(pd.NaT, index=frame.index)
    for column in columns:
        if column in frame.columns:
            parsed = pd.to_datetime(frame[column], errors="coerce", utc=True).dt.tz_convert(None)
            values = values.where(values.notna(), parsed)
    return values


def latest_inventory(inventory: pd.DataFrame) -> dict[str, str]:
    if inventory.empty:
        return {}
    frame = inventory.copy()
    frame["_context_sort"] = coalesced_datetime(
        frame, ["model_as_of_date", "latest_market_date", "warehouse_exported_at"]
    )
    frame["_export_sort"] = coalesced_datetime(frame, ["warehouse_exported_at"])
    if "bytes_copied" in frame.columns:
        frame["_bytes_sort"] = pd.to_numeric(frame["bytes_copied"], errors="coerce").fillna(0)
    else:
        frame["_bytes_sort"] = 0
    frame = frame.sort_values(["_context_sort", "_bytes_sort", "_export_sort"])
    row = frame.iloc[-1].to_dict()
    return {key: "" if pd.isna(value) else str(value) for key, value in row.items()}


def model_trust_call(model_history: pd.DataFrame) -> tuple[str, list[str]]:
    if model_history.empty:
        return "Waiting for model score history.", ["No model score summary is available yet."]
    frame = to_numeric(
        model_history,
        ["roc_auc", "brier_skill", "accuracy_lift", "walk_forward_avg_auc", "champion_score"],
    )
    champions = frame
    if "is_champion" in frame.columns:
        mask = frame["is_champion"].astype(str).str.lower().isin({"true", "1", "yes"})
        if mask.any():
            champions = frame[mask]
    avg_auc = champions["roc_auc"].mean() if "roc_auc" in champions.columns else float("nan")
    avg_brier = champions["brier_skill"].mean() if "brier_skill" in champions.columns else float("nan")
    avg_lift = champions["accuracy_lift"].mean() if "accuracy_lift" in champions.columns else float("nan")
    warnings = []
    if pd.isna(avg_auc) or avg_auc < 0.52:
        warnings.append("Champion ROC AUC is still near random, so keep predictions research-only.")
    if not pd.isna(avg_brier) and avg_brier < 0:
        warnings.append("Brier skill is negative, which means calibration is worse than a simple baseline.")
    if not pd.isna(avg_lift) and avg_lift < 0:
        warnings.append("Accuracy lift is negative versus the majority baseline on at least part of the evidence.")
    if warnings:
        return "Research only", warnings
    return "Cautious paper review", ["Model metrics cleared the basic gates, but paper outcomes still control trust."]


def markdown_table(frame: pd.DataFrame, columns: list[str], limit: int = 8) -> str:
    if frame.empty:
        return "_No rows available._"
    available = [column for column in columns if column in frame.columns]
    if not available:
        return "_No requested columns available._"
    output = frame[available].head(limit).copy()
    headers = [column.replace("_", " ").title() for column in output.columns]
    rows = []
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
        rows.append(row)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cleaned = [cell.replace("|", "\\|").replace("\n", " ") for cell in row]
        lines.append("| " + " | ".join(cleaned) + " |")
    return "\n".join(lines)


def build_markdown(summaries: dict[str, pd.DataFrame]) -> str:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inventory = summaries["inventory"]
    latest = latest_inventory(inventory)
    model_history = summaries["model_history"]
    model_by_model = to_numeric(
        summaries["model_by_model"],
        ["horizon_days", "champion_runs", "avg_score", "avg_roc_auc", "avg_brier_skill"],
    )
    prediction_recurrence = to_numeric(
        summaries["prediction_recurrence"],
        ["horizon_days", "appearances", "avg_rank", "avg_probability_up"],
    )
    monte_carlo = to_numeric(
        summaries["monte_carlo"],
        [
            "horizon_days",
            "avg_median_return",
            "avg_drawdown_probability",
            "avg_target_probability",
        ],
    )
    feature_groups = to_numeric(
        summaries["feature_groups"],
        ["horizon_days", "avg_importance_delta", "std_importance_delta", "avg_sample_rows"],
    )
    paper_outcomes = to_numeric(
        summaries["paper_outcomes"],
        ["evaluation_horizon_days", "evaluated", "avg_return", "median_return", "win_rate"],
    )
    quality_gates = to_numeric(
        summaries["quality_gates"],
        [
            "horizon_days",
            "roc_auc",
            "brier_skill",
            "selected_return_edge",
            "walk_forward_avg_score",
            "test_rows",
        ],
    )
    calibration_proxy = to_numeric(
        summaries["calibration_proxy"],
        [
            "horizon_days",
            "evaluation_horizon_days",
            "evaluated",
            "avg_probability_up",
            "observed_win_rate",
            "calibration_gap",
            "avg_return",
        ],
    )
    leakage_audit = summaries["leakage_audit"]
    analysis_priorities = summaries["analysis_priorities"]

    trust, warnings = model_trust_call(model_history)

    if not model_by_model.empty:
        model_by_model = model_by_model.sort_values(
            ["horizon_days", "champion_runs", "avg_score"],
            ascending=[True, False, False],
        )
    if not prediction_recurrence.empty:
        prediction_recurrence = prediction_recurrence.sort_values(
            ["appearances", "avg_probability_up"], ascending=[False, False]
        )
    if not monte_carlo.empty:
        monte_carlo = monte_carlo.sort_values(
            ["horizon_days", "avg_median_return"], ascending=[True, False]
        )
    if not feature_groups.empty:
        feature_groups = feature_groups.sort_values(
            ["horizon_days", "stock_type", "avg_importance_delta"],
            ascending=[True, True, False],
        )
    if not paper_outcomes.empty:
        paper_outcomes = paper_outcomes.sort_values(
            ["evaluation_horizon_days", "avg_return"], ascending=[True, False]
        )
    if not quality_gates.empty:
        champion_mask = (
            quality_gates["is_champion_gate"].astype(str).str.lower().isin({"true", "1"})
            if "is_champion_gate" in quality_gates.columns
            else pd.Series(False, index=quality_gates.index)
        )
        quality_gates = quality_gates[champion_mask].sort_values(["horizon_days", "model_name"])
    if not calibration_proxy.empty:
        matching_mask = (
            calibration_proxy["matching_horizon"].astype(str).str.lower().isin({"true", "1"})
            if "matching_horizon" in calibration_proxy.columns
            else pd.Series(False, index=calibration_proxy.index)
        )
        calibration_proxy = calibration_proxy[matching_mask].sort_values(
            ["evaluation_horizon_days", "evaluated"], ascending=[True, False]
        )
    leakage_issues = pd.DataFrame()
    if not leakage_audit.empty and "leakage_audit_status" in leakage_audit.columns:
        leakage_issues = leakage_audit[~leakage_audit["leakage_audit_status"].eq("ok")]

    lines = [
        "# Stockprediction2025 Warehouse Summary",
        "",
        f"Generated: `{generated_at}`",
        "",
        "## Latest Run",
        "",
        f"- Run id: `{latest.get('run_id', 'unknown')}`",
        f"- GitHub run: `{latest.get('github_run_id', '')}`",
        f"- Market date: `{latest.get('latest_market_date', '')}`",
        f"- Files copied: `{latest.get('files_copied', '')}`",
        f"- Bytes copied: `{latest.get('bytes_copied', '')}`",
        f"- Run archives in warehouse: `{len(inventory) if not inventory.empty else 0}`",
        "",
        "## Trust Call",
        "",
        f"**{trust}**",
        "",
    ]
    lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(
        [
            "",
            "## Analysis Priorities",
            "",
            markdown_table(
                analysis_priorities,
                ["priority", "area", "evidence", "next_step"],
                limit=6,
            ),
            "",
            "## Model Quality Gates",
            "",
            markdown_table(
                quality_gates,
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
                limit=9,
            ),
            "",
            "## Model Score By Model",
            "",
            markdown_table(
                model_by_model,
                [
                    "horizon_days",
                    "model_name",
                    "champion_runs",
                    "avg_score",
                    "avg_roc_auc",
                    "avg_brier_skill",
                    "avg_walk_forward_score",
                ],
            ),
            "",
            "## Paper Calibration Proxy",
            "",
            markdown_table(
                calibration_proxy,
                [
                    "action",
                    "horizon_days",
                    "evaluation_horizon_days",
                    "probability_bucket",
                    "evaluated",
                    "avg_probability_up",
                    "observed_win_rate",
                    "calibration_gap",
                    "avg_return",
                ],
                limit=10,
            ),
            "",
            "## Leakage Audit",
            "",
            (
                "All compact leakage audit rows are `ok`."
                if leakage_issues.empty
                else markdown_table(
                    leakage_issues,
                    [
                        "run_id",
                        "horizon_days",
                        "model_name",
                        "training_end",
                        "test_start",
                        "embargo_dates",
                        "leakage_audit_status",
                    ],
                    limit=10,
                )
            ),
            "",
            "## Recurring Model Picks",
            "",
            markdown_table(
                prediction_recurrence,
                [
                    "horizon_days",
                    "ticker",
                    "appearances",
                    "avg_rank",
                    "avg_probability_up",
                    "latest_seen",
                ],
            ),
            "",
            "## Monte Carlo Stock-Type Summary",
            "",
            markdown_table(
                monte_carlo,
                [
                    "horizon_days",
                    "stock_type",
                    "rows",
                    "avg_median_return",
                    "avg_drawdown_probability",
                    "avg_target_probability",
                ],
            ),
            "",
            "## Feature Group Stability",
            "",
            markdown_table(
                feature_groups,
                [
                    "horizon_days",
                    "stock_type",
                    "feature_group",
                    "runs",
                    "avg_importance_delta",
                    "std_importance_delta",
                ],
            ),
            "",
            "## Paper Outcome Summary",
            "",
            markdown_table(
                paper_outcomes,
                [
                    "action",
                    "evaluation_horizon_days",
                    "evaluated",
                    "avg_return",
                    "median_return",
                    "win_rate",
                ],
            ),
            "",
            "## Next Analysis Work",
            "",
            "- Export true row-level holdout predictions so calibration curves are not only paper-decision proxies.",
            "- Add leakage-audit summaries for feature availability dates, train/test windows, and embargo gaps.",
            "- Compare paper outcomes against market, sector, and same-rank watchlist baselines.",
            "- Keep large artifacts inside Open Science Lab; push only scripts, docs, and compact summary logic to GitHub.",
            "",
            "_Research and paper-decision review only. No live trading recommendation._",
            "",
        ]
    )
    return "\n".join(lines)


def simple_markdown_to_html(markdown: str) -> str:
    rows = []
    in_table = False
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            rows.append("</ul>")
            in_list = False

    def close_table() -> None:
        nonlocal in_table
        if in_table:
            rows.append("</pre>")
            in_table = False

    for line in markdown.splitlines():
        if line.startswith("# "):
            close_list()
            close_table()
            rows.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            close_list()
            close_table()
            rows.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            close_table()
            if not in_list:
                rows.append("<ul>")
                in_list = True
            rows.append(f"<li>{html.escape(line[2:])}</li>")
        elif line.startswith("|"):
            close_list()
            if not in_table:
                rows.append("<pre>")
                in_table = True
            rows.append(html.escape(line))
        else:
            close_list()
            close_table()
            if line.strip():
                rows.append(f"<p>{html.escape(line)}</p>")
            else:
                rows.append("")
    close_list()
    close_table()
    body = "\n".join(rows)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: Arial, sans-serif; color: #171717; line-height: 1.45; }}
    h1, h2 {{ margin-bottom: 0.35rem; }}
    h2 {{ margin-top: 1.2rem; border-top: 1px solid #ddd; padding-top: 0.9rem; }}
    pre {{ background: #f6f6f6; padding: 12px; overflow-x: auto; border-radius: 6px; }}
    ul {{ margin-top: 0.2rem; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def build_message(args: argparse.Namespace, markdown: str, html_body: str) -> EmailMessage:
    sender = args.from_email or args.smtp_user
    message = EmailMessage()
    message["Subject"] = args.subject
    message["From"] = sender
    message["To"] = args.to
    message.set_content(markdown)
    message.add_alternative(html_body, subtype="html")
    return message


def attach_summary_csvs(message: EmailMessage, warehouse: Path) -> None:
    for relative in SUMMARY_FILES.values():
        path = warehouse / relative
        if not path.exists() or path.stat().st_size > 2_000_000:
            continue
        message.add_attachment(
            path.read_bytes(),
            maintype="text",
            subtype="csv",
            filename=path.name,
        )


def send_message(args: argparse.Namespace, message: EmailMessage) -> None:
    if not args.to:
        raise RuntimeError("Missing recipient. Pass --to or set GMAIL_TO.")
    if not args.smtp_user or not args.smtp_password:
        raise RuntimeError(
            "Missing Gmail SMTP credentials. Set GMAIL_SMTP_USER and GMAIL_APP_PASSWORD."
        )
    with smtplib.SMTP(args.smtp_host, args.smtp_port) as server:
        server.starttls()
        server.login(args.smtp_user, args.smtp_password)
        server.send_message(message)


def main() -> int:
    args = parse_args()
    warehouse = resolve_path(args.warehouse)
    output_dir = resolve_path(args.output_dir) if args.output_dir else warehouse / "summaries" / "email"
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries = load_summaries(warehouse)
    markdown = build_markdown(summaries)
    html_body = simple_markdown_to_html(markdown)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    markdown_path = output_dir / f"warehouse_email_summary_{stamp}.md"
    html_path = output_dir / f"warehouse_email_summary_{stamp}.html"
    eml_path = output_dir / f"warehouse_email_summary_{stamp}.eml"
    markdown_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(html_body, encoding="utf-8")

    message = build_message(args, markdown, html_body)
    if args.attach_summaries:
        attach_summary_csvs(message, warehouse)
    eml_path.write_bytes(bytes(message))
    print(f"Wrote {markdown_path}")
    print(f"Wrote {html_path}")
    print(f"Wrote {eml_path}")

    if args.send:
        send_message(args, message)
        print(f"Sent Gmail summary to {args.to}")
    else:
        print("Not sent. Add --send with Gmail SMTP environment variables to send.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
