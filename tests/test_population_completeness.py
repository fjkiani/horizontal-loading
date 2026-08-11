"""T0b: did the generator rank the whole collection, or one page of it?

The defect this guards was live in production. gen_health issued a single
request at pageSize=200 and ranked the response. For the baked ALS seed that
happens to be the entire collection (51 studies) so the build pipeline never
saw it; the deployed API rotates a seed roster, and the multiple sclerosis
seed has 235 such studies, so the service ranked 200 of them -- 85.1% -- and
every one of the twelve gates then in the battery passed it.

The measured aftermath (healthforensic.json, full nextPageToken enumeration of
all six roster conditions) is that no answer actually moved. That is the whole
reason this file exists: a defect that does not change the answer this time is
still a defect, and "we got lucky" is not a property a gate can rely on.

Three states are asserted separately because collapsing "unproven" into either
"pass" or "fail" is how the original bug survived: a truncated page and an
unmeasured population are different claims and the battery must say which.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evaluate_traps as et


# ---------------------------------------------------------------- the states

def test_complete_enumeration_passes():
    ok, why = et.t0b_population_complete(
        {"n_base": 235, "n_true": 235, "pages_fetched": 2, "page_cap": 200})
    assert ok is True
    assert "235 of 235" in why


def test_truncated_page_fails():
    """The exact production shape: 200 served, 235 exist."""
    ok, why = et.t0b_population_complete(
        {"n_base": 200, "n_true": 235, "pages_fetched": 1, "page_cap": 200})
    assert ok is False
    assert "200 of 235" in why
    assert "85" in why           # the served fraction is stated, not just failed


def test_sitting_exactly_on_the_page_cap_with_no_total_fails():
    """No total to check against and n == cap is the truncation fingerprint."""
    ok, why = et.t0b_population_complete({"n_base": 200, "page_cap": 200})
    assert ok is False
    assert "cap" in why.lower()


def test_no_total_and_no_cap_breach_is_unproven_not_pass():
    ok, why = et.t0b_population_complete({"n_base": 51, "page_cap": 200})
    assert ok is None
    assert "unproven" in why.lower()


def test_bulk_download_passes_without_a_total():
    ok, why = et.t0b_population_complete(
        {"collection_is_bulk_download": True, "n_base": 171})
    assert ok is True
    assert "bulk" in why.lower()


def test_missing_base_size_is_unproven():
    ok, why = et.t0b_population_complete({})
    assert ok is None


def test_over_enumeration_still_passes():
    """n > n_true happens when a source revises its total mid-walk. Ranking
    MORE than the source claims exists is not a truncation."""
    ok, why = et.t0b_population_complete({"n_base": 236, "n_true": 235})
    assert ok is True


# ------------------------------------------------------- wired into the battery

def test_t0b_is_registered_in_the_battery():
    names = [n for n, _ in et.TESTS_EV]
    assert "T0b_population_complete" in names
    assert names.index("T0b_population_complete") == 1, (
        "T0b must run early: a truncated population invalidates every "
        "downstream rank statistic, so it should not be buried")


def test_a_truncated_trap_does_not_ship():
    """End to end through evaluate_one, not just the predicate."""
    rec = {"trap": {"category": "health and medicine",
                    "answer": "NCT01817166",
                    "ranking_evidence": {"n_base": 200, "n_true": 235,
                                         "page_cap": 200, "pages_fetched": 1}}}
    out = et.evaluate_one("health and medicine", rec)
    assert out["verdict"] != "ship"
    assert out["tests"]["T0b_population_complete"]["pass"] is False


# ------------------------------------------------- the generators record a total

@pytest.mark.parametrize("gen_module,fn_name", [
    ("category_traps", "gen_health"),
    ("category_traps", "gen_science"),
    ("gen_v3", "gen_sports"),
])
def test_generator_records_population_evidence(gen_module, fn_name):
    """Static check, no network: the generator must write n_true / page_cap /
    pages_fetched into LAST_RANK, otherwise T0b can only ever say 'unproven'.

    Run in a subprocess so the import-order requirement (ct -> v2 -> v3 -> v4)
    is honoured exactly as the build driver honours it.
    """
    src = (
        "import inspect, category_traps, gen_v2, gen_v3, gen_v4\n"
        f"import {gen_module} as m\n"
        f"s = inspect.getsource(m.{fn_name})\n"
        "missing = [k for k in ('n_true', 'page_cap', 'pages_fetched') "
        "if k not in s]\n"
        "print('MISSING=' + ','.join(missing))\n"
    )
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, cwd=os.path.dirname(
                           os.path.dirname(os.path.abspath(__file__))))
    assert r.returncode == 0, r.stderr[-2000:]
    assert "MISSING=" in r.stdout
    missing = r.stdout.split("MISSING=")[1].strip()
    assert missing == "", f"{fn_name} does not record {missing} in LAST_RANK"


def test_health_generator_refuses_rather_than_answering_from_a_prefix():
    """If the walk hits the page cap with a token still outstanding, the
    generator must raise, not rank what it has."""
    import inspect
    import category_traps as ct
    s = inspect.getsource(ct.gen_health)
    assert "nextPageToken" in s, "gen_health must follow the page token"
    assert "TrapUnavailable" in s, (
        "gen_health must refuse when enumeration is incomplete rather than "
        "ranking a prefix")


# ------------------------------------------------------------ measured record

def test_the_measured_forensic_result_is_on_disk():
    """The claim 'no answer moved under full enumeration' is a measurement,
    not an assertion. Skip if the artifact was not carried over."""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "healthforensic.json")
    if not os.path.exists(p):
        pytest.skip("healthforensic.json not present in this checkout")
    rows = json.load(open(p))
    rows = rows if isinstance(rows, list) else list(rows.values())[0]
    assert len(rows) >= 6
    changed = [r["condition"] for r in rows
               if r.get("answer_changes_with_full_enumeration")]
    assert changed == [], f"answer moved under full enumeration: {changed}"
    ms = [r for r in rows if r.get("condition") == "multiple sclerosis"]
    assert ms, "the seed that exposed the defect is missing from the forensic"
    assert ms[0]["n_page1"] == 200 and ms[0]["n_full"] == 235 == ms[0]["n_true"]
    assert ms[0]["k_robustness_page1"] == ms[0]["k_robustness_full"] == 26, (
        "the prefix and the full set must agree on k-robustness; if they "
        "diverge the 'answer did not move' claim is weaker than stated")
    # sickle cell ties at the extremum on both the prefix and the full set,
    # so _pick_extreme refuses it either way. Recorded so the refusal is not
    # later mistaken for a truncation artefact.
    sc = [r for r in rows if r.get("condition") == "sickle cell disease"]
    assert sc and sc[0]["page1_n_tied"] == sc[0]["full_n_tied"] == 2
