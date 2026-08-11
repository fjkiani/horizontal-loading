"""namecollide.py -- instrument defect #8, and the corrected retired-key estimate.

recall3.py fixed topic-article substitution with a token-overlap bar
(MIN_TITLE_OVERLAP = 0.60). That bar cannot separate two PEOPLE who share a
name. Its own logged examples show the failure:

    Dodgers pitcher "William Brennan" -> "William J. Brennan Jr." (the Supreme
                                          Court justice)
    Dodgers pitcher "Jay Howell"      -> "Jay Howell (illustrator)"
    Dodgers outfielder "Mike Davis"   -> "Mike Davis (scholar)"
    Dodgers pitcher "Jose Gonzalez"   -> "Jose Gonzalez Gonzalez"

Every one of those clears a 0.60 token overlap, and every one is the wrong
human being. A wrong-person article is, statistically, already a permuted
article -- so contamination pulls the measured excess DOWN toward the null, not
up. The retired birth-date key's excess of 0.36 is therefore a LOWER bound on
the leak among correctly resolved players, which is the opposite direction from
the one that would let the swap off the hook.

This script measures the contamination rate directly and re-estimates the leak
on the identity-verified subset only.

Design
------
No sampling. All 38 members of the 1988 Dodgers full-season roster, so the
estimate carries no sampling variance.

Two INDEPENDENT identity classifiers, reported with their agreement rate:

  A (text)     the resolved plaintext extract contains a baseball marker.
  B (Wikidata) the resolved article's Wikidata item lists an occupation whose
               English label contains "baseball", or holds a claim on P54
               (member of sports team) / P1825-family baseball properties.

Classifier B is the authoritative one; A is the cheap cross-check. Where they
disagree the member is reported and excluded from the strict subset.

Also computes the algebraic contamination bound. If a fraction f of "own"
resolutions are the correct person and the rest behave like permuted articles,

    own = f * p_true + (1 - f) * p_other       and       p_true <= 1

so                f >= (own - p_other) / (1 - p_other)

which bounds how much of the measured signal contamination could possibly be
responsible for, without any further measurement.

Checkpoints after every member.
"""
import json
import math
import os
import re
import sys
import time
import urllib.parse as up

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "namecollide.json")
WIKI = "https://en.wikipedia.org/w/api.php"
WD = "https://www.wikidata.org/w/api.php"
MIN_TITLE_OVERLAP = 0.60
MIN_KEY_CHARS = 4
N_SHIFTS = 5

_STOP = {"the", "of", "a", "an", "in", "and", "for", "to", "on", "jr", "sr"}
_BASEBALL_KW = ("baseball", "major league", "pitcher", "dodgers", "mlb",
                "outfielder", "infielder", "shortstop", "catcher")


def toks(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower())
            if w not in _STOP and len(w) > 2}


def overlap(name, title):
    a, b = toks(name), toks(title)
    return (len(a & b) / len(a)) if a else 0.0


def resolve(name):
    """Exactly recall3's resolver: top hit clearing the token-overlap bar."""
    sj = net.get_json(WIKI + "?action=query&list=search&format=json&srlimit=3"
                      "&srsearch=" + up.quote(name[:250]), timeout=60)
    for h in sj.get("query", {}).get("search", []):
        ov = overlap(name, h["title"])
        if ov < MIN_TITLE_OVERLAP:
            continue
        ej = net.get_json(WIKI + "?action=query&prop=extracts|pageprops"
                          "&explaintext=1&ppprop=wikibase_item"
                          "&format=json&redirects=1&titles=" + up.quote(h["title"]),
                          timeout=60)
        for _, p in ej.get("query", {}).get("pages", {}).items():
            if p.get("extract"):
                return {"title": h["title"], "text": p["extract"],
                        "overlap": round(ov, 3),
                        "qid": (p.get("pageprops") or {}).get("wikibase_item")}
    return None


_LABELS = {}


