"""Pin the import-graph bug that silently invalidated every cross-cohort run.

cross_cohort.py imported category_traps and source_gate but not gen_v2. Because
gen_v2 installs its generators with ct.GENERATORS.update(_OVERRIDES) at import
time, Loop B was validating the PRE-OVERRIDE base generators: six categories
raised "unexpected keyword argument" and finance returned the month-end-leaking
date answer that gen_v2 had already replaced with the balance.

Every assertion here runs in a FRESH SUBPROCESS. That is the entire point. The
original hand-check passed because it ran in a process where gen_v2 had already
been imported for other reasons, so ct.GENERATORS was already patched and every
signature bound. A same-process test would reproduce that false pass exactly.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Modules that dispatch through ct.GENERATORS must resolve the OVERRIDDEN ones.
# Listed explicitly so a new caller added without gen_v2 fails here rather than
# in a report that silently describes code nobody ships.
GENERATOR_CALLERS = ["cross_cohort", "run_category_traps"]

# gen_v2 owns these. If any silently falls back to category_traps, the caller is
# exercising a generator that is not the live one.
OWNED_BY_GEN_V2 = {
    "finance", "business", "politics", "history", "celebrities/public figures",
    "geography", "shopping", "tv shows and movies", "video games",
}


def _run(code):
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, f"subprocess failed:\n{p.stdout}\n{p.stderr[-2000:]}"
    return p.stdout.strip().splitlines()[-1]


@pytest.mark.parametrize("caller", GENERATOR_CALLERS)
def test_caller_sees_overridden_generators(caller):
    """Importing the caller alone must be enough to install gen_v2."""
    mods = json.loads(_run(
        f"import {caller}\n"
        "import category_traps as ct, json\n"
        "print(json.dumps({k: v.__module__ for k, v in ct.GENERATORS.items()}))"
    ))
    overridden = {k for k, v in mods.items() if v == "gen_v2"}
    missing = sorted(OWNED_BY_GEN_V2 - overridden)
    assert not missing, (
        f"{caller} does not install gen_v2 for {missing}; it is exercising the "
        f"base generators. Add `import gen_v2` to {caller}.py."
    )


@pytest.mark.parametrize("caller", GENERATOR_CALLERS)
def test_caller_kwargs_bind_inside_its_own_import_graph(caller):
    """The six TypeErrors in loopB_2 were all signature mismatches.

    Binding is checked in a subprocess importing ONLY the caller, so the
    signatures under test are the ones that caller will actually invoke.
    """
    d = json.loads(_run(
        f"import {caller} as C\n"
        "import category_traps as ct, inspect, json\n"
        "seeds = getattr(C, 'ALT', None) or getattr(C, 'SEEDS', None) or {}\n"
        "bad = {}\n"
        "for cat, kw in seeds.items():\n"
        "    fn = ct.GENERATORS.get(cat)\n"
        "    if fn is None:\n"
        "        bad[cat] = 'no generator registered'\n"
        "        continue\n"
        "    try:\n"
        "        inspect.signature(fn).bind(**(kw or {}))\n"
        "    except TypeError as e:\n"
        "        bad[cat] = str(e)\n"
        "print(json.dumps({'n_seeds': len(seeds), 'bad': bad}))"
    ))
    if not d["n_seeds"]:
        pytest.skip(f"{caller} exposes no seed dict to bind-check")
    assert not d["bad"], f"{caller} seed kwargs do not bind: {d['bad']}"


def test_gen_v2_actually_overrides_and_is_not_a_noop():
    """Guard the inverse failure: gen_v2 imported but overriding nothing."""
    changed = json.loads(_run(
        "import category_traps as ct\n"
        "base = {k: v.__module__ for k, v in ct.GENERATORS.items()}\n"
        "import gen_v2\n"
        "after = {k: v.__module__ for k, v in ct.GENERATORS.items()}\n"
        "import json\n"
        "print(json.dumps(sorted(k for k in after if base.get(k) != after[k])))"
    ))
    assert len(changed) >= 8, (
        f"gen_v2 only replaced {changed}; if _OVERRIDES stops being applied, "
        "callers silently fall back to the base generators."
    )
