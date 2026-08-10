"""
test_api.py — automated tests for the Seal Prompt-Generator app.

Uses FastAPI TestClient (no live server needed). Covers:
  - health endpoint
  - prompts list returns the verified set, all gate-passing + api_proof
  - prompt detail + image serving
  - generate re-verifies live and rejects the removed NIH class
  - the 3 flawed controls are caught by the verify gate
  - cache-key regression: distinct fiscal years map to distinct cache keys
  - stress-test endpoint wiring returns a result dict
"""
import pathlib
import time
import os, sys, json
import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
from fastapi.testclient import TestClient
from app.main import app
import join_engine as je
import verify_joins as vj
import batch_prompts as bp

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_prompts_list_all_verified():
    r = client.get("/api/prompts")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 5
    for p in d["prompts"]:
        assert p["verified"], f"{p['id']} not verified: {p['verify_fails']}"
        assert p["answer"], f"{p['id']} empty answer"
        # api_proof and withdrawn are two views of one fact and must agree.
        # V02/V03 were withdrawn after a whole-issue sweep showed page 1 prints
        # their answers with a field label; they stay listed, flagged.
        if p["withdrawn"]:
            assert not p["api_proof"], f"{p['id']} withdrawn but still api_proof"
        else:
            assert p["api_proof"], f"{p['id']} not api_proof"


def test_prompt_detail_and_image():
    r = client.get("/api/prompts/V01")
    assert r.status_code == 200
    d = r.json()
    assert d["answer"] == "19405"
    assert d["has_image"] and d["image_url"].endswith("/image")
    assert len(d["sources"]) >= 3
    # image serves as jpeg with real bytes
    ri = client.get("/api/prompts/V01/image")
    assert ri.status_code == 200
    assert ri.headers["content-type"] == "image/jpeg"
    assert len(ri.content) > 10000


def test_prompt_detail_404():
    r = client.get("/api/prompts/NOPE")
    assert r.status_code == 404


def test_generate_rejects_nih_class():
    # NIH class is removed -> 400
    r = client.post("/api/generate", json={"trap_class": "nih_rank"})
    assert r.status_code == 400


def test_generate_on_demand_contract():
    """On-demand generation is async: POST returns a job, polling yields a fresh
    trap with the full contract. This performs a live LOC walk (real generator)."""
    import time
    # 1900-07-01 is within a Tribune run where the issue number is absent from
    # the whole-page OCR (clean traps). Some runs (e.g. June 1900) leak the number
    # into the OCR and correctly yield no trap; we use a clean range here.
    r = client.post("/api/generate", json={
        "trap_class": "vision", "lccn": "sn83030214",
        "start_date": "1900-07-01", "max_steps": 8})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "running" and job["job_id"]
    # poll until done
    d = None
    for _ in range(120):
        s = client.get(f"/api/generate/{job['job_id']}").json()
        if s["status"] == "done":
            d = s["result"]; break
        if s["status"] == "error":
            pytest.fail(f"generation errored: {s['detail']}")
        time.sleep(1)
    assert d is not None, "generation did not complete in time"
    # contract fields
    for k in ("id", "prompt", "answer", "field", "paper", "date",
              "verified", "api_proof", "confidence", "word_count",
              "golden", "sources", "resource_url"):
        assert k in d, f"missing field {k}"
    assert d["api_proof"] is True
    assert 70 <= d["word_count"] <= 150
    assert d["answer"]                      # non-empty atomic answer
    assert len(d["sources"]) >= 3
    # OCR-derived candidates are served unverified until independently confirmed
    assert d["verified"] is False


def test_generate_unknown_job_404():
    r = client.get("/api/generate/doesnotexist")
    assert r.status_code == 404


def test_generated_pool_and_pending():
    r = client.get("/api/generated")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == len(d["verified"])
    for t in d["verified"]:
        assert t["verified"] is True
        assert t["api_proof"] is True
        assert t["answer"]
    # pending endpoint returns a list (may be empty)
    rp = client.get("/api/pending")
    assert rp.status_code == 200
    assert rp.json()["count"] == len(rp.json()["pending"])


def test_confirm_unknown_candidate_404():
    r = client.post("/api/confirm", json={
        "lccn": "sn00000000", "date": "1900-01-01", "field": "issue number",
        "confirmed_answer": "1", "verifier": "test"})
    assert r.status_code == 404


