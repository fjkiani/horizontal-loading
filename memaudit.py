"""Does the inline 13-test battery push a generation over Render's free-tier
512 MB, and if so which category?

The deployed container restarted mid-request with no traceback -- the SIGKILL
signature of an OOM kill rather than an application exception. That is a
hypothesis, not a finding, so it gets measured: peak RSS for each live category
through the exact API entry point, with the battery on and off, so the marginal
cost of the fix is separated from the baseline cost of generation.

Runs each category in its own subprocess. Sharing an interpreter would let one
category's retained allocations be charged to the next, which is exactly the
confusion this is meant to resolve.
"""
import json
import os
import subprocess
import sys

D = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(D, "memaudit.json")
LIMIT_MB = 512  # Render free tier

LIVE = ["business", "education", "geography", "health and medicine", "legal",
        "politics", "science and technology", "shopping", "sports", "travel",
        "tv shows and movies"]

CHILD = r'''
import json, os, resource, sys, tracemalloc
sys.path.insert(0, %(D)r)
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")
cat = sys.argv[1]
with_battery = sys.argv[2] == "1"

def rss_mb():
    # ru_maxrss is KiB on Linux
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

base_import = rss_mb()
import category_traps as ct, gen_v2, gen_v3, gen_v4          # noqa: F401
import source_gate as sg
import evaluate_traps as et
after_import = rss_mb()

rec = {"category": cat, "with_battery": with_battery,
       "rss_after_interpreter_mb": round(base_import, 1),
       "rss_after_imports_mb": round(after_import, 1)}

tracemalloc.start()
try:
    fn = ct.GENERATORS[cat]
    with ct.generation():
        cand = fn()
        trap = cand.to_trap()
    rec["generated"] = True
    rec["n_base"] = (trap.get("ranking_evidence") or {}).get("n_base")
    rec["rss_after_generate_mb"] = round(rss_mb(), 1)
    if with_battery:
        sg.validate_trap(trap, min_operators=3)
        out = et.evaluate_one(cat, {"trap": trap})
        rec["verdict"] = out["verdict"]
    rec["rss_after_gate_mb"] = round(rss_mb(), 1)
except Exception as e:                                        # noqa: BLE001
    rec["generated"] = False
    rec["error"] = "%%s: %%s" %% (type(e).__name__, str(e)[:180])
    rec["rss_after_generate_mb"] = round(rss_mb(), 1)
    rec["rss_after_gate_mb"] = round(rss_mb(), 1)
cur, peak = tracemalloc.get_traced_memory()
rec["python_heap_peak_mb"] = round(peak / 1e6, 1)
rec["peak_rss_mb"] = round(rss_mb(), 1)
print("RESULT " + json.dumps(rec))
''' % {"D": D}


def run(cat, with_battery):
    r = subprocess.run([sys.executable, "-c", CHILD, cat,
                        "1" if with_battery else "0"],
                       capture_output=True, text=True, cwd=D, timeout=600)
    for line in r.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    return {"category": cat, "with_battery": with_battery,
            "crashed": True, "returncode": r.returncode,
            "stderr": r.stderr[-500:]}


def main():
    out = []
    if os.path.exists(OUT):
        try:
            out = json.load(open(OUT))
        except Exception:  # noqa: BLE001
            out = []
    seen = {(r["category"], r.get("with_battery")) for r in out}

    for cat in LIVE:
        for wb in (False, True):
            if (cat, wb) in seen:
                continue
            print(f"--- {cat} battery={wb} ...", flush=True)
            rec = run(cat, wb)
            out.append(rec)
            json.dump(out, open(OUT, "w"), indent=1)
            print(f"    peak_rss={rec.get('peak_rss_mb')} MB  "
                  f"heap_peak={rec.get('python_heap_peak_mb')} MB  "
                  f"n={rec.get('n_base')}  {rec.get('verdict') or rec.get('error','')}"[:140],
                  flush=True)

    print()
    hdr = (f"{'category':<24}{'imports':>9}{'peak(no bat)':>14}"
           f"{'peak(bat)':>11}{'delta':>8}{'headroom':>10}")
    print(hdr); print("-" * len(hdr))
    worst = None
    for cat in LIVE:
        a = next((r for r in out if r["category"] == cat and not r.get("with_battery")), {})
        b = next((r for r in out if r["category"] == cat and r.get("with_battery")), {})
        pa, pb = a.get("peak_rss_mb"), b.get("peak_rss_mb")
        if pa is None or pb is None:
            print(f"{cat:<24}{'crashed':>9}")
            continue
        delta = round(pb - pa, 1)
        head = round(LIMIT_MB - pb, 1)
        print(f"{cat:<24}{a.get('rss_after_imports_mb', 0):>9}{pa:>14}"
              f"{pb:>11}{delta:>8}{head:>10}")
        if worst is None or pb > worst[1]:
            worst = (cat, pb)
    print(f"\nRender free tier limit: {LIMIT_MB} MB")
    if worst:
        print(f"worst category: {worst[0]} at {worst[1]} MB peak RSS "
              f"({LIMIT_MB - worst[1]:.1f} MB headroom for the web server, "
              f"the interpreter's other threads and any concurrent request)")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
