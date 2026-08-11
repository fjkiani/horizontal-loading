"""recall4b -- interrogate my own superlative counter before reporting it.

recall4 found superlative language somewhere in 42 of 49 covered health
members. At face value that reads like a large argmax leak. It is almost
certainly nothing, because the statistic fires when any of
most/highest/largest/lowest appears ANYWHERE in up to 25 concatenated
biomedical abstracts -- "the most common adverse event was headache" trips it
-- and it never looks at the key. Being member-independent, it cannot
separate a corpus that names this trial as the extremum from one that merely
uses the word "most". Reporting 0.857 as a leak rate would be a tenth
instrument defect, so it gets measured, not asserted.

Three nested statistics, tightening the specificity at each step, with a
permutation control on the only one that admits one:

  A  ANY superlative anywhere. Member-independent. Expected near base rate.
  B  Superlative within WINDOW chars of key-NAMING language. Still
     member-independent, but it upper-bounds any possible argmax leak.
  C  Superlative AND the member's own key VALUE both within WINDOW of the
     same anchor. Member-dependent, so a permuted-key control is meaningful
     and exact McNemar applies.

Reuses recall4's helpers so the instrument under test is literally the code
that produced the number. Writes recall4b.json.
"""
import json
import re
import time
import urllib.parse

import recall4 as R

KEYWORDS = R.KEYWORDS["health"]
WINDOW = R.WINDOW


def anchors_of(low):
    out = []
    for w in KEYWORDS:
        out += [m.start() for m in re.finditer(re.escape(w), low)]
    return out


def near(positions, anchors):
    return any(abs(p - a) <= WINDOW for p in positions for a in anchors)


def stat_b(low, anchors):
    sup = [m.start() for m in R.SUPERLATIVE.finditer(low)]
    return bool(anchors) and bool(sup) and near(sup, anchors)


def stat_c(low, anchors, value):
    if not anchors or value in (None, ""):
        return False
    sup = [m.start() for m in R.SUPERLATIVE.finditer(low)]
    if not sup:
        return False
    pat = re.compile(r"(?<![\d.])" + re.escape(str(value)) + r"(?![\d.])")
    vals = [m.start() for m in pat.finditer(low)]
    if not vals:
        return False
    return any(any(abs(v - a) <= WINDOW for v in vals)
               and any(abs(s - a) <= WINDOW for s in sup) for a in anchors)


def main():
    R.CAPTURED.clear()
    orig = R.patch()
    try:
        cand = R.ct.GENERATORS["health and medicine"]()
    finally:
        R.unpatch(orig)
    rows = R.CAPTURED[next(iter(R.CAPTURED))]
    members = [{"id": r.get("nct") or v, "key": k} for r, k, v in rows]
    print("answer %s, %d members" % (cand.answer, len(members)), flush=True)

    blobs, hits = {}, {}
    for m in members:
        try:
            js = json.loads(R._get(R.EPMC.format(
                q=urllib.parse.quote(str(m["id"])))))
            res = (js.get("resultList") or {}).get("result") or []
            blobs[m["id"]] = " ".join(
                " ".join(str(a.get(k) or "") for k in ("title", "abstractText"))
                for a in res).lower()
            hits[m["id"]] = js.get("hitCount")
        except Exception:  # noqa: BLE001
            blobs[m["id"]] = ""
            hits[m["id"]] = None
        time.sleep(0.35)

    cov = [m for m in members if blobs[m["id"]]]
    n = len(cov)
    A = B = C = Cp = anc = mb = mc = 0
    detail = []
    for i, m in enumerate(cov):
        low = blobs[m["id"]]
        an = anchors_of(low)
        a_ = bool(R.SUPERLATIVE.search(low))
        b_ = stat_b(low, an)
        c_ = stat_c(low, an, m["key"])
        # permute to the nearest member carrying a DIFFERENT key value
        j = (i + 1) % n
        while j != i and cov[j]["key"] == m["key"]:
            j = (j + 1) % n
        cp_ = stat_c(low, an, cov[j]["key"])
        A += a_
        B += b_
        C += c_
        Cp += cp_
        anc += bool(an)
        mb += int(c_ and not cp_)
        mc += int(cp_ and not c_)
        detail.append({"id": m["id"], "key": m["key"], "hit_count": hits[m["id"]],
                       "chars": len(low), "n_key_anchors": len(an),
                       "A_any_superlative": a_, "B_near_key_language": b_,
                       "C_own": c_, "C_permuted": cp_})

    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "category": "health and medicine", "answer": cand.answer,
           "n_members": len(members), "n_covered": n, "window_chars": WINDOW,
           "n_with_key_naming_language": anc,
           "A_any_superlative": {"k": A, "n": n, "frac": round(A / n, 4),
                                 "ci95": R.wilson(A, n),
                                 "member_independent": True},
           "B_superlative_near_key_language": {
               "k": B, "n": n, "frac": round(B / n, 4), "ci95": R.wilson(B, n),
               "member_independent": True},
           "C_own_key_with_superlative": {"k": C, "n": n,
                                          "frac": round(C / n, 4),
                                          "ci95": R.wilson(C, n)},
           "C_permuted": {"k": Cp, "n": n, "frac": round(Cp / n, 4)},
           "C_excess": round((C - Cp) / n, 4), "C_mcnemar_b": mb,
           "C_mcnemar_c": mc, "C_mcnemar_p": R.mcnemar_exact(mb, mc),
           "members": detail}
    ans = [d for d in detail if str(d["id"]) == str(cand.answer)]
    out["answer_row"] = ans[0] if ans else None
    if A / n > 0.5 and C == 0:
        out["verdict"] = ("A is base rate and carries no information; "
                          "the argmax is not leaked")
    elif C > Cp and out["C_mcnemar_p"] < 0.05:
        out["verdict"] = "genuine argmax leak; the key must be replaced"
    else:
        out["verdict"] = ("no argmax leak detectable at this specificity; "
                          "A remains uninformative")
    for k in ("n_with_key_naming_language", "A_any_superlative",
              "B_superlative_near_key_language", "C_own_key_with_superlative",
              "C_permuted", "C_excess", "C_mcnemar_p", "verdict"):
        print("  %-32s %s" % (k, out[k]), flush=True)
    print("  answer row: %s" % out["answer_row"], flush=True)
    with open("recall4b.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print("wrote recall4b.json", flush=True)


if __name__ == "__main__":
    main()
