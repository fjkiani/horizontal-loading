#!/usr/bin/env python3
"""P5 -- secondary evaluation loop over the API-native trap corpus.

The primary gate (source_gate.validate_trap) answers "is this trap legal?".
This loop answers a harder question: "is the answer actually recoverable only
by doing the work, or is there a cheaper path?"  Every test is stated as a
null hypothesis with an exact test where one exists, so a PASS is a measured
statement rather than an absence of complaint.

Per-trap tests (deterministic, no network -- Loop A)
  T1 uniqueness      the extremum is not tied
  T2 guessability    the answer is not cheaply guessable from the answer-field
                     marginal distribution, and is not the modal value
  T3 order leak      the winner is not the first or last record the API returned
  T4 separation      top-1 and top-2 ranking keys are actually distinct
  T5 confirmation    >= 2 independent confirming OPERATORS (stricter than the
                     shipped gate, which requires >= 1 confirming source)
  T6 gate            source_gate.validate_trap still passes
  T7 prompt leak     the answer (or a decisive component of it) is not printed
                     in the prompt text

Corpus-level tests (Loop A2)
  C1 positional      under H0 "winner position is uniform over n_base", the
                     number of first-or-last winners is Poisson-binomial with
                     p_i = 2/n_i (1 when n_i <= 2). Exact two-sided p-value.
  C2 guess yield     under H0 "guesser samples the answer field uniformly",
                     expected number of corpus-wide hits = sum(p_i). Exact
                     Poisson-binomial again.

Loop B (cross-cohort, network) lives in cross_cohort.py.

Writes evaluation_report.json after every trap so an interrupt loses nothing.
"""
import json
import os
import re
import sys
import time

import source_gate as sg

CAND = "category_trap_candidates.json"
OUT = "evaluation_report.json"

# Thresholds. Stated once, here, so the report can print them alongside results.
MAX_UNIFORM_GUESS = 0.10     # T2: P(hit) by guessing from the answer-field marginal
MAX_MODAL_SHARE_IF_MODAL = 0.0  # T2: answer must not BE the modal value
MIN_CONFIRMING_OPERATORS = 2  # T5
WORD_MIN, WORD_MAX = 70, 150  # T7


# --------------------------------------------------------------------------
# exact Poisson-binomial
# --------------------------------------------------------------------------
def poisson_binomial_pmf(ps):
    """Exact PMF of sum of independent Bernoulli(p_i) by DP convolution."""
    dist = [1.0]
    for p in ps:
        nd = [0.0] * (len(dist) + 1)
        for k, v in enumerate(dist):
            nd[k] += v * (1.0 - p)
            nd[k + 1] += v * p
        dist = nd
    return dist


def pb_two_sided_p(ps, observed):
    """Two-sided exact p-value by the method of small probabilities:
    sum the probability of every outcome no more likely than the observed one."""
    pmf = poisson_binomial_pmf(ps)
    if observed < 0 or observed >= len(pmf):
        return 0.0
    obs_p = pmf[observed]
    tol = obs_p * (1 + 1e-9) + 1e-15
    return min(1.0, sum(v for v in pmf if v <= tol))


def pb_mean(ps):
    return sum(ps)


def _ks_uniform(xs):
    """One-sample KS statistic against Uniform(0,1)."""
    n = len(xs)
    s = sorted(xs)
    return max(max(abs((i + 1) / n - v), abs(v - i / n)) for i, v in enumerate(s))


def _ks_p(d, n):
    """Asymptotic Kolmogorov p-value. Approximate; n here is small, so this is a
    direction-of-evidence statistic, not a confirmatory test."""
    if n == 0:
        return 1.0
    lam = (n ** 0.5 + 0.12 + 0.11 / n ** 0.5) * d
    total, sign = 0.0, 1
    for k in range(1, 101):
        total += sign * 2 * (2.718281828459045 ** (-2 * k * k * lam * lam))
        sign = -sign
    return max(0.0, min(1.0, total))


# --------------------------------------------------------------------------
# per-trap tests
# --------------------------------------------------------------------------
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-\.]*")


def _words(s):
    return _WORD.findall(s or "")


