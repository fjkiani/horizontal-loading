"""The served pool is validated on WRITE, not on read.

A read-time check is too late: by the time it fires the trap has already been
served once. These tests pin the admission door shut against the four ways the
old pool could be corrupted -- a banned source, a missing witness, a
self-confirming witness, and an unattributable host -- and pin the two shapes
(scan trap, api-native trap) that must coexist in one file without colliding.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import source_gate as sg  # noqa: E402
import trap_generator as tg  # noqa: E402


def _api_trap(**over):
    t = {
        "track": "api-native",
        "category": "legal",
        "field": "United States Reports page",
        "answer": "768",
        "entity": "volume 504 of the United States Reports",
        "primary_operator": "Harvard Law School Library Innovation Lab",
        "sources": [
            "https://static.case.law/us/504/CasesMetadata.json",
            "https://www.courtlistener.com/api/rest/v4/search/?q=citation:(%22504%20U.S.%20768%22)",
            "https://www.law.cornell.edu/supremecourt/text/504/768",
        ],
        "confirming_sources": [
            "https://www.courtlistener.com/api/rest/v4/search/?q=citation:(%22504%20U.S.%20768%22)",
            "https://www.law.cornell.edu/supremecourt/text/504/768",
        ],
        "prompt": "x " * 90,
    }
    t.update(over)
    return t


@pytest.fixture(autouse=True)
def isolated_pool(tmp_path, monkeypatch):
    p = tmp_path / "pool.json"
    p.write_text("[]")
    monkeypatch.setattr(tg, "_POOL_PATH", str(p))
    yield p


def test_a_gate_valid_api_trap_is_admitted(isolated_pool):
    tg.admit_api_trap(_api_trap())
    assert len(json.load(open(isolated_pool))) == 1


def test_admission_is_idempotent(isolated_pool):
    tg.admit_api_trap(_api_trap())
    tg.admit_api_trap(_api_trap())
    assert len(json.load(open(isolated_pool))) == 1


def test_a_self_confirming_trap_is_refused(isolated_pool):
    t = _api_trap(confirming_sources=[
        "https://static.case.law/us/504/CasesMetadata.json"])
    with pytest.raises(tg.PoolRejected):
        tg.admit_api_trap(t)
    assert json.load(open(isolated_pool)) == []


def test_a_trap_with_no_asserted_witness_is_refused(isolated_pool):
    with pytest.raises(tg.PoolRejected):
        tg.admit_api_trap(_api_trap(confirming_sources=None))
    assert json.load(open(isolated_pool)) == []


def test_a_banned_source_is_refused_even_with_three_operators(isolated_pool):
    t = _api_trap(sources=_api_trap()["sources"] + [
        "https://chroniclingamerica.loc.gov/lccn/sn83030214/1900-01-01/ed-1/seq-1/ocr.txt"])
    with pytest.raises(tg.PoolRejected):
        tg.admit_api_trap(t)


def test_an_unattributable_host_is_refused(isolated_pool):
    t = _api_trap(sources=[
        "https://someone.github.io/data/vol504.json",
        "https://www.courtlistener.com/api/rest/v4/search/?q=citation:(%22504%20U.S.%20768%22)",
        "https://www.law.cornell.edu/supremecourt/text/504/768",
    ])
    with pytest.raises(tg.PoolRejected):
        tg.admit_api_trap(t)


def test_a_banned_prompt_reference_is_refused(isolated_pool):
    with pytest.raises(tg.PoolRejected):
        tg.admit_api_trap(_api_trap(
            prompt="Consult the Library of Congress holdings. " + "x " * 80))


def test_scan_and_api_keys_cannot_collide(isolated_pool):
    scan = {"lccn": "sn83030214", "date": "1900-01-01",
            "field": "United States Reports page", "sources": []}
    api = _api_trap()
    assert tg._key(scan) != tg._key(api)
    assert tg._key(api)[0] == "api-native"


def test_save_pool_drops_invalid_entries_and_reports_them(isolated_pool):
    good = _api_trap()
    bad = _api_trap(entity="other", confirming_sources=None)
    n, rejected = tg._save_pool([good, bad])
    assert n == 1
    assert len(rejected) == 1
    assert any("R3b" in m for m in rejected[0][1])
    assert len(json.load(open(isolated_pool))) == 1


def test_violation_messages_are_not_shredded_into_characters(isolated_pool):
    _, rejected = tg._save_pool([_api_trap(confirming_sources=None)])
    msgs = rejected[0][1]
    assert all(len(m) > 3 for m in msgs), msgs


def test_every_admitted_trap_carries_one_of_the_sixteen_categories(isolated_pool):
    tg.admit_api_trap(_api_trap())
    pool = json.load(open(isolated_pool))
    assert all(p["category"] in sg.CATEGORIES for p in pool)


# ------------------------------------------------- the shipped artifact itself
def _catalog():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "web", "public", "catalog.json")
    with open(p) as fh:
        return json.load(fh)


def test_the_baked_category_counts_match_the_baked_traps():
    """The catalog carries a `categories` summary AND the traps it summarises.

    Those drifted: a rebake replaced `traps` and carried `categories` over
    unchanged, so the shipped artifact advertised business, education, politics,
    sports and tv shows as stocked with one trap each after the Wikimedia ban
    had emptied them -- 14 traps mis-advertised in total. The SPA reads this
    block when the origin is asleep, so the stale summary was the reader's only
    view. Recomputing it is a one-line fix; keeping it from drifting again is
    this test.
    """
    doc = _catalog()
    counted = {}
    for t in doc["traps"]:
        counted[t["category"]] = counted.get(t["category"], 0) + 1
    for row in doc["categories"]:
        assert row["n_served"] == counted.get(row["category"], 0), (
            "categories block claims %d for %s, traps hold %d"
            % (row["n_served"], row["category"], counted.get(row["category"], 0)))
    assert sum(r["n_served"] for r in doc["categories"]) == len(doc["traps"])


def test_a_category_is_gold_only_if_every_trap_in_it_is_gold():
    """One silver trap in a pair must not let the category advertise gold."""
    doc = _catalog()
    tiers = {}
    for t in doc["traps"]:
        tiers.setdefault(t["category"], set()).add(t.get("witness_tier"))
    for row in doc["categories"]:
        seen = tiers.get(row["category"])
        if not seen:
            assert row["tier"] is None
        elif row["tier"] == "gold":
            assert seen == {"gold"}, (row["category"], seen)
