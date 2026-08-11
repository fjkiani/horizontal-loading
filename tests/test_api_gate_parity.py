"""Pin the three defects a live smoke test of the deployed API exposed.

After commit 316ac4a the service passed pytest, passed the build gate, and
still shipped wrong behaviour on three fronts. All three share one shape: the
DEPLOYED path and the BUILD path had drifted apart, and only the build path was
under test.

D1  Withdrawn generators refused only for seeds their post-withdrawal signature
    happened to bind. `POST {"category":"finance"}` hit roster seed
    {"year": 2010} and returned
        TypeError: gen_finance() got an unexpected keyword argument 'year'
    as job status "error". Art, whose roster seeds still bound, refused cleanly.
    The withdrawal must be a property of the CATEGORY, not of the arguments.

D2  The API gate was sg.validate_trap() alone -- source independence and word
    count. The build gate is twelve tests. So the service served, as "done", a
    health trap (NCT04300920, n_base 30) that had never been through the depth
    or witness measurement qualifying the catalog's NCT05178810 (n_base 51).

D3  category_traps.LAST_RANK is a module global that _pick_extreme rebinds and
    to_trap() reads AFTER the generator's witness round trips. The build driver
    is sequential; the API spawns a daemon thread per request and held no lock.
    Two interleaved generations cross-contaminated ranking evidence in 200 of
    200 trials -- and every one of the twelve tests reads that evidence, so an
    unlocked API would score the wrong population and call the verdict measured.

These tests are offline. D1 and D3 need no network by construction, and D2 is
checked against a synthetic trap rather than a live generation.
"""
from __future__ import annotations

import inspect
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import category_traps as ct
import gen_v2  # noqa: F401  import order is load-bearing: v2, then v3, then v4
import gen_v3  # noqa: F401
import gen_v4  # noqa: F401
import evaluate_traps as et
import seed_roster


WITHDRAWN = ["art", "celebrities/public figures", "finance", "history",
             "video games"]


# --------------------------------------------------------------------------
# D1 -- every roster seed must reach the generator body
# --------------------------------------------------------------------------

def _all_seeds():
    for cat in sorted(ct.GENERATORS):
        for i, seed in enumerate(seed_roster.seeds_for(cat)):
            yield cat, i, seed


@pytest.mark.parametrize("cat,idx,seed", list(_all_seeds()),
                         ids=lambda v: str(v)[:24])
def test_every_roster_seed_binds(cat, idx, seed):
    """A seed the generator cannot bind becomes an opaque TypeError in prod.

    The roster is rotated by /api/generate with no per-call validation, so a
    signature change silently converts a whole category into 500-class errors.
    Binding is checked here, at build time, where it is cheap.
    """
    fn = ct.GENERATORS[cat]
    sig = inspect.signature(fn)
    accepts_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD
                         for p in sig.parameters.values())
    unknown = sorted(set(seed) - set(sig.parameters))
    assert accepts_var_kw or not unknown, (
        "%s roster seed[%d]=%r passes %s, which %s.%s does not accept (%s)"
        % (cat, idx, seed, unknown, fn.__module__, fn.__name__,
           sorted(sig.parameters)))
    sig.bind_partial(**seed)


@pytest.mark.parametrize("cat", WITHDRAWN)
def test_withdrawn_generator_refuses_under_every_seed(cat):
    """Withdrawal is unconditional. Every roster seed must raise
    TrapUnavailable -- never TypeError, which the API reports as `error` and a
    reader cannot distinguish from a crash."""
    fn = ct.GENERATORS[cat]
    for i, seed in enumerate(seed_roster.seeds_for(cat)):
        with pytest.raises(ct.TrapUnavailable):
            fn(**seed)
    # and under a seed nobody wrote, which is the case the roster cannot cover
    with pytest.raises(ct.TrapUnavailable):
        fn(**{"a_parameter_that_never_existed": 1})


@pytest.mark.parametrize("cat", WITHDRAWN)
def test_withdrawn_generator_states_a_measured_reason(cat):
    """A refusal with no reason is indistinguishable from an outage."""
    fn = ct.GENERATORS[cat]
    with pytest.raises(ct.TrapUnavailable) as ei:
        fn()
    msg = str(ei.value)
    assert len(msg) >= 80, "%s withdrawal message is too thin: %r" % (cat, msg)
    # the message must name the category it is withdrawing, so a refusal read in
    # isolation is self-describing
    stem = cat.split("/")[0].split()[0].lower()
    assert stem in msg.lower(), \
        "%s withdrawal message does not name the category: %r" % (cat, msg[:120])


