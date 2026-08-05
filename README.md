# Seal Prompt Generator

A FastAPI + static-frontend app that serves **API-proof research prompts** — questions
whose answers are computed from live data and verified, and that a model cannot answer
with a single API call.

## What it serves

The current catalog is **5 verified vision traps**: historical newspaper front pages from
the Library of Congress where a masthead value (issue number / year established) is
**legible in the scan image but absent from the OCR text layer**. A text-only solver
("just call the API") cannot find the answer; only reading the image yields it.

| ID | Paper | Date | Answer | Field |
|----|-------|------|--------|-------|
| V01 | New-York Tribune | 1900-01-01 | 19405 | issue number |
| V02 | Evening Public Ledger | 1922-03-06 | 148 | issue number |
| V03 | Evening Star & Newark Advertiser | 1907-12-02 | 1832 | year established |
| V04 | New-York Tribune | 1900-01-06 | 19410 | issue number |
| V05 | Evening Public Ledger | 1922-03-27 | 166 | issue number |

Every prompt's ground truth is **computed** by `join_engine.py` from the live LOC source
and gated by `verify_joins.py` (8 rules, including V8 API-proof: the OCR must not contain
the answer).

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
| GET | `/api/prompts` | list the verified prompts (summary + verify badge) |
| GET | `/api/prompts/{id}` | full detail: prompt, answer, golden trace, sources |
| GET | `/api/prompts/{id}/image` | the scan image for a vision trap |
| POST | `/api/generate` | re-verify live + return a prompt from the pool |
| POST | `/api/stress_test` | run solver+judge over selected prompts |

### `POST /api/generate`
```json
{ "trap_class": "vision", "page_id": "V04" }
```
Re-runs the verification gate live before serving. Only `trap_class="vision"` is
supported — the NIH ranking class was **removed as non-reproducible** (the RePORTER API
never surfaces ~13% of records, duplicates rows across pages, truncates large sets, and
ties many awards at $1, so "Nth-lowest award" is not defensible ground truth).

### `POST /api/stress_test`
```json
{ "prompt_ids": ["V01", "V02"], "solver": "agent", "n_runs": 3 }
```
- `solver="agent"`: offline probe (the live agent blind-solve is run out-of-band).
- `solver="openai"`: supply `api_key`, `base_url`, and `model` to run a real
  OpenAI-compatible frontier model (e.g. ChatGPT 5.5 Pro) as the solver. The key is used
  only for the request and never persisted.

## Testing

```bash
pytest tests/test_api.py -v
```
9 tests: health, prompts verified, detail+image, generate re-verify/reject, flawed
controls caught, cache-key regression, stress-test wiring.

## Honest capability note

- **Text-only / naive-API solver: 5/5 stumped** — the answer is absent from the OCR and
  no single API call returns it. This satisfies the design constraint.
- **Vision-capable solver: 0/5 stumped** — a model with image input reads the masthead and
  extracts the answer. These traps defeat text-only solvers, not vision solvers.
- The built-in stress test uses the same underlying model as the author (proxy), so its
  results are evidence of difficulty, **not proof** of frontier failure. For the real
  ≥2/3 proof, run `solver="openai"` against your target model.

## Files

- `app/main.py` — FastAPI app + endpoints
- `app/solvers.py` — solver model_fn (agent + OpenAI-compatible)
- `app/static/` — frontend (index.html, app.js, style.css)
- `join_engine.py` — live-data connectors + compute_* (ground-truth derivation)
- `batch_prompts.py` — the 5 vision traps + 3 negative controls
- `verify_joins.py` — 8-rule verification gate
- `stress_test_runner.py` — blind-solver + judge + ≥2/3 aggregator
- `tests/test_api.py` — pytest suite
