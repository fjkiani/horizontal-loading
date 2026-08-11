"""memgap3 -- deployed memory for every category, unprofiled.

memgap2 proved the 132.5 MB gap was tracemalloc's own bookkeeping, measured on
geography and travel: overhead 134.4 and 136.8 MB, roughly 0.77 of traced heap
in both cases. The tempting next step is to scale every memaudit number down
by ~38 per cent. That does not survive arithmetic.

memaudit's tv row was peak 5442.1 MB with traced heap 4636.2 MB and imports
about 31 MB. If overhead were 0.77 of heap it would be 3574 MB, which would
put the unprofiled peak at 1868 MB -- below the 4636 MB of heap the same run
reported. Impossible. So overhead is not proportional to heap BYTES, and the
geography/travel ratio cannot be extrapolated to a workload whose objects are
a different size. tv's overhead can be at most 5442.1 - 31 - 4636.2 = 775 MB,
a ratio of 0.17, not 0.77.

The only honest fix is to measure, not scale. Every category is re-run with
tracemalloc OFF so the deployed peak is known per category, and the 512 MB
container headroom is computed from measured numbers.

Writes memgap3.json.
"""
import json
import os
import subprocess
import sys
import time

HERE = "/workspace/seal_deploy"
CATS = ("business", "education", "geography", "health and medicine", "legal",
        "politics", "science and technology", "shopping", "sports", "travel",
        "tv shows and movies")
LIMIT_MB = 512.0
# memaudit's profiled peaks, for the side-by-side.
PROFILED = {"business": 39.1, "education": 33.6, "geography": 352.7,
            "health and medicine": 62.1, "legal": 48.0, "politics": 31.9,
            "science and technology": 32.5, "shopping": 34.4, "sports": 32.9,
            "travel": 363.8, "tv shows and movies": 33.4}

CHILD = r'''
import json, os, resource, sys
sys.path.insert(0, %(here)r)

def maxrss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

rec = {"cat": %(cat)r, "tracemalloc": False}
rec["rss_interpreter_mb"] = round(maxrss_mb(), 1)
import category_traps as ct
import gen_v2, gen_v3, gen_v4
rec["rss_after_imports_mb"] = round(maxrss_mb(), 1)
try:
    with ct.generation():
        cand = ct.GENERATORS[%(cat)r]()
    rec["answer"] = cand.answer
    rec["n_base"] = cand.n_base
except Exception as e:
    rec["error"] = "%%s: %%s" %% (type(e).__name__, e)
rec["peak_rss_mb"] = round(maxrss_mb(), 1)
print("@@JSON@@" + json.dumps(rec))
'''


def run(cat):
    env = dict(os.environ)
    env["SEAL_NET_CACHE"] = "/workspace/seal_cache"
    src = CHILD % {"here": HERE, "cat": cat}
    p = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, cwd=HERE, env=env, timeout=1800)
    for ln in (p.stdout or "").splitlines():
        if ln.startswith("@@JSON@@"):
            return json.loads(ln[len("@@JSON@@"):])
    return {"cat": cat, "error": "no json",
            "stderr": (p.stderr or "")[-300:]}


def main():
    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "container_limit_mb": LIMIT_MB, "tracemalloc": False,
           "why": "overhead scales with allocation COUNT, not heap bytes, so "
                  "the geography/travel ratio cannot be extrapolated",
           "runs": [], "rows": []}
    print("%-24s %9s %9s %9s %9s" % ("category", "deployed", "profiled",
                                     "overhead", "headroom"), flush=True)
    for cat in CATS:
        try:
            r = run(cat)
        except Exception as e:  # noqa: BLE001
            r = {"cat": cat, "error": f"{type(e).__name__}: {e}"}
        out["runs"].append(r)
        dep = r.get("peak_rss_mb")
        prof = PROFILED.get(cat)
        row = {"category": cat, "deployed_peak_mb": dep,
               "profiled_peak_mb": prof,
               "instrument_overhead_mb": (round(prof - dep, 1)
                                          if dep and prof else None),
               "overhead_frac_of_profiled": (round((prof - dep) / prof, 4)
                                             if dep and prof else None),
               "headroom_mb": round(LIMIT_MB - dep, 1) if dep else None,
               "answer": r.get("answer"), "n_base": r.get("n_base"),
               "error": r.get("error")}
        out["rows"].append(row)
        print("%-24s %9s %9s %9s %9s%s"
              % (cat, dep, prof, row["instrument_overhead_mb"],
                 row["headroom_mb"], "  " + r["error"][:44] if r.get("error") else ""),
              flush=True)

    ok = [x for x in out["rows"] if x["deployed_peak_mb"]]
    worst = max(ok, key=lambda x: x["deployed_peak_mb"])
    out["summary"] = {
        "n_measured": len(ok), "n_categories": len(CATS),
        "worst_case_category": worst["category"],
        "worst_case_deployed_peak_mb": worst["deployed_peak_mb"],
        "worst_case_headroom_mb": worst["headroom_mb"],
        "worst_case_profiled_peak_mb": worst["profiled_peak_mb"],
        "max_profiled_peak_mb": max((x["profiled_peak_mb"] for x in ok
                                     if x["profiled_peak_mb"]), default=None),
        "overhead_frac_range": [
            min(x["overhead_frac_of_profiled"] for x in ok
                if x["overhead_frac_of_profiled"] is not None),
            max(x["overhead_frac_of_profiled"] for x in ok
                if x["overhead_frac_of_profiled"] is not None)],
    }
    fr = out["summary"]["overhead_frac_range"]
    out["verdict"] = (
        "deployed worst case is %s at %.1f MB, %.1f MB of headroom in a %.0f MB "
        "container; the instrument overhead fraction ranges %.4f to %.4f across "
        "categories, so no single scale factor could have been applied to "
        "memaudit"
        % (worst["category"], worst["deployed_peak_mb"], worst["headroom_mb"],
           LIMIT_MB, fr[0], fr[1]))
    print("\n" + json.dumps(out["summary"], indent=2), flush=True)
    print("\nverdict:", out["verdict"], flush=True)
    with open("memgap3.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print("wrote memgap3.json", flush=True)


if __name__ == "__main__":
    main()
