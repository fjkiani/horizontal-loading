#!/usr/bin/env python3
"""poolsmoke.py -- live production smoke test of the consumption ledger.

Runs against the deployed origin, not a TestClient. Every assertion is recorded
as a row rather than raised, so one failure does not hide the rest, and the
whole result set is checkpointed to disk after EVERY step so an interrupt loses
at most one HTTP call.

This test SPENDS PRODUCTION STOCK. That is the point -- exhaustion cannot be
verified without exhausting something. The ledger lives on Render's ephemeral
disk, so a redeploy restocks it; that is the documented recovery and it is
disclosed rather than hidden.

env:
  SEAL_ORIGIN   default https://seal-prompt-generator.onrender.com
  SEAL_SMOKE_OUT default poolsmoke.json
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

ORIGIN = os.environ.get("SEAL_ORIGIN", "https://seal-prompt-generator.onrender.com").rstrip("/")
OUT = os.environ.get("SEAL_SMOKE_OUT", "poolsmoke.json")
PROBES = "/mnt/shared-workspace/shared/probes"

# The banned list is read from the repo so the test cannot drift from the gate.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import source_gate as sg
    BANNED = tuple(sg.BANNED_DOMAINS)
    _BANNED_SRC = "source_gate.BANNED_DOMAINS"
except Exception as e:  # pragma: no cover
    BANNED = ()
    _BANNED_SRC = "UNAVAILABLE: %s" % e

STATE = {
    "origin": ORIGIN,
    "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "banned_list_source": _BANNED_SRC,
    "n_banned_domains": len(BANNED),
    "checks": [],
    "steps": {},
    "served": [],          # every prompt this run drew, for the banned-source audit
    "done": False,
}

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def save():
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(STATE, fh, indent=2, sort_keys=False)
    os.replace(tmp, OUT)
    try:
        os.makedirs(PROBES, exist_ok=True)
        with open(os.path.join(PROBES, os.path.basename(OUT)), "w") as fh:
            json.dump(STATE, fh, indent=2, sort_keys=False)
    except Exception:
        pass  # the local checkpoint is authoritative; the probes copy is a mirror


def check(name, ok, detail=""):
    STATE["checks"].append({"check": name, "ok": bool(ok), "detail": str(detail)[:500]})
    print("%-4s %-58s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:110]), flush=True)
    save()
    return bool(ok)


def call(method, path, body=None, timeout=60):
    """-> (status, parsed_json_or_text, elapsed_s). Never raises on HTTP status."""
    url = ORIGIN + path
    data = None
    headers = {"Accept": "application/json", "User-Agent": "seal-poolsmoke/1"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            raw, status = r.read().decode("utf-8", "replace"), r.getcode()
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:
        return (None, {"transport_error": repr(e)}, time.time() - t0)
    try:
        return (status, json.loads(raw), time.time() - t0)
    except Exception:
        return (status, raw[:2000], time.time() - t0)


def bycat(js):
    """/api/pool returns categories as a LIST of rows; index it by name."""
    rows = (js or {}).get("categories") or []
    if isinstance(rows, dict):
        return rows
    return {r.get("category"): r for r in rows if isinstance(r, dict)}


def draw(cat, request_key=None, timeout=90):
    body = {"trap_class": "category", "category": cat}
    if request_key:
        body["request_key"] = request_key
    st, js, el = call("POST", "/api/generate", body, timeout=timeout)
    if st == 200 and isinstance(js, dict) and js.get("status") == "served":
        STATE["served"].append({
            "category": cat, "trap_id": js.get("trap_id"),
            "request_key": js.get("request_key"), "reissued": js.get("reissued"),
            "n_available": js.get("n_available"),
            "answer": (js.get("result") or {}).get("answer"),
            "sources": (js.get("result") or {}).get("sources") or [],
        })
    return st, js, el


# ---------------------------------------------------------------- step 0: wake
def step_wake():
    """Free-tier origin sleeps; a cold start is ~50 s. Poll health, don't assume."""
    t0, st, js = time.time(), None, None
    while time.time() - t0 < 240:
        st, js, el = call("GET", "/api/health", timeout=120)
        if st == 200:
            break
        time.sleep(5)
    STATE["steps"]["wake"] = {"status": st, "body": js, "cold_start_s": round(time.time() - t0, 1)}
    save()
    check("origin answers /api/health 200", st == 200, "%s in %.1fs" % (st, time.time() - t0))
    return st == 200


