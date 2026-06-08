# Stock Prediction Roadmap Checklist

Last updated: 2026-06-08

## Current Product Read

The app is already a private stock research dashboard with:

- Pipeline freshness checks
- Latest shortlist
- Ranked watchlist
- Research Lab
- Model Lab
- 3D stock universe
- Paper-trade review notes
- Automatic daily paper-decision ledger
- Portfolio Replay
- Mobile Activity and Architecture views
- Guarded GitHub Actions pipeline reruns

The next product phase is to turn it from a research dashboard into a validated personal trading assistant.

For the current implementation order, see `NEXT_STEPS_PLAN.md`.

## Phase 1: Decision System Foundation

- [x] Add a Daily Decision Board page.
- [x] Define initial allowed action labels: hold, hold/add review, review reduce, paper buy candidate, watch, avoid.
- [x] Make "do nothing / hold" a first-class recommendation.
- [x] Make the Daily Decision Board the first page after the flow proves useful.
- [x] Combine watchlist, model rankings, portfolio holdings, and risk into one decision table.
- [ ] Add trading-limit checks to the decision table.
- [x] Add plain-English explanation for each decision.
- [x] Add confidence level and primary time horizon for each decision when model data is available.

## Phase 2: Trading Constraint Engine

- [x] Add a manual local snapshot scaffold for Robinhood account type and current equity threshold status.
- [x] Add a manual local snapshot scaffold for rolling 5-trading-day day trade count.
- [x] Warn on the Daily Decision Board when trading constraints are unknown, cautious, or blocked.
- [ ] Add buying-power checks.
- [ ] Add cash-settlement or margin constraint checks.
- [ ] Block automatic real-trade actions when trading constraints are unclear.
- [ ] Add a trade-frequency budget to the decision score.

## Phase 3: Automatic Paper Trading

- [x] Convert model/watchlist queue into automatic paper trade candidates.
- [x] Add paper position sizing from portfolio value and 1% risk budget.
- [x] Add stop loss and target logic for each paper trade.
- [x] Save every paper decision, including rejected and hold decisions.
- [ ] Track paper trade outcomes by horizon.
- [ ] Add a paper-trading performance dashboard.
- [ ] Compare automatic paper trades against manual review choices.

## Phase 4: Serious Backtesting

- [ ] Build a full backtest engine using historical model snapshots.
- [ ] Simulate daily decisions across multiple horizons.
- [ ] Include position sizing, stops, targets, and hold/exit rules.
- [ ] Include realistic turnover and trade-frequency limits.
- [ ] Score strategies by return, Sharpe-style score, drawdown, win rate, and consistency.
- [ ] Compare strategy modes: momentum, mean reversion, swing, value, growth, long-term.
- [ ] Show which mode works best under different market conditions.

## Phase 5: Model Tournament

- [ ] Keep logistic baselines as benchmark models.
- [ ] Add tree-model baselines such as XGBoost or LightGBM.
- [ ] Compare models by horizon: 1d, 5d, 20d, 60d, longer-term.
- [ ] Compare models by sector, volatility group, and market cap group.
- [ ] Add calibration checks so probabilities mean what they claim.
- [ ] Add model decay alerts when recent results degrade.
- [ ] Only consider deep learning after simpler models plateau.

## Phase 6: Portfolio-Aware Assistant

- [ ] Make Robinhood portfolio snapshot required for the main decision board.
- [ ] Add current position risk by ticker.
- [ ] Add portfolio concentration warnings.
- [ ] Recommend add/reduce/exit based on portfolio impact, not just ticker score.
- [ ] Add cash allocation recommendations.
- [ ] Add "best next move" summary for the whole portfolio.
- [ ] Keep broad stock-universe scanning active so the app can find missed opportunities.

## Phase 7: Main Ticker Research Reports

- [ ] Create a report page for each main ticker.
- [ ] Include model signal, price chart, feature drivers, risk, and portfolio context.
- [ ] Include current action and why.
- [ ] Include target, stop, horizon, and position-size suggestion.
- [ ] Add news/sentiment section when a free reliable source is available.
- [ ] Save report snapshots for later auditing.

## Phase 8: Daily Report And Alerts

- [ ] Generate daily markdown report.
- [ ] Summarize new picks, dropped picks, changed rankings, and portfolio actions.
- [ ] Add alerts for stop loss, target, model threshold, ranking change, and PDT risk.
- [ ] Add model performance notes to the daily report.
- [ ] Add email or local notification delivery after the report format is stable.

## Phase 9: Real Trade Execution Gate

- [ ] Keep real trade execution disabled by default.
- [ ] Require passing paper-trade and backtest thresholds before enabling.
- [ ] Require trading-constraint engine to pass before enabling.
- [ ] Require explicit confirmation mode before any live order placement.
- [ ] Add kill switch.
- [ ] Add max daily/weekly trade limits.
- [ ] Add max position and max loss limits.
- [ ] Log every proposed and executed action with reason, model version, and portfolio state.

## Progress Scorecard

Update this section as work is completed.

- Product decision record: complete
- Daily decision board: active primary surface
- Trading constraint engine: scaffolded, incomplete
- Automatic paper trading: daily decision generation complete; outcomes incomplete
- Full backtesting: not started
- Model tournament: not started
- Portfolio-aware assistant: not started
- Ticker research reports: not started
- Daily report and alerts: not started
- Real trade execution gate: not started
