"""smoke_api.py -- end-to-end HTTP test of the new category-generation path.

Every check writes to smoke_api.json the moment it finishes, so an interrupt
loses at most the check in flight. Run:  python3 smoke_api.py
"""
from __future__ import annotations
import json, os, sys, time

os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke_api.json")
STATE = {"started": time.time(), "checks": {}}


def save():
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(STATE, fh, indent=2, default=str)
    os.replace(tmp, OUT)


def record(name, **kw):
    STATE["checks"][name] = dict(kw, at=time.time())
    save()
    print("[%s] %s" % (name, json.dumps(kw, default=str)[:400]), flush=True)


from fastapi.testclient import TestClient  # noqa: E402
import app.main as m  # noqa: E402

C = TestClient(m.app)


def poll(job_id, limit=180, every=2.0):
    """Poll a job to a terminal state. Returns the final job dict."""
    for _ in range(limit):
        r = C.get("/api/generate/%s" % job_id)
        if r.status_code != 200:
            return {"status": "poll_http_%d" % r.status_code, "body": r.text[:300]}
        j = r.json()
        if j.get("status") != "running":
            return j
        time.sleep(every)
    return {"status": "poll_timeout"}


# ---- 1. health -------------------------------------------------------------
r = C.get("/api/health")
record("health", code=r.status_code, ok=r.status_code == 200)

# ---- 2. seed roster --------------------------------------------------------
r = C.get("/api/seeds")
body = r.json() if r.status_code == 200 else {"err": r.text[:300]}
record("seeds", code=r.status_code,
       categories=len(body.get("roster", body)) if isinstance(body, dict) else None,
       sample=str(body)[:300])

r = C.get("/api/seeds", params={"category": "celebrities/public figures"})
record("seeds_filtered", code=r.status_code, body=str(r.json())[:400])

# ---- 3. bad category -> 400 -----------------------------------------------
r = C.post("/api/generate", json={"trap_class": "category", "category": "astrology"})
record("bad_category", code=r.status_code, expect=400,
       pass_=r.status_code == 400, detail=str(r.json())[:200])

# ---- 4. bad seed kwargs -> 400 with the accepted-parameter list ------------
r = C.post("/api/generate", json={"trap_class": "category",
                                  "category": "celebrities/public figures",
                                  "seed": {"bogus": 1}})
record("bad_seed", code=r.status_code, expect=400,
       pass_=r.status_code == 400, detail=str(r.json())[:300])

# ---- 5. celebrities default seed -> expect the GND identifier -------------
r = C.post("/api/generate", json={"trap_class": "category",
                                  "category": "celebrities/public figures",
                                  "seed": 0})
record("celeb_submit", code=r.status_code, body=str(r.json())[:300])
if r.status_code == 200:
    j = poll(r.json()["job_id"])
    res = (j.get("result") or {})
    record("celeb_result", status=j.get("status"), answer=res.get("answer"),
           field=res.get("field"), detail=str(j.get("detail"))[:300],
           expect_answer="119009846",
           pass_=j.get("status") == "done" and res.get("answer") == "119009846")

# ---- 6. sports seed 0 -> FAST identifier ----------------------------------
r = C.post("/api/generate", json={"trap_class": "category",
                                  "category": "sports", "seed": 0})
if r.status_code == 200:
    j = poll(r.json()["job_id"])
    res = (j.get("result") or {})
    record("sports_result", status=j.get("status"), answer=res.get("answer"),
           field=res.get("field"), detail=str(j.get("detail"))[:300],
           expect_answer="22477",
           pass_=j.get("status") == "done" and res.get("answer") == "22477")
else:
    record("sports_result", code=r.status_code, body=r.text[:300])

# ---- 7. a seed the gate is known to refuse -> status 'refused' + reason ----
# geography's roster carries seeds beyond the shipped one; walk them until one
# terminates non-'done' so we can prove refusals surface a stated reason.
import seed_roster as sr  # noqa: E402
geo = sr.seeds_for("geography")
record("geography_roster", n=len(geo), seeds=str(geo)[:400])
found = None
for i in range(1, min(len(geo), 5)):
    r = C.post("/api/generate", json={"trap_class": "category",
                                      "category": "geography", "seed": i})
    if r.status_code != 200:
        continue
    j = poll(r.json()["job_id"])
    record("geography_seed_%d" % i, status=j.get("status"),
           answer=(j.get("result") or {}).get("answer"),
           detail=str(j.get("detail"))[:300],
           violations=str(j.get("violations"))[:300])
    if j.get("status") in ("refused", "error"):
        found = i
        break
record("refusal_probe", found_refusing_seed=found,
       pass_=found is not None)

# ---- 8. vision branch still reachable / error text updated ----------------
r = C.post("/api/generate", json={"trap_class": "nih"})
record("vision_branch_error", code=r.status_code,
       detail=str(r.json())[:300],
       pass_=r.status_code == 400 and "category" in r.text)

STATE["ended"] = time.time()
STATE["elapsed_s"] = round(STATE["ended"] - STATE["started"], 1)
passes = {k: v.get("pass_") for k, v in STATE["checks"].items() if "pass_" in v}
STATE["summary"] = {"passed": sum(1 for v in passes.values() if v),
                    "failed": sorted(k for k, v in passes.items() if not v)}
save()
print("SUMMARY", json.dumps(STATE["summary"], indent=2), flush=True)
