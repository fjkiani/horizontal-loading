#!/usr/bin/env python3
"""Choose one shipping trap per science-and-technology family, and say why.

Selection rule: among the seeds that pass every gate, take the one with the
LARGEST base set. This is not a taste judgement. The sweep established that all
four families carry a positional prior -- the ranking key is not independent of
the order the API returns records -- and the best a position-only guesser can
do is (lift x 1/n). The lift is a property of the family and cannot be filtered
away, but 1/n is a property of the seed, so maximising n is the only lever that
lowers the guess floor without conditioning on the answer's position (which
merely relocates the leak).

Ties break toward the seed whose winner sits furthest from the population's
most-likely position, then toward the larger relative margin.

Writes select_sci.json and prints the slate.
"""
import glob
import itertools
import json
import os

os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import source_gate as sg  # noqa: E402

LOCAL = "/workspace/sweep"
OUT = "select_sci.json"

# Measured search-leak verdicts. The in-sandbox probe in prescreen.py cannot run
# (lite.duckduckgo.com serves 202 interstitials, mojeek 403, searx 0 outlinks),
# so each candidate head was probed with an external web search using ONLY the
# prompt's distinguishing wording and never the answer string. A seed marked
# "leak" is refused outright regardless of how large its base set is: a lower
# guess floor is worthless if the answer is the first search result.
SEARCH_LEAK = {
    "sci_standard|2025|March": {
        "verdict": "leak",
        "query": "RFC Editor index RFCs published March 2025 longest document page count",
        "detail": ("first-page results named RFC 9777 (MLDv2 for IPv6, STD 101, "
                   "56 pages) directly, plus RFC 9776 as runner-up; the argmax is "
                   "an INTERNET STANDARD, a status measured 6.65x enriched among "
                   "month-argmaxes and therefore widely announced"),
    },
    "sci_standard|2012|February": {
        "verdict": "clean",
        "query": "RFC Editor index RFCs published February 2012 longest document page count",
        "detail": ("results returned only generic RFC Editor index pages, an RFC "
                   "Editor annual production review and unrelated RFCs; RFC 6513 "
                   "was never named and no page-count ranking surfaced"),
    },
    "sci_asn|IS|Iceland": {
        "verdict": "clean",
        "query": "RIPE country resource list Iceland autonomous system most announced prefixes",
        "detail": ("results surfaced the incumbent carriers AS6677 Mila, AS12969 "
                   "Ljosleidarinn, AS1850 and AS44735; AS200651 FlokiNET never "
                   "appeared, so the searchable candidates are all wrong"),
    },
    "sci_supplychain|pillow": {
        "verdict": "clean",
        "query": "Python Package Index pillow project which release has the most distribution files uploaded",
        "detail": ("results pointed at the current release 12.3.0 (87 files) and "
                   "at 11.2.0 (79 wheels); 11.3.0 was never identified as the "
                   "maximum, so the searchable answer is wrong"),
    },
    "sci_vulnerability|2022-01-19": {
        "verdict": "clean",
        "query": "NVD CVE records published 2022-01-19 which record has the most external reference links",
        "detail": ("results returned CVEs published 2022-01-11 and 2022-01-17 and "
                   "no ranking for 2022-01-19; CVE-2022-23221 never appeared"),
    },
}


def _leak_key(fid, seed):
    if isinstance(seed, (list, tuple)):
        return "|".join([fid] + [str(s) for s in seed])
    return f"{fid}|{seed}"


def _leak(fid, seed):
    return SEARCH_LEAK.get(_leak_key(fid, seed))


def _rows():
    fams = {}
    # Exact filenames only. A stray backup such as sweep_sci_standard.pre_repick.json
    # sorts after the live file and silently overwrote it in the family dict,
    # which re-selected a superseded head. Enumerate the known families instead.
    import sci_families as _sf
    for fid in _sf.FAMILY_IDS:
        path = os.path.join(LOCAL, f"sweep_{fid}.json")
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            d = json.load(fh)
        fams[d["family"]] = d
    return fams


def _floor(rec, lift):
    n = rec.get("n_base")
    if not n or lift is None:
        return None
    return round(lift / n, 4)


# Lift measured over a family's FULL candidate population rather than the handful
# of seeds the sweep touched. Only sci_standard has one: probe_rfc_monotone.py
# enumerated all 193 candidate months (190 with a unique argmax) and found the
# lowest-numbered RFC of the month is the answer 23/190 = 0.1211 of the time
# against 0.0558 expected, i.e. 2.17x. Where a population estimate exists it
# supersedes the sweep estimate, which is built on 9-24 seeds.
POPULATION_LIFT = {
    "sci_standard": {
        "lift": 2.17,
        "n_measured": 190,
        "source": "probe_rfc_monotone.py over all 193 candidate months",
    },
}

