#!/usr/bin/env python3
"""bridges2 -- close the exact confirming-witness gaps the ban opens.

Measured shortfalls after removing "Wikimedia Foundation" from the deployed
catalog (source_gate needs >=3 operators AND >=2 independent confirming):

  travel     operator counts survive, but the ANSWER is read from Wikidata
             P1566 and travelbear.json proved that path answer-bearing.
             Keeping it as uncredited logic would hide the defect, not fix
             it, so travel needs the GeoNames id FROM GeoNames.  -> R2
  education  2 operators, 0 confirming. Hipo Labs already publishes the
             domain; ROR would be a third operator and a second confirmer.
             The first probe crashed on ROR v2, whose `links` are dicts
             ({"type","value"}) not strings -- my bug, not ROR's.   -> R1
  geography  3 operators, 1 confirming (OpenFlights). Needs one more
             publisher of ICAO 'SKIP'.                              -> R3
  sports     3 operators, 1 confirming (OCLC). Needs one more publisher
             of FAST 243777 = Orel Hershiser.                       -> R4

ROUNDS env var selects rounds. Writes bridges2_<tag>.json.
"""
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net  # noqa: E402

TAG = os.environ.get("BRIDGE_TAG", "all")
WANT = [b.strip() for b in os.environ.get("ROUNDS", "R1,R2,R3,R4").split(",") if b.strip()]
OUT = os.environ.get("BRIDGE_OUT", f"bridges2_{TAG}.json")
STATE = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rounds": {}}


def save():
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(STATE, fh, indent=2)
    os.replace(tmp, OUT)


def record(name, fn):
    if name not in WANT:
        return
    print("\n--- %s ---" % name, flush=True)
    t0 = time.time()
    try:
        r = fn()
    except Exception as e:  # noqa: BLE001
        r = {"viable": False, "error": "%s: %s" % (type(e).__name__, e),
             "tb": traceback.format_exc()[-500:]}
    r["secs"] = round(time.time() - t0, 1)
    STATE["rounds"][name] = r
    print("  => %s  %s" % ("VIABLE" if r.get("viable") else "DEAD",
                           str(r.get("note", r.get("error", "")))[:230]), flush=True)
    save()


# ---------------------------------------------------------------- R1 education
def r1_ror():
    """ROR v2: links are [{'type':'website','value':...}] not strings."""
    want = "ntnu.no"
    j = net.get_json("https://api.ror.org/organizations?query="
                     "Norwegian+University+of+Science+and+Technology", timeout=90)
    rows = []
    for i in (j or {}).get("items", [])[:5]:
        links = []
        for l in (i.get("links") or []):
            links.append(l.get("value") if isinstance(l, dict) else l)
        rows.append({"id": i.get("id"), "name": i.get("name"), "links": links,
                     "match": any(want in (s or "").lower() for s in links)})
    match = [r for r in rows if r["match"]]
    return {"viable": bool(match), "operator": "Research Organization Registry",
            "want": want, "n": len(rows), "rows": rows,
            "ror_id": (match[0]["id"] if match else None),
            "note": ("ROR publishes %s at %s" % (want, match[0]["id"]) if match
                     else "ROR returned %d orgs, none carrying %s" % (len(rows), want))}


# ---------------------------------------------------------------- R2 travel
def r2_geonames_search():
    """Can GeoNames itself supply the id, with no Wikidata in the path?

    Two things must hold: the search must return the airport, and it must be
    UNIQUE enough to pin. Also verify the coordinate agrees with OurAirports,
    which is the check the old one-token substring guard failed to make.
    """
    want_id, lat, lon = "6296543", 68.607299804688, 27.405300140381
    out = {"want_id": want_id, "users": {}}
    for user in ("demo", "openstreetmap", "geonamesfree"):
        try:
            j = net.get_json("http://api.geonames.org/searchJSON?q=Ivalo+Airport"
                             "&maxRows=10&featureClass=S&username=%s" % user, timeout=60)
            gs = (j or {}).get("geonames")
            if gs is None:
                out["users"][user] = {"ok": False, "status": str(j.get("status"))[:180]}
                continue
            rows = [{"geonameId": str(g.get("geonameId")), "name": g.get("name"),
                     "fcode": g.get("fcode"), "lat": g.get("lat"), "lng": g.get("lng")}
                    for g in gs[:10]]
            exact = [r for r in rows if r["geonameId"] == want_id]
            near = [r for r in rows if r.get("lat")
                    and abs(float(r["lat"]) - lat) < 0.05
                    and abs(float(r["lng"]) - lon) < 0.05]
            out["users"][user] = {"ok": True, "n": len(rows), "rows": rows,
                                  "returns_target": bool(exact),
                                  "n_coordinate_verified": len(near),
                                  "coord_unique": len(near) == 1,
                                  "coord_hit_is_target": bool(near and near[0]["geonameId"] == want_id)}
        except Exception as e:  # noqa: BLE001
            out["users"][user] = {"ok": False,
                                  "error": "%s: %s" % (type(e).__name__, str(e)[:140])}
    good = [u for u, v in out["users"].items()
            if v.get("ok") and v.get("coord_unique") and v.get("coord_hit_is_target")]
    out["viable"] = bool(good)
    out["usable_usernames"] = good
    out["operator"] = "GeoNames" if good else None
    out["note"] = ("GeoNames search pins %s by coordinate under username(s) %s -- "
                   "a coordinate check, unlike the one-token substring guard"
                   % (want_id, good)) if good else \
        "no unauthenticated GeoNames search route pinned the airport uniquely"
    return out


