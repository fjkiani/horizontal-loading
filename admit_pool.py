"""Rebuild the served pool from the current candidate checkpoint.

Every entry goes through source_gate on WRITE. Nothing that fails is written,
so nothing that fails can be served. Reports the witness-tier breakdown and the
source diversity, which is the number the original audit failed on: the retired
corpus had 1 registrable domain, 1 answer field and 1 newspaper across 18 traps.
"""
import json
import sys

import source_gate as sg
import trap_generator as tg

CAND = "category_trap_candidates.json"


def main():
    results = json.load(open(CAND))["results"]
    tg._save(tg._POOL_PATH, [])
    admitted, refused = [], []
    for cat, rec in results.items():
        trap = rec.get("trap")
        if not trap:
            refused.append((cat, [rec.get("error") or "no trap produced"]))
            continue
        try:
            admitted.append(tg.admit_api_trap(trap))
        except tg.PoolRejected as exc:
            refused.append((cat, [str(exc)]))

    tiers = {"gold": [], "silver": [], "unwitnessed": []}
    for t in admitted:
        k = len(t.get("independent_confirming_operators") or [])
        tiers["gold" if k >= 2 else ("silver" if k == 1 else "unwitnessed")].append(
            t["category"])

    print(f"admitted {len(admitted)}/{len(results)}  refused {len(refused)}")
    for tier in ("gold", "silver", "unwitnessed"):
        print(f"  {tier:12s} {len(tiers[tier]):2d}  {sorted(tiers[tier])}")
    for cat, msg in refused:
        print(f"  REFUSED {cat}: {str(msg[0])[:150]}")

    doms = {sg.registrable_domain(u) for t in admitted for u in t["sources"]}
    ops = {sg.resolve_operator(u) for t in admitted for u in t["sources"]}
    print(f"\nsource diversity: {len(doms)} registrable domains, {len(ops)} operators, "
          f"{len({t['field'] for t in admitted})} answer fields, "
          f"{len({t['category'] for t in admitted})} categories")
    if tiers["unwitnessed"]:
        print("BUG: an unwitnessed trap reached the pool", tiers["unwitnessed"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
