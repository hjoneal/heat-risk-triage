# heat-risk-triage

Ranking 900 substation transformers by heat-failure risk 72 hours ahead of a forecast heat event, so
a crew that can reach 15 of them knows which 15.

The output is a ranked queue with an explanation attached to every row: which factors drove the
score, what the recorded condition of the asset is, in the inspector's own words, and which
maintenance procedure applies.

All data is synthetic. Every number below came out of a run and is reproduced from the files in
`output/`.

## Problem

A utility receives a heat forecast. Some transformers will fail; the crew can reach a fraction of the
fleet before the event begins. Ranking by forecast severity alone is wrong, because failure depends
on the interaction between hazard exposure and the condition the asset is already in — and condition
is recorded in free-text inspection notes rather than in structured fields.

Heat damage also accumulates. Oil temperature lags ambient by a few hours, so a transformer
substantially *does* reset overnight: at the fast end of the modelled range it sheds 96% of its
offset over an eight-hour night. What the overnight minimum sets is the **floor it resets to**. A
night at 27°C instead of 18°C starts the next day nine degrees hotter, and each successive day
reaches a higher peak from a higher base. Because insulation ageing is Arrhenius and irreversible,
five days of elevated temperature does five days of damage that the weather breaking does not give
back.

That is why thermal stress here is accumulated *equivalent ageing* rather than a peak, and why
`consecutive_warm_nights` is a feature alongside degree-hours. `max_overnight_min_c` was one too and
was later dropped for collinearity — 98% predictable from the rest — though it is still computed and
still shown on the forecast strip. See `DECISIONS.md` D-040.

The second mechanism is that heat raises demand at the same time it degrades cooling. Air
conditioning load climbs with ambient temperature, and resistive loss goes as the square of current,
so a heavily loaded transformer diverges from a lightly loaded one as the day gets hotter rather than
tracking it at a fixed offset. Measured in the generator, the ratio of mean thermal stress between
the top and bottom load quintiles is 7.1 on a long-moderate event against 3.0 under a static load
assumption. That differential is what a forecast can act on, and it is why `peak_load_pct` appears
both on its own and multiplied by degree-hours.

## Architecture

![Architecture](heat_risk_architecture_diagram.svg)

A batch pipeline writes JSON to `output/`; the web application only reads it.

1. **Extraction** (`extract.py`) — an LLM turns 1,800 free-text inspection notes into four boolean
   condition flags, each with a verbatim evidence quote. Two rules do the work: a defect the note
   records as *fixed* is false, and a defect the note records as *absent* is false. A ~20-line
   keyword baseline exists only as an evaluation comparator.
2. **Risk model** (`features.py`, `model.py`) — 15 features (3 hazard, 3 asset static, 6 condition,
   3 interaction), `StandardScaler` → `LogisticRegression`, `GroupKFold(5)` grouped on `event_id`,
   evaluated within event and pooled out-of-fold, then refit on all 16 events to score the four demo
   scenarios. The interaction terms exist because every hazard feature is constant within an event:
   without them the forecast adds the same number to every asset's log-odds and cannot reorder
   anything at all.
3. **Retrieval** (`retrieve.py`) — BM25 over 25 procedure documents, whole-document rather than
   chunked because 300 words is already the unit a supervisor reads. Applicability is filtered before
   relevance is scored: two procedures cover forced-air cooling systems, which a naturally-cooled
   ONAN unit does not have, and a document does not become applicable by ranking well. The query is
   built deterministically from the asset's positive feature contributions, then an LLM writes an
   action brief that may reference only the documents it was given — checked in its citation array
   *and* in its prose. D-037, D-038.
4. **Validation** (`validate.py`, `tests/`) — extraction against generation-time truth, a leakage
   check, and the Bayes ceiling. 312 tests across six files: retrieval behaviour
   (`test_retrieval.py`), citation integrity (`test_citations.py`), the claim that the interaction
   terms are what let the forecast reorder the queue (`test_ranking.py`), what the interface must not
   misreport (`test_interface.py`), whether the committed notebook still matches the pipeline
   (`test_notebook.py`), and whether this README's tables still match `output/` (`test_readme.py`).

The asset page reports each factor's **effect on the odds** of failure — the exponential of its
log-odds contribution, which is exact rather than a simplification — with the reading in its own
units and a marker showing where that reading sits across the training range. Factors run from the
one that raised this asset's odds most to the one that lowered them most. The log-odds figures stay
in the scored JSON and in `metrics.md`; the screen carries the readable unit only, and a test checks
that the displayed multipliers and the baseline compose back to the risk shown. D-032, D-035.

Queue columns sort on click. The crew-capacity line is drawn only when the queue is in dispatch
order, because in any other order it would assert that the rows above it get visited. D-033.

One 30-line script (`static/app.js`) makes the capacity slider live and then hides the Apply button
it replaced. It issues no request of its own, and the interface works without it. D-034.

The queue carries a crew-capacity control (5–25), which decides where the line falls across the
ranking. Beside it the page reports what those visits cover: at the long-moderate scenario the top 25
assets are 2.8% of the fleet and carry 9.6% of its expected failures, 3.4× the risk of visiting 25 at
random. That is a statement about where risk is concentrated, not about how much of it a visit
removes — the model has no estimate of intervention effectiveness. D-032.

