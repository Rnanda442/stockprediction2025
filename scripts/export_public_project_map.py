#!/usr/bin/env python3
"""Create a deterministic, privacy-filtered documentary export for GitHub.

This is a presentation exporter only.  It never opens market data, notebooks,
credentials, private release directories, or model outputs, and it does not run
an experiment.  The canonical reviewed files remain authoritative.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "graph": ROOT / "research_context/project_knowledge_graph_v1.json",
    "methods": ROOT / "research_context/method_performance_review_v1_20261121.json",
    "test_index": ROOT / "research_context/test_explanation_index_v1_20261122.json",
    "titles": ROOT / "research_context/model_evidence_titles_v1_20261122.json",
    "workflow": ROOT / "research_context/daily_decision_workflow_view_v1_20261122.json",
    "output": ROOT / "docs/research",
}

PUBLIC_KINDS = {
    "project", "goal", "governance", "guardrail", "decision", "blocker",
    "dataset", "data", "experiment", "variable", "variable_stage",
    "model_policy", "target", "transform", "validation", "visualization",
    "geometry_axis", "visual_encoding", "workflow", "collector", "component",
    "contract", "implementation_contract", "qualification_contract",
    "provider_route", "dependency_assertion", "metric_scope", "research_state",
    "next_action",
}
NEVER_PUBLIC_KINDS = {
    "account_configuration", "account_constraint", "account_state", "artifact",
    "documentation", "workspace",
}
WORKFLOW_KINDS = {
    "workflow", "collector", "component", "contract", "implementation_contract",
    "qualification_contract", "next_action", "blocker",
}
REVIEWED_VIEWER_FILES = {"index.html", "map.css", "map.js", "knowledge-canvas.js"}
REVIEWED_SUPPLEMENTS = {"workflow-details.public.json", "WORKFLOW_DETAILS.md", "GRAPH_CANVAS.md", "THIRD_PARTY_NOTICES.md"}
FORBIDDEN_KEY_FRAGMENTS = {
    "url", "path", "uri", "notion", "instance", "database_name", "username",
    "password", "token", "secret", "credential", "local_repository",
}
FORBIDDEN_TEXT = re.compile(
    r"(?i)(https?://|[a-z]:[\\/]|/home/|research_context/knowledge_(?:releases|deliveries)/|"
    r"warehouse/|\.sites-checkout|gprj_[a-z0-9]+|databases\.neo4j\.io|"
    r"NEO4J_(?:PASSWORD|USERNAME|URI|DATABASE)|\b(?:api[_ -]?key|password|verification code)\b|"
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\$\s?3[, ]?000|\b3k\b)"
)
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def public_id(value: Any, label: str = "identifier") -> str:
    if not isinstance(value, str) or not value or not SAFE_ID.fullmatch(value):
        raise ValueError(f"Unsafe {label}: {value!r}")
    return value


def public_text(value: Any, label: str, *, allow_empty: bool = True) -> str:
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    text = " ".join(value.split())
    if not text and not allow_empty:
        raise ValueError(f"{label} cannot be empty")
    if FORBIDDEN_TEXT.search(text):
        raise ValueError(f"Private/location-bearing text in {label}")
    return text


def public_scalar(value: Any, label: str) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return public_text(value, label)
    if isinstance(value, list):
        return [public_scalar(item, f"{label}[]") for item in value]
    if isinstance(value, dict):
        result = {}
        for key in sorted(value):
            low = str(key).lower()
            if any(fragment in low for fragment in FORBIDDEN_KEY_FRAGMENTS):
                continue
            result[str(key)] = public_scalar(value[key], f"{label}.{key}")
        return result
    raise ValueError(f"Unsupported public value in {label}")


def with_digest(document: dict[str, Any]) -> dict[str, Any]:
    body = dict(document)
    body.pop("content_sha256", None)
    document["content_sha256"] = sha256_bytes(canonical_bytes(body))
    return document


def title_from_id(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace(":", " — ").replace("_", " ").replace("-", " ")).strip().title()


def snapshot(paths: dict[str, Path]) -> dict[str, str]:
    return {name: file_sha(path) for name, path in sorted(paths.items())}


def method_ids(titles: dict[str, Any]) -> set[str]:
    methods = titles.get("methods")
    if not isinstance(methods, list):
        raise ValueError("Evidence-title catalog must contain methods[]")
    ids = {public_id(row.get("canonical_id"), "method canonical_id") for row in methods}
    if len(ids) != 34 or len(methods) != 34:
        raise ValueError(f"Expected 34 current method IDs, found {len(ids)}")
    return ids


def evidence_catalog(graph: dict[str, Any], titles: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_path: dict[str, str] = {}
    for src in graph.get("sources", []):
        sid = public_id(src.get("id"), "source id")
        path = src.get("path")
        normalized_path = None
        if isinstance(path, str):
            normalized_path = path.replace("\\", "/")
            by_path[normalized_path] = sid
        scope = src.get("scope") or "Reviewed documentary evidence"
        try:
            item = {
                "id": sid,
                "title": title_from_id(sid),
                "authority": public_text(src.get("authority") or "reviewed_documentary_source", f"source {sid} authority"),
                "sha256": public_id(src.get("sha256"), f"source {sid} sha256"),
                "scope": public_text(scope, f"source {sid} scope"),
            }
        except ValueError:
            if normalized_path is not None:
                by_path.pop(normalized_path, None)
            continue
        by_id[sid] = item
    for src in titles.get("sources", []):
        path = src.get("path")
        digest = src.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str):
            continue
        normalized = path.replace("\\", "/")
        if normalized in by_path:
            continue
        sid = "document:" + digest[:16]
        by_path[normalized] = sid
        by_id[sid] = {
            "id": sid,
            "title": title_from_id(Path(normalized).stem),
            "authority": public_text(src.get("role") or "reviewed_documentary_source", "source role"),
            "sha256": public_id(digest, "source digest"),
            "scope": "Reviewed documentary input to the public method catalog.",
        }
    return by_id, by_path


def refs_from_paths(paths: Any, by_path: dict[str, str]) -> list[str]:
    if not isinstance(paths, list):
        return []
    refs = []
    for path in paths:
        if not isinstance(path, str):
            continue
        normalized = path.split("#", 1)[0].replace("\\", "/")
        if normalized in by_path:
            refs.append(by_path[normalized])
    return sorted(set(refs))


def build_graph(graph: dict[str, Any], current_ids: set[str], safe_sources: dict[str, dict[str, Any]], source_snapshot: dict[str, str]) -> tuple[dict[str, Any], set[str]]:
    kept: list[dict[str, Any]] = []
    reasons = {"private_kind": 0, "non_public_kind": 0, "non_current_experiment": 0, "non_catalog_variable": 0, "unsafe_text": 0}
    evidence_used: set[str] = set()
    for node in graph.get("nodes", []):
        kind = node.get("kind")
        nid = node.get("id")
        if kind in NEVER_PUBLIC_KINDS:
            reasons["private_kind"] += 1
            continue
        if kind not in PUBLIC_KINDS:
            reasons["non_public_kind"] += 1
            continue
        # Keep the three public-safe dependency/history experiment records in the
        # property graph, but distinguish them from the 34 current registry IDs.
        if kind == "variable" and not node.get("metadata", {}).get("catalog_assertion"):
            reasons["non_catalog_variable"] += 1
            continue
        try:
            item = {
                "id": public_id(nid, "node id"),
                "kind": public_id(kind, "node kind"),
                "title": public_text(node.get("label") or title_from_id(nid), f"node {nid} title", allow_empty=False),
                "status": public_text(node.get("status") or "documented", f"node {nid} status"),
                "lifecycle_status": public_text(node.get("lifecycle_status") or "DOCUMENTED", f"node {nid} lifecycle"),
                "gate_outcome": public_text(node.get("gate_outcome") or "NOT_APPLICABLE", f"node {nid} gate"),
                "certainty": public_text(node.get("certainty") or "DOCUMENTED", f"node {nid} certainty"),
                "as_of": public_text(node.get("as_of") or graph.get("as_of"), f"node {nid} as_of"),
                "summary": public_text(node.get("summary") or "", f"node {nid} summary"),
                "scope": public_text(node.get("validity_scope") or "", f"node {nid} scope"),
            }
        except ValueError:
            reasons["unsafe_text"] += 1
            if kind in {"experiment", "variable", "variable_stage"}:
                raise
            continue
        refs = sorted({ref for ref in node.get("source_refs", []) if ref in safe_sources})
        item["evidence_ids"] = refs
        evidence_used.update(refs)
        if kind == "experiment":
            item["model_promoted"] = False
            item["current_registry_id"] = nid.removeprefix("experiment:") in current_ids
        if kind == "variable":
            assertion = node["metadata"]["catalog_assertion"]
            item.update({
                "stage": public_text(assertion.get("stage") or "unspecified", f"variable {nid} stage"),
                "formula": public_text(assertion.get("formula") or "", f"variable {nid} formula"),
                "consumers": public_scalar(assertion.get("feeds") or [], f"variable {nid} consumers"),
                "is_predictive_proof": False,
            })
        kept.append(item)
    kept.sort(key=lambda row: row["id"])
    kept_ids = {row["id"] for row in kept}
    edges = []
    for edge in graph.get("edges", []):
        if edge.get("source") not in kept_ids or edge.get("target") not in kept_ids:
            continue
        eid = edge.get("id")
        refs = sorted({ref for ref in edge.get("source_refs", []) if ref in safe_sources})
        evidence_used.update(refs)
        edges.append({
            "id": public_id(eid, "edge id"),
            "source": public_id(edge.get("source"), "edge source"),
            "target": public_id(edge.get("target"), "edge target"),
            "relation": public_id(edge.get("relation"), "edge relation"),
            "summary": public_text(edge.get("summary") or "", f"edge {eid} summary"),
            "evidence_ids": refs,
        })
    edges.sort(key=lambda row: row["id"])
    counts = {
        "nodes": len(kept), "edges": len(edges),
        "experiment_records": sum(row["kind"] == "experiment" for row in kept),
        "current_registered_experiments": sum(row["kind"] == "experiment" and row["current_registry_id"] for row in kept),
        "dependency_history_experiments": sum(row["kind"] == "experiment" and not row["current_registry_id"] for row in kept),
        "variables": sum(row["kind"] == "variable" for row in kept),
        "variable_stages": sum(row["kind"] == "variable_stage" for row in kept),
    }
    if (counts["current_registered_experiments"], counts["variables"], counts["variable_stages"]) != (34, 59, 7):
        raise ValueError(f"Required public coverage failed: {counts}")
    return with_digest({
        "schema_version": "public_project_graph_v1",
        "document_kind": "sanitized_documentary_property_graph",
        "as_of": graph.get("as_of"),
        "source_snapshot": source_snapshot,
        "counts": counts,
        "filter_report": {
            "canonical_nodes": len(graph.get("nodes", [])),
            "canonical_edges": len(graph.get("edges", [])),
            "omitted_nodes": len(graph.get("nodes", [])) - len(kept),
            "omitted_edges": len(graph.get("edges", [])) - len(edges),
            "node_reasons": reasons,
            "statement": "Private, account-specific, artifact, workspace, path-bearing and out-of-scope records are intentionally omitted.",
        },
        "status_semantics": "Lifecycle completion is documentary status, not predictive success or model promotion.",
        "nodes": kept,
        "edges": edges,
    }), evidence_used


def build_methods(titles: dict[str, Any], source_snapshot: dict[str, str], by_path: dict[str, str]) -> tuple[dict[str, Any], set[str]]:
    studies = []
    evidence_used: set[str] = set()
    for row in titles["methods"]:
        cid = public_id(row.get("canonical_id"), "method id")
        refs = refs_from_paths(row.get("evidence_paths"), by_path)
        evidence_used.update(refs)
        metrics = []
        for metric in row.get("metrics", []):
            mrefs = refs_from_paths(metric.get("sourcePaths") or metric.get("evidence_paths") or metric.get("review_source_path"), by_path)
            # When an older metric pointer is not itself a graph source, bind the
            # aggregate to the method's reviewed documentary evidence rather than
            # publishing a local artifact path or leaving the metric unsourced.
            if not mrefs:
                mrefs = list(refs)
            evidence_used.update(mrefs)
            clean = {
                "label": public_text(metric.get("label") or "Saved aggregate", f"{cid} metric label"),
                "value": public_scalar(metric.get("value"), f"{cid} metric value"),
                "unit": public_text(metric.get("unit") or "", f"{cid} metric unit"),
                "definition": public_text(metric.get("definition") or "", f"{cid} metric definition"),
                "scope": public_text(metric.get("scope") or "", f"{cid} metric scope"),
                "comparability_group": public_text(metric.get("comparabilityGroup") or "within_experiment_only", f"{cid} metric group"),
                "evidence_ids": mrefs,
            }
            metrics.append(clean)
        appropriate = public_scalar(row.get("appropriate_use") or {}, f"{cid} appropriate use")
        studies.append({
            "canonical_id": cid,
            "friendly_title": public_text(row.get("friendly_title") or cid, f"{cid} title", allow_empty=False),
            "question_title": public_text(row.get("question_title") or row.get("friendly_title") or cid, f"{cid} question title", allow_empty=False),
            "family_id": public_text(row.get("family_id") or "other", f"{cid} family"),
            "family_title": public_text(row.get("family_title") or "Other", f"{cid} family title"),
            "registry_lifecycle_records": public_scalar(row.get("registry_lifecycle_records") or [], f"{cid} registry records"),
            "design_signatures": public_scalar(row.get("design_signatures") or [], f"{cid} signatures"),
            "what_it_predicts": public_text(row.get("what_it_predicts") or "Not a predictive model test", f"{cid} prediction target"),
            "inputs_and_method": public_text(row.get("inputs_and_method") or "", f"{cid} method"),
            "baseline": public_text(row.get("baseline") or "", f"{cid} baseline"),
            "scope": public_scalar(row.get("scope") or {}, f"{cid} scope"),
            "positive_finding": public_text(row.get("positive_finding") or "No promoted predictive finding.", f"{cid} positive finding"),
            "positive_finding_type": public_text(row.get("positive_finding_type") or "none", f"{cid} finding type"),
            "reviewed_verdict": public_text(row.get("reviewed_verdict") or "", f"{cid} verdict"),
            "what_failed_or_limits": public_scalar(row.get("what_failed_or_limits") or [], f"{cid} limits"),
            "appropriate_use": appropriate,
            "metrics": metrics,
            "evidence_ids": refs,
            "model_promoted": False,
            "has_positive_finding": bool(row.get("has_positive_finding", False)),
            "cross_experiment_ranking_allowed": False,
        })
    studies.sort(key=lambda row: row["canonical_id"])
    if len(studies) != 34 or len({row["canonical_id"] for row in studies}) != 34:
        raise ValueError("Method catalog must contain 34 unique current IDs")
    return with_digest({
        "schema_version": "public_method_catalog_v1",
        "document_kind": "saved_aggregate_method_review",
        "as_of": titles.get("as_of"),
        "source_snapshot": source_snapshot,
        "counts": {"studies": 34, "saved_aggregate_metrics": sum(len(row["metrics"]) for row in studies)},
        "interpretation": {
            "registry_scope": "34 current registered IDs include predictive, diagnostic, documentary, software and planned work; they are not 34 successful models.",
            "metric_scope": "Compare a metric only within its declared experiment and comparability group.",
            "promotion": "No model is promoted and no live-trading use is authorized.",
        },
        "studies": studies,
    }), evidence_used


def build_workflow(public_graph: dict[str, Any], workflow: dict[str, Any], source_snapshot: dict[str, str], by_path: dict[str, str]) -> tuple[dict[str, Any], set[str]]:
    nodes = public_graph["nodes"]
    variables = [row for row in nodes if row["kind"] == "variable"]
    stage_nodes = {row["id"].split(":", 1)[1]: row for row in nodes if row["kind"] == "variable_stage"}
    stages = []
    for stage_id in sorted(stage_nodes):
        members = [
            {"id": row["id"], "title": row["title"], "formula": row["formula"], "consumers": row["consumers"], "evidence_ids": row["evidence_ids"]}
            for row in variables if row["stage"] == stage_id
        ]
        members.sort(key=lambda row: row["id"])
        stages.append({
            "id": stage_nodes[stage_id]["id"], "title": stage_nodes[stage_id]["title"],
            "definition_count": len(members), "variables": members,
        })
    records = [
        {key: row[key] for key in ("id", "kind", "title", "status", "lifecycle_status", "summary", "scope", "evidence_ids")}
        for row in nodes if row["kind"] in WORKFLOW_KINDS
    ]
    records.sort(key=lambda row: row["id"])
    public_node_ids = {row["id"] for row in nodes}
    evidence_used: set[str] = set()
    pipeline_stages = []
    for row in workflow.get("stages", []):
        stage_id = public_id(row.get("id"), "pipeline stage id")
        refs = refs_from_paths(row.get("evidence"), by_path)
        evidence_used.update(refs)
        known = row.get("known") or ""
        if stage_id == "portfolio_state":
            known = "The paper-account input contract requires supplied holdings and settled funds; no personal portfolio data is included."
        pipeline_stages.append({
            "id": stage_id,
            "label": public_text(row.get("label") or title_from_id(stage_id), f"pipeline {stage_id} label"),
            "status": public_text(row.get("status") or "documented", f"pipeline {stage_id} status"),
            "question": public_text(row.get("question") or "", f"pipeline {stage_id} question"),
            "known": public_text(known, f"pipeline {stage_id} known"),
            "remaining": public_text(row.get("remaining") or "", f"pipeline {stage_id} remaining"),
            "canonical_node_ids": sorted(nid for nid in row.get("canonical_node_ids", []) if nid in public_node_ids),
            "evidence_ids": refs,
        })
    pipeline_edges = []
    pipeline_ids = {row["id"] for row in pipeline_stages}
    for edge in workflow.get("edges", []):
        source = public_id(edge.get("source"), "pipeline edge source")
        target = public_id(edge.get("target"), "pipeline edge target")
        if source not in pipeline_ids or target not in pipeline_ids:
            raise ValueError("Pipeline edge references an unknown public stage")
        pipeline_edges.append({
            "source": source,
            "target": target,
            "relation": public_id(edge.get("relation"), "pipeline edge relation"),
        })
    if len(pipeline_stages) != 12 or len(pipeline_edges) != 15:
        raise ValueError("Expected the reviewed 12-stage, 15-edge decision workflow")
    document = with_digest({
        "schema_version": "public_workflow_view_v1",
        "document_kind": "documentary_decision_workflow_and_variable_index",
        "as_of": public_graph["as_of"],
        "source_snapshot": source_snapshot,
        "counts": {"pipeline_stages": len(pipeline_stages), "pipeline_edges": len(pipeline_edges), "variable_stages": len(stages), "variable_definitions": len(variables), "workflow_records": len(records)},
        "scope_note": "The pipeline is a reviewed design view, not a running service. Variable definitions describe historical project components; inclusion is not evidence of predictive power or current production use.",
        "pipeline_stages": pipeline_stages,
        "pipeline_edges": pipeline_edges,
        "next_design_decision": public_text(workflow.get("next_design_decision") or "", "next design decision"),
        "clock_status": public_scalar(workflow.get("clock_contract_to_decide") or {}, "clock status"),
        "constraints": public_scalar(workflow.get("constraints") or {}, "workflow constraints"),
        "variable_stages": stages,
        "workflow_records": records,
    })
    return document, evidence_used


def build_evidence(all_sources: dict[str, dict[str, Any]], used: set[str], source_snapshot: dict[str, str], as_of: str, canonical_source_count: int) -> dict[str, Any]:
    evidence = [all_sources[sid] for sid in sorted(used) if sid in all_sources]
    return with_digest({
        "schema_version": "public_evidence_index_v1",
        "document_kind": "sanitized_source_title_and_digest_index",
        "as_of": as_of,
        "source_snapshot": source_snapshot,
        "counts": {
            "canonical_source_records": canonical_source_count,
            "public_safe_source_records": len(all_sources),
            "referenced_public_evidence_records": len(evidence),
            "unsafe_source_records_omitted": canonical_source_count - len(all_sources),
            "unused_safe_source_records_omitted": len(all_sources) - len(evidence),
        },
        "privacy_note": "Titles, authority labels and content digests are public; local paths, URLs, account identifiers and private release locations are omitted.",
        "evidence": evidence,
    })


def docs_text(graph_doc: dict[str, Any], methods_doc: dict[str, Any], evidence_doc: dict[str, Any]) -> dict[str, str]:
    readme = f"""# Public stock-research map

