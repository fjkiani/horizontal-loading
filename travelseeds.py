"""travelseeds.py -- is the travel seed grid actually producing distinct traps?

Production smoke of the deployed GeoNames build returned, across the six-seed
travel roster:

    idx 0  {}                     -> 6296543  Ivalo Airport
    idx 1  LH / FRA               -> 6301511  Helsinki Vantaa Airport
    idx 2  SK / CPH               -> refused  (P238 'TRD' maps to 2 Wikidata items)
    idx 3  OS / VIE               -> 3156088  Oslo Lufthavn
    idx 4  LO / WAW               -> 6300971  Pulkovo Airport
    idx 5  TP / LIS               -> 6301511  Helsinki Vantaa Airport

Seeds 1 and 5 are different airlines with different hubs and they returned the
SAME answer. Four distinct answers from five shipped traps. Every winner is a
far-northern airport.

Two competing explanations, and they call for different fixes:

  H1  COLLECTION OVERLAP. The seeds are not really building different
      collections -- the destination sets are largely the same, so of course the
      argmax repeats. Fix: pick seeds whose networks are disjoint.

  H2  DEGENERATE KEY. The collections genuinely differ, but ranking European
      destinations by LATITUDE has a small extremal set: nearly every European
      network's northernmost point is one of a handful of Nordic airports. Fix:
      the key, not the seeds.

Jaccard similarity of the destination sets separates them. Low Jaccard with a
shared argmax is H2. High Jaccard is H1.

Also reports, per seed, how much of the collection sits within one degree of
latitude of the winner -- the margin in the units the key actually ranks on. A
thin margin means the answer is unstable to a single route change upstream.

Runs the real generator locally against the warm cache. Checkpoints per seed.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import category_traps as ct  # noqa: E402
import gen_v2, gen_v3, gen_v4  # noqa: E402,F401
import seed_roster as sr  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "travelseeds.json")

CAPTURE = {}
_REAL = ct._pick_extreme


def _spy(rows, keyfn, label, mode="max", valuefn=None):
    out = _REAL(rows, keyfn, label, mode=mode, valuefn=valuefn)
    try:
        CAPTURE["rows"] = [(valuefn(r) if valuefn else None, float(keyfn(r)))
                           for r in rows]
    except Exception:  # noqa: BLE001
        CAPTURE["rows"] = None
    CAPTURE["label"] = label
    CAPTURE["mode"] = mode
    return out


for mod in (ct, gen_v2, gen_v3, gen_v4):
    if hasattr(mod, "_pick_extreme"):
        mod._pick_extreme = _spy


def jaccard(a, b):
    a, b = set(a), set(b)
    if not a and not b:
        return None
    return round(len(a & b) / len(a | b), 4)


def main():
    seeds = sr.seeds_for("travel")
    res = {"started": time.time(), "n_seeds": len(seeds), "seeds": []}
    for i, sd in enumerate(seeds):
        CAPTURE.clear()
        rec = {"idx": i, "seed": sd}
        try:
            cand = ct.GENERATORS["travel"](**sd)
            rec["status"] = "ok"
            rec["answer"] = cand.answer
            rec["entity"] = cand.entity
            rec["n_base"] = cand.n_base
        except Exception as e:  # noqa: BLE001
            rec["status"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        rows = CAPTURE.get("rows")
        if rows:
            vals = sorted((v for _, v in rows), reverse=True)
            rec["n_ranked"] = len(rows)
            rec["dests"] = sorted({k for k, _ in rows if k})
            rec["top_lat"] = [round(v, 4) for v in vals[:5]]
            if len(vals) >= 2:
                rec["margin_deg"] = round(vals[0] - vals[1], 4)
                rec["n_within_1deg"] = sum(1 for v in vals if vals[0] - v <= 1.0)
                rec["n_within_1deg_frac"] = round(
                    rec["n_within_1deg"] / len(vals), 4)
        res["seeds"].append(rec)
        print("[%d] %-34s %-9s %-24s n_ranked=%s margin=%s" % (
            i, json.dumps(sd)[:34], str(rec.get("answer")),
            str(rec.get("entity"))[:24], rec.get("n_ranked"),
            rec.get("margin_deg")), flush=True)
        json.dump(res, open(OUT, "w"), indent=1)

    ok = [r for r in res["seeds"] if r.get("status") == "ok" and r.get("dests")]
    pairs = []
    for i in range(len(ok)):
        for j in range(i + 1, len(ok)):
            pairs.append({
                "a": ok[i]["idx"], "b": ok[j]["idx"],
                "seed_a": ok[i]["seed"], "seed_b": ok[j]["seed"],
                "jaccard_destinations": jaccard(ok[i]["dests"], ok[j]["dests"]),
                "same_answer": ok[i]["answer"] == ok[j]["answer"],
                "answer_a": ok[i]["answer"], "answer_b": ok[j]["answer"],
            })
    res["pairs"] = pairs

    answers = [r["answer"] for r in res["seeds"] if r.get("status") == "ok"]
    dup_pairs = [p for p in pairs if p["same_answer"]]
    dis_pairs = [p for p in pairs if not p["same_answer"]]
    res["summary"] = {
        "n_shipped_locally": len(answers),
        "n_distinct_answers": len(set(answers)),
        "distinct_answer_rate": (round(len(set(answers)) / len(answers), 4)
                                 if answers else None),
        "mean_jaccard_all_pairs": (
            round(sum(p["jaccard_destinations"] for p in pairs) / len(pairs), 4)
            if pairs else None),
        "mean_jaccard_same_answer_pairs": (
            round(sum(p["jaccard_destinations"] for p in dup_pairs) / len(dup_pairs), 4)
            if dup_pairs else None),
        "mean_jaccard_diff_answer_pairs": (
            round(sum(p["jaccard_destinations"] for p in dis_pairs) / len(dis_pairs), 4)
            if dis_pairs else None),
        "verdict": None,
    }
    mj = res["summary"]["mean_jaccard_same_answer_pairs"]
    if mj is not None:
        res["summary"]["verdict"] = (
            "H1 collection overlap" if mj >= 0.5 else "H2 degenerate key")
    res["finished"] = time.time()
    json.dump(res, open(OUT, "w"), indent=1)
    print()
    print(json.dumps({"summary": res["summary"], "pairs": pairs}, indent=2))


if __name__ == "__main__":
    main()
