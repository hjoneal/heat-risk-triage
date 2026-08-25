# Validation

## Leakage

Feature matrix columns equal `FEATURES` exactly, in order: 16 columns.

Hazard features derive from ambient temperature alone. `theta`, `tau`,
the hourly load rise, `condition` and `thermal_stress` never enter the
matrix. The three interaction columns are products of features already
in it, so they cross no boundary the components did not already sit on:
`peak_load_pct` and `age_years` come from the asset register, the
condition flag count from the extracted notes, and the hazard terms from
ambient temperature.
The correlations below are computed against the hidden state that
generated the outcomes, which only this module may read.

Highest absolute correlation with hidden state: **0.7234** (threshold 0.95).

| Feature | vs condition | vs thermal stress |
|---|---|---|
| Accumulated heat above 30°C | -0.0000 | +0.7234 |
| Warmest overnight minimum | -0.0000 | +0.6397 |
| Consecutive warm nights | -0.0000 | +0.5176 |
| Time since last maintenance | +0.4723 | -0.0204 |
| Peak load | -0.0030 | +0.4374 |
| Age | +0.4013 | -0.0367 |
| Peak temperature | +0.0000 | +0.3962 |
| Ventilation obstructed (from inspection notes) | +0.3816 | -0.0061 |
| Outstanding remedial work (from inspection notes) | +0.3576 | +0.0031 |
| Oil issue (from inspection notes) | +0.3547 | -0.0169 |
| Heavy load through sustained heat | -0.0000 | +0.3480 |
| Cooling degraded (from inspection notes) | +0.3455 | +0.0037 |
| Cooling system type | +0.0224 | -0.2625 |
| Prior heat-related faults | +0.2581 | -0.0255 |
| Ageing asset with warm nights | +0.0000 | -0.0161 |
| Known defects through sustained heat | +0.0000 | -0.0053 |

## Ranking quality against the Bayes ceiling

Ranking by the true generative probability is the best any model could do:
it uses the exact hidden state that produced the outcomes. Outcomes are
Bernoulli draws at roughly 1%, so most of the remaining variation is
irreducible.

| Ranked by | Pooled AUC | Within-event AUC | Precision@15 |
|---|---|---|---|
| The model (out-of-fold) | 0.8412 | 0.6213 | 0.0667 |
| True generative probability | 0.8683 | 0.7260 | 0.1077 |

The model reaches **85.6%** of the achievable within-event AUC and **61.9%** of the achievable precision at the crew's capacity.

## Citation integrity

**100.00%** of briefs reference only documents they were given (160 of 160).

Checked twice: the `cited_doc_ids` array, and every doc id written into the
prose. The second check exists because the first reported 100% clean while two
briefs named a procedure in a sentence that had never been supplied — a valid
array beside an invented reference. A supplied document may itself cite another
procedure by id, and passing that id on reads as an attached document when it is
not one.

| Check | Failing |
|---|---|
| `cited_doc_ids` outside the retrieved set | 0 |
| doc id in the prose outside the retrieved set | 0 |
