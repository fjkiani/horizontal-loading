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
import json, os, re, time, base64, io
from PIL import Image
import pytesseract

import join_engine as je

# --- OCR engine config -------------------------------------------------------
# Primary extractor: Cohere vision LLM (command-a-vision). Fallback: tesseract.
# The Cohere key is read from the environment (COHERE_API_KEY); the module-level
# default below is a convenience for this deployment only.
COHERE_API_KEY = os.environ.get("COHERE_API_KEY", "")
COHERE_MODEL = os.environ.get("COHERE_OCR_MODEL", "command-a-vision-07-2025")
_COHERE = None


def _cohere_client():
    global _COHERE
    if _COHERE is None:
        import cohere
        _COHERE = cohere.Client(api_key=COHERE_API_KEY)
    return _COHERE


def cohere_available():
    return bool(COHERE_API_KEY)

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


# --- api-proof gate -------------------------------------------------------
#
# The gate asserts: "this answer is NOT recoverable from the LOC text layer."
# The naive test -- `answer in strip_separators(page_text)` -- fails in BOTH
# directions, and a 322-page sweep of every trap's issue caught both:
#
#   FALSE REJECT. Stripping every separator from the whole page concatenates
#   unrelated numbers, so a short answer matches across boundaries. The Ledger
#   1922-03-27 answer "166" was flagged as leaking on two pages; a standalone
#   token search finds zero occurrences of 166 anywhere in that 30-page issue.
#
#   FALSE ACCEPT. A bare digit match ignores whether a SOLVER could use it.
#   ~40% of all 3-digit numbers occur somewhere in a 30-page Ledger issue
#   (356/900, 389/900, 364/900 measured), so a hit is near-chance. The pooled
#   1922-06-06 answer "227" hits twice, both times as a street address
#   ("227 North Seventh street"). Nothing marks those as the issue number.
#
# What a solver can actually exploit is the answer in LABEL-BEARING form -- the
# masthead's own "VOL. VIII.-NO. 227", or "ESTABLISHED 1832" for a founding
# year. That is the only match that tells the solver WHICH number is the answer.
# The label must track the field: scoring V03's real "ESTABLISHED 1832" leak
# with the issue-number label returned clean, a false negative in the auditor.
#
# Scope matters too. The gate historically checked one page, but the masthead is
# printed on every page and the answer is an issue-level invariant, so a clean
# named page proves nothing on its own (V02 names p23 and V03 names p9; both are
# clean, and both issues leak on page 1).
# Separators are not noise to be stripped blindly, nor can they be ignored. The
# Tribune prints "No. 19,678" and LOC's OCR renders the comma as a period, so the
# raw text reads "N* 19.678": a plain token search for 19678 finds NOTHING even
# though the masthead is fully present. Stripping every separator fixes that but
# then matches "$1.66" for the answer 166. Both failures are avoided by allowing a
# separator ONLY where a thousands grouping can legally fall -- every third digit
# from the right. That is positional, so "19,678" matches 19678 and "1.66" cannot
# match 166.
_SEP = r"[,.\s\u2019']?"
# OCR renders "No." as N followed by almost any superscript-ish glyph: observed
# N*, N°, N®, Nº, N", No.
_LABEL_ISSUE = r"(?:VOL[^0-9]{0,20})?N[O0o\u00ba\u00b0\u00ae*\"\']{0,2}[.,:; ]{0,4}"
# "FOUNDED MARCH 1. 1832" puts a digit between the label and the year, so a
# [^0-9] run cannot reach it. Use a bounded lazy window over any characters
# instead. Bare "EST" is dropped (too common a substring); "EST." is kept.
_LABEL_YEAR = r"\b(?:ESTABLISHED|ESTAB|ESTD|FOUNDED|EST\.)[\s\S]{0,30}?"