This directory is a deterministic, sanitized documentary view of the project as of **{graph_doc['as_of']}**. It contains {graph_doc['counts']['current_registered_experiments']} current registered study IDs, {graph_doc['counts']['dependency_history_experiments']} additional dependency/history experiment records, {graph_doc['counts']['variables']} reviewed variable definitions, and {evidence_doc['counts']['referenced_public_evidence_records']} public evidence-title records.

It is not a trading system, model leaderboard, live database mirror, or complete copy of the private research workspace. No model is promoted. The consumed May 29–August 24, 2026 holdout is not exposed or reused here.

## Files

- `index.html`, `map.css`, and `map.js` — a self-contained browser for the public graph, workflow, studies, and evidence index.
- `knowledge-canvas.js` and `vendor/` — the movable graph workspace and its pinned local Cytoscape.js renderer.
- `GRAPH_CANVAS.md` and `THIRD_PARTY_NOTICES.md` — graph controls, presentation limits and renderer attribution.
- `project-graph.public.json` — Neo4j/property-graph-compatible nodes and typed edges.
- `method-catalog.public.json` — {methods_doc['counts']['studies']} saved-evidence study cards with metric scope and limitations.
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
"""
    architecture = """# Public documentary architecture

```mermaid
flowchart LR
    R[Reviewed research documents] --> E[Strict public exporter]
    E --> G[Public property graph]
    E --> M[Public method catalog]
    E --> W[Public workflow view]
    E --> V[Evidence title + digest index]
    G --> S[Versioned website snapshot]
    M --> S
    W --> S
    V --> S
    N[(Private Neo4j Free)] -. separate reviewed revision .-> R
    O[Private Notion library] -. separate acknowledgements .-> R
```

