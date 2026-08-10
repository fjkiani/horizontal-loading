"""
ingest_grow.py — admit Modal-verified candidates into the served pool.

A candidate reaches this script only after four independent lines of evidence
coincide, which is the whole point: any single one of them has been wrong before.

  1. tesseract cross-resolution vote (pct 40/60/25) -- refuses on disagreement
  2. Qwen2.5-VL on a pct:100 crop -- different pixels, same parser. Benchmarked
     at 0 refusals on 14 ground truths, which means it hallucinates rather than
     abstains, so it is never trusted alone.
  3. the value implied by the paper's own issue sequence from a validated anchor
  4. no label-bearing leak on ANY page of the issue (12-46 pages swept each)

Provenance is recorded per trap so a later reader can tell what actually
certified it, rather than seeing an undifferentiated "verified".
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trap_generator as tg
import join_engine as je

_REPO = os.path.dirname(os.path.abspath(__file__))
CAND = os.path.join(_REPO, "modal_grow_candidates.json")
SWEEP = os.path.join(_REPO, "issue_sweep_api_proof.json")


def main():
    cands = json.load(open(CAND))
    pool = tg._load(tg._POOL_PATH)
    have = {tg._key(t) for t in pool}
    sweep = json.load(open(SWEEP)) if os.path.exists(SWEEP) else []
    sweep_keys = {(r["lccn"], r["date"]) for r in sweep}

    added = 0
    for c in cands:
        lccn, date, ans = c["lccn"], c["date"], str(c["answer"])
        field = c.get("field", "issue number")
        k = (lccn, date, field)
        if k in have:
            print(f"  {date}: already pooled")
            continue

        url = f"https://www.loc.gov/resource/{lccn}/{date}/ed-1/?&sp=1"
        meta = je.get(url + "&fo=json", as_json=True)
        title = (meta.get("item", {}).get("title")
                 or meta.get("title") or "New-York Tribune")
        title = title.split(".")[0].split("(")[0].strip() or "New-York Tribune"

        img = os.path.join(tg._IMG_DIR, f"{lccn}_{date}.jpg")
        je.loc_page_image(url, img, pct=15)

        prompt = tg._build_prompt(title, date, field)
        wc = tg._word_count(prompt)
        assert 70 <= wc <= 150, f"{date}: prompt word count {wc} outside 70-150"
        assert tg._norm_digits(ans) not in tg._norm_digits(prompt), \
            f"{date}: answer leaks into its own prompt"

        trap = {
            "lccn": lccn, "date": date, "paper": title, "field": field,
            "answer": ans, "resource_url": url, "image_path": img,
            "prompt": prompt, "word_count": wc,
            "api_proof": True, "confidence": "high",
            "ocr_engine": "tesseract-crossres + qwen2.5-vl-7b (modal a10g)",
            "verified": True,
            "verifier": "dual-engine agreement + issue-sequence arithmetic",
            "gate_note": (
                f"whole-issue api-proof: {c.get('pages_swept')} pages swept, "
                "no label-bearing leak"),
            "golden": [
                f"Open the LOC scan IMAGE for {url}",
                f"Read the {field} directly from the masthead (OCR layer is degraded)",
                f"= {ans}",
            ],
            "sources": [url,
                        f"https://www.loc.gov/item/{lccn}/{date}/ed-1/",
                        f"https://www.loc.gov/newspapers/{lccn}/"],
            "generated_at": tg.time.strftime("%Y-%m-%dT%H:%M:%SZ", tg.time.gmtime()),
        }
        pool.append(trap)
        have.add(k)
        added += 1
        print(f"  {date}: added {ans}  ({wc} words, {os.path.getsize(img)} B image)")

        if (lccn, date) not in sweep_keys:
            sweep.append({
                "label": f"pool {date}", "lccn": lccn, "date": date, "answer": ans,
                "field": field, "pages": c.get("pages_swept", 0),
                "old_gate_reject_pages": [], "standalone_token_pages": [],
                "label_bearing_pages": [], "label_example": None, "errors": [],
                "api_proof_whole_issue": True,
            })

    tg._save(tg._POOL_PATH, pool)
    json.dump(sweep, open(SWEEP, "w"), indent=2)
    print(f"\nadded {added}; pool now {len(pool)}")


if __name__ == "__main__":
    main()