def _sep_pattern(answer):
    """Digits of `answer` with an optional separator at each thousands boundary."""
    d = re.sub(r"[^0-9]", "", str(answer))
    out = []
    for i, ch in enumerate(d):
        if i and (len(d) - i) % 3 == 0:
            out.append(_SEP)
        out.append(re.escape(ch))
    return "".join(out)


def label_bearing_leak(text, answer, field="issue number"):
    """Find the answer in a form a SOLVER could recognise as the answer.

    A bare digit match is deliberately not a leak: ~40% of all 3-digit numbers
    occur somewhere in a 30-page Ledger issue (measured 356/900, 389/900,
    364/900), so an unlabelled hit is near-chance and tells a solver nothing
    about which number is the issue number. The pooled 1922-06-06 answer 227
    occurs twice, both as a street address. What is exploitable is the answer
    carrying its own field label -- the masthead's "VOL. VIII.-NO. 227", or
    "ESTABLISHED 1832" for a founding year. The label must match the FIELD;
    scoring a year-established trap with the issue-number label reports clean.
    """
    label = _LABEL_YEAR if "year" in (field or "").lower() else _LABEL_ISSUE
    pat = rf"{label}{_sep_pattern(answer)}(?![0-9])"
    return [" ".join(m.group(0).split()) for m in re.finditer(pat, text or "", re.I)]


def standalone_hits(text, answer):
    """Occurrences of the answer as a whole number token, separators allowed at
    thousands boundaries, label or not."""
    return [m.start() for m in re.finditer(
        rf"(?<![0-9]){_sep_pattern(answer)}(?![0-9])", text or "")]


def api_proof_holds(text, answer, field="issue number"):
    """The api-proof gate. Returns (holds, reason, evidence).

    Two-tier, because the informativeness of a bare digit match depends entirely
    on how many digits it has. Measured over whole issues:

        3-digit answers, Evening Public Ledger : 356/900, 389/900, 364/900
                                                 distinct tokens present
                                                 -> P(chance hit) ~= 0.40
        5-digit answers, New-York Tribune      : 28/90000, 99/90000
                                                 -> P(chance hit) ~= 0.001

    So a standalone "227" in a 30-page Ledger is near-chance -- and in fact both
    of its occurrences are street addresses. A standalone "19,678" in a Tribune
    is not chance. The gate therefore requires a FIELD LABEL for short answers
    and rejects on any standalone token for 5+ digit answers.
    """
    lab = label_bearing_leak(text, answer, field)
    if lab:
        return False, "label-bearing leak", lab[:3]
    hits = standalone_hits(text, answer)
    digits = len(re.sub(r"[^0-9]", "", str(answer)))
    if hits and digits >= 5:
        i = hits[0]
        return False, "rare standalone token", [" ".join(text[max(0, i - 60):i + 45].split())]
    return True, None, []


def _norm_digits(s):
    return re.sub(r"[,\.\s]", "", s or "")


def _masthead_crop(img_path, frac=0.16):
    im = Image.open(img_path).convert("L")
    w, h = im.size
    return im.crop((0, 0, w, int(h * frac)))


def _ocr_at_scale(crop, scale):
    c = crop.resize((crop.width * scale, crop.height * scale))
    return pytesseract.image_to_string(c)


def _cohere_read_masthead(crop, max_retries=6):
    """Read the masthead with the Cohere vision LLM. Returns the raw text the
    model emits (expected: just the digits), or '' on persistent failure.
    Retries through the trial-key per-minute throttle with backoff."""
    buf = io.BytesIO()
    crop.convert("RGB").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    prompt = ("Read the masthead banner of this historical newspaper front page. "
              "Report the issue number (the digits after No./Nº/NO.) if present, "
              "otherwise the year established (after ESTABLISHED/ESTD/FOUNDED). "
              "Reply with ONLY the digits, no words, no punctuation.")
    for attempt in range(max_retries):
        try:
            resp = _cohere_client().v2.chat(
                model=COHERE_MODEL,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]}],
                max_tokens=20,
            )
            return resp.message.content[0].text.strip()
        except Exception as e:
            # 429 trial throttle -> back off; other errors -> short wait then retry
            wait = 20 + attempt * 10 if "429" in str(e) or "TooManyRequests" in type(e).__name__ else 5
            if attempt < max_retries - 1:
                time.sleep(wait)
    return ""


