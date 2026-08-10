"""Round 3. Interrogate the two new anomalies, then settle the remaining keys.

finance_season : is 'date of the annual maximum' seasonally predictable?
politics_alpha : alphabetical-FIRST EO title repeats across years -- test max/filtered
business_state : within-state rho of R&D value against SEC return order
history_alpha  : alphabetical laureate-surname key for the >=3-laureate prizes
celebs_alpha   : laureates ranked alphabetically, answer = date of birth
video_games    : alphabetical title key (fixed: _steam_date returns a tuple)
shopping_pages : paginate a category to full coverage, rank by a real nutrient
tv             : single-pass IMDb scan for an interior winner
"""
import json, os, sys, time, collections, urllib.parse as up
import net
import category_traps as ct

OUT = "probe_redesign3.json"
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


# ------------------------------------------------- 1. finance seasonality
def p_finance_season():
    """The 2012/2013/2018 maxima all landed on 30 April. If the annual argmax
    concentrates on a handful of tax-calendar month-ends then the date is
    guessable by a domain-aware solver even though every date is distinct,
    and T2's uniform-guess null understates the true hit rate."""
    prev = json.load(open("probe_redesign.json"))["finance"]["scan"]
    rows = [o for o in prev if o.get("date")]
    rows.append({"year": 2015, "date": "2015-12-31", "depth": 1.0, "n": 252})
    months = [int(o["date"][5:7]) for o in rows]
    days = [o["date"][8:10] for o in rows]
    hist = collections.Counter(months)
    n = len(months)
    # exact test: is the modal month more common than a uniform-month null allows?
    from math import comb
    k = max(hist.values())
    p1 = 1.0 / 12
    # P(some month gets >= k) via Bonferroni-free union bound on 12 months
    tail = sum(comb(n, j) * p1 ** j * (1 - p1) ** (n - j) for j in range(k, n + 1))
    p_union = min(1.0, 12 * tail)
    monthend = sum(1 for o in rows
                   if int(o["date"][8:10]) >= 28)
    p_me_null = 3.0 / 21  # ~3 of ~21 business days per month sit on day >= 28
    tail_me = sum(comb(n, j) * p_me_null ** j * (1 - p_me_null) ** (n - j)
                  for j in range(monthend, n + 1))
    out = {"n_years": n, "rows": [(o["year"], o["date"], o.get("depth")) for o in rows],
           "month_hist": sorted(hist.items()), "modal_month": hist.most_common(1)[0],
           "p_modal_month_union_bound": round(p_union, 6),
           "n_on_day_ge_28": monthend,
           "p_monthend_binomial_upper_tail": round(tail_me, 8),
           "distinct_months": len(hist)}
    print(f"  season: months={sorted(hist.items())} modal={hist.most_common(1)[0]} "
          f"p_union={out['p_modal_month_union_bound']}", flush=True)
    print(f"  season: {monthend}/{n} maxima fall on day>=28, "
          f"binomial upper tail p={out['p_monthend_binomial_upper_tail']}", flush=True)
    return out


# ------------------------------------------------ 2. politics alphabetical
def p_politics_alpha():
    out = []
    for y in (1998, 2003, 1996, 2007, 2011, 1994, 2009, 2015):
        u = ("https://www.federalregister.gov/api/v1/documents.json?"
             "conditions[type][]=PRESDOCU"
             "&conditions[presidential_document_type][]=executive_order"
             f"&conditions[publication_date][gte]={y}-01-01"
             f"&conditions[publication_date][lte]={y}-12-31"
             "&fields[]=executive_order_number&fields[]=title"
             "&fields[]=publication_date&per_page=1000&order=oldest")
        try:
            js = net.get_json(u, timeout=120, attempts=3)
            rows = [r for r in js.get("results", [])
                    if r.get("executive_order_number") and r.get("title")]
            if len(rows) < 10:
                continue
            keys = [r["title"].strip().lower() for r in rows]
            rec = {"year": y, "n": len(rows),
                   "alpha_min": {**rr(keys, "min"),
                                 "title": rows[rr(keys, 'min')['index']]["title"][:62],
                                 "eo": rows[rr(keys, 'min')['index']]["executive_order_number"]},
                   "alpha_max": {**rr(keys, "max"),
                                 "title": rows[rr(keys, 'max')['index']]["title"][:62],
                                 "eo": rows[rr(keys, 'max')['index']]["executive_order_number"]}}
            # drop titles that begin with a digit: those are the recurring
            # "<year> Amendments to the Manual for Courts-Martial" orders
            keep = [(k, r) for k, r in zip(keys, rows) if not k[:1].isdigit()]
            if len(keep) >= 10:
                k2 = [k for k, _ in keep]
                w = rr(k2, "min")
                rec["alpha_min_nodigit"] = {
                    **w, "title": keep[w["index"]][1]["title"][:62],
                    "eo": keep[w["index"]][1]["executive_order_number"]}
            out.append(rec)
            a, b = rec["alpha_min"], rec["alpha_max"]
            c = rec.get("alpha_min_nodigit", {})
            print(f"  pol {y}: min d={a['depth']} EO{a['eo']} {a['title'][:34]!r}", flush=True)
            print(f"          max d={b['depth']} EO{b['eo']} {b['title'][:34]!r}", flush=True)
            print(f"       nodigit d={c.get('depth')} EO{c.get('eo')} "
                  f"{str(c.get('title'))[:34]!r}", flush=True)
        except Exception as e:
            out.append({"year": y, "error": f"{type(e).__name__}: {e}"[:160]})
    for lab in ("alpha_min", "alpha_max", "alpha_min_nodigit"):
        ts = [o[lab]["title"].lower()[:28] for o in out if o.get(lab)]
        rep = collections.Counter(ts).most_common(1)
        print(f"  >> {lab}: most repeated winning title {rep} of {len(ts)} years",
              flush=True)
    return out


