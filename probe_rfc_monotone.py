#!/usr/bin/env python3
"""Is the RFC page-count key systematically anti-correlated with RFC number?

The sweep refused four of nine months on T3b (|spearman| > 0.45) and eight of
nine measured rho values came out NEGATIVE. If that sign is a property of the
RFC series rather than of the individual months, then the family leaks even in
the months T3b lets through: a solver that always names the LOWEST-numbered RFC
of the month would beat chance without enumerating anything.

This probe measures the whole candidate population from the cached index, so
the claim is settled by counting rather than by argument.
"""
import json
import os
import statistics as st
from collections import Counter

os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")
import sci_families as sf  # noqa: E402


def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return None if dx == 0 or dy == 0 else num / (dx * dy)


def main():
    idx = sf._rfc_months()
    rows = []
    for (yr, mon), ents in idx.items():
        if int(yr) < sf._RFC_MIN_YEAR or not (8 <= len(ents) <= 60):
            continue
        # The API hands records back in doc-id order, which is the natural
        # order a solver sees. Rank position 0 = lowest RFC number that month.
        ents = sorted(ents, key=lambda e: int(e["num"]))
        pages = [e["pages"] for e in ents]
        n = len(ents)
        top = max(pages)
        winners = [i for i, p in enumerate(pages) if p == top]
        rho = spearman(list(range(n)), pages)
        rows.append({
            "year": yr, "month": mon, "n": n,
            "rho": None if rho is None else round(rho, 4),
            "unique": len(winners) == 1,
            "argmax_pos": winners[0] if len(winners) == 1 else None,
            "argmax_quantile": (winners[0] / (n - 1)) if len(winners) == 1
                               and n > 1 else None,
            "t3b_pass": None if rho is None else abs(rho) <= 0.45,
        })

    uniq = [r for r in rows if r["unique"]]
    rhos = [r["rho"] for r in rows if r["rho"] is not None]
    neg = sum(1 for x in rhos if x < 0)

    # Sign test against rho symmetric about zero.
    from math import comb
    n_r = len(rhos)
    k = max(neg, n_r - neg)
    p_sign = 2 * sum(comb(n_r, i) for i in range(k, n_r + 1)) / 2 ** n_r

    # Does "always name the first-listed RFC" beat 1/n?
    first_hits = sum(1 for r in uniq if r["argmax_pos"] == 0)
    exp_first = sum(1.0 / r["n"] for r in uniq)
    quants = [r["argmax_quantile"] for r in uniq
              if r["argmax_quantile"] is not None]

    passing = [r for r in uniq if r["t3b_pass"]]
    p_first_hits = sum(1 for r in passing if r["argmax_pos"] == 0)
    p_exp_first = sum(1.0 / r["n"] for r in passing)
    p_quants = [r["argmax_quantile"] for r in passing
                if r["argmax_quantile"] is not None]

    out = {
        "n_candidate_months": len(rows),
        "n_unique_argmax": len(uniq),
        "rho": {
            "n": n_r, "mean": round(st.mean(rhos), 4),
            "median": round(st.median(rhos), 4),
            "negative": neg, "positive": n_r - neg,
            "sign_test_p": round(p_sign, 6),
            "frac_abs_gt_0.45": round(
                sum(1 for x in rhos if abs(x) > 0.45) / n_r, 4),
        },
        "first_listed_heuristic_all": {
            "hits": first_hits, "n": len(uniq),
            "observed_rate": round(first_hits / len(uniq), 4),
            "expected_rate_if_uniform": round(exp_first / len(uniq), 4),
            "mean_argmax_quantile": round(st.mean(quants), 4),
            "median_argmax_quantile": round(st.median(quants), 4),
        },
        "first_listed_heuristic_t3b_passing_only": {
            "hits": p_first_hits, "n": len(passing),
            "observed_rate": round(p_first_hits / len(passing), 4)
                             if passing else None,
            "expected_rate_if_uniform": round(p_exp_first / len(passing), 4)
                                        if passing else None,
            "mean_argmax_quantile": round(st.mean(p_quants), 4)
                                    if p_quants else None,
        },
        "argmax_position_histogram_decile": dict(
            Counter(min(9, int((r["argmax_quantile"] or 0) * 10))
                    for r in uniq)),
        "rows": rows,
    }
    with open("probe_rfc_monotone.json", "w") as fh:
        json.dump(out, fh, indent=1)
    o = dict(out)
    o.pop("rows")
    print(json.dumps(o, indent=1))


if __name__ == "__main__":
    main()
