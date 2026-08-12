#!/usr/bin/env python3
"""Sweep one science-and-technology family over its seed roster.

One family per process. Every seed is generated, gated, evaluated, linted and
pre-screened, and the state file is rewritten after EVERY seed, so an interrupt
costs at most the seed in flight. The checkpoint is written to local disk first
and mirrored to the shared volume, because the shared volume is S3-backed and
does not support the rename-in-place that an atomic local write uses.

Disjointness is measured against three things at once:
  * the traps this process has already accepted,
  * the traps the OTHER families have checkpointed into the shared volume,
  * the deployed catalogue.
Without the second of those, four processes could each mint a clean family and
still collide with one another.

  SWEEP_FAMILY   family id (required)
  SWEEP_MAX      stop after this many SHIPPING traps (default 6)
  SWEEP_SEEDS    stop after this many seed attempts (default 40)
  SWEEP_LINKS    1 to cold-fetch every cited URL (default 1)
  SWEEP_SEARCH   1 to run the search-leak probe (default 0; throttled here)
"""
import json
import os
import shutil
import sys
import time
import traceback

os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import category_traps as ct  # noqa: E402
import gen_v2, gen_v3, gen_v4  # noqa: F401,E402
import evaluate_traps as et  # noqa: E402
import evidence as evd  # noqa: E402
import ground_rules as gr  # noqa: E402
import prescreen as ps  # noqa: E402
import sci_families as sf  # noqa: E402
import source_gate as sg  # noqa: E402
from category_traps import TrapUnavailable  # noqa: E402

CATEGORY = "science and technology"
SHARED = "/mnt/shared-workspace/shared"
LOCAL = "/workspace/sweep"
CATALOG = "/workspace/seal_deploy/web/public/catalog.json"

FAMILY = os.environ.get("SWEEP_FAMILY", "")
MAX_SHIP = int(os.environ.get("SWEEP_MAX", "6"))
MAX_SEEDS = int(os.environ.get("SWEEP_SEEDS", "40"))
DO_LINKS = os.environ.get("SWEEP_LINKS", "1") == "1"
DO_SEARCH = os.environ.get("SWEEP_SEARCH", "0") == "1"

# Each family's seed roster and the keyword that carries it. The rosters live in
# sci_families so the API sees the same seeds the sweep does.
ROSTERS = {
    "sci_vulnerability": ("days", list(sf._NVD_DAYS)),
    "sci_standard": ("months", None),          # filled from the RFC index
    "sci_supplychain": ("packages", list(sf._PYPI_PKGS)),
    "sci_asn": ("countries", list(sf._RIPE_COUNTRIES)),
}


def _rfc_month_seeds(limit=60):
    """Recent months first, sized so a unique page-count argmax is plausible."""
    idx = sf._rfc_months()
    keys = [k for k, v in idx.items()
            if int(k[0]) >= sf._RFC_MIN_YEAR and 8 <= len(v) <= 60]
    keys.sort(key=lambda k: (-int(k[0]),
                             ["January", "February", "March", "April", "May",
                              "June", "July", "August", "September", "October",
                              "November", "December"].index(k[1])
                             if k[1] in ["January", "February", "March", "April",
                                         "May", "June", "July", "August",
                                         "September", "October", "November",
                                         "December"] else 0))
    return keys[:limit]


def _load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return default


def _catalog_traps():
    doc = _load_json(CATALOG, {}) or {}
    return doc.get("traps") or []


def _read_family_state(fid):
    """Prefer the local checkpoint; fall back to the shared mirror.

    All four processes run on one machine, so local disk is a POSIX exchange
    with atomic rename. The shared mirror exists for durability across machine
    lifecycles, not for inter-process messaging: it is S3-backed, and a
    truncating overwrite there while a sibling has the same object open for
    read returns EPERM. Reading local first removes that race from the hot path.
    """
    st = _load_json(os.path.join(LOCAL, f"sweep_{fid}.json"), None)
    if st is None:
        st = _load_json(os.path.join(SHARED, f"sweep_{fid}.json"), {}) or {}
    return st


def _sibling_traps():
    """Everything a candidate must be disjoint from, EXCLUDING its own family.

    Within a hard-disjoint category two seeds of the same family always collide:
    they share every operator, every domain, and their prompts differ only in the
    seed token. That is a property of the family, not a defect in the seed. So a
    seed is judged as if it were the family's sole representative, and the
    same-family collision is measured separately as `sibling_similarity`. The
    practical consequence, recorded here rather than assumed: the S&T pool can
    hold at most ONE live trap per family, and the other qualified seeds are
    rotation stock that only becomes servable after the live one burns.
    """
    out = []
    for fid in sf.FAMILY_IDS:
        if fid == FAMILY:
            continue
        st = _read_family_state(fid)
        for r in (st.get("results") or []):
            if isinstance(r.get("trap"), dict):
                out.append(r["trap"])
    out.extend(_catalog_traps())
    return out


def _checkpoint(state, mirror=True):
    """Local write is authoritative and atomic; the shared mirror is advisory.

    Mirroring must never kill a sweep: the first run lost 25 seeds of work
    because a concurrent sibling had the S3 object open for read when this
    process truncated it, and the resulting EPERM propagated out of the loop.
    """
    os.makedirs(LOCAL, exist_ok=True)
    local = os.path.join(LOCAL, f"sweep_{FAMILY}.json")
    tmp = local + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, local)                       # atomic on local disk
    if not mirror:
        return
    dst = os.path.join(SHARED, f"sweep_{FAMILY}.json")
    for attempt in range(3):
        try:
            os.makedirs(SHARED, exist_ok=True)
            shutil.copy(local, dst)
            state.pop("mirror_error", None)
            return
        except OSError as exc:
            state["mirror_error"] = f"{type(exc).__name__}: {exc}"
            try:
                os.remove(dst)
            except OSError:
                pass
            time.sleep(0.5 * (attempt + 1))


