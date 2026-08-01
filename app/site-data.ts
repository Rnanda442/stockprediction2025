import type { SiteSnapshot } from "./site-types";

export const REMOTE_SNAPSHOT_URL =
  "https://raw.githubusercontent.com/Rnanda442/stockprediction2025/main/public/data/latest-analysis.json";

export const fallbackSnapshot: SiteSnapshot = {
  schema_version: 1,
  generated_at: "2026-07-31T01:42:00+00:00",
  snapshot_fingerprint: "bundled-30587429387",
  source: {
    kind: "bundled_fallback",
    latest_run_id: "30587429387",
    latest_market_date: "2026-07-29",
    run_archives: 7,
    latest_archive_megabytes: 131.8,
  },
  trust: {
    status: "Research only",
    paper_ready_champions: 0,
    leakage_issue_rows: 0,
    paper_buy_evaluated: 1,
    reason:
      "The pipeline passed its compact checks, but champion discrimination is near random and probability skill is negative.",
  },
  key_metrics: {
    latest_run_id: "30587429387",
    latest_market_date: "2026-07-29",
    run_archives: 7,
    latest_archive_megabytes: 131.8,
    paper_ready_champions: 0,
    leakage_issue_rows: 0,
    paper_buy_evaluated: 1,
  },
  findings: [
    {
      priority: "P0",
      area: "model_trust",
      finding: "No champion clears every quality gate.",
      evidence:
        "Champion ROC AUC is close to 0.50 and Brier skill is negative across the current horizons.",
      next_step:
        "Improve calibration and walk-forward stability before using high-confidence ranks.",
    },
    {
      priority: "P1",
      area: "leakage_audit",
      finding: "Compact leakage checks pass.",
      evidence:
        "Train/test order, embargo, walk-forward, and metric-spike checks are currently clear.",
      next_step:
        "Add feature-availability timestamps so every input is proven prediction-time safe.",
    },
  ],
  model_actions: [
    {
      priority: "P0",
      component: "ranking_discrimination",
      recommended_change:
        "Run temporal feature ablations and compare every candidate with the simple logistic baseline.",
      evidence: "Current champion ROC AUC ranges from 0.500 to 0.511.",
      acceptance_test:
        "Walk-forward ROC AUC reaches at least 0.52 without test-set tuning.",
      execution: "review_required",
    },
    {
      priority: "P0",
      component: "probability_calibration",
      recommended_change:
        "Fit calibration inside training folds and cap confidence until it validates.",
      evidence: "Champion Brier skill is negative for all three horizons.",
      acceptance_test:
        "Out-of-sample Brier skill is non-negative and reliability buckets track outcomes.",
      execution: "review_required",
    },
    {
      priority: "P1",
      component: "paper_validation",
      recommended_change:
        "Keep decisions paper-only while buy, watch, and avoid outcomes mature.",
      evidence: "The current paper buy sample is too small for a stable conclusion.",
      acceptance_test:
        "At least 50 matching-horizon buy outcomes exist with baseline comparisons.",
      execution: "automated_by_osl",
    },
  ],
  charts: {
    model_gate_matrix: [
      {
        horizon_days: 5,
        model_name: "SGD logistic baseline",
        trust_tier: "watch",
        auc_gate: false,
        brier_gate: false,
        return_edge_gate: true,
        walk_forward_gate: false,
        sample_gate: true,
        roc_auc: 0.51072,
        brier_skill: -0.00871,
        selected_return_edge: 0.00511,
      },
      {
        horizon_days: 20,
        model_name: "SGD logistic baseline",
        trust_tier: "research_only",
        auc_gate: false,
        brier_gate: false,
        return_edge_gate: true,
        walk_forward_gate: false,
        sample_gate: true,
        roc_auc: 0.49992,
        brier_skill: -0.01441,
        selected_return_edge: 0.02038,
      },
      {
        horizon_days: 60,
        model_name: "Histogram gradient boosting",
        trust_tier: "watch",
        auc_gate: false,
        brier_gate: false,
        return_edge_gate: true,
        walk_forward_gate: false,
        sample_gate: true,
        roc_auc: 0.50834,
        brier_skill: -0.01163,
        selected_return_edge: 0.04893,
      },
    ],
    calibration_proxy: [],
    probability_signal_shape: [],
    paper_outcomes: [],
    leakage_audit: [],
    artifact_health: [
      {
        run_id: "30587429387",
        latest_market_date: "2026-07-29",
        megabytes_copied: 131.8,
        analysis_ready: true,
        compact_only: false,
      },
    ],
    feature_group_stability: [],
    model_score_weekly: [],
  },
  disclaimer:
    "Research and paper-decision review only. No live trading recommendation.",
};
