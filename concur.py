"""concur.py -- is there still a concurrency OOM, and does RSS come back down?

Two questions the memaudit table could not answer, because it measured one
generator per process.

Q1 SERIALIZATION. app/main.py runs the generator inside `with ct.generation():`
   and category_traps.generation() holds GENERATION_LOCK (an RLock). If that
   holds, two overlapping HTTP requests can NEVER both be inside a generator
   body, so the peaks do not add and the "two concurrent geography requests
   OOM" claim is false for the deployed path. Measured here by timestamping
   lock entry and exit in four threads and counting the maximum simultaneous
   occupancy.

Q2 HIGH-WATER RETENTION. Serialization only helps if the memory is RELEASED.
   Python may hold freed blocks in its allocator, and glibc may not trim the
   arena, so RSS is a high-water mark. If a 351 MB geography build leaves RSS
   parked at 351 MB, the container idles a few hundred MB from the 512 MB cap
   and the next request is the one that dies. Measured by alternating builds
   and reading RSS after each, with an explicit malloc_trim attempt.
"""
import ctypes, gc, json, os, resource, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import category_traps as ct
import gen_v2, gen_v3, gen_v4  # noqa: F401

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "concur.json")
LIMIT_MB = 512.0


def rss_mb():
    """Current resident set, MB. /proc is the live value; ru_maxrss is the peak."""
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1024.0 ** 2
    except Exception:  # noqa: BLE001
        return float("nan")


def peak_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def trim():
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001
        pass
    return rss_mb()


def q1_serialization(cat="geography", n_threads=4):
    """Do generator bodies overlap under the deployed lock discipline?"""
    events, lk = [], threading.Lock()

    def one(i):
        t_req = time.time()
        with ct.generation():
            t_in = time.time()
            try:
                ct.GENERATORS[cat]()
                err = None
            except Exception as e:  # noqa: BLE001
                err = "%s" % type(e).__name__
            t_out = time.time()
        with lk:
            events.append({"thread": i, "queued_s": round(t_in - t_req, 3),
                           "t_in": t_in, "t_out": t_out,
                           "held_s": round(t_out - t_in, 2), "err": err})

    ths = [threading.Thread(target=one, args=(i,)) for i in range(n_threads)]
    t0 = time.time()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    wall = time.time() - t0

    marks = []
    for e in events:
        marks.append((e["t_in"], +1))
        marks.append((e["t_out"], -1))
    marks.sort()
    cur = mx = 0
    for _, d in marks:
        cur += d
        mx = max(mx, cur)
    held = sorted(e["held_s"] for e in events)
    return {"category": cat, "n_threads": n_threads,
            "max_simultaneous_in_generator": mx,
            "serialized": mx == 1,
            "wall_s": round(wall, 2),
            "sum_held_s": round(sum(held), 2),
            "max_queue_wait_s": round(max(e["queued_s"] for e in events), 2),
            "peak_rss_mb": round(peak_mb(), 1),
            "rss_after_mb": round(rss_mb(), 1),
            "threads": sorted(events, key=lambda e: e["thread"])}


def q2_retention(seq=("geography", "travel", "geography", "travel", "health and medicine")):
    steps = []
    base = trim()
    steps.append({"step": "baseline", "rss_mb": round(base, 1),
                  "peak_mb": round(peak_mb(), 1)})
    for cat in seq:
        t0 = time.time()
        try:
            with ct.generation():
                ct.GENERATORS[cat]()
            err = None
        except Exception as e:  # noqa: BLE001
            err = "%s" % type(e).__name__
        r_hot = rss_mb()
        r_cold = trim()
        steps.append({"step": cat, "err": err,
                      "rss_after_build_mb": round(r_hot, 1),
                      "rss_after_trim_mb": round(r_cold, 1),
                      "peak_mb": round(peak_mb(), 1),
                      "elapsed_s": round(time.time() - t0, 1)})
    return {"baseline_mb": round(base, 1), "steps": steps,
            "final_rss_mb": round(rss_mb(), 1),
            "process_peak_mb": round(peak_mb(), 1),
            "headroom_at_final_mb": round(LIMIT_MB - rss_mb(), 1)}


if __name__ == "__main__":
    res = {"started": time.time(), "limit_mb": LIMIT_MB}

    print("=== Q2 retention (sequential, the deployed path) ===", flush=True)
    res["retention"] = q2_retention()
    for s in res["retention"]["steps"]:
        print("  %-24s build=%-8s trim=%-8s peak=%-8s" % (
            s["step"], s.get("rss_after_build_mb", s.get("rss_mb")),
            s.get("rss_after_trim_mb", s.get("rss_mb")), s["peak_mb"]), flush=True)
    json.dump(res, open(OUT, "w"), indent=1)

    print("\n=== Q1 serialization (4 threads on geography) ===", flush=True)
    res["serialization"] = q1_serialization()
    s = res["serialization"]
    print("  max simultaneous in generator: %d  (serialized=%s)"
          % (s["max_simultaneous_in_generator"], s["serialized"]), flush=True)
    print("  wall %.2fs vs sum of held %.2fs; max queue wait %.2fs"
          % (s["wall_s"], s["sum_held_s"], s["max_queue_wait_s"]), flush=True)
    print("  process peak %.1f MB, headroom %.1f MB"
          % (s["peak_rss_mb"], LIMIT_MB - s["peak_rss_mb"]), flush=True)

    res["finished"] = time.time()
    json.dump(res, open(OUT, "w"), indent=1)
    print("\nwrote", OUT)
