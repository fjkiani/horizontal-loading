# Seal Prompt Generator

A FastAPI + static-frontend app that **generates API-proof research prompts on demand** —
questions whose answers are computed from live data, gated, and independently confirmed,
and that a text-only model cannot answer with a single API call.

## What it does

This is a **real generator, not a static pool.** Each `POST /api/generate` call walks
**forward through live Library of Congress newspaper front pages** (following each issue's
`next_resource` link), OCRs the masthead of each new scan, and keeps only the pages that
pass every gate. A novel front page is produced on every call — repeated calls keep
advancing the walk so you get fresh traps, not repeats.

**The trap principle (vision-true vertical):** a masthead value (issue number / year
established) is **legible in the scan image but absent from the whole-page OCR text
layer**. A text-only / naive-API solver cannot find it; only reading the image yields it.

### Generation gates (all must pass)
- **G1 extraction** — a masthead answer is read by OCR consensus (3 scales, 2-of-3 vote)
- **G2 api-proof** — the answer string is **absent** from the whole-page OCR
- **G3 leak** — the answer is not revealed by the prompt text itself
- **G4 legibility** — the masthead crop is a real, readable scan (size + contrast)

About **half** of front pages are clean (answer absent from OCR); the other half leak it
and are rejected. The walker keeps going until it finds a clean one, which is what makes
"generate on repeat" viable. Some date runs (e.g. Tribune June 1900) leak the number into
the OCR on every page and correctly yield **no trap** (`422`) rather than a leaky one.

## Two-tier trust: pending → confirmed

**OCR-derived answers are NOT ground truth until independently confirmed.** Two different
OCR engines have been observed to converge on the *same wrong digit* on a degraded
masthead (a "5" read as a "6"). So generation is two-tier:

1. **`/api/generate`** serves a fresh candidate with `verified=false` and queues it in
   **`/api/pending`**.
2. An **independent high-fidelity read of the image** (agent vision / a vision API / a
   human) confirms or corrects the answer via **`POST /api/confirm`**, which moves it to
   the verified pool in **`/api/generated`**.

Only **`/api/generated`** (independently confirmed) should be treated as ground truth.

## Run it

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000/ for the frontend, or use the JSON API directly.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | liveness |
| POST | `/api/generate` | **generate a novel trap on demand** (live LOC walk) |
| GET | `/api/generated` | the growing catalog of **confirmed** generated traps |
| GET | `/api/pending` | OCR-derived candidates awaiting confirmation |
| POST | `/api/confirm` | confirm a candidate's ground truth from an image read |
| GET | `/api/generated/image?lccn&date&field` | the scan image for a generated trap |
| GET | `/api/prompts` | the curated verified set (summary + verify badge) |
| GET | `/api/prompts/{id}` | full detail: prompt, answer, golden trace, sources |
| GET | `/api/prompts/{id}/image` | the scan image for a curated trap |
| POST | `/api/stress_test` | run solver+judge over selected prompts |

### `POST /api/generate`
```json
{ "trap_class": "vision", "lccn": "sn83030214", "start_date": "1900-07-01", "max_steps": 15 }
```
All fields optional. With no `lccn`/`start_date`, the server picks a random seed and
**continues past the last-served page** so each call is novel. Returns the fresh trap with
`verified=false` (OCR candidate) plus `prompt`, `answer`, `api_proof`, `confidence`,
`word_count`, `golden`, `sources`, and `resource_url`. Only `trap_class="vision"` is
supported — the NIH ranking class was **removed as non-reproducible**.

### `POST /api/confirm`
```json
{ "lccn": "sn83030214", "date": "1900-07-04", "field": "issue number",
  "confirmed_answer": "19589", "verifier": "agent-vision" }
```
Confirms (or corrects) a pending candidate's answer and promotes it to the verified pool.
If `confirmed_answer` differs from the OCR candidate, the confirmed value wins.

### `POST /api/stress_test`
```json
{ "prompt_ids": ["V01", "V02"], "solver": "agent", "n_runs": 3 }
```
- `solver="agent"`: offline probe (the live agent blind-solve is run out-of-band).
- `solver="openai"`: supply `api_key`, `base_url`, and `model` to run a real
  OpenAI-compatible frontier model as the solver. The key is used only for the request and
  never persisted.

## Testing

```bash
pytest tests/test_api.py -v
```
12 tests: health, curated prompts verified, detail+image, generate rejects NIH class,
**on-demand generate contract** (live walk), generated pool + pending, confirm 404,
flawed controls caught, cache-key regression, stress-test wiring.

## Honest capability note

- **Text-only / naive-API solver: stumped** — the answer is absent from the OCR and no
  single API call returns it. This satisfies the design constraint.
- **Vision-capable solver: not stumped** — a model with image input reads the masthead and
  extracts the answer. These traps defeat text-only solvers, not vision solvers.
- **OCR-only generation is not self-verifying.** Degraded mastheads can make two OCR
  engines agree on a wrong digit, so every generated candidate requires an independent
  image read before it counts as ground truth. The pending→confirm architecture exists for
  exactly this reason. To make confirmation fully hands-off, slot a vision API key into
  the confirm step.
- The built-in stress test uses the same underlying model as the author (proxy), so its
  results are evidence of difficulty, **not proof** of frontier failure. For the real
  ≥2/3 proof, run `solver="openai"` against your target model.

## Files

- `app/main.py` — FastAPI app + endpoints (generate / generated / pending / confirm)
- `app/solvers.py` — solver model_fn (agent + OpenAI-compatible)
- `app/static/` — frontend (index.html, app.js, style.css)
- `trap_generator.py` — **the on-demand generator** (LOC walk, OCR consensus, gates, pools)
- `join_engine.py` — live-data connectors + compute_* (ground-truth derivation)
- `batch_prompts.py` — the curated vision traps + negative controls
- `verify_joins.py` — 8-rule verification gate
- `stress_test_runner.py` — blind-solver + judge + ≥2/3 aggregator
- `generated_pool.json` — confirmed generated traps (grows over time)
- `generated_pending.json` — OCR candidates awaiting confirmation
- `tests/test_api.py` — pytest suite
