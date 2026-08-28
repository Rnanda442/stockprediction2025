"use client";

import { useEffect, useMemo, useState } from "react";
import { fallbackSnapshot, REMOTE_SNAPSHOT_URL } from "./site-data";
import type {
  ArtifactRow,
  CalibrationRow,
  GateRow,
  LeakageRow,
  PaperOutcomeRow,
  ProbabilityRow,
  RidgeDriverRow,
  SimilarityPairRow,
  SiteSnapshot,
} from "./site-types";

function asBool(value: boolean | string | undefined) {
  return value === true || ["true", "1", "yes"].includes(String(value).toLowerCase());
}

function finite(value: unknown, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function percent(value: unknown, digits = 1) {
  return `${(finite(value) * 100).toFixed(digits)}%`;
}

function signedPercent(value: unknown, digits = 1) {
  const number = finite(value);
  return `${number >= 0 ? "+" : "-"}${Math.abs(number * 100).toFixed(digits)}%`;
}

function shortDate(value: string) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: value.includes("T") ? "numeric" : undefined,
    minute: value.includes("T") ? "2-digit" : undefined,
    timeZoneName: value.includes("T") ? "short" : undefined,
  }).format(date);
}

function readableLabel(value: string | undefined) {
  if (!value) return "Unknown";
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function validSnapshot(value: unknown): value is SiteSnapshot {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<SiteSnapshot>;
  return (
    candidate.schema_version === 1 &&
    Boolean(candidate.source) &&
    Boolean(candidate.trust) &&
    Boolean(candidate.charts) &&
    Array.isArray(candidate.findings) &&
    Array.isArray(candidate.model_actions) &&
    Array.isArray(candidate.charts?.model_gate_matrix)
  );
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

const gateColumns: Array<{ key: keyof GateRow; label: string }> = [
  { key: "auc_gate", label: "ROC AUC" },
  { key: "brier_gate", label: "Brier" },
  { key: "return_edge_gate", label: "Return edge" },
  { key: "walk_forward_gate", label: "Walk-forward" },
  { key: "sample_gate", label: "Sample" },
];

function ModelGateMatrix({ rows }: { rows: GateRow[] }) {
  if (!rows.length) {
    return <EmptyState>No model gate rows have reached the compact snapshot yet.</EmptyState>;
  }
  return (
    <div className="matrix-scroll" aria-label="Model quality gate heatmap">
      <div className="gate-matrix">
        <div className="matrix-head matrix-model">Model / horizon</div>
        {gateColumns.map((column) => (
          <div className="matrix-head" key={column.key}>{column.label}</div>
        ))}
        {rows.slice(0, 16).flatMap((row, rowIndex) => [
          <div className="matrix-label" key={`label-${rowIndex}`}>
            <strong>{row.model_name || "Unknown model"}</strong>
            <span>
              {finite(row.horizon_days)}d / ROC {finite(row.roc_auc).toFixed(3)} / Brier {finite(row.brier_skill).toFixed(3)}
            </span>
          </div>,
          ...gateColumns.map((column) => {
            const passed = asBool(row[column.key] as boolean | string | undefined);
            return (
              <div className={`gate-cell ${passed ? "gate-pass" : "gate-fail"}`} key={`${rowIndex}-${column.key}`}>
                <span aria-hidden="true">{passed ? "PASS" : "FAIL"}</span>
                <span className="sr-only">{column.label} {passed ? "passed" : "failed"}</span>
              </div>
            );
          }),
        ])}
      </div>
    </div>
  );
}

function CalibrationPlot({ rows }: { rows: CalibrationRow[] }) {
  const points = rows
    .filter((row) => row.avg_probability_up != null && row.observed_win_rate != null)
    .filter((row) => row.matching_horizon == null || asBool(row.matching_horizon))
    .sort((a, b) => finite(a.avg_probability_up) - finite(b.avg_probability_up))
    .slice(0, 30);
  if (points.length < 2) {
    return <EmptyState>Calibration will appear after at least two matching-horizon probability buckets mature.</EmptyState>;
  }
  const left = 54;
  const top = 24;
  const width = 416;
  const height = 196;
  return (
    <div className="plot-wrap">
      <svg viewBox="0 0 500 260" role="img" aria-label="Predicted probability compared with observed paper win rate">
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const x = left + tick * width;
          const y = top + (1 - tick) * height;
          return (
            <g key={tick}>
              <line x1={left} y1={y} x2={left + width} y2={y} className="plot-grid" />
              <text x={left - 10} y={y + 4} textAnchor="end" className="plot-label">{Math.round(tick * 100)}%</text>
              <text x={x} y={top + height + 22} textAnchor="middle" className="plot-label">{Math.round(tick * 100)}%</text>
            </g>
          );
        })}
        <line x1={left} y1={top + height} x2={left + width} y2={top} className="ideal-line" />
        <polyline
          points={points.map((row) => `${left + finite(row.avg_probability_up) * width},${top + (1 - finite(row.observed_win_rate)) * height}`).join(" ")}
          className="calibration-line"
        />
        {points.map((row, index) => {
          const x = left + finite(row.avg_probability_up) * width;
          const y = top + (1 - finite(row.observed_win_rate)) * height;
          const radius = Math.min(9, 4 + Math.sqrt(Math.max(0, finite(row.evaluated))) / 2);
          return (
            <circle cx={x} cy={y} r={radius} className="calibration-point" key={`${row.probability_bucket}-${index}`}>
              <title>{`${row.probability_bucket || "bucket"}: predicted ${percent(row.avg_probability_up)}, observed ${percent(row.observed_win_rate)}, n=${finite(row.evaluated)}`}</title>
            </circle>
          );
        })}
        <text x={left + width / 2} y="256" textAnchor="middle" className="axis-title">Average predicted probability</text>
        <text transform="translate(14 122) rotate(-90)" textAnchor="middle" className="axis-title">Observed win rate</text>
      </svg>
    </div>
  );
}

