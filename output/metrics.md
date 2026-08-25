# Model metrics

Extraction model: `gemini-3.5-flash-lite`
Training rows: 14400, failures: 138 (0.0096)
Cross-validation: GroupKFold(5) grouped on event_id

## Ablations

Every variant goes through the same grouped cross-validation loop. The two
middle rows differ from the full model in one respect each, so the notes
and the interaction terms can be credited separately rather than jointly.

Within-event AUC ranks assets against each other under one forecast, which
is the only comparison the crew makes. The pooled figure also rewards
telling a severe event from a mild one — easy from the hazard features and
already known from the forecast — and so runs well above it.

| Variant | Features | Within-event AUC | Pooled AUC | Precision@15 | Recall@15 |
|---|---|---|---|---|---|
| Heuristic baseline (peak temp x age) | 2 | 0.5487 | 0.6039 | 0.0042 | 0.0018 |
| Register only | 8 | 0.6469 | 0.8480 | 0.0667 | 0.0599 |
| Register + notes | 12 | 0.6605 | 0.8485 | 0.0458 | 0.0275 |
| Register + interactions (no notes) | 10 | 0.6107 | 0.8431 | 0.0625 | 0.0471 |
| Full model | 15 | 0.6216 | 0.8432 | 0.0542 | 0.0627 |

## Crew capacity

Capacity is a client operating parameter, not a property of the system, and
the 15 this build reports at was chosen rather than derived. A
monthly substation inspection cadence puts realistic pre-event capacity nearer 30
(see config.py). 15 is retained as the conservative case and
the range is reported here rather than folded into the headline.

Full model, same out-of-fold predictions and the same per-event averaging as above.

| Capacity | Precision@k | Recall@k | % of fleet |
|---|---|---|---|
| 10 | 0.0563 | 0.0239 | 1.1% |
| 15 (default) | 0.0542 | 0.0627 | 1.7% |
| 20 | 0.0500 | 0.0695 | 2.2% |
| 25 | 0.0500 | 0.1065 | 2.8% |
| 30 | 0.0542 | 0.1344 | 3.3% |
| 40 | 0.0516 | 0.1582 | 4.4% |

### By variant

The k=15 column is the ablation table above. The rest answers what a single
capacity cannot: whether a feature set that looks worse at one capacity is
worse across the range, or whether the ordering just moves around.

Read these as counts, not as rates. Each figure rests on the failures found
in the top k of 900, summed over the 16 events, and those totals run from
about 10 to 35 out of 138. One or two hits move a rate in the third decimal
place, so a variant leading one column and trailing the next is noise rather
than a finding, and none of the gaps here is large enough to rank the middle
three variants against each other. Three of the sixteen events contain no
failures at all and contribute a guaranteed zero to every precision figure.

**Precision@k**

| Variant | k=10 | k=15 | k=20 | k=25 | k=30 | k=40 |
|---|---|---|---|---|---|---|
| Heuristic baseline (peak temp x age) | 0.0063 | 0.0042 | 0.0031 | 0.0050 | 0.0063 | 0.0094 |
| Register only | 0.0688 | 0.0667 | 0.0656 | 0.0625 | 0.0583 | 0.0500 |
| Register + notes | 0.0625 | 0.0458 | 0.0562 | 0.0475 | 0.0458 | 0.0469 |
| Register + interactions (no notes) | 0.0688 | 0.0625 | 0.0688 | 0.0650 | 0.0604 | 0.0547 |
| Full model | 0.0563 | 0.0542 | 0.0500 | 0.0500 | 0.0542 | 0.0516 |

**Recall@k**

