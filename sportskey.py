"""Can sports be re-keyed off the birth-date recall leak, or must it be withdrawn?

The recall probe (ocr1_recall.json) gives sports key_in_article_frac = 1.00 and
the verdict "TRAVERSAL FREE BY RECALL -- the ranking key is printed in 100% of
members' own articles including the winner's". That is verbatim the verdict that
withdrew celebrities/public figures. The sports key is a BIRTH DATE, and birth
dates are in every ballplayer's Wikipedia article, so a solver that recalls the
1988 Dodgers roster can rank it from memory and never call the MLB API.

A prior probe measured two replacement keys drawn from SEASON PERFORMANCE rather
than biography -- pitching_battersFaced (article leak 0.0, answer depth 0.4324,
winner Orel Hershiser) and hitting_atBats (leak 0.0263, winner Steve Sax). Season
counting stats are the right shape: they are not in the general-knowledge corpus
at per-player granularity the way a birth year is.

The blocker recorded against the swap was that only 5 of 38 players carry a FAST
identifier (Wikidata P2163), and the served answer must be an authority
identifier rather than a name. That blocker is about the POPULATION. It only
actually blocks if the WINNER lacks one. This script settles that.

Measured here, per candidate key:
  1. the winner under the key, from the live MLB stats API
  2. whether that winner resolves to a FAST id, a LCCN, a VIAF, and a Wikidata QID
  3. the answer's depth in the API's own returned order (order-leak check)
  4. uniqueness at the extremum and k-robustness (how many units the winner can
     lose and still hold the argmax)
  5. whether the key value appears in the winner's own Wikipedia article

Checkpoints after every key.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sportskey.json")

ROSTER = ("https://statsapi.mlb.com/api/v1/teams/119/roster"
          "?season=1988&rosterType=fullSeason")
STATS = ("https://statsapi.mlb.com/api/v1/people/{pid}"
         "?hydrate=stats(group=[hitting,pitching],type=[season],season=1988)")

CANDIDATE_KEYS = [
    ("pitching_battersFaced", "pitching", "battersFaced", "max"),
    ("hitting_atBats",        "hitting",  "atBats",       "max"),
    ("pitching_inningsPitched", "pitching", "inningsPitched", "max"),
    ("hitting_plateAppearances", "hitting", "plateAppearances", "max"),
]


def roster():
    js = net.get_json(ROSTER, timeout=90)
    return [{"pid": r["person"]["id"], "name": r["person"]["fullName"]}
            for r in js.get("roster", [])]


def season_stats(pid):
    js = net.get_json(STATS.format(pid=pid), timeout=90)
    people = js.get("people") or [{}]
    out = {}
    for grp in people[0].get("stats", []):
        g = ((grp.get("group") or {}).get("displayName")
             or (grp.get("group") or {}).get("name"))
        for split in grp.get("splits", []):
            if str(split.get("season")) != "1988":
                continue
            out.setdefault(g, {}).update(split.get("stat") or {})
    return out


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v))
    except ValueError:
        return None


# ------------------------------------------------------------------ identifiers
def wikidata_qid(name):
    try:
        js = net.wikidata_search(name)
    except Exception:  # noqa: BLE001
        return None
    for hit in (js if isinstance(js, list) else js.get("search", [])):
        desc = (hit.get("description") or "").lower()
        if "baseball" in desc:
            return hit.get("id")
    hits = js if isinstance(js, list) else js.get("search", [])
    return hits[0].get("id") if hits else None


def wikidata_ids(qid):
    """P2163 FAST, P244 LCCN, P214 VIAF, P1825 (MLB) -- read from the entity."""
    if not qid:
        return {}
    try:
        ent = net.wikidata_entity(qid)
    except Exception:  # noqa: BLE001
        return {}
    ents = (ent.get("entities") or {}).get(qid) or {}
    claims = ents.get("claims") or {}
    want = {"P2163": "fast", "P244": "lccn", "P214": "viaf", "P1825": "mlb"}
    out = {}
    for prop, label in want.items():
        for c in claims.get(prop, []):
            v = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
            if isinstance(v, str):
                out[label] = v
                break
    out["enwiki"] = ((ents.get("sitelinks") or {}).get("enwiki") or {}).get("title")
    return out


def article_text(title):
    if not title:
        return ""
    u = ("https://en.wikipedia.org/w/api.php?action=query&prop=extracts"
         "&explaintext=1&redirects=1&format=json&titles="
         + title.replace(" ", "%20"))
    try:
        js = net.get_json(u, timeout=90)
    except Exception:  # noqa: BLE001
        return ""
    pages = ((js.get("query") or {}).get("pages") or {})
    for p in pages.values():
        return p.get("extract") or ""
    return ""


def key_in_article(text, value):
    """Is the key VALUE recoverable from the prose? Counting stats are the
    point: a birth year matches trivially, a season batters-faced total should
    not appear at all unless the article tabulates it."""
    if not text or value is None:
        return False, None
    iv = int(round(value))
    for pat in (rf"\b{iv}\b", rf"\b{iv:,}\b"):
        if re.search(pat, text):
            return True, pat
    return False, None


def main():
    state = {}
    if os.path.exists(OUT):
        try:
            state = json.load(open(OUT))
        except Exception:  # noqa: BLE001
            state = {}

    if "roster" not in state:
        r = roster()
        state["roster"] = r
        json.dump(state, open(OUT, "w"), indent=1)
    r = state["roster"]
    print(f"roster n={len(r)}", flush=True)

    if "stats" not in state:
        state["stats"] = {}
    for p in r:
        if str(p["pid"]) in state["stats"]:
            continue
        try:
            state["stats"][str(p["pid"])] = season_stats(p["pid"])
        except Exception as e:  # noqa: BLE001
            state["stats"][str(p["pid"])] = {"_error": f"{type(e).__name__}: {e}"}
        json.dump(state, open(OUT, "w"), indent=1)
    print(f"stats fetched for {len(state['stats'])} players", flush=True)

    state.setdefault("keys", {})
    for label, group, field, mode in CANDIDATE_KEYS:
        if label in state["keys"]:
            continue
        print(f"--- {label} ...", flush=True)
        vals = []
        for p in r:
            st = state["stats"].get(str(p["pid"])) or {}
            v = _num((st.get(group) or {}).get(field))
            if v is not None:
                vals.append({"name": p["name"], "pid": p["pid"], "v": v})
        rec = {"key": label, "group": group, "field": field, "mode": mode,
               "n_with_value": len(vals), "n_roster": len(r)}
        if len(vals) < 5:
            rec["status"] = "too few players carry this stat"
            state["keys"][label] = rec
            json.dump(state, open(OUT, "w"), indent=1)
            print(f"    {rec['status']} (n={len(vals)})", flush=True)
            continue

        ordered = sorted(vals, key=lambda x: x["v"], reverse=(mode == "max"))
        top, second = ordered[0], ordered[1]
        tied = [x for x in vals if x["v"] == top["v"]]
        rec.update({
            "winner": top["name"], "winner_pid": top["pid"],
            "winner_value": top["v"], "runner_up": second["name"],
            "runner_up_value": second["v"],
            "n_tied_at_extremum": len(tied),
            "k_robustness": int(top["v"] - second["v"] - 1),
            "margin": top["v"] - second["v"],
            # depth of the winner in the API's OWN returned roster order
            "winner_index_in_api_order": next(
                (i for i, p in enumerate(r) if p["pid"] == top["pid"]), None),
            "api_order_depth": round(
                next((i for i, p in enumerate(r) if p["pid"] == top["pid"]), 0)
                / max(1, len(r) - 1), 4),
            "p_answer_by_uniform_guess": round(1.0 / len(vals), 6),
        })

        qid = wikidata_qid(top["name"])
        ids = wikidata_ids(qid)
        rec["winner_qid"] = qid
        rec["winner_ids"] = ids
        rec["winner_has_fast"] = bool(ids.get("fast"))

        txt = article_text(ids.get("enwiki") or top["name"])
        rec["winner_article"] = ids.get("enwiki") or top["name"]
        rec["winner_article_bytes"] = len(txt)
        hit, pat = key_in_article(txt, top["v"])
        rec["winner_key_in_article"] = hit
        rec["winner_key_match_pattern"] = pat
        rec["answer_in_winner_article"] = bool(
            ids.get("fast") and ids["fast"] in txt)

        # population-level article leak on this key: sample up to 20 members
        checked, leaked = 0, 0
        for x in ordered[:20]:
            q = wikidata_qid(x["name"])
            i2 = wikidata_ids(q)
            t2 = article_text(i2.get("enwiki") or x["name"])
            if not t2:
                continue
            checked += 1
            h, _ = key_in_article(t2, x["v"])
            leaked += 1 if h else 0
        rec["members_checked"] = checked
        rec["members_key_in_article"] = leaked
        rec["key_in_article_frac"] = round(leaked / checked, 4) if checked else None

        state["keys"][label] = rec
        json.dump(state, open(OUT, "w"), indent=1)
        print(f"    winner={rec['winner']} v={rec['winner_value']} "
              f"k={rec['k_robustness']} depth={rec['api_order_depth']} "
              f"FAST={ids.get('fast')} leak={rec['key_in_article_frac']}", flush=True)

    print()
    print("== summary")
    hdr = (f"{'key':<26}{'winner':<20}{'val':>7}{'k':>5}{'depth':>8}"
           f"{'leak':>7}  {'FAST':<10}{'verdict'}")
    print(hdr); print("-" * (len(hdr) + 24))
    for label, _, _, _ in CANDIDATE_KEYS:
        rec = state["keys"].get(label) or {}
        if rec.get("status"):
            print(f"{label:<26}{rec['status']}")
            continue
        leak = rec.get("key_in_article_frac")
        fast = rec.get("winner_ids", {}).get("fast")
        ok = (rec.get("n_tied_at_extremum") == 1 and bool(fast)
              and leak is not None and leak <= 0.10
              and 0.08 <= (rec.get("api_order_depth") or 0) <= 0.92)
        print(f"{label:<26}{str(rec.get('winner'))[:19]:<20}"
              f"{rec.get('winner_value'):>7}{rec.get('k_robustness'):>5}"
              f"{rec.get('api_order_depth'):>8}{str(leak):>7}  "
              f"{str(fast):<10}{'USABLE' if ok else 'no'}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
