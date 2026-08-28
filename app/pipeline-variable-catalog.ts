export type EvidenceStatus = "supported" | "mixed" | "harmful" | "protected";

export type VariableNode = {
  id: string;
  label: string;
  stage: string;
  status: EvidenceStatus;
  formula: string;
  why: string;
  feeds: string[];
};

export const VARIABLE_STAGES = [
  { id: "identity", label: "Identity", note: "Keys and descriptive classifications" },
  { id: "raw", label: "Raw market data", note: "Point-in-time ResearchPrices inputs" },
  { id: "core", label: "Core features", note: "Returns, risk, drawdown, and liquidity" },
  { id: "graph", label: "Similarity graph", note: "Training-fitted connectivity and neighbors" },
  { id: "motion", label: "3D and motion", note: "Latent geometry and time derivatives" },
  { id: "model", label: "Targets and models", note: "Residual labels, predictions, and policies" },
  { id: "validation", label: "Portfolio and validation", note: "Paper outcomes, costs, and final checks" },
] as const;

const v = (
  id: string,
  label: string,
  stage: string,
  status: EvidenceStatus,
  formula: string,
  why: string,
  feeds: string[],
): VariableNode => ({ id, label, stage, status, formula, why, feeds });

export const VARIABLE_CATALOG: VariableNode[] = [
  v("ticker", "Ticker", "identity", "supported", "normalized uppercase symbol", "Required join key; it identifies a stock but is not itself a numeric predictor.", ["all ticker-level features"]),
  v("company_name", "Company name", "identity", "supported", "Nasdaq symbol -> security name", "Improves interpretation without changing scores; all 120 visual stocks are matched.", ["3D hover", "stock inspector"]),
  v("sector", "Sector", "identity", "mixed", "Nasdaq symbol -> sector", "Useful for color and future residualization, but historical point-in-time sector membership is not verified.", ["market-pulse color", "future sector residual target"]),
  v("begins_at", "Observation date", "identity", "supported", "trading-date timestamp", "Anchors chronological splits, embargoes, animation, and the sealed holdout.", ["splits", "date slider", "embargo"]),

  v("close_price", "Close price", "raw", "supported", "ResearchPrices.close_price at t", "Primary point-in-time input for returns, drawdowns, and path simulation.", ["daily_log_return", "returns", "drawdown", "dollar_volume"]),
  v("volume", "Volume", "raw", "supported", "ResearchPrices.volume at t", "Required for liquidity context and never used as a future label.", ["dollar_volume"]),

  v("daily_log_return", "Daily log return", "core", "supported", "ln(close_t / close_t-1)", "Causal additive return used by volatility, similarity, and Monte Carlo blocks.", ["volatility", "similarity graph", "Monte Carlo"]),
  v("dollar_volume", "Dollar volume", "core", "supported", "close_price x volume", "Required intermediate measure of tradability.", ["dollar_vol_20d_log"]),
  v("ret_5d", "5-day return", "core", "mixed", "close_t / close_t-5 - 1", "Useful short-momentum context, but no reliable standalone ranking value was established.", ["ANN", "ridge", "market pulse"]),
  v("ret_20d", "20-day momentum", "core", "supported", "close_t / close_t-20 - 1", "One of the stronger provisional contributors; removal hurt ranking and after-cost return in the robustness battery.", ["ANN", "ridge", "neighbor return"]),
  v("ret_60d", "60-day return", "core", "mixed", "close_t / close_t-60 - 1", "Longer momentum context has not cleared a standalone importance gate.", ["ANN", "ridge", "custom 3D"]),
  v("vol_20d", "20-day volatility", "core", "mixed", "std(log return, 20) x sqrt(252)", "Captures near-term risk, but evidence is less stable than the lean 60-day volatility candidate.", ["ANN", "ridge", "Monte Carlo regime"]),
  v("vol_60d", "60-day volatility", "core", "supported", "std(log return, 60) x sqrt(252)", "The lean volatility sleeve is the leading pre-holdout candidate, though not promoted.", ["lean volatility ANN", "risk view", "Monte Carlo regime"]),
  v("drawdown_60d", "60-day drawdown", "core", "mixed", "close_t / rolling_max_60 - 1", "Helped ranking in one lab, but later classification and return evidence was mixed.", ["ANN", "ridge", "market-pulse Z"]),
  v("dollar_vol_20d_log", "Log dollar volume", "core", "harmful", "ln(1 + median(dollar_volume, 20))", "Operationally useful for eligibility and bubble size, but noisy or mildly harmful as a predictor.", ["eligibility", "bubble size"]),
  v("z_ma20", "MA20 z-score", "core", "harmful", "robust_z(close / MA20 - 1)", "Added noise in the broad combination work and should not be repeated unchanged.", ["legacy broad ANN"]),
  v("risk_adjusted_momentum", "Risk-adjusted momentum", "core", "harmful", "ret_20d / stabilized vol_20d", "The tested form did not improve ranking and contributed noise.", ["legacy broad ANN"]),

  v("graph_degree", "Graph degree", "graph", "supported", "count(similarity >= threshold)", "The clearest provisional graph feature and a useful crowding proxy, but not promoted.", ["narrow graph block", "bubble size", "crowding change"]),
  v("graph_similarity_mean", "Mean neighbor similarity", "graph", "mixed", "mean(top-k positive correlations)", "Helped weighting in one battery but failed to confirm in the later 400-date linear test.", ["similarity weighting", "similarity color"]),
  v("neighbor_ret_20d", "Neighbor 20-day return", "graph", "harmful", "weighted_mean(neighbor ret_20d)", "Raw neighbor-return inputs were noisy or harmful in the unrestricted ANN block.", ["neighbor divergence", "legacy graph ANN"]),
  v("neighbor_divergence", "Neighbor divergence", "graph", "harmful", "neighbor_ret_20d - ret_20d", "Did not establish reliable incremental skill; retain as visualization context only.", ["convergence velocity", "custom 3D color"]),

  v("latent_x", "Latent X", "motion", "harmful", "PCA component 1", "Descriptive geometry only; no positive predictive importance was established.", ["similarity X", "delta X"]),
  v("latent_y", "Latent Y", "motion", "harmful", "PCA component 2", "Noisy or harmful in reviewed combination tests.", ["similarity Y", "delta Y"]),
  v("latent_z", "Latent Z", "motion", "mixed", "PCA component 3", "The only coordinate with provisional positive importance, but it remains unconfirmed.", ["similarity Z", "delta Z"]),
  v("latent_radius", "Latent radius", "motion", "harmful", "sqrt(x^2 + y^2 + z^2)", "Visually intuitive, but noisy or harmful as a predictor.", ["radial expansion"]),
  v("state_cluster", "Graph state cluster", "motion", "mixed", "KMeans(latent_x, latent_y, latent_z)", "Useful regime description, while the broad cluster design and diversification rule failed confirmation.", ["cluster switches", "regime residence"]),
  v("delta_latent_x", "Change in latent X", "motion", "harmful", "latent_x_t - latent_x_t-1", "Needed for motion calculation, but raw kinematics did not improve the broad model.", ["velocity", "curvature"]),
  v("delta_latent_y", "Change in latent Y", "motion", "harmful", "latent_y_t - latent_y_t-1", "Needed for motion calculation, but raw kinematics did not improve the broad model.", ["velocity", "curvature"]),
  v("delta_latent_z", "Change in latent Z", "motion", "harmful", "latent_z_t - latent_z_t-1", "Needed for motion calculation, but raw kinematics did not improve the broad model.", ["velocity", "curvature"]),
  v("latent_velocity", "Latent velocity", "motion", "harmful", "norm(delta_x, delta_y, delta_z)", "Useful visually, but the unrestricted motion block reduced after-cost value.", ["motion X", "acceleration"]),
  v("latent_acceleration", "Latent acceleration", "motion", "harmful", "velocity_t - velocity_t-1", "No stable out-of-sample value was established.", ["custom 3D"]),
  v("latent_path_curvature", "Path curvature", "motion", "harmful", "angle(delta_t, delta_t-1)", "Describes turning behavior but did not establish predictive value.", ["custom 3D"]),
  v("latent_radial_expansion", "Radial expansion", "motion", "harmful", "radius_t - radius_t-1", "Interesting visually, but broad motion additions reduced after-cost performance.", ["motion Y"]),
  v("neighbor_convergence_velocity", "Neighbor convergence", "motion", "harmful", "change in -|neighbor divergence|", "Did not survive as a narrow confirmed feature.", ["custom 3D"]),
  v("graph_cluster_switch_count_20d", "20-date cluster switches", "motion", "mixed", "rolling_sum(cluster changed, 20)", "Captures instability but did not confirm inside the full graph-survivor design.", ["motion sphere size"]),
  v("graph_regime_residence_days", "Regime residence", "motion", "mixed", "consecutive dates in current cluster", "A narrow hypothesis worth preserving, but weak in the later full-block confirmation.", ["motion Z", "regime exposure"]),
  v("crowding_change_5d", "5-date crowding change", "motion", "mixed", "graph_degree_t - graph_degree_t-5", "Best narrow graph addition, but the gain was small and uncertainty crossed zero.", ["motion color", "narrow challenger"]),

  v("empirical_upside_probability_5d", "Empirical upside rate", "model", "mixed", "rolling_mean(ret_5d > 0, 126)", "Historical context and visualization size only, not a promoted live probability.", ["confidence proxy", "bubble size"]),
  v("prediction_confidence_proxy", "Confidence proxy", "model", "mixed", "2 x |upside rate - 0.5|", "Distance from a historical base rate, not calibrated investment confidence.", ["custom bubble size"]),
  v("future_return_5d", "Future 5-day return", "model", "protected", "close_t+5 / close_t - 1", "Evaluation label protected by chronological splitting and a five-date embargo.", ["universe mean", "residual target", "paper outcome"]),
  v("universe_mean_return_5d", "Universe mean return", "model", "protected", "mean(future_return_5d within date)", "Target-side transformation that removes broad common movement; never a prediction-time feature.", ["residual target"]),
  v("residual_return_5d", "5-day residual target", "model", "protected", "future return - universe mean", "Current target for stock-specific relative ranking and strictly unavailable at decision time.", ["ANN label", "ridge label", "rank IC"]),
  v("ann_probability", "ANN score", "model", "mixed", "ANN(features) -> score/probability", "Residual ANN beat controls in some sleeves, but no feature family cleared every gate.", ["decision rank", "five-sleeve replay", "regime policy"]),
  v("ridge_score", "Ridge score", "model", "supported", "Ridge(features) -> residual score", "Transparent linear control required to justify ANN complexity.", ["decision rank", "validation metrics"]),
  v("similarity_weight", "Similarity weight", "model", "mixed", "base_weight x f(similarity)", "Strong in one battery and negative in a later 400-date confirmation.", ["weighted paper portfolio"]),
  v("regime_exposure", "Regime exposure", "model", "mixed", "base exposure x regime gate", "Trend-down cash improved a point estimate, but selection was post hoc and its interval crossed zero.", ["paper portfolio weight"]),

  v("decision_rank", "Decision rank", "validation", "supported", "rank(model score within date)", "Converts scores into comparable same-date paper selections.", ["top-k selection"]),
  v("top_k_selection", "Top-k paper selection", "validation", "supported", "highest ranks in eligible universe", "Creates auditable blind paper cohorts without enabling orders.", ["turnover", "paper outcomes"]),
  v("turnover", "Turnover", "validation", "supported", "fraction of holdings changed", "Required to prevent unrealistic gross-return conclusions.", ["transaction costs", "net return"]),
  v("transaction_cost_bps", "Transaction costs", "validation", "supported", "turnover x round-trip cost", "Tests whether apparent edge survives implementation friction.", ["net return"]),
  v("net_return", "Net paper return", "validation", "supported", "gross return - costs", "Primary paper economic outcome after friction.", ["excess return", "maximum drawdown"]),
  v("excess_return", "Excess return", "validation", "supported", "net return - eligible-universe return", "Separates stock selection from broad market movement.", ["bootstrap", "promotion review"]),
  v("maximum_drawdown", "Maximum drawdown", "validation", "supported", "min(wealth / running peak - 1)", "Prevents average return from hiding severe path risk.", ["promotion review"]),
  v("auc", "AUC", "validation", "supported", "ROC AUC", "Direction-ranking metric; 0.5 is random and uncertainty must be date-blocked.", ["promotion review"]),
  v("brier", "Brier score", "validation", "supported", "mean((probability - outcome)^2)", "Probability-error metric interpreted against the target base rate.", ["promotion review"]),
  v("log_loss", "Log loss", "validation", "supported", "negative Bernoulli log likelihood", "Penalizes confident probability errors.", ["promotion review"]),
  v("ece_10", "10-bin ECE", "validation", "supported", "weighted calibration gap across 10 bins", "Checks whether probabilities align with observed frequencies.", ["promotion review"]),
  v("rank_ic", "Rank IC", "validation", "supported", "corr(score, residual_return_5d)", "Measures cross-sectional ranking alignment with residual outcomes.", ["promotion review"]),
  v("sealed_holdout", "Sealed 60-date holdout", "validation", "protected", "2026-05-29 through 2026-08-24", "Untouched final test; never open it to select features, losses, thresholds, or policies.", ["final promotion review"]),
];
