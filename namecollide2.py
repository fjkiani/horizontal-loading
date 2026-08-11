"""namecollide2.py -- resolve the contradiction namecollide.py exposed.

namecollide.py measured, on all 37 resolvable members of the 1988 Dodgers
full-season roster:

    year-only key in own article      0.9189   (34 of 37)
    year-only key in permuted article 0.5405
    identity-verified fraction        0.7297   (27 of 37)

Under the two-component model

    own = f * p_true + (1 - f) * p_other,     p_true <= 1

the observed own rate forces

    f >= (0.9189 - 0.5405) / (1 - 0.5405) = 0.8235

but only 0.7297 verified. 0.7297 < 0.8235, so the model is FALSE. The
wrong-person articles are not behaving like permuted articles. Something in the
"wrong" bucket still carries the member's own identity.

The hypothesis this script tests: Wikipedia DISAMBIGUATION pages. "Dave Anderson"
with no Wikidata occupation and a text extract that mentions baseball is the
signature of a page reading "Dave Anderson may refer to: Dave Anderson
(baseball, born 1960), Dave Anderson (journalist), ...". Such a page is neither
the member's own article nor a stranger's -- it LISTS the member, birth year
included. Classifier A (keyword) calls it baseball; classifier B (Wikidata
occupation) calls it not-a-baseball-player. Both are right about what they
measure and both are wrong about identity, which is exactly why they disagreed
on exactly these seven members and nowhere else.

Three-way partition, and an exact hit accounting per group.

  VERIFIED   Wikidata item has a baseball occupation or team.
  DISAMBIG   Wikidata item is instance-of a Wikimedia disambiguation page
             (P31 = Q4167410), or the extract opens "X may refer to".
  COLLISION  a real article about a different human being.

Prediction under the hypothesis: VERIFIED + DISAMBIG == the observed own-hit
count, exactly, with no free parameter.

Runs off the warm net cache; the only new upstream calls are the P31 lookups.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net  # noqa: E402
import namecollide as nc  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "namecollide2.json")
WD = "https://www.wikidata.org/w/api.php"
DISAMBIG_QID = "Q4167410"


def is_disambig(qid, text):
    low = (text or "").lower()
    textual = ("may refer to" in low[:400]) or ("may also refer to" in low[:400])
    p31 = None
    if qid:
        j = net.get_json(WD + "?action=wbgetentities&props=claims&format=json&ids="
                         + qid, timeout=90)
        cl = ((j.get("entities") or {}).get(qid) or {}).get("claims") or {}
        vals = []
        for c in cl.get("P31", []):
            dv = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {})
            if isinstance(dv, dict) and dv.get("id"):
                vals.append(dv["id"])
        p31 = DISAMBIG_QID in vals
    return bool(textual or p31), {"p31_disambig": p31, "text_may_refer_to": textual}


def main():
    res = {"started": time.time(), "members": []}
    rj = net.get_json("https://statsapi.mlb.com/api/v1/teams/119/roster?season=1988"
                      "&rosterType=fullSeason", timeout=120)
    ids = ",".join(str(p["person"]["id"]) for p in rj["roster"])
    pj = net.get_json("https://statsapi.mlb.com/api/v1/people?personIds=" + ids,
                      timeout=120)
    roster = [{"name": p["fullName"], "key": p.get("birthDate")}
              for p in pj.get("people", []) if p.get("birthDate")]

    members = []
    for m in roster:
        a = nc.resolve(m["name"])
        if not a:
            res["members"].append({"name": m["name"], "group": "UNRESOLVED"})
            continue
        w = nc.classify_wd(a["qid"])
        if w["ok"] is True:
            group, why = "VERIFIED", w["reason"]
        else:
            dis, ev = is_disambig(a["qid"], a["text"])
            group = "DISAMBIG" if dis else "COLLISION"
            why = json.dumps(ev) if dis else ", ".join(w.get("occupations") or
                                                       [w["reason"]])
        rec = {"name": m["name"], "key": m["key"], "article": a["title"],
               "qid": a["qid"], "group": group, "why": why, "text": a["text"]}
        members.append(rec)
        res["members"].append({k: v for k, v in rec.items() if k != "text"})
        print("  %-9s %-20s -> %-32s %s" % (group, m["name"][:20],
                                            a["title"][:32], why[:52]), flush=True)
        json.dump(res, open(OUT, "w"), indent=1)

    n = len(members)
    for i, m in enumerate(members):
        y = m["key"][:4]
        m["own_hit"] = y in m["text"]
        m["perm_hit"] = any(y in members[(i + s) % n]["text"]
                            for s in range(1, min(nc.N_SHIFTS, n - 1) + 1))
        m["iso_hit"] = m["key"] in m["text"]

    groups = {}
    for g in ("VERIFIED", "DISAMBIG", "COLLISION"):
        sel = [m for m in members if m["group"] == g]
        if not sel:
            continue
        own = sum(m["own_hit"] for m in sel)
        perm = sum(m["perm_hit"] for m in sel)
        groups[g] = {
            "n": len(sel),
            "year_in_page": own,
            "year_in_page_frac": round(own / len(sel), 4),
            "year_in_page_ci95": nc.wilson(own, len(sel)),
            "year_in_permuted_page_frac": round(perm / len(sel), 4),
            "iso_date_in_page": sum(m["iso_hit"] for m in sel),
            "members": [m["name"] for m in sel],
        }
    res["groups"] = groups

    own_tot = sum(m["own_hit"] for m in members)
    perm_tot = sum(m["perm_hit"] for m in members)
    own_f, perm_f = own_tot / n, perm_tot / n
    f_min = (own_f - perm_f) / (1 - perm_f)
    carriers = sum(groups[g]["n"] for g in ("VERIFIED", "DISAMBIG") if g in groups)
    ver = groups.get("VERIFIED", {}).get("n", 0)
    res["reconciliation"] = {
        "n_pages": n,
        "own_year_hits": own_tot,
        "own_frac": round(own_f, 4),
        "perm_frac": round(perm_f, 4),
        "excess": round(own_f - perm_f, 4),
        "f_min_implied_by_two_component_model": round(f_min, 4),
        "identity_verified_frac": round(ver / n, 4),
        "two_component_model_contradicted": (ver / n) < f_min,
        "identity_carrying_frac_verified_plus_disambig": round(carriers / n, 4),
        "carriers_over_n": "%d/%d" % (carriers, n),
        "own_hits_over_n": "%d/%d" % (own_tot, n),
        "prediction_exact_match": carriers == own_tot,
        "note": ("Prediction with no free parameter: if DISAMBIG pages carry the "
                 "member's identity then VERIFIED+DISAMBIG must equal the "
                 "observed own-hit count."),
    }
    res["finished"] = time.time()
    json.dump(res, open(OUT, "w"), indent=1)
    print()
    print(json.dumps({"groups": {g: {k: v for k, v in d.items() if k != "members"}
                                 for g, d in groups.items()},
                      "reconciliation": res["reconciliation"]}, indent=2))


if __name__ == "__main__":
    main()
