"""recall4 -- close the 'unmeasurable' verdict on health and science.

recall3 returned n_informative = 0 for both categories and I reported that as
"unmeasurable, not clean". That was honest but incomplete. The reason it was
unmeasurable is not only that Wikipedia is the wrong corpus; it is that
SUBSTRING RECALL IS THE WRONG STATISTIC for these two keys.

  health  key = len(secondaryOutcomes)   values like 18, 10, 9
  science key = number of authors        values like 20, 5, 4

A two-character key cannot be tested by asking "does this string appear in
that document" against any corpus, because it appears in almost every
document by accident. recall3's MIN_KEY_CHARS = 4 filter correctly refused to
report a number, which is why n_informative was 0.

Two statistics that ARE measurable, and that answer the question the recall
test was actually groping at -- can a solver get the answer without doing the
enumeration the trap demands?

  S1 DERIVABILITY / COVERAGE. For each ranked member, is the key recomputable
     from a public per-record view, and does the recomputed value AGREE with
     the value the trap ranked on? Disagreement would be a data-integrity
     defect in the key itself, which no leak test would ever surface. High
     agreement is GOOD: it means the item is computable rather than a guess.
     Coverage of the population bounds prose reconstructibility from below.

  S2 CONTEXTUAL KEY MATCH, with the permutation control retained. Instead of
     bare substring matching, require the key value to appear ADJACENT to
     language that names the key ("secondary outcome", "authors"). Context
     restores specificity on low-entropy values, so the permutation control
     becomes informative where the bare match was pure collision.

  S3 ARGMAX LEAK. The real leak is not the key value, it is the EXTREMAL
     STATUS. Scan retrieved prose for superlative language near the member.

Writes recall4.json.
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import category_traps as ct  # noqa: E402
import gen_v2  # noqa: E402,F401
import gen_v3  # noqa: E402,F401
import gen_v4  # noqa: E402,F401
import net  # noqa: E402

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = "SealTrapGenerator/1.0 (research; contact fahad@crispro.ai)"

EPMC = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        "?query=%22{q}%22&format=json&pageSize=25&resultType=core")
ARXIV = "http://export.arxiv.org/api/query?id_list={q}"

# Words that NAME the key. A number is only counted when it sits within
# WINDOW characters of one of these, which is what makes a 2-char key testable.
KEYWORDS = {
    "health": ("secondary outcome", "secondary endpoint", "secondary efficacy",
               "secondary measure"),
    "science": ("author", "co-author", "coauthor", "authors listed"),
}
WINDOW = 140
SUPERLATIVE = re.compile(
    r"(?i)\b(most|highest|greatest|largest|maximum|fewest|lowest|only trial|"
    r"only study|record number|unprecedented number)\b")


def _get(url, timeout=60, attempts=3):
    last = None
    for i in range(attempts):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(r, timeout=timeout, context=CTX) as fh:
                return fh.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2.0 * (i + 1))
    raise last


# --------------------------------------------------------------------------
# spy. Generator modules do `from category_traps import _pick_extreme`, so
# patching ct alone leaves gen_v2/v3/v4 bound to the original (defect #7).
# --------------------------------------------------------------------------
CAPTURED = {}


def patch():
    orig = ct._pick_extreme

    def spy(rows, keyfn, label, mode="max", valuefn=None):
        try:
            CAPTURED[label] = [(r, keyfn(r),
                                valuefn(r) if valuefn else None) for r in rows]
        except Exception:  # noqa: BLE001
            pass
        return orig(rows, keyfn, label, mode=mode, valuefn=valuefn)

    for m in (ct, gen_v2, gen_v3, gen_v4):
        if hasattr(m, "_pick_extreme"):
            m._pick_extreme = spy
    return orig


def unpatch(orig):
    for m in (ct, gen_v2, gen_v3, gen_v4):
        if hasattr(m, "_pick_extreme"):
            m._pick_extreme = orig


def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return [round(max(0.0, (c - m) / d), 4), round(min(1.0, (c + m) / d), 4)]


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    from math import comb
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1))
    return round(min(1.0, 2.0 * tail / (2 ** n)), 6)


def contextual_hit(text, value, words):
    """True iff `value` appears within WINDOW chars of a key-naming word."""
    if not text or value is None:
        return False
    low = text.lower()
    pat = re.compile(r"(?<![\d.])" + re.escape(str(value)) + r"(?![\d.])")
    spans = [m.start() for m in pat.finditer(low)]
    if not spans:
        return False
    anchors = []
    for w in words:
        anchors += [m.start() for m in re.finditer(re.escape(w), low)]
    if not anchors:
        return False
    return any(abs(s - a) <= WINDOW for s in spans for a in anchors)


# --------------------------------------------------------------------------
# HEALTH. Members are trial registry records. The corpus is the literature
# that cites the trial, retrieved from Europe PMC by accession, which is the
# corpus a solver would actually reach for -- not Wikipedia, which carries no
# article for an individual trial (recall3 found 4 of 25, all of them topic
# substitutions such as "Dexpramipexole" standing in for its phase 3 study).
# --------------------------------------------------------------------------
def do_health():
    ct.LAST_RANK.clear()
    CAPTURED.clear()
    orig = patch()
    try:
        cand = ct.GENERATORS["health and medicine"]()
    finally:
        unpatch(orig)
    label = next(iter(CAPTURED))
    rows = CAPTURED[label]
    members = []
    for r, key, val in rows:
        members.append({"id": r.get("nct") or val, "key": key,
                        "n_secondary": r.get("n_secondary")})
    out = {"category": "health and medicine", "answer": cand.answer,
           "n_members": len(members),
           "key_definition": "len(secondaryOutcomes)",
           "corpus": "Europe PMC, articles citing the trial accession",
           "members": []}

    for m in members:
        rec = {"id": m["id"], "key": m["key"]}
        try:
            js = json.loads(_get(EPMC.format(q=urllib.parse.quote(str(m["id"])))))
            rec["hit_count"] = js.get("hitCount")
            res = (js.get("resultList") or {}).get("result") or []
            texts = []
            for a in res:
                texts.append(" ".join(str(a.get(k) or "") for k in
                                      ("title", "abstractText")))
            rec["n_texts"] = len(texts)
            blob = "\n".join(texts)
            rec["chars"] = len(blob)
            rec["_blob"] = blob
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["hit_count"] = None
            rec["_blob"] = ""
        out["members"].append(rec)
        time.sleep(0.35)

    covered = [r for r in out["members"] if (r.get("hit_count") or 0) > 0
               and r.get("chars")]
    out["n_covered"] = len(covered)
    out["prose_coverage"] = round(len(covered) / len(members), 4) if members else None
    out["prose_coverage_ci95"] = wilson(len(covered), len(members))

    # S2 contextual, own vs permuted, on the covered subset only.
    words = KEYWORDS["health"]
    own = perm = b = c = 0
    n_inf = 0
    for i, r in enumerate(out["members"]):
        if r not in covered:
            continue
        n_inf += 1
        o = contextual_hit(r["_blob"], r["key"], words)
        j = (i + 1) % len(out["members"])
        while j != i and out["members"][j]["key"] == r["key"]:
            j = (j + 1) % len(out["members"])
        p = contextual_hit(r["_blob"], out["members"][j]["key"], words)
        own += int(o)
        perm += int(p)
        b += int(o and not p)
        c += int(p and not o)
        r["own_contextual"] = o
        r["permuted_contextual"] = p
        r["superlative_near"] = bool(SUPERLATIVE.search(r["_blob"] or ""))
    out["n_informative"] = n_inf
    if n_inf:
        out["own_contextual_frac"] = round(own / n_inf, 4)
        out["permuted_contextual_frac"] = round(perm / n_inf, 4)
        out["excess"] = round((own - perm) / n_inf, 4)
        out["mcnemar_b"], out["mcnemar_c"] = b, c
        out["mcnemar_p"] = mcnemar_exact(b, c)
        out["own_ci95"] = wilson(own, n_inf)
    out["n_superlative"] = sum(1 for r in out["members"]
                              if r.get("superlative_near"))
    for r in out["members"]:
        r.pop("_blob", None)
    return out


# --------------------------------------------------------------------------
# SCIENCE. Members are arXiv preprints and the key is the author count, so
# the per-record public view TRIVIALLY carries the key: the author list is
# printed in full. This is the measurement that substring recall could never
# make, because the key is not a string in the document -- it is the LENGTH
# of a list in the document. Derivability is expected to be ~1.0 and that is
# the right outcome for item validity; the leak question is whether the
# EXTREMAL status is also recoverable.
# --------------------------------------------------------------------------
def do_science():
    ct.LAST_RANK.clear()
    CAPTURED.clear()
    orig = patch()
    try:
        cand = ct.GENERATORS["science and technology"]()
    finally:
        unpatch(orig)
    label = next(iter(CAPTURED))
    rows = CAPTURED[label]
    members = [{"id": val or r.get("aid"), "key": key} for r, key, val in rows]
    out = {"category": "science and technology", "answer": cand.answer,
           "n_members": len(members), "key_definition": "number of authors",
           "corpus": "arXiv per-record metadata (public abs view)",
           "members": []}

    agree = 0
    derivable = 0
    for m in members:
        rec = {"id": m["id"], "api_key": m["key"]}
        try:
            xml = _get(ARXIV.format(q=urllib.parse.quote(str(m["id"]))))
            names = re.findall(r"<author>\s*<name>(.*?)</name>", xml, re.S)
            rec["derived_author_count"] = len(names)
            rec["derivable"] = len(names) > 0
            rec["agrees"] = (len(names) == m["key"])
            summ = re.search(r"<summary>(.*?)</summary>", xml, re.S)
            title = re.search(r"<title>(.*?)</title>", xml, re.S)
            blob = " ".join(x.group(1) for x in (title, summ) if x)
            rec["superlative_near"] = bool(SUPERLATIVE.search(blob))
            rec["own_contextual"] = contextual_hit(blob, m["key"],
                                                  KEYWORDS["science"])
            derivable += int(rec["derivable"])
            agree += int(rec["agrees"])
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["derivable"] = False
            rec["agrees"] = False
        out["members"].append(rec)
        time.sleep(3.2)  # arXiv asks for one request every three seconds

    n = len(members)
    out["n_derivable"] = derivable
    out["derivability_frac"] = round(derivable / n, 4) if n else None
    out["derivability_ci95"] = wilson(derivable, n)
    out["n_key_agrees"] = agree
    out["key_agreement_frac"] = round(agree / n, 4) if n else None
    out["key_agreement_ci95"] = wilson(agree, n)
    out["n_informative"] = derivable
    out["n_superlative"] = sum(1 for r in out["members"]
                               if r.get("superlative_near"))
    out["disagreements"] = [r for r in out["members"] if not r.get("agrees")]
    return out


def main():
    res = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "window_chars": WINDOW}
    for name, fn in (("health and medicine", do_health),
                     ("science and technology", do_science)):
        print("=== %s ===" % name, flush=True)
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001
            r = {"error": f"{type(e).__name__}: {e}"}
            print("  FAILED %s" % r["error"], flush=True)
        res[name] = r
        for k in ("n_members", "n_covered", "prose_coverage",
                  "prose_coverage_ci95", "n_informative",
                  "own_contextual_frac", "permuted_contextual_frac", "excess",
                  "mcnemar_p", "derivability_frac", "key_agreement_frac",
                  "n_superlative"):
            if k in r:
                print("  %-28s %s" % (k, r[k]), flush=True)

    with open("recall4.json", "w") as fh:
        json.dump(res, fh, indent=2)
    print("\nwrote recall4.json", flush=True)


if __name__ == "__main__":
    main()
