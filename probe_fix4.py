#!/usr/bin/env python3
"""Round-4 legal probes.

The per-day framing is structurally broken: SCOTUS order-list days carry
hundreds of cert denials (1994-01-10 -> count=826), and the days small enough
to enumerate in one page are order pages whose U.S. Reports cites are not
carried by Cornell LII. So probe a different, genuinely closed collection: a
single volume of United States Reports.
"""
import json
import re

import net

OUT = "probe_fix4.json"
res = {}


def save():
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=2, default=str)


# 1. Cornell LII volume index
for vol in (504, 505):
    k = f"lii_vol_{vol}"
    try:
        html = net.fetch(f"https://www.law.cornell.edu/supremecourt/text/{vol}",
                         timeout=90, attempts=3)
        links = re.findall(r'href="(/supremecourt/text/%d/(\d+))"[^>]*>([^<]{3,120})<' % vol, html)
        res[k] = {"status": "ok", "bytes": len(html), "n_links": len(links),
                  "sample": [{"page": int(p), "name": n.strip()} for _, p, n in links[:10]],
                  "title": (re.search(r"<title>(.*?)</title>", html, re.S) or ["", ""])[1].strip()[:120]}
    except Exception as e:
        res[k] = {"status": "err", "error": f"{type(e).__name__}: {e}"}
    print(k, json.dumps(res[k])[:400])
    save()

# 2. Justia volume index
for vol in (504,):
    k = f"justia_vol_{vol}"
    try:
        html = net.fetch(f"https://supreme.justia.com/cases/federal/us/{vol}/",
                         timeout=90, attempts=3)
        links = re.findall(r'href="(/cases/federal/us/%d/(\d+)/)"[^>]*>\s*([^<]{3,160})' % vol, html)
        res[k] = {"status": "ok", "bytes": len(html), "n_links": len(links),
                  "sample": [{"page": int(p), "name": n.strip()} for _, p, n in links[:10]]}
    except Exception as e:
        res[k] = {"status": "err", "error": f"{type(e).__name__}: {e}"}
    print(k, json.dumps(res[k])[:400])
    save()

# 3. CourtListener: confirm a single case by citation lookup (1 request, no paging)
try:
    js = net.get_json(
        "https://www.courtlistener.com/api/rest/v4/search/?q=%22504%20U.S.%20555%22"
        "&type=o&court=scotus", timeout=90, attempts=3, base_sleep=20.0)
    rows = js.get("results", [])
    res["cl_cite_lookup"] = {"count": js.get("count"), "n": len(rows),
                             "names": sorted({r.get("caseName") for r in rows})[:8],
                             "cites": sorted({c for r in rows for c in (r.get("citation") or [])})[:8]}
except Exception as e:
    res["cl_cite_lookup"] = {"error": f"{type(e).__name__}: {e}"}
print("cl_cite_lookup", json.dumps(res["cl_cite_lookup"])[:400])
save()
print("wrote", OUT)
