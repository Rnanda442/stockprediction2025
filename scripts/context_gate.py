#!/usr/bin/env python3
"""Read and enforce the cumulative stock-research context gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def load_gate(path: str | Path) -> dict[str, Any]:
    gate_path = Path(path)
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if gate.get("schema_version") != 1:
        raise ValueError(f"Unsupported context gate schema: {gate.get('schema_version')}")
    return gate


def design_fingerprint(design: dict[str, Any]) -> str:
    canonical = json.dumps(design, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_experiment_allowed(
    gate: dict[str, Any],
    experiment_id: str,
    design_signature: str,
    design: dict[str, Any],
) -> str:
    completed = gate.get("completed_experiments", [])
    completed_ids = {item.get("experiment_id") for item in completed}
    completed_signatures = {item.get("design_signature") for item in completed}
    if experiment_id in completed_ids:
        raise RuntimeError(f"Context gate blocked duplicate experiment_id: {experiment_id}")
    if design_signature in completed_signatures:
        raise RuntimeError(f"Context gate blocked duplicate design_signature: {design_signature}")

    approved = {
        item.get("experiment_id"): item
        for item in gate.get("next_experiments", [])
        if item.get("status") == "approved_next"
    }
    if experiment_id not in approved:
        raise RuntimeError(f"Experiment is not approved in context gate: {experiment_id}")
    if approved[experiment_id].get("design_signature") != design_signature:
        raise RuntimeError("Experiment design signature does not match the approved context entry")

    holdout = gate["guardrails"]["sealed_holdout"]
    if holdout.get("opened_for_evaluation"):
        raise RuntimeError("Context gate reports the sealed holdout was already opened")
    return design_fingerprint(design)


def candidate_update(
    gate: dict[str, Any],
    experiment_id: str,
    design_signature: str,
    design: dict[str, Any],
    results: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "base_context_id": gate["context_id"],
        "base_updated_at": gate["updated_at"],
        "action": "review_then_merge",
        "completed_experiment": {
            "experiment_id": experiment_id,
            "design_signature": design_signature,
            "design_fingerprint": design_fingerprint(design),
            "status": "completed_pending_review",
            "design": design,
            "results": results,
        },
        "holdout_status": gate["guardrails"]["sealed_holdout"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gate")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    gate = load_gate(args.gate)
    if args.summary:
        print(json.dumps({
            "context_id": gate["context_id"],
            "updated_at": gate["updated_at"],
            "completed_experiments": [
                item["experiment_id"] for item in gate.get("completed_experiments", [])
            ],
            "approved_next": [
                item["experiment_id"]
                for item in gate.get("next_experiments", [])
                if item.get("status") == "approved_next"
            ],
            "sealed_holdout": gate["guardrails"]["sealed_holdout"],
        }, indent=2))


if __name__ == "__main__":
    main()
