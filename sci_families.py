"""Four mutually disjoint science-and-technology generator families.

WHY THIS MODULE EXISTS
----------------------
The deployed pool advertised four science-and-technology prompts. Measured, they
were four SEEDS OF ONE TEMPLATE: pairwise textual similarity 0.980-0.993, one
primary operator (Cornell University / arXiv), one witness pair (DataCite,
OpenAlex), one ranking key (author count). Cross-category pairs measured
0.016-0.043 similar, so the four were clones under any threshold placed in the
empty 0.043-0.947 band. Solving one solved all four.

The fix is not new seeds. It is four families sharing NO operator, NO source
domain and NO ranking key, so success on one carries no information about the
others.

         family            primary            witnesses               ranking key
  -----------------------------------------------------------------------------
  f1  vulnerabilities   NIST (NVD)        MITRE, FIRST            reference count
  f2  standards         RFC Editor        Crossref, Sem. Scholar  page count
  f3  supply chain      PSF (PyPI)        Google deps.dev,        artefact count
                                          ecosyste.ms
  f4  internet numbers  RIPE NCC          PeeringDB, CAIDA        announced prefixes

Twelve distinct operators, zero shared registrable domains, four different keys
and four different collection shapes (a UTC publication day, a calendar month,
one project's release history, one country's registry roster).

WHAT THE PROBES CHANGED -- every design here was falsifiable, and three were
falsified before a line of this shipped
--------------------------------------------------------------------------
* ARIN was the specified family-4 witness and is DISPROVED.
  rdap.arin.net/registry/autnum/3333 redirects to rdap.db.ripe.net and returns
  RIPE's own record, so for a RIPE-region ASN it is the primary restating itself
  under a second brand (arin.net does serve 7018 itself, so the echo is
  region-dependent, not a blanket property of ARIN). Replaced by CAIDA, whose AS
  Rank is computed from its own BGP collection. source_gate.echo_violations now
  catches this whole class mechanically.
* GitHub was the specified family-1 witness and is DROPPED.
  api.github.com/advisories?cve_id=CVE-2023-34095 returns [], so advisory
  coverage cannot witness an arbitrary CVE. FIRST covers every published CVE and
  models EPSS itself.
* Software Heritage was the specified family-3 witness and is DROPPED. Probed, it
  binds a project ORIGIN and not a release, so it cannot see the answer. An
  operator that cannot see the answer is not a witness however independent it is.
  ClearlyDefined was tried as a replacement and timed out; ecosyste.ms answered
  every query and correctly REJECTED fabricated versions.
* Ranking family 3 by artefact count over pure-Python packages FAILED outright:
  the modern default is one sdist plus one wheel, so flask tied 46 ways, rich
  tied 207 ways, and 8 of 10 packages were unusable. The key survives only on
  projects shipping a compiled wheel matrix (7 of 15 isolate a unique argmax).

HONEST LIMITS
-------------
Witness independence is GRADED, not asserted -- see source_gate.grade_witnesses.
Crossref holds a record the RFC Editor DEPOSITED (publisher "RFC Editor", member
7045), so family 2's weakest witness tier is "deposit", recorded on the trap
rather than hidden behind a witness count of two. Family 1 has the same shape
elsewhere: NVD ingests the CVE List from MITRE, so the two can never disagree
that a CVE EXISTS. What they can disagree about is the identifier-to-description
binding, which is what the confirmation actually tests.

Nothing here measures DIFFICULTY. These generators produce well-posed,
source-backed items; whether a model finds them hard is a separate measurement
this module does not make and does not claim.
"""
from __future__ import annotations

import json
import re
import statistics
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

import category_traps as ct
import net
from category_traps import (Candidate, TrapUnavailable, build_prompt, _norm,
                            _pick_extreme)

CATEGORY = "science and technology"


def _rank_extra(**kw):
    """Attach family-specific ranking evidence after _pick_extreme rebinds it."""
    ct.LAST_RANK.update(kw)


def _gj(url, timeout=90, attempts=3):
    raw = net.fetch(url, timeout=timeout, attempts=attempts, base_sleep=2.0)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return json.loads(raw)


def _tokens(s):
    """Lowercase alphanumeric tokens, for reconciling naming variants."""
    return {t for t in re.split(r"[^a-z0-9]+", str(s or "").lower()) if len(t) > 2}