def _numeric_keys(top_keys):
    out = []
    for k in top_keys:
        try:
            out.append(float(k))
        except (TypeError, ValueError):
            return None
    return out


def t1_uniqueness(ev):
    n = ev.get("n_tied_at_extremum")
    if n is None:
        return None, "no ranking evidence recorded"
    return (n == 1), f"n_tied_at_extremum={n}"


def t2_guessability(ev):
    p = ev.get("p_answer_by_uniform_guess")
    modal = ev.get("answer_field_modal_share")
    ndist = ev.get("answer_field_distinct_values")
    if p is None:
        return None, "generator did not supply valuefn; answer-field marginal unknown"
    # An answer field where every value occurs once has modal_share == 1/ndistinct
    # and every value ties for "modal". Flagging that as a degenerate answer was a
    # false positive in this test: a flat marginal is the BEST case, not the worst.
    # The defect is only real when the mode is strictly above the flat rate AND the
    # answer is that mode.
    cnt = ev.get("answer_field_count_of_answer")
    mx = ev.get("answer_field_max_count")
    nv = ev.get("answer_field_n_values")
    if cnt is not None and mx is not None and nv:
        p = cnt / nv                                  # exact, not the rounded field
        is_modal_defect = (cnt == mx and mx > 1)      # integer test, no rounding
        detail = (f"answer occurs {cnt}/{nv} times (p={p:.4g}), modal count {mx}, "
                  f"{ndist} distinct values, answer_is_strict_mode={is_modal_defect}")
    else:
        flat_rate = 1.0 / ndist if ndist else 1.0
        is_modal_defect = (modal is not None and modal > flat_rate * 1.5
                           and abs(p - modal) < 1e-9)
        detail = (f"p_uniform_guess={p} (legacy evidence, no integer counts), "
                  f"modal_share={modal}, distinct_values={ndist}, "
                  f"answer_is_strict_mode={is_modal_defect}")
    ok = (p <= MAX_UNIFORM_GUESS) and not is_modal_defect
    return ok, detail


MIN_RANKED = 5          # T0: a ranking over fewer than this does no isolating work
MAX_ABS_SPEARMAN = 0.45  # T3b: |rho| above this means the key tracks the file order
# Lowered from 0.95. At 0.95 the test was almost unfalsifiable: the 13 served
# non-education traps all sit at |rho| <= 0.2610, while the education
# domain-alphabetical key sat at +0.4685..+0.9498 across ten countries and
# still passed. A ceiling no shipped trap approaches from below, and which the
# one leaking trap cleared anyway, measures nothing. 0.45 sits above every
# clean trap measured and below every leaking one measured.


def t0_base_adequacy(ev):
    """The isolation step must actually choose between candidates. n_ranked == 1
    means the prompt's comparative wording ('one such year came before all the
    others') describes a set with no others in it."""
    n = ev.get("n_ranked", ev.get("n_base"))
    if n is None:
        return None, "no base size recorded"
    ok = n >= MIN_RANKED
    return ok, (f"n_ranked={n} (>= {MIN_RANKED}); "
                + ("isolation is vacuous, the constraint already leaves one record"
                   if n <= 1 else "isolation chooses among candidates"))


EXEMPT_REASON = (
    "EXEMPT (collection_is_explicit): the prompt enumerates every member of the "
    "base collection by identifier, so the API return order carries no "
    "information the solver does not already hold. Both order tests assume the "
    "ordering is hidden; that assumption is false here, so they are reported as "
    "not-applicable rather than passed.")


def t3b_monotone_key(ev):
    """If the ranking key is monotone in the order the API returns records, the
    extremum is an endpoint and the enumeration the prompt demands is skippable
    no matter how large the base set is."""
    if ev.get("_collection_is_explicit"):
        rho = ev.get("spearman_key_vs_api_order")
        return True, f"{EXEMPT_REASON} (measured rho={rho})"
    rho = ev.get("spearman_key_vs_api_order")
    if rho is None:
        return None, "rho undefined (n<3, tied keys, or evidence predates this test)"
    ok = abs(rho) <= MAX_ABS_SPEARMAN
    return ok, (f"spearman(key, api_index)={rho} (|rho| <= {MAX_ABS_SPEARMAN}); "
                + ("key tracks the natural order" if not ok else "key is off-axis"))


