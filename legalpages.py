"""legalpages -- is the Cornell LII 404 a page-selection bug or a coverage wall?

I told the user the legal 404 was "our bug, picking page numbers that are not
case-START pages". Reading gen_legal afterwards, that claim looks wrong: the
generator takes first_page straight out of the Caselaw Access Project volume
metadata and then REQUIRES the official citation to read "{vol} U.S. {first_page}"
before accepting the row. That is a case-start page by construction.

So the retraction needs its own retraction, or confirmation, and only a
measurement can decide which. Three things measured here.

  M1 SEED SWEEP. Run gen_legal on every legal seed and record whether it
     ships, and for the failures the exact reason string the generator itself
     recorded in `tried`. That separates "LII 404" from every other cause.

  M2 LII COVERAGE PER VOLUME. For each volume in the roster, take case-start
     pages straight from CAP metadata -- pages that are correct by
     construction -- and record the raw HTTP status LII returns for each.
     If correct pages 404 in bulk for some volumes, the defect is upstream
     coverage and no amount of page arithmetic fixes it.

  M3 WHAT THE WINNER WOULD BE. For every volume, compute the argmin-by-name
     winner the way gen_legal does, then fetch its LII page and record the
     status and whether the case name token is present. This is the exact
     request the generator makes, isolated from the rest of the pipeline.

Writes legalpages.json.
"""
import json
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import category_traps as ct  # noqa: E402
import gen_v2  # noqa: E402,F401
import gen_v3  # noqa: E402,F401
import gen_v4  # noqa: E402,F401
import seed_roster as sr  # noqa: E402

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = "SealTrapGenerator/1.0 (research; contact fahad@crispro.ai)"
LII = "https://www.law.cornell.edu/supremecourt/text/{vol}/{page}"
SAMPLE_PER_VOL = 6
random.seed(20260811)


def status_of(url, timeout=45):
    """Raw HTTP status, without net.fetch's exception wrapping."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as fh:
            body = fh.read().decode("utf-8", "replace")
            return fh.status, body
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}", ""


def multi_cases(vol):
    """Reproduce gen_legal's row filter exactly."""
    cases = ct._cap_volume(vol)

    def official(c):
        return next((x.get("cite") for x in (c.get("citations") or [])
                     if x.get("type") == "official"), None)

    out = []
    for c in cases:
        fp, lp = str(c.get("first_page") or ""), str(c.get("last_page") or "")
        nm = (c.get("name_abbreviation") or "").strip()
        if not (fp.isdigit() and lp.isdigit() and nm):
            continue
        if int(lp) <= int(fp):
            continue
        if not nm.isascii():
            continue
        if official(c) != f"{vol} U.S. {int(fp)}":
            continue
        out.append({"name": nm, "page": int(fp), "last": int(lp),
                    "cite": official(c)})
    return len(cases), out


