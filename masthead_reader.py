"""
masthead_reader.py — resolution-aware masthead extractor.

WHY THIS EXISTS
---------------
The original extractor read a pct:15 downscale of the front page, cropped a
blanket top-16% band, and upscaled that one raster 2x/3x/4x with tesseract,
treating agreement across those three upscales as "high confidence".

A controlled resolution sweep on the Evening Public Ledger 1922-04-06 masthead
(ground truth, read by agent vision on the full-res scan: NO. 175) showed that
design is unsound:

    pct: 15  -> 378  (low)
    pct: 25  -> 176  (HIGH confidence, WRONG)
    pct: 40  -> 175  (high, correct)
    pct: 60  -> 176  (low)
    pct:100  -> 175  (low, correct)

Two independent findings:

1. Upscaling ONE raster is correlated error, not independent evidence. At pct:25
   all three upscales agreed on the wrong digit, so the pipeline emitted
   "high confidence" for 176. tesseract, EasyOCR and the Cohere vision LLM all
   returned 176 from that same degraded raster - three "independent" engines
   sharing one bad input.
2. The error was never intrinsic to the glyph. On a tight, correctly located,
   full-resolution crop of the VOL./NO. line, plain tesseract --psm 6 reads
   "VOL. VIII.-NO. 175" correctly.

So the fix is crop geometry + resolution + cross-RESOLUTION agreement, which
uses genuinely different rasters as independent evidence.
"""
import os
import re

import pytesseract
from PIL import Image

import join_engine as je
import trap_generator as tg

# Two genuinely different source rasters. pct:40 was the accuracy/latency sweet
# spot in the sweep; pct:60 is a different downsample of the same master scan, so
# its errors are not guaranteed to correlate with pct:40's.
# Ordered by cost/benefit. pct:40 was the accuracy/latency sweet spot in the sweep
# and is read first. pct:60 is a different downsample of the same master scan, so
# its errors need not correlate. pct:25 is consulted ONLY as a tie-breaker, because
# the benchmark showed three correct answers were being rejected purely for want of
# a second agreeing vote (confidence "low"), which cost recall without buying
# precision. A third raster converts those into a 2-of-3 majority; a genuine 2-2
# split (as on Ledger 1922-04-06) still ends as "conflict" and is refused.
RESOLUTIONS = (40, 60, 25)
MIN_VOTES = 2
# psm 6 ("assume a uniform block of text") preserves the masthead's line layout;
# psm 7 ("single line") helps when the crop isolates one rule-to-rule band.
PSMS = ("--psm 6", "--psm 7")
# The VOL./NO. line sits below the nameplate, not at the very top. A blanket top
# crop dilutes it with the nameplate and weather box; this band brackets it.
BAND = (0.02, 0.12)


def _band_crop(path, lo=BAND[0], hi=BAND[1], right=0.45):
    """Crop the horizontal strip that carries VOL./NO., left portion only."""
    im = Image.open(path).convert("L")
    w, h = im.size
    return im.crop((0, int(h * lo), int(w * right), int(h * hi)))


def _read_variants(crop):
    """All raw OCR strings for one crop, across psm modes and a mild upscale."""
    out = []
    for cfg in PSMS:
        try:
            out.append(pytesseract.image_to_string(crop, config=cfg))
        except Exception:
            pass
    big = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    for cfg in PSMS:
        try:
            out.append(pytesseract.image_to_string(big, config=cfg))
        except Exception:
            pass
    return out


# Implausibly short issue numbers are almost always spurious regex hits on nearby
# body text (e.g. the pct:40 read of Ledger 1922-04-06 matched a bare "2"). Such a
# junk vote is not harmless: it manufactures a fake tie and makes the reader refuse
# a page it could otherwise have called, so it costs recall.
_MIN_ISSUE_DIGITS = 2


def _parse(texts):
    """First plausible (answer, field) across candidate texts, using shared regexes."""
    for t in texts:
        flat = " ".join((t or "").split())
        for pat in tg._ISSUE_PATTERNS:
            m = re.search(pat, flat, re.IGNORECASE)
            if m:
                d = tg._norm_digits(m.group(1))
                if len(d) >= _MIN_ISSUE_DIGITS and int(d) > 0:
                    return d, "issue number"
        for pat in tg._YEAR_PATTERNS:
            m = re.search(pat, flat, re.IGNORECASE)
            if m:
                return tg._norm_digits(m.group(1)), "year established"
    return None, None


