"""Replace memorable ANSWER FIELDS with opaque, independently-resolvable identifiers.

The expansion sweep made the fault plain. Four categories answer with an
ATTRIBUTE of the winning entity:

    celebrities  city of birth   -> Leiden, Berlin, Bern, Garding, Paris
    sports       city of birth   -> Newton, Houston, Tuscaloosa, Sellersville
    history      award year      -> 1975, 2000, 1974, 1975   (and it repeats)
    video games  developing studio -> Colossal Order, Gearbox Software

Berlin and Paris are worse than Leiden. Once a solver identifies the entity it
recalls the attribute for free, so the ranking never binds. The other ten
categories answer with an identifier -- arXiv id, NCT id, CIK, ICAO, IATA, tt id,
US Reports page, EO number, accession number, domain -- which cannot be recalled
even when the entity is known.

So the fix is not a better ranking, it is a better FIELD. This probe asks, for the
entities those four categories actually select: which opaque identifier does
Wikidata carry, and does an operator that is NOT the primary source resolve it
back to the same entity? An identifier nobody independent can resolve is no better
than an attribute, because it cannot be witnessed.

Checkpoints per entity.
"""
import json
import os
import re
import sys
import time

import net

OUT = os.environ.get("FIELD_OUT", "field_redesign.json")

# prop -> (human field name, resolver URL template, operator, regex the resolved
#          document must satisfy to count as a confirmation)
IDS = {
    "P213": ("ISNI", "https://isni.org/isni/{v}", "ISNI International Agency"),
    "P214": ("VIAF identifier", "https://viaf.org/viaf/{v}/viaf.json", "OCLC"),
    "P496": ("ORCID iD", "https://pub.orcid.org/v3.0/{v}/person", "ORCID"),
    "P1957": ("Retrosheet player identifier", "https://www.retrosheet.org/boxesetc/{a}/P{v}.htm", "Retrosheet"),
    "P2002": (None, None, None),
}

# The entities each broken category actually selected, across the seed grid.
TARGETS = {
    "celebrities/public figures": [
        "Johannes Diderik van der Waals", "Max Born", "Emil Theodor Kocher",
        "Theodor Mommsen", "Fredrik Bajer",
    ],
    "sports": [
        "Jim Rice", "Ben Zobrist", "Juan Marichal", "Bob Feller",
    ],
    "history": [
        "Aage Bohr", "Alan MacDiarmid", "Albert Claude",
    ],
    "video games": [
        "Colossal Order", "Gearbox Software",
    ],
}


def wd_entity(name):
    hits = (net.wikidata_search(name) or {}).get("search") or []
    if not hits:
        return None, None
    qid = hits[0]["id"]
    ent = net.wikidata_entity(qid)
    return qid, ((ent.get("entities") or {}).get(qid) or {})


def claims_of(ent, prop):
    out = []
    for c in (ent.get("claims") or {}).get(prop, []):
        dv = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(dv, str):
            out.append(dv)
    return out


def resolves(prop, val, name):
    """Does an independent registry resolve the identifier back to this entity?"""
    fname, tmpl, op = IDS[prop][0], IDS[prop][1], IDS[prop][2]
    if not tmpl:
        return None
    url = tmpl.format(v=val.replace(" ", ""), a=val[:2] if val else "")
    try:
        doc = net.fetch(url, timeout=45, attempts=2)
    except Exception as exc:
        return {"url": url, "operator": op, "ok": False, "err": f"{type(exc).__name__}"}
    # surname is the discriminating token; initials and given names collide
    surname = re.sub(r"[^A-Za-z]", "", name.split()[-1]).lower()
    hit = surname in re.sub(r"[^A-Za-z]", "", doc).lower()
    return {"url": url, "operator": op, "ok": bool(hit), "bytes": len(doc)}


def main():
    state = json.load(open(OUT)) if os.path.exists(OUT) else {"entities": {}}
    for cat, names in TARGETS.items():
        for name in names:
            key = f"{cat}||{name}"
            if key in state["entities"]:
                print(f"skip {name}")
                continue
            rec = {"category": cat, "name": name, "ids": {}}
            try:
                qid, ent = wd_entity(name)
                rec["qid"] = qid
                if ent:
                    for prop in IDS:
                        if IDS[prop][1] is None:
                            continue
                        vals = claims_of(ent, prop)
                        if not vals:
                            continue
                        rec["ids"][prop] = {
                            "field": IDS[prop][0], "values": vals,
                            "n_values": len(vals),
                            "resolve": resolves(prop, vals[0], name),
                        }
                        time.sleep(1.0)
            except Exception as exc:
                rec["error"] = f"{type(exc).__name__}: {exc}"
            state["entities"][key] = rec
            with open(OUT + ".tmp", "w") as fh:
                json.dump(state, fh, indent=1)
            os.replace(OUT + ".tmp", OUT)
            got = {p: (v["resolve"] or {}).get("ok") for p, v in rec["ids"].items()}
            print(f"{cat:26s} {name:32s} {rec.get('qid')} {got}")
            time.sleep(1.0)

    # Which identifier is available AND independently witnessable per category?
    print("\n=== usable identifier fields by category ===")
    by_cat = {}
    for rec in state["entities"].values():
        c = rec["category"]
        for prop, v in rec.get("ids", {}).items():
            ok = (v.get("resolve") or {}).get("ok")
            single = v["n_values"] == 1
            by_cat.setdefault(c, {}).setdefault(prop, []).append(bool(ok and single))
    for c, props in by_cat.items():
        for p, oks in props.items():
            print(f"{c:26s} {IDS[p][0]:30s} witnessed {sum(oks)}/{len(oks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
