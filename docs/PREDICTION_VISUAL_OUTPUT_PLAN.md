# Prediction Visual And Output Plan

Last updated: 2026-07-27

This plan uses the last user-approved GitHub Actions run as the current product
baseline:

- Run: `30013117678`
- URL: `https://github.com/Rnanda442/stockprediction2025/actions/runs/30013117678`
- Event: manual `workflow_dispatch`
- Commit: `6fc301715332e3a12928402d7068ca5a0465e6e9`
- Completed: `2026-07-23T16:34:46Z`
- Local approved artifact export: `2026-07-23T16:31:12Z`
- Latest market, shortlist, and watchlist date: `2026-07-22`

The later remote auto-commit is not part of this plan because it was not
approved through Robinhood.

## Current Approved State

The approved artifact says the system is usable for review and paper decisions,
not live trading.

| Area | Current State |
|---|---|
| Market coverage | 2,504 latest-date tickers out of 2,530 tracked, 99.0% coverage |
| Latest shortlist | AMAT, MTD, LRCX, MKSI, ROK |
| Latest watchlist | 50 ranked names |
| Model baselines | 5d, 20d, and 60d leakage-controlled logistic baselines |
| Latest model predictions | 7,590 rows |
| Paper decisions | 300 records |
| Paper outcomes | 750 events |
| Matured paper decisions | 239 |
| Open paper decisions | 61 |
| Account constraints | Needs local broker constraint snapshot |
| Live trading | Blocked |

The existing model evidence is modest. Accuracy is close to chance, ROC AUC is
around 0.49-0.51, and high-confidence selected buckets have slightly better
average forward return than all test rows. The website must show this honestly:
the model is a research filter, not a trade oracle.

## Website Goal

When you open Streamlit, the website should answer these questions in order:

1. Is the data fresh enough to trust today?
2. What are the best candidates?
3. What is good about each candidate?
4. What is bad or risky about each candidate?
5. Which model or rule supports it?
6. What should I do: hold, paper buy, watch, avoid, reduce, or wait?
7. What proof do we have from prior paper outcomes?
8. What is blocked because account constraints or evidence are not ready?

The site should guide action, but it should keep real-money execution disabled
until constraints, backtests, and paper results are strong.

## Primary Visuals To Build

### 1. Daily Command Center

Purpose: first page, one-screen answer for the day.

Required outputs:

- Market freshness card: market date, artifact export time, coverage, stale-data warning.
- Readiness strip: artifact, model, decisions, paper loop, account constraints, live trading.
- Best next move card: top action bucket from the Daily Decision Board.
- Action counts: hold, paper buy candidate, watch, avoid, review reduce.
- Top 5 cards: ticker, action, probability, watchlist rank, risk, why.
- "Do nothing is good" state when the best action is hold/watch.

Good/bad guidance:

- Good: high watchlist rank, persistent rank, model probability above 60%, low volatility, high liquidity, in shortlist.
- Bad: stale data, no model support, high volatility, weak trend fit, poor paper history, account constraints blocked.

### 2. Candidate Decision Detail

Purpose: clicking a ticker should show why it is good or bad.

Required outputs:

- Signal panel: watchlist rank, model rank, model probability, model horizon.
- Good reasons: trend, persistence, liquidity, model drivers, paper outcomes.
- Bad reasons: volatility, weak trend fit, negative model drivers, constraint status.
- Price context: recent price chart, 20/60 day return, volatility.
- Decision contract: action, reason, paper quantity, stop loss, target, horizon.
- Evidence label: "strong", "moderate", "weak", or "avoid".

### 3. Prediction Quality Board

Purpose: prevent blind trust in weak predictions.

Required outputs:

- Model tournament table by horizon.
- Champion model by horizon.
- Accuracy, ROC AUC, Brier score, selected average return, selected win rate.
- Calibration buckets: predicted probability band vs actual win rate.
- High-confidence bucket return vs all-test-row return.
- Model decay warning when recent paper outcomes fall below baseline.

Visuals:

- Bar chart: model metrics by horizon.
- Calibration line: predicted probability vs realized win rate.
- Bucket table: bearish, neutral, modest bullish, bullish, strong bullish.
- Champion badge beside daily decisions.

### 4. Good/Bad Explainer Matrix

Purpose: quickly explain why the site likes or dislikes a stock.

Rows:

- Direction
- Consistency
- Risk
- Tradability
- Model evidence
- Portfolio context
- Paper history
- Account constraints

Columns:

- Score/value
- Good interpretation
- Bad interpretation
- Current state
- Action impact

This matrix should be generated per ticker and reused in the Daily Decision
Board, Model Lab, and Ticker Explorer.

### 5. Paper Learning Dashboard

Purpose: show whether the automated decision loop is improving.

Required outputs:

