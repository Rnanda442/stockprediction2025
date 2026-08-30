import { COMPLETED_PROGRESS, NEXT_PROGRESS, PRICE_STAGE, PROGRESS_REVIEW_DATE, PROGRESS_STATUS_LABEL, RESEARCH_PROGRESS } from "./research-progress";
import styles from "./research-next-steps.module.css";

const number = (value: number) => value.toLocaleString("en-US");

export default function ResearchNextSteps() {
  return <section className="analysis-section" id="next-actions">
    <div className="section-heading"><div><p className="eyebrow">Reviewed progress / {PROGRESS_REVIEW_DATE}</p><h2>Next steps, without repeating old work</h2></div><p>This plan tracks reviewed infrastructure progress separately from the older model-results snapshot. A completed data step does not mean a validated prediction.</p></div>
    <div className={styles.notice}><strong>The next run is a loader check, not another ANN sweep.</strong><p>First record the completed stage in the context gate, then test the new input path. No Robinhood approval or bulk download to your computer is needed for these steps.</p><a href="#architecture-node-staged_loader">Locate the next connection in the diagram</a></div>
    <div className={styles.stats} aria-label="Ready pre-holdout price snapshot">
      <article><span>Eligible decision rows</span><strong>{number(PRICE_STAGE.eligibleRows)}</strong><small>Within {number(PRICE_STAGE.rows)} stored rows</small></article>
      <article><span>Warm-up retained</span><strong>{number(PRICE_STAGE.warmupRows)}</strong><small>Features calculated before eligibility filtering</small></article>
      <article><span>OSL snapshot</span><strong>{Math.round(PRICE_STAGE.bytes / 1e6)} MB</strong><small>{number(PRICE_STAGE.tickers)} tickers; no warehouse download</small></article>
    </div>
    <p className={styles.scope}>The source archive spans nearly five years. This stage uses only <strong>{PRICE_STAGE.firstDate} to {PRICE_STAGE.lastDate}</strong>. Historical identity coverage, not simply another price refresh, is the current data gap.</p>
    <h3 className={styles.groupTitle}>Completed, within the stated scope</h3>
    <div className={styles.completed}>{COMPLETED_PROGRESS.map((id) => {
      const item = RESEARCH_PROGRESS[id];
      return <article key={id}><span className={styles.badge} data-status={item.status}>{PROGRESS_STATUS_LABEL[item.status]}</span><h4>{item.title}</h4><p>{item.summary}</p><small>{item.evidence}</small><a href={`#architecture-node-${item.node}`}>Inspect this system</a></article>;
    })}</div>
    <h3 className={styles.groupTitle}>Ordered action plan</h3>
    <ol className={styles.steps}>{NEXT_PROGRESS.map((id, index) => {
      const item = RESEARCH_PROGRESS[id];
      return <li key={id} id={`research-step-${id}`} data-status={item.status}>
        <div className={styles.rank}>{String(index + 1).padStart(2, "0")}</div>
        <div><span className={styles.badge} data-status={item.status}>{PROGRESS_STATUS_LABEL[item.status]}</span><h4>{item.title}</h4><p>{item.summary}</p><a href={`#architecture-node-${item.node}`}>Show the connected diagram box</a><details><summary>Evidence and source</summary><p>{item.evidence}</p><code>{item.source}</code></details></div>
        <div className={styles.acceptance}><strong>Done when</strong><p>{item.doneWhen}</p></div>
      </li>;
    })}</ol>
    <p className={styles.scope}>Green means the named task is complete, not that a stock is a good buy. Amber means untested or dependent work. Red means missing required data, not negative stock performance. The sealed holdout stays protected.</p>
  </section>;
}
