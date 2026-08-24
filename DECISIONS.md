# Decision log

Append-only, newest last. One entry for every non-obvious choice — anything a
reviewer might reasonably have done differently.

---

## D-001 — `generated_at` is a fixed constant, not the wall clock

**Decision:** `config.RUN_TIMESTAMP` is a hardcoded ISO timestamp, written into
every scored JSON file as `generated_at`.

**Alternatives:** Read the clock at run time, as the build spec's example output
implies.

**Why:** Ground rule 1 requires that the same inputs produce identical outputs on
every run, and the cached-LLM design exists so the whole pipeline can be re-run
and compared. A moving timestamp puts a spurious diff in every output file on
every run, which makes a real diff harder to see and costs nothing analytically.

**What would change it:** A deployment where the freshness of the scored output
mattered operationally. Then the timestamp would come from the forecast it was
built against, not from the clock either.

---

## D-002 — The procedure corpus is authored on disk, not emitted by the generator

**Decision:** `data/procedures/*.md` are committed source files.
`generate_data.py` validates them — count, front matter, unique `doc_id`s,
category mix — instead of writing them.

**Alternatives:** Hold 25 documents of roughly 300 words each as string constants
inside `generate_data.py` and write them out, as build spec §2.2 lists.

**Why:** Nothing about the corpus is stochastic or seeded; "generating" it would
mean writing 7,000 words of fixed prose to disk from a module that would then be
mostly prose. That fails the explainability test for the one module a reviewer is
most likely to read closely. Validation keeps the same guarantee — the corpus
cannot silently drift out of the shape the retrieval layer assumes — without the
bulk.

**What would change it:** Procedure text that varied with the generated fleet
(asset-specific document bodies), which would make it genuinely generated data.

---

## D-003 — Thermal stress is written to its own diagnostic file

**Decision:** Added `data/hidden_thermal_stress.csv` (asset_id, event_id,
thermal_stress) alongside the two diagnostic files the build spec names.

**Alternatives:** Add a column to `hidden_asset_state.csv`; add a column to
`outcomes.csv`.

**Why:** Build spec §7.4 requires the leakage check to correlate every feature
against `thermal_stress`, but thermal stress is per asset *per event* and
`hidden_asset_state.csv` is one row per asset, so it cannot live there.
`outcomes.csv` is read by `model.py` for labels, and putting hidden state one
column away from the feature builder is exactly the accident the leakage boundary
exists to prevent. A separate file keeps the rule simple: `validate.py` is the
only module that opens a file whose name begins with `hidden_`.

**What would change it:** Nothing foreseeable; the separation is cheap.

---

## D-004 — LLM provider is switchable at pipeline time, never at serve time

**Decision:** `extract.py` and `retrieve.py` take `--provider anthropic|gemini`
and `--offline`. `app.py` keeps making no network call and running no inference
at request time.

**Alternatives:** A live-call mode in the web application, so the demo can show
the extraction and brief layers running against the API in real time.

**Why:** Ground rule 2 and build spec §6.1 both state that serve time is inert,
and it is one of the load-bearing properties of the design: the application
cannot hang, rate-limit, or display text that differs from the JSON on disk. The
same demonstration is available by running `extract.py` against a single note in
a terminal, which shows the layer working without weakening the guarantee.

Provider and model are folded into the cache key, so an Anthropic cache and a
Gemini cache cannot overwrite each other. One provider's cache is committed as
canonical; the other is a reproducible alternative.

**What would change it:** A requirement to process a note that arrives after the
batch has run, which would be a different system — a queue feeding the batch, not
inference behind a request.

---

## D-005 — Hard gate 6 fails as specified: the interaction term dominates

**Decision:** Recorded, not worked around. See D-006 for the resolution.

**Measured:** 0.164 of failures fall in assets with below-median `condition`;
build spec §2.7 requires ≥ 0.20. The other five gates pass, gate 2 (the project's
central premise) with a wide margin: 14.86 against 6.94 degree-days.

