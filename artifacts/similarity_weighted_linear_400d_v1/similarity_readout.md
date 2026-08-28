# Similarity-Weighted Linear 400-Date Readout

- Paired after-cost return delta: -0.00542
- Return interval: [-0.01672, 0.00259]
- Paired AUC delta: -0.00397
- AUC interval: [-0.01422, 0.00631]
- Paired Brier gain: -0.00035
- Neighbor-placebo return delta: 0.00038
- Promotion candidate: `false`
- Final 60-date holdout opened: `false`

## Primary strategies

| strategy | auc_mean | brier_mean | log_loss_mean | ece_10_mean | mean_excess_return | win_rate | mean_turnover | worst_net_return | splits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| plain | 0.50273 | 0.25073 | 0.69461 | 0.05738 | 0.00046 | 0.37500 | 0.65833 | -0.08302 | 6 |
| similarity_weighted | 0.49505 | 0.25107 | 0.69530 | 0.05779 | -0.00496 | 0.41667 | 0.75833 | -0.07711 | 6 |

## Cost sensitivity

| cost_bps | strategy | mean_excess_return |
| --- | --- | --- |
| 0.00000 | plain | 0.00112 |
| 0.00000 | similarity_weighted | -0.00420 |
| 10.00000 | plain | 0.00046 |
| 10.00000 | similarity_weighted | -0.00496 |
| 25.00000 | plain | -0.00053 |
| 25.00000 | similarity_weighted | -0.00609 |
| 50.00000 | plain | -0.00217 |
| 50.00000 | similarity_weighted | -0.00799 |

## Guardrails

- One fixed elastic model and one fixed similarity rule were used.
- Imputation, scaling, the classifier, distance scale, and neighbor reference were fit on training rows only.
- Placebos shuffled neighbor labels within training date without refitting the base model.
- Paired uncertainty used five-date blocks.
- Portfolio size, rebalance interval, cost, neighbor count, and alpha were not tuned.