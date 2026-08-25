# Extraction evaluation

Model: `gemini-3.5-flash-lite`, prompt version `v1`.
Notes: 1800. Compared against `data/inspection_truth.csv`.

The keyword baseline is a deliberately naive comparator: per-flag term
lists, case-insensitive substring match, no handling of a defect that
was resolved or explicitly negated. The gap between the two is the
value the extraction layer adds.

## Per-flag precision and recall

| Flag | Actual positives | LLM precision | LLM recall | Keyword precision | Keyword recall |
|---|---|---|---|---|---|
| Cooling degraded (from inspection notes) | 412 | 0.957 | 0.976 | 0.695 | 1.000 |
| Ventilation obstructed (from inspection notes) | 410 | 0.856 | 0.985 | 0.722 | 1.000 |
| Oil issue (from inspection notes) | 422 | 0.913 | 0.964 | 0.718 | 0.737 |
| Outstanding remedial work (from inspection notes) | 385 | 0.837 | 1.000 | 0.818 | 0.855 |

## Errors by note category

An error is one wrong flag decision; each note carries four.

| Category | Notes | Flag decisions | LLM errors | LLM error rate | Keyword errors | Keyword error rate |
|---|---|---|---|---|---|---|
| straightforward positive | 908 | 3632 | 26 | 0.0072 | 330 | 0.0909 |
| resolution present | 159 | 636 | 82 | 0.1289 | 203 | 0.3192 |
| negation present | 146 | 584 | 123 | 0.2106 | 168 | 0.2877 |
| distractors only | 587 | 2348 | 0 | 0.0000 | 0 | 0.0000 |

## Evidence and extraction failures

- Evidence quotes checked: 1798
- Quotes not found verbatim in their note: 0 (0.00%)
- Extractions that failed after one retry: 0 of 1800 (0.00%)

A failed extraction sets no flags and marks the asset
`extraction_status = "failed"`; the interface then says the condition
data is unavailable rather than showing four clean negatives.
