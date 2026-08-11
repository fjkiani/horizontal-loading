"""legalsmoke -- does the deployed service reproduce the 5-of-5 seed sweep?

legalfix.json measured the patch in-process. This measures it through the live
HTTP API, which is a different code path: app/main.py wraps the generator in
ct.generation(), then runs sg.validate_trap(trap, min_operators=3) and
et.evaluate_one(...), and only reports status="done" on verdict=="ship".

The API rotates seeds with seed_roster.next_seed, so N calls walk the grid.
The interesting column is facts.text_of_record: the LOC tier is only exercised
on the two seeds Cornell LII does not serve, and if validate_trap counted LOC
as a non-independent operator those two would refuse with a gate violation
rather than ship.

Writes legalsmoke.json.
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("SEAL_API", "https://seal-prompt-generator.onrender.com")
CAT = "legal"
N_CALLS = 6
POLL_EVERY = 5.0
POLL_MAX = 90
PAUSE_BETWEEN = 3.0


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def get(path):
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def one_call(i):
    rec = {"call": i}
    try:
        sub = post("/api/generate", {"trap_class": "category", "category": CAT})
    except urllib.error.HTTPError as e:
        rec["transport_error"] = f"HTTP {e.code} {e.read()[:200]!r}"
        return rec
    except Exception as e:  # noqa: BLE001
        rec["transport_error"] = f"{type(e).__name__}: {e}"
        return rec
    rec["job_id"] = sub.get("job_id")
    rec["seed"] = sub.get("seed")
    rec["seed_index"] = sub.get("seed_index")
    js = {}
    for _ in range(POLL_MAX):
        time.sleep(POLL_EVERY)
        try:
            js = get(sub["poll"])
        except urllib.error.HTTPError as e:
            rec["poll_error"] = f"HTTP {e.code}"
            continue
        except Exception as e:  # noqa: BLE001
            rec["poll_error"] = f"{type(e).__name__}: {e}"
            continue
        if js.get("status") in ("done", "refused", "error"):
            break
    else:
        rec["status"] = "timeout"
        return rec
    rec["status"] = js.get("status")
    rec["elapsed"] = js.get("elapsed")
    if js.get("status") == "done":
        res = js.get("result") or {}
        facts = res.get("facts") or {}
        rec.update({
            "answer": res.get("answer"), "entity": res.get("entity"),
            "n_base": res.get("n_base"),
            "witness_tier": res.get("witness_tier"),
            "text_of_record": facts.get("text_of_record"),
            "volume": facts.get("volume"), "page": facts.get("page"),
            "source_operators": res.get("source_operators"),
            "independent_confirming_operators":
                res.get("independent_confirming_operators"),
            "n_operators": len(res.get("source_operators") or []),
            "prompt_words": len((res.get("prompt") or "").split()),
        })
    else:
        rec["detail"] = js.get("detail")
        rec["violations"] = js.get("violations")
    return rec


def main():
    out = {"base": BASE, "category": CAT,
           "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "calls": []}
    print("%-4s %-6s %-9s %-8s %-34s %-6s %s"
          % ("call", "seed_i", "status", "answer", "entity", "n_ops",
             "text of record"), flush=True)
    for i in range(N_CALLS):
        rec = one_call(i)
        out["calls"].append(rec)
        print("%-4d %-6s %-9s %-8s %-34s %-6s %s"
              % (i, rec.get("seed_index"), rec.get("status") or
                 rec.get("transport_error", "")[:9], rec.get("answer"),
                 str(rec.get("entity"))[:34], rec.get("n_operators"),
                 rec.get("text_of_record") or
                 (rec.get("detail") or "")[:60]), flush=True)
        time.sleep(PAUSE_BETWEEN)

    done = [c for c in out["calls"] if c.get("status") == "done"]
    via = [c.get("text_of_record") for c in done]
    ans = [c.get("answer") for c in done]
    out["summary"] = {
        "n_calls": len(out["calls"]), "n_done": len(done),
        "n_refused": sum(1 for c in out["calls"]
                         if c.get("status") == "refused"),
        "ship_rate": round(len(done) / len(out["calls"]), 4),
        "distinct_answers": len(set(ans)),
        "answers": ans,
        "via_cornell": sum(1 for v in via if v and "Cornell" in v),
        "via_loc": sum(1 for v in via if v and "Congress" in v),
        "min_operators_observed": min((c["n_operators"] for c in done),
                                     default=None),
        "all_gold": all(c.get("witness_tier") == "gold" for c in done),
        "loc_tier_exercised": any(v and "Congress" in v for v in via),
    }
    s = out["summary"]
    out["verdict"] = (
        "deployed legal ships %d of %d calls, %d distinct answers, %d served by "
        "Cornell LII and %d by the Library of Congress; minimum operator count "
        "observed %s (gate requires 3), LOC tier exercised in production: %s"
        % (s["n_done"], s["n_calls"], s["distinct_answers"], s["via_cornell"],
           s["via_loc"], s["min_operators_observed"], s["loc_tier_exercised"]))
    print("\n" + json.dumps(s, indent=2), flush=True)
    print("\nverdict:", out["verdict"], flush=True)
    with open("legalsmoke.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print("wrote legalsmoke.json", flush=True)


if __name__ == "__main__":
    main()