Forecast values are **not** adjustable, but no longer because they would change nothing. An earlier
build measured the ranking as invariant to hazard, and it was: with only additive hazard features,
every asset in a scenario receives the same addition to its log-odds, and adding a constant cannot
reorder anything. The three interaction terms exist to remove that property, and they do — the top 15
now differs by 3 to 9 assets between scenarios. What remains fixed is the *set of scenarios*: the
four are precomputed, because scoring an arbitrary forecast would mean running the model at request
time, which the offline-at-serve-time constraint rules out. See `DECISIONS.md` D-022 (superseded in
part), D-024, D-026.

Priority is `risk × customers_served` — expected customers affected — so calibration matters as much
as discrimination. There is no class weighting, no resampling, and no post-hoc calibration layer.

**Not every ranked asset needs a crew.** Each asset carries an intervention type derived from the
largest positive contribution among the drivers something can be done about: *crew visit* where that
is a condition flag, an overdue maintenance interval or a prior fault history; *load transfer* where
it is loading, which is remedied from the control room by moving demand to an adjacent feeder;
*monitor only* where nothing actionable is raising its risk. It is a rule over contributions that
already exist in the scored JSON — no model change, no extra field on the model, and no LLM call.

The queue shows the whole fleet with a filter over the three types, because the capacity line lands
around rank 44 to 97 and a queue capped at 40 rows could not show where its own line fell. Briefs
still cover the top 40 by expected impact; rows below that are ranked and explained but marked as
carrying no brief. See `DECISIONS.md` D-050.

The consequence is that the crew budget constrains only part of the queue. The capacity line falls
after the *n*th condition-ranked row rather than the *n*th row, which in the `short-severe` scenario
is rank 52 rather than rank 15; the 37 assets above it that are ranked on loading consume no crew
capacity. Selecting over all fifteen features instead was tried first and is degenerate — see
`DECISIONS.md` D-048 for the measurements, which are the reason the pool is restricted.

**The badge names the driver, not the remedy.** It read "Crew visit" / "Load transfer" and was
changed: one argmax establishes what is driving an asset's position, not that a truck is
unnecessary, and 400 of 534 load-ranked assets in `short-severe` carried a recorded defect. What to
do is the action brief's job, which can say *transfer load and repair the fan* where a single-valued
badge cannot. Three alternative rules were built and measured before the labels were changed; all
three fail, and how they fail is recorded in `DECISIONS.md` D-051.

Explanations are the model's own arithmetic: each contribution is `coefficient × standardised value`,
and they sum to `logit(p) − intercept`, asserted to 1e-6. No SHAP.

### Design constraints

- **Deterministic.** One seed. The same inputs produce byte-identical outputs; `generated_at` is a
  fixed constant rather than the clock, for that reason.
- **Runs with no API key.** Every LLM call is cached to disk by content hash and the cache is
  committed. `--offline` fails loudly on a cache miss rather than reaching for the network.
- **Offline at serve time.** `app.py` loads the scored JSON and briefs at startup. No inference, no
  network call, no CDN asset, no web font, no map tile at request time. Its only write is appending
  to `output/decisions.jsonl`. The one script it serves is local, 30 lines, and issues no request.
- **No leakage.** Hazard features derive from ambient temperature alone. `theta`, `tau`, the hourly
  load rise, `condition` and `thermal_stress` never enter the feature matrix. Only `validate.py`
  opens the diagnostic files.

## Running

Python 3.11+.

```bash
pip install -r requirements.txt

python generate_data.py   # data/, output/data_checks.txt
python extract.py         # cache/extractions/, output/extraction_cost.txt
python model.py           # output/scored_*.json, metrics.{json,md}, calibration.png
python retrieve.py        # output/briefs_*.json, output/bm25_scores.txt
python validate.py        # output/extraction_eval.md, output/validation.md
pytest tests/

uvicorn app:app --reload
```

Each script is independently re-runnable and idempotent. `extract.py` and `retrieve.py` accept
`--offline` to read the committed cache only. To re-run the LLM layers against the API, put a key in
`.env` as `GEMINI_API_KEY=...` — `.env` is gitignored.

## Scale

| Item | Count |
|---|---|
| Assets | 900 |
| Historical heat events | 16 |
| Training rows | 14,400 |
| Failures | 138 (0.96%) |
| Inspection notes | 1,800 (1,298 distinct texts) |
| Procedure documents | 25 |
| Demo scenarios | 4 |
| Action briefs | 160 |
| Crew capacity | 15 (sweep 10–40) |

The fleet size is derived from the client brief rather than assumed: 8M residents ÷ ~2.5 per
household ≈ 3.2M customer accounts; ÷ ~8,000 per substation ≈ 400 distribution substations; × ~2.2
transformers each ≈ 900. The generated fleet totals 3,216,114 customers, which closes the loop.

## Assumptions

Every constant lives in `config.py` marked *chosen*, *measured* or *assumed*. The ones that carry
weight:

| Assumption | Value | Basis |
|---|---|---|
| Failure rate per asset per event | 0.01 | Inflated ~12× for trainability; see below |
| Ageing reference temperature | 38.0 °C | Proxy scale; this model tracks bulk oil, not winding hot-spot |
| Montsinger halving interval | 6 °C | Ageing rate doubles per 6 °C; literature range 6–8 |
| Oil thermal time constant | 3–8 h | Bulk oil, not winding (which responds in minutes) |
| Warm-night threshold | 24.0 °C | Overnight minimum above which recovery is materially reduced |
| Hazard coefficient scale | 1.14 | Measured; the centre of the band gate 4 and the AUC ceiling leave |
| Customers per MVA | 92 | Substations run N-1, so customers per transformer sit well below rating |
| Cooling offset | ONAN +2, ONAF 0, OFAF −2 °C | Forced cooling sheds heat |
| Condition weights | age .35, maintenance .30, faults .20, noise .15 | Assumed |
| Crew capacity | 15 default, 20–50 plausible | Derived from a monthly substation inspection cadence: 400 substations ÷ 21 working days × 3 days ≈ 57 substation visits of nominal capacity, ~50% divertible to targeted pre-event work. Reported as a sweep rather than a single value |

