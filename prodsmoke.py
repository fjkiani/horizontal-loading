"""Production smoke against the live Render service, every category, seed-resolved.

Two jobs.

(1) Verify the four fixes landed in the deployed container: finance must refuse
    with its withdrawal reason instead of raising TypeError; every response must
    carry the 13-test evaluation; nothing may report status=done on a verdict
    other than ship.

(2) Settle an unresolved provenance mismatch. The pre-fix live service reported
    seed {"condition":"multiple sclerosis","phase":"PHASE3"} and returned
    NCT04300920 with n_base 30. Full enumeration afterwards showed
    NCT04300920 is the argmax of the IDIOPATHIC PULMONARY FIBROSIS collection
    (n=30); multiple sclerosis yields NCT01817166 at n=235. So the executed
    seed did not match the reported seed. Mechanisms ruled out by inspection:
    net cache keys are sha256(url+body) so no collision; answer and n_base are
    Candidate fields, not read from LAST_RANK, so the race does not explain it;
    next_seed() hands one object straight to the thread. The live hypothesis is
    that the request hit the previous container during Render rollover.

    This script tests it directly: it walks the health roster far enough to see
    every seed, and for each response checks the returned answer against the
    answer that seed is KNOWN to produce (measured by healthforensic.json under
    full enumeration). A mismatch that survives a clean deploy is a live bug; no
    mismatch is consistent with rollover.

Checkpoints after every single call, so an interrupt costs one HTTP request.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("SEAL_API", "https://seal-prompt-generator.onrender.com")
OUT = "/workspace/seal_deploy/prodsmoke.json"
POLL_MAX = 90          # seconds per job
POLL_EVERY = 2.0
PAUSE_BETWEEN = 6.0   # let the free-tier container reclaim between generations

# measured under full nextPageToken enumeration -- healthforensic.json
KNOWN_HEALTH = {
    "": "NCT05178810",                                   # default = ALS
    "amyotrophic lateral sclerosis": "NCT05178810",
    "multiple sclerosis": "NCT01817166",
    "idiopathic pulmonary fibrosis": "NCT04300920",
    "sickle cell disease": None,                         # ties at the extremum -> must refuse
    "Duchenne muscular dystrophy": "NCT03179631",
    "cystic fibrosis": "NCT02565914",
}

CATEGORIES = ["art", "business", "celebrities/public figures", "education",
              "finance", "geography", "health and medicine", "history",
              "legal", "politics", "science and technology", "shopping",
              "sports", "travel", "tv shows and movies", "video games"]

# enough repeats of health to see every one of its 6 roster seeds rotate past.
# Health is walked FIRST: the free-tier container has been observed to die
# mid-run, and the provenance question is the one measurement that cannot be
# reconstructed from a partial run.
PLAN = [("health and medicine", 7)] + [(c, 1) for c in CATEGORIES]


def _req(url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "SealProdSmoke/1.0"})
    with urllib.request.urlopen(r, timeout=timeout) as fh:
        return json.loads(fh.read().decode())


def one_call(category):
    rec = {"category": category, "t_start": time.time()}
    try:
        sub = _req(f"{BASE}/api/generate",
                   {"trap_class": "category", "category": category})
    except urllib.error.HTTPError as e:
        rec["transport_error"] = f"HTTP {e.code}: {e.read()[:400].decode('utf8','replace')}"
        return rec
    except Exception as e:  # noqa: BLE001
        rec["transport_error"] = f"{type(e).__name__}: {e}"
        return rec

    rec["submit"] = sub
    rec["reported_seed"] = sub.get("seed")
    rec["seed_index"] = sub.get("seed_index")
    poll = sub.get("poll")
    if not poll:
        rec["note"] = "no poll url; synchronous response"
        return rec

    t0 = time.time()
    js = {}
    while time.time() - t0 < POLL_MAX:
        time.sleep(POLL_EVERY)
        try:
            js = _req(BASE + poll, timeout=60)
        except Exception as e:  # noqa: BLE001
            rec["poll_error"] = f"{type(e).__name__}: {e}"
            break
        if js.get("status") in ("done", "refused", "error"):
            break
    rec["elapsed"] = round(time.time() - t0, 1)
    rec["status"] = js.get("status")
    rec["detail"] = str(js.get("detail") or "")[:600]
    # the reported seed can be echoed on the poll response too -- capture both
    rec["poll_seed"] = js.get("seed")
    # The done payload carries the trap under "result", NOT "trap". Reading the
    # wrong key here silently reported answer=None for every shipped trap on the
    # first run -- the same class of instrument defect as reading
    # ranking_evidence["n"] instead of ["n_base"] in the race probe. Confirmed
    # against app/main.py rather than guessed a second time.
    t = js.get("result") or js.get("trap") or {}
    ev = js.get("evaluation") or t.get("evaluation") or {}
    rec["answer"] = t.get("answer")
    rec["entity"] = str(t.get("entity") or "")[:90]
    rec["n_base"] = (t.get("ranking_evidence") or {}).get("n_base") or t.get("n_base")
    rec["verdict"] = ev.get("verdict")
    rec["witness_tier"] = ev.get("witness_tier")
    rec["n_tests"] = ev.get("n_tests")
    rec["failed_tests"] = ev.get("failed_tests")
    rec["unproven_tests"] = ev.get("unproven_tests")
    tests = ev.get("tests") or {}
    t0b = tests.get("T0b_population_complete") or {}
    rec["T0b"] = t0b.get("pass")
    rec["T0b_detail"] = str(t0b.get("detail") or "")[:160]
    rec["solver_difficulty"] = (t.get("solver_difficulty")
                                if "solver_difficulty" in t else "ABSENT")
    rec["solver_difficulty_status"] = str(t.get("solver_difficulty_status") or "")[:200]
    rec["rejected_candidate"] = js.get("rejected_candidate")

    # ---- the provenance check
    if category == "health and medicine":
        seed = rec["reported_seed"] or {}
        cond = (seed or {}).get("condition", "")
        expect = KNOWN_HEALTH.get(cond, "UNKNOWN_SEED")
        rec["expected_answer_for_reported_seed"] = expect
        if expect == "UNKNOWN_SEED":
            rec["provenance"] = "seed not in the measured roster"
        elif expect is None:
            rec["provenance"] = ("consistent" if rec["status"] == "refused"
                                 else f"MISMATCH: tied seed answered {rec['answer']}")
        elif rec["status"] == "done":
            rec["provenance"] = ("consistent" if rec["answer"] == expect
                                 else f"MISMATCH: reported seed {cond!r} should "
                                      f"give {expect}, got {rec['answer']}")
        else:
            rec["provenance"] = f"refused: {rec['detail'][:120]}"
    return rec


def main():
    out = []
    if os.path.exists(OUT):
        try:
            out = json.load(open(OUT))
        except Exception:  # noqa: BLE001
            out = []
    done_keys = {(r["category"], i) for i, r in enumerate(out)}
    idx = len(out)

    flat = []
    for cat, reps in PLAN:
        flat.extend([cat] * reps)

    for i, cat in enumerate(flat):
        if i < idx:
            continue
        print(f"--- [{i+1}/{len(flat)}] {cat} ...", flush=True)
        rec = one_call(cat)
        time.sleep(PAUSE_BETWEEN)
        out.append(rec)
        json.dump(out, open(OUT, "w"), indent=1)
        seedstr = json.dumps(rec.get("reported_seed"))[:52]
        print(f"    {str(rec.get('status')):<9} verdict={str(rec.get('verdict')):<9} "
              f"answer={str(rec.get('answer')):<14} n={str(rec.get('n_base')):<6} "
              f"T0b={rec.get('T0b')} seed={seedstr}", flush=True)
        if rec.get("provenance"):
            print(f"    provenance: {rec['provenance']}", flush=True)
        if rec.get("transport_error"):
            print(f"    TRANSPORT: {rec['transport_error'][:200]}", flush=True)

    print()
    print("== summary")
    bad = [r for r in out if str(r.get("provenance", "")).startswith("MISMATCH")]
    err = [r for r in out if r.get("transport_error")]
    shipped_not_ship = [r for r in out if r.get("status") == "done"
                        and r.get("verdict") != "ship"]
    print(f"calls={len(out)} transport_errors={len(err)} "
          f"provenance_mismatches={len(bad)} done_without_ship={len(shipped_not_ship)}")
    for r in out:
        print(f"  {r['category']:<28}{str(r.get('status')):<9}"
              f"{str(r.get('verdict')):<9}{str(r.get('answer'))[:14]:<15}"
              f"n={str(r.get('n_base')):<6}T0b={r.get('T0b')}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
