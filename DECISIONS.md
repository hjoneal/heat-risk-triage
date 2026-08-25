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

**What this means for the design.** No real query reaches the `no_match` path.
Left there, the branch and its fixed text would be unexecuted code, so two unit
tests reach it with a synthetic degenerate query — which covers the behaviour
without moving the production threshold to manufacture a trigger. That the path
is unreachable in production is stated as a limitation rather than hidden behind
a tuned threshold.

The concrete cost of a floor that did fire: at 14.5 it suppresses exactly one of
the 75 queries, baseline-mild / SUB-SGW-095, whose three retrieved documents —
MG-021 de-rating, SOP-014 cooling inspection, MG-025 overnight recovery — are
the right three for an asset driven by peak load, a warm overnight minimum, an
oil issue and degraded cooling. The single query it would suppress is one where
retrieval worked.

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


---

## D-021 — Results are reported against the Bayes ceiling, not against perfection

**Decision:** `validate.py` computes and reports how the model ranks against the
true generative probability, and `output/validation.md` and the README carry it
alongside every headline figure.

**Measured:**

| Ranked by | Pooled AUC | Within-event AUC | Precision@15 |
|---|---|---|---|
| The model (out-of-fold) | 0.8261 | 0.6219 | 0.0792 |
| True generative probability | 0.8483 | 0.7192 | 0.0833 |

The model reaches 86.5% of achievable within-event AUC and 95.0% of achievable
precision at the crew's capacity.

**Why it was added.** Precision@15 of 0.0792 means the crew finds about 1.2
failures in 15 visits, which reads as a weak system. Measured against a
*clairvoyant* ranking — one that simply knows which assets failed — it looks
worse still, capturing 17.9% of what that ranking achieves. But a clairvoyant
ranking is not a ceiling any model can approach, because the outcome is a
Bernoulli draw at roughly 1% and most of its variation is irreducible. The
honest ceiling is the true generative probability, and against that the model
has little headroom left.

Reporting the operational numbers without this context would understate the
model; reporting the lift over random without it would overstate it. Both
appear, with the ceiling between them.

**The diagnostic reads hidden state, so it lives in `validate.py`** — the only
module permitted to — and the generator intercept is re-solved by importing
`generate_data.solve_intercept` rather than stored, so it cannot drift from the
generator it describes.

**What would change it:** Real outcome data, where no true probability exists and
the ceiling would have to be estimated rather than computed.

---

## D-022 — Crew capacity is adjustable; forecast values are not

> **Superseded in part by D-024 and D-031.** The invariance recorded below was
> real and was measured correctly, but it was a property of a model with no
> interaction terms rather than a property of the problem. Adding them, and
> centring them, makes the forecast reorder the queue by 3 to 9 of the top 15.
> The conclusion that capacity is the more decisive control still holds — the
> forecast's effect on the ranking remains smaller than capacity's — but "the
> ranking is invariant to hazard" is no longer true and should not be quoted.


**Decision:** The queue carries a crew-capacity control, range 5 to 25, applied
as a query parameter with a plain form and a submit button. No JavaScript. The
hazard values stay fixed at the three precomputed scenarios.

**Why capacity and not the forecast.** Measured: the ranking is invariant to
hazard. Varying the long-moderate scenario across ±3 °C of peak, 3 to 6 days,
and amplitudes from 2.5 to 7.0 leaves the top 15 unchanged in every case —
15 of 15 overlap, rank 1 never moves.

That is structural rather than incidental. Within one forecast every asset gets
identical hazard features, so the hazard term is a constant added to every
asset's log-odds. At a 1% base rate the sigmoid is still in its exponential
regime, so that constant is a uniform multiplicative rescaling of every risk, and
`priority = risk × customers_served` preserves the order exactly. A forecast
slider would move every percentage on screen and change nobody's queue position —
which would imply a sensitivity the model does not have.

Capacity is the opposite: it changes nothing about the scores and everything
about which assets get visited, because it decides where the line falls across a
fixed ranking. It is the only input on this screen that changes an outcome.