# ---------------------------------------------------------------- R3 geography
def r3_icao_third():
    """A third publisher of ICAO 'SKIP' (San Luis Airport, Ipiales, Colombia)."""
    # DEFECT #13 (mine, 2 sites): `icao, out = "SKIP", {"icao": icao, ...}`
    # Python evaluates the entire RHS tuple before binding any target, so the
    # dict literal referenced `icao` while it was still unbound.
    icao = "SKIP"
    out = {"icao": icao, "routes": {}}
    try:
        j = net.get_json("https://aviationweather.gov/api/data/stationinfo"
                         "?ids=%s&format=json" % icao, timeout=90)
        rows = j if isinstance(j, list) else [j]
        hit = [r for r in rows if isinstance(r, dict)
               and str(r.get("icaoId", "")).upper() == icao]
        out["routes"]["noaa_awc"] = {"ok": bool(hit), "n": len(rows),
                                     "row": (hit[0] if hit else (rows[0] if rows else None))}
    except Exception as e:  # noqa: BLE001
        out["routes"]["noaa_awc"] = {"ok": False,
                                     "error": "%s: %s" % (type(e).__name__, str(e)[:150])}
    try:
        txt = net.fetch("https://raw.githubusercontent.com/mwgg/Airports/master/airports.json",
                        timeout=120)
        j = json.loads(txt)
        r = j.get(icao)
        out["routes"]["mwgg_airports"] = {"ok": bool(r), "row": r, "n_total": len(j)}
    except Exception as e:  # noqa: BLE001
        out["routes"]["mwgg_airports"] = {"ok": False,
                                          "error": "%s: %s" % (type(e).__name__, str(e)[:150])}
    ok = [k for k, v in out["routes"].items() if v.get("ok")]
    out["viable"] = bool(ok)
    out["operator"] = {"noaa_awc": "US National Oceanic and Atmospheric Administration",
                       "mwgg_airports": "mwgg Airports"}
    out["working_routes"] = ok
    out["note"] = "routes publishing ICAO %s: %s" % (icao, ok or "none")
    return out


# ---------------------------------------------------------------- R4 sports
def r4_fast_second():
    """A second publisher tying FAST 243777 to Orel Hershiser."""
    fid, want = "243777", "hershiser"  # see DEFECT #13 note in r3_icao_third
    out = {"fast_id": fid, "routes": {}}
    try:
        txt = net.fetch("https://viaf.org/viaf/search?query=local.personalNames+all+"
                        "%22Orel+Hershiser%22&httpAccept=application/json", timeout=90)
        out["routes"]["viaf"] = {"ok": want in txt.lower(), "bytes": len(txt),
                                 "names_target": want in txt.lower()}
    except Exception as e:  # noqa: BLE001
        out["routes"]["viaf"] = {"ok": False,
                                 "error": "%s: %s" % (type(e).__name__, str(e)[:150])}
    try:
        j = net.get_json("https://api.datacite.org/dois?query=%22Orel+Hershiser%22"
                         "&page[size]=1", timeout=90)
        out["routes"]["datacite"] = {"ok": bool((j or {}).get("data")),
                                     "n": len(((j or {}).get("data") or []))}
    except Exception as e:  # noqa: BLE001
        out["routes"]["datacite"] = {"ok": False,
                                     "error": "%s: %s" % (type(e).__name__, str(e)[:150])}
    try:
        # Retrosheet is already an operator on this trap; does it name the man?
        txt = net.fetch("https://www.retrosheet.org/boxesetc/H/Phershio001.htm", timeout=90)
        out["routes"]["retrosheet"] = {"ok": want in txt.lower(), "bytes": len(txt)}
    except Exception as e:  # noqa: BLE001
        out["routes"]["retrosheet"] = {"ok": False,
                                       "error": "%s: %s" % (type(e).__name__, str(e)[:150])}
    ok = [k for k, v in out["routes"].items() if v.get("ok")]
    out["viable"] = bool(ok)
    out["working_routes"] = ok
    out["note"] = ("routes naming the FAST %s subject: %s. NOTE Retrosheet naming the "
                   "PERSON is not the same as publishing the FAST IDENTIFIER; only an "
                   "operator that carries the identifier can confirm the answer."
                   % (fid, ok or "none"))
    return out


def main():
    print("bridges2 tag=%s rounds=%s" % (TAG, WANT), flush=True)
    record("R1", r1_ror)
    record("R2", r2_geonames_search)
    record("R3", r3_icao_third)
    record("R4", r4_fast_second)
    STATE["summary"] = {k: bool(v.get("viable")) for k, v in STATE["rounds"].items()}
    save()
    print("\nsummary: %s" % STATE["summary"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
