"""ARCHITECTURE.md reproduces numbers from output/; those copies must not drift.

Every figure in it is a transcription of something a script wrote. The
transcription is done by hand, so it goes stale silently — and it has, repeatedly.
Two audits during the build passed while the file contained wrong numbers,
because they asked whether the correct value appeared *anywhere* in the document.
A value that is right in one table and stale in another satisfies that test and
misleads a reader, which is precisely what happened: the capacity sweep reported
recall@10 as 0.0495 while the per-variant table three paragraphs above reported
0.0239 for the same model.

These tests parse the document's tables and compare them **cell by cell** against
the file that produced them. A stale cell fails even when the same number appears
correctly elsewhere.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402

README = config.REPO_ROOT / "README.md"
ARCHITECTURE = config.REPO_ROOT / "ARCHITECTURE.md"


def load_readme():
    """The measured tables live in ARCHITECTURE.md; the README is instructions."""
    if not ARCHITECTURE.exists():
        pytest.skip("ARCHITECTURE.md not present")
    return ARCHITECTURE.read_text()


def load_metrics():
    path = config.OUTPUT_DIR / "metrics.json"
    if not path.exists():
        pytest.skip("metrics.json not present; run model.py first")
    return json.loads(path.read_text())


def tables(text):
    """Every markdown table, as a list of rows of stripped cells."""
    found = []
    current = []
    for line in text.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(set(c) <= set("- :") for c in cells):  # skip separator rows
                current.append(cells)
        elif current:
            found.append(current)
            current = []
    if current:
        found.append(current)
    return found


def table_with_header(text, *header_prefix):
    """The one table whose header row starts with these cells.

    Matched on a prefix rather than the first cell alone: three tables in the
    document are headed "Variant" and they carry different columns.
    """
    matches = [t for t in tables(text)
               if t and tuple(t[0][:len(header_prefix)]) == header_prefix]
    assert len(matches) == 1, \
        f"expected exactly one table headed {header_prefix}, found {len(matches)}"
    return matches[0]


def numeric(cell):
    """The number in a cell, ignoring markdown emphasis and annotations."""
    stripped = re.sub(r"[*`]|\s*\(default\)\s*", "", cell).strip().rstrip("%")
    return float(stripped)


def test_the_ablation_table_matches_metrics():
    metrics = load_metrics()
    rows = table_with_header(load_readme(), "Variant", "Features")
    body = {r[0].strip("*"): r for r in rows[1:]}
    for name, result in metrics["ablations"].items():
        assert name in body, f"{name} is missing from the ablation table"
        row = body[name]
        for column, key, places in [(1, "n_features", 0), (2, "within_event_auc", 4),
                                    (3, "auc", 4), (4, "precision_at_15", 4),
                                    (5, "recall_at_15", 4)]:
            expected = f"{result[key]:.{places}f}"
            assert numeric(row[column]) == float(expected), (
                f"ablation table, {name}, {key}: the doc says {row[column]}, "
                f"metrics.json says {expected}"
            )


def test_the_capacity_sweep_table_matches_metrics():
    """The table that was wrong. Recall@10 read 0.0495 against a true 0.0239."""
    metrics = load_metrics()
    rows = table_with_header(load_readme(), "Capacity", "Precision@k")
    body = {int(numeric(r[0])): r for r in rows[1:]}
    for entry in metrics["capacity_sweep"]:
        k = entry["capacity"]
        assert k in body, f"capacity {k} missing from the sweep table"
        row = body[k]
        assert numeric(row[1]) == float(f"{entry['precision']:.4f}"), \
            f"sweep k={k} precision: doc says {row[1]}, metrics {entry['precision']:.4f}"
        assert numeric(row[2]) == float(f"{entry['recall']:.4f}"), \
            f"sweep k={k} recall: doc says {row[2]}, metrics {entry['recall']:.4f}"
        assert numeric(row[3]) == float(f"{entry['fleet_share'] * 100:.1f}"), \
            f"sweep k={k} fleet share: doc says {row[3]}"


@pytest.mark.parametrize("field,places", [("hits", 0), ("recall", 4)])
def test_the_per_variant_tables_match_metrics(field, places):
    """Two tables share a header, so they are matched by the values they carry."""
    metrics = load_metrics()
    by_variant = metrics["capacity_sweep_by_variant"]
    expected_first = f"{by_variant['Register only'][0][field]:.{places}f}"

    candidates = [t for t in tables(load_readme())
                  if t and t[0][0] == "Variant" and len(t[0]) == len(config.CAPACITY_SWEEP) + 1]
    matching = [t for t in candidates
                if any(r[0] == "Register only" and numeric(r[1]) == float(expected_first)
                       for r in t[1:])]
    assert matching, f"no table carries the per-variant {field} figures"

    body = {r[0]: r for r in matching[0][1:]}
    for name, rows in by_variant.items():
        assert name in body, f"{name} missing from the per-variant {field} table"
        for column, entry in enumerate(rows, start=1):
            expected = f"{entry[field]:.{places}f}"
            assert numeric(body[name][column]) == float(expected), (
                f"per-variant {field}, {name}, k={entry['capacity']}: "
                f"the doc says {body[name][column]}, metrics says {expected}"
            )


def test_the_two_recall_tables_do_not_contradict_each_other():
    """The specific failure: the full model's recall appears in two tables.

    They are transcribed separately and were allowed to disagree. Whatever the
    source says, the document must say the same thing in both places.
    """
    metrics = load_metrics()
    sweep = {r["capacity"]: r["recall"] for r in metrics["capacity_sweep"]}
    variant = {r["capacity"]: r["recall"]
               for r in metrics["capacity_sweep_by_variant"]["Full model"]}
    assert sweep == variant, "metrics.json itself disagrees; model.py is at fault"

    text = load_readme()
    rows = table_with_header(text, "Capacity", "Precision@k")
    for entry in metrics["capacity_sweep"]:
        printed = numeric({int(numeric(r[0])): r for r in rows[1:]}[entry["capacity"]][2])
        assert printed == float(f"{variant[entry['capacity']]:.4f}"), (
            f"the capacity sweep table and the per-variant table disagree at "
            f"k={entry['capacity']}"
        )


def test_headline_counts_match_the_build():
    text = load_readme()
    for label, expected in [
        ("features", f"{len(config.FEATURES)} features"),
        ("scenarios", f"| Demo scenarios | {len(config.SCENARIOS)} |"),
        ("briefs", f"| Action briefs | {config.BRIEF_TOP_N * len(config.SCENARIOS)} |"),
        ("assets", f"| Assets | {config.N_ASSETS} |"),
    ]:
        assert expected in text, f"{label}: expected {expected!r}"


def test_no_dropped_feature_is_described_as_a_feature():
    """Prose survives numeric audits. Two claims about `max_overnight_min_c`
    outlived its removal because neither contained a number."""
    text = load_readme()
    for name in ["max_overnight_min_c"]:
        if name in config.FEATURES:
            continue
        for pattern in [rf"`{name}`[^.]{{0,60}}\bis a feature\b",
                        rf"`{name}`[^.]{{0,60}}\bare features\b"]:
            assert not re.search(pattern, text), \
                f"the doc still describes the dropped {name} as a feature"


def test_the_cost_table_matches_what_the_scripts_recorded():
    """Cost figures are quoted in a PRD, so a stale one is worse than none.

    The previous extraction figure overstated tokens by 38% for a year of this
    build's life, because the counts were summed per inspection while the cache
    is keyed per note text. Nothing caught it until someone needed the
    number for something.
    """
    import json as _json
    for path in ["extraction_cost.txt", "brief_cost.txt"]:
        if not (config.OUTPUT_DIR / path).exists():
            pytest.skip(f"{path} not present; run the pipeline first")

    def figure(filename, prefix):
        for line in (config.OUTPUT_DIR / filename).read_text().splitlines():
            if line.startswith(prefix):
                return int(line.split()[-1])
        raise AssertionError(f"{filename} has no line starting {prefix!r}")

    rows = table_with_header(load_readme(), "Layer", "Calls from empty")
    body = {r[0].strip("*"): r for r in rows[1:]}

    expected = {
        "Extraction": (figure("extraction_cost.txt", "calls to rebuild from empty:"),
                       figure("extraction_cost.txt", "input tokens:"),
                       figure("extraction_cost.txt", "output tokens:")),
        "Briefs": (figure("brief_cost.txt", "calls to rebuild from empty:"),
                   figure("brief_cost.txt", "input tokens:"),
                   figure("brief_cost.txt", "output tokens:")),
    }
    for layer, (calls, tokens_in, tokens_out) in expected.items():
        assert layer in body, f"{layer} missing from the cost table"
        row = body[layer]
        for column, value, what in [(1, calls, "calls"), (2, tokens_in, "input tokens"),
                                    (3, tokens_out, "output tokens")]:
            assert numeric(row[column].replace(",", "")) == value, (
                f"cost table, {layer}, {what}: the doc says {row[column]}, "
                f"the run recorded {value:,}"
            )

    total = body["Total"]
    assert numeric(total[1].replace(",", "")) == sum(v[0] for v in expected.values())
    assert numeric(total[2].replace(",", "")) == sum(v[1] for v in expected.values())
    assert numeric(total[3].replace(",", "")) == sum(v[2] for v in expected.values())


def test_extraction_tokens_are_counted_once_per_distinct_note():
    """The bug this guards: 1,800 inspections share 1,298 texts and one cache
    entry each, so summing per inspection charged 502 duplicates twice."""
    import pandas as pd
    path = config.DATA_DIR / "inspections.csv"
    if not path.exists() or not (config.OUTPUT_DIR / "extraction_cost.txt").exists():
        pytest.skip("inspections or extraction cost not present")
    reported = None
    for line in (config.OUTPUT_DIR / "extraction_cost.txt").read_text().splitlines():
        if line.startswith("distinct note texts:"):
            reported = int(line.split()[-1])
    assert reported is not None, "extraction_cost.txt no longer reports distinct texts"
    assert reported == pd.read_csv(path)["note_text"].nunique()


def test_the_readme_names_scripts_that_exist():
    """The README is now the only run instruction, so a stale command is a dead end."""
    text = README.read_text()
    for script in re.findall(r"^python (\w+\.py)", text, re.MULTILINE):
        assert (config.REPO_ROOT / script).exists(), \
            f"README tells the reader to run {script}, which does not exist"


def test_the_readme_names_test_files_that_exist():
    text = README.read_text()
    for name in re.findall(r"`(test_\w+\.py)`", text):
        assert (config.REPO_ROOT / "tests" / name).exists(), \
            f"README lists tests/{name}, which does not exist"


def test_no_tracked_file_points_at_a_document_that_is_not_shipped():
    """The build's decision log is kept locally and is not part of the repository.

    Thirty-odd source comments used to end by citing an entry in it. Left in
    place they would send a reader to a file that is not there, which is worse
    than no pointer at all — the reasoning they pointed at now sits in the
    comment itself or in ARCHITECTURE.md. The needle is assembled here rather
    than written out so that this file does not trip its own check.
    """
    import subprocess
    needle = "DECISIONS" + ".md"
    entry = re.compile(r"\bD-0\d\d\b")
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=config.REPO_ROOT,
        capture_output=True, text=True, check=True).stdout.split()
    offenders = []
    for name in tracked:
        path = config.REPO_ROOT / name
        if path.suffix not in {".py", ".md", ".html", ".js", ".css", ".ipynb", ".txt"}:
            continue
        try:
            body = path.read_text()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        if needle in body or entry.search(body):
            offenders.append(name)
    assert not offenders, f"tracked files cite the local decision log: {offenders}"


def test_the_divergence_figures_match_what_model_py_measured():
    """Prose, not a table, so the cell-by-cell tests never saw it.

    It was stale by one asset on two of the six pairs, and by one on the upper
    end of the range quoted twice elsewhere. Every other number in the document
    is guarded; this one was guarded by nobody reading it.
    """
    path = config.OUTPUT_DIR / "ranking_divergence.txt"
    if not path.exists():
        pytest.skip("ranking_divergence.txt not present; run model.py first")
    measured = re.findall(r"(\d+) of 15 differ by priority, (\d+) by risk alone",
                          path.read_text())
    assert measured, "ranking_divergence.txt no longer reports pairwise divergence"

    text = load_readme()
    quoted = re.search(
        r"top 15 by priority: ([\d, ]+) assets differ\s+pairwise; by risk alone ([\d, ]+)\.",
        text)
    assert quoted, "the document no longer quotes the pairwise divergence figures"
    by_priority = [int(n) for n in quoted.group(1).split(",")]
    by_risk = [int(n) for n in quoted.group(2).split(",")]
    assert by_priority == [int(p) for p, _ in measured], \
        f"priority divergence: doc says {by_priority}, model.py measured {measured}"
    assert by_risk == [int(r) for _, r in measured], \
        f"risk divergence: doc says {by_risk}, model.py measured {measured}"

    # The same span is quoted twice more, in prose, as a range.
    low, high = min(by_priority), max(by_priority)
    assert f"differs by {low} to {high} assets between scenarios" in text, \
        f"the document does not quote the measured range of {low} to {high}"
