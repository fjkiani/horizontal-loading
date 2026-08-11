#!/usr/bin/env python3
"""restock.py -- restore production stock after the smoke test spent it.

The ledger is a file on Render's ephemeral disk and `pool_ledger.json` is
gitignored, so a redeploy starts a container that has no ledger and `_seed_pool()`
rebuilds it from the baked catalog. `upsert()` alone cannot do this: it never
resurrects a burned or retired record, by design.

The interesting part is not that stock returns -- it is WHY. `/api/pool`
carries `seeded_at_startup`, the return value of `upsert()` at process start,
and it discriminates the two hypotheses outright:

    added=[14], refreshed=[]   ->  the ledger file was absent: disk was wiped
    added=[],   refreshed=[14] ->  a ledger survived the restart

Checkpoints after every phase.
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

ORIGIN = os.environ.get("SEAL_ORIGIN", "https://seal-prompt-generator.onrender.com").rstrip("/")
SERVICE = os.environ.get("RENDER_SERVICE", "srv-d9qf1najobas738066lg")
KEY = os.environ.get("RENDER_KEY", "")
OUT = os.environ.get("SEAL_RESTOCK_OUT", "restock.json")
PROBES = "/mnt/shared-workspace/shared/probes"

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

STATE = {"origin": ORIGIN, "service": SERVICE, "phases": {},
         "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "done": False}


def save():
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(STATE, fh, indent=2)
    os.replace(tmp, OUT)
    try:
        os.makedirs(PROBES, exist_ok=True)
        with open(os.path.join(PROBES, os.path.basename(OUT)), "w") as fh:
            json.dump(STATE, fh, indent=2)
    except Exception:
        pass


def call(method, url, body=None, timeout=60, auth=False):
    headers = {"Accept": "application/json", "User-Agent": "seal-restock/1"}
    if auth:
        headers["Authorization"] = "Bearer " + KEY
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            raw, st = r.read().decode("utf-8", "replace"), r.getcode()
    except urllib.error.HTTPError as e:
        raw, st = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:
        return None, {"transport_error": repr(e)}
    try:
        return st, json.loads(raw)
    except Exception:
        return st, raw[:1500]


def pool():
    st, js = call("GET", ORIGIN + "/api/pool", timeout=120)
    if st != 200 or not isinstance(js, dict):
        return None
    rows = [r for r in js.get("categories", []) if r.get("n_total")]
    seed = js.get("seeded_at_startup") or {}
    return {"n_available_total": js.get("n_available_total"),
            "n_burned_total": js.get("n_burned_total"),
            "n_served_total": sum(r["n_served"] for r in rows),
            "by_cat": {r["category"]: r["n_available"] for r in rows},
            "n_added": len(seed.get("added") or []),
            "n_refreshed": len(seed.get("refreshed") or []),
            "catalog_traps": js.get("catalog_traps")}


def main():
    if not KEY:
        print("RENDER_KEY not set", flush=True)
        return 1

    before = pool()
    STATE["phases"]["before"] = before
    save()
    print("BEFORE  available=%s burned=%s served=%s  seeded(added=%s refreshed=%s)"
          % (before["n_available_total"], before["n_burned_total"], before["n_served_total"],
             before["n_added"], before["n_refreshed"]), flush=True)

    st, js = call("POST", "https://api.render.com/v1/services/%s/deploys" % SERVICE,
                  {"clearCache": "do_not_clear"}, auth=True)
    dep = (js or {}).get("id")
    STATE["phases"]["trigger"] = {"status": st, "deploy_id": dep,
                                  "commit": ((js or {}).get("commit") or {}).get("id")}
    save()
    print("TRIGGER status=%s deploy=%s commit=%s"
          % (st, dep, STATE["phases"]["trigger"]["commit"]), flush=True)
    if not dep:
        STATE["fatal"] = js
        STATE["done"] = True
        save()
        return 1

    t0, dstat = time.time(), None
    while time.time() - t0 < 900:
        time.sleep(15)
        s, d = call("GET", "https://api.render.com/v1/services/%s/deploys/%s" % (SERVICE, dep),
                    auth=True)
        dstat = (d or {}).get("status")
        print("  ... %4ds  %s" % (int(time.time() - t0), dstat), flush=True)
        STATE["phases"]["deploy"] = {"status": dstat, "elapsed_s": int(time.time() - t0),
                                     "finishedAt": (d or {}).get("finishedAt")}
        save()
        if dstat in ("live", "build_failed", "update_failed", "canceled", "deactivated"):
            break
    if dstat != "live":
        STATE["fatal"] = "deploy ended %s" % dstat
        STATE["done"] = True
        save()
        return 1

    after, t1 = None, time.time()
    while time.time() - t1 < 300:
        after = pool()
        if after:
            break
        time.sleep(5)
    STATE["phases"]["after"] = after
    save()
    if not after:
        STATE["fatal"] = "origin never answered /api/pool after deploy"
        STATE["done"] = True
        save()
        return 1
    print("AFTER   available=%s burned=%s served=%s  seeded(added=%s refreshed=%s)"
          % (after["n_available_total"], after["n_burned_total"], after["n_served_total"],
             after["n_added"], after["n_refreshed"]), flush=True)

    checks = [
        ("stock is back to the full baked catalog",
         after["n_available_total"] == after["catalog_traps"] == 14,
         "available=%s catalog=%s" % (after["n_available_total"], after["catalog_traps"])),
        ("every category is restocked to its baked count",
         after["by_cat"] == {"geography": 2, "health and medicine": 1, "legal": 3,
                             "science and technology": 4, "travel": 4},
         after["by_cat"]),
        ("nothing is carried over as burned or served",
         after["n_burned_total"] == 0 and after["n_served_total"] == 0,
         "burned=%s served=%s" % (after["n_burned_total"], after["n_served_total"])),
        ("the disk was wiped, not merely re-upserted (added=14, refreshed=0)",
         after["n_added"] == 14 and after["n_refreshed"] == 0,
         "added=%s refreshed=%s" % (after["n_added"], after["n_refreshed"])),
        ("this is a restart effect, not an un-burn: prior stock really was spent",
         before["n_available_total"] == 7 and after["n_available_total"] == 14,
         "%s -> %s" % (before["n_available_total"], after["n_available_total"])),
    ]
    STATE["checks"] = [{"check": c, "ok": bool(o), "detail": str(d)[:300]} for c, o, d in checks]
    n_ok = sum(1 for _, o, _ in checks if o)
    STATE["summary"] = {"n_checks": len(checks), "n_pass": n_ok,
                        "failed": [c for c, o, _ in checks if not o]}
    STATE["done"] = True
    STATE["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save()
    print()
    for c, o, d in checks:
        print("%-4s %-62s %s" % ("PASS" if o else "FAIL", c, str(d)[:90]), flush=True)
    print("\nRESTOCK: %d/%d" % (n_ok, len(checks)), flush=True)
    return 0 if n_ok == len(checks) else 2


if __name__ == "__main__":
    sys.exit(main())
