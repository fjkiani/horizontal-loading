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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Any, Optional

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
import batch_prompts as bp
import verify_joins as vj
import stress_test_runner as str_
import trap_generator as tg
import source_gate as sg
# Category-trap stack. Import order is load-bearing: gen_v2 then gen_v3 both mutate
# ct.GENERATORS in place, and gen_v3 must win on the categories it re-points
# (history, celebrities/public figures, education, sports).
import category_traps as ct
import gen_v2  # noqa: F401  installs its overrides into ct.GENERATORS on import
import gen_v3  # noqa: F401  installs its overrides on top of gen_v2's
import gen_v4  # noqa: F401  finance only; must load last so it wins that key
# The SAME battery the build pipeline runs. Imported here so the deployed
# service cannot apply a weaker gate than the catalog it serves alongside.
import evaluate_traps as et
import seed_roster
import pool_ledger as pl
from app import solvers

WORKSPACE = _REPO
STATIC = os.path.join(os.path.dirname(__file__), "static")
AUTHOR_PATH = os.path.join(WORKSPACE, "author_payloads.json")
CATALOG_PATH = os.path.join(WORKSPACE, "web", "public", "catalog.json")

# The ledger is addressed ABSOLUTELY. pool_ledger's default is the relative
# "pool_ledger.json", and the service's working directory under gunicorn is not
# guaranteed to be the repo root -- a relative path would silently open a second,
# empty ledger and every category would read as freshly stocked.
pl.LEDGER_PATH = os.environ.get(
    "SEAL_POOL_LEDGER", os.path.join(WORKSPACE, "pool_ledger.json"))

app = FastAPI(title="Seal Prompt Generator", version="1.0")


# --------------------------------------------------------------- prompt pool
def _catalog_traps():
    """The baked catalog, as a list. Empty if the bake has not run."""
    if not os.path.exists(CATALOG_PATH):
        return []
    try:
        with open(CATALOG_PATH) as fh:
            doc = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    return doc.get("traps") or []


def _pool_index():
    """trap_id -> baked trap. The ledger stores accounting, not prompt text."""
    idx = {}
    for t in _catalog_traps():
        tid = t.get("trap_id") or pl.trap_id(
            t.get("category"), t.get("field"), str(t.get("answer")))
        idx[tid] = t
    return idx


def _seed_pool():
    """Stock the ledger from the catalog at startup.

    Idempotent by construction: pool_ledger.upsert refreshes metadata but never
    resets a status, so a redeploy cannot un-burn a prompt that was already
    spent. It CAN lose the ledger entirely, because Render's disk is ephemeral --
    that is a stated limitation of this deployment, not a property of the model.
    """
    try:
        return pl.upsert(_catalog_traps())
    except Exception as e:  # noqa: BLE001  never let seeding break startup
        return {"error": "%s: %s" % (type(e).__name__, e)}


_POOL_SEED = _seed_pool()


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
    # 'category' runs the API-native category track; 'vision' runs the
    # newspaper walk. The console previously had no way to reach the first,
    # which is why every category served one frozen trap.
    trap_class: str = "vision"
    category: Optional[str] = None       # category track: which of the 16
    seed: Optional[Any] = None           # dict of generator kwargs, or int index
    lccn: Optional[str] = None           # newspaper to draw from (default: random seed)
    start_date: Optional[str] = None     # YYYY-MM-DD seed; default: randomized for novelty
    max_steps: int = 8                   # how many front pages to walk before giving up
    # Consumption controls. request_key makes a retry idempotent: the same key
    # inside the reissue window returns the identical prompt instead of burning
    # a second one. fresh=True bypasses the pool and traverses the source APIs,
    # which is the slow path and is only correct when the caller WANTS a new
    # prompt minted rather than one drawn from stock.
    request_key: Optional[str] = None
    fresh: bool = False


