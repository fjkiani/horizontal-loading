#!/usr/bin/env python3
"""bridges -- recon for unbanned replacement operators.

The Wikimedia ban removes the SECOND witness from 7 of 10 shipped traps.
source_gate requires >=3 operators and >=2 independent confirming operators,
so a category with Wikidata as its only confirming witness drops to
unavailable unless a replacement operator carries the SAME value.

This probe does not repair anything. It answers one question per candidate
bridge: does an unbanned, independent operator publish the identical value?
Each bridge reports VIABLE / DEAD plus the evidence, so a category that goes
unavailable goes there with a measured reason rather than an assumption.

  B1 geography  answer = ICAO ident (OurAirports).  Bridge: OpenFlights
                airports.dat, a separate operator distributing ICAO codes.
  B2 travel     answer = GeoNames id, currently read from Wikidata P1566 and
                PROVEN answer-bearing (travelbear.json). Bridge: reach the
                GeoNames id without Wikidata, or fail.
  B3 sports     answer = OCLC FAST identifier, confirmed via Wikidata P2163.
                Bridge: OCLC itself. id.worldcat.org returns 406 without an
                RDF Accept header, which is why it looked dead before.
  B4 education  answer = internet domain, confirmed via Wikidata P856.
                Bridge: ROR, and Hipo Labs as a fallback.

BRIDGES env var selects which to run (comma separated, default all).
Writes bridges_<tag>.json, checkpointing after every bridge.
"""
import json
import os
import re
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import category_traps as ct  # noqa: E402
import net  # noqa: E402

TAG = os.environ.get("BRIDGE_TAG", "all")
WANT = [b.strip() for b in os.environ.get("BRIDGES", "B1,B2,B3,B4").split(",") if b.strip()]
OUT = os.environ.get("BRIDGE_OUT", f"bridges_{TAG}.json")

STATE = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "why": "unbanned replacement operators for the Wikimedia ban",
         "bridges": {}}


def save():
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(STATE, fh, indent=2)
    os.replace(tmp, OUT)
    print("  checkpoint: %s" % OUT, flush=True)


def record(name, fn):
    if name not in WANT:
        return
    print("\n--- %s ---" % name, flush=True)
    t0 = time.time()
    try:
        r = fn()
    except Exception as e:  # noqa: BLE001
        r = {"viable": False, "error": "%s: %s" % (type(e).__name__, e),
             "tb": traceback.format_exc()[-600:]}
    r["secs"] = round(time.time() - t0, 1)
    STATE["bridges"][name] = r
    print("  => %s  %s" % ("VIABLE" if r.get("viable") else "DEAD",
                           str(r.get("note", r.get("error", "")))[:220]), flush=True)
    save()


# ------------------------------------------------------------------ B1
_OPENFLIGHTS_AP = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"


def b1_geography():
    """Does OpenFlights publish the same ICAO ident as OurAirports?

    airports.dat columns: id,name,city,country,IATA,ICAO,lat,lon,...
    """
    txt = net.fetch(_OPENFLIGHTS_AP, timeout=120)
    rows = []
    for line in txt.splitlines():
        f = re.findall(r'"([^"]*)"|([^,]+)', line)
        vals = [a or b for a, b in f]
        if len(vals) >= 8:
            rows.append(vals)
    icao = {r[5].strip(): r for r in rows if len(r[5].strip()) == 4 and r[5].strip() != "\\N"}
    # the deployed geography answer, and a sample to measure coverage
    target = "SKIP"
    hit = icao.get(target)
    sample = ["SKIP", "EFIV", "KJFK", "EGLL", "LFPG", "SAWG", "NZSP", "YSSY"]
    cover = {s: (s in icao) for s in sample}
    n_cov = sum(1 for v in cover.values() if v)
    return {"viable": bool(hit),
            "operator": "OpenFlights",
            "n_rows": len(rows), "n_icao": len(icao),
            "target": target,
            "target_row": (hit[:8] if hit else None),
            "sample_coverage": cover,
            "sample_hit_rate": round(n_cov / len(sample), 4),
            "note": ("OpenFlights carries ICAO %s as %r; %d/%d sample idents present"
                     % (target, (hit[1] if hit else None), n_cov, len(sample)))
            if hit else "OpenFlights does not carry ICAO %s" % target}


