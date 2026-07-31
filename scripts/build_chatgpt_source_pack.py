"""Build one Markdown source pack for uploading to a ChatGPT Project.

The generated file is written outside the repo by default so it does not become
another project source or accidentally get committed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT.parent / f"{ROOT.name}_chatgpt_source_pack.md"
SKIP_PATHS = {
    ".gitignore",
    "CHATGPT_SOURCE_PACK.md",
    "package.json",
    "pnpm-lock.yaml",
    "scripts/check_robinhood_auth_preflight.py",
    "notebook/robinhood_auth_login.py",
    "scripts/sync_robinhood_session.py",
    "stockprediction2025_chatgpt_source_pack.md",
    "tsconfig.json",
    "vite.config.ts",
}
SKIP_PREFIXES = (
    ".openai/",
    "app/",
    "build/",
    "public/",
    "worker/",
)
LANG_BY_SUFFIX = {
    ".md": "markdown",
    ".py": "python",
    ".ps1": "powershell",
    ".txt": "text",
    ".yaml": "yaml",
    ".yml": "yaml",
}


def git_stdout(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def tracked_paths() -> list[Path]:
    files = []
    for raw_path in git_stdout("ls-files").splitlines():
        rel = raw_path.replace("\\", "/")
        if rel in SKIP_PATHS or rel.startswith(SKIP_PREFIXES):
            continue
        files.append(ROOT / rel)
    return files


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def stable_fence(text: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def sha256_short(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def source_from_cell(cell: dict) -> str:
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(source)
    return str(source)


def render_notebook(path: Path, rel_path: str) -> str:
    notebook = json.loads(read_text(path))
    lines = [
        f"## {rel_path}",
        "",
        "Notebook converted to markdown/code cells. Outputs and metadata omitted.",
        "",
    ]

    for index, cell in enumerate(notebook.get("cells", []), start=1):
        source = source_from_cell(cell).strip()
        if not source:
            continue

        cell_type = cell.get("cell_type", "raw")
        language = "python" if cell_type == "code" else "markdown"
        fence = stable_fence(source)
        lines.extend(
            [
                f"### Cell {index} - {cell_type}",
                "",
                f"{fence}{language}",
                source,
                fence,
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def render_text_file(path: Path, rel_path: str) -> str:
    text = read_text(path).rstrip()
    language = LANG_BY_SUFFIX.get(path.suffix.lower(), "text")
    fence = stable_fence(text)
    return "\n".join(
        [
            f"## {rel_path}",
            "",
            f"{fence}{language}",
            text,
            fence,
            "",
        ]
    )


def render_file(path: Path) -> str:
    rel_path = path.relative_to(ROOT).as_posix()
    if path.suffix.lower() == ".ipynb":
        return render_notebook(path, rel_path)
    return render_text_file(path, rel_path)


def build_pack(output_path: Path) -> tuple[int, int]:
    paths = tracked_paths()
    commit = git_stdout("rev-parse", "--short", "HEAD")
    branch = git_stdout("branch", "--show-current") or "detached"
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    manifest_rows = [
        "| Path | Bytes | SHA-256 |",
        "| --- | ---: | --- |",
    ]
    for path in paths:
        rel_path = path.relative_to(ROOT).as_posix()
        manifest_rows.append(f"| `{rel_path}` | {path.stat().st_size} | `{sha256_short(path)}` |")

    sections = [
        "# stockprediction2025 ChatGPT Source Pack",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Repo path: `{ROOT}`",
        f"- Git branch: `{branch}`",
        f"- Git commit: `{commit}`",
        f"- Tracked files included: `{len(paths)}`",
        "",
        "## How To Use This File",
        "",
        "Upload this single Markdown file as the ChatGPT Project source for the stock prediction project.",
        "It is a snapshot of the current repo source, so regenerate it after commits that change code, model logic, pipeline behavior, or project instructions.",
        "You do not need to regenerate it just because GitHub Actions produced new database artifacts, unless the source code also changed.",
        "",
        "## Manifest",
        "",
        "\n".join(manifest_rows),
        "",
        "# Source Files",
        "",
    ]

    for path in paths:
        sections.append(render_file(path))

    content = "\n".join(sections).rstrip() + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return len(paths), len(content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output Markdown path. Default: {DEFAULT_OUTPUT}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = args.output.resolve()
    file_count, char_count = build_pack(output_path)
    print(f"Wrote {output_path}")
    print(f"Included {file_count} tracked files")
    print(f"Size: {output_path.stat().st_size:,} bytes / {char_count:,} characters")


if __name__ == "__main__":
    main()