def main():
    res = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "seeds": [], "volumes": []}

    # ---------------- M1 seed sweep ----------------
    print("=== M1 seed sweep ===", flush=True)
    for i, sd in enumerate(sr.seeds_for("legal")):
        row = {"idx": i, "seed": sd}
        ct.LAST_RANK.clear()
        try:
            cand = ct.GENERATORS["legal"](**sd)
            row["shipped"] = True
            row["answer"] = cand.answer
            row["entity"] = cand.entity
        except Exception as e:  # noqa: BLE001
            row["shipped"] = False
            row["error"] = f"{type(e).__name__}: {e}"
        res["seeds"].append(row)
        print("[%d] %-34s %s %s" % (i, json.dumps(sd)[:34],
                                    "SHIP " + str(row.get("answer"))
                                    if row["shipped"] else "REFUSE",
                                    "" if row["shipped"]
                                    else row["error"][:150]), flush=True)

    # ---------------- M2 / M3 per volume ----------------
    vols = sorted({v for sd in sr.seeds_for("legal")
                   for v in sd.get("vols", (504, 505, 498, 510, 512, 517))})
    print("\n=== M2/M3 per volume (%d volumes) ===" % len(vols), flush=True)
    for vol in vols:
        row = {"vol": vol}
        try:
            n_cap, multi = multi_cases(vol)
        except Exception as e:  # noqa: BLE001
            row["error"] = f"{type(e).__name__}: {e}"
            res["volumes"].append(row)
            print("vol %s  CAP %s" % (vol, row["error"][:80]), flush=True)
            continue
        row["n_cap_cases"] = n_cap
        row["n_multi_page"] = len(multi)

        # M3 the winner gen_legal would pick
        if multi:
            win = min(multi, key=lambda r: r["name"].lower())
            st, body = status_of(LII.format(vol=vol, page=win["page"]))
            tok = ct._cite_token(win["name"])
            row["winner"] = {"name": win["name"], "page": win["page"],
                             "cite": win["cite"], "lii_status": st,
                             "token": tok,
                             "token_in_page": (tok in ct._norm(body))
                             if body else None}
            print("vol %s  winner p%-5s %-34s LII=%s token=%s"
                  % (vol, win["page"], win["name"][:34], st,
                     row["winner"]["token_in_page"]), flush=True)
            time.sleep(1.0)

        # M2 coverage on pages that are correct by construction
        samp = random.sample(multi, min(SAMPLE_PER_VOL, len(multi)))
        cov = []
        for c in samp:
            st, body = status_of(LII.format(vol=vol, page=c["page"]))
            tok = ct._cite_token(c["name"])
            cov.append({"page": c["page"], "name": c["name"], "status": st,
                        "token_in_page": (tok in ct._norm(body))
                        if body else None})
            time.sleep(1.0)
        row["coverage_sample"] = cov
        ok = sum(1 for c in cov if c["status"] == 200)
        named = sum(1 for c in cov if c.get("token_in_page"))
        row["n_sampled"] = len(cov)
        row["n_http_200"] = ok
        row["n_token_present"] = named
        row["lii_covers_volume"] = (ok == len(cov)) if cov else None
        print("vol %s  CAP=%-4d multi=%-4d LII 200: %d/%d  token: %d/%d"
              % (vol, n_cap, len(multi), ok, len(cov), named, len(cov)),
              flush=True)
        res["volumes"].append(row)

    good = [v for v in res["volumes"] if v.get("lii_covers_volume") is True]
    bad = [v for v in res["volumes"] if v.get("lii_covers_volume") is False]
    res["summary"] = {
        "n_seeds": len(res["seeds"]),
        "n_seeds_shipped": sum(1 for s in res["seeds"] if s["shipped"]),
        "n_volumes": len(res["volumes"]),
        "n_volumes_lii_complete": len(good),
        "n_volumes_lii_incomplete": len(bad),
        "volumes_lii_complete": [v["vol"] for v in good],
        "volumes_lii_incomplete": [v["vol"] for v in bad],
        "n_correct_pages_tested": sum(v.get("n_sampled", 0)
                                      for v in res["volumes"]),
        "n_correct_pages_http_200": sum(v.get("n_http_200", 0)
                                        for v in res["volumes"]),
    }
    tot = res["summary"]["n_correct_pages_tested"]
    ok = res["summary"]["n_correct_pages_http_200"]
    res["summary"]["correct_page_200_rate"] = round(ok / tot, 4) if tot else None
    res["summary"]["verdict"] = (
        "page selection is correct; LII coverage is the binding constraint"
        if tot and ok < tot else
        "LII serves every correctly constructed page; the 404 was a bad page"
    )
    print("\n" + json.dumps(res["summary"], indent=2), flush=True)
    with open("legalpages.json", "w") as fh:
        json.dump(res, fh, indent=2)
    print("wrote legalpages.json", flush=True)


if __name__ == "__main__":
    main()
