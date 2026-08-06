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
import trap_generator as tg
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
    trap_class: str = "vision"           # only 'vision' is supported
    lccn: Optional[str] = None           # newspaper to draw from (default: random seed)
    start_date: Optional[str] = None     # YYYY-MM-DD seed; default: randomized for novelty
    max_steps: int = 15                  # how many front pages to walk before giving up


# Tracks the furthest date walked per paper so repeated Generate calls keep
# producing NOVEL pages instead of re-serving the same seed.
_WALK_CURSOR = {}


@app.post("/api/generate")
def generate(req: GenerateRequest):
    """Generate a NOVEL vision trap on demand. Walks forward through LOC front
    pages from a seed, auto-extracts the masthead answer via OCR consensus, and
    runs the api-proof + leak + legibility gates. The candidate is served as a
    fresh trap and queued for independent ground-truth confirmation (agent/vision
    read of the image); `verified` is True only after that confirmation.

    NOTE: OCR-derived answers are NOT guaranteed correct until confirmed — two
    OCR engines have been observed to converge on the same wrong digit on
    degraded mastheads. Confirmed traps are in GET /api/generated."""
    if req.trap_class != "vision":
        raise HTTPException(400, "only trap_class='vision' is currently supported; "
                                 "the NIH ranking class was removed as non-reproducible")
    import random, datetime
    lccn = req.lccn or random.choice(list(tg.SEEDS.keys()))
    if req.start_date:
        seed = req.start_date
    elif lccn in _WALK_CURSOR:
        seed = _WALK_CURSOR[lccn]  # continue past the last-served page
    else:
        # randomize the seed within the paper's early run for variety
        base = tg.SEEDS[lccn]
        y, m, d = map(int, base.split("-"))
        seed = (datetime.date(y, m, d) + datetime.timedelta(days=random.randint(0, 120))).isoformat()
    try:
        trap = tg.generate_trap(lccn=lccn, start_date=seed, max_steps=max(1, min(req.max_steps, 30)))
    except RuntimeError as e:
        raise HTTPException(422, str(e))
    # advance the cursor past the served page so the next call is novel
    try:
        y, m, d = map(int, trap["date"].split("-"))
        _WALK_CURSOR[lccn] = (datetime.date(y, m, d) + datetime.timedelta(days=1)).isoformat()
    except Exception:
        pass
    return {
        "id": f"{trap['lccn']}:{trap['date']}:{trap['field']}",
        "prompt": trap["prompt"], "answer": trap["answer"],
        "field": trap["field"], "paper": trap["paper"], "date": trap["date"],
        "verified": trap["verified"], "api_proof": trap["api_proof"],
        "confidence": trap["confidence"], "word_count": trap["word_count"],
        "golden": trap["golden"], "sources": trap["sources"],
        "resource_url": trap["resource_url"],
        "note": ("OCR-derived candidate; pending independent image confirmation. "
                 "Confirmed traps appear in /api/generated."),
    }


def _trap_summary(t):
    return {
        "id": f"{t['lccn']}:{t['date']}:{t['field']}",
        "lccn": t["lccn"], "date": t["date"], "paper": t["paper"],
        "field": t["field"], "answer": t["answer"],
        "verified": t.get("verified", False), "api_proof": t.get("api_proof", False),
        "confidence": t.get("confidence"), "word_count": t.get("word_count"),
        "image_url": f"/api/generated/image?lccn={t['lccn']}&date={t['date']}&field={t['field']}",
    }


@app.get("/api/generated")
def list_generated():
    """The growing catalog of independently-confirmed (verified) generated traps."""
    pool = tg.list_generated()
    return {"count": len(pool), "verified": [_trap_summary(t) for t in pool]}


@app.get("/api/generated/image")
def generated_image(lccn: str, date: str, field: str):
    """Serve the scan image for a generated trap (verified or pending)."""
    for t in tg.list_generated() + tg.list_pending():
        if (t["lccn"], t["date"], t["field"]) == (lccn, date, field):
            path = tg.resolve_image_path(t)
            if path and os.path.exists(path):
                return FileResponse(path, media_type="image/jpeg")
            raise HTTPException(404, "image file missing")
    raise HTTPException(404, "no matching generated trap")


@app.get("/api/pending")
def list_pending():
    """OCR-derived candidates awaiting ground-truth confirmation."""
    pend = tg.list_pending()
    return {"count": len(pend), "pending": [_trap_summary(t) for t in pend]}


class ConfirmRequest(BaseModel):
    lccn: str
    date: str
    field: str
    confirmed_answer: str
    verifier: str = "agent"


@app.post("/api/confirm")
def confirm(req: ConfirmRequest):
    """Confirm a pending candidate's ground truth from an independent image read.
    If confirmed_answer differs from the OCR candidate, the confirmed value wins."""
    t = tg.confirm_candidate(req.lccn, req.date, req.field, req.confirmed_answer, req.verifier)
    if not t:
        raise HTTPException(404, "no matching pending candidate")
    return {"verified": True, "trap": t}


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