# ==========================================================================
# FAMILY 1 -- SOFTWARE VULNERABILITIES
# NIST (NVD) x MITRE (CVE Program) x FIRST (EPSS)
# ==========================================================================
# Collection: every CVE record the NVD stamps with one UTC publication day.
# Key:        number of external reference links on the record.
# Answer:     the CVE identifier.
#
# The NVD 2.0 API accepts no sort parameter at all and offers no reference-count
# filter, so the whole day must be pulled and counted. Probed over six days:
# five isolated a unique argmax (12 vs 10, 92 vs 15, 14 vs 13, 68 vs 44,
# 15 vs 12); one tied three ways at 14, which _pick_extreme refuses rather than
# breaking with a tiebreak the prompt never states.
_NVD_DAYS = ("2023-06-14", "2023-09-12", "2022-11-08", "2021-07-13",
             "2023-03-14", "2022-04-12", "2021-11-09", "2024-06-11",
             # spread across years and weekdays so the roster is not a run of
             # Patch Tuesdays, whose volume and vendor mix are atypical
             "2021-02-17", "2021-05-05", "2021-08-25", "2022-01-19",
             "2022-06-22", "2022-09-07", "2023-01-25", "2023-04-19",
             "2023-08-16", "2023-11-01", "2024-03-06", "2024-08-21",
             "2024-10-16", "2025-02-05", "2025-05-14", "2025-07-23")

# Unauthenticated NVD allows 5 requests per rolling 30 s.
_NVD_PACE = 7.0


def gen_sci_vulnerability(days=_NVD_DAYS):
    """NVD publication day x MITRE description x FIRST EPSS."""
    tried = []
    for day in days:
        url = ("https://services.nvd.nist.gov/rest/json/cves/2.0"
               f"?pubStartDate={day}T00:00:00.000&pubEndDate={day}T23:59:59.999"
               "&resultsPerPage=2000")
        try:
            j = _gj(url, timeout=120)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{day}: fetch {type(e).__name__}")
            time.sleep(_NVD_PACE)
            continue
        total = j.get("totalResults")
        rows = []
        for v in j.get("vulnerabilities") or []:
            c = (v or {}).get("cve") or {}
            cid = c.get("id")
            if not cid:
                continue
            desc = next((d.get("value") for d in (c.get("descriptions") or [])
                         if d.get("lang") == "en"), "")
            rows.append({"id": cid, "nref": len(c.get("references") or []),
                         "desc": desc})
        # resultsPerPage caps at 2000. If the day did not fit one page the
        # collection is not fully enumerated and "more than any other" is unproven.
        if total is None or len(rows) != total:
            tried.append(f"{day}: not fully enumerated ({len(rows)}/{total})")
            time.sleep(_NVD_PACE)
            continue
        if not (12 <= len(rows) <= 1900):
            tried.append(f"{day}: n={len(rows)}")
            time.sleep(_NVD_PACE)
            continue
        try:
            best = _pick_extreme(rows, lambda r: r["nref"], f"vuln {day}",
                                 mode="max", valuefn=lambda r: r["id"])
            _rank_extra(
                key_component_depths={}, key_is_aggregated=False,
                equivalent_served_fields=[], n_fields_swept=0,
                sort_field_rejected_by_service=[
                    "the NVD 2.0 API exposes no sort parameter",
                    "no reference-count filter exists"],
                n_true=total, page_cap=2000, pages_fetched=1)
        except TrapUnavailable as te:
            tried.append(str(te))
            time.sleep(_NVD_PACE)
            continue

        cve = best["id"]
        try:
            mit = _gj(f"https://cveawg.mitre.org/api/cve/{cve}", timeout=60)
            cna = ((mit or {}).get("containers") or {}).get("cna") or {}
            mit_desc = next((d.get("value") for d in (cna.get("descriptions") or [])), "")
            eps = _gj(f"https://api.first.org/data/v1/epss?cve={cve}", timeout=60)
            row = ((eps or {}).get("data") or [{}])[0]
        except Exception as e:  # noqa: BLE001
            tried.append(f"{day}: confirm {type(e).__name__}")
            time.sleep(_NVD_PACE)
            continue

        # BINDING TEST, not an existence test. NVD ingests the CVE List from
        # MITRE, so the two cannot disagree that a CVE exists and confirming
        # existence would be vacuous. What is tested is that THIS identifier
        # carries THIS description at MITRE: a wrong id resolves to different
        # prose and fails here. A fabricated id fails harder -- probed,
        # CVE-2023-99999 is rejected by MITRE rather than echoed back.
        if _norm(mit_desc)[:60] != _norm(best["desc"])[:60]:
            tried.append(f"{day}: MITRE description mismatch for {cve}")
            time.sleep(_NVD_PACE)
            continue
        if str(row.get("cve")) != cve or row.get("epss") in (None, ""):
            tried.append(f"{day}: FIRST publishes no EPSS row for {cve}")
            time.sleep(_NVD_PACE)
            continue

        srcs = [url,
                f"https://cveawg.mitre.org/api/cve/{cve}",
                f"https://api.first.org/data/v1/epss?cve={cve}"]
        return Candidate(
            category=CATEGORY,
            primary_operator="US National Institute of Standards and Technology",
            field="CVE identifier",
            answer=cve,
            entity=(best["desc"][:120] or cve),
            n_base=len(rows),
            sources=srcs,
            confirming_sources=srcs[1:],
            api_proof_argument=(
                "The National Vulnerability Database interface accepts no sort "
                "parameter and offers no filter on how many references a record "
                f"carries, so every one of the {len(rows)} records published "
                "that day must be retrieved and its reference list counted."),
            confirmation=(
                f"MITRE resolves {cve} to the same description text, and FIRST "
                f"publishes an exploit-prediction score of {row.get('epss')} for "
                "it; MITRE operates the CVE Program and FIRST models exploit "
                "probability independently of both"),
            facts={"day": day, "n": len(rows), "references": best["nref"],
                   "epss": row.get("epss"), "epss_percentile": row.get("percentile"),
                   "runner_up_references": (sorted((r["nref"] for r in rows),
                                                   reverse=True) + [None])[1],
                   "landing_pages": [
                       f"https://nvd.nist.gov/vuln/detail/{cve}",
                       f"https://www.cve.org/CVERecord?id={cve}"]},
            prompt=build_prompt(
                "The National Vulnerability Database operated by the United "
                "States National Institute of Standards and Technology stamps "
                "every vulnerability record it publishes with a publication date "
                "and lists the external reference links attached to that record.",
                "Consider only the records that database stamps as published on "
                f"{day} in Coordinated Universal Time.",
                "Exactly one of those records carries more external reference "
                "links than any other record published that day.",
                "Report the CVE identifier of that single record.",
                "Give the identifier alone, in the form CVE-YYYY-NNNNN, with no "
                "other words.",
                note="Confirm the identifier against the CVE Program record and "
                     "an exploit-prediction listing before answering."),
        )
    raise TrapUnavailable("sci_vulnerability: no publication day isolated a unique "
                          "record; tried " + "; ".join(tried[:8]))


