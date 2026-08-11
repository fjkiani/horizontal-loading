"""Classify every generator's Wikimedia dependency by MUTATING Wikidata.

The question a source audit cannot answer by reading `sources`: if someone edits
Wikidata, does this trap REFUSE or does it ship a different answer? Those are
different risks and they need different fixes.

  veto-only      the answer comes from elsewhere and Wikidata can only cause a
                 raise. Being wrong costs coverage. Repair is cheap: keep the
                 check as logic, stop counting it as an operator.
  independent    the answer does not move and no raise happens. Nothing to fix
                 beyond deleting the URL from `sources`.
  ANSWER-BEARING the returned answer tracks the mutated value. An editor of a
                 public wiki controls what the benchmark calls correct, and
                 nothing downstream notices. Repair needs a new operator.

Two mutation strengths, because one is not enough.

  T1 garbage            values that are well-formed for their datatype but
                        meaningless. Detects a generator with NO check at all.
                        A raise here is ambiguous: it may only mean the value
                        failed a FORMAT test, not that the fact was verified.
  T2 real-but-wrong     a genuine identifier belonging to a DIFFERENT entity.
                        This is the adversarial case and the only one that
                        separates a format check from a fact check.

Baselines come from category_trap_candidates.json at the current commit rather
than a fresh unpatched run, so a category that already fails for unrelated
reasons is not scored as though the mutation caused it.

Checkpoints after every category.
"""
import json
import os
import re
import sys
import time
import traceback

import category_traps as ct
import gen_v2  # noqa: F401  installs gen_v2 overrides
import gen_v3  # noqa: F401  MUST follow gen_v2
import gen_v4  # noqa: F401  MUST follow gen_v3

OUT = os.environ.get("WIKIMUT_OUT", "wikimut.json")
BASE = "category_trap_candidates.json"

# ---------------------------------------------------------------------------
# Mutation payloads.
#
# T2 values are real identifiers that belong to the WRONG entity, so the
# generator receives something that will pass any syntactic check and must be
# caught, if at all, by actually verifying the fact against another operator.
# ---------------------------------------------------------------------------
T1 = {
    "qid": "Q900000001",
    "label": "Mutant Entity One",
    "string": "MUTANTVALUE1",
    "time": "+1901-01-01T00:00:00Z",
    "amount": "+11111",
    "item": "Q900000011",
}
T2 = {
    "qid": "Q90",                 # Q90 is Paris: a real item, wrong entity
    "label": "Paris",
    # real identifiers, all belonging to something other than any trap winner
    "string_by_prop": {
        "P239": "KJFK",           # real ICAO, New York JFK
        "P1566": "5128581",       # real GeoNames id, New York City
        "P5531": "0000320193",    # real SEC CIK, Apple Inc
        "P345": "tt0111161",      # real IMDb id, The Shawshank Redemption
        "P2163": "1914005",       # real FAST id, wrong person
        "P856": "https://www.example.edu",
        "P227": "118540238",      # real GND id, Goethe
        "P217": "1942.9",         # real museum accession, wrong object
        "P239_alt": "EFHK",
    },
    "string": "KJFK",
    "time": "+1902-02-02T00:00:00Z",
    "amount": "+22222",
    "item": "Q64",                # Berlin
}


