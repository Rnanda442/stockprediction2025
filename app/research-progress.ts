// Curated infrastructure evidence, separate from the older model-result packet.
// This presentation record does not merge completion candidates into the context gate.
export const PROGRESS_REVIEW_DATE = "2026-08-30";
export const PRICE_STAGE = {
  id: "materialized_eligible_prices_v1",
  tickers: 2597,
  rows: 3104633,
  eligibleRows: 2951410,
  warmupRows: 153223,
  bytes: 257949696,
  firstDate: "2021-08-23",
  lastDate: "2026-05-28",
  holdoutStart: "2026-05-29",
  holdoutEnd: "2026-08-24",
} as const;

export type ProgressStatus = "ready" | "next" | "blocked" | "waiting" | "protected";
export type ProgressItem = {
  title: string;
  status: ProgressStatus;
  summary: string;
  evidence: string;
  doneWhen: string;
  node: string;
  source: string;
};
export const PROGRESS_STATUS_LABEL: Record<ProgressStatus, string> = {
  ready: "Completed scope", next: "Next", blocked: "Missing data", waiting: "After dependencies", protected: "Protected",
};
export const RESEARCH_PROGRESS: Record<string, ProgressItem> = {
  universe_audit: {
    title: "Past-only universe audit", status: "ready", node: "membership",
    summary: "Audited pre-holdout coverage and created decision-date eligibility using at least 60 prior/current observations.",
    evidence: "3,104,633 rows; 2,597 tickers; no duplicate ticker-date groups or missing close/volume rows in this audit. A largely fixed survivor universe remains a warning.",
    doneWhen: "Audit and membership artifacts produced. Listing, delisting, and historical sector truth are NOT certified by this audit.",
    source: "warehouse/audits/point_in_time_universe_audit_v1_20260830/audit_manifest.json",
  },
  security_master: {
    title: "Security-master scaffold", status: "ready", node: "security_master",
    summary: "Built source-aware ingestion rules that separate observed coverage, current descriptions, and authoritative dated events.",
    evidence: "2,597 observed tickers; 120 current sector profiles; zero authoritative dated events ingested. The historical master is not complete.",
    doneWhen: "Scaffold and input contract produced, with missing-source guardrails enabled. This is not a survivorship-bias fix.",
    source: "warehouse/security_master/dated_security_master_v1_20260830/security_master_manifest.json",
  },
  eligible_snapshot: {
    title: "Warm-up-preserving price snapshot", status: "ready", node: "eligible_snapshot",
    summary: "Replaced the slow cross-database join with a materialized pre-holdout price snapshot stored entirely in OSL.",
    evidence: "2,951,410 eligible rows plus 153,223 warm-up rows, about 258 MB. Original databases unchanged; sealed holdout not read; no model training or scoring performed.",
    doneWhen: "Materialization reported ready and retained all required warm-up prices. Loader integration is a separate next step.",
    source: "warehouse/materialized_prices/materialized_eligible_prices_v1_20260830/materialization_manifest.json",
  },
  context_review: {
    title: "Record the completed stage in research memory", status: "next", node: "context",
    summary: "Review the generated completion candidate, then merge it into the canonical context gate so this data stage is not repeated.",
    evidence: "The stage is registered and its completion candidate exists on OSL. Candidate generation is not the same as merging reviewed completion.",
    doneWhen: "The canonical gate records the reviewed artifact, unique experiment signature, and unresolved source caveats without changing the sealed-holdout policy.",
    source: "warehouse/materialized_prices/materialized_eligible_prices_v1_20260830/context_gate_candidate_update.json",
  },
  loader_smoke: {
    title: "Exercise the staged loader on a bounded panel", status: "next", node: "staged_loader",
    summary: "Pin the materialization ID in a newly registered panel input and run a small integration check, not a model search.",
    evidence: "The --eligible-prices-db loader is implemented but has not been exercised end-to-end in a panel/model run.",
    doneWhen: "The panel retains trailing-history warm-up, applies eligibility after feature construction, respects label maturity and the cutoff, and finishes without the old cross-database join.",
    source: "scripts/build_point_in_time_residual_panel.py",
  },
  lineage_sources: {
    title: "Supply authoritative dated security events", status: "blocked", node: "lineage_sources",
    summary: "Ingest confirmed listing, delisting, and symbol-change records with effective dates. Obtain historical sector classifications separately.",
    evidence: "Zero authoritative events are loaded. All 2,597 tickers reach the final pre-holdout date; only seven enter after the dataset start. Observed prices alone cannot establish delisting-safe coverage.",
    doneWhen: "Source coverage, identity matches, effective dates, delisting treatment, and remaining gaps are explicitly audited. Current sector descriptions never substitute for dated historical labels.",
    source: "warehouse/security_master/dated_security_master_v1_20260830/coverage_gaps.csv",
  },
  controlled_comparison: {
    title: "Register the next controlled model comparison", status: "waiting", node: "models",
    summary: "After the loader check and lineage review, freeze a materially new pre-holdout comparison with linear/random controls and cost-aware paper replay.",
    evidence: "Existing ANN, feature-family, graph, and motion results are historical research evidence. The new data snapshot has not produced new model scores.",
    doneWhen: "A unique design and signature are approved in the context gate, inputs are pinned, remaining bias is disclosed, and training-only transforms plus purged temporal evaluation are specified. Do not repeat rejected broad graph/motion sweeps.",
    source: "research_context/context_gate.json",
  },
  final_holdout: {
    title: "Reserve final confirmation", status: "protected", node: "holdout",
    summary: "Keep May 29 through August 24, 2026 sealed while infrastructure and exploratory work continue.",
    evidence: "60 trading dates are reserved. No automatic promotion or brokerage order is connected.",
    doneWhen: "A frozen candidate earns an explicitly authorized final promotion review. Do not open these dates to select variables, architectures, losses, or thresholds.",
    source: "research_context/context_gate.json",
  },
};
export const COMPLETED_PROGRESS = ["universe_audit", "security_master", "eligible_snapshot"];
export const NEXT_PROGRESS = ["context_review", "loader_smoke", "lineage_sources", "controlled_comparison", "final_holdout"];
