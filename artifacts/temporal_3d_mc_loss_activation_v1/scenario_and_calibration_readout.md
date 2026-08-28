# Temporal 3D and Monte Carlo Lab

- Status: visual lab complete; predictive ablation pending
- Pre-holdout cutoff: 2026-05-28
- Stocks: 120
- Animation frames: 90
- Monte Carlo paths per stock: 2000
- PCA explained variance: [0.370309, 0.199298, 0.129062]
- Final 60-date holdout: sealed

## Fastest latent movers on the final visual frame

| ticker | latent_velocity | ret_20d | graph_degree |
|---|---:|---:|---:|
| SNOW | 5.3804 | 0.6938 | 18.0000 |
| HOOD | 3.6525 | 0.1916 | 35.0000 |
| TER | 2.5236 | 0.2491 | 44.0000 |
| SNPS | 2.4478 | -0.0012 | 20.0000 |
| INTC | 2.2502 | 0.2759 | 22.0000 |
| SOFI | 2.1355 | 0.0931 | 27.0000 |
| PLTR | 1.4658 | 0.0389 | 19.0000 |
| NET | 1.4032 | 0.0761 | 7.0000 |
| TMO | 1.3782 | 0.0450 | 28.0000 |
| GLW | 1.3125 | 0.2045 | 37.0000 |

## Highest target-before-stop Monte Carlo probabilities

| ticker | target_before_stop_probability | expected_shortfall_5pct |
|---|---:|---:|
| TXN | 0.8970 | 0.0877 |
| CSCO | 0.8885 | -0.0085 |
| SYK | 0.8330 | -0.0436 |
| GS | 0.8055 | -0.0722 |
| NOW | 0.7995 | -0.2147 |
| AAPL | 0.7840 | -0.0813 |
| MS | 0.7615 | -0.1074 |
| STX | 0.7610 | -0.0305 |
| PM | 0.7575 | -0.1359 |
| GILD | 0.7510 | -0.1093 |

## Interpretation guardrails

- The 3D map creates hypotheses; it does not establish causal relationships.
- Monte Carlo paths are conditional historical resamples, not independent training observations.
- Loss and activation curves are an interactive mechanism explorer, not ablation results.
- Motion features require chronological ablation before context-gate promotion.
- No final holdout, brokerage, or live-trading behavior was opened or changed.
