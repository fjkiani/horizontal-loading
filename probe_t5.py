"""Test candidate INDEPENDENT second confirming operators, one per category.

Five categories currently pass the confirming-source gate tautologically: the
confirming source resolves to the same operator as the primary source (Open
Food Facts confirms Open Food Facts, the Met confirms the Met, Hipo Labs
confirms Hipo Labs, the Federal Register confirms the Federal Register, the
Treasury confirms the Treasury). A source restating itself is not a witness.

This probe tests, for each category, whether a genuinely independent operator
can be made to confirm the ANSWER. Nothing is wired until it is measured, and
a category that has no independent witness is reported as single-witness
rather than given a fabricated one.
"""
from __future__ import annotations

import json
import os
import traceback

import category_traps as ct
import net

OUT = "probe_t5.json"
R = {}


def save():
    json.dump(R, open(OUT + ".tmp", "w"), indent=2, default=str)
    os.replace(OUT + ".tmp", OUT)


def step(name, fn):
    try:
        R[name] = fn()
    except Exception as exc:  # noqa: BLE001
        R[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                   "tb": traceback.format_exc()[-600:]}
    save()
    v = R[name]
    print(f"[{name:14s}] ok={v.get('ok')} {json.dumps({k: x for k, x in v.items() if k not in ('tb',)}, default=str)[:340]}")


# ------------------------------------------------------------------- art 71.84
def art():
    """Wikidata P217 (inventory number) scoped to the Met collection (Q160236)."""
    q = ('SELECT ?item ?itemLabel WHERE { ?item wdt:P217 "71.84"; wdt:P195 wd:Q160236. '
         'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } LIMIT 5')
    b = net.wikidata_sparql(q).get("results", {}).get("bindings", [])
    hits = [{"qid": x["item"]["value"].rsplit("/", 1)[-1],
             "label": x.get("itemLabel", {}).get("value")} for x in b]
    # unscoped, to see how ambiguous a bare accession number is
    q2 = 'SELECT (COUNT(?i) AS ?n) WHERE { ?i wdt:P217 "71.84" }'
    b2 = net.wikidata_sparql(q2).get("results", {}).get("bindings", [])
    n_all = int(b2[0]["n"]["value"]) if b2 else None
    return {"ok": len(hits) == 1, "operator": "Wikimedia Foundation",
            "met_scoped_hits": hits, "unscoped_count_same_accession": n_all,
            "note": "P217 alone is ambiguous across museums; P195 scoping is required"}


# ------------------------------------------------------------ education wit.ie
def education():
    """ROR is run by a separate registry community; it publishes official links."""
    js = net.get_json("https://api.ror.org/v2/organizations?query="
                      + "Waterford%20Institute%20of%20Technology", timeout=90)
    items = js.get("items") or []
    found = []
    for it in items[:5]:
        links = [l.get("value") for l in (it.get("links") or [])]
        names = [n.get("value") for n in (it.get("names") or [])]
        found.append({"id": it.get("id"), "names": names[:2], "links": links})
    hit = [f for f in found if any("wit.ie" in (l or "") for l in f["links"])]
    return {"ok": bool(hit), "operator": "Research Organization Registry",
            "n_items": len(items), "matches": hit or found[:2]}


# ------------------------------------------------------ health NCT00021697
def health():
    """Europe PMC (EMBL-EBI) indexes trial accessions independently of NIH."""
    js = net.get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
                      "query=ACCESSION_ID%3A%22NCT00021697%22&format=json&pageSize=3",
                      timeout=90)
    rs = js.get("resultList", {}).get("result", [])
    return {"ok": int(js.get("hitCount", 0)) > 0, "operator": "EMBL-EBI",
            "hit_count": js.get("hitCount"),
            "titles": [r.get("title", "")[:90] for r in rs]}


# ------------------------------------------------------------------ travel IVL
def travel():
    hits = ct._wikidata_by_value("P238", "IVL")
    return {"ok": len(hits) == 1, "operator": "Wikimedia Foundation", "hits": hits}


