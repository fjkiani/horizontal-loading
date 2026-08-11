"""legalops3 -- measure the EXACT predicate that will go into production.

legalops2 scored the Library of Congress loosely: it accepted a confirmation
when the case token appeared in ANY returned title. That is too weak. A LOC
query for "545 U.S. 75" returns ten titles from the same volume, so a token
can match a DIFFERENT case, or the volume's table of cases, and still count.
Left in, it would be instrument defect eleven.

The production predicate is stricter: some single returned title must contain
BOTH the case token AND the literal citation "{vol} u.s. {page}". LOC titles
are formatted "u.s. reports: {case}, {vol} u.s. {page} ({year})." so that
conjunction pins the case to the page rather than to the volume.

Measured on all 132 pages legalpages sampled, split by whether Cornell LII
serves them, so the strict rate is known separately on the gap and on the
control set. A negative control is included: the same query scored against a
token drawn from a DIFFERENT case in the same volume, which must NOT confirm.

Writes legalops3.json.
"""
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, "/workspace/seal_deploy")
import category_traps as ct  # noqa: E402

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = "SealTrapGenerator/1.0 (research; contact fahad@crispro.ai)"
LOC = ("https://www.loc.gov/collections/united-states-reports/?q={q}"
       "&fo=json&c=10")


def get(url, timeout=60):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as fh:
            return fh.status, fh.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, ""


def titles_for(vol, page):
    st, body = get(LOC.format(q=urllib.parse.quote(f"{vol} U.S. {page}")))
    if st != 200:
        return st, []
    try:
        js = json.loads(body)
    except Exception:  # noqa: BLE001
        return st, []
    return st, [ct._norm(r.get("title") or "") for r in (js.get("results") or [])]


def strict(titles, vol, page, token):
    """The production predicate: one title names the case AND the citation."""
    cite = ct._norm(f"{vol} u.s. {page}")
    return any(token in t and cite in t for t in titles)


def loose(titles, token):
    """legalops2's predicate, kept so the difference is quantified."""
    return any(token in t for t in titles)


def main():
    lp = json.load(open("/workspace/seal_deploy/legalpages.json"))
    rows = []
    for v in lp["volumes"]:
        smp = v.get("coverage_sample") or []
        for i, s in enumerate(smp):
            other = smp[(i + 1) % len(smp)]["name"] if len(smp) > 1 else None
            rows.append({"vol": v["vol"], "page": s["page"], "name": s["name"],
                         "lii_serves": s["status"] == 200,
                         "other_name": other})
    print("scoring %d sampled pages against the strict LOC predicate" % len(rows),
          flush=True)

    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "predicate": "some title contains BOTH the case token AND "
                        "'{vol} u.s. {page}'",
           "n_rows": len(rows), "rows": []}
    for r in rows:
        token = ct._cite_token(r["name"])
        st, titles = titles_for(r["vol"], r["page"])
        ok_s = strict(titles, r["vol"], r["page"], token)
        ok_l = loose(titles, token)
        neg = None
        if r["other_name"]:
            neg_tok = ct._cite_token(r["other_name"])
            if neg_tok != token:
                neg = strict(titles, r["vol"], r["page"], neg_tok)
        rec = {"vol": r["vol"], "page": r["page"], "name": r["name"],
               "token": token, "lii_serves": r["lii_serves"], "http": st,
               "n_titles": len(titles), "strict": ok_s, "loose": ok_l,
               "negative_control_strict": neg}
        out["rows"].append(rec)
        print("  %-4d p%-5d LII=%-5s strict=%-5s loose=%-5s neg=%-5s %s"
              % (r["vol"], r["page"], r["lii_serves"], ok_s, ok_l, neg,
                 r["name"][:34]), flush=True)
        time.sleep(0.8)

    def rate(sel, field="strict"):
        v = [x[field] for x in out["rows"] if sel(x) and x[field] is not None]
        return (sum(1 for x in v if x), len(v),
                round(sum(1 for x in v if x) / len(v), 4) if v else None)

    g = rate(lambda x: not x["lii_serves"])
    c = rate(lambda x: x["lii_serves"])
    a = rate(lambda x: True)
    gl = rate(lambda x: not x["lii_serves"], "loose")
    neg = rate(lambda x: True, "negative_control_strict")
    n_lii = sum(1 for x in out["rows"] if x["lii_serves"])
    union = sum(1 for x in out["rows"] if x["lii_serves"] or x["strict"])

    out["summary"] = {
        "strict_gap": {"k": g[0], "n": g[1], "rate": g[2]},
        "strict_control": {"k": c[0], "n": c[1], "rate": c[2]},
        "strict_overall": {"k": a[0], "n": a[1], "rate": a[2]},
        "loose_gap_for_comparison": {"k": gl[0], "n": gl[1], "rate": gl[2]},
        "negative_control": {"k": neg[0], "n": neg[1], "rate": neg[2]},
        "lii_only_coverage": round(n_lii / len(out["rows"]), 4),
        "lii_or_loc_coverage": round(union / len(out["rows"]), 4),
        "pages_rescued_by_loc": union - n_lii,
    }
    ok_ctrl = (c[2] or 0) >= 0.90
    ok_neg = (neg[2] or 1.0) <= 0.10
    if not ok_ctrl:
        out["verdict"] = ("LOC fails the positive control at %.4f; not usable"
                          % (c[2] or 0))
    elif not ok_neg:
        out["verdict"] = ("negative control confirms at %.4f; the predicate is "
                          "not specific to the page" % (neg[2] or 1.0))
    elif out["summary"]["lii_or_loc_coverage"] > out["summary"]["lii_only_coverage"]:
        out["verdict"] = ("add LOC as an alternative confirmation: coverage "
                          "%.4f -> %.4f, %d pages rescued"
                          % (out["summary"]["lii_only_coverage"],
                             out["summary"]["lii_or_loc_coverage"],
                             out["summary"]["pages_rescued_by_loc"]))
    else:
        out["verdict"] = "LOC adds no coverage over LII"
    print("\n" + json.dumps(out["summary"], indent=2), flush=True)
    print("verdict:", out["verdict"], flush=True)
    with open("legalops3.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print("wrote legalops3.json", flush=True)


if __name__ == "__main__":
    main()
