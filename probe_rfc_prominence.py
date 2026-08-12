"""Quantify the prominence shortcut for candidate sci_standard months.

The March 2025 seed leaked because its argmax (RFC 9777) is an INTERNET STANDARD
flagship: measured 6.65x enriched among month-argmaxes. "Not an INTERNET
STANDARD" is a crude proxy for fame. This probe replaces it with a measured one.

For each candidate month, Crossref is queried once for every RFC DOI published
that month, returning is-referenced-by-count. Then:

  cit_argmax          citations of the longest document
  cit_rank            its rank by citations within the month (0 = most cited)
  is_citation_argmax  True when longest is ALSO most cited -> a fame shortcut
                      answers the question without reading page counts
  rho_pages_cit       within-month spearman(pages, citations)

A month is preferred when the longest document is NOT the most cited, because a
solver reasoning from familiarity is then actively wrong. Crossref is already a
witness operator for this family, so no new domain enters the source set.
"""
import json
import os
import statistics
import time

os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net
import sci_families as sf

_MONTH_NUM = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
    "December": 12,
}


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


def crossref_month(year, month):
    """One Crossref request covering every RFC DOI published in the month."""
    mn = _MONTH_NUM[month]
    nxt_y, nxt_m = (int(year) + 1, 1) if mn == 12 else (int(year), mn + 1)
    url = (
        "https://api.crossref.org/prefixes/10.17487/works"
        f"?filter=from-pub-date:{year}-{mn:02d}-01,until-pub-date:{nxt_y}-{nxt_m:02d}-01"
        "&rows=200&select=DOI,is-referenced-by-count,title,published"
        "&mailto=seal-audit@example.org"
    )
    raw = net.fetch(url, timeout=60)
    data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    out = {}
    for it in data.get("message", {}).get("items", []):
        doi = (it.get("DOI") or "").upper()
        num = doi.split("RFC")[-1] if "RFC" in doi else None
        if num and num.isdigit():
            out[str(int(num))] = int(it.get("is-referenced-by-count") or 0)
    return out


def main():
    pick = json.load(open("pick_rfc_month.json"))
    cands = pick["top_20"]
    months = sf._rfc_months()
    rows = []

    for c in cands:
        key = (c["year"], c["month"])
        docs = sorted(
            [d for d in months.get(key, []) if d.get("pages")], key=lambda d: d["num"]
        )
        try:
            cits = crossref_month(c["year"], c["month"])
        except Exception as exc:  # noqa: BLE001
            rows.append({**c, "crossref_error": str(exc)[:160]})
            print(f"  {c['year']}-{c['month']} crossref FAILED {exc}")
            continue
        time.sleep(0.4)

        got = [(d, cits.get(str(int(d["num"])))) for d in docs]
        covered = [(d, v) for d, v in got if v is not None]
        if len(covered) < 5:
            rows.append({**c, "crossref_coverage": len(covered), "note": "sparse"})
            print(f"  {c['year']}-{c['month']} sparse coverage {len(covered)}/{len(docs)}")
            continue

        cit_of = {str(int(d["num"])): v for d, v in covered}
        arg = str(int(c["rfc"]))
        cit_arg = cit_of.get(arg)
        ordered = sorted(cit_of.items(), key=lambda kv: -kv[1])
        cit_rank = [k for k, _ in ordered].index(arg) if arg in cit_of else None
        top_cit_rfc, top_cit_val = ordered[0]
        rho_pc = spearman([d["pages"] for d, _ in covered], [v for _, v in covered])

        row = {
            **c,
            "crossref_coverage": f"{len(covered)}/{len(docs)}",
            "cit_argmax": cit_arg,
            "cit_rank": cit_rank,
            "cit_rank_quantile": None if cit_rank is None else round(cit_rank / max(1, len(ordered) - 1), 3),
            "is_citation_argmax": (cit_rank == 0),
            "most_cited_rfc": top_cit_rfc,
            "most_cited_value": top_cit_val,
            "median_cit": statistics.median([v for _, v in covered]),
            "rho_pages_citations": None if rho_pc is None else round(rho_pc, 4),
        }
        rows.append(row)
        print(
            f"  {c['year']}-{c['month']:<10} n={c['n']:<3} rfc={c['rfc']:<5} "
            f"cit={cit_arg:<5} rank={cit_rank:<3} most_cited={top_cit_rfc}({top_cit_val}) "
            f"rho_pc={row['rho_pages_citations']}"
        )

    scored = [r for r in rows if r.get("cit_rank") is not None]
    scored.sort(key=lambda r: (r["is_citation_argmax"], -r["n"]))
    out = {
        "n_candidates": len(cands),
        "n_scored": len(scored),
        "n_fame_shortcut_months": sum(1 for r in scored if r["is_citation_argmax"]),
        "frac_fame_shortcut": round(
            sum(1 for r in scored if r["is_citation_argmax"]) / max(1, len(scored)), 4
        ),
        "rows": rows,
        "ranked": scored,
    }
    with open("probe_rfc_prominence.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print("\nFAME SHORTCUT RATE among candidate months:",
          out["n_fame_shortcut_months"], "/", out["n_scored"],
          f"= {out['frac_fame_shortcut']}")
    print("\nRANKED (longest-is-not-most-cited first, then largest n)")
    for r in scored[:12]:
        print(
            f"  {r['year']}-{r['month']:<10} n={r['n']:<3} rfc={r['rfc']:<5} "
            f"pages={r['pages']:<4} pos={r['pos']:<3} rho={r['rho']:+.3f} "
            f"cit={r['cit_argmax']:<5} citrank={r['cit_rank']:<3} "
            f"fame={r['is_citation_argmax']} {r['status']}"
        )


if __name__ == "__main__":
    main()
