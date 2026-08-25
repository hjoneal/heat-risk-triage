"""The notebook is a committed artefact, and committed artefacts go stale.

`notebooks/01_data_model_and_evaluation.ipynb` stores its outputs inline, so
re-running the pipeline silently invalidates every number displayed in it. That
happened repeatedly during the build and was caught by eye rather than by
anything automatic — twice it had been committed showing figures from a previous
model. These tests fail instead.

They do not execute the notebook. Executing it takes about a minute and needs
jupyter in the test environment; reading its stored outputs and comparing them
against `output/metrics.json` catches the same drift for the cost of parsing a
JSON file.

Two things they cannot catch, both verified rather than assumed. A change in what
the notebook *computes* that leaves these particular figures untouched — for that,
run it. And partial staleness: the figure check asks whether the current value
appears anywhere in the notebook's output, so a notebook where some cells were
re-run interactively and others were not can pass while displaying a mixture.
Checked against the real failure mode — the committed notebook from before
`max_overnight_min_c` was dropped — the tests fail on four counts, because
`nbconvert` re-executes everything or nothing and genuine staleness is total.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402

NOTEBOOK = config.REPO_ROOT / "notebooks" / "01_data_model_and_evaluation.ipynb"


def load_notebook():
    if not NOTEBOOK.exists():
        pytest.skip(f"{NOTEBOOK.name} not present")
    return json.loads(NOTEBOOK.read_text())


def load_metrics():
    path = config.OUTPUT_DIR / "metrics.json"
    if not path.exists():
        pytest.skip("metrics.json not present; run model.py first")
    return json.loads(path.read_text())


def code_cells(notebook):
    return [c for c in notebook["cells"] if c["cell_type"] == "code"]


def output_text(notebook):
    """Everything the notebook displays, as one string."""
    chunks = []
    for cell in notebook["cells"]:
        for out in cell.get("outputs", []):
            if out.get("output_type") == "stream":
                chunks.append("".join(out.get("text", [])))
            elif "text/plain" in out.get("data", {}):
                chunks.append("".join(out["data"]["text/plain"]))
    return "\n".join(chunks)


def test_the_notebook_is_committed_with_its_outputs():
    """Stripped outputs would make every check below vacuously pass."""
    notebook = load_notebook()
    cells = code_cells(notebook)
    unexecuted = [i for i, c in enumerate(cells) if c.get("execution_count") is None]
    assert not unexecuted, (
        f"{len(unexecuted)} code cells carry no execution count; the notebook was "
        f"committed unexecuted or with outputs stripped"
    )
    assert output_text(notebook).strip(), "the notebook displays nothing"


def test_the_notebook_ran_clean():
    notebook = load_notebook()
    errors = [
        (i, out.get("ename"), "".join(out.get("evalue", "")))
        for i, cell in enumerate(notebook["cells"])
        for out in cell.get("outputs", [])
        if out.get("output_type") == "error"
    ]
    assert not errors, f"the committed notebook contains error output: {errors}"


def test_the_notebook_rendered_its_plots():
    """A notebook whose figures did not render looks fine in git and is useless.

    This has happened once already: `matplotlib.use("Agg")` at import time in
    model.py hijacked the backend for anything importing it, and the notebook
    committed with every plot missing. The backend call now lives inside main().
    """
    notebook = load_notebook()
    images = sum(
        1
        for cell in notebook["cells"]
        for out in cell.get("outputs", [])
        if "image/png" in out.get("data", {})
    )
    assert images >= config.NOTEBOOK_MIN_PLOTS, \
        f"only {images} plots rendered, expected at least {config.NOTEBOOK_MIN_PLOTS}"


def displayed_figures(metrics):
    """The figures the notebook prints, formatted exactly as it prints them."""
    full = metrics["ablations"]["Full model"]
    return {
        "training rows": f"{metrics['n_rows']:,}",
        "failure count": f"failures: {metrics['n_failures']}",
        "base rate": f"base rate: {metrics['base_rate']:.4f}",
        "feature count": f"x {len(config.FEATURES)} columns",
        "pooled AUC": f"{full['auc']:.4f}",
        "within-event AUC": f"{full['within_event_auc']:.4f}",
        "Brier, model": f"{metrics['brier']['model']:.5f}",
        "Brier, base rate": f"{metrics['brier']['base_rate']:.5f}",
    }


@pytest.mark.parametrize("name", list(displayed_figures({
    "ablations": {"Full model": {"auc": 0, "within_event_auc": 0}},
    "brier": {"model": 0, "base_rate": 0}, "n_rows": 0, "n_failures": 0, "base_rate": 0,
})))
def test_notebook_figures_match_the_pipeline(name):
    """Each headline figure in the notebook must match what model.py last wrote.

    If this fails the notebook is stale: re-run it with
    `jupyter nbconvert --to notebook --execute --inplace`.
    """
    notebook = load_notebook()
    expected = displayed_figures(load_metrics())[name]
    assert expected in output_text(notebook), (
        f"the notebook does not show the current {name} ({expected!r}); "
        f"re-execute it against the current output/"
    )


def test_the_notebook_imports_rather_than_reimplements():
    """The scripts are the source of truth; the notebook is a reader of them.

    A notebook that redefines the model would drift from the pipeline while
    every figure above still matched, because it would be self-consistent.
    """
    notebook = load_notebook()
    source = "\n".join("".join(c["source"]) for c in code_cells(notebook))
    for module in ["config", "features", "model", "extract", "validate"]:
        assert re.search(rf"^import {module}$", source, re.M), \
            f"the notebook does not import {module}"
    for banned, reason in [
        (r"\bLogisticRegression\s*\(", "the model is built by model.build_model"),
        (r"\bStandardScaler\s*\(", "scaling belongs to the pipeline in model.py"),
        (r"\bGroupKFold\s*\(", "the CV protocol is model.out_of_fold_predictions"),
    ]:
        assert not re.search(banned, source), \
            f"the notebook reimplements part of the pipeline: {reason}"
