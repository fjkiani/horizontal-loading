"""Third pass: rescue shopping, politics, tv, art.

shopping   Measured coverage of the current winning barcode outside Open Food
           Facts is zero (UPCitemdb 1/15 on the ranked set, and the one hit is
           not the winner; Wikidata GTIN 0/15). The answer is unconfirmable, so
           the SEED must move, not the answer field. Scan candidate
           (category, country) pairs, compute the argmax winner for each, and
           test that winner's barcode against an independent registry. Keep
           only a seed whose winner is independently carried.
politics   The GovInfo package summary resolves; test whether a granule inside
           the same issue names executive order 13292, which would make GPO a
           real witness distinct from the Office of the Federal Register.
tv         TVmaze resolves television series by IMDb id. Test the actual
           candidate winners, not a known-popular control, because TVmaze
           coverage of obscure foreign series is the thing in question.
art        Test whether Europeana carries the Met accession number, which
           would give art a second witness beyond Wikidata P217.
"""
from __future__ import annotations

import json
import os
import time
import traceback

import category_traps as ct
import net

OUT = "probe_t5c.json"
R = {}


def save():
    json.dump(R, open(OUT + ".tmp", "w"), indent=2, default=str)
    os.replace(OUT + ".tmp", OUT)


def step(name, fn):
    try:
        R[name] = fn()
    except Exception as exc:  # noqa: BLE001
        R[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                   "tb": traceback.format_exc()[-500:]}
    save()
    print(f"[{name}] " + json.dumps(
        {k: v for k, v in R[name].items() if k not in ("tb", "rows", "pairs")},
        default=str)[:600])


# ---------------------------------------------------------------- shopping
_PAIRS = [("en:instant-coffees", "germany"), ("en:baked-beans", "united-kingdom"),
          ("en:tahini", "france"), ("en:mineral-waters", "france"),
          ("en:chocolates", "switzerland"), ("en:breakfast-cereals", "united-kingdom"),
          ("en:olive-oils", "italy"), ("en:pastas", "italy"),
          ("en:yogurts", "germany"), ("en:teas", "united-kingdom"),
          ("en:crisps", "united-kingdom"), ("en:beers", "belgium")]


def _off_page(tag, cty, page):
    return net.get_json(
        "https://world.openfoodfacts.org/api/v2/search?"
        f"categories_tags={tag}&countries_tags_en={cty}"
        "&fields=code,brands,product_name,nutriments&page_size=100"
        f"&page={page}", timeout=150, attempts=4)


def _upc(code):
    js = net.get_json(f"https://api.upcitemdb.com/prod/trial/lookup?upc={code}",
                      timeout=60, attempts=2)
    return int(js.get("total") or 0), (js.get("items") or [{}])[0].get("title", "")[:70]


def shopping():
    out, pairs = [], []
    for tag, cty in _PAIRS:
        rec = {"tag": tag, "country": cty}
        try:
            prods, page, total = [], 1, None
            while page <= 4:
                js = _off_page(tag, cty, page)
                total = js.get("count")
                got = js.get("products") or []
                prods += got
                if len(prods) >= (total or 0) or not got:
                    break
                page += 1
                time.sleep(1.8)
            rec["count"] = total
            rec["fetched"] = len(prods)
            vals = [(p["code"], float(p["nutriments"]["fat_100g"]),
                     p.get("brands"), (p.get("product_name") or "")[:40])
                    for p in prods
                    if p.get("code") and isinstance(
                        (p.get("nutriments") or {}).get("fat_100g"), (int, float))]
            rec["n_ranked"] = len(vals)
            if len(vals) < 25:
                rec["skip"] = "too few coded rows"
                pairs.append(rec)
                continue
            vals.sort(key=lambda v: -v[1])
            rec["top1"] = vals[0]
            rec["top2"] = vals[1]
            rec["ties"] = sum(1 for v in vals if v[1] == vals[0][1])
            rec["sep_ratio"] = round(vals[0][1] / vals[1][1], 4) if vals[1][1] else None
            if rec["ties"] != 1:
                rec["skip"] = "tie at the extremum"
                pairs.append(rec)
                continue
            t, title = _upc(vals[0][0])
            rec["upc_total"], rec["upc_title"] = t, title
            rec["independent_witness"] = t > 0
            if t > 0:
                out.append(rec)
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:90]}"
        pairs.append(rec)
        print("   ", tag, cty, rec.get("n_ranked"), rec.get("ties"),
              rec.get("upc_total"), rec.get("skip", ""), rec.get("error", ""))
        time.sleep(1.5)
    return {"ok": bool(out), "n_pairs": len(_PAIRS),
            "n_with_independent_witness": len(out),
            "winners_confirmed": out, "pairs": pairs}


