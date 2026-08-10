"""Ordering reframes that reuse already-cached endpoints.

video games : rank the 12 Steam titles ALPHABETICALLY (orthogonal to appid order)
tv          : re-seed so the winner is not at index 0
history     : widen the prize window until n_ranked >= 5
celebrities : widen the repeat-laureate window until n_ranked >= 5
art         : the cross-cohort run died on an unencoded space in the Met query
"""
import json, os, sys, time, urllib.parse as up
import net
import category_traps as ct

OUT = "probe_redesign2.json"
R = json.load(open(OUT)) if os.path.exists(OUT) else {}


def save(k, v):
    R[k] = v
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(R, fh, indent=2, default=str)
    os.replace(tmp, OUT)
    print(f"[saved] {k}", flush=True)


def depth(i, n):
    return round(i / (n - 1), 4) if n > 1 else 0.0


def rank_report(keys, mode="min"):
    """Position + monotonicity of the extremum of `keys` in the given order."""
    tgt = min(keys) if mode == "min" else max(keys)
    ties = [i for i, k in enumerate(keys) if k == tgt]
    i = ties[0]
    return {"n": len(keys), "index": i, "depth": depth(i, len(keys)),
            "n_ties": len(ties), "distinct_keys": len(set(keys)),
            "spearman_key_vs_order": ct._spearman(list(range(len(keys))),
                                                  ct._rankdata(keys)),
            "winner_is_first": i == 0, "winner_is_last": i == len(keys) - 1}


# ------------------------------------------------------------ video games
def p_video_games():
    sets = {
        "primary": (3830, 6910, 8930, 22380, 105600, 250900, 39210, 271590,
                    292030, 377160, 578080, 1091500),
        "alt": (400, 620, 730, 4000, 220, 550, 240, 70, 242760, 413150,
                431960, 322330),
    }
    out = {}
    for label, appids in sets.items():
        rows = []
        for a in appids:
            try:
                u = f"https://store.steampowered.com/api/appdetails?appids={a}&cc=us&l=english"
                js = net.get_json(u, timeout=60, attempts=3)
                d = js.get(str(a)) or {}
                if not d.get("success"):
                    continue
                dd = d["data"]
                devs = dd.get("developers") or []
                rows.append({"appid": a, "name": dd.get("name"),
                             "dev": devs[0] if devs else None,
                             "release": (dd.get("release_date") or {}).get("date")})
            except Exception as e:
                print(f"   appid {a}: {type(e).__name__}", flush=True)
        rows = [r for r in rows if r["name"] and r["dev"]]
        rec = {"n_rows": len(rows)}
        if len(rows) >= 5:
            # OLD key: release date (monotone in appid -> rho 0.993)
            dates = [ct._steam_date(r["release"]) for r in rows]
            if all(dates):
                rec["by_release"] = rank_report([d.isoformat() for d in dates], "min")
                rec["by_release"]["winner_dev"] = rows[
                    rec["by_release"]["index"]]["dev"]
            # NEW key: title, alphabetically
            titles = [r["name"].strip().lower() for r in rows]
            rec["by_title"] = rank_report(titles, "min")
            j = rec["by_title"]["index"]
            rec["by_title"].update({"winner_title": rows[j]["name"],
                                    "winner_dev": rows[j]["dev"]})
            devs = [r["dev"] for r in rows]
            rec["distinct_devs"] = len(set(devs))
            rec["dev_count_of_winner"] = devs.count(rows[j]["dev"])
            rec["p_guess_dev"] = round(devs.count(rows[j]["dev"]) / len(devs), 6)
            rec["titles"] = sorted(titles)
        out[label] = rec
        b = rec.get("by_title", {})
        print(f"  vg {label}: n={rec['n_rows']} title-key depth={b.get('depth')} "
              f"rho={b.get('spearman_key_vs_order')} dev={b.get('winner_dev')} "
              f"p_guess={rec.get('p_guess_dev')}", flush=True)
    return out


