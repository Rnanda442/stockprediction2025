# Temporal Motion Predictive Ablation

- Winning activation: `tanh`
- Winning loss: `focal_gamma_1`
- Winning mean AUC: 0.51300
- Winning mean Brier: 0.25476
- Winning mean after-cost excess return: 0.00790
- Placebo mean AUC: 0.51192
- Final holdout opened: false

## Loss and activation ranking

| activation | loss | auc_mean | brier_mean | ece_10_mean | mean_excess_return_mean | selection_score |
|---|---|---:|---:|---:|---:|---:|
| tanh | focal_gamma_1 | 0.51300 | 0.25476 | 0.08432 | 0.00790 | 0.94000 |
| tanh | brier_mse | 0.51607 | 0.25664 | 0.09250 | 0.00731 | 0.93250 |
| tanh | binary_cross_entropy | 0.51527 | 0.25668 | 0.09172 | 0.00548 | 0.88500 |
| tanh | focal_gamma_2 | 0.50218 | 0.25556 | 0.08254 | 0.00165 | 0.86750 |
| tanh | return_aware_bce | 0.51507 | 0.25687 | 0.09344 | 0.00710 | 0.85250 |
| leaky_relu | focal_gamma_2 | 0.49847 | 0.25902 | 0.08843 | 0.00065 | 0.71750 |
| leaky_relu | binary_cross_entropy | 0.49660 | 0.26563 | 0.11005 | 0.00162 | 0.59000 |
| relu | focal_gamma_2 | 0.49963 | 0.26140 | 0.09549 | -0.00270 | 0.56750 |
| leaky_relu | return_aware_bce | 0.49687 | 0.26656 | 0.11332 | 0.00104 | 0.53500 |
| leaky_relu | brier_mse | 0.49627 | 0.26614 | 0.11240 | 0.00105 | 0.51500 |
| leaky_relu | focal_gamma_1 | 0.49657 | 0.26376 | 0.10031 | -0.00109 | 0.48250 |
| relu | focal_gamma_1 | 0.49497 | 0.26297 | 0.10268 | -0.00109 | 0.47750 |
| gelu | focal_gamma_2 | 0.49217 | 0.26331 | 0.09774 | -0.00075 | 0.39500 |
| relu | brier_mse | 0.49455 | 0.26756 | 0.11370 | -0.00042 | 0.35750 |
| relu | return_aware_bce | 0.49437 | 0.26749 | 0.11232 | -0.00101 | 0.33250 |
| gelu | focal_gamma_1 | 0.48675 | 0.26662 | 0.10388 | -0.00075 | 0.30500 |
| relu | binary_cross_entropy | 0.49419 | 0.26741 | 0.11360 | -0.00117 | 0.26750 |
| gelu | return_aware_bce | 0.49244 | 0.27121 | 0.12081 | -0.00084 | 0.19000 |
| gelu | binary_cross_entropy | 0.49223 | 0.27017 | 0.11849 | -0.00143 | 0.15000 |
| gelu | brier_mse | 0.49289 | 0.27046 | 0.11888 | -0.00284 | 0.14000 |

## Feature-set ranking

| feature_set | auc_mean | brier_mean | ece_10_mean | mean_excess_return_mean | selection_score |
|---|---:|---:|---:|---:|---:|
| base_plus_all_motion | 0.51476 | 0.25600 | 0.09155 | 0.00785 | 0.76250 |
| base_only | 0.51004 | 0.25500 | 0.08938 | 0.01239 | 0.75000 |
| base_plus_graph_motion | 0.51149 | 0.25663 | 0.08814 | 0.00124 | 0.56250 |
| base_plus_kinematic | 0.48763 | 0.25643 | 0.08584 | -0.00283 | 0.42500 |

## Motion leave-one-feature-out

| dropped_feature | auc_drop | brier_harm | excess_return_drop |
|---|---:|---:|---:|
| crowding_change_5d | 0.01094 | 0.00206 | 0.00908 |
| graph_regime_residence_days | 0.00619 | -0.00123 | 0.00923 |
| graph_cluster_switch_count_20d | 0.00378 | -0.00025 | 0.00560 |
| latent_velocity | -0.00105 | 0.00367 | 0.00173 |
| latent_acceleration | -0.00133 | 0.00486 | 0.00217 |
| latent_radial_expansion | -0.00285 | 0.00601 | -0.00576 |
| neighbor_convergence_velocity | -0.00315 | 0.00402 | 0.00201 |
| latent_path_curvature | -0.00594 | 0.00344 | -0.00110 |

## Guardrails

- All fits use chronological train, validation, embargo, and test windows.
- Scaling and imputation are fit on training rows only.
- Portfolio returns include the configured transaction cost and five-date rebalance spacing.
- Monte Carlo paths are not used as independent training observations.
- Results remain provisional until reviewed; the final 60-date holdout remains sealed.
