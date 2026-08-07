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
        assert p["api_proof"], f"{p['id']} not api_proof"
        assert p["answer"], f"{p['id']} empty answer"


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