function ProbabilityShape({ rows }: { rows: ProbabilityRow[] }) {
  const visible = [...rows]
    .sort((a, b) => finite(b.high_confidence_share) - finite(a.high_confidence_share))
    .slice(0, 10);
  if (!visible.length) {
    return <EmptyState>Probability-shape bars will appear when a latest model slice has enough rows.</EmptyState>;
  }
  return (
    <div className="bar-list" aria-label="Probability signal distribution">
      {visible.map((row, index) => (
        <div className="bar-row" key={`${row.model_name}-${row.horizon_days}-${index}`}>
          <div className="bar-label">
            <strong>{row.model_name || "Unknown"} / {finite(row.horizon_days)}d</strong>
            <span>n={finite(row.rows)}</span>
          </div>
          <div className="bar-track" title={`High confidence ${percent(row.high_confidence_share)}`}>
            <span className="bar-high" style={{ width: `${Math.min(100, finite(row.high_confidence_share) * 100)}%` }} />
          </div>
          <div className="bar-track" title={`Extreme confidence ${percent(row.extreme_share)}`}>
            <span className="bar-extreme" style={{ width: `${Math.min(100, finite(row.extreme_share) * 100)}%` }} />
          </div>
          <div className="bar-values"><span>{percent(row.high_confidence_share)}</span><span>{percent(row.extreme_share)}</span></div>
        </div>
      ))}
      <div className="bar-legend"><span className="legend-high">At least 70%</span><span className="legend-extreme">At least 90%</span></div>
    </div>
  );
}

function PaperOutcomes({ rows }: { rows: PaperOutcomeRow[] }) {
  if (!rows.length) {
    return <EmptyState>Paper outcomes are still too sparse for a stable comparison.</EmptyState>;
  }
  return (
    <div className="outcome-table" role="table" aria-label="Matured paper outcomes">
      <div className="outcome-row outcome-head" role="row"><span>Decision</span><span>n</span><span>Win rate</span><span>Avg return</span></div>
      {rows.slice(0, 18).map((row, index) => (
        <div className="outcome-row" role="row" key={`${row.action}-${row.evaluation_horizon_days}-${index}`}>
          <strong>{row.action || "Unknown"} / {finite(row.evaluation_horizon_days)}d</strong>
          <span>{finite(row.evaluated)}</span>
          <span>{percent(row.win_rate)}</span>
          <span className={finite(row.avg_return) >= 0 ? "tone-positive" : "tone-negative"}>{signedPercent(row.avg_return)}</span>
        </div>
      ))}
    </div>
  );
}