### The failure rate is not an annual rate

CIGRE Technical Brochure 642 (2015), WG A2.37, *Transformer Reliability Survey* — 964 major failures
across 167,459 transformer-years, 56 utilities, 21 countries — puts substation transformer failure
below 1% per year: 0.8% for pre-1978 units, 0.4% for post-1978 units up to 20 years old. This fleet
skews old, so 0.8% applies.

**CIGRE supports the annual figure only.** It says nothing about a per-event rate. Deriving one — 900
assets × 0.8% annual × ~40% heat-attributable ÷ ~4 events a year — gives roughly **0.08% per
asset-event**. The generator uses **1%**, about twelve times that, because at the real rate 14,400
rows would carry about a dozen positives, too few to fit 15 features.

This scales predicted probabilities and preserves ranking, and the system consumes a ranking.
Calibration is to the synthetic base rate; a production deployment would recalibrate against observed
outcomes.

## Measured results

### Data gates

All six pass. `output/data_checks.txt`.

| Gate | Measured | Required |
|---|---|---|
| 1. Realised failure rate | 0.0096 | 0.008–0.013 |
| 2. Long-moderate stress > short-severe | 2.56 vs 1.57 equivalent days | strictly greater |
| 3. Non-zero stress on heat events | 0.9962 | ≥ 0.80 |
| 4. Mild-event failure rate | 0.0015 | < 0.002 |
| 5. Degree-hours vs peak correlation | 0.4961 | < 0.85 |
| 6. Failures in better-maintained half | 0.2609 (36 of 138) | ≥ 0.20 |

Gate 4 and the leakage ceiling on out-of-fold AUC bound the hazard scale from opposite sides and
leave a feasible band of [0.98, 1.35]; 1.14 is its centre. Gate 4 fails at 0.95, the AUC ceiling at
1.40. Both lower-bound checks turn on single-figure failure counts, so the band is narrow by
construction and moves with the generator's random stream — an early sweep that drew failures from a
fresh stream rather than continuing the pipeline's own reported a band that does not exist and
concluded, wrongly, that no value satisfied every gate. Details in `DECISIONS.md` D-027.

A seventh gate was specified — the top 15 must differ by at least three assets between any two
scenarios — and is not present. It was measured as unreachable, then became reachable after two later
changes, and was left out rather than reinstated at a threshold now known to be exactly met.
`DECISIONS.md` D-026.

Failures by event type: mild 4, short-severe 8, long-moderate 32, long-severe 94. 13 of the 16 events
carry at least one failure; the three that do not are mild events, which is the intended behaviour of
gate 4 rather than a sampling accident.

### Extraction

1,800 notes, `gemini-3.5-flash-lite`, temperature 0. The cache is keyed on note text and only
**1,298 of the 1,800 texts are distinct**, so a build from empty makes 1,298 calls and costs
**1,005,218 input and 144,882 output tokens**. A re-run makes zero calls.

Those token figures were previously reported as 1,382,833 and 184,879, an overstatement of 38% and
28%: the counts are stored per cache entry but were summed per inspection, so the 502 duplicate
notes were charged twice. `DECISIONS.md` D-045.

**Zero extractions failed and zero came back wrapped in markdown fences. One was retried and
succeeded on the second attempt. Zero of 1,798 evidence quotes were not found verbatim in their
note** (spec expectation: under 2%).

| Flag | Actual positives | LLM P | LLM R | Keyword P | Keyword R |
|---|---|---|---|---|---|
| Cooling degraded | 412 | 0.957 | 0.976 | 0.695 | 1.000 |
| Ventilation obstructed | 410 | 0.856 | 0.985 | 0.722 | 1.000 |
| Oil issue | 422 | 0.913 | 0.964 | 0.718 | 0.737 |
| Outstanding remedial work | 385 | 0.837 | 1.000 | 0.818 | 0.855 |

The keyword baseline reaches recall 1.000 on two flags precisely because it cannot tell a fixed
defect from an outstanding one — it flags everything the words appear in. The cost shows up in
precision, and in the category breakdown:

| Note category | Notes | LLM error rate | Keyword error rate |
|---|---|---|---|
| Straightforward positive | 864 | 0.0110 | 0.0946 |
| Resolution present | 144 | 0.1302 | 0.3368 |
| Negation present | 148 | 0.2061 | 0.3108 |
| Distractors only | 644 | 0.0000 | 0.0000 |

Negation is the hardest case for both. A 20.6% error rate on it is the weakest measured result in
the extraction layer and is not hidden here.

### Model

Out-of-fold across 14,400 rows, `GroupKFold(5)` on `event_id`. 138 failures, base rate 0.96%.

| Variant | Features | Within-event AUC | Pooled AUC | Precision@15 | Recall@15 |
|---|---|---|---|---|---|
| Heuristic baseline (peak temp x age) | 2 | 0.5487 | 0.6039 | 0.0042 | 0.0018 |
| Register only | 8 | 0.6469 | 0.8480 | 0.0667 | 0.0599 |
| Register + notes | 12 | 0.6605 | 0.8485 | 0.0458 | 0.0275 |
| Register + interactions (no notes) | 10 | 0.6107 | 0.8431 | 0.0625 | 0.0471 |
| Full model | 15 | 0.6216 | 0.8432 | 0.0542 | 0.0627 |

