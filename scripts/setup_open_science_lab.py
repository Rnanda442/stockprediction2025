import argparse
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = ROOT / "warehouse"
EXCLUDE_HEADER = "# stockprediction2025 local warehouse"

WAREHOUSE_DIRS = (
    "prices/raw",
    "prices/clean",
    "features/vectorized",
    "model_runs/runs",
    "model_runs/evaluations",
    "model_runs/predictions",
    "monte_carlo/latest",
    "monte_carlo/history",
    "paper_outcomes",
    "summaries/daily",
    "summaries/weekly",
    "manifests",
    "logs",
    "scratch",
)

EXPORT_PATTERNS = (
    "analytics/*.csv",
    "analytics/*.html",
    "dashboard_data.db",
    "vectorized.db",
    "historicals.db",
    "filtered_tickers.db",
    "checkpoint_filtered.csv",
    "checkpoint_rejected.csv",
    "vector_analysis_results.csv",
    "logs/*.ipynb",
    "logs/*.txt",
    "logs/*.log",
)

CATEGORY_COPIES = {
    "analytics/model_evaluation.csv": "model_runs/evaluations",
    "analytics/model_tournament_evaluation.csv": "model_runs/evaluations",
    "analytics/model_walk_forward_evaluation.csv": "model_runs/evaluations",
    "analytics/latest_model_predictions.csv": "model_runs/predictions",
    "analytics/latest_model_candidate_predictions.csv": "model_runs/predictions",
    "analytics/model_prediction_history_latest.csv": "model_runs/predictions",
    "analytics/latest_monte_carlo_simulations.csv": "monte_carlo/latest",
    "analytics/latest_monte_carlo_paths.csv": "monte_carlo/latest",
    "analytics/monte_carlo_simulation_history_latest.csv": "monte_carlo/history",
    "analytics/automatic_paper_decisions.csv": "paper_outcomes",
    "analytics/automatic_paper_decision_outcomes.csv": "paper_outcomes",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Manage the local-only Open Science Lab warehouse for stockprediction2025."
        )
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("setup", "export-run", "summarize", "status"),
        default="setup",
        help="Command to run. Defaults to setup for backward compatibility.",
    )
    parser.add_argument(
        "--warehouse",
        default=os.getenv("STOCKPREDICTION_WAREHOUSE", str(DEFAULT_WAREHOUSE)),
        help=(
            "Warehouse directory. Defaults to ./warehouse or "
            "STOCKPREDICTION_WAREHOUSE when set."
        ),
    )
    parser.add_argument(
        "--source",
        default=str(ROOT),
        help=(
            "Run output source for export-run. Use the repo root or a downloaded "
            "GitHub artifact folder such as stock-analysis-outputs."
        ),
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Optional run id override for export-run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without writing or copying files.",
    )
    return parser.parse_args()


def resolve_path(value, base=ROOT):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def relative_exclude_pattern(path):
    try:
        relative = path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return None
    return "/" + relative.as_posix().rstrip("/") + "/"


def update_local_git_exclude(warehouse, dry_run=False):
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        return []

    exclude_path = git_dir / "info" / "exclude"
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    additions = []
    pattern = relative_exclude_pattern(warehouse)
    if pattern and pattern not in existing:
        additions.append(pattern)
    default_pattern = "/warehouse/"
    if default_pattern not in existing and default_pattern not in additions:
        additions.append(default_pattern)
    if additions and not dry_run:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        exclude_path.write_text(
            existing + prefix + EXCLUDE_HEADER + "\n" + "\n".join(additions) + "\n",
            encoding="utf-8",
        )
    return additions


