#!/usr/bin/env python3
"""probe_fix_shapes.py — probe the exact API shapes needed to repair the 5 failed
category generators. Checkpoints to probe_fix_shapes.json after every probe.
"""
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET

import net

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_fix_shapes.json")
STATE = {}


def save():
    with open(OUT + ".tmp", "w") as fh:
        json.dump(STATE, fh, indent=2, default=str)
    os.replace(OUT + ".tmp", OUT)


def rec(key, **kw):
    STATE[key] = kw
    save()
    print(f"[{key}] " + json.dumps(kw, default=str)[:600], flush=True)


# ---------------------------------------------------------------- arXiv
def probe_arxiv():
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for day in ("20240115", "20240206", "20240311"):
        url = ("http://export.arxiv.org/api/query?search_query="
               f"cat:q-bio.GN+AND+submittedDate:[{day}0000+TO+{day}2359]"
               "&max_results=200&start=0")
        try:
            xml = net.fetch(url, timeout=90)
            root = ET.fromstring(xml)
            ents = root.findall("a:entry", ns)
            rows = []
            for e in ents:
                aid = e.find("a:id", ns).text.rsplit("/", 1)[-1]
                nau = len(e.findall("a:author", ns))
                ttl = " ".join((e.find("a:title", ns).text or "").split())
                rows.append({"id": aid, "nau": nau, "title": ttl[:70]})
            counts = sorted((r["nau"] for r in rows), reverse=True)
            rec(f"arxiv_{day}", n=len(rows), top_author_counts=counts[:6],
                unique_max=(len(counts) > 1 and counts[0] != counts[1]),
                winner=max(rows, key=lambda r: r["nau"]) if rows else None)
        except Exception as e:  # noqa: BLE001
            rec(f"arxiv_{day}", error=f"{type(e).__name__}: {e}")


def probe_datacite_arxiv():
    for aid in ("2401.01234", "2402.00001"):
        try:
            js = net.get_json(f"https://api.datacite.org/dois/10.48550/arxiv.{aid}", timeout=60)
            t = (((js.get("data") or {}).get("attributes") or {}).get("titles") or [{}])
            rec(f"datacite_{aid}", ok=True, title=str(t[0].get("title"))[:80])
        except Exception as e:  # noqa: BLE001
            rec(f"datacite_{aid}", error=f"{type(e).__name__}: {e}")


def probe_openalex_arxiv():
    try:
        js = net.get_json(
            "https://api.openalex.org/works/doi:10.48550/arXiv.2401.01234", timeout=60)
        rec("openalex_arxiv", ok=True, title=str(js.get("title"))[:80])
    except Exception as e:  # noqa: BLE001
        rec("openalex_arxiv", error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- Cornell LII
def probe_lii():
    for vol, page in ((504, 555), (505, 1), (498, 19)):
        try:
            html = net.fetch(
                f"https://www.law.cornell.edu/supremecourt/text/{vol}/{page}", timeout=60)
            m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
            rec(f"lii_{vol}_{page}", ok=True, title=" ".join((m.group(1) if m else "")[:120].split()))
        except Exception as e:  # noqa: BLE001
            rec(f"lii_{vol}_{page}", error=f"{type(e).__name__}: {e}")


def probe_courtlistener_days():
    url = ("https://www.courtlistener.com/api/rest/v4/search/?type=o"
           "&court=scotus&filed_after=1992-01-01&filed_before=1992-12-31"
           "&order_by=dateFiled%20asc&page_size=100")
    try:
        js = net.get_json(url, timeout=120)
        res = js.get("results", [])
        by = {}
        for r in res:
            d = r.get("dateFiled")
            by.setdefault(d, []).append(r)
        sample = res[0] if res else {}
        rec("courtlistener", n=len(res), n_dates=len(by),
            dates_with_ge4=[d for d, v in by.items() if len(v) >= 4][:8],
            sample_keys=sorted(sample.keys()),
            sample_citation=sample.get("citation"),
            sample_caseName=sample.get("caseName"))
    except Exception as e:  # noqa: BLE001
        rec("courtlistener", error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- MLB roster
def probe_mlb_roster():
    try:
        js = net.get_json(
            "https://statsapi.mlb.com/api/v1/teams/147/roster?season=1998"
            "&rosterType=fullSeason", timeout=90)
        roster = js.get("roster", [])
        ids = ",".join(str(p["person"]["id"]) for p in roster[:60])
        pj = net.get_json(
            f"https://statsapi.mlb.com/api/v1/people?personIds={ids}", timeout=90)
        people = pj.get("people", [])
        dated = [p for p in people if p.get("birthDate")]
        dated.sort(key=lambda p: p["birthDate"])
        rec("mlb_roster", n_roster=len(roster), n_people=len(people), n_dated=len(dated),
            oldest=[{k: p.get(k) for k in
                     ("fullName", "birthDate", "birthCity", "birthCountry", "id")}
                    for p in dated[:3]])
    except Exception as e:  # noqa: BLE001
        rec("mlb_roster", error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- IMDb bulk
def probe_imdb_bulk():
    t0 = time.time()
    try:
        n = 0
        hits = []
        for line in net.get_gzip_lines("https://datasets.imdbws.com/title.basics.tsv.gz",
                                       timeout=600):
            n += 1
            if n == 1:
                header = line.split("\t")
                continue
            f = line.split("\t")
            if len(f) < 9:
                continue
            if f[1] == "tvSeries" and f[5] == "1998" and "Sci-Fi" in f[8] and f[7] != r"\N":
                hits.append({"tconst": f[0], "title": f[2], "runtime": int(f[7])})
        hits.sort(key=lambda h: -h["runtime"])
        rec("imdb_bulk", lines=n, header=header, n_hits=len(hits),
            top=hits[:5], secs=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001
        rec("imdb_bulk", error=f"{type(e).__name__}: {e}", secs=round(time.time() - t0, 1))


def probe_tvmaze_lookup():
    for imdb in ("tt0944947", "tt0813715"):
        try:
            js = net.get_json(f"https://api.tvmaze.com/lookup/shows?imdb={imdb}", timeout=60)
            rec(f"tvmaze_{imdb}", ok=True, name=js.get("name"), premiered=js.get("premiered"))
        except Exception as e:  # noqa: BLE001
            rec(f"tvmaze_{imdb}", error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- Steam pool
def probe_steam_pool():
    pool = [70, 220, 400, 620, 570, 730, 105600, 292030, 271590, 377160,
            578080, 1091500, 3830, 6910, 22380, 8930, 250900, 39210]
    rows = []
    for a in pool:
        try:
            js = net.get_json(
                f"https://store.steampowered.com/api/appdetails?appids={a}", timeout=60)
            d = (js.get(str(a)) or {})
            if not d.get("success"):
                rows.append({"appid": a, "ok": False})
                continue
            data = d.get("data", {})
            rows.append({"appid": a, "ok": True, "name": data.get("name"),
                         "released": (data.get("release_date") or {}).get("date"),
                         "dev": (data.get("developers") or [None])[0]})
        except Exception as e:  # noqa: BLE001
            rows.append({"appid": a, "ok": False, "err": str(e)[:80]})
        time.sleep(0.6)
    rec("steam_pool", rows=rows)


if __name__ == "__main__":
    probe_arxiv()
    probe_datacite_arxiv()
    probe_openalex_arxiv()
    probe_lii()
    probe_courtlistener_days()
    probe_mlb_roster()
    probe_tvmaze_lookup()
    probe_steam_pool()
    probe_imdb_bulk()
    print("done ->", OUT)
