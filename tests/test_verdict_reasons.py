"""A hold must record WHY it is held.

Instrument defect #17. `expand_seeds.py` extracted the failing tests with

    sorted(k for k, v in ev["tests"].items() if not v)

and `evaluate_one` sets `tests[name] = {"pass": bool|None, "detail": str}` --
a non-empty dict, which is always truthy. So the predicate never fired, all 81
seeds in the post-ban sweep recorded `failing_tests == []`, and every one of the
24 holds looked reason-less. The verdict itself was unaffected (it is read off
`ev["verdict"]`), but the diagnosis built on the empty field was wrong: it said
17 prompts were one confirming operator short when the recomputed evidence says
4 are.

These tests pin the extraction so the same class of bug cannot come back
silently, and pin the property that made it detectable -- a hold has at least
one test that actually failed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import evaluate_traps as et  # noqa: E402


def _failing(ev):
    """The corrected extraction, mirroring expand_seeds.py."""
    return sorted(k for k, v in (ev.get("tests") or {}).items()
                  if isinstance(v, dict) and v.get("pass") is False)


def _unproven(ev):
    return sorted(k for k, v in (ev.get("tests") or {}).items()
                  if isinstance(v, dict) and v.get("pass") is None)


def test_the_old_predicate_was_vacuous():
    """`not v` on a populated dict is always False. Guard the regression."""
    tests = {"T5_confirmation": {"pass": False, "detail": "no witness"},
             "T6_gate": {"pass": False, "detail": "banned source"},
             "T0b": {"pass": None, "detail": "unproven"},
             "T7": {"pass": True, "detail": "clean"}}
    assert [k for k, v in tests.items() if not v] == [], \
        "if this ever finds something the bug's premise changed; re-read the extraction"
    assert _failing({"tests": tests}) == ["T5_confirmation", "T6_gate"]
    assert _unproven({"tests": tests}) == ["T0b"]


def test_a_held_trap_names_at_least_one_failed_test():
    """`hold` is only ever set by a test returning False, so the list cannot be empty."""
    trap = {"category": "business", "field": "LEI", "answer": "X",
            "prompt": "which company", "primary_operator": "US SEC",
            "sources": ["https://www.sec.gov/x"], "confirming_sources": [],
            "ranking_evidence": {}}
    ev = et.evaluate_one("business", {"status": "ok", "trap": trap})
    assert ev["verdict"] == "hold"
    assert _failing(ev), "a hold with no named failing test means the extraction is broken again"


def test_unproven_is_not_reported_as_failed():
    """A test that could not be evaluated is not a measured failure.

    Conflating them is what turns 'we did not check' into 'it failed', which is
    the reporting error this whole file exists to prevent.
    """
    tests = {"A": {"pass": None, "detail": "no evidence"}}
    assert _failing({"tests": tests}) == []
    assert _unproven({"tests": tests}) == ["A"]


@pytest.mark.parametrize("shape", [{"pass": False}, {"pass": False, "detail": ""}])
def test_extraction_survives_a_missing_detail(shape):
    assert _failing({"tests": {"T": shape}}) == ["T"]