# ------------------------------------------------- shopping 7613036900096
def shopping():
    """UPCitemdb is an independent barcode registry."""
    js = net.get_json("https://api.upcitemdb.com/prod/trial/lookup?upc=7613036900096",
                      timeout=90)
    items = js.get("items") or []
    return {"ok": bool(items), "operator": "UPCitemdb", "code": js.get("code"),
            "total": js.get("total"),
            "titles": [i.get("title", "")[:80] for i in items[:3]],
            "brands": [i.get("brand") for i in items[:3]]}


# ------------------------------------------------------------- politics 13292
def politics():
    """GovInfo (GPO) prints the bound Executive Order text; OFR runs the FR API."""
    js = net.get_json("https://api.govinfo.gov/search?api_key=DEMO_KEY", timeout=60) \
        if False else None
    # no key needed for the public link service
    out = {}
    url = "https://www.govinfo.gov/link/cpd/executiveorder/13292?link-type=json"
    try:
        js = net.get_json(url, timeout=90)
        out["link_service"] = js if isinstance(js, dict) else str(js)[:300]
    except Exception as exc:  # noqa: BLE001
        out["link_service_error"] = f"{type(exc).__name__}: {exc}"
    ok = bool(out.get("link_service"))
    return {"ok": ok, "operator": "US Government Publishing Office", **out}


# ---------------------------------------------------------- tv tt1278381
def tv():
    """TVmaze indexes by IMDb id; test whether it resolves a feature film."""
    out = {}
    try:
        js = net.get_json("https://api.tvmaze.com/lookup/shows?imdb=tt1278381", timeout=60)
        out["tvmaze"] = {"id": js.get("id"), "name": js.get("name")}
    except Exception as exc:  # noqa: BLE001
        out["tvmaze_error"] = f"{type(exc).__name__}: {exc}"
    try:
        js2 = net.get_json("https://api.wikimedia.org/core/v1/wikipedia/en/search/page?"
                           "q=tt1278381&limit=1", timeout=60)
        out["wikimedia_search_n"] = len(js2.get("pages") or [])
    except Exception as exc:  # noqa: BLE001
        out["wikimedia_error"] = f"{type(exc).__name__}: {exc}"
    return {"ok": bool(out.get("tvmaze", {}).get("id")),
            "operator": "TVmaze", **out}


# ------------------------------------------------- celebrities Leiden / laureate
def celebrities():
    """VIAF (OCLC) is an independent authority file. Does it carry birthplace?"""
    out = {}
    try:
        js = net.get_json("https://viaf.org/api/search?"
                          "query=local.personalNames%20all%20%22van%20der%20Waals%2C%20"
                          "Johannes%20Diderik%22&maximumRecords=2&httpAccept="
                          "application%2Fjson", timeout=90)
        out["viaf_raw_keys"] = list(js.keys())[:8] if isinstance(js, dict) else None
        out["viaf_snippet"] = json.dumps(js)[:400]
    except Exception as exc:  # noqa: BLE001
        out["viaf_error"] = f"{type(exc).__name__}: {exc}"
    return {"ok": False, "operator": "OCLC (VIAF)",
            "note": "birthplace is not an authority-file field; expected to fail",
            **out}


# --------------------------------------------------------- history 1975 physics
def history():
    """Crossref indexes the Nobel lecture, published the year after the prize."""
    js = net.get_json("https://api.crossref.org/works?query.bibliographic="
                      "Aage+Bohr+Nobel+Lecture+rotational+motion&rows=3", timeout=90)
    items = js.get("message", {}).get("items", [])
    rows = [{"title": (i.get("title") or [""])[0][:80],
             "year": ((i.get("issued") or {}).get("date-parts") or [[None]])[0][0],
             "doi": i.get("DOI"),
             "container": (i.get("container-title") or [""])[0][:40]} for i in items]
    return {"ok": bool(rows), "operator": "Crossref", "rows": rows,
            "note": "confirms the laureate and the lecture, not the award year directly"}


if __name__ == "__main__":
    for nm, fn in [("art", art), ("education", education), ("health", health),
                   ("travel", travel), ("shopping", shopping),
                   ("politics", politics), ("tv", tv),
                   ("celebrities", celebrities), ("history", history)]:
        step(nm, fn)
    print("\nwrote", OUT)
