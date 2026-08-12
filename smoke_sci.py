"""Smoke-run each science-and-technology family once and gate the result.

One seed per family, chosen from the seeds that PASSED the design probes, so a
failure here is a defect in the generator or the gate, not an unlucky seed.
Writes /workspace/seal_deploy/smoke_sci.json and prints a compact verdict.
"""
import json
import os
import sys
import time
import traceback

os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import category_traps as ct  # noqa: E402
import gen_v2, gen_v3, gen_v4  # noqa: F401,E402  (import-order overrides)
import sci_families as sf  # noqa: E402
import source_gate as sg  # noqa: E402

# one known-good seed per family, from the recorded probe results
SEEDS = {
    "sci_vulnerability": dict(days=("2023-06-14",)),
    # index keys are (year, month) strings, not a rendered label
    "sci_standard": dict(months=(("2026", "April"),)),
    "sci_supplychain": dict(packages=("pillow",)),
    # Iceland's full roster is 86 ASNs; the design probe only bounded itself to
    # the first 40, so the generator was right to refuse at max_n=60. Ranking
    # "more than any other in the country" is only defensible over the WHOLE
    # roster, so the cap is raised rather than the roster truncated.
    "sci_asn": dict(countries=(("IS", "Iceland"),), max_n=120),
}

EXPECT_WEAKEST = {
    "sci_vulnerability": "original",
    "sci_standard": "deposit",
    "sci_supplychain": "derived",
    "sci_asn": "original",
}


def run_one(fid, fam):
    t0 = time.time()
    row = {"family_id": fid, "kwargs": {k: str(v) for k, v in SEEDS[fid].items()}}
    try:
        cand = fam.fn(**SEEDS[fid])
    except Exception as exc:
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc()[-1500:]
        row["seconds"] = round(time.time() - t0, 1)
        return row, None

    trap = cand.to_trap()
    ok, viol = sg.validate_trap(trap, min_operators=3)
    echo = sg.echo_violations(trap.get("sources", []),
                              trap.get("confirming_sources", []),
                              trap.get("primary_operator", ""))
    grade = sg.grade_witnesses(trap.get("sources", []),
                               trap.get("confirming_sources", []),
                               trap.get("primary_operator", ""))
    words = len((trap.get("prompt") or "").split())
    row.update({
        "status": "ok",
        "seconds": round(time.time() - t0, 1),
        "field": trap.get("field"),
        "answer": trap.get("answer"),
        "answer_len": len(str(trap.get("answer") or "")),
        "entity": trap.get("entity"),
        "n_base": trap.get("n_base"),
        "primary_operator": trap.get("primary_operator"),
        "source_operators": trap.get("source_operators"),
        "confirming_operators": trap.get("confirming_operators"),
        "independent_confirming_operators": trap.get(
            "independent_confirming_operators"),
        "sources": trap.get("sources"),
        "confirming_sources": trap.get("confirming_sources"),
        "landing_pages": (trap.get("facts") or {}).get("landing_pages"),
        "prompt_words": words,
        "prompt_ok": 70 <= words <= 150,
        "gate_ok": ok,
        "gate_violations": viol,
        "echo_violations": echo,
        "weakest_tier": grade.get("weakest_tier"),
        "n_witnesses": grade.get("n_witnesses"),
        "n_at_or_above_derived": grade.get("n_at_or_above_derived"),
        "tier_expected": EXPECT_WEAKEST[fid],
        "tier_matches": grade.get("weakest_tier") == EXPECT_WEAKEST[fid],
        "ranking_evidence": trap.get("ranking_evidence"),
        "prompt": trap.get("prompt"),
    })
    return row, trap


def main():
    fams = ct.families_for("science and technology", servable_only=True)
    out, traps = [], []
    for fid in sf.FAMILY_IDS:
        fam = fams.get(fid)
        if fam is None:
            out.append({"family_id": fid, "status": "missing_from_registry"})
            continue
        row, trap = run_one(fid, fam)
        out.append(row)
        if trap is not None:
            traps.append(trap)
        print(f"[{row['status']:5s}] {fid:20s} "
              f"{row.get('answer', row.get('error', ''))!r:28s} "
              f"words={row.get('prompt_words')} gate={row.get('gate_ok')} "
              f"tier={row.get('weakest_tier')} {row.get('seconds')}s",
              flush=True)

    dis_v, dis_w = ([], [])
    for i, t in enumerate(traps):
        others = traps[:i] + traps[i + 1:]
        v, w = sg.disjointness_violations(t, others)
        dis_v.extend(v)
        dis_w.extend(w)
    depth = sg.effective_depth(traps) if traps else 0

    summary = {
        "n_families": len(sf.FAMILY_IDS),
        "n_ok": sum(1 for r in out if r.get("status") == "ok"),
        "disjointness_violations": dis_v,
        "disjointness_warnings": dis_w,
        "effective_depth": depth,
        "rows": out,
    }
    with open("/workspace/seal_deploy/smoke_sci.json", "w") as fh:
        json.dump(summary, fh, indent=1)
    with open("/workspace/seal_deploy/smoke_sci_traps.json", "w") as fh:
        json.dump(traps, fh, indent=1)
    print(f"\nok={summary['n_ok']}/{summary['n_families']} "
          f"effective_depth={depth} "
          f"disjointness_violations={len(dis_v)} warnings={len(dis_w)}")
    for v in dis_v:
        print("  VIOL", v)
    return 0 if (summary["n_ok"] == summary["n_families"] and not dis_v) else 1


if __name__ == "__main__":
    sys.exit(main())
