"""Probes for the six redesigns the P5 evaluation loop demanded.

Each section writes its result into probe_redesign.json immediately, so an
interrupt loses at most one section.
"""
import json, os, re, sys, time, datetime as dt
import net

OUT = "probe_redesign.json"
R = json.load(open(OUT)) if os.path.exists(OUT) else {}


def save(k, v):
    R[k] = v
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(R, fh, indent=2, default=str)
    os.replace(tmp, OUT)
    print(f"[saved] {k}", flush=True)


def depth(idx, n):
    return round(idx / (n - 1), 4) if n > 1 else 0.0


# --------------------------------------------------------------- 1. finance
def p_finance():
    """Find a year whose Federal Reserve Account close balance peaks in the
    interior of the year, so 'date of the maximum' is not 'the last date'."""
    base = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
            "/v1/accounting/dts/operating_cash_balance")
    out = []
    for y in (2011, 2012, 2013, 2014, 2016, 2017, 2018, 2019, 2021, 2022, 2023):
        try:
            u = (f"{base}?filter=record_date:gte:{y}-01-01,record_date:lte:{y}-12-31"
                 f"&page[size]=2000&sort=record_date")
            js = net.get_json(u, timeout=120, attempts=3)
            rows = [r for r in js.get("data", [])
                    if r.get("account_type") == "Federal Reserve Account"]
            if len(rows) < 200:
                rows = [r for r in js.get("data", [])
                        if "Treasury General Account" in (r.get("account_type") or "")]
            vals = [(r["record_date"], float(r["close_today_bal"])) for r in rows
                    if r.get("close_today_bal") not in (None, "", "null")]
            if len(vals) < 200:
                out.append({"year": y, "n": len(vals), "note": "too few rows"})
                continue
            mx = max(v for _, v in vals)
            ties = [i for i, (_, v) in enumerate(vals) if v == mx]
            i = ties[0]
            out.append({"year": y, "n": len(vals), "argmax_index": i,
                        "depth": depth(i, len(vals)), "date": vals[i][0],
                        "value": mx, "n_ties": len(ties),
                        "account_type": rows[0].get("account_type")})
            print(f"  finance {y}: n={len(vals)} depth={depth(i,len(vals))} "
                  f"date={vals[i][0]} ties={len(ties)}", flush=True)
        except Exception as e:
            out.append({"year": y, "error": f"{type(e).__name__}: {e}"[:200]})
    interior = [o for o in out
                if o.get("n_ties") == 1 and 0.08 <= (o.get("depth") or 0) <= 0.92]
    return {"scan": out, "interior_candidates": interior}


# -------------------------------------------------------------- 2. business
def p_business():
    """SEC XBRL frames scoped by the `loc` field, answer = CIK.
    Global max is Alphabet (guessable by name); a state scope is not."""
    u = ("https://data.sec.gov/api/xbrl/frames/us-gaap/"
         "ResearchAndDevelopmentExpense/USD/CY2015.json")
    js = net.get_json(u, timeout=180, attempts=3)
    rows = js.get("data", [])
    from collections import Counter
    locs = Counter(r.get("loc") for r in rows if r.get("loc"))
    out = {"n_total": len(rows), "loc_hist": locs.most_common(14), "per_loc": []}
    for loc, _ in locs.most_common(14):
        sub = [r for r in rows if r.get("loc") == loc and r.get("val") is not None]
        if len(sub) < 12:
            continue
        mx = max(r["val"] for r in sub)
        ties = [i for i, r in enumerate(sub) if r["val"] == mx]
        i = ties[0]
        ciks = [str(r["cik"]) for r in sub]
        out["per_loc"].append({
            "loc": loc, "n": len(sub), "argmax_index": i,
            "depth": depth(i, len(sub)), "n_ties": len(ties),
            "winner": sub[i]["entityName"], "cik": sub[i]["cik"],
            "val": mx, "distinct_ciks": len(set(ciks)),
            "runner_up": sorted((r["val"] for r in sub), reverse=True)[1],
        })
        print(f"  business {loc}: n={len(sub)} depth={depth(i,len(sub))} "
              f"{sub[i]['entityName'][:34]} cik={sub[i]['cik']}", flush=True)
    return out


