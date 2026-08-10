"""Fourth witness pass. Close probe_t5c's measurement gaps and test the
remaining hypotheses for the categories that are still singly witnessed or
unwitnessed under the hardened R3c rule.

What probe_t5c established, and what it did not
-----------------------------------------------
politics    130 granules exist in FR-2003-03-28 but the probe asked for
            pageSize=100 at offset 0, so 30 granules were never inspected.
            "No hit" was a measurement of 77% of the issue, not of the issue.
            Re-run with full paging, open the presidential granules, and test
            whether Wikidata carries the order independently.
tv          TVmaze resolved 5 of 12 candidate series. The video-games lesson
            says to check operator agreement before adopting a field: IMDb
            prints 195 minutes for Micawber and TVmaze prints 50, because
            IMDb records the miniseries total and TVmaze the episode length.
            So the runtime is NOT claimed as agreed. The narrower witness
            claim -- TVmaze independently binds the same IMDb identifier to
            the same title -- is what gets measured here, alongside Wikidata
            P345 as a possible third operator.
shopping    The only seed with an independently carried winner, en:crisps /
            united-kingdom, was ranked over a 400-product prefix of a
            1982-product category. Ranking a prefix and calling it the
            category maximum is a false claim. Enumerate the whole category,
            recompute the argmax, and re-test the true winner.
celebrities lobid-GND is run by the hbz library service centre, unrelated to
            Wikimedia and to Nobel Prize Outreach, and its authority records
            carry placeOfBirth. VIAF returned 405; GND has a working API.
history     Same GND test for the award year, which is expected to fail
            because award years are not an authority-file field.
finance     No operator but the Treasury publishes the daily closing balance.
            Test whether moving to an auction CUSIP -- a cross-registry
            identifier rather than an internal figure -- is witnessable.
videogames  PCGamingWiki is a separate operator from Valve and Wikimedia and
            records developer credits.
"""
from __future__ import annotations

import json
import os
import time
import traceback

import category_traps as ct
import net

OUT = "probe_t5e.json"
R = {}


def save():
    json.dump(R, open(OUT + ".tmp", "w"), indent=2, default=str)
    os.replace(OUT + ".tmp", OUT)


def step(name, fn):
    t0 = time.time()
    try:
        R[name] = fn()
    except Exception as exc:  # noqa: BLE001
        R[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                   "tb": traceback.format_exc()[-600:]}
    R[name]["secs"] = round(time.time() - t0, 1)
    save()
    print(f"[{name}] " + json.dumps(
        {k: v for k, v in R[name].items()
         if k not in ("tb", "rows", "pairs", "top10", "presidential_summaries")},
        default=str)[:800], flush=True)


# ---------------------------------------------------------------- politics
def politics():
    out = {}
    seen, offset = [], 0
    while offset < 500:
        js = net.get_json("https://api.govinfo.gov/packages/FR-2003-03-28/granules?"
                          f"offset={offset}&pageSize=100&api_key=DEMO_KEY", timeout=120)
        g = js.get("granules") or []
        out["count"] = js.get("count")
        seen += g
        if not g or len(seen) >= (js.get("count") or 0):
            break
        offset += 100
        time.sleep(1.0)
    out["n_granules_seen"] = len(seen)
    out["complete"] = len(seen) >= (out.get("count") or 0)
    out["granule_hits_13292"] = [g.get("granuleId") for g in seen
                                 if "13292" in json.dumps(g)][:5]
    pres = [g for g in seen
            if "presidential" in json.dumps(g).lower()
            or "executive order" in str(g.get("title", "")).lower()]
    out["n_presidential_granules"] = len(pres)
    deep = []
    for g in pres[:15]:
        try:
            s = net.get_json("https://api.govinfo.gov/packages/FR-2003-03-28/granules/"
                             f"{g['granuleId']}/summary?api_key=DEMO_KEY", timeout=90)
            deep.append({"granuleId": g["granuleId"], "title": str(s.get("title"))[:70],
                         "has13292": "13292" in json.dumps(s)})
        except Exception as exc:  # noqa: BLE001
            deep.append({"granuleId": g.get("granuleId"),
                         "err": f"{type(exc).__name__}: {str(exc)[:60]}"})
        time.sleep(0.8)
    out["presidential_summaries"] = deep
    out["deep_hits"] = [d["granuleId"] for d in deep if d.get("has13292")]
    try:
        hits = net.wikidata_search("Executive Order 13292").get("search", [])
        out["wikidata_search"] = [{"id": h["id"], "label": h.get("label"),
                                   "desc": (h.get("description") or "")[:70]}
                                  for h in hits[:5]]
    except Exception as exc:  # noqa: BLE001
        out["wikidata_err"] = f"{type(exc).__name__}: {str(exc)[:70]}"
    out["ok"] = bool(out.get("deep_hits") or out.get("granule_hits_13292")
                     or out.get("wikidata_search"))
    return out


