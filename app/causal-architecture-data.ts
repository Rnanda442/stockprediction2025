import type { EvidenceStatus } from "./pipeline-variable-catalog";
import { RESEARCH_PROGRESS } from "./research-progress";

export type EdgeCategory = "main" | "pending" | "experimental" | "protected" | "visualization" | "blocked";
export type RouteFocus = "all" | "main" | "data" | "graph" | "validation" | "wrong";

export type ArchitectureNode = {
  id: string;
  title: string;
  subtitle: string;
  status: EvidenceStatus;
  x: number;
  y: number;
  variables: string[];
  detail: string;
  verdict: string;
  progressId?: string;
};

export type ArchitectureEdge = {
  id: string;
  from: string;
  to: string;
  label: string;
  category: EdgeCategory;
  routes: RouteFocus[];
  explanation: string;
};

const n = (id: string, title: string, subtitle: string, status: EvidenceStatus, x: number, y: number, variables: string[], detail: string, verdict: string): ArchitectureNode => ({ id, title, subtitle, status, x: id === "core" ? 1520 : x >= 620 ? x + 1220 : x, y, variables, detail, verdict });
const progressNode = (id: string, progressId: string, status: EvidenceStatus, x: number, y: number, variables: string[]): ArchitectureNode => {
  const item = RESEARCH_PROGRESS[progressId];
  return { id, progressId, title: item.title, subtitle: item.summary, status, x, y, variables, detail: item.evidence, verdict: item.doneWhen };
};
const e = (id: string, from: string, to: string, label: string, category: EdgeCategory, routes: RouteFocus[], explanation: string): ArchitectureEdge => ({ id, from, to, label, category, routes, explanation });

