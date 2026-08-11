#!/usr/bin/env python3
"""keyvalidate.py -- is the surviving finance key real, or is it selection noise?

The repair sweep tried 22 candidates (11 seeds x 2 key families) and exactly one
passed: 2023/ALL under the percentile-rank-difference key, CUSIP 91282CJR3,
shallowest component depth 0.1332 against a 0.10 bar. One pass in 22 is the
signature of a key hunted until something cleared, so it does not get adopted on
the strength of having passed.

Three independent checks, none of which the search could have optimised against:

  1. HELD-OUT SEEDS. Years never touched by the search. If rank-difference is a
     sound key family it should clear the component-depth bar on some of them
     too. If 2023 is the only year it ever works, it is a property of that
     population, not of the key.
  2. BOOTSTRAP STABILITY. Resample the 2023 population with replacement and ask
     how often the same CUSIP wins. A percentile-rank key is sensitive to ties
     and to which rows are present; an answer that survives resampling is a real
     extremum, one that does not is an artefact of the exact row set.
  3. JACKKNIFE. Drop each row in turn and recompute. Reports how many single-row
     deletions change the answer.

Also resolves witnesses for the candidate, since a key that passes every
statistical gate is still unshippable without two independent operators.
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net  # noqa: E402
import category_traps as ct  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keyvalidate.json")
NUM, DEN = "indirect_bidder_accepted", "primary_dealer_accepted"
MIN_DEPTH = 0.10
HELD_OUT = [(2022, None), (2020, None), (2018, None), (2016, None),
            (2014, None), (2012, None), (2010, None), (2008, None)]
STATE = {"bar": MIN_DEPTH, "stages": {}}


def save():
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(STATE, fh, indent=1)
    os.replace(tmp, OUT)


def num(r, f):
    v = r.get(f)
    if v in (None, "", "null", "*"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch(year, stype):
    u = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
         "accounting/od/auctions_query?filter=auction_date:gte:%d-01-01,"
         "auction_date:lte:%d-12-31" % (year, year))
    if stype:
        u += ",security_type:eq:%s" % stype
    return net.get_json(u + "&page[size]=5000&sort=auction_date",
                        timeout=180, attempts=4).get("data", [])


def clean(raw):
    out = []
    for r in raw:
        x, y = num(r, NUM), num(r, DEN)
        if (r.get("cusip") or "").strip() and x is not None and y is not None and y:
            out.append(r)
    return out


def rankdiff(rows):
    n = len(rows)
    px = [v / n for v in ct._rankdata([num(r, NUM) for r in rows])]
    py = [v / n for v in ct._rankdata([num(r, DEN) for r in rows])]
    return [px[i] - py[i] for i in range(n)]


def winner_of(rows):
    k = rankdiff(rows)
    i = max(range(len(rows)), key=lambda j: k[j])
    return rows[i]["cusip"].strip(), k


def depths(rows, cusip):
    d = {}
    for f in (NUM, DEN):
        vals = [num(r, f) for r in rows]
        for dr in ("asc", "desc"):
            s = sorted(range(len(rows)),
                       key=(lambda i: -vals[i]) if dr == "desc" else (lambda i: vals[i]))
            rank = [rows[i]["cusip"].strip() for i in s].index(cusip) + 1
            d["%s:%s" % (f, dr)] = round(rank / len(rows), 4)
    return d


def stage_heldout():
    res = {}
    for year, st in HELD_OUT:
        tag = "%d/%s" % (year, st or "ALL")
        try:
            rows = clean(fetch(year, st))
        except Exception as e:  # noqa: BLE001
            res[tag] = {"error": str(e)[:70]}
            save()
            continue
        if len(rows) < 150:
            res[tag] = {"status": "SKIP", "n": len(rows)}
            save()
            continue
        c, k = winner_of(rows)
        top = sorted(k, reverse=True)
        d = depths(rows, c)
        worst = min(d.values())
        res[tag] = {"status": "PASS" if worst >= MIN_DEPTH else "FAIL",
                    "n": len(rows), "cusip": c,
                    "rel_separation": round((top[0] - top[1]) / abs(top[0]), 6)
                    if top[0] else None,
                    "shallowest_component_depth": worst, "component_depths": d}
        print("  held-out %-10s %-5s n=%-4s cusip=%-11s depth=%s"
              % (tag, res[tag]["status"], len(rows), c, worst), flush=True)
        save()
        time.sleep(1.0)
    ok = [v for v in res.values() if v.get("status") == "PASS"]
    tried = [v for v in res.values() if v.get("status") in ("PASS", "FAIL")]
    res["_summary"] = {"n_tried": len(tried), "n_pass": len(ok),
                       "pass_rate": round(len(ok) / len(tried), 3) if tried else None}
    return res


def stage_stability(rows, expect, B=400):
    random.seed(11)
    n = len(rows)
    hits, winners = 0, {}
    for _ in range(B):
        samp = [rows[random.randrange(n)] for _ in range(n)]
        c, _ = winner_of(samp)
        winners[c] = winners.get(c, 0) + 1
        if c == expect:
            hits += 1
    jack = 0
    for i in range(n):
        c, _ = winner_of(rows[:i] + rows[i + 1:])
        if c != expect:
            jack += 1
    top = sorted(winners.items(), key=lambda kv: -kv[1])[:5]
    return {"bootstrap_B": B, "bootstrap_hit_rate": round(hits / B, 4),
            "bootstrap_top_winners": top,
            "jackknife_n": n, "jackknife_flips": jack,
            "jackknife_flip_rate": round(jack / n, 4)}


def stage_witness(cusip):
    out = {}
    try:
        js = net.get_json("https://efts.sec.gov/LATEST/search-index?q=%%22%s%%22" % cusip,
                          timeout=90, attempts=3)
        out["sec_edgar_hits"] = ((js.get("hits") or {}).get("total") or {}).get("value")
    except Exception as e:  # noqa: BLE001
        out["sec_edgar_error"] = str(e)[:70]
    try:
        js = net.get_json("https://efts.sec.gov/LATEST/search-index?q=%22ZZ9999NOTACUSIP%22",
                          timeout=90, attempts=2)
        out["sec_control_hits"] = ((js.get("hits") or {}).get("total") or {}).get("value")
    except Exception as e:  # noqa: BLE001
        out["sec_control_error"] = str(e)[:70]
    try:
        r = net.fetch("https://api.openfigi.com/v3/mapping",
                      body=[{"idType": "ID_CUSIP", "idValue": cusip}],
                      timeout=90, attempts=3,
                      headers={"Content-Type": "application/json"})
        js = json.loads(r) if isinstance(r, (str, bytes)) else r
        d = (js[0].get("data") or []) if isinstance(js, list) and js else []
        out["openfigi"] = [{"figi": x.get("figi"), "name": x.get("name"),
                            "type": x.get("securityType")} for x in d[:3]]
    except Exception as e:  # noqa: BLE001
        out["openfigi_error"] = str(e)[:70]
    return out


def main():
    print("stage 1: held-out seeds", flush=True)
    STATE["stages"]["held_out"] = stage_heldout()
    save()
    print("  summary", STATE["stages"]["held_out"]["_summary"], flush=True)

    print("stage 2: resampling stability on 2023/ALL", flush=True)
    rows = clean(fetch(2023, None))
    c, _ = winner_of(rows)
    STATE["stages"]["candidate_reproduced"] = {"cusip": c, "n": len(rows)}
    STATE["stages"]["stability"] = stage_stability(rows, c)
    save()
    print("  reproduced %s over n=%d; %s" % (c, len(rows),
          {k: v for k, v in STATE["stages"]["stability"].items()
           if k != "bootstrap_top_winners"}), flush=True)
    print("  top bootstrap winners:",
          STATE["stages"]["stability"]["bootstrap_top_winners"], flush=True)

    print("stage 3: witnesses for %s" % c, flush=True)
    STATE["stages"]["witness"] = stage_witness(c)
    save()
    print(" ", STATE["stages"]["witness"], flush=True)

    ho = STATE["stages"]["held_out"]["_summary"]
    stab = STATE["stages"]["stability"]
    STATE["stages"]["VERDICT"] = {
        "held_out_pass_rate": ho["pass_rate"], "held_out_n": ho["n_tried"],
        "bootstrap_hit_rate": stab["bootstrap_hit_rate"],
        "jackknife_flip_rate": stab["jackknife_flip_rate"],
        "conclusion": "see interpretation; no automatic adopt",
    }
    STATE["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save()
    print(json.dumps(STATE["stages"]["VERDICT"], indent=1), flush=True)


if __name__ == "__main__":
    main()
