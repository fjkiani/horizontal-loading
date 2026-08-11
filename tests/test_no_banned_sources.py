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
