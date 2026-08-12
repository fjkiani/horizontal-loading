"""Sweep every category over a grid of seeds instead of shipping one frozen trap.

Two problems this fixes at once.

1. STALENESS. The served pool held exactly one trap per category, so "generate"
   returned the same fourteen strings forever. A grid gives an on-demand supply.

2. FIELD MEMORABILITY. The stump harness showed the real failure mode is a
   memorable ANSWER FIELD, not a leaky ranking. 'city of birth' and 'award year'
   are attributes a model recalls the moment it identifies the entity; 'ICAO
   identifier' and 'SEC Central Index Key' are not. So every candidate is tagged
   with field_class here, and the ranker prefers opaque identifiers.

Checkpoints after every seed so an interrupt costs one API traversal, not a run.
"""
import json
import os
import re
import sys
import time
import traceback

import hashlib

import category_traps as ct
# ORDER MATTERS. Each module overrides entries in ct.GENERATORS, so importing
# only gen_v2 -- as this script did until now -- silently ran the WRONG
# generator for celebrities, education, finance, history and sports. Sports
# would have ignored the validated pitching_battersFaced key swap entirely.
# assert_ownership() below is the guard; mirrors tests/test_import_graph.py.
import gen_v2  # noqa: F401,E402
import gen_v3  # noqa: F401,E402
import gen_v4  # noqa: F401,E402
import source_gate as sg  # noqa: E402
import evaluate_traps as et  # noqa: E402

SHARD = os.environ.get("EXPAND_SHARD", "A")
OUT = os.environ.get("EXPAND_OUT", f"expand_{SHARD}.json")

_OWNER = {
    "business": "gen_v2", "politics": "gen_v2", "geography": "gen_v2",
    "shopping": "gen_v2", "tv shows and movies": "gen_v2", "video games": "gen_v2",
    "history": "gen_v3", "celebrities/public figures": "gen_v3",
    "education": "gen_v3", "sports": "gen_v3",
    "finance": "gen_v4",
    "art": "category_traps", "health and medicine": "category_traps",
    "legal": "category_traps", "science and technology": "category_traps",
    "travel": "category_traps",
}


def assert_ownership():
    """Fail loudly rather than sweep 81 seeds through stale code."""
    bad = [f"{c}: expected {w}, got {getattr(ct.GENERATORS.get(c), '__module__', None)}"
           for c, w in _OWNER.items()
           if getattr(ct.GENERATORS.get(c), "__module__", None) != w]
    if bad:
        raise SystemExit("GENERATOR OWNERSHIP MISMATCH\n  " + "\n  ".join(bad))
    print(f"ownership ok: {len(_OWNER)} categories match tests/test_import_graph.py")


def trap_id(category, field, answer):
    """Pool identity. Two seeds yielding one answer are ONE prompt; travel's
    measured distinct_answer_rate is 0.8, so collisions are real."""
    return hashlib.sha256(f"{category}|{field}|{answer}".encode()).hexdigest()[:16]

# An identifier is a token whose value cannot be inferred or recalled from
# knowing the entity; an attribute is a fact about the entity that a model may
# simply remember. This is the axis the celebrities trap failed on.
_IDENTIFIER_FIELDS = {
    "arXiv identifier", "accession number", "SEC Central Index Key",
    "ICAO identifier", "trial registry identifier", "United States Reports page",
    "IATA code", "IMDb title identifier", "executive order number",
    "internet domain", "barcode", "CUSIP",
}


def field_class(field):
    f = (field or "").lower()
    if field in _IDENTIFIER_FIELDS:
        return "identifier"
    if re.search(r"\b(identifier|number|code|accession|key|domain|cusip|barcode|page)\b", f):
        return "identifier"
    return "attribute"