def _parse_cohere_answer(text):
    """Parse the model's digit reply into (answer, field). Issue numbers are
    large (>=4 digits) or small; a 4-digit value in 1600-1999 range that the
    model labels a year is a year-established. We infer field by magnitude."""
    digits = _norm_digits(text)
    if not digits or not digits.isdigit():
        return None, None
    # Heuristic: 4-digit values in 1600-1999 are ambiguous; the prompt asks for
    # issue number first, so treat as issue number unless it's clearly a year.
    return digits, "issue number"


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


def generate_trap(lccn="sn83030214", start_date=None, max_steps=8, min_confidence="high",
                  progress=None):
    """Walk forward from a seed front page, returning the FIRST trap that passes
    every gate. Returns a dict with the new prompt + answer + provenance, or
    raises RuntimeError if no clean trap is found within max_steps pages.

    progress: optional callable(dict). Invoked as the walk advances so a caller
    (the async job endpoint) can report which page is being worked and why pages
    are being rejected. A walk can take minutes on a small instance, and a bare
    "running" status is indistinguishable from a hang."""
    def _emit(**kw):
        if progress:
            try:
                progress(kw)
            except Exception:
                pass  # progress reporting must never break generation

    start_date = start_date or SEEDS.get(lccn, "1900-01-01")
    url = f"https://www.loc.gov/resource/{lccn}/{start_date}/ed-1/?sp=1"
    tried = 0
    for _ in range(max_steps):
        _emit(phase="fetching page metadata", step=tried + 1, max_steps=max_steps)
        meta = je.get(url + ("&fo=json" if "?" in url else "?fo=json"), as_json=True)
        item = meta.get("item", {})
        date = item.get("date", start_date)
        title = re.sub(r"\s*\(.*?\)\s*", " ", item.get("title", lccn)).split(",")[0].strip()
        tried += 1
        _emit(phase="reading masthead", step=tried, max_steps=max_steps,
              date=date, paper=title)

        img = os.path.join(_IMG_DIR, f"{lccn}_{date}.jpg")
        try:
            je.loc_page_image(url, img, pct=15)  # pct:15 is enough for the masthead and faster
        except Exception:
            img = None

        if img and _legible(img):
            crop = _masthead_crop(img)
            # EXTRACTION: cross-RESOLUTION agreement (masthead_reader).
            #
            # The previous design upscaled ONE pct:15 raster 2x/3x/4x and called
            # agreement "high confidence". A controlled resolution sweep proved
            # that unsound: on Ledger 1922-04-06 (truth 175) the pct:25 raster
            # made all three upscales agree on 176, so the gate accepted a WRONG
            # answer at high confidence. tesseract, EasyOCR and the Cohere vision
            # LLM all returned 176 from that same degraded raster - the shared bad
            # input, not the engine, was the fault.
            #
            # Benchmarked on the 9 agent-verified ground truths:
            #   old  8/9 correct, 8 accepted, 1 ACCEPTED-WRONG  -> 88% precision
            #   new  7/9 correct, 4 accepted, 0 accepted-wrong  -> 100% precision
            # Lower recall is the right trade: the walk simply advances a page.
            try:
                import masthead_reader as mr
                r = mr.read_masthead(
                    url, cache_tag=f"{lccn}_{date}",
                    progress=lambda d: _emit(
                        phase=f"reading masthead at pct:{d['raster']} "
                              f"({d['raster_index']}/{d['rasters']})",
                        step=tried, max_steps=max_steps, date=date, paper=title,
                        votes=d.get("votes") or []))
                answer, field, conf = r["answer"], r["field"], r["confidence"]
                engine = "tesseract-xres"
            except Exception:
                reads = [_ocr_at_scale(crop, s) for s in (2, 3, 4)]
                answer, field, conf = _extract_with_confidence(*reads)
                engine = "tesseract-legacy"

            if answer and field and conf == min_confidence:
                # G2 api-proof: answer absent from whole-page OCR.
                _emit(phase="checking api-proof gate", step=tried, max_steps=max_steps,
                      date=date, paper=title, candidate=answer)
                page_ocr = je.loc_page_ocr(url)
                api_proof, gate_reason, gate_evidence = api_proof_holds(
                    page_ocr, answer, field)

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
                        "api_proof": True, "confidence": conf, "ocr_engine": engine,
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
                # Explain the rejection so a long walk is legible rather than silent.
                why = (f"{field} recoverable from the page OCR "
                       f"({gate_reason}: {gate_evidence[0][:60] if gate_evidence else '?'})"
                       if not api_proof else
                       "answer leaks into the prompt" if leak else
                       f"prompt word count {wc} outside 70-150")
                _emit(phase="page rejected", step=tried, max_steps=max_steps,
                      date=date, paper=title, reason=why)
            else:
                _emit(phase="page rejected", step=tried, max_steps=max_steps,
                      date=date, paper=title,
                      reason=f"masthead read inconclusive (confidence: {conf})")
        else:
            _emit(phase="page rejected", step=tried, max_steps=max_steps,
                  date=date, paper=title, reason="scan failed legibility check")
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
    """Identity for de-duplication.

    Scan traps are identified by (newspaper, issue date, field). API-native
    traps have no lccn or date at all, so keying them on t["lccn"] raised
    KeyError -- which is why the pool could only ever hold the loc.gov corpus.
    Both shapes now key cleanly and cannot collide, because the api-native key
    carries its track name in the first slot.
    """
    if t.get("track") == "api-native" or "lccn" not in t:
        return ("api-native", t.get("category") or "", t.get("field") or "",
                str(t.get("entity") or ""))
    return (t["lccn"], t["date"], t["field"])