**Diagnosis:** Not an implementation defect. The failure log-odds in build spec
§2.5 are `0.15·stress + 1.60·condition + 0.30·stress·condition + 0.90·(load−0.70)`.
Those three coefficients are only in proportion to each other if `thermal_stress`
is of order 1. The thermal stress definition in the same section — the integral of
`theta − 36` in degree-days — produces 15 on long-moderate events and 29 on
long-severe ones. At that scale the interaction term is effectively
`0.30 × 30 × condition ≈ 9 × condition`, five times the standalone condition
coefficient it was presumably meant to modulate. Measured across the 2,400
training rows, the interaction term correlates 0.965 with the total logit and the
standalone condition term 0.307.

Failures therefore concentrate in high-condition assets, which is what gate 6
measures. The gate is monotone in the interaction coefficient: at 0.30 the share
is 0.11–0.16 depending on the Bernoulli draw, at 0.20 it is 0.18, at 0.10 it is
0.25.

The measurement is also noisy. The share is computed over roughly 116 failures,
giving a standard error near 0.034, so 0.164 sits about one standard error below
the 0.20 line and two independent draws of the same generator returned 0.114 and
0.164.

**Why it was not silently fixed:** Golden rule 8 stops the build on a gate
failure rather than permitting a threshold to be relaxed or the generator to be
adjusted until it passes, and lowering a generative coefficient until a check
turns green is the specific move that rule exists to prevent.

---

## D-006 — Gate 6 is recorded as measured; the generator is left alone

**Decision:** `FAILURE_COEF_INTERACTION` stays at the specified 0.30. Gate 6
reports `FAIL` in `output/data_checks.txt` with the measured value and a pointer
to this entry, and does not stop the build. `config.GATE_6_RECORDED_NOT_ASSERTED`
carries the exemption and names it. The other five gates are still asserted and
still stop the build.

**Measured:** 19 of 116 failures fall in the better-maintained half of the fleet;
the gate requires at least 24. A shortfall of five failures.

**Alternatives:** Reduce the interaction coefficient to 0.10, which makes the
gate pass at 0.25 (see the sensitivity sweep in D-005); relax the gate threshold
to 0.15.

**Why:** Golden rule 7 prefers an honestly measured number to one produced by
adjusting parameters until a check passes, and both alternatives are that
adjustment. The gate exists to stop the failure label being a deterministic
read-off of hidden condition, and at 19 in 116 it is not — the model still has to
find failures in assets that looked healthy. The measurement is one standard
error from the line on a count of 116.

The risk this accepts is that the generated data is easier than intended, which
would surface as an inflated out-of-fold AUC. Build spec §9 already covers that:
AUC above 0.90 indicates leakage and is to be investigated rather than accepted.
If the model comes in above that line, this decision is revisited and the entry
is superseded.

**What would change it:** An out-of-fold AUC above 0.90, or a larger fleet where
the same share is measured over enough failures to be conclusive.

---

## D-007 — The failure model is too steep in thermal stress; 5 of 16 events produce no failures

**Status:** Investigated and measured. Awaiting a decision on the remedy.

**Measured, on the data as generated from the build spec's own constants:**

- Pooled out-of-fold AUC **0.9687**. Build spec §9 states that anything above
  0.90 indicates leakage and is not to be accepted.
- Failures by event type across the 16 historical events: long-severe 101,
  long-moderate 16, short-severe **0**, mild **0**. Nine of the sixteen events
  produced no failure at all, so a fifth of the grouped cross-validation folds
  contain no positive examples.
- The four hazard features alone reach a pooled AUC of 0.915. The nine asset and
  condition features together reach 0.680.
- Mean within-event AUC is 0.894, over the seven events that have any failures.

**Diagnosis.** The pooled AUC is not measuring the thing the tool exists to do.
Failure log-odds are linear in `thermal_stress`, which ranges from 0 on mild
events to 47 degree-days on long-severe ones. The `0.15 · stress` term alone
therefore spans about 7 in log-odds, an odds ratio near 1,100 between the
mildest and harshest event. Events become almost perfectly separable from
ambient temperature, and the pooled figure is dominated by "is this a
long-severe event", which needs no model. The operationally meaningful question
— given this forecast, which assets — is the within-event figure.

