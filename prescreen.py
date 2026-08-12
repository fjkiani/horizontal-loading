"""Cheap pre-screen for a candidate trap. A PROXY, never a difficulty measure.

No test in this repository measures whether a model finds a prompt hard. There
is no solver quota, so `solver_difficulty` is null on every trap and this module
is forbidden from writing it -- writing a proxy into that field is how a guess
becomes a number someone later cites.

What is measured here is the cheap-path surface:

  n_base                 how many records must be examined
  enumerable_in_one_page whether the whole collection arrived in one request
  margin                 how far the winner sits above the runner-up
  p_uniform_guess        chance of hitting the answer by sampling the field
  search_leak            whether a plain web search of the prompt's own wording
                         returns the answer in its result text

The last one is the only network call. It is a leak probe, not a difficulty
probe: a hit means the answer is reachable without doing the work, which is
decisive evidence AGAINST the prompt. A miss is weak evidence -- one engine on
one day -- and is reported as such.
"""
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

SEARCH_ENDPOINT = "https://lite.duckduckgo.com/lite/?q="
SEARCH_TIMEOUT = 25
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")
_STOP = {"the", "a", "an", "of", "that", "those", "this", "these", "and", "or",
         "in", "on", "for", "to", "with", "by", "it", "its", "as", "at", "from",
         "every", "each", "only", "consider", "report", "give", "exactly", "one",
         "other", "than", "any", "more", "most", "single", "words", "word",
         "alone", "no", "not", "before", "answering", "check", "against",
         "publishes", "lists", "carries", "which", "who", "what", "where"}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _num(x):
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def margins(trap):
    """Absolute and relative gap between the top two ranking keys."""
    ev = trap.get("ranking_evidence") or {}
    tk = [_num(k) for k in (ev.get("top_keys") or [])]
    tk = [k for k in tk if k is not None]
    if len(tk) < 2:
        return {"margin_absolute": None, "margin_relative": None,
                "top_keys_numeric": False}
    gap = tk[0] - tk[1]
    rel = (gap / tk[0]) if tk[0] else None
    return {"margin_absolute": gap,
            "margin_relative": (round(rel, 4) if rel is not None else None),
            "top_keys_numeric": True}


def search_query(trap, max_terms=9):
    """The prompt's own distinguishing wording, not the answer.

    Searching the answer is meaningless -- a real identifier is always indexed.
    The question worth asking is whether an engine hands over the answer when
    it is shown the QUESTION, which is the cheap path a solver would actually
    take.
    """
    text = str(trap.get("prompt") or "")
    ans = str(trap.get("answer") or "")
    terms, seen = [], set()
    for w in re.findall(r"[A-Za-z][A-Za-z0-9.\-]{2,}", text):
        lw = w.lower().strip(".")
        if lw in _STOP or lw in seen or lw == ans.lower():
            continue
        seen.add(lw)
        terms.append(w)
        if len(terms) >= max_terms:
            break
    ent = str((trap.get("facts") or {}).get("day")
              or (trap.get("facts") or {}).get("month") or "")
    return " ".join(terms + ([ent] if ent else []))


