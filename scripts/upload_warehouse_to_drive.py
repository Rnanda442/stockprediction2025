"""Upload Open Science Lab warehouse outputs to Google Drive with rclone.

Run this from Open Science Lab after syncing GitHub artifacts. By default it
uploads compact summaries and manifests, not the full warehouse.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = ROOT / "warehouse"
DEFAULT_REMOTE = os.getenv("GDRIVE_RCLONE_REMOTE", "gdrive")
DEFAULT_DRIVE_PATH = os.getenv("GDRIVE_WAREHOUSE_PATH", "stockprediction2025/warehouse")

COMPACT_ITEMS = (
    "summaries",
    "manifests",
    "paper_outcomes",
)

LARGE_ITEMS = (
    "model_runs/runs",
    "model_runs/evaluations",
    "model_runs/predictions",
    "monte_carlo",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warehouse",
        default=os.getenv("STOCKPREDICTION_WAREHOUSE", str(DEFAULT_WAREHOUSE)),
        help="Open Science Lab warehouse path.",
    )
    parser.add_argument(
        "--remote",
        default=DEFAULT_REMOTE,
        help="Configured rclone remote name for Google Drive.",
    )
    parser.add_argument(
        "--drive-path",
        default=DEFAULT_DRIVE_PATH,
        help="Destination folder path inside the Google Drive remote.",
    )
    parser.add_argument(
        "--include-run-archives",
        action="store_true",
        help="Also upload model run archives and Monte Carlo folders.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Upload only this warehouse-relative path. Can be passed more than once.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show rclone actions only.")
    parser.add_argument("--verbose", action="store_true", help="Pass -v to rclone.")
    return parser.parse_args()


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def run_command(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def require_rclone(remote: str) -> None:
    try:
        run_command(["rclone", "version"])
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "rclone is not installed. In Open Science Lab run: "
            "mamba install -c conda-forge rclone -y"
        ) from exc

    result = run_command(["rclone", "listremotes"])
    remotes = {line.rstrip(":") for line in result.stdout.splitlines()}
    if remote.rstrip(":") not in remotes:
        raise RuntimeError(
            f"rclone remote `{remote}` is not configured. Run `rclone config` in Open Science Lab "
            "and create a Google Drive remote with that name."
        )


def drive_target(remote: str, drive_path: str, relative: str) -> str:
    remote_name = remote.rstrip(":")
    clean_drive_path = drive_path.strip("/")
    clean_relative = relative.strip("/")
    parts = [part for part in (clean_drive_path, clean_relative) if part]
    return f"{remote_name}:{'/'.join(parts)}"


def upload_item(
    source: Path,
    target: str,
    *,
    dry_run: bool,
    verbose: bool,
) -> bool:
    if not source.exists():
        print(f"Skipping missing path: {source}")
        return False
    command = ["rclone", "copy", str(source), target, "--create-empty-src-dirs"]
    if dry_run:
        command.append("--dry-run")
    if verbose:
        command.append("-v")
    print(f"Uploading {source} -> {target}")
    result = run_command(command, check=False)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    if result.returncode != 0:
        raise RuntimeError(f"rclone copy failed for {source} with exit code {result.returncode}")
    return True


def upload_readme(warehouse: Path, remote: str, drive_path: str, *, dry_run: bool, verbose: bool) -> bool:
    readme = warehouse / "README.local.md"
    if not readme.exists():
        return False
    return upload_item(
        readme,
        drive_target(remote, drive_path, ""),
        dry_run=dry_run,
        verbose=verbose,
    )


def selected_items(args: argparse.Namespace) -> list[str]:
    if args.only:
        return [item.strip().strip("/\\") for item in args.only if item.strip()]
    items = list(COMPACT_ITEMS)
    if args.include_run_archives:
        items.extend(LARGE_ITEMS)
    return items


def main() -> int:
    args = parse_args()
    warehouse = resolve_path(args.warehouse)
    if not warehouse.exists():
        raise RuntimeError(f"Warehouse does not exist: {warehouse}")
    require_rclone(args.remote)

    uploaded = 0
    skipped = 0
    for relative in selected_items(args):
        source = warehouse / relative
        target = drive_target(args.remote, args.drive_path, relative)
        if upload_item(source, target, dry_run=args.dry_run, verbose=args.verbose):
            uploaded += 1
        else:
            skipped += 1

    if upload_readme(warehouse, args.remote, args.drive_path, dry_run=args.dry_run, verbose=args.verbose):
        uploaded += 1

    print(
        "Drive upload complete: "
        f"uploaded_items={uploaded} skipped_items={skipped} "
        f"destination={args.remote.rstrip(':')}:{args.drive_path.strip('/')}"
    )
    if not args.include_run_archives and not args.only:
        print("Compact mode only. Add --include-run-archives to upload larger run folders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