def make_patches(mode):
    """Return replacements for the three net chokepoints every helper uses.

    T0 is the decisive mode and it was missing from the first version of this
    probe. Identity mutations (T1, T2) cannot exercise a check of the form
    `_wikidata_by_value(prop, answer)`, because that query asks "does exactly
    one item assert prop = answer" and a mock that always returns one binding
    satisfies it vacuously. Geography passed both identity mutations for that
    reason, which would have been reported as "no dependency" when in fact the
    call is load-bearing logic. T0 simulates the ban itself: the call cannot be
    made at all. Anything that raises under T0 must be re-plumbed before the
    ban lands, and anything that ships under T0 needs only its URL deleted.
    """
    if mode == "T0":
        def _banned(*a, **k):
            raise ct.net.FetchError(
                "banned domain wikidata.org (simulated by wikimut T0)")
        return {"wikidata_search": _banned, "wikidata_entity": _banned,
                "wikidata_sparql": _banned}

    cfg = T1 if mode == "T1" else T2
    n_hits = 2 if mode == "T4" else 1

    def _claim_bundle(prop):
        """Every datavalue flavour the helpers can read, for ANY property.

        _wikidata_value / _wikidata_values read str, time and amount and skip
        item claims. _wikidata_item_label(s) read only item claims. Emitting
        all three means one mock serves every caller and no helper falls
        through to an empty list for a reason unrelated to the mutation.
        """
        sval = cfg.get("string_by_prop", {}).get(prop, cfg["string"])
        return [
            {"mainsnak": {"datavalue": {"value": sval}}},
            {"mainsnak": {"datavalue": {"value": {"time": cfg["time"]}}}},
            {"mainsnak": {"datavalue": {"value": {"amount": cfg["amount"]}}}},
            {"mainsnak": {"datavalue": {"value": {"entity-type": "item",
                                                  "id": cfg["item"]}}}},
        ]

    class _Claims(dict):
        """claims.get(prop) must answer for any property the caller asks for.

        __bool__ MUST be True. category_traps.py:751 reads
            claims = (...).get("claims", {}) or {}
        and an empty dict subclass is falsy, so without this the mock
        collapses to a plain {} and the mutation is silently discarded.
        That single line is the one that sets the travel answer, so the
        first version of this harness was blind exactly where it mattered.
        """

        def get(self, prop, default=None):
            return _claim_bundle(prop)

        def __bool__(self):
            return True

    def search(q, *a, **k):
        return {"search": [{"id": cfg["qid"] if i == 0 else "Q9999999%d" % i,
                            "label": cfg["label"],
                            "description": "mutated %s airport university" % mode}
                           for i in range(n_hits)]}

    def entity(qid, *a, **k):
        return {"entities": {qid: {
            "claims": _Claims(),
            "labels": {"en": {"value": cfg["label"]},
                       "mul": {"value": cfg["label"]}},
            "aliases": {"en": [{"value": cfg["label"]}]},
            "sitelinks": {"enwiki": {"title": cfg["label"]}},
        }}}

    def sparql(q, *a, **k):
        return {"results": {"bindings": [
            {"item": {"value": "http://www.wikidata.org/entity/%s"
                      % (cfg["qid"] if i == 0 else "Q9999999%d" % i)},
             "itemLabel": {"value": cfg["label"]}} for i in range(n_hits)]}}

    return {"wikidata_search": search, "wikidata_entity": entity,
            "wikidata_sparql": sparql}


def load_baselines():
    """answer / status / etype per category from the current candidate file."""
    out = {}
    if not os.path.exists(BASE):
        return out
    for cat, rec in json.load(open(BASE))["results"].items():
        t = rec.get("trap") or {}
        out[cat] = {"status": rec.get("status"),
                    "answer": t.get("answer"),
                    "field": t.get("field"),
                    "etype": rec.get("etype")}
    return out


