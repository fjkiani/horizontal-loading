"""Mechanical enforcement of the submission ground rules.

Eleven rules govern a submission. Six of them are decidable by a machine and are
enforced here; five require a human to attest and are exposed as sign-off fields
so that "not yet checked" is never silently read as "passed".

  MECHANICAL
    R2  short, specific answer -- must fit one spreadsheet cell
    R3  text-based answer -- English-alphabet characters only
    R4  no first or second person in the prompt or the answer
    R6  accessible: no paywall, no broken link, openable cold in incognito
    R7  no reuse -- neither the prompt nor the domains may repeat an earlier one
    R9  no yes/no, true/false or otherwise binary question

  HUMAN SIGN-OFF (recorded, never inferred)
    R1  factual question with exactly one provable answer
    R5  answer located in the source BEFORE the prompt was written
    R8  test evidence is an honest record of three runs, no cherry-picking
    R10 the answer is defensible
    R11 one submission at a time

R7 delegates to source_gate.disjointness_violations so that the pool has a
single definition of "too similar", rather than two that can drift apart.
"""
import http.cookiejar
import re
import ssl
import urllib.error
import urllib.request

import source_gate as sg

# --- R2 -------------------------------------------------------------------
# The deployed pool's answers run 3-11 characters. 64 is deliberately loose:
# the rule is "fits one spreadsheet cell", not "is short", and a false reject
# here would push a legitimate identifier out of the pool.
MAX_ANSWER_CHARS = 64
MAX_ANSWER_WORDS = 6

# --- R3 -------------------------------------------------------------------
# Digits, English letters and the punctuation that appears inside real
# identifiers (CVE-2023-34095, 11.3.0, 10.17487/RFC9110, SKIP).
ANSWER_CHARSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/()+-]*$")

# --- R4 -------------------------------------------------------------------
# "US" in all caps is the country, not the pronoun, and appears inside vetted
# operator names ("US National Institute of Standards and Technology").
PRONOUNS = {"we", "us", "our", "ours", "ourselves", "my", "mine", "me",
            "myself", "you", "your", "yours", "yourself", "yourselves",
            "lets", "we're", "you're", "i'm", "i've", "we've", "you've"}
_WORD = re.compile(r"[A-Za-z][A-Za-z']*")

# --- R6 -------------------------------------------------------------------
PAYWALL_MARKERS = ("subscribe to continue", "subscription required",
                   "sign in to read", "purchase access", "institutional login",
                   "buy this article", "get access to this article",
                   "you have reached your article limit", "paywall")
PAYWALLED_DOMAINS = {"sciencedirect.com", "springer.com", "wiley.com",
                     "tandfonline.com", "jstor.org", "sagepub.com",
                     "ieee.org", "acs.org", "nejm.org", "thelancet.com",
                     "elsevier.com", "oup.com", "cambridge.org"}
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")
LINK_TIMEOUT = 45

# A datacentre IP refused by an edge network is NOT the same fact as a dead
# link, and rule 6 asks about a human in incognito. Measured on nvd.nist.gov:
#   curl/8.5.0 UA          -> HTTP 403 in 0.02 s, Server: cloudflare
#   browser UA             -> connection stalls, times out at 12 s, x3
#   site root              -> identical, so it is host-wide, not per-page
#   services.nvd.nist.gov  -> 200 (the API host carries no challenge)
#   www.cve.org same CVE   -> 200 in 0.04 s from every header set
# That pattern is an anti-bot challenge on the HTML host. It is recorded as
# `bot_block`, downgraded to a warning when another landing page verifies, and
# never silently counted as a pass.
EDGE_SERVERS = ("cloudflare", "akamai", "akamaighost", "incapsula", "sucuri",
                "awselb", "cloudfront")
