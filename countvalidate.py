#!/usr/bin/env python3
"""countvalidate.py -- interrogate the count key's accepted cells.

countkey.py accepted 4 of 9 cells, best NUS 2018 (>49 citations): winner
Yusuke Yamauchi, 45 works, margin 16, k_robustness 15.  Against the
citation-sum key's k = 0 in all 9 cohorts that is a real improvement, but the
other accepted winners -- S. Bansal, X. Janssen, and the rejected E. Asilar and
C. F. Anders -- have the name shape and the work counts of large-collaboration
physics and astronomy authors.  If the key is really measuring 'who is listed
on the most hyperauthored papers' then it is both uninteresting and unstable,
because OpenAlex author disambiguation is weakest exactly there.

Three things are measured, none assumed:
  1. the author-count distribution of the winner's qualifying works
  2. whether the winner and the margin survive dropping hyperauthored works
  3. whether the key is reachable by a server-side author sort, i.e. whether
     ranking authors by works_count or cited_by_count reproduces the answer
"""
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "countvalidate.json")
MAILTO = "fahad@crispro.ai"
WORKS = "https://api.openalex.org/works"
AUTHORS = "https://api.openalex.org/authors"

CELLS = [
    ("https://openalex.org/I165143802", "National University of Singapore",
     2018, 49),
    ("https://openalex.org/I161318765", "University of Vienna", 2012, 49),
    ("https://openalex.org/I165143802", "National University of Singapore",
     2005, 49),
    ("https://openalex.org/I67311998", "Leiden University", 2018, 9),
]
HYPER_CUTOFFS = [None, 100, 50, 20]

RESULT = {"started": time.time(), "hyper_cutoffs": HYPER_CUTOFFS}


def put(k, v):
    RESULT[k] = v
    with open(OUT, "w") as fh:
        json.dump(RESULT, fh, indent=1, default=str)
    print("[put] %s" % k, flush=True)


def fetch_works(inst_id, year, thresh, cap_pages=20):
    flt = "institutions.id:%s,publication_year:%d,cited_by_count:>%d" % (
        inst_id, year, thresh)
    sel = "id,doi,title,cited_by_count,authorships"
    cursor, rows, pages = "*", [], 0
    while cursor and pages < cap_pages:
        q = urllib.parse.urlencode({"filter": flt, "select": sel,
                                    "per-page": 200, "cursor": cursor,
                                    "mailto": MAILTO})
        d = net.get_json("%s?%s" % (WORKS, q))
        rows.extend(d.get("results") or [])
        pages += 1
        cursor = (d.get("meta") or {}).get("next_cursor")
    return rows


def dedup(rows):
    seen, out = set(), []
    for w in rows:
        doi = (w.get("doi") or "").lower()
        k = doi.split("<")[0] if doi else (w.get("title") or "").strip().lower()
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        out.append(w)
    return out


def tally(rows, max_authors=None):
    by = {}
    for w in rows:
        aus = w.get("authorships") or []
        if max_authors is not None and len(aus) > max_authors:
            continue
        for a in aus:
            au = a.get("author") or {}
            oid = au.get("orcid")
            if not oid:
                continue
            oid = oid.rsplit("/", 1)[-1]
            rec = by.setdefault(oid, {"orcid": oid,
                                      "name": au.get("display_name"),
                                      "n": 0, "author_counts": []})
            rec["n"] += 1
            rec["author_counts"].append(len(aus))
    return by


def rank(by):
    r = sorted(by.values(), key=lambda x: -x["n"])
    if not r:
        return None, None, 0, 0
    win = r[0]
    tied = [x for x in r if x["n"] == win["n"]]
    run = next((x for x in r if x["n"] < win["n"]), None)
    return win, run, len(tied), (win["n"] - run["n"]) if run else win["n"]


def spearman(a, b):
    n = len(a)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    da = sum((ra[i] - ma) ** 2 for i in range(n)) ** 0.5
    db = sum((rb[i] - mb) ** 2 for i in range(n)) ** 0.5
    return round(num / (da * db), 4) if da and db else None


def author_globals(orcids):
    """Batch-fetch global works_count / cited_by_count for up to 50 ORCIDs."""
    out = {}
    for i in range(0, len(orcids), 50):
        chunk = orcids[i:i + 50]
        q = urllib.parse.urlencode({
            "filter": "orcid:" + "|".join(chunk),
            "select": "id,orcid,display_name,works_count,cited_by_count",
            "per-page": 200, "mailto": MAILTO})
        try:
            d = net.get_json("%s?%s" % (AUTHORS, q))
        except Exception as exc:  # noqa: BLE001
            print("   author fetch err %s" % str(exc)[:80], flush=True)
            continue
        for r in d.get("results") or []:
            oid = (r.get("orcid") or "").rsplit("/", 1)[-1]
            if not oid:
                continue
            prev = out.get(oid)
            # collapse duplicate OpenAlex records per ORCID by max
            if prev is None or (r.get("cited_by_count") or 0) > prev["cited"]:
                out[oid] = {"name": r.get("display_name"),
                            "works": r.get("works_count") or 0,
                            "cited": r.get("cited_by_count") or 0}
    return out