def run_patched(cat, mode):
    """Run one generator with Wikidata mutated. Never raises."""
    patches = make_patches(mode)
    saved = {k: getattr(ct.net, k) for k in patches}
    for k, v in patches.items():
        setattr(ct.net, k, v)
    t0 = time.time()
    try:
        with ct.generation():
            cand = ct.GENERATORS[cat]()
            trap = cand.to_trap()
        return {"outcome": "returned", "answer": trap.get("answer"),
                "field": trap.get("field"),
                "operators": trap.get("source_operators"),
                "secs": round(time.time() - t0, 1)}
    except ct.TrapUnavailable as e:
        return {"outcome": "raised", "etype": "TrapUnavailable",
                "error": str(e)[:400], "secs": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        return {"outcome": "raised", "etype": type(e).__name__,
                "error": f"{type(e).__name__}: {e}"[:400],
                "tb": traceback.format_exc()[-600:],
                "secs": round(time.time() - t0, 1)}
    finally:
        for k, v in saved.items():
            setattr(ct.net, k, v)


def mutated_strings(mode):
    cfg = T1 if mode == "T1" else T2
    vals = {cfg["string"], cfg["qid"], cfg["item"], cfg["time"],
            cfg["amount"].lstrip("+")}
    vals |= set(cfg.get("string_by_prop", {}).values())
    return {str(v) for v in vals if v}


MODES = ("T0", "T1", "T2", "T4")


def classify(cat, base, runs):
    """Return (verdict, why). The taxonomy is what the REPAIR depends on."""
    b_ans = base.get("answer")
    b_ok = base.get("status") == "ok"

    if not b_ok:
        return ("UNINFORMATIVE",
                "baseline is %s (%s), so nothing observed under mutation is "
                "attributable to Wikidata" % (base.get("status"),
                                              base.get("etype")))

    # WORST CASE FIRST: does a mutated value reach the shipped answer?
    for mode in ("T1", "T2", "T4"):
        m = runs[mode]
        if m["outcome"] != "returned":
            continue
        a = str(m.get("answer") or "")
        for mv in mutated_strings(mode):
            if a and (a == mv or mv in a):
                return ("ANSWER_BEARING",
                        "%s: mutated value %r reached the shipped answer %r, "
                        "so a wiki editor controls what the benchmark calls "
                        "correct" % (mode, mv, a))
        if a and a != str(b_ans):
            return ("ANSWER_BEARING",
                    "%s: answer moved from %r to %r while still shipping"
                    % (mode, b_ans, a))

    # THE BAN ITSELF. T0 makes the call impossible, which is exactly the world
    # after wikidata.org joins BANNED_DOMAINS.
    t0 = runs["T0"]
    if t0["outcome"] == "raised":
        return ("LOAD_BEARING_LOGIC",
                "T0: with the call unavailable the generator raises (%s), so "
                "the ban breaks it and the check must be repointed, not just "
                "unlinked" % str(t0.get("error"))[:120])
    if str(t0.get("answer")) == str(b_ans):
        return ("SAFE_TO_DROP",
                "T0: ships the baseline answer %r with Wikidata unreachable, "
                "so only the URL and the operator credit need removing"
                % b_ans)
    return ("ANSWER_BEARING",
            "T0: shipped %r instead of the baseline %r with Wikidata "
            "unreachable" % (t0.get("answer"), b_ans))


def fail_closed_note(runs):
    """Does an AMBIGUOUS wiki edit refuse (fail-closed) or get accepted?"""
    m = runs.get("T4") or {}
    if m.get("outcome") == "raised":
        return "fail-closed: two colliding items caused a refusal"
    if m.get("outcome") == "returned":
        return ("fail-arbitrary: two colliding items were accepted and it "
                "shipped %r" % m.get("answer"))
    return "not measured"


def main():
    cats = sys.argv[1].split(",") if len(sys.argv) > 1 else list(ct.GENERATORS)
    base = load_baselines()
    state = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "why": __doc__.strip().split("\n")[0],
             "n_categories": len(cats), "rows": [], "summary": {}}

    for cat in cats:
        b = base.get(cat, {})
        print("--- %s (baseline %s %r) ..." % (cat, b.get("status"),
                                               b.get("answer")), flush=True)
        runs = {}
        if b.get("status") != "ok":
            # classify() returns UNINFORMATIVE for any non-ok baseline, so four
            # mutation runs would buy nothing and art/finance are slow.
            for mode in MODES:
                runs[mode] = {"outcome": "skipped",
                              "error": "baseline not ok; mutation uninformative"}
            print("      skipped: baseline %s (%s)"
                  % (b.get("status"), b.get("etype")), flush=True)
        else:
            for mode in MODES:
                runs[mode] = run_patched(cat, mode)
                print("      %s %-8s %s" % (mode, runs[mode]["outcome"],
                                            runs[mode].get("answer")
                                            or runs[mode].get("error", "")[:88]),
                      flush=True)
        verdict, why = classify(cat, b, runs)
        print("      => %s  %s" % (verdict, why[:130]), flush=True)
        row = {"category": cat, "baseline": b, "verdict": verdict, "why": why,
               "ambiguity_behaviour": fail_closed_note(runs)}
        row.update(runs)
        state["rows"].append(row)
        from collections import Counter
        c = Counter(r["verdict"] for r in state["rows"])
        state["summary"] = dict(c)
        tmp = OUT + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh, indent=1)
        os.replace(tmp, OUT)

    print("\n== %s" % json.dumps(state["summary"]))
    unsafe = [r["category"] for r in state["rows"]
              if r["verdict"] == "ANSWER_BEARING"]
    state["answer_bearing"] = unsafe
    state["verdict"] = ("%d generator(s) let a public wiki set the answer: %s"
                        % (len(unsafe), ", ".join(unsafe) or "none"))
    with open(OUT + ".tmp", "w") as fh:
        json.dump(state, fh, indent=1)
    os.replace(OUT + ".tmp", OUT)
    print(state["verdict"])
    print("checkpoint: %s" % OUT)


if __name__ == "__main__":
    main()
