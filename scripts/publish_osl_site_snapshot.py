"""Publish the tiny OSL website snapshot to GitHub for the Sites dashboard."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = ROOT / "warehouse"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=str(DEFAULT_WAREHOUSE / "drive_pack" / "site_snapshot.json"),
    )
    parser.add_argument(
        "--repo",
        default=os.getenv("GITHUB_REPOSITORY", "Rnanda442/stockprediction2025"),
    )
    parser.add_argument("--branch", default="main")
    parser.add_argument("--path", default="public/data/latest-analysis.json")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def gh(command: list[str], *, input_text: str = "", check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *command],
        cwd=ROOT,
        input=input_text or None,
        capture_output=True,
        text=True,
        check=check,
    )


def require_gh() -> None:
    if not shutil.which("gh"):
        raise RuntimeError("GitHub CLI is not installed in Open Science Lab.")
    result = gh(["auth", "status"], check=False)
    if result.returncode != 0:
        raise RuntimeError("GitHub CLI is not authenticated in Open Science Lab.")


def remote_file(repo: str, branch: str, path: str) -> dict[str, object] | None:
    endpoint = f"repos/{repo}/contents/{quote(path, safe='/')}"
    result = gh(["api", "--method", "GET", endpoint, "-f", f"ref={branch}"], check=False)
    if result.returncode == 0:
        return json.loads(result.stdout)
    if "404" in result.stderr or "Not Found" in result.stderr:
        return None
    raise RuntimeError(f"Could not inspect the existing GitHub snapshot: {result.stderr.strip()}")


def decoded_remote_payload(metadata: dict[str, object] | None) -> dict[str, object]:
    if not metadata:
        return {}
    content = str(metadata.get("content", "")).replace("\n", "")
    if not content:
        return {}
    try:
        return json.loads(base64.b64decode(content).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def main() -> int:
    args = parse_args()
    source = resolve_path(args.source)
    if not source.exists():
        raise RuntimeError(f"OSL website snapshot does not exist: {source}")
    raw = source.read_text(encoding="utf-8")
    local_payload = json.loads(raw)
    if int(local_payload.get("schema_version", 0) or 0) != 1:
        raise RuntimeError("Unsupported website snapshot schema version.")
    if args.dry_run:
        print(
            f"Would publish {source} to {args.repo}:{args.branch}/{args.path} "
            f"({len(raw.encode('utf-8')) / 1000:.1f} KB)."
        )
        return 0

    require_gh()
    metadata = remote_file(args.repo, args.branch, args.path)
    remote_payload = decoded_remote_payload(metadata)
    if remote_payload.get("snapshot_fingerprint") == local_payload.get("snapshot_fingerprint"):
        print("Website snapshot is unchanged; no GitHub commit needed.")
        return 0

    request = {
        "message": "Update OSL website analysis snapshot",
        "content": base64.b64encode(raw.encode("utf-8")).decode("ascii"),
        "branch": args.branch,
    }
    if metadata and metadata.get("sha"):
        request["sha"] = metadata["sha"]
    endpoint = f"repos/{args.repo}/contents/{quote(args.path, safe='/')}"
    result = gh(
        ["api", "--method", "PUT", endpoint, "--input", "-"],
        input_text=json.dumps(request),
    )
    response = json.loads(result.stdout)
    commit = response.get("commit", {})
    print(
        "Published OSL website snapshot: "
        f"path={args.path} commit={str(commit.get('sha', ''))[:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
