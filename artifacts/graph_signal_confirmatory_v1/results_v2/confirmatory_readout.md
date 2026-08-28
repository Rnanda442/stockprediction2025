# Training-Fitted Graph Signal Confirmatory Readout

- Best feature set: `base_plus_crowding`
- Best model: `elastic_logistic_c1_l25`
- Best mean AUC: 0.50238
- Placebo mean AUC: 0.49617
- Promotion candidate: `false`
- Final 60-date holdout opened: `false`

## Model and feature-set summary

| feature_set | model | auc_mean | auc_std | brier_mean | log_loss_mean | ece_10_mean | mean_excess_return | win_rate | worst_net_return | fits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_plus_crowding | elastic_logistic_c1_l25 | 0.50238 | 0.04460 | 0.25076 | 0.69469 | 0.05711 | 0.00241 | 0.50000 | -0.06926 | 6 |
| base_plus_crowding | ridge_logistic_c01 | 0.50238 | 0.04461 | 0.25076 | 0.69468 | 0.05712 | 0.00182 | 0.50000 | -0.06926 | 6 |
| base_plus_regime | elastic_logistic_c1_l25 | 0.50209 | 0.04301 | 0.25079 | 0.69474 | 0.05697 | 0.00219 | 0.33333 | -0.05702 | 6 |
| base_plus_regime | ridge_logistic_c01 | 0.50203 | 0.04301 | 0.25079 | 0.69474 | 0.05698 | 0.00230 | 0.33333 | -0.05702 | 6 |
| base_only | elastic_logistic_c1_l25 | 0.50156 | 0.04445 | 0.25080 | 0.69476 | 0.05678 | 0.00154 | 0.41667 | -0.06946 | 6 |
| base_only | ridge_logistic_c01 | 0.50149 | 0.04449 | 0.25080 | 0.69476 | 0.05678 | 0.00087 | 0.37500 | -0.06946 | 6 |
| graph_survivors | ridge_logistic_c01 | 0.50137 | 0.04325 | 0.25084 | 0.69484 | 0.05751 | 0.00448 | 0.50000 | -0.04815 | 6 |
| graph_survivors | elastic_logistic_c1_l25 | 0.50133 | 0.04314 | 0.25084 | 0.69484 | 0.05753 | 0.00538 | 0.50000 | -0.04759 | 6 |
| base_plus_crowding | tanh_mlp_16_8 | 0.48895 | 0.02801 | 0.25382 | 0.70093 | 0.07763 | -0.00031 | 0.52778 | -0.08864 | 18 |
| graph_survivors | tanh_mlp_16_8 | 0.48749 | 0.02183 | 0.25536 | 0.70413 | 0.08186 | -0.00109 | 0.50000 | -0.07629 | 18 |
| base_plus_regime | tanh_mlp_16_8 | 0.48554 | 0.02747 | 0.25385 | 0.70100 | 0.07650 | -0.00138 | 0.44444 | -0.06987 | 18 |
| base_only | tanh_mlp_16_8 | 0.48139 | 0.02635 | 0.25456 | 0.70242 | 0.08051 | -0.00395 | 0.45833 | -0.08264 | 18 |

## Paired graph-survivor deltas versus base

| model | seed | auc_delta_mean | auc_ci_2_5 | auc_ci_97_5 | brier_gain_mean | brier_ci_2_5 | brier_ci_97_5 | excess_return_delta_mean | return_ci_2_5 | return_ci_97_5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| elastic_logistic_c1_l25 | 0 | -0.00070 | -0.00417 | 0.00305 | -0.00004 | -0.00015 | 0.00008 | 0.00384 | -0.00300 | 0.01075 |
| ridge_logistic_c01 | 0 | -0.00064 | -0.00413 | 0.00301 | -0.00004 | -0.00015 | 0.00008 | 0.00361 | -0.00358 | 0.01081 |
| tanh_mlp_16_8 | 442 | 0.01104 | -0.00320 | 0.02647 | 0.00002 | -0.00114 | 0.00123 | 0.00505 | -0.00077 | 0.01364 |
| tanh_mlp_16_8 | 2025 | 0.00434 | -0.00427 | 0.01362 | -0.00189 | -0.00354 | -0.00046 | -0.00055 | -0.00996 | 0.00767 |
| tanh_mlp_16_8 | 9001 | 0.00520 | -0.00606 | 0.01726 | -0.00055 | -0.00169 | 0.00073 | 0.00410 | -0.00238 | 0.01104 |

## Guardrails

- Raw features and labels end before the sealed holdout.
- The scaler, imputer, and 24-cluster similarity model are refit on training rows for every split.
- Test rows are transformed but never used to fit cluster centers.
- Raw 3D kinematic variables are excluded.
- Uncertainty uses paired five-date blocks and placebos shuffle training labels within date.