# ==========================================================================
# FAMILY 2 -- INTERNET STANDARDS
# RFC Editor x Crossref x Semantic Scholar
# ==========================================================================
# Collection: every RFC the RFC Editor published in one calendar month.
# Key:        page count.
# Answer:     the RFC number.
#
# The whole collection arrives in ONE cached request (rfc-index.xml, ~13.7 MB,
# 9823 entries, all carrying a page count), so months after the first are free.
# <page-count> is a SIBLING of <format>, not a child; reading it as a child
# returns zero page counts from a document holding 19,646 of them, which is how
# the first probe of this design failed.
#
# Measured: 190 months from 2010 on, sized 8-60 RFCs, isolate a UNIQUE page-count
# argmax. Semantic Scholar coverage is INCOMPLETE (12/15 sampled RFC DOIs
# resolve; 9112, 9113, 9293 do not; 9 of 10 candidate months passed both
# witnesses), so it is a CONDITIONAL witness -- a month whose winner is missing
# from Semantic Scholar is skipped, never shipped with one witness.
_RFC_INDEX = "https://www.rfc-editor.org/rfc-index.xml"
_RFC_MIN_YEAR = 2010
_RFC_INDEX_CACHE = {}


def _strip_ns(tag):
    return str(tag).rsplit("}", 1)[-1]


def _rfc_months():
    """Parse rfc-index.xml once into {(year, month): [entries]}."""
    if _RFC_INDEX_CACHE:
        return _RFC_INDEX_CACHE
    raw = net.fetch(_RFC_INDEX, timeout=240, attempts=3)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    root = ET.fromstring(raw)
    out = defaultdict(list)
    for e in root.iter():
        if _strip_ns(e.tag) != "rfc-entry":
            continue
        kids = defaultdict(list)
        for k in e:
            kids[_strip_ns(k.tag)].append(k)

        def one(name):
            n = kids.get(name)
            return (n[0].text or "").strip() if n else ""

        docid = one("doc-id")
        if not docid.startswith("RFC"):
            continue
        pc = one("page-count")
        if not pc.isdigit():
            continue
        dn = kids.get("date")
        if not dn:
            continue
        dk = {_strip_ns(k.tag): (k.text or "").strip() for k in dn[0]}
        mon, yr = dk.get("month"), dk.get("year")
        if not (mon and yr and yr.isdigit()):
            continue
        out[(yr, mon)].append({
            "id": docid, "num": docid[3:].lstrip("0"), "pages": int(pc),
            "nau": len(kids.get("author") or []), "doi": one("doi"),
            "status": one("current-status"), "stream": one("stream"),
            "title": one("title")})
    _RFC_INDEX_CACHE.update(out)
    return _RFC_INDEX_CACHE