**Read the within-event column, not the pooled one.** Pooling puts a mild event's rows beside a
severe event's, so a model scores partly by telling those apart — which the hazard features make
trivial and which the supervisor already knows, because the forecast is why they opened the tool.
Within an event every asset shares identical hazard features, so within-event AUC measures only what
the system is for: ranking 900 assets against each other under one forecast. The 0.22 gap between the
columns is the part of the pooled score that answers a question nobody asked.

Pooling also interacts badly with `GroupKFold`. Each fold's intercept is fitted to the events *not*
held out, so a low-risk event held out is scored by a model calibrated on higher-risk ones. A feature
set without hazard features cannot correct for it, which is how the four condition flags alone score
**0.4374 pooled — below random — and 0.6171 within event**, while showing a clean monotone lift in
failure rate from 0.59% at zero flags to 1.94% at three. Precision and recall at capacity were
already computed per event and were never affected. See `DECISIONS.md` D-029.

**The notes add 0.0136 within-event AUC** (0.6468 → 0.6604). Measured pooled it looks like −0.0006,
which is the artefact above and not a finding.

**The interaction terms cost 0.039 within-event AUC** (0.6605 → 0.6216) and are kept anyway. They are
not there to discriminate better; they are there so the forecast can change the ranking at all.
Without them every hazard feature is constant within a scenario, the hazard block adds the same
number to every asset's log-odds, and the queue is provably identical whatever the weather does —
`tests/test_ranking.py` asserts that in both directions. That is the trade, recorded rather than
argued away.

**Ranking divergence across the four scenarios**, top 15 by priority: 3, 4, 4, 3, 7, 8 assets differ
pairwise; by risk alone 5, 6, 7, 4, 12, 13. Priority is consistently damped because
`customers_served` spans 45× across the fleet, far more than a forecast re-weights any asset's risk.
An earlier build measured 0 on every pair; `DECISIONS.md` D-026 records what changed and why the gate
that was written around this was dropped rather than reinstated.

**Failures found in the top k, summed over the 16 events** (of 138). The k=15 column is
the ablation table above; the rest shows whether a variant that looks worse at one capacity is worse
across the range. These are counts of 10 to 35, so one or two hits move a rate in the third decimal
place — the gaps between the middle three variants are not large enough to rank them. Three of the
16 events contain no failures at all and contribute a guaranteed zero to every precision figure.

| Variant | k=10 | k=15 | k=20 | k=25 | k=30 | k=40 |
|---|---|---|---|---|---|---|
| Heuristic baseline (peak temp x age) | 1 | 1 | 1 | 2 | 3 | 6 |
| Register only | 11 | 16 | 21 | 25 | 28 | 32 |
| Register + notes | 10 | 11 | 18 | 19 | 22 | 30 |
| Register + interactions (no notes) | 11 | 15 | 22 | 26 | 29 | 35 |
| Full model | 9 | 13 | 16 | 20 | 26 | 33 |

Recall@k for the same variants:

| Variant | k=10 | k=15 | k=20 | k=25 | k=30 | k=40 |
|---|---|---|---|---|---|---|
| Heuristic baseline (peak temp x age) | 0.0018 | 0.0018 | 0.0018 | 0.0274 | 0.0338 | 0.0484 |
| Register only | 0.0353 | 0.0599 | 0.0713 | 0.0856 | 0.0955 | 0.1119 |
| Register + notes | 0.0257 | 0.0275 | 0.0855 | 0.0873 | 0.0973 | 0.1233 |
| Register + interactions (no notes) | 0.0389 | 0.0471 | 0.0952 | 0.1048 | 0.1354 | 0.1696 |
| Full model | 0.0239 | 0.0627 | 0.0695 | 0.1065 | 0.1344 | 0.1582 |

**Crew capacity is a client operating parameter, not a property of the system**, and the 15 this
build reports at was chosen rather than derived. A monthly substation inspection cadence puts
realistic pre-event capacity nearer 30 — 400 substations ÷ 21 working days × 3 days ≈ 57 substation
visits, roughly half divertible to targeted work. The curve is published so a reader with their own
capacity figure can read off their own number:

| Capacity | Precision@k | Recall@k | % of fleet |
|---|---|---|---|
| 10 | 0.0563 | 0.0239 | 1.1% |
| 15 **(default)** | 0.0542 | 0.0627 | 1.7% |
| 20 | 0.0500 | 0.0695 | 2.2% |
| 25 | 0.0500 | 0.1065 | 2.8% |
| 30 | 0.0542 | 0.1344 | 3.3% |
| 40 | 0.0516 | 0.1582 | 4.4% |

15 is retained as the conservative reported case. It was **not** raised to the derived figure:
recall roughly doubles between 15 and 30 on identical predictions, and improving a headline metric on
the strength of an assumption introduced in the same change is not a result worth having. Precision
does not fall monotonically because at these capacities each event contributes a handful of failures
and the per-event average moves on single hits — that is noise at a 1% base rate, left visible.
`DECISIONS.md` D-036.

**Against the Bayes ceiling.** Ranking by the true generative probability is the best any model
could do, because it uses the exact hidden state that produced the outcomes:

| Ranked by | Pooled AUC | Within-event AUC | Precision@15 |
|---|---|---|---|
| The model (out-of-fold) | 0.8432 | 0.6216 | 0.0667 |
| True generative probability | 0.8683 | 0.7260 | 0.1077 |

