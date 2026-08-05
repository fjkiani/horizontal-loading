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

sys.path.insert(0, "/workspace")
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


def test_generate_reverifies_and_rejects_nih():
    # valid vision generation re-verifies live
    r = client.post("/api/generate", json={"trap_class": "vision", "page_id": "V04"})
    assert r.status_code == 200
    assert r.json()["verified"] and r.json()["answer"] == "19410"
    # NIH class is removed -> 400
    r2 = client.post("/api/generate", json={"trap_class": "nih_rank"})
    assert r2.status_code == 400
    # unknown page -> 404
    r3 = client.post("/api/generate", json={"trap_class": "vision", "page_id": "V99"})
    assert r3.status_code == 404


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
