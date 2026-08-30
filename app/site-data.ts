import type { PipelineArchitectureData, SiteSnapshot } from "./site-types";
import { PROGRESS_REVIEW_DATE, RESEARCH_PROGRESS } from "./research-progress";

export const REMOTE_SNAPSHOT_URL =
  "https://raw.githubusercontent.com/Rnanda442/stockprediction2025/main/public/data/latest-analysis.json";

export const pipelineArchitecture: PipelineArchitectureData = {
  version: 1,
  generated_at: PROGRESS_REVIEW_DATE,
  nodes: [
    { id: "historical_prices", stage: "Inputs", label: "Historical ResearchPrices", detail: "Nearly five years of observed prices; historical listing and delisting coverage is not certified.", status: "active", feeds: ["research_history_db"] },
    { id: "robinhood_manual", stage: "Inputs", label: "Robinhood manual refresh", detail: "Source refresh is usable when authorization is completed manually.", status: "active", feeds: ["research_history_db"] },
    { id: "robinhood_auto", stage: "Inputs", label: "Automatic Robinhood login", detail: "No durable unattended authentication flow is connected.", status: "unused", feeds: [] },
    { id: "dated_sector_map", stage: "Inputs", label: "Dated sector membership", detail: "Required for a leakage-safe sector residual target, but currently missing.", status: "unused", feeds: ["sector_residual_target"] },
    { id: "research_history_db", stage: "OSL warehouse", label: "research_history.db", detail: "Original source retained unchanged in OSL. Only pre-holdout rows entered the new snapshot.", status: "active", feeds: ["universe_audit", "eligible_snapshot", "compact_snapshot"] },
    { id: "security_master_scaffold", stage: "OSL warehouse", label: RESEARCH_PROGRESS.security_master.title, detail: RESEARCH_PROGRESS.security_master.evidence, status: "active", feeds: ["historical_lineage"] },
    { id: "eligible_snapshot", stage: "OSL warehouse", label: RESEARCH_PROGRESS.eligible_snapshot.title, detail: RESEARCH_PROGRESS.eligible_snapshot.evidence, status: "active", feeds: ["staged_panel_loader"] },
    { id: "raw_archives", stage: "OSL warehouse", label: "Raw run archives", detail: "Large databases, Parquet files, and model artifacts remain in OSL.", status: "active", feeds: ["research_history_db"] },
    { id: "sealed_holdout", stage: "OSL warehouse", label: "Sealed holdout", detail: "Dates beginning 2026-05-29 are intentionally protected from exploratory tuning.", status: "protected", feeds: ["future_confirmation"] },
    { id: "staged_panel_loader", stage: "Transforms", label: "New staged loader: awaiting check", detail: RESEARCH_PROGRESS.loader_smoke.summary, status: "exploratory", feeds: ["point_in_time_panel"] },
    { id: "point_in_time_panel", stage: "Transforms", label: "Prior research panel / new input pending", detail: "Prior study: 1.131M rows, 1,131 decision dates, 1,676 tickers. Do not confuse this with the 2,951,410 eligible source rows; a new panel has not been run.", status: "active", feeds: ["market_residual_target", "feature_families"] },
    { id: "market_residual_target", stage: "Transforms", label: "5-day market residual target", detail: "Removes the equal-weight market move before model training and evaluation.", status: "active", feeds: ["lean_volatility_ann", "full_residual_ann", "controls"] },
    { id: "feature_families", stage: "Transforms", label: "Feature-family matrix", detail: "Momentum, volatility, liquidity, and training-only regime context.", status: "active", feeds: ["lean_volatility_ann", "full_residual_ann"] },
    { id: "sector_residual_target", stage: "Transforms", label: "Sector residual target", detail: "Not generated because dated sector ownership is unavailable.", status: "unused", feeds: [] },
    { id: "graph_motion_features", stage: "Transforms", label: "Similarity + 3D motion features", detail: "Built and visualized, but not connected to the current residual ANN candidate.", status: "unused", feeds: ["temporal_3d_lab"] },
    { id: "lean_volatility_ann", stage: "Models", label: "Lean volatility ANN", detail: "Leading frozen candidate: 0.696 AUC and +0.622% mean net residual return per cohort.", status: "active", feeds: ["purged_walk_forward"] },
    { id: "full_residual_ann", stage: "Models", label: "Full residual ANN", detail: "Research baseline using all four feature families and two random seeds.", status: "active", feeds: ["purged_walk_forward", "regime_policies"] },
    { id: "controls", stage: "Models", label: "Linear and random controls", detail: "Momentum, logistic, removal, shuffle, and random controls prevent false promotion.", status: "active", feeds: ["purged_walk_forward"] },
    { id: "similarity_model", stage: "Models", label: "Similarity / 3D prediction model", detail: "Graph-motion variables have not entered the frozen residual candidate architecture.", status: "unused", feeds: [] },
    { id: "purged_walk_forward", stage: "Validation", label: "Purged walk-forward folds", detail: "Four temporal folds enforce training evaluation dates before each test period.", status: "active", feeds: ["five_sleeve", "uncertainty"] },
    { id: "five_sleeve", stage: "Validation", label: "Five-sleeve capital replay", detail: "Tracks overlapping positions, turnover, missing returns, drawdown, and transaction costs.", status: "active", feeds: ["context_gate"] },
    { id: "uncertainty", stage: "Validation", label: "Bootstrap + cost controls", detail: "Weekly-block confidence intervals and 0-40 bps cost sensitivity.", status: "active", feeds: ["context_gate"] },
    { id: "regime_policies", stage: "Validation", label: "Regime exposure policies", detail: "Stress-only and trend-down cash rules are post-selection hypotheses awaiting future data.", status: "exploratory", feeds: ["future_confirmation"] },
    { id: "universe_audit", stage: "Validation", label: RESEARCH_PROGRESS.universe_audit.title, detail: RESEARCH_PROGRESS.universe_audit.evidence, status: "active", feeds: ["eligible_snapshot", "historical_lineage"] },
    { id: "historical_lineage", stage: "Validation", label: "Authoritative historical lineage missing", detail: RESEARCH_PROGRESS.lineage_sources.evidence, status: "unused", feeds: ["future_confirmation"] },
    { id: "future_confirmation", stage: "Validation", label: "Future unseen confirmation", detail: "Frozen candidates wait for newly arriving dates without retuning.", status: "exploratory", feeds: ["context_gate"] },
    { id: "context_gate", stage: "Outputs", label: "Accumulated context gate", detail: "Stores reviewed experiments and do-not-repeat rules. The new materialization completion candidate still needs review and merge.", status: "active", feeds: ["compact_snapshot", "research_site"] },
    { id: "compact_snapshot", stage: "Outputs", label: "Compact GitHub snapshot", detail: "Only reviewed summaries leave OSL; raw warehouse artifacts remain remote.", status: "active", feeds: ["research_site"] },
    { id: "research_site", stage: "Outputs", label: "Private research website", detail: "Current evidence, model status, architecture, and next actions.", status: "active", feeds: [] },
    { id: "temporal_3d_lab", stage: "Outputs", label: "Temporal 3D stock lab", detail: "Published and interactive, but disconnected from the newest ANN and policy outputs.", status: "unused", feeds: [] },
    { id: "paper_decisions", stage: "Outputs", label: "Paper-decision monitoring", detail: "Maturing outcomes remain research-only while candidates are validated.", status: "exploratory", feeds: ["context_gate"] },
    { id: "live_orders", stage: "Outputs", label: "Live brokerage orders", detail: "Explicitly disabled. No model can place trades.", status: "unused", feeds: [] }
  ],
};

