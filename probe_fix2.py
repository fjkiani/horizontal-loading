#!/usr/bin/env python3
"""probe_fix2.py — resolve the two remaining category failures.

legal : is the CourtListener 429 a hard cap or my own request rate? and does a
        day-scoped query return a top-level count so one page proves complete
        enumeration?
sports: Wikidata missed 'Tim Raines Sr.' (label has no suffix) and TheSportsDB
        has no MLB birthplaces. Test suffix-stripped lookup and candidate third
        operators.
"""
import json
import os
import time

import net

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_fix2.json")
S = {}


def rec(k, **kw):
    S[k] = kw
    with open(OUT + ".tmp", "w") as fh:
        json.dump(S, fh, indent=2, default=str)
    os.replace(OUT + ".tmp", OUT)
    print(f"[{k}] " + json.dumps(kw, default=str)[:500], flush=True)


def cl_day(day):
    url = ("https://www.courtlistener.com/api/rest/v4/search/?type=o&court=scotus"
           f"&filed_after={day}&filed_before={day}&order_by=dateFiled%20asc")
    try:
        js = net.get_json(url, timeout=120, attempts=4, base_sleep=20.0)
        res = js.get("results", [])
        rec(f"cl_day_{day}", top_keys=sorted(js.keys()), count=js.get("count"),
            n=len(res), has_next=bool(js.get("next")),
            cases=[{"n": r.get("caseName"), "c": r.get("citation")} for r in res[:25]])
    except Exception as e:  # noqa: BLE001
        rec(f"cl_day_{day}", error=f"{type(e).__name__}: {str(e)[-160:]}")


def wd(name):
    try:
        hits = net.wikidata_search(name).get("search", [])
        rec(f"wd_{name}", n=len(hits),
            hits=[{"id": h["id"], "label": h.get("label"),
                   "desc": h.get("description")} for h in hits[:4]])
    except Exception as e:  # noqa: BLE001
        rec(f"wd_{name}", error=f"{type(e).__name__}: {e}")


def sportsdb(name):
    try:
        js = net.get_json("https://www.thesportsdb.com/api/v1/json/3/searchplayers.php?p="
                          + name.replace(" ", "%20"), timeout=60)
        pl = js.get("player") or []
        rec(f"sdb_{name}", n=len(pl),
            rows=[{"name": p.get("strPlayer"), "sport": p.get("strSport"),
                   "birth": p.get("strBirthLocation"), "dob": p.get("dateBorn")}
                  for p in pl[:4]])
    except Exception as e:  # noqa: BLE001
        rec(f"sdb_{name}", error=f"{type(e).__name__}: {e}")


def retrosheet():
    for u in ("https://www.retrosheet.org/BIOFILE.TXT",
              "https://raw.githubusercontent.com/chadwickbureau/retrosheet/master/reference/biofile.csv",
              "https://raw.githubusercontent.com/chadwickbureau/register/master/data/people-0.csv"):
        try:
            txt = net.fetch(u, timeout=180)
            head = txt.splitlines()[:2]
            rec("retro_" + u.rsplit("/", 1)[-1], ok=True, bytes=len(txt), head=head)
        except Exception as e:  # noqa: BLE001
            rec("retro_" + u.rsplit("/", 1)[-1], error=f"{type(e).__name__}: {str(e)[-120:]}")


def nominatim(q):
    try:
        js = net.get_json(
            "https://nominatim.openstreetmap.org/search?format=json&limit=3&q="
            + q.replace(" ", "%20").replace(",", "%2C"), timeout=60)
        rec(f"nom_{q}", n=len(js),
            rows=[{"name": r.get("display_name"), "type": r.get("type")} for r in js[:3]])
    except Exception as e:  # noqa: BLE001
        rec(f"nom_{q}", error=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    print("waiting 60s to let any CourtListener throttle window drain", flush=True)
    time.sleep(60)
    cl_day("1992-01-10")
    time.sleep(5)
    cl_day("1993-06-28")
    wd("Tim Raines")
    wd("Chili Davis")
    sportsdb("Tim Raines")
    sportsdb("Danny Darwin")
    retrosheet()
    nominatim("Sanford, Florida, United States")
    print("done ->", OUT)