The model reaches **85.6%** of achievable within-event AUC and **61.9%** of achievable precision at
the crew's capacity. The absolute numbers are low because outcomes are Bernoulli draws at a 1% rate —
most of the variation is irreducible — not because the model is leaving that much on the table.
Recomputed on every run by `validate.py`.

Calibration: Brier **0.00883** against **0.00949** for a base-rate-only baseline. Reliability diagram
in `output/calibration.png`, over **equal-frequency** bins with a 95% Wilson interval on each. Equal
width is the conventional choice and the wrong one at a 1% base rate: it put 98.3% of rows in the
first bin and left a single asset-event — one transformer given 41% that did not fail — drawn as a
line collapsing to zero, which read as the model breaking down at high probability. The top two bins
now sit on the diagonal (predicted 0.0138 against observed 0.0146, and 0.0629 against 0.0611); the
eight below carry about four failures each and have intervals wide enough to cross the diagonal
several times. Calibration is good where there is signal and unmeasurable where there is not.
`DECISIONS.md` D-042. Leakage check: highest correlation between any feature and the hidden
state is **0.7234** (degree-hours against thermal stress), under the 0.95 threshold; pooled AUC is
under the 0.90 line that would indicate leakage.

Regularisation check, recorded not searched: C=0.01 → 0.8494, C=0.1 → 0.8467, C=1.0 → 0.8432,
C=10.0 → 0.8424. Lower C scores marginally better pooled and `C=1.0` is kept, because the sweep is a
check that the default is not badly wrong rather than a search to be won.

**Coefficient signs.** Degree-hours (+1.09), peak load (+0.57), time since maintenance, prior faults,
age and all four condition flags are positive, as expected. Cooling type is negative, which is also
correct: the ordinal runs ONAN→ONAF→OFAF, so a higher value means better cooling.

**`peak_temp_c` is negative (−0.31), and that is the project's premise rather than an artefact.**
Univariately it correlates **+0.35** with the event failure rate; the negative sign is what remains
once degree-hours is accounted for, and its partial correlation given degree-hours is **−0.45**. The
reason is visible in the data: the two hottest events in the bank, at 42.2 °C and 41.8 °C, produced
**zero failures between them**, because both were two-day spikes. At a comparable degree-hour total a
higher peak means the heat was concentrated into a shorter event, and a shorter event does less
damage. Dropping the feature measured *worse* (within-event AUC −0.0014), so it earns its place.

`max_overnight_min_c` was a fourth hazard feature and has been **dropped**. Variance inflation factor
49.4 — 98% predictable from the rest — with a coefficient that flipped sign between folds and an
asset page reporting that a 32 °C night had lowered an asset's risk. Removing it left precision@15
and @30 identical, moved within-event AUC by +0.0002, and took the model's worst VIF to 3.6. It is
still computed and still shown on the forecast strip, because it is a real property of the weather;
it is no longer a model input. `DECISIONS.md` D-040.

It is worth recording how much worse this was. Built with **raw** interaction products rather than
centred ones, five features came out wrong-signed, and the asset page for the highest-risk asset in
the most severe scenario reported that 40.7 °C and a 33 °C overnight minimum had *lowered* its risk.
It also silently broke retrieval, because `build_query` reads only positive contributions and so
dropped every heat term from the query. Centring the components before multiplying fixed the
explanation, the query, and the forecast sensitivity, at a cost of 0.014 within-event AUC. See
`DECISIONS.md` D-031.

Per-fold coefficient means and standard deviations, sign flips included, are in `output/metrics.md`.

### Retrieval and briefs

160 briefs across four scenarios. **Citation integrity 100.00% (160 of 160)** on both checks — the
`cited_doc_ids` array and every doc id written into the prose — against a spec expectation of ≥99%.

The second check was added after the first reported 100% clean while **2 of 160 briefs named a
procedure in a sentence that had never been retrieved**, with a valid citation array sitting beside
the invented reference. `BRIEF_PROMPT_VERSION` moved to v2, which forbids passing on an id found
inside a supplied document, and all 160 briefs were regenerated. Both offenders are gone.
`DECISIONS.md` D-038.

**The brief is written from the inspection findings.** Until v3 it was not: the prompt carried asset
facts, the top three positive contributions and three procedure documents, and never the extracted
condition findings — the output of the layer this project reads 1,800 free-text notes to obtain, and
the content the asset page prints directly beside the brief. Two causes, both measured. The findings
were absent entirely, affecting the **150 of 160** briefed assets that carry evidence. And the driver
list was truncated with `BM25_TOP_K` — a constant meaning *how many procedures a supervisor will
read*, reused to decide how many risk drivers to state — which cut a positive condition flag out of
**149 of 160** briefs, because the flags sit below cooling type and the maintenance interval in
contribution order. The highest-ranked asset in `short-severe`, whose notes record an oil sight glass
below minimum, a deferred bushing inspection and nesting material in the ventilation grille, was
given a brief about the general management of ageing units.

v4 supplies each finding verbatim with its flag and date, and every positive contribution as its
reading rather than its raw value. The inspection **id** is deliberately withheld: v3 supplied it and
forbade its misuse in as many words, and **13 of 160** briefs wrote it as though it were a procedure
reference — *"clear the radiator fins, as specified in INS-340-2"* — which inverts what an inspection
is. Withholding the token makes that impossible rather than discouraged, and 0 of 160 v4 briefs name
one. The existing citation checks never saw this: `DOC_ID_PATTERN` does not match an inspection id,
so both reported clean throughout. `DECISIONS.md` D-049.

