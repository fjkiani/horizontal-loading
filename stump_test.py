"""Measure whether a shipped trap actually DEFEATS a solver.

Every test in evaluate_traps.py measures LEAKAGE -- positional leaks, uniform
guessability, key/order monotonicity, witness independence. None of them measures
DIFFICULTY. A trap can be perfectly leak-clean and still be trivial, because the
solver never has to do the traversal at all: it recognises the entity and recalls
the answer from parametric memory.

That is what happened with celebrities. 'Leiden' is where Johannes Diderik van der
Waals was born. A model does not need the Nobel API, the 104-laureate window, or
the min-date-of-birth ranking to say Leiden; it needs to notice the question is
about van der Waals. The ranking was never the bottleneck.

This harness separates the two things a solver must do:

  CONDITION A (trap):   the shipped prompt, closed book, no tools.
                        Accuracy here is the trap's real failure rate.

  CONDITION B (oracle): the entity is GIVEN, and the solver is asked only for the
                        field -- "What is the <field> of <entity>?" Accuracy here
                        measures pure memorisation of the answer.

  CONDITION C (recall): the solver is asked to name the entity that satisfies the
                        ranking, without being asked for the answer. Accuracy
                        measures how identifiable the entity is from the prompt.

Reading the three together tells you WHY a trap is weak, not just that it is:

  A high              -> dead trap. Solver shortcuts end to end.
  A low,  B high, C high -> the ranking is decorative. Solver can identify the
                        entity and then recall the field. Fix the FIELD: pick one
                        that is not memorable even when the entity is known.
  A low,  B high, C low  -> healthy. The entity is the bottleneck, which is
                        exactly what the API traversal is for.
  A low,  B low        -> healthy and robust; the field is not memorable either.

Writes stump_report.json after every condition of every trap, so an interrupt
loses at most one API call.
"""
import json
import os
import re
import sys
import time

import net

OUT = os.environ.get("STUMP_OUT", "stump_report.json")
POOL = "generated_pool.json"
KEY = os.environ.get("COHERE_API_KEY", "")
# Use the strongest model on the key. A weak solver failing proves nothing; a
# trap is only interesting if the best available solver cannot shortcut it.
MODEL = os.environ.get("STUMP_MODEL", "command-a-03-2025")
SLEEP = float(os.environ.get("STUMP_SLEEP", "6.0"))  # trial keys throttle ~20/min
N_RUNS = int(os.environ.get("STUMP_RUNS", "3"))
ONLY = [c for c in os.environ.get("STUMP_ONLY", "").split(",") if c]

_URL = "https://api.cohere.com/v2/chat"

# A refusal is a PASS for the trap, so it must be distinguished from a wrong
# answer. These are the forms the models actually emit.
_REFUSAL = re.compile(
    r"\b(i (do not|don't) (have|know)|cannot determine|can't determine|unable to|"
    r"i (do not|don't) have (access|enough)|insufficient information|would need to|"
    r"i'm not able|not able to answer|cannot answer|no way to know|"
    r"without access|requires access|i cannot verify)\b", re.I)


def ask(prompt, temperature=0.0):
    # net.fetch does `json.dumps(body).encode()` itself (net.py:60). Passing a
    # pre-serialised string double-encodes it into a JSON *string literal*, and
    # Cohere answers 422. Pass the dict.
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": 300,
    }
    raw = net.fetch(_URL, body=body, use_cache=False, attempts=5, timeout=120,
                    headers={"Authorization": f"Bearer {KEY}",
                             "Accept": "application/json"})
    d = json.loads(raw)
    parts = (d.get("message") or {}).get("content") or []
    return " ".join(p.get("text", "") for p in parts).strip()


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def graded(reply, answer):
    """Correct if the answer appears as a token/substring; refusal tracked apart.

    Substring matching is deliberately GENEROUS to the solver. Being generous is
    conservative for our purpose: it can only make a trap look weaker than it is,
    never stronger, so a trap that survives this grader really did stump.
    """
    if not reply:
        return "empty"
    n_reply, n_ans = _norm(reply), _norm(answer)
    if n_ans and n_ans in n_reply:
        return "correct"
    if _REFUSAL.search(reply):
        return "refused"
    return "wrong"