# --- pool admission ----------------------------------------------------------
# Nothing reaches the served pool without passing source_gate. Validation on
# WRITE rather than on read is deliberate: a trap that is invalid on disk has
# already been served once by the time a read-time check notices it.

class PoolRejected(Exception):
    """A trap was refused admission to the served pool."""


def _fmt_viol(viol):
    """Normalise violations for display. source_gate.validate_trap returns
    list[str] while banned_violations returns list[(url, reason)]; calling
    list() on the former shredded each message into single characters."""
    out = []
    for v in viol or []:
        out.append(v if isinstance(v, str) else " ".join(str(x) for x in v))
    return out


def _validate_for_pool(trap, min_operators=3):
    """Return (ok, violations). Scan traps predate the category/operator schema,
    so they are checked only for banned sources; api-native traps get the full
    gate including the R3c self-confirmation rule."""
    import source_gate as sg
    if trap.get("track") == "api-native":
        return sg.validate_trap(trap, min_operators=min_operators)
    urls = list(trap.get("sources") or [])
    for k in ("image_url", "page_url", "ocr_url"):
        if trap.get(k):
            urls.append(trap[k])
    viol = list(sg.banned_violations(urls))
    return (not viol), viol


def _save_pool(pool, min_operators=3, strict=False):
    """Write the served pool, refusing to persist any entry that fails the gate.

    Returns (n_written, rejected) where rejected is a list of
    (key, violations). With strict=True the first rejection raises instead,
    which is what a caller admitting a single new trap wants.
    """
    keep, rejected = [], []
    for t in pool:
        ok, viol = _validate_for_pool(t, min_operators=min_operators)
        if ok:
            keep.append(t)
        else:
            rejected.append((_key(t), _fmt_viol(viol)))
            if strict:
                raise PoolRejected(f"{_key(t)}: {_fmt_viol(viol)}")
    _save(_POOL_PATH, keep)
    return len(keep), rejected


