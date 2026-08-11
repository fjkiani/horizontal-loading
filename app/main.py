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
import json, os, sys, threading, uuid, time
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
import source_gate as sg
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
            "withdrawn": rec.get("withdrawn", False),
            "withdrawn_reason": rec.get("withdrawn_reason"),
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
        "withdrawn": rec.get("withdrawn", False),
        "withdrawn_reason": rec.get("withdrawn_reason"),
        "withdrawn_evidence": rec.get("withdrawn_evidence"),
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
    max_steps: int = 8                   # how many front pages to walk before giving up


# Tracks the furthest date walked per paper so repeated Generate calls keep
# producing NOVEL pages instead of re-serving the same seed.
_WALK_CURSOR = {}

# In-memory job store for asynchronous generation. A live walk downloads and
# OCRs several front pages, which can exceed Render's ~100s proxy timeout on the
# free (0.1 vCPU) tier — so generation runs in a background thread and the client
# polls /api/generate/{job_id}.
_JOBS = {}
_JOBS_LOCK = threading.Lock()


def _run_generate(job_id, lccn, seed, max_steps):
    def _progress(ev):
        # A walk can run for minutes on a small instance. Without this the poll
        # endpoint returns a bare "running" and the UI cannot distinguish real
        # work from a hang.
        with _JOBS_LOCK:
            j = _JOBS.get(job_id)
            if j and j.get("status") == "running":
                j["progress"] = ev
                j.setdefault("log", []).append(ev)
                del j["log"][:-12]  # keep the tail bounded

    try:
        trap = tg.generate_trap(lccn=lccn, start_date=seed, max_steps=max_steps,
                                progress=_progress)
        # advance the cursor past the served page so the next call is novel
        try:
            import datetime
            y, m, d = map(int, trap["date"].split("-"))
            _WALK_CURSOR[lccn] = (datetime.date(y, m, d) + datetime.timedelta(days=1)).isoformat()
        except Exception:
            pass
        result = {
            "id": f"{trap['lccn']}:{trap['date']}:{trap['field']}",
            "prompt": trap["prompt"], "answer": trap["answer"],
            "field": trap["field"], "paper": trap["paper"], "date": trap["date"],
            "verified": trap["verified"], "api_proof": trap["api_proof"],
            "confidence": trap["confidence"], "word_count": trap["word_count"],
            "golden": trap["golden"], "sources": trap["sources"],
            "resource_url": trap["resource_url"],
            "image_url": f"/api/generated/image?lccn={trap['lccn']}&date={trap['date']}&field={trap['field']}",
            "note": ("OCR-derived candidate; pending independent image confirmation. "
                     "Confirmed traps appear in /api/generated."),
        }
        with _JOBS_LOCK:
            # update in place: replacing the dict would discard started/log, which
            # is exactly the context needed to explain a slow or failed walk.
            _JOBS.setdefault(job_id, {}).update(
                {"status": "done", "result": result, "ended": time.time()})
    except RuntimeError as e:
        with _JOBS_LOCK:
            _JOBS.setdefault(job_id, {}).update(
                {"status": "error", "detail": str(e), "ended": time.time()})
    except Exception as e:
        with _JOBS_LOCK:
            _JOBS.setdefault(job_id, {}).update(
                {"status": "error", "detail": f"generation failed: {e}", "ended": time.time()})


