"""Bake the static catalog the Vercel front end reads on first paint.

The origin is a free-tier Render service that sleeps after inactivity, so a
console that fetches its pool from the API shows an empty screen for ~12 s on
every cold visit. The catalog is therefore baked into the build as static JSON:
the console renders instantly with the origin asleep, and only live generation
touches Render.

Each trap carries its difficulty evidence alongside its leakage evidence,
because the two are orthogonal and the pool was previously shipped with only
the second measured.
"""
import json
import os

import source_gate as sg

# Read the freshly evaluated candidates, not generated_pool.json. The pool file
# is the API's agent/vision-confirmed store and nothing in the build writes it,
# so baking from it silently shipped the previous build's answers -- Leiden,
# 1975 and wit.ie -- after the generators had already been repointed. The
# candidates file is the output of the run that just executed.
CANDS = os.environ.get("BAKE_CANDS", "category_trap_candidates.json")
EVAL = os.environ.get("BAKE_EVAL", "evaluation_report.json")
POOL = os.environ.get("BAKE_POOL", "generated_pool.json")
MEM = os.environ.get("BAKE_MEM", "memorability.json")
OUT = os.environ.get("BAKE_OUT", "web/public/catalog.json")

# Measured Wikipedia pageviews per year on the exact answer string, restricted
# to answers whose article is about the same referent as the answer itself.
# That restriction matters: the raw metric has two false positives, because a
# short identifier can collide with an unrelated article. The ICAO code SKIP
# matches an article about the verb, and the United States Reports page number
# 768 matches an article about the year 768 AD. Neither article is the answer's
# referent, so neither indicates that the answer is memorable. Once referent
# identity is required, the metric partitions the pool exactly the same way the
# field class does, which is why both are surfaced.
RECALL_EVIDENCE = {
    "celebrities/public figures": {
        "was": "Leiden", "was_pageviews_365": 145636,
        "now": "GND identifier", "now_wikipedia_mentions": 0},
    "history": {
        "was": "1975", "was_pageviews_365": 77453,
        "now": "GND identifier", "now_wikipedia_mentions": 0},
    "sports": {
        "was": "Newton", "was_pageviews_365": 26976,
        "now": "FAST identifier", "now_wikipedia_mentions": 0,
        "note": "GND covers 1 of 4 MLB players probed and ISNI/VIAF resolvers "
                "are 403 from here, but OCLC FAST is single-valued for 4 of 4. "
                "The first probe called FAST unreachable on an HTTP 406, which "
                "is content negotiation, not absence."},
    "video games": {
        "was": "Colossal Order", "was_pageviews_365": 40617, "now": None,
        "note": "NOT FIXED, and not for want of trying. No reachable authority "
                "covers game studios: GND 0 of 3, ISNI and VIAF 403, BnF has no "
                "record for the winning studio, MobyGames and IGDB both 403, "
                "and the two remaining single-valued Wikidata properties are a "
                "Basque library ID and a Mod DB slug, neither of which is an "
                "authority file. The answer stays an attribute and stays "
                "flagged rather than being given a witness that does not "
                "exist."},
}


def field_class(t):
    fc = (t.get("facts") or {}).get("answer_field_class")
    if fc:
        return fc
    f = (t.get("field") or "").lower()
    attribute = ("city of birth", "award year", "developing studio")
    return "attribute" if any(a in f for a in attribute) else "identifier"


def main():
    cands = json.load(open(CANDS))["results"]
    # A gate-rejected candidate still carries a populated trap dict, so filtering
    # on the presence of "trap" alone shipped finance and shopping -- both of
    # which fail R3c because every confirming source is run by the primary
    # operator. Filter on status, which is what run_category_traps actually sets.
    pool = [v["trap"] for v in cands.values()
            if v.get("trap") and v.get("status") == "ok" and not v.get("error")]

    # gold means an operator that runs neither the primary source nor the other
    # witness confirmed the answer-to-entity binding; the evaluator encodes that
    # as T5 passing, which is exactly the difference between ship and hold.
    verdicts = {}
    if os.path.exists(EVAL):
        doc = json.load(open(EVAL))
        rows = doc.get("per_trap") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
        for r in rows:
            if isinstance(r, dict) and r.get("category"):
                verdicts[r["category"]] = r.get("verdict")

    mem = {}
    if os.path.exists(MEM):
        for r in json.load(open(MEM)).get("answers", {}).values():
            mem[r.get("category")] = r

    traps = []
    for t in pool:
        cat = t.get("category")
        fc = field_class(t)
        m = mem.get(cat) or {}
        traps.append(dict(
            t,
            field_class=fc,
            # No solver measurement is available: the Cohere trial key is out of
            # its 1000-call monthly quota (HTTP 429, no retry-after, not cleared
            # by waiting). Left explicitly null rather than defaulted, so the UI
            # shows "not measured" instead of implying a passing score.
            stump=None,
            # Witness tier is a property of how many INDEPENDENT operators
            # confirm the answer, not of whether the trap passed the leakage
            # gates. Deriving it from the verdict conflated two different things
            # and could label a single-witness trap gold.
            witness_tier=(
                "gold" if len(t.get("independent_confirming_operators") or []) >= 2
                else "silver" if len(t.get("independent_confirming_operators") or []) == 1
                else "unwitnessed"),
            verdict=verdicts.get(cat),
            # There is no solver measurement. Every gate in this pipeline
            # measures LEAKAGE -- whether the answer can be reached by sorting,
            # guessing, reading the prompt, or recalling it from training -- and
            # none measures whether a model actually fails to find it. Those are
            # different quantities and the catalog must not print one under the
            # other's name. Null until a solver key exists.
            solver_difficulty=None,
            solver_difficulty_status=(
                "unmeasured: no solver quota. The available Cohere key is a trial "
                "key capped at 1000 calls/month and is exhausted (HTTP 429 with an "
                "explicit trial-quota message). A usable measurement needs about "
                "600 calls: the shipping traps x 3 access conditions (no tools, "
                "search only, full API) x ~20 repeats for workable intervals."),
            memorization_proxy=dict(
                measured_by="wikipedia-recall-proxy",
                caveat=("a proxy for whether the answer is memorable, NOT for "
                        "whether the trap is hard"),
                answer_wikipedia_mentions=m.get("search_hits"),
                answer_pageviews_365=m.get("pageviews_365"),
                exact_article=m.get("exact_article"),
                verdict=m.get("verdict"),
                field_class=fc,
                changed_this_build=RECALL_EVIDENCE.get(cat),
            ),
        ))

    # Keep the API's served pool in step with what the console shows, otherwise
    # /api/generated and catalog.json disagree about what shipped.
    old = {t.get("category"): t for t in json.load(open(POOL))} if os.path.exists(POOL) else {}
    merged = [dict(t, verified=(old.get(t["category"], {}).get("verified", True)))
              for t in pool]
    with open(POOL, "w") as fh:
        json.dump(merged, fh, indent=1)

    cats = []
    for c in sg.CATEGORIES:
        served = [t for t in traps if t.get("category") == c]
        cats.append({"category": c, "n_served": len(served),
                     "tier": (served[0].get("witness_tier") if served else None)})

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump({"generated_at": __import__("datetime").datetime.utcnow()
                   .strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "traps": traps, "categories": cats}, fh, indent=1)
    ident = sum(1 for t in traps if t["field_class"] == "identifier")
    print("baked %s: %d traps, %d categories, %d identifier-field, %d attribute-field"
          % (OUT, len(traps), len(cats), ident, len(traps) - ident))


if __name__ == "__main__":
    main()
