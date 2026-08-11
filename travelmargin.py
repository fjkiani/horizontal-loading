"""travelmargin.py -- is the travel answer inside its own verification tolerance?

travelseeds.py measured the winning margin, in degrees of latitude, for each
travel seed:

    idx 0  {}         2.0425
    idx 1  LH / FRA   0.0238     <-- about 2.6 km
    idx 3  OS / VIE   0.3207
    idx 4  LO / WAW   0.1484
    idx 5  TP / LIS   0.1962

category_traps.gen_travel cross-checks the winner against OurAirports and
accepts the trap if the two sources agree to within HALF A DEGREE:

    if abs(float(match[0]["latitude_deg"]) - best[1]["lat"]) > 0.5:
        raise TrapUnavailable("travel: OurAirports latitude disagrees with OpenFlights")

For four of the five shipped seeds the winning MARGIN is smaller than the
DISAGREEMENT the gate permits -- by a factor of 21 in the LH/FRA case. A gate
that tolerates more error than the decision it is protecting does not protect
that decision. Whether that is a live defect or only a badly chosen constant
depends on one unmeasured quantity: how far apart the two sources ACTUALLY are.

This measures it. For every seed, for the winner and the runner-up, it reports
the OpenFlights latitude, the OurAirports latitude, and the absolute
disagreement, then compares the worst disagreement against the margin.

  disagreement << margin   the answer is safe and only the CONSTANT is wrong.
  disagreement >= margin   the ranking is not established by its own sources.

Runs locally against the warm cache.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import category_traps as ct  # noqa: E402
import gen_v2, gen_v3, gen_v4  # noqa: E402,F401
import net  # noqa: E402
import seed_roster as sr  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "travelmargin.json")
TOLERANCE_DEG = 0.5   # the constant gen_travel actually enforces

CAPTURE = {}
_REAL = ct._pick_extreme


def _spy(rows, keyfn, label, mode="max", valuefn=None):
    out = _REAL(rows, keyfn, label, mode=mode, valuefn=valuefn)
    if label == "travel":
        try:
            CAPTURE["rows"] = sorted(
                ((valuefn(r) if valuefn else None, float(keyfn(r))) for r in rows),
                key=lambda t: -t[1])
        except Exception:  # noqa: BLE001
            CAPTURE["rows"] = None
    return out


for mod in (ct, gen_v2, gen_v3, gen_v4):
    if hasattr(mod, "_pick_extreme"):
        mod._pick_extreme = _spy


_OA = {}


def ourairports_lat(iata):
    """OurAirports latitude for an IATA code, from the bulk CSV the trap cites."""
    if not _OA:
        txt = net.fetch("https://davidmegginson.github.io/ourairports-data/airports.csv",
                        timeout=300)
        import csv
        import io
        for row in csv.DictReader(io.StringIO(txt)):
            code = (row.get("iata_code") or "").strip()
            if code:
                _OA[code] = row
    r = _OA.get(iata)
    return (float(r["latitude_deg"]), r.get("name")) if r else (None, None)


def main():
    seeds = sr.seeds_for("travel")
    res = {"started": time.time(), "tolerance_deg": TOLERANCE_DEG, "seeds": []}
    for i, sd in enumerate(seeds):
        CAPTURE.clear()
        rec = {"idx": i, "seed": sd}
        try:
            cand = ct.GENERATORS["travel"](**sd)
            rec["shipped"] = True
            rec["answer"] = cand.answer
            rec["entity"] = cand.entity
        except Exception as e:  # noqa: BLE001
            rec["shipped"] = False
            rec["refusal"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        rows = CAPTURE.get("rows") or []
        rec["n_ranked"] = len(rows)
        if len(rows) >= 2:
            (w_iata, w_lat), (r_iata, r_lat) = rows[0], rows[1]
            rec["margin_deg"] = round(w_lat - r_lat, 6)
            rec["margin_km_approx"] = round((w_lat - r_lat) * 111.32, 3)
            pair = []
            for role, iata, of_lat in (("winner", w_iata, w_lat),
                                       ("runner_up", r_iata, r_lat)):
                oa_lat, oa_name = ourairports_lat(iata)
                pair.append({
                    "role": role, "iata": iata, "name": oa_name,
                    "openflights_lat": round(of_lat, 6),
                    "ourairports_lat": (round(oa_lat, 6) if oa_lat is not None
                                        else None),
                    "disagreement_deg": (round(abs(oa_lat - of_lat), 6)
                                         if oa_lat is not None else None),
                })
            rec["cross_source"] = pair
            ds = [p["disagreement_deg"] for p in pair
                  if p["disagreement_deg"] is not None]
            if ds:
                worst = max(ds)
                rec["worst_disagreement_deg"] = worst
                rec["margin_over_disagreement"] = (
                    round(rec["margin_deg"] / worst, 2) if worst > 0 else None)
                rec["ranking_established_by_sources"] = worst < rec["margin_deg"]
            rec["margin_under_gate_tolerance"] = rec["margin_deg"] < TOLERANCE_DEG
        res["seeds"].append(rec)
        print("[%d] %-26s margin=%-9s worst_disagree=%-9s margin<tol=%-5s "
              "ranking_ok=%s" % (
                  i, json.dumps(sd)[:26], rec.get("margin_deg"),
                  rec.get("worst_disagreement_deg"),
                  rec.get("margin_under_gate_tolerance"),
                  rec.get("ranking_established_by_sources")), flush=True)
        json.dump(res, open(OUT, "w"), indent=1)

    sel = [r for r in res["seeds"] if "margin_deg" in r]
    ok = [r for r in sel if r.get("ranking_established_by_sources") is True]
    under = [r for r in sel if r.get("margin_under_gate_tolerance")]
    ds = [r["worst_disagreement_deg"] for r in sel
          if r.get("worst_disagreement_deg") is not None]
    res["summary"] = {
        "n_seeds_measured": len(sel),
        "n_margin_under_gate_tolerance": len(under),
        "gate_tolerance_deg": TOLERANCE_DEG,
        "min_margin_deg": min(r["margin_deg"] for r in sel),
        "max_worst_disagreement_deg": (max(ds) if ds else None),
        "median_worst_disagreement_deg": (sorted(ds)[len(ds) // 2] if ds else None),
        "n_ranking_established_by_sources": len(ok),
        "tolerance_over_min_margin": round(TOLERANCE_DEG /
                                           min(r["margin_deg"] for r in sel), 1),
        "verdict": ("constant is too loose but every ranking is established"
                    if len(ok) == len(sel) else
                    "at least one ranking is NOT established by its own sources"),
    }
    res["finished"] = time.time()
    json.dump(res, open(OUT, "w"), indent=1)
    print()
    print(json.dumps(res["summary"], indent=2))


if __name__ == "__main__":
    main()
