"""yearforensics2.py -- finish the 5 cohorts that died on OpenAlex HTTP 429.

yearforensics measured 4 of 9 cohorts and found a perfect rank correlation
(rho = 1.0) between my MIN_REL_SEPARATION gate and the share of the winning
year's citations carried by a SINGLE paper:

    Leiden   sep 0.810 -> share 0.869   (LINCS)
    Technion sep 0.582 -> share 0.813   (high-harmonic generation)
    UBC      sep 0.552 -> share 0.793   (Puterman's MDP textbook)
    Seoul    sep 0.507 -> share 0.581   (OPGL/RANKL Nature paper)

The remaining 5 are the LOW-separation cohorts, and they are the ones the
correlation predicts should be least concentrated -- i.e. the only candidates
that could survive a concentration ceiling. They failed only because OpenAlex
throttled the works endpoint after ~4 authors.

Fixes vs the first pass: join the polite pool with mailto=, pace at 6 s, and back
off up to 5 times on 429 rather than 3. Results merge into a separate file so the
first pass is not clobbered.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net  # noqa: E402

OUT = "/workspace/seal_deploy/yearforensics2.json"
SRC = "/workspace/seal_deploy/yearkey.json"
PRIOR = "/workspace/seal_deploy/yearforensics.json"
MAILTO = "fahad@crispro.ai"
CONCENTRATION_LIMIT = 0.50
PACE = 6.0

STATE = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "cohorts": {}}


def put(k, v):
    STATE["cohorts"][k] = v
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(STATE, fh, indent=1, default=str)
    os.replace(tmp, OUT)
    print("[put] %s" % k, flush=True)


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def dedupe_doi(doi):
    d = (doi or "").lower()
    return re.sub(r"\)\d+\.\d+\.co;2-.$", ")", d)


def fetch_works(oid, year):
    u = ("https://api.openalex.org/works?filter=authorships.author.orcid:%s,"
         "publication_year:%d&select=id,doi,title,cited_by_count,authorships"
         "&per-page=100&mailto=%s" % (oid, year, MAILTO))
    last = None
    for attempt in range(5):
        try:
            return json.loads(net.fetch(u, timeout=120, attempts=1,
                                        use_cache=(attempt == 0)))
        except Exception as e:
            last = e
            wait = 8.0 * (attempt + 1)
            print("    429/err attempt %d, sleeping %.0fs" % (attempt + 1, wait),
                  flush=True)
            time.sleep(wait)
    raise last


def analyse(inst, year, oid, name, year_value):
    js = fetch_works(oid, year)
    works = js.get("results") or []
    rec = {"institution": inst, "year": year, "winner_orcid": oid,
           "winner_name": name, "year_value_from_counts_by_year": year_value,
           "n_works_in_year": len(works)}
    if not works:
        rec["verdict"] = "NO_WORKS_RETURNED"
        return rec
    works.sort(key=lambda w: -(w.get("cited_by_count") or 0))
    total = sum(w.get("cited_by_count") or 0 for w in works)
    top = works[0]
    tc = top.get("cited_by_count") or 0

    by_title, by_doi = {}, {}
    for w in works:
        by_title.setdefault(norm_title(w.get("title")), []).append(w)
        by_doi.setdefault(dedupe_doi(w.get("doi")), []).append(w)
    dup_title = {k: v for k, v in by_title.items() if len(v) > 1}
    dup_doi = {k: v for k, v in by_doi.items() if len(v) > 1}
    dup_inflation = 0
    for grp in dup_title.values():
        cs = sorted((w.get("cited_by_count") or 0) for w in grp)
        dup_inflation += sum(cs[:-1])

    auths = top.get("authorships") or []
    co = [{"name": (a.get("author") or {}).get("display_name"),
           "orcid": (a.get("author") or {}).get("orcid")} for a in auths]
    n_co_orcid = sum(1 for c in co if c["orcid"])

    rec.update({
        "sum_of_year_work_citations": total,
        "top_work_title": (top.get("title") or "")[:110],
        "top_work_doi": top.get("doi"),
        "top_work_citations": tc,
        "top_work_share_of_year": round(tc / float(total), 4) if total else None,
        "top_work_n_authors": len(auths),
        "top_work_n_authors_with_orcid": n_co_orcid,
        "top_work_coauthors": co[:12],
        "n_duplicate_title_groups": len(dup_title),
        "n_duplicate_doi_groups": len(dup_doi),
        "duplicate_inflation_citations": dup_inflation,
        "deduped_year_total": total - dup_inflation,
        "duplicate_inflation_frac": (round(dup_inflation / float(total), 4)
                                     if total else None),
    })
    flags = []
    if rec["top_work_share_of_year"] and rec["top_work_share_of_year"] >= CONCENTRATION_LIMIT:
        flags.append("SINGLE_WORK_DOMINATES")
    if dup_inflation > 0:
        flags.append("DUPLICATE_RECORDS_INFLATE")
    if len(auths) > 1 and n_co_orcid > 1:
        flags.append("COAUTHOR_HINGE")
    rec["flags"] = flags
    rec["verdict"] = "FRAGILE" if flags else "ROBUST"
    return rec


def main():
    d = json.load(open(SRC))
    prior = json.load(open(PRIOR))["cohorts"]
    todo = [r for r in d["summary"]["recommended_seed_order"]
            if (prior.get(r["institution"]) or {}).get("verdict") == "ERROR"]
    print("cohorts to finish: %d" % len(todo), flush=True)

    results = []
    for r in todo:
        inst, year = r["institution"], r["year"]
        by = d["cohorts"][inst]["best_year"]
        try:
            out = analyse(inst, year, by["winner_orcid"], by["winner_name"],
                          by["winner_value"])
        except Exception as e:
            out = {"institution": inst, "year": year, "error": str(e)[:200],
                   "verdict": "ERROR"}
        out["yearkey_rel_separation"] = r["rel"]
        out["yearkey_rho_vs_total"] = r["rho_vs_total"]
        put(inst, out)
        results.append(out)
        print("  %-30s %-8s sep=%-8s share=%-7s dup=%-7s %s" % (
            inst[:30], out.get("verdict"), out.get("yearkey_rel_separation"),
            out.get("top_work_share_of_year"), out.get("duplicate_inflation_frac"),
            ",".join(out.get("flags", []))), flush=True)
        time.sleep(PACE)

    # combine both passes and re-test the inversion on the full set of 9
    allrows = []
    for src in (prior, STATE["cohorts"]):
        for k, v in src.items():
            if isinstance(v, dict) and v.get("institution") and v.get("verdict") != "ERROR":
                allrows.append(v)
    seen, uniq = set(), []
    for v in allrows:
        if v["institution"] in seen:
            continue
        seen.add(v["institution"])
        uniq.append(v)

    pairs = [(v["yearkey_rel_separation"], v["top_work_share_of_year"])
             for v in uniq if v.get("top_work_share_of_year") is not None]

    def spear(xs, ys):
        n = len(xs)
        if n < 3:
            return None

        def rank(v):
            o = sorted(range(len(v)), key=lambda i: v[i])
            rr = [0.0] * len(v)
            for p, i in enumerate(o):
                rr[i] = p + 1.0
            return rr
        rx, ry = rank(xs), rank(ys)
        mx, my = sum(rx) / n, sum(ry) / n
        num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
        dx = sum((a - mx) ** 2 for a in rx) ** .5
        dy = sum((b - my) ** 2 for b in ry) ** .5
        return round(num / (dx * dy), 4) if dx and dy else None

    rho = spear([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 3 else None
    robust = [v["institution"] for v in uniq if v.get("verdict") == "ROBUST"]
    passes_ceiling = [(v["institution"], v["yearkey_rel_separation"],
                       v["top_work_share_of_year"])
                      for v in uniq
                      if v.get("top_work_share_of_year") is not None
                      and v["top_work_share_of_year"] < CONCENTRATION_LIMIT]
    passes_ceiling.sort(key=lambda t: t[2])

    put("VERDICT_COMBINED", {
        "n_measured": len(uniq),
        "rho_separation_vs_concentration_full_set": rho,
        "separation_gate_is_inverted": (rho is not None and rho > 0.5),
        "n_robust": len(robust),
        "robust_cohorts": robust,
        "concentration_ceiling": CONCENTRATION_LIMIT,
        "cohorts_below_ceiling": passes_ceiling,
        "n_below_ceiling": len(passes_ceiling),
        "recommended_cohort_for_gen_v5": (passes_ceiling[0][0]
                                          if passes_ceiling else None),
        "corrected_gate": (
            "Replace MIN_REL_SEPARATION-as-maximand with a two-sided band: require "
            "a unique argmax and rel separation >= 0.05, but ALSO require the single "
            "most-cited work of the winning year to carry < %.2f of that year's "
            "citations. Rank candidate cohorts by ASCENDING separation, not "
            "descending -- the highest-separation cohort is the most shortcuttable, "
            "because a lone landmark paper decides the winner and a solver with "
            "domain knowledge reaches it without enumerating." % CONCENTRATION_LIMIT),
        "residual_risk": (
            "Even below the ceiling, the winner is decided by which co-authors of "
            "the top works happen to hold an ORCID and happen to be labelled with "
            "this institution in last_known_institutions. That hinge is a property "
            "of OpenAlex metadata, not of the scholarship, and it is not stationary."),
    })
    STATE["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    put("_done", True)
    print(json.dumps(STATE["cohorts"]["VERDICT_COMBINED"], indent=1), flush=True)


if __name__ == "__main__":
    main()
