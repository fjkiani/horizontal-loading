"""memgap -- account for the 132.5 MB between memaudit and concur.

memaudit measured geography standalone at 351.6 MB peak RSS. concur measured
geography's peak at 219.1 MB. Same instrument in both cases -- Linux
ru_maxrss, a high-water mark that cannot be missed by sampling -- so the gap
is not an artefact of how the number was read.

Two candidate explanations were already killed from the artifacts:

  GATE OVERHEAD. Dead. memaudit records rss_after_generate and rss_after_gate
  separately and they are EQUAL for all 11 categories, so source-gate witness
  traffic contributes nothing to the peak.

  SAMPLING ALIASING. Dead. concur's peak_mb() also reads ru_maxrss.

What the artifacts do point at is an accounting gap inside memaudit itself.
For geography, tracemalloc reports a peak Python heap of 182.8 MB while RSS
peaks at 351.6 MB. Interpreter plus imports is 30.6 MB. So

    30.6 + 182.8 = 213.4 MB  of RSS is attributable to the Python heap,
                             and that is concur's 219.1 within 6 MB,

leaving ~138 MB resident that the Python allocator never saw. Memory that
tracemalloc cannot see is memory allocated by C: TLS record buffers during the
HTTPS download, and zlib's output buffers during decompression. Both exist
only on a COLD cache. concur ran warm.

This script tests that directly. Each measurement runs in its own subprocess,
so ru_maxrss is scoped to one generator call, and the cache is either a fresh
empty directory (cold) or the real cache (warm). It also splits the fetch from
the parse, so download transient and parsed structures are separated.

Prediction if the hypothesis holds: cold ~= 350 MB, warm ~= 215 MB, and the
difference sits in RSS that tracemalloc does not attribute.

Writes memgap.json.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_CACHE = "/workspace/seal_cache"
CATS = ("geography", "travel")

CHILD = r'''
import json, os, resource, sys, tracemalloc
sys.path.insert(0, %(here)r)

def maxrss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

def cur_rss_mb():
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") / 1048576.0)
    except Exception:
        return None

def vmhwm_mb():
    try:
        for ln in open("/proc/self/status"):
            if ln.startswith("VmHWM:"):
                return int(ln.split()[1]) / 1024.0
    except Exception:
        return None

rec = {"cat": %(cat)r, "mode": %(mode)r}
rec["rss_after_interpreter_mb"] = round(maxrss_mb(), 1)

import category_traps as ct
import gen_v2, gen_v3, gen_v4
import net
rec["rss_after_imports_mb"] = round(maxrss_mb(), 1)

# Stage the raw fetch on its own so the download transient is separable from
# the parsed structures the generator builds afterwards.
tracemalloc.start()
try:
    if %(cat)r == "geography":
        blob = net.fetch("https://davidmegginson.github.io/ourairports-data/airports.csv",
                         timeout=900, attempts=3)
        rec["fetch_bytes"] = len(blob)
    else:
        a = net.fetch("https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat",
                      timeout=900, attempts=3)
        b = net.fetch("https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat",
                      timeout=900, attempts=3)
        rec["fetch_bytes"] = len(a) + len(b)
        blob = a
        del a, b
    del blob
except Exception as e:
    rec["fetch_error"] = "%%s: %%s" %% (type(e).__name__, e)
c1, p1 = tracemalloc.get_traced_memory()
rec["heap_peak_after_fetch_mb"] = round(p1 / 1048576.0, 1)
rec["rss_after_fetch_mb"] = round(maxrss_mb(), 1)
rec["cur_rss_after_fetch_mb"] = round(cur_rss_mb() or 0, 1)

tracemalloc.reset_peak()
try:
    cand = ct.GENERATORS[%(cat)r]()
    rec["answer"] = cand.answer
    rec["n_base"] = cand.n_base
except Exception as e:
    rec["error"] = "%%s: %%s" %% (type(e).__name__, e)
c2, p2 = tracemalloc.get_traced_memory()
rec["heap_peak_generate_only_mb"] = round(p2 / 1048576.0, 1)
rec["heap_current_end_mb"] = round(c2 / 1048576.0, 1)
rec["peak_rss_mb"] = round(maxrss_mb(), 1)
rec["vmhwm_mb"] = round(vmhwm_mb() or 0, 1)
rec["cur_rss_end_mb"] = round(cur_rss_mb() or 0, 1)

base = rec["rss_after_imports_mb"]
heap = max(rec["heap_peak_after_fetch_mb"], rec["heap_peak_generate_only_mb"])
rec["heap_peak_max_mb"] = round(heap, 1)
rec["rss_attributable_to_heap_mb"] = round(base + heap, 1)
rec["rss_not_attributed_mb"] = round(rec["peak_rss_mb"] - (base + heap), 1)
print("@@JSON@@" + json.dumps(rec))
'''


def run(cat, mode):
    if mode == "cold":
        cache = tempfile.mkdtemp(prefix="memgap_cold_")
    else:
        cache = REAL_CACHE
    env = dict(os.environ)
    env["SEAL_NET_CACHE"] = cache
    src = CHILD % {"here": HERE, "cat": cat, "mode": mode}
    try:
        p = subprocess.run([sys.executable, "-c", src], capture_output=True,
                           text=True, timeout=1500, env=env, cwd=HERE)
        out = p.stdout
        rec = None
        for ln in out.splitlines():
            if ln.startswith("@@JSON@@"):
                rec = json.loads(ln[8:])
        if rec is None:
            rec = {"cat": cat, "mode": mode,
                   "child_error": (p.stderr or out)[-600:]}
        rec["cache_dir_bytes"] = sum(
            os.path.getsize(os.path.join(cache, f))
            for f in os.listdir(cache)
            if os.path.isfile(os.path.join(cache, f))) if os.path.isdir(cache) else None
    finally:
        if mode == "cold" and cache.startswith("/tmp/"):
            shutil.rmtree(cache, ignore_errors=True)
    return rec


def main():
    res = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "memaudit_geography_peak_mb": 351.6,
           "concur_geography_peak_mb": 219.1,
           "gap_mb": 132.5, "runs": []}
    for cat in CATS:
        for mode in ("warm", "cold"):
            print("=== %s / %s ===" % (cat, mode), flush=True)
            r = run(cat, mode)
            res["runs"].append(r)
            for k in ("peak_rss_mb", "rss_after_imports_mb",
                      "heap_peak_after_fetch_mb",
                      "heap_peak_generate_only_mb", "heap_peak_max_mb",
                      "rss_attributable_to_heap_mb",
                      "rss_not_attributed_mb", "fetch_bytes", "answer",
                      "error", "child_error"):
                if k in r:
                    print("  %-30s %s" % (k, r[k]), flush=True)

    by = {(r.get("cat"), r.get("mode")): r for r in res["runs"]}
    summ = {}
    for cat in CATS:
        w, c = by.get((cat, "warm"), {}), by.get((cat, "cold"), {})
        pw, pc = w.get("peak_rss_mb"), c.get("peak_rss_mb")
        if pw and pc:
            summ[cat] = {
                "warm_peak_mb": pw, "cold_peak_mb": pc,
                "cold_minus_warm_mb": round(pc - pw, 1),
                "warm_heap_peak_mb": w.get("heap_peak_max_mb"),
                "cold_heap_peak_mb": c.get("heap_peak_max_mb"),
                "heap_delta_mb": round((c.get("heap_peak_max_mb") or 0)
                                       - (w.get("heap_peak_max_mb") or 0), 1),
                "warm_unattributed_mb": w.get("rss_not_attributed_mb"),
                "cold_unattributed_mb": c.get("rss_not_attributed_mb"),
            }
    res["summary"] = summ
    g = summ.get("geography", {})
    if g:
        res["verdict"] = (
            "cold-cache C-level allocation explains the gap"
            if abs(g["cold_minus_warm_mb"]) > 50
            and abs(g["heap_delta_mb"]) < abs(g["cold_minus_warm_mb"]) / 2
            else "cold/warm does NOT explain the gap; another cause remains")
        res["worst_case_peak_mb"] = max(
            r.get("peak_rss_mb") or 0 for r in res["runs"])
        res["headroom_at_worst_case_mb"] = round(
            512 - res["worst_case_peak_mb"], 1)
    print("\n" + json.dumps({"summary": summ,
                             "verdict": res.get("verdict"),
                             "worst_case_peak_mb": res.get("worst_case_peak_mb"),
                             "headroom_at_worst_case_mb":
                                 res.get("headroom_at_worst_case_mb")},
                            indent=2), flush=True)
    with open("memgap.json", "w") as fh:
        json.dump(res, fh, indent=2)
    print("wrote memgap.json", flush=True)


if __name__ == "__main__":
    main()