# Minimum expected number of first-position hits under the uniform null before a
# point estimate of lift means anything. Below this the sweep can only bound the
# lift from above, never confirm it is zero.
MIN_EXPECTED_HITS = 3.0
# Poisson one-sided 95% bound: P(0 hits | lambda) = exp(-lambda) = 0.05.
_POISSON_95 = 2.9957


def _lift_estimate(hits, expected):
    """Report a lift only when the sweep could have detected one.

    Observing zero first-position hits against an expectation of 0.16 is not
    evidence that the family has no positional leak; it is evidence that the
    sweep was too small to see one. In that regime the honest statistic is an
    upper bound, not a point estimate of zero.
    """
    if expected <= 0:
        return None, None, "no seed carried both a base size and a winner position"
    if expected >= MIN_EXPECTED_HITS:
        return round(hits / expected, 3), None, None
    upper = round(((hits + _POISSON_95) / expected), 3) if hits == 0 else None
    if hits == 0:
        return (None, upper,
                f"underpowered: {expected:.2f} hits expected under the uniform "
                f"null, so 0 observed bounds the lift at <= {upper} (Poisson "
                f"one-sided 95%) rather than establishing 0")
    return (round(hits / expected, 3), None,
            f"weak: only {expected:.2f} hits expected under the uniform null")


