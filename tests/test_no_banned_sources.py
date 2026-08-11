"""No generator module may name a banned domain in a source URL.

Why this test exists. gen_legal was given a Library of Congress tier as a
fallback text of record when Cornell LII 404s. It generated correctly, passed
py_compile, passed the whole existing suite, and was refused in production at
T6_gate with:

    R4 banned source https://www.loc.gov/collections/united-states-reports/
    ?q=520%20U.S.%2083 (banned domain: loc.gov)

loc.gov is on source_gate.BANNED_DOMAINS by design -- it is audit defect D1,
and archive_banned_corpus.py exists to retire every prompt the ban invalidated.
loc.gov appears in OPERATOR_MAP only so the operator ban can FIRE.

Nothing caught it earlier because the probe that blessed the change called the
generator directly and never ran sg.validate_trap, and because no existing test
exercises a seed whose volume Cornell fails to serve. Both gaps are cheap to
close statically: a banned domain must never appear as a URL literal in a
generator module, whether or not a test happens to reach that branch.
"""
from __future__ import annotations

import pathlib
import re

import pytest

import source_gate as sg

HERE = pathlib.Path(__file__).resolve().parent.parent
GEN_MODULES = ("category_traps.py", "gen_v2.py", "gen_v3.py", "gen_v4.py",
               "source_gate.py", "seed_roster.py")

# A banned substring is legitimate inside source_gate's own ban list and inside
# a comment that explains the ban. Only URL literals are a defect.
_URL = re.compile(r"""["'](?:https?://|//)[^"'\s]+["']""")

# Domains enforced by the ratchet rather than the strict literal test.
_RATCHETED = ("wikipedia.org", "wikidata.org", "wikimedia.org",
              "wikisource.org", "wikiquote.org", "wikivoyage.org",
              "wiktionary.org", "wikibooks.org", "wikinews.org",
              "wikiversity.org")


def _url_literals(text):
    """Every http(s) URL literal in the file, with its 1-based line number."""
    out = []
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue  # a comment cannot become a source at runtime
        for m in _URL.finditer(line):
            out.append((i, m.group(0).strip("\"'")))
    return out


@pytest.mark.parametrize("mod", GEN_MODULES)
def test_no_banned_domain_in_url_literals(mod):
    path = HERE / mod
    if not path.exists():
        pytest.skip(f"{mod} not present")
    text = path.read_text(encoding="utf8")
    bad = []
    for lineno, url in _url_literals(text):
        for b in sg.BANNED_DOMAINS:
            if b in _RATCHETED:
                # Wikimedia was banned long after these modules were written and
                # 40 historical literals predate the ban. Deleting all of them in
                # one pass is riskier than the defect: the gate already refuses
                # any trap that cites them at runtime, which the post-ban sweep
                # confirmed (business, education, politics, sports and tv all
                # dropped to hold or unavailable). These domains are instead held
                # by test_wikimedia_literal_count_does_not_increase, which fails
                # on any commit that ADDS one.
                continue
            if b in url:
                bad.append(f"{mod}:{lineno} cites banned domain {b!r} -> {url}")
    assert not bad, (
        "generator modules must not build URLs on banned domains; "
        "source_gate rule R4 refuses the trap at T6_gate:\n  "
        + "\n  ".join(bad))


def test_banned_list_still_contains_the_domains_that_caused_d1():
    """Guard the guard: if the ban is ever relaxed, this test must be the thing
    that fails, not a production refusal."""
    for dom in ("loc.gov", "archive.org", "hathitrust.org"):
        assert dom in sg.BANNED_DOMAINS, f"{dom} dropped from BANNED_DOMAINS"
    assert "US Library of Congress" in sg.BANNED_OPERATORS
    assert "Internet Archive" in sg.BANNED_OPERATORS


def test_operator_map_bans_are_reachable():
    """Every banned operator must be reachable from some OPERATOR_MAP entry,
    otherwise the operator ban silently never fires."""
    reachable = set(sg.OPERATOR_MAP.values())
    unreachable = sorted(op for op in sg.BANNED_OPERATORS
                         if op not in reachable)
    assert not unreachable, (
        "banned operators with no OPERATOR_MAP entry can never be detected: "
        + ", ".join(unreachable))


def test_a_loc_gov_source_is_actually_refused():
    """End-to-end on the gate itself, using the exact URL production refused."""
    url = ("https://www.loc.gov/collections/united-states-reports/"
           "?q=520%20U.S.%2083&fo=json&c=10")
    bad = sg.banned_violations([url])
    assert bad, "the URL that production refused must still be refused"
    assert any("loc.gov" in reason for _, reason in bad)


