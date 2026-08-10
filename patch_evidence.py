#!/usr/bin/env python3
"""patch_evidence.py — one-shot, assertive rewrite of category_traps.py.

Replaces every ad-hoc extremum selection with `_pick_extreme`, which records the
ranking evidence the secondary evaluation loop needs:

  * how many records tie at the extremum (a tie means the prompt is ambiguous)
  * the winner's position in the API's own return order (a leak if first or last)
  * the top-5 ordering keys (the margin between #1 and #2)
  * the distribution of the ANSWER FIELD across the base set (chance-hit rate)

Every replacement asserts it matched exactly once, so a silent no-op is impossible.
"""
import io
import sys

PATH = "category_traps.py"
src = io.open(PATH, encoding="utf-8").read()
orig = src


def sub1(old, new, tag):
    global src
    n = src.count(old)
    if n != 1:
        sys.exit(f"FAIL [{tag}]: pattern occurs {n} times, expected 1\n---\n{old[:300]}")
    src = src.replace(old, new, 1)
    print(f"  ok  {tag}")


OLD_HELPER = '''def _uniq_or_fail(rows, keyfn, label):
    """Return the single extremum row, or fail if the extremum is tied."""
    if not rows:
        raise TrapUnavailable(f"{label}: empty base set")
    best = max(rows, key=keyfn)
    bv = keyfn(best)
    tied = [r for r in rows if keyfn(r) == bv]
    if len(tied) != 1:
        raise TrapUnavailable(f"{label}: extremum is tied across {len(tied)} records")
    return best
'''

NEW_HELPER = '''# Ranking evidence for the most recent _pick_extreme call. The runner clears this
# before each generator and Candidate.to_trap() attaches it to the emitted trap.
LAST_RANK = {}


def _pick_extreme(rows, keyfn, label, mode="max", valuefn=None):
    """Isolate the unique extremum and RECORD why it is the answer.

    Fails on a tie: if two records share the extremum the prompt has two
    defensible answers and is not a well-posed item. The earlier code resolved
    such ties with a secondary sort key (e.g. `key=(date, doi)`) that the prompt
    never states, which silently produced ambiguous prompts.
    """
    global LAST_RANK
    LAST_RANK = {}
    if not rows:
        raise TrapUnavailable(f"{label}: empty base set")
    keys = [keyfn(r) for r in rows]
    order = sorted(range(len(rows)), key=lambda i: keys[i], reverse=(mode == "max"))
    win = order[0]
    tied = [i for i in order if keys[i] == keys[win]]

    ev = {
        "label": label,
        "mode": mode,
        "n_base": len(rows),
        "n_tied_at_extremum": len(tied),
        "distinct_keys": len({str(k) for k in keys}),
        "winner_position_in_api_order": win,
        "winner_is_first_returned": win == 0,
        "winner_is_last_returned": win == len(rows) - 1,
        "top_keys": [str(keys[i]) for i in order[:5]],
    }
    if valuefn is not None:
        vals = [str(valuefn(r)) for r in rows]
        counts = {}
        for v in vals:
            counts[v] = counts.get(v, 0) + 1
        ev["answer_field_distinct_values"] = len(counts)
        ev["answer_field_modal_share"] = round(max(counts.values()) / len(vals), 4)
        ev["p_answer_by_uniform_guess"] = round(counts[vals[win]] / len(vals), 4)
    LAST_RANK = ev

    if len(tied) != 1:
        raise TrapUnavailable(f"{label}: extremum tied across {len(tied)} records")
    return rows[win]


def _uniq_or_fail(rows, keyfn, label, valuefn=None):
    """Backwards-compatible alias: maximum, tie-intolerant."""
    return _pick_extreme(rows, keyfn, label, mode="max", valuefn=valuefn)
'''
sub1(OLD_HELPER, NEW_HELPER, "helper _pick_extreme")

sub1('''            "operators": sorted(sg.resolve_operators(self.sources)),
            "track": "api-native",
        }''',
     '''            "source_operators": sorted(sg.resolve_operators(self.sources)),
            "confirming_operators": sorted(sg.resolve_operators(self.confirming_sources)),
            "track": "api-native",
            "ranking_evidence": dict(LAST_RANK),
        }''',
     "to_trap schema")