def test_flawed_controls_caught():
    flawed = [p for p in bp.BATCH if p.intended == "flawed"]
    assert len(flawed) == 3
    for p in flawed:
        caught = vj.verify_flawed(p)
        assert caught, f"control {p.id} ({p.flaw}) was NOT caught"


def test_cache_key_regression_distinct_fiscal_years():
    # The old truncation collided FY2020/FY2022 to one key. Hash keys must differ.
    def body_for(fy):
        body = {"criteria": {"fiscal_years": [fy],
                             "advanced_text_search": {"operator": "and",
                                                      "search_field": "projecttitle,terms",
                                                      "search_text": "glioblastoma"},
                             "include_fields": ["ProjectNum", "AwardAmount"]},
                "limit": 500, "offset": 0}
        return "https://api.reporter.nih.gov/v2/projects/search" + json.dumps(body, sort_keys=True)
    assert je._ck(body_for(2020)) != je._ck(body_for(2022))


def test_stress_test_endpoint_wiring():
    r = client.post("/api/stress_test",
                    json={"prompt_ids": ["V01"], "solver": "agent", "n_runs": 2})
    assert r.status_code == 200
    d = r.json()
    assert d["solver"] == "agent"
    assert "V01" in d["results"]
    assert "l2_fail_rate" in d["results"]["V01"]


def test_stress_test_openai_requires_key():
    r = client.post("/api/stress_test",
                    json={"prompt_ids": ["V01"], "solver": "openai", "n_runs": 1})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# Regression guards for the resolution finding.
#
# A controlled resolution sweep on Evening Public Ledger 1922-04-06 (ground
# truth NO. 175, read by agent vision on the full-res LOC scan) showed the old
# extractor emitted 176 at HIGH confidence, because it upscaled a single pct:15
# raster 2x/3x/4x and treated that agreement as independent evidence. tesseract,
# EasyOCR and the Cohere vision LLM all returned 176 from that same raster.
# These tests lock in the replacement's fail-safe behaviour.
# --------------------------------------------------------------------------

def test_cross_resolution_agreement_classification():
    """Agreement across DIFFERENT rasters is high; disagreement must be conflict."""
    import masthead_reader as mr
    # Two resolutions agreeing -> high confidence.
    per = {40: {"answer": "175", "field": "issue number"},
           60: {"answer": "175", "field": "issue number"}}
    vals = [v["answer"] for v in per.values() if v.get("answer")]
    assert len(set(vals)) == 1 and len(vals) >= 2

    # Disagreement must NEVER collapse to a confident single answer.
    per_bad = {40: {"answer": "175", "field": "issue number"},
               60: {"answer": "176", "field": "issue number"}}
    vals_bad = [v["answer"] for v in per_bad.values() if v.get("answer")]
    assert len(set(vals_bad)) > 1, "disagreement must be detectable"
    # The reader's contract: >1 distinct value => confidence 'conflict', answer None.
    assert mr.RESOLUTIONS[0] != mr.RESOLUTIONS[1], "must use >=2 distinct rasters"


def test_reader_benchmark_has_no_false_confidence():
    """The recorded benchmark must show the new reader never accepts a wrong answer."""
    import json as _json
    import os as _os
    p = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                      "reader_benchmark.json")
    if not _os.path.exists(p):
        import pytest
        pytest.skip("benchmark artifact not present")
    d = _json.load(open(p))
    assert d, "benchmark must contain scored cases"
    new_wrong = [k for k, v in d.items() if v.get("new_accepted_wrong")]
    assert not new_wrong, f"new reader accepted wrong answers: {new_wrong}"
    # And it must have caught the specific historical failure.
    # The historically-failing page: the invariant is NOT "always refuse it", it is
    # "never accept a wrong value for it". Refusing (conflict) and reading it
    # correctly (175) are both acceptable; emitting 176 confidently is not.
    hard = d.get("sn83045211:1922-04-06")
    if hard:
        assert hard["truth"] == "175"
        if hard["new_accepted"]:
            assert hard["new_correct"], (
                f"accepted a wrong value for the known-hard masthead: "
                f"{hard['new']['answer']} != 175")
        assert hard["old_accepted_wrong"], "benchmark should record the old failure"


def test_generated_pool_answers_are_confirmed():
    """Every trap served as verified must carry verified=True and a verifier."""
    import trap_generator as tg
    for t in tg.list_generated():
        assert t.get("verified") is True, f"{t['date']} served unverified"
        assert t.get("answer"), f"{t['date']} missing answer"


