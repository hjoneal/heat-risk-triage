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
| Register only | 9 | 0.6468 | 0.8467 | 0.0667 | 0.0599 |
| Register + notes | 13 | 0.6604 | 0.8472 | 0.0458 | 0.0275 |
| Register + interactions (no notes) | 11 | 0.6104 | 0.8412 | 0.0625 | 0.0471 |
| Full model | 16 | 0.6213 | 0.8412 | 0.0542 | 0.0627 |

## Crew capacity

Capacity is a client operating parameter, not a property of the system, and
the 15 this build reports at was chosen rather than derived. A
monthly substation inspection cadence puts realistic pre-event capacity nearer 30
(see config.py). 15 is retained as the conservative case and
the range is reported here rather than folded into the headline.

Full model, same out-of-fold predictions and the same per-event averaging as above.

| Capacity | Precision@k | Recall@k | % of fleet |
|---|---|---|---|
| 10 | 0.0625 | 0.0495 | 1.1% |
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
| Register + notes | 0.0625 | 0.0458 | 0.0562 | 0.0475 | 0.0437 | 0.0469 |
| Register + interactions (no notes) | 0.0688 | 0.0625 | 0.0688 | 0.0650 | 0.0604 | 0.0547 |
| Full model | 0.0625 | 0.0542 | 0.0500 | 0.0500 | 0.0542 | 0.0516 |

**Recall@k**

| Variant | k=10 | k=15 | k=20 | k=25 | k=30 | k=40 |
|---|---|---|---|---|---|---|
| Heuristic baseline (peak temp x age) | 0.0018 | 0.0018 | 0.0018 | 0.0274 | 0.0338 | 0.0484 |
| Register only | 0.0353 | 0.0599 | 0.0713 | 0.0856 | 0.0955 | 0.1119 |
| Register + notes | 0.0257 | 0.0275 | 0.0855 | 0.0873 | 0.0955 | 0.1233 |
| Register + interactions (no notes) | 0.0389 | 0.0471 | 0.0952 | 0.1048 | 0.1354 | 0.1696 |
| Full model | 0.0495 | 0.0627 | 0.0695 | 0.1065 | 0.1344 | 0.1582 |

**Failures found in the top k, summed over the 16 events (of 138)**

| Variant | k=10 | k=15 | k=20 | k=25 | k=30 | k=40 |
|---|---|---|---|---|---|---|
| Heuristic baseline (peak temp x age) | 1 | 1 | 1 | 2 | 3 | 6 |
| Register only | 11 | 16 | 21 | 25 | 28 | 32 |
| Register + notes | 10 | 11 | 18 | 19 | 21 | 30 |
| Register + interactions (no notes) | 11 | 15 | 22 | 26 | 29 | 35 |
| Full model | 10 | 13 | 16 | 20 | 26 | 33 |

## Calibration

| Brier score, full model | 0.00882 |
|---|---|
| Brier score, base rate only | 0.00949 |
| Improvement | 0.00068 |

## Regularisation sweep

| C | Out-of-fold AUC |
|---|---|
| 0.01 | 0.8521 |
| 0.1 | 0.8475 |
| 1.0 | 0.8412 |
| 10.0 | 0.8366 |

## Coefficients

Fitted on all events, on standardised features. Sign is the direction of
the effect on the log-odds of failure. The fold mean and standard
deviation come from the same GroupKFold split used above: interaction
terms are correlated with the features they are built from, so a wide
spread there is expected and is reported rather than smoothed away.

| Feature | Coefficient | Fold mean | Fold SD | Sign flips |
|---|---|---|---|---|
| Peak temperature | -0.2438 | -0.2336 | 0.3267 | yes |
| Accumulated heat above 30°C | +1.0919 | +1.0244 | 0.2472 | no |
| Warmest overnight minimum | -0.3060 | -0.2363 | 0.2728 | yes |
| Consecutive warm nights | +0.3154 | +0.3304 | 0.0878 | no |
| Age | +0.1150 | +0.1166 | 0.0865 | yes |
| Cooling system type | -0.5696 | -0.5495 | 0.1696 | no |
| Peak load | +0.5738 | +0.5674 | 0.0504 | no |
| Time since last maintenance | +0.2507 | +0.2540 | 0.0572 | no |
| Prior heat-related faults | +0.2179 | +0.2206 | 0.0301 | no |
| Cooling degraded (from inspection notes) | +0.0374 | +0.0453 | 0.0563 | no |
| Ventilation obstructed (from inspection notes) | +0.0161 | +0.0141 | 0.0097 | no |
| Oil issue (from inspection notes) | +0.1564 | +0.1524 | 0.0280 | no |
| Outstanding remedial work (from inspection notes) | +0.0357 | +0.0315 | 0.0704 | yes |
| Heavy load through sustained heat | +0.3868 | +0.3827 | 0.0796 | no |
| Known defects through sustained heat | +0.0387 | +0.0260 | 0.0613 | yes |
| Ageing asset with warm nights | +0.1905 | +0.1963 | 0.1207 | no |
| _intercept_ | -5.8274 | | | |

## Ranking divergence across scenarios

Assets differing in the top 15, pairwise. Priority is
the crew's queue order; risk is the model's own ranking. Measured, not gated.

| Scenario | Scenario | Differing by priority | Differing by risk |
|---|---|---|---|
| short-severe | long-moderate | 3 | 5 |
| short-severe | long-severe | 4 | 6 |
| short-severe | baseline-mild | 4 | 7 |
| long-moderate | long-severe | 3 | 4 |
| long-moderate | baseline-mild | 7 | 12 |
| long-severe | baseline-mild | 8 | 13 |
