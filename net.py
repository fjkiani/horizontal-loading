"""net.py — shared HTTP layer for the category trap generators.

On-disk caching plus exponential backoff. Caching matters for two reasons:
reproducibility (a trap must be re-verifiable against the bytes it was built
from) and rate limits (Wikidata 429s under rapid probing, as measured).
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

CACHE = os.environ.get("SEAL_NET_CACHE", "/workspace/seal_cache")
os.makedirs(CACHE, exist_ok=True)

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

UA = "SealTrapGenerator/1.0 (research; contact fahad@crispro.ai)"

RETRY_STATUS = {429, 500, 502, 503, 504, 522, 524}


class FetchError(RuntimeError):
    pass


def _key(url, body):
    h = hashlib.sha256()
    h.update(url.encode())
    if body is not None:
        h.update(b"|")
        h.update(json.dumps(body, sort_keys=True).encode())
    return h.hexdigest()[:40]


def fetch(url, body=None, timeout=60, attempts=5, base_sleep=2.0,
          headers=None, use_cache=True, binary=False):
    """GET (or POST when body is given) with cache and backoff. Returns text/bytes."""
    path = os.path.join(CACHE, _key(url, body) + (".bin" if binary else ".txt"))
    if use_cache and os.path.exists(path) and os.path.getsize(path) > 0:
        mode = "rb" if binary else "r"
        with open(path, mode) as fh:
            return fh.read()

    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
         "Accept-Encoding": "gzip"}
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"

    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                out = raw if binary else raw.decode("utf-8", "replace")
                if use_cache:
                    mode = "wb" if binary else "w"
                    with open(path, mode) as fh:
                        fh.write(out)
                return out
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in RETRY_STATUS:
                raise FetchError(f"{url} -> HTTP {e.code}") from None
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(base_sleep * (2 ** i))
    raise FetchError(f"{url} -> gave up after {attempts} attempts ({last})")


def get_json(url, body=None, **kw):
    txt = fetch(url, body=body, **kw)
    return json.loads(txt)


def get_gzip_lines(url, max_lines=None, **kw):
    """Stream a .gz text resource (IMDb datasets, NCES) into a list of lines."""
    raw = fetch(url, binary=True, **kw)
    buf = io.BytesIO(raw)
    with gzip.GzipFile(fileobj=buf) as gz:
        out = []
        for i, line in enumerate(gz):
            if max_lines is not None and i >= max_lines:
                break
            out.append(line.decode("utf-8", "replace").rstrip("\n"))
    return out


def wikidata_sparql(query, timeout=90):
    """Wikidata SPARQL with the backoff the probe showed is required."""
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query)
    return json.loads(fetch(url, timeout=timeout, attempts=6, base_sleep=3.0))


def wikidata_search(term, timeout=45):
    url = ("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json"
           f"&language=en&limit=5&search={urllib.parse.quote(str(term))}")
    return json.loads(fetch(url, timeout=timeout))


def wikidata_entity(qid, timeout=60):
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    return json.loads(fetch(url, timeout=timeout))
