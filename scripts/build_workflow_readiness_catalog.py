#!/usr/bin/env python3
"""Build a conservative public readiness overlay for the documentary workflow.

This builder reads only the five frozen public JSON documents.  It does not
open market data, notebooks, credentials, private receipts, or model outputs.
Operational readiness remains separate from scientific results and lifecycle
completion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "docs" / "research"
DEFAULT_OUTPUT = PUBLIC / "workflow-readiness.public.json"

SOURCE_FILES = (
    "project-graph.public.json",
    "method-catalog.public.json",
    "workflow-view.public.json",
    "evidence-index.public.json",
    "workflow-details.public.json",
)
EXPECTED_FILE_SHA256 = {
    "project-graph.public.json": "ee2e6a764a5f5b6ccda084970992f25952d12ce0d43e20e97c99df8b8aeb55c4",
    "method-catalog.public.json": "7555009221e1403287c6f7c378cbef3ac3cd634b8f883ca3a34ed6ae1408aaf6",
    "workflow-view.public.json": "fc1cc970c8a35dcd01bb6e385fe1b95d477cc9c31ad424d44cac111d12b0f057",
    "evidence-index.public.json": "262a81680ac3e3e44ab4264acd025bc38e67457f5716a98190931a9a043de4d2",
    "workflow-details.public.json": "ac54b8773edabb3382ed1b9db3a3dfac4541d62d6cdccf43840541e005426fb8",
}

STATUS_DEFINITIONS = {
    "connected": "Reviewed evidence shows this exact operational connection is current and functioning.",
    "needs_connection": "A component or reviewed record exists, but its current operational connection is not established.",
    "blocked": "A required design, qualification, input, or operational fact remains unresolved.",
    "historical": "Historical definition or saved evidence; it is not current operational readiness.",
}

RECORDED_STATUS_RULES = {
    "historical_reviewed": ("historical", "Reviewed historical definition or saved evidence; not current operational proof."),
    "blocked": ("blocked", "The recorded part is blocked by an unresolved required dependency."),
    "design_needed": ("blocked", "A required design or interface has not been frozen."),
    "unverified": ("blocked", "The required operational fact has not been verified."),
    "partially_connected": ("needs_connection", "Some pieces exist, but the reviewed end-to-end connection is absent."),
    "implemented_mechanics": ("needs_connection", "Mechanics exist, but current integration and operational use are not established."),
    "existing": ("needs_connection", "An existing record or component is documented, but current operational connection is not established."),
}

PART_RESEARCH_RESULT = {
    "historical_reviewed": "Reviewed historical definition or saved evidence; not current operational proof or predictive approval.",
    "blocked": "No positive operational result is claimed; the recorded dependency is blocked.",
    "design_needed": "This is a recorded design question or interface, not a completed scientific result.",
    "unverified": "The required fact has not been verified; no scientific or operational result is inferred.",
    "partially_connected": "Separate reviewed pieces exist, but that is not evidence of a working end-to-end connection.",
    "implemented_mechanics": "Software or accounting mechanics exist; this is not predictive success or evidence of current integration.",
    "existing": "An existing reviewed record is documented; its presence alone is not evidence of current use or predictive success.",
}

STAGE_STATUS = {
    "market_data": "blocked",
    "availability_clock": "blocked",
    "forecast_contract": "blocked",
    "features_universe": "blocked",
    "model": "blocked",
    "entry_review": "blocked",
    "portfolio_state": "blocked",
    "holding_review": "blocked",
    "constraints": "needs_connection",
    "paper_report": "needs_connection",
    "paper_ledger": "needs_connection",
    "mature_outcomes": "needs_connection",
}

STAGE_RESEARCH_RESULT = {
    "market_data": "Historical archive evidence exists, but it is survivor-selected and does not certify a current or point-in-time universe.",
    "availability_clock": "Calendar and metadata-guard mechanics have reviewed software evidence; continuous daily availability has not been observed.",
    "forecast_contract": "Prior studies used different targets and horizons; those saved results do not choose the current forecast contract.",
    "features_universe": "Historical measurements and feature definitions exist; their presence does not freeze or validate a current predictive schema.",
    "model": "Saved linear, neural, and geometry research exists. No model is promoted or qualified as a current daily predictor.",
    "entry_review": "Legacy proposal and BUY-formatting components exist; they do not establish an approved entry policy.",
    "portfolio_state": "A paper-account input contract exists, but no personal portfolio data is included and no current import is connected.",
    "holding_review": "Ledger mechanics preserve holdings until an explicit valid action; no approved profitable continuation or exit policy is established.",
    "constraints": "Cash, settlement, fee, and action-validation mechanics have synthetic evidence; required real inputs and adapters remain absent.",
    "paper_report": "A BUY-only formatter and a metadata guard exist separately; neither is a complete BUY/HOLD/SELL_REVIEW/WAIT report runner.",
    "paper_ledger": "The state engine has reviewed synthetic mechanics; no current execution runner or real paper-account state is connected.",
    "mature_outcomes": "Saved research and evaluation records exist; a future outcome process is not active and must not reuse the consumed holdout.",
}

STAGE_DONE_WHEN = {
    "market_data": "A complete dated input is attested with required fields, identity, membership, adjustment, and lifecycle coverage.",
    "availability_clock": "Provider availability, session calendar, completeness, decision deadline, and pre-outcome lock timing are reviewed and observed.",
    "forecast_contract": "Target, horizon, baseline, downside, maturity, execution convention, and missing-outcome policy are frozen before model choice.",
    "features_universe": "Eligible-universe, lookback, missing-data, preprocessing, and past-only predictive feature rules are frozen and source-qualified.",
    "model": "A separately registered, reviewed scoring contract is fixed and receives qualified inputs; no promotion is implied by connection.",
    "entry_review": "A reviewed entry rule produces explainable paper intents under fixed costs, sizing, validity, and no forced daily purchase.",
    "portfolio_state": "Qualified holdings, quantities, settled cash, receivables, marks, and durable state are supplied without exposing private values publicly.",
    "holding_review": "Every supplied holding receives an explainable HOLD, SELL_REVIEW, or WAIT assessment from a reviewed continuation contract.",
    "constraints": "The intent adapter supplies valid dated state and all cash, settlement, cost, position, calendar, identity, and corporate-action checks pass or block explicitly.",
    "paper_report": "One immutable advance report records versions, timestamps, expiry, reasons, constraints, candidates, holdings reviews, and blocked decisions.",
    "paper_ledger": "Only separately valid simulated fills update persistent paper positions, settled cash, costs, and receivables under qualified dated inputs.",
    "mature_outcomes": "Locked advance decisions are evaluated only after maturity under a registered policy, with no holdout reuse or automatic retuning.",
}

EDGE_STATUS = {
    ("market_data", "availability_clock", "attested_as_of"): ("blocked", "No complete current input date and continuous availability are certified."),
    ("availability_clock", "forecast_contract", "constrains_timing"): ("blocked", "Exact provider availability and decision/lock timing remain unverified."),
    ("forecast_contract", "features_universe", "defines_requirements"): ("blocked", "The target, horizon, baseline, and missing-outcome contract are not frozen."),
    ("features_universe", "model", "supplies_features"): ("blocked", "The predictive schema and qualified current inputs are not frozen."),
    ("forecast_contract", "model", "defines_target"): ("blocked", "The executable target and horizon are not frozen."),
    ("model", "entry_review", "supplies_estimates"): ("blocked", "No current qualified scoring payload and reviewed entry policy are connected."),
    ("model", "holding_review", "supplies_estimates"): ("blocked", "No current qualified scoring payload and reviewed holding-continuation/exit policy are connected."),
    ("portfolio_state", "holding_review", "identifies_held_positions"): ("blocked", "Qualified holdings and durable imported state are not supplied."),
    ("entry_review", "constraints", "proposes_entry"): ("blocked", "The entry policy and intent interface are not approved or connected."),
    ("holding_review", "constraints", "proposes_continuation_or_exit"): ("blocked", "The continuation/exit policy and intent interface are not approved or connected."),
    ("portfolio_state", "constraints", "constrains_cash_and_positions"): ("blocked", "Actual settled cash, positions, receivables, and configuration remain unconfirmed."),
    ("constraints", "paper_report", "accepts_or_blocks"): ("needs_connection", "Constraint mechanics exist, but the reviewed adapter and complete report schema are not connected."),
    ("paper_report", "paper_ledger", "supplies_immutable_valid_intents"): ("needs_connection", "No unified immutable-intent report currently drives the ledger."),
    ("paper_ledger", "mature_outcomes", "records_outcome_accounting"): ("needs_connection", "The ledger engine exists, but no qualified runner or mature-outcome process is active."),
    ("paper_report", "mature_outcomes", "preserves_prior_decisions"): ("needs_connection", "No active locked-report and maturity adapter is connected."),
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def load_sources(source_dir: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    docs: dict[str, Any] = {}
    snapshot: dict[str, dict[str, Any]] = {}
    for name in SOURCE_FILES:
        path = source_dir / name
        raw = path.read_bytes()
        digest = sha256_bytes(raw)
        if digest != EXPECTED_FILE_SHA256[name]:
            raise ValueError(f"Frozen source hash mismatch for {name}: {digest}")
        doc = json.loads(raw)
        docs[name] = doc
        snapshot[name] = {
            "file_sha256": digest,
            "content_sha256": doc.get("content_sha256", ""),
            "as_of": doc.get("as_of", ""),
        }
    return docs, snapshot


def text_list(values: Any) -> list[str]:
    return [str(value) for value in values] if isinstance(values, list) else []


def build(source_dir: Path) -> dict[str, Any]:
    docs, snapshot = load_sources(source_dir)
    graph = docs["project-graph.public.json"]
    methods = docs["method-catalog.public.json"]
    workflow = docs["workflow-view.public.json"]
    evidence = docs["evidence-index.public.json"]
    details = docs["workflow-details.public.json"]

    if (len(graph.get("nodes", [])), len(graph.get("edges", []))) != (189, 229):
        raise ValueError("Expected frozen 189-node/229-edge public graph")
    if len(methods.get("studies", [])) != 34:
        raise ValueError("Expected 34 frozen study records")
    stages = workflow.get("pipeline_stages", [])
    edges = workflow.get("pipeline_edges", [])
    if (len(stages), len(edges)) != (12, 15):
        raise ValueError("Expected exact 12-stage/15-edge workflow")
    sections = details.get("sections", [])
    parts = [part for section in sections for part in section.get("nodes", [])]
    if len(sections) != 12 or len(parts) != 303:
        raise ValueError("Expected exact 12-section/303-part detail catalog")
    evidence_ids = {row["id"] for row in evidence.get("evidence", [])}
    if len(evidence_ids) != 55:
        raise ValueError("Expected 55 public evidence records")

    stage_by_id = {stage["id"]: stage for stage in stages}
    if set(stage_by_id) != set(STAGE_STATUS):
        raise ValueError("Stage IDs do not match the reviewed readiness rules")

    incoming: dict[str, list[str]] = {stage_id: [] for stage_id in stage_by_id}
    for edge in edges:
        incoming[edge["target"]].append(edge["source"])
    study_by_id = {study["canonical_id"]: study for study in methods.get("studies", [])}

    stage_cards = []
    for stage in stages:
        stage_id = stage["id"]
        refs = text_list(stage.get("evidence_ids"))
        if not set(refs) <= evidence_ids:
            raise ValueError(f"Unknown stage evidence ID on {stage_id}")
        dependencies = [stage_by_id[source]["label"] for source in incoming[stage_id]]
        stage_cards.append({
            "stage_id": stage_id,
            "title": stage["label"],
            "recorded_status": stage["status"],
            "operational_status": STAGE_STATUS[stage_id],
            "purpose": stage["question"],
            "needs": "Reviewed outputs from " + ", ".join(dependencies) + "." if dependencies else "A separately supplied root input; it is not produced by an earlier workflow stage.",
            "exists": stage["known"],
            "research_result": STAGE_RESEARCH_RESULT[stage_id],
            "missing": stage["remaining"],
            "done_when": STAGE_DONE_WHEN[stage_id],
            "last_reviewed": workflow["as_of"],
            "evidence_ids": refs,
            "derivation": "Reviewed stage fields plus conservative stage-specific operational rule; no live check performed.",
        })

    edge_cards = []
    actual_edge_keys = set()
    for edge in edges:
        key = (edge["source"], edge["target"], edge["relation"])
        actual_edge_keys.add(key)
        if key not in EDGE_STATUS:
            raise ValueError(f"Missing readiness rule for workflow edge {key}")
        status, reason = EDGE_STATUS[key]
        refs = sorted(set(text_list(stage_by_id[edge["source"]].get("evidence_ids")) + text_list(stage_by_id[edge["target"]].get("evidence_ids"))))
        edge_cards.append({
            "source": edge["source"],
            "target": edge["target"],
            "relation": edge["relation"],
            "operational_status": status,
            "reason": reason,
            "evidence_ids": refs,
            "derivation": "Exact reviewed workflow edge with conservative operational connection rule; the documentary arrow alone is not connection proof.",
        })
    if actual_edge_keys != set(EDGE_STATUS):
        raise ValueError("Edge readiness rules do not exactly match the reviewed 15 edges")

    part_cards = []
    for section in sections:
        stage_id = section["stage_id"]
        for part in section.get("nodes", []):
            recorded = part.get("status", "")
            status, reason = RECORDED_STATUS_RULES.get(recorded, ("blocked", "Recorded readiness is unknown or unreviewed; it cannot be treated as connected."))
            role = part.get("role", "leaf")
            if role == "category" and not part["id"].split(":")[-1].startswith("catalog-"):
                status = STAGE_STATUS[stage_id]
                reason = "This node is a grouping label. Its operational color follows the reviewed stage context; its recorded status is retained separately."
            refs = text_list(part.get("evidence_ids"))
            if not set(refs) <= evidence_ids:
                raise ValueError(f"Unknown part evidence ID on {part['id']}")
            inputs = text_list(part.get("inputs"))
            outputs = text_list(part.get("outputs"))
            linked_studies = text_list(part.get("linked_study_ids"))
            if not set(linked_studies) <= set(study_by_id):
                raise ValueError(f"Unknown linked study on {part['id']}")
            stored_study_results = [{
                "canonical_id": study_id,
                "friendly_title": study_by_id[study_id]["friendly_title"],
                "reviewed_verdict": study_by_id[study_id]["reviewed_verdict"],
                "appropriate_use_status": study_by_id[study_id].get("appropriate_use", {}).get("status", ""),
                "model_promoted": bool(study_by_id[study_id].get("model_promoted", False)),
            } for study_id in linked_studies]
            exists_bits = [part.get("summary", "")]
            if part.get("formula"):
                exists_bits.append(f"Recorded formula or rule: {part['formula']}")
            components = text_list(part.get("code_components"))
            if components:
                exists_bits.append("Recorded code components: " + ", ".join(components) + ".")
            if part.get("linked_variable_ids"):
                exists_bits.append(f"{len(part['linked_variable_ids'])} linked variable definition(s) are recorded.")
            research_result = PART_RESEARCH_RESULT.get(recorded, "No reviewed scientific result is inferred from this catalog record.")
            if stored_study_results:
                research_result = "Exact saved-study verdicts are attached as documentary results; they do not establish a current operational connection."
            elif part.get("formula") or part.get("linked_variable_ids"):
                research_result = "A formula or variable definition is recorded; definition presence is not current predictive proof."
            if role == "category":
                research_result = "This is a navigation grouping, not a scientific result or operational component."
            identifier_tail = part["id"].split(":")[-1]
            legacy_or_display = identifier_tail.startswith(("catalog-", "definition-")) or identifier_tail == "display_geometry" or "paused display" in part["title"].lower()
            dedicated_study_record = bool(linked_studies)
            current_workflow = not dedicated_study_record and not legacy_or_display
            past_research = dedicated_study_record or recorded == "historical_reviewed" or legacy_or_display
            facets = []
            if current_workflow:
                facets.append("current_workflow")
            if current_workflow and status in {"blocked", "needs_connection"}:
                facets.append("needs_attention")
            if past_research:
                facets.append("past_research")
            part_cards.append({
                "part_id": part["id"],
                "stage_id": stage_id,
                "title": part["title"],
                "role": role,
                "recorded_status": recorded,
                "operational_status": status,
                "purpose": part.get("summary", ""),
                "needs": inputs,
                "exists": " ".join(bit for bit in exists_bits if bit),
                "research_result": research_result,
                "missing": reason if status in {"blocked", "needs_connection"} else "No current operational use is claimed for this historical record.",
                "done_when": "A reviewed current connection must be recorded explicitly; this overlay never infers connection from code, tests, completion, or a saved result." if status != "historical" else "Historical status remains documentary unless a separate reviewed current-use record is created.",
                "outputs": outputs,
                "stored_study_results": stored_study_results,
                "view_facets": facets,
                "facet_derivation": "Current workflow excludes dedicated study records, legacy definitions, and the paused display branch. Past research includes linked study records (including saved blocked or unrun plans), reviewed historical definitions, and paused/legacy material; inclusion never means a plan ran. Needs attention is limited to non-historical current-workflow records that are blocked or need connection.",
                "stage_context_status": STAGE_STATUS[stage_id],
                "last_reviewed": details["as_of"],
                "evidence_ids": refs,
                "derivation": f"recorded status '{recorded}' mapped by the published conservative readiness rule",
            })

    status_counts = {status: 0 for status in STATUS_DEFINITIONS}
    for row in stage_cards:
        status_counts[row["operational_status"]] += 1
    part_status_counts = {status: 0 for status in STATUS_DEFINITIONS}
    for row in part_cards:
        part_status_counts[row["operational_status"]] += 1
    leaf_status_counts = {status: 0 for status in STATUS_DEFINITIONS}
    category_status_counts = {status: 0 for status in STATUS_DEFINITIONS}
    for row in part_cards:
        target = category_status_counts if row["role"] == "category" else leaf_status_counts
        target[row["operational_status"]] += 1
    edge_status_counts = {status: 0 for status in STATUS_DEFINITIONS}
    for row in edge_cards:
        edge_status_counts[row["operational_status"]] += 1
    facet_counts = {facet: sum(facet in row["view_facets"] for row in part_cards) for facet in ("current_workflow", "needs_attention", "past_research")}

    result: dict[str, Any] = {
        "schema_version": 1,
        "document_kind": "workflow_operational_readiness_supplement",
        "as_of": "2026-11-23",
        "review_basis": "Reviewed November 22–23 documentary sources only; no live checks were performed.",
        "source_snapshot": snapshot,
        "status_definitions": STATUS_DEFINITIONS,
        "view_facet_definitions": {
            "current_workflow": "Parts in the current paper-workflow design, excluding dedicated saved-study records, legacy definitions, and the paused display branch.",
            "needs_attention": "Current-workflow parts that are blocked or need a reviewed connection.",
            "past_research": "Historical definitions and dedicated saved-study records, including blocked or unrun plans. Inclusion does not mean the plan ran or completed; use each saved verdict and lifecycle status.",
        },
        "summary": {
            "stages": len(stage_cards),
            "main_edges": len(edge_cards),
            "parts": len(part_cards),
            "stage_status_counts": status_counts,
            "edge_status_counts": edge_status_counts,
            "part_status_counts": part_status_counts,
            "part_role_counts": {"categories": sum(category_status_counts.values()), "leaves": sum(leaf_status_counts.values())},
            "category_status_counts": category_status_counts,
            "leaf_status_counts": leaf_status_counts,
            "view_facet_counts": facet_counts,
            "connected_end_to_end_path_verified": False,
            "connected_stage_count": status_counts["connected"],
            "connected_main_edge_count": edge_status_counts["connected"],
        },
        "stage_cards": stage_cards,
        "main_edge_statuses": edge_cards,
        "part_readiness": part_cards,
        "limitations": [
            "This is a reviewed snapshot, not a live service or current-market check.",
            "Private holdings, cash, credentials, account identifiers, local paths, and private links are excluded.",
            "A saved result, implemented component, or passing software test does not prove an operational connection or predictive usefulness.",
            "No automatic website, Neo4j, Notion, collector, or brokerage synchronization is enabled by this supplement.",
            "The consumed 2026-05-29 through 2026-08-24 holdout is not reopened or reused.",
            "No model is promoted and no trading action is authorized.",
        ],
    }
    result["content_sha256"] = sha256_bytes(canonical_bytes(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=PUBLIC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--site-output", type=Path)
    args = parser.parse_args()
    result = build(args.source_dir.resolve())
    rendered = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    outputs = [args.output.resolve()]
    if args.site_output:
        outputs.append(args.site_output.resolve())
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(rendered)
    print(json.dumps({
        "status": "written",
        "outputs": [path.as_posix() for path in outputs],
        "bytes": len(rendered),
        "file_sha256": sha256_bytes(rendered),
        "content_sha256": result["content_sha256"],
        "summary": result["summary"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