# --------------------------------------------------- step 1: initial pool state
def step_pool_initial():
    st, js, _ = call("GET", "/api/pool")
    STATE["steps"]["pool_initial"] = {"status": st, "body": js}
    save()
    if st != 200 or not isinstance(js, dict):
        check("GET /api/pool 200", False, js)
        return None
    check("GET /api/pool 200", True, "")
    cats = bycat(js)
    stocked = {k: v for k, v in cats.items() if (v or {}).get("n_total", 0) > 0}
    tot_avail = sum((v or {}).get("n_available", 0) for v in cats.values())
    check("pool holds 14 available prompts at rest",
          tot_avail == 14 and js.get("n_available_total") == 14,
          "summed=%s reported=%s" % (tot_avail, js.get("n_available_total")))
    check("stock spans exactly 5 categories", len(stocked) == 5,
          "%s" % {k: v.get("n_total") for k, v in sorted(stocked.items())})
    low = sorted(k for k, v in cats.items() if (v or {}).get("low_water"))
    check("low-water alarm trips on the thin categories",
          set(low) >= {"geography", "health and medicine"}, "low_water=%s" % low)
    persistence = json.dumps(js.get("persistence", ""))
    check("/api/pool states prompts are not recycled",
          "not recycled" in persistence.lower() or "not recycled" in json.dumps(js).lower(),
          persistence[:200])
    return js


# ------------------------------------------ step 2: categories splits the cases
def step_categories():
    st, js, _ = call("GET", "/api/categories")
    STATE["steps"]["categories"] = {"status": st, "body": js}
    save()
    if st != 200 or not isinstance(js, dict):
        check("GET /api/categories 200", False, js)
        return
    check("GET /api/categories 200", True, "")
    rows = js.get("categories") or []
    by = {r.get("category"): r for r in rows if isinstance(r, dict)}
    check("all 16 taxonomy categories are reported", len(by) == 16, "n=%d" % len(by))
    unstocked = sorted(k for k, r in by.items() if r.get("unstocked"))
    exhausted = sorted(k for k, r in by.items() if r.get("exhausted"))
    check("never-baked categories report unstocked, not exhausted",
          len(unstocked) == 11 and exhausted == [],
          "unstocked=%d %s | exhausted=%s" % (len(unstocked), unstocked, exhausted))
    check("legacy n_served alias equals n_available",
          all(r.get("n_served") == r.get("n_available") for r in rows),
          "checked %d rows" % len(rows))


# ------------------------------- step 3: drain a 1-prompt category -> 409 body
def step_drain_health():
    cat = "health and medicine"
    st, js, _ = draw(cat)
    ok = st == 200 and isinstance(js, dict) and js.get("status") == "served"
    check("health and medicine serves its single prompt", ok, "%s %s" % (st, (js or {}).get("trap_id")))
    if ok:
        check("draining to zero reports n_available=0", js.get("n_available") == 0,
              "n_available=%s" % js.get("n_available"))
    st2, js2, _ = draw(cat)
    STATE["steps"]["drain_health"] = {"first": {"status": st, "body": js},
                                      "second": {"status": st2, "body": js2}}
    save()
    check("an empty category refuses with 409, never a silent reissue", st2 == 409, "status=%s" % st2)
    if isinstance(js2, dict):
        check("the 409 body names the shortfall",
              js2.get("status") == "exhausted" and js2.get("n_available") == 0
              and js2.get("n_burned", 0) + js2.get("n_served", 0) >= 1
              and "replenish" in js2,
              json.dumps({k: js2.get(k) for k in
                          ("status", "n_available", "n_burned", "n_served", "n_total", "replenish")}))


# ----------------------------- step 4: countdown on the 4-prompt science pool
def step_countdown_science():
    cat = "science and technology"
    seen, counts, rows = [], [], []
    for i in range(5):
        st, js, _ = draw(cat)
        rows.append({"i": i, "status": st,
                     "trap_id": (js or {}).get("trap_id"),
                     "n_available": (js or {}).get("n_available"),
                     "answer": ((js or {}).get("result") or {}).get("answer")})
        if st == 200:
            seen.append(js.get("trap_id"))
            counts.append(js.get("n_available"))
        else:
            rows[-1]["body"] = js
            break
    STATE["steps"]["countdown_science"] = {"rows": rows}
    save()
    check("science and technology serves 4 distinct prompts",
          len(seen) == 4 and len(set(seen)) == 4, "n=%d distinct=%d" % (len(seen), len(set(seen))))
    check("the countdown is 3,2,1,0 with no off-by-one", counts == [3, 2, 1, 0], "counts=%s" % counts)
    check("the 5th draw is refused", rows[-1]["status"] == 409, "status=%s" % rows[-1]["status"])
    check("no science answer repeats within the run",
          len({r["answer"] for r in rows if r.get("answer")}) == len([r for r in rows if r.get("answer")]),
          [r.get("answer") for r in rows])