**What it shows.** Alongside the line, the expected number of failures the
selected visits would reach, against the expected total fleet-wide. At the
long-moderate scenario: 5 visits reach 0.1 of 5.3, 15 reach 0.4, 25 reach 0.6.
The diminishing return is visible, and so is the coverage problem — 25 visits
against 900 assets intercept about a ninth of what the forecast implies.

**Serve time.** Both figures are sums over probabilities already in the scored
JSON. No model is loaded, no inference runs, no network call is made; the
property in build spec §6.1 holds unchanged.

**Alternatives:** Forecast sliders driving a re-ranked queue — rejected on the
measurement above, and because off-template hazard would change the sign of
standardised hazard contributions, changing the retrieval query and leaving the
displayed LLM brief describing documents that no longer match. A fleet-magnitude
sensitivity view without re-ranking remains viable and is not built.

**What would change it:** Per-district hazard. If districts received different
forecast values the hazard term would stop being uniform and the ranking would
genuinely reorder, which is what would make a forecast control meaningful — and
which is the extension already recorded in D-015.

## D-023 — Air-conditioning demand is coupled to temperature, and the load rise goes as the square

**Decision:** `load_rise` is computed per asset per hour inside
`compute_thermal_stress` rather than once per asset in `generate_hidden_state`.
Effective load rises 2% per degree above 25 °C, caps at 1.15 of rating, and the
winding rise scales with the square of effective load against the reference.

**Why.** A static per-asset load rise makes a mild day and a severe day impose
identical loading, which contradicts the mechanism the project exists to model:
air-conditioning demand peaks at the same hours cooling capacity is worst, so an
asset is carrying its heaviest load exactly when it can least afford to.

The exponent is physics rather than a fitted parameter. Resistive loss goes as
I²R, so a 20% load increase produces about 44% more heat from that source. That
is what makes the gap between a heavily and a lightly loaded asset *widen* as the
day gets hotter, instead of staying constant as it did under an additive form.

**Measured effect.** The ratio of mean thermal stress between the top and bottom
load quintiles rises from 2.97 to 7.11 on a long-moderate event, and from 1.99 to
4.07 on a short-severe one. Fleet-mean stress rises from 1.11 to 2.58 equivalent
days, which is what forced the `HAZARD_SCALE` re-derivation in D-027.

**Alternatives considered.** A linear rise in load with temperature was rejected
because it has no physical justification and understates the differential at the
top of the range. Raising `AC_DEMAND_BASE_C` to 28 or 30 °C, so mild events stay
demand-free, was measured and rejected: it did not change any gate outcome and
would have been a parameter chosen for its effect on a gate rather than on
physical grounds.

**What would change it.** Real loading telemetry. The 2% per degree slope is
assumed; a utility with metered feeder data would fit it, and the cap would come
from the protection settings rather than from a round number.

**Risk accepted.** The cap binds on only 5.5% of asset-hours during a severe
event, so it is close to inert. Left in because a transformer that runs
indefinitely past its rating is not a model of anything real.

## D-024 — Three interaction features, because an additive model cannot reorder under a uniform hazard

**Decision:** `FEATURES` gains `load_x_degree_hours`,
`condition_x_degree_hours` and `age_x_warm_nights`, taking it to 16. Raw
products, standardised like any other column.

**Why.** Every hazard feature is constant within an event: all 900 assets share
one `degree_hours_above_30`. Logistic regression is additive in the log-odds, so
the hazard block adds the *same number* to every asset's logit, and adding a
constant cannot change an ordering. The consequence is not subtle — before this
change the top 15 was byte-identical across all three forecasts, and no amount of
weather variation could move it. D-022 documented that invariance as a property;
this entry removes its cause.

An interaction term is asset value × event value. Standardised, its contribution
is `(b·DH(e)/σ)·load_i + constant`, so the *coefficient on the asset's own load*
depends on the forecast. That is the only way an additive model in the log-odds
can express "a heavily loaded asset suffers disproportionately in a severe
event".

**Measured.** `load_x_degree_hours` fits at the largest coefficient in the model.
Risk ranking now moves between scenarios; without the three terms it is provably
identical, which `tests/test_ranking.py` asserts in both directions.