- Open vs matured decisions.
- Average return by action and horizon.
- Win rate by action and horizon.
- Model-only vs watchlist-only vs combined policy comparison.
- Bad-action audit: actions that consistently lose.
- Good-action audit: actions that consistently beat baseline.
- Outcome timeline: paper decisions over time and when they matured.

Decision guidance:

- Promote actions with repeated positive outcomes.
- Downgrade actions with repeated negative outcomes.
- Never hide weak paper evidence.

### 6. Risk And Constraint Guardrails

Purpose: avoid false "buy" confidence when the account is not ready.

Required outputs:

- Constraint snapshot age.
- Equity, cash, buying power.
- Day-trade count and max allowed.
- Weekly trade budget.
- Per-action block/caution/safe status.
- "Paper only" banner when constraints are missing.

Until this is green, real trading remains blocked.

### 7. Model Tournament And ANN Output

Purpose: add stronger machine-learning candidates without breaking the product.

Next-run model candidates:

- `sgd_logistic`: current leakage-controlled baseline.
- `mlp_ann`: scikit-learn ANN using `MLPClassifier`.
- Optional later: `hist_gradient_boosting`, enabled through `MODEL_CANDIDATES`.

Runtime controls:

- `MODEL_CANDIDATES=sgd_logistic,mlp_ann`
- `MODEL_CANDIDATES=sgd_logistic,mlp_ann,hist_gradient_boosting`
- `MODEL_MLP_HIDDEN_LAYERS=32,16`
- `MODEL_MLP_MAX_ITER=80`
- `MODEL_MAX_TRAIN_ROWS=350000`
- `MODEL_MAX_TEST_ROWS=150000`

Manual dispatch helper:

```powershell
.\scripts\sync_and_run_stock_pipeline.ps1 -ModelCandidates "sgd_logistic,mlp_ann" -Watch
```

Output tables:

- `ModelEvaluation`: champion model only, one row per horizon for existing UI compatibility.
- `LatestModelPredictions`: champion predictions only, used by decisions and paper records.
- `ModelTournamentEvaluation`: all candidate models by horizon.
- `LatestModelCandidatePredictions`: all candidate latest predictions.
- `ModelTournamentFeatureImportance`: global linear importances when available.
- `ModelFeatureImportance`: champion or fallback linear importances for existing charts.

Champion selection:

- Primary: held-out ROC AUC.
- Tie breakers: high-confidence average return, high-confidence win rate, and Brier score.
- A model can win a horizon only on held-out data after the embargo.

ANN guardrails:

- Use scikit-learn `MLPClassifier`, not TensorFlow/PyTorch, to keep Actions stable.
- Use early stopping.
- Keep logistic baseline as the benchmark.
- Show ANN driver attribution as pending until we add SHAP/permutation importance.
- Do not let ANN predictions place real trades.

### 8. Backtest Lab

Purpose: decide whether the full decision policy works historically.

Required outputs:

- Strategy equity curve.
- Benchmark equity curve.
- Drawdown chart.
- Trade list.
- Turnover.
- Blocked trades.
- Win rate.
- Average return by action.
- Strategy-mode comparison: momentum, swing, long-term, watchlist-only, model-only, combined.

Done means the app can say whether the decision policy would have helped in the
past, not just whether a classifier has decent metrics.

## Page-Level Build Order

1. Pipeline Health: add readiness and model tournament status.
2. Overview: turn into Daily Command Center.
3. Decisions: split into action buckets and candidate detail.
4. Model Lab: show tournament, calibration, champion model, and model decay.
5. Paper Performance: add good/bad action audits.
6. Ticker Explorer: add candidate explainer matrix.
7. Portfolio Replay: become Backtest Lab.
8. Pipeline Controls: show last approved run, last artifact sync, and next-run model candidates.

## Data Output Contract For Every Run

Every successful approved run should publish:

- `dashboard_data.db`
- `analytics/latest_watchlist.csv`
- `analytics/winners_shortlist.csv`
- `analytics/model_evaluation.csv`
- `analytics/model_tournament_evaluation.csv`
- `analytics/latest_model_predictions.csv`
- `analytics/latest_model_candidate_predictions.csv`
- `analytics/model_feature_importance.csv`
- `analytics/automatic_paper_decisions.csv`
- `analytics/automatic_paper_decision_outcomes.csv`
- `analytics/watchlist_performance_summary.csv`
- `analytics/shortlist_performance_summary.csv`

The compact database should include row counts for all important output tables
in `PipelineHealth`.

## Definition Of Done

The project is "done enough to use daily" when:

- Streamlit opens to a command center.
- The first screen says what is good, bad, blocked, and why.
- Every ticker action has a reason and risk warning.
- The model tournament runs on every approved pipeline run.
- ANN is compared against the logistic baseline, not trusted blindly.
- Paper outcomes are visible by action and horizon.
- Backtests exist for the full daily decision policy.
- Live trading remains blocked until constraints and backtests are strong.
