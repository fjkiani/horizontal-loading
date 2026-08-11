#!/usr/bin/env python3
"""apirace.py -- does the deployed /api/generate path cross-contaminate
ranking evidence between concurrent jobs?

category_traps._pick_extreme REBINDS the module global LAST_RANK, and
Candidate.to_trap() reads that same global with dict(LAST_RANK). The build
driver (run_category_traps.run_one) resets it per generator and runs strictly
sequentially. The API does NOT: _run_category_generate spawns a daemon thread
per request, never resets LAST_RANK, and holds no lock.

If that is a real race then two overlapping requests can hand trap A the
ranking evidence of trap B -- and every depth/leak/separation test I am about
to wire into the API reads exactly that field, so the gate would be scoring the
wrong population. Measured here rather than argued.

No network: two fake generators drive _pick_extreme directly.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import category_traps as ct

OUT = os.path.join(_HERE, "apirace.json")

N_TRIALS = 200
WITNESS_DELAY = 0.002   # stands in for the witness-confirmation round trips


def _fake_generate(tag, rows, delay, results, idx):
    """Mimic a generator: rank a population, then do slow witness work, then
    build the trap -- exactly the ordering every real generator uses."""
    best = ct._pick_extreme(rows, keyfn=lambda r: r["k"], label="k", mode="max")
    time.sleep(delay)                       # witness confirmation window
    cand = ct.Candidate(
        category="science and technology", field="f", answer=str(best["id"]),
        entity=tag, n_base=len(rows), sources=["arxiv.org"],
        confirming_sources=["openalex.org"], api_proof_argument="x",
        confirmation="y", prompt="p")
    trap = cand.to_trap()
    results[idx] = {
        "tag": tag,
        "own_n": len(rows),
        # the evidence key is n_base, NOT n. Reading .get("n") made the check
        # vacuous (always None) and the first run of this probe reported a
        # contamination rate of exactly 0.0000 -- an instrument defect, not a
        # clean bill of health.
        "evidence_n": trap["ranking_evidence"].get("n_base"),
        "evidence_top": (trap["ranking_evidence"].get("top_keys") or [None])[0],
        "own_top": best["k"],
    }


def trial(delay):
    rows_a = [{"id": i, "k": i} for i in range(1, 41)]        # n=40, top=40
    rows_b = [{"id": 1000 + i, "k": i * 7} for i in range(1, 18)]  # n=17, top=112
    res = {}
    ta = threading.Thread(target=_fake_generate, args=("A", rows_a, delay, res, 0))
    tb = threading.Thread(target=_fake_generate, args=("B", rows_b, delay, res, 1))
    ta.start()
    time.sleep(delay / 4.0)   # stagger so B ranks inside A's witness window
    tb.start()
    ta.join(); tb.join()
    return res


def main():
    ct.LAST_RANK = {}
    contaminated = 0
    detail = []
    for t in range(N_TRIALS):
        res = trial(WITNESS_DELAY)
        bad = [r for r in res.values()
               if r["evidence_n"] is not None and r["evidence_n"] != r["own_n"]]
        if bad:
            contaminated += 1
            if len(detail) < 5:
                detail.append({"trial": t, "rows": list(res.values())})
    rate = contaminated / float(N_TRIALS)
    out = {"n_trials": N_TRIALS, "n_contaminated": contaminated,
           "contamination_rate": round(rate, 4),
           "witness_delay_s": WITNESS_DELAY,
           "examples": detail,
           "mechanism": ("_pick_extreme rebinds the module global LAST_RANK; "
                         "to_trap() reads it after the witness round trips, so "
                         "any generation that ranks inside another's witness "
                         "window overwrites the evidence the gate will score")}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)
    print("trials %d  contaminated %d  rate %.4f"
          % (N_TRIALS, contaminated, rate))
    for d in detail[:3]:
        print("  trial %d: %s" % (d["trial"], json.dumps(d["rows"])))
    print("wrote %s" % OUT)


if __name__ == "__main__":
    sys.exit(main() or 0)
