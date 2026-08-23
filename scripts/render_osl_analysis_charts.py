"""Render compact Open Science Lab diagnostics as small Drive-ready PNG charts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = ROOT / "warehouse"

INK = "#17191d"
MUTED = "#68717b"
GRID = "#d9dde2"
BLUE = "#2e6fbb"
BLUE_LIGHT = "#dce9f8"
GOLD = "#d49a22"
GOLD_LIGHT = "#f3e6bd"
PINK = "#c4587a"
OLIVE = "#6f7d38"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warehouse",
        default=os.getenv("STOCKPREDICTION_WAREHOUSE", str(DEFAULT_WAREHOUSE)),
    )
    parser.add_argument("--output-dir", default="", help="Defaults to warehouse/drive_pack/charts.")
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


def as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def latest_rows(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    for column in (
        "model_as_of_date",
        "as_of_date",
        "latest_market_date",
        "warehouse_exported_at",
    ):
        if column not in output.columns:
            continue
        parsed = pd.to_datetime(output[column], errors="coerce", utc=True)
        if parsed.notna().any():
            output = output[parsed.eq(parsed.max())].copy()
            break
    if "run_id" in output.columns and output["run_id"].nunique(dropna=True) > 1:
        run_ids = pd.to_numeric(output["run_id"], errors="coerce")
        if run_ids.notna().any():
            output = output[run_ids.eq(run_ids.max())].copy()
    return output


def style_axis(ax: plt.Axes) -> None:
    ax.set_facecolor("white")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.7, alpha=0.7)
    ax.set_axisbelow(True)


def save_figure(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.patch.set_facecolor("white")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def heatmap(
    values: np.ndarray,
    row_labels: list[str],
    column_labels: list[str],
    title: str,
    subtitle: str,
    path: Path,
) -> None:
    height = max(4.2, 1.1 + len(row_labels) * 0.45)
    fig, ax = plt.subplots(figsize=(10.5, height))
    ax.imshow(values, cmap=ListedColormap([GOLD_LIGHT, BLUE]), vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(np.arange(len(column_labels)), labels=column_labels)
    ax.set_yticks(np.arange(len(row_labels)), labels=row_labels)
    ax.tick_params(axis="x", top=True, bottom=False, labeltop=True, labelbottom=False, pad=8)
    ax.tick_params(axis="y", length=0)
    for row in range(values.shape[0]):
        for column in range(values.shape[1]):
            passed = bool(values[row, column])
            ax.text(
                column,
                row,
                "PASS" if passed else "FAIL",
                ha="center",
                va="center",
                color="white" if passed else INK,
                fontsize=8,
                fontweight="bold",
            )
    ax.set_xticks(np.arange(-0.5, len(column_labels), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(row_labels), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="minor", bottom=False, left=False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.suptitle(title, x=0.01, y=0.99, ha="left", color=INK, fontsize=15, fontweight="bold")
    fig.text(0.01, 0.955, subtitle, ha="left", va="top", color=MUTED, fontsize=9)
    fig.subplots_adjust(top=0.84)
    save_figure(fig, path)


def render_model_gate_matrix(csv_dir: Path, output: Path) -> str | None:
    frame = latest_rows(read_csv(csv_dir / "model_quality_gates.csv"))
    required = ["model_name", "horizon_days", "auc_gate", "brier_gate", "return_edge_gate", "walk_forward_gate"]
    if frame.empty or not set(required).issubset(frame.columns):
        return "Missing latest model quality-gate rows."
    frame = frame.sort_values(["horizon_days", "model_name"]).head(32)
    gates = ["auc_gate", "brier_gate", "return_edge_gate", "walk_forward_gate"]
    values = np.column_stack([as_bool(frame[gate]).astype(int).to_numpy() for gate in gates])
    labels = [f"{row.model_name} / {int(row.horizon_days)}d" for row in frame.itertuples()]
    heatmap(
        values,
        labels,
        ["ROC AUC", "Brier skill", "Return edge", "Walk-forward"],
        "Model quality gate matrix",
        "Latest run; blue passes and gold requires review",
        output,
    )
    return None


def render_calibration(csv_dir: Path, output: Path) -> str | None:
    frame = read_csv(csv_dir / "paper_decision_calibration_proxy.csv")
    required = {"probability_bucket", "avg_probability_up", "observed_win_rate", "evaluated"}
    if frame.empty or not required.issubset(frame.columns):
        return "Calibration proxy is unavailable."
    if "matching_horizon" in frame.columns:
        matching = frame[as_bool(frame["matching_horizon"])]
        if not matching.empty:
            frame = matching
    for column in ("avg_probability_up", "observed_win_rate", "evaluated"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["avg_probability_up", "observed_win_rate", "evaluated"])
    if frame["probability_bucket"].nunique() < 2 or frame["evaluated"].sum() < 10:
        return "Fewer than two useful probability buckets or ten evaluated outcomes."

    def weighted(group: pd.DataFrame, column: str) -> float:
        weights = group["evaluated"].clip(lower=0)
        return float(np.average(group[column], weights=weights)) if weights.sum() else float("nan")

    grouped = pd.DataFrame(
        [
            {
                "probability_bucket": bucket,
                "predicted": weighted(group, "avg_probability_up"),
                "observed": weighted(group, "observed_win_rate"),
                "evaluated": group["evaluated"].sum(),
            }
            for bucket, group in frame.groupby("probability_bucket", dropna=False)
        ]
    ).sort_values("predicted")
    fig, ax = plt.subplots(figsize=(8.4, 5.2))
    ax.plot([0, 1], [0, 1], color=INK, linewidth=1.2, linestyle="--", label="Ideal calibration")
    ax.plot(grouped["predicted"], grouped["observed"], color=BLUE, marker="o", linewidth=2, label="Paper outcomes")
    for row in grouped.itertuples():
        ax.annotate(f"n={int(row.evaluated)}", (row.predicted, row.observed), xytext=(4, 7), textcoords="offset points", fontsize=8, color=MUTED)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("Average predicted probability", color=MUTED)
    ax.set_ylabel("Observed paper win rate", color=MUTED)
    ax.set_title("Paper-decision calibration proxy\nMatching horizons; dashed line is ideal", loc="left", color=INK, fontsize=14, fontweight="bold")
    ax.legend(frameon=False, loc="lower right")
    style_axis(ax)
    save_figure(fig, output)
    return None


def render_probability_shape(csv_dir: Path, output: Path) -> str | None:
    frame = read_csv(csv_dir / "latest_probability_signal_shape.csv")
    required = {"model_name", "horizon_days", "rows", "high_confidence_share", "extreme_share"}
    if frame.empty or not required.issubset(frame.columns):
        return "Latest probability-shape rows are unavailable."
    for column in ("rows", "high_confidence_share", "extreme_share"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["high_confidence_share", "extreme_share"]).copy()
    frame = frame.sort_values("high_confidence_share", ascending=False).head(14)
    if frame.empty:
        return "No numeric probability-shape rows are available."
    labels = [f"{row.model_name} / {int(row.horizon_days)}d (n={int(row.rows)})" for row in frame.itertuples()]
    positions = np.arange(len(frame))
    fig, ax = plt.subplots(figsize=(10.5, max(4.5, len(frame) * 0.48)))
    ax.barh(positions - 0.18, frame["high_confidence_share"], height=0.34, color=BLUE, label=">=70% probability")
    ax.barh(positions + 0.18, frame["extreme_share"], height=0.34, color=GOLD, label=">=90% probability")
    ax.set_yticks(positions, labels=labels)
    ax.invert_yaxis()
    ax.set_xlim(left=0)
    ax.xaxis.set_major_formatter(lambda value, _: f"{value:.0%}")
    ax.set_xlabel("Share of prediction rows", color=MUTED)
    ax.set_title("Latest probability signal shape\nOnly meaningful latest slices; sample size shown in labels", loc="left", color=INK, fontsize=14, fontweight="bold")
    ax.legend(frameon=False, loc="lower right")
    style_axis(ax)
    save_figure(fig, output)
    return None


def render_paper_outcomes(csv_dir: Path, output: Path) -> str | None:
    frame = read_csv(csv_dir / "paper_outcome_summary.csv")
    required = {"action", "evaluation_horizon_days", "evaluated", "avg_return", "win_rate"}
    if frame.empty or not required.issubset(frame.columns):
        return "Paper outcomes are unavailable."
    for column in ("evaluation_horizon_days", "evaluated", "avg_return", "win_rate"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["avg_return", "win_rate"]).copy()
    if frame.empty:
        return "No matured paper outcomes are available."
    frame["label"] = frame.apply(lambda row: f"{row['action']} / {int(row['evaluation_horizon_days'])}d (n={int(row['evaluated'])})", axis=1)
    frame = frame.sort_values(["evaluation_horizon_days", "action"]).head(18)
    positions = np.arange(len(frame))
    fig, axes = plt.subplots(1, 2, figsize=(13, max(4.8, len(frame) * 0.42)), sharey=True)
    axes[0].barh(positions, frame["win_rate"], color=BLUE)
    axes[0].axvline(0.5, color=INK, linestyle="--", linewidth=1)
    axes[0].set_xlim(0, 1)
    axes[0].xaxis.set_major_formatter(lambda value, _: f"{value:.0%}")
    axes[0].set_title("Win rate", loc="left", fontweight="bold", color=INK)
    axes[1].barh(positions, frame["avg_return"], color=[BLUE if value >= 0 else GOLD for value in frame["avg_return"]])
    axes[1].axvline(0, color=INK, linewidth=1)
    axes[1].xaxis.set_major_formatter(lambda value, _: f"{value:.1%}")
    axes[1].set_title("Average return", loc="left", fontweight="bold", color=INK)
    axes[0].set_yticks(positions, labels=frame["label"])
    axes[0].invert_yaxis()
    for ax in axes:
        style_axis(ax)
    fig.suptitle("Matured paper outcomes", x=0.01, y=1.0, ha="left", color=INK, fontsize=15, fontweight="bold")
    save_figure(fig, output)
    return None


def render_leakage_audit(csv_dir: Path, output: Path) -> str | None:
    frame = latest_rows(read_csv(csv_dir / "leakage_audit_summary.csv"))
    checks = ["train_before_test", "embargo_matches_horizon", "has_walk_forward", "too_good_to_be_true_metric"]
    required = {"model_name", "horizon_days", *checks}
    if frame.empty or not required.issubset(frame.columns):
        return "Leakage-audit rows are unavailable."
    frame = frame.sort_values(["horizon_days", "model_name"]).head(32)
    values = np.column_stack(
        [
            as_bool(frame["train_before_test"]).astype(int),
            as_bool(frame["embargo_matches_horizon"]).astype(int),
            as_bool(frame["has_walk_forward"]).astype(int),
            (~as_bool(frame["too_good_to_be_true_metric"])).astype(int),
        ]
    )
    labels = [f"{row.model_name} / {int(row.horizon_days)}d" for row in frame.itertuples()]
    heatmap(
        values,
        labels,
        ["Train before test", "Embargo", "Walk-forward", "No metric spike"],
        "Compact leakage audit",
        "Latest run; this complements, but does not replace, feature timestamp checks",
        output,
    )
    return None


def render_artifact_health(csv_dir: Path, output: Path) -> str | None:
    frame = read_csv(csv_dir / "artifact_health.csv")
    required = {"run_id", "megabytes_copied", "analysis_ready"}
    if frame.empty or not required.issubset(frame.columns):
        return "Artifact-health rows are unavailable."
    frame["megabytes_copied"] = pd.to_numeric(frame["megabytes_copied"], errors="coerce").fillna(0)
    frame = frame.tail(12).copy()
    labels = [str(value) for value in frame["run_id"]]
    ready = as_bool(frame["analysis_ready"])
    colors = [BLUE if value else GOLD for value in ready]
    fig, ax = plt.subplots(figsize=(10.5, 5.2))
    ax.bar(np.arange(len(frame)), frame["megabytes_copied"], color=colors)
    ax.set_xticks(np.arange(len(frame)), labels=labels, rotation=35, ha="right")
    ax.set_ylabel("Archive size in OSL (MB)", color=MUTED)
    ax.set_title("Archived run health\nBlue is analysis-ready; gold is partial or compact-only", loc="left", color=INK, fontsize=14, fontweight="bold")
    style_axis(ax)
    save_figure(fig, output)
    return None


def render_feature_stability(csv_dir: Path, output: Path) -> str | None:
    frame = read_csv(csv_dir / "feature_group_stability.csv")
    required = {"feature_group", "avg_importance_delta", "std_importance_delta", "runs"}
    if frame.empty or not required.issubset(frame.columns):
        return "Feature-group stability rows are unavailable."
    for column in ("avg_importance_delta", "std_importance_delta", "runs"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    grouped = (
        frame.groupby("feature_group", dropna=False)
        .agg(
            avg_importance_delta=("avg_importance_delta", "mean"),
            std_importance_delta=("std_importance_delta", "mean"),
            runs=("runs", "max"),
        )
        .reset_index()
        .dropna(subset=["avg_importance_delta"])
    )
    grouped = grouped.sort_values("avg_importance_delta", ascending=False).head(12)
    if len(grouped) < 4:
        return "Fewer than four feature groups are available."
    positions = np.arange(len(grouped))
    fig, ax = plt.subplots(figsize=(9.5, max(4.5, len(grouped) * 0.46)))
    ax.barh(positions, grouped["avg_importance_delta"], xerr=grouped["std_importance_delta"].fillna(0), color=OLIVE, ecolor=INK, capsize=3)
    ax.set_yticks(positions, labels=grouped["feature_group"])
    ax.invert_yaxis()
    ax.axvline(0, color=INK, linewidth=1)
    ax.set_xlabel("Average permutation importance delta", color=MUTED)
    ax.set_title("Feature-group stability\nMean importance across available runs; error bars show run variation", loc="left", color=INK, fontsize=14, fontweight="bold")
    style_axis(ax)
    save_figure(fig, output)
    return None


def render_weekly_score(csv_dir: Path, output: Path) -> str | None:
    frame = read_csv(csv_dir / "model_score_weekly.csv")
    required = {"run_week", "horizon_days", "model_name", "avg_score"}
    if frame.empty or not required.issubset(frame.columns):
        return "Weekly model-score rows are unavailable."
    frame["avg_score"] = pd.to_numeric(frame["avg_score"], errors="coerce")
    if frame["run_week"].nunique() < 8:
        return "Fewer than eight weekly observations; a trend line would be misleading."
    frame = frame.dropna(subset=["avg_score"]).copy()
    fig, ax = plt.subplots(figsize=(10.5, 5.5))
    palette = [BLUE, GOLD, PINK, OLIVE]
    for index, ((horizon, model), group) in enumerate(frame.groupby(["horizon_days", "model_name"])):
        group = group.sort_values("run_week")
        ax.plot(group["run_week"], group["avg_score"], marker="o", linewidth=1.7, color=palette[index % len(palette)], label=f"{model} / {int(horizon)}d")
    ax.axhline(0, color=INK, linewidth=1)
    ax.tick_params(axis="x", rotation=35)
    ax.set_ylabel("Average champion score", color=MUTED)
    ax.set_title("Weekly model score\nRendered only after eight or more observed weeks", loc="left", color=INK, fontsize=14, fontweight="bold")
    ax.legend(frameon=False, fontsize=8, ncol=2)
    style_axis(ax)
    save_figure(fig, output)
    return None


def main() -> int:
    args = parse_args()
    warehouse = resolve_path(args.warehouse)
    csv_dir = warehouse / "drive_pack" / "csv"
    output_dir = resolve_path(args.output_dir) if args.output_dir else warehouse / "drive_pack" / "charts"
    output_dir.mkdir(parents=True, exist_ok=True)
    renderers = [
        ("model_gate_matrix", render_model_gate_matrix),
        ("calibration_proxy", render_calibration),
        ("probability_signal_shape", render_probability_shape),
        ("paper_outcomes", render_paper_outcomes),
        ("leakage_audit", render_leakage_audit),
        ("artifact_health", render_artifact_health),
        ("feature_group_stability", render_feature_stability),
        ("model_score_weekly", render_weekly_score),
    ]
    statuses = []
    for name, renderer in renderers:
        output = output_dir / f"{name}.png"
        if output.exists():
            output.unlink()
        reason = renderer(csv_dir, output)
        rendered = reason is None and output.exists()
        statuses.append(
            {
                "chart": name,
                "status": "rendered" if rendered else "skipped",
                "reason": "" if rendered else reason,
                "file": output.name if rendered else "",
                "bytes": output.stat().st_size if rendered else 0,
            }
        )
        print(f"{name}: {'rendered' if rendered else f'skipped - {reason}'}")
    (output_dir / "chart_status.json").write_text(
        json.dumps(statuses, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    total_bytes = sum(item["bytes"] for item in statuses)
    print(f"Rendered {sum(item['status'] == 'rendered' for item in statuses)} charts ({total_bytes / 1_000_000:.2f} MB).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