**LLM cost, measured.** Both layers record the tokens their cached results cost to produce, so a
run that makes no calls still reports what the corpus was worth. `output/extraction_cost.txt` and
`output/brief_cost.txt`.

| Layer | Calls from empty | Input tokens | Output tokens | Per call (in / out) |
|---|---|---|---|---|
| Extraction | 1,298 | 1,005,218 | 144,882 | 774 / 111 |
| Briefs | 160 | 287,166 | 21,559 | 1,795 / 135 |
| **Total** | **1,458** | **1,292,384** | **166,441** | |

A brief costs about 2.3× the input of an extraction, because it carries three procedure documents in
full alongside the asset's inspection findings — 1,552 to 3,596 input tokens depending on which
documents were retrieved and how much the notes recorded, against 54 to 313 output.

**The two layers have different cost models, and only one is a one-off.** Extraction is keyed on note
text, which never changes once an inspector has written it, so the entry is durable and the cost
tracks inspection cadence rather than tool usage — scoring the same fleet against a hundred forecasts
makes zero extraction calls. Briefs are keyed on the forecast, so **every forecast run regenerates
them**, including the same event re-scored at 72h, 48h and 24h as it firms up. That is not an
artefact of the key: of the 63 assets appearing in any scenario's top 40, only 24 retrieve the same
document set across the scenarios they appear in, and the prompt embeds every positive contribution
with its reading, which moves with the weather. Caching a brief across forecasts would serve the
right procedures for the wrong reasons. The key is a hash of the prompt itself, so this is enforced
rather than assumed: a brief cannot be served for a prompt it was not written from. D-049.

In production, then, budget extraction against new notes and briefs against
`events × forecast updates × 40`. The risk model and the web application make no
LLM calls at all.

Retrieval filters on applicability before scoring relevance: MG-022 and SOP-014 cover forced-air
cooling systems and do not apply to naturally-cooled ONAN units. No brief had ever returned an
inapplicable document, but the constraint was holding by luck — one reaches **rank 4 for 9 of the
135 briefed ONAN assets**, one place outside the top-3 cut. The asset's cooling type is no longer a
query term: `applies_to` is not indexed, so as a term it only matched cooling types written into a
document's prose, which is one document for `ONAN` and none for `ONAF` or `OFAF`. D-037.

The retrieval floor is a **per-term** score, not a total. A BM25 total is a sum of per-term
contributions, so it grows with query length — measured across 160 queries the total correlates
**+0.87** with the number of terms, which run from 13 to 50. An absolute floor on that quantity tests
how long a query was, not how well it matched, and it needed re-deriving after every change to
`build_query`: 12.0, then 16.0, then 14.0. That was treated as maintenance twice before being
recognised as a design fault.

It caught a real false positive on the fifth change. Dropping `max_overnight_min_c` removed three
terms from every query carrying them, and one asset's 15-term query fell to a total of 12.03 against
a floor of 14.0 — which would have suppressed three genuinely applicable procedures (insulation
ageing, loading de-rating, maintenance compliance) for an old, heavily loaded, overdue asset. On
per-term quality that query ranked **62nd of 160**: mid-pack, not degenerate.

`BM25_FLOOR_PER_TERM` is **0.45**. Per-term scores run 0.589 to 1.360, median 0.845, so it sits 24%
below the observed minimum and does not move when query construction does. A test asserts that the
same terms repeated three times get the same verdict as the terms once. **It does not trigger on any
of the 160 queries**, so no real query reaches the `no_match` path. `DECISIONS.md` D-041.

312 tests pass, including a freshness check on the notebook: it is committed with its outputs, so
re-running the pipeline silently invalidates every figure in it. The check compares its displayed
numbers against `output/metrics.json` without executing it, and also asserts it ran clean, rendered
its plots, and imports from the modules rather than reimplementing them. It went stale twice during
the build and was caught by eye both times. The cold-weather negative control is asserted exhaustively over the vocabulary
`build_query` can emit, not only over hand-written queries. `tests/test_ranking.py` additionally
asserts that every demo scenario sits inside the hazard envelope the model was trained on — a check
that immediately caught the first `long-severe` scenario, whose overnight minimum of 33.3 °C sat
above the trained maximum of 32.45 °C.

## Scope exclusions

Out of scope by decision, not oversight.

