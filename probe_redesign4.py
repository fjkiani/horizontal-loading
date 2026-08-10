"""Round 4. Replace the keys that round 3 disproved.

politics_pages : rank EOs by Federal Register page length (unpatterned, off-axis)
history_full   : one consistent alphabetical key (fullName), not surname-or-fallback
celebs_laur    : /2.1/laureates carries birth dates; nobelPrizes does not
shopping_pages : paginate a category to FULL coverage, rank by a real nutrient
"""
import json, os, sys, time, collections
import net
import category_traps as ct

OUT = "probe_redesign4.json"
R = json.load(open(OUT)) if os.path.exists(OUT) else {}


def save(k, v):
    R[k] = v
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(R, fh, indent=2, default=str)
    os.replace(tmp, OUT)
    print(f"[saved] {k}", flush=True)


def rr(keys, mode="min"):
    tgt = min(keys) if mode == "min" else max(keys)
    ties = [i for i, k in enumerate(keys) if k == tgt]
    i, n = ties[0], len(keys)
    return {"n": n, "index": i, "depth": round(i / (n - 1), 4) if n > 1 else 0.0,
            "n_ties": len(ties), "distinct_keys": len(set(keys)),
            "rho": ct._spearman(list(range(n)), ct._rankdata(keys)),
            "first": i == 0, "last": i == n - 1}


# ---------------------------------------------------- politics: page length
def p_politics_pages():
    """Title-alphabetical failed: annual boilerplate EOs ('Adjustments of
    Certain Rates of Pay') win 5 of 8 years. Printed page length is not
    patterned by title and is orthogonal to issuance order."""
    out, winners = [], []
    for y in (1998, 2003, 1996, 2007, 2011, 1994, 2009, 2015, 2001, 2013):
        u = ("https://www.federalregister.gov/api/v1/documents.json?"
             "conditions[type][]=PRESDOCU"
             "&conditions[presidential_document_type][]=executive_order"
             f"&conditions[publication_date][gte]={y}-01-01"
             f"&conditions[publication_date][lte]={y}-12-31"
             "&fields[]=executive_order_number&fields[]=title&fields[]=start_page"
             "&fields[]=end_page&fields[]=page_length&fields[]=publication_date"
             "&fields[]=citation&per_page=1000&order=oldest")
        try:
            js = net.get_json(u, timeout=120, attempts=3)
            rows = [r for r in js.get("results", [])
                    if r.get("executive_order_number")
                    and isinstance(r.get("page_length"), int)
                    and r["page_length"] > 0]
            if len(rows) < 12:
                out.append({"year": y, "n": len(rows), "note": "too few"})
                continue
            pl = [r["page_length"] for r in rows]
            w = rr([f"{v:05d}" for v in pl], "max")
            i = w["index"]
            eos = [r["executive_order_number"] for r in rows]
            rec = {"year": y, **w, "eo": eos[i], "title": rows[i]["title"][:70],
                   "pages": pl[i], "runner_up": sorted(pl, reverse=True)[1],
                   "citation": rows[i].get("citation"),
                   "pub": rows[i]["publication_date"],
                   "distinct_eo": len(set(eos)),
                   "p_guess_eo": round(1 / len(set(eos)), 6),
                   "max_page_hist": collections.Counter(pl).most_common(4)}
            out.append(rec)
            winners.append(rows[i]["title"].lower()[:30])
            print(f"  pol {y}: n={w['n']} d={w['depth']} rho={w['rho']} "
                  f"ties={w['n_ties']} pages={pl[i]} (next {rec['runner_up']}) "
                  f"EO{eos[i]} {rows[i]['title'][:36]!r}", flush=True)
        except Exception as e:
            out.append({"year": y, "error": f"{type(e).__name__}: {e}"[:160]})
    rep = collections.Counter(winners).most_common(2)
    print(f"  >> repeated winning titles across years: {rep} of {len(winners)}",
          flush=True)
    ok = [o for o in out if o.get("n_ties") == 1
          and 0.08 <= (o.get("depth") or 0) <= 0.92
          and abs(o.get("rho") or 0) <= 0.95
          and (o.get("pages") or 0) > (o.get("runner_up") or 0)]
    return {"scan": out, "repeated_titles": rep, "usable": ok}


