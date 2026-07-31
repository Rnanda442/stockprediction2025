"""Download successful GitHub Actions artifacts into the Open Science Lab warehouse.

Run this from Open Science Lab, not from the laptop, so large run artifacts stay
close to the warehouse.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import setup_open_science_lab as warehouse_tools


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "Rnanda442/stockprediction2025"
DEFAULT_ARTIFACT = "stock-analysis-outputs"
DEFAULT_WAREHOUSE = warehouse_tools.DEFAULT_WAREHOUSE
DEFAULT_DOWNLOAD_ROOT = DEFAULT_WAREHOUSE / "scratch" / "github_artifacts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo, owner/name.")
    parser.add_argument(
        "--workflow",
        default="Run Stock Pipeline",
        help="Workflow name or file. Use an empty string to skip workflow filtering.",
    )
    parser.add_argument("--artifact-name", default=DEFAULT_ARTIFACT)
    parser.add_argument("--limit", type=int, default=10, help="Successful runs to inspect.")
    parser.add_argument(
        "--run-id",
        action="append",
        default=[],
        help="Specific GitHub run id to sync. Can be passed more than once.",
    )
    parser.add_argument(
        "--warehouse",
        default=str(DEFAULT_WAREHOUSE),
        help="Open Science Lab warehouse path.",
    )
    parser.add_argument(
        "--download-root",
        default=str(DEFAULT_DOWNLOAD_ROOT),
        help="Where downloaded artifacts are staged before export.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download and re-export runs even when a warehouse archive exists.",
    )
    parser.add_argument(
        "--keep-downloads",
        action="store_true",
        help="Keep staged artifact folders after export.",
    )
    parser.add_argument(
        "--skip-summarize",
        action="store_true",
        help="Do not rebuild warehouse summary CSVs after syncing.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any individual run fails to download or export.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions only.")
    return parser.parse_args()


def run_command(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def require_gh_auth() -> None:
    try:
        run_command(["gh", "--version"])
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("GitHub CLI is not installed. Install `gh` in Open Science Lab.") from exc
    status = run_command(["gh", "auth", "status"], check=False)
    if status.returncode != 0:
        detail = (status.stderr or status.stdout).strip()
        raise RuntimeError(f"GitHub CLI is not authenticated. Run `gh auth login` first.\n{detail}")


def successful_runs(args: argparse.Namespace) -> list[dict]:
    if args.run_id:
        return [
            {
                "databaseId": int(run_id),
                "status": "completed",
                "conclusion": "success",
                "url": f"https://github.com/{args.repo}/actions/runs/{run_id}",
            }
            for run_id in args.run_id
        ]
    command = [
        "gh",
        "run",
        "list",
        "--repo",
        args.repo,
        "--status",
        "success",
        "--limit",
        str(args.limit),
        "--json",
        "databaseId,headSha,createdAt,updatedAt,conclusion,status,url,workflowName",
    ]
    if args.workflow:
        command.extend(["--workflow", args.workflow])
    result = run_command(command)
    return json.loads(result.stdout or "[]")


def existing_run_ids(warehouse: Path) -> set[str]:
    manifests = warehouse_tools.read_run_manifests(warehouse)
    ids = {str(item.get("run_id", "")).strip() for item in manifests}
    ids.update(path.name for path in (warehouse / "model_runs" / "runs").glob("*") if path.is_dir())
    return {run_id for run_id in ids if run_id}


def folder_has_files(path: Path) -> bool:
    return path.exists() and any(item.is_file() for item in path.rglob("*"))


def download_artifact(args: argparse.Namespace, run_id: str, destination: Path) -> None:
    if destination.exists() and args.force:
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if folder_has_files(destination) and not args.force:
        print(f"Using existing download for run {run_id}: {destination}")
        return
    command = [
        "gh",
        "run",
        "download",
        run_id,
        "--repo",
        args.repo,
        "--name",
        args.artifact_name,
        "--dir",
        str(destination),
    ]
    print(f"Downloading run {run_id} artifact to {destination}")
    run_command(command)


def export_download(args: argparse.Namespace, warehouse: Path, run_id: str, source: Path) -> None:
    export_args = argparse.Namespace(
        source=str(source),
        run_id=run_id,
        dry_run=args.dry_run,
    )
    warehouse_tools.export_run(export_args, warehouse)


def print_run_failure(run_id: str, exc: BaseException) -> None:
    print(f"Skipping run {run_id}: download/export failed.")
    if isinstance(exc, subprocess.CalledProcessError):
        print(f"  command: {' '.join(str(part) for part in exc.cmd)}")
        print(f"  exit code: {exc.returncode}")
        detail = (exc.stderr or exc.stdout or "").strip()
        if detail:
            print(f"  detail: {detail[:1200]}")
        return
    detail = str(exc).strip()
    if detail:
        print(f"  detail: {detail[:1200]}")


def main() -> int:
    args = parse_args()
    warehouse = warehouse_tools.resolve_path(args.warehouse)
    download_root = warehouse_tools.resolve_path(args.download_root)
    warehouse_tools.ensure_warehouse(warehouse, dry_run=False)
    require_gh_auth()

    imported = 0
    skipped = 0
    failed = 0
    failed_ids: list[str] = []
    known_ids = existing_run_ids(warehouse)
    runs = successful_runs(args)
    if not runs:
        print("No successful runs found.")
        return 0

    for run in runs:
        run_id = str(run.get("databaseId", "")).strip()
        if not run_id:
            continue
        if run_id in known_ids and not args.force:
            print(f"Skipping run {run_id}: already archived.")
            skipped += 1
            continue
        source = download_root / run_id
        if args.dry_run:
            print(f"Would download/export run {run_id}: {run.get('url', '')}")
            imported += 1
            continue
        try:
            download_artifact(args, run_id, source)
            export_download(args, warehouse, run_id, source)
        except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
            failed += 1
            failed_ids.append(run_id)
            print_run_failure(run_id, exc)
            if not args.keep_downloads:
                shutil.rmtree(source, ignore_errors=True)
            continue
        known_ids.add(run_id)
        imported += 1
        if not args.keep_downloads:
            shutil.rmtree(source, ignore_errors=True)

    if not args.skip_summarize and not args.dry_run:
        warehouse_tools.summarize(warehouse)
    print(f"Sync complete: imported={imported} skipped={skipped} failed={failed} warehouse={warehouse}")
    if failed_ids:
        print("Failed runs: " + ", ".join(failed_ids))
    return 1 if failed and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
