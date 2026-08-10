"""
purge_invalid_traps.py — remove pooled traps whose answer is recoverable from the
LOC text layer, recording WHY rather than deleting silently.

Two entries fail, both Evening Public Ledger, both for the same structural reason
documented in confirm_candidate(): the api-proof gate was evaluated against an
answer that later changed, and was never re-run.

  1922-03-11  recorded 158, true 153 (arithmetic + full-res crop + GPU VLM agree).
              153 appears in the text layer as "VOL. VIII. NO. 153".
  1922-04-06  recorded 175 (correct). 175 appears as "VOL. VIII. NO. 175".

Withdrawn traps are written to withdrawn_traps.json so the record of what was
served, and why it was pulled, survives. A benchmark that quietly deletes its own
failures is not a benchmark.
"""
import json
import os

POOL = "generated_pool.json"
WITHDRAWN = "withdrawn_traps.json"
AUDIT = "pool_api_proof_audit.json"

# Reason strings are the audit's own findings, not editorial summaries.
INVALID = {
    ("sn83045211", "1922-03-11"): (
        "recorded answer 158 is a misread of 153 (arithmetic from 1922-03-06=148 "
        "and back from 1922-04-06=175 both give 153; full-resolution crop reads "
        "'VOL. VIII.-NO. 153'); the true answer 153 is present in the LOC text "
        "layer, so the page fails api-proof"),
    ("sn83045211", "1922-04-06"): (
        "answer 175 is correct but present in the LOC text layer as "
        "'VOL. VIII. NO. 175'; the gate had passed only because it was evaluated "
        "against the earlier misread 176, which is absent from that text"),
}


def main():
    pool = json.load(open(POOL))
    keep, pulled = [], []
    for t in pool:
        key = (t["lccn"], t["date"])
        if key in INVALID:
            t = dict(t)
            t["withdrawn_reason"] = INVALID[key]
            t["api_proof"] = False
            pulled.append(t)
        else:
            keep.append(t)

    prior = json.load(open(WITHDRAWN)) if os.path.exists(WITHDRAWN) else []
    seen = {(w["lccn"], w["date"]) for w in prior}
    prior += [p for p in pulled if (p["lccn"], p["date"]) not in seen]

    json.dump(keep, open(POOL, "w"), indent=2)
    json.dump(prior, open(WITHDRAWN, "w"), indent=2)

    # Prune orphaned scans so generated_images/ matches the pool exactly.
    basenames = {os.path.basename(t["image_path"]) for t in keep}
    removed = []
    if os.path.isdir("generated_images"):
        for f in sorted(os.listdir("generated_images")):
            if f not in basenames:
                os.remove(os.path.join("generated_images", f))
                removed.append(f)

    print(f"pool {len(pool)} -> {len(keep)}")
    for p in pulled:
        print(f"  withdrew {p['date']} = {p['answer']}")
        print(f"    {p['withdrawn_reason']}")
    print(f"withdrawn_traps.json now holds {len(prior)} record(s)")
    print(f"pruned {len(removed)} orphaned scan(s): {removed}")
    print(f"generated_images/ = {len(os.listdir('generated_images'))} files, "
          f"pool = {len(keep)}")


if __name__ == "__main__":
    main()
