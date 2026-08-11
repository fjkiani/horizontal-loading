"""Targeted production smoke for the two changes in e4deef0.

Change 1 (travel, category_traps.gen_travel): the shipped answer moved off the
IATA code -- which Ivalo Airport's own 1065-character Wikipedia article prints
verbatim -- onto the GeoNames identifier, which it does not. The swap added two
new refusal conditions that did not exist before:

    len(wd_claims["P238"]) != 1   -> TrapUnavailable
    len(wd_claims["P1566"]) != 1  -> TrapUnavailable

Both were only ever checked on ONE seed (the default hub). Across the six-seed
travel roster the swap could silently cost availability. This script measures
the survival rate directly: six travel calls, one per roster position, recording
whether each shipped and what identifier came back.

Change 2 (tv, gen_v2.gen_tv): the generator no longer materialises 11.7M decoded
lines of title.basics.tsv.gz per seed. Pre-fix this SIGKILLed the 512 MB
container and the NEXT call got HTTP 502. So tv is followed here by three live
calls; a clean refusal plus three healthy neighbours is the pass condition.

Checkpoints after every call. An interrupt costs one HTTP request.
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("SEAL_API", "https://seal-prompt-generator.onrender.com")
OUT = "/workspace/seal_deploy/prodsmoke3.json"
POLL_MAX = 120
POLL_EVERY = 2.0
PAUSE_BETWEEN = 6.0

# six travel calls exercise the whole roster, then tv, then three neighbours
PLAN = ["travel"] * 6 + ["tv shows and movies", "health and medicine",
                         "geography", "sports"]


def _post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _get(path):
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def run_one(cat):
    rec = {"category": cat, "t0": time.time()}
    try:
        sub = _post("/api/generate", {"trap_class": "category", "category": cat})
    except urllib.error.HTTPError as e:
        rec["transport_error"] = "HTTP %d on submit" % e.code
        return rec
    except Exception as e:
        rec["transport_error"] = "%s: %s" % (type(e).__name__, e)
        return rec

    rec["job_id"] = sub.get("job_id")
    rec["seed"] = sub.get("seed")
    rec["seed_index"] = sub.get("seed_index")
    poll = sub.get("poll")

    deadline = time.time() + POLL_MAX
    js = None
    while time.time() < deadline:
        time.sleep(POLL_EVERY)
        try:
            js = _get(poll)
        except urllib.error.HTTPError as e:
            rec["transport_error"] = "HTTP %d on poll" % e.code
            return rec
        except Exception as e:
            rec["transport_error"] = "%s: %s" % (type(e).__name__, e)
            return rec
        if js.get("status") in ("done", "refused", "error"):
            break

    if js is None:
        rec["status"] = "no_response"
        return rec

    rec["status"] = js.get("status")
    rec["elapsed"] = js.get("elapsed")
    ev = js.get("evaluation") or {}
    rec["verdict"] = ev.get("verdict")
    rec["witness_tier"] = ev.get("witness_tier")
    tests = ev.get("tests") or {}
    rec["n_tests"] = len(tests)
    rec["failed"] = sorted(k for k, v in tests.items()
                           if isinstance(v, dict) and v.get("pass") is False)
    rec["unproven"] = sorted(k for k, v in tests.items()
                             if isinstance(v, dict) and v.get("pass") is None)
    res = js.get("result") or {}
    rec["answer"] = res.get("answer")
    rec["field"] = res.get("field")
    rec["entity"] = res.get("entity")
    rec["prompt_words"] = len((res.get("prompt") or "").split())
    # _api_trap_summary() lifts n_base to the TOP level and does not emit
    # ranking_evidence at all. Reading res["ranking_evidence"]["n_base"] silently
    # returned None on every call -- probe defect #9, same family as #5 and #6.
    rec["n_base"] = res.get("n_base")
    rec["witness_ops"] = res.get("independent_confirming_operators")
    if js.get("status") != "done":
        rec["detail"] = js.get("detail")
    return rec


def main():
    out = {"base": BASE, "plan": PLAN, "calls": []}
    for i, cat in enumerate(PLAN):
        rec = run_one(cat)
        rec["i"] = i
        out["calls"].append(rec)
        with open(OUT, "w") as fh:
            json.dump(out, fh, indent=2)
        print("[%02d] %-22s status=%-8s verdict=%-11s answer=%s" % (
            i, cat, rec.get("status"), rec.get("verdict"), rec.get("answer")),
            flush=True)
        if rec.get("detail"):
            print("      detail: %s" % str(rec["detail"])[:180], flush=True)
        time.sleep(PAUSE_BETWEEN)

    trav = [c for c in out["calls"] if c["category"] == "travel"]
    shipped = [c for c in trav if c.get("status") == "done"]
    out["travel_summary"] = {
        "n_seeds_tried": len(trav),
        "n_shipped": len(shipped),
        "survival_rate": (len(shipped) / len(trav)) if trav else None,
        "answers": [c.get("answer") for c in trav],
        "fields": sorted({c.get("field") for c in shipped if c.get("field")}),
        "refusal_details": [c.get("detail") for c in trav
                            if c.get("status") != "done"],
    }
    after_tv = [c for c in out["calls"] if c["i"] > PLAN.index("tv shows and movies")]
    out["tv_cascade"] = {
        "tv_status": next(c.get("status") for c in out["calls"]
                          if c["category"] == "tv shows and movies"),
        "n_after": len(after_tv),
        "n_transport_errors_after": sum(1 for c in after_tv
                                        if c.get("transport_error")),
        "statuses_after": [c.get("status") or c.get("transport_error")
                           for c in after_tv],
    }
    out["transport_errors"] = sum(1 for c in out["calls"]
                                  if c.get("transport_error"))
    out["done_without_ship"] = sum(1 for c in out["calls"]
                                   if c.get("status") == "done"
                                   and c.get("verdict") != "ship")
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps({k: v for k, v in out.items() if k != "calls"}, indent=2))


if __name__ == "__main__":
    main()
