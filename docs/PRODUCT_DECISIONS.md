# Stock Prediction Product Decisions

Last updated: 2026-06-04

## Product Direction

This project is a personal trading assistant for the primary user, not a generic SaaS product. The goal is to help one user make better stock decisions from their own Robinhood portfolio, research preferences, risk tolerance, and trading constraints.

The assistant should eventually recommend specific trade actions and, after enough validation, connect to real trade execution. Until the system proves itself through paper trading, backtesting, and explicit safety checks, all automation should remain paper-trade or review-gated.

## Core User

- Primary user: Rohan / the app owner.
- Portfolio data: essential, not optional.
- Broker context: Robinhood.
- Current practical constraint: likely under the $25,000 pattern day trading threshold, so trading-frequency rules must be treated as a hard product constraint.

## Trading Constraint Notes

The app must account for pattern day trading limits before recommending or automating same-day exits and re-entries.

Current verified references:

- FINRA day trading overview: https://www.finra.org/investors/investing/investment-products/stocks/day-trading
- Robinhood pattern day trading support: https://robinhood.com/support/articles/pattern-day-trading/
- Robinhood PDT protection support: https://robinhood.com/support/articles/pattern-day-trade-protection/

Product rule for now:

- Track day trades in a rolling 5-trading-day window.
- Treat fewer than 4 day trades in 5 trading days as the safe default for accounts below $25,000.
- Add a "do nothing / hold" decision as a first-class action so the assistant does not overtrade.
- Never let an automated real-trade mode ignore PDT, cash settlement, buying power, or position-size limits.

## Prediction Goals

The assistant should evaluate multiple horizons and choose the best action based on evidence:

- 1-day movement
- 5-day swing
- 20-day swing
- 60-day trend
- Longer-term winner/loser ranking

The app should predict more than direction. Desired prediction outputs:

- Direction probability
- Expected return
- Risk-adjusted return
- Volatility
- Drawdown risk
- Probability of outperforming the market or a benchmark
- Confidence and explanation

## Model Direction

Use separate models when useful, especially by:

- Trading horizon
- Sector
- Market cap group
- Volatility group
- Trading style

Model families to test:

- Current logistic baselines as the benchmark
- Tree models such as XGBoost or LightGBM if they improve results
- Deep learning only after simpler models stop improving or there is a clear sequence/image/text use case

The app should compare models and expose which model is currently winning by horizon and by evaluation metric.

## Strategy Modes

The assistant should support toggles for different trading modes:

- Swing trading
- Long-term investing
- Momentum
- Mean reversion
- Dividend/value
- High-growth speculative ideas

The system should optimize across these styles based on model performance, risk, and user preference. Daily decisions are desired, but "do not change anything" should often be a valid decision.

## Stock Universe

The app should always keep a broad view of the stock universe so it can notice opportunities outside the current portfolio or shortlist.

There should also be a smaller high-signal set that says: these are the ideas worth reviewing now.

## Risk And Position Sizing

Initial risk budget:

- Start with a 1% portfolio-risk limit per idea.

Desired risk inputs:

- Volatility
- Drawdown history
- Liquidity
- Model uncertainty
- Portfolio concentration
- PDT / trading-frequency constraints
- Buying power
- Cash settlement or margin constraints

Longer-term goal:

- Let the model learn dynamic risk weights across different market scenarios, but require clear guardrails before it can act.

## Validation Metrics

The app should not rely on one metric. The final decision score should combine:

- Accuracy
- ROC AUC
- Brier score / calibration
- Win rate
- Average forward return
- Sharpe-style score
- Max drawdown
- Risk-adjusted return
- Consistency across time windows
- Turnover / trading-frequency cost

## Data Expansion

Use free and accessible data for now.

Possible future sources if easy and effective:

- News and sentiment
- Earnings dates
- Analyst revisions
- Insider transactions
- Options-related signals
- Macro indicators

## UX Direction

The first screen should become a daily decision board.

Each main ticker should eventually have a research report page with:

- Current action
- Why this action
- Model signal
- Risk estimate
- Position-size suggestion
- Portfolio impact
- Chart context
- News/sentiment if available
- Paper-trade or real-trade audit trail

The app should also produce a daily email or markdown report summarizing:

- New picks
- Changed rankings
- Portfolio actions
- Risk warnings
- Model performance notes

## Automation Direction

Target progression:

1. Automatic paper trades.
2. Human-reviewed proposed real trades.
3. Real trade execution only after strong validation and explicit safety controls.

The long-term goal includes real trade execution, but the near-term build should prove the system with paper trading and backtesting first.

## Alerting

Add alerts when:

- A ticker crosses a model threshold.
- A stop loss or target is reached.
- A ranking changes materially.
- A PDT or trade-frequency limit is close.
- Portfolio concentration gets too high.
- A model stops performing well.