# ------------------------- step 5: idempotent retry vs a genuinely new request
def step_request_key():
    cat = "travel"
    a_st, a, _ = draw(cat, request_key="smoke-1")
    b_st, b, _ = draw(cat, request_key="smoke-1")
    c_st, c, _ = draw(cat, request_key="smoke-2")
    STATE["steps"]["request_key"] = {"a": {"status": a_st, "trap_id": (a or {}).get("trap_id"),
                                           "n_available": (a or {}).get("n_available")},
                                     "b": {"status": b_st, "trap_id": (b or {}).get("trap_id"),
                                           "n_available": (b or {}).get("n_available"),
                                           "reissued": (b or {}).get("reissued")},
                                     "c": {"status": c_st, "trap_id": (c or {}).get("trap_id"),
                                           "n_available": (c or {}).get("n_available")}}
    save()
    ok3 = a_st == b_st == c_st == 200
    check("travel serves three requests", ok3, "%s %s %s" % (a_st, b_st, c_st))
    if not ok3:
        return
    check("a repeated request_key returns the identical prompt",
          a.get("trap_id") == b.get("trap_id"), "%s vs %s" % (a.get("trap_id"), b.get("trap_id")))
    check("a repeated request_key burns nothing",
          a.get("n_available") == b.get("n_available") and b.get("reissued") is True,
          "n_available %s -> %s reissued=%s" % (a.get("n_available"), b.get("n_available"), b.get("reissued")))
    check("a different request_key gets a different prompt",
          c.get("trap_id") not in (a.get("trap_id"),), "%s" % c.get("trap_id"))
    check("a genuinely new request does decrement",
          c.get("n_available") == b.get("n_available") - 1,
          "%s -> %s" % (b.get("n_available"), c.get("n_available")))


# --------------------------------------- step 6: banned sources in live output
def step_banned_audit():
    viol = []
    for row in STATE["served"]:
        blob = json.dumps(row.get("sources") or []).lower()
        for dom in BANNED:
            if dom.lower() in blob:
                viol.append({"trap_id": row["trap_id"], "category": row["category"], "domain": dom})
    STATE["steps"]["banned_audit"] = {"n_prompts_audited": len(STATE["served"]),
                                      "n_sources": sum(len(r.get("sources") or []) for r in STATE["served"]),
                                      "violations": viol}
    save()
    check("no live-served prompt cites a banned source",
          len(BANNED) > 0 and not viol,
          "%d prompts / %d sources / %d banned domains checked"
          % (len(STATE["served"]), sum(len(r.get("sources") or []) for r in STATE["served"]), len(BANNED)))


# ----------------------------------------------- step 7: final ledger arithmetic
def step_pool_final(initial):
    st, js, _ = call("GET", "/api/pool")
    STATE["steps"]["pool_final"] = {"status": st, "body": js}
    save()
    if st != 200 or not isinstance(js, dict) or not initial:
        check("GET /api/pool 200 after the run", False, js)
        return
    ci, cf = bycat(initial), bycat(js)
    spent = {k: (ci.get(k, {}).get("n_available", 0) - cf.get(k, {}).get("n_available", 0))
             for k in cf if (ci.get(k, {}).get("n_total", 0) or 0) > 0}
    drawn = {}
    for r in STATE["served"]:
        if not r.get("reissued"):
            drawn[r["category"]] = drawn.get(r["category"], 0) + 1
    check("every prompt spent is a prompt this test drew",
          {k: v for k, v in spent.items() if v} == drawn,
          "ledger says %s / test drew %s" % ({k: v for k, v in spent.items() if v}, drawn))
    check("untouched categories did not move",
          all(v == 0 for k, v in spent.items() if k not in drawn),
          {k: v for k, v in spent.items() if k not in drawn})
    STATE["spent_by_category"] = spent
    STATE["remaining_available_total"] = sum(v.get("n_available", 0) for v in cf.values())


def main():
    if not step_wake():
        STATE["done"] = True
        STATE["fatal"] = "origin never answered health"
        save()
        return 1
    initial = step_pool_initial()
    step_categories()
    step_drain_health()
    step_countdown_science()
    step_request_key()
    step_banned_audit()
    step_pool_final(initial)
    n = len(STATE["checks"])
    n_ok = sum(1 for c in STATE["checks"] if c["ok"])
    STATE["summary"] = {"n_checks": n, "n_pass": n_ok, "n_fail": n - n_ok,
                        "failed": [c["check"] for c in STATE["checks"] if not c["ok"]]}
    STATE["done"] = True
    STATE["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save()
    print("\nSMOKE: %d/%d checks passed%s" % (n_ok, n, "" if n_ok == n else "  FAILED: %s"
                                              % STATE["summary"]["failed"]), flush=True)
    return 0 if n_ok == n else 2


if __name__ == "__main__":
    sys.exit(main())