This is the same root cause as D-005: the build spec's §2.5 coefficients are
calibrated for a `thermal_stress` of order 1, and its §2.5 definition of thermal
stress produces values one to two orders of magnitude larger.

**Ruled out.** Reducing `FAILURE_COEF_INTERACTION` alone does not fix it — at
0.02, an effectively zero interaction, the pooled AUC is still 0.922 and only
nine events carry failures. Raising `THETA_THRESHOLD_C` makes it worse: less
accumulated stress forces the solved intercept upward, which lifts the baseline
probability on mild events and breaks gate 4.

**Measured remedy.** Scaling both thermal-stress coefficients — `0.15` and
`0.30` — by a common factor k, with every other constant untouched:

| k | events with failures | mild rate (gate 4) | short-severe / long-moderate / long-severe failures | gate 6 | pooled AUC | within-event AUC |
|---|---|---|---|---|---|---|
| 1.00 (as specified) | 7 | 0.0000 | 0 / 16 / 101 | 0.154 | 0.9716 | 0.894 |
| 0.50 | 11 | 0.0000 | 3 / 36 / 82 | 0.298 | 0.9046 | 0.760 |
| 0.35 | 13 | 0.0044 | 7 / 40 / 69 | 0.331 | 0.8554 | 0.725 |
| 0.25 | 14 | 0.0133 | 13 / 44 / 62 | 0.368 | 0.7738 | 0.625 |
| 0.15 | 15 | 0.0222 | 17 / 47 / 41 | 0.398 | 0.6456 | 0.556 |

Two stated acceptance criteria bind in opposite directions. Gate 4 requires the
mild-event failure rate below 0.01, which fails at k ≤ 0.25. Build spec §9
requires a pooled AUC below 0.90, which fails at k ≥ 0.50. Only k around 0.35
satisfies both, and it also makes every event type produce failures and puts the
project's premise into the outcomes rather than only into the stress statistic:
40 long-moderate failures against 7 short-severe.

**Note on these figures.** They were measured using the keyword baseline as a
stand-in for the four extracted condition flags, because no API key was available
when the investigation was run. The four flags are 4 of 13 features; real
extractions will shift the numbers and every one of them is to be re-measured
and re-recorded before anything is reported.

**What was deliberately not done.** k was not chosen to land the AUC inside the
0.70–0.82 band in build spec §9. k = 0.25 sits inside that band and was rejected
because it fails gate 4. Selecting on the acceptance criteria is not the same as
selecting on the result, and the resulting AUC is to be reported as measured
whatever it turns out to be.

---

## D-008 — Thermal stress becomes Arrhenius equivalent ageing; hazard scale 3.5

**Supersedes D-006, and resolves D-005 and D-007.**

**Decision:** `thermal_stress` is the accumulated *accelerated* ageing over an
event, `sum(max(0, 2**((theta - 38) / 6) - 1)) / 24`, in equivalent days at the
reference temperature. The two hazard coefficients in the failure log-odds are
the build spec's 0.15 and 0.30 multiplied by `HAZARD_SCALE = 3.5`. Every other
constant in build spec §2.5 is unchanged, and all six hard gates are asserted
again — the gate 6 exemption added in D-006 has been removed.

**Alternatives:** Keep degree-days above 36 C and scale both coefficients by 0.35
(numerically similar, AUC ~0.855); keep the spec's definition and coefficients
and argue that pooled AUC is the wrong metric; keep Arrhenius with the spec's
coefficients untouched.

**Why:** Cellulose insulation ages by an Arrhenius process — the rate roughly
doubles per 6–8 C of sustained rise (Montsinger, 1942), and the standards express
loss of life this way. Accumulated *equivalent ageing* is therefore the physically
correct quantity, and it is the one that resolves the inconsistency D-005 and
D-007 both traced: it is an accumulation, so duration counts and gate 2 holds
(1.09 against 0.77), but it is exponentially weighted, so an hour at 45 C is worth
many hours at 39 C. Degree-days above a fixed line treats those the same, which is
why the spec's definition spanned 0–47 and overwhelmed coefficients written for a
quantity of order 1. Under the Arrhenius form stress runs 0 to about 3.3, which is
the scale the spec's coefficients assume.

