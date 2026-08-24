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
| Cooling degraded (from inspection notes) | 380 | 0.922 | 0.958 | 0.652 | 1.000 |
| Ventilation obstructed (from inspection notes) | 374 | 0.895 | 0.979 | 0.744 | 1.000 |
| Oil issue (from inspection notes) | 407 | 0.875 | 0.963 | 0.702 | 0.725 |
| Outstanding remedial work (from inspection notes) | 390 | 0.855 | 1.000 | 0.826 | 0.826 |

## Errors by note category

An error is one wrong flag decision; each note carries four.

| Category | Notes | Flag decisions | LLM errors | LLM error rate | Keyword errors | Keyword error rate |
|---|---|---|---|---|---|---|
| straightforward positive | 864 | 3456 | 38 | 0.0110 | 327 | 0.0946 |
| resolution present | 144 | 576 | 75 | 0.1302 | 194 | 0.3368 |
| negation present | 148 | 592 | 122 | 0.2061 | 184 | 0.3108 |
| distractors only | 644 | 2576 | 0 | 0.0000 | 0 | 0.0000 |

## Evidence and extraction failures

- Evidence quotes checked: 1708
- Quotes not found verbatim in their note: 0 (0.00%)
- Extractions that failed after one retry: 0 of 1800 (0.00%)

A failed extraction sets no flags and marks the asset
`extraction_status = "failed"`; the interface then says the condition
data is unavailable rather than showing four clean negatives.
