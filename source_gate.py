"""source_gate.py — hardened source and taxonomy gate for Project Seal.

Replaces two defects found by audit:

D1  The banned list did not contain ``loc.gov`` or ``archive.org``, so the entire
    shipped corpus passed a gate that should have rejected it.

D2  The ">= 3 distinct sources" rule counted URL STRINGS. The shipped pool cited
    ``loc.gov/resource/...``, ``loc.gov/item/...`` and ``loc.gov/newspapers/...``
    and scored 3/3 while resting on ONE publisher. Counting is now done over
    resolved OPERATORS -- the entity that controls the data -- so that:

      * three paths on one agency's site count once,
      * ``wikidata.org`` and ``en.wikipedia.org`` count once (both Wikimedia),
      * ``clinicaltrials.gov``, ``pubmed`` and ``reporter.nih.gov`` count once
        (all NIH), which is why "four health sources" was really two,
      * ``openlibrary.org`` inherits the Internet Archive ban even though the
        literal string ``archive.org`` never appears in the URL.

A third rule is added: at least one source must INDEPENDENTLY CONFIRM the answer,
not merely mention the entity. Three sources that all name a company but only one
of which carries the figure being asked for is a single-source prompt wearing a
disguise.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# --------------------------------------------------------------------------
# Taxonomy: closed enumeration, exactly one per prompt.
# --------------------------------------------------------------------------
CATEGORIES = (
    "science and technology",
    "art",
    "business",
    "celebrities/public figures",
    "education",
    "finance",
    "geography",
    "health and medicine",
    "history",
    "legal",
    "politics",
    "shopping",
    "sports",
    "travel",
    "tv shows and movies",
    "video games",
)
_CATSET = frozenset(CATEGORIES)

# --------------------------------------------------------------------------
# Banned sources.
# --------------------------------------------------------------------------
# Substring match on the raw URL. Kept as substrings (not registrable domains)
# so that subdomains such as chroniclingamerica.loc.gov are caught directly.
BANNED_DOMAINS = (
    "archive.org",
    "loc.gov",
    "hathitrust.org",
    "sports-reference.com",
    "pro-football-reference.com",
    "basketball-reference.com",
    "baseball-reference.com",
    "hockey-reference.com",
    "fbref.com",
    "stathead.com",
    # ---- Wikimedia, banned 2026-08-11 ----------------------------------
    # Requested as "no wikipedia.org so by extension wikidata.org", and the
    # measurement backs the request rather than merely complying with it:
    # travelbear.py rewrote ONE Wikidata claim (P1566 on the airport item)
    # and the shipped travel answer moved 6296543 -> 656220 while
    # evaluate_one still returned ship / gold / 0 of 13 tests failing. The
    # only guard was `place.lower() in rdf.lower()` on a single token, and
    # k = 2 GeoNames records pass it (the airport and the village of the
    # same name), so its discrimination was 0.5. A public wiki that can set
    # the answer is a correctness defect, not a citation-style preference.
    "wikidata.org",
    "wikipedia.org",
    "wikimedia.org",
    "wikisource.org",
    "wikiquote.org",
    "wikivoyage.org",
    "wiktionary.org",
    "wikibooks.org",
    "wikinews.org",
    "wikiversity.org",
)

# Operators banned wholesale, catching sibling properties that do not contain a
# banned substring. openlibrary.org is an Internet Archive project; web.archive.org
# and openlibrary.org are the same publisher wearing two domains.
BANNED_OPERATORS = frozenset({
    "Internet Archive",
    "US Library of Congress",
    "HathiTrust",
    "Sports Reference LLC",
    # Catches every Wikimedia project property under one operator name, the
    # way "Internet Archive" catches openlibrary.org. pcgamingwiki.com maps
    # to "PCGamingWiki" and is a different publisher, so it is NOT caught
    # here; it stays in scope only if you ask for it.
    "Wikimedia Foundation",
})

# --------------------------------------------------------------------------
# Operator resolution.
# --------------------------------------------------------------------------
# Multi-part public suffixes needed to compute a registrable domain without a
# network call or the tldextract dependency.
_MULTI_SUFFIX = (
    "co.uk", "org.uk", "ac.uk", "gov.uk", "net.uk", "sch.uk",
    "com.au", "org.au", "net.au", "edu.au", "gov.au",
    "co.nz", "org.nz", "govt.nz",
    "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
    "com.br", "gov.br", "org.br",
    "co.za", "org.za", "gov.za",
    "com.cn", "org.cn", "gov.cn", "edu.cn",
    "co.in", "org.in", "gov.in", "nic.in", "ac.in",
    "com.mx", "gob.mx", "com.sg", "gov.sg", "edu.sg",
)


def registrable_domain(url: str) -> str:
    """Return the registrable domain (eTLD+1) for a URL or bare host."""
    if not url:
        return ""
    s = str(url).strip()
    if "//" not in s:
        s = "//" + s
    host = (urlparse(s).netloc or "").lower().split("@")[-1].split(":")[0]
    host = host.rstrip(".")
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last2 = ".".join(parts[-2:])
    last3 = ".".join(parts[-3:])
    if last2 in _MULTI_SUFFIX:
        return last3
    return last2


# registrable domain -> controlling operator. Anything unmapped falls back to its
# registrable domain, which is conservative: it can only ever OVER-count operators
# for hosts we have not classified, so unmapped hosts are reported by audit_operators().
OPERATOR_MAP = {
    # --- banned publishers (mapped so the operator ban can fire) ---
    "loc.gov": "US Library of Congress",
    "archive.org": "Internet Archive",
    "openlibrary.org": "Internet Archive",
    "hathitrust.org": "HathiTrust",
    "sports-reference.com": "Sports Reference LLC",
    "pro-football-reference.com": "Sports Reference LLC",
    "basketball-reference.com": "Sports Reference LLC",
    "baseball-reference.com": "Sports Reference LLC",
    "hockey-reference.com": "Sports Reference LLC",
    "fbref.com": "Sports Reference LLC",
    "stathead.com": "Sports Reference LLC",

    # --- Wikimedia: one operator, many hostnames ---
    "wikidata.org": "Wikimedia Foundation",
    "wikipedia.org": "Wikimedia Foundation",
    "wikimedia.org": "Wikimedia Foundation",
    "wikisource.org": "Wikimedia Foundation",

    # --- US federal, resolved at AGENCY level, not "the government" ---
    "nih.gov": "US National Institutes of Health",
    "clinicaltrials.gov": "US National Institutes of Health",
    "fda.gov": "US Food and Drug Administration",
    "sec.gov": "US Securities and Exchange Commission",
    "treasury.gov": "US Department of the Treasury",
    "federalregister.gov": "US Office of the Federal Register",
    # A community wiki, but a distinct controlling entity from Valve and
    # from Wikimedia, and it records developer credits editorially rather
    # than by mirroring either one.
    "pcgamingwiki.com": "PCGamingWiki",
    "govinfo.gov": "US Government Publishing Office",
    "gpo.gov": "US Government Publishing Office",
    "usgs.gov": "US Geological Survey",
    "faa.gov": "US Federal Aviation Administration",
    "nasa.gov": "US National Aeronautics and Space Administration",
    "uspto.gov": "US Patent and Trademark Office",
    "ed.gov": "US Department of Education",
    "data.gov": "US General Services Administration",
    "congress.gov": "US Congress",
    "fec.gov": "US Federal Election Commission",
    "bts.gov": "US Bureau of Transportation Statistics",
    "usda.gov": "US Department of Agriculture",
    # The Federal Reserve Banks are NOT the Treasury. The Fed is a separate
    # legal entity that buys Treasury paper on the open market, so when the New
    # York Fed's SOMA holdings file lists a CUSIP it is a genuine witness to a
    # security Treasury issued, not Treasury restating itself. Without this
    # entry the host resolved to the bare string "newyorkfed.org", which the
    # gate would have counted as an operator anyway but could never audit.
    "newyorkfed.org": "Federal Reserve Bank of New York",
    "federalreserve.gov": "US Federal Reserve Board",

    # --- non-US public bodies ---
    "clinicaltrialsregister.eu": "European Medicines Agency",
    "europa.eu": "European Union",
    "isrctn.com": "BMJ / ISRCTN registry",
    "who.int": "World Health Organization",

    # --- independent projects, societies and companies ---
    # arXiv is operated by Cornell, so it must collapse to the same operator as
    # Cornell LII -- otherwise one controlling entity would be counted twice.
    "arxiv.org": "Cornell University",
    "cornell.edu": "Cornell University",
    "justia.com": "Justia",
    "courtlistener.com": "Free Law Project",
    # case.law is the Caselaw Access Project, run by the Harvard Law School
    # Library Innovation Lab. Free Law Project ingested CAP data in 2024, so the
    # two are distinct operators but NOT fully independent witnesses for cases
    # whose CourtListener record derives from the Harvard scan.
    "case.law": "Harvard Law School Library Innovation Lab",
    "metmuseum.org": "Metropolitan Museum of Art",
    "artic.edu": "Art Institute of Chicago",
    "clevelandart.org": "Cleveland Museum of Art",
    "nobelprize.org": "Nobel Prize Outreach",
    # German National Library. Runs neither the Nobel registry nor Wikidata,
    # so a GND record resolving there is an independent witness for the
    # answer-to-entity binding. culturegraph.org is also DNB-operated and is
    # mapped to the same operator so it can never be double-counted.
    "d-nb.info": "German National Library",
    # OCLC runs the FAST subject/name authority and WorldCat. Independent of
    # Major League Baseball and of Wikimedia, so a FAST record resolving to the
    # same person is a genuine witness for a player identifier.
    "worldcat.org": "OCLC",
    "oclc.org": "OCLC",
    "dnb.de": "German National Library",
    "culturegraph.org": "German National Library",
    # OpenFIGI is Bloomberg's open security-identifier service. Bloomberg is a
    # private company with no relationship to Treasury, the SEC or the Fed, so
    # a CUSIP -> FIGI mapping is a fourth independent line on the same security.
    "openfigi.com": "Bloomberg L.P.",
    "bloomberg.com": "Bloomberg L.P.",
    "openfoodfacts.org": "Open Food Facts",
    "restcountries.com": "REST Countries project",
    "ourairports.com": "OurAirports",
    "thesportsdb.com": "TheSportsDB",
    "tvmaze.com": "TVmaze",
    "gleif.org": "Global Legal Entity Identifier Foundation",
    "steampowered.com": "Valve Corporation",
    "pegi.info": "Pan European Game Information",
    "esrb.org": "Entertainment Software Rating Board",
    "google.com": "Google LLC",
    "hipolabs.com": "Hipo Labs",
    "openalex.org": "OurResearch (OpenAlex)",
    "crossref.org": "Crossref",
    "datacite.org": "DataCite",
    # ORCID, Inc. is the registrar OF the identifier, so it is the authoritative
    # witness for an ORCID iD and is institutionally independent of any index
    # that merely cites one (OpenAlex, Crossref, Europe PMC).
    "orcid.org": "ORCID, Inc.",
    "europeana.eu": "Europeana Foundation",
    # Europe PMC is operated by EMBL-EBI. Distinct operator from NIH/NLM even
    # though the two indexes overlap on content: EBI runs its own ingest,
    # its own identifier extraction and its own REST service.
    "ebi.ac.uk": "EMBL-EBI",
    # Europe PMC is operated by EMBL-EBI. Mapping it to the SAME operator string
    # as ebi.ac.uk is a DEDUPE, not an addition: it stops a trap from counting
    # Europe PMC and EBI as two independent witnesses. Required now because the
    # only working PMC PDF route lives on the europepmc.org host.
    "europepmc.org": "EMBL-EBI",
    "geonames.org": "GeoNames",
    "osti.gov": "US Department of Energy",
    "imdbws.com": "IMDb (Amazon)",
    "imdb.com": "IMDb (Amazon)",
    "mlb.com": "Major League Baseball",
    "retrosheet.org": "Retrosheet",
    "nhle.com": "National Hockey League",
    "football-data.org": "football-data.org",
    "olympedia.org": "Olympedia",
    "upcitemdb.com": "UPCitemdb",
    "semanticscholar.org": "Allen Institute for AI",

    # --- science and technology, four MUTUALLY DISJOINT families -------------
    # Added because every S&T trap resolved through arXiv/Cornell with the same
    # witness pair, so the four "prompts" shared one failure mode: solving one
    # solved all four. Each family below is vetted to a distinct controlling
    # entity, and operators are allocated EXCLUSIVELY across families so no two
    # families can touch the same operator.
    #
    # family 1 -- software vulnerabilities
    # NIST runs the NVD, whose per-day publication list is the enumerated
    # collection, so NIST is the PRIMARY. MITRE mints the identifier and holds
    # the authoritative description, and FIRST publishes an EPSS score it
    # computes itself. GitHub was dropped as a witness after
    # api.github.com/advisories?cve_id=CVE-2023-34095 returned [] -- advisory
    # coverage is patchy, so it cannot witness an arbitrary CVE. It stays mapped
    # so that if it is ever cited it resolves to the right controlling entity.
    "mitre.org": "MITRE Corporation",
    "cve.org": "MITRE Corporation",
    "nist.gov": "US National Institute of Standards and Technology",
    "first.org": "Forum of Incident Response and Security Teams",
    "github.com": "GitHub, Inc. (Microsoft)",
    #
    # family 2 -- internet standards
    # The RFC Editor publishes the series; Crossref registers the RFC DOI under
    # 10.17487 and is already vetted above; IANA (a function of ICANN/PTI) runs
    # the protocol registries that cite the RFC. Distinct legal entities, though
    # IANA and the RFC Editor are closer than the other families' witnesses --
    # noted so the independence claim can be audited rather than assumed.
    "rfc-editor.org": "RFC Editor",
    "iana.org": "Internet Assigned Numbers Authority",
    #
    # family 3 -- software supply chain
    # PyPI is the registry of record; deps.dev is Google Open Source Insights and
    # MUST collapse to the same operator as google.com and osv.dev, or one
    # company would be counted as two witnesses; Software Heritage is Inria.
    "pypi.org": "Python Software Foundation",
    "python.org": "Python Software Foundation",
    "deps.dev": "Google LLC",
    "osv.dev": "Google LLC",
    "softwareheritage.org": "Software Heritage (Inria)",
    # ecosyste.ms describes itself on /about as "a non-profit initiative hosted
    # by Open Source Collective"; the fiscal host is named so the operator is a
    # legal entity rather than a hostname. Distinct from Google and from the
    # PSF. Software Heritage stays mapped but is NOT used as a witness here:
    # measured, it binds a project ORIGIN only and cannot see a release version.
    "ecosyste.ms": "ecosyste.ms (Open Source Collective)",
    #
    # family 4 -- internet number resources
    # ARIN WAS SPECIFIED AS A WITNESS AND IS DISPROVED. Measured:
    #   rdap.arin.net/registry/autnum/3333 -> redirects to rdap.db.ripe.net and
    #   returns RIPE's own record (name RIPE-NCC-AS).
    #   rdap.arin.net/registry/autnum/7018 -> stays on arin.net.
    # RDAP bootstraps to the registry that HOLDS the resource, so ARIN can only
    # "confirm" ASNs it already administers; for a RIPE-region ASN it is RIPE
    # restating itself through a second hostname. Replaced by CAIDA, which
    # derives its record from its own BGP observation rather than from any RIR,
    # and retained PeeringDB, whose records are authored by the networks
    # themselves (it carries info_traffic / info_ratio / policy_general /
    # irr_as_set, none of which exist in the RIPE database).
    "ripe.net": "RIPE NCC",
    "caida.org": "CAIDA, University of California San Diego",
    "peeringdb.com": "PeeringDB",
    # kept mapped so a stray citation still resolves, but NOT used as a witness
    "arin.net": "American Registry for Internet Numbers",

    # --- code hosts: the OPERATOR is the project, not the host. Datasets served
    # from GitHub Pages / raw.githubusercontent must be mapped explicitly by the
    # caller via SOURCE_OVERRIDES; left unmapped they resolve to the host, which
    # would wrongly merge two unrelated projects into one operator.
    "github.io": "UNRESOLVED-github-pages",
    "githubusercontent.com": "UNRESOLVED-github-raw",
}

# Exact-URL-prefix overrides for datasets hosted on shared infrastructure, where
# the registrable domain names the host rather than the publisher.
SOURCE_OVERRIDES = (
    ("davidmegginson.github.io/ourairports-data", "OurAirports"),
    # Third independent ICAO publisher, added when the Wikimedia ban left
    # geography with one confirming witness against a floor of two.
    ("aviationweather.gov", "US National Oceanic and Atmospheric Administration"),
    ("raw.githubusercontent.com/jpatokal/openflights", "OpenFlights"),
)


def resolve_operator(url: str) -> str:
    """Resolve a source URL to its controlling operator."""
    low = str(url or "").lower()
    for prefix, op in SOURCE_OVERRIDES:
        if prefix in low:
            return op
    rd = registrable_domain(url)
    return OPERATOR_MAP.get(rd, rd)


def resolve_operators(sources) -> dict:
    """Map operator -> list of source URLs attributed to it."""
    out: dict[str, list] = {}
    for s in sources or []:
        out.setdefault(resolve_operator(s), []).append(s)
    return out


def vetted_operator(url: str):
    """Operator ONLY when it came from an explicit mapping, else None.

    resolve_operator() ends with ``OPERATOR_MAP.get(rd, rd)``, so a host that was
    never vetted resolves to its own bare registrable domain.  Counting that
    string as an operator promotes an unvetted site to an institution: a probe
    of pre-1976 US patents reported "two independent confirming operators" when
    the second was the literal string 'freepatentsonline.com', a commercial
    mirror of the very USPTO data being confirmed.  Independence has to be
    asserted by a human once, in OPERATOR_MAP, not inferred from DNS.
    """
    low = str(url or "").lower()
    for prefix, op in SOURCE_OVERRIDES:
        if prefix in low:
            return op
    return OPERATOR_MAP.get(registrable_domain(url))


def vetted_operators(sources) -> dict:
    """Map vetted operator -> source URLs. Unvetted hosts are dropped."""
    out: dict[str, list] = {}
    for s in sources or []:
        op = vetted_operator(s)
        if op:
            out.setdefault(op, []).append(s)
    return out


def unvetted_hosts(sources) -> list:
    """Registrable domains that are absent from OPERATOR_MAP / SOURCE_OVERRIDES."""
    return sorted({registrable_domain(s) for s in (sources or [])
                   if vetted_operator(s) is None and registrable_domain(s)})


# --------------------------------------------------------------------------
# Gate.
# --------------------------------------------------------------------------
def banned_violations(sources):
    """Return [(url, reason)] for every source that is banned by domain or operator."""
    bad = []
    for s in sources or []:
        low = str(s).lower()
        for b in BANNED_DOMAINS:
            if b in low:
                bad.append((s, f"banned domain: {b}"))
                break
        else:
            op = resolve_operator(s)
            if op in BANNED_OPERATORS:
                bad.append((s, f"banned operator: {op}"))
    return bad


def check_sources(sources, min_operators=3, confirming_sources=None,
                  primary_operator=None):
    """Enforce the source rules. Returns a list of violation strings (empty == pass).

    confirming_sources: the subset of `sources` that independently CONFIRM the
    answer. At least one is required. Passing None means "not asserted", which
    is itself a violation -- silence is not evidence.

    primary_operator: the operator whose collection was ranked to produce the
    answer. A confirming source run by that same operator is the primary
    restating itself, not a witness, so it does not count. Passing None means
    "not asserted", which is itself a violation for the same reason: an
    unnamed primary makes the self-confirmation check unenforceable.
    """
    v = []
    srcs = list(sources or [])

    for url, reason in banned_violations(srcs):
        v.append(f"R4 banned source {url} ({reason})")

    # Only explicitly mapped operators count. An unvetted host is not an
    # institution just because it resolves in DNS.
    ops = {op: u for op, u in vetted_operators(srcs).items()
           if not str(op).startswith("UNRESOLVED-")}
    if len(ops) < min_operators:
        v.append(
            f"R3 only {len(ops)} independent operator(s) across {len(srcs)} source(s): "
            + "; ".join(f"{op} x{len(u)}" for op, u in sorted(ops.items()))
        )

    unresolved = [op for op in resolve_operators(srcs)
                  if str(op).startswith("UNRESOLVED-")]
    if unresolved:
        v.append(f"R3 unattributable source host(s), operator cannot be verified: {unresolved}")

    unvetted = unvetted_hosts(srcs)
    if unvetted:
        v.append(
            "R3d unvetted source host(s) absent from OPERATOR_MAP, so they cannot "
            f"be counted as independent operators: {unvetted}")

    if confirming_sources is None:
        v.append("R3b no answer-confirming source asserted")
    else:
        conf = [c for c in confirming_sources if c in srcs]
        if not conf:
            v.append("R3b no listed source independently confirms the answer")
        elif primary_operator is None:
            v.append("R3c no primary operator asserted, so self-confirmation "
                     "cannot be ruled out")
        else:
            witness = sorted(o for o in vetted_operators(conf)
                             if o != primary_operator
                             and not str(o).startswith("UNRESOLVED-"))
            if not witness:
                v.append(
                    f"R3c every confirming source is run by the primary operator "
                    f"{primary_operator!r}; a source restating itself is not a witness")
    return v


# Operators that are NOT independent of other operators, because the first is
# governed or jointly operated by the second. This is a DIRECTED, PAIRWISE
# relation, deliberately not an equivalence class: the Research Organization
# Registry is jointly governed by the California Digital Library, Crossref and
# DataCite, so ROR is not an independent witness alongside any of those three --
# but Crossref and DataCite remain independent OF EACH OTHER, since they are
# separately incorporated DOI registration agencies with separate boards.
# Collapsing them into one group would wrongly demote traps that legitimately
# cite both.
NOT_INDEPENDENT_OF = {
    "Research Organization Registry": {
        "California Digital Library", "Crossref", "DataCite",
    },
}

# Hosts proven to ECHO the query back -- they return a populated page for
# fabricated identifiers, so a "hit" there confirms nothing. These must never be
# added to OPERATOR_MAP. Recorded here so the finding is not re-litigated.
REJECTED_ECHO_SOURCES = {
    "freepatentsonline.com": "returns a populated page for fabricated patent numbers",
    "lens.org": "returns a populated page for fabricated patent numbers",
    "patents.google.com": "bare-queried search echoes fabricated numbers 9999998 / 8888887",
}


def independent_witnesses(sources, confirming_sources, primary_operator):
    """Operators that confirm the answer and did NOT supply the ranked collection.

    Beyond dropping the primary operator and unresolved hosts, this collapses
    operators that share governance with a co-present operator (see
    NOT_INDEPENDENT_OF). Without that collapse a registry jointly run by two
    other cited operators would be counted as a third, independent voice.
    """
    conf = [c for c in (confirming_sources or []) if c in list(sources or [])]
    ops = set(o for o in vetted_operators(conf)
              if o != primary_operator
              and not str(o).startswith("UNRESOLVED-"))
    # A dependent operator is dropped when any of its governing operators is
    # also present, or when a governing operator IS the primary -- in the latter
    # case the registry would be the primary speaking under a second name.
    for dependent, governors in NOT_INDEPENDENT_OF.items():
        if dependent in ops and (ops & governors or primary_operator in governors):
            ops.discard(dependent)
    return sorted(ops)


# --------------------------------------------------------------------------
# witness independence TIERS
# --------------------------------------------------------------------------
# independent_witnesses() counts DISTINCT OPERATORS. That test is necessary and
# not sufficient: two operators can be separate legal entities while one is
# holding a copy the other handed it. Three failure modes were measured directly
# rather than assumed, and they are not equally bad:
#
#   ECHO      rdap.arin.net/registry/autnum/3333 REDIRECTS to rdap.db.ripe.net
#             and serves RIPE's own record. Distinct operator, distinct brand,
#             ZERO independent information. Mechanically detectable -- see
#             echo_violations() -- because the final URL leaves the operator.
#   DEPOSIT   api.crossref.org/works/10.17487/RFC9110 reports publisher
#             "RFC Editor", member 7045: the primary deposited its own record.
#             Crossref stores, validates and resolves it, but did not derive the
#             underlying fact. Same shape for 10.2210 (publisher "Worldwide
#             Protein Data Bank") and for DataCite holding arXiv-deposited DOIs
#             -- which is exactly what the retired arXiv family rested on.
#   DERIVED   the witness independently harvested and re-modelled the primary's
#             data. A harvesting error surfaces as disagreement, so the witness
#             carries real, if correlated, information.
#   ORIGINAL  the witness generates its own observation of the entity. PeeringDB
#             records are authored by the networks (they carry info_traffic,
#             info_ratio, policy_general and irr_as_set, none of which exist in
#             the RIPE database); CAIDA infers from its own BGP collection;
#             FIRST computes EPSS itself; NIST performs its own CVSS and CPE
#             analysis on top of the MITRE record.
#
# This does NOT change the witness count. Collapsing deposit-tier witnesses
# would drop every family to two operators and fail min_operators=3, which would
# be a worse lie than the one being fixed -- it would delete true information
# about who can be checked against whom. It makes the claim auditable: a trap
# now says which tier each witness is and why, so "two independent witnesses"
# can be read at its real strength instead of taken at face value.
WT_ECHO = "echo"
WT_DEPOSIT = "deposit"
WT_DERIVED = "derived"
WT_ORIGINAL = "original"

TIER_RANK = {WT_ECHO: 0, WT_DEPOSIT: 1, WT_DERIVED: 2, WT_ORIGINAL: 3}

# (witness operator, primary operator) -> (tier, measured evidence)
# Pair-specific entries win over the per-operator defaults below.
WITNESS_PROVENANCE_PAIRS = {
    ("American Registry for Internet Numbers", "RIPE NCC"): (
        WT_ECHO,
        "rdap.arin.net/registry/autnum/3333 redirects to rdap.db.ripe.net and "
        "returns name RIPE-NCC-AS; arin.net serves 7018 itself, so the echo is "
        "region-dependent, not a blanket property of ARIN"),
    ("Crossref", "RFC Editor"): (
        WT_DEPOSIT,
        "api.crossref.org/works/10.17487/RFC9110 -> publisher 'RFC Editor', "
        "member 7045, prefix 10.17487: the primary is the depositing member"),
    ("Crossref", "RCSB Protein Data Bank"): (
        WT_DEPOSIT,
        "api.crossref.org/works/10.2210/pdb1tup/pdb -> publisher 'Worldwide "
        "Protein Data Bank', member 7763; RCSB is a wwPDB member"),
    ("DataCite", "Cornell University"): (
        WT_DEPOSIT,
        "arXiv DOIs under 10.48550 are deposited by arXiv itself; "
        "api.crossref.org 404s on the same DOI, confirming a single deposit path"),
}

# witness operator -> (tier, evidence) when no pair-specific rule applies
WITNESS_PROVENANCE = {
    "PeeringDB": (WT_ORIGINAL,
                  "records authored by the networks themselves; carries "
                  "info_traffic, info_ratio, policy_general and irr_as_set, "
                  "which have no counterpart in any RIR database"),
    "CAIDA, University of California San Diego": (
        WT_ORIGINAL,
        "AS Rank is computed from CAIDA's own BGP collection and inferred "
        "customer-cone topology, not fetched from an RIR"),
    "Forum of Incident Response and Security Teams": (
        WT_ORIGINAL,
        "EPSS probability and percentile are modelled by FIRST; no other "
        "operator publishes the same numbers"),
    "US National Institute of Standards and Technology": (
        WT_ORIGINAL,
        "NVD adds its own CVSS vectors and CPE applicability statements on top "
        "of the ingested CVE record"),
    "MITRE Corporation": (WT_ORIGINAL,
                          "MITRE mints the identifier and holds the "
                          "authoritative CNA-supplied description"),
    "Software Heritage (Inria)": (
        WT_DERIVED,
        "independently crawls and archives the upstream VCS; the archive is "
        "Software Heritage's own copy of the source tree, not a registry feed"),
    "Google LLC": (WT_DERIVED,
                   "deps.dev independently harvests registry metadata and "
                   "re-derives the dependency graph"),
    "ecosyste.ms (Open Source Collective)": (
        WT_DERIVED,
        "crawls 60+ package registries on its own schedule and republishes a "
        "normalised version record; it answered every version query that "
        "ClearlyDefined timed out on, which is evidence of a separate pipeline "
        "rather than a shared feed"),
    "Allen Institute for AI": (
        WT_DERIVED,
        "Semantic Scholar harvests and re-models bibliographic records; "
        "coverage is incomplete (12/15 sampled RFC DOIs resolved), which is "
        "itself evidence the pipeline is separate from the publisher's"),
    "Crossref": (WT_DEPOSIT, "DOI records are supplied by the depositing member"),
    "DataCite": (WT_DEPOSIT, "DOI records are supplied by the depositing member"),
    "OurResearch (OpenAlex)": (
        WT_DERIVED, "harvests Crossref, PubMed and repository metadata"),
}

DEFAULT_WITNESS_PROVENANCE = (
    WT_DERIVED, "tier not individually measured; assumed derived pending audit")


def witness_provenance(witness_operator, primary_operator=""):
    """Independence tier for one witness against one primary, with evidence."""
    key = (witness_operator, primary_operator)
    if key in WITNESS_PROVENANCE_PAIRS:
        tier, why = WITNESS_PROVENANCE_PAIRS[key]
    elif witness_operator in WITNESS_PROVENANCE:
        tier, why = WITNESS_PROVENANCE[witness_operator]
    else:
        tier, why = DEFAULT_WITNESS_PROVENANCE
    return {"operator": witness_operator, "tier": tier,
            "rank": TIER_RANK[tier], "evidence": why}


def grade_witnesses(sources, confirming_sources, primary_operator):
    """Per-witness independence grades plus the weakest tier present.

    ``weakest_tier`` is the honest headline: a trap with one original and one
    deposit witness is only as strong as the deposit, if the original is the one
    that turns out to be wrong.
    """
    ops = independent_witnesses(sources, confirming_sources, primary_operator)
    grades = [witness_provenance(o, primary_operator) for o in ops]
    ranks = [g["rank"] for g in grades]
    return {
        "witnesses": grades,
        "n_witnesses": len(grades),
        "weakest_tier": min(grades, key=lambda g: g["rank"])["tier"] if grades else None,
        "n_echo": sum(1 for g in grades if g["tier"] == WT_ECHO),
        "n_deposit": sum(1 for g in grades if g["tier"] == WT_DEPOSIT),
        "n_at_or_above_derived": sum(1 for r in ranks if r >= TIER_RANK[WT_DERIVED]),
    }


def echo_violations(sources, confirming_sources, primary_operator):
    """Refuse any witness graded ECHO against this primary.

    An echo witness is not a weak witness, it is a fake one: the bytes come from
    the primary's own server. This is the only tier that is a hard failure.
    """
    v = []
    for o in independent_witnesses(sources, confirming_sources, primary_operator):
        g = witness_provenance(o, primary_operator)
        if g["tier"] == WT_ECHO:
            v.append(f"R7 witness {o!r} echoes primary {primary_operator!r}: "
                     f"{g['evidence']}")
    return v


# --------------------------------------------------------------------------
# intra-category DISJOINTNESS
# --------------------------------------------------------------------------
# The defect this exists to make unrepeatable: four science-and-technology traps
# that were four seeds of one generator. Measured, they were 98.0-99.3%
# textually identical and cited an identical operator set, so solving one solved
# all four.
#
# The threshold was originally 0.50, placed in the middle of a measured empty
# band of 0.043-0.947. That calibration is void: it was computed with the
# asymmetric SequenceMatcher call that prompt_similarity has since fixed, and
# the asymmetric metric under-reported similarity by up to 0.1151 absolute and
# 5.29x relative depending on argument order.
#
# Recalibrated on the corrected symmetric metric over the same 153 pairs (the
# 14 live traps plus the 4 retired arXiv baselines):
#
#   non-clone pairs   0.3410 min, 0.4256 median, 0.5404 MAX
#   clone pairs       0.9818 MIN, 0.9985 max
#   empty band        [0.5404, 0.9818], width 0.4414 -- the largest gap in the
#                     whole distribution by a factor of 22 over the next one
#
# The threshold is the midpoint of that void. Keeping 0.50 would have put the
# boundary INSIDE the non-clone population and refused genuinely distinct
# prompts: the corrected metric has a hard floor near 0.34 because every
# generated prompt shares the same house grammar (an explicit collection, a
# uniqueness assertion, a report instruction, a format instruction), so a
# similarity of ~0.34-0.54 measures shared style, not a shared question.
CLONE_SIMILARITY_THRESHOLD = 0.76
CLONE_BAND_NON_CLONE_MAX = 0.5404
CLONE_BAND_CLONE_MIN = 0.9818
CLONE_BAND_CALIBRATED_ON = "153 pairs: 14 live catalogue traps + 4 retired arXiv baselines"

# The content-word threshold, calibrated on the same 153 pairs. Against the two
# populations the generators actually produce, it separates far more cleanly
# than the character metric:
#
#                        character metric      content metric
#   non-clone   min        0.3410               0.0000
#               median     0.4239               0.0385
#               MAX        0.5404               0.1132
#   clone       MIN        0.9818               0.8857
#               max        0.9985               0.9524
#   empty band  width      0.4415               0.7725
#
# SCOPE, MEASURED -- read before trusting this gate:
#
# This catches SEED-VARIANT clones (one generator, one token swapped), which is
# the defect that actually shipped. It does NOT catch an adversarial rewording,
# and no lexical metric tested does. probe_reword_attack.py supplied the missing
# population -- 12 hand-written rewordings that preserve the question while
# maximising vocabulary divergence -- and measured, for different-question MAX
# against reworded-clone MIN:
#
#   jaccard    0.0732 vs 0.0732   width  0.0000   (exact touch)
#   overlap    0.1429 vs 0.1364   width -0.0065   (overlap)
#   dice       0.1364 vs 0.1364   width  0.0000
#   character  0.5404 vs 0.2156   width -0.3248   (inverted)
#
# The character metric is not merely weak here, it is INVERTED: two genuinely
# different generated prompts score 0.5404 while a prompt and its own rewording
# score 0.2156. It ranks style, not question identity. No threshold on any of
# these metrics admits every distinct question and refuses every rewording.
#
# What actually carries the load is structural and wording-independent: a
# rewording still cites the same collection and still answers in the same field,
# so the operator-overlap refusal above and effective_depth() below both catch
# it. Measured in probe_structural_gate.py over the same 12 rewordings: the
# operator-overlap violation fired 4/4, effective_depth collapsed 3 rows to 1 in
# every case, and the text gate fired 0/8. Treat the two similarity numbers as a
# cheap tripwire for the copy-paste case, never as a paraphrase defence.
#
# 0.50 sits inside the void between the two generator-produced populations
# (0.1132 .. 0.8857). It is deliberately NOT lowered to clip part of the
# rewording range: that range was estimated from 12 examples of one author's
# writing, and fitting a boundary to it would be overfitting to that style
# rather than measuring a property of the product.
CLONE_CONTENT_THRESHOLD = 0.50
CLONE_CONTENT_BAND_NON_CLONE_MAX = 0.1132
CLONE_CONTENT_BAND_CLONE_MIN = 0.8857
CLONE_CONTENT_REWORD_MIN_MEASURED = 0.0732
CLONE_CONTENT_CATCHES_REWORDING = False


def content_similarity(a, b):
    """Similarity of the CONTENT words only, with the house grammar removed.

    Character similarity cannot separate "same question, different seed" from
    "different question, same writing style", because every generated prompt is
    built from one scaffolding vocabulary. Measured, that scaffolding alone puts
    a floor of about 0.34 under every pair and lifts unrelated pairs (an airport
    identifier question against a physics-preprint question) to 0.4966.

    This is the sharper test: drop the scaffolding vocabulary, then take the
    Jaccard overlap of what remains. Two seeds of one generator keep almost all
    their content words; two different questions keep almost none.
    """
    wa = {w for w in re.findall(r"[a-z0-9.:/-]+", _norm_prompt(a))
          if w not in _SCAFFOLD_WORDS and len(w) > 2}
    wb = {w for w in re.findall(r"[a-z0-9.:/-]+", _norm_prompt(b))
          if w not in _SCAFFOLD_WORDS and len(w) > 2}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# Words carrying the house grammar rather than the question. Kept explicit so
# the exclusion list is auditable rather than an opaque stopword import.
_SCAFFOLD_WORDS = frozenset("""
the that this those these and but for with from into than then only alone other
others without out over under each every any all one two both same single
exactly more most less least than report give consider check confirm verify
answer answering before after against listing lists list listed records record
record's publishes published publishes publishing publication maintains
maintain maintained separately together consider considers considering
distinct number numbers digits letters words word form format spells spelled
spelling exact exactly its it their they them there here which who whom whose
what when where how many much has have had been being are was were will would
can could may might must shall should does did doing done make makes made
carry carries carrying carried run runs running ran sits sit sitting
attach attached attaches carry carries name names named call called
data database index registry series entries entry item items
""".split())

# Hard-fail here. Elsewhere the same test only warns: applying it retroactively
# across the whole pool admits one trap per connected component and cuts 14 -> 3.
HARD_DISJOINT_CATEGORIES = {"science and technology"}


def source_keys(sources):
    """Comparable source identities, with CDN-hosted assets folded to operator.

    ``davidmegginson.github.io/ourairports-data`` and
    ``raw.githubusercontent.com/jpatokal/openflights`` are two registrable
    domains but not two independent sources, so an override match collapses to
    the operator instead of the host it happens to be parked on.
    """
    out = set()
    for s in sources or []:
        low = str(s).lower()
        hit = next((op for prefix, op in SOURCE_OVERRIDES if prefix in low), None)
        out.add(f"operator:{hit}" if hit else registrable_domain(s))
    out.discard("")
    return out


def _norm_prompt(text):
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def prompt_similarity(a, b):
    """Character-level similarity of two prompts in [0, 1]. Order-independent.

    This used to be a bare `SequenceMatcher(None, a, b).ratio()`, which is not
    symmetric, and the asymmetry was large: measured over the 153 prompt pairs
    in the catalogue plus the retired arXiv baseline, 136 pairs returned a
    different number depending on argument order, with a maximum absolute gap of
    0.1151 and a worst multiplicative disagreement of 5.29x. The same four
    science-and-technology prompts scored 0.1375 from one call site and 0.0405
    from another purely because the arguments were passed in a different order.

    Two independent causes, both addressed here:

    1. `autojunk`. SequenceMatcher treats elements of the SECOND argument that
       occur in more than 1% of positions as junk once that sequence reaches 200
       elements. Prose is mostly such characters, so each order discards a
       different character set. Disabling it cuts the maximum gap from 0.1151 to
       0.0440.
    2. The matching itself is a greedy recursive longest-match, which stays
       mildly order-dependent even with autojunk off (128 of 153 pairs still
       differ). Only symmetrisation removes this.

    `max` is the correct symmetrisation for a refusal boundary: a pair is a
    clone if EITHER direction says so, so the gate cannot be evaded by argument
    order. No verdict on the current corpus changes -- zero pairs straddle the
    0.50 threshold today -- but a maximum gap of 0.1151 is wide enough to flip a
    future pair reported anywhere between 0.39 and 0.50.
    """
    import difflib
    na, nb = _norm_prompt(a), _norm_prompt(b)
    fwd = difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()
    rev = difflib.SequenceMatcher(None, nb, na, autojunk=False).ratio()
    return max(fwd, rev)


def disjointness_violations(trap, admitted, hard=None):
    """Compare one candidate against already-admitted traps in its category.

    Returns (violations, warnings). Inside HARD_DISJOINT_CATEGORIES everything
    lands in violations; elsewhere everything lands in warnings so the finding
    is recorded without demolishing categories built before the rule existed.
    """
    cat = trap.get("category")
    hard = (cat in HARD_DISJOINT_CATEGORIES) if hard is None else bool(hard)
    found = []

    ops_a = set(trap.get("source_operators") or resolve_operators(trap.get("sources")))
    dom_a = source_keys(trap.get("sources"))
    p_a = trap.get("prompt") or ""
    id_a = (trap.get("field"), str(trap.get("answer")))

    for other in admitted or []:
        if other.get("category") != cat:
            continue
        if (other.get("field"), str(other.get("answer"))) == id_a:
            continue  # the same trap, not a collision
        label = f"{other.get('field')!r}/{other.get('answer')!r}"

        shared_ops = ops_a & set(other.get("source_operators")
                                 or resolve_operators(other.get("sources")))
        if shared_ops:
            found.append(f"R8 shares operator(s) {sorted(shared_ops)} with {label}")

        shared_dom = dom_a & source_keys(other.get("sources"))
        if shared_dom:
            found.append(f"R8 shares source domain(s) {sorted(shared_dom)} with {label}")

        p_b = other.get("prompt") or ""

        sim = prompt_similarity(p_a, p_b)
        if sim >= CLONE_SIMILARITY_THRESHOLD:
            found.append(f"R8 prompt similarity {sim:.3f} >= "
                         f"{CLONE_SIMILARITY_THRESHOLD} against {label}")

        # Independent of the character test: two prompts can be worded
        # differently and still ask the same question of the same collection.
        csim = content_similarity(p_a, p_b)
        if csim >= CLONE_CONTENT_THRESHOLD:
            found.append(f"R8 content similarity {csim:.3f} >= "
                         f"{CLONE_CONTENT_THRESHOLD} against {label}")

    return (found, []) if hard else ([], found)


def effective_depth(traps):
    """How many genuinely distinct questions a set of traps holds.

    Traps are grouped into families by (field, frozenset of source operators);
    the count is the number of families, not the number of rows. This is the
    number that should have been on /api/pool: four arXiv rows are depth 1.
    """
    fams = set()
    for t in traps or []:
        ops = frozenset(t.get("source_operators") or resolve_operators(t.get("sources")))
        fams.add((t.get("field"), ops))
    return len(fams)


def check_category(category):
    """Enforce the closed taxonomy. Returns a list of violation strings."""
    if category is None or str(category).strip() == "":
        return ["R6 no category assigned"]
    if category not in _CATSET:
        return [f"R6 category {category!r} is not one of the 16 permitted values"]
    return []


def validate_trap(trap: dict, min_operators=3):
    """Full gate for one trap dict. Returns (ok: bool, violations: list[str])."""
    v = []
    v += check_category(trap.get("category"))
    v += check_sources(
        trap.get("sources"),
        min_operators=min_operators,
        confirming_sources=trap.get("confirming_sources"),
        primary_operator=trap.get("primary_operator"),
    )
    # the prompt text itself must not direct the solver to a banned publisher
    prompt = str(trap.get("prompt") or "").lower()
    for b in BANNED_DOMAINS:
        if b in prompt:
            v.append(f"R4 prompt text references banned domain {b}")
    for phrase, who in (("library of congress", "US Library of Congress"),
                        ("chronicling america", "US Library of Congress"),
                        ("internet archive", "Internet Archive"),
                        ("hathitrust", "HathiTrust"),
                        ("wikipedia", "Wikimedia Foundation"),
                        ("wikidata", "Wikimedia Foundation"),
                        ("wikisource", "Wikimedia Foundation"),
                        ("wikimedia", "Wikimedia Foundation")):
        if phrase in prompt:
            v.append(f"R4 prompt text directs the solver to banned operator {who}")
    return (not v), v


def audit_operators(sources):
    """Diagnostic: which sources fell back to their bare domain (i.e. unmapped)."""
    unmapped = []
    for s in sources or []:
        rd = registrable_domain(s)
        low = str(s).lower()
        if any(p in low for p, _ in SOURCE_OVERRIDES):
            continue
        if rd and rd not in OPERATOR_MAP:
            unmapped.append((s, rd))
    return unmapped