def t3_order_leak(ev):
    n = ev.get("n_base")
    pos = ev.get("winner_position_in_api_order")
    first = ev.get("winner_is_first_returned")
    last = ev.get("winner_is_last_returned")
    if ev.get("_collection_is_explicit"):
        return True, (f"{EXEMPT_REASON} (measured index {pos} of {n}, "
                      f"first={first} last={last})")
    if n is None or pos is None:
        return None, "no positional evidence recorded"
    frac = round(pos / max(1, n - 1), 4) if n > 1 else 0.0
    ok = not (first or last)
    return ok, (f"winner at index {pos} of {n} (depth {frac}), "
                f"first={first} last={last}, H0 P(first|last)={round(min(1.0, 2/n), 4)}")


def t4_separation(ev):
    tk = ev.get("top_keys") or []
    if len(tk) < 2:
        return None, f"only {len(tk)} ranked keys recorded"
    if tk[0] == tk[1]:
        return False, f"top two keys identical: {tk[0]!r}"
    nums = _numeric_keys(tk[:2])
    if nums:
        gap = abs(nums[0] - nums[1])
        rel = gap / max(1e-12, abs(nums[0])) if nums[0] else None
        return True, f"top1={tk[0]} top2={tk[1]} gap={gap:g}" + (
            f" rel={rel:.4g}" if rel is not None else "")
    return True, f"top1={tk[0]!r} top2={tk[1]!r} (non-numeric key, distinct)"


def t5_confirmation(trap):
    """Count only operators that did NOT supply the ranked collection.

    Resolving each confirming source to its controlling operator showed five
    traps confirming themselves: the Met object record "confirming" a Met
    accession number, Open Food Facts "confirming" an Open Food Facts barcode,
    and so on. A registry restating its own record adds no information, so the
    primary operator is subtracted before the count.
    """
    prim = trap.get("primary_operator")
    ind = trap.get("independent_confirming_operators")
    if ind is None:
        ind = sg.independent_witnesses(trap.get("sources"),
                                       trap.get("confirming_sources"), prim)
    ops = trap.get("confirming_operators")
    if ops is None:
        ops = sorted(sg.resolve_operators(trap.get("confirming_sources") or []))
    src_ops = trap.get("source_operators") or sorted(
        sg.resolve_operators(trap.get("sources") or []))
    ok = len(ind) >= MIN_CONFIRMING_OPERATORS
    self_conf = sorted(set(ops) - set(ind))
    return ok, (f"{len(ind)} independent witness(es): {ind}; "
                f"primary={prim!r}"
                + (f"; self-confirming and not counted: {self_conf}" if self_conf else "")
                + f"; {len(src_ops)} source operator(s): {src_ops}")


def t6_gate(trap):
    ok, viol = sg.validate_trap(trap, min_operators=3)
    return ok, ("clean" if ok else "; ".join(f"{v[0]}:{v[1]}" for v in viol)
                if viol and isinstance(viol[0], (list, tuple)) else str(viol))


def t7_prompt_leak(trap):
    prompt = trap.get("prompt") or ""
    ans = str(trap.get("answer") or "")
    low = prompt.lower()
    n = len(_words(prompt))
    notes = []
    ok = True

    if not (WORD_MIN <= n <= WORD_MAX):
        ok = False
        notes.append(f"word count {n} outside [{WORD_MIN},{WORD_MAX}]")
    else:
        notes.append(f"word count {n}")

    if ans and ans.lower() in low:
        ok = False
        notes.append(f"ANSWER {ans!r} appears verbatim in prompt")

    # component leak: a date answer whose year is printed in the prompt narrows
    # the search space even though the full string is absent.
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", ans)
    if m and re.search(r"\b" + m.group(1) + r"\b", prompt):
        ok = False
        notes.append(f"date answer leaks its year {m.group(1)} into the prompt")

    # a 4-digit year answer stated inside the prompt's own range wording is a
    # boundary leak only if it is literally present; handled by the verbatim
    # test above. Record the stated range for the report.
    yrs = sorted(set(re.findall(r"\b(1[6-9]\d{2}|20[0-2]\d)\b", prompt)))
    if yrs:
        notes.append(f"years named in prompt: {','.join(yrs)}")

    for bad in ("library of congress", "chronicling america", "internet archive",
                "hathitrust", "sports reference"):
        if bad in low:
            ok = False
            notes.append(f"banned source named in prompt: {bad}")
    return ok, "; ".join(notes)


