#!/usr/bin/env python3
"""travelbear -- is gen_travel ANSWER-BEARING on Wikidata?

wikimut returned LOAD_BEARING_LOGIC for travel, contradicting the plan's
reading of the source. Root cause found: category_traps.py:751 reads

    claims = (ent.get("entities", {}).get(wq, {}) or {}).get("claims", {}) or {}

and wikimut's mock `_Claims` is an EMPTY dict subclass. An empty dict is
falsy, so `_Claims() or {}` collapses to a plain {} and the mutated P1566
value is discarded before the generator ever sees it. Line 751 is the ONLY
site in the repo using that idiom, and it is the one line that sets the
travel answer. The harness was blind exactly where it mattered.

This probe replaces the identity mutation with the real attack:

  STAGE 1  measure the strength of the only guard. The guard is
             place.lower() in rdf.lower()
           where place = best[1]["name"].split()[0] -- ONE token from the
           OpenFlights airport name -- tested as a substring of a GeoNames
           RDF document. Enumerate every GeoNames id reachable from a
           Wikidata search for that token and count how many pass. k passing
           ids  =>  guard discriminates 1-in-k, not 1-in-1.

  STAGE 2  surgically poison exactly one claim. Wrap the REAL
           net.wikidata_entity, take the REAL response, and rewrite only the
           P1566 datavalue to a collider measured in stage 1. Everything else
           -- the OpenFlights join, the ranking, the P238 SPARQL lookup, the
           GeoNames fetch -- stays genuine. This is precisely "someone edited
           one Wikidata claim".

  STAGE 3  ask whether anything downstream notices: source_gate.validate_trap
           and the 13-test evaluate_traps battery are both run on the
           poisoned trap.

Verdict ANSWER_BEARING iff the shipped answer becomes the collider.
Writes travelbear.json. Checkpoints after every stage.
"""
import json
import os
import sys
import time
import traceback

OUT = os.environ.get("TRAVELBEAR_OUT", "travelbear.json")
BASE = "category_trap_candidates.json"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import category_traps as ct  # noqa: E402
import gen_v2  # noqa: E402,F401
import gen_v3  # noqa: E402,F401
import gen_v4  # noqa: E402,F401
import source_gate as sg  # noqa: E402
import evaluate_traps as et  # noqa: E402

STATE = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "why": __doc__.strip().splitlines()[0],
         "root_cause": ("category_traps.py:751 uses `(...).get('claims', {}) or {}`; "
                        "wikimut's _Claims is an empty dict subclass, so the mock "
                        "collapses to {} and the mutation never reaches the caller. "
                        "Only site in the repo with that idiom."),
         "stages": {}}


def save():
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(STATE, fh, indent=2)
    os.replace(tmp, OUT)
    print("  checkpoint: %s" % OUT, flush=True)


def baseline():
    rec = json.load(open(BASE))["results"]["travel"]
    t = rec.get("trap") or {}
    return {"status": rec.get("status"), "answer": t.get("answer"),
            "entity": t.get("entity"), "field": t.get("field")}


# ---------------------------------------------------------------- stage 1
def guard_strength(place, baseline_id):
    """How many distinct GeoNames ids pass `place.lower() in rdf.lower()`?"""
    cands, seen = [], set()
    try:
        hits = ct.net.wikidata_search(place).get("search", []) or []
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e), "k": None}
    for h in hits[:12]:
        qid = h.get("id")
        if not qid:
            continue
        try:
            ent = ct.net.wikidata_entity(qid)
            cl = ent.get("entities", {}).get(qid, {}).get("claims", {}) or {}
        except Exception:  # noqa: BLE001
            continue
        for c in cl.get("P1566", []) or []:
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(v, str) and v.strip() and v.strip() not in seen:
                seen.add(v.strip())
                cands.append({"geonames_id": v.strip(), "qid": qid,
                              "label": (h.get("label") or ""),
                              "description": (h.get("description") or "")[:90]})
    for c in cands:
        try:
            rdf = ct.net.fetch("https://sws.geonames.org/%s/about.rdf"
                               % c["geonames_id"], timeout=90)
            c["rdf_bytes"] = len(rdf)
            c["passes_guard"] = place.lower() in rdf.lower()
        except Exception as e:  # noqa: BLE001
            c["rdf_bytes"] = 0
            c["passes_guard"] = False
            c["error"] = "%s" % type(e).__name__
    passing = [c for c in cands if c.get("passes_guard")]
    return {"place_token": place, "n_wikidata_hits": len(hits),
            "n_geonames_ids_found": len(cands), "candidates": cands,
            "k_passing_guard": len(passing),
            "passing_ids": [c["geonames_id"] for c in passing],
            "baseline_in_passing": baseline_id in [c["geonames_id"] for c in passing],
            "guard_discrimination": (None if not passing else 1.0 / len(passing))}


