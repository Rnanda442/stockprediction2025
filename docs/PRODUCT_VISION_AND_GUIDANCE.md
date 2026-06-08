# Product Vision and Guidance

Updated: 2026-06-08
Source: Owner answers in Gmail, subject "Stock pred answers with new soruces to help guidance and asistance"

## Long-Term Product

Build an always-running, multi-stock decision system that monitors the portfolio and
market throughout the day, proposes portfolio changes, explains them briefly, and
eventually submits trades only after the models and safety controls have been proven.

The target interaction is not one generic daily pick. It is a concrete proposal such as:

> Sell 20% of one position and allocate it to another stock because the approved models,
> expected return, and risk controls support the change.

The owner reviews and approves the proposal from a phone before execution. Real trading
must remain disabled until paper performance, backtesting, constraints, and auditability
meet explicit thresholds.

## Website Guidance

### Today: Daily Decision Board

- Monitor multiple candidates and existing holdings.
- Surface positive and negative signals for owned positions visually.
- Produce decisions throughout the day when fresh data is available.
- Prioritize the reason for the action, main risks, and expected return.
- Support approval of proposed trades from the website in a later gated phase.
- Show portfolio reallocations, not only isolated ticker recommendations.

### Action Vocabulary

- `paper buy`: simulated entry after evidence and constraints pass.
- `hold`: maintain an existing position because no stronger change is justified.
- `watch`: monitor for a better price, stronger model agreement, or a defined trigger.
- `reduce`: sell a specified portion because risk or expected return has deteriorated.
- `avoid`: do not enter because the current evidence is unfavorable.
- `blocked`: a model-supported action stopped by data, risk, account, or safety rules.

Exact thresholds remain an implementation task and must be validated rather than chosen
for visual convenience.

### Architecture

The primary architecture should be a visual knowledge graph:

`Market Data -> Features -> ML Models -> Strategy Rules -> Portfolio Rules -> Paper Decision -> Results`

- Nodes should be clickable.
- Variable icons should open explanations of meaning, source, and model use.
- Model nodes should reveal structure, inputs, outputs, validation, and performance.
- Use circles, connecting lines, size, position, transparency, and motion before long text.
- Preserve a simpler five-stage explanation for first-time orientation.

### Visualization Direction

Combine four compatible visual modes:

1. Factory gates for candidates passing or failing checks.
2. Neural-network or knowledge-graph connections between variables, models, and decisions.
3. A living market map showing opportunity, risk, confidence, time, and accuracy changes.
4. A control room separating model evidence, portfolio state, risk, and market context.

Processing is a visual prototyping environment. The production app remains web-based.
Useful Processing studies should be exported or rebuilt as web-native interactive visuals.

### Variables and Data

- Portfolio context affects the final action, not the raw stock prediction.
- Investigate company fundamentals, analyst ratings, news/sentiment, broad-market
  direction, and interest rates.
- Prefer stable APIs or scheduled data ingestion over browser automation.
- Treat every external data source as optional until licensing, reliability, cost, and
  historical availability are verified.

### Model Architecture

Use specialized models rather than one opaque model:

- Direction model: probability of price rising by horizon.
- Return model: expected percentage return.
- Risk model: volatility and drawdown.
- Decision policy: combines approved model outputs with portfolio and account constraints.

Models may become nonlinear, but training must remain time-aware with no future leakage.
The app should visualize variable influence, contribution magnitude, model agreement,
calibration, prediction changes over time, and performance of similar historical signals.

### Model Development

- Design the evaluation architecture before adding many models.
- Test one challenger at a time against an approved baseline.
- Keep approved models on Today.
- Keep challengers and comparisons in Research / Model Lab.
- Track implementation status in Activity.
- Explain conceptual connections in Architecture.

### Activity Board

- Track technical work, product goals, ideas, and completed evidence separately.
- The owner eventually wants to update tasks and approve proposed trades in the website.
- Website writes require authentication, persistence, audit history, and conflict handling.

## Non-Negotiable Safety Boundary

The long-term goal includes trade execution, but approval UI does not make a model safe.
Before real orders, require:

- proven paper outcomes
- walk-forward backtesting with costs and turnover
- account, buying-power, settlement, and PDT checks
- position, concentration, and loss limits
- explicit approval and a kill switch
- complete proposal and execution audit logs