TESTS_EV = [("T0_base_adequacy", t0_base_adequacy),
            ("T1_uniqueness", t1_uniqueness), ("T2_guessability", t2_guessability),
            ("T3_order_leak", t3_order_leak), ("T3b_monotone_key", t3b_monotone_key),
            ("T4_separation", t4_separation)]
TESTS_TRAP = [("T5_confirmation", t5_confirmation), ("T6_gate", t6_gate),
              ("T7_prompt_leak", t7_prompt_leak)]


def evaluate_one(cat, rec):
    trap = rec.get("trap") or {}
    ev = dict(trap.get("ranking_evidence") or {})
    ev["_collection_is_explicit"] = bool(trap.get("collection_is_explicit"))
    res = {"category": cat, "answer": trap.get("answer"),
           "field": trap.get("field"), "tests": {},
           "collection_is_explicit": ev["_collection_is_explicit"]}
    verdict = "ship"
    for name, fn in TESTS_EV:
        ok, why = fn(ev)
        res["tests"][name] = {"pass": ok, "detail": why}
        if ok is False:
            verdict = "hold"
        elif ok is None and verdict == "ship":
            verdict = "unproven"
    for name, fn in TESTS_TRAP:
        ok, why = fn(trap)
        res["tests"][name] = {"pass": ok, "detail": why}
        if ok is False:
            verdict = "hold"
        elif ok is None and verdict == "ship":
            verdict = "unproven"
    res["verdict"] = verdict
    # witness tier is reported separately from the verdict so a trap that is
    # sound but singly witnessed is not silently presented as equivalent to
    # one that two unrelated operators independently carry.
    prim = trap.get("primary_operator")
    ind = trap.get("independent_confirming_operators")
    if ind is None:
        ind = sg.independent_witnesses(trap.get("sources"),
                                       trap.get("confirming_sources"), prim)
    res["primary_operator"] = prim
    res["independent_witnesses"] = ind
    res["witness_tier"] = ("gold" if len(ind) >= 2 else
                           "silver" if len(ind) == 1 else "unwitnessed")
    res["evidence"] = ev
    return res


