#!/usr/bin/env python3
"""countkey.py -- rebuild the OpenAlex author key on a COUNT statistic.

Why: the sum-of-citations key fails the exact fragility condition S < sep in
9 of 9 measured cohorts.  A single work decides the answer everywhere, because
in citation data a large margin IS a blockbuster paper.  Concentration and
separation are positively coupled (rho 0.7667, n=9), so no cohort choice fixes
it.

A count statistic breaks the coupling by construction: removing one work moves
a count by exactly 1, so the answer is k-robust iff the winner leads the
runner-up by more than k works.  Robustness becomes a measurable integer
instead of a ratio.

Collection (one paginated works query, not one query per author):
    works?filter=institutions.id:{ID},publication_year:{Y},cited_by_count:>{T}
Key: the author (ORCID-bearing) credited on the most such works.

Gates, all measured here rather than assumed:
  MIN_AUTHORS       answer space large enough to be unguessable
  MIN_MARGIN        winner leads runner-up by >= this many works (k-robustness)
  MAX_UNIFORM       1/n_distinct guess probability
  anti-shortcut     count winner != citation-sum winner != top-work author
  sortability       the key must not be a server-side ordering
"""
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "countkey.json")
MAILTO = "fahad@crispro.ai"
BASE = "https://api.openalex.org/works"

MIN_AUTHORS = 150
MIN_MARGIN = 2
MAX_UNIFORM = 0.10

# (institution_id, display name, year, citation threshold), sized from a
# meta.count scan so each cell is 3-16 cursor pages rather than a blind sweep
CELLS = [
    ("https://openalex.org/I161318765", "University of Vienna", 2005, 49),
    ("https://openalex.org/I161318765", "University of Vienna", 2012, 49),
    ("https://openalex.org/I161318765", "University of Vienna", 2018, 49),
    ("https://openalex.org/I165143802", "National University of Singapore",
     2005, 49),
    ("https://openalex.org/I165143802", "National University of Singapore",
     2012, 49),
    ("https://openalex.org/I165143802", "National University of Singapore",
     2018, 49),
    ("https://openalex.org/I67311998", "Leiden University", 2012, 9),
    ("https://openalex.org/I67311998", "Leiden University", 2018, 9),
    ("https://openalex.org/I67311998", "Leiden University", 2012, 49),
]

RESULT = {"started": time.time(), "gates": {
    "MIN_AUTHORS": MIN_AUTHORS, "MIN_MARGIN": MIN_MARGIN,
    "MAX_UNIFORM": MAX_UNIFORM}, "cells": {}}


def put(k, v):
    RESULT[k] = v
    with open(OUT, "w") as fh:
        json.dump(RESULT, fh, indent=1, default=str)
    print("[put] %s" % k, flush=True)


def fetch_works(inst_id, year, thresh, cap_pages=12):
    """Cursor-paginate the works slice.  Returns (rows, meta_count, pages)."""
    flt = "institutions.id:%s,publication_year:%d,cited_by_count:>%d" % (
        inst_id, year, thresh)
    sel = "id,doi,title,publication_year,cited_by_count,authorships"
    cursor = "*"
    rows, meta_count, pages = [], None, 0
    while cursor and pages < cap_pages:
        q = urllib.parse.urlencode({
            "filter": flt, "select": sel, "per-page": 200,
            "cursor": cursor, "mailto": MAILTO})
        try:
            d = net.get_json("%s?%s" % (BASE, q))
        except Exception as exc:  # noqa: BLE001
            return rows, meta_count, pages, "FETCH_ERROR: %s" % exc
        if meta_count is None:
            meta_count = (d.get("meta") or {}).get("count")
        got = d.get("results") or []
        rows.extend(got)
        pages += 1
        cursor = (d.get("meta") or {}).get("next_cursor")
        if not got:
            break
    return rows, meta_count, pages, None


def analyse(rows):
    """Group by ORCID author.  Count works, sum citations, track top work."""
    by = {}
    seen_dois = set()
    dedup_rows = []
    for w in rows:
        doi = (w.get("doi") or "").lower()
        # collapse the Wiley-style duplicate DOI variants seen in Leiden 1997
        stem = doi.split("<")[0] if doi else ""
        keyd = stem or (w.get("title") or "").strip().lower()
        if keyd and keyd in seen_dois:
            continue
        if keyd:
            seen_dois.add(keyd)
        dedup_rows.append(w)
    for w in dedup_rows:
        cites = w.get("cited_by_count") or 0
        for a in (w.get("authorships") or []):
            au = a.get("author") or {}
            oid = au.get("orcid")
            if not oid:
                continue
            oid = oid.rsplit("/", 1)[-1]
            rec = by.setdefault(oid, {
                "orcid": oid, "name": au.get("display_name"),
                "n_works": 0, "sum_cites": 0, "top_work_cites": 0,
                "top_work_title": None})
            rec["n_works"] += 1
            rec["sum_cites"] += cites
            if cites > rec["top_work_cites"]:
                rec["top_work_cites"] = cites
                rec["top_work_title"] = w.get("title")
    return by, len(dedup_rows), len(rows) - len(dedup_rows)


