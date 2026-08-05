"""
stress_test_runner.py — blind-solver + judge + aggregator for Project Seal.

ROLE SEPARATION (author != solver != judge):
  * author   : precomputed author_payloads.json (prompt + payload + answer + golden).
  * solver   : BLIND. Receives ONLY the prompt + the redacted payload (NO answer, NO golden).
               Layer 1: answer from parametric knowledge alone (no payload).
               Layer 2: solve using the inline payload.
  * judge    : scores each solver run — final-answer correctness (deterministic normalized
               match) + reasoning-trace fidelity vs the golden trajectory + failure-mode
               classification.
  * aggregator : per-prompt failure rate; >= 2/3 => proxy-validated stump.

The solver and judge are LLM calls via a pluggable model_fn. This file ships with a
deterministic local "oracle/echo" model_fn for self-test, and a hook to plug in a real
frontier model (OpenAI-compatible) for the live run.
"""
from __future__ import annotations
import json, os, re, unicodedata

# ---------------------------------------------------------------------------
# Redaction: build the solver-facing payload (strip anything that leaks the answer
# beyond what a human would legitimately have). For our traps the answer IS derivable
# from the payload by design, so we pass the payload through but NEVER the answer/golden.
# ---------------------------------------------------------------------------
def redacted_view(rec):
    view = {"prompt": rec["prompt"], "payload": rec["payload"]}
    if rec.get("image_path"):
        view["image_path"] = rec["image_path"]
    return view


# ---------------------------------------------------------------------------
# Deterministic answer matching (normalized)
# ---------------------------------------------------------------------------
def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def answer_correct(gold, given):
    if given is None:
        return False
    g, v = _norm(gold), _norm(given)
    if not v:
        return False
    # exact / substring
    if g == v or g in v or v in g:
        return True
    gtoks, vtoks = set(g.split()), set(v.split())
    # all gold tokens present (handles "Lenard", "Marie Curie")
    if len(gtoks) > 0 and gtoks.issubset(vtoks):
        return True
    # surname + at least one given-name token present (handles "Philipp Lenard")
    if len(gtoks) >= 2 and len(vtoks) >= 2 and vtoks.issubset(gtoks):
        return True
    return False


# ---------------------------------------------------------------------------
# Solver prompt construction
# ---------------------------------------------------------------------------
SOLVER_L1 = (
    "You are a research assistant. Answer the following question from your own knowledge, "
    "without any external documents. Give ONLY the final atomic answer (a name, date, or "
    "number), then a one-line justification.\n\nQUESTION:\n{prompt}\n\n"
    "FORMAT:\nANSWER: <atomic answer>\nWHY: <one line>")

SOLVER_L2 = (
    "You are a research assistant solving a multi-step research problem. You are given a "
    "question and a source dataset. Work through the dataset carefully, applying every "
    "constraint. Do not skip rows. Show your elimination reasoning step by step, then give "
    "the final atomic answer.\n\nQUESTION:\n{prompt}\n\nSOURCE DATA:\n{payload}\n\n"
    "FORMAT:\nSTEPS:\n<your step-by-step elimination>\nANSWER: <atomic answer>")


def parse_solver_output(text):
    m = re.search(r"ANSWER:\s*(.*)", text)
    if not m:
        return None, text
    ans = m.group(1).strip()
    ans = ans.splitlines()[0].strip() if ans else None
    if not ans:
        ans = None
    return ans, text


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------
JUDGE = (
    "You are a strict grader. You are given a research QUESTION, the GOLDEN elimination "
    "trajectory, the CORRECT answer, and a SOLVER's reasoning trace and final answer. "
    "Assess the solver's reasoning fidelity (not just the final answer). "
    "Classify the PRIMARY failure mode, choosing exactly one of: "
    "correct, dropped_candidate, self_correction, row_skip, hallucinated_source, "
    "confusable_name_bait, ocr_misread, arithmetic_slip, wrong_answer_other. "
    "Also give per-golden-step coverage as a fraction 0..1.\n\n"
    "QUESTION:\n{prompt}\n\nGOLDEN TRAJECTORY:\n{golden}\n\nCORRECT ANSWER: {answer}\n\n"
    "SOLVER TRACE:\n{trace}\n\nSOLVER FINAL ANSWER: {solver_answer}\n\n"
    "Respond in EXACTLY this format:\n"
    "COVERAGE: <0..1>\nFAILURE_MODE: <one of the labels>\nREASON: <one sentence>")


