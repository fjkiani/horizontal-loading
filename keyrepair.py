#!/usr/bin/env python3
"""keyrepair.py -- replace the ratio key, which is structurally leaky.

The depth sweep rejected 15 of 15 seeds, and `primary_dealer_accepted:asc` was
shallow in every one. That is not bad luck in the seed grid, it is a property of
the key family. For a ratio x/y over a heavy-tailed denominator, the argmax is
drawn almost surely from the smallest-y tail: y spans orders of magnitude, x
does not vary enough to compensate, so "sort ascending on the denominator" finds
the answer in a handful of rows no matter which year is chosen. Any ratio key
whose denominator is a served field is one sorted request away.

Note also why the depth gate must stay scoped to the key's own inputs rather
than sweeping every served field: with 74 orderings, demanding depth >= 0.10 in
all of them is close to unsatisfiable -- under independence a random row clears
it with probability 0.9^74, about 4 in 10,000. Orderings are correlated so the
true figure is higher, but a full sweep at that bar rejects almost everything
including honest keys. The solver-derivable inputs are the right scope.

Two replacement families that are not tail-dominated by construction:

  RESIDUAL  fit indirect on primary across the population by least squares and
            rank by the residual. The largest residual is the auction that most
            departs from the crowd's relation, which need not be extreme in
            either field.
  RANKDIFF  convert both fields to within-population percentile ranks and take
            the difference. Scale-free, and bounded, so no tail dominates.

Each candidate is gated on component depth, rank-equivalence against every
served numeric field, uniqueness of the argmax, guessability, and correlation
with the order the API returns records in.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net  # noqa: E402
import category_traps as ct  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keyrepair.json")
NUM, DEN = "indirect_bidder_accepted", "primary_dealer_accepted"
MIN_DEPTH = 0.10
MAX_EQ_RHO = 0.98
MAX_RHO_API = 0.45
MAX_UNIFORM = 0.10
MIN_ROWS = 150
SEEDS = [(2021, "Bill"), (2021, None), (2019, "Bill"), (2019, None), (2017, "Bill"),
         (2017, None), (2015, None), (2013, None), (2011, "Bill"), (2023, "Bill"),
         (2023, None)]
STATE = {"candidates": {}}


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


def spear(a, b):
    ra, rb = ct._rankdata(a), ct._rankdata(b)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((x - mb) ** 2 for x in rb) ** 0.5
    if not va or not vb:
        return None
    return round(sum((ra[i] - ma) * (rb[i] - mb) for i in range(n)) / (va * vb), 4)


def pct(vals):
    r = ct._rankdata(vals)
    n = len(vals)
    return [x / n for x in r]


def fetch(year, stype):
    u = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
         "accounting/od/auctions_query?filter=auction_date:gte:%d-01-01,"
         "auction_date:lte:%d-12-31" % (year, year))
    if stype:
        u += ",security_type:eq:%s" % stype
    return net.get_json(u + "&page[size]=5000&sort=auction_date",
                        timeout=180, attempts=4).get("data", [])


def depth(rows, cusip, key, direction):
    s = sorted(range(len(rows)), key=(lambda i: -key[i]) if direction == "desc"
               else (lambda i: key[i]))
    rank = [rows[i]["cusip"].strip() for i in s].index(cusip) + 1
    return round(rank / len(rows), 4)


def assess(rows, keyvals, label):
    idx = max(range(len(rows)), key=lambda i: keyvals[i])
    top = sorted(keyvals, reverse=True)
    if sum(1 for v in keyvals if abs(v - top[0]) < 1e-12) != 1:
        return {"status": "REJECT", "reason": "tie at extremum"}
    cusip = rows[idx]["cusip"].strip()
    sep = round((top[0] - top[1]) / abs(top[0]), 6) if top[0] else None
    comp = {}
    for f in (NUM, DEN):
        vals = [num(r, f) for r in rows]
        for d in ("asc", "desc"):
            comp["%s:%s" % (f, d)] = depth(rows, cusip, vals, d)
    shallow = sorted(k for k, v in comp.items() if v < MIN_DEPTH)
    # rank-equivalence against every served numeric field
    fields, eq = set(), []
    for r in rows[:50]:
        fields.update(r.keys())
    swept = 0
    for f in sorted(fields):
        vals = [num(r, f) for r in rows]
        if any(v is None for v in vals):
            continue
        swept += 1
        rho = spear(keyvals, vals)
        if rho is not None and abs(rho) >= MAX_EQ_RHO:
            eq.append({"field": f, "rho": rho})
    rho_api = spear(keyvals, list(range(len(rows))))
    distinct = len({round(v, 10) for v in keyvals})
    p_uni = round(1.0 / max(1, len({(r.get("cusip") or "").strip() for r in rows})), 6)
    fails = []
    if shallow:
        fails.append("COMPONENT_SHALLOW:%s" % shallow)
    if eq:
        fails.append("EQUIVALENT:%s" % [e["field"] for e in eq])
    if rho_api is not None and abs(rho_api) > MAX_RHO_API:
        fails.append("TRACKS_API_ORDER")
    if p_uni > MAX_UNIFORM:
        fails.append("GUESSABLE")
    return {"status": "ACCEPT" if not fails else "REJECT", "fails": fails,
            "label": label, "winner_cusip": cusip, "n_rows": len(rows),
            "rel_separation": sep, "component_depths": comp,
            "shallowest_component_depth": min(comp.values()),
            "n_fields_swept": swept, "equivalent_served_fields": eq,
            "rho_vs_api_order": rho_api, "n_distinct_keys": distinct,
            "p_uniform": p_uni}


def main():
    for year, stype in SEEDS:
        tag = "%d/%s" % (year, stype or "ALL")
        try:
            raw = fetch(year, stype)
        except Exception as e:  # noqa: BLE001
            STATE["candidates"][tag] = {"error": str(e)[:80]}
            save()
            continue
        rows = []
        for r in raw:
            x, y = num(r, NUM), num(r, DEN)
            if (r.get("cusip") or "").strip() and x is not None and y is not None and y:
                rows.append(r)
        if len(rows) < MIN_ROWS:
            STATE["candidates"][tag] = {"status": "REJECT", "reason": "too few rows"}
            save()
            continue
        xs = [num(r, NUM) for r in rows]
        ys = [num(r, DEN) for r in rows]
        n = len(rows)
        mx, my = sum(xs) / n, sum(ys) / n
        sxy = sum((ys[i] - my) * (xs[i] - mx) for i in range(n))
        syy = sum((y - my) ** 2 for y in ys)
        b = sxy / syy if syy else 0.0
        a = mx - b * my
        resid = [xs[i] - (a + b * ys[i]) for i in range(n)]
        px, py = pct(xs), pct(ys)
        rankdiff = [px[i] - py[i] for i in range(n)]
        out = {}
        for label, kv in (("residual_indirect_on_primary", resid),
                          ("rank_difference", rankdiff)):
            out[label] = assess(rows, kv, label)
            r = out[label]
            print("%-12s %-30s %-7s n=%-4s cusip=%-11s sep=%-9s depth=%-7s %s"
                  % (tag, label, r["status"], r.get("n_rows"), r.get("winner_cusip"),
                     r.get("rel_separation"), r.get("shallowest_component_depth"),
                     ";".join(r.get("fails", []))[:60]), flush=True)
        out["ols"] = {"slope": round(b, 6), "intercept": round(a, 3)}
        STATE["candidates"][tag] = out
        save()
        time.sleep(1.0)
    acc = []
    for tag, v in STATE["candidates"].items():
        for label in ("residual_indirect_on_primary", "rank_difference"):
            c = (v or {}).get(label) or {}
            if c.get("status") == "ACCEPT":
                acc.append({"seed": tag, "key": label,
                            "depth": c["shallowest_component_depth"],
                            "sep": c["rel_separation"], "n": c["n_rows"],
                            "cusip": c["winner_cusip"]})
    acc.sort(key=lambda d: -d["depth"])
    STATE["VERDICT"] = {"n_accept": len(acc), "accepting": acc[:10],
                        "best": acc[0] if acc else None,
                        "conclusion": ("finance repairable with a non-ratio key"
                                       if acc else "no non-ratio key passes either")}
    save()
    print(json.dumps(STATE["VERDICT"], indent=1)[:1200], flush=True)


if __name__ == "__main__":
    main()