def search_leak(trap, query=None, attempts=3, backoff=(20, 60)):
    """Query the engine with the prompt's wording; retry through the throttle.

    Measured: the endpoint answers 200 to a cold query and 202 once queries
    arrive back to back. 202 is a throttle, so it is retried with a widening
    pause before the result is declared unmeasured.
    """
    q = query or search_query(trap)
    url = SEARCH_ENDPOINT + urllib.parse.quote(q)
    ans = str(trap.get("answer") or "")
    out = {"query": q, "endpoint": "duckduckgo lite", "status": 0,
           "answer_in_results": None, "n_chars": 0, "error": "",
           "attempts": 0, "interpretation": ""}
    body = ""
    for i in range(max(1, attempts)):
        out["attempts"] = i + 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                       "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT,
                                        context=_CTX) as r:
                body = r.read(400_000).decode("utf-8", "replace")
                out["status"] = r.status
        except urllib.error.HTTPError as e:
            out["status"] = e.code
            out["error"] = f"HTTP {e.code}"
            body = ""
        except Exception as e:  # noqa: BLE001
            out["error"] = f"{type(e).__name__}: {str(e)[:80]}"
            body = ""
        if out["status"] == 200 and body:
            break
        if i < attempts - 1:
            time.sleep(backoff[min(i, len(backoff) - 1)])
    if not body or out["status"] != 200:
        # A 202 from this endpoint is an anti-bot interstitial, not an empty
        # result set. Scoring it as "no leak" would turn a throttle into
        # exculpatory evidence, so it stays explicitly unmeasured.
        out["interpretation"] = (f"unmeasured; the engine answered "
                                 f"{out['status'] or out['error']} rather than "
                                 "returning results")
        return out
    text = re.sub(r"<[^>]+>", " ", body)
    out["n_chars"] = len(text)
    out["n_result_links"] = len(re.findall(r'<a[^>]+href="https?://', body))
    if out["n_result_links"] < 3:
        out["interpretation"] = ("unmeasured; the response carried "
                                 f"{out['n_result_links']} outbound links, so "
                                 "no result set was returned")
        return out
    hit = bool(ans) and ans.lower() in text.lower()
    out["answer_in_results"] = hit
    out["interpretation"] = (
        "LEAK: the answer is present in the result text for the prompt's own "
        "wording, so it is reachable without doing the work"
        if hit else
        "no hit on this engine on this run; weak evidence only, one engine and "
        "one query formulation")
    return out


def prescreen(trap, do_search=True):
    ev = trap.get("ranking_evidence") or {}
    pages = ev.get("pages_fetched")
    cap = ev.get("page_cap")
    n_true = ev.get("n_true") or ev.get("n_base") or trap.get("n_base")
    out = {
        "field": trap.get("field"),
        "answer": trap.get("answer"),
        "n_base": trap.get("n_base"),
        "n_true": n_true,
        "pages_fetched": pages,
        "page_cap": cap,
        "enumerable_in_one_page": (pages == 1),
        "collection_truncated": bool(cap and n_true and n_true >= cap),
        "n_tied_at_extremum": ev.get("n_tied_at_extremum"),
        "distinct_keys": ev.get("distinct_keys"),
        "p_uniform_guess": ev.get("p_answer_by_uniform_guess"),
        "spearman_key_vs_api_order": ev.get("spearman_key_vs_api_order"),
        "answer_chars": len(str(trap.get("answer") or "")),
        "collection_is_explicit": trap.get("collection_is_explicit"),
    }
    out.update(margins(trap))
    out["search_leak"] = search_leak(trap) if do_search else {
        "interpretation": "not run"}
    sl = out["search_leak"] or {}
    leak = sl.get("answer_in_results") is True
    out["search_leak_measured"] = sl.get("answer_in_results") is not None
    blockers, caveats = [], []
    if leak:
        blockers.append("the answer is returned by a plain search of the prompt")
    elif not out["search_leak_measured"]:
        # An unmeasured probe is not a clean probe. It is recorded as an open
        # question so that "no leak found" can never be read off a throttle.
        caveats.append("the search-leak probe did not run to completion "
                       f"({sl.get('interpretation', 'not run')}); leak status "
                       "is unknown, not absent")
    out["caveats"] = caveats
    if out["collection_truncated"]:
        blockers.append("the collection hit the service page cap, so the "
                        "ranking may be over a truncated set")
    if (out["n_tied_at_extremum"] or 1) != 1:
        blockers.append("the extremum is tied")
    out["blockers"] = blockers
    out["clear"] = not blockers
    out["label"] = ("PROXY -- cheap-path surface only. This is NOT a measurement "
                    "of solver difficulty and must never be written to "
                    "solver_difficulty.")
    return out
