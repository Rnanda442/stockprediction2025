import argparse
import copy
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError


AUTH_TIMEOUT_MARKERS = (
    "Login confirmation timed out",
    "Verification workflow required",
)

STAGE_RULES = (
    (
        "Robinhood login",
        ("from robinhood_auth_login import login",),
        ("robinhood_token.txt",),
    ),
    (
        "Ticker filter",
        ("FilteredTickers", "ThreadPoolExecutor", "checkpoint_filtered.csv"),
        ("filtered_tickers.db", "checkpoint_filtered.csv", "checkpoint_rejected.csv"),
    ),
    (
        "Historical download and vectorization",
        ("update_historicals_and_vectorized", "HistoricalPrices", "VectorizedFeatures"),
        ("historicals.db", "vectorized.db"),
    ),
    (
        "Feature summary export",
        ("FeatureSummary", "vector_analysis_results.csv"),
        ("vectorized.db", "vector_analysis_results.csv"),
    ),
    (
        "Similarity and family selection",
        ("pairwise_corr_on_signs", "flipcorr_pairs_5y.csv", "variant_families.csv"),
        (
            "analytics/flipcorr_pairs_5y.csv",
            "analytics/flipcorr_winners_5y.csv",
            "analytics/variant_families.csv",
            "analytics/flipcorr_cache_manifest.json",
        ),
    ),
    (
        "Winner shortlist",
        ("winners_shortlist.csv", "WinnerUniverse"),
        ("analytics/winners_shortlist.csv", "analytics/winners_enriched.csv", "vectorized.db"),
    ),
)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cell_preview(source):
    for line in source.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:100]
    return "(empty cell)"


def stage_slug(stage):
    return "".join(char.lower() if char.isalnum() else "_" for char in stage).strip("_")


def detect_stage(source):
    for stage, markers, outputs in STAGE_RULES:
        if all(marker in source for marker in markers):
            return stage, outputs
    return "Notebook cell", ()


def file_snapshot(root, relative_path):
    path = root / relative_path
    if not path.exists():
        return {"path": relative_path, "exists": False}
    stat = path.stat()
    return {
        "path": relative_path,
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(
            timespec="seconds"
        ),
    }


class ProgressNotebookClient(NotebookClient):
    def __init__(self, nb, *args, project_root, checkpoint_dir, manifest_path, **kwargs):
        super().__init__(nb, *args, **kwargs)
        self.project_root = project_root
        self.checkpoint_dir = checkpoint_dir
        self.manifest_path = manifest_path
        self.total_cells = sum(1 for cell in nb.cells if cell.cell_type == "code")
        self.current_code_cell = 0
        self.cell_started_at = None
        self.stage_records = []

    def write_stage_record(self, record):
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_name = (
            f"{record['code_cell_number']:03d}_"
            f"{stage_slug(record['stage'])}_"
            f"{record['status']}.json"
        )
        checkpoint_path = self.checkpoint_dir / checkpoint_name
        checkpoint_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        self.stage_records.append({**record, "checkpoint": str(checkpoint_path)})
        self.manifest_path.write_text(
            json.dumps(
                {
                    "updated_at": utc_now(),
                    "notebook_stages": self.stage_records,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def preprocess_cell(self, cell, resources, cell_index):
        if cell.cell_type == "code":
            self.current_code_cell += 1
            self.cell_started_at = time.monotonic()
            stage, output_paths = detect_stage(cell.source)
            source_digest = hashlib.sha256(cell.source.encode("utf-8")).hexdigest()[:16]
            print(
                f"::group::Cell {self.current_code_cell}/{self.total_cells} "
                f"(notebook index {cell_index})"
            )
            print(f"Stage: {stage}")
            print(cell_preview(cell.source), flush=True)

        status = "ok"
        error = ""
        try:
            return super().preprocess_cell(cell, resources, cell_index)
        except Exception as exc:
            status = "failed"
            error = str(exc)[:1000]
            raise
        finally:
            if cell.cell_type == "code":
                elapsed = time.monotonic() - self.cell_started_at
                outputs = [file_snapshot(self.project_root, path) for path in output_paths]
                record = {
                    "completed_at": utc_now(),
                    "code_cell_number": self.current_code_cell,
                    "elapsed_seconds": round(elapsed, 3),
                    "error": error,
                    "notebook_cell_index": cell_index,
                    "output_files": outputs,
                    "source_digest": source_digest,
                    "stage": stage,
                    "status": status,
                }
                self.write_stage_record(record)
                print(
                    f"Finished {stage} cell {self.current_code_cell}/{self.total_cells} "
                    f"in {elapsed:.1f}s with status={status}"
                )
                if outputs:
                    ready = sum(1 for item in outputs if item["exists"])
                    print(f"Stage checkpoint outputs present: {ready}/{len(outputs)}")
                print("::endgroup::", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Execute a notebook with GitHub Actions-friendly cell progress.")
    parser.add_argument("notebook", type=Path)
    parser.add_argument("--kernel-name", default="python3")
    parser.add_argument(
        "--executed-output",
        type=Path,
        help="Write the executed notebook to this path instead of overwriting the source notebook.",
    )
    parser.add_argument(
        "--stage-manifest",
        type=Path,
        default=Path("logs/notebook_stage_manifest.json"),
        help="Write stage timing and output snapshots to this JSON file.",
    )
    parser.add_argument(
        "--stage-checkpoint-dir",
        type=Path,
        default=Path("logs/notebook_checkpoints"),
        help="Write one compact checkpoint JSON per executed stage.",
    )
    args = parser.parse_args()

    nb = nbformat.read(args.notebook, as_version=4)
    executed = copy.deepcopy(nb)
    notebook_path = args.notebook.resolve()
    project_root = notebook_path.parent.parent if notebook_path.parent.name == "notebook" else Path.cwd()
    client = ProgressNotebookClient(
        executed,
        kernel_name=args.kernel_name,
        project_root=project_root,
        checkpoint_dir=args.stage_checkpoint_dir,
        manifest_path=args.stage_manifest,
        timeout=None,
        allow_errors=False,
        force_raise_errors=True,
    )

    start = time.monotonic()
    try:
        client.execute()
    except CellExecutionError as exc:
        message = str(exc)
        if any(marker in message for marker in AUTH_TIMEOUT_MARKERS):
            print(
                "::error title=Robinhood verification required::"
                "The notebook reached Robinhood login, but the mobile/app verification "
                "workflow was not approved before timeout. Approve the Robinhood prompt "
                "and rerun the workflow. If no prompt appears, refresh the cached "
                "Robinhood session locally before dispatching another cloud run.",
                flush=True,
            )
        raise
    finally:
        output_path = args.executed_output or args.notebook
        output_path.parent.mkdir(parents=True, exist_ok=True)
        nbformat.write(executed, output_path)
        print(f"Executed notebook saved to {output_path}")
        print(f"Total notebook runtime: {time.monotonic() - start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
