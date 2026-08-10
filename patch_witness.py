"""Harden the confirmation layer: name the primary operator, wire real witnesses.

Why this patch exists
---------------------
The shipped gate required "at least one listed source confirms the answer" and
nothing more.  Resolving every confirming source to its controlling operator
showed that five of sixteen traps satisfied that rule with a source run by the
SAME operator that supplied the ranked collection:

    art        primary Metropolitan Museum of Art  confirmed by the Met
    education  primary Hipo Labs                   confirmed by Hipo Labs
    finance    primary US Department of the Treasury  confirmed by Treasury
    politics   primary US Office of the Federal Register  confirmed by OFR
    shopping   primary Open Food Facts             confirmed by Open Food Facts

A registry restating its own record is not a witness.  This patch:

  1. adds `primary_operator` to Candidate and threads it into the gate as R3c,
  2. sets the primary operator on all sixteen generators,
  3. wires the witnesses that the probes actually measured:
       art        Wikidata P217 scoped by P195 wd:Q160236 (Met collection)
       education  Wikidata P856 must print the SAME domain as the answer
       health     Europe PMC ACCESSION_ID search (EMBL-EBI)
       travel     Wikidata P238 exact-value lookup

The education change is deliberately fail-closed.  Waterford Institute of
Technology merged into South East Technological University in 2022 and ROR now
links setu.ie, so the register answer wit.ie may be a lag.  Requiring Wikidata
P856 to print the same domain means the generator refuses to ship rather than
serve a stale answer if the knowledge base moves first.
"""
from __future__ import annotations

import re
import sys

PRIMARY = {
    "science and technology": "Cornell University",
    "art": "Metropolitan Museum of Art",
    "business": "US Securities and Exchange Commission",
    "celebrities/public figures": "Nobel Prize Outreach",
    "education": "Hipo Labs",
    "finance": "US Department of the Treasury",
    "geography": "OurAirports",
    "health and medicine": "US National Institutes of Health",
    "history": "Nobel Prize Outreach",
    "legal": "Harvard Law School Library Innovation Lab",
    "politics": "US Office of the Federal Register",
    "shopping": "Open Food Facts",
    "sports": "Major League Baseball",
    "travel": "OpenFlights",
    "tv shows and movies": "IMDb (Amazon)",
    "video games": "Valve Corporation",
}

_CAT = re.compile(r'(\n(\s+)category="([^"]+)",)')


def add_primary(path):
    src = open(path).read()
    hits = []

    def sub(m):
        whole, indent, cat = m.group(1), m.group(2), m.group(3)
        if cat not in PRIMARY:
            return whole
        if 'primary_operator=' in src[m.end():m.end() + 400]:
            return whole
        hits.append(cat)
        return whole + f'\n{indent}primary_operator="{PRIMARY[cat]}",'

    out = _CAT.sub(sub, src)
    if out != src:
        open(path, "w").write(out)
    return hits


def repl(path, old, new, label, marker):
    """Idempotent replace. `marker` must be text unique to the NEW block.

    An earlier version of this patch guessed the marker from the first 60
    characters of the replacement, which for three of these edits is text the
    old block already contains, so the edit silently no-opped and left the
    generator referencing variables that were never defined. The marker is now
    explicit and is asserted to be absent from the pre-patch file.
    """
    src = open(path).read()
    if marker in src:
        print(f"  [skip] {label}: already applied")
        return
    if old not in src:
        print(f"  [MISS] {label}: anchor not found")
        sys.exit(1)
    assert marker in new, f"{label}: marker not in replacement text"
    open(path, "w").write(src.replace(old, new, 1))
    print(f"  [ok]   {label}")


# ------------------------------------------------------------------ helper
_HELPER = '''

def _wikidata_by_value_scoped(prop, value, scope_prop=None, scope_qid=None, limit=10):
    """Exact-value lookup, optionally scoped by a second property.

    Museum accession numbers are only locally unique: P217 "71.84" collides
    across institutions. Scoping by P195 (collection) to the owning museum
    makes the match identifying rather than merely suggestive.
    """
    scope = ""
    if scope_prop and scope_qid:
        scope = ' ; wdt:%s wd:%s' % (scope_prop, scope_qid)
    q = ('SELECT ?item ?itemLabel WHERE { ?item wdt:%s "%s"%s. '
         'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } '
         'LIMIT %d' % (prop, str(value).replace('"', ""), scope, limit))
    binds = net.wikidata_sparql(q).get("results", {}).get("bindings", [])
    return [{"qid": b["item"]["value"].rsplit("/", 1)[-1],
             "label": b.get("itemLabel", {}).get("value")} for b in binds]

'''

