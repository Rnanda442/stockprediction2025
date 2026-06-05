# Stock Prediction Next Steps Plan

Last updated: 2026-06-04

## Purpose

This plan connects the product decisions in `PRODUCT_DECISIONS.md` to the dashboard as it exists today. The goal is to turn the current research dashboard into a validated personal Robinhood-aware trading assistant without skipping the safety and proof stages.

The order matters:

1. Make model outputs reliable.
2. Make the daily decision board portfolio-aware.
3. Add trading-limit guards.
4. Automate paper trades.
5. Backtest the full decision system.
6. Expand models and strategy modes.
7. Only then design real-trade execution.

## Current App Reality

The app already has these working surfaces:

- `Overview`: market freshness, latest shortlist, tracked candidates.
- `Daily Decision Board`: first action table combining watchlist, portfolio snapshot when present, model queue when present, and paper sizing.
- `Ranked Watchlist`: 50-stock research funnel with confidence and holding-window guidance.
- `Research Lab`: single-ticker historical slices, Monte Carlo view, and walk-forward testing.
- `Model Lab`: UI for model evaluation, feature importance, model/watchlist overlap, and manual paper review.
- `Portfolio Replay`: compares hold behavior against simple rotation rules.
- `Pipeline Controls`: guarded GitHub Actions reruns with pipeline parameters.
- `3D Stock Universe` and `Visual Lab`: broad stock-universe exploration.
- `Pipeline Health`: freshness and compact database contents.

Current local dashboard database has:

- `LatestShortlist`
- `LatestWatchlist`
- `ShortlistHistory`
- `WatchlistPerformanceSummary`
- `FeatureSummary`
- `RecentPrices`
- `StockUniverse`
- `StockUniverseSnapshot`
- `PipelineHealth`

Initial inspection found the local dashboard database was missing:

- `ModelEvaluation`
- `ModelFeatureImportance`
- `LatestModelPredictions`

The code already expects those model tables. The pipeline has `scripts/build_model_baseline.py`, `scripts/export_dashboard_data.py`, and validation checks for those tables, so the first next step is to verify why the current synced local `dashboard_data.db` does not include them.

Inspection update, 2026-06-04:

- Latest successful GitHub Actions run inspected: `26964554433`.
- Run URL: `https://github.com/Rnanda442/stockprediction2025/actions/runs/26964554433`.
- The cloud artifact was healthy and included `ModelEvaluation`, `ModelFeatureImportance`, and `LatestModelPredictions`.
- Local `dashboard_data.db` was stale from `2026-06-01T20:14:42Z`.
- Running `scripts/start_dashboard.ps1 -SyncOnly` refreshed local `dashboard_data.db` from the artifact.
- Local model tables after sync: `ModelEvaluation=3`, `ModelFeatureImportance=63`, `LatestModelPredictions=7410`.
- Local model horizons after sync: 5d, 20d, and 60d, each with 2,470 predictions.
- Watchlist/model overlap after sync: 44 rows.

Action update, 2026-06-04:

- Committed dashboard model-health status and this next-steps plan in commit `72e1bc6`.
- Dispatched GitHub Actions run `26987118096` on `main`.
- Run URL: `https://github.com/Rnanda442/stockprediction2025/actions/runs/26987118096`.
- The run failed in the notebook login cell because Robinhood required app verification and the confirmation timed out.
- This was an authentication/session issue, not a dashboard/model-health code failure.
- The failed run uploaded a fallback dashboard DB, but it was the restored prior healthy DB, not a newly refreshed export.
- Follow-up added: the notebook runner now emits a clear GitHub Actions error when Robinhood verification times out.
- Rerun flow documented in `ROBINHOOD_ACTION_RUNBOOK.md`.

## Build 1: Restore Reliable Model Exports

Why this comes first:

The user wants the app to predict multiple horizons, choose the best decision, and explain why. The dashboard UI already has a Model Lab, but the current local DB does not include the model tables that make it useful.

