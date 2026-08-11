"""memgap2 -- the 132.5 MB is the measuring instrument.

memgap killed the cold-cache hypothesis: cold minus warm is 3.4 MB for
geography and 2.8 MB for travel, and warm geography peaks at 350.3 MB, which
reproduces memaudit's 351.6 rather than concur's 219.1. So concur is the
outlier, and the ~145 MB of RSS that tracemalloc does not attribute to the
Python heap is present on both paths.

What differs between the two scripts is not the workload and not the cache.
It is that memaudit and memgap both call tracemalloc.start() and concur does
not. tracemalloc retains a frame trace for every LIVE allocation. That
bookkeeping is allocated by the C extension, so get_traced_memory() cannot
see it while ru_maxrss certainly can. A generator that builds hundreds of
thousands of small objects therefore pays a large, invisible, RSS-resident
tax for being watched.

If that is right, the number I have been quoting as the container's memory
requirement is an artefact of profiling and the deployed process never uses
it. That matters in both directions: it would mean concur was right, memaudit
overstated, and the real headroom is larger than I reported.

One variable, two levels, fresh subprocess per cell, nothing else changed:

    tracemalloc ON   vs   tracemalloc OFF

Prediction if the hypothesis holds: OFF lands near concur's 219 MB, ON near
memaudit's 351 MB, and the difference is roughly the unattributed residual.

Writes memgap2.json.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CATS = ("geography", "travel")
REPS = 2

CHILD = r'''
import json, os, resource, sys
sys.path.insert(0, %(here)r)
TM = %(tm)s

def maxrss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

def cur_rss_mb():
    with open("/proc/self/statm") as fh:
        return int(fh.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") / 1048576.0)

rec = {"cat": %(cat)r, "tracemalloc": TM}
import category_traps as ct
import gen_v2, gen_v3, gen_v4
rec["rss_after_imports_mb"] = round(maxrss_mb(), 1)

if TM:
    import tracemalloc
    tracemalloc.start()
    rec["rss_after_tracemalloc_start_mb"] = round(maxrss_mb(), 1)

# EXACTLY the call concur made, including the generation lock.
try:
    with ct.generation():
        cand = ct.GENERATORS[%(cat)r]()
    rec["answer"] = cand.answer
    rec["n_base"] = cand.n_base
except Exception as e:
    rec["error"] = "%%s: %%s" %% (type(e).__name__, e)

rec["rss_after_build_mb"] = round(cur_rss_mb(), 1)
rec["peak_rss_mb"] = round(maxrss_mb(), 1)
if TM:
    cur, peak = tracemalloc.get_traced_memory()
    rec["heap_peak_mb"] = round(peak / 1048576.0, 1)
    rec["heap_current_mb"] = round(cur / 1048576.0, 1)
    rec["rss_not_attributed_mb"] = round(
        rec["peak_rss_mb"] - rec["rss_after_imports_mb"] - rec["heap_peak_mb"], 1)
print("@@JSON@@" + json.dumps(rec))
'''


def run(cat, tm):
    env = dict(os.environ)
    env.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")
    src = CHILD % {"here": HERE, "cat": cat, "tm": "True" if tm else "False"}
    p = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, timeout=1200, env=env, cwd=HERE)
    for ln in p.stdout.splitlines():
        if ln.startswith("@@JSON@@"):
            return json.loads(ln[8:])
    return {"cat": cat, "tracemalloc": tm,
            "child_error": (p.stderr or p.stdout)[-600:]}


def main():
    res = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "memaudit_geography_peak_mb": 351.6,
           "concur_geography_peak_mb": 219.1,
           "gap_to_explain_mb": 132.5,
           "memaudit_used_tracemalloc": True,
           "concur_used_tracemalloc": False,
           "runs": []}
    for cat in CATS:
        for tm in (False, True):
            for rep in range(REPS):
                r = run(cat, tm)
                r["rep"] = rep
                res["runs"].append(r)
                print("%-10s tracemalloc=%-5s rep%d  peak=%-7s after_build=%-7s "
                      "heap_peak=%-7s unattributed=%s"
                      % (cat, tm, rep, r.get("peak_rss_mb"),
                         r.get("rss_after_build_mb"), r.get("heap_peak_mb"),
                         r.get("rss_not_attributed_mb")), flush=True)

    summ = {}
    for cat in CATS:
        off = [r["peak_rss_mb"] for r in res["runs"]
               if r.get("cat") == cat and not r.get("tracemalloc")
               and r.get("peak_rss_mb")]
        on = [r["peak_rss_mb"] for r in res["runs"]
              if r.get("cat") == cat and r.get("tracemalloc")
              and r.get("peak_rss_mb")]
        if off and on:
            mo, mn = sum(off) / len(off), sum(on) / len(on)
            summ[cat] = {
                "peak_tracemalloc_off_mb": round(mo, 1),
                "peak_tracemalloc_on_mb": round(mn, 1),
                "instrument_overhead_mb": round(mn - mo, 1),
                "overhead_as_frac_of_on": round((mn - mo) / mn, 4),
                "off_runs": off, "on_runs": on,
            }
    res["summary"] = summ
    g = summ.get("geography")
    if g:
        res["explains_gap"] = abs(g["instrument_overhead_mb"] - 132.5) < 45
        res["verdict"] = (
            "the 132.5 MB is tracemalloc's own bookkeeping, not the workload; "
            "concur's unprofiled number is the deployed reality"
            if res["explains_gap"] else
            "instrument overhead does not account for the gap")
        deployed = max(v["peak_tracemalloc_off_mb"] for v in summ.values())
        res["deployed_worst_case_peak_mb"] = deployed
        res["deployed_headroom_mb"] = round(512 - deployed, 1)
        res["profiled_worst_case_peak_mb"] = max(
            v["peak_tracemalloc_on_mb"] for v in summ.values())
    print("\n" + json.dumps({k: res[k] for k in
                             ("summary", "explains_gap", "verdict",
                              "deployed_worst_case_peak_mb",
                              "deployed_headroom_mb",
                              "profiled_worst_case_peak_mb") if k in res},
                            indent=2), flush=True)
    with open("memgap2.json", "w") as fh:
        json.dump(res, fh, indent=2)
    print("wrote memgap2.json", flush=True)


if __name__ == "__main__":
    main()