def main():
    if not os.path.exists(CAND):
        print(f"missing {CAND}", file=sys.stderr)
        return 2
    doc = json.load(open(CAND))
    results = doc["results"]

    per = []
    ps_order, ps_guess, depths, rhos = [], [], [], []
    obs_order, obs_guess = 0, 0
    exempt = []
    for cat, rec in results.items():
        if rec.get("status") != "ok":
            per.append({"category": cat, "verdict": "unavailable",
                        "error": rec.get("error"), "tests": {}})
            _save(per, None)
            continue
        r = evaluate_one(cat, rec)
        per.append(r)
        ev = r["evidence"]
        # The order-based corpus nulls (C1, C3, C4) all condition on the return
        # order being hidden from the solver. A trap that names every member of
        # its collection violates that condition, so including it would bias the
        # nulls rather than test them. Excluded and named, not silently dropped.
        if ev.get("_collection_is_explicit"):
            exempt.append(cat)
            p = ev.get("p_answer_by_uniform_guess")
            if p is not None:
                ps_guess.append(p)
                if p > MAX_UNIFORM_GUESS:
                    obs_guess += 1
            _save(per, None)
            print(f"{cat:28s} {r['verdict']:9s} " +
                  " ".join(f"{k.split('_')[0]}={'.' if v['pass'] else ('?' if v['pass'] is None else 'X')}"
                           for k, v in r["tests"].items()) + "  [order tests exempt]")
            continue
        n = ev.get("n_base")
        if n:
            ps_order.append(min(1.0, 2.0 / n))
            if ev.get("winner_is_first_returned") or ev.get("winner_is_last_returned"):
                obs_order += 1
        pos = ev.get("winner_position_in_api_order")
        if n and n > 1 and pos is not None:
            depths.append(pos / (n - 1))
        if ev.get("spearman_key_vs_api_order") is not None:
            rhos.append(ev["spearman_key_vs_api_order"])
        p = ev.get("p_answer_by_uniform_guess")
        if p is not None:
            ps_guess.append(p)
            if p > MAX_UNIFORM_GUESS:
                obs_guess += 1
        _save(per, None)
        print(f"{cat:28s} {r['verdict']:9s} " +
              " ".join(f"{k.split('_')[0]}={'.' if v['pass'] else ('?' if v['pass'] is None else 'X')}"
                       for k, v in r["tests"].items()))

    depths = [d for d in depths if d is not None]
    rhos = [r for r in rhos if r is not None]
    corpus = {
        "order_test_exemptions": {
            "rule": "collection_is_explicit traps are excluded from C1/C3/C4",
            "reason": EXEMPT_REASON,
            "categories": sorted(exempt),
            "n_excluded": len(exempt),
        },
        "C3_depth_uniformity": {
            "h0": "winner relative depth pos/(n-1) is Uniform(0,1) across the corpus",
            "n": len(depths),
            "depths": [round(x, 4) for x in sorted(depths)],
            "mean_depth": round(sum(depths) / len(depths), 4) if depths else None,
            "ks_statistic": round(_ks_uniform(depths), 4) if depths else None,
            "ks_p_approx": round(_ks_p(_ks_uniform(depths), len(depths)), 6) if depths else None,
        },
        "C4_key_monotonicity": {
            "h0": "ranking keys are unrelated to API return order (rho ~ 0)",
            "n": len(rhos),
            "abs_rho": sorted(round(abs(r), 4) for r in rhos),
            "mean_abs_rho": round(sum(abs(r) for r in rhos) / len(rhos), 4) if rhos else None,
            "n_over_threshold": sum(1 for r in rhos if abs(r) > MAX_ABS_SPEARMAN),
        },
        "C1_positional_leak": {
            "h0": "winner index is uniform over the n_base records the API returned",
            "n_traps_tested": len(ps_order),
            "per_trap_p_first_or_last": [round(x, 4) for x in ps_order],
            "expected_first_or_last": round(pb_mean(ps_order), 4) if ps_order else None,
            "observed_first_or_last": obs_order,
            "exact_two_sided_p": round(pb_two_sided_p(ps_order, obs_order), 6) if ps_order else None,
        },
        "C2_guess_yield": {
            "h0": "a guesser draws uniformly from the observed answer-field values",
            "n_traps_tested": len(ps_guess),
            "expected_hits_across_corpus": round(pb_mean(ps_guess), 4) if ps_guess else None,
            "p_at_least_one_hit": round(1 - poisson_binomial_pmf(ps_guess)[0], 6) if ps_guess else None,
            "n_traps_over_threshold": obs_guess,
            "threshold": MAX_UNIFORM_GUESS,
        },
    }
    _save(per, corpus)

    tally = {}
    for r in per:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("\nverdicts:", tally)
    print("C1 positional leak: observed", obs_order, "expected",
          corpus["C1_positional_leak"]["expected_first_or_last"],
          "exact p =", corpus["C1_positional_leak"]["exact_two_sided_p"])
    print("C2 guess yield: expected hits",
          corpus["C2_guess_yield"]["expected_hits_across_corpus"],
          "P(>=1 hit) =", corpus["C2_guess_yield"]["p_at_least_one_hit"])
    return 0


def _save(per, corpus):
    doc = {"evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "thresholds": {"max_uniform_guess": MAX_UNIFORM_GUESS,
                          "min_confirming_operators": MIN_CONFIRMING_OPERATORS,
                          "word_range": [WORD_MIN, WORD_MAX]},
           "per_trap": per, "corpus": corpus,
           "counts": {v: sum(1 for r in per if r["verdict"] == v)
                      for v in {x["verdict"] for x in per}}}
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, OUT)


if __name__ == "__main__":
    sys.exit(main())
