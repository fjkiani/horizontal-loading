#!/usr/bin/env python3
"""rankgate.py -- the gate the art probe proved was missing.

The equivalence test used up to now asked whether a served field's ordering
correlates with the composite key at |rho| >= 0.98. That is the wrong question.
`colorfulness` scored 0.89-0.97 against `color.s` and passed the test, yet the
answer it selects ranks 1st, 3rd, 6th and 7th on plain `color.s`. A solver that
sorts server-side on one field and reads seven rows has the answer. Rank
correlation over the whole population says nothing about the neighbourhood that
actually matters, which is the top of each honoured ordering.

The correct question is: IN EVERY ORDERING THE SERVER WILL PERFORM, HOW DEEP IS
THE ANSWER? If the answer sits within the first K rows of any single ordering,
the trap does not force enumeration.

This script applies that test to the two rekeyed traps.

  FINANCE  local. 336 auctions x 114 served fields, ranked ascending and
           descending. Costs one request.
  HEALTH   remote. The registry performs the ordering, so each honoured sort
           field is requested and the returned page scanned for the answer.

Writes rankgate.json incrementally.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rankgate.json")
K_SHALLOW = 20   # an answer this near the top of any ordering is not enumeration
STATE = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stages": {}}


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


# ------------------------------------------------------------------ finance
def finance():
    u = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
         "accounting/od/auctions_query?filter=auction_date:gte:2021-01-01,"
         "auction_date:lte:2021-12-31,security_type:eq:Bill&page[size]=5000"
         "&sort=auction_date")
    rows = net.get_json(u, timeout=180, attempts=4).get("data", [])
    keep = []
    for r in rows:
        x, y = num(r, "indirect_bidder_accepted"), num(r, "primary_dealer_accepted")
        if (r.get("cusip") or "").strip() and x is not None and y is not None and y:
            rr = dict(r)
            rr["_key"] = x / y
            keep.append(rr)
    ans = max(keep, key=lambda r: r["_key"])["cusip"].strip()
    fields = set()
    for r in keep[:50]:
        fields.update(r.keys())
    fields = sorted(f for f in fields if not f.startswith("_"))

    shallow, ranks = [], {}
    for f in fields:
        vals = [(num(r, f), r["cusip"].strip()) for r in keep]
        if any(v is None for v, _ in vals):
            continue
        desc = sorted(vals, key=lambda t: -t[0])
        asc = sorted(vals, key=lambda t: t[0])
        rd = [c for _, c in desc].index(ans) + 1
        ra = [c for _, c in asc].index(ans) + 1
        ranks[f] = {"desc": rd, "asc": ra}
        if min(rd, ra) <= K_SHALLOW:
            shallow.append({"field": f, "best_rank": min(rd, ra),
                            "direction": "desc" if rd < ra else "asc"})
    shallow.sort(key=lambda d: d["best_rank"])
    best = min((min(v["desc"], v["asc"]) for v in ranks.values()), default=None)
    return {"answer": ans, "n_rows": len(keep), "n_numeric_fields": len(ranks),
            "shallowest_rank_over_all_served_orderings": best,
            "fields_placing_answer_in_top_%d" % K_SHALLOW: shallow,
            "n_shallow": len(shallow),
            "all_ranks": ranks,
            "status": "PASS" if not shallow else "FAIL"}


# ------------------------------------------------------------------ health
HEALTH_SORTS = ["StartDate", "EnrollmentCount", "NumArmGroups", "LastUpdatePostDate",
                "CompletionDate", "PrimaryCompletionDate", "NumLocations",
                "StudyFirstPostDate"]
BASE = ("https://clinicaltrials.gov/api/v2/studies?pageSize=200"
        "&query.cond=amyotrophic+lateral+sclerosis"
        "&filter.overallStatus=COMPLETED&aggFilters=phase:3")


def health(answer="NCT05178810"):
    out = {"answer": answer, "orderings": {}, "shallow": [], "rejected_sorts": []}
    for f in HEALTH_SORTS:
        for d in ("asc", "desc"):
            u = "%s&sort=%s:%s&fields=NCTId" % (BASE, f, d)
            try:
                js = net.get_json(u, timeout=120, attempts=3)
            except Exception as e:  # noqa: BLE001
                out["orderings"]["%s:%s" % (f, d)] = {"error": str(e)[:70]}
                time.sleep(0.8)
                continue
            ids = [(s.get("protocolSection", {}).get("identificationModule", {})
                    .get("nctId")) for s in js.get("studies", [])]
            rank = (ids.index(answer) + 1) if answer in ids else None
            out["orderings"]["%s:%s" % (f, d)] = {"n": len(ids), "rank": rank,
                                                  "row_one": ids[0] if ids else None}
            if rank is not None and rank <= K_SHALLOW:
                out["shallow"].append({"ordering": "%s:%s" % (f, d), "rank": rank})
            time.sleep(0.8)
    # the two fields the registry refuses; confirm the refusal still holds
    for f in ("SecondaryOutcomeCount", "OutcomeMeasureCount"):
        u = "%s&sort=%s:desc&fields=NCTId" % (BASE, f)
        try:
            net.get_json(u, timeout=90, attempts=2)
            out["rejected_sorts"].append({"field": f, "still_rejected": False})
        except Exception:  # noqa: BLE001
            out["rejected_sorts"].append({"field": f, "still_rejected": True})
        time.sleep(0.8)
    ranks = [v["rank"] for v in out["orderings"].values()
             if isinstance(v, dict) and v.get("rank")]
    out["shallowest_rank"] = min(ranks) if ranks else None
    out["n_shallow"] = len(out["shallow"])
    out["status"] = "PASS" if not out["shallow"] else "FAIL"
    return out


def main():
    print("finance rank gate", flush=True)
    STATE["stages"]["finance"] = finance()
    save()
    f = STATE["stages"]["finance"]
    print("  %s  answer %s  shallowest rank %s of %d rows over %d served fields; "
          "%d field(s) place it in the top %d"
          % (f["status"], f["answer"], f["shallowest_rank_over_all_served_orderings"],
             f["n_rows"], f["n_numeric_fields"], f["n_shallow"], K_SHALLOW), flush=True)
    for s in f["fields_placing_answer_in_top_%d" % K_SHALLOW][:8]:
        print("     ", s, flush=True)

    print("health rank gate", flush=True)
    STATE["stages"]["health"] = health()
    save()
    h = STATE["stages"]["health"]
    print("  %s  answer %s  shallowest rank %s; %d ordering(s) place it in the top %d"
          % (h["status"], h["answer"], h["shallowest_rank"], h["n_shallow"], K_SHALLOW),
          flush=True)
    print("  rejected sorts:", h["rejected_sorts"], flush=True)

    STATE["stages"]["VERDICT"] = {
        "k_shallow": K_SHALLOW,
        "finance": f["status"], "finance_shallowest": f["shallowest_rank_over_all_served_orderings"],
        "health": h["status"], "health_shallowest": h["shallowest_rank"],
        "conclusion": ("both rekeys survive the rank gate"
                       if f["status"] == "PASS" and h["status"] == "PASS"
                       else "at least one rekey is near-argmax of a served ordering"),
    }
    STATE["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save()
    print(json.dumps(STATE["stages"]["VERDICT"], indent=1), flush=True)


if __name__ == "__main__":
    main()