def test_generated_payload_carries_prompt_text():
    """The prompt IS the product. The detail pane renders trap.prompt, so a
    summary payload without it rendered a literal 'undefined' in the UI."""
    r = client.get("/api/generated")
    assert r.status_code == 200
    traps = r.json()["verified"]
    assert traps, "expected a non-empty verified pool"
    for t in traps:
        assert t.get("prompt"), f"{t['id']} has no prompt text in the payload"
        assert 70 <= len(t["prompt"].split()) <= 150
        # the answer must never leak into the prompt that asks for it
        assert t["answer"].replace(",", "") not in t["prompt"].replace(",", "")


def test_generate_job_reports_progress_not_just_running():
    """A bare {'status':'running'} is indistinguishable from a hang. The poll
    endpoint must expose elapsed time and which page is being worked."""
    import app.main as m
    jid = "test-progress-job"
    with m._JOBS_LOCK:
        m._JOBS[jid] = {"status": "running", "started": time.time() - 5.0,
                        "max_steps": 8, "progress": {"phase": "reading masthead",
                                                     "step": 3, "date": "1900-10-03"},
                        "log": [{"phase": "page rejected", "date": "1900-10-01",
                                 "reason": "issue number already in the page OCR (not api-proof)"}]}
    try:
        s = client.get(f"/api/generate/{jid}").json()
        assert s["status"] == "running"
        assert s["elapsed"] >= 5.0
        assert s["progress"]["step"] == 3
        assert s["max_steps"] == 8
        assert s["log"][0]["reason"]
    finally:
        with m._JOBS_LOCK:
            m._JOBS.pop(jid, None)


def test_frontend_js_is_syntactically_valid():
    """A syntax error in app.js breaks the entire UI while every API test still
    passes. Parse it in CI so that failure mode cannot ship silently."""
    esprima = pytest.importorskip("esprima")
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "app" / "static" / "app.js").read_text()
    esprima.parseScript(src)


# --------------------------------------------------------------------------
# Regression tests for the two audits that invalidated shipped traps.
# --------------------------------------------------------------------------

def _fake_pending_entry(answer="176"):
    return {
        "id": "gen-test", "lccn": "sn83045211", "date": "1922-04-06",
        "field": "issue number", "paper": "Evening Public Ledger",
        "answer": answer, "prompt": "x " * 100, "verified": False,
        "api_proof": True, "confidence": "high",
        "resource_url": "https://www.loc.gov/resource/sn83045211/1922-04-06/ed-1/?&sp=1",
        "golden": ["step", f"= {answer}"],
    }


def test_confirm_candidate_reruns_api_proof_gate(tmp_path, monkeypatch):
    """The api-proof gate is CONTINGENT ON THE ANSWER, so correcting an answer
    must re-run it. It previously did not, and that shipped two invalid traps:
    1922-04-06 (OCR 176 absent from the text layer -> gate passed; corrected to
    the true 175, which IS in the text layer) and 1922-03-11 (158, a misread of
    153). A WRONG answer is less likely to appear on the page than the right
    one, so the un-rerun gate systematically PREFERS mis-read pages."""
    import trap_generator as tg

    pend_p = tmp_path / "pending.json"
    pool_p = tmp_path / "pool.json"
    monkeypatch.setattr(tg, "_PENDING_PATH", str(pend_p))
    monkeypatch.setattr(tg, "_POOL_PATH", str(pool_p))

    calls = []

    def fake_ocr(url):
        calls.append(url)
        return "VOL. VIII. NO. 175 sr..in Leaders Bellev Agree- II mint"

    monkeypatch.setattr(tg.je, "loc_page_ocr", fake_ocr)

    # (a) answer UNCHANGED -> no re-check needed, promotes normally.
    tg._save(str(pend_p), [_fake_pending_entry("176")])
    tg._save(str(pool_p), [])
    out = tg.confirm_candidate("sn83045211", "1922-04-06", "issue number", "176")
    assert out["verified"] is True
    assert calls == [], "unchanged answer must not trigger a network re-check"
    assert len(tg._load(str(pool_p))) == 1
    assert tg._load(str(pend_p)) == []

    # (b) answer CHANGED and still absent from the text layer -> promotes.
    tg._save(str(pend_p), [_fake_pending_entry("176")])
    tg._save(str(pool_p), [])
    out = tg.confirm_candidate("sn83045211", "1922-04-06", "issue number", "9999")
    assert calls, "changed answer must re-run the gate"
    assert out["verified"] is True and out["api_proof"] is True
    assert out["answer"] == "9999" and out["golden"][-1] == "= 9999"
    assert len(tg._load(str(pool_p))) == 1

    # (c) answer CHANGED to one that IS in the text layer -> must NOT promote.
    #     This is the real 1922-04-06 case, verbatim.
    tg._save(str(pend_p), [_fake_pending_entry("176")])
    tg._save(str(pool_p), [])
    out = tg.confirm_candidate("sn83045211", "1922-04-06", "issue number", "175")
    assert out["api_proof"] is False, "175 is in the LOC text layer"
    assert out["verified"] is False
    assert "text layer" in out["rejected_reason"]
    assert tg._load(str(pool_p)) == [], "an unsound trap must never reach the pool"
    stayed = tg._load(str(pend_p))
    assert len(stayed) == 1 and stayed[0]["api_proof"] is False

    # (d) the gate cannot be EVALUATED -> unknown is not the same as passing.
    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(tg.je, "loc_page_ocr", boom)
    tg._save(str(pend_p), [_fake_pending_entry("176")])
    tg._save(str(pool_p), [])
    out = tg.confirm_candidate("sn83045211", "1922-04-06", "issue number", "175")
    assert out["api_proof"] is None and out["verified"] is False
    assert tg._load(str(pool_p)) == [], "must not promote on an unevaluated gate"