GRID = {
    "science and technology": [
        {},
        {"days": ("2023-02-14", "2023-05-16", "2023-09-12", "2023-11-14", "2024-09-10", "2024-10-08"),
         "cats": ("cs.CR", "math.PR", "cond-mat.mes-hall", "astro-ph.GA", "q-bio.PE", "physics.flu-dyn")},
        {"days": ("2022-03-08", "2022-06-14", "2022-10-11", "2023-01-10", "2023-04-11", "2023-07-11"),
         "cats": ("cs.LG", "math.CO", "cond-mat.supr-con", "astro-ph.HE", "q-bio.NC", "physics.optics")},
        {"days": ("2021-05-11", "2021-09-14", "2022-01-11", "2022-04-12", "2022-08-09", "2022-11-08"),
         "cats": ("cs.DS", "math.NT", "cond-mat.soft", "astro-ph.SR", "q-bio.QM", "physics.plasm-ph")},
    ],
    "art": [
        {}, {"artist": "Vincent van Gogh", "dept": 11}, {"artist": "Claude Monet", "dept": 11},
        {"artist": "Katsushika Hokusai", "dept": 6}, {"artist": "Albrecht Durer", "dept": 9},
        {"artist": "Paul Cezanne", "dept": 11},
    ],
    "business": [
        {}, {"loc": "US-WA", "concept": "ResearchAndDevelopmentExpense", "year": 2018},
        {"loc": "US-CA", "concept": "ResearchAndDevelopmentExpense", "year": 2016},
        {"loc": "US-MA", "concept": "ResearchAndDevelopmentExpense", "year": 2017},
        {"loc": "US-NY", "concept": "ResearchAndDevelopmentExpense", "year": 2019},
        {"loc": "US-IL", "concept": "ResearchAndDevelopmentExpense", "year": 2015},
    ],
    "celebrities/public figures": [
        {}, {"category_key": "Chemistry", "y0": 1901, "y1": 1975},
        {"category_key": "Physiology or Medicine", "y0": 1901, "y1": 1970},
        {"category_key": "Literature", "y0": 1901, "y1": 1980},
        {"category_key": "Peace", "y0": 1901, "y1": 1975},
    ],
    "education": [
        {}, {"country": "Norway"}, {"country": "Portugal"}, {"country": "Finland"},
        {"country": "Israel"}, {"country": "Chile"}, {"country": "Hungary"}, {"country": "Denmark"},
    ],
    "geography": [
        {}, {"country_iso": "CH", "country_name": "Switzerland"},
        {"country_iso": "PE", "country_name": "Peru"}, {"country_iso": "NP", "country_name": "Nepal"},
        {"country_iso": "BO", "country_name": "Bolivia"}, {"country_iso": "EC", "country_name": "Ecuador"},
        {"country_iso": "KE", "country_name": "Kenya"},
    ],
    "health and medicine": [
        {}, {"condition": "multiple sclerosis", "phase": "PHASE3"},
        {"condition": "idiopathic pulmonary fibrosis", "phase": "PHASE3"},
        {"condition": "sickle cell disease", "phase": "PHASE3"},
        {"condition": "Duchenne muscular dystrophy", "phase": "PHASE3"},
        {"condition": "cystic fibrosis", "phase": "PHASE3"},
    ],
    "history": [
        {}, {"category_key": "Chemistry", "y0": 1901, "y1": 2000, "min_laureates": 3},
        {"category_key": "Physiology or Medicine", "y0": 1901, "y1": 2000, "min_laureates": 3},
        {"category_key": "Physics", "y0": 1930, "y1": 1990, "min_laureates": 3},
    ],
    "legal": [
        {}, {"vols": (520, 524, 530, 533)}, {"vols": (540, 545, 550, 555)},
        {"vols": (460, 465, 470, 475)}, {"vols": (480, 485, 490, 495)},
    ],
    "politics": [
        {}, {"years": (1997, 1999, 2005, 2009)}, {"years": (2011, 2017, 2019, 2021)},
        {"years": (1993, 1995, 2007, 2012)},
    ],
    "sports": [
        {},
        {"pairs": ((112, "Chicago Cubs", 2016), (120, "Washington Nationals", 2019),
                   (137, "San Francisco Giants", 2010), (114, "Cleveland Indians", 1995),
                   (115, "Colorado Rockies", 2007))},
        {"pairs": ((121, "New York Mets", 1986), (143, "Philadelphia Phillies", 1980),
                   (110, "Baltimore Orioles", 1983), (116, "Detroit Tigers", 1984),
                   (138, "St. Louis Cardinals", 2011))},
        {"pairs": ((136, "Seattle Mariners", 2001), (139, "Tampa Bay Rays", 2008),
                   (133, "Oakland Athletics", 1989), (135, "San Diego Padres", 1998),
                   (141, "Toronto Blue Jays", 1993))},
    ],
    "travel": [
        {}, {"airline_iata": "LH", "hub_iata": "FRA"}, {"airline_iata": "SK", "hub_iata": "CPH"},
        {"airline_iata": "OS", "hub_iata": "VIE"}, {"airline_iata": "LO", "hub_iata": "WAW"},
        {"airline_iata": "TP", "hub_iata": "LIS"},
    ],
    "tv shows and movies": [
        {},
        {"seeds": ((1996, "Crime"), (2001, "Mystery"), (2005, "Sci-Fi"), (1999, "Fantasy"),
                   (2010, "Musical"), (1994, "Crime"))},
        {"seeds": ((1988, "Western"), (1992, "War"), (2007, "Biography"), (2013, "Adventure"),
                   (1985, "Horror"), (2016, "Animation"))},
    ],
    "video games": [
        {},
        {"appids": (400, 292030, 379720, 588650, 264710, 646570, 204360, 49520, 233450,
                    275850, 8930, 294100, 219740, 632470)},
        {"appids": (620, 240, 550, 730, 570, 440, 10, 70, 220, 320, 360, 380, 420, 500)},
    ],
    "finance": [{}, {"year": 2010}, {"year": 2014}, {"year": 2021}, {"year": 2023}],
    "shopping": [
        {}, {"category_tag": "en:chocolates", "country": "france", "nutrient": "fat_100g", "max_pages": 6},
        {"category_tag": "en:breakfast-cereals", "country": "united-kingdom", "nutrient": "fat_100g", "max_pages": 6},
        {"category_tag": "en:biscuits", "country": "belgium", "nutrient": "fat_100g", "max_pages": 6},
        {"category_tag": "en:cheeses", "country": "switzerland", "nutrient": "fat_100g", "max_pages": 6},
    ],
}