def _run_category_generate(job_id, category, kwargs, seed_index, request_key=None):
    """Generate one category trap on demand and gate it before returning.

    The gate result is reported, not hidden. A seed that fails is a measured
    refusal with a stated reason -- an argmax at an endpoint, a population too
    small for the guessability ceiling, a witness run by the primary operator --
    and the console shows that reason rather than silently falling back to the
    frozen trap.

    THE GATE USED TO BE ONE TEST WIDE. This function called sg.validate_trap()
    and nothing else, which checks source independence and word count. The
    build pipeline runs TWELVE tests (T0 base adequacy, T1 uniqueness, T2
    guessability, T3 order leak, T3b monotone key, T3c key-derivable depth, T3d
    derivable key, T4 separation, T4b fragility, T5 confirmation, T6 gate, T7
    prompt leak) and holds anything that fails. So the deployed service was
    shipping, as "done", traps that the build gate would never have shipped.

    That was not hypothetical. A live smoke test of the health category returned
    NCT04300920 from seed {"condition": "multiple sclerosis"} on a base of 30 --
    a trap that had never been through the depth or witness measurement that
    qualified the baked answer NCT05178810 (n_base 51). Travel likewise served
    HEL on n_base 171 against a baked IVL on 73. The API was a second, weaker
    authority disagreeing with the catalog.

    The fix is to run the SAME battery the build runs, on the freshly generated
    trap, and let its verdict decide the job status. `ship` is served; `hold`
    and `unproven` are refused with the failing test names and their measured
    reasons attached, so a refusal stays a result rather than becoming an error.
    """
    def _p(phase, **extra):
        with _JOBS_LOCK:
            j = _JOBS.get(job_id)
            if j and j.get("status") == "running":
                j["progress"] = dict({"phase": phase}, **extra)
                j.setdefault("log", []).append(j["progress"])
                del j["log"][:-12]

    try:
        _p("traversing source APIs", category=category, seed=kwargs)
        # ct.generation() serializes ranking-through-emission. LAST_RANK is a
        # module global that _pick_extreme rebinds and to_trap() reads AFTER the
        # witness round trips, so two overlapping requests handed each other's
        # ranking evidence to the gate -- 200/200 in a two-thread reproduction.
        # Every test below reads that evidence, so the lock is load-bearing for
        # the gate's correctness, not just for tidy bookkeeping.
        with ct.generation():
            fn = ct.GENERATORS[category]
            cand = fn(**kwargs)
            trap = cand.to_trap()
        _p("checking source independence")
        ok_gate, violations = sg.validate_trap(trap, min_operators=3)
        _p("scoring the full trap battery", tests=len(et.TESTS_EV) + len(et.TESTS_TRAP))
        ev = et.evaluate_one(category, {"trap": trap})
        failed = sorted(n for n, r in ev["tests"].items() if r["pass"] is False)
        unproven = sorted(n for n, r in ev["tests"].items() if r["pass"] is None)
        verdict = ev["verdict"]
        ok = (verdict == "ship") and ok_gate
        evaluation = {
            "verdict": verdict,
            "witness_tier": ev.get("witness_tier"),
            "independent_witnesses": ev.get("independent_witnesses"),
            "failed_tests": failed,
            "unproven_tests": unproven,
            "tests": ev["tests"],
            "n_tests": len(ev["tests"]),
        }
        pool = None
        if ok:
            detail = None
            # A freshly minted prompt is a CONSUMED prompt: the caller has now
            # seen it, so it must enter the ledger already spent. Booking it as
            # `available` would let the same answer be handed out a second time
            # through the pool path, which is the exact double-serve this ledger
            # exists to prevent. upsert() is a no-op on an existing record's
            # status, so re-minting an answer already in stock cannot resurrect it.
            try:
                tid = pl.trap_id(category, trap.get("field"), str(trap.get("answer")))
                pl.upsert([dict(trap, verdict=verdict,
                                witness_tier=ev.get("witness_tier"),
                                seed_repr=repr(kwargs))])
                rec, meta = pl.book_minted(tid, request_key or ("mint:" + job_id))
                pool = {"trap_id": tid, "minted": True,
                        "status": meta.get("status"),
                        "already_spent": meta.get("already_spent"),
                        "n_available": meta.get("n_available")}
            except Exception as e:  # noqa: BLE001  accounting must not eat a good trap
                pool = {"error": "%s: %s" % (type(e).__name__, e)}
        elif failed:
            detail = ("trap held: %d of %d tests failed -- %s"
                      % (len(failed), len(ev["tests"]), "; ".join(
                          "%s: %s" % (n, ev["tests"][n]["detail"]) for n in failed)))
        elif unproven:
            detail = ("trap unproven: %d of %d tests could not be measured -- %s"
                      % (len(unproven), len(ev["tests"]), "; ".join(
                          "%s: %s" % (n, ev["tests"][n]["detail"]) for n in unproven)))
        else:
            detail = "seed refused by the source gate: " + "; ".join(map(str, violations))
        with _JOBS_LOCK:
            _JOBS.setdefault(job_id, {}).update({
                "status": "done" if ok else "refused",
                "ended": time.time(),
                "seed": kwargs, "seed_index": seed_index,
                "result": _api_trap_summary(trap, evaluation) if ok else None,
                "violations": violations,
                "evaluation": evaluation,
                # A refused trap still carries what it WOULD have answered, so
                # the console can show the reader exactly which candidate the
                # gate rejected instead of an anonymous failure.
                "rejected_candidate": None if ok else {
                    "answer": trap.get("answer"), "field": trap.get("field"),
                    "entity": trap.get("entity"), "n_base": trap.get("n_base")},
                "detail": detail,
                "pool": pool,
            })
    except Exception as e:  # noqa: BLE001
        with _JOBS_LOCK:
            _JOBS.setdefault(job_id, {}).update(
                {"status": "refused" if type(e).__name__ == "TrapUnavailable" else "error",
                 "seed": kwargs, "seed_index": seed_index,
                 "detail": "%s: %s" % (type(e).__name__, e), "ended": time.time()})


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
    if req.trap_class == "category" or req.category:
        cat = req.category
        if cat not in ct.GENERATORS:
            raise HTTPException(400, "unknown category %r; expected one of %s"
                                % (cat, list(sg.CATEGORIES)))

        # POOL FIRST. A plain "give me a prompt for this category" is answered
        # from stock, synchronously, and the prompt is burned. Two reasons the
        # pool beats generating on demand here: request latency is bounded (a
        # live traversal is 30-170 s and can fail mid-request the way legal's
        # volume walk does), and a served prompt is single-use by construction --
        # once a solver has seen it, re-serving it measures recall, not the
        # capability the trap probes. An explicit seed, or fresh=True, still
        # takes the slow minting path below.
        if not req.fresh and req.seed is None:
            key = req.request_key or uuid.uuid4().hex[:12]
            rec, meta = pl.serve(cat, key)
            if rec is None:
                # Exhausted is a REFUSAL, never a silent reissue. There is no
                # code path that serves a burned prompt.
                return JSONResponse(status_code=409, content={
                    "status": "exhausted", "category": cat,
                    "n_available": 0,
                    "n_burned": meta.get("n_burned", 0),
                    "n_served": meta.get("n_served", 0),
                    "n_retired": meta.get("n_retired", 0),
                    "n_total": meta.get("n_total", 0),
                    "detail": ("the %s pool is spent: %d prompt(s) burned, %d "
                               "still inside their reissue window. Prompts are "
                               "not recycled -- refill by sweeping the seed "
                               "roster (expand_seeds.py) and re-baking the "
                               "catalog. Pass fresh=true to mint one live "
                               "instead, which traverses the source APIs and "
                               "may refuse."
                               % (cat, meta.get("n_burned", 0),
                                  meta.get("n_served", 0))),
                    "replenish": "/api/pool",
                })
            trap = _pool_index().get(rec["trap_id"])
            if trap is None:
                # The ledger knows an id the catalog no longer carries. Report
                # it rather than 500 -- and do not pretend a prompt was served.
                raise HTTPException(503, "pool record %s has no baked trap; "
                                         "the ledger and catalog are out of sync"
                                    % rec["trap_id"])
            ev = {"verdict": trap.get("verdict"),
                  "witness_tier": trap.get("witness_tier"),
                  "independent_witnesses": trap.get("independent_confirming_operators"),
                  "source": "catalog bake (13-test battery at build time)"}
            return {"status": "served", "source": "pool", "category": cat,
                    "trap_id": rec["trap_id"], "request_key": key,
                    "reissued": bool(meta.get("reissued")),
                    "reissue_expires_at": rec.get("reissue_expires_at"),
                    "n_available": meta.get("n_available"),
                    "result": _api_trap_summary(trap, ev),
                    "note": ("this prompt is now spent; the same request_key "
                             "returns it again for %d s, a different key gets a "
                             "different prompt" % pl.REISSUE_SECONDS)}

        if isinstance(req.seed, dict):
            try:
                kwargs, idx = seed_roster.validate_kwargs(cat, req.seed), None
            except TypeError as e:
                raise HTTPException(400, str(e))
        elif isinstance(req.seed, int):
            roster = seed_roster.seeds_for(cat)
            idx = req.seed % len(roster)
            kwargs = roster[idx]
        else:
            kwargs, idx = seed_roster.next_seed(cat)
        job_id = uuid.uuid4().hex[:12]
        with _JOBS_LOCK:
            _JOBS[job_id] = {"status": "running", "started": time.time(),
                             "category": cat, "seed": kwargs, "seed_index": idx,
                             "progress": {"phase": "starting"}, "log": []}
        threading.Thread(target=_run_category_generate,
                         args=(job_id, cat, kwargs, idx, req.request_key),
                         daemon=True).start()
        return {"job_id": job_id, "status": "running",
                "poll": "/api/generate/%s" % job_id, "category": cat,
                "seed": kwargs, "seed_index": idx,
                "note": "category generation traverses live source APIs; poll the job URL"}

    if req.trap_class != "vision":
        raise HTTPException(400, "trap_class must be 'vision' or 'category'; "
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


@app.get("/api/seeds")
def list_seeds(category: str | None = None):
    """The measured seed roster: 81 seeds across 16 categories, 53 of which
    produced a gate-valid trap in the expansion sweep."""
    if category is not None:
        if category not in ct.GENERATORS:
            raise HTTPException(400, "unknown category %r" % category)
        return {"category": category, "seeds": seed_roster.seeds_for(category)}
    return {"counts": seed_roster.roster_summary(),
            "seeds": {c: seed_roster.seeds_for(c) for c in ct.GENERATORS}}


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
        return {"job_id": job_id, "status": "error", "detail": job.get("detail"),
                "elapsed": round(job.get("ended", time.time()) - job.get("started", time.time()), 1),
                "log": job.get("log") or []}
    if job["status"] == "refused":
        # A refusal is a RESULT, not a failure. The seed was traversed and the
        # trap was rejected for a stated reason -- a tie at the extremum, a
        # population too small for the guessability ceiling, or a witness run by
        # the primary operator. Surfacing the reason is the whole point; the
        # previous code fell through to the 'done' branch and raised KeyError
        # on the missing 'result', turning every honest refusal into a 500.
        return {"job_id": job_id, "status": "refused",
                "detail": job.get("detail"),
                "violations": job.get("violations") or [],
                "evaluation": job.get("evaluation"),
                "rejected_candidate": job.get("rejected_candidate"),
                "category": job.get("category"), "seed": job.get("seed"),
                "seed_index": job.get("seed_index"),
                "elapsed": round(job.get("ended", time.time()) - job.get("started", time.time()), 1),
                "log": job.get("log") or []}
    if "result" not in job:
        # Defensive: never let an unexpected terminal state 500. Report the
        # state we actually observed instead of pretending it produced a trap.
        return {"job_id": job_id, "status": job.get("status", "unknown"),
                "detail": job.get("detail") or "terminal state carried no result",
                "log": job.get("log") or []}
    return {"job_id": job_id, "status": "done", "result": job["result"],
            "evaluation": job.get("evaluation"), "pool": job.get("pool"),
            "category": job.get("category"), "seed": job.get("seed"),
            "seed_index": job.get("seed_index"),
            "elapsed": round(job.get("ended", time.time()) - job.get("started", time.time()), 1)}


def _api_trap_summary(t, evaluation=None):
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
        # The full gate verdict travels WITH the trap. A consumer that receives
        # an answer without knowing which of the twelve tests were measured, and
        # which merely went unmeasured, cannot tell a proven trap from an
        # unproven one -- and that is precisely the confusion that let the API
        # serve an unvalidated health answer beside a validated catalog.
        "evaluation": evaluation,
        "verdict": (evaluation or {}).get("verdict"),
        "solver_difficulty": None,
        "solver_difficulty_status": (
            "not measured: a solver-based difficulty score needs a production "
            "LLM key; the trial key is exhausted (1000 calls/month) and the "
            "measurement design needs roughly 600 calls. Leakage tests below "
            "bound what a solver could SHORTCUT, which is not the same quantity."),
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


@app.get("/api/pool")
def pool_status():
    """Consumption accounting for the prompt pool.

    Answers the question the old API could not: how many prompts are left, how
    many were spent, and when to refill. `low_water` trips at
    SEAL_LOW_WATER (default 2) or fewer available, BEFORE exhaustion, because
    the refill path is a seed sweep that takes tens of minutes, not a request.

    `burned` and `retired` are separate on purpose. Burned means consumed by
    service. Retired means withdrawn by policy -- the Wikimedia ban retired 7
    prompts that no caller ever saw, and counting those as consumption would
    misreport how much of the pool has actually been spent.
    """
    st = pl.status(categories=sg.CATEGORIES)
    st["catalog_traps"] = len(_catalog_traps())
    st["seeded_at_startup"] = _POOL_SEED
    st["persistence"] = ("the ledger is a file on the service's ephemeral disk; "
                         "a redeploy restocks it from the baked catalog, so "
                         "consumption counts are per-deploy, not lifetime")
    return st


@app.get("/api/categories")
def list_categories():
    """The closed 16-value taxonomy, with what each category can actually serve.

    `n_served` used to be the size of the generated pool for the category -- a
    number that never moved when a prompt was handed out, so a caller could be
    served the same prompt forever and the API would keep reporting stock. It
    now means AVAILABLE: prompts this category can still serve. It is kept under
    the old key so existing clients that use it to decide "can this category be
    asked?" keep working, and the unambiguous names are alongside it.

    A category with 0 available is reported as 0 rather than omitted: the
    absence is the finding. `unservable` is now split -- `exhausted` means the
    pool was spent, `unstocked` means nothing was ever baked for it, and those
    are different failures with different remedies.
    """
    st = pl.status(categories=sg.CATEGORIES)
    by_cat = {c["category"]: c for c in st["categories"]}
    rows = []
    for c in sg.CATEGORIES:
        r = by_cat.get(c) or {"n_total": 0, "n_available": 0, "n_served": 0,
                              "n_burned": 0, "n_retired": 0, "low_water": True,
                              "exhausted": True}
        rows.append({
            "category": c,
            "n_served": r["n_available"],   # legacy key: what can be served now
            "n_available": r["n_available"],
            "n_total": r["n_total"],
            "n_burned": r["n_burned"],
            "n_in_window": r["n_served"],
            "n_retired": r["n_retired"],
            "low_water": r["low_water"],
            "exhausted": r["n_total"] > 0 and r["n_available"] == 0,
            "unstocked": r["n_total"] == 0,
        })
    return {"categories": rows,
            "n_categories": len(sg.CATEGORIES),
            "n_served_total": st["n_available_total"],
            "n_available_total": st["n_available_total"],
            "n_burned_total": st["n_burned_total"],
            "unservable": [r["category"] for r in rows if not r["n_available"]],
            "exhausted": [r["category"] for r in rows if r["exhausted"]],
            "unstocked": [r["category"] for r in rows if r["unstocked"]],
            "low_water": [r["category"] for r in rows
                          if r["low_water"] and not r["unstocked"]]}


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
