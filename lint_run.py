"""Run the ground-rules linter over the four new S&T traps and the live 14.

The live catalogue is linted too, because a rule the deployed pool already
violates is a rule worth knowing about before four more rows are added.
"""
import json
import sys

import ground_rules as gr

NEW = "/workspace/seal_deploy/smoke_sci_traps.json"
LIVE = "/workspace/seal_deploy/web/public/catalog.json"


def show(label, traps, others_pool, check_links):
    print("=" * 78)
    print(f"{label}  (n={len(traps)}, links={'cold-fetched' if check_links else 'skipped'})")
    print("=" * 78)
    rows = []
    for t in traps:
        others = [o for o in others_pool
                  if not (o.get("field") == t.get("field")
                          and o.get("answer") == t.get("answer"))]
        v = gr.lint_trap(t, others=others, check_links=check_links)
        rows.append({"category": t.get("category"), "field": t.get("field"),
                     "answer": t.get("answer"), **{k: v[k] for k in
                     ("ok", "violations", "warnings", "submittable",
                      "sign_off_missing")},
                     "link_detail": v["link_detail"]})
        mark = "PASS" if v["ok"] else "FAIL"
        print(f"[{mark}] {str(t.get('category'))[:22]:22s} {str(t.get('answer'))[:16]:16s} "
              f"{len(v['violations'])} violations")
        for x in v["violations"]:
            print("        -", x)
        for x in v["warnings"]:
            print("        ~", x)
    return rows


def main():
    check_links = "--no-links" not in sys.argv
    new = json.load(open(NEW))
    live = (json.load(open(LIVE)) or {}).get("traps") or []
    out = {}
    out["new"] = show("NEW science and technology", new, new, check_links)
    out["live"] = show("LIVE deployed catalogue", live, live, check_links)
    with open("/workspace/seal_deploy/lint_run.json", "w") as fh:
        json.dump(out, fh, indent=1)
    n_bad_new = sum(1 for r in out["new"] if not r["ok"])
    n_bad_live = sum(1 for r in out["live"] if not r["ok"])
    print(f"\nnew: {len(new) - n_bad_new}/{len(new)} clean   "
          f"live: {len(live) - n_bad_live}/{len(live)} clean")
    return 0 if n_bad_new == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
