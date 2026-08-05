"""
verify_joins.py — verification gate for the Project Seal batch.

For every prompt, run its compute() over live data and enforce:
  V1  compute() returns unique == True        (constraint isolates exactly one candidate)
  V2  answer is non-empty and atomic          (single name/date/number, not a sentence)
  V3  answer string appears in the payload    (ground truth is in the data the solver sees)
  V4  prompt word count in [70, 150]          (R1)
  V5  >= 3 sources declared                   (R3)
  V6  no banned source domains                (R4)
  V7  no arithmetic framing                   (R5) — clean prompts must not require + - * /
  V8  API-proof                               (the answer is NOT returned by any single API
                                              call: vision traps assert the OCR text layer
                                              lacks the answer; ranking traps assert the
                                              compute() flagged api_proof=True)

A CLEAN prompt that fails any gate is DROPPED from the stress-test set.
A FLAWED prompt (negative control) is expected to fail its intended rule.
"""
from __future__ import annotations
import re
from batch_prompts import BATCH

BANNED = ["archive.org", "hathitrust.org", "pro-football-reference.com",
          "sports-reference.com", "basketball-reference.com", "baseball-reference.com",
          "hockey-reference.com", "fbref.com", "stathead.com"]
ARITH = re.compile(r"\b(add|sum|total of|plus|multiply|divided by|average of|subtract|product of)\b", re.I)


def word_count(s):
    return len(re.findall(r"\S+", s))


def is_atomic(ans):
    if not ans or len(str(ans)) > 60:
        return False
    # atomic = short; not a full sentence (no terminal period with multiple words + verb-ish)
    return len(str(ans).split()) <= 8


def verify_clean(p):
    res = compute_safely(p)
    fails = []
    if not res.get("unique"):
        fails.append(f"V1 not unique (survivors={res.get('survivors')})")
    ans = res.get("answer")
    if not is_atomic(ans):
        fails.append(f"V2 answer not atomic: {ans!r}")
    # V3: answer must be recoverable from the solver-facing evidence. For vision traps the
    # answer lives in the IMAGE (image_path), not the corrupted OCR payload; for ranking
    # traps it lives in the aggregated payload.
    payload = str(res.get("payload", ""))
    if res.get("image_path"):
        import os
        if not (os.path.exists(res["image_path"]) and os.path.getsize(res["image_path"]) > 10000):
            fails.append(f"V3 vision image missing/too small: {res['image_path']}")
    elif ans and str(ans).lower() not in payload.lower():
        fails.append(f"V3 answer {ans!r} not found in payload")
    wc = word_count(p.prompt)
    if not (70 <= wc <= 150):
        fails.append(f"V4 word count {wc} outside [70,150]")
    if len(p.sources) < 3:
        fails.append(f"V5 only {len(p.sources)} sources")
    if any(b in " ".join(p.sources).lower() for b in BANNED):
        fails.append("V6 banned source present")
    if ARITH.search(p.prompt):
        fails.append("V7 arithmetic framing in prompt")
    # V8 API-proof: the compute() must have proven the answer is not single-call retrievable.
    if not res.get("api_proof"):
        fails.append("V8 not API-proof (answer may be single-call retrievable)")
    if res.get("ocr_leaks"):
        fails.append(f"V8 OCR leaks answer strings: {res['ocr_leaks']}")
    return res, fails


def compute_safely(p):
    try:
        return p.compute()
    except Exception as e:
        return {"answer": None, "unique": False, "n_base": 0, "payload": "",
                "survivors": [], "trace": [f"compute error: {e}"]}


def verify_flawed(p):
    # A flawed control is 'caught' if it would trip a hard rule.
    src = " ".join(p.sources).lower()
    caught = []
    if any(b in src for b in BANNED):
        caught.append("R4 banned source")
    if ARITH.search(p.prompt):
        caught.append("R5 arithmetic")
    if "self_correcting" in p.exploit:
        caught.append("W1 self-correcting")
    return caught


def main():
    clean = [p for p in BATCH if p.intended == "clean"]
    flawed = [p for p in BATCH if p.intended == "flawed"]
    print(f"Verifying {len(clean)} clean + {len(flawed)} flawed prompts\n")
    passed, dropped = [], []
    for p in clean:
        res, fails = verify_clean(p)
        status = "PASS" if not fails else "DROP"
        (passed if not fails else dropped).append(p.id)
        print(f"[{status}] {p.id} ({p.domain}, {p.method})  answer={res.get('answer')!r}  n_base={res.get('n_base')}")
        for f in fails:
            print(f"        - {f}")
    print("\n--- Negative controls (must be caught) ---")
    for p in flawed:
        caught = verify_flawed(p)
        ok = len(caught) > 0
        print(f"[{'CAUGHT' if ok else 'MISSED'}] {p.id}: {p.flaw}  ->  {caught}")
    print(f"\nClean passed: {len(passed)}  |  Clean dropped: {len(dropped)}  {dropped}")
    return passed, dropped


if __name__ == "__main__":
    main()