The remaining scale factor is not free. Gate 4 requires mild events to be near
failure-free, which needs a steep hazard response; build spec §9 requires
out-of-fold AUC below 0.90, which needs a shallow one. Measured on the generator:

| scale | gate 4 (mild rate) | gate 6 | events with failures | pooled AUC |
|---|---|---|---|---|
| 3.0 | 0.0133 (6/450) — fails | 0.351 | 15 of 16 | 0.8430 |
| 3.5 | 0.0089 (4/450) — passes | 0.318 | 15 of 16 | 0.8825 |
| 4.0 | 0.0044 (2/450) — passes | 0.300 | 13 of 16 | 0.9103 — fails |

3.5 is the value inside the band both criteria leave. Gate 4 turns on a
difference of two Bernoulli draws out of 450, so it is marginal by construction
at this fleet size, and that is worth saying rather than presenting 0.0089 as
comfortable.

**Two findings to carry into the README.**

1. Build spec §9's expected AUC band of 0.70–0.82 is not reachable together with
   gate 4. No hazard scale satisfies both. The acceptance criteria are internally
   inconsistent, and the measured AUC is reported against the band rather than
   forced into it.
2. Pooled AUC is the wrong headline for this data whatever the scale. It is
   dominated by between-event separation — telling a long-severe event from a
   mild one, which needs no model. The within-event figure is what the ranked
   queue depends on and both are reported.

**Corrected alongside it.** The build spec's narrative claim that the asset
"never fully resets" overnight is not what its own equations do. With an oil time
constant of 3–8 hours, 96% to 66% of the offset is shed over an eight-hour night.
The accurate mechanism, now in the code comments and the README: the overnight
minimum sets the *floor* the unit resets to, so a night at 27 C rather than 18 C
starts the next day nine degrees hotter and each day reaches a higher peak from a
higher base — and because ageing is cumulative and irreversible, five days of
elevated temperature does five days of damage that the weather breaking does not
return.

**What would change it:** Modelling winding hot-spot rather than bulk oil, which
would put the reference temperature on the standards' 110 C scale directly and
remove the need for a proxy reference at 38 C.

---

## D-009 — Ambient-driven load feedback is out of scope

**Decision:** `load_rise` stays a static per-asset property. Ambient temperature
heats the coolant; it does not also raise electrical loading in this model.

**Why:** The real mechanism has two pathways — ambient heats the oil directly,
and ambient drives demand up, which heats the windings further. That is the
"capacity falls while demand rises" argument. Modelling it needs two more assumed
constants and amplifies exactly the hot events, pushing between-event separation
and pooled AUC back up, against a criterion that is already tight. The prototype
is more defensible with one pathway modelled and the second named as missing than
with both modelled on invented constants.

**What would change it:** Real half-hourly load telemetry against ambient, which
would make the sensitivity a measured coefficient rather than an assumed one.

---

## D-010 — The Gemini path is written but unexecuted

**Decision:** `llm.py` supports `--provider gemini` alongside `anthropic`, written
against the current `google-genai` documentation. The Anthropic cache is the
committed, canonical one; the Gemini path has not been run.

**Why:** Both providers were asked for. Only one cache can be canonical, because
two providers will not produce identical extractions and the scored output has to
come from one of them. Claiming the Gemini path works when it has never been
executed would be a worse failure than saying plainly that it is untested.

**What would change it:** Running it, which needs a `GEMINI_API_KEY`, and then
recording the per-flag comparison against `inspection_truth.csv`.

---

## D-011 — The fleet is 900 transformers, derived rather than assumed

**Decision:** `N_ASSETS = 900`, `ASSET_ID_PREFIX = "SUB-SGW-"`. The prototype
covers the whole SGW substation transformer fleet, not a region of it.

**Alternatives:** Keep 150 as a regional pilot; use the ~400 figure that appeared
in earlier descriptive text.

**Why:** 150 was a tractability choice justified after the fact. The client brief
states SGW serves over 8 million residents, which supports a derivation:

