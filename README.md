# heat-risk-triage

Ranking 900 substation transformers by heat-failure risk 72 hours ahead of a forecast heat event, so
a crew that can reach 15 of them knows which 15.

The output is a ranked queue with an explanation attached to every row: which factors drove the
score, what the recorded condition of the asset is, in the inspector's own words, and which
maintenance procedure applies.

All data is synthetic. **`ARCHITECTURE.md` describes the architecture, the assumptions behind it,
the measured results and the limitations.** This file covers setup and execution only.

## Requirements

Python 3.11 or later. No database, no external services, no API key.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Running the pipeline

Run the five scripts in order. Each is independently re-runnable and idempotent, and the whole
sequence takes a few minutes.

```bash
python generate_data.py   # data/, output/data_checks.txt
python extract.py         # cache/extractions/, output/extraction_cost.txt
python model.py           # output/scored_*.json, metrics.{json,md}, calibration.png
python retrieve.py        # output/briefs_*.json, output/bm25_scores.txt
python validate.py        # output/extraction_eval.md, output/validation.md
```

| Script | What it does | Fails the build if |
|---|---|---|
| `generate_data.py` | Generates the synthetic fleet, weather, inspection notes and outcomes | Any of six hard data gates fails |
| `extract.py` | LLM extraction of four condition flags from 1,800 inspection notes, plus the keyword baseline | An evidence quote is not verbatim in its note |
| `model.py` | Trains, cross-validates, scores the four demo scenarios, writes explanations | Out-of-fold AUC is above the leakage threshold |
| `retrieve.py` | BM25 retrieval over 25 procedures, then an LLM action brief per top-ranked asset | A brief cites a document it was not given |
| `validate.py` | Extraction against generation-time truth, leakage check, Bayes ceiling, citation integrity | A validation invariant is violated |

The outputs are committed, so the repository can be inspected and the web application run without
executing anything above.

### Running with no API key

`extract.py` and `retrieve.py` are the only scripts that call an LLM, and every call is cached to
disk by a hash of its prompt. **The cache directories are committed, so both scripts complete with no
API key and no network access**, producing byte-identical output.

```bash
python extract.py --offline
python retrieve.py --offline
```

`--offline` fails loudly on a cache miss rather than reaching for the network. To re-run either layer
against the API instead, put a key in `.env`:

```
GEMINI_API_KEY=...
```

`.env` is gitignored. Changing a prompt requires bumping `PROMPT_VERSION` or `BRIEF_PROMPT_VERSION`
in `config.py`, which invalidates the affected cache keys.

## Running the web application

```bash
uvicorn app:app --reload
```

Then open <http://127.0.0.1:8000>. It reads the JSON in `output/` at startup and serves it; there is
no inference, no network call and no external asset at request time. Its only write is appending to
`output/decisions.jsonl` when a decision is accepted or denied.

- `/` — scenario picker
- `/scenario/{id}` — the ranked queue, with the crew-capacity control
- `/scenario/{id}/asset/{asset_id}` — score drivers, inspection evidence, action brief
- `/procedure/{doc_id}` — a procedure document
- `/decisions` — every decision recorded

## Tests

```bash
pytest tests/                                # all 388
pytest tests/test_retrieval.py               # one file
pytest tests/test_retrieval.py::test_name    # one test
```

Tests read the committed `output/` files, so they run without re-running the pipeline.

| File | Covers |
|---|---|
| `test_retrieval.py` | BM25 behaviour, the tokeniser, applicability filtering, the score floor |
| `test_citations.py` | Every brief cites only documents it was given, in its array and in its prose |
| `test_ranking.py` | The interaction terms are what let a forecast reorder the queue |
| `test_interface.py` | What the web application must not misreport |
| `test_notebook.py` | The committed notebook still matches the pipeline that produced it |
| `test_docs.py` | Every table in `ARCHITECTURE.md` still matches the file that produced it |

## Notebook

```bash
jupyter lab notebooks/01_data_model_and_evaluation.ipynb
```

Committed with its outputs intact, so it can be read without being run. It imports from the modules
rather than reimplementing them.

## Repository

```
config.py                  all constants, each marked chosen, measured or assumed
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
tests/                     retrieval, citations, ranking, interface, notebook and doc freshness
ARCHITECTURE.md            architecture, assumptions, measured results, limitations
```