def main():
    cells = {}
    for inst_id, inst, year, thresh in CELLS:
        tag = "%s|%d|>%d" % (inst, year, thresh)
        print("== %s" % tag, flush=True)
        rows = dedup(fetch_works(inst_id, year, thresh))
        cell = {"institution": inst, "year": year, "threshold": thresh,
                "n_works": len(rows)}

        # 1 + 2. hyperauthorship sensitivity
        sweeps = {}
        for cut in HYPER_CUTOFFS:
            by = tally(rows, cut)
            win, run, ntied, margin = rank(by)
            if win is None:
                sweeps[str(cut)] = {"error": "NO_AUTHORS"}
                continue
            ac = sorted(win["author_counts"])
            sweeps[str(cut)] = {
                "max_authors_per_work": cut,
                "n_works_kept": sum(1 for w in rows
                                    if cut is None
                                    or len(w.get("authorships") or []) <= cut),
                "n_orcid_authors": len(by),
                "winner_name": win["name"], "winner_orcid": win["orcid"],
                "winner_n_works": win["n"],
                "winner_work_author_counts_median": ac[len(ac) // 2],
                "winner_work_author_counts_max": ac[-1],
                "winner_frac_works_over_50_authors": round(
                    sum(1 for x in ac if x > 50) / len(ac), 4),
                "runner_up_name": run["name"] if run else None,
                "margin": margin, "n_tied_at_max": ntied,
                "k_robustness": max(0, margin - 1),
            }
            print("   cut=%-5s authors=%-6s winner=%-26s n=%-4s margin=%-3s "
                  "med_auth=%s frac>50auth=%s"
                  % (cut, len(by), (win["name"] or "")[:26], win["n"], margin,
                     sweeps[str(cut)]["winner_work_author_counts_median"],
                     sweeps[str(cut)]["winner_frac_works_over_50_authors"]),
                  flush=True)
        cell["hyperauthorship_sweep"] = sweeps
        base = sweeps.get("None", {})
        strict = sweeps.get("50", {})
        cell["winner_survives_hyperauthor_filter"] = (
            base.get("winner_orcid") == strict.get("winner_orcid"))

        # 3. is the key reachable by a server-side author sort?
        by = tally(rows, None)
        top = sorted(by.values(), key=lambda x: -x["n"])[:50]
        gl = author_globals([t["orcid"] for t in top])
        paired = [(t["n"], gl[t["orcid"]]["works"], gl[t["orcid"]]["cited"],
                   t["orcid"], t["name"])
                  for t in top if t["orcid"] in gl]
        cell["n_top_authors_resolved"] = len(paired)
        if len(paired) >= 3:
            kn = [p[0] for p in paired]
            cell["rho_count_vs_global_works_count"] = spearman(
                kn, [p[1] for p in paired])
            cell["rho_count_vs_global_cited_by_count"] = spearman(
                kn, [p[2] for p in paired])
            gw = max(paired, key=lambda p: p[1])
            gc = max(paired, key=lambda p: p[2])
            cell["global_works_count_winner"] = gw[4]
            cell["global_cited_by_count_winner"] = gc[4]
            cell["shortcut_global_works_gives_same_answer"] = (
                gw[3] == base.get("winner_orcid"))
            cell["shortcut_global_cited_gives_same_answer"] = (
                gc[3] == base.get("winner_orcid"))
            print("   rho_vs_works=%s rho_vs_cited=%s shortcut_works=%s "
                  "shortcut_cited=%s"
                  % (cell["rho_count_vs_global_works_count"],
                     cell["rho_count_vs_global_cited_by_count"],
                     cell["shortcut_global_works_gives_same_answer"],
                     cell["shortcut_global_cited_gives_same_answer"]),
                  flush=True)
        cells[tag] = cell
        put("cells", cells)

    surv = {k: v for k, v in cells.items()
            if v.get("winner_survives_hyperauthor_filter")
            and not v.get("shortcut_global_works_gives_same_answer")
            and not v.get("shortcut_global_cited_gives_same_answer")}
    best = None
    if surv:
        best = max(surv.items(),
                   key=lambda kv: kv[1]["hyperauthorship_sweep"]["50"][
                       "k_robustness"])
    put("VERDICT", {
        "n_cells": len(cells),
        "n_surviving": len(surv),
        "surviving": sorted(surv.keys()),
        "best_cell": best[0] if best else None,
        "best_winner": (best[1]["hyperauthorship_sweep"]["50"]["winner_name"]
                        if best else None),
        "best_orcid": (best[1]["hyperauthorship_sweep"]["50"]["winner_orcid"]
                       if best else None),
        "best_k_robustness": (
            best[1]["hyperauthorship_sweep"]["50"]["k_robustness"]
            if best else None),
        "conclusion": ("count key SURVIVES interrogation" if surv else
                       "count key FAILS interrogation"),
    })
    put("finished", time.time())


if __name__ == "__main__":
    main()