def gen_sci_standard(months=None, min_n=8, max_n=60):
    """RFC Editor month listing x Crossref DOI x Semantic Scholar."""
    try:
        bymonth = _rfc_months()
    except Exception as e:  # noqa: BLE001
        raise TrapUnavailable(f"sci_standard: index unavailable ({type(e).__name__})")

    if months:
        keys = [tuple(m) for m in months]
    else:
        keys = sorted((k for k in bymonth if int(k[0]) >= _RFC_MIN_YEAR),
                      key=lambda k: -int(k[0]))
    tried = []
    for key in keys:
        rows = bymonth.get(key) or []
        yr, mon = key
        if not (min_n <= len(rows) <= max_n):
            tried.append(f"{mon} {yr}: n={len(rows)}")
            continue
        try:
            best = _pick_extreme(rows, lambda r: r["pages"], f"standard {mon} {yr}",
                                 mode="max", valuefn=lambda r: r["num"])
            _rank_extra(
                key_component_depths={}, key_is_aggregated=False,
                equivalent_served_fields=[], n_fields_swept=0,
                sort_field_rejected_by_service=[
                    "the RFC Editor search interface orders only by number, "
                    "date, title and status",
                    "no page-count ordering or filter is offered"],
                n_true=len(rows), page_cap=None, pages_fetched=1)
        except TrapUnavailable as te:
            tried.append(str(te))
            continue

        num = best["num"]
        doi = best["doi"] or f"10.17487/RFC{num}"
        try:
            cr = _gj(f"https://api.crossref.org/works/{doi}", timeout=60, attempts=2)
            cr_title = ((cr.get("message") or {}).get("title") or [""])[0]
        except Exception as e:  # noqa: BLE001
            tried.append(f"{mon} {yr}: Crossref {type(e).__name__} for RFC {num}")
            continue
        try:
            s2 = _gj("https://api.semanticscholar.org/graph/v1/paper/"
                     f"DOI:{doi}?fields=title,year", timeout=60, attempts=2)
            s2_title = (s2 or {}).get("title") or ""
        except Exception:  # noqa: BLE001
            # A documented coverage gap, not a transport failure. Skip the month
            # rather than ship a trap with a single witness.
            tried.append(f"{mon} {yr}: RFC {num} absent from Semantic Scholar")
            time.sleep(1.2)
            continue
        time.sleep(1.2)

        if _norm(cr_title)[:40] != _norm(best["title"])[:40]:
            tried.append(f"{mon} {yr}: Crossref title mismatch for RFC {num}")
            continue
        if _norm(s2_title)[:40] != _norm(best["title"])[:40]:
            tried.append(f"{mon} {yr}: Semantic Scholar title mismatch for RFC {num}")
            continue

        srcs = [_RFC_INDEX,
                f"https://api.crossref.org/works/{doi}",
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
                "?fields=title,year"]
        return Candidate(
            category=CATEGORY,
            primary_operator="RFC Editor",
            field="RFC number",
            answer=num,
            entity=best["title"][:120],
            n_base=len(rows),
            sources=srcs,
            confirming_sources=srcs[1:],
            api_proof_argument=(
                "The RFC Editor search interface orders results only by number, "
                "date, title or status and offers no ordering or filter on "
                f"length, so all {len(rows)} documents published that month must "
                "be listed and their page counts compared."),
            confirmation=(
                f"Crossref and Semantic Scholar both resolve {doi} to the same "
                "document title, from two bibliographic services outside the "
                "RFC Editor"),
            facts={"month": mon, "year": yr, "n": len(rows), "pages": best["pages"],
                   "doi": doi, "stream": best["stream"], "status": best["status"],
                   "runner_up_pages": (sorted((r["pages"] for r in rows),
                                              reverse=True) + [None])[1],
                   "landing_pages": [
                       f"https://www.rfc-editor.org/rfc/rfc{num}.html",
                       f"https://www.rfc-editor.org/info/rfc{num}"]},
            prompt=build_prompt(
                "The RFC Editor maintains a published index of the Request for "
                "Comments series in which every document appears with its "
                "publication month and its length in pages.",
                "Consider only the documents that index records as published in "
                f"{mon} {yr}.",
                "Exactly one of those documents runs to more pages than any "
                "other published in that month.",
                "Report the RFC number of that single document.",
                "Give the number alone, as digits, without the letters RFC and "
                "with no other words.",
                note="Verify the number against its registered digital object "
                     "identifier before answering."),
        )
    raise TrapUnavailable("sci_standard: no month isolated a unique document; tried "
                          + "; ".join(tried[:8]))