**Alternatives considered.** Per-event models were rejected: 900 rows and roughly
nine failures per event will not support 13 features. A tree model would capture
the interaction without being told to, and is on the flagged list for exactly the
reason it is refused here — the contribution table on the asset page is the
product, and `coefficient × standardised value` summing to `logit(p) − intercept`
is what makes it honest.

**Cost accepted.** 16 features against about 138 positives is roughly nine events
per variable, at the conventional lower limit, and interaction terms are
correlated with their components by construction. `LOGREG_PENALTY = "l2"` matters
for that reason: L1 would zero one member of each correlated pair arbitrarily and
make the on-screen explanation unstable between runs. Per-fold coefficient means
and standard deviations are reported in `output/metrics.md`, sign flips included.

## D-025 — The no-notes ablation excludes the interaction built from the notes

**Decision:** `NO_NOTES_FEATURES` is a comprehension excluding both
`flag_*` and `condition_x_degree_hours`, giving 11 features rather than the
previous positional slice `FEATURES[:9]`.

**Why.** `condition_x_degree_hours` is built from the count of extracted
condition flags. Leaving it in the no-notes arm would feed note-derived
information into the variant that is supposed to have none, and would understate
the uplift the extraction layer is credited with — the arm would quietly get
most of the notes' signal while being labelled as having none.

The positional slice was also fragile in a way the comprehension is not: it
depended on the flag features staying contiguous at index 9, which the
interaction block broke.

**What would change it.** Nothing about the split. If further interaction terms
are added, each has to be classified as note-derived or not, and the
comprehension makes that a one-line decision rather than an index arithmetic
problem.

## D-026 — Gate 7 was specified, measured as unreachable, and left dropped after it became reachable

**Decision:** There is no gate 7. The specified form — at least three of the top
fifteen must differ between any two demo scenarios — was dropped when measured as
unreachable. Two later changes made it reachable, and it was still left out. The
measurement lives in `output/ranking_divergence.txt`.

**Why it failed at first.** The diagnosis behind the gate analysed `logit(p)`,
but the crew's queue is ordered by `priority = risk × customers_served`, and that
is where it broke. Measured across five scenario configurations spanning the
whole historical envelope:

| Scenario set | Degree-hour ratio | Differ by priority | By risk |
|---|---|---|---|
| Original three | 1.4× | 0, 0, 0 | 2, 2, 4 |
| Envelope edges | 1.8× | 1, 0, 1 | 2, 2, 4 |
| long-severe substituted | 3.5× | 1, 0, 1 | 3, 2, 5 |
| Both combined | 3.5× | 2, 0, 2 | 4, 2, 6 |

Then the decisive control: removing `customers_served` from the ranking
*entirely* still gave 4, 2, 6, because the short-severe against baseline-mild
pair capped at 2 on pure risk. `customers_served` spans 45× across the fleet,
3.81 natural-log units, against a between-scenario re-weighting of log-risk with
a range near 0.5 — roughly seven times smaller.

**What made it reachable.** A fourth scenario (D-028) widened the forecasts on
offer, and centring the interaction products (D-031) stopped the model putting
negative coefficients on its own hazard features, which had been suppressing the
forecast's effect on the ranking. Divergence is now 3 to 9 assets by priority and
5 to 13 by risk. The dropped gate would pass.

**Why it stays dropped.** The threshold of three was chosen before anyone knew
what the system could reach. Reinstating it now, having seen that the measured
minimum is exactly three, would be picking a threshold that fits the data — the
same move as lowering it to two would have been when the measurement was two. The
number would carry no information either way. What is worth keeping is the
comparison that has a right answer independent of any threshold: with the
interaction terms the ranking responds to the forecast, without them it is
provably identical. `tests/test_ranking.py` asserts both directions.

**What still stands.** Priority divergence is consistently below risk divergence,
so `customers_served` does damp the forecast's effect even though it no longer
erases it. The finding is pinned by a test.

## D-027 — HAZARD_SCALE re-derived, and the sweep that got it wrong first

**Decision:** `HAZARD_SCALE` moves from 2.4 to **1.14**. The bracketing procedure
from D-014 was repeated against gates 1, 4 and 6 and the out-of-fold AUC ceiling.
Measured on the final configuration, gate 4 fails at 0.95 (six mild failures) and
the AUC ceiling at 1.40 (0.9092 against a 0.90 limit), leaving a feasible band of
**[0.98, 1.35]**; 1.14 is its centre. Gate 6 has margin throughout the band and
only binds outside it, at 1.50.

