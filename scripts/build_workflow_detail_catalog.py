#!/usr/bin/env python3
"""Assemble reviewed workflow subgraphs without opening market or account data."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from export_public_project_map import privacy_scan


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "docs/research"
SHARDS = [
    ROOT / "research_context/workflow_detail_inputs_v1_20261123.json",
    ROOT / "research_context/workflow_detail_decisions_v1_20261123.json",
]
STATUS = {
    "existing", "historical_reviewed", "implemented_mechanics",
    "partially_connected", "design_needed", "blocked", "unverified",
}
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
NODE_KEYS = {
    "id", "title", "status", "summary", "inputs", "outputs", "details",
    "formula", "units", "windows", "evidence_ids", "canonical_node_ids",
    "code_components", "linked_study_ids", "linked_variable_ids",
    "role", "parent_id", "order",
}
LIST_KEYS = {
    "inputs", "outputs", "details", "evidence_ids", "canonical_node_ids",
    "code_components", "linked_study_ids", "linked_variable_ids",
}
SECTION_KEYS = {
    "stage_id", "title", "introduction", "nodes", "edges", "evidence_ids",
}


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )


def category(section, identifier, title, order):
    found = next((n for n in section["nodes"] if n["id"] == identifier), None)
    if found:
        return found
    node = {
        "id": identifier, "title": title, "role": "category", "order": order,
        "status": "historical_reviewed",
        "summary": "Existing catalog definitions are documentation, not a selected predictive schema.",
        "inputs": ["Reviewed legacy definitions"],
        "outputs": ["Inspectable definitions and their recorded scope"],
        "details": ["Some catalog entries are targets, evaluation measures or boundaries rather than model inputs."],
        "evidence_ids": ["legacy_catalog"], "canonical_node_ids": [],
        "code_components": [], "linked_study_ids": [], "linked_variable_ids": [],
    }
    section["nodes"].append(node)
    return node


def supplement_variable_definitions(sections, variables):
    """Keep every legacy definition inspectable, in its documented role."""
    assigned = {v for s in sections for n in s["nodes"] for v in n.get("linked_variable_ids", [])}
    mapping = {
        "raw": "market_data", "identity": "market_data",
        "core": "features_universe", "graph": "features_universe", "motion": "features_universe",
        "model": "model", "validation": "mature_outcomes",
    }
    by_stage = {s["stage_id"]: s for s in sections}
    for i, variable in enumerate(variables):
        if variable["id"] in assigned:
            continue
        group = variable["stage"]
        section = by_stage[mapping[group]]
        parent = f"wd:{section['stage_id']}:catalog-{group}"
        category(section, parent, f"Legacy {group} definitions", 800)
        identifier = f"wd:{section['stage_id']}:definition-{variable['id'].split(':', 1)[1]}"
        section["nodes"].append({
            "id": identifier, "title": variable["title"], "role": "leaf",
            "parent_id": parent, "order": 810 + i,
            "status": "historical_reviewed", "summary": variable["summary"],
            "inputs": ["Definition from the reviewed legacy variable catalog"],
            "outputs": [variable.get("scope", "Definition only; not predictive proof.")],
            "details": [
                f"Catalog group: {group}.",
                "Original formula retained. This entry does not authorize use as an input, target or evaluation sample.",
                *([f"Recorded consumers: {', '.join(variable['consumers'])}."] if variable.get("consumers") else []),
            ],
            "formula": variable.get("formula", ""),
            "evidence_ids": variable.get("evidence_ids", []),
            "canonical_node_ids": [variable["id"]],
            "linked_variable_ids": [variable["id"]], "linked_study_ids": [],
            "code_components": [],
        })
        section["edges"].append({"source": parent, "target": identifier, "relation": "contains"})


def ensure_method_links(sections, studies):
    section = next(s for s in sections if s["stage_id"] == "model")
    dedicated = {n["linked_study_ids"][0] for s in sections for n in s["nodes"]
                 if n.get("role") == "leaf" and len(n.get("linked_study_ids", [])) == 1}
    missing = [m for m in studies if m["canonical_id"] not in dedicated]
    if not missing:
        return
    parent = "wd:model:saved-study-records"
    root = category(section, parent, "Saved studies and designs", 900)
    root.update(status="existing", summary="Every registered study/design has its own record, grouped by its documented family.",
                details=["Includes completed tests, software/data work, audits and unfinished plans; this is not a list of successful predictors."])
    families = {}
    for i, method in enumerate(missing):
        family_id = method["family_id"]
        if family_id not in families:
            family_parent = f"wd:model:study-family-{family_id}"
            family = category(section, family_parent, method["family_title"], 905 + len(families))
            family.update(parent_id=parent, status="existing", summary="A documented method family. Open a study to inspect its original question, evidence and limits.",
                          details=["Family membership is organizational and does not rank predictive performance."])
            section["edges"].append({"source": parent, "target": family_parent, "relation": "contains"})
            families[family_id] = family_parent
        family_parent = families[family_id]
        identifier = f"wd:model:study-{method['canonical_id']}"
        reuse = method.get("appropriate_use", {}).get("status")
        status = "blocked" if reuse == "blocked/notrun" else "existing" if reuse == "reusable_software" else "historical_reviewed"
        section["nodes"].append({
            "id": identifier, "title": method["friendly_title"], "role": "leaf",
            "parent_id": family_parent, "order": 930 + i, "status": status,
            "summary": method["question_title"],
            "inputs": [method["inputs_and_method"]],
            "outputs": [method["what_it_predicts"]],
            "details": [method["reviewed_verdict"], "This is a saved study/design record, not a promoted daily predictor."],
            "evidence_ids": method.get("evidence_ids", []),
            "canonical_node_ids": [f"experiment:{method['canonical_id']}"],
            "code_components": [], "linked_study_ids": [method["canonical_id"]],
            "linked_variable_ids": [],
        })
        section["edges"].append({"source": family_parent, "target": identifier, "relation": "contains"})


def validate(sections, stages, evidence, graph, studies, cross_edges):
    stage_ids = [s["id"] for s in stages]
    if Counter(s["stage_id"] for s in sections) != Counter(stage_ids):
        raise ValueError("Every canonical workflow stage needs exactly one detail section")
    all_ids = set()
    refs = set()
    method_ids = {s["canonical_id"] for s in studies}
    graph_ids = {n["id"] for n in graph["nodes"]}
    variables = {n["id"] for n in graph["nodes"] if n["kind"] == "variable"}
    linked_methods, linked_variables = set(), set()
    edges = 0
    for section in sections:
        unexpected = set(section) - SECTION_KEYS
        if unexpected:
            raise ValueError(f"Unexpected section fields: {unexpected}")
        if not section["title"] or not section["introduction"]:
            raise ValueError("Section title and introduction are required")
        ids = {n["id"] for n in section["nodes"]}
        if len(ids) != len(section["nodes"]) or all_ids & ids:
            raise ValueError("Duplicate detail node ID")
        all_ids.update(ids)
        refs.update(section.get("evidence_ids", []))
        by_id = {n["id"]: n for n in section["nodes"]}
        edge_keys = set()
        contained = set()
        connected = set()
        for edge in section["edges"]:
            if set(edge) - {"source", "target", "relation", "summary", "evidence_ids"}:
                raise ValueError("Unexpected detail relationship field")
            key = (edge["source"], edge["target"], edge["relation"])
            if not SAFE_ID.fullmatch(key[2]):
                raise ValueError("Relationships must use stable typed names")
            if key in edge_keys or key[0] not in ids or key[1] not in ids or key[0] == key[1]:
                raise ValueError(f"Invalid detail edge: {key}")
            edge_keys.add(key)
            connected.update(key[:2])
            if key[2] == "contains":
                contained.add(key[:2])
            refs.update(edge.get("evidence_ids", []))
        for node in section["nodes"]:
            if set(node) - NODE_KEYS:
                raise ValueError(f"Unexpected fields in {node['id']}: {set(node) - NODE_KEYS}")
            if not SAFE_ID.fullmatch(node["id"]) or not node["id"].startswith(f"wd:{section['stage_id']}:"):
                raise ValueError("Node must retain its canonical parent-stage prefix")
            if node["status"] not in STATUS or node.get("role") not in {"category", "leaf"}:
                raise ValueError(f"Unknown role/status on {node['id']}")
            for key in ("id", "title", "summary"):
                if not isinstance(node.get(key), str) or not node[key]:
                    raise ValueError(f"Missing {key} on {node['id']}")
            for key in LIST_KEYS:
                node.setdefault(key, [])
                if not isinstance(node[key], list) or any(not isinstance(s, str) for s in node[key]):
                    raise ValueError(f"{key} must be a text list on {node['id']}")
            parent = node.get("parent_id")
            if parent and (parent, node["id"]) not in contained:
                raise ValueError(f"Missing explicit parent containment edge for {node['id']}")
            if parent and by_id[parent].get("role") != "category":
                raise ValueError(f"A parent must be a category: {node['id']}")
            ancestors = {node["id"]}
            cursor = parent
            while cursor:
                if cursor in ancestors:
                    raise ValueError(f"Containment cycle at {node['id']}")
                ancestors.add(cursor)
                cursor = by_id[cursor].get("parent_id")
            if node["id"] not in connected and len(ids) > 1:
                raise ValueError(f"Disconnected node: {node['id']}")
            if not node["evidence_ids"]:
                raise ValueError(f"Every detail node needs evidence: {node['id']}")
            if set(node["canonical_node_ids"]) - graph_ids:
                raise ValueError(f"Unknown canonical link on {node['id']}")
            if set(node["linked_study_ids"]) - method_ids:
                raise ValueError(f"Unknown study link on {node['id']}")
            if set(node["linked_variable_ids"]) - variables:
                raise ValueError(f"Unknown variable link on {node['id']}")
            if any("/" in x or "\\" in x for x in node["code_components"]):
                raise ValueError("Use code component names, not internal paths")
            linked_methods.update(node["linked_study_ids"])
            linked_variables.update(node["linked_variable_ids"])
            refs.update(node["evidence_ids"])
        edges += len(edge_keys)
    if refs - set(evidence):
        raise ValueError(f"Unresolved evidence IDs: {sorted(refs - set(evidence))}")
    if linked_methods != method_ids or linked_variables != variables:
        raise ValueError("The detail catalog must link every saved study and legacy definition")
    cross_keys = set()
    for edge in cross_edges:
        key = (edge["source"], edge["target"], edge["relation"])
        if set(edge) - {"source", "target", "relation", "summary", "evidence_ids"}:
            raise ValueError("Unexpected cross-step relationship field")
        if key in cross_keys or key[0] not in all_ids or key[1] not in all_ids or key[0] == key[1] or key[2] == "contains":
            raise ValueError(f"Invalid cross-step relationship: {key}")
        cross_keys.add(key)
    part_refs = 0
    for section in sections:
        for node in section["nodes"]:
            for key in ("inputs", "outputs"):
                for value in node[key]:
                    if value.startswith("wd:"):
                        if value not in all_ids:
                            raise ValueError(f"Unresolved part reference on {node['id']}: {value}")
                        part_refs += 1
    return {
        "sections": len(sections), "detail_nodes": len(all_ids), "detail_edges": edges,
        "categories": sum(n["role"] == "category" for s in sections for n in s["nodes"]),
        "leaves": sum(n["role"] == "leaf" for s in sections for n in s["nodes"]),
        "linked_studies": len(linked_methods), "linked_variable_definitions": len(linked_variables),
        "evidence_titles_used": len(refs),
        "cross_section_edges": len(cross_keys),
        "explicit_part_reference_links": part_refs,
    }


def main():
    base_names = ["project-graph.public.json", "method-catalog.public.json", "workflow-view.public.json", "evidence-index.public.json"]
    graph, methods, workflow, evidence = (read(PUBLIC / name) for name in base_names)
    shards = [read(path) for path in SHARDS]
    sections = [s for shard in shards for s in shard["sections"]]
    stages = workflow["pipeline_stages"]
    order = {s["id"]: i for i, s in enumerate(stages)}
    sections.sort(key=lambda s: order[s["stage_id"]])
    supplement_variable_definitions(sections, [n for n in graph["nodes"] if n["kind"] == "variable"])
    ensure_method_links(sections, methods["studies"])
    cross_edges = []
    for section in sections:
        present = {(e["source"], e["target"], e["relation"]) for e in section["edges"]}
        for node in section["nodes"]:
            parent = node.get("parent_id")
            if parent and (parent, node["id"], "contains") not in present:
                section["edges"].append({"source": parent, "target": node["id"], "relation": "contains"})
                present.add((parent, node["id"], "contains"))
        local = {n["id"] for n in section["nodes"]}
        internal = []
        for edge in section["edges"]:
            if edge["source"] in local and edge["target"] in local:
                internal.append(edge)
            else:
                cross_edges.append(edge)
        section["edges"] = internal
    evidence_by_id = {e["id"]: e for e in evidence["evidence"]}
    counts = validate(sections, stages, evidence_by_id, graph, methods["studies"], cross_edges)
    edge_ids = []
    for edge in [edge for section in sections for edge in section["edges"]] + cross_edges:
        edge["id"] = "wd-edge:" + digest([edge["source"], edge["target"], edge["relation"]])[:24]
        edge_ids.append(edge["id"])
    if len(set(edge_ids)) != len(edge_ids):
        raise ValueError("Duplicate stable detail relationship ID")
    document = {
        "schema_version": 1, "as_of": "2026-11-23",
        "document_kind": "reviewed_documentary_workflow_subgraphs",
        "scope_note": "Detailed navigation of existing evidence, mechanics and undecided contracts. These boxes do not run a model, collect data, or authorize stock actions.",
        "base_snapshot_as_of": workflow["as_of"],
        "base_content_hashes": {name: read(PUBLIC / name)["content_sha256"] for name in base_names},
        "status_definitions": {
            "existing": "An existing component or reviewed record; no daily-service claim.",
            "historical_reviewed": "Historical definition or saved evidence; not promotion.",
            "implemented_mechanics": "Implemented accounting or software mechanics; not predictive proof.",
            "partially_connected": "Pieces exist but the full reviewed connection is absent.",
            "design_needed": "A decision or interface has not been frozen.",
            "blocked": "A required gate or dependency has not passed.",
            "unverified": "This operational fact has not been established.",
        },
        "counts": counts, "sections": sections, "cross_section_edges": cross_edges,
        "guardrails": [
            "Paper-only; no model promoted.",
            "No new model tests, fitting, backtests, or collection.",
            "Consumed holdout observations remain permanently excluded.",
            "No automatic Neo4j, Notion, brokerage or website-data synchronization.",
            "Legacy definitions include outcomes and evaluation measures, not only possible inputs.",
        ],
    }
    privacy_scan(document)
    document["content_sha256"] = digest(document)
    rendered = json.dumps(document, ensure_ascii=False, indent=2).encode()
    if len(rendered) > 2_500_000:
        raise ValueError("Workflow detail catalog exceeds its bounded size")
    source_paths = sorted({p for shard in shards for p in shard.get("reviewed_source_paths", [])})
    canonical = {
        **document,
        "reviewed_source_paths": source_paths,
        "assembly_sources": [path.relative_to(ROOT).as_posix() for path in SHARDS],
        "review_scope": "Documentary decomposition only; original reviewed artifacts retain authority.",
    }
    write(ROOT / "research_context/workflow_detail_catalog_v1_20261123.json", canonical)
    write(PUBLIC / "workflow-details.public.json", document)
    print(json.dumps({"valid": True, "counts": counts, "content_sha256": document["content_sha256"], "bytes": len(rendered)}))


if __name__ == "__main__":
    main()