# ==========================================================================
# FAMILY 3 -- SOFTWARE SUPPLY CHAIN
# Python Software Foundation (PyPI) x Google (deps.dev) x ecosyste.ms
# ==========================================================================
# Collection: every published release of one project on the Python Package Index.
# Key:        number of distribution artefacts uploaded for the release.
# Answer:     the version string.
#
# THE ROSTER IS LOAD-BEARING, and this is the design that failed hardest before
# it worked. On pure-Python projects the key is degenerate -- the modern default
# is one sdist plus one wheel -- so flask tied 46 ways, rich tied 207 ways and 8
# of 10 probed packages were unusable. Projects shipping compiled per-platform
# wheels have a release-to-release wheel matrix, and 7 of 15 of those isolate a
# unique argmax. The seven measured clean are listed first.
_PYPI_PKGS = ("pillow", "lxml", "coverage", "regex", "pandas", "scikit-learn",
              "psycopg2-binary",
              # measured tied at the time of writing; retained because artefact
              # counts change with every release, so a tie is not permanent
              "numpy", "scipy", "cryptography", "pyzmq", "grpcio",
              "matplotlib", "aiohttp", "msgpack",
              # further compiled-wheel projects, same selection rule
              "pyarrow", "duckdb", "polars", "rapidfuzz", "shapely",
              "zstandard", "greenlet", "cffi", "markupsafe", "bcrypt",
              "psutil", "orjson", "ujson", "pycryptodome", "netcdf4",
              "opencv-python-headless", "tokenizers", "safetensors")


def gen_sci_supplychain(packages=_PYPI_PKGS, min_n=12, max_n=400):
    """PyPI release history x deps.dev version x ecosyste.ms version."""
    tried = []
    for pkg in packages:
        try:
            j = _gj(f"https://pypi.org/pypi/{pkg}/json", timeout=120)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{pkg}: fetch {type(e).__name__}")
            continue
        rel = (j or {}).get("releases") or {}
        rows = [{"v": v, "nfiles": len(f)} for v, f in rel.items() if f]
        if not (min_n <= len(rows) <= max_n):
            tried.append(f"{pkg}: n={len(rows)}")
            continue
        vals = [r["nfiles"] for r in rows]
        try:
            best = _pick_extreme(rows, lambda r: r["nfiles"], f"supplychain {pkg}",
                                 mode="max", valuefn=lambda r: r["v"])
            _rank_extra(
                key_component_depths={}, key_is_aggregated=False,
                equivalent_served_fields=[], n_fields_swept=0,
                sort_field_rejected_by_service=[
                    "the PyPI JSON API serves one document per project with no "
                    "query, sort or filter parameters of any kind"],
                n_true=len(rows), page_cap=None, pages_fetched=1,
                # dispersion is why this roster works and a pure-Python roster
                # does not; recorded so the choice can be re-audited later
                key_dispersion_sd=(round(statistics.pstdev(vals), 4)
                                   if len(vals) > 1 else 0.0),
                key_distinct_values=len(set(vals)))
        except TrapUnavailable as te:
            tried.append(str(te))
            continue

        ver = best["v"]
        try:
            dd = _gj("https://api.deps.dev/v3alpha/systems/pypi/packages/"
                     f"{pkg}/versions/{ver}", timeout=60, attempts=2)
            dd_ver = ((dd or {}).get("versionKey") or {}).get("version")
        except Exception as e:  # noqa: BLE001
            tried.append(f"{pkg}: deps.dev {type(e).__name__} for {ver}")
            continue
        try:
            eco = _gj("https://packages.ecosyste.ms/api/v1/registries/pypi.org/"
                      f"packages/{pkg}/versions/{ver}", timeout=60, attempts=2)
            eco_ver = (eco or {}).get("number")
        except Exception as e:  # noqa: BLE001
            tried.append(f"{pkg}: ecosyste.ms {type(e).__name__} for {ver}")
            continue

        # Both witnesses were probed against fabricated versions (numpy 99.98.97,
        # pandas 0.0.0-nonexistent) and both REFUSED, so a hit here is a real
        # confirmation and not an echo of the query string.
        if str(dd_ver) != str(ver):
            tried.append(f"{pkg}: deps.dev does not carry {ver}")
            continue
        if str(eco_ver) != str(ver):
            tried.append(f"{pkg}: ecosyste.ms does not carry {ver}")
            continue

        srcs = [f"https://pypi.org/pypi/{pkg}/json",
                f"https://api.deps.dev/v3alpha/systems/pypi/packages/{pkg}/versions/{ver}",
                "https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/"
                f"{pkg}/versions/{ver}"]
        return Candidate(
            category=CATEGORY,
            primary_operator="Python Software Foundation",
            field="package version string",
            answer=ver,
            entity=f"{pkg} on the Python Package Index",
            n_base=len(rows),
            sources=srcs,
            confirming_sources=srcs[1:],
            api_proof_argument=(
                "The Python Package Index serves one document per project with "
                "no query, ordering or filtering parameters at all, so the "
                f"artefact lists of all {len(rows)} published releases must be "
                "read out of that document and compared."),
            confirmation=(
                f"Google Open Source Insights and ecosyste.ms both resolve "
                f"{pkg} {ver} as a published release, from two indexes outside "
                "the Python Package Index"),
            facts={"package": pkg, "n": len(rows), "files": best["nfiles"],
                   "runner_up_files": (sorted(vals, reverse=True) + [None])[1],
                   "distinct_file_counts": len(set(vals)),
                   "landing_pages": [f"https://pypi.org/project/{pkg}/{ver}/",
                                     f"https://deps.dev/pypi/{pkg}/{ver}"]},
            prompt=build_prompt(
                "The Python Package Index publishes, for every project it hosts, "
                "a release history listing each released version together with "
                "the distribution files uploaded for it.",
                f"Consider only the released versions of the project named {pkg} "
                "that have at least one distribution file.",
                "Exactly one of those versions has more uploaded distribution "
                "files than any other version of the project.",
                "Report the version string of that single release.",
                "Give the version string alone, exactly as the index spells it, "
                "with no other words.",
                note="Confirm the version against two package indexes outside "
                     "the registry before answering."),
        )
    raise TrapUnavailable("sci_supplychain: no project isolated a unique release; "
                          "tried " + "; ".join(tried[:8]))