**Why it had to move.** Demand coupling (D-023) raised fleet-mean thermal stress
from 1.11 to 2.58 equivalent days. The scale multiplies two coefficients that act
on that quantity, so leaving it alone would have roughly doubled the hazard's
contribution to the log-odds. Gate 6 failed outright at 2.4, at 0.0677 against a
0.20 threshold.

**The error worth recording.** The first sweep drew failures from a fresh
`default_rng(SEED)` rather than continuing the generator's own stream. Both
binding gates turn on single-figure failure counts, so the draw's position in the
stream is not a detail: the faulty sweep reported gate 6 at 0.16 where the true
value was 0.24, and concluded that *no* scale satisfied both gates. The band it
described did not exist. Repeating the sweep with the pipeline's exact stream
found a feasible band immediately.

The band was re-measured twice more, because it moves with the stream: adding a
fourth scenario (D-028) shifted every draw and widened it from [1.02, 1.12] to
[0.98, 1.35]. The value recorded here is from the final configuration, not from
the first sweep that produced a passing number.

The general lesson: when a gate turns on a handful of Bernoulli draws, any
harness that reproduces the pipeline approximately is measuring a different
system.

**What would change it.** A higher `TARGET_FAILURE_RATE`. Both bounds are
counting statistics on very few events, and the band is narrow by construction
rather than by choice. More positives would widen it and make the centre mean
something.

## D-028 — A fourth scenario, because three did not span what the model was trained on

**Decision:** `long-severe` joins the demo scenarios, at 5 days, peak 40 °C,
amplitude 3.5.

**Why.** The three original scenarios spanned 2.8 to 283.6 degree-hours. The
model is trained on events spanning 0 to 787. `long-severe` is three of the
sixteen training events — the most damaging type in the bank — and had no
scenario at all, so the demo silently omitted the case the tool most needs to
handle. Every scenario now sits inside the historical envelope for its type,
which `tests/test_ranking.py` asserts; a forecast outside it would ask a linear
model to extrapolate, and nothing in a linear model warns when it does.

**Cost accepted.** Adding a scenario adds hourly weather draws, which shifts the
generator's random stream and therefore changes every inspection note. All 1,800
extractions were re-run. That was the known price and it was paid deliberately:
the alternative was a demo that misrepresented the model's training range in
order to protect a cache.

**Alternatives considered.** Substituting `long-severe` for `long-moderate` would
have been stream-neutral and free, but `long-moderate` is the scenario that
demonstrates the project's premise — a long moderate event outranking a short
severe one — and dropping it to save an API bill was the wrong trade.

## D-029 — Within-event AUC leads the reporting; pooled AUC was flattering the model

**Decision:** `evaluate()` returns both `within_event_auc` and pooled `auc`, and
`output/metrics.md` leads with the within-event figure. The pooled number is kept
for continuity with the build spec and because the leakage assertion is set
against it.

**Why.** Pooled AUC puts a mild event's rows beside a severe event's, so a model
scores partly by telling those apart. The hazard features make that trivial, and
the supervisor already knows which one they are in — the forecast is why they
opened the tool. Within an event the hazard block is constant for every asset, so
within-event AUC measures only the thing the system is for: ranking 900 assets
against one another under one forecast. It runs about 0.20 below the pooled
figure, and that gap is the part of the pooled score that answers a question
nobody asked.

**How it surfaced, and the mistake it corrected.** The interaction features and
demand coupling appeared to have destroyed the extraction layer's value: pooled
AUC without notes came out *above* the full model. Measured within event, the
notes were still clearly positive. The pooled comparison was the artefact.

**The mechanism.** With `GroupKFold` each fold's intercept is fitted to the events
*not* held out. Hold out the mild events and the model scoring them was
calibrated on hotter ones, so it over-predicts them; hold out severe events and
it under-predicts. Pooling those predictions makes fold membership — which
anti-correlates with true event risk — a component of the ranking. A feature set
with hazard features can partly correct for it; one without cannot. That is how
four condition flags with a clean monotone 3.3× lift in failure rate (0.59% at
zero flags rising to 1.94% at three) score **0.4374** pooled, below random, and
**0.6171** within event.

