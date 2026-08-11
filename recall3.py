"""recall3.py -- a valid recall measurement, after recall2 showed the old one is not.

recall2 ran the original key_in_article_frac statistic with a matched
permutation null for the first time and the null came back HIGHER than the
signal:

    health   true 0.4444  perm 0.8889  excess -0.4444
    science  true 0.6667  perm 1.0000  excess -0.3333

A negative excess means the statistic was measuring string collision, not
leakage. Three separate defects produced it, all fixed here.

D-a  LOW-ENTROPY KEYS. Health ranks on a count of secondary outcome measures;
     the sampled key values are single characters ("3", "8", "1"). Searching a
     multi-kilobyte article for "3" hits almost surely. Sports previously
     matched birth dates by YEAR ALONE, and biography articles are dense with
     four-digit years. Fixed: the statistic is computed only over members whose
     most-specific key variant is >= MIN_KEY_CHARS, and the excluded count is
     reported rather than silently folded in.

D-b  TOPIC-ARTICLE SUBSTITUTION. The resolver took Wikipedia's top search hit
     unconditionally, so the trial "Phase 3 Study of Dexpramipexole in ALS"
     resolved to the article "Dexpramipexole" and the paper "A Novel Method to
     Constrain Tidal Quality Factor..." resolved to "2021 in science". Neither
     is the member's own article, so a hit says something about the topic, not
     the member. Fixed: a resolved article must clear a token-overlap bar.

D-c  SPY BLINDNESS (instrument defect #7). The generator modules do
     `from category_traps import _pick_extreme`, so patching ct._pick_extreme
     left gen_v2/gen_v3/gen_v4 bound to the original and geography reported
     "generator did not call _pick_extreme". Fixed: patch every module holding
     a reference.

Also re-tests the RETIRED sports birth-date key both ways -- full ISO date and
year-only -- to establish whether the 1.00 that condemned it was real leakage
or the year-collision artefact.
"""
import json, math, os, re, random, sys, time
import urllib.parse as up

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net
import category_traps as ct
import gen_v2, gen_v3, gen_v4  # noqa: F401

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recall3.json")
WIKI = "https://en.wikipedia.org/w/api.php"
N_SAMPLE = 25
N_SHIFTS = 5
MIN_KEY_CHARS = 4
MIN_TITLE_OVERLAP = 0.60
random.seed(20260811)

_MODULES = [ct, gen_v2, gen_v3, gen_v4]
_REAL = ct._pick_extreme
CAPTURE = {}


def _spy(rows, keyfn, label, mode="max", valuefn=None):
    CAPTURE.update(rows=list(rows), keyfn=keyfn, label=label, mode=mode)
    return _REAL(rows, keyfn, label, mode=mode, valuefn=valuefn)


def patch(fn):
    for m in _MODULES:
        if hasattr(m, "_pick_extreme"):
            setattr(m, "_pick_extreme", fn)


_STOP = {"the", "of", "a", "an", "in", "and", "for", "to", "on", "study", "trial",
         "phase", "randomized", "randomised", "placebo", "controlled", "patients",
         "with", "safety", "efficacy", "evaluate", "assess", "airport", "international"}


def toks(s):
    return {w for w in re.findall(r"[a-z0-9]+", str(s).lower())
            if w not in _STOP and len(w) > 2}


def overlap(name, title):
    a, b = toks(name), toks(title)
    return (len(a & b) / len(b)) if b else 0.0


NAME_FIELDS = ("briefTitle", "primaryTitle", "fullName", "display_name", "title",
               "name", "label", "entity", "airport_name", "municipality", "player")


def member_name(row):
    if isinstance(row, str):
        return row
    if isinstance(row, (list, tuple)) and row:
        return member_name(row[0])
    if not isinstance(row, dict):
        return str(row)
    for f in NAME_FIELDS:
        v = row.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for sub in row.values():
        if isinstance(sub, dict):
            n = member_name(sub)
            if n and not n.startswith("{"):
                return n
    return str(row)[:90]


def key_variants(k, year_only=False):
    s = str(k).strip()
    m = re.match(r"^(\d{4})-\d{2}-\d{2}$", s)
    if m:
        return [m.group(1)] if year_only else [s]
    out = [s]
    if re.match(r"^-?\d+\.\d+$", s):
        out += ["%.1f" % float(s), s.split(".")[0]]
    return list(dict.fromkeys(v for v in out if v))


