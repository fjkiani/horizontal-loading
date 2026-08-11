"""The ledger must not be able to serve a prompt twice.

Acceptance criterion 5 of the approved plan: drain a category to HTTP 409, and
show that a repeated request_key inside the reissue window returns the SAME
prompt while a new key does not.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pool_ledger as pl  # noqa: E402


def _traps(n, cat="science and technology"):
    return [{"category": cat, "field": "arXiv identifier", "answer": f"24{i:04d}",
             "entity": f"paper {i}", "verdict": "ship", "witness_tier": "gold"}
            for i in range(n)]


@pytest.fixture()
def led(tmp_path):
    return str(tmp_path / "ledger.json")


def test_identity_is_the_answer_not_the_seed(led):
    """Two seeds converging on one answer are ONE prompt.

    This is not hypothetical: in the post-ban sweep the travel LH/FRA and
    TP/LIS seeds both resolved to GeoNames 6301511.
    """
    a = {"category": "travel", "field": "GeoNames identifier", "answer": "6301511",
         "seed_repr": '{"airline_iata": "LH"}'}
    b = dict(a, seed_repr='{"airline_iata": "TP"}')
    r = pl.upsert([a, b], path=led)
    assert r["n_total"] == 1, "distinct seeds, same answer, must collapse to one prompt"


def test_serving_burns_and_drains_to_exhaustion(led):
    pl.upsert(_traps(3), path=led)
    seen = []
    for i in range(3):
        rec, meta = pl.serve("science and technology", f"key-{i}", path=led)
        assert rec is not None
        seen.append(rec["answer"])
    assert len(set(seen)) == 3, "each serve must hand out a DIFFERENT prompt"

    rec, meta = pl.serve("science and technology", "key-x", path=led)
    assert rec is None, "a drained category must refuse, not recycle"
    assert meta["n_available"] == 0
    assert meta["n_served"] + meta["n_burned"] == 3


def test_reissue_window_returns_the_same_prompt_but_only_for_that_key(led):
    pl.upsert(_traps(2), path=led)
    first, _ = pl.serve("science and technology", "req-A", path=led)
    again, meta = pl.serve("science and technology", "req-A", path=led)
    assert meta["reissued"] is True
    assert again["trap_id"] == first["trap_id"], "a retry must not cost a prompt"

    other, meta2 = pl.serve("science and technology", "req-B", path=led)
    assert meta2["reissued"] is False
    assert other["trap_id"] != first["trap_id"], "a new key must get a new prompt"


def test_window_expiry_burns_and_blocks_reissue(led):
    pl.upsert(_traps(1), path=led)
    t0 = 1_000_000.0
    rec, _ = pl.serve("science and technology", "req-A", path=led, now=t0)
    assert rec is not None
    later = t0 + pl.REISSUE_SECONDS + 1
    rec2, meta = pl.serve("science and technology", "req-A", path=led, now=later)
    assert rec2 is None, "the window has closed; the prompt is spent"
    assert meta["n_burned"] == 1


def test_the_window_closes_exactly_at_the_boundary_not_after_it(led):
    """`_expire` uses `<= now`, so T+W burns and T+W-1 does not.

    The live smoke could not see this: it finished inside the window, so every
    spent prompt was still `served` and `n_burned` read 0 across the board. The
    boundary is only observable on an injected clock.
    """
    pl.upsert(_traps(3), path=led)
    T, W = 1_800_000_000.0, pl.REISSUE_SECONDS
    for i in range(3):
        assert pl.serve("science and technology", f"k{i}", path=led, now=T)[0] is not None

    just_inside = pl.status(path=led, now=T + W - 1)["categories"][0]
    assert (just_inside["n_served"], just_inside["n_burned"]) == (3, 0), \
        "one second before the window closes the prompts are still reissuable"
    at_boundary = pl.status(path=led, now=T + W)["categories"][0]
    assert (at_boundary["n_served"], at_boundary["n_burned"]) == (0, 3), \
        "the window must close AT T+W, not after it"


def test_expiry_never_returns_a_prompt_to_available(led):
    """Closing a reissue window must burn, never restore.

    If an expired reservation fell back to `available` the ledger would re-serve
    a prompt a solver has already seen, which is the single failure the whole
    design exists to prevent. Availability must be flat across the boundary
    while served -> burned moves one-for-one.
    """
    pl.upsert(_traps(5), path=led)
    T, W = 1_800_000_000.0, pl.REISSUE_SECONDS
    for i in range(2):
        pl.serve("science and technology", f"k{i}", path=led, now=T)

    seen = []
    for now in (T, T + W - 1, T + W, T + W + 1, T + 10 * W):
        row = pl.status(path=led, now=now)["categories"][0]
        seen.append((row["n_available"], row["n_served"], row["n_burned"], row["n_total"]))

    avail = [s[0] for s in seen]
    assert avail == [3] * 5, f"expiry moved availability: {avail}"
    burned = [s[2] for s in seen]
    assert burned == sorted(burned), f"a burned prompt un-burned: {burned}"
    assert burned[-1] == 2 and seen[-1][1] == 0, "both reservations must end burned"
    for a, s, b, n in seen:
        assert a + s + b == n, f"records lost or double-counted: {a}+{s}+{b} != {n}"


def test_upsert_cannot_resurrect_a_spent_prompt(led):
    """Re-running the sweep must not silently un-spend anything."""
    pl.upsert(_traps(1), path=led)
    pl.serve("science and technology", "k", path=led, now=1000.0)
    pl.status(path=led, now=1000.0 + pl.REISSUE_SECONDS + 1)  # force the burn
    pl.upsert(_traps(1), path=led)                            # identical trap
    st = pl.status(path=led)
    row = [c for c in st["categories"] if c["category"] == "science and technology"][0]
    assert row["n_available"] == 0, "re-upserting a burned trap must not revive it"
    assert row["n_burned"] == 1


def test_retired_is_not_the_same_as_burned(led):
    """Withdrawal by policy and consumption by service are different events."""
    pl.upsert(_traps(2), path=led)
    st = pl.load(led)
    tid = sorted(st["records"])[0]
    pl.retire([tid], "source banned: Wikimedia", path=led)
    row = [c for c in pl.status(path=led)["categories"]][0]
    assert row["n_retired"] == 1
    assert row["n_burned"] == 0, "retiring must not be counted as spending"
    assert row["n_available"] == 1


def test_low_water_trips_before_exhaustion(led):
    pl.upsert(_traps(5), path=led)
    st = pl.status(path=led)
    row = st["categories"][0]
    assert row["low_water"] is False
    for i in range(3):
        pl.serve("science and technology", f"k{i}", path=led)
    st2 = pl.status(path=led)
    row2 = st2["categories"][0]
    assert row2["n_available"] == 2
    assert row2["low_water"] is True, "the warning must precede the 409"
    assert row2["exhausted"] is False


def test_reported_remainder_never_goes_negative(led):
    """Regression: serve() subtracted 1 from a count already excluding the
    prompt it had just marked SERVED, so the last serve reported -1 left."""
    pl.upsert(_traps(4), path=led)
    seen = []
    for i in range(4):
        rec, meta = pl.serve("science and technology", f"k{i}", path=led)
        assert rec is not None
        seen.append(meta["n_available"])
    assert seen == [3, 2, 1, 0], f"remainder must count down to zero, got {seen}"


# ----------------------------------------------------- booking a minted prompt
def test_book_minted_spends_the_named_prompt_not_the_oldest(led):
    """A trap generated on demand has ALREADY been disclosed to the caller.

    serve() hands out the oldest available record, which for a mint would burn
    a prompt nobody read while leaving the one that WAS read available to be
    served again later. book_minted spends the named id instead.
    """
    pl.upsert(_traps(3), path=led)
    tid = pl.trap_id("science and technology", "arXiv identifier", "240002")
    rec, meta = pl.book_minted(tid, "mint-1", path=led)
    assert rec["trap_id"] == tid and meta["status"] == "served"
    st = pl.load(led)
    oldest = pl.trap_id("science and technology", "arXiv identifier", "240000")
    assert st["records"][oldest]["status"] == "available"
    assert meta["n_available"] == 2


def test_book_minted_reports_a_re_minted_answer_instead_of_double_counting(led):
    """Re-minting an answer that was already spent is a real event -- the
    generator converged on a used answer -- and must not be booked twice."""
    pl.upsert(_traps(1), path=led)
    tid = pl.trap_id("science and technology", "arXiv identifier", "240000")
    pl.book_minted(tid, "mint-1", path=led)
    st = pl.load(led)
    st["records"][tid]["status"] = "burned"
    pl.save(st, led)
    rec, meta = pl.book_minted(tid, "mint-2", path=led)
    assert meta["already_spent"] is True
    assert rec["status"] == "burned"
    assert rec["n_serves"] == 1, "a re-mint must not increment the spend count"


def test_book_minted_on_an_unknown_id_is_not_an_error(led):
    rec, meta = pl.book_minted("deadbeefdeadbeef", "mint-1", path=led)
    assert rec is None and meta["known"] is False
