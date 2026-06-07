# Stock Research System Architecture

Last updated: 2026-06-07

## Mental Model

The system is a learning loop, not one prediction formula:

```text
market + portfolio data
        |
        v
quality and liquidity gates
        |
        v
features grouped by direction, consistency, risk, and tradability
        |
        +-------------------+
        |                   |
        v                   v
heuristic ranking      time-split models
        |                   |
        +---------+---------+
                  v
        daily decision policy
                  |
        portfolio + account constraints
                  |
        paper action / hold / reject
                  |
             later outcome
                  |
       evaluation and model feedback
```

## Architecture Layers

### 1. Inputs

- Historical prices and volume.
- Latest stock-universe and shortlist snapshots.
- Forward-return history used only after the relevant prediction date.
- Optional local Robinhood portfolio and trading-constraint snapshots.
- Pipeline parameters supplied through guarded GitHub Actions controls.

### 2. Data Quality And Safety Gates

- Missing or invalid quote checks.
- Minimum history and liquidity checks.
- Volatility and pipeline-freshness checks.
- Time ordering that prevents future outcomes from entering training features.
- Authentication and artifact validation before dashboard promotion.

### 3. Feature Families

| Family | Question | Examples |
|---|---|---|
| Direction | Is price moving, and which way? | return, momentum, trend slope |
| Consistency | Is the movement orderly? | trend fit, persistence |
| Risk | How unstable or damaging can the path be? | volatility, drawdown |
| Tradability | Can the idea be entered and exited reasonably? | dollar volume, price |
| Context | Does it fit the portfolio and intended horizon? | holding status, concentration, horizon |

Derived scores such as leader score and trend score summarize some of these
families. They are not independent facts and should not be counted as separate
proof when they reuse the same underlying variables.

### 4. Research Engines

**Heuristic engine**

Creates the ranked watchlist and shortlist from transparent rules. Its
confidence value means rule agreement, not probability of profit.

**Model engine**

Trains baseline classifiers on earlier dates, uses an embargo around the split,
and evaluates on later dates. It currently produces 5-day, 20-day, and 60-day
probability rankings.

**Scenario and replay engine**

Supports Monte Carlo exploration, walk-forward checks, and portfolio replay.
These tools describe historical or simulated behavior; they are not a live
execution engine.

### 5. Decision Policy

The Daily Decision Board combines:

- watchlist rank and heuristic confidence
- best available model probability and horizon
- current holding and portfolio weight
- volatility-based paper sizing
- trading-constraint status

The policy should produce explicit actions such as hold, paper buy candidate,
watch, review reduce, or avoid. Every action needs a reason and can be blocked
by a constraint gate.

### 6. Outputs

- Daily Decision Board.
- Ranked Watchlist and focused shortlist.
- Model Lab and Research Lab.
- Paper decision ledger.
- Portfolio Replay and future Backtest Lab.
- Pipeline health, daily report, and future alerts.

### 7. Feedback Loop

Later returns and paper outcomes are attached to the decision that existed at
that time. Evaluation compares:

- heuristic-only decisions
- model-only decisions
- combined decision policy
- hold or benchmark behavior

This separation is essential. A better prediction metric does not automatically
mean a better portfolio policy after sizing, turnover, and drawdown.

## Main Runtime Boundaries

| Boundary | Responsibility |
|---|---|
| GitHub Actions | Refresh data, build features and models, validate outputs, publish artifacts |
| `dashboard_data.db` | Compact read-only contract between pipeline and dashboard |
| Streamlit dashboard | Explain, explore, compare, and review decisions |
| Ignored local files | Private portfolio snapshots, constraints, and paper-review state |
| GitHub `main` | Public-safe code and documentation source of truth |

## Near-Term Build Contract

The next implementation should complete one vertical path before adding more
variables:

1. One daily decision schema.
2. One automatic paper ledger.
3. One outcome updater.
4. One paper-performance view.
5. One backtest using the same decision policy.

Only after that loop works should the model tournament add tree models or new
data sources. This keeps model complexity from outrunning the product's ability
to prove whether decisions helped.