The website is a versioned, reviewed snapshot. Neo4j Free and Notion are separate private surfaces; neither is asserted to update this GitHub export automatically. There is no background or two-way synchronization.

## Property-graph contract

`project-graph.public.json` uses stable `nodes[].id` and `edges[].id`. Every edge names an existing `source`, `target`, and typed `relation`. `evidence_ids` join to `evidence-index.public.json`. A Neo4j importer can `MERGE` nodes by `id`, then relationships by `id`, while preserving the published `kind` and `relation` allowlists. The repository does not include database credentials or a cloud import command.

## Research boundary

The graph is navigation and documentation, not market evidence. Architecture v3 remains blocked on a qualified historical security master and 756 trustworthy point-in-time dates. Prospective collection does not repair historical identity, membership, or delisting gaps.
"""
    allowlist = """# GitHub documentation allowlist

Only the following public documentation files belong to this export:

```text
scripts/export_public_project_map.py
scripts/build_workflow_detail_catalog.py
docs/research/README.md
docs/research/ARCHITECTURE.md
docs/research/EXPORT_ALLOWLIST.md
docs/research/index.html
docs/research/map.css
docs/research/map.js
docs/research/knowledge-canvas.js
docs/research/vendor/cytoscape.min.js
docs/research/vendor/cytoscape.LICENSE
docs/research/GRAPH_CANVAS.md
docs/research/THIRD_PARTY_NOTICES.md
docs/research/project-graph.public.json
docs/research/method-catalog.public.json
docs/research/workflow-view.public.json
docs/research/evidence-index.public.json
docs/research/workflow-details.public.json
docs/research/WORKFLOW_DETAILS.md
```

