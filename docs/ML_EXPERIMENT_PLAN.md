# ML Experiment Plan

Updated: 2026-06-08

## Rule

Model complexity is earned. A challenger is promoted only when it improves a later,
untouched time window and remains understandable enough to audit.

## Experiment Queue

| Order | Model idea | Purpose | Status | Prerequisite | Promotion gate |
|---|---|---|---|---|---|
| 1 | Time-split logistic baseline | Transparent probability baseline for 5d, 20d, and 60d horizons | Active baseline | Existing forward-return labels | Calibration, precision, and return lift reported by horizon |
| 2 | Calibrated logistic regression | Make baseline probabilities more honest | Planned | Stable outcome updater | Better Brier score without losing useful ranking lift |
| 3 | Gradient-boosted trees | Capture nonlinear feature interactions | Queued | Walk-forward evaluation harness | Beats logistic after costs on untouched dates |
| 4 | Expected-return regression | Predict magnitude instead of only up/down | Queued | Clean continuous labels | Better rank correlation and portfolio return after costs |
| 5 | Volatility and drawdown models | Separate opportunity from expected path risk | Planned | Consistent risk labels | Improves sizing or reduces drawdown without destroying return |
| 6 | Regime classifier | Detect when model behavior changes | Research idea | Longer market/context history | Improves stability across independently defined regimes |
| 7 | Horizon ensemble | Combine specialized 5d, 20d, and 60d evidence | Research idea | Multiple validated winners | Outperforms best single model with controlled turnover |

## Required Evaluation

- Walk-forward or expanding-window splits only.
- Embargo between training labels and evaluation dates.
- Precision, recall, ROC AUC, Brier score, and calibration.
- Average forward return, hit rate, turnover, drawdown, and benchmark lift.
- Results separated by horizon, action, liquidity, volatility, and market regime.
- One untouched final window before promotion.

## Immediate Implementation Order

1. Finish automatic outcome updates.
2. Build the paper performance page.
3. Reuse the production decision policy in walk-forward backtests.
4. Add calibrated logistic as the first challenger.
5. Add one tree model only after the evaluation harness is stable.
