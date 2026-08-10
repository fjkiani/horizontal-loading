"""archive_banned_corpus.py — retire every prompt invalidated by the loc.gov ban.

Moves all served traps and curated prompts that fail the hardened gate into
retired_corpus.json, recording per prompt the exact violating URLs and the rule
broken. Nothing is deleted; the served pool is emptied of banned content.

Idempotent: re-running will not duplicate records.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import source_gate as sg

HERE = os.path.dirname(os.path.abspath(__file__))
POOL = os.path.join(HERE, "generated_pool.json")
PAYLOADS = os.path.join(HERE, "author_payloads.json")
RETIRED = os.path.join(HERE, "retired_corpus.json")


def _load(path, default):
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return default


def main(apply=True):
    pool = _load(POOL, [])
    payloads = _load(PAYLOADS, {})
    retired = _load(RETIRED, {"retired_at": None, "rule": "", "generated": [], "curated": []})

    retired["rule"] = (
        "Project Seal banned-source list extended with loc.gov and archive.org. "
        "Sources are counted by controlling operator, not by URL string."
    )

    already_gen = {(r["lccn"], r["date"], r["field"]) for r in retired.get("generated", [])}
    already_cur = {r["id"] for r in retired.get("curated", [])}

    kept_pool, moved_gen = [], 0
    for e in pool:
        trap = dict(e)
        trap.setdefault("category", None)
        ok, violations = sg.validate_trap(trap)
        if ok:
            kept_pool.append(e)
            continue
        key = (e.get("lccn"), e.get("date"), e.get("field"))
        if key not in already_gen:
            retired["generated"].append({
                "lccn": e.get("lccn"), "date": e.get("date"), "field": e.get("field"),
                "answer": e.get("answer"), "paper": e.get("paper"),
                "prompt": e.get("prompt"),
                "sources": e.get("sources"),
                "operators": sorted(sg.resolve_operators(e.get("sources"))),
                "violating_urls": [u for u, _ in sg.banned_violations(e.get("sources"))],
                "violations": violations,
                "originally_verified": e.get("verified"),
                "original_verifier": e.get("verifier"),
                "retired_reason": "all sources resolve to the banned operator "
                                  "US Library of Congress",
            })
            moved_gen += 1

    kept_cur, moved_cur = {}, 0
    for pid, p in payloads.items():
        trap = dict(p)
        trap.setdefault("category", None)
        ok, violations = sg.validate_trap(trap)
        if ok:
            kept_cur[pid] = p
            continue
        if pid not in already_cur:
            retired["curated"].append({
                "id": pid,
                "domain": p.get("domain"),
                "answer": p.get("answer"),
                "prompt": p.get("prompt"),
                "sources": p.get("sources"),
                "operators": sorted(sg.resolve_operators(p.get("sources"))),
                "violating_urls": [u for u, _ in sg.banned_violations(p.get("sources"))],
                "violations": violations,
                "previously_withdrawn": bool(p.get("withdrawn")),
                "previous_withdrawn_reason": p.get("withdrawn_reason"),
                "retired_reason": "all sources resolve to the banned operator "
                                  "US Library of Congress",
            })
            moved_cur += 1

    retired["retired_at"] = datetime.now(timezone.utc).isoformat()
    retired["counts"] = {"generated": len(retired["generated"]),
                         "curated": len(retired["curated"])}

    print(f"generated pool : {len(pool)} -> {len(kept_pool)} kept, {moved_gen} newly retired")
    print(f"curated prompts: {len(payloads)} -> {len(kept_cur)} kept, {moved_cur} newly retired")
    print(f"retired store  : {retired['counts']}")

    if not apply:
        print("\n(dry run, nothing written)")
        return

    with open(RETIRED, "w") as fh:
        json.dump(retired, fh, indent=1)
    with open(POOL, "w") as fh:
        json.dump(kept_pool, fh, indent=1)
    with open(PAYLOADS, "w") as fh:
        json.dump(kept_cur, fh, indent=1)
    print(f"\nwrote {RETIRED}")
    print(f"wrote {POOL} ({len(kept_pool)} entries)")
    print(f"wrote {PAYLOADS} ({len(kept_cur)} entries)")


if __name__ == "__main__":
    main(apply="--dry-run" not in sys.argv)
