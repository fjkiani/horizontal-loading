"""
audit_pool_arithmetic.py — independent arithmetic audit of every pooled answer.

WHY
---
The GPU vision engine disagreed with a pool answer that had been marked
agent-vision "verified": 1922-03-11 was recorded as 158, the GPU read 153.
Arithmetic settled it from two directions (forward from 1922-03-06=148, backward
from 1922-04-06=175) and a full-resolution crop confirmed "VOL. VIII.-NO. 153".

So a "verified" label was wrong. My own earlier vision confirmation had been run
on too small a raster, which is the exact failure this project already documented
for 175/176 -- a confident reading of insufficient pixels. The verifier is only as
good as the resolution it is shown.

That means no single answer can be trusted on its label alone. Issue numbers are
a near-arithmetic sequence in publication date, so the pool can audit ITSELF:
fit the sequence and flag any member that breaks it. This is independent of OCR
entirely -- it cannot be fooled by a glyph confusion.

METHOD
------
For each paper, model issue_number = base + issues_published_between(date0, date).
The publication calendar is read from the masthead itself, not assumed:
  * New-York Tribune   -- daily including Sunday
  * Evening Public Ledger -- "Published Daily Except Sunday"
Both are then VALIDATED against the pool: if the calendar were wrong, residuals
would not be uniformly zero for the majority of entries.

An entry is flagged when its observed value differs from the value implied by the
consensus fit of the other entries for that paper.
"""
import datetime
import json
from collections import defaultdict

POOL = "generated_pool.json"

# Publication calendars, expressed as a predicate on weekday (Mon=0 .. Sun=6).
# Not an assumption: each is validated below by checking that the resulting
# sequence fit leaves zero residual on the majority of that paper's entries.
CALENDARS = {
    "sn83030214": ("New-York Tribune (daily incl. Sunday)", lambda d: True),
    "sn83045211": ("Evening Public Ledger (daily except Sunday)", lambda d: d.weekday() != 6),
}


def issues_between(a, b, publishes):
    """Count published issues strictly after date a, through date b."""
    a, b = datetime.date.fromisoformat(a), datetime.date.fromisoformat(b)
    step = 1 if b >= a else -1
    n, d = 0, a
    while d != b:
        d += datetime.timedelta(days=step)
        if publishes(d):
            n += step
    return n


def audit(pool_path=POOL):
    pool = json.load(open(pool_path))
    by_paper = defaultdict(list)
    for t in pool:
        by_paper[t["lccn"]].append(t)

    report = {}
    for lccn, entries in by_paper.items():
        if lccn not in CALENDARS:
            report[lccn] = {"skipped": "no publication calendar known"}
            continue
        label, publishes = CALENDARS[lccn]
        entries = sorted(entries, key=lambda t: t["date"])

        # Each entry proposes an anchor; project every other entry from it and
        # count how many it explains exactly. The anchor with the most support
        # defines the consensus sequence. A single bad value can never win.
        best = None
        for anchor in entries:
            a_date, a_val = anchor["date"], int(anchor["answer"])
            agree, resid = 0, {}
            for e in entries:
                pred = a_val + issues_between(a_date, e["date"], publishes)
                r = int(e["answer"]) - pred
                resid[e["date"]] = r
                if r == 0:
                    agree += 1
            if best is None or agree > best["agree"]:
                best = {"anchor": a_date, "anchor_value": a_val,
                        "agree": agree, "residuals": resid}

        flagged = []
        for e in entries:
            r = best["residuals"][e["date"]]
            if r != 0:
                implied = int(e["answer"]) - r
                flagged.append({"date": e["date"], "recorded": e["answer"],
                                "implied_by_sequence": str(implied), "residual": r,
                                "verifier": e.get("verifier")})
        report[lccn] = {
            "calendar": label,
            "n_entries": len(entries),
            "consensus_anchor": f"{best['anchor']} = {best['anchor_value']}",
            "explained_exactly": f"{best['agree']}/{len(entries)}",
            "calendar_validated": best["agree"] >= max(2, len(entries) - 2),
            "flagged": flagged,
        }
    return report


if __name__ == "__main__":
    rep = audit()
    for lccn, r in rep.items():
        print(f"\n=== {lccn} ===")
        if "skipped" in r:
            print("  skipped:", r["skipped"]); continue
        print(f"  calendar             : {r['calendar']}")
        print(f"  entries              : {r['n_entries']}")
        print(f"  consensus anchor     : {r['consensus_anchor']}")
        print(f"  explained exactly    : {r['explained_exactly']}")
        print(f"  calendar validated   : {r['calendar_validated']}")
        if r["flagged"]:
            for f in r["flagged"]:
                print(f"  FLAG {f['date']}: recorded {f['recorded']} but sequence "
                      f"implies {f['implied_by_sequence']} (off by {f['residual']:+d}, "
                      f"verifier={f['verifier']})")
        else:
            print("  no discrepancies")
    with open("pool_arithmetic_audit.json", "w") as f:
        json.dump(rep, f, indent=2)
    print("\nwrote pool_arithmetic_audit.json")
