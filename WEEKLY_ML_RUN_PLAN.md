# Weekly ML Run Plan

Goal: run the stock pipeline every day for one market week, compare model
behavior by horizon, and choose better model/decision weights from evidence
instead of one lucky leaderboard.

## What Runs

Default candidates:

```text
sgd_logistic,mlp_ann,hist_gradient_boosting
```

Default horizons:

```text
5d, 20d, 60d
```

Main workflow:

```text
GitHub Actions -> Run Stock Pipeline
```

Automation:

```text
Weekdays after U.S. market close, plus manual dispatch when needed
```

Each run refreshes data, rebuilds the model tournament, exports dashboard data,
records automatic paper decisions, updates matured paper outcomes, and uploads
the `stock-analysis-outputs` artifact.

## Daily Loop

Run once per market day after fresh prices are available. Weekend days are for
reviewing matured outcomes, not forcing new no-data model changes.

1. Let the scheduled `Run Stock Pipeline` action run, or start it manually with
   all three model candidates enabled.
2. Confirm the artifact has `dashboard_data.db`, model CSVs, paper decisions,
   and validation output.
3. Open Streamlit and check the first-page run timestamp, pipeline health, model
   champions, and the daily decision board.
4. Record the top paper actions only. Do not treat them as real trades yet.
5. Save notes on where the candidates disagree most.

## What To Compare

Model quality:

- Champion by horizon
- `champion_score`
- Accuracy
- Brier score
- ROC AUC
- Average return of high-confidence picks
- Number of eligible predictions

Decision quality:

- Win rate by action and horizon
- Average return by action and horizon
- Drawdown or worst observed return
- How often a ticker stays ranked across days
- Whether the model agrees with shortlist/watchlist signals

Feature/weight quality:

- Top feature weights for `sgd_logistic`
- Feature importance stability for `hist_gradient_boosting`
- ANN performance versus simpler models
- Features that stay useful across multiple horizons
- Features that flip sign or importance too often

## Week Schedule

Day 1: Baseline

- Run the default model tournament.
- Do not change weights.
- Mark the champion per horizon as the starting benchmark.

Day 2: Stability

- Run the same settings.
- Compare champion changes from Day 1.
- Flag any model that wins once but has weak calibration or low coverage.

Day 3: Confidence Thresholds

- Keep the same models.
- Compare paper decisions at probability bands: `55-60%`, `60-65%`, `65%+`.
- Prefer fewer stronger ideas if lower bands are noisy.

Day 4: Horizon Fit

- Compare which tickers look best at `5d`, `20d`, and `60d`.
- Separate quick swing ideas from slower trend ideas.
- Do not force one model to rule every horizon.

Day 5: Weight Review

- Review feature weights/importances across the week.
- Increase decision weight for signals that were stable and profitable.
- Reduce decision weight for unstable or overconfident signals.

Weekend Review

- Wait for any matured `5d` paper outcomes.
- Pick provisional weights for the next week.
- Write down which model/horizon combinations are trusted, watched, or blocked.

## Promotion Rules

Promote a model/horizon only if it passes all of these:

- It wins or nearly wins on more than one day.
- It has acceptable calibration, not just high accuracy.
- It produces enough predictions to be useful.
- Its top picks do not all come from one fragile sector or one repeated ticker.
- Paper outcomes are at least directionally better than the baseline watchlist.

Block or downgrade a model/horizon if:

- It wins only through a tiny sample.
- Its confidence is high while paper outcomes are weak.
- Its feature importance changes wildly every day.
- It repeatedly recommends names that fail liquidity or risk constraints.

## Next Engineering Moves

1. Save every run's `ModelEvaluation` rows into a persistent run-history table.
2. Add a weekly model scorecard in Streamlit with day-by-day champion movement.
3. Add probability-band analysis for paper decisions.
4. Add model blending weights by horizon:
   - `5d`: favor recent performance and calibration
   - `20d`: balance calibration, return, and stability
   - `60d`: favor trend stability and drawdown control
5. Add an experiment config so each week can test one controlled change only.

## Decision Rule

The best move is not the highest probability row. The best move is the ticker
where model confidence, watchlist quality, liquidity, portfolio fit, and recent
paper outcome evidence all agree.

## How To Judge A Completed Run

Use this after each GitHub Actions run finishes. The goal is to decide what the
run proves, what it does not prove, and what the next best move is.

Capability map:

- Confirm the chain completed: Robinhood data, ticker filters, historical
  database, vector features, similarity families, shortlist, model tournament,
  Monte Carlo, paper decisions, dashboard export, and validation.
- Check which output files and dashboard tables changed.
- If the notebook was slow, inspect stage timings before changing model code.

Model trust rubric:

- Trust a signal only when it beats a simple baseline out of sample.
- Prefer models that are stable across walk-forward splits, not just one holdout.
- Treat weak ROC AUC or negative Brier skill as a do-not-trust warning even when
  accuracy looks acceptable.
- Require enough rows, clear train/test dates, acceptable downside risk, and
  paper outcome confirmation before raising decision weights.

Post-run triage:

- First check validation status, data freshness, and latest market coverage.
- Then compare champions by horizon and ask whether ANN beat the simpler models.
- Review Monte Carlo downside and target probabilities for the top candidates.
- Separate "pipeline passed" from "model is trustworthy"; those are different
  claims.

Next UI decisions:

- Make the first page answer: best opportunities, highest risk, model
  confidence, what changed, and why each ticker surfaced.
- Show a clear do-not-trust state when calibration, walk-forward stability, or
  downside risk is weak.
- Keep the app personal: it should tell Gargi what to inspect next, not just
  display tables.