Current app hooks:

- `scripts/build_model_baseline.py`
- `scripts/export_dashboard_data.py`
- `scripts/validate_pipeline_outputs.py`
- `dashboard/data.py`
- `dashboard/app.py` Model Lab and Daily Decision Board

Tasks:

- [x] Run or inspect the latest GitHub Actions pipeline artifact and confirm whether model tables are present there.
- [x] If the artifact has model tables, refresh local `dashboard_data.db` with `scripts/start_dashboard.ps1 -SyncOnly`.
- [ ] If the artifact is missing model tables, debug `scripts/build_model_baseline.py` inside the pipeline.
- [x] Add model table row counts to `Pipeline Health` so missing models are obvious in the app.
- [x] Add a Daily Decision Board warning when model tables are missing.
- [x] Add a small model export smoke test that checks all three horizons: 5d, 20d, 60d.
- [x] Add a clearer Actions error when Robinhood app verification times out.
- [x] Add a Robinhood auth/session preflight before the expensive notebook step.
- [x] Add a documented rerun flow for when Robinhood requires app approval.

Done criteria:

- `ModelEvaluation` has at least 3 rows.
- `LatestModelPredictions` has rows for 5d, 20d, and 60d.
- `Model Lab` shows real metrics.
- `Daily Decision Board` shows model probability and horizon for candidate tickers.

## Build 2: Make The Daily Decision Board The Main Product Surface

Why this comes next:

The user wants daily decisions, including "do nothing" when the portfolio is already positioned well. The decision board should become the place to start every day.

Current app hooks:

- `dashboard/app.py` `render_daily_decision_board`
- `dashboard/portfolio_replay.py` portfolio snapshot helpers
- `dashboard/data.py` watchlist, shortlist, model predictions

Tasks:

- [ ] Move `Daily Decision Board` to the first sidebar option after it proves stable.
- [ ] Add action buckets: hold, buy, add, reduce, exit, watch, avoid.
- [ ] Add a "best next move" summary at the top.
- [ ] Split decisions into sections: Current Holdings, New Paper Candidates, Watch Only, Avoid/Rejected.
- [ ] Require portfolio snapshot for portfolio-aware decisions, while still allowing a limited watchlist-only mode.
- [ ] Add per-action explanation fields: signal, risk, portfolio impact, model horizon, and constraint status.
- [ ] Add a decision timestamp and source data date.

Done criteria:

- The first screen answers: "What should I do today?"
- Every recommendation can also say "why."
- "Hold / do nothing" appears as a normal good decision, not a fallback.

## Build 3: Trading Constraint Engine

Why this comes before automatic trades:

The user has Robinhood and likely needs to respect pattern day trading limits, account equity status, buying power, and cash/margin constraints. The app cannot safely automate decisions before these are visible.

Current app hooks:

- `dashboard/portfolio_replay.py` portfolio snapshot helpers
- `scripts/snapshot_robinhood_portfolio.py`
- `dashboard/app.py` Daily Decision Board

Tasks:

- [x] Define a local ignored constraint snapshot file, such as `data/trading_constraints_snapshot.csv`.
- [ ] Track account equity, buying power, cash, margin/cash account mode if available, and snapshot time.
- [x] Track known day trades in a rolling 5-trading-day window through the manual snapshot scaffold.
- [x] Add a manual override field for day-trade count until reliable broker-derived trade history exists.
- [ ] Add constraint status to every Daily Decision Board row.
- [x] Add overall trading constraint status to the Daily Decision Board.
- [ ] Block any same-day exit/re-entry recommendation if PDT status is unclear.
- [ ] Add warning thresholds: safe, caution, blocked.
- [ ] Add max weekly trade budget and max same-day trade budget.

Done criteria:

- The board can say whether a proposed trade is allowed, risky, or blocked.
- A trade action cannot be treated as executable unless constraints pass.
- Constraint assumptions are visible to the user.