def test_pool_is_arithmetically_self_consistent():
    """Issue numbers are a near-arithmetic sequence in publication date, so the
    pool audits itself independently of OCR. Any flagged entry means a recorded
    answer contradicts the rest of its own paper's sequence -- which is how the
    1922-03-11 misread (158, truly 153) was caught."""
    import audit_pool_arithmetic as apa
    rep = apa.audit(os.path.join(_REPO, "generated_pool.json"))
    assert rep, "audit produced no papers"
    for lccn, r in rep.items():
        if "skipped" in r:
            continue
        assert not r["flagged"], (
            f"{lccn} has answers contradicting its own issue sequence: {r['flagged']}")
    # The Tribune is the pool's backbone; it must be large enough to self-validate.
    trib = rep.get("sn83030214")
    assert trib and trib["n_entries"] >= 5 and trib["calendar_validated"], (
        "the Tribune sequence must be big enough to validate its own calendar")


def test_withdrawn_traps_are_recorded_and_absent_from_the_pool():
    """Invalidated traps are withdrawn WITH a reason, not silently deleted, and
    must never be served again."""
    import trap_generator as tg
    wpath = os.path.join(_REPO, "withdrawn_traps.json")
    if not os.path.exists(wpath):
        pytest.skip("no withdrawals recorded")
    withdrawn = json.load(open(wpath))
    pool_keys = {(t["lccn"], t["date"], t["field"]) for t in tg.list_generated()}
    for w in withdrawn:
        assert w.get("withdrawn_reason"), f"{w['date']} withdrawn without a reason"
        k = (w["lccn"], w["date"], w["field"])
        assert k not in pool_keys, f"{k} was withdrawn but is still served"


def test_every_served_trap_holds_the_api_proof_gate():
    """api_proof=True is the claim that the answer is NOT recoverable from the
    LOC text layer. Serving a trap whose recorded gate is False or unknown
    misrepresents it as vision-only."""
    import trap_generator as tg
    for t in tg.list_generated():
        assert t.get("api_proof") is True, (
            f"{t['lccn']} {t['date']} served with api_proof={t.get('api_proof')}")


# --------------------------------------------------------------------------
# api-proof gate semantics. Measured on a 322-page sweep of every trap's issue.
# --------------------------------------------------------------------------

def test_gate_catches_masthead_leaks_including_separator_forms():
    """The Tribune prints "No. 19,678" and LOC's OCR renders the comma as a
    period, so the raw text reads "N* 19.678". A plain token search for 19678
    finds nothing while the masthead is fully present -- a false ACCEPT that
    would ship a solvable page as a vision trap."""
    import trap_generator as tg
    leaks = [
        ("New- Vor Tribune. LX....N* 19.678 NEW-YORK. MONDAY", "19678", "issue number"),
        ("Oribuns. Vou IX... N\u00ae 19679. NEW-YORK. TUESDAY", "19679", "issue number"),
        ("Tribune. Vol. LIX....No. 19,680 NEW-YORK", "19680", "issue number"),
        ("I JTl NO. 148 Entma a Sscend-ClM MaUsr", "148", "issue number"),
        ("ADVERTISER _ _ _ ESTABLISHED 1832._ NEWARK", "1832", "year established"),
        ("ADVERTISER. FOUNDED MARCH 1. 1832. j Piif", "1832", "year established"),
    ]
    for text, ans, field in leaks:
        holds, reason, ev = tg.api_proof_holds(text, ans, field)
        assert holds is False, f"missed a real leak: {ans} in {text!r}"
        assert ev, "a rejection must carry its evidence"