# --------------------------------------------------------------------------
# D2 -- the deployed gate must be the build gate
# --------------------------------------------------------------------------

def test_api_runs_the_full_battery_not_just_the_source_gate():
    """The API generation path must reference evaluate_one, not only
    sg.validate_trap. Checked structurally AND behaviourally below."""
    from app import main as api
    src = inspect.getsource(api._run_category_generate)
    assert "et.evaluate_one" in src, (
        "the deployed generation path does not run the trap battery; it would "
        "ship traps the build pipeline holds")


def _synthetic_trap(**over):
    """A trap dict shaped like Candidate.to_trap() output."""
    t = {
        "collection_is_explicit": False,
        "primary_operator": "arXiv",
        "independent_confirming_operators": ["Crossref", "OurResearch (OpenAlex)"],
        "category": "science and technology", "field": "arxiv_id",
        "answer": "2403.07505", "entity": "a paper", "n_base": 15,
        "sources": ["arxiv.org"], "confirming_sources": ["api.crossref.org"],
        "api_proof": True, "api_proof_argument": "x", "confirmation": "y",
        "prompt": "p " * 90, "source_operators": ["arXiv"],
        "confirming_operators": ["Crossref"], "track": "api-native",
        "ranking_evidence": {}, "facts": {},
    }
    t.update(over)
    return t


def test_battery_holds_a_trap_whose_answer_is_first_returned():
    """The exact class the API used to serve: a winner sitting at index 0 of the
    returned order is one read away, and T3 must catch it."""
    ev = {"n_ranked": 40, "n_base": 40, "winner_position_in_api_order": 0,
          "winner_is_first_returned": True, "winner_is_last_returned": False,
          "distinct_keys": 40, "n_tied_at_extremum": 1,
          "spearman_key_vs_api_order": 0.05, "top_keys": ["9", "8"],
          "p_answer_by_uniform_guess": 0.025}
    res = et.evaluate_one("science and technology",
                          {"trap": _synthetic_trap(ranking_evidence=ev)})
    assert res["tests"]["T3_order_leak"]["pass"] is False
    assert res["verdict"] != "ship"


def test_battery_marks_unmeasured_evidence_unproven_not_shipped():
    """An EMPTY ranking_evidence -- which is what a generator that never called
    _pick_extreme produces -- must not read as a pass. Silence is not evidence."""
    res = et.evaluate_one("science and technology",
                          {"trap": _synthetic_trap(ranking_evidence={})})
    assert res["verdict"] != "ship", res["tests"]
    unproven = [n for n, r in res["tests"].items() if r["pass"] is None]
    assert unproven, "no test reported unproven on empty evidence"


# --------------------------------------------------------------------------
# D3 -- ranking evidence must not cross between concurrent generations
# --------------------------------------------------------------------------

def _rank_then_emit(rows, tag, delay, out, idx, lock):
    def body():
        best = ct._pick_extreme(rows, keyfn=lambda r: r["k"], label="k", mode="max")
        time.sleep(delay)
        cand = ct.Candidate(
            category="science and technology", field="f", answer=str(best["id"]),
            entity=tag, n_base=len(rows), sources=["arxiv.org"],
            confirming_sources=["api.crossref.org"], api_proof_argument="x",
            confirmation="y", prompt="p")
        out[idx] = (len(rows), cand.to_trap()["ranking_evidence"].get("n_base"))
    if lock:
        with ct.generation():
            body()
    else:
        body()


def _contamination(locked, trials=40, delay=0.004):
    bad = 0
    for _ in range(trials):
        out = {}
        a = [{"id": i, "k": i} for i in range(1, 41)]
        b = [{"id": 1000 + i, "k": i * 7} for i in range(1, 18)]
        ta = threading.Thread(target=_rank_then_emit,
                              args=(a, "A", delay, out, 0, locked))
        tb = threading.Thread(target=_rank_then_emit,
                              args=(b, "B", delay, out, 1, locked))
        ta.start(); time.sleep(delay / 4.0); tb.start(); ta.join(); tb.join()
        if any(own != seen for own, seen in out.values()):
            bad += 1
    return bad


def test_unlocked_generation_really_does_cross_contaminate():
    """Guard the guard: if this ever stops failing, the lock test below has
    stopped proving anything and the fix has become untestable."""
    assert _contamination(locked=False) > 0, (
        "the race no longer reproduces -- re-derive the mechanism before "
        "trusting the locked case")


def test_generation_lock_stops_the_cross_contamination():
    assert _contamination(locked=True) == 0
