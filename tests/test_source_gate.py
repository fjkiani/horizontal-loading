"""Tests for the hardened banned-source / operator / taxonomy gate.

These exist because the previous gate passed a corpus it should have rejected:
it had no loc.gov entry, and it counted URL strings rather than publishers, so
three paths on one host scored as three independent sources.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import source_gate as sg  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# operator resolution
# --------------------------------------------------------------------------
def test_three_paths_on_one_host_is_one_operator():
    """The exact defect that let the original corpus ship."""
    srcs = [
        "https://www.loc.gov/resource/sn83030214/1900-01-01/ed-1/?sp=1",
        "https://www.loc.gov/item/sn83030214/1900-01-01/ed-1/",
        "https://www.loc.gov/newspapers/sn83030214/",
    ]
    ops = sg.resolve_operators(srcs)
    assert len(ops) == 1
    assert "US Library of Congress" in ops
    v = sg.check_sources(srcs, confirming_sources=srcs[:1])
    assert any("only 1 independent operator" in x for x in v)


def test_subdomain_does_not_create_a_second_operator():
    srcs = ["https://www.loc.gov/item/x/", "https://chroniclingamerica.loc.gov/lccn/y/"]
    assert len(sg.resolve_operators(srcs)) == 1


def test_wikidata_and_wikipedia_are_one_operator():
    srcs = ["https://www.wikidata.org/wiki/Q7186",
            "https://en.wikipedia.org/wiki/Marie_Curie",
            "https://api.nobelprize.org/2.1/laureates"]
    ops = sg.resolve_operators(srcs)
    assert len(ops) == 2, ops
    assert ops["Wikimedia Foundation"] and len(ops["Wikimedia Foundation"]) == 2
    v = sg.check_sources(srcs, confirming_sources=srcs[:1])
    assert any("only 2 independent operator" in x for x in v)


def test_nih_properties_collapse_to_one_operator():
    """ClinicalTrials.gov, PubMed and RePORTER are all NIH."""
    srcs = ["https://clinicaltrials.gov/api/v2/studies",
            "https://api.reporter.nih.gov/v2/projects/search",
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"]
    assert len(sg.resolve_operators(srcs)) == 1


def test_distinct_federal_agencies_are_distinct_operators():
    srcs = ["https://clinicaltrials.gov/api/v2/studies",
            "https://api.fda.gov/drug/label.json",
            "https://www.clinicaltrialsregister.eu/ctr-search/"]
    assert len(sg.resolve_operators(srcs)) == 3
    assert sg.check_sources(srcs, confirming_sources=srcs[1:2],
                            primary_operator="US National Institutes of Health") == []


# --------------------------------------------------------------------------
# banned sources
# --------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://www.loc.gov/item/x/",
    "https://chroniclingamerica.loc.gov/lccn/y/",
    "https://web.archive.org/web/2020/http://example.com",
    "https://babel.hathitrust.org/cgi/pt?id=x",
    "https://www.pro-football-reference.com/players/A/x.htm",
])
def test_banned_domains_are_rejected(url):
    assert sg.banned_violations([url]), url


def test_open_library_is_banned_by_operator_not_by_string():
    """openlibrary.org contains no banned substring but is an Internet Archive property."""
    url = "https://openlibrary.org/isbn/9780140328721.json"
    assert "archive.org" not in url
    viols = sg.banned_violations([url])
    assert viols and "banned operator: Internet Archive" in viols[0][1]


def test_clean_sources_are_not_flagged():
    srcs = ["https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Assets.json",
            "https://api.gleif.org/api/v1/lei-records",
            "https://en.wikipedia.org/wiki/Apple_Inc."]
    assert sg.banned_violations(srcs) == []
    assert sg.check_sources(
        srcs, confirming_sources=[srcs[1]],
        primary_operator="US Securities and Exchange Commission") == []


# --------------------------------------------------------------------------
# answer-confirming source
# --------------------------------------------------------------------------
def test_missing_confirming_source_is_a_violation():
    srcs = ["https://api.fda.gov/a", "https://clinicaltrials.gov/b",
            "https://www.clinicaltrialsregister.eu/c"]
    v = sg.check_sources(srcs, confirming_sources=None)
    assert any("no answer-confirming source asserted" in x for x in v)


def test_confirming_source_must_be_one_of_the_listed_sources():
    srcs = ["https://api.fda.gov/a", "https://clinicaltrials.gov/b",
            "https://www.clinicaltrialsregister.eu/c"]
    v = sg.check_sources(srcs, confirming_sources=["https://elsewhere.example/z"])
    assert any("no listed source independently confirms" in x for x in v)


# --------------------------------------------------------------------------
# taxonomy
# --------------------------------------------------------------------------
def test_taxonomy_has_the_sixteen_required_categories():
    assert len(sg.CATEGORIES) == 16
    for expected in ("science and technology", "art", "business",
                     "celebrities/public figures", "education", "finance",
                     "geography", "health and medicine", "history", "legal",
                     "politics", "shopping", "sports", "travel",
                     "tv shows and movies", "video games"):
        assert expected in sg.CATEGORIES


@pytest.mark.parametrize("cat", [None, "", "Science", "misc", "History"])
def test_invalid_category_is_rejected(cat):
    assert sg.check_category(cat)


def test_valid_category_passes():
    assert sg.check_category("video games") == []


# --------------------------------------------------------------------------
# prompt text
# --------------------------------------------------------------------------
def test_prompt_text_directing_to_a_banned_operator_is_rejected():
    trap = {
        "category": "history",
        "sources": ["https://api.fda.gov/a", "https://clinicaltrials.gov/b",
                    "https://www.clinicaltrialsregister.eu/c"],
        "confirming_sources": ["https://api.fda.gov/a"],
        "prompt": "Consult the front page digitized by the Library of Congress and read it.",
    }
    ok, v = sg.validate_trap(trap)
    assert not ok
    assert any("banned operator US Library of Congress" in x for x in v)


# --------------------------------------------------------------------------
# regression against the real retired corpus
# --------------------------------------------------------------------------
def test_every_retired_prompt_fails_the_gate():
    """The 23 archived prompts must all be rejected, and for the stated reason."""
    path = os.path.join(HERE, "retired_corpus.json")
    if not os.path.exists(path):
        pytest.skip("retired_corpus.json not present")
    retired = json.load(open(path))
    records = retired["generated"] + retired["curated"]
    assert len(records) == 23, f"expected 23 retired prompts, found {len(records)}"
    for r in records:
        trap = {"category": None, "sources": r["sources"],
                "confirming_sources": None, "prompt": r.get("prompt", "")}
        ok, v = sg.validate_trap(trap)
        assert not ok
        assert any("loc.gov" in x or "Library of Congress" in x for x in v), (r.get("id"), v)


def test_served_corpus_contains_no_banned_source():
    """Whatever is currently served must be clean. Empty counts as clean."""
    for name in ("generated_pool.json", "author_payloads.json"):
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        data = json.load(open(path))
        entries = data if isinstance(data, list) else list(data.values())
        for e in entries:
            assert sg.banned_violations(e.get("sources")) == [], (name, e.get("sources"))


# --------------------------------------------------------------------------
# R3c: a source run by the primary operator is not a witness
# --------------------------------------------------------------------------
_SELF = ["https://collectionapi.metmuseum.org/public/collection/v1/objects/437416",
         "https://www.wikidata.org/wiki/Q19905220",
         "https://api.europeana.eu/record/v2/search.json?query=rembrandt"]


def test_primary_operator_cannot_confirm_itself():
    """The Met object record confirming a Met accession number is not evidence."""
    v = sg.check_sources(_SELF, confirming_sources=[_SELF[0]],
                         primary_operator="Metropolitan Museum of Art")
    assert any(x.startswith("R3c") for x in v), v


def test_a_second_operator_rescues_a_self_confirmation():
    v = sg.check_sources(_SELF, confirming_sources=[_SELF[0], _SELF[1]],
                         primary_operator="Metropolitan Museum of Art")
    assert v == [], v


def test_unnamed_primary_operator_is_itself_a_violation():
    """Silence about the primary makes the self-confirmation check unenforceable."""
    v = sg.check_sources(_SELF, confirming_sources=[_SELF[0]])
    assert any(x.startswith("R3c") for x in v), v


def test_independent_witnesses_excludes_the_primary():
    w = sg.independent_witnesses(_SELF, [_SELF[0], _SELF[1]],
                                 "Metropolitan Museum of Art")
    assert w == ["Wikimedia Foundation"], w
    assert sg.independent_witnesses(_SELF, [_SELF[0]],
                                    "Metropolitan Museum of Art") == []


def test_confirming_source_not_listed_in_sources_is_ignored():
    """A witness the trap never cites cannot be counted."""
    w = sg.independent_witnesses(_SELF, ["https://example.org/elsewhere"],
                                 "Metropolitan Museum of Art")
    assert w == []


def test_europe_pmc_is_embl_ebi_not_nih():
    """Europe PMC overlaps NIH on content but is a separate operator."""
    u = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
         "query=ACCESSION_ID%3A%22NCT00021697%22&format=json")
    assert sg.resolve_operator(u) == "EMBL-EBI"
    assert sg.resolve_operator("https://clinicaltrials.gov/api/v2/studies") != "EMBL-EBI"
