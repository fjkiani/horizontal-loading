"""Calibrate the clone gate against the threat class the corpus was missing.

content_similarity was calibrated as the midpoint of the void between two
measured populations:

    seed-variant clones (same generator, one token changed)   0.8857 - 0.9524
    different questions (different generator entirely)        0.0000 - 0.1132

That corpus contains no example of the attack the metric exists to stop: the
SAME question asked in DIFFERENT words. A hand-written rewording of the RFC
prompt measured char 0.3738 / content 0.2800 -- under both thresholds. The void
midpoint is therefore fitted on a corpus that omits the threat class, and a
threshold derived from it is not evidence of coverage.

This probe supplies the missing population and re-derives the boundary from it.

The rewordings are written adversarially: preserve the question exactly, while
pushing vocabulary as far from the original as English allows. That biases the
measured similarity DOWNWARD, which is the conservative direction for setting a
refusal threshold -- a threshold that catches these will catch a lazier
paraphrase too.

Two set metrics are compared, because Jaccard is length-sensitive: a terse
paraphrase inflates the union and depresses the score even when it reuses every
key term. The overlap coefficient |A n B| / min(|A|,|B|) removes that
dependence. Which one to ship is decided by measured separation, not taste.
"""
import itertools
import json
import re
import statistics

import source_gate as sg

CATALOG = "/workspace/seal_deploy/web/public/catalog.json"


def _content_words(text):
    return {w for w in re.findall(r"[a-z0-9.:/-]+", sg._norm_prompt(text))
            if w not in sg._SCAFFOLD_WORDS and len(w) > 2}


def jaccard(a, b):
    wa, wb = _content_words(a), _content_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def overlap(a, b):
    wa, wb = _content_words(a), _content_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def dice(a, b):
    wa, wb = _content_words(a), _content_words(b)
    if not wa or not wb:
        return 0.0
    return 2 * len(wa & wb) / (len(wa) + len(wb))


METRICS = [("jaccard", jaccard), ("overlap", overlap), ("dice", dice)]


def load_live():
    with open(CATALOG) as fh:
        cat = json.load(fh)
    return {t.get("answer"): t for t in cat["traps"] if t.get("prompt")}


# Adversarial rewordings, keyed by the answer of the trap they target.
# Each asks the SAME question of the SAME collection for the SAME key, using
# as little of the original vocabulary as the question permits.
REWORDS = {
    "6513": [
        # terse, near-zero scaffolding
        "Among every Request for Comments document whose publication month is "
        "recorded as February 2012 in the official index of that series, one "
        "single document is longer, measured in pages, than all the rest. "
        "Which RFC number belongs to it? State only the digits.",
        # verbose, synonym-heavy
        "Look at the catalogue of standards memoranda issued by the body that "
        "curates the Request for Comments collection. Restrict attention to "
        "memoranda dated to the second month of 2012. One such memorandum "
        "exceeds all its contemporaries in length. Supply its numeric "
        "designation and nothing else.",
    ],
    "CVE-2022-23221": [
        "Look through the vulnerability catalogue kept by the American "
        "standards agency for entries dated 19 January 2022. A single entry "
        "there cites a greater quantity of outside hyperlinks than any of its "
        "peers from that day. Provide its CVE designation only.",
        "On 19 January 2022 the United States federal vulnerability catalogue "
        "released a batch of security entries. One of them accumulated the "
        "largest quantity of outbound citation links in the batch. Which CVE "
        "designation identifies it? Answer with the designation alone.",
    ],
    "11.3.0": [
        "Examine the release archive that the Python packaging authority keeps "
        "for the project called pillow. Among every release possessing at "
        "least one downloadable artefact, a solitary release ships a greater "
        "quantity of artefacts than the others. Supply that release label "
        "verbatim.",
        "For the pillow project on the canonical Python software archive, work "
        "out which published release carries the largest quantity of "
        "downloadable build artefacts. Answer with the release label as the "
        "archive spells it, nothing more.",
    ],
    "200651": [
        "The European regional internet registry keeps a roster of autonomous "
        "system identifiers assigned to bodies based in Iceland, and it also "
        "measures how much address space each identifier advertises to the "
        "global routing mesh. Within that Icelandic roster a lone identifier "
        "advertises more address blocks than the rest. Supply its numeric "
        "designation.",
        "Which Icelandic autonomous system advertises the greatest quantity of "
        "address blocks to the worldwide routing mesh, according to the roster "
        "kept by the European regional internet registry? Give the bare "
        "numeric designation.",
    ],
}