def main():
    if FAMILY not in ROSTERS:
        raise SystemExit(f"SWEEP_FAMILY must be one of {sorted(ROSTERS)}")
    fam = ct.families_for(CATEGORY, servable_only=True).get(FAMILY)
    if fam is None:
        raise SystemExit(f"{FAMILY} is not a servable family")

    kw, roster = ROSTERS[FAMILY]
    if roster is None:
        roster = _rfc_month_seeds()
    # SWEEP_SEEDS_JSON overrides the roster with an explicit seed list. The
    # default rosters are recency-ordered and truncated, so a seed selected from
    # the full population (as sci_standard's replacement was, after RFC 9777 /
    # March 2025 leaked to web search) is not reachable otherwise.
    override = os.environ.get("SWEEP_SEEDS_JSON", "").strip()
    if override:
        roster = [tuple(s) if isinstance(s, list) else s
                  for s in json.loads(override)]

    state = _load_json(os.path.join(LOCAL, f"sweep_{FAMILY}.json"),
                       {"family": FAMILY, "results": []})
    done = {json.dumps(r["seed"], sort_keys=True, default=str)
            for r in state["results"]}
    shipped = [r for r in state["results"] if r.get("ships")]

    for seed in roster:
        if len(shipped) >= MAX_SHIP or len(state["results"]) >= MAX_SEEDS:
            break
        sr = json.dumps(seed, sort_keys=True, default=str)
        if sr in done:
            continue
        rec = {"seed": seed, "seed_repr": sr, "family": FAMILY, "ships": False}
        t0 = time.time()
        try:
            cand = fam.fn(**{kw: (seed,)})
            trap = cand.to_trap()
            rec["trap"] = trap
            rec["answer"] = str(trap.get("answer"))
            rec["entity"] = trap.get("entity")
            rec["n_base"] = trap.get("n_base")

            ok_gate, viol = sg.validate_trap(trap, min_operators=3)
            rec["gate_ok"] = ok_gate
            rec["gate_violations"] = viol
            rec["echo_violations"] = sg.echo_violations(
                trap.get("sources"), trap.get("confirming_sources"),
                trap.get("primary_operator"))
            rec["witness_grade"] = sg.grade_witnesses(
                trap.get("sources"), trap.get("confirming_sources"),
                trap.get("primary_operator"))

            others = _sibling_traps()
            ev = et.evaluate_one(CATEGORY, {"status": "ok", "trap": trap},
                                 others=others)
            rec["verdict"] = ev.get("verdict")
            rec["witness_tier"] = ev.get("witness_tier")
            rec["failing_tests"] = sorted(
                k for k, v in (ev.get("tests") or {}).items()
                if v.get("pass") is False)
            rec["unproven_tests"] = sorted(
                k for k, v in (ev.get("tests") or {}).items()
                if v.get("pass") is None)
            rec["test_detail"] = {k: v.get("detail")
                                  for k, v in (ev.get("tests") or {}).items()}

            lint = gr.lint_trap(trap, others=others, check_links=DO_LINKS)
            rec["lint_ok"] = lint["ok"]
            rec["lint_violations"] = lint["violations"]
            rec["lint_warnings"] = lint["warnings"]
            rec["link_detail"] = lint["link_detail"]

            rec["prescreen"] = ps.prescreen(trap, do_search=DO_SEARCH)
            rec["worksheet"] = evd.blank_worksheet(trap)
            rec["ships"] = bool(ok_gate and rec["verdict"] == "ship"
                                and lint["ok"] and not rec["echo_violations"])
            # Quantify, rather than assume, that same-family seeds are clones.
            if shipped:
                head = shipped[0]["trap"]
                rec["sibling_similarity"] = round(
                    sg.prompt_similarity(head.get("prompt", ""),
                                         trap.get("prompt", "")), 4)
                rec["sibling_of"] = head.get("answer")
        except TrapUnavailable as tu:
            rec["refused"] = str(tu)
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {exc}"
            rec["traceback"] = traceback.format_exc()[-1200:]
        rec["secs"] = round(time.time() - t0, 1)
        state["results"].append(rec)
        done.add(sr)
        if rec["ships"]:
            shipped.append(rec)
        # Local checkpoint every seed; mirror to the shared volume every fifth,
        # so an interrupt costs one seed locally and at most five off-machine.
        _checkpoint(state, mirror=(len(state["results"]) % 5 == 0))
        flag = ("SHIP" if rec["ships"] else
                "refused" if rec.get("refused") else
                "ERROR" if rec.get("error") else "hold")
        print(f"[{flag:7s}] {FAMILY} {sr[:34]:34s} "
              f"ans={rec.get('answer')!r} n={rec.get('n_base')} "
              f"fail={rec.get('failing_tests')} lint={rec.get('lint_violations')} "
              f"{rec['secs']}s", flush=True)

    state["n_shipped"] = len(shipped)
    state["n_attempted"] = len(state["results"])
    state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _checkpoint(state)
    print(f"\n{FAMILY}: {len(shipped)} shipping of {len(state['results'])} "
          f"seeds attempted", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