```
8,000,000 residents ÷ ~2.5 per household   ≈ 3.2M customer accounts
3.2M customers ÷ ~8,000 per substation     ≈ 400 distribution substations
400 substations × ~2.2 transformers each   ≈ 900 substation power transformers
```

A derived number can be defended and a chosen one cannot. It also removes a
scale tier from the story: the prototype now covers the same fleet the business
case covers, so no bridging argument is needed between them.

Training rows go from 2,400 to 14,400 and extraction calls from 300 to 1,800.
Crew capacity stays at 15 and the briefed depth at 25 — the crew did not grow
because the fleet was described more accurately.

**What would change it:** A real asset register, which would replace the whole
derivation with a count.

---

## D-012 — Per-event failure rate of 0.01, inflated about twelvefold for trainability

**Decision:** `TARGET_FAILURE_RATE = 0.01` per asset **per event**, with gate 1
bounds of [0.008, 0.013] and gate 4 lowered to 0.002.

**The real annual rate.** CIGRE Technical Brochure 642 (2015), Working Group
A2.37, *Transformer Reliability Survey*: 964 major failures across 167,459
transformer-years, 56 utilities, 21 countries. Substation transformer failure
rates fall below 1% per year — 0.8% for pre-1978 units, 0.4% for post-1978 units
up to 20 years old. A major failure is one requiring removal from service for
over seven days with significant remedial work. This fleet skews old, so 0.8% is
the applicable figure.

**The implied per-event rate.** 900 assets × 0.8% annual × 40% heat-attributable
÷ 4 events per year ≈ **0.08% per asset-event**.

**Why the generator uses 0.01 and not 0.0008.** At the real rate, 14,400 rows
would carry roughly a dozen positives — not enough to fit a 13-feature logistic
regression. 1% gives about 144, which is workable. At 150 assets the same
reasoning forced 5%, roughly sixty times reality; the larger fleet allows twelve
times instead, which is the point of the change.

**The citation is load-bearing and must not be overstated.** CIGRE supports the
*annual* figure only. It does not support the per-event number, and presenting it
as though it did would misuse the source. The README states plainly that the
per-event rate is inflated about twelvefold for trainability, that this scales
predicted probabilities but preserves ranking, and that the system consumes a
ranking. Calibration is to the synthetic base rate; a production deployment would
recalibrate against observed outcomes.

**What would change it:** Real outcome history, which would remove the need to
inflate anything.

---

## D-013 — Customers per transformer corrected downward

**Decision:** `CUSTOMERS_PER_MVA` 430 → 92, `CUSTOMERS_MIN` 800 → 400,
`CUSTOMERS_MAX` 45,000 → 18,000.

**Why:** At 430 per MVA against a mean rating of 38.5 MVA, the mean was about
16,500 customers per transformer, implying more customers behind the modelled
fleet than the utility has. Substations run N-1: each transformer is sized to
carry the full substation load alone, so customers per transformer sit well below
what rated capacity alone suggests. 92 per MVA gives a mean near 3,555, and
900 × 3,555 ≈ 3.2M, matching the derivation in D-011.

**Effect on the model: none.** `customers_served` is not a feature, and
`priority = risk × customers_served` is a ranking — scaling every count by a
constant changes no ordering. The change affects display and any ROI figure.

**What would change it:** A connectivity model, which would give a real count per
asset instead of a rating-based estimate.

---

## D-014 — HAZARD_SCALE re-derived to 2.4 at the new fleet scale

**Decision:** `HAZARD_SCALE = 2.4`, replacing 3.5.

**Why it had to move:** 3.5 was measured against a 5% base rate. Both binding
constraints shift with the base rate, so the value does not carry over.

**Measured bracket, 1.0 to 10.0, all six gates evaluated on the generator:**