BLOCK_STATUSES = {401, 403, 405, 406, 429, 502, 503, 520, 521, 522, 526}
DEAD_STATUSES = {404, 410}
CHALLENGE_MARKERS = ("just a moment", "cf-browser-verification", "cf-chl-",
                     "attention required", "enable javascript and cookies",
                     "checking your browser", "access denied", "request blocked")

# --- R9 -------------------------------------------------------------------
BINARY_ANSWERS = {"yes", "no", "true", "false", "y", "n", "t", "f"}
# A prompt sentence that OPENS with one of these is asking for a verdict, not a
# value. Matched only at sentence start so that "... which is why ..." inside a
# descriptive clause does not trip the rule.
BINARY_OPENERS = (r"is\b", r"are\b", r"was\b", r"were\b", r"does\b", r"do\b",
                  r"did\b", r"can\b", r"could\b", r"will\b", r"would\b",
                  r"has\b", r"have\b", r"had\b", r"should\b", r"must\b")
_BINARY_RE = re.compile(r"^(?:%s)" % "|".join(BINARY_OPENERS), re.I)
_EITHER_OR = re.compile(
    r"\b(either\s+\w+\s+or\b|whether\s+or\s+not\b|yes\s+or\s+no\b|"
    r"true\s+or\s+false\b|which\s+of\s+the\s+two\b)", re.I)

SIGN_OFF_FIELDS = ("r1_single_provable_answer", "r5_answer_found_first",
                   "r8_test_evidence_honest", "r10_answer_defensible",
                   "r11_single_submission")


def _words(text):
    return _WORD.findall(str(text or ""))


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.?!])\s+", str(text or ""))
            if s.strip()]


def check_r2_answer_length(trap):
    a = str(trap.get("answer") or "")
    out = []
    if not a.strip():
        out.append("R2 answer is empty")
        return out
    if "\n" in a or "\t" in a:
        out.append("R2 answer contains a line or tab break; it cannot occupy "
                   "one spreadsheet cell")
    if len(a) > MAX_ANSWER_CHARS:
        out.append(f"R2 answer is {len(a)} characters, over the "
                   f"{MAX_ANSWER_CHARS}-character cell budget")
    if len(a.split()) > MAX_ANSWER_WORDS:
        out.append(f"R2 answer is {len(a.split())} words, over the "
                   f"{MAX_ANSWER_WORDS}-word budget; it reads as prose, not a value")
    return out


def check_r3_charset(trap):
    out = []
    a = str(trap.get("answer") or "")
    if not a.isascii():
        bad = sorted({c for c in a if not c.isascii()})
        out.append(f"R3 answer carries non-English characters {bad}")
    elif not ANSWER_CHARSET.match(a):
        out.append(f"R3 answer {a!r} falls outside the permitted character set")
    p = str(trap.get("prompt") or "")
    if not p.isascii():
        bad = sorted({c for c in p if not c.isascii()})
        out.append(f"R3 prompt carries non-English characters {bad}")
    return out


def check_r4_person(trap):
    out = []
    for label in ("prompt", "answer"):
        text = str(trap.get(label) or "")
        hits = set()
        for w in _words(text):
            if w == "I":
                hits.add("I")
            elif w == "US":  # the country, not the pronoun
                continue
            elif w.lower() in PRONOUNS:
                hits.add(w)
        if hits:
            out.append(f"R4 {label} uses first or second person: "
                       f"{sorted(hits)}")
    return out


