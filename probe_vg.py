"""Rebuild the video-games trap after the release-date answer field failed.

Measured in probe_conf.json: across the 12 pinned appids, Steam and Wikidata
agree on the release date for only 3 of 12. Four disagree by exactly -1 day
(all in the same direction, consistent with a global-midnight launch read in a
US store locale) and five disagree by 307-5257 days because the storefront
records the date the title appeared ON STEAM while Wikidata P577 records the
original publication. The release date is therefore not an atomic fact across
operators and cannot be the answer.

The replacement candidate is the DEVELOPER, which failed before only on
coarseness: 12 titles gave a modal developer share of 0.167, above the 0.10
guess ceiling. Coarseness is fixable by construction - if the pinned roster
holds n titles from n DISTINCT studios, a uniform guess over the observed
developer values is exactly 1/n. This probe measures a wide candidate pool,
reports which appids resolve, how many distinct studios they span, whether the
normalised-title key ties, and whether Wikidata P178 confirms the winner.
"""
from __future__ import annotations

import json
import os
import re
import traceback

import category_traps as ct
import net

OUT = "probe_vg.json"

POOL = [3830, 6910, 8930, 22380, 105600, 250900, 39210, 271590, 292030,
        367520, 377160, 578080, 413150, 322330, 236850, 261550, 108600,
        251570, 4000, 220200, 242760, 218620, 268910, 391540, 620, 570,
        427520, 294100, 255710, 289070]


def norm_title(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def fetch(appid):
    js = net.get_json(f"https://store.steampowered.com/api/appdetails?"
                      f"appids={appid}&cc=us&l=english", timeout=90, attempts=4)
    d = js.get(str(appid)) or {}
    if not d.get("success"):
        return None
    dd = d["data"]
    devs = dd.get("developers") or []
    pubs = dd.get("publishers") or []
    if not dd.get("name") or not devs:
        return None
    return {"appid": appid, "name": dd["name"], "dev": devs[0],
            "n_devs_listed": len(devs), "pub": pubs[0] if pubs else None,
            "key": norm_title(dd["name"])}


def main():
    rows, errs = [], []
    for a in POOL:
        try:
            r = fetch(a)
            if r:
                rows.append(r)
                print(f"  {a:8d} {r['key'][:28]:28s} {r['dev'][:34]}")
            else:
                errs.append({"appid": a, "why": "success=false or missing fields"})
        except Exception as exc:  # noqa: BLE001
            errs.append({"appid": a, "why": f"{type(exc).__name__}: {exc}"})
        json.dump({"rows": rows, "errors": errs}, open(OUT + ".tmp", "w"), indent=2)
        os.replace(OUT + ".tmp", OUT)

    # Greedy roster: keep the first title from each distinct studio, so the
    # observed developer values are all distinct and p_guess collapses to 1/n.
    seen, roster = set(), []
    for r in sorted(rows, key=lambda x: x["key"]):
        d = ct._norm(r["dev"])
        if d in seen:
            continue
        seen.add(d)
        roster.append(r)

    keys = [r["key"] for r in roster]
    ties = len(keys) - len(set(keys))
    winner = min(roster, key=lambda r: r["key"]) if roster else None

    res = {"n_pool": len(POOL), "n_resolved": len(rows), "errors": errs,
           "n_distinct_devs_in_pool": len({ct._norm(r["dev"]) for r in rows}),
           "roster_size": len(roster), "title_key_ties": ties,
           "p_guess_if_all_distinct": round(1.0 / len(roster), 6) if roster else None,
           "roster": [{"appid": r["appid"], "key": r["key"], "name": r["name"],
                       "dev": r["dev"]} for r in roster],
           "winner": winner}

    # Confirm the alphabetically-first title's developer against Wikidata P178,
    # scanning every claim (P178 is multi-valued, like P166).
    if winner:
        try:
            labs, qid = ct._wikidata_item_labels(winner["name"], "P178",
                                                 must_contain="video game")
            res["wd_qid"] = qid
            res["wd_p178"] = labs
            res["wd_match"] = any(ct._norm(winner["dev"]) == ct._norm(l["label"])
                                  or ct._norm(winner["dev"]) in ct._norm(l["label"])
                                  or ct._norm(l["label"]) in ct._norm(winner["dev"])
                                  for l in labs if l.get("label"))
        except Exception as exc:  # noqa: BLE001
            res["wd_error"] = f"{type(exc).__name__}: {exc}"
            res["wd_tb"] = traceback.format_exc()[-600:]

    # How robust is the winner? Report the next few alphabetically so a small
    # roster edit does not silently change the answer.
    res["alphabetical_head"] = [{"key": r["key"], "dev": r["dev"], "appid": r["appid"]}
                                for r in sorted(roster, key=lambda x: x["key"])[:5]]

    json.dump(res, open(OUT + ".tmp", "w"), indent=2)
    os.replace(OUT + ".tmp", OUT)
    print("\nresolved", len(rows), "of", len(POOL),
          "| roster", len(roster), "| ties", ties,
          "| p_guess", res["p_guess_if_all_distinct"])
    print("winner:", winner and winner["name"], "->", winner and winner["dev"])
    print("wikidata P178 match:", res.get("wd_match"), res.get("wd_p178"))


if __name__ == "__main__":
    main()