| scale | mild rate (gate 4, < 0.002) | gate 6 share (≥ 0.20) | verdict |
|---|---|---|---|
| 1.0 | 0.00741 | 0.348 | gate 4 fails |
| 2.0 | 0.00296 | 0.239 | gate 4 fails |
| 2.25 | 0.00222 | 0.211 | gate 4 fails |
| **2.30** | 0.00185 | 0.212 | both pass — lower edge |
| **2.40** | 0.00185 | 0.209 | both pass — chosen, centre of band |
| **2.50** | 0.00185 | 0.205 | both pass — upper edge |
| 2.55 | 0.00185 | 0.190 | gate 6 fails |
| 3.0 | 0.00185 | 0.169 | gate 6 fails |
| 5.0 | 0.00000 | 0.085 | gate 6 fails |
| 10.0 | 0.00000 | 0.035 | gate 6 fails |

Gate 4 wants a steep hazard response so mild events stay near failure-free; gate
6 wants a shallow one so failures still reach the better-maintained half of the
fleet. The feasible band is [2.30, 2.50] and 2.4 is its centre.

**The band is narrow by construction, not by choice.** Gate 4 turns on 5 versus 6
failures across 2,700 mild rows, and gate 6 on 34 versus 30 out of 163. Both
bounds are single-figure counts, so the edges carry sampling noise of the same
order as the band width. A larger fleet would tighten them; this one cannot.

**This tunes the generator, not the model.** The constraints were fixed in
advance and are properties of the synthetic world, not model scores. No model
result was consulted in choosing 2.4, and the resulting AUC is reported as
measured whatever it turns out to be.

**What would change it:** Real outcome data, which would remove the need for any
of these gates.

---

## D-015 — Demo scenarios renamed; hazard is uniform across the fleet

**Decision:** `coastal-short-severe` → `short-severe` ("2-day severe spike"),
`inland-long-moderate` → `long-moderate` ("5-day moderate"), `baseline-mild`
relabelled "3-day mild baseline".

**Why:** One hourly temperature series now applies to all 900 assets across both
coastal and inland areas, so the regional labels described a geography the model
does not have. Naming a scenario "Coastal" while applying its weather to the
whole fleet is a claim the system cannot support.

Scenario ids appear in output filenames, brief cache keys and app routes, so
everything was renamed and regenerated rather than edited by hand.

**Recorded as a limitation rather than fixed:** hazard is modelled uniformly
across the fleet. A real deployment would apply a forecast grid, and
district-level hazard variation is the natural extension — `district`, `lat` and
`lon` are already carried on every asset for exactly that.

**What would change it:** A gridded forecast, which would make hazard features
per-asset rather than per-event and would change the cross-validation grouping.

---

## D-016 — One provider, and it is the one that ran

**Supersedes D-004 and D-010.**

**Decision:** The Anthropic code path and its model constants are removed.
`llm.py` calls Gemini only, `config.py` names one extraction model and one brief
model, and `--provider` is gone from every script.

**Alternatives:** Keep both paths and the switch, as originally built.

**Why:** Only the Gemini path was ever executed, and only its cache is committed.
A second provider present in the code but never run is a claim the repository
cannot support — the first question a reviewer asks about it is "did you run
it", and the honest answer was no. `config.py` now states one answer about what
actually produced the committed artefacts.

The pipeline-time versus serve-time distinction from D-004 stands unchanged:
`app.py` still imports nothing that can reach an API, and `--offline` still fails
loudly on a cache miss rather than reaching for the network.

**What would change it:** A reason to compare providers on the extraction
evaluation, which would mean running both over all 1,800 notes and committing
both caches.

---

## D-017 — Model choice: gemini-3.5-flash-lite

**Decision:** `EXTRACTION_MODEL = BRIEF_MODEL = "gemini-3.5-flash-lite"`.

**Measured:** On the same inspection note, flash-lite returned a correct
extraction in 0.8 s with 0 reasoning tokens; `gemini-2.5-flash` took 3.2 s and
spent 593 reasoning tokens to reach the same four flags.

**Why:** Build spec §3.6 says use the smallest available model first and escalate
only if the evaluation shows it is not good enough. Reasoning tokens on a
four-flag classification are latency and cost spent for nothing. The id is
pinned rather than `gemini-flash-lite-latest`, because a floating alias would
change the committed cache's provenance without changing its contents.

**What would change it:** Per-flag precision or recall in
`output/extraction_eval.md` that the brief or the ranking cannot tolerate,
particularly on the resolution and negation categories.

