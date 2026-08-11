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
    "business", "politics",
    "geography", "shopping", "tv shows and movies", "video games",
}

# gen_v3 replaced the ANSWER FIELD for these two: the Nobel population and the
# ranking are unchanged, but the value read off the winning record is now the
# laureate's GND identifier instead of a birth city or an award year. Both of
# those were measured recallable (Leiden 145,636 Wikipedia views/yr, 1975
# 77,453), which made the traversal decorative. Pinned separately so a caller
# that loads gen_v2 but forgets gen_v3 fails here rather than silently shipping
# the memorable answers again.
# gen_v3 re-points four categories off memorable ANSWER FIELDS onto opaque
# authority identifiers. education and sports were added in the same pass as
# history and celebrities and must be pinned here too, or a regression that
# silently hands them back to gen_v2 would reintroduce 'wit.ie' and 'Newton'
# without failing a single test.
OWNED_BY_GEN_V3 = {"history", "celebrities/public figures", "education", "sports"}

# gen_v4 rescues finance. gen_v2 still registers a finance generator, so gen_v4
# only wins if it is imported LAST -- import order is load-bearing here in a way
# it is not for gen_v2/gen_v3, which own disjoint keys.
#
# finance was moved OUT of OWNED_BY_GEN_V2 when gen_v4 took it. Leaving it
# unpinned would be worse than leaving it in the wrong set: a caller that drops
# `import gen_v4` would silently fall back to gen_v2's Treasury-BALANCE
# generator, which the source gate refuses, and no test would fail. Pinning the
# key to gen_v4 makes the import-order requirement enforceable.
OWNED_BY_GEN_V4 = {"finance"}

OVERRIDE_OWNER = dict(
    [(k, "gen_v2") for k in OWNED_BY_GEN_V2] +
    [(k, "gen_v3") for k in OWNED_BY_GEN_V3] +
    [(k, "gen_v4") for k in OWNED_BY_GEN_V4]
)


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
    wrong = sorted((cat, want, mods.get(cat))
                   for cat, want in OVERRIDE_OWNER.items()
                   if mods.get(cat) != want)
    assert not wrong, (
        f"{caller} resolves the wrong generator module for {wrong} "
        "(category, expected, actual). It is exercising generators that are not "
        f"the live ones. Add `import gen_v2` and `import gen_v3` to {caller}.py."
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