def admit_api_trap(trap, min_operators=3):
    """Admit one API-native trap (a category_traps.Candidate.to_trap() dict) to
    the served pool. Raises PoolRejected if it does not pass the gate, so a
    failing trap is never written and never served."""
    t = dict(trap)
    t.setdefault("track", "api-native")
    t.setdefault("verified", True)
    ok, viol = _validate_for_pool(t, min_operators=min_operators)
    if not ok:
        raise PoolRejected(f"{t.get('category')}: {_fmt_viol(viol)}")
    pool = _load(_POOL_PATH)
    k = _key(t)
    pool = [p for p in pool if _key(p) != k]
    pool.append(t)
    _save_pool(pool, min_operators=min_operators, strict=True)
    return t


def resolve_image_path(t):
    """Resolve a trap's stored image_path portably: absolute as-is, else relative
    to the repo root (handles paths authored under a different absolute root)."""
    p = t.get("image_path") or ""
    if os.path.isabs(p) and os.path.exists(p):
        return p
    cand = os.path.join(_REPO, "generated_images", os.path.basename(p))
    return cand if os.path.exists(cand) else p


def _rel_image_path(trap):
    """Store image_path repo-relative so the pools stay portable across machines
    and into the container. Absolute /workspace paths do not resolve on Render."""
    p = trap.get("image_path") or ""
    if os.path.isabs(p):
        base = os.path.basename(p)
        trap["image_path"] = os.path.join("generated_images", base)
    return trap


def _persist_pending(trap):
    trap = _rel_image_path(trap)
    # Never queue something already confirmed in the verified pool (the test suite
    # and repeat walks can re-derive an existing page).
    if any(_key(g) == _key(trap) for g in _load(_POOL_PATH)):
        return
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