# -------------------------------------------------- 3. business within-state
def p_business_state():
    u = ("https://data.sec.gov/api/xbrl/frames/us-gaap/"
         "ResearchAndDevelopmentExpense/USD/CY2015.json")
    rows = net.get_json(u, timeout=180, attempts=3).get("data", [])
    out = []
    for loc in ("US-TX", "US-MA", "US-CA", "US-NY", "US-NJ", "US-PA",
                "US-IL", "US-OH", "US-CO", "US-MN", "US-WA", "US-MI"):
        sub = [r for r in rows if r.get("loc") == loc and r.get("val") is not None]
        if len(sub) < 20:
            continue
        vals = [float(r["val"]) for r in sub]
        w = rr([f"{v:020.0f}" for v in vals], "max")
        ciks = [str(r["cik"]) for r in sub]
        i = w["index"]
        out.append({"loc": loc, **w, "winner": sub[i]["entityName"],
                    "cik": sub[i]["cik"], "val": vals[i],
                    "runner_up": sorted(vals, reverse=True)[1],
                    "sep_ratio": round(vals[i] / max(1.0, sorted(vals, reverse=True)[1]), 3),
                    "distinct_ciks": len(set(ciks)),
                    "p_guess_cik": round(1 / len(set(ciks)), 6),
                    "accn": sub[i]["accn"], "end": sub[i]["end"]})
        print(f"  biz {loc}: n={w['n']} d={w['depth']} rho={w['rho']} "
              f"sep={out[-1]['sep_ratio']} {sub[i]['entityName'][:30]} "
              f"cik={sub[i]['cik']}", flush=True)
    return out