# --------------------------------------------------------------------- art
ART_OLD = '''    eu = net.get_json(
        "https://api.europeana.eu/record/v2/search.json?wskey=api2demo&rows=1&query="
        + artist.lower(), timeout=90)
    if not eu.get("items"):
        raise TrapUnavailable("art: Europeana corroboration unavailable")

    srcs = [f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{best['objectID']}",
            f"https://www.wikidata.org/w/index.php?search={artist}",
            "https://api.europeana.eu/record/v2/search.json?query=" + artist.lower()]'''

ART_NEW = '''    eu = net.get_json(
        "https://api.europeana.eu/record/v2/search.json?wskey=api2demo&rows=1&query="
        + artist.lower(), timeout=90)
    if not eu.get("items"):
        raise TrapUnavailable("art: Europeana corroboration unavailable")

    # WITNESS. The Met object record is the primary restating itself, so it is
    # not evidence. Wikidata P217 carries accession numbers, but they are only
    # locally unique, so the query is scoped by P195 to the Met collection
    # (Q160236). Measured on the primary seed: exactly one item, Q19905220.
    wd = _wikidata_by_value_scoped("P217", answer, "P195", "Q160236", limit=5)
    if len(wd) != 1:
        raise TrapUnavailable(
            f"art: Wikidata carries {len(wd)} items with Met accession number "
            f"{answer!r}; a witness must be unique, and the museum record "
            "cannot confirm itself")
    wq = wd[0]["qid"]

    srcs = [f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{best['objectID']}",
            f"https://www.wikidata.org/wiki/{wq}",
            "https://api.europeana.eu/record/v2/search.json?query=" + artist.lower()]'''

ART_CONF_OLD = '''        confirming_sources=[srcs[0]],
        api_proof_argument=(
            "The museum search endpoint returns only object identifiers; accession years are "'''
ART_CONF_NEW = '''        confirming_sources=[srcs[0], srcs[1]],
        api_proof_argument=(
            "The museum search endpoint returns only object identifiers; accession years are "'''

ART_CONFTXT_OLD = '''        confirmation=f"the object record reports accession year {best['accessionYear']}",
        facts={"artist": artist, "n": len(rows), "year": best["accessionYear"]},'''
ART_CONFTXT_NEW = '''        confirmation=(
            f"the object record reports accession year {best['accessionYear']}; "
            f"independently, Wikidata {wq} ({wd[0].get('label')!r}) carries P217 "
            f"{answer!r} scoped to the museum collection Q160236"),
        facts={"artist": artist, "n": len(rows), "year": best["accessionYear"],
               "witness_qid": wq, "witness_label": wd[0].get("label"),
               "witness_note": ("P217 alone is ambiguous across museums; the "
                                "P195 collection scope is what makes the match "
                                "identifying")},'''

# --------------------------------------------------------------- education
EDU_OLD = '''    inception, qid = _wikidata_value(best["name"], "P571", must_contain="universit")
    if not qid:
        raise TrapUnavailable(f"education: Wikidata could not resolve {best['name']}")'''

EDU_NEW = '''    inception, qid = _wikidata_value(best["name"], "P571", must_contain="universit")
    if not qid:
        raise TrapUnavailable(f"education: Wikidata could not resolve {best['name']}")

    # WITNESS. Resolving the institution is not confirming the ANSWER: the
    # answer is the domain. Require Wikidata P856 to print the same registrable
    # host. This is deliberately fail-closed against register lag -- Waterford
    # Institute of Technology merged into South East Technological University
    # in 2022 and ROR already links setu.ie, so if the knowledge base moves
    # first the generator refuses rather than serving a stale domain.
    site, _ = _wikidata_value(qid, "P856")
    site_host = re.sub(r"^https?://", "", str(site or "")).split("/")[0].lower()
    site_host = site_host[4:] if site_host.startswith("www.") else site_host
    if site_host != answer.lower():
        raise TrapUnavailable(
            f"education: Wikidata P856 for {qid} prints {site_host!r}, not the "
            f"register domain {answer!r}; the register is the primary and "
            "cannot confirm itself")'''