# HOST-DISJOINT shards. The grouping is by upstream operator, not by size.
# legalops3 self-rate-limited (107 of 132 rows HTTP 429) because two workers
# hit loc.gov concurrently, which invalidated its verdict outright. No two
# shards may share an upstream host.
#   W0 imdb/tvmaze + pcgamingwiki   W1 openflights/ourairports/geonames
#   W2 cornell+courtlistener        W3 arxiv/openalex/datacite/ctgov/hipolabs
#   W4 sec/gleif/govinfo/mlb/aic/gs1/wikidata-free remainder
SHARDS = {
    "W0": ["tv shows and movies", "video games"],
    "W1": ["geography", "travel"],
    "W2": ["legal", "history"],
    "W3": ["science and technology", "health and medicine", "education"],
    "W4": ["business", "finance", "politics", "sports", "art", "shopping",
           "celebrities/public figures"],
}


def _accepted_traps(state):
    """Every trap this run has already banked, as the disjointness corpus.

    Held traps are included on purpose: a prompt that was refused for some other
    reason still occupies its domains and phrasing, so reusing them would be a
    ground-rule-7 breach even though the earlier row never shipped.
    """
    return [r["trap"] for r in (state.get("results") or [])
            if isinstance(r.get("trap"), dict)]


def main():
    assert_ownership()
    if SHARD not in SHARDS:
        raise SystemExit(f"unknown shard {SHARD!r}; have {sorted(SHARDS)}")
    cats = SHARDS[SHARD]
    state = json.load(open(OUT)) if os.path.exists(OUT) else {"results": []}
    done = {(r["category"], r["seed_repr"]) for r in state["results"]}
    for cat in cats:
        gen = ct.GENERATORS.get(cat)
        if gen is None:
            print(f"!! no generator for {cat}")
            continue
        for seed in GRID.get(cat, [{}]):
            sr = json.dumps(seed, sort_keys=True, default=str)
            if (cat, sr) in done:
                print(f"skip {cat} {sr[:60]}")
                continue
            rec = {"category": cat, "seed_repr": sr, "ok": False}
            t0 = time.time()
            try:
                cand = gen(**seed)
                trap = cand.to_trap()
                ok, viol = sg.validate_trap(trap)
                # DEFECT: this script used to stop at the gate and call that a
                # result. Gate-pass is NOT ship -- the retracted legalfix probe
                # made exactly this error and produced a false "5 of 5 seeds".
                # A prompt only enters the pool with an evaluate_one verdict.
                ev, verdict, tier, failing, unproven = None, "error", None, None, None
                try:
                    # T8/T9 are pairwise, so a lone trap scores "unproven" and
                    # is refused. Every trap already accepted in this run is the
                    # sibling set, which makes the sweep self-policing: seed k+1
                    # is measured against seeds 1..k rather than against nothing.
                    ev = et.evaluate_one(cat, {"status": "ok", "trap": trap},
                                         others=_accepted_traps(state))
                    verdict = ev.get("verdict")
                    tier = ev.get("witness_tier")
                    # evaluate_one returns tests[name] = {"pass": bool|None,
                    # "detail": str}. The original predicate here was `if not v`,
                    # and v is a non-empty dict, so it was ALWAYS falsey-negative:
                    # every one of the 81 seeds recorded failing_tests == [],
                    # including all 24 holds. The verdict was still correct
                    # (it is read straight off ev["verdict"]), but the REASON
                    # for every hold was silently discarded, and the roadmap
                    # built on it was wrong. `pass is False` and `pass is None`
                    # are kept apart: False is a measured failure, None is an
                    # unproven test, and conflating them is how a hold gets
                    # mistaken for a refusal.
                    failing = sorted(k for k, v in (ev.get("tests") or {}).items()
                                     if isinstance(v, dict) and v.get("pass") is False)
                    unproven = sorted(k for k, v in (ev.get("tests") or {}).items()
                                      if isinstance(v, dict) and v.get("pass") is None)
                except Exception as ee:  # noqa: BLE001
                    rec["eval_error"] = f"{type(ee).__name__}: {ee}"
                rank = dict(ct.LAST_RANK or {})
                rec.update({
                    "ok": bool(ok), "violations": viol,
                    "verdict": verdict, "witness_tier": tier,
                    "failing_tests": failing, "unproven_tests": unproven,
                    "ships": verdict == "ship",
                    "trap_id": trap_id(cat, trap.get("field"),
                                       str(trap.get("answer"))),
                    "trap": trap,
                    "field": trap.get("field"), "answer": str(trap.get("answer")),
                    "entity": trap.get("entity"), "n_base": trap.get("n_base"),
                    "field_class": field_class(trap.get("field")),
                    "spearman": rank.get("spearman_key_vs_api_order"),
                    "ties_at_extremum": rank.get("n_tied_at_extremum"),
                    "p_uniform_guess": rank.get("p_answer_by_uniform_guess"),
                    "winner_is_first": rank.get("winner_is_first_returned"),
                    "winner_is_last": rank.get("winner_is_last_returned"),
                    "n_confirming": len(trap.get("independent_confirming_operators") or []),
                    "prompt": trap.get("prompt"),
                    "source_operators": trap.get("source_operators"),
                    "primary_operator": trap.get("primary_operator"),
                })
            except Exception as exc:
                rec["error"] = f"{type(exc).__name__}: {exc}"
                rec["tb"] = traceback.format_exc()[-600:]
            rec["secs"] = round(time.time() - t0, 1)
            state["results"].append(rec)
            with open(OUT + ".tmp", "w") as fh:
                json.dump(state, fh, indent=1)
            os.replace(OUT + ".tmp", OUT)
            flag = ("SHIP" if rec.get("ships") else
                    ("gate" if rec["ok"] else "--  "))
            print(f"{flag} {cat:28s} {sr[:44]:44s} "
                  f"{str(rec.get('verdict')):11s} "
                  f"{rec.get('answer') or rec.get('error','')[:52]}")
    n_ok = sum(1 for r in state["results"] if r["ok"])
    n_ship = sum(1 for r in state["results"] if r.get("ships"))
    n_uniq = len({r["trap_id"] for r in state["results"] if r.get("ships")})
    print(f"\nshard {SHARD}: {len(state['results'])} seeds -> {n_ok} gate-valid "
          f"-> {n_ship} SHIP -> {n_uniq} distinct answers  [{OUT}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
