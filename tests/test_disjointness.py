"""Acceptance tests for source-diversity: the defect that shipped four clones.

The deployed science-and-technology slate was four arXiv prompts drawn from one
primary operator (Cornell University) and two witnesses (DataCite, OurResearch)
shared by all four. Pairwise prompt similarity measured 0.980-0.993. A pool that
reports depth 4 while offering one source family and one question shape has an
effective depth of 1, and a single upstream change -- a rate limit, a schema
edit, an outage -- takes the whole category down at once.

These tests pin the mechanisms that now prevent that: an operator-overlap
refusal, a source-domain-overlap refusal, and a prompt-similarity refusal; plus
the depth accounting that would have made the original defect visible, the
ledger conservation identity across a family retirement, the ground-rules
linter, and the three-run evidence schema.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import evidence as evd  # noqa: E402
import ground_rules as gr  # noqa: E402
import pool_ledger as pl  # noqa: E402
import source_gate as sg  # noqa: E402

CAT = "science and technology"


def _trap(answer, field, sources, prompt, operator, entity="thing"):
    return {
        "category": CAT,
        "field": field,
        "answer": answer,
        "entity": entity,
        "prompt": prompt,
        "sources": list(sources),
        "confirming_sources": list(sources[1:]),
        "primary_operator": operator,
        "source_operators": sorted({sg.resolve_operator(s) for s in sources}),
        "facts": {"landing_pages": ["https://www.rfc-editor.org/rfc/rfc9914.html"]},
    }


# The prompts below are the ones the generators actually emit, not paraphrases.
# Hand-written stand-ins scored 0.594 against each other purely because one
# author wrote both with the same scaffolding, which would have made this file
# assert a property of the fixtures rather than of the product.
VULN_PROMPT = (
    "The National Vulnerability Database operated by the United States National "
    "Institute of Standards and Technology stamps every vulnerability record it "
    "publishes with a publication date and lists the external reference links "
    "attached to that record. Consider only the records that database stamps as "
    "published on 2023-06-14 in Coordinated Universal Time. Exactly one of those "
    "records carries more external reference links than any other record "
    "published that day. Report the CVE identifier of that single record.")
STANDARD_PROMPT = (
    "The RFC Editor maintains a published index of the Request for Comments "
    "series in which every document appears with its publication month and its "
    "length in pages. Consider only the documents that index records as "
    "published in April 2026. Exactly one of those documents runs to more pages "
    "than any other published in that month. Report the RFC number of that "
    "single document.")

VULN = _trap(
    "CVE-2023-34095", "CVE identifier",
    ["https://services.nvd.nist.gov/rest/json/cves/2.0?pubStartDate=2023-06-14",
     "https://cveawg.mitre.org/api/cve/CVE-2023-34095",
     "https://api.first.org/data/v1/epss?cve=CVE-2023-34095"],
    VULN_PROMPT, "NIST")
STANDARD = _trap(
    "9914", "RFC number",
    ["https://www.rfc-editor.org/rfc-index.xml",
     "https://api.crossref.org/works/10.17487/RFC9914",
     "https://api.semanticscholar.org/graph/v1/paper/DOI:10.17487/RFC9914"],
    STANDARD_PROMPT, "RFC Editor")


# --------------------------------------------------------------------------
# 1. intersecting operator sets
# --------------------------------------------------------------------------
def test_shared_operator_is_refused_even_with_different_domains():
    """Two prompts may cite different hosts and still lean on one institution.

    That is the arXiv defect in miniature: arxiv.org and export.arxiv.org are
    different hosts run by the same university, so counting hosts scored the
    slate as diverse when it was not.
    """
    a = _trap("2203.11011", "arXiv identifier",
              ["https://export.arxiv.org/api/query?search_query=cat:cs.LG"],
              "Among the preprints announced in one listing, identify the one "
              "with the greatest number of indexed citations and report its "
              "identifier.", "Cornell University")
    b = _trap("2211.04278", "arXiv identifier",
              ["https://arxiv.org/list/cs.CL/2211"],
              "Among the manuscripts posted in a distinct listing window, "
              "determine which accumulated the highest citation count and give "
              "its accession string.", "Cornell University")
    viol, _warn = sg.disjointness_violations(b, [a], hard=True)
    assert any("operator" in v for v in viol), viol

    soft, softwarn = sg.disjointness_violations(b, [a], hard=False)
    assert not soft, "outside a hard-disjoint category this must warn, not refuse"
    assert any("operator" in w for w in softwarn), softwarn


# --------------------------------------------------------------------------
# 2. disjoint operators, one shared domain
# --------------------------------------------------------------------------
def test_one_shared_source_domain_is_refused():
    """A shared host is a shared failure mode whoever is named as primary."""
    a = _trap("9914", "RFC number",
              ["https://www.rfc-editor.org/rfc-index.xml",
               "https://api.crossref.org/works/10.17487/RFC9914"],
              "Among the standards documents published in one calendar month, "
              "identify the longest by page count and report its number.",
              "RFC Editor")
    b = _trap("11.3.0", "package version",
              ["https://pypi.org/pypi/pillow/json",
               "https://api.crossref.org/works/10.5555/x"],
              "Among the releases of a single library, determine which shipped "
              "the greatest number of distributed artefacts and report the "
              "version string.", "Python Software Foundation")
    assert a["primary_operator"] != b["primary_operator"]
    viol, _ = sg.disjointness_violations(b, [a], hard=True)
    assert viol, "a shared witness host must not pass"
    assert any("crossref" in v.lower() for v in viol), viol


# --------------------------------------------------------------------------
# 3. prompt similarity band
# --------------------------------------------------------------------------
def test_near_identical_prompt_refused_and_distinct_prompt_admitted():
    """The threshold sits in a measured empty band, not at a round number.

    Within-category similarity on the deployed pool ran 0.947-0.999 and
    cross-category ran 0.016-0.043. Nothing was observed between, so 0.50
    separates the two populations without splitting either.
    """
    base = STANDARD
    clone = dict(base)
    clone["answer"] = "9915"
    clone["prompt"] = base["prompt"].replace("April 2026", "May 2026")
    clone["sources"] = ["https://example-registry.test/index",
                        "https://another-registry.test/item"]
    clone["confirming_sources"] = ["https://another-registry.test/item"]
    clone["source_operators"] = ["Another Registry", "Example Registry"]
    clone["primary_operator"] = "Example Registry"
    sim = sg.prompt_similarity(base["prompt"], clone["prompt"])
    assert sim >= 0.90, f"constructed clone should be near-identical, got {sim}"
    viol, _ = sg.disjointness_violations(clone, [base], hard=True)
    assert any("similarity" in v for v in viol), viol

    far_sim = sg.prompt_similarity(base["prompt"], VULN["prompt"])
    assert far_sim < sg.CLONE_SIMILARITY_THRESHOLD, far_sim
    viol2, _ = sg.disjointness_violations(VULN, [base], hard=True)
    assert not any("similarity" in v for v in viol2), viol2


# --------------------------------------------------------------------------
# 4. depth accounting regression
# --------------------------------------------------------------------------
def test_four_arxiv_rows_report_effective_depth_one():
    """The number the dashboard should have shown instead of 4."""
    rows = []
    for i, ans in enumerate(["2203.11011", "2211.04278", "2302.07287",
                             "2403.07505"]):
        rows.append(_trap(
            ans, "arXiv identifier",
            ["https://export.arxiv.org/api/query?start=%d" % i,
             "https://api.datacite.org/dois/10.48550/arXiv." + ans,
             "https://api.openalex.org/works/doi:10.48550/arXiv." + ans],
            "Among the preprints announced in one window, identify the most "
            "cited and report its identifier.", "Cornell University"))
    assert len(rows) == 4
    assert sg.effective_depth(rows) == 1, "four clones are one family"
    assert sg.effective_depth([VULN, STANDARD]) == 2
    assert sg.effective_depth(rows + [VULN, STANDARD]) == 3


# --------------------------------------------------------------------------
# 5. conservation across a family retirement
# --------------------------------------------------------------------------
def test_retiring_a_family_conserves_the_ledger(tmp_path):
    """Retiring must move rows between buckets, never destroy or mint them."""
    led = str(tmp_path / "ledger.json")
    arxiv = [{"category": CAT, "field": "arXiv identifier", "answer": a,
              "entity": "paper", "verdict": "ship", "witness_tier": "gold",
              "primary_operator": "Cornell University"}
             for a in ["2203.11011", "2211.04278", "2302.07287", "2403.07505"]]
    keep = [{"category": "travel", "field": "GeoNames identifier",
             "answer": "6296543", "entity": "airport", "verdict": "ship",
             "witness_tier": "gold", "primary_operator": "OpenFlights"}]
    pl.upsert(arxiv + keep, path=led)

    before = pl.status(path=led)
    total_before = sum(c["n_total"] for c in before["categories"])
    assert total_before == 5

    # Burn one first, so the test proves retirement does not rewrite history.
    rec, _ = pl.serve(CAT, "req-1", path=led)
    pl.book_minted(rec["trap_id"], "req-1", path=led)

    ids = [pl.trap_id(t["category"], t["field"], t["answer"]) for t in arxiv]
    hit = pl.retire(ids, "superseded: single-source family", path=led)
    assert len(hit) == 4

    after = pl.status(path=led)
    assert sum(c["n_total"] for c in after["categories"]) == total_before, \
        "retirement must not delete rows"
    row = [c for c in after["categories"] if c["category"] == CAT][0]
    assert row["n_total"] == 4
    assert row["n_retired"] == 4, "all four arXiv rows read as retired"
    assert row["n_available"] == 0 and row["exhausted"] is True
    for c in after["categories"]:
        assert (c["n_available"] + c["n_served"] + c["n_burned"]
                + c["n_retired"]) == c["n_total"], f"{c['category']} leaks rows"


# --------------------------------------------------------------------------
# 6. ground-rules linter
# --------------------------------------------------------------------------
@pytest.mark.parametrize("mutate,rule", [
    (lambda t: t.__setitem__(
        "prompt", t["prompt"].replace("Report the RFC number",
                                      "You must report to us the RFC number")),
     "R4"),
    (lambda t: t.__setitem__("answer", "9914\u2013A"), "R3"),
    (lambda t: t.__setitem__(
        "answer", "Request for Comments number nine thousand nine hundred "
                  "and fourteen inclusive of appendices"), "R2"),
    (lambda t: t.__setitem__(
        "prompt", "Is the longest standards document published in one stated "
                  "calendar month the one numbered 9914, considering only "
                  "documents the editor indexes and two further registries "
                  "confirm with the same recorded page count for that same "
                  "document in that same stated month of publication?"), "R9"),
])
def test_linter_rejects_each_mechanical_violation(mutate, rule):
    t = dict(STANDARD)
    t["facts"] = dict(STANDARD["facts"])
    mutate(t)
    # A string replace that silently matches nothing would leave the trap clean
    # and turn this into a test of the fixture instead of a test of the linter.
    assert (t["prompt"], t["answer"]) != (STANDARD["prompt"], STANDARD["answer"]), \
        "the mutation did not change the trap"
    out = gr.lint_trap(t, others=(), check_links=False)
    assert out["ok"] is False, out
    assert any(v.startswith(rule) for v in out["violations"]), out["violations"]


def test_linter_rejects_a_reused_domain_and_passes_the_clean_trap():
    clean = gr.lint_trap(STANDARD, others=(), check_links=False)
    assert clean["ok"] is True, clean["violations"]
    reused = gr.lint_trap(STANDARD, others=[dict(STANDARD, answer="9915")],
                          check_links=False)
    assert reused["ok"] is False
    assert any(v.startswith("R7") for v in reused["violations"]), reused


def test_submittable_requires_explicit_human_sign_off():
    """Five of the eleven rules are claims about what a person did.

    Rule 5 (the answer was found before the prompt was written) and rule 8
    (the test evidence is honest) cannot be decided by a machine, so the
    linter must refuse to assert them on a human's behalf.
    """
    out = gr.lint_trap(STANDARD, others=(), check_links=False)
    assert out["ok"] is True
    assert out["submittable"] is False, "clean lint alone is not submittable"
    assert set(out["sign_off_missing"]) == set(gr.SIGN_OFF_FIELDS)
    signed = gr.lint_trap(STANDARD, others=(), check_links=False,
                          sign_off={k: True for k in gr.SIGN_OFF_FIELDS})
    assert signed["submittable"] is True


# --------------------------------------------------------------------------
# 7. three-run evidence schema
# --------------------------------------------------------------------------
def test_fewer_than_three_runs_cannot_be_marked_evidenced():
    ws = evd.blank_worksheet(STANDARD)
    assert evd.validate_evidence(ws)[0] is False

    for i in range(evd.RUN_SLOTS - 1):
        ws["runs"][i].update(chat_share_url=f"https://chat.example.test/s/{i}",
                             model="model-x", model_answer_verbatim="9915",
                             correct=False)
    part_ok, part_problems = evd.validate_evidence(ws)
    assert part_ok is False, "two of three runs is not evidence"
    assert any("run slots are populated" in p for p in part_problems), \
        part_problems

    ws["runs"][evd.RUN_SLOTS - 1].update(
        chat_share_url="https://chat.example.test/s/last", model="model-x",
        model_answer_verbatim="the answer is 9914", correct=True)
    full_ok, full_problems = evd.validate_evidence(ws)
    assert full_ok is True, full_problems
    s = evd.summarise(ws)
    assert s["n_correct"] == 1 and s["n_incorrect"] == 2
    assert s["stumped_all_runs"] is False


def test_duplicate_share_links_and_unquoted_answers_are_refused():
    """Rule 8 is about honesty, so the two cheapest ways to fake it are closed."""
    ws = evd.blank_worksheet(STANDARD)
    for i in range(evd.RUN_SLOTS):
        ws["runs"][i].update(chat_share_url="https://chat.example.test/s/same",
                             model="model-x", model_answer_verbatim="9915",
                             correct=False)
    dup_ok, dup_problems = evd.validate_evidence(ws)
    assert dup_ok is False
    assert any("share" in p.lower() or "link" in p.lower()
               for p in dup_problems), dup_problems

    ws2 = evd.blank_worksheet(STANDARD)
    for i in range(evd.RUN_SLOTS):
        ws2["runs"][i].update(chat_share_url=f"https://chat.example.test/s/{i}",
                              model="model-x",
                              model_answer_verbatim="totally different text",
                              correct=True)
    lie_ok, lie_problems = evd.validate_evidence(ws2)
    assert lie_ok is False
    assert any("correct" in p.lower() for p in lie_problems), lie_problems