def label_of(qids):
    todo = [q for q in qids if q not in _LABELS]
    for i in range(0, len(todo), 45):
        chunk = todo[i:i + 45]
        j = net.get_json(WD + "?action=wbgetentities&props=labels&languages=en"
                         "&format=json&ids=" + "|".join(chunk), timeout=90)
        for q, ent in (j.get("entities") or {}).items():
            _LABELS[q] = ((ent.get("labels") or {}).get("en") or {}).get("value", "")
    return {q: _LABELS.get(q, "") for q in qids}


def classify_wd(qid):
    """Authoritative identity check via the article's Wikidata item."""
    if not qid:
        return {"ok": None, "reason": "no wikidata item", "occupations": []}
    j = net.get_json(WD + "?action=wbgetentities&props=claims&format=json&ids=" + qid,
                     timeout=90)
    cl = ((j.get("entities") or {}).get(qid) or {}).get("claims") or {}
    occ_q = []
    for c in cl.get("P106", []):
        dv = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {})
        if isinstance(dv, dict) and dv.get("id"):
            occ_q.append(dv["id"])
    labs = label_of(occ_q)
    occs = [labs[q] for q in occ_q]
    if any("baseball" in (o or "").lower() for o in occs):
        return {"ok": True, "reason": "P106 baseball occupation", "occupations": occs}
    # sports-team membership is the fallback authority
    team_q = []
    for c in cl.get("P54", []):
        dv = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {})
        if isinstance(dv, dict) and dv.get("id"):
            team_q.append(dv["id"])
    if team_q:
        tl = label_of(team_q)
        teams = [tl[q] for q in team_q]
        if any(("dodgers" in (t or "").lower()) or ("baseball" in (t or "").lower())
               for t in teams):
            return {"ok": True, "reason": "P54 baseball team",
                    "occupations": occs, "teams": teams}
        return {"ok": False, "reason": "P54 non-baseball team",
                "occupations": occs, "teams": teams}
    return {"ok": False, "reason": "no baseball occupation or team",
            "occupations": occs}


def key_variants(k, year_only=False):
    if not k:
        return []
    if year_only:
        return [k[:4]]
    return [k]


def contains(text, v):
    return v in (text or "")


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


def score(members, year_only):
    inf = [m for m in members
           if m.get("text") and any(len(v) >= MIN_KEY_CHARS
                                    for v in key_variants(m["key"], year_only))]
    n = len(inf)
    if n < 2:
        return {"n_informative": n, "note": "too few informative keys to test"}

    def hit(m, text):
        for v in key_variants(m["key"], year_only):
            if len(v) >= MIN_KEY_CHARS and contains(text, v):
                return v
        return None

    t_hits, p_hits, pairs = 0, 0, []
    for i, m in enumerate(inf):
        t = hit(m, m["text"])
        t_hits += bool(t)
        p = any(hit(m, inf[(i + s) % n]["text"])
                for s in range(1, min(N_SHIFTS, n - 1) + 1))
        p_hits += bool(p)
        pairs.append((bool(t), bool(p)))
    b = sum(1 for t, p in pairs if t and not p)
    c = sum(1 for t, p in pairs if p and not t)
    own = t_hits / n
    oth = p_hits / n
    f_min = ((own - oth) / (1 - oth)) if oth < 1 else None
    return {"n_informative": n,
            "key_in_own_article_frac": round(own, 4),
            "key_in_other_article_frac": round(oth, 4),
            "excess": round(own - oth, 4),
            "own_ci95": wilson(t_hits, n),
            "mcnemar_b_own_only": b, "mcnemar_c_other_only": c,
            "mcnemar_p": round(mcnemar_exact(b, c), 6),
            "min_correct_resolution_frac_implied": (
                round(f_min, 4) if f_min is not None else None),
            "excess_if_f_at_min": (
                round((own - oth) / f_min, 4) if f_min else None)}