def read_masthead(resource_url, workdir="/workspace/adjudicate", cache_tag=None,
                  progress=None):
    """Read the masthead answer using cross-RESOLUTION agreement.

    Returns dict: answer, field, confidence, per_resolution, agree.
      confidence == "high"     -> every resolution that produced a value agreed
      confidence == "conflict" -> resolutions disagreed (do NOT trust either)
      confidence == "low"      -> only one resolution produced a value
      confidence == "none"     -> nothing read
    """
    os.makedirs(workdir, exist_ok=True)
    tag = cache_tag or re.sub(r"[^A-Za-z0-9]+", "_", resource_url)[-60:]
    per = {}
    for i, pct in enumerate(RESOLUTIONS):
        # Each raster is a multi-MB download plus OCR; on a 0.1 vCPU instance a
        # single page can sit here ~2 min. Report per-raster so the caller can
        # show movement instead of one opaque "reading masthead".
        if progress:
            try:
                progress({"raster": pct, "raster_index": i + 1,
                          "rasters": len(RESOLUTIONS),
                          "votes": [v["answer"] for v in per.values() if v.get("answer")]})
            except Exception:
                pass
        got = [v["answer"] for v in per.values() if v.get("answer")]
        # STAGED, to keep the walk affordable on a 0.1 vCPU instance:
        #  * bail out entirely if the first raster found nothing - most walked pages
        #    carry no parseable issue number, and those rejects should cost one
        #    download, not three.
        #  * stop as soon as MIN_VOTES rasters agree - the common case costs two.
        #  * only pay for the third raster to break an actual tie.
        # Bail only after TWO rasters have come up empty. Quitting after one was
        # too eager: the sweep showed no single resolution is universally best, so
        # a page that fails to parse at pct:40 can still parse at pct:60 (this cost
        # two known-good pages, 1900-07-04 and 1900-08-01, in benchmarking). A
        # false reject is not even cheap - the walk then pays for a whole extra
        # page (JSON + download + OCR) to replace the one we discarded.
        if i >= 2 and not got:
            break
        if len(got) >= MIN_VOTES and len(set(got)) == 1:
            break
        path = os.path.join(workdir, f"{tag}_pct{pct}.jpg")
        try:
            if not (os.path.exists(path) and os.path.getsize(path) > 10000):
                je.loc_page_image(resource_url, path, pct=pct)
            crop = _band_crop(path)
            ans, field = _parse(_read_variants(crop))
            per[pct] = {"answer": ans, "field": field,
                        "image_size": list(Image.open(path).size),
                        "crop_size": list(crop.size)}
        except Exception as e:
            per[pct] = {"answer": None, "field": None, "error": str(e)[:120]}

    vals = [v["answer"] for v in per.values() if v.get("answer")]
    fields = [v["field"] for v in per.values() if v.get("answer")]
    if not vals:
        return {"answer": None, "field": None, "confidence": "none",
                "per_resolution": per, "agree": False}

    from collections import Counter
    tally = Counter(vals)
    top, top_n = tally.most_common(1)[0]
    field = next((f for v, f in zip(vals, fields) if v == top), fields[0])
    runner_n = tally.most_common(2)[1][1] if len(tally) > 1 else 0

    # A clear majority across DISTINCT rasters is the only thing that earns "high".
    if top_n >= MIN_VOTES and top_n > runner_n:
        return {"answer": top, "field": field, "confidence": "high",
                "per_resolution": per, "agree": True, "votes": dict(tally)}
    # A tie between different rasters is exactly the 175-vs-176 failure mode:
    # refuse it rather than pick a winner.
    if len(tally) > 1 and top_n == runner_n:
        return {"answer": None, "field": field, "confidence": "conflict",
                "per_resolution": per, "agree": False,
                "candidates": sorted(tally), "votes": dict(tally)}
    # Single raster produced a value: plausible but uncorroborated.
    return {"answer": top, "field": field, "confidence": "low",
            "per_resolution": per, "agree": False, "votes": dict(tally)}
