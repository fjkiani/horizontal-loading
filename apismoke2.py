#!/usr/bin/env python3
"""apismoke2.py -- re-run the live-API smoke test against the FIXED path.

Calls app.main._run_category_generate synchronously (same code the daemon
thread runs) so the job dict can be inspected in full, and checkpoints after
EVERY case so an interrupt loses at most one generation.

What has to be true now:

  finance  -> status "refused", detail begins "TrapUnavailable", NOT "error"
              and NOT a TypeError. This was the D1 failure.
  art      -> status "refused" (it already did; regression guard)
  health   -> the exact seed the live service used, {"condition": "multiple
              sclerosis", "phase": "PHASE3"}, which returned NCT04300920 on a
              base of 30 with NO depth or witness measurement. It must now come
              back with all twelve tests scored. Whether it ships or is held is
              the measurement, not a target -- but it can no longer be served
              unmeasured.
  travel   -> the live service served HEL (n_base 171) against a baked IVL
              (n_base 73). Same question: measured now, either way.
  legal    -> a category whose baked verdict was ship, as a positive control:
              if the fixed path refuses EVERYTHING the gate is miscalibrated,
              not strict.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

from app import main as api  # noqa: E402

OUT = os.path.join(_HERE, "apismoke2.json")

CASES = [
    ("finance", {"year": 2010}),                 # D1: was TypeError -> error
    ("finance", {}),                             # default seed
    ("art", {"artist": "Albrecht Durer", "dept": 9}),
    ("health and medicine", {"condition": "multiple sclerosis",
                             "phase": "PHASE3"}),   # D2: was served unmeasured
    ("health and medicine", {}),                 # the baked seed
    ("travel", {"airline_iata": "LH", "hub_iata": "FRA"}),  # D2: served HEL
    ("travel", {}),
    ("legal", {}),                               # positive control
]


def load():
    if os.path.exists(OUT):
        try:
            return json.load(open(OUT))
        except Exception:
            pass
    return {"cases": []}


def save(state):
    state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = OUT + ".tmp"
    json.dump(state, open(tmp, "w"), indent=2, default=str)
    os.replace(tmp, OUT)


def main():
    state = load()
    done = {(c["category"], json.dumps(c["seed"], sort_keys=True))
            for c in state["cases"]}
    for cat, seed in CASES:
        key = (cat, json.dumps(seed, sort_keys=True))
        if key in done:
            print("skip (checkpointed): %s %s" % (cat, seed), flush=True)
            continue
        job_id = "smoke%d" % len(state["cases"])
        api._JOBS[job_id] = {"status": "running", "started": time.time(),
                             "category": cat, "seed": seed, "log": []}
        t0 = time.time()
        print("--- %s %s ..." % (cat, json.dumps(seed)), flush=True)
        try:
            api._run_category_generate(job_id, cat, seed, None)
        except Exception:
            api._JOBS[job_id].update({"status": "error",
                                      "detail": traceback.format_exc()[-800:]})
        job = api._JOBS[job_id]
        ev = job.get("evaluation") or {}
        rec = {
            "category": cat, "seed": seed,
            "status": job.get("status"),
            "detail": (job.get("detail") or "")[:1200],
            "verdict": ev.get("verdict"),
            "witness_tier": ev.get("witness_tier"),
            "independent_witnesses": ev.get("independent_witnesses"),
            "failed_tests": ev.get("failed_tests"),
            "unproven_tests": ev.get("unproven_tests"),
            "n_tests": ev.get("n_tests"),
            "tests": {k: {"pass": v["pass"], "detail": v["detail"][:400]}
                      for k, v in (ev.get("tests") or {}).items()},
            "answer": (job.get("result") or {}).get("answer")
                      or (job.get("rejected_candidate") or {}).get("answer"),
            "n_base": (job.get("result") or {}).get("n_base")
                      or (job.get("rejected_candidate") or {}).get("n_base"),
            "secs": round(time.time() - t0, 1),
        }
        state["cases"].append(rec)
        save(state)
        print("    %-9s verdict=%-9s answer=%-14s n=%-5s failed=%s unproven=%s  %.1fs"
              % (rec["status"], rec["verdict"], rec["answer"], rec["n_base"],
                 rec["failed_tests"], rec["unproven_tests"], rec["secs"]),
              flush=True)
        if rec["status"] == "error":
            print("    ERROR DETAIL: %s" % rec["detail"][:300], flush=True)

    print("\n== summary")
    for c in state["cases"]:
        print("  %-22s %-34s %-9s %-9s %s"
              % (c["category"], json.dumps(c["seed"])[:34], c["status"],
                 c["verdict"], c["answer"]))
    print("wrote %s" % OUT)


if __name__ == "__main__":
    sys.exit(main() or 0)