export const ARCHITECTURE_NODES: ArchitectureNode[] = [
  n("osl", "OSL research warehouse", "Bulk history and experiment artifacts", "supported", 40, 70, [], "OpenScienceLab stores the large research database, raw archives, and experiment outputs so the local computer and GitHub only carry compact reviewed artifacts.", "This is the active compute and storage origin."),
  n("raw", "Observed historical prices", "Price coverage is not verified historical membership", "mixed", 310, 70, ["ticker", "company_name", "sector", "begins_at", "close_price", "volume"], "The source spans nearly five years. The new stage reads only 2021-08-23 through 2026-05-28. All 2,597 tickers reach the final pre-holdout date; only seven enter after the source start. Current profiles are descriptive only.", "Usable observed prices, but fixed-survivor bias and historical identity coverage remain unresolved."),
  progressNode("membership", "universe_audit", "supported", 620, 70, ["ticker", "begins_at", "history_observations", "universe_eligible"]),
  progressNode("eligible_snapshot", "eligible_snapshot", "supported", 920, 70, ["close_price", "volume", "universe_eligible", "materialization_id", "warmup_prices_retained"]),
  progressNode("staged_loader", "loader_smoke", "mixed", 1220, 70, ["eligible_price_materialization_id", "sealed_holdout_start"]),
  { id: "decision_eligibility", title: "Decision-date eligibility", subtitle: "Apply eligibility AFTER trailing features; integration pending", status: "mixed", x: 1840, y: 70, variables: ["universe_eligible", "label_maturity"], detail: "Warm-up prices remain available for rolling calculations. Only then are decision rows filtered by universe_eligible; forward labels must mature before the pre-holdout cutoff. Source eligibility does not certify listing or delisting truth.", verdict: "Implemented in the staged input path, but awaiting the bounded panel smoke test.", progressId: "loader_smoke" },
  progressNode("security_master", "security_master", "supported", 310, 390, ["source_authority", "effective_date", "match_status"]),
  progressNode("lineage_sources", "lineage_sources", "harmful", 620, 390, ["listing_date", "delisting_date", "symbol_change", "sector_effective_date"]),
  { id: "legacy_join", title: "Old cross-database join", subtitle: "Superseded loading route, not a model failure", status: "harmful", x: 920, y: 390, variables: [], detail: "The previous read-only join was too slow for the bounded check. The streaming materialization now avoids this join during price loading. No model result was produced by that attempt.", verdict: "Superseded. Use the staged database and complete its integration check; do not rerun the slow join as the default path." },
  n("core", "Core feature factory", "Causal returns, risk, drawdown, and liquidity", "supported", 620, 70, ["daily_log_return", "dollar_volume", "ret_5d", "ret_20d", "ret_60d", "vol_20d", "vol_60d", "drawdown_60d", "dollar_vol_20d_log", "z_ma20", "risk_adjusted_momentum"], "Only current and earlier prices are used. Some produced variables are supported, while z_ma20, risk-adjusted momentum, and predictive use of log dollar volume are red in their tested forms.", "The factory is green; individual outputs retain their own evidence status below."),
  n("target", "Residual target construction", "Protected future label and market removal", "protected", 940, 70, ["future_return_5d", "universe_mean_return_5d", "residual_return_5d"], "The future five-day stock return is compared with the same-date eligible-universe mean to create a stock-specific residual target.", "These variables are legal training and evaluation labels only. They must never flow backward into prediction-time features."),
  n("models", "Frozen model families", "Ridge control, residual ANN, and lean volatility ANN", "mixed", 1250, 70, ["ridge_score", "ann_probability"], "Chronological training compares transparent linear controls with ANN candidates. The residual ANN is promising economically, but no family cleared every confirmation gate.", "Ridge is a required green control. ANN candidates remain gold and paper-only."),
  n("scores", "Same-date scores and ranks", "Comparable cross-sectional decisions", "supported", 1560, 70, ["decision_rank"], "Model outputs are ranked within each trading date so the system compares stocks with the contemporaneous eligible universe rather than treating scores as timeless absolute probabilities.", "This is the active bridge from models to paper decisions."),
  n("portfolio", "Paper portfolio engine", "Top-k selection, sleeves, turnover, and costs", "supported", 1870, 70, ["top_k_selection", "turnover", "transaction_cost_bps", "net_return", "excess_return", "maximum_drawdown"], "The engine creates blind top-k cohorts, overlapping sleeves, turnover, cost deductions, net outcomes, excess returns, and drawdown paths.", "Green means an active evaluation mechanism, not brokerage authorization."),
  n("validation", "Chronological validation", "Purged splits, embargo, placebo, and uncertainty", "supported", 2180, 70, ["auc", "brier", "log_loss", "ece_10", "rank_ic"], "Purged walk-forward splits, horizon-sized embargoes, within-date placebos, and date-block uncertainty protect against common leakage and dependence errors.", "This is the primary evidence gate before any final confirmation."),
  n("holdout", "Sealed final holdout", "60 untouched dates", "protected", 2490, 70, ["sealed_holdout"], "Dates 2026-05-29 through 2026-08-24 remain unavailable for feature, model, loss, threshold, cost, or policy selection.", "Protected until a frozen candidate earns one final promotion review."),

  n("graph", "Training-fitted similarity graph", "Correlations, neighbors, connectivity, and crowding", "mixed", 620, 390, ["graph_degree", "graph_similarity_mean", "neighbor_ret_20d", "neighbor_divergence"], "The graph is fit separately inside training windows. Graph degree is the strongest provisional survivor; unrestricted neighbor variables were noisy or harmful.", "Useful as a narrow hypothesis and visualization context, not as a broad ANN block."),
  n("latent", "Latent 3D geometry", "Training-fitted scaling, PCA, radius, and state clusters", "mixed", 940, 390, ["latent_x", "latent_y", "latent_z", "latent_radius", "state_cluster"], "Standardized core and graph variables are compressed into three descriptive coordinates. Only latent_z showed provisional positive importance.", "The geometry generates hypotheses. Visual separation is not predictive evidence."),
  n("motion", "Temporal motion factory", "Velocity, acceleration, curvature, persistence, and crowding", "mixed", 1250, 390, ["delta_latent_x", "delta_latent_y", "delta_latent_z", "latent_velocity", "latent_acceleration", "latent_path_curvature", "latent_radial_expansion", "neighbor_convergence_velocity", "graph_cluster_switch_count_20d", "graph_regime_residence_days", "crowding_change_5d"], "The time animation creates derivatives and regime descriptors. Crowding change and residence remain narrow hypotheses; the unrestricted kinematic block reduced after-cost value.", "Gold as a feature factory, red when passed wholesale into an ANN."),
  n("similarity_policy", "Similarity-weighted policy", "Modify paper weights using neighbor similarity", "mixed", 1560, 390, ["similarity_weight"], "Similarity weighting was strong in one robustness battery but negative in the later 400-date linear confirmation.", "Conflicting evidence. Keep gold until a materially different unseen test resolves it."),
  n("regime_policy", "Regime exposure policy", "Modify exposure using persistence or trend state", "mixed", 1870, 390, ["regime_exposure"], "Trend-down cash improved a point estimate, but the rule was selected post hoc and its confidence interval crossed zero.", "Exploratory and not promotable in its current form."),
  n("lab3d", "Four-view 3D market lab", "Market pulse, similarity, motion, and custom views", "mixed", 1560, 690, ["empirical_upside_probability_5d", "prediction_confidence_proxy"], "The lab maps variables to coordinates, sphere size, color, trails, sector identity, and Monte Carlo context. It is designed for discovery and explanation.", "Visualization-only until a derived variable improves pre-registered out-of-sample evidence."),
  n("paper", "Auditable paper decisions", "Selections, scores, assumptions, and later outcomes", "supported", 2180, 390, [], "Each paper decision preserves the date, ticker, model version, score, horizon, entry assumptions, costs, and eventual evaluation outcome.", "Active audit trail with no order placement."),
  n("context", "Accumulating context gate", "Evidence registry and do-not-repeat memory", "supported", 2180, 690, [], "Reviewed findings, rejected designs, approved-next experiments, metric definitions, and holdout policy accumulate here so future runs do not repeat failed work.", "Canonical research memory for planning and interpretation."),
  n("website", "Private research website", "Architecture, evidence, 3D lab, and paper summaries", "supported", 2490, 540, [], "The website receives compact reviewed summaries and interactive artifacts, not the entire OSL warehouse.", "Active private communication layer."),

  n("sector_gap", "Missing dated sector lineage", "Current profile is not historical point-in-time sector truth", "harmful", 310, 760, ["sector"], "A present-day sector label cannot safely define historical peer groups without effective dates and change history.", "Red because sector residualization is disconnected until a dated map exists."),
  n("broad_graph_ann", "Broad graph and motion ANN", "Unrestricted neighbor plus kinematic feature block", "harmful", 1250, 830, ["neighbor_ret_20d", "neighbor_divergence", "latent_velocity", "latent_acceleration", "latent_path_curvature", "latent_radial_expansion"], "Feeding the full graph and motion block into the ANN added unstable complexity and reduced after-cost value.", "Rejected in its tested form. Narrow survivor tests are still allowed."),
  n("leakage", "Leakage shortcut", "Fit transforms, graphs, or selection using later dates", "harmful", 940, 1030, [], "This wrong turn lets test or future information influence scaling, PCA, graph construction, feature selection, or labels.", "Always red. It invalidates evidence even when metrics improve."),
  n("early_holdout", "Open holdout too early", "Use sealed dates to choose a candidate or threshold", "harmful", 2180, 1030, ["sealed_holdout"], "Repeatedly checking the final dates converts the holdout into another tuning set.", "Always red until every design choice is frozen."),
  n("live_orders", "Live brokerage orders", "Automatic order placement", "harmful", 2490, 870, [], "Research scores and visual insights are not authorized to place trades. Authentication automation must not silently become execution automation.", "Blocked. The project remains paper-only."),
];

