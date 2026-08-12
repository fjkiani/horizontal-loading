#!/usr/bin/env python3
"""Measure how asymmetric the clone-gate similarity metric is, and whether the
asymmetry can flip a gate verdict.

Found while cross-checking two independent computations of the same quantity:
select_sci.py reported max pairwise S&T prompt similarity 0.1375 and
bake_catalog.py reported 0.0405 for the SAME four prompts. Same function, same
inputs, different answers -- so the difference is argument order.

Cause: difflib.SequenceMatcher applies its `autojunk` heuristic to the SECOND
argument only. For sequences of 200+ elements, any element occurring in more
than 1% of positions is treated as junk and excluded from matching. Prose is
overwhelmingly made of such elements (spaces, vowels), so ratio(a, b) and
ratio(b, a) junk different character sets and disagree.

This matters because CLONE_SIMILARITY_THRESHOLD = 0.50 is a refusal boundary. A
metric whose value depends on which trap the caller happens to pass first can
admit a clone that a reordered call would have refused.

Writes probe_similarity_symmetry.json.
"""
import difflib
import itertools
import json
import os

os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import source_gate as sg  # noqa: E402

CATALOG = "web/public/catalog.json"
RETIRED = "retired_sci_baseline.json"
THRESH = sg.CLONE_SIMILARITY_THRESHOLD


def raw(a, b, autojunk=True):
    return difflib.SequenceMatcher(None, sg._norm_prompt(a), sg._norm_prompt(b),
                                   autojunk=autojunk).ratio()


def main():
    traps = json.load(open(CATALOG))["traps"]
    if os.path.exists(RETIRED):
        traps = traps + json.load(open(RETIRED))["traps"]

    rows = []
    for a, b in itertools.combinations(traps, 2):
        pa, pb = a.get("prompt", ""), b.get("prompt", "")
        if not pa or not pb:
            continue
        ab, ba = raw(pa, pb), raw(pb, pa)
        ab_nj, ba_nj = raw(pa, pb, autojunk=False), raw(pb, pa, autojunk=False)
        rows.append({
            "a": f"{a['category']}/{a['answer']}",
            "b": f"{b['category']}/{b['answer']}",
            "same_category": a["category"] == b["category"],
            "ratio_ab": round(ab, 4),
            "ratio_ba": round(ba, 4),
            "abs_gap": round(abs(ab - ba), 4),
            "ratio_ab_nojunk": round(ab_nj, 4),
            "ratio_ba_nojunk": round(ba_nj, 4),
            "abs_gap_nojunk": round(abs(ab_nj - ba_nj), 4),
            "verdict_flips": (ab >= THRESH) != (ba >= THRESH),
            "verdict_flips_nojunk": (ab_nj >= THRESH) != (ba_nj >= THRESH),
        })

    gaps = [r["abs_gap"] for r in rows]
    gaps_nj = [r["abs_gap_nojunk"] for r in rows]
    flips = [r for r in rows if r["verdict_flips"]]
    worst = max(rows, key=lambda r: r["abs_gap"])

    # How close does the asymmetry come to the refusal boundary? A pair is at
    # risk when the interval [min(ab,ba), max(ab,ba)] straddles the threshold.
    straddle = [r for r in rows
                if min(r["ratio_ab"], r["ratio_ba"]) < THRESH <= max(r["ratio_ab"],
                                                                     r["ratio_ba"])]
    out = {
        "n_pairs": len(rows),
        "threshold": THRESH,
        "asymmetry_with_autojunk": {
            "max_abs_gap": round(max(gaps), 4),
            "mean_abs_gap": round(sum(gaps) / len(gaps), 4),
            "n_pairs_with_any_gap": sum(1 for g in gaps if g > 1e-9),
            "max_ratio_of_the_two": round(
                max(max(r["ratio_ab"], r["ratio_ba"])
                    / max(1e-9, min(r["ratio_ab"], r["ratio_ba"])) for r in rows), 3),
        },
        "asymmetry_without_autojunk": {
            "max_abs_gap": round(max(gaps_nj), 6),
            "mean_abs_gap": round(sum(gaps_nj) / len(gaps_nj), 6),
            "n_pairs_with_any_gap": sum(1 for g in gaps_nj if g > 1e-9),
        },
        "n_verdict_flips": len(flips),
        "verdict_flips": flips,
        "n_pairs_straddling_threshold": len(straddle),
        "worst_pair": worst,
        "rows": rows,
    }
    with open("probe_similarity_symmetry.json", "w") as fh:
        json.dump(out, fh, indent=1)

    print(f"pairs compared              : {out['n_pairs']}")
    print(f"threshold                   : {THRESH}")
    a1 = out["asymmetry_with_autojunk"]
    print("\nWITH autojunk (current behaviour)")
    print(f"  pairs where ratio(a,b) != ratio(b,a) : {a1['n_pairs_with_any_gap']}"
          f" of {out['n_pairs']}")
    print(f"  max absolute gap                     : {a1['max_abs_gap']}")
    print(f"  mean absolute gap                    : {a1['mean_abs_gap']}")
    print(f"  worst multiplicative disagreement    : {a1['max_ratio_of_the_two']}x")
    print(f"  worst pair: {worst['a']} vs {worst['b']} -> "
          f"{worst['ratio_ab']} / {worst['ratio_ba']}")
    a2 = out["asymmetry_without_autojunk"]
    print("\nWITHOUT autojunk")
    print(f"  pairs with any gap                   : {a2['n_pairs_with_any_gap']}"
          f" of {out['n_pairs']}")
    print(f"  max absolute gap                     : {a2['max_abs_gap']}")
    print(f"\nverdict flips at threshold {THRESH}   : {len(flips)}")
    print(f"pairs straddling the threshold       : {len(straddle)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
