#!/usr/bin/env python3
"""depthsweep.py -- repair the finance key against the component-depth leak.

The flat rank gate failed both rekeys, but the two failures are not the same
thing and must not be treated the same way.

HEALTH. n_base is 51. Every `:desc` ordering puts the answer at rank 5-15 and
every `:asc` ordering at 34-47. That is one fact, not seven leaks: the trial is
recent, large and multi-site, so it sits high on every recency/size ordering and
low on their inverses. None of those fields is inferable from the key, which is
the count of declared secondary outcomes -- and the registry still refuses to
sort on SecondaryOutcomeCount and OutcomeMeasureCount. A solver reading the top
five of StudyFirstPostDate:desc has no way to know it is holding the answer.

FINANCE. Different, and fatal. The key is indirect_bidder_accepted divided by
primary_dealer_accepted, and the answer ranks 9th of 336 on
primary_dealer_accepted ASCENDING -- the key's own DENOMINATOR. A ratio is
dominated by a small denominator, so "smallest primary-dealer award" is the
first shortcut anyone derives from the key definition itself. Nine rows. The
numerator leaks nothing by comparison (ranks 51 and 67), which is exactly the
asymmetry the algebra predicts.

So the gate must be depth-normalised and scoped to KEY-DERIVABLE fields. This
sweep looks for a seed where the answer is deep on BOTH components.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "depthsweep.json")
NUM, DEN = "indirect_bidder_accepted", "primary_dealer_accepted"
MIN_COMPONENT_DEPTH = 0.10   # answer must be below the top decile of each component
MIN_ROWS = 150
SEEDS = [(2021, "Bill"), (2021, None), (2019, "Bill"), (2019, None), (2017, "Bill"),
         (2017, None), (2015, "Bill"), (2015, None), (2013, "Bill"), (2013, None),
         (2011, "Bill"), (2011, None), (2009, "Bill"), (2023, "Bill"), (2023, None)]
STATE = {"seeds": {}, "min_component_depth": MIN_COMPONENT_DEPTH}


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
    u += "&page[size]=5000&sort=auction_date"
    return net.get_json(u, timeout=180, attempts=4).get("data", [])


def depth_of(rows, cusip, field, direction):
    vals = [(num(r, field), (r.get("cusip") or "").strip()) for r in rows]
    if any(v is None for v, _ in vals):
        return None
    s = sorted(vals, key=(lambda t: -t[0]) if direction == "desc" else (lambda t: t[0]))
    rank = [c for _, c in s].index(cusip) + 1
    return {"rank": rank, "depth": round(rank / len(s), 4)}


def one(year, stype):
    rows = fetch(year, stype)
    keep = []
    for r in rows:
        x, y = num(r, NUM), num(r, DEN)
        if (r.get("cusip") or "").strip() and x is not None and y is not None and y:
            rr = dict(r)
            rr["_key"] = x / y
            keep.append(rr)
    if len(keep) < MIN_ROWS:
        return {"status": "REJECT", "reason": "too few rows", "n_rows": len(keep)}
    keep.sort(key=lambda r: -r["_key"])
    win, run = keep[0], keep[1]
    cusip = win["cusip"].strip()
    if sum(1 for r in keep if abs(r["_key"] - win["_key"]) < 1e-12) != 1:
        return {"status": "REJECT", "reason": "tie at extremum", "n_rows": len(keep)}
    comp = {}
    for f in (NUM, DEN):
        for d in ("asc", "desc"):
            comp["%s:%s" % (f, d)] = depth_of(keep, cusip, f, d)
    shallow = {k: v for k, v in comp.items()
               if v and v["depth"] < MIN_COMPONENT_DEPTH}
    sep = round((win["_key"] - run["_key"]) / win["_key"], 6) if win["_key"] else None
    return {"status": "REJECT" if shallow else "ACCEPT",
            "reason": ("component shallow: %s" % sorted(shallow)) if shallow else "",
            "n_rows": len(keep), "winner_cusip": cusip,
            "winner_key": round(win["_key"], 6), "runner_up_key": round(run["_key"], 6),
            "rel_separation": sep,
            "n_distinct_cusips": len({(r.get("cusip") or "").strip() for r in keep}),
            "component_depths": comp,
            "shallowest_component_depth": min((v["depth"] for v in comp.values() if v),
                                              default=None)}


def main():
    for year, stype in SEEDS:
        tag = "%d/%s" % (year, stype or "ALL")
        try:
            r = one(year, stype)
        except Exception as e:  # noqa: BLE001
            r = {"status": "ERROR", "reason": str(e)[:90]}
        STATE["seeds"][tag] = r
        save()
        print("%-12s %-7s n=%-5s cusip=%-11s sep=%-9s shallowest component depth=%-7s %s"
              % (tag, r["status"], r.get("n_rows"), r.get("winner_cusip"),
                 r.get("rel_separation"), r.get("shallowest_component_depth"),
                 r.get("reason", "")), flush=True)
        time.sleep(1.0)
    acc = {k: v for k, v in STATE["seeds"].items() if v.get("status") == "ACCEPT"}
    # among passing seeds prefer the DEEPEST component exposure, then more rows
    best = max(acc.items(), key=lambda kv: (kv[1]["shallowest_component_depth"],
                                            kv[1]["n_rows"])) if acc else None
    STATE["VERDICT"] = {"n_seeds": len(SEEDS), "n_accept": len(acc),
                        "accepting": sorted(acc),
                        "best_seed": best[0] if best else None,
                        "best": best[1] if best else None,
                        "conclusion": ("finance repairable on a deeper seed" if best
                                       else "no seed hides both components")}
    save()
    print(json.dumps(STATE["VERDICT"], indent=1)[:1400], flush=True)


if __name__ == "__main__":
    main()