# -------------------------------------------------- history: consistent key
def p_history_full():
    js = net.get_json("https://api.nobelprize.org/2.1/nobelPrizes?limit=1000"
                      "&nobelPrizeYear=1901&yearTo=2024", timeout=180, attempts=4)
    prizes = js.get("nobelPrizes", [])
    out = []
    for cat in ("Physics", "Chemistry", "Physiology or Medicine"):
        for (y0, y1) in ((1901, 2000), (1901, 2024)):
            sub = [p for p in prizes
                   if p.get("category", {}).get("en") == cat
                   and y0 <= int(p["awardYear"]) <= y1
                   and len(p.get("laureates") or []) >= 3]
            if len(sub) < 5:
                continue
            keys, missing = [], 0
            for p in sub:
                nm = ((p["laureates"][0].get("fullName") or {}).get("en") or "")
                if not nm:
                    missing += 1
                keys.append(nm.strip().lower())
            w = rr(keys, "min")
            yrs = [int(p["awardYear"]) for p in sub]
            out.append({"category": cat, "window": [y0, y1], **w,
                        "missing_names": missing,
                        "answer_year": yrs[w["index"]],
                        "winner_name": keys[w["index"]],
                        "second_key": sorted(keys)[1],
                        "distinct_years": len(set(yrs)),
                        "p_guess_year": round(
                            yrs.count(yrs[w["index"]]) / len(yrs), 6)})
            print(f"  hist {cat[:11]:11s} {y0}-{y1}: n={w['n']} d={w['depth']} "
                  f"rho={w['rho']} miss={missing} -> {yrs[w['index']]} "
                  f"({keys[w['index']][:26]}) p={out[-1]['p_guess_year']}", flush=True)
    return out


# ------------------------------------------- celebrities: laureate registry
def p_celebs_laur():
    """nobelPrizes embeds laureates without birth data. /2.1/laureates does."""
    laur = []
    off = 0
    while True:
        u = f"https://api.nobelprize.org/2.1/laureates?limit=200&offset={off}"
        js = net.get_json(u, timeout=180, attempts=4)
        got = js.get("laureates", [])
        laur.extend(got)
        if len(got) < 200 or off > 1400:
            break
        off += 200
    print(f"  laureates fetched: {len(laur)}", flush=True)
    if laur:
        print(f"  sample keys: {sorted(laur[0].keys())}", flush=True)

    def person(L):
        nm = (L.get("knownName") or {}).get("en") or (L.get("fullName") or {}).get("en")
        bd = (L.get("birth") or {}).get("date")
        return nm, bd

    rows = []
    for L in laur:
        nm, bd = person(L)
        if not (nm and bd and len(bd) == 10 and not bd.endswith("-00-00")
                and bd[5:7] != "00" and bd[8:10] != "00"):
            continue
        prizes = L.get("nobelPrizes") or []
        for p in prizes:
            rows.append({"name": nm, "dob": bd, "id": L.get("id"),
                         "cat": (p.get("category") or {}).get("en"),
                         "year": int(p.get("awardYear") or 0)})
    print(f"  usable laureate-prize rows: {len(rows)}", flush=True)
    out = []
    for cat in ("Physics", "Chemistry", "Physiology or Medicine", "Literature",
                "Peace"):
        for (y0, y1) in ((1901, 1950), (1951, 2000), (1901, 1975), (1976, 2024)):
            sub = [r for r in rows if r["cat"] == cat and y0 <= r["year"] <= y1]
            if len(sub) < 12:
                continue
            keys = [r["name"].strip().lower() for r in sub]
            w = rr(keys, "min")
            dobs = [r["dob"] for r in sub]
            i = w["index"]
            out.append({"category": cat, "window": [y0, y1], **w,
                        "winner": sub[i]["name"], "answer_dob": dobs[i],
                        "award_year": sub[i]["year"], "laureate_id": sub[i]["id"],
                        "distinct_dob": len(set(dobs)),
                        "p_guess_dob": round(dobs.count(dobs[i]) / len(dobs), 6),
                        "dob_year": int(dobs[i][:4]),
                        "dob_year_named_in_window": y0 <= int(dobs[i][:4]) <= y1})
            print(f"  celeb {cat[:11]:11s} {y0}-{y1}: n={w['n']} d={w['depth']} "
                  f"rho={w['rho']} -> {dobs[i]} ({sub[i]['name'][:24]}) "
                  f"p={out[-1]['p_guess_dob']} dobyr_in_win="
                  f"{out[-1]['dob_year_named_in_window']}", flush=True)
    return out