EDU_CONF_OLD = '''        confirming_sources=[srcs[0]],
        api_proof_argument=(
            "The register returns an unsorted national list and supports no ordering parameter, "'''
EDU_CONF_NEW = '''        confirming_sources=[srcs[1]],
        api_proof_argument=(
            "The register returns an unsorted national list and supports no ordering parameter, "'''

EDU_TXT_OLD = '''        confirmation=f"Wikidata {qid} resolves {best['name']}",
        facts={"country": country, "n": len(rows), "inst": best["name"]},'''
EDU_TXT_NEW = '''        confirmation=(f"Wikidata {qid} resolves {best['name']} and its P856 "
                      f"official website prints the same host {site_host!r}"),
        facts={"country": country, "n": len(rows), "inst": best["name"],
               "witness_qid": qid, "witness_p856": site,
               "stationarity_note": ("the institution merged into South East "
                                     "Technological University in 2022; the "
                                     "P856 equality check is what stops a "
                                     "register lag from shipping")},'''

# ------------------------------------------------------------------ health
HEALTH_OLD = '''    srcs = [url, f"https://www.wikidata.org/wiki/{qid}",
            "https://api.fda.gov/drug/label.json?search=" + condition.replace(" ", "+")]'''

HEALTH_NEW = '''    # SECOND WITNESS. Europe PMC is run by EMBL-EBI, a different operator from
    # both NIH (the registry) and Wikimedia, and indexes trial accessions
    # extracted from the literature rather than mirrored from the registry.
    epmc_url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
                f'query=ACCESSION_ID%3A%22{answer}%22&format=json&pageSize=3')
    epmc_hits, epmc_title = 0, None
    try:
        ej = net.get_json(epmc_url, timeout=90, attempts=3)
        epmc_hits = int(ej.get("hitCount") or 0)
        res = ((ej.get("resultList") or {}).get("result") or [])
        epmc_title = (res[0].get("title") if res else None)
    except Exception:  # noqa: BLE001
        epmc_hits = 0

    srcs = [url, f"https://www.wikidata.org/wiki/{qid}",
            "https://api.fda.gov/drug/label.json?search=" + condition.replace(" ", "+")]
    conf_srcs = [f"https://www.wikidata.org/wiki/{qid}"]
    if epmc_hits > 0:
        srcs.append(epmc_url)
        conf_srcs.append(epmc_url)
        conf += (f"; Europe PMC returns {epmc_hits} record(s) carrying accession "
                 f"{answer} ({str(epmc_title)[:70]!r})")'''

HEALTH_CONF_OLD = '''        confirming_sources=[f"https://www.wikidata.org/wiki/{qid}"],
        api_proof_argument=(
            "The registry filters by condition, phase and status but will not return the "'''
HEALTH_CONF_NEW = '''        confirming_sources=conf_srcs,
        api_proof_argument=(
            "The registry filters by condition, phase and status but will not return the "'''

HEALTH_FACTS_OLD = '''        facts={"condition": condition, "n": len(rows)},'''
HEALTH_FACTS_NEW = '''        facts={"condition": condition, "n": len(rows),
               "europepmc_hits": epmc_hits, "europepmc_title": epmc_title},'''

# ------------------------------------------------------------------ travel
TRAVEL_OLD = '''    srcs = [_OPENFLIGHTS_RT, _OURAIRPORTS,
            f"https://www.wikidata.org/w/index.php?search={answer}+airport"]'''

