#!/usr/bin/env python3
"""run_category_traps.py — execute every category generator, checkpointing per item.

Writes category_trap_candidates.json after EVERY generator so an interrupt loses at
most one category. Each record is either:

    {"category":..., "status":"ok",   "trap": {...}, "validation": {...}, "secs":...}
    {"category":..., "status":"fail", "error": "...", "etype": "...", "secs":...}

A trap is only marked ok if source_gate.validate_trap() passes it: valid category,
>=3 independent operators, >=1 confirming source, no banned domain, 70-150 words.

Usage:
    python run_category_traps.py                 # all 16
    python run_category_traps.py --only sports,art
    python run_category_traps.py --retry-failed
"""
from __future__ import annotations

import argparse
import json
import os
import time
import traceback

import category_traps as ct
import gen_v2  # noqa: F401
import gen_v3  # noqa: F401  # field redesign; must load after gen_v2  installs the redesigned generators into ct.GENERATORS
import gen_v4  # noqa: F401  # finance rescue; must load after gen_v2, which owned finance before
import source_gate as sg

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "category_trap_candidates.json")

ORDER = [
    "science and technology", "art", "business", "celebrities/public figures",
    "education", "finance", "geography", "health and medicine", "history",
    "legal", "politics", "shopping", "sports", "travel",
    "tv shows and movies", "video games",
]


def load():
    if os.path.exists(OUT):
        try:
            with open(OUT) as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"generated_at": None, "results": {}}


def save(state):
    state["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    res = state["results"]
    state["counts"] = {
        "ok": sum(1 for r in res.values() if r.get("status") == "ok"),
        "fail": sum(1 for r in res.values() if r.get("status") == "fail"),
        "total": len(res),
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, OUT)


def run_one(cat):
    fn = ct.GENERATORS[cat]
    t0 = time.time()
    try:
        # ct.generation() clears LAST_RANK and holds the generation lock across
        # ranking-through-emission. This driver is single-threaded so the lock
        # is free here, but using the same context manager the API uses keeps
        # ONE definition of "a generation" instead of two that can drift apart.
        with ct.generation():
            cand = fn()
            trap = cand.to_trap()
        ok, violations = sg.validate_trap(trap, min_operators=3)
        unmapped = sg.audit_operators(trap.get("sources"))
        rec = {
            "category": cat,
            "status": "ok" if ok else "fail",
            "trap": trap,
            "validation": {"ok": ok, "violations": violations,
                           "unmapped_hosts": unmapped},
            "secs": round(time.time() - t0, 1),
        }
        if not ok:
            rec["error"] = "gate rejected: " + json.dumps(violations)
            rec["etype"] = "GateRejected"
        return rec
    except ct.TrapUnavailable as e:
        return {"category": cat, "status": "fail", "error": str(e),
                "etype": "TrapUnavailable", "secs": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001 - we want the reason, not a crash
        return {"category": cat, "status": "fail",
                "error": f"{type(e).__name__}: {e}",
                "etype": type(e).__name__,
                "tb": traceback.format_exc()[-1200:],
                "secs": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--retry-failed", action="store_true")
    args = ap.parse_args()

    state = load()
    cats = list(ORDER)
    if args.only:
        cats = [c.strip() for c in args.only.split(",") if c.strip()]
        bad = [c for c in cats if c not in ct.GENERATORS]
        if bad:
            raise SystemExit(f"unknown categories: {bad}")
    elif args.retry_failed:
        cats = [c for c in ORDER
                if state["results"].get(c, {}).get("status") != "ok"]

    print(f"running {len(cats)} generator(s)")
    for cat in cats:
        print(f"--- {cat} ...", flush=True)
        rec = run_one(cat)
        state["results"][cat] = rec
        save(state)
        if rec["status"] == "ok":
            t = rec["trap"]
            print(f"    OK  {t['field']} = {t['answer']!r} "
                  f"[{len(t['source_operators'])} ops: {', '.join(t['source_operators'])}] "
                  f"{rec['secs']}s", flush=True)
        else:
            print(f"    FAIL ({rec['etype']}) {rec['error'][:220]} "
                  f"{rec['secs']}s", flush=True)

    res = state["results"]
    ok = [c for c in ORDER if res.get(c, {}).get("status") == "ok"]
    bad = [c for c in ORDER if c in res and res[c].get("status") != "ok"]
    print(f"\n== {len(ok)}/{len(res)} categories produced a gate-valid trap")
    if bad:
        print("unservable right now: " + "; ".join(bad))
    print(f"checkpoint: {OUT}")


if __name__ == "__main__":
    main()