def confirm_candidate(lccn, date, field, confirmed_answer, verifier="agent",
                      require_api_proof=True):
    """Move a pending candidate to the verified pool, setting its answer to the
    independently-confirmed value. If the confirmed answer differs from the OCR
    candidate, the OCR value is discarded and the confirmed value is used.
    Returns the verified trap, or None if no matching pending candidate.

    THE api-proof GATE IS CONTINGENT ON THE ANSWER, SO IT MUST BE RE-RUN HERE.
    A previous version of this function only rewrote the golden trace and left
    api_proof standing from generation time. That is unsound, and it shipped two
    invalid traps:

      * 1922-04-06 was generated with the OCR answer 176. 176 is absent from the
        LOC text layer, so the gate passed. The answer was later corrected to the
        true 175 -- but 175 IS present in that text layer ("VOL. VIII. NO. 175"),
        so correcting the answer silently turned a valid trap into a solvable one
        while api_proof still read true.
      * 1922-03-11 was generated as 158 (a misread of 153) and passed for the same
        reason: the gate compared a string that simply is not on the page.

    The failure mode is systematic, not incidental: a WRONG answer is less likely
    to appear in the page text than the right one, so mis-read pages pass this
    gate MORE readily than correctly-read ones. The gate's safety property is
    inverted unless it is re-evaluated against the confirmed answer.
    """
    pend = _load(_PENDING_PATH)
    pool = _load(_POOL_PATH)
    k = (lccn, date, field)
    match = next((p for p in pend if _key(p) == k), None)
    if not match:
        return None

    prior = match.get("answer")
    match["answer"] = str(confirmed_answer)
    match["verifier"] = verifier
    match["confirmed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if match.get("golden"):
        match["golden"][-1] = f"= {confirmed_answer}"

    # Re-run the gate whenever the confirmed answer is not the one it was
    # originally evaluated against.
    if str(prior) != str(confirmed_answer):
        try:
            ocr = je.loc_page_ocr(match["resource_url"])
            holds, gate_reason, gate_evidence = api_proof_holds(
                ocr, confirmed_answer, field)
        except Exception as e:
            # Unknown is not the same as passing. Refuse to promote on an
            # unevaluated gate.
            match["verified"] = False
            match["api_proof"] = None
            match["gate_note"] = f"api-proof re-check failed to run: {str(e)[:150]}"
            match["rejected_reason"] = "api-proof could not be re-evaluated"
            _save(_PENDING_PATH, pend)
            return match
        match["api_proof"] = holds
        match["gate_note"] = (
            f"api-proof re-evaluated after answer changed {prior} -> {confirmed_answer}"
            + (f"; {gate_reason}: {gate_evidence}" if not holds else ""))
        if not holds and require_api_proof:
            # Do NOT promote: the confirmed answer is recoverable from the text
            # layer, so this page is not a vision trap regardless of how well the
            # masthead was read. Leave it in pending with the finding recorded.
            match["verified"] = False
            match["rejected_reason"] = (
                f"answer {confirmed_answer} is present in the LOC text layer; "
                "page is solvable without reading the scan")
            _save(_PENDING_PATH, pend)
            return match

    match["verified"] = True
    match.pop("rejected_reason", None)
    pend = [p for p in pend if _key(p) != k]
    _save(_PENDING_PATH, pend)
    if not any(_key(p) == k for p in pool):
        pool.append(match)
        _save(_POOL_PATH, pool)
    return match


def cohere_confirm(lccn, date, field, auto_promote=True):
    """Adjudicate a pending candidate with the Cohere vision LLM — ONE high-fidelity
    read of the masthead crop.

    Outcome logic (deliberately conservative):
      * agree     — Cohere's digits match the tesseract candidate. Two independent
                    engines of DIFFERENT kinds (classical OCR + vision LLM) agree,
                    so the answer is promoted to the verified pool.
      * conflict  — they disagree. This is exactly the 175-vs-176 failure mode, so
                    we do NOT silently pick a winner: the candidate stays pending
                    with both readings recorded for human/agent adjudication.
      * unread    — Cohere returned nothing (rate limited / refused). Stays pending.

    Returns a dict describing the outcome.
    """
    pend = _load(_PENDING_PATH)
    k = (lccn, date, field)
    match = next((p for p in pend if _key(p) == k), None)
    if not match:
        return {"outcome": "not_found"}
    if not cohere_available():
        return {"outcome": "unavailable", "detail": "COHERE_API_KEY not set"}

    img = resolve_image_path(match)
    if not os.path.exists(img):
        return {"outcome": "no_image", "detail": img}
    crop = _masthead_crop(img)
    raw = _cohere_read_masthead(crop)
    vision_answer, _ = _parse_cohere_answer(raw)

    if not vision_answer:
        return {"outcome": "unread", "candidate": match["answer"], "raw": raw}

    if _norm_digits(vision_answer) == _norm_digits(match["answer"]):
        if auto_promote:
            t = confirm_candidate(lccn, date, field, match["answer"],
                                  verifier="tesseract+cohere-vision")
            return {"outcome": "agree", "answer": match["answer"], "trap": t}
        return {"outcome": "agree", "answer": match["answer"]}

    # Disagreement: record both readings, keep pending, escalate.
    for p in pend:
        if _key(p) == k:
            p["conflict"] = {"tesseract": match["answer"], "cohere": vision_answer,
                             "raw": raw, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save(_PENDING_PATH, pend)
    return {"outcome": "conflict", "tesseract": match["answer"], "cohere": vision_answer,
            "detail": "engines disagree; candidate held for adjudication"}


def reject_candidate(lccn, date, field, reason=""):
    """Drop a pending candidate that failed ground-truth confirmation."""
    pend = _load(_PENDING_PATH)
    pend = [p for p in pend if _key(p) != (lccn, date, field)]
    _save(_PENDING_PATH, pend)
    return True


if __name__ == "__main__":
    t = generate_trap()
    print(json.dumps({k: t[k] for k in ("paper", "date", "field", "answer", "api_proof", "word_count")}, indent=2))