# ---------------------------------------------------------------- stage 2
def poison_run(collider):
    """Rewrite ONLY the P1566 datavalue on the real Wikidata response."""
    real_entity = ct.net.wikidata_entity
    touched = {"n_calls": 0, "n_rewritten": 0, "originals": []}

    def poisoned(qid, *a, **k):
        ent = real_entity(qid, *a, **k)
        touched["n_calls"] += 1
        try:
            claims = ent["entities"][qid]["claims"]
            for c in claims.get("P1566", []) or []:
                dv = c["mainsnak"]["datavalue"]
                if isinstance(dv.get("value"), str):
                    touched["originals"].append(dv["value"])
                    dv["value"] = collider
                    touched["n_rewritten"] += 1
        except Exception:  # noqa: BLE001
            pass
        return ent

    ct.net.wikidata_entity = poisoned
    t0 = time.time()
    try:
        with ct.generation():
            cand = ct.GENERATORS["travel"]()
            trap = cand.to_trap()
        return {"outcome": "returned", "answer": trap.get("answer"),
                "entity": trap.get("entity"), "field": trap.get("field"),
                "sources": trap.get("sources"),
                "confirmation": (trap.get("confirmation") or "")[:300],
                "trap": trap, "patch": touched,
                "secs": round(time.time() - t0, 1)}
    except ct.TrapUnavailable as e:
        return {"outcome": "raised", "etype": "TrapUnavailable",
                "error": str(e)[:400], "patch": touched,
                "secs": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        return {"outcome": "raised", "etype": type(e).__name__,
                "error": "%s: %s" % (type(e).__name__, e), "patch": touched,
                "tb": traceback.format_exc()[-700:],
                "secs": round(time.time() - t0, 1)}
    finally:
        ct.net.wikidata_entity = real_entity


# ---------------------------------------------------------------- stage 3
def downstream(trap):
    """Does anything downstream notice the poison?"""
    out = {}
    try:
        sg.validate_trap(trap, min_operators=3)
        out["validate_trap"] = "PASSED"
    except Exception as e:  # noqa: BLE001
        out["validate_trap"] = "REJECTED: %s: %s" % (type(e).__name__, str(e)[:220])
    try:
        ev = et.evaluate_one("travel", {"status": "ok", "trap": trap})
        out["verdict"] = ev.get("verdict")
        out["witness_tier"] = ev.get("witness_tier")
        out["failing"] = [k for k, v in (ev.get("tests") or {}).items()
                          if v is False]
        out["n_tests"] = len(ev.get("tests") or {})
    except Exception as e:  # noqa: BLE001
        out["evaluate_one"] = "ERROR %s: %s" % (type(e).__name__, str(e)[:220])
    return out


def main():
    b = baseline()
    STATE["baseline"] = b
    print("baseline travel: status=%s answer=%s entity=%r"
          % (b["status"], b["answer"], b["entity"]), flush=True)
    if b["status"] != "ok" or not b["answer"]:
        STATE["verdict"] = "UNINFORMATIVE: travel baseline is not ok"
        save()
        return

    place = (b["entity"] or "").split()[0]
    print("\n[stage 1] guard is  %r.lower() in rdf.lower()  -- measuring k"
          % place, flush=True)
    g = guard_strength(place, b["answer"])
    STATE["stages"]["1_guard_strength"] = g
    print("  wikidata hits=%s  geonames ids=%s  k passing guard=%s  ids=%s"
          % (g.get("n_wikidata_hits"), g.get("n_geonames_ids_found"),
             g.get("k_passing_guard"), g.get("passing_ids")), flush=True)
    save()

    colliders = [i for i in (g.get("passing_ids") or []) if i != b["answer"]]
    if not colliders:
        STATE["verdict"] = ("NOT_DEMONSTRATED: no GeoNames id other than the "
                            "baseline passes the guard, so the substring check "
                            "is 1-in-1 for this seed")
        save()
        print("\n=> %s" % STATE["verdict"], flush=True)
        return
    collider = colliders[0]
    print("\n[stage 2] poisoning ONE claim: P1566 -> %s (was %s)"
          % (collider, b["answer"]), flush=True)
    r = poison_run(collider)
    STATE["stages"]["2_poison"] = {k: v for k, v in r.items() if k != "trap"}
    STATE["stages"]["2_poison"]["collider"] = collider
    print("  %s  answer=%s  (rewrote %s claim value(s), originals=%s)"
          % (r["outcome"], r.get("answer"), r["patch"]["n_rewritten"],
             r["patch"]["originals"]), flush=True)
    if r["outcome"] == "raised":
        print("  error: %s" % r.get("error"), flush=True)
    save()

    if r["outcome"] == "returned" and str(r.get("answer")) == str(collider):
        STATE["verdict"] = "ANSWER_BEARING"
        STATE["why_verdict"] = (
            "editing one Wikidata claim (P1566) moved the shipped answer from "
            "%s to %s with every other operator untouched" % (b["answer"], collider))
        print("\n[stage 3] does anything downstream notice?", flush=True)
        d = downstream(r["trap"])
        STATE["stages"]["3_downstream"] = d
        print("  validate_trap: %s" % d.get("validate_trap"), flush=True)
        print("  evaluate_one : verdict=%s tier=%s failing=%s"
              % (d.get("verdict"), d.get("witness_tier"), d.get("failing")),
              flush=True)
    elif r["outcome"] == "returned":
        STATE["verdict"] = "INDEPENDENT"
        STATE["why_verdict"] = ("poisoned P1566 but shipped %s, not the collider %s"
                                % (r.get("answer"), collider))
    else:
        STATE["verdict"] = "FAIL_CLOSED"
        STATE["why_verdict"] = ("poisoning P1566 with a guard-passing collider "
                                "still refused: %s" % r.get("error"))
    save()
    print("\n=> VERDICT %s\n   %s" % (STATE["verdict"], STATE["why_verdict"]),
          flush=True)


if __name__ == "__main__":
    main()
