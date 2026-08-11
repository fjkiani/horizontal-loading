#!/usr/bin/env python3
"""pool_ledger -- consumption accounting for the trap pool.

WHY THIS EXISTS
---------------
Nothing tracked whether a prompt had been used. `/api/categories` reported
`n_served` as simply the number of traps baked into the catalog for that
category -- a constant. It did not decrease when a prompt was handed out, so a
caller could be served the same prompt indefinitely and the API could not tell
anyone the pool was empty.

That is a correctness problem, not a cosmetic one. A benchmark prompt is
single-use by construction: once a solver has seen it, its answer is in that
solver's context, and re-serving it measures recall rather than the capability
the trap was built to probe. A pool of N prompts supports N measurements.

MODEL
-----
One record per DISTINCT trap, keyed by

    trap_id = sha256("category|field|answer")[:16]

Identity is the ANSWER, not the seed. Different seeds routinely converge on one
answer -- travel's measured distinct_answer_rate across its six-seed roster is
0.8, and in the post-ban sweep the LH/FRA and TP/LIS seeds both resolved to
GeoNames 6301511 -- so two seeds yielding one answer are one prompt, not two.

STATUS LIFECYCLE

    available --serve--> served --(reissue window closes)--> burned
                              \\--(same request_key, in window)--> served

    available/served/burned --retire--> retired

`burned` and `retired` are deliberately distinct. Burned means CONSUMED: served,
window closed, never servable again. Retired means WITHDRAWN BY POLICY: pulled
because it was found defective or its source was banned, independent of whether
anyone ever saw it. Collapsing them would make "how many prompts did we spend?"
unanswerable, which is the question this ledger exists to answer.

REISSUE WINDOW
--------------
A serve burns the prompt, but not instantly. A client that retries after a
dropped connection must get the SAME prompt back, or a network blip silently
costs a prompt. A serve records a `request_key`, and for REISSUE_SECONDS any
repeat of that key returns the identical record. A DIFFERENT key gets a
different prompt. That window is the only reuse permitted.

REPLENISHMENT
-------------
There is no automatic replenishment and the ledger does not pretend otherwise.
Prompts come from running generators against the seed roster; when a category
is exhausted the honest response is HTTP 409, not a recycled prompt.
`low_water` exists to signal the need for a sweep BEFORE that happens.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time

LEDGER_PATH = os.environ.get("SEAL_POOL_LEDGER", "pool_ledger.json")
REISSUE_SECONDS = int(os.environ.get("SEAL_REISSUE_SECONDS", "600"))
LOW_WATER = int(os.environ.get("SEAL_LOW_WATER", "2"))

AVAILABLE, SERVED, BURNED, RETIRED = "available", "served", "burned", "retired"
_LOCK = threading.RLock()


def trap_id(category, field, answer):
    return hashlib.sha256(
        f"{category}|{field}|{answer}".encode()).hexdigest()[:16]


def _now():
    return time.time()


def _iso(ts):
    return None if ts is None else time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# --------------------------------------------------------------- persistence
def load(path=None):
    p = path or LEDGER_PATH
    if not os.path.exists(p):
        return {"version": 1, "records": {}}
    with open(p) as fh:
        d = json.load(fh)
    d.setdefault("records", {})
    return d


def save(state, path=None):
    """Atomic. A torn ledger would lose the record of what was spent."""
    p = path or LEDGER_PATH
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, p)


# ------------------------------------------------------------------ mutation
def upsert(traps, path=None):
    """Add traps to the pool. Never resurrects a burned or retired record.

    Re-running a sweep must not silently un-spend a prompt, so an existing
    record's status is left exactly as it is; only metadata is refreshed.
    """
    with _LOCK:
        st = load(path)
        added, refreshed = [], []
        for t in traps:
            tid = trap_id(t.get("category"), t.get("field"), str(t.get("answer")))
            rec = st["records"].get(tid)
            meta = {
                "trap_id": tid,
                "category": t.get("category"),
                "field": t.get("field"),
                "answer": str(t.get("answer")),
                "entity": t.get("entity"),
                "seed_repr": t.get("seed_repr"),
                "verdict": t.get("verdict"),
                "witness_tier": t.get("witness_tier"),
                "primary_operator": t.get("primary_operator"),
            }
            if rec is None:
                meta.update({"status": AVAILABLE, "served_at": None,
                             "burned_at": None, "retired_at": None,
                             "request_key": None, "reissue_expires_at": None,
                             "n_serves": 0, "added_at": _now()})
                st["records"][tid] = meta
                added.append(tid)
            else:
                rec.update(meta)  # status intentionally untouched
                refreshed.append(tid)
        save(st, path)
        return {"added": added, "refreshed": refreshed,
                "n_total": len(st["records"])}


def _expire(st, now=None):
    """Close reissue windows. A served record past its window is burned."""
    now = now or _now()
    n = 0
    for r in st["records"].values():
        if r["status"] == SERVED and (r.get("reissue_expires_at") or 0) <= now:
            r["status"] = BURNED
            r["burned_at"] = r.get("burned_at") or now
            n += 1
    return n


def serve(category, request_key, path=None, now=None):
    """Hand out one prompt and burn it.

    Returns (record, meta). record is None when the category is exhausted;
    meta then carries the counts the HTTP 409 body reports.
    """
    now = now or _now()
    with _LOCK:
        st = load(path)
        _expire(st, now)
        recs = [r for r in st["records"].values() if r["category"] == category]

        # Reissue: the same key inside the window gets the identical prompt.
        if request_key:
            for r in recs:
                if (r.get("request_key") == request_key
                        and r["status"] == SERVED
                        and (r.get("reissue_expires_at") or 0) > now):
                    save(st, path)
                    return r, {"reissued": True,
                               "n_available": sum(1 for x in recs
                                                  if x["status"] == AVAILABLE)}

        avail = sorted([r for r in recs if r["status"] == AVAILABLE],
                       key=lambda r: (r.get("added_at") or 0, r["trap_id"]))
        if not avail:
            save(st, path)
            return None, {
                "reissued": False,
                "n_available": 0,
                "n_burned": sum(1 for r in recs if r["status"] == BURNED),
                "n_served": sum(1 for r in recs if r["status"] == SERVED),
                "n_retired": sum(1 for r in recs if r["status"] == RETIRED),
                "n_total": len(recs),
            }
        rec = avail[0]
        rec["status"] = SERVED
        rec["served_at"] = now
        rec["request_key"] = request_key
        rec["reissue_expires_at"] = now + REISSUE_SECONDS
        rec["n_serves"] = int(rec.get("n_serves") or 0) + 1
        save(st, path)
        # NB no "- 1" here. `recs` holds references into st["records"], and
        # rec["status"] was set to SERVED above, so the comprehension already
        # excludes the prompt just handed out. Subtracting again drove the
        # reported remainder to -1 on the final serve.
        return rec, {"reissued": False,
                     "n_available": sum(1 for r in recs
                                        if r["status"] == AVAILABLE)}


def book_minted(tid, request_key, path=None, now=None):
    """Book ONE named prompt as consumed, for a trap minted on demand.

    `serve()` picks the oldest available record for a category, which is wrong
    for a mint: the caller has seen the prompt that was just generated, not the
    oldest one in stock, and burning the oldest would spend a prompt nobody
    read while leaving the disclosed one available to be served again.

    An already-burned id is reported, not resurrected and not re-burned:
    re-minting an answer that was previously spent is a real event (the
    generator converged on a used answer), and hiding it would make the spend
    count wrong in the other direction.
    """
    now = now or _now()
    with _LOCK:
        st = load(path)
        _expire(st, now)
        rec = st["records"].get(tid)
        if rec is None:
            save(st, path)
            return None, {"known": False, "already_spent": False}
        already = rec["status"] in (BURNED, RETIRED)
        if not already:
            rec["status"] = SERVED
            rec["served_at"] = now
            rec["request_key"] = request_key
            rec["reissue_expires_at"] = now + REISSUE_SECONDS
            rec["n_serves"] = int(rec.get("n_serves") or 0) + 1
        save(st, path)
        cat = rec["category"]
        return rec, {
            "known": True,
            "already_spent": already,
            "status": rec["status"],
            "n_available": sum(1 for r in st["records"].values()
                               if r["category"] == cat and r["status"] == AVAILABLE),
        }


def retire(trap_ids, reason, path=None):
    """Withdraw prompts by policy. Distinct from burning."""
    now = _now()
    with _LOCK:
        st = load(path)
        hit = []
        for tid in trap_ids:
            r = st["records"].get(tid)
            if r is None:
                continue
            r["status"] = RETIRED
            r["retired_at"] = now
            r["retired_reason"] = reason
            hit.append(tid)
        save(st, path)
        return hit


# ------------------------------------------------------------------ reporting
def status(path=None, categories=None, now=None):
    """Per-category consumption. This is what GET /api/pool returns."""
    now = now or _now()
    with _LOCK:
        st = load(path)
        _expire(st, now)
        save(st, path)
        recs = list(st["records"].values())
    cats = sorted({r["category"] for r in recs} | set(categories or []))
    out = []
    for c in cats:
        rs = [r for r in recs if r["category"] == c]
        n_av = sum(1 for r in rs if r["status"] == AVAILABLE)
        out.append({
            "category": c,
            "n_total": len(rs),
            "n_available": n_av,
            "n_served": sum(1 for r in rs if r["status"] == SERVED),
            "n_burned": sum(1 for r in rs if r["status"] == BURNED),
            "n_retired": sum(1 for r in rs if r["status"] == RETIRED),
            "low_water": n_av <= LOW_WATER,
            "exhausted": n_av == 0,
        })
    return {
        "generated_at": _iso(now),
        "reissue_seconds": REISSUE_SECONDS,
        "low_water_mark": LOW_WATER,
        "categories": out,
        "n_available_total": sum(c["n_available"] for c in out),
        "n_burned_total": sum(c["n_burned"] for c in out),
        "exhausted_categories": [c["category"] for c in out if c["exhausted"]],
        "low_water_categories": [c["category"] for c in out if c["low_water"]],
        "replenishment": ("Prompts are not recycled. A burned prompt is spent "
                          "permanently, because re-serving it would measure "
                          "recall rather than the capability the trap probes. "
                          "Refill by running expand_seeds.py over the seed "
                          "roster and re-upserting; low_water marks the "
                          "categories that need it."),
    }