def parse_judge_output(text):
    cov = re.search(r"COVERAGE:\s*([0-9.]+)", text)
    fm = re.search(r"FAILURE_MODE:\s*([a-z_]+)", text)
    rs = re.search(r"REASON:\s*(.+)", text)
    return (float(cov.group(1)) if cov else None,
            fm.group(1) if fm else "wrong_answer_other",
            rs.group(1).strip() if rs else "")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
_REPO = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_AUTHOR = os.path.join(_REPO, "author_payloads.json")


def run_stress_test(model_fn, n_runs=3, author_path=_DEFAULT_AUTHOR):
    author = json.load(open(author_path))
    results = {}
    for pid, rec in author.items():
        view = redacted_view(rec)
        runs = []
        for r in range(n_runs):
            # Layer 1: knowledge-only
            l1_raw = model_fn(SOLVER_L1.format(prompt=view["prompt"]), task="solver", layer=1, run=r)
            l1_ans, _ = parse_solver_output(l1_raw)
            # Layer 2: with payload
            l2_raw = model_fn(SOLVER_L2.format(prompt=view["prompt"], payload=view["payload"]),
                              task="solver", layer=2, run=r)
            l2_ans, l2_trace = parse_solver_output(l2_raw)
            # Judge on the Layer-2 trace
            judge_raw = model_fn(JUDGE.format(prompt=view["prompt"],
                                              golden="\n".join(rec["golden"]),
                                              answer=rec["answer"],
                                              trace=l2_trace, solver_answer=l2_ans),
                                 task="judge", layer=2, run=r)
            cov, fm, reason = parse_judge_output(judge_raw)
            runs.append({
                "run": r,
                "l1_answer": l1_ans, "l1_correct": answer_correct(rec["answer"], l1_ans),
                "l2_answer": l2_ans, "l2_correct": answer_correct(rec["answer"], l2_ans),
                "coverage": cov, "failure_mode": fm, "judge_reason": reason,
            })
        n_l2_fail = sum(1 for x in runs if not x["l2_correct"])
        fail_rate = n_l2_fail / n_runs
        results[pid] = {
            "domain": rec["domain"], "method": rec["method"], "answer": rec["answer"],
            "n_base": rec["n_base"], "exploit": rec["exploit"],
            "runs": runs, "l2_fail_rate": fail_rate,
            "l1_any_correct": any(x["l1_correct"] for x in runs),
            "proxy_validated": fail_rate >= (2 / 3),
        }
    return results


def summary(results):
    lines = []
    hdr = f"{'ID':5} {'method':11} {'n_base':>6} {'L1ok':>4} {'L2fail':>6} {'verdict':16} answer"
    lines.append(hdr)
    for pid, r in results.items():
        verdict = "PROXY-STUMP" if r["proxy_validated"] else ("solved-w/-data" if not r["l1_any_correct"] else "too-easy(L1)")
        lines.append(f"{pid:5} {r['method']:11} {r['n_base']:>6} "
                     f"{str(r['l1_any_correct']):>4} {r['l2_fail_rate']:>6.2f} {verdict:16} {r['answer']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-test model_fn (deterministic, no LLM): proves the harness wiring end-to-end.
#   - For a designated "easy" prompt it returns the gold answer (0% fail).
#   - For others it returns a wrong answer (100% fail) so the gate logic is exercised.
# ---------------------------------------------------------------------------
def selftest_model_fn(prompt_text, task, layer, run, easy_id_answer=None):
    if task == "solver":
        if easy_id_answer and easy_id_answer in prompt_text:
            return f"STEPS:\nstep1\nANSWER: {easy_id_answer}"
        return "STEPS:\nguessed\nANSWER: ZZZ-WRONG"
    else:  # judge
        return "COVERAGE: 0.5\nFAILURE_MODE: wrong_answer_other\nREASON: self-test"


if __name__ == "__main__":
    # End-to-end wiring self-test with the deterministic model_fn.
    res = run_stress_test(selftest_model_fn, n_runs=3)
    print(summary(res))
    out = os.path.join(_REPO, "stress_selftest.json")
    json.dump(res, open(out, "w"), indent=2)
    print(f"\nself-test wrote {out}")
