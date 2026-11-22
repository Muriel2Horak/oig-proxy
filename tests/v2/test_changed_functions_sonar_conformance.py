# pylint: disable=missing-function-docstring
"""Offline exact conformance of scripts/quality/changed_functions.cognitive_complexity
against the Sonar python:S3776 baseline of local:oig-proxy (analysis cd805b34, source 9fc94237).

Fixture: tests/fixtures/changed_functions/s3776_baseline.jsonl.gz
  meta record  = provenance (project, rule, analysis key, source revision, retrieval)
  issue record = one per Sonar S3776 issue: path, line, col (def NAME position), score
                 (from the message "from N to the 15 allowed"), flow_sum (sum of Sonar's +N flow items)
  file record  = every main-code .py file Sonar held at the baseline revision (path, sha256, text);
                 files under a .gitignore rule (`scripts/`) are not in Sonar, tracked or not
No Sonar access and no repo history are needed.

Checks: exact score match on every Sonar-flagged function; none scores <= 15 locally; and no
main-code function Sonar did NOT flag scores > 15 locally (never stricter than Sonar).
"""
import ast
import collections
import gzip
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "changed_functions" / "s3776_baseline.jsonl.gz"
CHECKER = ROOT / "scripts" / "quality" / "changed_functions.py"
THRESHOLD = 15

# Independent pins: they live in this file, NOT in the fixture, so editing the fixture
# (dropping issues, rewriting metadata, editing sources) cannot also edit the expectation.
PINNED_SOURCE_SHA = "9fc9423710e22c06a1df8bd3eb72774af114b1c9"
PINNED_ANALYSIS_KEY = "cd805b34-447c-4646-b2c5-1c1f4bbee141"
PINNED_ISSUE_COUNT = 42
PINNED_FILE_COUNT = 41
# sha256 over the sorted JSON list of (path, line, col, score, key) for every issue
PINNED_IDENTITY_DIGEST = "2b0732f5747981450e504b49b828c95fdb4fc76e25a41cfaf69ced8d685ef11c"
# sha256 over the sorted JSON list of (path, sha256) for every file record
PINNED_FILES_DIGEST = "fbcdcec9756598e7e1ca5d1935e5e7a51ce731b9b4d1d1ad14d4f507357768ea"


def identity_digest(issues):
    rows = sorted((r["path"], r["line"], r["col"], r["score"], r["key"]) for r in issues)
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def files_digest(files):
    rows = sorted((r["path"], r["sha256"]) for r in files)
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def _load_fixture():
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh if line.strip()]
    meta = recs[0]
    assert meta["record"] == "meta"
    issues = [r for r in recs[1:] if r["record"] == "issue"]
    files = [r for r in recs[1:] if r["record"] == "file"]
    assert len(issues) + len(files) == len(recs) - 1
    return meta, issues, files