# ==========================================================================
# FAMILY 4 -- INTERNET NUMBER RESOURCES
# RIPE NCC x PeeringDB x CAIDA
# ==========================================================================
# Collection: the autonomous system numbers RIPE lists for one country.
# Key:        number of prefixes the system announces in the global routing table.
# Answer:     the autonomous system number.
#
# ARIN WAS THE SPECIFIED WITNESS AND IS DISPROVED -- see the module docstring and
# source_gate.WITNESS_PROVENANCE_PAIRS. PeeringDB records are authored by the
# networks themselves and CAIDA computes AS Rank from its own BGP collection, so
# neither is the primary speaking twice. Measured: Iceland 40 ASNs -> AS12969
# unique at 34 prefixes against 22; Malta 40 ASNs -> AS12709 unique at 201
# against 162.
#
# ON WITNESS DISAGREEMENT. For AS12969 the three sources give three names:
# CAIDA "Vodafone_Iceland", PeeringDB "Reykjavik Fibre Network" (aka "Og
# Vodafone, Og Fjarskipti, Vodafone Iceland"), RIPE holder "Vodafone_Iceland
# Ljosleidarinn ehf". That is not a contradiction, it is three vintages of one
# renamed operator, and PeeringDB's aka field bridges them. It is also positive
# evidence of independence: an echo witness cannot differ from its source at all.
# _reconcile_names() therefore REQUIRES the variants to share a token and records
# the variants and the reconciliation basis on the trap. Genuinely irreconcilable
# names are refused rather than resolved by preferring the primary.
_RIPE_COUNTRIES = (("IS", "Iceland"), ("MT", "Malta"), ("EE", "Estonia"),
                   ("LU", "Luxembourg"), ("CY", "Cyprus"), ("LV", "Latvia"),
                   ("SI", "Slovenia"), ("HR", "Croatia"),
                   ("LI", "Liechtenstein"), ("MC", "Monaco"),
                   ("SM", "San Marino"), ("AD", "Andorra"),
                   ("ME", "Montenegro"), ("MK", "North Macedonia"),
                   ("AL", "Albania"), ("BA", "Bosnia and Herzegovina"),
                   ("GE", "Georgia"), ("AM", "Armenia"), ("MD", "Moldova"),
                   ("KG", "Kyrgyzstan"), ("TJ", "Tajikistan"),
                   ("AZ", "Azerbaijan"))
_RIPE_PACE = 0.25