def contains(text, v):
    if not text or not v:
        return False
    if re.match(r"^[\w.\-]+$", v):
        return re.search(r"(?<![\w.\-])" + re.escape(v) + r"(?![\w.\-])", text) is not None
    return v in text


_ART = {}


def article(name):
    """The member's OWN article, or None. Rejects topic-article substitution."""
    if name in _ART:
        return _ART[name]
    got = None
    try:
        sj = net.get_json(WIKI + "?action=query&list=search&format=json&srlimit=3"
                          "&srsearch=" + up.quote(name[:250]), timeout=60)
        for h in sj.get("query", {}).get("search", []):
            ov = overlap(name, h["title"])
            if ov < MIN_TITLE_OVERLAP:
                continue
            ej = net.get_json(WIKI + "?action=query&prop=extracts&explaintext=1"
                              "&format=json&redirects=1&titles=" + up.quote(h["title"]),
                              timeout=60)
            for _, p in ej.get("query", {}).get("pages", {}).items():
                if p.get("extract"):
                    got = (h["title"], p["extract"], round(ov, 3))
                    break
            if got:
                break
    except Exception:  # noqa: BLE001
        got = None
    _ART[name] = got
    return got


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2.0 * sum(math.comb(n, i) for i in range(k + 1)) / (2.0 ** n))


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p, d = k / n, 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, c - h), 4), round(min(1.0, c + h), 4))


def score(members, year_only=False):
    inf = [m for m in members
           if m["article"] and any(len(v) >= MIN_KEY_CHARS
                                   for v in key_variants(m["key"], year_only))]
    n = len(inf)
    if n < 2:
        return {"n_informative": n, "note": "too few informative keys to test"}

    def hit(m, text):
        for v in key_variants(m["key"], year_only):
            if len(v) >= MIN_KEY_CHARS and contains(text, v):
                return v
        return None

    t_hits, p_hits, pairs, found, absent = 0, 0, [], [], []
    for i, m in enumerate(inf):
        t = hit(m, m["text"])
        t_hits += bool(t)
        (found if t else absent).append({"member": m["name"][:70],
                                         "article": m["article"], "key": m["key"],
                                         "matched_as": t})
        p = any(hit(m, inf[(i + s) % n]["text"])
                for s in range(1, min(N_SHIFTS, n - 1) + 1))
        p_hits += bool(p)
        pairs.append((bool(t), bool(p)))
    b = sum(1 for t, p in pairs if t and not p)
    c = sum(1 for t, p in pairs if p and not t)
    return {"n_informative": n,
            "key_in_own_article_frac": round(t_hits / n, 4),
            "key_in_other_article_frac": round(p_hits / n, 4),
            "excess": round((t_hits - p_hits) / n, 4),
            "own_ci95": wilson(t_hits, n),
            "mcnemar_b_own_only": b, "mcnemar_c_other_only": c,
            "mcnemar_p": round(mcnemar_exact(b, c), 6),
            "examples_found": found[:3], "examples_absent": absent[:3]}


