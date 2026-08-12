"""Does the STRUCTURAL family key catch what text similarity cannot?

probe_reword_attack.py established a negative result: no lexical metric tested
separates "same question, reworded" from "different question". Measured bands,
different-max vs reword-min:

    jaccard    0.0732 vs 0.0732   width  0.0000
    overlap    0.1429 vs 0.1364   width -0.0065
    dice       0.1364 vs 0.1364   width  0.0000
    character  0.5404 vs 0.2156   width -0.3248

The character metric is not merely weak, it is inverted: two GENUINELY DIFFERENT
generated prompts (0.5404) are more character-similar than a prompt and its own
adversarial rewording (0.2156). It measures house style, not question identity.

The claim under test here is that the load is carried by something wording
independent: effective_depth groups traps by (field, frozenset(source_operators)).
Two prompts that ask the same question of the same collection MUST cite the same
collection and MUST answer in the same field, whatever words they use. If that
holds, a rewording cannot inflate reported depth no matter how well it evades
the text gate.
"""
import json

import source_gate as sg
from probe_reword_attack import REWORDS, load_live


def main():
    live = load_live()
    results = []

    print("=== can a rewording inflate effective_depth? ===\n")
    for ans, variants in REWORDS.items():
        base = live.get(ans)
        if not base:
            continue

        # A submitter reworded the prompt but is still asking the same question
        # of the same collection, so field and sources are unchanged. Only the
        # answer label differs (a different seed of the same question).
        twins = [base]
        for i, rw in enumerate(variants):
            t = dict(base)
            t["prompt"] = rw
            t["answer"] = "%s-rw%d" % (ans, i)
            twins.append(t)

        depth = sg.effective_depth(twins)
        text_gate_fires = []
        for t in twins[1:]:
            viol, warn = sg.disjointness_violations(t, [base], hard=True)
            text_gate_fires.append(
                any("similarity" in v for v in viol))

        ok = depth == 1
        print("  %-16s rows=%d  effective_depth=%d  %s" % (
            ans, len(twins), depth, "OK (collapses)" if ok else "*** INFLATED ***"))
        print("      text gate fired on rewordings: %s" % text_gate_fires)
        print("      operator-overlap violation fired: %s" % (
            any("shares operator" in v
                for v in sg.disjointness_violations(twins[1], [base], hard=True)[0])))
        results.append({"answer": ans, "rows": len(twins), "depth": depth,
                        "collapses": ok, "text_gate": text_gate_fires})

    # And the converse: four genuinely different questions must NOT collapse.
    sci = [t for t in live.values()
           if t.get("category") == "science and technology"]
    d = sg.effective_depth(sci)
    print("\n  four real S&T heads: rows=%d effective_depth=%d  %s"
          % (len(sci), d, "OK (stays distinct)" if d == len(sci) else "*** COLLAPSED ***"))

    all_ok = all(r["collapses"] for r in results) and d == len(sci)
    print("\nSTRUCTURAL GATE HOLDS: %s" % ("YES" if all_ok else "NO"))
    print("TEXT GATE CAUGHT ANY REWORDING: %s" % (
        "YES" if any(any(r["text_gate"]) for r in results) else "NO"))

    with open("/workspace/seal_deploy/probe_structural_gate.json", "w") as fh:
        json.dump({"per_question": results, "sci_depth": d,
                   "sci_rows": len(sci), "structural_holds": all_ok}, fh, indent=2)
    print("\nwrote probe_structural_gate.json")


if __name__ == "__main__":
    main()