def _reconcile_names(ripe_holder, pdb_name, pdb_aka, caida_name):
    """Are three independent naming vintages the same organisation?

    Returns (ok, basis, variants). Requires a shared token between the primary's
    holder string and at least one witness name, and between the two witnesses
    directly or through PeeringDB's aka list.
    """
    variants = {"ripe_holder": ripe_holder, "peeringdb_name": pdb_name,
                "peeringdb_aka": pdb_aka, "caida_asn_name": caida_name}
    t_ripe, t_pdb = _tokens(ripe_holder), _tokens(pdb_name) | _tokens(pdb_aka)
    t_cai = _tokens(caida_name)
    basis = []
    if t_ripe & t_cai:
        basis.append(f"registry holder shares {sorted(t_ripe & t_cai)} with CAIDA")
    if t_ripe & t_pdb:
        basis.append(f"registry holder shares {sorted(t_ripe & t_pdb)} with PeeringDB")
    if t_pdb & t_cai:
        basis.append(f"PeeringDB shares {sorted(t_pdb & t_cai)} with CAIDA")
    return (len(basis) >= 2, "; ".join(basis), variants)


def gen_sci_asn(countries=_RIPE_COUNTRIES, min_n=12, max_n=120):
    # max_n was 60 and silently excluded Iceland, whose full roster is 86. The
    # design probe had bounded itself to the first 40 of those 86 and reported
    # AS12969 at 34 prefixes; over the whole roster the argmax is AS200651 at
    # 39. A cap that hides the true winner is worse than a slower sweep, so the
    # cap is set above the roster sizes actually encountered.
    """RIPE country ASN roster x PeeringDB network x CAIDA AS Rank."""
    tried = []
    for cc, name in countries:
        list_url = ("https://stat.ripe.net/data/country-resource-list/data.json"
                    f"?resource={cc}")
        try:
            j = _gj(list_url, timeout=120)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{cc}: roster {type(e).__name__}")
            continue
        asns = (((j or {}).get("data") or {}).get("resources") or {}).get("asn") or []
        asns = [str(a).strip() for a in asns if str(a).strip().isdigit()]
        if not (min_n <= len(asns) <= max_n):
            tried.append(f"{cc}: roster n={len(asns)}")
            continue

        rows, failed = [], 0
        for a in asns:
            try:
                jj = _gj("https://stat.ripe.net/data/announced-prefixes/data.json"
                         f"?resource=AS{a}", timeout=60, attempts=2)
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            rows.append({"asn": a,
                         "npfx": len(((jj or {}).get("data") or {}).get("prefixes") or [])})
            time.sleep(_RIPE_PACE)
        # A partial roster cannot support "more than any other in the country".
        if failed or len(rows) != len(asns):
            tried.append(f"{cc}: {failed} of {len(asns)} ASNs unresolved")
            continue
        if not any(r["npfx"] for r in rows):
            tried.append(f"{cc}: no listed system announces any prefix")
            continue
        try:
            best = _pick_extreme(rows, lambda r: r["npfx"], f"asn {cc}",
                                 mode="max", valuefn=lambda r: r["asn"])
            _rank_extra(
                key_component_depths={}, key_is_aggregated=False,
                equivalent_served_fields=[], n_fields_swept=0,
                sort_field_rejected_by_service=[
                    "the country resource list returns an unordered roster",
                    "announced-prefixes is a per-resource call, not a rankable "
                    "collection endpoint"],
                n_true=len(rows), page_cap=None, pages_fetched=len(asns) + 1)
        except TrapUnavailable as te:
            tried.append(str(te))
            continue

        asn = best["asn"]
        try:
            pdb_row = ((_gj(f"https://www.peeringdb.com/api/net?asn={asn}",
                            timeout=60, attempts=2) or {}).get("data") or [{}])[0]
            cai_row = ((_gj(f"https://api.asrank.caida.org/v2/restful/asns/{asn}",
                            timeout=60, attempts=2) or {}).get("data") or {}
                       ).get("asn") or {}
            holder = ((_gj("https://stat.ripe.net/data/as-overview/data.json"
                           f"?resource=AS{asn}", timeout=60, attempts=2) or {}
                       ).get("data") or {}).get("holder") or ""
        except Exception as e:  # noqa: BLE001
            tried.append(f"{cc}: confirm {type(e).__name__} for AS{asn}")
            continue

        if str(pdb_row.get("asn")) != str(asn):
            tried.append(f"{cc}: AS{asn} is not registered at PeeringDB")
            continue
        if str(cai_row.get("asn")) != str(asn):
            tried.append(f"{cc}: AS{asn} absent from CAIDA AS Rank")
            continue
        cai_cc = ((cai_row.get("country") or {}).get("iso") or "").upper()
        if cai_cc and cai_cc != cc:
            # Two independent observers disagreeing about the very attribute the
            # collection is DEFINED by. Refuse it; do not paper over it with the
            # primary's own answer.
            tried.append(f"{cc}: CAIDA places AS{asn} in {cai_cc}, not {cc}")
            continue
        ok, basis, variants = _reconcile_names(
            holder, pdb_row.get("name") or "", " ".join(pdb_row.get("aka") or "")
            if isinstance(pdb_row.get("aka"), list) else (pdb_row.get("aka") or ""),
            cai_row.get("asnName") or "")
        if not ok:
            tried.append(f"{cc}: AS{asn} names irreconcilable across sources "
                         f"({variants})")
            continue

        srcs = [list_url,
                f"https://www.peeringdb.com/api/net?asn={asn}",
                f"https://api.asrank.caida.org/v2/restful/asns/{asn}"]
        return Candidate(
            category=CATEGORY,
            primary_operator="RIPE NCC",
            field="autonomous system number",
            answer=asn,
            entity=(holder or pdb_row.get("name") or f"AS{asn}")[:120],
            n_base=len(rows),
            sources=srcs,
            confirming_sources=srcs[1:],
            api_proof_argument=(
                "The registry returns the country roster as an unordered list "
                "and publishes announced prefixes only one resource at a time, "
                f"so each of the {len(rows)} listed numbers must be queried "
                "separately and the prefix lists compared."),
            confirmation=(
                f"PeeringDB lists AS{asn} as {pdb_row.get('name')!r} and CAIDA "
                f"records it as {cai_row.get('asnName')!r} in {cai_cc or cc}; "
                "PeeringDB records are maintained by the networks themselves and "
                "CAIDA infers from its own routing observations"),
            facts={"country": cc, "country_name": name, "n": len(rows),
                   "prefixes": best["npfx"],
                   "runner_up_prefixes": (sorted((r["npfx"] for r in rows),
                                                 reverse=True) + [None])[1],
                   "caida_rank": cai_row.get("rank"),
                   "name_variants": variants,
                   "name_reconciliation_basis": basis,
                   "landing_pages": [
                       f"https://stat.ripe.net/AS{asn}",
                       f"https://www.peeringdb.com/asn/{asn}"]},
            prompt=build_prompt(
                "The RIPE NCC publishes, for each country, the list of "
                "autonomous system numbers registered to organisations there, "
                "and separately reports the address prefixes each autonomous "
                "system announces in the global routing table.",
                "Consider only the autonomous system numbers that registry lists "
                f"for {name}.",
                "Exactly one of them announces more prefixes than any other "
                "number on that list.",
                "Report that autonomous system number.",
                "Give the number alone, as digits, without the letters AS and "
                "with no other words.",
                note="Check the number against a peering directory and an "
                     "academic routing observatory before answering."),
        )
    raise TrapUnavailable("sci_asn: no country isolated a unique autonomous system; "
                          "tried " + "; ".join(tried[:8]))