def measure(cat):
    rec = {"category": cat}
    CAPTURE.clear()
    patch(_spy)
    try:
        with ct.generation():
            cand = ct.GENERATORS[cat]()
    except Exception as e:  # noqa: BLE001
        return dict(rec, status="%s: %s" % (type(e).__name__, str(e)[:180]))
    finally:
        patch(_REAL)
    if "rows" not in CAPTURE:
        return dict(rec, status="generator did not call _pick_extreme")

    rows, keyfn = CAPTURE["rows"], CAPTURE["keyfn"]
    rec.update(status="ok", answer=cand.answer, entity=cand.entity,
               n_population=len(rows), mode=CAPTURE["mode"])
    members = []
    for r in rows:
        try:
            members.append({"name": member_name(r), "key": str(keyfn(r))})
        except Exception:  # noqa: BLE001
            pass
    idx = sorted(random.sample(range(len(members)), min(N_SAMPLE, len(members))))
    samp = [members[i] for i in idx]
    for m in samp:
        a = article(m["name"])
        m["article"], m["text"], m["overlap"] = (a[0], a[1], a[2]) if a else (None, "", None)

    rec["sample_names"] = [m["name"][:70] for m in samp[:4]]
    rec["n_sampled"] = len(samp)
    rec["n_with_own_article"] = sum(1 for m in samp if m["article"])
    rec["own_article_frac"] = round(rec["n_with_own_article"] / len(samp), 4) if samp else None
    rec["own_article_ci95"] = wilson(rec["n_with_own_article"], len(samp))
    rec["key_chars_min"] = min((max(len(v) for v in key_variants(m["key"]))
                                for m in samp), default=None)
    rec["key_chars_max"] = max((max(len(v) for v in key_variants(m["key"]))
                                for m in samp), default=None)
    rec["stage2_key"] = score(samp)

    wa = article(cand.entity)
    rec["winner_article"] = wa[0] if wa else None
    rec["winner_has_own_article"] = bool(wa)
    rec["answer_in_winner_article"] = contains(wa[1], str(cand.answer)) if wa else None
    return rec


def retired_sports_key():
    rj = net.get_json("https://statsapi.mlb.com/api/v1/teams/119/roster?season=1988"
                      "&rosterType=fullSeason", timeout=120)
    ids = ",".join(str(p["person"]["id"]) for p in rj["roster"])
    pj = net.get_json("https://statsapi.mlb.com/api/v1/people?personIds=" + ids, timeout=120)
    members = [{"name": p["fullName"], "key": p["birthDate"]}
               for p in pj.get("people", []) if p.get("birthDate")]
    idx = sorted(random.sample(range(len(members)), min(N_SAMPLE, len(members))))
    samp = [members[i] for i in idx]
    for m in samp:
        a = article(m["name"])
        m["article"], m["text"] = (a[0], a[1]) if a else (None, "")
    return {"n_population": len(members), "n_sampled": len(samp),
            "n_with_own_article": sum(1 for m in samp if m["article"]),
            "full_iso_date": score(samp, year_only=False),
            "year_only_as_originally_measured": score(samp, year_only=True)}


if __name__ == "__main__":
    res = {"started": time.time(), "min_key_chars": MIN_KEY_CHARS,
           "min_title_overlap": MIN_TITLE_OVERLAP, "categories": {}}
    for cat in ("health and medicine", "science and technology", "sports",
                "travel", "geography"):
        t0 = time.time()
        print("--- %s ..." % cat, flush=True)
        try:
            r = measure(cat)
        except Exception as e:  # noqa: BLE001
            r = {"category": cat, "status": "%s: %s" % (type(e).__name__, str(e)[:200])}
        r["elapsed_s"] = round(time.time() - t0, 1)
        res["categories"][cat] = r
        s = r.get("stage2_key", {})
        print("    pop=%s own_article=%s/%s keychars=%s-%s | informative=%s own=%s "
              "other=%s excess=%s p=%s" % (
                  r.get("n_population"), r.get("n_with_own_article"), r.get("n_sampled"),
                  r.get("key_chars_min"), r.get("key_chars_max"), s.get("n_informative"),
                  s.get("key_in_own_article_frac"), s.get("key_in_other_article_frac"),
                  s.get("excess"), s.get("mcnemar_p")), flush=True)
        json.dump(res, open(OUT, "w"), indent=1)

    print("--- retired sports birth-date key ...", flush=True)
    try:
        res["retired_sports_birthdate_key"] = retired_sports_key()
        for lbl in ("full_iso_date", "year_only_as_originally_measured"):
            s = res["retired_sports_birthdate_key"][lbl]
            print("    %-34s own=%s other=%s excess=%s p=%s (n=%s)" % (
                lbl, s.get("key_in_own_article_frac"),
                s.get("key_in_other_article_frac"), s.get("excess"),
                s.get("mcnemar_p"), s.get("n_informative")), flush=True)
    except Exception as e:  # noqa: BLE001
        res["retired_sports_birthdate_key"] = {"status": "%s: %s" % (type(e).__name__, e)}

    res["finished"] = time.time()
    json.dump(res, open(OUT, "w"), indent=1)
    print("\nwrote", OUT)