def _load_module(path, name):
    """Standard import semantics: registered in sys.modules while executing, so
    dataclasses / postponed annotations that look the module up work."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def _checker():
    return _load_module(CHECKER, "changed_functions_under_test")


def _outermost_functions(files, checker):
    """{(path, def line, name col): (qualname, node)} for every outermost function of every file."""
    found = {}
    for rec in files:
        units = checker.analyze_module(rec["text"])
        for unit in units:
            if not unit.outermost:
                continue
            node = unit.node
            col = node.col_offset + len("async def " if isinstance(node, ast.AsyncFunctionDef) else "def ")
            found[(rec["path"], node.lineno, col)] = (unit.qualname, node)
    return found


def test_fixture_is_complete_and_consistent():
    meta, issues, files = _load_fixture()
    assert meta["rule"] == "python:S3776"
    assert meta["project"] == "local:oig-proxy"
    assert meta["analysis_key"] == PINNED_ANALYSIS_KEY
    assert meta["source_sha"] == PINNED_SOURCE_SHA
    assert len(issues) == PINNED_ISSUE_COUNT == meta["total"] == meta["issue_count"]
    assert len(files) == PINNED_FILE_COUNT == meta["file_count"]
    assert identity_digest(issues) == PINNED_IDENTITY_DIGEST
    assert files_digest(files) == PINNED_FILES_DIGEST
    assert len({(r["path"], r["line"], r["col"]) for r in issues}) == len(issues)
    assert len({r["key"] for r in issues}) == len(issues)
    paths = {r["path"] for r in files}
    assert len(paths) == len(files)
    # the file records are exactly the .py files Sonar holds as main code (scope cross-check)
    assert sorted(paths) == sorted(p for p in meta["sonar_main_files"] if p.endswith(".py"))
    for r in issues:
        assert r["score"] > THRESHOLD, r
        assert r["score"] == r["flow_sum"], r  # Sonar's own +N increments add up
        assert r["path"] in paths, r
    for rec in files:
        assert hashlib.sha256(rec["text"].encode("utf-8")).hexdigest() == rec["sha256"], rec["path"]


def test_fixture_files_are_main_code_per_sonar_properties():
    _, _, files = _load_fixture()
    scope = _checker().load_scope(str(ROOT))
    assert [r["path"] for r in files if not scope.is_main(r["path"])] == []


def test_every_issue_maps_to_exactly_one_outermost_function():
    _, issues, files = _load_fixture()
    found = _outermost_functions(files, _checker())
    missing = [(r["path"], r["line"], r["col"]) for r in issues if (r["path"], r["line"], r["col"]) not in found]
    assert not missing, missing


def test_every_baseline_function_matches_sonar_exactly():
    _, issues, files = _load_fixture()
    cc = _checker().cognitive_complexity
    found = _outermost_functions(files, _checker())
    mismatches = []
    for r in issues:
        qual, node = found[(r["path"], r["line"], r["col"])]
        got = cc(node)
        assert isinstance(got, int) and not isinstance(got, bool), (r["path"], qual, got)
        if got != r["score"]:
            mismatches.append((r["path"], r["line"], qual, r["score"], got))
    if mismatches:
        by_delta = collections.Counter(got - want for *_, want, got in mismatches)
        lines = [f"{len(mismatches)} of {len(issues)} differ from Sonar; delta histogram {dict(sorted(by_delta.items()))}"]
        lines += [f"{p}:{ln} {q} sonar={want} local={got}" for p, ln, q, want, got in mismatches]
        raise AssertionError("\n".join(lines))


def test_no_sonar_flagged_function_scores_within_threshold():
    _, issues, files = _load_fixture()
    cc = _checker().cognitive_complexity
    found = _outermost_functions(files, _checker())
    low = []
    for r in issues:
        qual, node = found[(r["path"], r["line"], r["col"])]
        score = cc(node)
        if score <= THRESHOLD:
            low.append((r["path"], r["line"], qual, score))
    assert not low, "\n".join(f"{p}:{ln} {q} local={s}" for p, ln, q, s in low)


def test_no_unflagged_main_function_scores_above_threshold():
    """The check must never be stricter than Sonar: every main-code function Sonar did not flag
    at the baseline revision scores <= 15 locally."""
    _, issues, files = _load_fixture()
    checker = _checker()
    flagged = {(r["path"], r["line"], r["col"]) for r in issues}
    found = _outermost_functions(files, checker)
    assert flagged <= set(found)
    over = [
        (path, line, qual, checker.cognitive_complexity(node))
        for (path, line, col), (qual, node) in sorted(found.items())
        if (path, line, col) not in flagged and checker.cognitive_complexity(node) > THRESHOLD
    ]
    assert len(found) > len(flagged)
    assert not over, "\n".join(f"{p}:{ln} {q} local={s}" for p, ln, q, s in over)


def test_checker_loader_uses_standard_import_semantics(tmp_path):
    stub = tmp_path / "stub_checker.py"
    stub.write_text(
        "from __future__ import annotations\n"
        "import ast\n"
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass\n"
        "class Result:\n"
        "    value: int\n"
        "    node: ast.AST | None = None\n"
        "\n"
        "def cognitive_complexity(node):\n"
        "    return Result(7).value\n"
    )
    mod = _load_module(stub, "stub_checker_under_test")
    try:
        assert sys.modules["stub_checker_under_test"] is mod
        assert mod.cognitive_complexity(ast.parse("def f(): pass").body[0]) == 7
    finally:
        sys.modules.pop("stub_checker_under_test", None)


def test_integrity_pins_reject_tampering():
    _, issues, files = _load_fixture()
    assert identity_digest(issues) == PINNED_IDENTITY_DIGEST
    assert identity_digest(issues[:-1]) != PINNED_IDENTITY_DIGEST  # removed issue
    renamed = [dict(r) for r in issues]
    renamed[0]["path"] = "Bogus/" + renamed[0]["path"]
    assert identity_digest(renamed) != PINNED_IDENTITY_DIGEST
    moved = [dict(r) for r in issues]
    moved[0]["line"] += 1
    moved[0]["col"] += 1
    assert identity_digest(moved) != PINNED_IDENTITY_DIGEST
    assert files_digest(files[:-1]) != PINNED_FILES_DIGEST