export const ARCHITECTURE_EDGES: ArchitectureEdge[] = [
  e("osl_raw", "osl", "raw", "read-only pre-holdout prices", "main", ["main", "data"], "The original source stays unchanged in OSL. Price coverage is observed, not authoritative point-in-time identity membership."),
  e("raw_membership", "raw", "membership", "past-only coverage audit", "main", ["main", "data"], RESEARCH_PROGRESS.universe_audit.evidence),
  e("membership_snapshot", "membership", "eligible_snapshot", "stream eligibility flags", "main", ["main", "data"], "Sorted ticker-date keys are merged in bounded batches, retaining both eligible and warm-up rows. No cross-database SQL join is needed."),
  e("raw_snapshot", "raw", "eligible_snapshot", "retain all warm-up prices", "main", ["data"], RESEARCH_PROGRESS.eligible_snapshot.evidence),
  e("snapshot_loader", "eligible_snapshot", "staged_loader", "pin staged input / test next", "pending", ["main", "data"], RESEARCH_PROGRESS.loader_smoke.summary),
  e("loader_core", "staged_loader", "core", "trailing history preserved", "pending", ["main", "data"], "The new loader must be exercised end-to-end. Trailing features are computed before filtering out ineligible decision rows."),
  e("core_eligibility", "core", "decision_eligibility", "features before filtering", "pending", ["main", "data"], "Keep early prices for rolling features. Apply the decision-date universe_eligible flag only after feature construction."),
  e("eligibility_target", "decision_eligibility", "target", "eligible, mature labels only", "protected", ["main", "data", "validation"], "Future residual labels are isolated from features and must mature before the exclusive 2026-05-29 cutoff. Integration is awaiting its bounded check."),
  e("eligibility_models", "decision_eligibility", "models", "new panel not yet exercised", "pending", ["main", "data"], "This intended route is not evidence of a model rerun. Complete the loader check and approve a unique comparison before training."),
  e("raw_master", "raw", "security_master", "observed / provisional identity", "main", ["data"], "Observed first and last prices can populate provisional coverage, not authoritative listing or delisting dates."),
  e("events_master", "lineage_sources", "security_master", "dated sources still missing", "blocked", ["data", "wrong"], RESEARCH_PROGRESS.lineage_sources.evidence),
  e("master_membership", "security_master", "membership", "identity certification blocked", "blocked", ["data", "wrong"], "The scaffold contains zero authoritative dated events. It cannot yet certify historical membership or resolve survivorship bias."),
  e("master_sector", "security_master", "sector_gap", "120 current profiles only", "blocked", ["data", "wrong"], "Current descriptive sector labels do not supply historical effective dates; sector residual targets remain blocked."),
  e("raw_legacy", "raw", "legacy_join", "superseded SQL join", "blocked", ["data", "wrong"], "The old loading route was too slow and is no longer the recommended input path."),
  e("membership_legacy", "membership", "legacy_join", "do not use as default", "blocked", ["wrong"], "Eligibility flags now enter a streaming materialization instead of a slow attached-database join."),
  e("snapshot_context", "eligible_snapshot", "context", "completion review pending", "pending", ["data"], RESEARCH_PROGRESS.context_review.evidence),
  e("core_target", "core", "target", "future evaluation labels", "protected", ["validation"], "Core dates and prices define where future labels are measured, but those labels remain isolated from prediction-time inputs."),
  e("target_models", "target", "models", "training label only", "protected", ["main", "validation"], "Residual future return supervises model fitting inside training windows. It is never included as an input column."),
  e("models_scores", "models", "scores", "out-of-sample scores", "main", ["main"], "Frozen models produce scores only on later chronological rows not used to fit them."),
  e("scores_portfolio", "scores", "portfolio", "rank and select", "main", ["main"], "Same-date ranks become top-k paper cohorts and overlapping sleeves."),
  e("portfolio_validation", "portfolio", "validation", "costed outcomes", "main", ["main", "validation"], "Net return, excess return, turnover, and drawdown join ranking and calibration metrics for review."),
  e("validation_holdout", "validation", "holdout", "eligible only after freeze", "protected", ["validation"], "The final holdout can be opened once, only after candidate, features, costs, loss, and policy are frozen."),
  e("validation_context", "validation", "context", "reviewed evidence", "main", ["main", "validation"], "Only reviewed results, caveats, and decisions enter the canonical context gate."),
  e("context_site", "context", "website", "compact research snapshot", "main", ["main"], "The private site publishes reviewed summaries and guardrails rather than bulk databases."),
  e("portfolio_paper", "portfolio", "paper", "decision audit", "main", ["main"], "Every paper selection records its inputs, assumptions, and later outcome."),
  e("paper_site", "paper", "website", "paper history", "main", ["main"], "Auditable paper decisions and evaluated outcomes feed the private research desk."),

  e("core_graph", "core", "graph", "training-window correlations", "experimental", ["graph"], "Return histories create a new similarity graph inside each training split."),
  e("graph_latent", "graph", "latent", "standardize and compress", "experimental", ["graph"], "Core and graph variables are robustly scaled and compressed into descriptive PCA coordinates."),
  e("latent_motion", "latent", "motion", "differentiate through time", "experimental", ["graph"], "Ticker trajectories across successive 3D frames create velocity, curvature, persistence, and crowding-change variables."),
  e("graph_models", "graph", "models", "narrow survivors only", "experimental", ["graph"], "Graph degree and pre-registered narrow survivors may enter controlled challengers. Raw neighbor blocks should not."),
  e("motion_broad", "motion", "broad_graph_ann", "unrestricted feature block", "blocked", ["wrong"], "This is the tested wrong turn: all kinematics and neighbor variables enter one flexible ANN."),
  e("graph_broad", "graph", "broad_graph_ann", "raw neighbor block", "blocked", ["wrong"], "Noisy neighbor return and divergence variables add unstable capacity to the broad ANN."),
  e("broad_scores", "broad_graph_ann", "scores", "rejected scores", "blocked", ["wrong"], "The tested broad model did not establish enough reliable value to feed paper ranking."),
  e("graph_similarity", "graph", "similarity_policy", "weighting modifier", "experimental", ["graph"], "Similarity changes portfolio weights after scoring rather than entering the ANN unrestricted."),
  e("similarity_portfolio", "similarity_policy", "portfolio", "modified paper weights", "experimental", ["graph"], "The weighting rule remains a gold research branch because confirmations conflict."),
  e("motion_regime", "motion", "regime_policy", "persistence and crowding", "experimental", ["graph"], "Narrow regime descriptors propose an exposure adjustment without changing the base score."),
  e("regime_portfolio", "regime_policy", "portfolio", "exposure multiplier", "experimental", ["graph"], "The post-hoc policy may alter paper exposure but cannot be promoted without future unseen confirmation."),

  e("raw_lab", "raw", "lab3d", "company and sector identity", "visualization", ["graph"], "Ticker profiles label spheres and color the market-pulse view. Sector is descriptive, not historical model truth."),
  e("core_lab", "core", "lab3d", "market-pulse geometry", "visualization", ["graph"], "Return, risk, drawdown, and liquidity variables control 3D position and sphere size."),
  e("graph_lab", "graph", "lab3d", "similarity geometry", "visualization", ["graph"], "Connectivity and neighbor similarity explain the graph-oriented view."),
  e("latent_lab", "latent", "lab3d", "latent coordinates", "visualization", ["graph"], "PCA coordinates position stocks in the similarity-space view."),
  e("motion_lab", "motion", "lab3d", "trails and motion color", "visualization", ["graph"], "Derived kinematics and regime variables animate trajectories and encode color or size."),
  e("lab_site", "lab3d", "website", "interactive artifact", "visualization", ["graph"], "The lab is published as an exploratory visual tool with explicit non-predictive guardrails."),

  e("raw_sector_gap", "raw", "sector_gap", "undated sector metadata", "blocked", ["wrong"], "Current sector metadata cannot safely reconstruct historical peer groups."),
  e("sector_target", "sector_gap", "target", "sector residual target blocked", "blocked", ["wrong"], "A sector-neutral residual target remains disconnected until dated classification history exists."),
  e("core_leak", "core", "leakage", "fit on all dates", "blocked", ["wrong"], "Using future rows to scale or select core variables leaks evaluation information."),
  e("graph_leak", "graph", "leakage", "fit graph on test dates", "blocked", ["wrong"], "A graph fitted with test-period returns contaminates every downstream neighbor and latent variable."),
  e("target_leak", "target", "leakage", "label enters features", "blocked", ["wrong"], "Future return or residual labels must never become prediction-time inputs."),
  e("leak_models", "leakage", "models", "inflated model evidence", "blocked", ["wrong"], "A leaked model can look excellent while being unusable on genuinely unseen dates."),
  e("holdout_early", "holdout", "early_holdout", "repeated final checks", "blocked", ["wrong", "validation"], "Opening the sealed dates to choose a feature, loss, cost, or threshold destroys final-test independence."),
  e("scores_live", "scores", "live_orders", "automatic execution", "blocked", ["wrong"], "A model score cannot directly authorize a brokerage order."),
  e("paper_live", "paper", "live_orders", "paper does not equal live", "blocked", ["wrong"], "Paper decisions remain simulations even when later outcomes are favorable."),
];

export const EDGE_CATEGORY_COPY: Record<EdgeCategory, { label: string; description: string }> = {
  main: { label: "Built research route", description: "Existing infrastructure or prior research flow. Green does not imply a new model run, certified historical membership, or live-trading approval." },
  pending: { label: "Integration pending", description: "An implemented or intended connection that still needs a bounded check or explicit evidence review. Not a completed end-to-end run." },
  experimental: { label: "Experimental branch", description: "A legal research hypothesis that remains mixed or unconfirmed." },
  protected: { label: "Protected label route", description: "Future outcomes and sealed confirmation data with one-way access rules." },
  visualization: { label: "Visualization-only route", description: "Data used for interpretation and hypothesis discovery, not predictive proof." },
  blocked: { label: "Blocked or wrong turn", description: "Leakage, rejected architecture, missing lineage, or prohibited execution." },
};

export const ARCHITECTURE_WIDTH = 3980;
export const ARCHITECTURE_HEIGHT = 1260;
