"""Resolve the three ambiguous results from probe_sources.py.

1. NIH RePORTER returned 405 -- suspected probe error (endpoint is POST-only),
   not an unreachable source.
2. Wikidata SPARQL returned 429 on later probes but 200 earlier -- suspected
   rate limiting, not unavailability. Wikidata is load-bearing as the universal
   third corroborator, so this must be settled.
3. RAWG returned 522 (Cloudflare origin timeout) -- suspected transient.

Also measures the Wikipedia REST API as a Wikidata-independent Wikimedia route.
Checkpoints to source_probe_gaps.json.
"""
from __future__ import annotations
import urllib.parse

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "source_probe_gaps.json")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

UA = "SealSourceProbe/1.0 (research; contact fahad@crispro.ai)"


def _req(url, data=None, headers=None, timeout=30):
    h = {"User-Agent": UA, "Accept": "application/json,text/plain,*/*"}
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            return r.status, r.read(3000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(300).decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:150]}"


def with_backoff(fn, attempts=5, base=3.0):
    """Retry on 429/5xx with exponential backoff. Returns (status, note, tries)."""
    for i in range(attempts):
        status, note = fn()
        if status == 200:
            return status, note, i + 1
        if status is not None and status not in (429, 500, 502, 503, 504, 522):
            return status, note, i + 1
        time.sleep(base * (2 ** i))
    return status, note, attempts


def main():
    out = {}

    # --- 1. NIH RePORTER, correctly as POST -------------------------------
    body = {"criteria": {"fiscal_years": [2021]},
            "include_fields": ["ProjectNum"], "limit": 1, "offset": 0}
    st, note = _req("https://api.reporter.nih.gov/v2/projects/search", data=body)
    out["nih_reporter_post"] = {
        "status": st, "ok": st == 200, "sample": note[:200],
        "verdict": "probe error in probe_sources.py (GET on a POST-only endpoint)"
                   if st == 200 else "genuinely unavailable",
    }
    print(f"NIH RePORTER (POST): status={st} -> {out['nih_reporter_post']['verdict']}")
    sys.stdout.flush()

    # --- 2. Wikidata SPARQL with backoff ----------------------------------
    q = "SELECT ?s WHERE { ?s wdt:P31 wd:Q7889 } LIMIT 1"   # instances of video game
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q)
    st, note, tries = with_backoff(lambda: _req(url, timeout=45))
    out["wikidata_sparql_backoff"] = {
        "status": st, "ok": st == 200, "tries": tries, "sample": note[:200],
        "verdict": "rate-limited, usable with backoff" if st == 200 else "unusable",
    }
    print(f"Wikidata SPARQL (backoff): status={st} tries={tries} -> "
          f"{out['wikidata_sparql_backoff']['verdict']}")
    sys.stdout.flush()

    # --- 2b. Wikidata REST entity route (different infrastructure) --------
    st, note = _req("https://www.wikidata.org/wiki/Special:EntityData/Q7889.json", timeout=45)
    out["wikidata_entitydata"] = {"status": st, "ok": st == 200, "bytes": len(note)}
    print(f"Wikidata EntityData: status={st}")

    # --- 2c. Wikipedia REST (Wikimedia, distinct host) --------------------
    st, note = _req("https://en.wikipedia.org/api/rest_v1/page/summary/Doom_(1993_video_game)")
    out["wikipedia_rest"] = {"status": st, "ok": st == 200, "sample": note[:160]}
    print(f"Wikipedia REST: status={st}")

    # --- 3. RAWG retry ----------------------------------------------------
    st, note, tries = with_backoff(
        lambda: _req("https://api.rawg.io/api/games?page_size=1"), attempts=3)
    out["rawg_retry"] = {"status": st, "ok": st == 200, "tries": tries,
                         "sample": note[:160],
                         "verdict": "needs key" if st in (401, 403) else
                                    "reachable" if st == 200 else "down/blocked"}
    print(f"RAWG (retry): status={st} tries={tries} -> {out['rawg_retry']['verdict']}")

    # --- 3b. keyless video-game alternatives ------------------------------
    for name, u in [
        ("Internet Game Database proxy (Wikidata)",
         "https://www.wikidata.org/w/api.php?action=wbsearchentities&search=Half-Life&language=en&format=json"),
        ("Open Library (game guides)",
         "https://openlibrary.org/search.json?q=video+game&limit=1"),
        ("MobyGames public page", "https://www.mobygames.com/game/1/doom/"),
    ]:
        st, note = _req(u, timeout=30)
        out[f"vg_alt::{name}"] = {"status": st, "ok": st == 200}
        print(f"video-game alt :: {name}: status={st}")

    json.dump(out, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    import urllib.parse  # noqa: E402  (used above)
    main()
