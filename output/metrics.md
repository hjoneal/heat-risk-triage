# Model metrics

Extraction model: `gemini-3.5-flash-lite`
Training rows: 14400, failures: 163 (0.0113)
Cross-validation: GroupKFold(5) grouped on event_id

## Ablations

All three variants go through the same grouped cross-validation loop.

| Variant | Features | AUC | Precision@15 | Recall@15 |
|---|---|---|---|---|
| Heuristic baseline (peak temp x age) | 2 | 0.5830 | 0.0000 | 0.0000 |
| Without notes | 9 | 0.8223 | 0.0458 | 0.0377 |
| Full model | 13 | 0.8261 | 0.0792 | 0.0609 |

## Calibration

| Brier score, full model | 0.01049 |
|---|---|
| Brier score, base rate only | 0.01119 |
| Improvement | 0.00071 |

## Regularisation sweep

| C | Out-of-fold AUC |
|---|---|
| 0.01 | 0.8135 |
| 0.1 | 0.8264 |
| 1.0 | 0.8261 |
| 10.0 | 0.8219 |

## Coefficients

Fitted on all events, on standardised features. Sign is the direction of
the effect on the log-odds of failure.

| Feature | Coefficient |
|---|---|
| Peak temperature | +0.1060 |
| Accumulated heat above 30°C | +1.1381 |
| Warmest overnight minimum | -0.3009 |
| Consecutive warm nights | +0.1324 |
| Age | +0.0533 |
| Cooling system type | -0.8437 |
| Peak load | +0.5426 |
| Time since last maintenance | +0.1981 |
| Prior heat-related faults | +0.1144 |
| Cooling degraded (from inspection notes) | +0.1980 |
| Ventilation obstructed (from inspection notes) | +0.2197 |
| Oil issue (from inspection notes) | +0.2712 |
| Outstanding remedial work (from inspection notes) | +0.0092 |
| _intercept_ | -5.5967 |
