"""Pick a replacement education key, and close two holes in the first probe.

Probe 1 established:
  * Hipo Labs returns institutions in APPROXIMATELY name-alphabetical order
    (rho of return order vs name rank = +0.41..+0.77 across 9 of 10 countries;
    not 1.0, so the list is strongly but not perfectly name-sorted).
  * The shipped key -- alphabetically-last primary domain -- inherits that order
    in EVERY country tested: rho +0.4685 (NZ) to +0.9498 (Hungary), mean ~0.78.
    Ireland's +0.77 is not an Ireland artefact, it is the key's behaviour.

Two holes in probe 1 that this script closes:

  1. UNIQUENESS WAS MEASURED ON THE WRONG THING. The `domain_longest` key was
     the tuple (length, name), so `ties_at_extremum == 1` only proved the TUPLE
     was unique. A prompt can only state "the longest domain"; if two domains
     share the maximum LENGTH the answer is ambiguous no matter what the tuple
     does, and the tie-break silently reintroduces the alphabetical dependence
     the key exists to avoid. Uniqueness is now required on the stated property
     alone.

  2. NO WITNESS CHECK. A low-rho key is worthless if the winner has no
     independent confirmation, or is a defunct or unaccredited institution whose
     domain binding cannot be trusted. Probe 1's clean-exploit winners include
     `knightsbridgeuniversity.com` (unaccredited) and `puv.fi` (superseded by
     vamk.fi) -- exactly the failure the Portugal/utl.pt cross-cohort run
     already exposed. Each candidate is therefore checked against Wikidata P856
     with the same fail-closed equality test gen_education uses.

Writes education_keyfix.json after every candidate.
"""
import json
import os
import sys
import unicodedata

import category_traps as ct
import net

OUT = "education_keyfix.json"
_HIPO = "http://universities.hipolabs.com/search?country={}"

COUNTRIES = ["Austria", "Chile", "Portugal", "Hungary", "Ireland",
             "Denmark", "Israel", "Finland", "Norway"]

# Thresholds. MAX_ABS_RHO is deliberately far below the shipped 0.95: the 13
# other served traps all sit at |rho| <= 0.2610, and the domain-alphabetical
# keys sit at 0.68-0.95, so anything in between separates the two populations.
MAX_ABS_RHO = 0.40
MAX_EXPLOIT = 0.10
MIN_N = 20


def _norm(s):
    return "".join(c for c in unicodedata.normalize("NFKD", str(s))
                   if not unicodedata.combining(c)).lower()


def _primary_domain(rec):
    doms = [d for d in (rec.get("domains") or []) if d]
    return sorted(doms)[0] if doms else None


def _spearman(a, b):
    return ct._spearman(a, b)


# Each candidate is (name, value_fn, mode, prompt_phrase). value_fn returns the
# SINGLE scalar the prompt names -- no tuple tie-breaks, so a tie is a refusal.
CANDIDATES = {
    "domain_longest": (
        lambda d: len(d), "max",
        "whose primary internet domain is the longest"),
    "domain_label_reversed_last": (
        lambda d: _norm(d.split(".")[0])[::-1], "max",
        "whose primary internet domain, with the characters of its first label "
        "read in reverse, sorts alphabetically last"),
}


def witness(name, domain):
    """Fail-closed P856 check, identical in spirit to gen_education's."""
    try:
        val, qid = ct._wikidata_value(name, "P856")
    except Exception as exc:
        return {"ok": False, "reason": f"lookup failed: {type(exc).__name__}", "qid": None}
    if not val:
        return {"ok": False, "reason": "no P856 on the matched item", "qid": qid}
    host = val.split("//")[-1].split("/")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return {"ok": host == domain.lower(), "p856_host": host,
            "qid": qid, "reason": "" if host == domain.lower()
            else f"P856 host {host!r} != key domain {domain!r}"}