# ------------------------------------------------------------ celebrities
def _gnd(query):
    js = net.get_json("https://lobid.org/gnd/search?q=" + query.replace(" ", "+")
                      + "&format=json&size=5", timeout=90)
    return js


def celebrities():
    out = {}
    try:
        js = _gnd("Johannes Diderik van der Waals")
        members = js.get("member") or []
        out["gnd_total"] = js.get("totalItems")
        rows = [{"gndId": m.get("gndIdentifier"), "label": m.get("preferredName"),
                 "placeOfBirth": [p.get("label") for p in (m.get("placeOfBirth") or [])],
                 "dateOfBirth": m.get("dateOfBirth")} for m in members[:5]]
        out["gnd_rows"] = rows
        hit = next((r for r in rows if any("leiden" in str(p).lower()
                                           for p in r["placeOfBirth"])), None)
        out["gnd_confirms_leiden"] = bool(hit)
        out["gnd_hit"] = hit
    except Exception as exc:  # noqa: BLE001
        out["gnd_err"] = f"{type(exc).__name__}: {str(exc)[:90]}"
    out["ok"] = bool(out.get("gnd_confirms_leiden"))
    return out


def history():
    out = {}
    try:
        js = _gnd("Aage Niels Bohr")
        members = js.get("member") or []
        blob = json.dumps(members)
        out["gnd_total"] = js.get("totalItems")
        out["gnd_ids"] = [m.get("gndIdentifier") for m in members[:5]]
        out["gnd_mentions_1975"] = "1975" in blob
        out["gnd_mentions_nobel"] = "nobel" in blob.lower()
    except Exception as exc:  # noqa: BLE001
        out["gnd_err"] = f"{type(exc).__name__}: {str(exc)[:90]}"
    out["ok"] = bool(out.get("gnd_mentions_1975") and out.get("gnd_mentions_nobel"))
    return out


# ---------------------------------------------------------------------- tv
def tv():
    rows_in = json.load(open("probe_t5c.json"))["tv"]["rows"]
    got = [c for c in rows_in if c.get("tvmaze_id")]
    rows = []
    for c in got:
        rec = {k: c.get(k) for k in ("year", "genre", "n", "sep", "top", "title",
                                     "top_rt", "second_rt", "tvmaze_id",
                                     "tvmaze_name", "tvmaze_runtime")}
        a, b = ct._norm(str(c.get("title") or "")), ct._norm(str(c.get("tvmaze_name") or ""))
        rec["title_match"] = bool(a and b and (a == b or a in b or b in a))
        rec["runtime_agrees"] = (c.get("top_rt") == c.get("tvmaze_runtime"))
        try:
            wd = ct._wikidata_by_value("P345", c["top"], limit=3)
            rec["wikidata_hits"] = len(wd)
            rec["wikidata_qid"] = wd[0]["qid"] if len(wd) == 1 else None
            rec["wikidata_label"] = wd[0]["label"] if len(wd) == 1 else None
        except Exception as exc:  # noqa: BLE001
            rec["wikidata_err"] = f"{type(exc).__name__}: {str(exc)[:50]}"
        rows.append(rec)
        print(f"    {rec['year']} {str(rec['genre']):12s} n={rec['n']:4d} "
              f"sep={rec['sep']} title_match={rec['title_match']} "
              f"rt {rec['top_rt']} vs {rec['tvmaze_runtime']} "
              f"wd={rec.get('wikidata_hits')}", flush=True)
        time.sleep(0.5)
    two = [r for r in rows if r["title_match"] and r.get("wikidata_qid")]
    return {"ok": bool(two), "n_tvmaze": len(rows), "n_two_witness": len(two),
            "n_runtime_agrees": sum(1 for r in rows if r["runtime_agrees"]),
            "best": max(two, key=lambda r: r["sep"]) if two else None,
            "rows": rows}


# ------------------------------------------------------------- video games
def video_games():
    out = {}
    try:
        js = net.get_json("https://www.pcgamingwiki.com/w/api.php?action=query"
                          "&prop=revisions&rvprop=content&rvslots=main"
                          "&titles=Cities%3A%20Skylines&format=json", timeout=90)
        pages = (js.get("query") or {}).get("pages") or {}
        txt = ""
        for p in pages.values():
            revs = p.get("revisions") or []
            if revs:
                txt = ((revs[0].get("slots") or {}).get("main") or {}).get("*", "")
        out["page_bytes"] = len(txt)
        out["mentions_colossal_order"] = "colossal order" in txt.lower()
        i = txt.lower().find("colossal order")
        out["context"] = txt[max(0, i - 90):i + 60] if i >= 0 else None
    except Exception as exc:  # noqa: BLE001
        out["err"] = f"{type(exc).__name__}: {str(exc)[:90]}"
    out["ok"] = bool(out.get("mentions_colossal_order"))
    return out