@app.post("/api/generate")
def generate(req: GenerateRequest):
    """Start an on-demand generation job (async). Returns a job_id immediately;
    poll GET /api/generate/{job_id} for the result. The walk finds a NOVEL front
    page, OCRs its masthead, and gates it (api-proof / leak / legibility). The
    candidate is queued in /api/pending with verified=false until an independent
    image read confirms it via /api/confirm.

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
        base = tg.SEEDS[lccn]
        y, m, d = map(int, base.split("-"))
        seed = (datetime.date(y, m, d) + datetime.timedelta(days=random.randint(0, 120))).isoformat()
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {"status": "running", "started": time.time(),
                         "max_steps": max(1, min(req.max_steps, 12)),
                         "lccn": lccn, "seed": seed, "progress": {"phase": "starting"},
                         "log": []}
    threading.Thread(target=_run_generate, args=(job_id, lccn, seed, max(1, min(req.max_steps, 12))),
                     daemon=True).start()
    return {"job_id": job_id, "status": "running",
            "poll": f"/api/generate/{job_id}",
            "note": "generation runs in the background; poll the job URL for the result"}


@app.get("/api/generate/{job_id}")
def generate_status(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] == "running":
        return {"job_id": job_id, "status": "running",
                "elapsed": round(time.time() - job.get("started", time.time()), 1),
                "max_steps": job.get("max_steps"),
                "progress": job.get("progress") or {},
                "log": job.get("log") or []}
    if job["status"] == "error":
        return {"job_id": job_id, "status": "error", "detail": job["detail"],
                "elapsed": round(job.get("ended", time.time()) - job.get("started", time.time()), 1),
                "log": job.get("log") or []}
    return {"job_id": job_id, "status": "done", "result": job["result"]}


def _api_trap_summary(t):
    """Summary for an API-native trap.

    These have no lccn, date, paper or scan image, so the scan-trap summary
    below raised KeyError on every one of them -- the pool could hold them but
    the catalog endpoint could not render them. The witness tier is surfaced
    rather than hidden: `gold` means two or more operators independent of the
    one that supplied the ranked collection confirm the answer, `silver` means
    exactly one. Nothing with zero reaches the pool.
    """
    ind = t.get("independent_confirming_operators") or []
    return {
        "id": f"api-native:{t.get('category')}:{t.get('field')}",
        "track": "api-native",
        "category": t.get("category"),
        "field": t.get("field"), "answer": t.get("answer"),
        "entity": t.get("entity"),
        "prompt": t.get("prompt", ""),
        "verified": t.get("verified", False),
        "api_proof": t.get("api_proof", False),
        "n_base": t.get("n_base"),
        "primary_operator": t.get("primary_operator"),
        "source_operators": t.get("source_operators"),
        "independent_confirming_operators": ind,
        "witness_tier": "gold" if len(ind) >= 2 else ("silver" if ind else "unwitnessed"),
        "confirmation": t.get("confirmation"),
        "sources": t.get("sources"),
        # Surface facts, and lift any key named known_defect_* into a top-level
        # list. A measured defect recorded only in the generator is a defect the
        # consumer never sees; the whole point of writing them down is that
        # anything serving this trap has to see them too.
        "facts": t.get("facts") or {},
        "known_defects": {k: v for k, v in (t.get("facts") or {}).items()
                          if k.startswith("known_defect")},
        "witness_scope": (t.get("facts") or {}).get("witness_scope"),
        "image_url": None,
    }


def _trap_summary(t):
    if t.get("track") == "api-native" or "lccn" not in t:
        return _api_trap_summary(t)
    return {
        "id": f"{t['lccn']}:{t['date']}:{t['field']}",
        "track": "vision-scan",
        "category": t.get("category"),
        "lccn": t["lccn"], "date": t["date"], "paper": t["paper"],
        "field": t["field"], "answer": t["answer"],
        # The prompt IS the product - the UI detail pane renders it. Omitting it
        # here rendered a literal "undefined" in the detail view.
        "prompt": t.get("prompt", ""),
        "verified": t.get("verified", False), "api_proof": t.get("api_proof", False),
        "confidence": t.get("confidence"), "word_count": t.get("word_count"),
        # Provenance: which extractor proposed the answer, and who confirmed it.
        # This matters because a confident OCR reading is not ground truth - the
        # old same-raster extractor emitted 176 for a masthead that reads 175.
        "ocr_engine": t.get("ocr_engine"), "verifier": t.get("verifier"),
        "image_url": f"/api/generated/image?lccn={t['lccn']}&date={t['date']}&field={t['field']}",
    }


@app.get("/api/categories")
def list_categories():
    """The closed 16-value taxonomy, with how many served traps each holds.

    A category with 0 served traps is reported as 0 rather than omitted: the
    absence is the finding. `unservable` lists the categories whose generator
    currently produces an answer that no independent operator will confirm, so
    the gate refuses it.
    """
    pool = tg.list_generated()
    counts = {c: 0 for c in sg.CATEGORIES}
    for t in pool:
        c = t.get("category")
        if c in counts:
            counts[c] += 1
    return {"categories": [{"category": c, "n_served": counts[c]}
                           for c in sg.CATEGORIES],
            "n_categories": len(sg.CATEGORIES),
            "n_served_total": len(pool),
            "unservable": [c for c in sg.CATEGORIES if counts[c] == 0]}


@app.get("/api/retired")
def list_retired():
    """Prompts withdrawn from service, with the evidence for withdrawal.

    Retired prompts stay inspectable and stay OUT of the served pool. The 23
    archived here all resolved to a banned publisher.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "retired_corpus.json")
    if not os.path.exists(path):
        return {"count": 0, "retired": []}
    with open(path) as fh:
        doc = json.load(fh)
    if isinstance(doc, list):
        return {"count": len(doc), "retired": doc}
    curated = doc.get("curated") or []
    generated = doc.get("generated") or []
    return {"count": len(curated) + len(generated),
            "retired_at": doc.get("retired_at"),
            "rule": doc.get("rule"),
            "counts": doc.get("counts"),
            "curated": curated,
            "generated": generated}