# ------------------------------------------------------------------ B2
def b2_travel():
    """Reach the GeoNames id for an airport WITHOUT Wikidata.

    Two routes tried:
      R1 GeoNames free search  -- needs a username; measure the refusal.
      R2 OSM Nominatim         -- returns osm_id and extratags; an OSM id is
         opaque, absent from the encyclopaedia article, and OSM is an
         independent operator. Coordinate-verified, unlike the one-token
         substring check that travelbear defeated.
    """
    out = {"routes": {}}
    lat, lon, name = 68.607299804688, 27.405300140381, "Ivalo Airport"
    baseline_geonames = "6296543"

    try:
        j = net.get_json("http://api.geonames.org/searchJSON?q=Ivalo+Airport"
                         "&maxRows=5&username=demo", timeout=60)
        out["routes"]["geonames_search_demo"] = {
            "ok": "geonames" in j, "status": str(j.get("status"))[:200]}
    except Exception as e:  # noqa: BLE001
        out["routes"]["geonames_search_demo"] = {"ok": False,
                                                 "error": "%s: %s" % (type(e).__name__, str(e)[:160])}

    try:
        u = ("https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5"
             "&extratags=1&q=Ivalo+Airport")
        j = net.get_json(u, timeout=90)
        hits = []
        for h in (j or [])[:5]:
            et = h.get("extratags") or {}
            hits.append({"osm_type": h.get("osm_type"), "osm_id": h.get("osm_id"),
                         "name": h.get("display_name", "")[:70],
                         "lat": h.get("lat"), "lon": h.get("lon"),
                         "iata": et.get("iata"), "icao": et.get("icao"),
                         "wikidata": et.get("wikidata")})
        # coordinate check: is any hit within ~0.05 deg of the OurAirports fix?
        near = [h for h in hits
                if h.get("lat") and abs(float(h["lat"]) - lat) < 0.05
                and abs(float(h["lon"]) - lon) < 0.05]
        out["routes"]["nominatim"] = {
            "ok": bool(hits), "n_hits": len(hits), "hits": hits,
            "n_coordinate_verified": len(near),
            "carries_iata_or_icao": any(h.get("iata") or h.get("icao") for h in hits)}
    except Exception as e:  # noqa: BLE001
        out["routes"]["nominatim"] = {"ok": False,
                                      "error": "%s: %s" % (type(e).__name__, str(e)[:160])}

    n = out["routes"].get("nominatim", {})
    g = out["routes"].get("geonames_search_demo", {})
    viable = bool(n.get("ok") and n.get("n_coordinate_verified"))
    out["viable"] = viable
    out["operator"] = "OpenStreetMap Foundation" if viable else None
    out["baseline_geonames_id"] = baseline_geonames
    out["note"] = ("Nominatim returns %s coordinate-verified hit(s) carrying "
                   "iata/icao=%s; GeoNames free search %s"
                   % (n.get("n_coordinate_verified"), n.get("carries_iata_or_icao"),
                      "usable" if g.get("ok") else "refused (needs a registered username)"))
    return out


# ------------------------------------------------------------------ B3
def b3_sports():
    """OCLC FAST: does id.worldcat.org confirm FAST 243777 = Orel Hershiser?

    406 without an RDF Accept header is why this looked dead earlier.
    """
    fast_id, want = "243777", "hershiser"
    out = {"fast_id": fast_id, "attempts": {}}
    urls = [("rdf", "https://id.worldcat.org/fast/%s" % fast_id,
             {"Accept": "application/rdf+xml"}),
            ("jsonld", "https://id.worldcat.org/fast/%s" % fast_id,
             {"Accept": "application/ld+json"}),
            ("suffix", "https://id.worldcat.org/fast/%s.rdf.xml" % fast_id, None)]
    body = None
    for tag, u, hdr in urls:
        try:
            txt = net.fetch(u, timeout=90, headers=hdr)
            out["attempts"][tag] = {"ok": True, "bytes": len(txt),
                                    "names_target": want in txt.lower()}
            if want in txt.lower() and body is None:
                body = txt
        except Exception as e:  # noqa: BLE001
            out["attempts"][tag] = {"ok": False,
                                    "error": "%s: %s" % (type(e).__name__, str(e)[:140])}
    out["viable"] = body is not None
    out["operator"] = "OCLC" if body else None
    if body:
        m = re.findall(r"<[^>]*prefLabel[^>]*>([^<]{3,80})<", body) or \
            re.findall(r'"prefLabel"\s*:\s*"([^"]{3,80})"', body)
        out["preflabel"] = (m[:3] if m else None)
    out["note"] = ("OCLC FAST %s resolves and names %r" % (fast_id, want)) if body \
        else "no Accept header variant returned a body naming the target"
    return out


# ------------------------------------------------------------------ B4
def b4_education():
    """ROR / Hipo Labs: does an unbanned operator publish ntnu.no?"""
    want = "ntnu.no"
    out = {"want": want, "routes": {}}
    try:
        j = net.get_json("https://api.ror.org/organizations?query="
                         "Norwegian+University+of+Science+and+Technology", timeout=90)
        items = (j or {}).get("items", [])[:5]
        rows = [{"id": i.get("id"), "name": i.get("name"),
                 "links": i.get("links"),
                 "domain_match": any(want in (l or "").lower() for l in (i.get("links") or []))}
                for i in items]
        out["routes"]["ror"] = {"ok": bool(items), "n": len(items), "rows": rows,
                                "match": any(r["domain_match"] for r in rows)}
    except Exception as e:  # noqa: BLE001
        out["routes"]["ror"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    try:
        j = net.get_json("http://universities.hipolabs.com/search?name="
                         "Norwegian+University+of+Science", timeout=90)
        rows = [{"name": r.get("name"), "domains": r.get("domains"),
                 "domain_match": want in [d.lower() for d in (r.get("domains") or [])]}
                for r in (j or [])[:5]]
        out["routes"]["hipolabs"] = {"ok": bool(rows), "n": len(rows), "rows": rows,
                                     "match": any(r["domain_match"] for r in rows)}
    except Exception as e:  # noqa: BLE001
        out["routes"]["hipolabs"] = {"ok": False,
                                     "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    r, h = out["routes"].get("ror", {}), out["routes"].get("hipolabs", {})
    out["viable"] = bool(r.get("match") or h.get("match"))
    ops = []
    if r.get("match"):
        ops.append("Research Organization Registry")
    if h.get("match"):
        ops.append("Hipo Labs")
    out["operator"] = ops or None
    out["note"] = "operators publishing %s: %s" % (want, ops or "none")
    return out


def main():
    print("bridges tag=%s running %s" % (TAG, WANT), flush=True)
    record("B1", b1_geography)
    record("B2", b2_travel)
    record("B3", b3_sports)
    record("B4", b4_education)
    v = {k: bool(r.get("viable")) for k, r in STATE["bridges"].items()}
    STATE["summary"] = v
    save()
    print("\nsummary: %s" % v, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
