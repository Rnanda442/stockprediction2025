# Public stock-research map

This directory is a deterministic, sanitized documentary view of the project as of **2026-11-22**. It contains 34 current registered study IDs, 3 additional dependency/history experiment records, 59 reviewed variable definitions, and 55 public evidence-title records.

It is not a trading system, model leaderboard, live database mirror, or complete copy of the private research workspace. No model is promoted. The consumed May 29–August 24, 2026 holdout is not exposed or reused here.

## Files

- `index.html`, `map.css`, and `map.js` — a self-contained browser for the public graph, workflow, studies, and evidence index.
- `knowledge-canvas.js` and `vendor/` — the movable graph workspace and its pinned local Cytoscape.js renderer.
- `GRAPH_CANVAS.md` and `THIRD_PARTY_NOTICES.md` — graph controls, presentation limits and renderer attribution.
- `RADIAL_EXPANSION.md` — in-place branches, exact field annotations and declared-reference provenance.
- `project-graph.public.json` — Neo4j/property-graph-compatible nodes and typed edges.
- `method-catalog.public.json` — 34 saved-evidence study cards with metric scope and limitations.
- `workflow-view.public.json` — the reviewed 12-stage decision flow, seven variable categories, 59 variable definitions, and public-safe workflow records.
- `evidence-index.public.json` — source titles, authority labels, and content digests without local paths.
- `workflow-details.public.json` — separately reviewed child diagrams for the 12 workflow steps; its own content hash binds it to this base snapshot.
- `WORKFLOW_DETAILS.md` — how the child diagrams, status labels and source links are maintained.
- `ARCHITECTURE.md` — how the public snapshot relates to the reviewed private systems.
- `EXPORT_ALLOWLIST.md` — exact GitHub publication boundary.

## Status semantics

“Completed” means a registered activity finished. It does **not** mean a model passed, is profitable, or is approved. Metrics remain scoped to their own targets, windows, baselines, and costs. This snapshot does not update Neo4j, Notion, the website, or any collector.

## Rebuild

Rebuilding is a maintainer-only operation because the five reviewed inputs—the canonical graph, method review, test-explanation index, evidence-title catalog, and workflow view—are not all part of this public package. Maintainers can run `python scripts/export_public_project_map.py`; it applies a field allowlist, verifies required coverage, performs a privacy scan, and refuses output above 3 MB.

## View locally

The public viewer loads its graph renderer locally from the pinned `vendor/` files. From the repository root, run `python -m http.server 8000 --directory docs/research`, then open `localhost:8000` in a browser. The published JSON keeps canonical IDs, evidence IDs, source-document digests, and per-file content hashes inspectable without exposing the private inputs. Graph layout is navigation, not predictive evidence.