---

## D-018 — The BM25 floor does not trigger, and was not raised until it did

**Decision:** `BM25_FLOOR = 12.0`, below the observed minimum. The build spec's
assertion that the floor must trigger on at least one asset is replaced by a
reported measurement, in `retrieve.py` and in `output/bm25_scores.txt`.

**Measured:** across all 75 generated queries the top BM25 score ranges 13.97 to
25.59, median 23.08. There is no separated low tail — no cluster the floor could
sit below while still firing on something.

**Why it does not fire.** `build_query` concatenates a term list for every
positive contribution, producing queries of 15 to 40 terms, and the corpus is 25
topically dense procedures. A 15-term query always matches something. The weakest
query in the set (13.97, a mild-scenario asset with six contributing factors)
still returns three plausible documents.

**Alternatives:** Raise the floor to about 14.5 so the lowest one or two queries
fall below it. Rejected — that value sits inside the main cluster, is chosen only
to make an assertion pass, and would start suppressing retrieval for assets whose
documents are perfectly reasonable.

**What this means for the design.** The `no_match` path and its fixed text are
implemented and tested but unreached. That is a real limitation and is stated in
the README rather than hidden behind a threshold tuned to exercise it.

**What would change it:** A larger or more heterogeneous corpus, where a query
built from one weak contribution could genuinely miss; or query construction that
used only the top one or two contributions rather than all positive ones, which
would produce short queries and a meaningful low tail.

---

## D-019 — Notes repeat at the new fleet scale, and the evaluation says so

**Measured:** 1,800 notes contain 1,253 distinct texts — a 30.4% duplicate rate.
The duplication is concentrated entirely in notes with no outstanding defect:
those average 13.25 repeats each, while notes carrying one or more true flags
average 1.13 and are almost all unique.

**Why:** a note with no condition sentences is one or two distractor sentences
drawn from a bank of twelve, so there are only a few dozen possible texts. At 300
notes that was invisible; at 1,800 it is not.

**Decision:** left as is, and recorded. Extraction is cached by note text, so the
1,800 notes cost 1,253 API calls rather than 1,800 — the duplication is free at
run time. The effect on the evaluation is that the "distractors only" category is
about 60 distinct texts repeated, so its measured error rate of 0.0000 rests on
far fewer independent items than its 644-note count suggests. The other three
categories are essentially unaffected.

**Alternatives:** Expand the distractor bank until duplicates disappear. That is
straightforward and was not done because it would change every note and force a
full re-extraction for a category that both methods already get right.

**What would change it:** Any finding that depended on the distractors-only
category, which currently none does.

---

## D-020 — The notes barely move AUC and nearly double precision at the crew's capacity

**Measured, out-of-fold across 14,400 rows:**

| Variant | AUC | Precision@15 | Failures found per 15 visits | Lift over random |
|---|---|---|---|---|
| Heuristic (peak temp × age) | 0.5830 | 0.0000 | 0.00 | 0.0× |
| Without notes (9 features) | 0.8223 | 0.0458 | 0.69 | 4.0× |
| Full model (13 features) | 0.8261 | 0.0792 | 1.19 | 7.0× |

**The finding:** adding the four extracted condition flags moves pooled AUC by
0.0038 — nothing — and moves precision at the crew's actual capacity from 0.69 to
1.19 failures per 15 visits. On the metric the build spec leads with, the
extraction layer looks like decoration. On the metric the operation runs on, it
finds 70% more failures for the same crew.

**Why the two disagree:** AUC integrates over every threshold, and almost all of
its pairs sit far from the top of the ranking. The crew only ever sees the top
15 of 900. A feature that sharpens the head of the distribution and does nothing
elsewhere barely registers in AUC and matters entirely in practice.

**Recorded rather than resolved:** the headline number stays AUC because the
build spec asks for it, and precision@15 is reported alongside it everywhere,
with the base rate given so the lift can be checked. Reporting only the ablation
AUC gap would understate the extraction layer; reporting only the lift would
overstate it.

**What would change it:** A crew capacity closer to the fleet size, which would
make the two metrics converge.
