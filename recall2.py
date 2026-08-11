"""recall2.py -- key_in_article_frac WITH a permutation null, for the two
categories never measured (health, science) plus retro-controls.

Why a null is required. The original probe reported the raw fraction of
collection members whose ranking key string appears in their own Wikipedia
article. For a high-entropy key ("0000-0001-7854-927X") a hit is decisive. For
a LOW-entropy key it is close to meaningless: health ranks on a count of
secondary outcome measures whose winning value is the two-character string
"18", and essentially every long article contains "18" somewhere. Sports
matched birth dates by YEAR ALONE ("1945-04-02" -> "1945"), and biography
articles are dense with four-digit years.

So the raw fraction confounds real leakage with base-rate string collision. The
fix is a matched permutation control: test member i's key against member j's
article for j != i. Under the null that the key is not carried by the member's
own article, the two rates are equal. The informative statistic is the paired
excess, tested with an exact McNemar (binomial on discordant pairs).

Also settles travel's stage-2 leak by asking which identifiers for the winning
airport are printed in its own article.
"""
import json, math, os, random, re, sys, time
import urllib.parse as up

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net
import category_traps as ct
import gen_v2, gen_v3, gen_v4  # noqa: F401  (generator overrides, order matters)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recall2.json")
WIKI = "https://en.wikipedia.org/w/api.php"
N_SAMPLE = 25
N_SHIFTS = 5
random.seed(20260811)

CAPTURE = {}
_REAL_PICK = ct._pick_extreme


def _spy(rows, keyfn, label, mode="max", valuefn=None):
    CAPTURE["rows"] = list(rows)
    CAPTURE["keyfn"] = keyfn
    CAPTURE["label"] = label
    CAPTURE["mode"] = mode
    CAPTURE["valuefn"] = valuefn
    return _REAL_PICK(rows, keyfn, label, mode=mode, valuefn=valuefn)


NAME_FIELDS = ("briefTitle", "primaryTitle", "fullName", "display_name",
               "title", "name", "label", "entity", "airport_name", "municipality")


def member_name(row):
    if isinstance(row, str):
        return row
    if not isinstance(row, dict):
        return str(row)
    for f in NAME_FIELDS:
        v = row.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for f in ("protocolSection", "person", "properties"):
        sub = row.get(f)
        if isinstance(sub, dict):
            n = member_name(sub)
            if n and not n.startswith("{"):
                return n
    return str(row)[:80]


def key_variants(k):
    """Search strings for one key value, most specific first.

    Returns (variant, n_chars). n_chars is the entropy proxy the null is
    interpreted against: a 2-char numeric variant is near-worthless evidence.
    """
    s = str(k).strip()
    out = [s]
    m = re.match(r"^(\d{4})-\d{2}-\d{2}$", s)
    if m:
        out.append(m.group(1))          # the loosening the original probe used
    if re.match(r"^-?\d+\.\d+$", s):
        out.append(s.split(".")[0])
        out.append("%.1f" % float(s))
    return [v for v in dict.fromkeys(out) if v]


def contains(text, variant):
    if not text or not variant:
        return False
    if re.match(r"^[\w.\-]+$", variant):
        return re.search(r"(?<![\w.])" + re.escape(variant) + r"(?![\w.])", text) is not None
    return variant in text


_ART = {}


def article_text(title):
    """Best-matching en.wikipedia article plain text, or None."""
    if title in _ART:
        return _ART[title]
    txt = None
    try:
        sj = net.get_json(WIKI + "?action=query&list=search&format=json&srlimit=1&srsearch="
                          + up.quote(title), timeout=60)
        hits = sj.get("query", {}).get("search", [])
        if hits:
            page = hits[0]["title"]
            ej = net.get_json(WIKI + "?action=query&prop=extracts&explaintext=1&format=json"
                              "&redirects=1&titles=" + up.quote(page), timeout=60)
            for _, p in ej.get("query", {}).get("pages", {}).items():
                ex = p.get("extract")
                if ex:
                    txt = (page, ex)
    except Exception as e:  # noqa: BLE001
        txt = None
        _ART[title] = None
        return None
    _ART[title] = txt
    return txt


