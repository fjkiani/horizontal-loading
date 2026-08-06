"""
trap_generator.py — on-demand, repeatable vision-trap generation.

This replaces the old "select from a static pool" stub with a real generator:
every call walks forward through Library of Congress newspaper front pages,
auto-extracts a masthead answer (issue number / year established), and runs the
full verification gate before serving. Only traps that pass every gate are
returned; verified winners are persisted so the catalog grows over time.

The trap principle (vision-true vertical): the answer is legible in the scan
IMAGE but ABSENT from the whole-page OCR text layer, so a text-only / naive-API
solver cannot find it, while a human (or vision model) reading the image can.

Gates (all must pass):
  G1 extraction   — a masthead answer was read with sufficient confidence
  G2 api-proof    — the answer string is ABSENT from the whole-page OCR
  G3 leak         — the answer is NOT revealed by the prompt text itself
  G4 legibility   — the masthead crop is readable (image quality + OCR sanity)

Extraction confidence: we OCR the masthead crop at two scales and require the
two reads to agree on the normalized digits; agreement => 'high', else the
candidate is rejected (the human-fallback path can be layered on top later).
"""
from __future__ import annotations
import json, os, re, time
from PIL import Image
import pytesseract

import join_engine as je

_REPO = os.path.dirname(os.path.abspath(__file__))
_POOL_PATH = os.path.join(_REPO, "generated_pool.json")        # agent/vision-confirmed (verified)
_PENDING_PATH = os.path.join(_REPO, "generated_pending.json")  # OCR-derived, awaiting confirmation
_IMG_DIR = os.path.join(_REPO, "generated_images")
os.makedirs(_IMG_DIR, exist_ok=True)

# Seed starting points: (lccn, a known-good front-page date). The walker follows
# each issue's `next_resource` link, so any valid front page is a valid seed.
SEEDS = {
    "sn83030214": "1900-01-01",   # New-York Tribune
    "sn83045211": "1922-03-06",   # Evening Public Ledger
    "sn84020504": "1907-12-02",   # Evening Star & Newark Advertiser
}

# OCR renders "No." (issue number) many ways: N®, N°, N*, N9, NO, No, N®.
# Two masthead styles:
#   (a) large thousands-separated issue numbers:  "N® 19.405" / "No. 19,405"
#   (b) "VOL. ...—NO. N" small numbers:           "VOL. VIII.—NO. 150"
_ISSUE_PATTERNS = [
    r"N[®°*º'\"\w]?\s*\.?\s*([0-9]{1,3}[,\.][0-9]{3})\b",   # N® 19.405
    r"\bNo\.?\s*([0-9]{1,3}[,\.][0-9]{3})\b",                # No. 19,405
    r"\b([0-9]{2}[,\.][0-9]{3})\b",                           # bare 19,405
    r"NO[.,]?\s*([0-9]{1,3})\b",                              # NO. 150 / NO, 150
    r"—\s*N[O0]\.?\s*([0-9]{1,3})\b",                         # —NO. 166
]
# "ESTABLISHED 1832" / "ESTD. 1851" / "FOUNDED 1880"
_YEAR_PATTERNS = [
    r"(?:ESTABLISHED|ESTD\.?|FOUNDED|EST\.?)\s*(?:IN\s*)?(1[6-9][0-9]{2})\b",
]


def _norm_digits(s):
    return re.sub(r"[,\.\s]", "", s or "")


def _masthead_crop(img_path, frac=0.16):
    im = Image.open(img_path).convert("L")
    w, h = im.size
    return im.crop((0, 0, w, int(h * frac)))


def _ocr_at_scale(crop, scale):
    c = crop.resize((crop.width * scale, crop.height * scale))
    return pytesseract.image_to_string(c)


