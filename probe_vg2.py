"""Verify the 14-appid video-games roster before writing it into a generator.

Checks that matter:
  - every appid resolves and the 14 studios are distinct  -> p_guess = 1/14
  - no normalised-title ties                              -> unique extremum
  - no digit-leading title                                -> the key is not
    trivially predictable the way "7 Days to Die" would be, which is the same
    defect as the recurring boilerplate that killed the alphabetical politics key
  - spearman(alphabetical key, appid order) is near zero, so the exemption
    granted by collection_is_explicit is not load-bearing
  - the winner is interior to the fetch order
  - Wikidata P178 confirms the winning studio
"""
from __future__ import annotations

import json
import os
import re

import category_traps as ct
import net

OUT = "probe_vg2.json"

ROSTER = (4000, 6910, 22380, 39210, 105600, 108600, 220200,
          236850, 255710, 268910, 322330, 367520, 413150, 427520)


def norm_title(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def main():
    rows = []
    for a in ROSTER:
        js = net.get_json(f"https://store.steampowered.com/api/appdetails?"
                          f"appids={a}&cc=us&l=english", timeout=90, attempts=4)
        d = js.get(str(a)) or {}
        if not d.get("success"):
            rows.append({"appid": a, "error": "success=false"})
            continue
        dd = d["data"]
        rows.append({"appid": a, "name": dd.get("name"),
                     "dev": (dd.get("developers") or [None])[0],
                     "key": norm_title(dd.get("name"))})
        print(f"  {a:8d} {rows[-1]['key'][:30]:30s} {rows[-1]['dev']}")

    good = [r for r in rows if r.get("key") and r.get("dev")]
    keys = [r["key"] for r in good]
    devs = [ct._norm(r["dev"]) for r in good]
    order = list(range(len(good)))
    rho = ct._spearman([sorted(keys).index(k) for k in keys], order)
    winner = min(good, key=lambda r: r["key"])
    pos = good.index(winner)

    labs, qid = ct._wikidata_item_labels(winner["name"], "P178",
                                         must_contain="video game")
    match = any(ct._norm(winner["dev"]) == ct._norm(l["label"])
                or ct._norm(winner["dev"]) in ct._norm(l["label"])
                or ct._norm(l["label"]) in ct._norm(winner["dev"])
                for l in labs if l.get("label"))

    res = {"n": len(good), "n_resolved_of": len(ROSTER),
           "n_distinct_devs": len(set(devs)),
           "all_devs_distinct": len(set(devs)) == len(devs),
           "key_ties": len(keys) - len(set(keys)),
           "digit_leading": [k for k in keys if k[:1].isdigit()],
           "p_guess_dev": round(1.0 / len(set(devs)), 6) if devs else None,
           "spearman_key_vs_appid_order": rho,
           "winner": winner, "winner_index": pos,
           "winner_is_first": pos == 0, "winner_is_last": pos == len(good) - 1,
           "depth": round(pos / (len(good) - 1), 4),
           "alphabetical_head": sorted(keys)[:4],
           "wd_qid": qid, "wd_p178": labs, "wd_match": match,
           "rows": rows}
    json.dump(res, open(OUT + ".tmp", "w"), indent=2)
    os.replace(OUT + ".tmp", OUT)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"},
                     indent=2, default=str))


if __name__ == "__main__":
    main()
