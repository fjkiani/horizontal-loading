"""
benchmark_readers.py — old extractor vs resolution-aware extractor, scored against
the 9 agent-vision-verified ground truths in the pool, PLUS the known-hard case.

Old  = pct:15 image, blanket top-16% crop, 3 upscales of that one raster.
New  = pct:40 + pct:60 rasters, VOL./NO. band crop, cross-resolution agreement.

Checkpoints per case so an interrupted run resumes.
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import masthead_reader as mr
import trap_generator as tg

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reader_benchmark.json")


def cases():
    """Ground truth = the pool's agent-vision-confirmed answers."""
    out = []
    for t in tg.list_generated():
        out.append({"lccn": t["lccn"], "date": t["date"], "truth": t["answer"],
                    "url": t["resource_url"], "img": tg.resolve_image_path(t)})
    return out


def old_reader(img_path):
    if not os.path.exists(img_path):
        return {"answer": None, "confidence": "no_image"}
    crop = tg._masthead_crop(img_path)
    reads = [tg._ocr_at_scale(crop, s) for s in (2, 3, 4)]
    a, f, c = tg._extract_with_confidence(*reads)
    return {"answer": a, "field": f, "confidence": c}


def main():
    done = json.load(open(OUT)) if os.path.exists(OUT) else {}
    cs = cases()
    print(f"{len(cs)} ground-truth cases\n")
    for c in cs:
        k = f"{c['lccn']}:{c['date']}"
        if k in done:
            print(f"{k} cached")
            continue
        rec = {"truth": c["truth"]}
        o = old_reader(c["img"])
        rec["old"] = o
        rec["old_correct"] = tg._norm_digits(o.get("answer") or "") == tg._norm_digits(c["truth"])
        # old pipeline only ACCEPTS when confidence == high
        rec["old_accepted"] = o.get("confidence") == "high"
        rec["old_accepted_wrong"] = rec["old_accepted"] and not rec["old_correct"]

        t0 = time.time()
        n = mr.read_masthead(c["url"], cache_tag=f"{c['lccn']}_{c['date']}")
        rec["new"] = {kk: n[kk] for kk in ("answer", "field", "confidence", "agree")}
        rec["new_detail"] = n.get("per_resolution")
        rec["new_correct"] = tg._norm_digits(n.get("answer") or "") == tg._norm_digits(c["truth"])
        rec["new_accepted"] = n.get("confidence") == "high"
        rec["new_accepted_wrong"] = rec["new_accepted"] and not rec["new_correct"]
        rec["secs"] = round(time.time() - t0, 1)

        done[k] = rec
        json.dump(done, open(OUT, "w"), indent=2)
        print(f"{k} truth={c['truth']:>6} | OLD {str(o.get('answer')):>6} "
              f"({o.get('confidence')}) | NEW {str(n.get('answer')):>6} "
              f"({n.get('confidence')}) [{rec['secs']}s]", flush=True)

    n_tot = len(done)
    print(f"\n{'='*74}\nSCORED ON {n_tot} AGENT-VERIFIED GROUND TRUTHS\n{'='*74}")
    for label in ("old", "new"):
        corr = sum(1 for v in done.values() if v[f"{label}_correct"])
        acc = sum(1 for v in done.values() if v[f"{label}_accepted"])
        accw = sum(1 for v in done.values() if v[f"{label}_accepted_wrong"])
        acc_corr = sum(1 for v in done.values()
                       if v[f"{label}_accepted"] and v[f"{label}_correct"])
        prec = (acc_corr / acc * 100) if acc else 0.0
        print(f"{label.upper():4s}  correct {corr}/{n_tot}  "
              f"accepted(high-conf) {acc}  of which WRONG {accw}  "
              f"precision-when-accepted {prec:.0f}%")
    print("\nFALSE-CONFIDENCE CASES (accepted a wrong answer):")
    any_fc = False
    for k, v in done.items():
        for label in ("old", "new"):
            if v[f"{label}_accepted_wrong"]:
                any_fc = True
                print(f"  {label.upper()} {k}: said {v[label]['answer']} truth {v['truth']}")
    if not any_fc:
        print("  none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