# ==========================================================================
# REGISTRATION
# ==========================================================================
# supersedes_default=True stops families_for() synthesising a second entry from
# GENERATORS["science and technology"]; the arXiv generator is registered here
# explicitly instead, and RETIRED.
ct.register_family(
    CATEGORY, "sci_arxiv", fn=None, supersedes_default=True,
    primary_operator="Cornell University",
    witness_operators=("DataCite", "OurResearch (OpenAlex)"),
    ranking_key="author count", servable=False,
    retired_reason=(
        "Four traps were served from this one generator by varying only its date "
        "and subject-class seeds. Measured pairwise prompt similarity 0.980-0.993 "
        "against a cross-category baseline of 0.016-0.043, one primary operator "
        "and one witness pair across all four, so a solver that answered one had "
        "answered all four. Retired from service, kept as a calibration baseline."),
    note="records remain in the catalog as non-servable calibration baselines")

for _fid, _fn, _prim, _wits, _key in (
    ("sci_vulnerability", gen_sci_vulnerability,
     "US National Institute of Standards and Technology",
     ("MITRE Corporation", "Forum of Incident Response and Security Teams"),
     "external reference count"),
    ("sci_standard", gen_sci_standard, "RFC Editor",
     ("Crossref", "Allen Institute for AI"), "page count"),
    ("sci_supplychain", gen_sci_supplychain, "Python Software Foundation",
     ("Google LLC", "ecosyste.ms"), "distributed artefact count"),
    ("sci_asn", gen_sci_asn, "RIPE NCC",
     ("PeeringDB", "CAIDA, University of California San Diego"),
     "announced prefix count"),
):
    ct.register_family(CATEGORY, _fid, fn=_fn, primary_operator=_prim,
                       witness_operators=_wits, ranking_key=_key, servable=True)

FAMILY_IDS = ("sci_vulnerability", "sci_standard", "sci_supplychain", "sci_asn")