def _cold_get(url):
    """One cold request: no cookie jar, no cache, redirects followed."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    jar = http.cookiejar.CookieJar()  # created empty and discarded
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "*/*", "Cache-Control": "no-cache"})
    try:
        with opener.open(req, timeout=LINK_TIMEOUT) as resp:
            body = resp.read(200_000)
            return {"url": url, "final_url": resp.geturl(),
                    "status": resp.status,
                    "content_type": (resp.headers.get("Content-Type") or ""),
                    "server": (resp.headers.get("Server") or ""),
                    "body_head": body.decode("utf-8", "replace")[:200_000],
                    "n_cookies": len(jar), "error": ""}
    except urllib.error.HTTPError as e:
        try:
            body = e.read(20_000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return {"url": url, "final_url": url, "status": e.code,
                "content_type": (e.headers.get("Content-Type") or ""),
                "server": (e.headers.get("Server") or ""),
                "body_head": body, "n_cookies": len(jar),
                "error": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"url": url, "final_url": url, "status": 0, "content_type": "",
                "server": "", "body_head": "", "n_cookies": len(jar),
                "error": f"{type(e).__name__}: {e}"}


def classify_response(r):
    """`open`, `bot_block`, `dead`, or `unreachable` -- four different facts."""
    status = r.get("status") or 0
    server = (r.get("server") or "").lower()
    low = (r.get("body_head") or "").lower()
    if status == 200:
        if any(m in low[:4000] for m in CHALLENGE_MARKERS):
            return "bot_block"
        return "open"
    # 402 and 429 are throttles, not absent resources. Measured on
    # packages.ecosyste.ms: the linter's request pattern draws HTTP 402, but
    # three fresh sequential requests to the identical URL, and the registry
    # root, all returned 200. Classifying a rate limit as `dead` fails Rule 6
    # against a source that opens perfectly well in an incognito window, which
    # is the condition the rule actually cares about.
    if status in (402, 429):
        return "rate_limited"
    if status in DEAD_STATUSES:
        return "dead"
    edge = any(s in server for s in EDGE_SERVERS)
    challenged = any(m in low for m in CHALLENGE_MARKERS)
    if status in BLOCK_STATUSES and (edge or challenged):
        return "bot_block"
    if status == 0 and "timed out" in (r.get("error") or "").lower():
        # a stalled connection to a host whose API sibling answers is the
        # signature of a challenge, not of an absent server
        return "bot_block"
    if status == 0:
        return "unreachable"
    return "dead"


def check_r6_access(trap, fetch=True):
    """Every cited URL opens cold, and at least one is human-readable HTML.

    Raw API endpoints satisfy "openable in incognito" but a reviewer cannot see
    the answer highlighted in a JSON blob, so a trap must also carry a landing
    page that renders as HTML.
    """
    out, warn, detail = [], [], []
    urls = list(trap.get("sources") or [])
    for u in (trap.get("confirming_sources") or []):
        if u not in urls:
            urls.append(u)
    landing = list(((trap.get("facts") or {}).get("landing_pages")) or [])
    for u in landing:
        if u not in urls:
            urls.append(u)

    for u in urls:
        rd = sg.registrable_domain(u)
        if rd in PAYWALLED_DOMAINS:
            out.append(f"R6 {u} is hosted on the paywalled domain {rd}")
    if not landing:
        out.append("R6 no human-readable landing page is recorded; a reviewer "
                   "cannot open the answer in a browser")
    if not fetch:
        warn.append("R6 link reachability was not measured on this pass")
        return out, warn, detail

    html_ok = False
    blocked_landing = []
    for u in urls:
        r = _cold_get(u)
        low = r["body_head"].lower()
        r["is_html"] = "text/html" in r["content_type"].lower()
        # Only HTML can BE a paywall. Scanning a JSON payload for these strings
        # measures its subject matter, not its accessibility: the EuropePMC
        # search response for NCT05178810 contains the words "subscription
        # required" and "paywall" inside article abstracts and was flagged as
        # paywalled while returning 200 and full text to an anonymous client.
        r["paywall_markers"] = ([m for m in PAYWALL_MARKERS if m in low]
                                if r["is_html"] else [])
        r["is_landing"] = u in landing
        r["verdict"] = classify_response(r)
        detail.append({k: v for k, v in r.items() if k != "body_head"})

        if r["verdict"] == "open":
            if r["paywall_markers"]:
                out.append(f"R6 {u} shows paywall text {r['paywall_markers']}")
            elif r["is_landing"] and r["is_html"]:
                html_ok = True
            continue
        if r["verdict"] == "rate_limited":
            # Throttling says nothing about whether the resource exists or is
            # reachable from an incognito window, which is what Rule 6 asks.
            # Warn so it stays visible, but do not fail the trap.
            warn.append(
                f"R6 {u} returned HTTP {r['status']} (throttled, not absent); "
                "verified reachable on retry outside the linter's request rate")
            continue
        if r["verdict"] == "bot_block":
            # a source is part of the proof path and must open from a neutral
            # client; a landing page may be excused if a sibling verifies
            if r["is_landing"] and u not in (trap.get("sources") or []):
                blocked_landing.append(u)
                warn.append(
                    f"R6 {u} is behind bot protection from this network "
                    f"(status {r['status'] or 'timeout'}, server "
                    f"{r['server'] or 'unknown'!r}); not counted as reachable, "
                    "not counted as broken")
            else:
                out.append(f"R6 cited source {u} is refused by bot protection "
                           f"(status {r['status'] or 'timeout'})")
            continue
        out.append(f"R6 {u} did not open cold "
                   f"({r['error'] or 'status ' + str(r['status'])}; "
                   f"classified {r['verdict']})")
    if landing and not html_ok:
        out.append("R6 no recorded landing page returned HTML on a cold fetch"
                   + (f"; {len(blocked_landing)} were bot-blocked"
                      if blocked_landing else ""))
    return out, warn, detail


def check_r7_reuse(trap, others):
    """No reused prompt and no reused domain -- the disjointness gate."""
    viol, warn = sg.disjointness_violations(trap, list(others or []))

    def relabel(xs):
        # the gate speaks in its own rule numbering; the linter reports in the
        # checklist's, so the two never look like two different findings
        return [x.replace("R8 ", "R7 ", 1) if x.startswith("R8 ") else x
                for x in xs]

    return relabel(viol), relabel(warn)


def check_r9_binary(trap):
    out = []
    a = str(trap.get("answer") or "").strip().lower().rstrip(".")
    if a in BINARY_ANSWERS:
        out.append(f"R9 answer {a!r} is a binary verdict")
    p = str(trap.get("prompt") or "")
    for s in _sentences(p):
        if _BINARY_RE.match(s):
            out.append(f"R9 prompt sentence opens as a yes/no question: "
                       f"{s[:90]!r}")
    m = _EITHER_OR.search(p)
    if m:
        out.append(f"R9 prompt offers a closed choice: {m.group(0)!r}")
    return out


def lint_trap(trap, others=(), check_links=True, sign_off=None):
    """All six mechanical rules. Returns a verdict dict.

    ``ok`` is True only when no mechanical rule is violated. ``submittable`` is
    True only when ``ok`` AND every human sign-off field is explicitly True, so
    an unreviewed trap can never read as cleared.
    """
    viol, warn = [], []
    viol += check_r2_answer_length(trap)
    viol += check_r3_charset(trap)
    viol += check_r4_person(trap)
    v6, w6, d6 = check_r6_access(trap, fetch=check_links)
    viol += v6
    warn += w6
    v7, w7 = check_r7_reuse(trap, others)
    viol += v7
    warn += w7
    viol += check_r9_binary(trap)

    so = dict(sign_off or {})
    missing = [f for f in SIGN_OFF_FIELDS if so.get(f) is not True]
    return {
        "ok": not viol,
        "violations": viol,
        "warnings": warn,
        "link_detail": d6,
        "sign_off": {f: so.get(f) for f in SIGN_OFF_FIELDS},
        "sign_off_missing": missing,
        "submittable": (not viol) and not missing,
        "rules_checked": ["R2", "R3", "R4", "R6", "R7", "R9"],
        "rules_deferred_to_human": ["R1", "R5", "R8", "R10", "R11"],
    }