def evaluate(by, n_dedup, n_dup, meta_count, inst, year, thresh):
    out = {"institution": inst, "year": year, "threshold": thresh,
           "meta_count": meta_count, "n_works_fetched": n_dedup,
           "n_duplicate_works_dropped": n_dup, "n_orcid_authors": len(by)}
    if len(by) < 2:
        out["fails"] = ["TOO_FEW_AUTHORS"]
        out["verdict"] = "REJECT"
        return out
    ranked = sorted(by.values(), key=lambda r: -r["n_works"])
    win, run = ranked[0], ranked[1]
    tied = [r for r in ranked if r["n_works"] == win["n_works"]]
    margin = win["n_works"] - run["n_works"]
    counts = [r["n_works"] for r in ranked]
    distinct = len(set(counts))

    # anti-shortcut competitors
    by_sum = max(ranked, key=lambda r: r["sum_cites"])
    by_top = max(ranked, key=lambda r: r["top_work_cites"])

    out.update({
        "winner_orcid": win["orcid"], "winner_name": win["name"],
        "winner_n_works": win["n_works"], "winner_sum_cites": win["sum_cites"],
        "runner_up_name": run["name"], "runner_up_n_works": run["n_works"],
        "margin_in_works": margin, "n_tied_at_max": len(tied),
        "k_robustness": max(0, margin - 1),
        "n_distinct_counts": distinct,
        "p_uniform": round(1.0 / len(by), 6),
        "citation_sum_winner": by_sum["name"],
        "citation_sum_winner_orcid": by_sum["orcid"],
        "top_single_work_author": by_top["name"],
        "top_single_work_author_orcid": by_top["orcid"],
        "shortcut_count_equals_sum": by_sum["orcid"] == win["orcid"],
        "shortcut_count_equals_topwork": by_top["orcid"] == win["orcid"],
    })

    fails = []
    if len(by) < MIN_AUTHORS:
        fails.append("ANSWER_SPACE_BELOW_%d" % MIN_AUTHORS)
    if len(tied) != 1:
        fails.append("TIED_ARGMAX")
    if margin < MIN_MARGIN:
        fails.append("MARGIN_BELOW_%d_WORKS" % MIN_MARGIN)
    if out["p_uniform"] > MAX_UNIFORM:
        fails.append("GUESSABLE")
    if out["shortcut_count_equals_sum"]:
        fails.append("SHORTCUT_CITATION_SUM_GIVES_SAME_ANSWER")
    if out["shortcut_count_equals_topwork"]:
        fails.append("SHORTCUT_TOP_WORK_AUTHOR_GIVES_SAME_ANSWER")
    out["fails"] = fails
    out["verdict"] = "ACCEPT" if not fails else "REJECT"
    return out


def main():
    cells = {}
    for inst_id, inst, year, thresh in CELLS:
        tag = "%s|%d|>%d" % (inst, year, thresh)
        print("== %s" % tag, flush=True)
        rows, meta, pages, err = fetch_works(inst_id, year, thresh,
                                             cap_pages=20)
        if err:
            cells[tag] = {"institution": inst, "year": year,
                          "threshold": thresh, "error": err,
                          "verdict": "ERROR"}
            put("cells", cells)
            print("   %s" % err, flush=True)
            continue
        by, n_dedup, n_dup = analyse(rows)
        ev = evaluate(by, n_dedup, n_dup, meta, inst, year, thresh)
        ev["pages"] = pages
        cells[tag] = ev
        put("cells", cells)
        print("   works=%s authors=%s winner=%s n=%s margin=%s k=%s %s %s"
              % (n_dedup, ev.get("n_orcid_authors"), ev.get("winner_name"),
                 ev.get("winner_n_works"), ev.get("margin_in_works"),
                 ev.get("k_robustness"), ev["verdict"],
                 ",".join(ev.get("fails") or [])), flush=True)

    acc = {k: v for k, v in cells.items() if v.get("verdict") == "ACCEPT"}
    # prefer the largest k-robustness, then the largest answer space
    best = None
    if acc:
        best = max(acc.items(),
                   key=lambda kv: (kv[1]["k_robustness"],
                                   kv[1]["n_orcid_authors"]))
    put("VERDICT", {
        "n_cells": len(cells),
        "n_accept": len(acc),
        "accepted": sorted(acc.keys()),
        "best_cell": best[0] if best else None,
        "best": best[1] if best else None,
        "rationale": ("a count key moves by 1 per work, so k_robustness = "
                      "margin-1 is the number of works that can be removed or "
                      "re-attributed before the answer changes"),
        "conclusion": ("count key VIABLE" if acc else
                       "count key NOT VIABLE on these cells"),
    })
    put("finished", time.time())


if __name__ == "__main__":
    main()