function LeakageAudit({ rows, issueCount }: { rows: LeakageRow[]; issueCount: number }) {
  const checks = rows.reduce(
    (totals, row) => ({
      train: totals.train + Number(asBool(row.train_before_test)),
      embargo: totals.embargo + Number(asBool(row.embargo_matches_horizon)),
      walk: totals.walk + Number(asBool(row.has_walk_forward)),
      spike: totals.spike + Number(!asBool(row.too_good_to_be_true_metric)),
    }),
    { train: 0, embargo: 0, walk: 0, spike: 0 },
  );
  const total = rows.length;
  return (
    <div className="leakage-summary">
      <div className={`leakage-verdict ${issueCount ? "verdict-review" : "verdict-clear"}`}>
        <strong>{issueCount ? `${issueCount} rows need review` : "Compact checks clear"}</strong>
        <span>Feature timestamp auditing remains a separate required control.</span>
      </div>
      {total ? (
        <div className="check-grid">
          <span>Train before test <b>{checks.train}/{total}</b></span>
          <span>Embargo aligned <b>{checks.embargo}/{total}</b></span>
          <span>Walk-forward present <b>{checks.walk}/{total}</b></span>
          <span>No metric spike <b>{checks.spike}/{total}</b></span>
        </div>
      ) : (
        <p className="small-note">Detailed audit rows will replace this aggregate when the live OSL snapshot arrives.</p>
      )}
    </div>
  );
}

function ArtifactHealth({ rows }: { rows: ArtifactRow[] }) {
  if (!rows.length) return <EmptyState>No archive-health rows are available.</EmptyState>;
  const visible = rows.slice(-10);
  const max = Math.max(1, ...visible.map((row) => finite(row.megabytes_copied)));
  return (
    <div className="archive-list" aria-label="Open Science Lab archive health">
      {visible.map((row, index) => {
        const ready = asBool(row.analysis_ready);
        return (
          <div className="archive-row" key={`${row.run_id}-${index}`}>
            <div><strong>{String(row.run_id || "unknown")}</strong><span>{row.latest_market_date || "No market date"}</span></div>
            <div className="archive-track"><span className={ready ? "archive-ready" : "archive-partial"} style={{ width: `${Math.max(2, finite(row.megabytes_copied) / max * 100)}%` }} /></div>
            <span>{finite(row.megabytes_copied).toFixed(1)} MB</span>
          </div>
        );
      })}
    </div>
  );
}