# ---------------------------------------------------------------- finance
def finance():
    out = {}
    try:
        js = net.get_json(
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
            "accounting/od/auctions_query?filter=auction_date:gte:2018-01-01,"
            "auction_date:lte:2018-12-31&page[size]=2000"
            "&fields=cusip,security_type,security_term,auction_date,offering_amt",
            timeout=180, attempts=4)
        rows = []
        for r in js.get("data", []):
            try:
                amt = float(r.get("offering_amt"))
            except (TypeError, ValueError):
                continue
            if r.get("cusip") and amt > 0:
                r["_amt"] = amt
                rows.append(r)
        out["n_priced"] = len(rows)
        rows.sort(key=lambda r: -r["_amt"])
        out["top3"] = [{k: r.get(k) for k in
                        ("cusip", "security_type", "security_term",
                         "auction_date", "_amt")} for r in rows[:3]]
        out["ties_at_max"] = sum(1 for r in rows if r["_amt"] == rows[0]["_amt"])
        out["sep_ratio"] = (round(rows[0]["_amt"] / rows[1]["_amt"], 4)
                            if len(rows) > 1 and rows[1]["_amt"] else None)
        cusip = rows[0]["cusip"]
        out["cusip"] = cusip
        for label, u in (("sec_fts", "https://efts.sec.gov/LATEST/search-index?q=%22"
                          + cusip + "%22"),
                         ("finra", "https://api.finra.org/data/group/otcMarket/name/"
                          "weeklySummary?limit=1")):
            try:
                j2 = net.get_json(u, timeout=90, attempts=2)
                out[label] = str(j2)[:160]
            except Exception as exc:  # noqa: BLE001
                out[label + "_err"] = f"{type(exc).__name__}: {str(exc)[:70]}"
    except Exception as exc:  # noqa: BLE001
        out["err"] = f"{type(exc).__name__}: {str(exc)[:110]}"
    out["ok"] = "sec_fts" in out
    return out


# --------------------------------------------------------------- shopping
def _off_all(tag, cty, max_pages=25):
    prods, page, total = [], 1, None
    while page <= max_pages:
        js = net.get_json(
            "https://world.openfoodfacts.org/api/v2/search?"
            f"categories_tags={tag}&countries_tags_en={cty}"
            "&fields=code,brands,product_name,nutriments&page_size=100"
            f"&page={page}", timeout=180, attempts=4)
        total = js.get("count")
        got = js.get("products") or []
        prods += got
        if not got or len(prods) >= (total or 0):
            break
        page += 1
        time.sleep(1.8)
    return prods, total


def shopping():
    out = {}
    for tag, cty in (("en:crisps", "united-kingdom"),
                     ("en:instant-coffees", "germany")):
        rec = {"tag": tag, "country": cty}
        prods, total = _off_all(tag, cty)
        rec["count"] = total
        rec["fetched"] = len(prods)
        rec["complete"] = len(prods) >= (total or 0)
        vals = []
        for p in prods:
            f = (p.get("nutriments") or {}).get("fat_100g")
            if p.get("code") and isinstance(f, (int, float)) and 0 < f <= 100:
                vals.append((p["code"], float(f), p.get("brands"),
                             (p.get("product_name") or "")[:44]))
        rec["n_ranked"] = len(vals)
        vals.sort(key=lambda v: -v[1])
        rec["top10"] = vals[:10]
        if len(vals) >= 25:
            rec["ties"] = sum(1 for v in vals if v[1] == vals[0][1])
            rec["sep_ratio"] = round(vals[0][1] / vals[1][1], 4) if vals[1][1] else None
            rec["winner"] = vals[0]
            if rec["ties"] == 1:
                try:
                    js = net.get_json("https://api.upcitemdb.com/prod/trial/lookup?"
                                      f"upc={vals[0][0]}", timeout=60, attempts=2)
                    rec["upc_total"] = int(js.get("total") or 0)
                    rec["upc_title"] = ((js.get("items") or [{}])[0]
                                        .get("title", "") or "")[:80]
                except Exception as exc:  # noqa: BLE001
                    rec["upc_err"] = f"{type(exc).__name__}: {str(exc)[:60]}"
                try:
                    rec["wikidata_gtin_hits"] = len(
                        ct._wikidata_by_value("P3962", vals[0][0], limit=3))
                except Exception:  # noqa: BLE001
                    rec["wikidata_gtin_hits"] = None
        out[f"{tag}|{cty}"] = rec
        print(f"    {tag:22s} {cty:16s} total={total} fetched={len(prods)} "
              f"ranked={rec['n_ranked']} ties={rec.get('ties')} "
              f"upc={rec.get('upc_total')} win={rec.get('winner')}", flush=True)
        time.sleep(2.0)
    out["ok"] = any(isinstance(v, dict) and v.get("upc_total") for v in out.values())
    return out


if __name__ == "__main__":
    for nm, fn in [("celebrities", celebrities), ("history", history),
                   ("video_games", video_games), ("tv", tv),
                   ("politics", politics), ("finance", finance),
                   ("shopping", shopping)]:
        step(nm, fn)
    print("\nwrote", OUT)
