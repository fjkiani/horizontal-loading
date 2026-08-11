#!/usr/bin/env python3
"""trunc.py -- is the ranked population the WHOLE collection, or one page of it?

Wiring the full trap battery into the live /api/generate path surfaced this.
The health seed {"condition": "multiple sclerosis"} produced n_base = 200 --
exactly the pageSize in the request URL -- and every one of the twelve tests
passed it. n_base landing exactly on the page limit is the fingerprint of a
truncated collection, and a truncated collection breaks the trap's premise, not
one of its tests: the prompt says "among all completed phase 3 studies of X",
and the generator ranked an arbitrary first page of them. The argmax of a
prefix is not the argmax of the set.

The baked ALS seed never showed it because ALS has 51 such trials, so its first
page IS the collection. The API's seed rotation is what made the defect
reachable.

This measures the extent across every LIVE generator's collection request: the
declared page cap, the number of rows actually ranked, and -- where the service
reports it -- the true size of the collection. Checkpoints after every probe.

No generators are run here. Only their collection endpoints are called, so this
is cheap and cannot mutate LAST_RANK.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net  # noqa: E402

OUT = os.path.join(_HERE, "trunc.json")

# (label, url, page_cap, how to read the served rows, how to read the true total)
CT = "https://clinicaltrials.gov/api/v2/studies?pageSize=200&query.cond={}"\
     "&filter.overallStatus=COMPLETED&aggFilters=phase:3&countTotal=true"

HEALTH_SEEDS = ["amyotrophic+lateral+sclerosis", "multiple+sclerosis",
                "idiopathic+pulmonary+fibrosis", "sickle+cell+disease",
                "Duchenne+muscular+dystrophy", "cystic+fibrosis"]

ARXIV = ("http://export.arxiv.org/api/query?search_query=cat:{}"
         "+AND+submittedDate:[{}0000+TO+{}2359]&max_results=200"
         "&start=0&sortBy=submittedDate&sortOrder=ascending")

ARXIV_PROBES = [("cs.CR", "20230214"), ("math.PR", "20230516"),
                ("cs.LG", "20220314"), ("astro-ph.GA", "20230912")]


def load():
    if os.path.exists(OUT):
        try:
            return json.load(open(OUT))
        except Exception:
            pass
    return {"probes": []}


def save(state):
    state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = OUT + ".tmp"
    json.dump(state, open(tmp, "w"), indent=2, default=str)
    os.replace(tmp, OUT)


def probe_health(state, done):
    for cond in HEALTH_SEEDS:
        key = "health:" + cond
        if key in done:
            print("skip %s" % key, flush=True)
            continue
        rec = {"key": key, "category": "health and medicine",
               "collection": "ClinicalTrials.gov v2 studies",
               "page_cap": 200, "seed": cond.replace("+", " ")}
        try:
            js = net.get_json(CT.format(cond), timeout=120)
            served = len(js.get("studies") or [])
            total = js.get("totalCount")
            rec.update({
                "n_served": served, "n_true": total,
                "next_page_token": bool(js.get("nextPageToken")),
                "truncated": (total is not None and served < total),
                "fraction_seen": (round(served / total, 4)
                                  if total else None),
            })
        except Exception as e:  # noqa: BLE001
            rec["error"] = "%s: %s" % (type(e).__name__, e)
        state["probes"].append(rec)
        save(state)
        print("  %-46s served=%-5s true=%-6s truncated=%s frac=%s"
              % (key, rec.get("n_served"), rec.get("n_true"),
                 rec.get("truncated"), rec.get("fraction_seen")), flush=True)


def probe_arxiv(state, done):
    for cat, day in ARXIV_PROBES:
        key = "arxiv:%s:%s" % (cat, day)
        if key in done:
            print("skip %s" % key, flush=True)
            continue
        rec = {"key": key, "category": "science and technology",
               "collection": "arXiv Atom query", "page_cap": 200,
               "seed": "%s %s" % (cat, day)}
        try:
            raw = net.fetch(ARXIV.format(cat, day, day), timeout=120)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            m = re.search(r"<opensearch:totalResults[^>]*>(\d+)<", raw)
            total = int(m.group(1)) if m else None
            served = len(re.findall(r"<entry>", raw))
            rec.update({
                "n_served": served, "n_true": total,
                "next_page_token": None,
                "truncated": (total is not None and served < total),
                "fraction_seen": (round(served / total, 4) if total else None),
            })
        except Exception as e:  # noqa: BLE001
            rec["error"] = "%s: %s" % (type(e).__name__, e)
        state["probes"].append(rec)
        save(state)
        print("  %-46s served=%-5s true=%-6s truncated=%s frac=%s"
              % (key, rec.get("n_served"), rec.get("n_true"),
                 rec.get("truncated"), rec.get("fraction_seen")), flush=True)


def main():
    state = load()
    done = {p["key"] for p in state["probes"]}
    print("== ClinicalTrials.gov (health): pageSize=200, no pageToken follow")
    probe_health(state, done)
    print("\n== arXiv (science and technology): max_results=200")
    probe_arxiv(state, done)

    trunc = [p for p in state["probes"] if p.get("truncated")]
    exact = [p for p in state["probes"]
             if p.get("n_served") == p.get("page_cap")]
    state["summary"] = {
        "n_probes": len(state["probes"]),
        "n_truncated": len(trunc),
        "truncated_keys": [p["key"] for p in trunc],
        "n_served_exactly_at_cap": len(exact),
        "worst_fraction_seen": min([p["fraction_seen"] for p in state["probes"]
                                    if p.get("fraction_seen") is not None]
                                   or [None]),
    }
    save(state)
    print("\n== summary: %d of %d probes truncated; worst coverage %s"
          % (len(trunc), len(state["probes"]),
             state["summary"]["worst_fraction_seen"]))
    for p in trunc:
        print("   %-46s %s of %s (%.1f%%)"
              % (p["key"], p["n_served"], p["n_true"],
                 100.0 * p["fraction_seen"]))
    print("wrote %s" % OUT)


if __name__ == "__main__":
    sys.exit(main() or 0)
