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
- **OCR-only generation is not self-verifying.** Every generated candidate requires an
  independent image read before it counts as ground truth. The pending→confirm
  architecture exists for exactly this reason.

### The resolution finding (why the extractor was rebuilt)

Evening Public Ledger 1922-04-06 truly reads `VOL. VIII.—NO. 175`. The original
extractor returned **176 at HIGH confidence** — a wrong answer that passed every gate.
tesseract, EasyOCR *and* the Cohere `command-a-vision-07-2025` vision LLM all returned
176. That looked like three independent engines agreeing.

They were not independent: all three were fed the same `pct:15` downscale. A controlled
resolution sweep, holding the extractor fixed and varying only the source raster:

| LOC `pct:` | masthead crop | reading | confidence | correct |
|---|---|---|---|---|
| 15 | 857×170 | 378 | low | no |
| 25 | 1429×284 | **176** | **high** | **no** |
| 40 | 2286×455 | 175 | high | yes |
| 60 | 3429×683 | 176 | low | no |
| 100 | 5716×1139 | 175 | low | yes |

Two conclusions:

1. **Upscaling one raster 2×/3×/4× is correlated error, not independent evidence.** At
   `pct:25` all three upscales agreed on the wrong digit, so the pipeline reported high
   confidence for 176. Same-engine, same-raster consensus cannot certify itself.
2. **The error was never intrinsic to the glyph.** On a tight, correctly located,
   full-resolution crop of the VOL./NO. line, plain `tesseract --psm 6` reads
   `VOL. VIII.—NO. 175` correctly. The engine was fine; the input was not.

`masthead_reader.py` replaces the old path: it reads **two genuinely different rasters**
(`pct:40` and `pct:60`), crops the VOL./NO. band rather than a blanket top-16% strip, and
requires cross-**resolution** agreement. Scored on the agent-verified ground truths
(`benchmark_readers.py` → `reader_benchmark.json`):

| reader | correct | accepted (high-conf) | **accepted WRONG** | precision | 1922-04-06 |
|---|---|---|---|---|---|
| old (same-raster upscales) | 9/10 | 8 | **1** | 88% | **176 — wrong, high conf** |
| new (cross-resolution) | 8/10 | 7 | **0** | **100%** | **175 — correct, high conf** |

Precision goes to 100% *and* the hard case is now read correctly. Three refinements got
there, each driven by inspecting why a specific page failed:

1. **Three rasters, majority vote** (`pct:40`, `pct:60`, tie-breaker `pct:25`). Two
   resolutions alone rejected three correct answers for want of a second vote.
2. **Bail only after two empty rasters.** Quitting after one was too eager — no single
   resolution is universally best, and a false reject makes the walk pay for a whole
   extra page anyway.
3. **Discard implausibly short issue numbers.** At `pct:40` the reader matched a bare
   `"2"` from nearby body text. That junk vote was not harmless: it manufactured a fake
   tie. Filtering it lets the parse continue and find the real `175`, turning a refusal
   into a correct 2-of-3 majority (175 / 176 / 175).

Remaining misses are conservative refusals, never wrong answers: `1900-07-04` ends in
`conflict` (pct:60 reads 19589, pct:25 reads 19580) and `1900-08-01` parses at no
resolution. The walk simply advances a page. Locked in by
`test_reader_benchmark_has_no_false_confidence`.

### On the Cohere vision LLM

Wired and available at `POST /api/confirm/vision` (needs `COHERE_API_KEY`), but **not**
the primary extractor, on measured grounds:

- On a **trial** key the vision endpoint sustained roughly **one call per several
  minutes** — repeated HTTP 429 with 5–10 minute waits per successful read, against a
  1000-call/month cap. A `max_steps=8` walk would need one call per page, so in-loop use
  is not viable.
- Accuracy did not beat properly-fed tesseract: given the same `pct:15` raster it
  returned the same wrong 176; given a mislocated crop it returned 1874.
- It reads clean mastheads correctly and fast when it does get through (19589 in 1.1s).

A production key would make it a reasonable *second opinion* at the confirm step. It is
not a substitute for fixing crop geometry and resolution.
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