def evaluate(country):
    recs = json.loads(net.fetch(_HIPO.format(country.replace(" ", "%20"))))
    recs = [r for r in recs if _primary_domain(r)]
    n = len(recs)
    out = {"country": country, "n": n, "candidates": {}}
    if n < MIN_N:
        out["skip"] = f"n={n} < {MIN_N}; uniform guess too strong"
        return out
    api_order = list(range(n))
    doms = [_primary_domain(r) for r in recs]

    for cname, (fn, mode, phrase) in CANDIDATES.items():
        vals = [fn(d) for d in doms]
        want = max(vals) if mode == "max" else min(vals)
        # Uniqueness on the STATED property only. This is hole 1.
        ties = sum(1 for v in vals if v == want)
        rec = {"mode": mode, "phrase": phrase, "n": n,
               "ties_at_extremum_on_stated_property": ties,
               "p_uniform": round(1.0 / n, 4)}
        rho = _spearman(api_order, [sorted(set(vals)).index(v) for v in vals])
        rec["rho_vs_api_order"] = round(rho, 4) if rho is not None else None
        rec["abs_rho"] = round(abs(rho), 4) if rho is not None else None
        if ties != 1:
            rec["verdict"] = "refuse: extremum not unique on the stated property"
            out["candidates"][cname] = rec
            continue
        wi = vals.index(want)
        rec.update({"winner_domain": doms[wi], "winner_name": recs[wi].get("name"),
                    "winner_position": wi, "depth": round(wi / (n - 1), 4)})
        exploit = max([1.0 / k for k in (3, 5, 10)
                       if k < n and wi >= n - k] + [0.0])
        rec["max_endpoint_exploit"] = round(exploit, 4)
        fails = []
        if rec["abs_rho"] is None or rec["abs_rho"] > MAX_ABS_RHO:
            fails.append(f"abs_rho {rec['abs_rho']} > {MAX_ABS_RHO}")
        if exploit > MAX_EXPLOIT:
            fails.append(f"endpoint exploit {exploit:.4f} > {MAX_EXPLOIT}")
        if fails:
            rec["verdict"] = "refuse: " + "; ".join(fails)
            out["candidates"][cname] = rec
            continue
        w = witness(recs[wi].get("name", ""), doms[wi])
        rec["witness"] = w
        rec["verdict"] = "ACCEPT" if w["ok"] else f"refuse: witness {w['reason']}"
        out["candidates"][cname] = rec
    return out


def main():
    state = json.load(open(OUT)) if os.path.exists(OUT) else {"countries": {}}
    state["thresholds"] = {"MAX_ABS_RHO": MAX_ABS_RHO,
                           "MAX_EXPLOIT": MAX_EXPLOIT, "MIN_N": MIN_N}
    accepted = []
    for c in COUNTRIES:
        if c in state["countries"]:
            r = state["countries"][c]
        else:
            try:
                r = evaluate(c)
            except Exception as exc:
                r = {"country": c, "error": repr(exc)}
            state["countries"][c] = r
            with open(OUT + ".tmp", "w") as fh:
                json.dump(state, fh, indent=1)
            os.replace(OUT + ".tmp", OUT)
        if r.get("error") or r.get("skip"):
            print(f"{c:10s} {r.get('error') or r.get('skip')}")
            continue
        for cn, v in r["candidates"].items():
            line = (f"{c:10s} {cn:26s} rho={v.get('abs_rho')} "
                    f"ties={v['ties_at_extremum_on_stated_property']} "
                    f"depth={v.get('depth')} expl={v.get('max_endpoint_exploit')} "
                    f"-> {v.get('winner_domain')}")
            print(f"{'ACCEPT ' if v['verdict'] == 'ACCEPT' else '       '}{line}")
            if v["verdict"] != "ACCEPT":
                print(f"           {v['verdict']}")
            else:
                accepted.append((c, cn, v))
    state["accepted"] = [{"country": c, "key": k, **v} for c, k, v in accepted]
    with open(OUT + ".tmp", "w") as fh:
        json.dump(state, fh, indent=1)
    os.replace(OUT + ".tmp", OUT)
    print(f"\n{len(accepted)} (country, key) pairs pass rho, exploit, uniqueness AND witness")
    for c, k, v in accepted:
        print(f"  {c} / {k}: {v['winner_domain']} ({v['winner_name']}) "
              f"rho={v['abs_rho']} depth={v['depth']} qid={v['witness'].get('qid')}")
    print(f"checkpoint: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