# -------------------------------------------------------------- 3. politics
def p_politics():
    """Order the year's executive orders ALPHABETICALLY by title; the answer is
    the EO number. Alphabetical order is orthogonal to issuance order, so the
    monotone-key leak (rho = 1.0) disappears."""
    out = []
    for y in (1998, 2003, 1996, 2007, 2011):
        try:
            u = ("https://www.federalregister.gov/api/v1/documents.json?"
                 "conditions[type][]=PRESDOCU"
                 "&conditions[presidential_document_type][]=executive_order"
                 f"&conditions[publication_date][gte]={y}-01-01"
                 f"&conditions[publication_date][lte]={y}-12-31"
                 "&fields[]=executive_order_number&fields[]=title"
                 "&fields[]=publication_date&fields[]=signing_date"
                 "&per_page=1000&order=oldest")
            js = net.get_json(u, timeout=120, attempts=3)
            rows = [r for r in js.get("results", [])
                    if r.get("executive_order_number") and r.get("title")]
            if len(rows) < 8:
                out.append({"year": y, "n": len(rows), "note": "too few"})
                continue
            keys = [r["title"].strip().lower() for r in rows]
            mn = min(keys)
            ties = [i for i, k in enumerate(keys) if k == mn]
            i = ties[0]
            # correlation of the alphabetical key against API (issuance) order
            import category_traps as ct
            rho = ct._spearman(list(range(len(keys))),
                               ct._rankdata(keys))
            out.append({"year": y, "n": len(rows), "argmin_index": i,
                        "depth": depth(i, len(rows)), "n_ties": len(ties),
                        "title": rows[i]["title"],
                        "eo": rows[i]["executive_order_number"],
                        "pub": rows[i]["publication_date"],
                        "spearman_title_vs_order": rho,
                        "distinct_eo": len(set(r["executive_order_number"] for r in rows)),
                        "eo_min": min(r["executive_order_number"] for r in rows),
                        "eo_max": max(r["executive_order_number"] for r in rows)})
            print(f"  politics {y}: n={len(rows)} depth={depth(i,len(rows))} "
                  f"rho={rho} EO{rows[i]['executive_order_number']} "
                  f"{rows[i]['title'][:40]}", flush=True)
        except Exception as e:
            out.append({"year": y, "error": f"{type(e).__name__}: {e}"[:200]})
    return out


# ------------------------------------------------------------- 4. geography
def p_geography():
    """Widen the geography base set: Nepal gave only 9 candidates, so a guesser
    hits 1/9 = 0.111 > the 0.10 ceiling."""
    txt = net.fetch("https://davidmegginson.github.io/ourairports-data/airports.csv",
                    timeout=180, attempts=3)
    import csv, io
    rows = list(csv.DictReader(io.StringIO(txt)))
    out = []
    for iso, name in (("NP", "Nepal"), ("PE", "Peru"), ("BO", "Bolivia"),
                      ("CO", "Colombia"), ("ID", "Indonesia"), ("NO", "Norway"),
                      ("CL", "Chile"), ("EC", "Ecuador"), ("TR", "Turkey"),
                      ("JP", "Japan"), ("NZ", "New Zealand")):
        sub = [r for r in rows
               if r["iso_country"] == iso
               and r["type"] in ("medium_airport", "large_airport")
               and r["scheduled_service"] == "yes"
               and re.fullmatch(r"[A-Z]{4}", r["gps_code"] or "")
               and (r["elevation_ft"] or "").lstrip("-").isdigit()]
        if len(sub) < 5:
            out.append({"iso": iso, "n": len(sub), "note": "too few"})
            continue
        els = [int(r["elevation_ft"]) for r in sub]
        mx = max(els)
        ties = [i for i, e in enumerate(els) if e == mx]
        i = ties[0]
        icaos = [r["gps_code"] for r in sub]
        out.append({"iso": iso, "country": name, "n": len(sub),
                    "argmax_index": i, "depth": depth(i, len(sub)),
                    "n_ties": len(ties), "winner": sub[i]["name"],
                    "icao": sub[i]["gps_code"], "elev": mx,
                    "distinct_icao": len(set(icaos)),
                    "p_guess": round(1 / len(set(icaos)), 4),
                    "runner_up_elev": sorted(els, reverse=True)[1]})
        print(f"  geo {iso}: n={len(sub)} p_guess={round(1/len(set(icaos)),4)} "
              f"depth={depth(i,len(sub))} {sub[i]['gps_code']} {mx}ft", flush=True)
    return out


