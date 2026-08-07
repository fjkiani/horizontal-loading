"""
cohere_validate.py — validate the Cohere vision extractor against the verified pool.

Checkpoints every result to disk immediately so an interrupted run loses nothing
and can be resumed. Run:  python cohere_validate.py
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trap_generator as tg

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cohere_validation.json")


def load():
    if os.path.exists(OUT):
        try:
            return json.load(open(OUT))
        except Exception:
            return {}
    return {}


def save(d):
    json.dump(d, open(OUT, "w"), indent=2)


def main():
    if not tg.cohere_available():
        print("COHERE_API_KEY not set; aborting")
        return 1
    pool = tg.list_generated()
    done = load()
    for t in pool:
        key = f"{t['lccn']}:{t['date']}"
        if key in done:
            print(f"{t['date']}: cached -> {done[key]['cohere']}")
            continue
        img = tg.resolve_image_path(t)
        if not os.path.exists(img):
            print(f"{t['date']}: image missing, skip")
            continue
        crop = tg._masthead_crop(img)
        t0 = time.time()
        raw = tg._cohere_read_masthead(crop, max_retries=5)
        ans, field = tg._parse_cohere_answer(raw)
        rec = {"cohere": ans, "expected": t["answer"], "raw": raw,
               "ok": ans == t["answer"], "secs": round(time.time() - t0, 1)}
        done[key] = rec
        save(done)  # checkpoint immediately
        flag = "OK" if rec["ok"] else "MISMATCH"
        print(f"{t['date']}: cohere={ans} expected={t['answer']} {flag} ({rec['secs']}s)", flush=True)
        time.sleep(2)  # be gentle on the trial key

    n = len(done)
    ok = sum(1 for v in done.values() if v["ok"])
    print(f"--- {ok}/{n} correct ---")
    bad = [(k, v) for k, v in done.items() if not v["ok"]]
    for k, v in bad:
        print(f"    MISMATCH {k}: cohere={v['cohere']} expected={v['expected']} raw={v['raw']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
