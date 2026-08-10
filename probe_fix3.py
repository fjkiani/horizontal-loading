#!/usr/bin/env python3
"""Round-3 probes: why Retrosheet missed Raines, and how to find a SCOTUS
hand-down day that is small enough to enumerate in one page."""
import csv
import io
import json
import re
import sys

import net

OUT = "probe_fix3.json"
res = {}


def save():
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=2, default=str)


# --------------------------------------------------------------------------
# 1. Retrosheet BIOFILE -- find Raines, inspect the real column values
# --------------------------------------------------------------------------
try:
    txt = net.fetch("https://www.retrosheet.org/BIOFILE.TXT", timeout=180).decode(
        "utf-8", "replace")
    rdr = csv.DictReader(io.StringIO(txt))
    fns = rdr.fieldnames
    hits, n = [], 0
    for row in rdr:
        n += 1
        last = (row.get("LAST") or "").strip().lower()
        if last in ("raines", "corsi", "darwin", "davis"):
            hits.append({k: row.get(k) for k in
                         ("PLAYERID", "LAST", "FIRST", "BIRTHDATE", "BIRTH CITY",
                          "BIRTH STATE", "BIRTH COUNTRY") if k in row})
    res["retro"] = {"fieldnames": fns, "n_rows": n, "n_hits": len(hits),
                    "hits": hits[:40]}
except Exception as e:
    res["retro"] = {"error": f"{type(e).__name__}: {e}"}
save()
print("retro fields:", res["retro"].get("fieldnames"))
print("retro rows:", res["retro"].get("n_rows"), "hits:", res["retro"].get("n_hits"))
for h in (res["retro"].get("hits") or [])[:12]:
    print("   ", h)

# --------------------------------------------------------------------------
# 2. CourtListener: scan a month's first page to learn which days exist,
#    then size each day.
# --------------------------------------------------------------------------
CL = "https://www.courtlistener.com/api/rest/v4/search/"


def cl(params):
    url = CL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    js = net.get_json(url, timeout=90, attempts=3, base_sleep=20.0)
    return js, url


months = [("1992-03-01", "1992-03-31"), ("1992-06-01", "1992-06-30"),
          ("1994-01-01", "1994-01-31")]
res["cl_months"] = {}
for a, b in months:
    try:
        js, url = cl({"q": "", "type": "o", "court": "scotus",
                      "filed_after": a, "filed_before": b,
                      "order_by": "dateFiled+asc"})
        rows = js.get("results", [])
        days = {}
        for r in rows:
            d = (r.get("dateFiled") or "")[:10]
            days[d] = days.get(d, 0) + 1
        res["cl_months"][a] = {"count": js.get("count"), "n_page": len(rows),
                               "days_on_page": days, "url": url}
        print(f"month {a}: count={js.get('count')} page={len(rows)} days={days}")
    except Exception as e:
        res["cl_months"][a] = {"error": f"{type(e).__name__}: {e}"}
        print(f"month {a}: ERROR {e}")
    save()

# size the individual days seen
res["cl_days"] = {}
seen = []
for a, m in res["cl_months"].items():
    for d in (m.get("days_on_page") or {}):
        if d:
            seen.append(d)
for d in sorted(set(seen))[:10]:
    try:
        js, url = cl({"q": "", "type": "o", "court": "scotus",
                      "filed_after": d, "filed_before": d})
        rows = js.get("results", [])
        names = sorted({(r.get("caseName") or "")[:60] for r in rows})
        cites = [c for r in rows for c in (r.get("citation") or [])
                 if re.match(r"^\d+ U\.S\. \d+$", c)]
        res["cl_days"][d] = {"count": js.get("count"), "n": len(rows),
                             "complete": js.get("count") == len(rows),
                             "n_distinct_names": len(names),
                             "names": names[:12], "us_cites": sorted(set(cites))[:12]}
        print(f"day {d}: count={js.get('count')} n={len(rows)} "
              f"complete={js.get('count') == len(rows)} names={len(names)}")
    except Exception as e:
        res["cl_days"][d] = {"error": f"{type(e).__name__}: {e}"}
        print(f"day {d}: ERROR {e}")
    save()

print("\nwrote", OUT)
