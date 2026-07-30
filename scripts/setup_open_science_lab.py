import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = ROOT / "warehouse"
EXCLUDE_HEADER = "# stockprediction2025 local warehouse"

WAREHOUSE_DIRS = (
    "prices/raw",
    "prices/clean",
    "features/vectorized",
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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create a local-only Open Science Lab warehouse."
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
        "--dry-run",
        action="store_true",
        help="Print planned folders without writing anything.",
    )
    return parser.parse_args()


def relative_exclude_pattern(path):
    try:
        relative = path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return None
    return "/" + relative.as_posix().rstrip("/") + "/"


def update_local_git_exclude(warehouse):
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        return []

    exclude_path = git_dir / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    additions = []
    pattern = relative_exclude_pattern(warehouse)
    if pattern and pattern not in existing:
        additions.append(pattern)
    default_pattern = "/warehouse/"
    if default_pattern not in existing and default_pattern not in additions:
        additions.append(default_pattern)
    if additions:
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
                "Store raw data, Parquet/DuckDB files, model-run archives, Monte Carlo",
                "history, paper outcomes, and weekly summaries here.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def main():
    args = parse_args()
    warehouse = Path(args.warehouse).expanduser()
    if not warehouse.is_absolute():
        warehouse = (ROOT / warehouse).resolve()

    planned_dirs = [warehouse / relative for relative in WAREHOUSE_DIRS]
    if args.dry_run:
        print(f"Repo root: {ROOT}")
        print(f"Warehouse: {warehouse}")
        for path in planned_dirs:
            print(f"would create: {path}")
        return 0

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
    print("Next: keep large data here and push only code or compact summaries to GitHub.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