# ---------------------------------------------------------------- politics
def politics():
    out = {}
    try:
        js = net.get_json("https://api.govinfo.gov/packages/FR-2003-03-28/granules?"
                          "offset=0&pageSize=100&api_key=DEMO_KEY", timeout=120)
        grans = js.get("granules") or []
        out["n_granules"] = js.get("count")
        hits = [g for g in grans if "13292" in json.dumps(g)]
        out["granule_hits_13292"] = hits[:3]
        if hits:
            gid = hits[0]["granuleId"]
            s = net.get_json(f"https://api.govinfo.gov/packages/FR-2003-03-28/"
                             f"granules/{gid}/summary?api_key=DEMO_KEY", timeout=90)
            out["granule_summary"] = {k: s.get(k) for k in
                                      ("title", "granuleId", "governmentAuthor1",
                                       "documentType", "executiveOrderNumber")}
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:110]}"
    out["ok"] = bool(out.get("granule_hits_13292"))
    return out


# ---------------------------------------------------------------------- tv
def tv():
    cands = json.load(open("probe_t5b.json"))["tv"]["top_candidates"]
    rows = []
    for c in cands[:12]:
        rec = dict(c)
        try:
            js = net.get_json(f"https://api.tvmaze.com/lookup/shows?imdb={c['top']}",
                              timeout=60, attempts=2)
            rec["tvmaze_id"] = js.get("id")
            rec["tvmaze_name"] = js.get("name")
            rec["tvmaze_runtime"] = js.get("runtime")
            rec["tvmaze_avg_runtime"] = js.get("averageRuntime")
        except Exception as exc:  # noqa: BLE001
            rec["tvmaze_err"] = f"{type(exc).__name__}: {str(exc)[:50]}"
        rows.append(rec)
        print(f"    {c['year']} {c['genre']:10s} n={c['n']:4d} sep={c['sep']:.3f} "
              f"{str(rec.get('tvmaze_name'))[:30]:30s} {rec.get('tvmaze_err','')[:30]}")
        time.sleep(0.6)
    ok = [r for r in rows if r.get("tvmaze_id")]
    return {"ok": bool(ok), "n_tested": len(rows), "n_on_tvmaze": len(ok),
            "coverage": f"{len(ok)}/{len(rows)}", "rows": rows}


# --------------------------------------------------------------------- art
def art():
    out = {}
    try:
        js = net.get_json("https://api.europeana.eu/record/v2/search.json?"
                          "wskey=api2demo&rows=5&query=%2271.84%22%20Rembrandt",
                          timeout=90)
        out["europeana_total"] = js.get("totalResults")
        out["europeana_titles"] = [(i.get("title") or [""])[0][:60]
                                   for i in (js.get("items") or [])[:3]]
    except Exception as exc:  # noqa: BLE001
        out["europeana_err"] = f"{type(exc).__name__}: {str(exc)[:80]}"
    out["ok"] = bool(out.get("europeana_total"))
    return out


if __name__ == "__main__":
    for nm, fn in [("politics", politics), ("art", art), ("tv", tv),
                   ("shopping", shopping)]:
        step(nm, fn)
    print("\nwrote", OUT)