def conditions(t):
    field, entity, answer = t.get("field"), t.get("entity"), t.get("answer")
    return {
        "A_trap": t.get("prompt", ""),
        "B_oracle": (
            f"What is the {field} of {entity}? Answer with the value only, "
            "no explanation. If you do not know, say you do not know."),
        "C_entity": (
            t.get("prompt", "") +
            "\n\nDo NOT give the requested value. Name only the single entity "
            "that satisfies the description above."),
    }


def run_trap(t):
    res = {"category": t.get("category"), "field": t.get("field"),
           "answer": t.get("answer"), "entity": t.get("entity"),
           "witness_tier": t.get("witness_tier"), "conditions": {}}
    conds = conditions(t)
    for cname, prompt in conds.items():
        target = t.get("entity") if cname == "C_entity" else t.get("answer")
        grades, replies = [], []
        for i in range(N_RUNS):
            try:
                r = ask(prompt, temperature=0.0 if i == 0 else 0.7)
            except Exception as exc:
                grades.append("error")
                replies.append(f"{type(exc).__name__}: {exc}")
                time.sleep(SLEEP)
                continue
            grades.append(graded(r, target))
            replies.append(r[:400])
            time.sleep(SLEEP)
        n_ok = sum(1 for g in grades if g == "correct")
        n_scored = sum(1 for g in grades if g != "error")
        res["conditions"][cname] = {
            "grades": grades,
            "accuracy": round(n_ok / n_scored, 4) if n_scored else None,
            "refusal_rate": round(
                sum(1 for g in grades if g == "refused") / n_scored, 4) if n_scored else None,
            "replies": replies,
        }
    a = res["conditions"]["A_trap"]["accuracy"]
    b = res["conditions"]["B_oracle"]["accuracy"]
    c = res["conditions"]["C_entity"]["accuracy"]
    res["stump_rate"] = None if a is None else round(1.0 - a, 4)
    # Why it is weak, not just that it is.
    if a is None:
        res["diagnosis"] = "no scored runs"
    elif a >= 0.34:
        res["diagnosis"] = "DEAD: solver shortcuts the whole trap"
    elif (b or 0) >= 0.67 and (c or 0) >= 0.34:
        res["diagnosis"] = ("FRAGILE: ranking is decorative -- entity is "
                            "identifiable and the field is memorable")
    elif (b or 0) >= 0.67:
        res["diagnosis"] = ("WATCH: field is memorable, holding only because the "
                            "entity is hard to identify")
    else:
        res["diagnosis"] = "HEALTHY: neither the entity nor the field is recalled"
    return res


def main():
    if not KEY:
        print("COHERE_API_KEY not set", file=sys.stderr)
        return 2
    pool = json.load(open(POOL))
    traps = [t for t in pool if not ONLY or t.get("category") in ONLY]
    state = json.load(open(OUT)) if os.path.exists(OUT) else {"traps": {}}
    state["model"] = MODEL
    state["n_runs"] = N_RUNS
    for t in traps:
        cat = t.get("category")
        if cat in state["traps"]:
            print(f"skip {cat} (checkpointed)")
            continue
        r = run_trap(t)
        state["traps"][cat] = r
        with open(OUT + ".tmp", "w") as fh:
            json.dump(state, fh, indent=1)
        os.replace(OUT + ".tmp", OUT)
        ca = r["conditions"]
        print(f"{cat:28s} A={ca['A_trap']['accuracy']} B={ca['B_oracle']['accuracy']} "
              f"C={ca['C_entity']['accuracy']} stump={r['stump_rate']}  {r['diagnosis']}")
    print(f"\ncheckpoint: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
