"""Interrogate the three second-confirmation failures from the gen_v2 run.

Each failure is a hypothesis about WHY the confirmation missed, and each
hypothesis is measurable:

  geography   H: _wikidata_value searched by airport NAME, and "San Luis
                 Airport" is a name collision (Argentina SAOU vs Colombia).
                 Test: query Wikidata BY THE ANSWER (?item wdt:P239 "SKIP")
                 and count how many items carry that ICAO. If exactly one,
                 the confirmation direction was simply backwards.

  history     H: P166 is multi-valued and the helper returns the FIRST item.
                 Test: list every P166 value for the laureate and check
                 whether a Nobel prize is present but not first.

  video games H: Steam and Wikidata disagree by exactly one day because a
                 global midnight launch straddles a timezone boundary, i.e.
                 the release date is not an atomic fact. Test: measure the
                 signed Steam-minus-Wikidata day offset for ALL pinned
                 appids. A systematic +/-1 pattern kills the answer field;
                 a single stray disagreement is a per-title data issue.

Writes probe_conf.json incrementally so an interrupt loses nothing.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import traceback

import category_traps as ct
import gen_v2  # noqa: F401
import net

OUT = "probe_conf.json"
R = {}


def save():
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(R, fh, indent=2)
    os.replace(tmp, OUT)


def step(name, fn):
    try:
        R[name] = fn()
    except Exception as exc:  # noqa: BLE001
        R[name] = {"error": f"{type(exc).__name__}: {exc}",
                   "tb": traceback.format_exc()[-900:]}
    save()
    print(f"[{name}] {json.dumps(R[name], default=str)[:600]}")


# ------------------------------------------------------------------ geography
def geo_by_answer():
    """Confirm by the ANSWER (ICAO) rather than by the ambiguous name."""
    rows = ct._ourairports_rows()
    base = [r for r in rows
            if r["iso_country"] == "CO"
            and r["type"] in ("medium_airport", "large_airport")
            and r["scheduled_service"] == "yes"
            and re.fullmatch(r"[A-Z]{4}", (r.get("gps_code") or "").strip() or "")
            and (r["elevation_ft"] or "").lstrip("-").isdigit()]
    best = max(base, key=lambda r: int(r["elevation_ft"]))
    icao = best["gps_code"].strip()
    out = {"n_base": len(base), "winner_icao": icao,
           "winner_name": best["name"], "elev_ft": int(best["elevation_ft"]),
           "municipality": best.get("municipality")}

    # 1. how many Wikidata items carry this exact ICAO?
    q = 'SELECT ?item ?itemLabel ?ctry WHERE { ?item wdt:P239 "%s". ' \
        'OPTIONAL { ?item wdt:P17 ?c. ?c rdfs:label ?ctry FILTER(lang(?ctry)="en") } ' \
        'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } LIMIT 10' % icao
    b = net.wikidata_sparql(q).get("results", {}).get("bindings", [])
    out["by_answer_hits"] = [{"qid": x["item"]["value"].rsplit("/", 1)[-1],
                              "label": x.get("itemLabel", {}).get("value"),
                              "country": x.get("ctry", {}).get("value")} for x in b]
    out["by_answer_n"] = len(b)

    # 2. what did the NAME search actually resolve to? (the collision test)
    val, qid = ct._wikidata_value(best["name"], "P239", must_contain="airport")
    out["by_name_qid"] = qid
    out["by_name_p239"] = val
    if qid:
        q2 = 'SELECT ?ctry WHERE { wd:%s wdt:P17 ?c. ?c rdfs:label ?ctry ' \
             'FILTER(lang(?ctry)="en") } LIMIT 1' % qid
        b2 = net.wikidata_sparql(q2).get("results", {}).get("bindings", [])
        out["by_name_country"] = b2[0]["ctry"]["value"] if b2 else None
    out["collision"] = bool(out.get("by_name_country")
                            and out.get("by_name_country") != "Colombia")
    return out


# -------------------------------------------------------------------- history
def hist_awards():
    """List EVERY P166 value, in claim order, for the history winner."""
    name = "Aage Niels Bohr"
    hits = net.wikidata_search(name).get("search", [])
    if not hits:
        return {"error": "no wikidata hit"}
    qid = hits[0]["id"]
    ent = net.wikidata_entity(qid)
    claims = ent.get("entities", {}).get(qid, {}).get("claims", {})
    awards = []
    for i, c in enumerate(claims.get("P166", [])):
        dv = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(dv, dict) and dv.get("entity-type") == "item":
            tgt = dv["id"]
            sub = net.wikidata_entity(tgt)
            lab = (sub.get("entities", {}).get(tgt, {})
                   .get("labels", {}).get("en", {}).get("value"))
            awards.append({"claim_index": i, "qid": tgt, "label": lab})
    nobel = [a for a in awards if "nobel" in (a["label"] or "").lower()]
    return {"person": name, "qid": qid, "n_p166": len(awards),
            "awards_in_claim_order": awards,
            "nobel_present": bool(nobel),
            "nobel_claim_index": nobel[0]["claim_index"] if nobel else None,
            "first_claim_is_nobel": bool(awards) and "nobel" in (awards[0]["label"] or "").lower(),
            "diagnosis": ("helper returns claim 0 only; Nobel sits deeper"
                          if nobel and nobel[0]["claim_index"] != 0
                          else "no Nobel claim on this item")}


# ---------------------------------------------------------------- video games
_APPIDS = (3830, 6910, 8930, 22380, 105600, 250900, 39210,
           1091500, 292030, 367520, 620, 570)


def _steam_raw(appid):
    js = net.get_json(
        f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=us&l=en",
        timeout=90)
    d = js.get(str(appid), {})
    if not d.get("success"):
        return None
    dat = d["data"]
    return {"name": dat.get("name"),
            "date_str": (dat.get("release_date") or {}).get("date"),
            "coming_soon": (dat.get("release_date") or {}).get("coming_soon")}


def vg_offsets():
    """Signed Steam-minus-Wikidata day offset for every pinned appid."""
    rows = []
    for a in _APPIDS:
        rec = {"appid": a}
        try:
            s = _steam_raw(a)
            if not s:
                rec["error"] = "steam success=false"
                rows.append(rec)
                continue
            rec.update(s)
            tup = ct._steam_date(s["date_str"])
            rec["steam_date"] = f"{tup[0]:04d}-{tup[1]:02d}-{tup[2]:02d}" if tup else None
            val, qid = ct._wikidata_value(s["name"], "P577")
            rec["wd_qid"] = qid
            rec["wd_raw"] = val
            m = re.match(r"^\+?(\d{4})-(\d{2})-(\d{2})", str(val or ""))
            rec["wd_date"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
            if rec["steam_date"] and rec["wd_date"]:
                sd = dt.date(*map(int, rec["steam_date"].split("-")))
                wd = dt.date(*map(int, rec["wd_date"].split("-")))
                rec["offset_days"] = (sd - wd).days
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(rec)
        print("   ", rec.get("name"), rec.get("steam_date"), rec.get("wd_date"),
              rec.get("offset_days"), rec.get("error", ""))
    offs = [r["offset_days"] for r in rows if r.get("offset_days") is not None]
    hist = {}
    for o in offs:
        hist[str(o)] = hist.get(str(o), 0) + 1
    return {"rows": rows, "n_comparable": len(offs), "offset_histogram": hist,
            "n_exact": sum(1 for o in offs if o == 0),
            "n_off_by_one": sum(1 for o in offs if abs(o) == 1),
            "n_off_more": sum(1 for o in offs if abs(o) > 1),
            "verdict": ("release date is not atomic across operators"
                        if sum(1 for o in offs if o != 0) > 1
                        else "single-title discrepancy, not systematic")}


if __name__ == "__main__":
    step("geography_by_answer", geo_by_answer)
    step("history_awards", hist_awards)
    step("video_games_offsets", vg_offsets)
    print("\nwrote", OUT)
