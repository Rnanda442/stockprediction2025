# Stock Research Assistant Charter

Last updated: 2026-06-07

## Product In One Sentence

Build a private, explainable, portfolio-aware stock research assistant that
turns market data into reviewable daily decisions, proves those decisions
through paper trading and backtesting, and keeps real trading disabled until
the evidence and safety controls are strong enough.

## North Star

The main screen should answer three questions:

1. What deserves attention today?
2. Why does it deserve attention?
3. What risk, portfolio, or account constraint could change the decision?

The product is not finished when it produces a prediction. It is useful when a
prediction becomes a traceable decision with a horizon, explanation, risk
budget, constraint status, and later outcome.

## Core Principles

- A prediction is evidence, not an instruction.
- "Hold / do nothing" is a valid and often desirable action.
- Separate historical facts, heuristic scores, model probabilities, and final
  decisions in the interface.
- Evaluate every model on later unseen dates and compare it with a simple
  baseline.
- Keep portfolio context, liquidity, drawdown, trading limits, and position
  size visible beside expected upside.
- Record rejected, blocked, and hold decisions, not only successful picks.
- Prefer understandable models until a more complex model proves a meaningful
  out-of-sample improvement.
- Real orders remain disabled until paper trading, backtesting, constraints,
  audit logs, and explicit user confirmation all pass.

## Primary User Journey

1. Open the Daily Decision Board.
2. Check data freshness and model health.
3. Review current holdings and new candidates.
4. Read the main positive and negative drivers.
5. Check portfolio and trading constraints.
6. Approve, reject, or observe a paper decision.
7. Review later outcomes and model performance.

## Current Product Boundary

The application is a private research workspace. It can rank ideas, estimate
baseline probabilities, replay portfolio rules, and save paper-review notes.
It does not know future news and it does not place real trades.

## Definition Of A Trustworthy Decision

Every decision should eventually include:

- ticker and timestamp
- action, horizon, and confidence type
- source data date and model version
- positive and negative drivers
- expected return and risk estimate
- current position and portfolio impact
- suggested paper size, stop, and target
- constraint status and blocked reasons
- later outcome and evaluation status

## Implementation Order

1. Keep data and baseline-model exports reliable.
2. Make the Daily Decision Board the primary product surface.
3. Complete the trading constraint engine.
4. Automate paper decisions and outcome tracking.
5. Backtest the complete decision system.
6. Run a model tournament by horizon.
7. Add portfolio-level allocation and ticker reports.
8. Add daily reporting and alerts.
9. Consider live execution only after explicit safety gates pass.

## Visual Direction

The app should teach the system while it operates:

- Use a left-to-right flow for data, features, models, decisions, and feedback.
- Group variables by the question they answer rather than showing one long
  undifferentiated list.
- Use gates for validation, constraints, and human review.
- Use motion sparingly to show time, ranking changes, and feedback.
- Keep Processing/Java as an optional visual-prototyping tool. Export useful
  concepts as short videos, GIFs, or web-ready designs rather than making the
  production Streamlit app depend on a Java runtime.

## Source Of Truth

- Product intent: this charter.
- System map: `docs/SYSTEM_ARCHITECTURE.md`.
- Detailed decisions: `docs/PRODUCT_DECISIONS.md`.
- Ordered implementation work: `docs/NEXT_STEPS_PLAN.md`.
- Progress checklist: `docs/ROADMAP_CHECKLIST.md`.
- Current goals and next commit targets: `docs/activity_board.json`.
- Working code and deployment source: GitHub `main`.
