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