export const fallbackSnapshot: SiteSnapshot = {
  schema_version: 1,
  generated_at: "2026-08-28T17:50:01+00:00",
  snapshot_fingerprint: "bundled-regime-exposure-policy-v1",
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
      "The residual ANN has strong discrimination and after-cost edge, but feature-family certainty, fold stability, and point-in-time universe controls still block promotion.",
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
    residual_validation: [
      { model: "baseline_plus_volatility", label: "Lean volatility ANN", mean_auc: 0.696413, mean_rank_ic: 0.044771, mean_net_residual_return: 0.00622, win_rate: 0.619048, mean_turnover: 0.134683, status: "Leading candidate" },
      { model: "all_families", label: "Full residual ANN", mean_auc: 0.694487, mean_rank_ic: 0.039763, mean_net_residual_return: 0.005656, win_rate: 0.615079, mean_turnover: 0.14754, status: "Promising, unstable" },
      { model: "logistic_all_families", label: "Logistic control", mean_auc: 0.659389, mean_rank_ic: 0.024867, mean_net_residual_return: 0.000649, win_rate: 0.515873, mean_turnover: 0.118016, status: "Control" },
      { model: "random_top_50", label: "Random control", mean_auc: 0.497043, mean_rank_ic: -0.001345, mean_net_residual_return: -0.000494, win_rate: 0.440476, mean_turnover: 0.950794, status: "Control" },
    ],
    regime_diagnostics: [
      { regime: "stress", mean_auc: 0.716611, mean_rank_ic: 0.086321, mean_net_residual_return: 0.013618, win_rate: 0.730337 },
      { regime: "rotation_quiet", mean_auc: 0.688651, mean_rank_ic: 0.029451, mean_net_residual_return: 0.001983, win_rate: 0.56962 },
      { regime: "trend_up", mean_auc: 0.672711, mean_rank_ic: -0.021728, mean_net_residual_return: 0.001292, win_rate: 0.55 },
      { regime: "trend_down", mean_auc: 0.695653, mean_rank_ic: 0.109425, mean_net_residual_return: -0.00241, win_rate: 0.464286 },
    ],
    exposure_policies: [
      { policy: "ann_all_regimes", label: "ANN / all regimes", active_day_share: 1, mean_daily_net_residual_return: 0.001033, mean_path_cumulative_net_residual_return: 0.068586, worst_path_cumulative_net_residual_return: -0.083343, worst_maximum_drawdown: -0.126111, mean_turnover: 0.082397, delta_ci_lower: null, delta_ci_upper: null, status: "Research baseline" },
      { policy: "ann_stress_only", label: "Stress-only exposure", active_day_share: 0.343838, mean_daily_net_residual_return: 0.000834, mean_path_cumulative_net_residual_return: 0.055388, worst_path_cumulative_net_residual_return: 0, worst_maximum_drawdown: -0.055392, mean_turnover: 0.034774, delta_ci_lower: -0.000812, delta_ci_upper: 0.000386, status: "Risk-control only" },
      { policy: "ann_trend_down_cash", label: "Cash during trend-down", active_day_share: 0.948529, mean_daily_net_residual_return: 0.001187, mean_path_cumulative_net_residual_return: 0.078944, worst_path_cumulative_net_residual_return: -0.030716, worst_maximum_drawdown: -0.094342, mean_turnover: 0.108934, delta_ci_lower: -0.000085, delta_ci_upper: 0.000426, status: "Promising, unconfirmed" },
    ],
    pipeline_architecture: pipelineArchitecture,
  },
  disclaimer:
    "Research and paper-decision review only. No live trading recommendation.",
};
