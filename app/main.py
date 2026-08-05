"""
main.py — Seal Prompt-Generator API.

Serves the verified API-proof vision traps and runs the stress test. Ground truth is
COMPUTED from live data by join_engine (never declared) and gated by verify_joins.

Endpoints:
  GET  /api/health
  GET  /api/prompts                 -> list of verified prompts (summary)
  GET  /api/prompts/{pid}           -> full detail (prompt, answer, trace, payload/image)
  GET  /api/prompts/{pid}/image     -> the scan image for a vision trap
  POST /api/generate                -> build+verify a prompt from the verified page pool
  POST /api/stress_test             -> run solver+judge over selected prompts
  GET  /                            -> static frontend
"""
from __future__ import annotations
import json, os, sys
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
import batch_prompts as bp
import verify_joins as vj
import stress_test_runner as str_
from app import solvers

WORKSPACE = _REPO
STATIC = os.path.join(os.path.dirname(__file__), "static")
AUTHOR_PATH = os.path.join(WORKSPACE, "author_payloads.json")

app = FastAPI(title="Seal Prompt Generator", version="1.0")


def _load_author():
    return json.load(open(AUTHOR_PATH))


def _resolve_image(path):
    """Resolve a stored image_path portably: absolute as-is, else relative to repo root
    (handles payloads authored with a different absolute root, e.g. a deploy container)."""
    if not path:
        return None
    if os.path.isabs(path) and os.path.exists(path):
        return path
    cand = os.path.join(WORKSPACE, os.path.basename(path))
    return cand if os.path.exists(cand) else (path if os.path.exists(path) else cand)


def _verify_map():
    """Run the gate over the clean batch and return {pid: {pass, fails, answer,...}}."""
    out = {}
    for p in bp.BATCH:
        if p.intended != "clean":
            continue
        res, fails = vj.verify_clean(p)
        out[p.id] = {"pass": not fails, "fails": fails, "answer": res.get("answer"),
                     "n_base": res.get("n_base"), "api_proof": res.get("api_proof", False),
                     "unique": res.get("unique", False)}
    return out


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "seal-prompt-generator"}


@app.get("/api/prompts")
def list_prompts():
    author = _load_author()
    vm = _verify_map()
    items = []
    for pid, rec in author.items():
        v = vm.get(pid, {})
        items.append({
            "id": pid, "domain": rec["domain"], "method": rec["method"],
            "answer": rec["answer"], "n_base": rec["n_base"],
            "api_proof": rec.get("api_proof", False),
            "verified": v.get("pass", False), "verify_fails": v.get("fails", []),
            "exploit": rec.get("exploit", []),
        })
    return {"count": len(items), "prompts": items}


@app.get("/api/prompts/{pid}")
def prompt_detail(pid: str):
    author = _load_author()
    if pid not in author:
        raise HTTPException(404, f"prompt {pid} not found")
    rec = author[pid]
    return {
        "id": pid, "domain": rec["domain"], "method": rec["method"],
        "prompt": rec["prompt"], "answer": rec["answer"], "golden": rec["golden"],
        "n_base": rec["n_base"], "api_proof": rec.get("api_proof", False),
        "exploit": rec.get("exploit", []), "sources": rec.get("sources", []),
        "has_image": bool(rec.get("image_path")),
        "image_url": f"/api/prompts/{pid}/image" if rec.get("image_path") else None,
        "payload": rec["payload"] if not rec.get("image_path") else None,
    }


@app.get("/api/prompts/{pid}/image")
def prompt_image(pid: str):
    author = _load_author()
    rec = author.get(pid)
    if not rec or not rec.get("image_path"):
        raise HTTPException(404, "no image for this prompt")
    path = _resolve_image(rec["image_path"])
    if not path or not os.path.exists(path):
        raise HTTPException(404, "image file missing")
    return FileResponse(path, media_type="image/jpeg")


class GenerateRequest(BaseModel):
    trap_class: str = "vision"          # only 'vision' is supported (verified pool)
    page_id: Optional[str] = None        # one of the verified V01..V05 ids


@app.post("/api/generate")
def generate(req: GenerateRequest):
    """Return a verified prompt from the pool. Novel vision traps are NOT auto-generated
    (each needs a manual OCR-leak + image-legibility check), so generation selects from
    the verified pool and re-runs the gate to confirm it still passes live."""
    author = _load_author()
    if req.trap_class != "vision":
        raise HTTPException(400, "only trap_class='vision' is currently supported; "
                                 "the NIH ranking class was removed as non-reproducible")
    pid = req.page_id or sorted(author.keys())[0]
    if pid not in author:
        raise HTTPException(404, f"page_id {pid} not in verified pool {sorted(author.keys())}")
    # Re-verify live before serving.
    p = next((x for x in bp.BATCH if x.id == pid), None)
    if p is None:
        raise HTTPException(404, f"{pid} not in batch")
    res, fails = vj.verify_clean(p)
    if fails:
        raise HTTPException(422, f"prompt {pid} failed live verification: {fails}")
    rec = author[pid]
    return {"id": pid, "prompt": rec["prompt"], "answer": res["answer"],
            "verified": True, "api_proof": res.get("api_proof", False),
            "golden": rec["golden"], "sources": rec.get("sources", []),
            "image_url": f"/api/prompts/{pid}/image" if rec.get("image_path") else None}


class StressRequest(BaseModel):
    prompt_ids: list[str]
    solver: str = "agent"               # 'agent' | 'openai'
    n_runs: int = 3
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = "gpt-4o"


@app.post("/api/stress_test")
def stress_test(req: StressRequest):
    author = _load_author()
    for pid in req.prompt_ids:
        if pid not in author:
            raise HTTPException(404, f"prompt {pid} not found")
    if req.solver == "openai":
        if not (req.api_key and req.base_url):
            raise HTTPException(400, "solver='openai' requires api_key and base_url")
        model_fn = solvers.make_openai_solver(req.api_key, req.base_url, req.model or "gpt-4o")
    else:
        model_fn = solvers.agent_solver_fn
    # Run over the subset by writing a temp author file.
    subset = {pid: author[pid] for pid in req.prompt_ids}
    tmp = os.path.join(WORKSPACE, ".stress_subset.json")
    json.dump(subset, open(tmp, "w"))
    results = str_.run_stress_test(model_fn, n_runs=max(1, min(req.n_runs, 5)), author_path=tmp)
    summary = {pid: {"answer": r["answer"], "l2_fail_rate": r["l2_fail_rate"],
                     "l1_any_correct": r["l1_any_correct"],
                     "proxy_validated": r["proxy_validated"],
                     "failure_modes": [x["failure_mode"] for x in r["runs"]]}
               for pid, r in results.items()}
    note = ("agent solver is an offline probe; run the benchmark for a live agent blind-solve"
            if req.solver == "agent" else "results from user-supplied OpenAI-compatible endpoint")
    return {"solver": req.solver, "note": note, "results": summary}


# Static frontend (mounted last so /api routes win).
if os.path.isdir(STATIC):
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
