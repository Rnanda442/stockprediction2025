"""Run the full Open Science Lab compact-analysis workflow.

This keeps large GitHub Actions artifacts in Open Science Lab, rebuilds compact
analysis outputs, creates Gmail-ready reports, and uploads compact outputs to
Google Drive.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = ROOT / "warehouse"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="Successful GitHub runs to inspect.")
    parser.add_argument("--repo", default="Rnanda442/stockprediction2025")
    parser.add_argument("--workflow", default="Run Stock Pipeline")
    parser.add_argument("--artifact-name", default="stock-analysis-outputs")
    parser.add_argument(
        "--warehouse",
        default=os.getenv("STOCKPREDICTION_WAREHOUSE", str(DEFAULT_WAREHOUSE)),
    )
    parser.add_argument("--to", default=os.getenv("GMAIL_TO", ""))
    parser.add_argument("--send-email", action="store_true")
    parser.add_argument("--attach-summaries", action="store_true")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--skip-email", action="store_true")
    parser.add_argument("--skip-drive", action="store_true")
    parser.add_argument("--include-run-archives", action="store_true")
    parser.add_argument("--drive-dry-run", action="store_true")
    parser.add_argument("--drive-verbose", action="store_true")
    return parser.parse_args()


def run_step(label: str, command: list[str]) -> None:
    print(f"\n=== {label} ===")
    print(" ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    args = parse_args()
    python = sys.executable
    warehouse = str(Path(args.warehouse).expanduser())

    if args.skip_sync:
        run_step(
            "Summarize existing warehouse",
            [
                python,
                "scripts/setup_open_science_lab.py",
                "summarize",
                "--warehouse",
                warehouse,
            ],
        )
    else:
        run_step(
            "Sync GitHub Actions artifacts into warehouse",
            [
                python,
                "scripts/sync_github_artifacts_to_warehouse.py",
                "--repo",
                args.repo,
                "--workflow",
                args.workflow,
                "--artifact-name",
                args.artifact_name,
                "--limit",
                str(args.limit),
                "--warehouse",
                warehouse,
            ],
        )

    if not args.skip_email:
        email_command = [
            python,
            "scripts/email_warehouse_summary.py",
            "--warehouse",
            warehouse,
        ]
        if args.to:
            email_command.extend(["--to", args.to])
        if args.send_email:
            email_command.append("--send")
        if args.attach_summaries:
            email_command.append("--attach-summaries")
        run_step("Create Gmail-ready warehouse report", email_command)

    if not args.skip_drive:
        drive_command = [
            python,
            "scripts/upload_warehouse_to_drive.py",
            "--warehouse",
            warehouse,
        ]
        if args.include_run_archives:
            drive_command.append("--include-run-archives")
        if args.drive_dry_run:
            drive_command.append("--dry-run")
        if args.drive_verbose:
            drive_command.append("--verbose")
        run_step("Upload compact warehouse outputs to Google Drive", drive_command)

    print("\nWorkflow complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