## Build 4: Automatic Paper Trading

Why this comes before real execution:

The user wants automation, but the app needs proof. Automatic paper trading lets the app make decisions by itself while keeping real money untouched.

Current app hooks:

- `dashboard/paper_trades.py`
- `dashboard/app.py` Model Lab manual paper review
- `Daily Decision Board`

Tasks:

- [ ] Create an automatic paper decision ledger separate from manual review notes.
- [ ] Save every daily action, including hold, watch, avoid, buy, add, reduce, and exit.
- [ ] Calculate suggested paper quantity using portfolio value and 1% risk budget.
- [ ] Add stop loss, target price, and decision horizon.
- [ ] Add automatic paper open/close logic based on next daily run.
- [ ] Track outcome by 1d, 5d, 20d, and 60d.
- [ ] Add a Paper Trading page or expand Model Lab with paper performance.
- [ ] Compare automatic paper decisions against the existing watchlist and model-only picks.

Done criteria:

- The app can run a daily paper cycle without manual ticker selection.
- Every paper action has a reason, size, horizon, stop, target, and status.
- Paper performance can be evaluated over time.

## Build 5: Full Decision Backtesting

Why this is the proof layer:

The current Portfolio Replay is useful, but it is not yet a full backtest of the daily decision system. The product needs to know whether the combined decision logic works historically before live trading is considered.

Current app hooks:

- `dashboard/portfolio_replay.py`
- `RecentPrices`
- `WatchlistHistory`
- `ShortlistHistory`
- future model prediction snapshots

Tasks:

- [ ] Create a reusable backtest module for daily decisions.
- [ ] Simulate the Daily Decision Board rules across historical dates.
- [ ] Include position sizing, stops, targets, hold rules, exits, and trade budget.
- [ ] Include PDT-like limits and turnover penalties.
- [ ] Compare modes: momentum, mean reversion, swing, value, growth, long-term.
- [ ] Add benchmark comparisons: hold current portfolio, SPY-like benchmark if available, watchlist-only, model-only.
- [ ] Add metrics: total return, annualized return, max drawdown, Sharpe-style score, win rate, average return, turnover, and blocked trades.
- [ ] Add a Backtest page or expand Portfolio Replay into Backtest Lab.

Done criteria:

- The app can answer whether the full decision system would have helped historically.
- Backtest output includes both profit metrics and risk metrics.
- Strategy modes can be compared without changing code.

## Build 6: Model Tournament And Strategy Modes

Why this follows backtesting:

The user wants the app to predict multiple horizons and decide which model/style is more likely. That only matters if the system can compare models fairly.

Current app hooks:

- `scripts/build_model_baseline.py`
- `Model Lab`
- `LatestModelPredictions`
- `ModelEvaluation`

Tasks:

- [ ] Keep logistic baseline as the benchmark.
- [ ] Add tree models such as LightGBM or XGBoost if dependency/install cost is acceptable.
- [ ] Train/evaluate models by horizon.
- [ ] Add model calibration checks.
- [ ] Add sector, volatility, and market-cap grouping if those features are available or easy to add.
- [ ] Add strategy-mode scores that can be toggled in the app.
- [ ] Add model winner selection by horizon and market condition.
- [ ] Add model decay warnings when recent results underperform.

Done criteria:

- The app can show which model is currently best for 5d, 20d, and 60d.
- The decision board can explain which model/style influenced the action.
- Model upgrades are accepted only when they beat the baseline on out-of-sample tests.

## Build 7: Portfolio-Aware Assistant

Why this is separate:

Ranking good stocks is not the same as managing the user's actual portfolio. The assistant needs to account for current holdings, cash, concentration, and risk.

Current app hooks:

- `scripts/snapshot_robinhood_portfolio.py`
- `dashboard/portfolio_replay.py`
- `Daily Decision Board`

Tasks:

