"""Round 5.

celebs_dates : the laureate registry is returned ALPHABETICALLY (rho = 1.0), so
               the key must be a date, not a name. Test date-keyed variants.
shop_sanity  : 16 g salt per 100 g of instant coffee is implausible. If the
               winner is a data-entry error the prompt's semantics are false
               even though the API reproduces it.
tv           : single-pass IMDb scan for an interior winner.
"""
import json, os, sys, time, gzip, collections
import net
import category_traps as ct

OUT = "probe_redesign5.json"
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


def _laureates():
    laur, off = [], 0
    while True:
        js = net.get_json(f"https://api.nobelprize.org/2.1/laureates?limit=200"
                          f"&offset={off}", timeout=180, attempts=4)
        got = js.get("laureates", [])
        laur.extend(got)
        if len(got) < 200 or off > 1400:
            break
        off += 200
    return laur


def p_celebs_dates():
    laur = _laureates()
    rows = []
    for L in laur:
        nm = (L.get("knownName") or {}).get("en") or (L.get("fullName") or {}).get("en")
        b = L.get("birth") or {}
        d = L.get("death") or {}
        bd, dd = b.get("date"), d.get("date")
        city = (((b.get("place") or {}).get("city") or {}).get("en"))
        ctry = (((b.get("place") or {}).get("country") or {}).get("en"))

        def full(x):
            return bool(x) and len(x) == 10 and x[5:7] != "00" and x[8:10] != "00"
        for p in (L.get("nobelPrizes") or []):
            rows.append({"name": nm, "dob": bd if full(bd) else None,
                         "dod": dd if full(dd) else None, "city": city,
                         "country": ctry, "id": L.get("id"),
                         "cat": (p.get("category") or {}).get("en"),
                         "year": int(p.get("awardYear") or 0),
                         "wikidata": (L.get("wikidata") or {}).get("id")})
    print(f"  rows={len(rows)} with_dob={sum(1 for r in rows if r['dob'])} "
          f"with_dod={sum(1 for r in rows if r['dod'])} "
          f"with_city={sum(1 for r in rows if r['city'])}", flush=True)
    out = []
    variants = [
        # (key field, key mode, answer field)
        ("dob", "min", "city"), ("dob", "min", "country"),
        ("dod", "min", "dob"), ("dob", "max", "city"), ("dod", "max", "dob"),
    ]
    for cat in ("Physics", "Chemistry", "Physiology or Medicine", "Literature"):
        for (y0, y1) in ((1901, 1950), (1901, 1975), (1951, 2000)):
            for kf, mode, af in variants:
                sub = [r for r in rows if r["cat"] == cat and y0 <= r["year"] <= y1
                       and r.get(kf) and r.get(af)]
                if len(sub) < 15:
                    continue
                keys = [str(r[kf]) for r in sub]
                w = rr(keys, mode)
                if w["n_ties"] != 1:
                    continue
                i = w["index"]
                ans = [str(r[af]) for r in sub]
                cnt = ans.count(ans[i])
                rec = {"category": cat, "window": [y0, y1], "key": kf,
                       "mode": mode, "answer_field": af, **w,
                       "winner": sub[i]["name"], "answer": ans[i],
                       "wikidata": sub[i]["wikidata"],
                       "distinct_answers": len(set(ans)),
                       "count_of_answer": cnt,
                       "p_guess": round(cnt / len(ans), 6),
                       "award_year": sub[i]["year"]}
                # gate the shortlist on every P5 criterion at once
                rec["passes"] = bool(
                    w["n"] >= 15 and 0.08 <= w["depth"] <= 0.92
                    and abs(w["rho"] or 1) <= 0.95 and rec["p_guess"] <= 0.10)
                out.append(rec)
    ok = [o for o in out if o["passes"]]
    ok.sort(key=lambda o: (abs(o["depth"] - 0.5), o["p_guess"]))
    for o in ok[:10]:
        print(f"  OK {o['category'][:11]:11s} {o['window']} key={o['key']}/{o['mode']}"
              f" -> {o['answer_field']}={o['answer']!r} n={o['n']} d={o['depth']} "
              f"rho={o['rho']} p={o['p_guess']} ({o['winner']})", flush=True)
    print(f"  {len(ok)}/{len(out)} variants clear every criterion", flush=True)
    return {"n_variants": len(out), "usable": ok, "all": out[:200]}


