#!/usr/bin/env python3
"""healthforensic.py -- two questions the truncation probe raised.

Q1  DOES THE TRUNCATION CHANGE THE ANSWER? multiple sclerosis serves 200 of 235
    completed phase-3 studies. 35 records were never seen. If any of those 35
    declares more secondary outcomes than the winner of the first 200, the
    served answer is simply wrong -- not merely unproven. Page 2 settles it.

Q2  WHERE DID NCT04300920 / n_base=30 COME FROM? The live service reported that
    answer under seed {"condition": "multiple sclerosis", "phase": "PHASE3"},
    but multiple sclerosis has 235 such studies, not 30, and NCT04300920 is the
    ACTT COVID-19 trial. One of these is true: (a) the request hit the previous
    container during rollover, (b) some other roster seed yields exactly 30 and
    that answer, or (c) the seed reported in the job is not the seed the
    generator ran. This enumerates every health roster seed and computes the
    argmax the CURRENT generator logic would produce, so the explanation is
    identified rather than guessed.

Also records, for each seed, whether the `phase` kwarg does anything -- the
signature accepts it but the URL hardcodes aggFilters=phase:3.
"""
from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net  # noqa: E402

OUT = os.path.join(_HERE, "healthforensic.json")

BASE = ("https://clinicaltrials.gov/api/v2/studies?pageSize=200"
        "&query.cond={}&filter.overallStatus=COMPLETED"
        "&aggFilters=phase:3&countTotal=true")

SEEDS = ["amyotrophic lateral sclerosis", "multiple sclerosis",
         "idiopathic pulmonary fibrosis", "sickle cell disease",
         "Duchenne muscular dystrophy", "cystic fibrosis"]


def rows_from(js):
    out = []
    for s in js.get("studies", []):
        p = s.get("protocolSection", {})
        nct = p.get("identificationModule", {}).get("nctId")
        if not nct:
            continue
        om = p.get("outcomesModule") or {}
        out.append({"nct": nct,
                    "n_secondary": len(om.get("secondaryOutcomes") or []),
                    "n_primary": len(om.get("primaryOutcomes") or []),
                    "title": p.get("identificationModule", {})
                              .get("briefTitle", "")[:80]})
    return out


def argmax(rows):
    if not rows:
        return None, None, None, 0
    srt = sorted(rows, key=lambda r: r["n_secondary"], reverse=True)
    top = srt[0]["n_secondary"]
    tied = [r for r in srt if r["n_secondary"] == top]
    runner = srt[1]["n_secondary"] if len(srt) > 1 else 0
    return srt[0], top, runner, len(tied)


def pages(cond, cap=20):
    """Follow nextPageToken to enumerate the WHOLE collection."""
    url = BASE.format(cond.replace(" ", "+"))
    seen, total, npages, tok = [], None, 0, None
    while npages < cap:
        u = url + ("&pageToken=%s" % tok if tok else "")
        js = net.get_json(u, timeout=120)
        if total is None:
            total = js.get("totalCount")
        seen.extend(rows_from(js))
        npages += 1
        tok = js.get("nextPageToken")
        if not tok:
            break
    return seen, total, npages, bool(tok)


def main():
    state = {"seeds": [], "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                     time.gmtime())}
    for cond in SEEDS:
        rec = {"condition": cond}
        try:
            first = net.get_json(BASE.format(cond.replace(" ", "+")), timeout=120)
            r1 = rows_from(first)
            w1, top1, run1, tie1 = argmax(r1)
            allr, total, npages, more = pages(cond)
            wa, topa, runa, tiea = argmax(allr)
            rec.update({
                "n_page1": len(r1), "n_full": len(allr), "n_true": total,
                "pages_fetched": npages, "still_more": more,
                "page1_answer": w1["nct"] if w1 else None,
                "page1_top_secondary": top1, "page1_runner_up": run1,
                "page1_n_tied": tie1,
                "full_answer": wa["nct"] if wa else None,
                "full_top_secondary": topa, "full_runner_up": runa,
                "full_n_tied": tiea,
                "answer_changes_with_full_enumeration":
                    (w1 or {}).get("nct") != (wa or {}).get("nct"),
                "k_robustness_page1": (top1 - run1 - 1) if top1 is not None else None,
                "k_robustness_full": (topa - runa - 1) if topa is not None else None,
                "full_title": (wa or {}).get("title"),
            })
            rec["contains_NCT04300920"] = any(r["nct"] == "NCT04300920"
                                              for r in allr)
        except Exception as e:  # noqa: BLE001
            rec["error"] = "%s: %s" % (type(e).__name__, e)
        state["seeds"].append(rec)
        json.dump(state, open(OUT + ".tmp", "w"), indent=2, default=str)
        os.replace(OUT + ".tmp", OUT)
        print("%-34s page1 n=%-4s -> %-12s | full n=%-4s (true %-4s) -> %-12s | "
              "changes=%s  k1=%s kfull=%s  has04300920=%s"
              % (cond, rec.get("n_page1"), rec.get("page1_answer"),
                 rec.get("n_full"), rec.get("n_true"), rec.get("full_answer"),
                 rec.get("answer_changes_with_full_enumeration"),
                 rec.get("k_robustness_page1"), rec.get("k_robustness_full"),
                 rec.get("contains_NCT04300920")), flush=True)

    changed = [s for s in state["seeds"]
               if s.get("answer_changes_with_full_enumeration")]
    state["summary"] = {
        "n_seeds": len(state["seeds"]),
        "n_answer_changed_by_full_enumeration": len(changed),
        "changed": [{"condition": s["condition"], "page1": s["page1_answer"],
                     "full": s["full_answer"], "n_page1": s["n_page1"],
                     "n_full": s["n_full"]} for s in changed],
        "seed_yielding_NCT04300920": [s["condition"] for s in state["seeds"]
                                      if s.get("contains_NCT04300920")],
    }
    json.dump(state, open(OUT + ".tmp", "w"), indent=2, default=str)
    os.replace(OUT + ".tmp", OUT)
    print("\n== %d of %d seeds change answer under full enumeration: %s"
          % (len(changed), len(state["seeds"]),
             json.dumps(state["summary"]["changed"])))
    print("== seeds whose collection contains NCT04300920: %s"
          % state["summary"]["seed_yielding_NCT04300920"])
    print("wrote %s" % OUT)


if __name__ == "__main__":
    sys.exit(main() or 0)
