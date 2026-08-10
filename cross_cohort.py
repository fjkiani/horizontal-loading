#!/usr/bin/env python3
"""Loop B -- cross-cohort validation.

The 16 shipped traps were each produced from one hand-chosen seed (a country, a
year, a company, a volume). A generator that only works on its seed is not a
generator, it is a hard-coded answer with extra steps. This re-runs every
category against an INDEPENDENT seed and asks three questions:

  B1 does the mechanism still produce a gate-valid trap?
  B2 is the answer DIFFERENT from the primary cohort? (an identical answer
     across disjoint seeds means the seed was never doing any work)
  B3 does the ranking evidence hold up on the second cohort too?

A category that passes B1-B3 has a demonstrated mechanism. A category that
fails B1 is reported as seed-dependent -- that is a real limitation, not a
result to hide.

Checkpoints to cross_cohort.json after every category.
"""
import json
import os
import sys
import time
import traceback

import category_traps as ct
import gen_v2  # noqa: F401  -- installs ct.GENERATORS overrides ON IMPORT
import source_gate as sg

OUT = "cross_cohort.json"
PRIMARY = "category_trap_candidates.json"

# Every ALT seed must be DISJOINT from the primary seed and must match the
# generator signature actually installed after gen_v2's overrides -- four of the
# original ALT entries were written against pre-gen_v2 signatures (business took
# `cik`, politics took `year`, tv took `years`/`genres`) and would have raised
# TypeError, which Loop B would have mis-reported as a mechanism failure.
# Finance previously reused year=2018, i.e. the primary seed, so its B2 test was
# vacuous.
ALT = {
    "science and technology": dict(
        days=("2023-02-14", "2023-05-16", "2023-09-12", "2023-11-14",
              "2024-09-10", "2024-10-08"),
        cats=("cs.CR", "math.PR", "cond-mat.mes-hall", "astro-ph.GA",
              "q-bio.PE", "physics.flu-dyn")),
    # van Gogh rather than Monet: the accession witness needs P217 scoped by
    # P195=Q160236, and the Met's van Goghs are the better-catalogued set.
    "art": dict(artist="Vincent van Gogh", dept=11),
    "business": dict(loc="US-WA", concept="ResearchAndDevelopmentExpense",
                     year=2018),
    "celebrities/public figures": dict(category_key="Chemistry", y0=1901, y1=1975),
    # Portugal is the deliberate stress case: the alphabetically-last domain is
    # utl.pt, whose institution merged into Universidade de Lisboa in 2013, so
    # the P856 equality check should refuse rather than ship a stale domain.
    "education": dict(country="Portugal"),
    "finance": dict(year=2010),
    "geography": dict(country_iso="CH", country_name="Switzerland"),
    "health and medicine": dict(condition="multiple sclerosis", phase="PHASE3"),
    # widened from y1=1935, which had no Chemistry prize shared by >= 3
    "history": dict(category_key="Chemistry", y0=1901, y1=2000, min_laureates=3),
    "legal": dict(vols=(520, 524, 530, 533)),
    "politics": dict(years=(1997, 1999, 2005, 2009)),
    "shopping": dict(category_tag="en:chocolates", country="france",
                     nutrient="fat_100g", max_pages=6),
    "sports": dict(pairs=((112, "Chicago Cubs", 2016),
                          (120, "Washington Nationals", 2019),
                          (137, "San Francisco Giants", 2010),
                          (114, "Cleveland Indians", 1995),
                          (115, "Colorado Rockies", 2007))),
    "travel": dict(airline_iata="LH", hub_iata="FRA"),
    "tv shows and movies": dict(seeds=((1996, "Crime"), (2001, "Mystery"),
                                       (2005, "Sci-Fi"), (1999, "Fantasy"),
                                       (2010, "Musical"), (1994, "Crime"))),
    # 14 titles / 14 distinct studios, none overlapping _VG_ROSTER; the
    # generator refuses the roster if any store record fails to resolve or if
    # two titles share a studio.
    "video games": dict(appids=(400, 292030, 379720, 588650, 264710, 646570,
                                204360, 49520, 233450, 275850, 8930, 294100,
                                219740, 632470)),
}


def main():
    only = None
    if "--only" in sys.argv:
        only = {s.strip() for s in sys.argv[sys.argv.index("--only") + 1].split(",")}

    prim = json.load(open(PRIMARY))["results"]
    out = {}
    if os.path.exists(OUT):
        out = json.load(open(OUT)).get("results", {})

    for cat, kwargs in ALT.items():
        if only and cat not in only:
            continue
        t0 = time.time()
        ct.LAST_RANK = {}
        rec = {"category": cat, "alt_kwargs": {k: str(v) for k, v in kwargs.items()}}
        pa = ((prim.get(cat) or {}).get("trap") or {}).get("answer")
        rec["primary_answer"] = pa
        try:
            cand = ct.GENERATORS[cat](**kwargs)
            trap = cand.to_trap()
            ok, viol = sg.validate_trap(trap, min_operators=3)
            rec.update({
                "status": "ok" if ok else "gate_fail",
                "alt_answer": trap["answer"],
                "alt_entity": trap["entity"],
                "n_base": trap["n_base"],
                "source_operators": trap["source_operators"],
                "confirming_operators": trap["confirming_operators"],
                "primary_operator": trap.get("primary_operator"),
                "independent_confirming_operators": trap.get(
                    "independent_confirming_operators"),
                "B4_witness_tier": (
                    lambda k: "gold" if k >= 2 else ("silver" if k == 1
                                                     else "unwitnessed")
                )(len(trap.get("independent_confirming_operators") or [])),
                "ranking_evidence": trap.get("ranking_evidence"),
                "violations": [v if isinstance(v, str) else " ".join(map(str, v))
                               for v in viol],
                "B1_gate_valid": bool(ok),
                "B2_answer_differs": (trap["answer"] != pa) if pa else None,
                "B3_unique_extremum": (trap.get("ranking_evidence") or {}).get(
                    "n_tied_at_extremum") == 1,
                "prompt": trap["prompt"],
            })
        except Exception as e:  # noqa: BLE001
            rec.update({"status": "fail", "error": str(e)[:300],
                        "etype": type(e).__name__,
                        "tb": traceback.format_exc()[-600:],
                        "B1_gate_valid": False, "B2_answer_differs": None,
                        "B3_unique_extremum": None})
        rec["secs"] = round(time.time() - t0, 1)
        out[cat] = rec
        _save(out)
        flag = "OK " if rec.get("B1_gate_valid") else "FAIL"
        print(f"{flag} {cat:28s} alt={str(rec.get('alt_answer'))[:26]:26s} "
              f"prim={str(pa)[:22]:22s} differs={rec.get('B2_answer_differs')} "
              f"{rec['secs']}s")
        if not rec.get("B1_gate_valid"):
            print("      ", str(rec.get("error") or rec.get("violations"))[:220])

    n_ok = sum(1 for r in out.values() if r.get("B1_gate_valid"))
    n_diff = sum(1 for r in out.values() if r.get("B2_answer_differs"))
    print(f"\n== cross-cohort: {n_ok}/{len(out)} mechanisms reproduced on an "
          f"independent seed; {n_diff} returned a different answer")
    return 0


def _save(out):
    doc = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "results": out,
           "counts": {"reproduced": sum(1 for r in out.values() if r.get("B1_gate_valid")),
                      "answer_differs": sum(1 for r in out.values()
                                            if r.get("B2_answer_differs")),
                      "total": len(out)}}
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=2, default=str)
    os.replace(tmp, OUT)


if __name__ == "__main__":
    sys.exit(main())
