"""
solvers.py — pluggable solver/judge model_fn for the Seal stress test.

Two solver backends conform to stress_test_runner's model_fn signature:
    model_fn(prompt_text, task, layer, run) -> str

  * agent_solver_fn   : role-separated blind solver + judge performed by the Biomni
                        agent. Blind = receives only the prompt + redacted payload
                        (never the answer/golden). This is a SAME-MODEL PROXY for
                        difficulty, not a frontier proof.
  * openai_solver_fn  : calls a user-supplied OpenAI-compatible chat endpoint
                        (e.g. ChatGPT 5.5 Pro) for the real >=2/3 run. The key is
                        used only for the request and is never logged or persisted.

The agent solver is wired through a queue: the API enqueues solve jobs, the agent
picks them up (via the benchmark runner), and returns structured results. For the
in-app "agent" path we run a deterministic self-consistency probe so the endpoint is
fully functional offline; the live agent blind-solve is driven by run_benchmark().
"""
from __future__ import annotations
import json, re, ssl, urllib.request

_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------------------
# OpenAI-compatible solver (real frontier run; user supplies key + base_url + model)
# ---------------------------------------------------------------------------
def make_openai_solver(api_key: str, base_url: str, model: str):
    base = base_url.rstrip("/")

    def _chat(messages):
        body = json.dumps({"model": model, "messages": messages,
                           "temperature": 0}).encode()
        req = urllib.request.Request(
            base + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=120, context=_CTX) as r:
            j = json.loads(r.read())
        return j["choices"][0]["message"]["content"]

    def model_fn(prompt_text, task, layer, run):
        return _chat([{"role": "user", "content": prompt_text}])

    return model_fn


# ---------------------------------------------------------------------------
# Agent solver (offline proxy). The live agent blind-solve is run out-of-band by the
# benchmark; this in-app fn provides a deterministic, honest offline probe so the
# /api/stress_test endpoint works without an external key. It deliberately does NOT
# fabricate a solve: it reports that the agent path requires the benchmark runner.
# ---------------------------------------------------------------------------
def agent_solver_fn(prompt_text, task, layer, run):
    # We do not invent an answer here. The honest offline behaviour is to return a
    # parseable but non-committal response; the real agent solve is produced by
    # run_benchmark() which calls the agent with the blind prompt and records the
    # actual answer the agent gives.
    if task == "judge":
        return "COVERAGE: 0.0\nFAILURE_MODE: wrong_answer_other\nREASON: offline probe"
    return "STEPS:\noffline probe — run the benchmark for a live agent solve\nANSWER: "


# ---------------------------------------------------------------------------
# Live agent blind-solve (used by the benchmark runner, not the web endpoint).
# The agent reads the blind prompt (+ optional image) and returns its answer.
# This function just formats the blind task; the agent's actual reply is captured
# by the caller. Kept here so the harness and app share one definition.
# ---------------------------------------------------------------------------
def blind_task_prompt(rec):
    """The exact text the blind solver sees (no answer, no golden)."""
    p = rec["prompt"]
    if rec.get("image_path"):
        return (p + "\n\n[The referenced page scan is provided as an image. Read the "
                    "masthead value directly from the image.]")
    return p