# --------------------------------------------------------------- history
def p_nobel():
    """One fetch of the whole Nobel prize table, then test many windows."""
    js = net.get_json("https://api.nobelprize.org/2.1/nobelPrizes?limit=1000"
                      "&nobelPrizeYear=1901&yearTo=2024", timeout=180, attempts=4)
    prizes = js.get("nobelPrizes", [])
    print(f"  nobel: {len(prizes)} prize records", flush=True)

    def lauds(p):
        return p.get("laureates") or []

    hist, celeb = [], []
    # history: prizes divided among >= 3 laureates, ranked by year
    for cat in ("Physics", "Chemistry", "Physiology or Medicine"):
        for (y0, y1) in ((1901, 1935), (1901, 1970), (1901, 2000), (1901, 2024)):
            sub = [p for p in prizes
                   if (p.get("category", {}).get("en") == cat)
                   and y0 <= int(p["awardYear"]) <= y1
                   and len(lauds(p)) >= 3]
            years = [int(p["awardYear"]) for p in sub]
            rec = {"category": cat, "window": [y0, y1], "n_ranked": len(sub)}
            if sub:
                rr = rank_report([f"{y:04d}" for y in years], "min")
                rec.update(rr)
                rec["answer_year"] = years[rr["index"]]
                rec["distinct_years"] = len(set(years))
                rec["p_guess_year"] = round(
                    years.count(years[rr["index"]]) / len(years), 6)
            hist.append(rec)
            print(f"  hist {cat[:12]:12s} {y0}-{y1}: n_ranked={len(sub)} "
                  f"ans={rec.get('answer_year')} p={rec.get('p_guess_year')}",
                  flush=True)

    # celebrities: individuals with more than one prize, ranked by 2nd award year
    from collections import defaultdict
    for (y0, y1) in ((1901, 1960), (1901, 1980), (1901, 2024)):
        seen = defaultdict(list)
        for p in prizes:
            if not (y0 <= int(p["awardYear"]) <= y1):
                continue
            for L in lauds(p):
                nm = (L.get("fullName") or {}).get("en")
                if nm and L.get("id"):
                    seen[(L["id"], nm)].append(
                        (int(p["awardYear"]), p.get("category", {}).get("en")))
        rep = {k: sorted(v) for k, v in seen.items() if len(v) >= 2}
        rows = [{"name": k[1], "second_year": v[1][0],
                 "prizes": v} for k, v in rep.items()]
        rows.sort(key=lambda r: r["second_year"])
        rec = {"window": [y0, y1], "n_ranked": len(rows)}
        if rows:
            yrs = [r["second_year"] for r in rows]
            rr = rank_report([f"{y:04d}" for y in yrs], "min")
            rec.update(rr)
            rec["answer_year"] = yrs[rr["index"]]
            rec["winner"] = rows[rr["index"]]["name"]
            rec["all"] = [(r["name"], r["second_year"]) for r in rows]
            rec["distinct_years"] = len(set(yrs))
            rec["p_guess_year"] = round(yrs.count(yrs[rr["index"]]) / len(yrs), 6)
        celeb.append(rec)
        print(f"  celeb {y0}-{y1}: n_ranked={len(rows)} ans={rec.get('answer_year')} "
              f"{rec.get('winner')} p={rec.get('p_guess_year')}", flush=True)
    return {"n_prizes": len(prizes), "history": hist, "celebrities": celeb}


# -------------------------------------------------------------------- tv
def p_tv():
    """Re-seed gen_tv so the winner is not at index 0 of the IMDb scan order."""
    import gzip, io
    raw = net.fetch(ct._IMDB_BASICS, timeout=900, attempts=3, binary=True)
    lines = gzip.decompress(raw).decode("utf-8", "replace").splitlines()
    hdr = lines[0].split("\t")
    ix = {k: hdr.index(k) for k in hdr}
    print(f"  imdb: {len(lines)-1} title rows", flush=True)
    combos = []
    for year in (1998, 2003, 1994, 2008, 1996, 2001, 1991, 2012):
        for genre in ("Film-Noir", "Western", "Musical", "War", "Biography",
                      "Sci-Fi", "Fantasy", "Mystery"):
            rows = []
            for ln in lines[1:]:
                f = ln.split("\t")
                if len(f) < len(hdr):
                    continue
                if f[ix["titleType"]] != "movie":
                    continue
                if f[ix["startYear"]] != str(year):
                    continue
                if genre not in f[ix["genres"]]:
                    continue
                rt = f[ix["runtimeMinutes"]]
                if not rt.isdigit():
                    continue
                rows.append((f[ix["tconst"]], int(rt), f[ix["primaryTitle"]]))
            if not (10 <= len(rows) <= 400):
                continue
            rts = [r[1] for r in rows]
            rr = rank_report([f"{r:06d}" for r in rts], "max")
            ids = [r[0] for r in rows]
            combos.append({"year": year, "genre": genre, "n": len(rows),
                           "index": rr["index"], "depth": rr["depth"],
                           "n_ties": rr["n_ties"],
                           "rho": rr["spearman_key_vs_order"],
                           "tconst": rows[rr["index"]][0],
                           "title": rows[rr["index"]][2],
                           "runtime": rts[rr["index"]],
                           "runner_up": sorted(rts, reverse=True)[1],
                           "p_guess": round(1 / len(set(ids)), 6)})
    good = [c for c in combos if c["n_ties"] == 1 and 0.08 <= c["depth"] <= 0.92
            and c["p_guess"] <= 0.10 and abs(c["rho"] or 0) <= 0.95]
    good.sort(key=lambda c: abs(c["depth"] - 0.5))
    for c in good[:8]:
        print(f"  tv {c['year']}/{c['genre']:10s} n={c['n']} depth={c['depth']} "
              f"rho={c['rho']} {c['tconst']} {c['runtime']}min", flush=True)
    return {"n_combos": len(combos), "interior_ok": good, "all": combos}


# ------------------------------------------------------------------- art
def p_art():
    """cross_cohort died on a raw space in the Met search query."""
    out = []
    for artist in ("Rembrandt", "Claude Monet", "Vincent van Gogh"):
        rec = {"artist": artist}
        for label, q in (("raw", artist), ("quoted", up.quote(artist))):
            u = ("https://collectionapi.metmuseum.org/public/collection/v1/"
                 f"search?hasImages=true&q={q}")
            try:
                js = net.get_json(u, timeout=90, attempts=2)
                rec[label] = {"ok": True, "total": js.get("total")}
            except Exception as e:
                rec[label] = {"ok": False, "err": f"{type(e).__name__}"}
        out.append(rec)
        print(f"  art {artist}: raw={rec['raw']} quoted={rec['quoted']}", flush=True)
    return out


SECTIONS = [("video_games", p_video_games), ("nobel", p_nobel),
            ("art", p_art), ("tv", p_tv)]

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
