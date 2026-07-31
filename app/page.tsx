import { champions, nextMoves, run, simulations, watchlist } from "./site-data";

function percent(value: number, digits = 1) {
  return `${(value * 100).toFixed(digits)}%`;
}

function signedPercent(value: number, digits = 1) {
  const formatted = percent(Math.abs(value), digits);
  return value >= 0 ? `+${formatted}` : `-${formatted}`;
}

function number(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

export default function Home() {
  const avgRoc =
    champions.reduce((total, item) => total + item.rocAuc, 0) /
    champions.length;
  const worstBrier = Math.min(...champions.map((item) => item.brierSkill));

  return (
    <main>
      <section className="topbar" aria-label="Run status">
        <div>
          <p className="eyebrow">Stockprediction2025</p>
          <h1>Personal Stock Research Command Center</h1>
          <p className="lede">
            Latest successful pipeline snapshot turned into a Sites-ready
            dashboard. Treat the model output as research until paper outcomes
            prove the signal.
          </p>
        </div>
        <div className="status-lockup" aria-label="Validation and trust">
          <span className="status status-ok">Validation {run.validation}</span>
          <span className="status status-warn">{run.modelTrust}</span>
        </div>
      </section>

      <section className="metric-grid" aria-label="Latest run metrics">
        <article className="metric">
          <span>Run</span>
          <strong>{run.runId}</strong>
          <small>{run.duration} / commit {run.commit}</small>
        </article>
        <article className="metric">
          <span>Market Date</span>
          <strong>{run.marketDate}</strong>
          <small>Checked {run.checkedAt}</small>
        </article>
        <article className="metric">
          <span>Coverage</span>
          <strong>{percent(run.coverage)}</strong>
          <small>Latest-date market coverage</small>
        </article>
        <article className="metric">
          <span>Model Rows</span>
          <strong>{number(run.predictionRows)}</strong>
          <small>{number(run.monteCarloRows)} Monte Carlo rows</small>
        </article>
      </section>

      <section className="band trust-band" aria-label="Model trust">
        <div>
          <p className="eyebrow">Honest read</p>
          <h2>Pipeline works. Model edge still needs proof.</h2>
        </div>
        <p>{run.trustReason}</p>
        <div className="trust-facts">
          <span>Avg ROC AUC {avgRoc.toFixed(3)}</span>
          <span>Worst Brier skill {worstBrier.toFixed(3)}</span>
          <span>Paper decisions only</span>
        </div>
      </section>

      <section className="split" aria-label="Champion models and watchlist">
        <div>
          <div className="section-heading">
            <p className="eyebrow">Champion models</p>
            <h2>Out-of-sample scoreboard</h2>
          </div>
          <div className="champion-list">
            {champions.map((item) => (
              <article className="champion" key={item.horizon}>
                <div>
                  <strong>{item.horizon}</strong>
                  <span>{item.model}</span>
                </div>
                <dl>
                  <div>
                    <dt>Accuracy</dt>
                    <dd>{percent(item.accuracy, 1)}</dd>
                  </div>
                  <div>
                    <dt>ROC</dt>
                    <dd>{item.rocAuc.toFixed(3)}</dd>
                  </div>
                  <div>
                    <dt>Brier skill</dt>
                    <dd className="negative">{item.brierSkill.toFixed(3)}</dd>
                  </div>
                  <div>
                    <dt>Selected return</dt>
                    <dd>{signedPercent(item.selectedReturn, 1)}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        </div>

        <div>
          <div className="section-heading">
            <p className="eyebrow">Top watchlist</p>
            <h2>Rule-ranked ideas to inspect</h2>
          </div>
          <div className="watch-table" role="table" aria-label="Top watchlist">
            <div className="watch-row watch-head" role="row">
              <span>Ticker</span>
              <span>Confidence</span>
              <span>Price</span>
              <span>Total return</span>
            </div>
            {watchlist.map((item) => (
              <div className="watch-row" role="row" key={item.ticker}>
                <strong>#{item.rank} {item.ticker}</strong>
                <span>{item.confidence.toFixed(1)}%</span>
                <span>${item.price.toFixed(2)}</span>
                <span className={item.totalReturn >= 0 ? "positive" : "negative"}>
                  {signedPercent(item.totalReturn, 1)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="band" aria-label="Monte Carlo research signals">
        <div className="section-heading">
          <p className="eyebrow">Monte Carlo research queue</p>
          <h2>High-probability names still need risk review</h2>
        </div>
        <div className="simulation-grid">
          {simulations.map((item) => (
            <article className="simulation" key={`${item.ticker}-${item.horizon}`}>
              <div className="simulation-title">
                <strong>{item.ticker}</strong>
                <span>{item.horizon}</span>
              </div>
              <div className="range" aria-label={`${item.ticker} return range`}>
                <span style={{ left: "12%" }}>{signedPercent(item.p10Return)}</span>
                <b style={{ left: "48%" }}>{signedPercent(item.medianReturn)}</b>
                <span style={{ left: "78%" }}>{signedPercent(item.p90Return)}</span>
              </div>
              <dl>
                <div>
                  <dt>Probability up</dt>
                  <dd>{percent(item.probabilityUp, 1)}</dd>
                </div>
                <div>
                  <dt>Drawdown chance</dt>
                  <dd>{percent(item.drawdownProbability, 1)}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      </section>

      <section className="next" aria-label="Next actions">
        <div className="section-heading">
          <p className="eyebrow">Next best moves</p>
          <h2>What to clean up before trusting the model more</h2>
        </div>
        <ol>
          {nextMoves.map((move) => (
            <li key={move}>{move}</li>
          ))}
        </ol>
      </section>
    </main>
  );
}