TRAVEL_NEW = '''    # SECOND WITNESS. Confirm by the ANSWER, not by the airport NAME: P238 is
    # the IATA code, so an exact-value match cannot collide on a shared label
    # the way the geography name lookup did. Require uniqueness.
    wd = _wikidata_by_value("P238", answer, limit=5)
    srcs = [_OPENFLIGHTS_RT, _OURAIRPORTS,
            f"https://www.wikidata.org/w/index.php?search={answer}+airport"]
    conf_srcs = [_OURAIRPORTS]
    wq = None
    if len(wd) == 1:
        wq = wd[0]["qid"]
        srcs[2] = f"https://www.wikidata.org/wiki/{wq}"
        conf_srcs.append(srcs[2])'''

TRAVEL_CONF_OLD = '''        confirming_sources=[_OURAIRPORTS],
        api_proof_argument=(
            "OpenFlights distributes routes and airports as two separate flat files with no "'''
TRAVEL_CONF_NEW = '''        confirming_sources=conf_srcs,
        api_proof_argument=(
            "OpenFlights distributes routes and airports as two separate flat files with no "'''

TRAVEL_TXT_OLD = '''        confirmation=f"OurAirports lists {answer} at latitude {match[0]['latitude_deg']}",
        facts={"airline": airline_iata, "hub": hub_iata, "n": len(base)},'''
TRAVEL_TXT_NEW = '''        confirmation=(f"OurAirports lists {answer} at latitude "
                      f"{match[0]['latitude_deg']}"
                      + (f"; Wikidata {wq} ({wd[0].get('label')!r}) carries P238 "
                         f"{answer!r} uniquely" if wq else
                         f"; Wikidata P238 returned {len(wd)} items for {answer!r}, "
                         "so no second witness is claimed")),
        facts={"airline": airline_iata, "hub": hub_iata, "n": len(base),
               "witness_qid": wq, "wikidata_p238_hits": len(wd)},'''


def main():
    print("adding primary_operator ...")
    for p in ("category_traps.py", "gen_v2.py"):
        got = add_primary(p)
        print(f"  {p}: {len(got)} generator(s) -> {sorted(got)}")

    src = open("category_traps.py").read()
    if "_wikidata_by_value_scoped" not in src:
        anchor = "\ndef _wikidata_item_labels("
        i = src.index(anchor)
        open("category_traps.py", "w").write(src[:i] + _HELPER + src[i:])
        print("  [ok]   _wikidata_by_value_scoped helper")

    print("wiring witnesses ...")
    P = "category_traps.py"
    repl(P, ART_OLD, ART_NEW, "art / wikidata P217+P195",
         '_wikidata_by_value_scoped("P217"')
    repl(P, ART_CONF_OLD, ART_CONF_NEW, "art / confirming_sources",
         "confirming_sources=[srcs[0], srcs[1]],")
    repl(P, ART_CONFTXT_OLD, ART_CONFTXT_NEW, "art / confirmation text",
         '"witness_qid": wq, "witness_label"')
    repl(P, EDU_OLD, EDU_NEW, "education / wikidata P856 equality",
         'site, _ = _wikidata_value(qid, "P856")')
    repl(P, EDU_CONF_OLD, EDU_CONF_NEW, "education / confirming_sources",
         "confirming_sources=[srcs[1]],")
    repl(P, EDU_TXT_OLD, EDU_TXT_NEW, "education / confirmation text",
         '"stationarity_note"')
    repl(P, HEALTH_OLD, HEALTH_NEW, "health / Europe PMC", "epmc_url = (")
    repl(P, HEALTH_CONF_OLD, HEALTH_CONF_NEW, "health / confirming_sources",
         'confirming_sources=conf_srcs,\n        api_proof_argument=(\n            "The registry filters')
    repl(P, HEALTH_FACTS_OLD, HEALTH_FACTS_NEW, "health / facts", '"europepmc_hits"')
    repl(P, TRAVEL_OLD, TRAVEL_NEW, "travel / wikidata P238",
         '_wikidata_by_value("P238", answer, limit=5)')
    repl(P, TRAVEL_CONF_OLD, TRAVEL_CONF_NEW, "travel / confirming_sources",
         'confirming_sources=conf_srcs,\n        api_proof_argument=(\n            "OpenFlights')
    repl(P, TRAVEL_TXT_OLD, TRAVEL_TXT_NEW, "travel / confirmation text",
         '"wikidata_p238_hits"')
    print("done")


if __name__ == "__main__":
    main()
