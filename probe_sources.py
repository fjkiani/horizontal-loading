"""Feasibility probe for non-banned source families, one per Seal category.

Planning-phase only: checks reachability and auth requirements. Does NOT build
traps or extract answers. Checkpoints after every probe so an interrupt loses
at most one endpoint.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "source_probe.json")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

UA = "Mozilla/5.0 (compatible; SealSourceProbe/1.0; +research)"

# category -> list of (operator, domain, probe_url, needs_key)
CANDIDATES = {
    "science and technology": [
        ("arXiv", "export.arxiv.org",
         "http://export.arxiv.org/api/query?search_query=all:electron&max_results=1", False),
        ("USPTO PatentsView", "search.patentsview.org",
         "https://search.patentsview.org/api/v1/patent/?q=%7B%22patent_id%22:%2210000000%22%7D", True),
        ("NASA TechPort", "techport.nasa.gov",
         "https://techport.nasa.gov/api/projects?limit=1", False),
    ],
    "art": [
        ("Metropolitan Museum", "collectionapi.metmuseum.org",
         "https://collectionapi.metmuseum.org/public/collection/v1/objects/45734", False),
        ("Art Institute of Chicago", "api.artic.edu",
         "https://api.artic.edu/api/v1/artworks/27992", False),
        ("Cleveland Museum of Art", "openaccess-api.clevelandart.org",
         "https://openaccess-api.clevelandart.org/api/artworks/?limit=1", False),
    ],
    "business": [
        ("SEC EDGAR", "data.sec.gov",
         "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Assets.json", False),
    ],
    "celebrities/public figures": [
        ("Wikidata", "wikidata.org",
         "https://www.wikidata.org/w/api.php?action=wbsearchentities&search=Marie%20Curie&language=en&format=json", False),
        ("Nobel Prize", "api.nobelprize.org",
         "https://api.nobelprize.org/2.1/laureates?limit=1", False),
    ],
    "education": [
        ("College Scorecard", "api.data.gov",
         "https://api.data.gov/ed/collegescorecard/v1/schools?per_page=1", True),
        ("NCES IPEDS (bulk)", "nces.ed.gov",
         "https://nces.ed.gov/ipeds/datacenter/data/HD2022.zip", False),
    ],
    "finance": [
        ("US Treasury FiscalData", "api.fiscaldata.treasury.gov",
         "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny?page[size]=1", False),
        ("SEC EDGAR", "data.sec.gov",
         "https://data.sec.gov/api/xbrl/companyconcept/CIK0000789019/us-gaap/Revenues.json", False),
    ],
    "geography": [
        ("REST Countries", "restcountries.com",
         "https://restcountries.com/v3.1/alpha/nz", False),
        ("USGS GNIS/Water", "waterservices.usgs.gov",
         "https://waterservices.usgs.gov/nwis/site/?format=rdb&sites=01646500", False),
        ("OurAirports", "davidmegginson.github.io",
         "https://davidmegginson.github.io/ourairports-data/airports.csv", False),
    ],
    "health and medicine": [
        ("NIH RePORTER", "api.reporter.nih.gov", "https://api.reporter.nih.gov/v2/projects/search", False),
        ("ClinicalTrials.gov", "clinicaltrials.gov",
         "https://clinicaltrials.gov/api/v2/studies?pageSize=1", False),
        ("openFDA", "api.fda.gov",
         "https://api.fda.gov/drug/label.json?limit=1", False),
        ("PubMed E-utilities", "eutils.ncbi.nlm.nih.gov",
         "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=1&retmode=json", False),
    ],
    "history": [
        ("Nobel Prize", "api.nobelprize.org",
         "https://api.nobelprize.org/2.1/nobelPrizes?nobelPrizeYear=1901", False),
        ("Federal Register", "federalregister.gov",
         "https://www.federalregister.gov/api/v1/documents.json?per_page=1", False),
    ],
    "legal": [
        ("CourtListener", "courtlistener.com",
         "https://www.courtlistener.com/api/rest/v4/courts/?page_size=1", True),
        ("Federal Register", "federalregister.gov",
         "https://www.federalregister.gov/api/v1/documents.json?per_page=1", False),
        ("GovInfo", "api.govinfo.gov",
         "https://api.govinfo.gov/collections", True),
    ],
    "politics": [
        ("Congress.gov", "api.congress.gov",
         "https://api.congress.gov/v3/bill?limit=1", True),
        ("FEC", "api.open.fec.gov",
         "https://api.open.fec.gov/v1/candidates/?per_page=1", True),
        ("Federal Register", "federalregister.gov",
         "https://www.federalregister.gov/api/v1/agencies", False),
    ],
    "shopping": [
        ("Open Food Facts", "world.openfoodfacts.org",
         "https://world.openfoodfacts.org/api/v2/product/737628064502.json", False),
        ("Open Library", "openlibrary.org",
         "https://openlibrary.org/isbn/9780140328721.json", False),
    ],
    "sports": [
        ("Olympedia via Wikidata", "query.wikidata.org",
         "https://query.wikidata.org/sparql?format=json&query=SELECT%20?s%20WHERE%20%7B?s%20wdt:P31%20wd:Q18536594%7D%20LIMIT%201", False),
        ("TheSportsDB", "www.thesportsdb.com",
         "https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t=Arsenal", False),
        ("balldontlie", "api.balldontlie.io",
         "https://api.balldontlie.io/v1/teams?per_page=1", True),
    ],
    "travel": [
        ("OurAirports", "davidmegginson.github.io",
         "https://davidmegginson.github.io/ourairports-data/airports.csv", False),
        ("US BTS / FAA NASR", "nfdc.faa.gov",
         "https://nfdc.faa.gov/webContent/28DaySub/28DaySubscription_Effective_2024-01-25.zip", False),
        ("OpenFlights", "raw.githubusercontent.com",
         "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat", False),
    ],
    "tv shows and movies": [
        ("TVmaze", "api.tvmaze.com",
         "https://api.tvmaze.com/shows/1", False),
        ("Wikidata", "query.wikidata.org",
         "https://query.wikidata.org/sparql?format=json&query=SELECT%20?s%20WHERE%20%7B?s%20wdt:P31%20wd:Q5398426%7D%20LIMIT%201", False),
        ("TMDB", "api.themoviedb.org",
         "https://api.themoviedb.org/3/movie/550", True),
    ],
    "video games": [
        ("MobyGames", "api.mobygames.com",
         "https://api.mobygames.com/v1/games?limit=1", True),
        ("RAWG", "api.rawg.io",
         "https://api.rawg.io/api/games?page_size=1", True),
        ("Wikidata", "query.wikidata.org",
         "https://query.wikidata.org/sparql?format=json&query=SELECT%20?s%20WHERE%20%7B?s%20wdt:P31%20wd:Q7889%7D%20LIMIT%201", False),
    ],
}

BANNED = ["archive.org", "loc.gov", "hathitrust.org", "sports-reference",
          "baseball-reference.com", "basketball-reference.com",
          "pro-football-reference.com", "hockey-reference.com",
          "fbref.com", "stathead.com"]


def probe(url, timeout=20):
    """Return (status, note, n_bytes). Never raises."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            body = r.read(4096)
            return r.status, "ok", len(body)
    except urllib.error.HTTPError as e:
        try:
            snippet = e.read(300).decode("utf-8", "replace")
        except Exception:
            snippet = ""
        return e.code, snippet.replace("\n", " ")[:180], 0
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:150]}", 0