- [ ] Make portfolio snapshot status visible on the decision board.
- [ ] Add current position value, gain/loss if available, and portfolio weight.
- [ ] Add concentration warnings.
- [ ] Add cash allocation guidance.
- [ ] Add add/reduce/exit recommendations based on portfolio exposure.
- [ ] Add portfolio-level risk score.
- [ ] Add "missed opportunity" section from the broad stock universe.

Done criteria:

- The app recommends actions for the portfolio, not just isolated tickers.
- The user can see whether the portfolio is balanced, overexposed, underinvested, or constrained.

## Build 8: Main Ticker Research Reports

Why this matters:

The user wants stronger explanations before decisions. Main tickers should have research pages that combine signal, risk, chart, portfolio context, and audit history.

Current app hooks:

- `Ticker Explorer`
- `Research Lab`
- `Model Lab`
- `Daily Decision Board`

Tasks:

- [ ] Create a report section for selected tickers.
- [ ] Include current decision and why.
- [ ] Include model probabilities by horizon.
- [ ] Include feature drivers.
- [ ] Include price chart and historical slice.
- [ ] Include portfolio impact.
- [ ] Include paper-trade history.
- [ ] Add news/sentiment only if a free reliable source is easy to use.
- [ ] Save report snapshots for later review.

Done criteria:

- A main ticker page can explain why the assistant likes, dislikes, holds, or exits the ticker.
- Reports are auditable over time.

## Build 9: Daily Report And Alerts

Why this makes it usable:

The app should not require the user to dig through every page. A daily report should summarize what changed and what needs attention.

Current app hooks:

- `Daily Decision Board`
- `Pipeline Health`
- `WatchlistHistory`
- `ShortlistHistory`
- future paper ledger

Tasks:

- [ ] Generate a daily markdown report.
- [ ] Include new picks, dropped picks, changed rankings, portfolio actions, and risk warnings.
- [ ] Include model performance notes.
- [ ] Add alerts for stop loss, target, model threshold, ranking change, PDT risk, and concentration risk.
- [ ] Start with local markdown/file output before email.
- [ ] Add email only after the report format is stable.

Done criteria:

- The app can summarize the day without requiring manual dashboard exploration.
- Alerts are actionable and not noisy.

## Build 10: Real Trade Execution Gate

Why this is last:

The user eventually wants real execution, but real orders require stronger proof, explicit controls, and a kill switch.

Current app hooks:

- None should place real trades today.
- Future hooks should be separate from research, paper trading, and backtesting code.

Tasks:

- [ ] Keep live trading disabled by default.
- [ ] Define minimum paper-trading and backtest thresholds.
- [ ] Require trading constraints to pass.
- [ ] Require explicit user confirmation mode.
- [ ] Add kill switch.
- [ ] Add max daily and weekly trade limits.
- [ ] Add max position size and max loss limits.
- [ ] Log every proposed and executed action with model version and portfolio state.
- [ ] Add dry-run mode that uses the same order builder without submitting orders.

Done criteria:

- Live execution cannot turn on accidentally.
- The same action can be traced from model signal to decision to constraint check to paper result to live order.

## Immediate Next Work Order

This is the recommended sequence for the next coding sessions:

1. Verify model tables in the newest GitHub Actions artifact and fix local sync if needed.
2. Add model-table status to `Pipeline Health` and `Daily Decision Board`.
3. Add a trading constraint snapshot format and display constraint status.
4. Add automatic paper decision ledger.
5. Add Paper Trading performance page.
6. Upgrade Portfolio Replay into Backtest Lab.
7. Add model tournament only after the full decision backtest exists.

## Questions To Revisit Later

Do not block the next build on these, but revisit before live execution:

- Is the Robinhood account cash or margin?
- Is account equity consistently above or below $25,000?
- Can we reliably read trade history, or do we need manual day-trade count input?
- Which strategy mode should be the default after backtesting?
- What minimum paper-trade history is required before live orders are considered?
- What is the maximum acceptable weekly loss before the assistant stops trading?
