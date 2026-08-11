"""Diagnose and repair the education order leak mathematically.

Measured defect: for country=Ireland the ranking key (alphabetically-LAST primary
domain) correlates with Hipo Labs' API return order at Spearman rho = +0.7888,
against a next-worst served trap of 0.2610. The winner sits at position 23 of 27,
so C1 (first-or-last) does not fire, but a solver who ignores the ranking and
guesses among the last 5 records returned hits wit.ie with p = 0.20 -- 5.4x
uniform and above the 0.10 ceiling T2 enforces on every other trap.

Hypothesis H1: Hipo Labs returns institutions in NAME-alphabetical order, and for
Irish institutions the name predicts the domain (University College Dublin ->
ucd.ie), so a domain-alphabetical key inherits the name ordering.

If H1 holds, the repair is a key that is a function of the domain but NOT
monotone in the name. This script tests that directly:
  - confirms the return order is name-alphabetical (rho of api_order vs name rank)
  - for each candidate key, reports rho vs api order, whether the extremum is
    unique, and the top-k guess advantage the leak actually hands a solver
  - sweeps several countries, because the name->domain coupling is a property of
    a country's naming conventions, not of the API

Writes education_keyprobe.json after every country so an interrupt loses nothing.
"""
import json
import os
import sys
import unicodedata

import net

OUT = "education_keyprobe.json"
_HIPO = "http://universities.hipolabs.com/search?country={}"

COUNTRIES = ["Ireland", "Portugal", "New Zealand", "Finland", "Norway",
             "Denmark", "Israel", "Hungary", "Austria", "Chile"]


def _spearman(a, b):
    n = len(a)
    if n < 3:
        return None

    def rank(v):
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

    ra, rb = rank(a), rank(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    da = sum((ra[i] - ma) ** 2 for i in range(n)) ** 0.5
    db = sum((rb[i] - mb) ** 2 for i in range(n)) ** 0.5
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def _norm(s):
    """Fold accents so 'Universidade' and 'Universidade' sort identically."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(s))
                   if not unicodedata.combining(c)).lower()


def _primary_domain(rec):
    doms = [d for d in (rec.get("domains") or []) if d]
    return sorted(doms)[0] if doms else None


# Every key must be statable in a prompt in plain language with no arithmetic
# framing. The label is the sentence that would go into the prompt.
KEYS = {
    "domain_alpha_last": (
        lambda r: _norm(_primary_domain(r)),
        "max", "whose primary internet domain is alphabetically last"),
    "domain_alpha_first": (
        lambda r: _norm(_primary_domain(r)),
        "min", "whose primary internet domain is alphabetically first"),
    "domain_label_reversed_last": (
        lambda r: _norm(_primary_domain(r).split(".")[0])[::-1],
        "max", "whose primary internet domain, read right to left, is alphabetically last"),
    "domain_longest": (
        lambda r: (len(_primary_domain(r)), _norm(_primary_domain(r))),
        "max", "whose primary internet domain is the longest"),
    "domain_shortest": (
        lambda r: (len(_primary_domain(r)), _norm(_primary_domain(r))),
        "min", "whose primary internet domain is the shortest"),
}


def probe_country(country):
    recs = json.loads(net.fetch(_HIPO.format(country.replace(" ", "%20"))))
    recs = [r for r in recs if _primary_domain(r)]
    n = len(recs)
    res = {"country": country, "n": n, "keys": {}}
    if n < 8:
        res["skip"] = f"only {n} institutions with a domain; base too small"
        return res

    api_order = list(range(n))
    names = [_norm(r.get("name", "")) for r in recs]

    # H1: is the API order name-alphabetical? rho ~ 1.0 confirms it.
    name_rank = [0] * n
    for pos, i in enumerate(sorted(range(n), key=lambda i: names[i])):
        name_rank[i] = pos
    res["rho_api_order_vs_name_alpha"] = round(_spearman(api_order, name_rank) or 0, 4)
    res["n_distinct_names"] = len(set(names))

    for kname, (fn, mode, phrase) in KEYS.items():
        try:
            vals = [fn(r) for r in recs]
        except Exception as exc:
            res["keys"][kname] = {"error": repr(exc)}
            continue
        rho = _spearman(api_order, [
            sorted(set(vals)).index(v) for v in vals])
        want = max(vals) if mode == "max" else min(vals)
        tie = sum(1 for v in vals if v == want)
        wi = vals.index(want)
        # The leak a solver can actually exploit: ignore the ranking entirely and
        # guess among the last k (or first k) records the API returned.
        adv = {}
        for k in (3, 5, 10):
            if k >= n:
                continue
            adv[f"p_guess_last_{k}"] = round(1.0 / k if wi >= n - k else 0.0, 4)
            adv[f"p_guess_first_{k}"] = round(1.0 / k if wi < k else 0.0, 4)
        res["keys"][kname] = {
            "mode": mode, "phrase": phrase,
            "rho_vs_api_order": round(rho, 4) if rho is not None else None,
            "abs_rho": round(abs(rho), 4) if rho is not None else None,
            "winner_domain": _primary_domain(recs[wi]),
            "winner_name": recs[wi].get("name"),
            "winner_position": wi, "n": n,
            "depth": round(wi / (n - 1), 4) if n > 1 else None,
            "ties_at_extremum": tie,
            "unique_extremum": tie == 1,
            "p_uniform": round(1.0 / n, 4),
            "exploit": adv,
            # A key is only usable if it beats the ceiling every other trap meets.
            "beats_t2_ceiling": tie == 1 and max(adv.values() or [0]) <= 0.10,
        }
    return res


def main():
    state = json.load(open(OUT)) if os.path.exists(OUT) else {"countries": {}}
    for c in COUNTRIES:
        if c in state["countries"]:
            print(f"skip {c} (checkpointed)")
            continue
        try:
            r = probe_country(c)
        except Exception as exc:
            r = {"country": c, "error": repr(exc)}
        state["countries"][c] = r
        with open(OUT + ".tmp", "w") as fh:
            json.dump(state, fh, indent=1)
        os.replace(OUT + ".tmp", OUT)
        if "error" in r or "skip" in r:
            print(f"{c:14s} {r.get('error') or r.get('skip')}")
            continue
        print(f"{c:14s} n={r['n']:3d} rho(api,name_alpha)={r['rho_api_order_vs_name_alpha']:+.4f}")
        for k, v in r["keys"].items():
            if "error" in v:
                print(f"    {k:28s} ERROR {v['error'][:60]}")
                continue
            flag = "OK " if v["beats_t2_ceiling"] else "   "
            print(f"    {flag}{k:28s} rho={v['rho_vs_api_order']:+.4f} "
                  f"ties={v['ties_at_extremum']} depth={v['depth']} "
                  f"maxexploit={max(v['exploit'].values() or [0]):.4f} -> {v['winner_domain']}")
    print(f"\ncheckpoint: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
