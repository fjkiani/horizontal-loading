#!/usr/bin/env python3
"""apifix_audit.py -- offline audit of the deployed /api/generate path.

Two defects were observed against the live service after commit 316ac4a:

  D1  POST {"trap_class":"category","category":"finance"} returned
      TypeError: gen_finance() got an unexpected keyword argument 'year'
      instead of the clean TrapUnavailable refusal that art returns. The
      withdrawal raise sits AFTER a signature the roster seed cannot bind.

  D2  The served health trap was NCT04300920 (n_base 30) from seed
      {"condition":"multiple sclerosis"}, not the depth/witness-validated
      NCT05178810 (n_base 51). The API gate is sg.validate_trap() ONLY --
      source independence. It never runs the T0..T7 battery, so the deployed
      service ships answers the build-time gate would have held.

This script measures the full extent of both, offline (signature inspection
only, no network), so the fix is scoped by evidence rather than by the one
category that happened to be smoke-tested.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import category_traps as ct
import gen_v2  # noqa: F401
import gen_v3  # noqa: F401
import gen_v4  # noqa: F401
import seed_roster
import evaluate_traps as et

OUT = os.path.join(_HERE, "apifix_audit.json")


def _first_stmt_raises_unavailable(fn):
    """True iff the generator's FIRST executable statement is a TrapUnavailable
    raise -- i.e. the category is withdrawn unconditionally and the arguments
    are irrelevant by construction."""
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return None
    src = inspect.cleandoc(src) if src.startswith("def") else src
    try:
        mod = ast.parse(inspect.getsource(fn).lstrip())
    except SyntaxError:
        # nested/indented def -- reparse after dedenting
        import textwrap
        mod = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    fndef = mod.body[0]
    body = [s for s in fndef.body
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if not body:
        return False
    s0 = body[0]
    if not isinstance(s0, ast.Raise) or s0.exc is None:
        return False
    exc = s0.exc
    name = ""
    if isinstance(exc, ast.Call):
        f = exc.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
    return name == "TrapUnavailable"


def main():
    report = {"bind_failures": [], "withdrawn": [], "live": [],
              "api_gate_tests": [], "build_gate_tests": []}

    for cat in sorted(ct.GENERATORS):
        fn = ct.GENERATORS[cat]
        withdrawn = _first_stmt_raises_unavailable(fn)
        (report["withdrawn"] if withdrawn else report["live"]).append(cat)
        sig = inspect.signature(fn)
        accepts_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD
                             for p in sig.parameters.values())
        for i, seed in enumerate(seed_roster.seeds_for(cat)):
            unknown = sorted(set(seed) - set(sig.parameters))
            if unknown and not accepts_var_kw:
                report["bind_failures"].append({
                    "category": cat, "seed_index": i, "seed": seed,
                    "unknown_params": unknown,
                    "accepted": sorted(sig.parameters),
                    "generator": "%s.%s" % (fn.__module__, fn.__name__),
                    "withdrawn": withdrawn,
                    "consequence": ("error (TypeError) instead of refused"
                                    if withdrawn else "error: seed drifted"),
                })

    # D2: what the API gate covers vs what the build gate covers.
    report["api_gate_tests"] = ["source_gate.validate_trap (operators only)"]
    report["build_gate_tests"] = [n for n, _ in et.TESTS_EV] + \
                                 [n for n, _ in et.TESTS_TRAP]
    report["gate_gap"] = report["build_gate_tests"]
    report["n_bind_failures"] = len(report["bind_failures"])
    report["n_seeds_total"] = sum(len(seed_roster.seeds_for(c))
                                  for c in ct.GENERATORS)

    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=2)

    print("withdrawn generators (%d): %s" % (len(report["withdrawn"]),
                                             report["withdrawn"]))
    print("live generators      (%d): %s" % (len(report["live"]),
                                             report["live"]))
    print("\nD1 seed-binding failures: %d of %d roster seeds"
          % (report["n_bind_failures"], report["n_seeds_total"]))
    for b in report["bind_failures"]:
        print("  %-28s seed[%d] %-40s unknown=%s  withdrawn=%s -> %s"
              % (b["category"], b["seed_index"], json.dumps(b["seed"])[:40],
                 b["unknown_params"], b["withdrawn"], b["consequence"]))

    print("\nD2 gate gap")
    print("  API  runs: %s" % report["api_gate_tests"])
    print("  build runs: %s" % report["build_gate_tests"])
    print("  tests the deployed service NEVER applies: %d"
          % len(report["build_gate_tests"]))
    print("\nwrote %s" % OUT)


if __name__ == "__main__":
    sys.exit(main() or 0)