def mcnemar_exact(b, c):
    """Two-sided exact McNemar on discordant counts b (true-only) and c (perm-only)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def measure(cat, kwargs=None):
    rec = {"category": cat, "status": "ok"}
    ct._pick_extreme = _spy
    CAPTURE.clear()
    try:
        with ct.generation():
            cand = ct.GENERATORS[cat](**(kwargs or {}))
    except Exception as e:  # noqa: BLE001
        rec["status"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        ct._pick_extreme = _REAL_PICK
        return rec
    finally:
        ct._pick_extreme = _REAL_PICK
    if "rows" not in CAPTURE:
        rec["status"] = "generator did not call _pick_extreme"
        return rec

    rows, keyfn = CAPTURE["rows"], CAPTURE["keyfn"]
    rec.update(answer=cand.answer, entity=cand.entity, n_population=len(rows),
               mode=CAPTURE["mode"], key_label=CAPTURE["label"])

    members = []
    for r in rows:
        try:
            k = keyfn(r)
        except Exception:  # noqa: BLE001
            continue
        members.append({"name": member_name(r), "key": str(k)})
    idx = list(range(len(members)))
    random.shuffle(idx)
    idx = sorted(idx[:N_SAMPLE])
    samp = [members[i] for i in idx]

    for m in samp:
        a = article_text(m["name"])
        m["article"] = a[0] if a else None
        m["text"] = a[1] if a else ""
    withart = [m for m in samp if m["article"]]

    def hit(keystr, text):
        for v in key_variants(keystr):
            if contains(text, v):
                return v
        return None

    true_hits, perm_hits, pairs, ex_found, ex_absent = 0, 0, [], [], []
    n = len(withart)
    for i, m in enumerate(withart):
        t = hit(m["key"], m["text"])
        true_hits += 1 if t else 0
        (ex_found if t else ex_absent).append(
            {"member": m["name"], "article": m["article"], "key": m["key"],
             "matched_as": t})
        p_any = False
        for s in range(1, min(N_SHIFTS, n - 1) + 1):
            j = (i + s) % n
            if hit(m["key"], withart[j]["text"]):
                p_any = True
                break
        perm_hits += 1 if p_any else 0
        pairs.append((bool(t), p_any))

    b = sum(1 for t, p in pairs if t and not p)
    c = sum(1 for t, p in pairs if p and not t)
    rec.update(
        n_sampled=len(samp), n_with_article=n,
        key_in_article_frac=round(true_hits / n, 4) if n else None,
        key_in_OTHER_article_frac=round(perm_hits / n, 4) if n else None,
        excess=round((true_hits - perm_hits) / n, 4) if n else None,
        mcnemar_b_true_only=b, mcnemar_c_perm_only=c,
        mcnemar_p=round(mcnemar_exact(b, c), 6),
        key_variant_chars=(min(len(v) for m in withart for v in key_variants(m["key"]))
                           if withart else None),
        examples_key_found=[{k: v for k, v in e.items()} for e in ex_found[:4]],
        examples_key_absent=[{k: v for k, v in e.items()} for e in ex_absent[:4]],
    )

    wa = article_text(cand.entity)
    rec["winner_article"] = wa[0] if wa else None
    rec["winner_has_article"] = bool(wa)
    if wa:
        wtext = wa[1]
        try:
            wk = str(keyfn(_REAL_PICK.__self__)) if False else None
        except Exception:  # noqa: BLE001
            wk = None
        rec["winner_key_in_article"] = bool(hit(str(cand.facts.get("key", "")), wtext)) \
            if cand.facts.get("key") else None
        rec["answer_in_winner_article"] = contains(wtext, str(cand.answer))
        rec["winner_article_chars"] = len(wtext)
    return rec


def travel_stage2(rec_travel):
    """Which identifiers for the winning airport are printed in its own article?"""
    wa = article_text("Ivalo Airport")
    if not wa:
        return {"status": "no article"}
    txt = wa[1]
    cands = {}
    try:
        q = net.get_json("https://www.wikidata.org/w/api.php?action=wbsearchentities"
                         "&search=Ivalo%20Airport&language=en&format=json&limit=1", timeout=60)
        qid = q["search"][0]["id"]
        ent = net.get_json("https://www.wikidata.org/wiki/Special:EntityData/%s.json" % qid,
                           timeout=90)
        cl = ent["entities"][qid]["claims"]
        for pid, lbl in (("P238", "IATA"), ("P239", "ICAO"), ("P1584", "GeoNames"),
                         ("P240", "FAA"), ("P8905", "OurAirports")):
            if pid in cl:
                v = cl[pid][0]["mainsnak"].get("datavalue", {}).get("value")
                if isinstance(v, str):
                    cands[lbl] = v
        cands["WikidataQID"] = qid
    except Exception as e:  # noqa: BLE001
        cands["_error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
    return {"article": wa[0], "article_chars": len(txt),
            "identifiers": {k: {"value": v, "printed_in_article": contains(txt, v)}
                            for k, v in cands.items() if not k.startswith("_")},
            "error": cands.get("_error")}


if __name__ == "__main__":
    res = {"started": time.time(), "categories": {}}
    plan = [("health and medicine", None), ("science and technology", None),
            ("sports", None), ("travel", None), ("geography", None)]
    for cat, kw in plan:
        t0 = time.time()
        print("--- %s ..." % cat, flush=True)
        try:
            r = measure(cat, kw)
        except Exception as e:  # noqa: BLE001
            r = {"category": cat, "status": "%s: %s" % (type(e).__name__, str(e)[:200])}
        r["elapsed_s"] = round(time.time() - t0, 1)
        res["categories"][cat] = r
        print("    n=%s art=%s true=%s perm=%s excess=%s p=%s chars=%s" % (
            r.get("n_population"), r.get("n_with_article"),
            r.get("key_in_article_frac"), r.get("key_in_OTHER_article_frac"),
            r.get("excess"), r.get("mcnemar_p"), r.get("key_variant_chars")), flush=True)
        json.dump(res, open(OUT, "w"), indent=1)

    print("--- travel stage 2 ...", flush=True)
    res["travel_stage2"] = travel_stage2(res["categories"].get("travel"))
    print(json.dumps(res["travel_stage2"].get("identifiers", {}), indent=1), flush=True)

    res["finished"] = time.time()
    json.dump(res, open(OUT, "w"), indent=1)
    print("\n%-24s %6s %6s %8s %8s %8s %8s" % (
        "category", "n", "art", "true", "perm", "excess", "mcnemar"))
    print("-" * 70)
    for c, r in res["categories"].items():
        print("%-24s %6s %6s %8s %8s %8s %8s" % (
            c, r.get("n_population"), r.get("n_with_article"),
            r.get("key_in_article_frac"), r.get("key_in_OTHER_article_frac"),
            r.get("excess"), r.get("mcnemar_p")))
    print("wrote", OUT)