def create_manifest(warehouse):
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo_root": str(ROOT),
        "warehouse": str(warehouse),
        "purpose": "local large-data memory for stockprediction2025",
        "git_policy": "local-only; do not commit warehouse contents",
        "folders": list(WAREHOUSE_DIRS),
    }
    path = warehouse / "manifests" / "warehouse_manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def create_local_readme(warehouse):
    path = warehouse / "README.local.md"
    path.write_text(
        "\n".join(
            [
                "# Local Stock Prediction Warehouse",
                "",
                "This folder is for Open Science Lab large data and generated run memory.",
                "It is excluded locally from Git and should not be committed.",
                "",
                "Use these commands from the repo root:",
                "",
                "```bash",
                "python scripts/setup_open_science_lab.py export-run --source PATH",
                "python scripts/setup_open_science_lab.py summarize",
                "python scripts/setup_open_science_lab.py status",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def ensure_warehouse(warehouse, dry_run=False):
    planned_dirs = [warehouse / relative for relative in WAREHOUSE_DIRS]
    if dry_run:
        print(f"Repo root: {ROOT}")
        print(f"Warehouse: {warehouse}")
        for path in planned_dirs:
            print(f"would create: {path}")
        return warehouse

    for path in planned_dirs:
        path.mkdir(parents=True, exist_ok=True)
    readme_path = create_local_readme(warehouse)
    manifest_path = create_manifest(warehouse)
    exclude_additions = update_local_git_exclude(warehouse)

    print(f"Warehouse ready: {warehouse}")
    print(f"Local README: {readme_path}")
    print(f"Manifest: {manifest_path}")
    if exclude_additions:
        print("Added local Git exclude rules:")
        for item in exclude_additions:
            print(f"  {item}")
    else:
        print("Local Git exclude rules already covered this warehouse.")
    return warehouse


def safe_table_dict(db_path, table, key_column, value_column):
    if not db_path.exists():
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            return dict(conn.execute(f'SELECT "{key_column}", "{value_column}" FROM "{table}"'))
    except sqlite3.Error:
        return {}


def first_csv_row(path):
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path, nrows=1)
    except Exception:
        return {}
    if frame.empty:
        return {}
    return {
        column: "" if pd.isna(value) else str(value)
        for column, value in frame.iloc[0].to_dict().items()
    }


def source_metadata(source):
    health = safe_table_dict(source / "dashboard_data.db", "PipelineHealth", "metric", "value")
    model_summary = first_csv_row(source / "analytics" / "model_run_summary.csv")
    return {
        "github_run_id": health.get("github_run_id", ""),
        "github_run_url": health.get("github_run_url", ""),
        "latest_market_date": health.get("latest_market_date", ""),
        "dashboard_exported_at": health.get("exported_at", ""),
        "model_run_id": model_summary.get("run_id", ""),
        "model_created_at": model_summary.get("created_at", ""),
        "model_as_of_date": model_summary.get("as_of_date", ""),
        "model_candidates": model_summary.get("model_candidates", ""),
    }


def sanitize_run_id(value):
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(value))
    cleaned = cleaned.strip("-_")
    return cleaned or datetime.now(timezone.utc).strftime("manual-%Y%m%d%H%M%S")


def choose_run_id(args, metadata):
    return sanitize_run_id(
        args.run_id
        or metadata.get("github_run_id")
        or metadata.get("model_run_id")
        or datetime.now(timezone.utc).strftime("manual-%Y%m%d%H%M%S")
    )


def inside_path(path, parent):
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def copy_file(source, destination, dry_run=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = source.stat().st_size
    if not dry_run:
        shutil.copy2(source, destination)
    return {
        "source": str(source),
        "destination": str(destination),
        "bytes": size,
    }


def archive_destination(run_dir, source_root, path):
    relative = path.relative_to(source_root)
    if relative.parts and relative.parts[0] in {"analytics", "logs"}:
        return run_dir / relative
    if path.suffix.lower() == ".db":
        return run_dir / "databases" / path.name
    return run_dir / "root_outputs" / path.name


def export_run(args, warehouse):
    source = resolve_path(args.source)
    if not source.exists():
        raise RuntimeError(f"Source does not exist: {source}")
    metadata = source_metadata(source)
    run_id = choose_run_id(args, metadata)
    run_dir = warehouse / "model_runs" / "runs" / run_id
    copied = []
    seen = set()

    print(f"Export source: {source}")
    print(f"Run id: {run_id}")
    print(f"Run archive: {run_dir}")

    for pattern in EXPORT_PATTERNS:
        for path in source.glob(pattern):
            if not path.is_file() or inside_path(path, warehouse):
                continue
            destination = archive_destination(run_dir, source, path)
            key = str(destination.resolve())
            if key in seen:
                continue
            seen.add(key)
            copied.append(copy_file(path, destination, args.dry_run))

    for relative, target_folder in CATEGORY_COPIES.items():
        path = source / relative
        if not path.exists() or not path.is_file():
            continue
        destination = warehouse / target_folder / f"{run_id}_{path.name}"
        key = str(destination.resolve())
        if key in seen:
            continue
        seen.add(key)
        copied.append(copy_file(path, destination, args.dry_run))

    manifest = {
        "run_id": run_id,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(source),
        "run_archive": str(run_dir),
        "metadata": metadata,
        "files_copied": len(copied),
        "bytes_copied": int(sum(item["bytes"] for item in copied)),
        "files": copied,
    }
    if not copied:
        message = "No known output files found. Pass --source to a downloaded artifact folder."
        if args.dry_run:
            manifest["warning"] = message
        else:
            raise RuntimeError(message)
    if args.dry_run:
        print(json.dumps(manifest, indent=2))
    else:
        run_dir.mkdir(parents=True, exist_ok=True)
        run_manifest = run_dir / "manifest.json"
        manifest_copy = warehouse / "manifests" / f"run_{run_id}.json"
        run_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        manifest_copy.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Files copied: {manifest['files_copied']}")
        print(f"Bytes copied: {manifest['bytes_copied']:,}")
        print(f"Run manifest: {run_manifest}")
    return manifest


def read_run_manifests(warehouse):
    manifests = []
    for path in sorted((warehouse / "model_runs" / "runs").glob("*/manifest.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload["_manifest_path"] = str(path)
        manifests.append(payload)
    return manifests


def run_context(manifest):
    metadata = manifest.get("metadata", {})
    return {
        "run_id": manifest.get("run_id", ""),
        "warehouse_exported_at": manifest.get("exported_at", ""),
        "github_run_id": metadata.get("github_run_id", ""),
        "github_run_url": metadata.get("github_run_url", ""),
        "latest_market_date": metadata.get("latest_market_date", ""),
        "model_created_at": metadata.get("model_created_at", ""),
        "model_as_of_date": metadata.get("model_as_of_date", ""),
    }


def read_run_csv(manifest, relative_path):
    path = Path(manifest.get("run_archive", "")) / relative_path
    if not path.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
    context = run_context(manifest)
    for column, value in context.items():
        if column not in frame.columns:
            frame.insert(0, column, value)
    return frame


def write_csv(frame, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    print(f"Wrote {path} rows={len(frame)}")


def numeric(frame, columns):
    for column in columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def summarize_inventory(warehouse, manifests):
    rows = []
    for manifest in manifests:
        metadata = manifest.get("metadata", {})
        rows.append(
            {
                "run_id": manifest.get("run_id", ""),
                "warehouse_exported_at": manifest.get("exported_at", ""),
                "files_copied": manifest.get("files_copied", 0),
                "bytes_copied": manifest.get("bytes_copied", 0),
                "github_run_id": metadata.get("github_run_id", ""),
                "latest_market_date": metadata.get("latest_market_date", ""),
                "model_created_at": metadata.get("model_created_at", ""),
                "model_as_of_date": metadata.get("model_as_of_date", ""),
                "source": manifest.get("source", ""),
            }
        )
    frame = pd.DataFrame(rows)
    write_csv(frame, warehouse / "summaries" / "daily" / "run_inventory.csv")
    return frame


def summarize_model_scores(warehouse, manifests):
    frames = [read_run_csv(manifest, "analytics/model_tournament_evaluation.csv") for manifest in manifests]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return
    scores = pd.concat(frames, ignore_index=True)
    scores = numeric(
        scores,
        [
            "horizon_days",
            "champion_score",
            "selected_return_edge",
            "roc_auc",
            "brier_skill",
            "walk_forward_avg_score",
        ],
    )
    write_csv(scores, warehouse / "summaries" / "daily" / "model_score_history.csv")
    scores["champion_flag"] = scores.get("is_champion", "").astype(str).str.lower().isin(
        {"true", "1", "yes"}
    )
    grouped = (
        scores[scores.get("fit_status", "").eq("ok")]
        .groupby(["horizon_days", "model_name", "model_label"], dropna=False)
        .agg(
            runs=("run_id", "nunique"),
            champion_runs=("champion_flag", "sum"),
            avg_score=("champion_score", "mean"),
            avg_return_edge=("selected_return_edge", "mean"),
            avg_roc_auc=("roc_auc", "mean"),
            avg_brier_skill=("brier_skill", "mean"),
            avg_walk_forward_score=("walk_forward_avg_score", "mean"),
        )
        .reset_index()
        .sort_values(["horizon_days", "avg_score"], ascending=[True, False])
    )
    write_csv(grouped, warehouse / "summaries" / "daily" / "model_score_by_model.csv")


def summarize_predictions(warehouse, manifests):
    frames = [read_run_csv(manifest, "analytics/latest_model_predictions.csv") for manifest in manifests]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return
    predictions = pd.concat(frames, ignore_index=True)
    predictions = numeric(predictions, ["horizon_days", "model_rank", "probability_up"])
    recurrence = (
        predictions[predictions["model_rank"].le(25)]
        .groupby(["horizon_days", "ticker"], dropna=False)
        .agg(
            appearances=("run_id", "nunique"),
            avg_rank=("model_rank", "mean"),
            avg_probability_up=("probability_up", "mean"),
            latest_seen=("as_of_date", "max"),
        )
        .reset_index()
        .sort_values(["horizon_days", "appearances", "avg_probability_up"], ascending=[True, False, False])
    )
    write_csv(recurrence, warehouse / "summaries" / "daily" / "prediction_recurrence.csv")


def summarize_monte_carlo(warehouse, manifests):
    frames = [read_run_csv(manifest, "analytics/latest_monte_carlo_simulations.csv") for manifest in manifests]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return
    monte_carlo = pd.concat(frames, ignore_index=True)
    monte_carlo = numeric(
        monte_carlo,
        [
            "horizon_days",
            "probability_up",
            "median_return",
            "p10_return",
            "p90_return",
            "drawdown_probability",
            "target_probability",
        ],
    )
    grouped = (
        monte_carlo.groupby(["horizon_days", "stock_type"], dropna=False)
        .agg(
            runs=("run_id", "nunique"),
            rows=("ticker", "count"),
            avg_probability_up=("probability_up", "mean"),
            avg_median_return=("median_return", "mean"),
            avg_p10_return=("p10_return", "mean"),
            avg_p90_return=("p90_return", "mean"),
            avg_drawdown_probability=("drawdown_probability", "mean"),
            avg_target_probability=("target_probability", "mean"),
        )
        .reset_index()
        .sort_values(["horizon_days", "avg_median_return"], ascending=[True, False])
    )
    write_csv(grouped, warehouse / "summaries" / "daily" / "monte_carlo_stock_type_summary.csv")


def summarize_ann_importance(warehouse, manifests):
    frames = [read_run_csv(manifest, "analytics/ann_feature_group_importance.csv") for manifest in manifests]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return
    importance = pd.concat(frames, ignore_index=True)
    importance = numeric(importance, ["horizon_days", "importance_delta", "sample_rows"])
    grouped = (
        importance.groupby(["horizon_days", "stock_type", "feature_group"], dropna=False)
        .agg(
            runs=("run_id", "nunique"),
            avg_importance_delta=("importance_delta", "mean"),
            std_importance_delta=("importance_delta", "std"),
            avg_sample_rows=("sample_rows", "mean"),
        )
        .reset_index()
        .sort_values(["horizon_days", "stock_type", "avg_importance_delta"], ascending=[True, True, False])
    )
    write_csv(grouped, warehouse / "summaries" / "daily" / "feature_group_stability.csv")


def summarize_paper_outcomes(warehouse, manifests):
    frames = [read_run_csv(manifest, "analytics/automatic_paper_decision_outcomes.csv") for manifest in manifests]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return
    outcomes = pd.concat(frames, ignore_index=True)
    outcomes = numeric(outcomes, ["evaluation_horizon_days", "return_pct"])
    available = outcomes[outcomes["return_pct"].notna()].copy()
    if available.empty:
        write_csv(outcomes, warehouse / "summaries" / "daily" / "paper_outcome_events.csv")
        return
    available["win"] = available["return_pct"] > 0
    grouped = (
        available.groupby(["action", "evaluation_horizon_days"], dropna=False)
        .agg(
            evaluated=("decision_id", "count"),
            avg_return=("return_pct", "mean"),
            median_return=("return_pct", "median"),
            win_rate=("win", "mean"),
        )
        .reset_index()
        .sort_values(["evaluation_horizon_days", "avg_return"], ascending=[True, False])
    )
    write_csv(grouped, warehouse / "summaries" / "daily" / "paper_outcome_summary.csv")


def summarize_weekly(warehouse):
    daily_path = warehouse / "summaries" / "daily" / "model_score_history.csv"
    if not daily_path.exists():
        return
    scores = pd.read_csv(daily_path)
    if "model_created_at" not in scores.columns:
        return
    created_at = pd.to_datetime(
        scores["model_created_at"], errors="coerce", utc=True
    ).dt.tz_convert(None)
    scores["run_week"] = created_at.dt.to_period("W").astype(str)
    scores = numeric(scores, ["horizon_days", "champion_score", "selected_return_edge"])
    weekly = (
        scores[scores.get("fit_status", "").eq("ok")]
        .groupby(["run_week", "horizon_days", "model_name", "model_label"], dropna=False)
        .agg(
            runs=("run_id", "nunique"),
            avg_score=("champion_score", "mean"),
            avg_return_edge=("selected_return_edge", "mean"),
        )
        .reset_index()
        .sort_values(["run_week", "horizon_days", "avg_score"], ascending=[False, True, False])
    )
    write_csv(weekly, warehouse / "summaries" / "weekly" / "model_score_weekly.csv")


def summarize(warehouse):
    manifests = read_run_manifests(warehouse)
    if not manifests:
        print("No run archives found. Use export-run first.")
        return
    summarize_inventory(warehouse, manifests)
    summarize_model_scores(warehouse, manifests)
    summarize_predictions(warehouse, manifests)
    summarize_monte_carlo(warehouse, manifests)
    summarize_ann_importance(warehouse, manifests)
    summarize_paper_outcomes(warehouse, manifests)
    summarize_weekly(warehouse)


def status(warehouse):
    manifests = read_run_manifests(warehouse)
    summaries = sorted((warehouse / "summaries").glob("*/*.csv"))
    print(f"Warehouse: {warehouse}")
    print(f"Run archives: {len(manifests)}")
    print(f"Summary files: {len(summaries)}")
    if manifests:
        latest = sorted(manifests, key=lambda item: item.get("exported_at", ""))[-1]
        print(f"Latest run id: {latest.get('run_id', '')}")
        print(f"Latest source: {latest.get('source', '')}")
    if summaries:
        print("Summaries:")
        for path in summaries:
            print(f"  {path.relative_to(warehouse)}")


def main():
    args = parse_args()
    warehouse = resolve_path(args.warehouse)

    if args.command == "setup":
        ensure_warehouse(warehouse, dry_run=args.dry_run)
        print("Next: keep large data here and push only code or compact summaries to GitHub.")
        return 0

    ensure_warehouse(warehouse, dry_run=False)
    if args.command == "export-run":
        export_run(args, warehouse)
    elif args.command == "summarize":
        summarize(warehouse)
    elif args.command == "status":
        status(warehouse)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