# ------------------------------------------- 4/5. Nobel alphabetical keys
def p_nobel_alpha():
    js = net.get_json("https://api.nobelprize.org/2.1/nobelPrizes?limit=1000"
                      "&nobelPrizeYear=1901&yearTo=2024", timeout=180, attempts=4)
    prizes = js.get("nobelPrizes", [])

    def surname(L):
        fam = (L.get("familyName") or {}).get("en")
        return (fam or (L.get("fullName") or {}).get("en") or "").strip().lower()

    hist = []
    for cat in ("Physics", "Chemistry", "Physiology or Medicine"):
        for (y0, y1) in ((1901, 2000), (1901, 1990), (1901, 2024)):
            sub = [p for p in prizes
                   if p.get("category", {}).get("en") == cat
                   and y0 <= int(p["awardYear"]) <= y1
                   and len(p.get("laureates") or []) >= 3]
            if len(sub) < 5:
                continue
            keys = [surname((p["laureates"])[0]) for p in sub]
            if len(set(keys)) < len(keys):
                pass
            w = rr(keys, "min")
            yrs = [int(p["awardYear"]) for p in sub]
            hist.append({"category": cat, "window": [y0, y1], **w,
                         "answer_year": yrs[w["index"]],
                         "winner_surname": keys[w["index"]],
                         "distinct_years": len(set(yrs)),
                         "p_guess_year": round(yrs.count(yrs[w["index"]]) / len(yrs), 6)})
            print(f"  hist-alpha {cat[:10]:10s} {y0}-{y1}: n={w['n']} d={w['depth']} "
                  f"rho={w['rho']} ties={w['n_ties']} -> {yrs[w['index']]} "
                  f"({keys[w['index']]}) p={hist[-1]['p_guess_year']}", flush=True)

    celeb = []
    for cat in ("Physics", "Chemistry", "Physiology or Medicine", "Peace",
                "Literature"):
        for (y0, y1) in ((1901, 1950), (1951, 2000), (1901, 1975)):
            rowsL = []
            for p in prizes:
                if p.get("category", {}).get("en") != cat:
                    continue
                if not (y0 <= int(p["awardYear"]) <= y1):
                    continue
                for L in (p.get("laureates") or []):
                    nm = (L.get("fullName") or {}).get("en")
                    bd = ((L.get("birth") or {}).get("date"))
                    if nm and bd and len(bd) == 10 and not bd.endswith("-00-00"):
                        rowsL.append({"name": nm, "dob": bd, "id": L.get("id"),
                                      "year": int(p["awardYear"])})
            if len(rowsL) < 10:
                continue
            keys = [r["name"].strip().lower() for r in rowsL]
            w = rr(keys, "min")
            dobs = [r["dob"] for r in rowsL]
            celeb.append({"category": cat, "window": [y0, y1], **w,
                          "winner": rowsL[w["index"]]["name"],
                          "answer_dob": dobs[w["index"]],
                          "award_year": rowsL[w["index"]]["year"],
                          "distinct_dob": len(set(dobs)),
                          "p_guess_dob": round(dobs.count(dobs[w["index"]]) / len(dobs), 6),
                          "dob_year_in_window": y0 <= int(dobs[w["index"]][:4]) <= y1})
            print(f"  celeb-alpha {cat[:10]:10s} {y0}-{y1}: n={w['n']} d={w['depth']} "
                  f"rho={w['rho']} -> {dobs[w['index']]} ({rowsL[w['index']]['name'][:26]}) "
                  f"p={celeb[-1]['p_guess_dob']}", flush=True)
    return {"history_alpha": hist, "celebrities_alpha": celeb}


# ---------------------------------------------------------- 6. video games
def p_video_games():
    sets = {"primary": (3830, 6910, 8930, 22380, 105600, 250900, 39210, 271590,
                        292030, 377160, 578080, 1091500),
            "alt": (400, 620, 730, 4000, 220, 550, 240, 70, 242760, 413150,
                    431960, 322330)}
    out = {}
    for label, appids in sets.items():
        rowsA = []
        for a in appids:
            try:
                js = net.get_json(
                    f"https://store.steampowered.com/api/appdetails?appids={a}"
                    f"&cc=us&l=english", timeout=60, attempts=3)
                d = js.get(str(a)) or {}
                if not d.get("success"):
                    continue
                dd = d["data"]
                devs = dd.get("developers") or []
                if dd.get("name") and devs:
                    rowsA.append({"appid": a, "name": dd["name"], "dev": devs[0],
                                  "rel": ct._steam_date(
                                      (dd.get("release_date") or {}).get("date"))})
            except Exception as e:
                print(f"   appid {a}: {type(e).__name__}", flush=True)
        rec = {"n_rows": len(rowsA)}
        if len(rowsA) >= 5:
            rel = [r["rel"] for r in rowsA if r["rel"]]
            if len(rel) == len(rowsA):
                rec["by_release"] = rr(["%04d%02d%02d" % t for t in rel], "min")
            titles = [r["name"].strip().lower() for r in rowsA]
            w = rr(titles, "min")
            devs = [r["dev"] for r in rowsA]
            rec["by_title"] = {**w, "winner_title": rowsA[w["index"]]["name"],
                               "winner_dev": rowsA[w["index"]]["dev"],
                               "distinct_devs": len(set(devs)),
                               "p_guess_dev": round(
                                   devs.count(rowsA[w["index"]]["dev"]) / len(devs), 6)}
            rec["titles_sorted"] = sorted(titles)
        out[label] = rec
        b = rec.get("by_title", {})
        print(f"  vg {label}: n={rec['n_rows']} title d={b.get('depth')} "
              f"rho={b.get('rho')} dev={b.get('winner_dev')} "
              f"p={b.get('p_guess_dev')} | release d="
              f"{rec.get('by_release',{}).get('depth')} "
              f"rho={rec.get('by_release',{}).get('rho')}", flush=True)
    return out


SECTIONS = [("finance_season", p_finance_season),
            ("politics_alpha", p_politics_alpha),
            ("business_state", p_business_state),
            ("nobel_alpha", p_nobel_alpha),
            ("video_games", p_video_games)]

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
