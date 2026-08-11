"""legalops -- find a witness operator that covers U.S. Reports completely.

CORRECTION TO legalops v1. The first version hard-coded eight (vol, page,
name) triples and four of the names were wrong: I supplied them from memory
rather than from data. "Ankenbrandt v. Richards" is 504 U.S. 689, not 510
U.S. 317 or 505 U.S. 1230; "Alabama v. Shelton" is 535 U.S. 654, not 540
U.S. 844. The tell was in the result table: CourtListener, the Library of
Congress and CAP -- three unrelated operators -- agreed on all eight rows,
true for the same four and false for the same four. Independent sources do
not agree perfectly on real coverage. They agreed because the four failures
were rows where the name I passed does not live at that citation, so every
operator correctly failed to confirm it. The 0.5 confirm rate measured my
memory, not their coverage.

Every triple now comes from legalpages.json, whose names were read from CAP
volume metadata. Tests all 52 sampled pages Cornell LII does NOT serve, plus
20 it does serve as a positive control, so a confirm rate near 1.0 on the
control set is required before any claim about the gap set is credible.

Wikisource is dropped: its search does not index "{vol} U.S. {page}" citation
strings, so it scored 0 for 8 on rows other operators confirmed.

Writes legalops.json.
"""
import json
import random
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
N_CONTROL = 20
random.seed(20260811)


def get(url, timeout=45):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as fh:
            return fh.status, fh.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, ""


def norm(s):
    return " ".join((s or "").lower().replace("&amp;", "&").split())


def probe_courtlistener(vol, page, token):
    url = ("https://www.courtlistener.com/api/rest/v4/search/?q=" +
           urllib.parse.quote(f'citation:("{vol} U.S. {page}")') +
           "&type=o&court=scotus")
    st, body = get(url, timeout=60)
    if st != 200:
        return {"status": st, "confirms": False}
    try:
        js = json.loads(body)
    except Exception:  # noqa: BLE001
        return {"status": st, "confirms": False}
    rows = js.get("results") or []
    names = [norm(r.get("caseName") or "") for r in rows]
    return {"status": st, "count": js.get("count"), "n_rows": len(rows),
            "confirms": any(token in n for n in names), "names": names[:2]}


def probe_loc(vol, page, token):
    url = ("https://www.loc.gov/collections/united-states-reports/?q=" +
           urllib.parse.quote(f"{vol} U.S. {page}") + "&fo=json&c=10")
    st, body = get(url, timeout=60)
    if st != 200:
        return {"status": st, "confirms": False}
    try:
        js = json.loads(body)
    except Exception:  # noqa: BLE001
        return {"status": st, "confirms": False}
    res = js.get("results") or []
    titles = [norm(r.get("title")) for r in res]
    return {"status": st, "n_results": len(res),
            "confirms": any(token in t for t in titles), "titles": titles[:2]}


def probe_cap_case(vol, page, token):
    """A second CAP endpoint. NOT independent of the primary operator, so it
    cannot satisfy min_operators=3. Measured to separate 'the page exists
    upstream' from 'this particular operator serves it'."""
    st, body = get(f"https://static.case.law/us/{vol}/cases/{page:04d}-01.json",
                   timeout=60)
    if st != 200:
        return {"status": st, "confirms": False}
    try:
        js = json.loads(body)
    except Exception:  # noqa: BLE001
        return {"status": st, "confirms": False}
    nm = norm(js.get("name_abbreviation") or js.get("name") or "")
    return {"status": st, "confirms": token in nm, "name": nm[:70],
            "independent_of_primary": False}


PROBES = [("courtlistener", probe_courtlistener), ("loc.gov", probe_loc),
          ("static.case.law/cases", probe_cap_case)]


def main():
    lp = json.load(open("/workspace/seal_deploy/legalpages.json"))
    gap, ctrl = [], []
    for v in lp["volumes"]:
        for s in v.get("coverage_sample") or []:
            row = (v["vol"], s["page"], s["name"], s["status"] == 200)
            (ctrl if s["status"] == 200 else gap).append(row)
    random.shuffle(ctrl)
    triples = gap + ctrl[:N_CONTROL]
    print("triples: %d LII-gap + %d LII-served control = %d"
          % (len(gap), min(N_CONTROL, len(ctrl)), len(triples)), flush=True)

    res = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "n_gap": len(gap), "n_control": min(N_CONTROL, len(ctrl)),
           "names_sourced_from": "legalpages.json / CAP volume metadata",
           "triples": [], "operators": {}}

    for vol, page, name, lii_ok in triples:
        token = ct._cite_token(name)
        row = {"vol": vol, "page": page, "name": name, "token": token,
               "lii_serves": lii_ok, "probes": {}}
        for pname, fn in PROBES:
            try:
                row["probes"][pname] = fn(vol, page, token)
            except Exception as e:  # noqa: BLE001
                row["probes"][pname] = {"error": f"{type(e).__name__}: {e}",
                                        "confirms": False}
            time.sleep(1.0)
        res["triples"].append(row)
        print("%-4d p%-5d LII=%-5s %-34s %s"
              % (vol, page, lii_ok, name[:34],
                 "  ".join("%s=%s" % (p[:4], row["probes"][p].get("confirms"))
                           for p, _ in PROBES)), flush=True)

    for pname, _ in PROBES:
        g = [t["probes"][pname].get("confirms")
             for t in res["triples"] if not t["lii_serves"]]
        c = [t["probes"][pname].get("confirms")
             for t in res["triples"] if t["lii_serves"]]
        res["operators"][pname] = {
            "control_confirm": sum(1 for x in c if x), "n_control": len(c),
            "control_rate": round(sum(1 for x in c if x) / len(c), 4) if c else None,
            "gap_confirm": sum(1 for x in g if x), "n_gap": len(g),
            "gap_rate": round(sum(1 for x in g if x) / len(g), 4) if g else None,
            "instrument_valid": (sum(1 for x in c if x) / len(c) >= 0.90) if c else False,
        }
    # An operator only counts if it passes the control set first.
    viable = [p for p, v in res["operators"].items()
              if v["instrument_valid"] and v["gap_rate"] and v["gap_rate"] >= 0.90
              and p != "static.case.law/cases"]
    res["viable_replacements"] = viable
    if not any(v["instrument_valid"] for v in res["operators"].values()):
        res["verdict"] = "no operator passed the positive control; probe is unsound"
    elif viable:
        res["verdict"] = ("LII can be replaced by " + ", ".join(viable) +
                          "; coverage gap closes")
    else:
        res["verdict"] = ("controls pass but no operator covers the LII gap at "
                          ">=0.90; legal availability stays seed-limited")
    print("\n" + json.dumps({"operators": res["operators"],
                             "viable_replacements": viable,
                             "verdict": res["verdict"]}, indent=2), flush=True)
    with open("legalops.json", "w") as fh:
        json.dump(res, fh, indent=2)
    print("wrote legalops.json", flush=True)


if __name__ == "__main__":
    main()