@app.get("/api/generated")
def list_generated(category: str | None = None, tier: str | None = None,
                   track: str | None = None):
    """The growing catalog of independently-confirmed (verified) generated traps.

    Optional filters: `category` (one of the 16), `tier` (gold|silver),
    `track` (api-native|vision-scan).
    """
    if category is not None and category not in sg.CATEGORIES:
        raise HTTPException(400, f"category {category!r} is not one of the 16 "
                                 f"permitted values")
    rows = [_trap_summary(t) for t in tg.list_generated()]
    if category is not None:
        rows = [r for r in rows if r.get("category") == category]
    if tier is not None:
        rows = [r for r in rows if r.get("witness_tier") == tier]
    if track is not None:
        rows = [r for r in rows if r.get("track") == track]
    return {"count": len(rows), "filters": {"category": category, "tier": tier,
                                            "track": track},
            "verified": rows}


@app.get("/api/generated/image")
def generated_image(lccn: str, date: str, field: str):
    """Serve the scan image for a generated trap (verified or pending)."""
    for t in tg.list_generated() + tg.list_pending():
        if (t.get("lccn"), t.get("date"), t.get("field")) == (lccn, date, field):
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


@app.post("/api/confirm/vision")
def confirm_vision(lccn: str, date: str, field: str = "issue number"):
    """Adjudicate a pending candidate with the Cohere vision LLM (optional).

    Requires COHERE_API_KEY. Outcomes: 'agree' promotes the candidate,
    'conflict' holds it with BOTH readings recorded, 'unread' leaves it pending.

    MEASURED CAVEAT: on a Cohere TRIAL key the vision endpoint sustained only
    ~1 call per several minutes (repeated HTTP 429), and when fed the same
    downscaled raster that fooled tesseract it returned the same wrong digit
    (176 vs true 175). It is exposed as an optional second opinion, not as the
    primary extractor — the primary extractor is cross-resolution tesseract."""
    if not tg.cohere_available():
        raise HTTPException(400, "COHERE_API_KEY not configured")
    out = tg.cohere_confirm(lccn, date, field)
    if out.get("outcome") == "not_found":
        raise HTTPException(404, "no matching pending candidate")
    return out


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