| Variant | k=10 | k=15 | k=20 | k=25 | k=30 | k=40 |
|---|---|---|---|---|---|---|
| Heuristic baseline (peak temp x age) | 0.0018 | 0.0018 | 0.0018 | 0.0274 | 0.0338 | 0.0484 |
| Register only | 0.0353 | 0.0599 | 0.0713 | 0.0856 | 0.0955 | 0.1119 |
| Register + notes | 0.0257 | 0.0275 | 0.0855 | 0.0873 | 0.0973 | 0.1233 |
| Register + interactions (no notes) | 0.0389 | 0.0471 | 0.0952 | 0.1048 | 0.1354 | 0.1696 |
| Full model | 0.0239 | 0.0627 | 0.0695 | 0.1065 | 0.1344 | 0.1582 |

**Failures found in the top k, summed over the 16 events (of 138)**

| Variant | k=10 | k=15 | k=20 | k=25 | k=30 | k=40 |
|---|---|---|---|---|---|---|
| Heuristic baseline (peak temp x age) | 1 | 1 | 1 | 2 | 3 | 6 |
| Register only | 11 | 16 | 21 | 25 | 28 | 32 |
| Register + notes | 10 | 11 | 18 | 19 | 22 | 30 |
| Register + interactions (no notes) | 11 | 15 | 22 | 26 | 29 | 35 |
| Full model | 9 | 13 | 16 | 20 | 26 | 33 |

## Calibration

| Brier score, full model | 0.00883 |
|---|---|
| Brier score, base rate only | 0.00949 |
| Improvement | 0.00066 |

## Regularisation sweep

| C | Out-of-fold AUC |
|---|---|
| 0.01 | 0.8494 |
| 0.1 | 0.8467 |
| 1.0 | 0.8432 |
| 10.0 | 0.8424 |

## Coefficients

Fitted on all events, on standardised features. Sign is the direction of
the effect on the log-odds of failure. The fold mean and standard
deviation come from the same GroupKFold split used above: interaction
terms are correlated with the features they are built from, so a wide
spread there is expected and is reported rather than smoothed away.

| Feature | Coefficient | Fold mean | Fold SD | Sign flips |
|---|---|---|---|---|
| Peak temperature | -0.3062 | -0.2817 | 0.3204 | yes |
| Accumulated heat above 30°C | +0.9708 | +0.9263 | 0.2579 | no |
| Consecutive warm nights | +0.1701 | +0.2254 | 0.1821 | no |
| Age | +0.1106 | +0.1143 | 0.0838 | yes |
| Cooling system type | -0.5692 | -0.5493 | 0.1695 | no |
| Peak load | +0.5683 | +0.5656 | 0.0470 | no |
| Time since last maintenance | +0.2506 | +0.2540 | 0.0572 | no |
| Prior heat-related faults | +0.2175 | +0.2205 | 0.0302 | no |
| Cooling degraded (from inspection notes) | +0.0374 | +0.0454 | 0.0565 | no |
| Ventilation obstructed (from inspection notes) | +0.0164 | +0.0144 | 0.0095 | no |
| Oil issue (from inspection notes) | +0.1567 | +0.1527 | 0.0278 | no |
| Outstanding remedial work (from inspection notes) | +0.0362 | +0.0318 | 0.0704 | yes |
| Heavy load through sustained heat | +0.3906 | +0.3836 | 0.0795 | no |
| Known defects through sustained heat | +0.0386 | +0.0258 | 0.0615 | yes |
| Ageing asset with warm nights | +0.1919 | +0.1966 | 0.1202 | no |
| _intercept_ | -5.8342 | | | |

## Ranking divergence across scenarios

Assets differing in the top 15, pairwise. Priority is
the crew's queue order; risk is the model's own ranking. Measured, not gated.

| Scenario | Scenario | Differing by priority | Differing by risk |
|---|---|---|---|
| short-severe | long-moderate | 4 | 5 |
| short-severe | long-severe | 5 | 6 |
| short-severe | baseline-mild | 4 | 7 |
| long-moderate | long-severe | 3 | 5 |
| long-moderate | baseline-mild | 7 | 12 |
| long-severe | baseline-mild | 8 | 13 |