def p_shop_sanity():
    """Is the top-salt instant coffee a real product or a contributor typo?"""
    prods, page, total = [], 1, None
    while page <= 4:
        js = net.get_json(
            "https://world.openfoodfacts.org/api/v2/search?"
            "categories_tags=en:instant-coffees&countries_tags_en=germany"
            "&fields=code,brands,product_name,categories_tags,nutriments"
            f"&page_size=100&page={page}", timeout=180, attempts=4)
        total = js.get("count") if total is None else total
        got = js.get("products", [])
        prods.extend(got)
        if len(got) < 100:
            break
        page += 1
        time.sleep(1.5)
    rows = [p for p in prods
            if (p.get("code") or "").isdigit()
            and isinstance(p.get("nutriments"), dict)
            and isinstance(p["nutriments"].get("salt_100g"), (int, float))]
    rows.sort(key=lambda p: -float(p["nutriments"]["salt_100g"]))
    top = [{"code": p["code"], "salt": p["nutriments"]["salt_100g"],
            "name": (p.get("product_name") or "")[:44], "brands": p.get("brands"),
            "n_cats": len(p.get("categories_tags") or [])} for p in rows[:10]]
    for t in top:
        print(f"   salt={t['salt']:>8} {t['code']} {t['brands']!r} {t['name']!r}",
              flush=True)
    # a caffeine-based key instead: intrinsic to coffee, less typo-prone
    caf = [p for p in prods
           if (p.get("code") or "").isdigit()
           and isinstance(p.get("nutriments"), dict)
           and isinstance(p["nutriments"].get("caffeine_100g"), (int, float))]
    out = {"count": total, "fetched": len(prods), "complete": len(prods) >= (total or 0),
           "n_salt": len(rows), "top_salt": top, "n_caffeine": len(caf)}
    for fld in ("proteins_100g", "carbohydrates_100g", "energy-kcal_100g",
                "sugars_100g", "fat_100g"):
        sub = [p for p in prods
               if (p.get("code") or "").isdigit()
               and isinstance(p.get("nutriments"), dict)
               and isinstance(p["nutriments"].get(fld), (int, float))]
        if len(sub) < 15:
            out[fld] = {"n": len(sub), "note": "too few"}
            continue
        vals = [float(p["nutriments"][fld]) for p in sub]
        w = rr([f"{v:014.4f}" for v in vals], "max")
        i = w["index"]
        codes = [p["code"] for p in sub]
        out[fld] = {**w, "code": codes[i], "value": vals[i],
                    "runner_up": sorted(vals, reverse=True)[1],
                    "name": (sub[i].get("product_name") or "")[:40],
                    "brands": sub[i].get("brands"),
                    "p_guess_code": round(codes.count(codes[i]) / len(codes), 6),
                    "sep_ratio": round(vals[i] / max(1e-9, sorted(vals, reverse=True)[1]), 3)}
        print(f"  {fld}: n={w['n']} d={w['depth']} rho={w['rho']} ties={w['n_ties']} "
              f"{vals[i]} vs {out[fld]['runner_up']} sep={out[fld]['sep_ratio']} "
              f"{out[fld]['name']!r}", flush=True)
    return out


def p_tv():
    raw = net.fetch(ct._IMDB_BASICS, timeout=900, attempts=3, binary=True)
    lines = gzip.decompress(raw).decode("utf-8", "replace").splitlines()
    hdr = lines[0].split("\t")
    ix = {k: hdr.index(k) for k in hdr}
    YEARS = {"1998", "2003", "1994", "2008", "1996", "2001", "1991", "2012"}
    GEN = ("Film-Noir", "Western", "Musical", "War", "Biography", "Sci-Fi",
           "Fantasy", "Mystery")
    buckets = collections.defaultdict(list)
    for ln in lines[1:]:
        f = ln.split("\t")
        if len(f) < len(hdr) or f[ix["titleType"]] != "movie":
            continue
        y = f[ix["startYear"]]
        if y not in YEARS:
            continue
        rt = f[ix["runtimeMinutes"]]
        if not rt.isdigit():
            continue
        g = f[ix["genres"]]
        for gg in GEN:
            if gg in g:
                buckets[(y, gg)].append((f[ix["tconst"]], int(rt),
                                         f[ix["primaryTitle"]]))
    print(f"  imdb parsed; {len(buckets)} (year,genre) buckets", flush=True)
    combos = []
    for (y, g), rows in sorted(buckets.items()):
        if not (10 <= len(rows) <= 400):
            continue
        rts = [r[1] for r in rows]
        w = rr([f"{v:06d}" for v in rts], "max")
        i = w["index"]
        ids = [r[0] for r in rows]
        combos.append({"year": int(y), "genre": g, **w, "tconst": rows[i][0],
                       "title": rows[i][2][:44], "runtime": rts[i],
                       "runner_up": sorted(rts, reverse=True)[1],
                       "p_guess": round(1 / len(set(ids)), 6)})
    good = [c for c in combos if c["n_ties"] == 1 and 0.08 <= c["depth"] <= 0.92
            and c["p_guess"] <= 0.10 and abs(c["rho"] or 1) <= 0.95
            and c["runtime"] > c["runner_up"]]
    good.sort(key=lambda c: abs(c["depth"] - 0.5))
    for c in good[:10]:
        print(f"  tv {c['year']}/{c['genre']:10s} n={c['n']} d={c['depth']} "
              f"rho={c['rho']} {c['tconst']} {c['runtime']}min (next "
              f"{c['runner_up']}) {c['title']!r}", flush=True)
    return {"n_combos": len(combos), "usable": good}


SECTIONS = [("celebs_dates", p_celebs_dates), ("shop_sanity", p_shop_sanity),
            ("tv", p_tv)]

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