Precision and recall at crew capacity were already computed per event and were
never affected. Only AUC was.

**Alternatives considered.** Centring predictions per fold before pooling would
remove the distortion, but it adds a correction step to the metric rather than
reporting a metric that does not need one. Reporting only within-event AUC was
rejected because the build spec asks for pooled and because the two together are
more informative than either.

**What would change it.** Stratifying folds so each contains a similar mix of
event severities would shrink the effect without removing it. The clean fix is
per-event evaluation, which is what leading with within-event AUC amounts to.

## D-030 — The ablation is a ladder, not a pair

**Decision:** `output/metrics.md` reports five variants: heuristic, register
only, register plus notes, register plus interactions, and the full model.

**Why.** Two changes landed at once — the note flags were already there, and the
interaction terms were added on top. With only a with-notes and without-notes
pair, each variant differed from the full model in two respects, and neither the
notes nor the interactions could be credited separately. The middle two rungs
each differ from the full model in exactly one respect, which is the only
arrangement that answers "what did this buy".

**What it showed.** The interaction terms do not improve either AUC on their own —
they were added to make the ranking respond to the forecast at all (D-024), not
to improve discrimination, and the measurement says plainly that they did not.
Recording that is the point of running the ladder.

## D-031 — Interaction terms multiply centred components, not raw ones

**Decision:** Each interaction is a product of components centred on a fixed
training mean, not a raw product. The five centres are measured constants in
`config.py` and `model.py` asserts they still match the training data.

**Why, and it is not a statistical nicety.** A raw product is collinear with the
features it is built from. Fitted that way, the product term takes the shared
signal and leaves the residual behind on the components, which came out negative
on degree-hours, peak temperature, warmest overnight minimum, warm nights and
age — five of the model's own hazard and ageing features.

The consequence is visible on screen. The asset page for the highest-risk asset
in the most severe scenario reported that 40.7 °C contributed −0.191 and a 33 °C
overnight minimum −0.805: the interface told a crew supervisor that the heat had
*lowered* the asset's risk. It also broke retrieval, because `build_query` reads
only positive contributions, so every heat-related term was dropped from the
query for exactly the assets a heat procedure applies to.

Golden rule 1 puts explainability above performance, and this was not even a
trade against performance.

**Measured, raw against centred.**

| | Raw products | Centred |
|---|---|---|
| Wrong-signed hazard/age coefficients | 5 | 2 |
| Degree-hours coefficient | −0.2388 | +1.0920 |
| Within-event AUC | 0.6348 | 0.6213 |
| Pooled AUC | 0.8398 | 0.8412 |
| Top-15 divergence by priority | 2 to 5 | 3 to 9 |

Centring costs 0.014 of within-event AUC and buys back the explanation, the
retrieval query and more forecast sensitivity. The two coefficients still
negative — peak temperature and warmest overnight minimum — are the pre-existing
collinear pair already documented in the README, correlated at r=0.93 with
degree-hours, and were negative before any interaction term existed.

**Why the centres are constants.** They cannot be column means. A scenario matrix
holds one event, so its own mean degree-hours is that event's value, and a
self-centred term would collapse to zero for all 900 assets — the term would
silently stop working at exactly the moment it is used. Constants go stale
instead, which is the failure mode that can be checked, so `model.py` asserts the
training means still match within 1%.

**Alternatives considered.** Dropping the interaction terms restores the cleanest
coefficients and the best within-event AUC (0.6604) but takes the forecast's
effect on the ranking back to zero, which is what D-024 exists to fix. Centring
only `load_x_degree_hours`, the worst offender, was rejected as arbitrary.
Residualising each product against its components would decorrelate them fully
but makes the feature a fitted quantity rather than an arithmetic one, and the
contribution table would no longer be readable as a product of things a
supervisor can see.

**What would change it.** More positives. The collinearity is tolerable at 138
failures because L2 keeps the coefficients stable across folds; at a tenth of
that it would not be.