# --------------------------------------------------------------- 5. shopping
def p_shopping():
    """Two questions. (a) Can a category+country be fully enumerated, or is
    n_base only one page? (b) Replace the barcode-magnitude key -- which is a
    GS1 issuing-country proxy -- with a real product attribute."""
    out = {"prefix_evidence": {}, "enumerability": []}
    for tag, cty in (("en:chocolates", "france"),
                     ("en:breakfast-cereals", "united-kingdom"),
                     ("en:mineral-waters", "france"),
                     ("en:instant-coffees", "germany")):
        try:
            u = (f"https://world.openfoodfacts.org/api/v2/search?"
                 f"categories_tags={tag}&countries_tags_en={cty}"
                 f"&fields=code,brands,product_name,nutriments"
                 f"&page_size=100&page=1")
            js = net.get_json(u, timeout=150, attempts=3)
            n_total = js.get("count")
            prods = js.get("products", [])
            coded = [p for p in prods
                     if (p.get("code") or "").isdigit() and p.get("brands")]
            # (a) how much of the population does one page cover?
            rec = {"tag": tag, "country": cty, "count": n_total,
                   "page_size": js.get("page_size"), "returned": len(prods),
                   "coded": len(coded),
                   "fully_enumerable": bool(n_total and n_total <= 100)}
            # (b) is int(barcode) just the GS1 prefix?
            if coded:
                import category_traps as ct
                codes = [int(p["code"]) for p in coded]
                prefs = [int(p["code"][:3]) for p in coded]
                rec["spearman_code_vs_prefix"] = ct._spearman(codes, prefs)
                j = max(range(len(codes)), key=lambda k: codes[k])
                rec["max_code"] = coded[j]["code"]
                rec["max_code_prefix"] = coded[j]["code"][:3]
                rec["max_code_brand"] = coded[j]["brands"]
                # a real attribute instead: salt per 100 g
                salted = [p for p in coded
                          if isinstance(p.get("nutriments"), dict)
                          and isinstance(p["nutriments"].get("salt_100g"), (int, float))]
                rec["n_with_salt_100g"] = len(salted)
                if len(salted) >= 5:
                    sv = [p["nutriments"]["salt_100g"] for p in salted]
                    mx = max(sv)
                    ties = [k for k, v in enumerate(sv) if v == mx]
                    rec["salt_winner"] = {
                        "n": len(salted), "argmax_index": ties[0],
                        "depth": depth(ties[0], len(salted)), "n_ties": len(ties),
                        "code": salted[ties[0]]["code"],
                        "brand": salted[ties[0]]["brands"],
                        "name": (salted[ties[0]].get("product_name") or "")[:50],
                        "salt": mx,
                        "runner_up": sorted(sv, reverse=True)[1],
                        "distinct_codes": len(set(p["code"] for p in salted)),
                        "spearman_salt_vs_order": ct._spearman(
                            list(range(len(sv))), ct._rankdata(sv)),
                    }
            out["enumerability"].append(rec)
            print(f"  shop {tag}/{cty}: count={n_total} coded={len(coded)} "
                  f"rho_code_prefix={rec.get('spearman_code_vs_prefix')} "
                  f"maxpref={rec.get('max_code_prefix')} "
                  f"salt_n={rec.get('n_with_salt_100g')}", flush=True)
        except Exception as e:
            out["enumerability"].append(
                {"tag": tag, "country": cty, "error": f"{type(e).__name__}: {e}"[:200]})
            print(f"  shop {tag}/{cty}: ERR {type(e).__name__}", flush=True)
    return out


SECTIONS = [("finance", p_finance), ("business", p_business),
            ("politics", p_politics), ("geography", p_geography),
            ("shopping", p_shopping)]

if __name__ == "__main__":
    only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    for name, fn in SECTIONS:
        if only and name not in only:
            continue
        print(f"=== {name}", flush=True)
        t = time.time()
        try:
            save(name, fn())
        except Exception as e:
            import traceback
            save(name, {"error": f"{type(e).__name__}: {e}"[:300],
                        "tb": traceback.format_exc()[-900:]})
            print(f"  {name} FAILED {type(e).__name__}: {e}"[:200], flush=True)
        print(f"    {name} {time.time()-t:.1f}s", flush=True)
    print("done", flush=True)