def main():
    results = {}
    if os.path.exists(OUT):
        results = json.load(open(OUT))

    for cat, cands in CANDIDATES.items():
        results.setdefault(cat, [])
        done = {r["operator"] for r in results[cat]}
        for operator, domain, url, needs_key in cands:
            if operator in done:
                continue
            assert not any(b in domain for b in BANNED), f"banned domain in candidate: {domain}"
            t0 = time.time()
            status, note, nbytes = probe(url)
            rec = {
                "operator": operator,
                "domain": domain,
                "url": url,
                "needs_key_expected": needs_key,
                "status": status,
                "note": note,
                "bytes": nbytes,
                "elapsed_s": round(time.time() - t0, 2),
                "usable_keyless": bool(status == 200),
            }
            results[cat].append(rec)
            json.dump(results, open(OUT, "w"), indent=1)
            flag = "OK " if rec["usable_keyless"] else "-- "
            print(f"{flag}{cat:28s} {operator:24s} status={status} {note[:60]}")
            sys.stdout.flush()

    print("\n=== keyless coverage by category ===")
    n_ok = 0
    for cat in CANDIDATES:
        ok = [r for r in results.get(cat, []) if r["usable_keyless"]]
        ops = ", ".join(r["operator"] for r in ok) or "NONE"
        if ok:
            n_ok += 1
        print(f"{len(ok)}  {cat:28s} {ops}")
    print(f"\ncategories with >=1 keyless operator: {n_ok}/16")
    print(f"categories with >=2 keyless operators: "
          f"{sum(1 for c in CANDIDATES if len([r for r in results.get(c, []) if r['usable_keyless']]) >= 2)}/16")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
