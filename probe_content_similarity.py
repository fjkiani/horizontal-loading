"""Validate source_gate.content_similarity against the measured corpus.

The character-level metric (prompt_similarity) has a house-grammar floor: every
generated prompt shares the same scaffolding sentences, so unrelated prompts
score 0.34-0.54.  content_similarity is supposed to strip that scaffolding and
measure only whether two prompts ask the SAME QUESTION.

Ship criterion: the clone population and the non-clone population must be
separated by a real gap, and that gap must be wider than the one the character
metric gives.  If content_similarity does not separate them, do not ship it.
"""
import json
import itertools
import statistics
import sys

import source_gate as sg

CATALOG = "/workspace/seal_deploy/web/public/catalog.json"
RETIRED = "/workspace/seal_deploy/retired_sci_baseline.json"


def _load_traps():
    rows = []
    with open(CATALOG) as fh:
        cat = json.load(fh)
    # catalog.json shape: {"generated_at":..., "traps":[...], "categories":[...], "depth":{...}}
    if isinstance(cat, dict) and isinstance(cat.get("traps"), list):
        for t in cat["traps"]:
            rows.append(("live", t.get("category"), t))
    elif isinstance(cat, list):
        for t in cat:
            rows.append(("live", t.get("category"), t))
    try:
        with open(RETIRED) as fh:
            ret = json.load(fh)
        seq = ret if isinstance(ret, list) else ret.get("traps", [])
        for t in seq:
            rows.append(("retired", t.get("category"), t))
    except FileNotFoundError:
        pass
    return [r for r in rows if (r[2] or {}).get("prompt")]


def _label(kind, cat, trap):
    return "%s|%s|%s" % (kind, cat, trap.get("answer"))


def _family_of(trap):
    """Two traps are the SAME QUESTION iff same field and same operator set."""
    ops = tuple(sorted(trap.get("source_operators") or []))
    return (trap.get("field"), ops)


def main():
    rows = _load_traps()
    print("traps with prompts: %d" % len(rows))

    pairs = []
    for (ka, ca, ta), (kb, cb, tb) in itertools.combinations(rows, 2):
        char_ab = sg.prompt_similarity(ta["prompt"], tb["prompt"])
        char_ba = sg.prompt_similarity(tb["prompt"], ta["prompt"])
        cont_ab = sg.content_similarity(ta["prompt"], tb["prompt"])
        cont_ba = sg.content_similarity(tb["prompt"], ta["prompt"])
        is_clone = _family_of(ta) == _family_of(tb)
        pairs.append({
            "a": _label(ka, ca, ta),
            "b": _label(kb, cb, tb),
            "clone": bool(is_clone),
            "char": char_ab,
            "char_gap": abs(char_ab - char_ba),
            "content": cont_ab,
            "content_gap": abs(cont_ab - cont_ba),
        })

    print("pairs: %d" % len(pairs))

    clones = [p for p in pairs if p["clone"]]
    others = [p for p in pairs if not p["clone"]]
    print("clone pairs: %d   non-clone pairs: %d" % (len(clones), len(others)))

    def band(name, seq, key):
        if not seq:
            print("  %-10s (empty)" % name)
            return None, None
        v = sorted(p[key] for p in seq)
        print("  %-10s min=%.4f  p50=%.4f  max=%.4f" % (
            name, v[0], statistics.median(v), v[-1]))
        return v[0], v[-1]

    out = {"n_pairs": len(pairs), "n_clone": len(clones), "n_nonclone": len(others)}

    for key in ("char", "content"):
        print("\n=== %s ===" % key)
        nmin, nmax = band("non-clone", others, key)
        cmin, cmax = band("clone", clones, key)
        asym = max(p[key + "_gap"] for p in pairs) if pairs else 0.0
        print("  max asymmetry gap: %.6f" % asym)
        if nmax is not None and cmin is not None:
            gap = cmin - nmax
            print("  separation gap: %.4f  (non-clone max %.4f -> clone min %.4f)"
                  % (gap, nmax, cmin))
            out[key] = {
                "nonclone_min": nmin, "nonclone_max": nmax,
                "clone_min": cmin, "clone_max": cmax,
                "separation_gap": gap, "max_asymmetry": asym,
                "separated": bool(gap > 0),
            }

    # Science and technology heads specifically: the user's ask was
    # "entirely distinct prompts".  Report the four heads pairwise.
    print("\n=== science and technology heads (live) ===")
    sci = [r for r in rows if r[1] == "science and technology" and r[0] == "live"]
    for (ka, ca, ta), (kb, cb, tb) in itertools.combinations(sci, 2):
        print("  %-14s vs %-14s  char=%.4f  content=%.4f" % (
            ta.get("answer"), tb.get("answer"),
            sg.prompt_similarity(ta["prompt"], tb["prompt"]),
            sg.content_similarity(ta["prompt"], tb["prompt"])))
    if len(sci) > 1:
        out["sci_max_char"] = max(
            sg.prompt_similarity(a[2]["prompt"], b[2]["prompt"])
            for a, b in itertools.combinations(sci, 2))
        out["sci_max_content"] = max(
            sg.content_similarity(a[2]["prompt"], b[2]["prompt"])
            for a, b in itertools.combinations(sci, 2))
        print("  MAX char=%.4f  MAX content=%.4f"
              % (out["sci_max_char"], out["sci_max_content"]))

    # Worst offenders on each metric, for eyeballing.
    print("\n=== top 8 non-clone pairs by content ===")
    for p in sorted(others, key=lambda p: -p["content"])[:8]:
        print("  %.4f (char %.4f)  %s   VS   %s"
              % (p["content"], p["char"], p["a"], p["b"]))

    print("\n=== bottom 5 clone pairs by content ===")
    for p in sorted(clones, key=lambda p: p["content"])[:5]:
        print("  %.4f (char %.4f)  %s   VS   %s"
              % (p["content"], p["char"], p["a"], p["b"]))

    with open("/workspace/seal_deploy/probe_content_similarity.json", "w") as fh:
        json.dump({"summary": out, "pairs": pairs}, fh, indent=2)
    print("\nwrote probe_content_similarity.json")

    # Ship gate
    ok = out.get("content", {}).get("separated") and \
        out["content"]["separation_gap"] > out["char"]["separation_gap"]
    print("\nSHIP content_similarity: %s" % ("YES" if ok else "NO"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
