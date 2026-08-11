#!/usr/bin/env python3
"""operator_audit.py -- blast-radius audit of the unmapped-operator defect.

DEFECT
------
source_gate.resolve_operator() ends with:

    return OPERATOR_MAP.get(rd, rd)

so a host that is NOT in OPERATOR_MAP resolves to its own bare registrable
domain (e.g. 'freepatentsonline.com') rather than to None / 'UNRESOLVED-*'.

resolve_operators() then buckets by that string, and check_sources() counts
len(ops) for the R3 ">= 3 independent operators" rule.  independent_witnesses()
buckets the same way and feeds T5, which sets the gold/silver tier.

Consequence: an UNVETTED domain counts as a full institutional operator.  The
'UNRESOLVED-' guard does not catch it, because the domain parsed fine -- it was
simply never mapped.

This script quantifies the damage on the SHIPPED catalog without changing any
behaviour.  For every trap it recomputes R3 and T5 twice:

  as_shipped : operators = resolve_operator() output, unmapped domains COUNT
  strict     : operators = only those present in OPERATOR_MAP / SOURCE_OVERRIDES

and reports every verdict that would flip.

No network.  Pure local re-scoring.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import source_gate as sg  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CAND = os.path.join(HERE, "category_trap_candidates.json")
EVAL = os.path.join(HERE, "evaluation_report.json")
OUT = os.path.join(HERE, "operator_audit.json")

MIN_OPERATORS = 3      # R3
MIN_WITNESSES_GOLD = 2  # T5 gold threshold


# ---------------------------------------------------------------- mapped set
def mapped_operator(url: str):
    """Return the operator ONLY if it came from an explicit mapping.

    Mirrors resolve_operator() but refuses the bare-domain fallback.
    """
    low = str(url or "").lower()
    for prefix, op in sg.SOURCE_OVERRIDES:
        if prefix in low:
            return op
    rd = sg.registrable_domain(url)
    if rd in sg.OPERATOR_MAP:
        return sg.OPERATOR_MAP[rd]
    return None


def bucket(urls, strict: bool):
    out = {}
    for u in urls or []:
        op = mapped_operator(u) if strict else sg.resolve_operator(u)
        if op is None:
            continue
        out.setdefault(op, []).append(u)
    return out


def unmapped(urls):
    return sorted({sg.registrable_domain(u) for u in (urls or [])
                   if mapped_operator(u) is None})


# ---------------------------------------------------------------- main
def main():
    with open(CAND) as fh:
        cand = json.load(fh)
    per_eval = {}
    if os.path.exists(EVAL):
        with open(EVAL) as fh:
            pt = (json.load(fh) or {}).get("per_trap", {}) or {}
        if isinstance(pt, dict):
            per_eval = pt
        else:
            for e in pt:
                if isinstance(e, dict):
                    k = e.get("category") or e.get("category_key")
                    if k:
                        per_eval[k] = e

    raw = cand.get("results", {})
    if isinstance(raw, dict):
        items = list(raw.values())
    else:
        items = list(raw)
    results = [r for r in items
               if isinstance(r, dict) and r.get("status") == "ok"
               and not r.get("error")]

    rows = []
    flips_r3 = []
    flips_t5 = []

    for r in results:
        cat = r.get("category") or r.get("category_key") or "?"
        trap = r.get("trap") or r
        srcs = trap.get("sources") or r.get("sources") or []
        conf = trap.get("confirming_sources") or r.get("confirming_sources") or []
        prim = trap.get("primary_operator") or r.get("primary_operator")
        answer = trap.get("answer") or r.get("answer")

        conf_in = [c for c in conf if c in srcs]

        ops_ship = bucket(srcs, strict=False)
        ops_strict = bucket(srcs, strict=True)

        wit_ship = sorted(o for o in bucket(conf_in, strict=False) if o != prim)
        wit_strict = sorted(o for o in bucket(conf_in, strict=True) if o != prim)

        r3_ship = len(ops_ship) >= MIN_OPERATORS
        r3_strict = len(ops_strict) >= MIN_OPERATORS
        t5_ship = len(wit_ship) >= MIN_WITNESSES_GOLD
        t5_strict = len(wit_strict) >= MIN_WITNESSES_GOLD

        ev = per_eval.get(cat) or {}
        shipped_verdict = ev.get("verdict")
        shipped_tier = ev.get("witness_tier")

        row = {
            "category": cat,
            "answer": answer,
            "primary_operator": prim,
            "shipped_verdict": shipped_verdict,
            "shipped_witness_tier": shipped_tier,
            "n_sources": len(srcs),
            "n_confirming_listed": len(conf),
            "n_confirming_in_sources": len(conf_in),
            "operators_as_shipped": sorted(ops_ship),
            "operators_strict": sorted(ops_strict),
            "unmapped_domains_counted_as_operators": unmapped(srcs),
            "witnesses_as_shipped": wit_ship,
            "witnesses_strict": wit_strict,
            "R3_pass_as_shipped": r3_ship,
            "R3_pass_strict": r3_strict,
            "T5_gold_as_shipped": t5_ship,
            "T5_gold_strict": t5_strict,
            "R3_flips": bool(r3_ship and not r3_strict),
            "T5_flips": bool(t5_ship and not t5_strict),
        }
        rows.append(row)
        if row["R3_flips"]:
            flips_r3.append(cat)
        if row["T5_flips"]:
            flips_t5.append(cat)

        print(f"{cat:<28} ops {len(ops_strict)}/{len(ops_ship)}  "
              f"wit {len(wit_strict)}/{len(wit_ship)}  "
              f"R3 {'PASS' if r3_strict else 'FAIL'}"
              f"{' <-FLIP' if row['R3_flips'] else ''}  "
              f"T5 {'gold' if t5_strict else 'silver'}"
              f"{' <-FLIP' if row['T5_flips'] else ''}"
              + (f"  unmapped={row['unmapped_domains_counted_as_operators']}"
                 if row["unmapped_domains_counted_as_operators"] else ""))

    # every unmapped domain across the whole catalog, with how many traps lean on it
    leaning = {}
    for row in rows:
        for d in row["unmapped_domains_counted_as_operators"]:
            leaning.setdefault(d, []).append(row["category"])

    summary = {
        "n_traps_audited": len(rows),
        "R3_flips": flips_r3,
        "T5_flips": flips_t5,
        "n_R3_flips": len(flips_r3),
        "n_T5_flips": len(flips_t5),
        "unmapped_domains_in_catalog": {k: sorted(v) for k, v in sorted(leaning.items())},
        "defect": ("source_gate.resolve_operator falls back to the bare registrable "
                   "domain for hosts absent from OPERATOR_MAP, so an unvetted domain "
                   "is counted as an independent institutional operator by R3 and by "
                   "independent_witnesses()/T5."),
    }

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))

    with open(OUT, "w") as fh:
        json.dump({"summary": summary, "rows": rows}, fh, indent=1)
    print(f"\nWROTE {OUT}")


if __name__ == "__main__":
    main()
