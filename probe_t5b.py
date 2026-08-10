"""Second pass on the four categories with no independent witness yet.

education  ROR matched the wrong entity ("Waterford Institute", a US school).
           Retry with the full legal name. Also interrogate a stationarity
           risk: Waterford Institute of Technology merged into South East
           Technological University in 2022, so the register entry wit.ie may
           be stale. A trap whose answer is a decommissioned domain is a trap
           whose ground truth is a register lag, not a fact.
shopping   UPCitemdb returned total=0 for the winning barcode. Test the
           Wikidata GTIN property instead, and measure how many of the ranked
           products any independent registry actually carries -- if coverage
           is near zero the barcode is the wrong answer field.
politics   The GovInfo link service rejected the executive-order form (HTTP
           400). Test the package summary route for the Federal Register
           issue that printed the order.
tv         TVmaze 404s on a feature film because it indexes television. If the
           base set is restricted to television series instead, TVmaze becomes
           a real second operator. Measure whether the lookup works and how
           many series carry a runtime in the IMDb export.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import traceback
from collections import defaultdict

import category_traps as ct
import net

OUT = "probe_t5b.json"
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
        {k: v for k, v in R[name].items() if k not in ("tb", "rows")},
        default=str)[:700])


def education():
    out = {}
    for q in ("Waterford Institute of Technology", "South East Technological University"):
        try:
            js = net.get_json("https://api.ror.org/v2/organizations?query="
                              + net.up.quote(q) if hasattr(net, "up") else
                              "https://api.ror.org/v2/organizations?query="
                              + q.replace(" ", "%20"), timeout=90)
            items = js.get("items") or []
            recs = []
            for it in items[:6]:
                links = [l.get("value") for l in (it.get("links") or [])]
                names = [n.get("value") for n in (it.get("names") or [])]
                recs.append({"id": it.get("id"), "names": names[:3], "links": links,
                             "status": it.get("status")})
            out[q] = {"n": len(items), "recs": recs,
                      "wit_ie_present": any("wit.ie" in (l or "")
                                            for r in recs for l in r["links"])}
        except Exception as exc:  # noqa: BLE001
            out[q] = {"error": f"{type(exc).__name__}: {exc}"}
    # Wikidata P856 for the institution named by the register
    try:
        site, qid = ct._wikidata_value("Waterford Institute of Technology", "P856")
        out["wikidata_P856"] = {"qid": qid, "site": site,
                                "matches_wit_ie": "wit.ie" in str(site or "")}
    except Exception as exc:  # noqa: BLE001
        out["wikidata_P856"] = {"error": f"{type(exc).__name__}: {exc}"}
    # is the domain itself still live?
    for u in ("https://www.wit.ie/", "https://www.setu.ie/"):
        try:
            net.fetch(u, timeout=45, attempts=2, use_cache=False)
            out[f"live::{u}"] = "200"
        except Exception as exc:  # noqa: BLE001
            out[f"live::{u}"] = f"{type(exc).__name__}: {str(exc)[:90]}"
    out["ok"] = bool(out.get("wikidata_P856", {}).get("matches_wit_ie"))
    return out


def shopping():
    """Independent coverage of OFF barcodes, measured not assumed."""
    codes = ["7613036900096"]
    # pull the ranked German instant-coffee set and sample its barcodes
    try:
        js = net.get_json(
            "https://world.openfoodfacts.org/api/v2/search?"
            "categories_tags=en:instant-coffees&countries_tags_en=germany"
            "&fields=code,brands,product_name,nutriments&page_size=100&page=1",
            timeout=120)
        prods = js.get("products") or []
        codes += [p["code"] for p in prods[:14] if p.get("code")]
    except Exception as exc:  # noqa: BLE001
        R.setdefault("_shopping_fetch_err", str(exc))
    codes = list(dict.fromkeys(codes))[:15]

    wd_hits, upc_hits, rows = 0, 0, []
    for c in codes:
        rec = {"code": c}
        try:
            h = ct._wikidata_by_value("P3962", c)
            rec["wd_gtin"] = h
            wd_hits += bool(h)
        except Exception as exc:  # noqa: BLE001
            rec["wd_err"] = f"{type(exc).__name__}: {str(exc)[:60]}"
        try:
            js = net.get_json(f"https://api.upcitemdb.com/prod/trial/lookup?upc={c}",
                              timeout=60)
            rec["upc_total"] = js.get("total")
            rec["upc_title"] = (js.get("items") or [{}])[0].get("title", "")[:60]
            upc_hits += int(js.get("total") or 0) > 0
        except Exception as exc:  # noqa: BLE001
            rec["upc_err"] = f"{type(exc).__name__}: {str(exc)[:60]}"
        rows.append(rec)
    return {"ok": upc_hits > 0 or wd_hits > 0, "n_codes": len(codes),
            "upcitemdb_coverage": f"{upc_hits}/{len(codes)}",
            "wikidata_gtin_coverage": f"{wd_hits}/{len(codes)}",
            "rows": rows,
            "verdict": ("barcode has independent coverage"
                        if upc_hits + wd_hits > 0 else
                        "no independent registry carries these barcodes; the "
                        "barcode cannot be confirmed off Open Food Facts")}


def politics():
    out = {}
    for url in (
        "https://api.govinfo.gov/packages/FR-2003-03-28/summary?api_key=DEMO_KEY",
        "https://www.govinfo.gov/wssearch/rb/cpd/2003/executiveorder?fetchChildMetadata=true",
        "https://api.govinfo.gov/collections/CPD/2003-03-25T00:00:00Z?offset=0&pageSize=5&api_key=DEMO_KEY",
    ):
        try:
            js = net.get_json(url, timeout=90)
            out[url.split("//")[1][:58]] = json.dumps(js)[:300]
        except Exception as exc:  # noqa: BLE001
            out[url.split("//")[1][:58]] = f"{type(exc).__name__}: {str(exc)[:90]}"
    # Wikidata: is EO 13292 itself an item?
    try:
        hits = ct._wikidata_by_value("P3903", "13292")  # long shot
        out["wd_by_value"] = hits
    except Exception as exc:  # noqa: BLE001
        out["wd_by_value"] = f"{type(exc).__name__}: {str(exc)[:70]}"
    out["ok"] = any(v.startswith("{") for v in out.values() if isinstance(v, str))
    return out


def tv():
    """If the base set is television series, TVmaze becomes a real witness."""
    out = {}
    try:
        js = net.get_json("https://api.tvmaze.com/lookup/shows?imdb=tt0903747",
                          timeout=60)  # Breaking Bad, a known series
        out["tvmaze_series_lookup"] = {"id": js.get("id"), "name": js.get("name"),
                                       "premiered": js.get("premiered"),
                                       "runtime": js.get("runtime")}
    except Exception as exc:  # noqa: BLE001
        out["tvmaze_series_lookup"] = f"{type(exc).__name__}: {str(exc)[:80]}"

    # how many tvSeries per (startYear, genre) carry a runtime?
    raw = net.fetch(ct._IMDB_BASICS, timeout=900, attempts=3, binary=True)
    buckets = defaultdict(list)
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as fh:
        head = fh.readline()
        del head
        for line in fh:
            p = line.decode("utf-8", "replace").rstrip("\n").split("\t")
            if len(p) < 9 or p[1] != "tvSeries" or p[5] == r"\N" or p[7] == r"\N":
                continue
            try:
                yr = int(p[5])
                rt = int(p[7])
            except ValueError:
                continue
            if not (1990 <= yr <= 2015) or rt < 5 or rt > 200:
                continue
            for g in p[8].split(","):
                if g and g != r"\N":
                    buckets[(yr, g)].append((p[0], rt, p[2]))
    cand = []
    for (yr, g), rows in buckets.items():
        if not (25 <= len(rows) <= 400):
            continue
        rows_sorted = sorted(rows, key=lambda r: -r[1])
        if len(rows_sorted) < 2 or rows_sorted[0][1] == rows_sorted[1][1]:
            continue
        cand.append({"year": yr, "genre": g, "n": len(rows),
                     "top": rows_sorted[0][0], "top_rt": rows_sorted[0][1],
                     "second_rt": rows_sorted[1][1], "title": rows_sorted[0][2][:50],
                     "sep": round(rows_sorted[0][1] / rows_sorted[1][1], 3)})
    cand.sort(key=lambda c: -c["n"])
    out["n_buckets"] = len(buckets)
    out["n_candidates"] = len(cand)
    out["top_candidates"] = cand[:12]
    out["ok"] = bool(cand) and isinstance(out.get("tvmaze_series_lookup"), dict)
    return out


if __name__ == "__main__":
    for nm, fn in [("education", education), ("shopping", shopping),
                   ("politics", politics), ("tv", tv)]:
        step(nm, fn)
    print("\nwrote", OUT)