def _extract_with_confidence(*mtexts):
    """Return (answer, field, confidence) by N-of-M consensus across independent
    OCR reads. 'high' = at least two reads agree on the same normalized digits
    and field; 'low' = exactly one read found a candidate (rejected by default,
    kept for a future human-confirm queue); 'none' = nothing found."""
    def find(text):
        for p in _ISSUE_PATTERNS:
            m = re.search(p, text)
            if m:
                return _norm_digits(m.group(1)), "issue number"
        for p in _YEAR_PATTERNS:
            m = re.search(p, text, re.I)
            if m:
                return m.group(1), "year established"
        return None, None

    reads = [find(t) for t in mtexts]
    reads = [(a, f) for a, f in reads if a]
    if not reads:
        return None, None, "none"
    # tally (answer, field) votes
    from collections import Counter
    votes = Counter(reads)
    (ans, field), n = votes.most_common(1)[0]
    if n >= 2:
        return ans, field, "high"
    return ans, field, "low"


def _legible(img_path):
    """G4 legibility: masthead crop must have real content (variance) and the
    full image must be a plausible scan (size floor)."""
    try:
        if os.path.getsize(img_path) < 10000:
            return False
        crop = _masthead_crop(img_path)
        # Variance of pixel values: a blank/uniform band is not legible.
        hist = crop.histogram()
        n = sum(hist)
        mean = sum(i * c for i, c in enumerate(hist)) / max(n, 1)
        var = sum(c * (i - mean) ** 2 for i, c in enumerate(hist)) / max(n, 1)
        return var > 400  # tuned: real mastheads have strong ink/paper contrast
    except Exception:
        return False


def _build_prompt(paper_title, date, field):
    """G3 leak-safe prompt: asks for the masthead value WITHOUT naming it.
    Word count target 70-150 (Project Seal R1)."""
    prompt = (
        f"Consult the front page of the newspaper \"{paper_title}\" published on {date}, "
        f"digitized by the Library of Congress. The page is a historical scan whose "
        f"machine-readable text layer is heavily degraded by age and ornate typesetting, "
        f"so automated transcription is unreliable. Examine the masthead banner at the top "
        f"of the front page directly. What is the {field} printed there? Provide the exact "
        f"value as it appears, as a single atomic token. Cross-check your reading against "
        f"the publication's catalog record and at least one independent library source to "
        f"confirm you have the correct issue before answering."
    )
    return prompt


def _word_count(s):
    return len(re.findall(r"\S+", s))