| Excluded | Why | What it would need |
|---|---|---|
| Water network (pumping stations, treatment) | Same architecture, different failure model. Building it twice adds no evidence. | Pump and treatment asset registers, hydraulic demand model |
| Other hazards (hurricane, flood, wildfire) | Not because they carry less warning — hurricane tracks are forecast five days out, further than heat. Because heat is the hazard where warning converts into targeted work: a track cone tells you a storm is coming, not which substations it hits, whereas a temperature forecast covers the whole territory with confidence. And wind damage is not condition-dependent, so no inspection prevents it; heat failures are the assets that were already marginal. The architecture is hazard-agnostic — the risk model is the swappable part. | Hazard-specific models; flood needs elevation and hydrology data |
| Weather forecasting | The system consumes a forecast, it does not produce one. NWS/NOAA feeds are authoritative. | Nothing — permanently out of scope |
| Load forecasting | Needs real SCADA history to be credible. The risk ranking is useful without it. The generator does model demand rising with temperature, but that is an assumed physical response inside the synthetic world, not a forecast of load from history. | 2–3 years of half-hourly load telemetry per asset |
| Anomaly detection on telemetry | Complementary, not substitutable. Detects developing faults continuously; this model ranks known condition against forecast stress. | Streaming SCADA integration, labelled fault history |
| Crew scheduling optimisation | The system ranks; it does not route. Optimisation needs crew locations, skills, shift rules and travel times. | Field operations system integration |
| Feeder reconfiguration feasibility | The queue labels an asset *load transfer* when loading is the largest actionable driver of its risk. That says what kind of intervention the risk implies, not that the intervention is available: whether load can be moved depends on adjacent feeder headroom and switching state, and adjacent feeders are stressed at the same moment. No control-room capacity is modelled either, so the count of load transfers above the capacity line is not bounded by anything. | Network topology model, real-time switching state, feeder loading telemetry |
| Spatial / GIS analysis | GIS is a data dependency here, not an AI capability. Location and criticality enter as pre-computed fields. | PostGIS or equivalent, network topology model |
| Real-time integration | Runs on fixed forecast scenarios against cached data so it is reproducible and demonstrable offline. | Live feed adapters, scheduling, monitoring |
| LLM-as-judge evaluation of brief quality | Deterministic checks cover the failure modes that change decisions. A judge model would itself need validating. | A labelled set of good and bad briefs, plus human agreement measurement |
| Map view | Would require external tile services, breaking the offline property. `lat`/`lon` are stored as integration stubs only. | Local tile server or an accepted external dependency |

## Roadmap

Capabilities a production system would carry that this one does not, each gated on data rather than
on modelling effort. Reference: Nebulaworks, *Predictive Maintenance for Substation Equipment — A
Production MLOps Architecture*, October 2025.

| Capability | What it adds | Data dependency |
|---|---|---|
| Multi-horizon prediction (7 / 14 / 30 day) | Different horizons serve different decisions: a 30-day signal supports spare procurement, a 7-day signal triggers crew dispatch. Not achievable here because no feature in the current set varies on a weekly cadence. | Continuous telemetry; per-asset-day labelled failure history |
| Dissolved gas analysis | Reliable early indicator of internal faults. Classical interpretation methods (Rogers Ratio, Duval Triangle) encode decades of diagnostic standards as features. | Online DGA monitors, or periodic oil sampling records |
| Continuous thermal telemetry | Replaces the inferred thermal model with measured top-oil and winding temperature. | Fibre-optic probes or RTDs via the SCADA historian |
| Vibration and acoustic monitoring | Detects mechanical faults that do not appear in gas analysis until late stage. | Sensor installation on large power transformers |
| Drift-triggered retraining | Retraining driven by feature distribution drift rather than a calendar schedule. Grid topology changes and asset replacements shift feature distributions in ways a schedule does not anticipate. | Serving-time feature distribution monitoring |
| Sensor availability handling | When an input is unavailable the model should adjust confidence rather than treat an imputed value as observed. Parallel to the existing rule that a failed extraction is not a clean asset. | Applies once telemetry exists |

## Limitations

- **All data is synthetic.** The failure process is invented, and the model recovers a signal that
  was put there deliberately. Nothing here is evidence about real transformers.
- **The per-event failure rate is inflated about twelvefold** for trainability. Probabilities are
  calibrated to the synthetic world, not the real one. Ranking is unaffected.
- **Hazard is uniform across the fleet.** One hourly temperature series applies to all 900 assets. A
  real deployment would apply a forecast grid; `district`, `lat` and `lon` are carried for exactly
  that extension. This is why the scenarios carry no regional names, and why the forecast moves the
  queue less than it otherwise would: with identical weather everywhere, all a forecast can reweight
  is each asset's own susceptibility. The top 15 still differs by 3 to 9 assets between scenarios,
  but priority divergence runs consistently below risk divergence because `customers_served` spans
  45× across the fleet — more than a forecast re-weights any asset's risk.
- **Core temperature is inferred, not measured.** The thermal model drives an oil temperature from
  ambient through a first-order lag and a load-dependent rise, then accumulates Arrhenius ageing
  above a reference. A production system derives the same quantity — an accumulated equivalent-ageing
  index — from measured top-oil and winding telemetry rather than inferring it. The functional form
  is the same; the input is not, and every parameter of the inference is assumed.
- **The measurement window is defined by construction.** `degree_hours_above_30` and
  `consecutive_warm_nights` are accumulated over an event, and events exist here because they were
  manufactured with start dates and durations. Real weather is continuous and has no event
  boundaries, so on real data these features need an explicit window. Forward-looking that window is
  well defined — it is the forecast horizon — but constructing them from history needs a fixed
  trailing window with a rolling origin, and heat events defined from meteorology (a percentile
  against local climatology) rather than from where failures clustered, which would be circular. In
  this build the confound is measurable: `consecutive_warm_nights` correlates r=+0.86 with event
  duration and `degree_hours_above_30` r=+0.66, so both partly encode how long rather than how hot.
- **Condition features describe the asset as of the forecast, not as of each historical event.** The
  register holds a single maintenance date and the notes are undated relative to past events, so
  `days_since_maintenance` and the four flags are static across the training window.
- **The model tracks bulk oil, not winding hot-spot.** Hot-spot is the governing variable in the
  standards, and the reference temperature here is a proxy on a different scale.
- **The BM25 floor never fires** on the current corpus and query construction. Suppressing the
  weakest query would mean discarding three correctly retrieved documents, so the `no_match` path is
  reached only by unit tests, never by real traffic.
- **27.9% of notes are duplicate texts**, concentrated entirely in notes with no outstanding defect.
  The distractors-only evaluation category rests on far fewer independent items than its note count
  suggests.