def main():
    live = load_live()
    missing = [k for k in REWORDS if k not in live]
    if missing:
        print("WARNING: no live trap for %s" % missing)

    pops = {"reword": [], "clone": [], "different": []}

    # POPULATION A: same question, adversarially reworded.
    for ans, variants in REWORDS.items():
        base = live.get(ans)
        if not base:
            continue
        for i, rw in enumerate(variants):
            pops["reword"].append({
                "label": "%s~reword%d" % (ans, i),
                "a": base["prompt"], "b": rw,
            })
        # rewordings of the same question against EACH OTHER are also clones
        for x, y in itertools.combinations(variants, 2):
            pops["reword"].append({"label": "%s~rw-vs-rw" % ans, "a": x, "b": y})

    # POPULATION B: seed-variant clones (the original calibration clones).
    for ans, base in live.items():
        if base.get("category") != "science and technology":
            continue
    # use the retired arXiv baseline as the seed-variant clone population
    try:
        with open("/workspace/seal_deploy/retired_sci_baseline.json") as fh:
            ret = json.load(fh)
        seq = ret if isinstance(ret, list) else ret.get("traps", [])
        for x, y in itertools.combinations([t for t in seq if t.get("prompt")], 2):
            pops["clone"].append({
                "label": "%s/%s" % (x.get("answer"), y.get("answer")),
                "a": x["prompt"], "b": y["prompt"]})
    except FileNotFoundError:
        pass

    # POPULATION C: genuinely different questions (cross-family, live).
    for x, y in itertools.combinations(list(live.values()), 2):
        fx = (x.get("field"), tuple(sorted(x.get("source_operators") or [])))
        fy = (y.get("field"), tuple(sorted(y.get("source_operators") or [])))
        if fx == fy:
            continue
        pops["different"].append({
            "label": "%s/%s" % (x.get("answer"), y.get("answer")),
            "a": x["prompt"], "b": y["prompt"]})

    # Cross-contamination check: a rewording of question X against question Y
    # must stay in the "different" population.
    for ans, variants in REWORDS.items():
        for other_ans, other in live.items():
            if other_ans == ans:
                continue
            for rw in variants:
                pops["different"].append({
                    "label": "%s~rw/vs/%s" % (ans, other_ans),
                    "a": rw, "b": other["prompt"]})

    print("populations: " + "  ".join("%s=%d" % (k, len(v)) for k, v in pops.items()))

    out = {}
    for mname, mfn in METRICS:
        print("\n================ %s ================" % mname)
        stats = {}
        for pname in ("different", "reword", "clone"):
            vals = sorted(mfn(p["a"], p["b"]) for p in pops[pname])
            if not vals:
                continue
            stats[pname] = {"n": len(vals), "min": vals[0], "max": vals[-1],
                            "median": statistics.median(vals)}
            print("  %-10s n=%-4d min=%.4f  p50=%.4f  max=%.4f"
                  % (pname, len(vals), vals[0], statistics.median(vals), vals[-1]))
        # The gate must refuse reword AND clone, admit different.
        if "different" in stats and "reword" in stats:
            lo = stats["different"]["max"]
            hi = min(stats["reword"]["min"], stats.get("clone", {}).get("min", 9))
            gap = hi - lo
            print("  separating band [%.4f, %.4f]  width %.4f  %s"
                  % (lo, hi, gap, "SEPARATED" if gap > 0 else "*** OVERLAP ***"))
            stats["band"] = {"lo": lo, "hi": hi, "width": gap,
                             "separated": bool(gap > 0),
                             "midpoint": round((lo + hi) / 2, 4) if gap > 0 else None}
        out[mname] = stats

    # also report the character metric for reference
    print("\n================ character (prompt_similarity) ================")
    cstats = {}
    for pname in ("different", "reword", "clone"):
        vals = sorted(sg.prompt_similarity(p["a"], p["b"]) for p in pops[pname])
        if not vals:
            continue
        cstats[pname] = {"n": len(vals), "min": vals[0], "max": vals[-1],
                         "median": statistics.median(vals)}
        print("  %-10s n=%-4d min=%.4f  p50=%.4f  max=%.4f"
              % (pname, len(vals), vals[0], statistics.median(vals), vals[-1]))
    if "different" in cstats and "reword" in cstats:
        lo, hi = cstats["different"]["max"], cstats["reword"]["min"]
        print("  separating band [%.4f, %.4f]  width %.4f  %s"
              % (lo, hi, hi - lo, "SEPARATED" if hi > lo else "*** OVERLAP ***"))
    out["character"] = cstats

    # worst reword cases per metric
    print("\n=== weakest reword detections (overlap metric) ===")
    for p in sorted(pops["reword"], key=lambda p: overlap(p["a"], p["b"]))[:6]:
        print("  overlap=%.4f jaccard=%.4f char=%.4f  %s"
              % (overlap(p["a"], p["b"]), jaccard(p["a"], p["b"]),
                 sg.prompt_similarity(p["a"], p["b"]), p["label"]))

    print("\n=== strongest 'different' pairs (overlap metric) ===")
    for p in sorted(pops["different"], key=lambda p: -overlap(p["a"], p["b"]))[:6]:
        print("  overlap=%.4f jaccard=%.4f  %s"
              % (overlap(p["a"], p["b"]), jaccard(p["a"], p["b"]), p["label"]))

    with open("/workspace/seal_deploy/probe_reword_attack.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print("\nwrote probe_reword_attack.json")


if __name__ == "__main__":
    main()
