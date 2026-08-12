"""Is the NVD 502 a broken link, or a bot block that a real browser escapes?

R6 asks whether a reviewer can open the page in incognito. A 502 returned to a
scripted client is not the same fact as a 502 returned to a browser, so the two
cases are separated by varying ONLY the request headers against the same URL,
and by testing a second CVE and the site root to see whether the failure is
per-page or site-wide.
"""
import http.cookiejar
import json
import ssl
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
BROWSER = {
    "User-Agent": UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Connection": "close",
}
MINIMAL = {"User-Agent": UA, "Accept": "*/*", "Cache-Control": "no-cache"}
CURLISH = {"User-Agent": "curl/8.5.0", "Accept": "*/*"}

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

URLS = [
    "https://nvd.nist.gov/vuln/detail/CVE-2023-34095",
    "https://nvd.nist.gov/vuln/detail/CVE-2021-44228",
    "https://nvd.nist.gov/",
    "https://www.cve.org/CVERecord?id=CVE-2023-34095",
]
HEADERSETS = (("minimal", MINIMAL), ("browser", BROWSER), ("curl", CURLISH))
REPEATS = 3
TIMEOUT = 12


def get(url, headers):
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=CTX),
        urllib.request.HTTPCookieProcessor(jar))
    t0 = time.time()
    try:
        with op.open(urllib.request.Request(url, headers=headers),
                     timeout=TIMEOUT) as r:
            body = r.read(6000)
            return {"status": r.status, "bytes": len(body),
                    "ctype": (r.headers.get("Content-Type") or "")[:40],
                    "server": (r.headers.get("Server") or "")[:40],
                    "secs": round(time.time() - t0, 2), "err": ""}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "bytes": 0, "ctype": "",
                "server": (e.headers.get("Server") or "")[:40],
                "secs": round(time.time() - t0, 2), "err": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"status": 0, "bytes": 0, "ctype": "", "server": "",
                "secs": round(time.time() - t0, 2),
                "err": f"{type(e).__name__}: {str(e)[:80]}"}


def main():
    out = []
    for url in URLS:
        for label, hdr in HEADERSETS:
            trials = []
            for _ in range(REPEATS):
                trials.append(get(url, hdr))
                time.sleep(1.0)
            codes = [t["status"] for t in trials]
            out.append({"url": url, "headers": label, "codes": codes,
                        "trials": trials})
            print(f"{label:8s} {url[:50]:50s} {codes} "
                  f"{[t['secs'] for t in trials]} "
                  f"srv={trials[0]['server']!r}", flush=True)
    with open("/workspace/seal_deploy/probe_nvd_landing.json", "w") as fh:
        json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