def main():
    fams = _rows()
    # Positional lift per family, measured over that family's own swept
    # population: how much better than 1/n a single-position guess does.
    lift = {}
    for fid, d in fams.items():
        obs = [r for r in d["results"] if r.get("trap")]
        hits = 0
        exp = 0.0
        for r in obs:
            n = r.get("n_base")
            p = ((r["trap"].get("ranking_evidence") or {})
                 .get("winner_position_in_api_order"))
            if not n or p is None:
                continue
            exp += 1.0 / n
            if p == 0:
                hits += 1
        point, upper, caveat = _lift_estimate(hits, exp)
        entry = {
            "n_measured": len(obs),
            "first_position_hits": hits,
            "expected_hits_if_uniform": round(exp, 3),
            "lift": point,
            "lift_upper_bound_95": upper,
            "lift_source": "sweep sample",
            "caveat": caveat,
        }
        pop = POPULATION_LIFT.get(fid)
        if pop:
            entry["sweep_sample_lift"] = point
            entry["lift"] = pop["lift"]
            entry["lift_upper_bound_95"] = None
            entry["lift_source"] = pop["source"]
            entry["population_n_measured"] = pop["n_measured"]
            entry["caveat"] = (
                "population estimate supersedes the sweep sample" +
                (f"; sweep sample was {caveat}" if caveat else "")
            )
        lift[fid] = entry

    slate = {}
    for fid, d in fams.items():
        ships = [r for r in d["results"] if r.get("ships")]
        rejected_for_leak = [
            {"seed": r["seed"], "answer": r["answer"], "n_base": r.get("n_base"),
             **_leak(fid, r["seed"])}
            for r in ships
            if (_leak(fid, r["seed"]) or {}).get("verdict") == "leak"
        ]
        ships = [r for r in ships
                 if (_leak(fid, r["seed"]) or {}).get("verdict") != "leak"]
        if not ships:
            continue
        ships.sort(key=lambda r: (-(r.get("n_base") or 0),
                                  -((r.get("prescreen") or {})
                                    .get("margin_relative") or 0)))
        win = ships[0]
        ps = win.get("prescreen") or {}
        # Use the bound when no point estimate is defensible, so the reported
        # floor is conservative rather than flatteringly zero.
        lf = lift[fid]["lift"]
        lf_bound = lift[fid]["lift_upper_bound_95"]
        slate[fid] = {
            "answer": win["answer"],
            "entity": (win.get("entity") or "")[:90],
            "seed": win["seed"],
            "n_base": win.get("n_base"),
            "n_candidates_that_qualified": len(ships),
            "n_seeds_attempted": len(d["results"]),
            "yield": round(len(ships) / max(1, len(d["results"])), 3),
            "primary_operator": win["trap"]["primary_operator"],
            "source_operators": win["trap"]["source_operators"],
            "witness_tier": win.get("witness_tier"),
            "spearman_key_vs_api_order": ps.get("spearman_key_vs_api_order"),
            "winner_position_in_api_order": ((win["trap"].get("ranking_evidence")
                                              or {})
                                             .get("winner_position_in_api_order")),
            "n_tied_at_extremum": ps.get("n_tied_at_extremum"),
            "margin_absolute": ps.get("margin_absolute"),
            "margin_relative": ps.get("margin_relative"),
            "p_uniform_guess": ps.get("p_uniform_guess"),
            "family_positional_lift": lf,
            "family_positional_lift_upper_bound_95": lf_bound,
            "family_positional_lift_source": lift[fid]["lift_source"],
            "family_positional_lift_caveat": lift[fid]["caveat"],
            "position_only_guess_floor": _floor(win, lf),
            "position_only_guess_floor_upper_bound": _floor(
                win, lf if lf is not None else lf_bound),
            "uniform_guess_floor": (round(1.0 / win["n_base"], 4)
                                    if win.get("n_base") else None),
            "prompt": win["trap"]["prompt"],
            "sources": win["trap"]["sources"],
            "landing_pages": (win["trap"].get("facts") or {}).get("landing_pages"),
            "lint_warnings": win.get("lint_warnings"),
            "prescreen_caveats": ps.get("caveats"),
            "search_leak": _leak(fid, win["seed"]) or {
                "verdict": "not measured",
                "detail": "no external search probe was run for this seed",
            },
            "seeds_rejected_for_search_leak": rejected_for_leak,
        }

    heads = {k: v for k, v in slate.items()}
    traps = []
    for fid, d in fams.items():
        if fid in slate:
            traps.append([r for r in d["results"]
                          if r.get("ships")
                          and r["answer"] == slate[fid]["answer"]][0]["trap"])
    sims = {}
    for a, b in itertools.combinations(sorted(heads), 2):
        ta = [t for t in traps if t["answer"] == heads[a]["answer"]][0]
        tb = [t for t in traps if t["answer"] == heads[b]["answer"]][0]
        sims[f"{a}|{b}"] = round(sg.prompt_similarity(ta["prompt"],
                                                      tb["prompt"]), 4)
    viol_total = []
    for i, t in enumerate(traps):
        v, _w = sg.disjointness_violations(t, traps[:i] + traps[i + 1:],
                                           hard=True)
        viol_total.extend(v)

    out = {
        "slate": slate,
        "family_positional_lift": lift,
        "pairwise_prompt_similarity": sims,
        "max_pairwise_similarity": max(sims.values()) if sims else None,
        "clone_threshold": sg.CLONE_SIMILARITY_THRESHOLD,
        "effective_depth": sg.effective_depth(traps),
        "n_traps": len(traps),
        "disjointness_violations": viol_total,
        "traps": traps,
    }
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)

    hdr = ("family", "answer", "n", "qual", "1/n", "lift", "floor", "rho",
           "pos", "leak")
    print(f"{hdr[0]:18s} {hdr[1]:16s} {hdr[2]:>5s} {hdr[3]:>5s} {hdr[4]:>7s} "
          f"{hdr[5]:>8s} {hdr[6]:>8s} {hdr[7]:>8s} {hdr[8]:>5s} {hdr[9]:>6s}")
    for fid in sorted(slate):
        r = slate[fid]
        lf = r["family_positional_lift"]
        lb = r["family_positional_lift_upper_bound_95"]
        lift_s = f"{lf}" if lf is not None else (f"<={lb}" if lb else "n/a")
        fl = r["position_only_guess_floor_upper_bound"]
        floor_s = (f"{fl}" if r["position_only_guess_floor"] is not None
                   else (f"<={fl}" if fl is not None else "n/a"))
        print(f"{fid:18s} {str(r['answer']):16s} {str(r['n_base']):>5s} "
              f"{r['n_candidates_that_qualified']:>5d} "
              f"{str(r['uniform_guess_floor']):>7s} "
              f"{lift_s:>8s} {floor_s:>8s} "
              f"{str(r['spearman_key_vs_api_order']):>8s} "
              f"{str(r['winner_position_in_api_order']):>5s} "
              f"{r['search_leak']['verdict']:>6s}")
    print(f"\nmax pairwise prompt similarity = {out['max_pairwise_similarity']} "
          f"(threshold {sg.CLONE_SIMILARITY_THRESHOLD})")
    print(f"effective_depth = {out['effective_depth']} of {out['n_traps']} traps")
    print(f"disjointness violations = {len(viol_total)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