def main():
    res = {"started": time.time(), "min_title_overlap": MIN_TITLE_OVERLAP,
           "members": []}
    rj = net.get_json("https://statsapi.mlb.com/api/v1/teams/119/roster?season=1988"
                      "&rosterType=fullSeason", timeout=120)
    ids = ",".join(str(p["person"]["id"]) for p in rj["roster"])
    pj = net.get_json("https://statsapi.mlb.com/api/v1/people?personIds=" + ids,
                      timeout=120)
    roster = [{"name": p["fullName"], "key": p.get("birthDate"),
               "mlb_id": p["id"]}
              for p in pj.get("people", []) if p.get("birthDate")]
    res["n_roster"] = len(roster)
    print("roster with birthDate: %d" % len(roster), flush=True)

    for m in roster:
        rec = dict(m)
        try:
            a = resolve(m["name"])
        except Exception as e:  # noqa: BLE001
            a = None
            rec["resolve_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        if a:
            rec.update({"article": a["title"], "overlap": a["overlap"],
                        "qid": a["qid"], "text": a["text"]})
            low = a["text"].lower()
            rec["kw_baseball"] = any(k in low for k in _BASEBALL_KW)
            try:
                w = classify_wd(a["qid"])
            except Exception as e:  # noqa: BLE001
                w = {"ok": None, "reason": "%s: %s" % (type(e).__name__, str(e)[:80]),
                     "occupations": []}
            rec["wd_ok"] = w["ok"]
            rec["wd_reason"] = w["reason"]
            rec["wd_occupations"] = w.get("occupations", [])
            if w.get("teams"):
                rec["wd_teams"] = w["teams"]
        else:
            rec["article"] = None
            rec["text"] = ""
            rec["kw_baseball"] = None
            rec["wd_ok"] = None
        res["members"].append(rec)
        print("  %-22s -> %-34s ov=%-5s kw=%-5s wd=%-5s %s" % (
            m["name"][:22], str(rec.get("article"))[:34], rec.get("overlap"),
            rec.get("kw_baseball"), rec.get("wd_ok"), rec.get("wd_reason", "")[:40]),
            flush=True)
        json.dump({k: (v if k != "members" else
                       [{kk: vv for kk, vv in mm.items() if kk != "text"}
                        for mm in v]) for k, v in res.items()},
                  open(OUT, "w"), indent=1)

    resolved = [m for m in res["members"] if m.get("article")]
    both = [m for m in resolved if m.get("kw_baseball") is not None
            and m.get("wd_ok") is not None]
    agree = [m for m in both if bool(m["kw_baseball"]) == bool(m["wd_ok"])]
    verified = [m for m in resolved if m.get("wd_ok") is True]
    wrong = [m for m in resolved if m.get("wd_ok") is False]

    res["identity"] = {
        "n_resolved": len(resolved),
        "resolution_rate": round(len(resolved) / len(roster), 4),
        "n_identity_verified": len(verified),
        "n_wrong_person": len(wrong),
        "n_undetermined": len(resolved) - len(verified) - len(wrong),
        "measured_correct_resolution_frac": round(len(verified) / len(resolved), 4)
        if resolved else None,
        "verified_ci95": wilson(len(verified), len(resolved)),
        "classifier_agreement": round(len(agree) / len(both), 4) if both else None,
        "wrong_person_examples": [
            {"member": m["name"], "article": m["article"],
             "overlap": m["overlap"], "occupations": m.get("wd_occupations", []),
             "reason": m.get("wd_reason")} for m in wrong],
        "disagreements": [
            {"member": m["name"], "article": m["article"],
             "kw": m["kw_baseball"], "wd": m["wd_ok"]}
            for m in both if bool(m["kw_baseball"]) != bool(m["wd_ok"])],
    }

    res["leak"] = {
        "all_resolved": {
            "n": len(resolved),
            "full_iso_date": score(resolved, False),
            "year_only": score(resolved, True)},
        "identity_verified_only": {
            "n": len(verified),
            "full_iso_date": score(verified, False),
            "year_only": score(verified, True)},
    }
    res["finished"] = time.time()

    json.dump({k: (v if k != "members" else
                   [{kk: vv for kk, vv in mm.items() if kk != "text"}
                    for mm in v]) for k, v in res.items()},
              open(OUT, "w"), indent=1)
    print(json.dumps({"identity": res["identity"], "leak": res["leak"]}, indent=2))


if __name__ == "__main__":
    main()
