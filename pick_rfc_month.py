"""Re-pick the sci_standard month after RFC 9777 / March 2025 leaked to web search.

Selection is made over the FULL RFC candidate population (not the 10-month sweep
roster), using three measured filters and one measured objective:

  filter A  T3b: |spearman(pages, rfc-number order)| <= 0.45
  filter B  positional: argmax is not the first-listed document of the month
  filter C  prominence: argmax status is not INTERNET STANDARD
            (measured 6.65x enriched among month-argmaxes vs base rate, which is
             why RFC 9777 / STD 101 was search-reachable)
  objective maximise n_base, because the exploitable guess floor is
            approximately lift * (1/n) and n is the only lever that does not
            condition on the answer's own position.

Writes pick_rfc_month.json. No network calls beyond the cached rfc-index.xml.
"""
import json
import os
import statistics

os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import sci_families as sf


def spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def main():
    months = sf._rfc_months()
    rows = []
    status_all = {}
    status_argmax = {}
    n_docs_total = 0

    for (year, month), docs in months.items():
        docs = [d for d in docs if d.get("pages")]
        # match gen_sci_standard's own admissible window so every eligible month
        # is actually producible by the generator
        if not (8 <= len(docs) <= 60):
            continue
        if int(year) < sf._RFC_MIN_YEAR:
            continue
        docs = sorted(docs, key=lambda d: d["num"])
        pages = [d["pages"] for d in docs]
        top = max(pages)
        winners = [d for d in docs if d["pages"] == top]
        n_docs_total += len(docs)
        for d in docs:
            status_all[d.get("status") or "UNKNOWN"] = status_all.get(d.get("status") or "UNKNOWN", 0) + 1
        if len(winners) != 1:
            continue
        w = winners[0]
        status_argmax[w.get("status") or "UNKNOWN"] = status_argmax.get(w.get("status") or "UNKNOWN", 0) + 1
        pos = docs.index(w)
        runner = max(d["pages"] for d in docs if d is not w)
        rho = spearman(list(range(len(docs))), pages)
        rows.append(
            {
                "year": year,
                "month": month,
                "n": len(docs),
                "rfc": w["num"],
                "pages": w["pages"],
                "status": w.get("status"),
                "stream": w.get("stream"),
                "title": (w.get("title") or "")[:110],
                "doi": w.get("doi"),
                "pos": pos,
                "quantile": pos / max(1, len(docs) - 1),
                "rho": None if rho is None else round(rho, 4),
                "margin_abs": top - runner,
                "margin_rel": round((top - runner) / top, 4),
            }
        )

    # measured enrichment of statuses among argmaxes
    enrichment = {}
    n_arg = sum(status_argmax.values())
    for st, c in sorted(status_argmax.items(), key=lambda kv: -kv[1]):
        base = status_all.get(st, 0) / max(1, n_docs_total)
        enrichment[st] = {
            "argmax_count": c,
            "argmax_share": round(c / max(1, n_arg), 4),
            "base_rate": round(base, 4),
            "lift": round((c / max(1, n_arg)) / base, 3) if base else None,
        }

    def keep(r):
        if r["rho"] is None or abs(r["rho"]) > 0.45:
            return False
        if r["pos"] == 0:
            return False
        if (r["status"] or "").upper() == "INTERNET STANDARD":
            return False
        return True

    elig = [r for r in rows if keep(r)]
    elig.sort(key=lambda r: (-r["n"], -r["margin_rel"]))

    out = {
        "n_months_considered": len(rows),
        "n_docs_total": n_docs_total,
        "status_enrichment_among_argmax": enrichment,
        "filters": {
            "t3b_abs_rho_max": 0.45,
            "exclude_first_listed_argmax": True,
            "exclude_status": "INTERNET STANDARD",
        },
        "n_eligible": len(elig),
        "top_20": elig[:20],
        "sweep_roster_status": [
            r
            for r in rows
            if (r["year"], r["month"])
            in {
                ("2026", "January"),
                ("2026", "March"),
                ("2026", "May"),
                ("2026", "June"),
                ("2025", "March"),
            }
        ],
    }
    with open("pick_rfc_month.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps({k: v for k, v in out.items() if k != "top_20"}, indent=2))
    print("\nTOP ELIGIBLE MONTHS")
    for r in elig[:15]:
        print(
            f"  {r['year']}-{r['month']:<10} n={r['n']:<3} rfc={r['rfc']:<5} pages={r['pages']:<4} "
            f"pos={r['pos']:<3} q={r['quantile']:.2f} rho={r['rho']:+.3f} "
            f"marg={r['margin_rel']:.2f} {r['status']} | {r['title'][:60]}"
        )


if __name__ == "__main__":
    main()
