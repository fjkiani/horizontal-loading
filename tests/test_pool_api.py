"""The API must spend prompts, not re-serve them.

tests/test_pool_ledger.py proves the ledger's accounting in isolation. This file
proves the SERVICE is actually wired to it -- that the endpoint a caller hits
draws from stock, decrements, and refuses when the stock is gone. Those are
different claims: the ledger was correct and unreachable for one commit, which
is precisely the kind of gap a unit test cannot see.

Every test runs against a throwaway ledger seeded from the real baked catalog,
so the counts here are the counts the deployed service would report.
"""
import json
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from fastapi.testclient import TestClient  # noqa: E402

import pool_ledger as pl  # noqa: E402
import source_gate as sg  # noqa: E402
from app import main as m  # noqa: E402

client = TestClient(m.app)

CATALOG = os.path.join(_REPO, "web", "public", "catalog.json")


def _catalog():
    with open(CATALOG) as fh:
        return json.load(fh)["traps"]


def _stocked_categories():
    c = {}
    for t in _catalog():
        c[t["category"]] = c.get(t["category"], 0) + 1
    return c


@pytest.fixture(autouse=True)
def fresh_ledger(tmp_path, monkeypatch):
    """Point the service at an empty ledger and restock it from the catalog."""
    monkeypatch.setattr(pl, "LEDGER_PATH", str(tmp_path / "ledger.json"))
    m._seed_pool()
    yield


def _ask(cat, **kw):
    return client.post("/api/generate", json=dict({"category": cat}, **kw))


# ------------------------------------------------------------------ stocking
def test_startup_stocks_the_pool_from_the_catalog():
    js = client.get("/api/pool").json()
    assert js["n_available_total"] == len(_catalog())
    got = {c["category"]: c["n_available"] for c in js["categories"]
           if c["n_available"]}
    assert got == _stocked_categories()


def test_pool_declares_that_prompts_are_not_recycled():
    """The user's question was 'do they get replenished?'. The API must answer
    it in the payload, not leave it to be inferred from a count going down."""
    js = client.get("/api/pool").json()
    assert "not recycled" in js["replenishment"]
    assert js["low_water_mark"] == pl.LOW_WATER
    assert js["reissue_seconds"] == pl.REISSUE_SECONDS


# -------------------------------------------------------------------- serving
def test_a_served_prompt_carries_the_baked_trap():
    cat = max(_stocked_categories(), key=lambda c: _stocked_categories()[c])
    r = _ask(cat)
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["source"] == "pool" and js["status"] == "served"
    res = js["result"]
    assert res["prompt"] and res["answer"] and res["category"] == cat
    assert res["verdict"] == "ship"


def test_serving_decrements_availability():
    cat = "science and technology"
    before = _avail(cat)
    _ask(cat)
    assert _avail(cat) == before - 1


def _avail(cat):
    js = client.get("/api/pool").json()
    return next(c["n_available"] for c in js["categories"] if c["category"] == cat)


def test_two_calls_never_return_the_same_prompt():
    cat = "science and technology"
    seen = {_ask(cat).json()["trap_id"] for _ in range(_stocked_categories()[cat])}
    assert len(seen) == _stocked_categories()[cat]


# ----------------------------------------------------------------- exhaustion
def test_draining_a_category_refuses_with_409():
    cat = "science and technology"
    n = _stocked_categories()[cat]
    for _ in range(n):
        assert _ask(cat).status_code == 200
    r = _ask(cat)
    assert r.status_code == 409
    js = r.json()
    assert js["status"] == "exhausted"
    assert js["category"] == cat
    assert js["n_available"] == 0
    # All n are still inside their reissue window, so they read as served, not
    # burned. Both counts are reported so the difference is visible.
    assert js["n_served"] + js["n_burned"] == n


def test_the_reported_remainder_never_goes_negative():
    """Regression: serve() double-counted the record it had just handed out and
    reported remaining=-1 on the final serve."""
    cat = "science and technology"
    n = _stocked_categories()[cat]
    seen = [_ask(cat).json()["n_available"] for _ in range(n)]
    assert seen == list(range(n - 1, -1, -1))


def test_an_exhausted_category_is_never_silently_reissued():
    cat = "health and medicine"
    first = _ask(cat).json()["trap_id"]
    r = _ask(cat)
    assert r.status_code == 409
    assert "trap_id" not in r.json()
    assert first  # the one prompt that existed was spent, not recycled


# -------------------------------------------------------------- reissue window
def test_the_same_request_key_returns_the_same_prompt():
    cat = "travel"
    a = _ask(cat, request_key="retry-1").json()
    before = _avail(cat)
    b = _ask(cat, request_key="retry-1").json()
    assert b["trap_id"] == a["trap_id"]
    assert b["reissued"] is True
    assert _avail(cat) == before, "a retry must not cost a prompt"


def test_a_different_request_key_gets_a_different_prompt():
    cat = "travel"
    a = _ask(cat, request_key="client-a").json()
    b = _ask(cat, request_key="client-b").json()
    assert a["trap_id"] != b["trap_id"]


# ---------------------------------------------------------------- categories
def test_categories_distinguishes_exhausted_from_unstocked():
    """Two different failures with two different remedies. Collapsing them into
    one 'dead' state is what the old endpoint did."""
    cat = "health and medicine"
    _ask(cat)
    rows = {r["category"]: r for r in client.get("/api/categories").json()["categories"]}
    assert rows[cat]["exhausted"] is True and rows[cat]["unstocked"] is False
    assert rows["art"]["unstocked"] is True and rows["art"]["exhausted"] is False
    assert rows[cat]["n_served"] == 0, "legacy key must mean 'servable now'"


def test_every_taxonomy_category_is_reported():
    rows = client.get("/api/categories").json()["categories"]
    assert [r["category"] for r in rows] == list(sg.CATEGORIES)


# ------------------------------------------------------------------ restocking
def test_restocking_cannot_un_spend_a_prompt():
    """A redeploy re-seeds from the catalog. It must not resurrect what was
    spent, or the service would hand the same answer out again after every push."""
    cat = "science and technology"
    spent = _ask(cat).json()["trap_id"]
    before = _avail(cat)
    m._seed_pool()
    assert _avail(cat) == before
    st = pl.load()
    assert st["records"][spent]["status"] in ("served", "burned")


# ------------------------------------------------------------------ provenance
def test_no_served_prompt_cites_a_banned_source():
    """The ban is only real if the thing the API actually hands out obeys it."""
    for cat, n in _stocked_categories().items():
        for _ in range(n):
            r = _ask(cat)
            assert r.status_code == 200, r.text
            srcs = r.json()["result"].get("sources") or []
            assert sg.banned_violations(srcs) == [], (cat, srcs)