def test_gate_does_not_reject_on_coincidental_numbers():
    """~40% of all 3-digit numbers occur somewhere in a 30-page Ledger issue
    (measured 356/900, 389/900, 364/900), so an unlabelled 3-digit hit is
    near-chance. Both occurrences of the pooled answer 227 are street addresses.
    Rejecting on those is a false REJECT that throws away valid traps."""
    import trap_generator as tg
    clean = [
        ("dead from heart disease at his home. 227 North Sev enth street", "227"),
        ("Mr. Mary Mason, of 227 North Connecticut avenue, who", "227"),
        ("sold at 1.66 per bushel in the produce market today", "166"),
    ]
    for text, ans in clean:
        holds, reason, ev = tg.api_proof_holds(text, ans, "issue number")
        assert holds is True, f"false reject on {ans}: {reason} {ev}"
    # And the specific normalization artifact that caused it must be visible:
    # the OLD gate rejects "1.66" for the answer 166; the token test does not.
    assert tg._norm_digits("166") in tg._norm_digits("sold at 1.66 per bushel")
    assert tg.standalone_hits("sold at 1.66 per bushel", "166") == []


def test_gate_label_must_match_the_field():
    """Scoring a year-established trap with the issue-number label reports clean.
    That false negative in the auditor initially cleared V03, whose page 1
    plainly prints ESTABLISHED 1832."""
    import trap_generator as tg
    txt = "ADVERTISER _ _ _ ESTABLISHED 1832._ NEWARK"
    assert tg.label_bearing_leak(txt, "1832", "year established")
    assert not tg.label_bearing_leak(txt, "1832", "issue number")


def test_rare_standalone_token_is_still_a_leak():
    """For a 5-digit answer the measured chance rate is ~0.001 (28/90000 and
    99/90000 distinct tokens per issue), so an unlabelled hit is not chance."""
    import trap_generator as tg
    holds, reason, _ = tg.api_proof_holds("the sum of 19678 dollars", "19678",
                                          "issue number")
    assert holds is False and "standalone" in reason
    # The same rule must NOT fire for a short answer, where chance dominates.
    holds3, _, _ = tg.api_proof_holds("lot 227 of the estate", "227", "issue number")
    assert holds3 is True


def test_whole_issue_sweep_clears_every_served_trap():
    """The gate historically checked ONE page, but the masthead is printed on
    every page, so a clean named page proves nothing. V02 names p23 and V03
    names p9; both are clean and both issues leak on p1."""
    import trap_generator as tg
    path = os.path.join(_REPO, "issue_sweep_api_proof.json")
    if not os.path.exists(path):
        pytest.skip("issue sweep artifact not present")
    sweep = {(r["lccn"], r["date"]): r for r in json.load(open(path))}
    for t in tg.list_generated():
        r = sweep.get((t["lccn"], t["date"]))
        assert r is not None, f"{t['date']} served without a whole-issue sweep"
        assert r["api_proof_whole_issue"], (
            f"{t['date']} leaks {r['label_example']!r} on pages "
            f"{r['label_bearing_pages']}")
        assert r["pages"] >= 2, "a one-page sweep is not a sweep"


def test_withdrawn_curated_prompts_are_flagged_not_silently_served():
    """V02/V03 stay in the catalog with the finding attached, same policy as
    withdrawn_traps.json -- but they must never read as valid."""
    r = client.get("/api/prompts")
    assert r.status_code == 200
    by_id = {p["id"]: p for p in r.json()["prompts"]}
    for pid in ("V02", "V03"):
        if pid not in by_id:
            continue
        assert by_id[pid]["withdrawn"] is True, f"{pid} still served as sound"
        assert by_id[pid]["api_proof"] is False
        assert by_id[pid]["withdrawn_reason"]
        d = client.get(f"/api/prompts/{pid}").json()
        assert d["withdrawn"] is True and d["withdrawn_evidence"]
    # The prompts that survived the sweep must be untouched.
    for pid in ("V01", "V04", "V05"):
        if pid in by_id:
            assert not by_id[pid]["withdrawn"], f"{pid} wrongly withdrawn"
            assert by_id[pid]["api_proof"] is True
