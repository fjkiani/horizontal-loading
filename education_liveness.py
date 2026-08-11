"""Test whether the education answers name institutions that still exist.

The key-replacement probe surfaced something bigger than the order leak. The
fail-closed P856 witness rejected 7 of 13 candidates, and in 5 of those the
rejection was a STALE DOMAIN in Hipo Labs that Wikidata contradicts:

    Austria  moz.ac.at            -> P856 uni-mozarteum.at
    Chile    universidadarcis.cl  -> P856 uarcis.cl
    Portugal universidade-autonoma.pt -> P856 autonoma.pt
    Hungary  filmacademy.hu       -> P856 szfe.hu
    Ireland  nuigalway.ie         -> P856 universityofgalway.ie   (2022 rename)

That raises a question about the SHIPPED answer. wit.ie is Waterford Institute
of Technology, which was dissolved into South East Technological University in
2022. Its P856 still reads wit.ie, so the equality witness PASSES -- exactly the
hole the Portugal/utl.pt cross-cohort run already flagged: the witness confirms
the domain binding, not the institution's continued existence.

So the equality check is necessary but not sufficient. This script tests a
LIVENESS guard on top of it, using Wikidata properties that record an entity
ceasing to exist in its own right:

    P576  dissolved, abolished or demolished date
    P7888 merged into
    P1366 replaced by

A candidate whose item carries any of these is a defunct institution and must
not be the answer to a present-tense question, no matter what P856 says.

Writes education_liveness.json.
"""
import json
import os
import sys

import net

OUT = "education_liveness.json"

# The shipped answer plus every candidate the key probe accepted.
SUBJECTS = [
    ("SHIPPED", "wit.ie", "Waterford Institute of Technology"),
    ("Ireland", "shannoncollege.com", "Shannon College of Hotel Management"),
    ("Israel", "rbni.technion.ac.il", "Russell Berrie Nanotechnology Institute"),
    ("Israel", "openu.ac.il", "Open University of Israel"),
    ("Finland", "puv.fi", "Vaasa University of Applied Sciences"),
    ("Norway", "ntnu.no", "Norwegian University of Science and Technology"),
]

DEATH_PROPS = {
    "P576": "dissolved, abolished or demolished date",
    "P7888": "merged into",
    "P1366": "replaced by",
}


def _entity(qid):
    return net.wikidata_entity(qid)


def check(name, domain):
    rec = {"name": name, "domain": domain}
    # net.wikidata_search returns the raw wbsearchentities envelope, so the hits
    # live under "search"; indexing the dict directly raises KeyError: 0.
    try:
        hits = (net.wikidata_search(name) or {}).get("search") or []
    except Exception as exc:
        rec["error"] = f"search failed: {type(exc).__name__}"
        return rec
    if not hits:
        rec["error"] = "no Wikidata item matched the name"
        return rec
    rec["matched_label"] = (hits[0].get("display", {}).get("label", {}) or {}).get("value")
    qid = hits[0]["id"]
    rec["qid"] = qid
    try:
        ent = _entity(qid)
    except Exception as exc:
        rec["error"] = f"entity fetch failed: {type(exc).__name__}"
        return rec
    claims = (ent.get("entities", {}).get(qid, {}) or {}).get("claims", {}) or {}

    # P856 equality, the check that already ships.
    p856 = claims.get("P856") or []
    host = None
    if p856:
        try:
            url = p856[0]["mainsnak"]["datavalue"]["value"]
            host = url.split("//")[-1].split("/")[0].lower()
            if host.startswith("www."):
                host = host[4:]
        except Exception:
            host = None
    rec["p856_host"] = host
    rec["p856_matches_domain"] = (host == domain.lower()) if host else False

    # Liveness: any death property means the entity no longer exists as itself.
    found = {}
    for prop, label in DEATH_PROPS.items():
        for cl in claims.get(prop) or []:
            try:
                dv = cl["mainsnak"]["datavalue"]["value"]
            except Exception:
                continue
            val = dv.get("time") if isinstance(dv, dict) and "time" in dv else (
                dv.get("id") if isinstance(dv, dict) else str(dv))
            found.setdefault(label, []).append(val)
    rec["death_signals"] = found
    rec["is_live"] = not found
    rec["verdict"] = (
        "USABLE" if rec["p856_matches_domain"] and rec["is_live"]
        else "REJECT: " + ("; ".join(
            [f"P856 host {host!r} != {domain!r}"] if not rec["p856_matches_domain"] else []
            + [f"{k} = {v}" for k, v in found.items()]) or "unknown")
    )
    return rec


def main():
    state = json.load(open(OUT)) if os.path.exists(OUT) else {"subjects": {}}
    for tag, domain, name in SUBJECTS:
        k = f"{tag}:{domain}"
        if k in state["subjects"]:
            r = state["subjects"][k]
        else:
            r = check(name, domain)
            state["subjects"][k] = r
            with open(OUT + ".tmp", "w") as fh:
                json.dump(state, fh, indent=1)
            os.replace(OUT + ".tmp", OUT)
        if r.get("error"):
            print(f"{tag:8s} {domain:22s} ERROR {r['error']}")
            continue
        print(f"{tag:8s} {domain:22s} qid={r.get('qid'):10s} "
              f"p856={str(r.get('p856_host')):24s} live={r['is_live']}  {r['verdict']}")
        if r["death_signals"]:
            for lab, vals in r["death_signals"].items():
                print(f"         -> {lab}: {vals}")
    print(f"\ncheckpoint: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
