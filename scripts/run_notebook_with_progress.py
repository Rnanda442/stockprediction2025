import argparse
import copy
import time
from pathlib import Path

import nbformat
from nbclient import NotebookClient


def cell_preview(source):
    for line in source.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:100]
    return "(empty cell)"


class ProgressNotebookClient(NotebookClient):
    def __init__(self, nb, *args, **kwargs):
        super().__init__(nb, *args, **kwargs)
        self.total_cells = sum(1 for cell in nb.cells if cell.cell_type == "code")
        self.current_code_cell = 0
        self.cell_started_at = None

    def preprocess_cell(self, cell, resources, cell_index):
        if cell.cell_type == "code":
            self.current_code_cell += 1
            self.cell_started_at = time.monotonic()
            print(
                f"::group::Cell {self.current_code_cell}/{self.total_cells} "
                f"(notebook index {cell_index})"
            )
            print(cell_preview(cell.source), flush=True)

        try:
            return super().preprocess_cell(cell, resources, cell_index)
        finally:
            if cell.cell_type == "code":
                elapsed = time.monotonic() - self.cell_started_at
                print(f"Finished cell {self.current_code_cell}/{self.total_cells} in {elapsed:.1f}s")
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
    args = parser.parse_args()

    nb = nbformat.read(args.notebook, as_version=4)
    executed = copy.deepcopy(nb)
    client = ProgressNotebookClient(
        executed,
        kernel_name=args.kernel_name,
        timeout=None,
        allow_errors=False,
        force_raise_errors=True,
    )

    start = time.monotonic()
    try:
        client.execute()
    finally:
        output_path = args.executed_output or args.notebook
        output_path.parent.mkdir(parents=True, exist_ok=True)
        nbformat.write(executed, output_path)
        print(f"Executed notebook saved to {output_path}")
        print(f"Total notebook runtime: {time.monotonic() - start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