function RidgeDriverLens({ rows }: { rows: RidgeDriverRow[] }) {
  const strongest = new Map<string, RidgeDriverRow>();
  rows.forEach((row) => {
    const coefficient = Number(row.avg_coefficient);
    if (!row.target_name || !row.feature || !Number.isFinite(coefficient)) return;
    const key = `${row.target_name}-${finite(row.horizon_days)}`;
    const current = strongest.get(key);
    if (!current || Math.abs(coefficient) > Math.abs(finite(current.avg_coefficient))) {
      strongest.set(key, row);
    }
  });
  const targetOrder: Record<string, number> = { total_return: 0, upside_capture: 1, downside_risk: 2 };
  const visible = [...strongest.values()].sort((a, b) => {
    const horizonDifference = finite(b.horizon_days) - finite(a.horizon_days);
    if (horizonDifference) return horizonDifference;
    return (targetOrder[a.target_name || ""] ?? 9) - (targetOrder[b.target_name || ""] ?? 9);
  });
  if (!visible.length) {
    return <EmptyState>Ridge target drivers will appear after the next archived analysis-only run.</EmptyState>;
  }
  const coefficientMax = Math.max(...visible.map((row) => Math.abs(finite(row.avg_coefficient))), 0.0001);
  const bestR2 = Math.max(...rows.map((row) => finite(row.avg_test_r2, Number.NEGATIVE_INFINITY)));
  return (
    <div className="driver-lens">
      <div className={`evidence-note ${bestR2 < 0.05 ? "evidence-warning" : ""}`}>
        <strong>Best out-of-sample R2 {Number.isFinite(bestR2) ? bestR2.toFixed(3) : "unknown"}</strong>
        <span>{bestR2 < 0.05 ? "Use these coefficients to explain exposure, not as a standalone trade signal." : "Driver strength has cleared the exploratory evidence threshold."}</span>
      </div>
      <div className="driver-list" aria-label="Strongest standardized Ridge driver by target and horizon">
        {visible.map((row) => {
          const coefficient = finite(row.avg_coefficient);
          const width = Math.max(2, Math.abs(coefficient) / coefficientMax * 46);
          return (
            <article className="driver-row" key={`${row.target_name}-${row.horizon_days}`}>
              <div className="driver-name">
                <strong>{readableLabel(row.feature)}</strong>
                <span>{readableLabel(row.target_name)} / {finite(row.horizon_days)}d</span>
              </div>
              <div className="driver-axis" aria-label={`Coefficient ${coefficient.toFixed(4)}`}>
                <span className="driver-zero" />
                <span
                  className={`driver-fill ${coefficient >= 0 ? "driver-positive" : "driver-negative"}`}
                  style={{ left: coefficient >= 0 ? "50%" : `${50 - width}%`, width: `${width}%` }}
                />
              </div>
              <div className="driver-value">
                <strong>{coefficient >= 0 ? "+" : ""}{coefficient.toFixed(4)}</strong>
                <span>R2 {finite(row.avg_test_r2).toFixed(3)}</span>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}

function SimilarityExposure({ rows }: { rows: SimilarityPairRow[] }) {
  const visible = [...rows]
    .filter((row) => row.A && row.B && Number.isFinite(Number(row.avg_similarity)))
    .sort((a, b) => finite(b.runs) - finite(a.runs) || finite(b.avg_similarity) - finite(a.avg_similarity))
    .slice(0, 10);
  if (!visible.length) {
    return <EmptyState>Similarity exposure pairs will appear after the graph export is archived.</EmptyState>;
  }
  const repeated = rows.filter((row) => finite(row.runs) >= 3).length;
  return (
    <div className="similarity-exposure">
      <div className={`evidence-note ${repeated ? "" : "evidence-warning"}`}>
        <strong>{repeated} pairs repeated across 3+ runs</strong>
        <span>{repeated ? "Repeated links are stronger candidates for portfolio overlap review." : "Current links are exposure hypotheses until they recur in future runs."}</span>
      </div>
      <div className="similarity-list" aria-label="Most similar stock pairs">
        {visible.map((row, index) => (
          <article className="similarity-row" key={`${row.A}-${row.B}-${index}`}>
            <div className="pair-name"><strong>{row.A} / {row.B}</strong><span>{finite(row.runs)} {finite(row.runs) === 1 ? "run" : "runs"}</span></div>
            <div className="similarity-track"><span style={{ width: `${Math.min(100, finite(row.avg_similarity) * 100)}%` }} /></div>
            <strong className="similarity-value">{percent(row.avg_similarity)}</strong>
          </article>
        ))}
      </div>
      <p className="portfolio-rule"><strong>Portfolio rule:</strong> review high-similarity pairs before holding both at full size.</p>
    </div>
  );
}

export default function Home() {
  const [snapshot, setSnapshot] = useState<SiteSnapshot>(fallbackSnapshot);
  const [isLive, setIsLive] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${REMOTE_SNAPSHOT_URL}?v=${Date.now()}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("Snapshot unavailable");
        return response.json();
      })
      .then((value: unknown) => {
        if (!validSnapshot(value)) throw new Error("Invalid snapshot");
        setSnapshot(value);
        setIsLive(true);
      })
      .catch(() => {
        if (!controller.signal.aborted) setIsLive(false);
      });
    return () => controller.abort();
  }, []);

  const gateSummary = useMemo(() => {
    const rows = snapshot.charts.model_gate_matrix;
    const total = rows.length * gateColumns.length;
    const passed = rows.reduce(
      (sum, row) => sum + gateColumns.filter((column) => asBool(row[column.key] as boolean | string | undefined)).length,
      0,
    );
    return { passed, total };
  }, [snapshot]);

  const archiveMb = finite(snapshot.source.latest_archive_megabytes);
  const primaryAction = snapshot.model_actions[0];

  return (
    <main className="site-shell">
      <nav className="desk-nav" aria-label="Dashboard sections">
        <a className="brand-mark" href="#top" aria-label="Stock Research Desk home">
          <span>SP</span>
          <div><strong>Stock Research Desk</strong><small>OSL evidence system</small></div>
        </a>
        <div className="nav-links">
          <a href="#trust">Trust</a>
          <a href="#models">Models</a>
          <a href="#drivers">Drivers</a>
          <a href="#data-flow">Data flow</a>
          <a href="/market-lab/index.html">3D lab</a>
          <a href="#next-actions">Next actions</a>
        </div>
      </nav>

      <section className="hero" id="top">
        <div className="hero-copy">
          <p className="eyebrow">OSL-powered stock research</p>
          <h1>Keep the heavy data in OSL. Publish only the evidence.</h1>
          <p className="lede">This desk separates storage, analysis, and presentation. Open Science Lab retains the warehouse, GitHub carries a compact reviewed snapshot, and the website explains what is trustworthy before any signal is considered.</p>
          <div className="hero-actions">
            <a className="primary-link" href="#next-actions">See the next action</a>
            <a className="secondary-link" href="#data-flow">Follow the data</a>
            <a className="secondary-link" href="/market-lab/index.html">Open the 3D market lab</a>
          </div>
          <p className="hero-note">No raw run archive is downloaded by or served to this website.</p>
        </div>
        <aside className="verdict-card" aria-label="Current research verdict">
          <div className="verdict-card-head"><span>Evidence gate</span><span>{shortDate(snapshot.source.latest_market_date)}</span></div>
          <strong>{snapshot.trust.status}</strong>
          <p>{snapshot.trust.reason}</p>
          <div className="verdict-stats">
            <div><b>{snapshot.trust.paper_ready_champions}</b><span>paper-ready champions</span></div>
            <div><b>{snapshot.trust.paper_buy_evaluated}</b><span>matured paper buys</span></div>
            <div><b>{snapshot.trust.leakage_issue_rows}</b><span>leakage review rows</span></div>
          </div>
        </aside>
      </section>

      <section className="snapshot-bar" aria-label="Snapshot connection status">
        <div className="connection-line">
          <span className={`connection-dot ${isLive ? "dot-connected" : "dot-fallback"}`} aria-hidden="true" />
          <div><strong>{isLive ? "GitHub snapshot connected" : "Bundled snapshot in use"}</strong><span>Published {shortDate(snapshot.generated_at)}</span></div>
        </div>
        <p>Market evidence currently ends on <strong>{shortDate(snapshot.source.latest_market_date)}</strong>. Website polish and OSL transfer work can continue without a Robinhood refresh.</p>
      </section>

      <section className="metric-grid" aria-label="Latest snapshot metrics">
        <article className="metric"><span>Published run</span><strong>{String(snapshot.source.latest_run_id || "Unknown")}</strong><small>Compact evidence packet</small></article>
        <article className="metric"><span>OSL history</span><strong>{snapshot.source.run_archives} runs</strong><small>Archived outside the local computer</small></article>
        <article className="metric"><span>Quality gates</span><strong>{gateSummary.passed}/{gateSummary.total || 0}</strong><small>{snapshot.trust.paper_ready_champions} champions ready for paper review</small></article>
        <article className="metric"><span>Storage boundary</span><strong>Compact only</strong><small>{archiveMb ? `${archiveMb.toFixed(1)} MB latest raw archive stays in OSL` : "Raw archives remain in OSL"}</small></article>
      </section>

      <section className="data-flow-section" id="data-flow">
        <div className="section-heading"><div><p className="eyebrow">Storage architecture</p><h2>Large evidence in, small answers out</h2></div><p>The transfer path is intentionally one-way at publication time. Raw model artifacts remain in OSL; only reviewable summaries cross into GitHub and the website.</p></div>
        <div className="flow-grid">
          <article className="flow-card flow-osl"><span className="flow-index">01</span><span className="flow-label">Open Science Lab</span><strong>Warehouse</strong><p>Run archives, databases, Parquet files, model outputs, and long-term evidence.</p><small>{snapshot.source.run_archives} archived runs / {archiveMb ? `${archiveMb.toFixed(1)} MB latest archive` : "raw storage retained"}</small></article>
          <article className="flow-card flow-review"><span className="flow-index">02</span><span className="flow-label">Analysis pack</span><strong>Review layer</strong><p>Digest, quality gates, calibration summaries, small CSVs, charts, and the site snapshot.</p><small>Generated inside OSL</small></article>
          <article className="flow-card flow-site"><span className="flow-index">03</span><span className="flow-label">GitHub + Sites</span><strong>Research desk</strong><p>Website code and one compact JSON snapshot, designed for fast review on any device.</p><small>No raw warehouse dependency</small></article>
        </div>
        <div className="flow-rule"><strong>Boundary rule</strong><span>Code and compact evidence may leave OSL. Raw archives and credentials do not.</span></div>
      </section>

      <section className="trust-band" id="trust">
        <div><p className="eyebrow">Current verdict</p><h2>{snapshot.trust.status}</h2></div>
        <p>{snapshot.trust.reason}</p>
        <div className="trust-facts"><span>{snapshot.trust.leakage_issue_rows} leakage review rows</span><span>{snapshot.trust.paper_buy_evaluated} matured paper buys</span><span>{snapshot.model_actions.filter((action) => action.priority === "P0").length} P0 actions</span></div>
      </section>

      <section className="analysis-section" id="models">
        <div className="section-heading"><div><p className="eyebrow">Model quality</p><h2>Gate heatmap</h2></div><p>Every cell is an explicit promotion requirement. A strong return alone cannot override weak calibration or temporal validation.</p></div>
        <ModelGateMatrix rows={snapshot.charts.model_gate_matrix} />
      </section>

      <section className="two-column analysis-section">
        <div className="chart-panel">
          <div className="panel-heading"><p className="eyebrow">Calibration</p><h2>Predicted versus observed</h2></div>
          <CalibrationPlot rows={snapshot.charts.calibration_proxy} />
        </div>
        <div className="chart-panel">
          <div className="panel-heading"><p className="eyebrow">Confidence shape</p><h2>High and extreme probabilities</h2></div>
          <ProbabilityShape rows={snapshot.charts.probability_signal_shape} />
        </div>
      </section>

      <section className="analysis-section" id="drivers">
        <div className="section-heading"><div><p className="eyebrow">Decision context</p><h2>What moves each target, and what may overlap</h2></div><p>Ridge coefficients provide a signed linear explanation across return, upside, and downside targets. Similarity links flag holdings that may be expressing the same underlying exposure.</p></div>
        <div className="two-column driver-grid">
          <div className="chart-panel driver-panel">
            <div className="panel-heading"><p className="eyebrow">Ridge explanation</p><h2>Strongest driver by horizon</h2></div>
            <RidgeDriverLens rows={snapshot.charts.ridge_target_feature_stability || []} />
          </div>
          <div className="chart-panel driver-panel">
            <div className="panel-heading"><p className="eyebrow">Similarity graph</p><h2>Potential duplicate exposures</h2></div>
            <SimilarityExposure rows={snapshot.charts.similarity_pair_stability || []} />
          </div>
        </div>
      </section>

      <section className="two-column analysis-section">
        <div className="chart-panel">
          <div className="panel-heading"><p className="eyebrow">Leakage and bias</p><h2>Temporal safety checks</h2></div>
          <LeakageAudit rows={snapshot.charts.leakage_audit} issueCount={snapshot.trust.leakage_issue_rows} />
        </div>
        <div className="chart-panel">
          <div className="panel-heading"><p className="eyebrow">Paper evidence</p><h2>Matured outcomes</h2></div>
          <PaperOutcomes rows={snapshot.charts.paper_outcomes} />
        </div>
      </section>

      <section className="analysis-section" id="next-actions">
        <div className="section-heading"><div><p className="eyebrow">Recommended work</p><h2>Model and analysis action plan</h2></div><p>OSL generates these recommendations after each run. Model-code changes remain review-required; reporting and evidence collection can stay automatic.</p></div>
        {primaryAction ? (
          <div className="priority-callout">
            <div><span>Immediate research priority</span><strong>{primaryAction.component.replaceAll("_", " ")}</strong></div>
            <p>{primaryAction.recommended_change}</p>
            <small>Acceptance test: {primaryAction.acceptance_test}</small>
          </div>
        ) : null}
        <ol className="action-list">
          {snapshot.model_actions.map((action, index) => (
            <li key={`${action.component}-${index}`}>
              <div className="action-rank"><span>{action.priority}</span><b>{index + 1}</b></div>
              <div className="action-body"><strong>{action.component.replaceAll("_", " ")}</strong><p>{action.recommended_change}</p><small>{action.evidence}</small></div>
              <div className="acceptance"><span>Done when</span><p>{action.acceptance_test}</p><small>{action.execution.replaceAll("_", " ")}</small></div>
            </li>
          ))}
        </ol>
      </section>

      <section className="two-column analysis-section bottom-section">
        <div className="chart-panel">
          <div className="panel-heading"><p className="eyebrow">Run archive health</p><h2>What OSL retained</h2></div>
          <ArtifactHealth rows={snapshot.charts.artifact_health} />
        </div>
        <div className="chart-panel findings-panel">
          <div className="panel-heading"><p className="eyebrow">Automated review</p><h2>Latest findings</h2></div>
          <div className="finding-list">
            {snapshot.findings.slice(0, 6).map((finding, index) => (
              <article key={`${finding.area}-${index}`}><span>{finding.priority}</span><div><strong>{finding.finding}</strong><p>{finding.evidence}</p></div></article>
            ))}
          </div>
        </div>
      </section>

      <footer><span>{snapshot.disclaimer}</span><span>Bulk warehouse retained in Open Science Lab / Snapshot {snapshot.snapshot_fingerprint.slice(0, 10)}</span></footer>
    </main>
  );
}