- **Mean within-event AUC is 0.6216**, and that is the number the operational task depends on — but
  the Bayes ceiling for it is 0.7260, so the headroom is small. The ranking is limited by the
  problem being mostly coin-flip at a 1% base rate, not by the model.
- **The interaction terms cost 0.039 of within-event AUC.** They are kept because without them the
  forecast cannot change the ranking at all, but on discrimination alone the
  12-feature *Register + notes* variant is the better one, and that trade is a judgement rather
  than a measurement.
- **The crew reaches 1.7% of the fleet.** Capacity stayed at 15 while the fleet grew sixfold, so 15
  interventions now cover 15 of 900 rather than 15 of 150. Recall at 15 is correspondingly low in
  absolute terms — and is a consequence of a conservative capacity assumption rather than of the
  ranking. The sweep above shows how it moves: 0.063 at 15, 0.134 at 30, 0.158 at 40. A derivation
  from inspection cadence puts realistic capacity nearer 30, and that figure was deliberately not
  adopted in the same change that measured its effect. D-036.
- **The condition flags carry almost no weight, so no rule can split the queue by remedy.** The
  fitted coefficients are 0.037 for `flag_cooling_degraded` and 0.036 for `flag_overdue_remedial`
  against 0.568 for `peak_load_pct`; only `flag_oil_issue` (0.157) is comparable to the register
  features. Clearing *every* recorded defect on the median `short-severe` asset saves 0.32 expected
  customers. The ablation says the same from the other side: the notes buy 0.013 of within-event AUC
  over a register-only model (0.647 → 0.660). Three rules for deciding whether an asset needs a site
  visit were built and measured against this — a defect test, a counterfactual comparison of what
  each action would save, and a threshold on the log-odds reduction — and all three fail, one of
  them by swinging from 0 to 875 crew assets out of 900 across equally defensible settings of an
  assumption about network topology the system does not model. The queue therefore reports what is
  driving each asset's rank and leaves the remedy to the brief. D-051.
- **The crew budget constrains only part of the queue.** Assets ranked on loading through sustained
  heat are remediable from the control room by transferring load to an adjacent feeder, not by a site
  visit. The reported precision and recall at capacity treat every ranked asset as consuming a crew
  visit, so they understate the coverage achievable at a given crew budget: at capacity 15 the
  `short-severe` queue reaches rank 52 before it has spent 15 crew visits. **The figures were not
  adjusted for this** — the split is a reading of the queue, not a change to the model, and adjusting
  a metric on the strength of it would be tuning to a number. Load transfer is also not free:
  adjacent feeders are stressed at the same moment, so headroom is scarcest exactly when it is
  needed, and the system models no network topology and cannot tell whether a transfer is available.
  D-048.
- **A brief is appropriate to the intervention type by coincidence of the query, not by
  construction.** The retrieval query is built from all of an asset's positive contributions, so a
  load-driven asset usually retrieves the loading procedure — 11 of the 12 remote-classified assets
  spot-checked across three scenarios returned *MG-021 Loading and de-rating at high ambient
  temperature*. One did not: `SUB-SGW-340` under `short-severe` retrieved three condition documents
  and no loading document, because its other positive contributions outscored the loading terms.
  Nothing in the pipeline conditions retrieval on the intervention type, and this was checked rather
  than assumed. D-048.
- **A brief can still misattribute between two documents it was given.** Citation integrity is
  checked on the `cited_doc_ids` array and on every doc id in the prose, but both are membership
  tests. A brief that takes an instruction from one supplied document and attributes it to another
  supplied document passes both — and one does: the top-ranked asset's brief lifts a sentence from
  MG-023 that itself points at MG-021, and substitutes the id of the document it was reading. The
  v2 prompt forbids this explicitly, which is the most that can be asserted without checking claims
  against document *content*. That is an LLM-as-judge evaluation, a listed exclusion.
- **Missed failures are the ones with no recorded defect.** The design ranks recorded condition
  against forecast stress, so an asset whose condition was never written down is invisible to it.
  Continuous telemetry is the thing that would catch those, and it is a listed exclusion.

## Repository

```
config.py                  all constants
generate_data.py           synthetic data generation and six hard gates
extract.py                 Layer 1 — LLM extraction, keyword baseline
features.py                feature computation, leakage boundary
model.py                   Layer 2 — train, evaluate, score, explain
retrieve.py                Layer 3 — BM25 + brief generation
llm.py                     one LLM call and the disk cache
validate.py                extraction eval, leakage check, citation integrity
app.py                     FastAPI application
templates/  static/        server-rendered UI; one 30-line progressive-enhancement script
data/                      generated artefacts and the procedure corpus
cache/                     cached LLM results, committed
output/                    scored JSON, briefs, metrics, plots
notebooks/                 analytical record, committed with outputs
tests/                     retrieval, citations, ranking, interface, notebook and README freshness
DECISIONS.md               45 entries, append-only
```

`DECISIONS.md` records every non-obvious choice, newest last. The five that changed the shape of the
build: the thermal stress definition (D-008), the fleet-scale derivation (D-011), the failure rate
(D-012), coupling demand to temperature (D-023), and the interaction features that let a forecast
reorder the queue (D-024, with D-031 on why their components are centred).

Three entries record a measurement that overturned an earlier conclusion rather than confirming it,
and are the most useful ones to read: D-026 (a specified gate, measured as unreachable, then
reachable), D-027 (a sweep whose harness did not reproduce the pipeline, and the band it invented),
and D-029 (pooled AUC flattering the model, and the extraction uplift that had not actually
vanished).