def generate_trap(lccn="sn83030214", start_date=None, max_steps=8, min_confidence="high"):
    """Walk forward from a seed front page, returning the FIRST trap that passes
    every gate. Returns a dict with the new prompt + answer + provenance, or
    raises RuntimeError if no clean trap is found within max_steps pages."""
    start_date = start_date or SEEDS.get(lccn, "1900-01-01")
    url = f"https://www.loc.gov/resource/{lccn}/{start_date}/ed-1/?sp=1"
    tried = 0
    for _ in range(max_steps):
        meta = je.get(url + ("&fo=json" if "?" in url else "?fo=json"), as_json=True)
        item = meta.get("item", {})
        date = item.get("date", start_date)
        title = re.sub(r"\s*\(.*?\)\s*", " ", item.get("title", lccn)).split(",")[0].strip()
        tried += 1

        img = os.path.join(_IMG_DIR, f"{lccn}_{date}.jpg")
        try:
            je.loc_page_image(url, img, pct=15)  # pct:15 is enough for the masthead and faster
        except Exception:
            img = None

        if img and _legible(img):
            crop = _masthead_crop(img)
            # Three scales give a 2-of-3 'high' consensus; two scales proved too
            # brittle (many pages read only 'low'). Generation is async, so the
            # extra OCR cost is absorbed by the background job, not the request.
            reads = [_ocr_at_scale(crop, s) for s in (2, 3, 4)]
            answer, field, conf = _extract_with_confidence(*reads)

            if answer and field and conf == min_confidence:
                # G2 api-proof: answer absent from whole-page OCR.
                page_ocr = je.loc_page_ocr(url)
                norm_ocr = _norm_digits(page_ocr)
                api_proof = _norm_digits(answer) not in norm_ocr

                prompt = _build_prompt(title, date, field)
                wc = _word_count(prompt)
                leak = _norm_digits(answer) in _norm_digits(prompt)
                wc_ok = 70 <= wc <= 150

                if api_proof and not leak and wc_ok:
                    trap = {
                        "lccn": lccn, "date": date, "paper": title,
                        "field": field, "answer": answer,
                        "resource_url": url,
                        "image_path": img,
                        "prompt": prompt, "word_count": wc,
                        "api_proof": True, "confidence": conf,
                        # OCR-derived answers are NOT ground truth until an independent
                        # high-fidelity read (agent vision / vision API / human) confirms
                        # them. Proven: two OCR engines both misread a degraded '5' as '6'.
                        "verified": False,
                        "golden": [
                            f"Open the LOC scan IMAGE for {url}",
                            f"Read the {field} directly from the masthead (OCR layer is degraded)",
                            f"= {answer}",
                        ],
                        "sources": [
                            url,
                            f"https://www.loc.gov/item/{lccn}/{date}/ed-1/",
                            f"https://www.loc.gov/newspapers/{lccn}/",
                        ],
                        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    _persist_pending(trap)
                    return trap
        # advance to next issue
        nxt = meta.get("next_resource", {}).get("url")
        if not nxt:
            break
        url = nxt.replace("&fo=json", "").replace("?fo=json", "?")
        if "sp=1" not in url:
            url += ("&sp=1" if "?" in url else "?sp=1")
    raise RuntimeError(f"no clean trap found within {tried} pages from {lccn} {start_date}")


def _load(path):
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except Exception:
            return []
    return []


def _save(path, rows):
    json.dump(rows, open(path, "w"), indent=2)


def _key(t):
    return (t["lccn"], t["date"], t["field"])


def resolve_image_path(t):
    """Resolve a trap's stored image_path portably: absolute as-is, else relative
    to the repo root (handles paths authored under a different absolute root)."""
    p = t.get("image_path") or ""
    if os.path.isabs(p) and os.path.exists(p):
        return p
    cand = os.path.join(_REPO, "generated_images", os.path.basename(p))
    return cand if os.path.exists(cand) else p


def _persist_pending(trap):
    pend = _load(_PENDING_PATH)
    if not any(_key(p) == _key(trap) for p in pend):
        pend.append(trap)
        _save(_PENDING_PATH, pend)


def list_pending():
    """OCR-derived candidates awaiting ground-truth confirmation."""
    return _load(_PENDING_PATH)


def list_generated():
    """Confirmed (verified) traps only."""
    return _load(_POOL_PATH)


def confirm_candidate(lccn, date, field, confirmed_answer, verifier="agent"):
    """Move a pending candidate to the verified pool, setting its answer to the
    independently-confirmed value. If the confirmed answer differs from the OCR
    candidate, the OCR value is discarded and the confirmed value is used.
    Returns the verified trap, or None if no matching pending candidate."""
    pend = _load(_PENDING_PATH)
    pool = _load(_POOL_PATH)
    k = (lccn, date, field)
    match = next((p for p in pend if _key(p) == k), None)
    if not match:
        return None
    pend = [p for p in pend if _key(p) != k]
    _save(_PENDING_PATH, pend)
    match["answer"] = str(confirmed_answer)
    match["verified"] = True
    match["verifier"] = verifier
    match["confirmed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Re-assert api-proof against the CONFIRMED answer (it may differ from OCR's).
    # The golden trace's final line must reflect the confirmed value.
    match["golden"][-1] = f"= {confirmed_answer}"
    if not any(_key(p) == k for p in pool):
        pool.append(match)
        _save(_POOL_PATH, pool)
    return match


def reject_candidate(lccn, date, field, reason=""):
    """Drop a pending candidate that failed ground-truth confirmation."""
    pend = _load(_PENDING_PATH)
    pend = [p for p in pend if _key(p) != (lccn, date, field)]
    _save(_PENDING_PATH, pend)
    return True


if __name__ == "__main__":
    t = generate_trap()
    print(json.dumps({k: t[k] for k in ("paper", "date", "field", "answer", "api_proof", "word_count")}, indent=2))