sub1('''    best = _uniq_or_fail(base, lambda r: int(r["elevation_ft"]), "geography")''',
     '''    best = _pick_extreme(base, lambda r: int(r["elevation_ft"]), "geography",
                         mode="max", valuefn=lambda r: r["ident"].strip())''',
     "geography")

sub1('''    best = _uniq_or_fail(base, lambda kv: kv[1]["lat"], "travel")''',
     '''    best = _pick_extreme(base, lambda kv: kv[1]["lat"], "travel",
                         mode="max", valuefn=lambda kv: kv[0])''',
     "travel")

sub1('''    best = min(rows, key=lambda r: (r["start"], r["nct"]))
    same = [r for r in rows if r["start"] == best["start"]]
    if len(same) != 1:
        raise TrapUnavailable(f"health: earliest start date tied across {len(same)} studies")''',
     '''    best = _pick_extreme(rows, lambda r: r["start"], "health",
                         mode="min", valuefn=lambda r: r["nct"])''',
     "health")

sub1('''    best = _uniq_or_fail(rows, lambda r: float(r["tot_pub_debt_out_amt"]), "finance")''',
     '''    best = _pick_extreme(rows, lambda r: float(r["tot_pub_debt_out_amt"]), "finance",
                         mode="max", valuefn=lambda r: r["record_date"])''',
     "finance")

sub1('''    earliest = min(annual, key=lambda u: (u["start"], u["end"]))
    same = [u for u in annual if u["start"] == earliest["start"]]
    if len({u["end"] for u in same}) != 1:
        raise TrapUnavailable("business: earliest annual period is ambiguous")''',
     '''    earliest = _pick_extreme(annual, lambda u: u["start"], "business",
                             mode="min", valuefn=lambda u: u["end"])''',
     "business")

sub1('''    best = min(shared, key=lambda r: r["year"])
    if len([r for r in shared if r["year"] == best["year"]]) != 1:
        raise TrapUnavailable("history: earliest three-way share is tied")''',
     '''    best = _pick_extreme(shared, lambda r: r["year"], "history",
                         mode="min", valuefn=lambda r: str(r["year"]))''',
     "history")

sub1('''    best = max(eos, key=lambda d: int(d["executive_order_number"]))
    tied = [d for d in eos if int(d["executive_order_number"]) == int(best["executive_order_number"])]
    if len(tied) != 1:
        raise TrapUnavailable("politics: highest order number is tied")''',
     '''    best = _pick_extreme(eos, lambda d: int(d["executive_order_number"]), "politics",
                         mode="max", valuefn=lambda d: d["publication_date"])''',
     "politics")

sub1('''    best = min(rows, key=lambda o: (str(o["accessionYear"]), o["accessionNumber"]))
    same = [o for o in rows if str(o["accessionYear"]) == str(best["accessionYear"])]
    if len(same) != 1:
        raise TrapUnavailable(f"art: earliest accession year tied across {len(same)} objects")''',
     '''    best = _pick_extreme(rows, lambda o: str(o["accessionYear"]), "art",
                         mode="min", valuefn=lambda o: o["accessionNumber"])''',
     "art")

sub1('''    best = _uniq_or_fail(prods, lambda p: int(re.sub(r"\\D", "", p["code"]) or 0), "shopping")''',
     '''    best = _pick_extreme(
        prods, lambda p: int(re.sub(r"\\D", "", p["code"]) or 0), "shopping", mode="max",
        valuefn=lambda p: (p.get("brands") or "").split(",")[0].strip())''',
     "shopping")

sub1('''    best = _uniq_or_fail(rows, lambda u: u["domains"][0], "education")''',
     '''    best = _pick_extreme(rows, lambda u: u["domains"][0], "education",
                         mode="max", valuefn=lambda u: u["domains"][0])''',
     "education")

io.open(PATH, "w", encoding="utf-8").write(src)
print(f"\npatched {PATH}: {len(orig)} -> {len(src)} chars")
