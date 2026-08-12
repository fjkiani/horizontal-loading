#!/usr/bin/env python3
"""Retire the four arXiv traps and stock science and technology from the new slate.

The four incumbent science-and-technology prompts are 98.0-99.3% textually
identical, share one primary operator (Cornell University) and one witness pair
(DataCite, OurResearch), and resolve through the same three domains. Solving one
solves all four, which is the defect this rebuild exists to fix. They are
retired rather than deleted so the calibration baseline stays inspectable.

Writes generated_pool.json (the bake input) and prints the before/after
composition plus the ledger conservation identity.
"""
import json
import os
import shutil

os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import pool_ledger as pl  # noqa: E402
import source_gate as sg  # noqa: E402

CATALOG = "web/public/catalog.json"
POOL = "generated_pool.json"
SLATE = "select_sci.json"
CATEGORY = "science and technology"

RETIRE_REASON = (
    "source-family collapse: all four science-and-technology prompts resolve "
    "through Cornell University (arXiv) with the identical witness pair "
    "DataCite and OurResearch, share the domains arxiv.org, datacite.org and "
    "openalex.org, and sit at 0.980-0.993 pairwise prompt similarity against a "
    "clone threshold of 0.50. Measured effective depth of the four rows is 1, "
    "not 4. Retired to non-servable calibration baseline and replaced by four "
    "families with pairwise-disjoint operator and domain sets."
)


def main():
    catalog = json.load(open(CATALOG))
    old_traps = catalog.get("traps") or []
    slate = json.load(open(SLATE))
    new_sci = slate["traps"]

    old_sci = [t for t in old_traps if t.get("category") == CATEGORY]
    keep = [t for t in old_traps if t.get("category") != CATEGORY]

    print(f"catalog before: {len(old_traps)} traps, {len(old_sci)} in {CATEGORY!r}")
    for t in old_sci:
        print(f"  RETIRE {t.get('answer'):<14} {t.get('primary_operator')} "
              f"/ {t.get('field')}")

    # 1. Retire the incumbents in the ledger by their canonical trap ids.
    ids = [pl.trap_id(t.get("category"), t.get("field"), str(t.get("answer")))
           for t in old_sci]
    before = pl.status(categories=sg.CATEGORIES)
    pl.retire(ids, RETIRE_REASON)

    # 2. Stamp provenance on the retired records so /api/retired can explain
    #    the withdrawal without the reader having to reconstruct it.
    retired_baseline = [
        dict(t, servable=False, retired_reason=RETIRE_REASON,
             retired_as="calibration baseline")
        for t in old_sci
    ]

    # 3. New pool = every non-S&T trap, unchanged, plus the four new heads.
    pool = keep + new_sci
    shutil.copy(POOL, POOL + ".bak") if os.path.exists(POOL) else None
    with open(POOL, "w") as fh:
        json.dump(pool, fh, indent=1)
    with open("retired_sci_baseline.json", "w") as fh:
        json.dump({"retired_at": before.get("as_of"),
                   "reason": RETIRE_REASON,
                   "traps": retired_baseline}, fh, indent=1)

    print(f"\ncatalog after: {len(pool)} traps")
    for t in new_sci:
        ops = ", ".join(t.get("source_operators") or [])
        print(f"  ADD    {str(t.get('answer')):<14} {t.get('field'):<28} {ops}")

    # 4. Conservation: the ledger must not lose or invent records.
    after = pl.status(categories=sg.CATEGORIES)

    def tot(st):
        rows = st["categories"]
        return {
            "n_total": sum(r["n_total"] for r in rows),
            "n_available": sum(r["n_available"] for r in rows),
            "n_served": sum(r["n_served"] for r in rows),
            "n_burned": sum(r["n_burned"] for r in rows),
            "n_retired": sum(r["n_retired"] for r in rows),
        }

    b, a = tot(before), tot(after)
    print("\nledger conservation")
    print(f"  before  total={b['n_total']} available={b['n_available']} "
          f"burned={b['n_burned']} retired={b['n_retired']}")
    print(f"  after   total={a['n_total']} available={a['n_available']} "
          f"burned={a['n_burned']} retired={a['n_retired']}")
    ok_total = a["n_total"] == b["n_total"]
    ok_moved = (a["n_retired"] - b["n_retired"]) == len(ids)
    ok_avail = (b["n_available"] - a["n_available"]) == len(ids)
    print(f"  total preserved            : {ok_total}")
    print(f"  {len(ids)} moved into retired      : {ok_moved}")
    print(f"  {len(ids)} removed from available  : {ok_avail}")
    if not (ok_total and ok_moved and ok_avail):
        raise SystemExit("ledger conservation identity does not balance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