# ------------------------------------------------- shopping: full coverage
def p_shopping_pages():
    """Two defects to clear: (1) one page != the population, so the prompt made
    a claim the generator never checked; (2) int(barcode) ranks by GS1 issuing
    country. Fix: paginate to full coverage, rank by a real nutrient, and answer
    with the barcode instead of the brand."""
    out = []
    cands = [("en:instant-coffees", "germany"), ("en:mustards", "france"),
             ("en:sardines", "france"), ("en:pestos", "france"),
             ("en:baked-beans", "united-kingdom"), ("en:oat-milks", "germany"),
             ("en:tahini", "france"), ("en:worcestershire-sauces", "united-kingdom")]
    for tag, cty in cands:
        try:
            prods, page, total = [], 1, None
            while page <= 6:
                u = (f"https://world.openfoodfacts.org/api/v2/search?"
                     f"categories_tags={tag}&countries_tags_en={cty}"
                     f"&fields=code,brands,product_name,nutriments"
                     f"&page_size=100&page={page}")
                js = net.get_json(u, timeout=180, attempts=4)
                total = js.get("count") if total is None else total
                got = js.get("products", [])
                prods.extend(got)
                if len(got) < 100:
                    break
                page += 1
                time.sleep(1.5)
            complete = (total is not None and len(prods) >= total)
            rows = [p for p in prods
                    if (p.get("code") or "").isdigit()
                    and isinstance(p.get("nutriments"), dict)
                    and isinstance(p["nutriments"].get("salt_100g"), (int, float))]
            rec = {"tag": tag, "country": cty, "count": total,
                   "fetched": len(prods), "complete": complete,
                   "n_with_salt": len(rows), "pages": page}
            if len(rows) >= 12:
                sv = [float(p["nutriments"]["salt_100g"]) for p in rows]
                w = rr([f"{v:012.4f}" for v in sv], "max")
                i = w["index"]
                codes = [p["code"] for p in rows]
                rec["salt"] = {
                    **w, "code": codes[i], "brand": rows[i].get("brands"),
                    "name": (rows[i].get("product_name") or "")[:46],
                    "value": sv[i], "runner_up": sorted(sv, reverse=True)[1],
                    "distinct_codes": len(set(codes)),
                    "p_guess_code": round(codes.count(codes[i]) / len(codes), 6)}
            out.append(rec)
            s = rec.get("salt", {})
            print(f"  shop {tag}/{cty}: count={total} fetched={len(prods)} "
                  f"complete={complete} salt_n={len(rows)} d={s.get('depth')} "
                  f"rho={s.get('rho')} ties={s.get('n_ties')} "
                  f"code={s.get('code')} val={s.get('value')} "
                  f"(next {s.get('runner_up')})", flush=True)
        except Exception as e:
            out.append({"tag": tag, "country": cty,
                        "error": f"{type(e).__name__}: {e}"[:160]})
            print(f"  shop {tag}/{cty}: ERR {type(e).__name__}", flush=True)
        time.sleep(2.0)
    return out


SECTIONS = [("politics_pages", p_politics_pages),
            ("history_full", p_history_full),
            ("celebs_laur", p_celebs_laur),
            ("shopping_pages", p_shopping_pages)]

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
