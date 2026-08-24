# Validation

## Leakage

Feature matrix columns equal `FEATURES` exactly, in order: 13 columns.

Hazard features derive from ambient temperature alone. `theta`, `tau`,
`load_rise`, `condition` and `thermal_stress` never enter the matrix.
The correlations below are computed against the hidden state that
generated the outcomes, which only this module may read.

Highest absolute correlation with hidden state: **0.7695** (threshold 0.95).

| Feature | vs condition | vs thermal stress |
|---|---|---|
| Accumulated heat above 30°C | +0.0000 | +0.7695 |
| Warmest overnight minimum | -0.0000 | +0.6610 |
| Consecutive warm nights | -0.0000 | +0.5083 |
| Time since last maintenance | +0.4943 | -0.0242 |
| Peak temperature | -0.0000 | +0.4378 |
| Ventilation obstructed (from inspection notes) | +0.4082 | -0.0158 |
| Age | +0.4079 | -0.0314 |
| Cooling system type | +0.0068 | -0.3844 |
| Outstanding remedial work (from inspection notes) | +0.3710 | +0.0034 |
| Cooling degraded (from inspection notes) | +0.3620 | +0.0016 |
| Oil issue (from inspection notes) | +0.3614 | -0.0073 |
| Peak load | -0.0276 | +0.2392 |
| Prior heat-related faults | +0.2205 | -0.0203 |

## Citation integrity

**100.00%** of briefs cite only documents they were given (75 of 75).
