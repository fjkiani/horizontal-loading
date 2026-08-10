"""
withdraw_curated.py — mark curated vision prompts whose api-proof claim is false.

V02 and V03 assert that the machine-readable text for their page omits the
answer. On the page each prompt NAMES that is literally true -- V02 names page 23
and V03 names page 9, and both of those text layers are clean. It still does not
make them traps: the issue number is printed on the masthead of every page and
the founding year on every front page, so a text-only solver can fetch page 1 of
the same issue and read the answer with its own label attached.

Evidence, from a 322-page sweep of every trap's issue:

  V02  Evening Public Ledger 1922-03-06, answer 148
       page 1 text layer contains "NO. 148"
       (context: "... I JTl NO. 148 Entma a Sscend-ClM MaUsr at tha Postefflee ...")

  V03  Evening Star & Newark Advertiser 1907-12-02, answer 1832
       pages 1 and 6 contain "ESTABLISHED 1832"
       (context: "LAST EDITION AND NEWARK ADVERTISER _ _ _ ESTABLISHED 1832._")
       and page 1 also carries "FOUNDED MARCH 1. 1832"

Both were re-checked against a measured null: for a 4-digit answer only 99/9000
distinct tokens occur in the Newark issue (P(chance) = 0.011), and the hits are
label-bearing, so neither is a coincidence of small numbers.

They are flagged, not deleted -- same policy as withdrawn_traps.json. The record
of a broken trap is worth more than its absence.
"""
import json
import os

_REPO = os.path.dirname(os.path.abspath(__file__))
AUTHOR = os.path.join(_REPO, "author_payloads.json")

WITHDRAWN = {
    "V02": {
        "api_proof": False,
        "withdrawn": True,
        "withdrawn_reason": (
            "api-proof fails at the ISSUE level. The prompt names page 23, whose "
            "text layer is clean, but page 1 of the same issue prints 'NO. 148' in "
            "its text layer and the issue number is identical on every page. A "
            "text-only solver answers this without opening any scan."),
        "withdrawn_evidence": "page 1: '... I JTl NO. 148 Entma a Sscend-ClM MaUsr ...'",
    },
    "V03": {
        "api_proof": False,
        "withdrawn": True,
        "withdrawn_reason": (
            "api-proof fails at the ISSUE level. The prompt names page 9, whose text "
            "layer is clean, but pages 1 and 6 print 'ESTABLISHED 1832' and page 1 "
            "also prints 'FOUNDED MARCH 1. 1832'. The founding year is invariant "
            "across the whole paper, so it is recoverable from any issue."),
        "withdrawn_evidence": "page 1: 'NEWARK ADVERTISER _ _ _ ESTABLISHED 1832._'",
    },
}


def main():
    author = json.load(open(AUTHOR))
    changed = []
    for pid, patch in WITHDRAWN.items():
        if pid not in author:
            print(f"  {pid}: not present, skipped")
            continue
        author[pid].update(patch)
        changed.append(pid)
        print(f"  {pid}: api_proof -> False, withdrawn -> True")
    json.dump(author, open(AUTHOR, "w"), indent=2)
    still_clean = [p for p, r in author.items()
                   if r.get("method") == "vertical" and not r.get("withdrawn")]
    print(f"\nflagged {len(changed)}: {changed}")
    print(f"vertical prompts still sound: {sorted(still_clean)}")


if __name__ == "__main__":
    main()