# ---------------------------------------------------------------------------
# Wikimedia ban (D14/D15).
#
# travelbear.json measured why this is not a stylistic preference: the travel
# generator read its ANSWER out of Wikidata claim P1566 behind a guard that
# substring-tested a single name token, so rewriting one datavalue in one live
# entity response repointed the shipped answer from GeoNames 6296543 to 656220
# and the trap still scored verdict=ship, witness_tier=gold, 0 of 13 tests
# failing. One upstream edit silently owned the benchmark.
# ---------------------------------------------------------------------------
_WIKIMEDIA = ("wikipedia.org", "wikidata.org", "wikimedia.org", "wikisource.org",
              "wikiquote.org", "wikivoyage.org", "wiktionary.org",
              "wikibooks.org", "wikinews.org", "wikiversity.org")


@pytest.mark.parametrize("domain", _WIKIMEDIA)
def test_every_wikimedia_domain_is_banned(domain):
    assert domain in sg.BANNED_DOMAINS, f"{domain} must be banned"


@pytest.mark.parametrize("url", [
    "https://www.wikidata.org/wiki/Q850269",
    "https://en.wikipedia.org/wiki/Ivalo_Airport",
    "https://en.m.wikipedia.org/wiki/Ivalo_Airport",   # mobile subdomain
    "https://commons.wikimedia.org/wiki/File:X.jpg",
    "https://en.wikisource.org/wiki/Page",
])
def test_wikimedia_urls_are_refused(url):
    v = sg.banned_violations([url])
    assert v, f"{url} must be refused"


@pytest.mark.parametrize("url", [
    "https://www.pcgamingwiki.com/wiki/Half-Life",   # not Wikimedia
    "https://davidmegginson.github.io/ourairports-data/airports.csv",
    "https://sws.geonames.org/6296543/",
    "https://aviationweather.gov/api/data/stationinfo?ids=SKIP&format=json",
])
def test_the_ban_does_not_overreach(url):
    assert not sg.banned_violations([url]), f"{url} must NOT be refused"


def test_a_wikimedia_sourced_trap_is_actually_refused():
    """End to end: the exact trap shape that scored ship/gold must now fail."""
    trap = {
        "category": "travel", "field": "GeoNames identifier", "answer": "6296543",
        "primary_operator": "OpenFlights",
        "sources": ["https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat",
                    "https://davidmegginson.github.io/ourairports-data/airports.csv",
                    "https://www.wikidata.org/wiki/Q850269",
                    "https://sws.geonames.org/6296543/"],
        "confirming_sources": ["https://www.wikidata.org/wiki/Q850269"],
        "prompt": "x" * 80,
    }
    ok, viol = sg.validate_trap(trap)
    assert not ok
    assert any("wikidata.org" in v for v in viol), viol


def test_no_shipped_artifact_cites_wikimedia():
    """The catalog the SPA serves must be clean, not merely the source tree."""
    p = HERE / "web" / "public" / "catalog.json"
    if not p.exists():
        pytest.skip("catalog not baked")
    blob = p.read_text(encoding="utf8").lower()
    hits = [d for d in _WIKIMEDIA if d in blob]
    assert not hits, f"catalog.json still cites {hits}"


# Ratchet. Mass surgery on the historical literals is not required -- the gate
# is the enforcement point -- but the count must never GROW. Counting reuses
# _url_literals(), so comments explaining the ban do not inflate the baseline
# and only real URL literals are held.
# Measured after the travel and geography re-plumbs removed their literals.
_BASELINE = {"category_traps.py": 17, "gen_v2.py": 15, "gen_v3.py": 6, "gen_v4.py": 0}


def _wikimedia_literals(mod):
    text = (HERE / mod).read_text(encoding="utf8")
    return [f"{mod}:{ln} {u}" for ln, u in _url_literals(text)
            if any(d in u for d in _RATCHETED)]


@pytest.mark.parametrize("fname,cap", sorted(_BASELINE.items()))
def test_wikimedia_literal_count_does_not_increase(fname, cap):
    hits = _wikimedia_literals(fname)
    assert len(hits) <= cap, (
        f"{fname} now holds {len(hits)} Wikimedia URL literals, up from the "
        f"{cap} baseline. Wikimedia is a banned source (travelbear.json: the "
        f"travel answer was rewritable through Wikidata P1566 and still scored "
        f"ship/gold). Route the witness elsewhere.\n  " + "\n  ".join(hits))