Explicitly excluded: raw prices, databases, notebooks and outputs, predictions, portfolio or account information, credentials, environment files, private Notion links, Neo4j instance identifiers, local paths, website platform metadata, private release/delivery directories, backups, receipts with operational identifiers, and the unfiltered canonical graph.

The GitHub Actions research workflow is not part of this documentation export. Publishing these files must not be described as running, validating, or promoting a model.
"""
    return {"README.md": readme, "ARCHITECTURE.md": architecture, "EXPORT_ALLOWLIST.md": allowlist}


def privacy_scan(value: Any, location: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            low = key.lower()
            if any(fragment in low for fragment in FORBIDDEN_KEY_FRAGMENTS):
                raise ValueError(f"Forbidden public key at {location}.{key}")
            privacy_scan(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            privacy_scan(item, f"{location}[{index}]")
    elif isinstance(value, str) and FORBIDDEN_TEXT.search(value):
        raise ValueError(f"Forbidden public text at {location}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("graph", "methods", "test_index", "titles", "workflow", "output"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, default=DEFAULTS[name])
    args = parser.parse_args()
    paths = {name: getattr(args, name).resolve() for name in ("graph", "methods", "test_index", "titles", "workflow")}
    graph, methods, test_index, titles, workflow = (load(paths[name]) for name in ("graph", "methods", "test_index", "titles", "workflow"))
    # These reads also bind the export to both reviewed method indexes even though
    # friendly public cards come from the final curated title catalog.
    if len(methods.get("experiments", [])) != 33 or len(test_index.get("additions", [])) != 1:
        raise ValueError("Expected frozen 33-card catalog plus one current addition")
    current_ids = method_ids(titles)
    snap = snapshot(paths)
    all_sources, by_path = evidence_catalog(graph, titles)
    graph_doc, graph_refs = build_graph(graph, current_ids, all_sources, snap)
    methods_doc, method_refs = build_methods(titles, snap, by_path)
    workflow_doc, pipeline_refs = build_workflow(graph_doc, workflow, snap, by_path)
    workflow_refs = {ref for row in workflow_doc["workflow_records"] for ref in row["evidence_ids"]}
    for stage in workflow_doc["variable_stages"]:
        for variable in stage["variables"]:
            workflow_refs.update(variable["evidence_ids"])
    evidence_doc = build_evidence(all_sources, graph_refs | method_refs | workflow_refs | pipeline_refs, snap, graph_doc["as_of"], len(graph.get("sources", [])))
    documents = {
        "project-graph.public.json": graph_doc,
        "method-catalog.public.json": methods_doc,
        "workflow-view.public.json": workflow_doc,
        "evidence-index.public.json": evidence_doc,
    }
    for name, doc in documents.items():
        privacy_scan(doc, name)
    texts = docs_text(graph_doc, methods_doc, evidence_doc)
    for name, text in texts.items():
        if FORBIDDEN_TEXT.search(text):
            raise ValueError(f"Forbidden text in {name}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # The viewer is authored and reviewed separately.  Permit exactly its three
    # public files in the export directory, but never rewrite them here.
    allowed = set(documents) | set(texts) | REVIEWED_VIEWER_FILES | REVIEWED_SUPPLEMENTS
    unexpected = sorted(path.name for path in output.iterdir() if path.is_file() and path.name not in allowed)
    if unexpected:
        raise ValueError(f"Unexpected files in public export directory: {unexpected}")
    rendered: dict[str, bytes] = {name: json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n" for name, doc in documents.items()}
    rendered.update({name: text.encode("utf-8") for name, text in texts.items()})
    total = sum(len(data) for data in rendered.values())
    if total > 3_000_000:
        raise ValueError(f"Public export exceeds 3 MB: {total} bytes")
    for name in sorted(rendered):
        (output / name).write_bytes(rendered[name])
    print(json.dumps({
        "status": "written", "output": output.as_posix(), "total_bytes": total,
        "files": {name: {"bytes": len(data), "sha256": sha256_bytes(data)} for name, data in sorted(rendered.items())},
        "counts": {"graph": graph_doc["counts"], "methods": methods_doc["counts"], "workflow": workflow_doc["counts"], "evidence": evidence_doc["counts"]},
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
