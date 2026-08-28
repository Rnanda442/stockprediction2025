export type Finding = {
  priority: string;
  area: string;
  finding: string;
  evidence: string;
  next_step: string;
};

export type ModelAction = {
  priority: string;
  component: string;
  recommended_change: string;
  evidence: string;
  acceptance_test: string;
  execution: string;
};

export type GateRow = {
  horizon_days?: number;
  model_name?: string;
  trust_tier?: string;
  auc_gate?: boolean | string;
  brier_gate?: boolean | string;
  return_edge_gate?: boolean | string;
  walk_forward_gate?: boolean | string;
  sample_gate?: boolean | string;
  roc_auc?: number;
  brier_skill?: number;
  selected_return_edge?: number;
  walk_forward_avg_score?: number;
  test_rows?: number;
};

export type CalibrationRow = {
  action?: string;
  horizon_days?: number;
  evaluation_horizon_days?: number;
  probability_bucket?: string;
  matching_horizon?: boolean | string;
  evaluated?: number;
  avg_probability_up?: number;
  observed_win_rate?: number;
  avg_return?: number;
  calibration_gap?: number;
};

export type ProbabilityRow = {
  horizon_days?: number;
  model_name?: string;
  rows?: number;
  avg_probability_up?: number;
  median_probability_up?: number;
  high_confidence_share?: number;
  extreme_share?: number;
};

export type PaperOutcomeRow = {
  action?: string;
  evaluation_horizon_days?: number;
  evaluated?: number;
  avg_return?: number;
  median_return?: number;
  win_rate?: number;
};

export type LeakageRow = {
  horizon_days?: number;
  model_name?: string;
  train_before_test?: boolean | string;
  embargo_matches_horizon?: boolean | string;
  has_walk_forward?: boolean | string;
  too_good_to_be_true_metric?: boolean | string;
  leakage_audit_status?: string;
};

export type ArtifactRow = {
  run_id?: string | number;
  latest_market_date?: string;
  megabytes_copied?: number;
  analysis_ready?: boolean | string;
  compact_only?: boolean | string;
};

export type RidgeDriverRow = {
  horizon_days?: number;
  target_name?: string;
  feature?: string;
  runs?: number;
  avg_coefficient?: number;
  std_coefficient?: number;
  avg_absolute_coefficient?: number;
  avg_test_r2?: number;
  avg_test_mae?: number;
  importance_rank?: number;
};

export type SimilarityPairRow = {
  A?: string;
  B?: string;
  runs?: number;
  observations?: number;
  avg_similarity?: number;
  std_similarity?: number;
  min_similarity?: number;
  max_similarity?: number;
};

export type ResidualValidationRow = {
  model?: string;
  label?: string;
  mean_auc?: number;
  mean_rank_ic?: number;
  mean_net_residual_return?: number;
  win_rate?: number;
  mean_turnover?: number;
  status?: string;
};

export type RegimeValidationRow = {
  regime?: string;
  mean_auc?: number;
  mean_rank_ic?: number;
  mean_net_residual_return?: number;
  win_rate?: number;
};

export type ExposurePolicyRow = {
  policy?: string;
  label?: string;
  active_day_share?: number;
  mean_daily_net_residual_return?: number;
  mean_path_cumulative_net_residual_return?: number;
  worst_path_cumulative_net_residual_return?: number;
  worst_maximum_drawdown?: number;
  mean_turnover?: number;
  delta_ci_lower?: number | null;
  delta_ci_upper?: number | null;
  status?: string;
};

export type PipelineNode = {
  id: string;
  stage: string;
  label: string;
  detail: string;
  status: "active" | "exploratory" | "unused" | "protected";
  feeds: string[];
};

export type PipelineArchitectureData = {
  version: number;
  generated_at: string;
  nodes: PipelineNode[];
};

export type SiteSnapshot = {
  schema_version: number;
  generated_at: string;
  snapshot_fingerprint: string;
  source: {
    kind: string;
    latest_run_id: string | number;
    latest_market_date: string;
    run_archives: number;
    latest_archive_megabytes: number | null;
  };
  trust: {
    status: string;
    paper_ready_champions: number;
    leakage_issue_rows: number;
    paper_buy_evaluated: number;
    reason: string;
  };
  key_metrics: Record<string, string | number | null>;
  findings: Finding[];
  model_actions: ModelAction[];
  charts: {
    model_gate_matrix: GateRow[];
    calibration_proxy: CalibrationRow[];
    probability_signal_shape: ProbabilityRow[];
    paper_outcomes: PaperOutcomeRow[];
    leakage_audit: LeakageRow[];
    artifact_health: ArtifactRow[];
    feature_group_stability: Record<string, unknown>[];
    model_score_weekly: Record<string, unknown>[];
    ridge_target_feature_stability?: RidgeDriverRow[];
    similarity_pair_stability?: SimilarityPairRow[];
    residual_validation?: ResidualValidationRow[];
    regime_diagnostics?: RegimeValidationRow[];
    exposure_policies?: ExposurePolicyRow[];
    pipeline_architecture?: PipelineArchitectureData;
  };
  disclaimer: string;
};
