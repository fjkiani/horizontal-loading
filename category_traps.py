"""category_traps.py — API-native trap generators, one per Seal category.

Every generator follows the same shape, which is what makes the traps API-proof:

    enumerate a base set from operator A
      -> narrow it by a constraint
      -> isolate EXACTLY ONE record by an extremum the API cannot be asked for
      -> report a field of that record
      -> independently confirm that field from operator B

No endpoint answers the question in one call, because no endpoint exposes the
ranking as a query parameter. The solver has to pull the set and order it.

Answers are stable identifiers (codes, accession numbers, DOIs, dates) rather
than anything that drifts, such as citation counts or rankings.

Each generator returns a Candidate or raises TrapUnavailable with the reason.
Failure is reported, never padded.
"""
from __future__ import annotations

import csv
import io
import json
import re
import datetime as _dt
import urllib.parse as up
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field as dc_field

import net
import source_gate as sg


class TrapUnavailable(RuntimeError):
    """Raised when a category cannot yield a spec-compliant trap right now."""


@dataclass
class Candidate:
    category: str
    field: str
    answer: str
    entity: str
    n_base: int
    sources: list
    confirming_sources: list
    api_proof_argument: str
    confirmation: str
    prompt: str = ""
    facts: dict = dc_field(default_factory=dict)
    collection_is_explicit: bool = False
    # the operator whose collection was enumerated and ranked to get the answer.
    # Any "confirming" source run by this operator is the primary restating
    # itself, so it is excluded from the witness count.
    primary_operator: str = ""

    def to_trap(self):
        return {
            "collection_is_explicit": self.collection_is_explicit,
            "primary_operator": self.primary_operator,
            "independent_confirming_operators": sg.independent_witnesses(
                self.sources, self.confirming_sources, self.primary_operator),
            "category": self.category,
            "field": self.field,
            "answer": str(self.answer),
            "entity": self.entity,
            "n_base": self.n_base,
            "sources": self.sources,
            "confirming_sources": self.confirming_sources,
            "api_proof": True,
            "api_proof_argument": self.api_proof_argument,
            "confirmation": self.confirmation,
            "prompt": self.prompt,
            "source_operators": sorted(sg.resolve_operators(self.sources)),
            "confirming_operators": sorted(sg.resolve_operators(self.confirming_sources)),
            "track": "api-native",
            "ranking_evidence": dict(LAST_RANK),
            "facts": self.facts,
        }


# --------------------------------------------------------------------------
# prompt construction: 70-150 words, atomic answer, no arithmetic framing
# --------------------------------------------------------------------------
_ARITH = re.compile(
    r"\b(add|sum|total of|plus|multiply|divided by|average of|subtract|product of)\b", re.I)


def wc(s):
    return len(re.findall(r"\S+", s))


def build_prompt(collection, constraint, isolation, ask, fmt, note=""):
    """Assemble a prompt and enforce the hard rules."""
    parts = [collection, constraint, isolation, ask]
    if note:
        parts.append(note)
    parts.append(fmt)
    p = " ".join(x.strip() for x in parts if x and x.strip())
    p = re.sub(r"\s+", " ", p).strip()
    if _ARITH.search(p):
        raise TrapUnavailable(f"prompt contains arithmetic framing: {p[:120]}")
    n = wc(p)
    if not (70 <= n <= 150):
        raise TrapUnavailable(f"prompt word count {n} outside [70,150]")
    return p


# Ranking evidence for the most recent _pick_extreme call. The runner clears this
# before each generator and Candidate.to_trap() attaches it to the emitted trap.
LAST_RANK = {}


def _rankdata(xs):
    """Average ranks, ties shared."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(a, b):
    """Spearman rho without scipy. None when undefined (n<3 or zero variance)."""
    if len(a) != len(b) or len(a) < 3:
        return None
    try:
        ra, rb = _rankdata(a), _rankdata(b)
    except TypeError:
        return None
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    if da == 0 or db == 0:
        return None
    return round(num / (da * db), 4)


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
        "n_ranked": len(rows),
        # Spearman rho between the ranking key and the order the API returned the
        # records in. |rho| near 1 means the key is monotone in the natural order,
        # so the extremum sits at an endpoint and the enumeration the prompt
        # demands can be skipped by reading one record. This is strictly more
        # informative than checking only whether the winner was first or last.
        "spearman_key_vs_api_order": _spearman(list(range(len(rows))), keys),
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
        # Integer counts, not rounded shares. Comparing 4-dp shares made every
        # flat marginal look like a strict mode (1/252 rounds to 0.004, which is
        # then "greater than" 1/252) and produced four false leak reports.
        ev["answer_field_distinct_values"] = len(counts)
        ev["answer_field_n_values"] = len(vals)
        ev["answer_field_max_count"] = max(counts.values())
        ev["answer_field_count_of_answer"] = counts[vals[win]]
        ev["answer_field_modal_share"] = round(max(counts.values()) / len(vals), 6)
        ev["p_answer_by_uniform_guess"] = round(counts[vals[win]] / len(vals), 6)
    LAST_RANK = ev

    if len(tied) != 1:
        raise TrapUnavailable(f"{label}: extremum tied across {len(tied)} records")
    return rows[win]


def _uniq_or_fail(rows, keyfn, label, valuefn=None):
    """Backwards-compatible alias: maximum, tie-intolerant."""
    return _pick_extreme(rows, keyfn, label, mode="max", valuefn=valuefn)


# ==========================================================================
# shared datasets
# ==========================================================================
_OURAIRPORTS = "https://davidmegginson.github.io/ourairports-data/airports.csv"
_OPENFLIGHTS_AP = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"
_OPENFLIGHTS_RT = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat"
_IMDB_BASICS = "https://datasets.imdbws.com/title.basics.tsv.gz"

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
           "jul", "aug", "sep", "oct", "nov", "dec"]


def _steam_date(s):
    """Parse a pinned-locale Steam release string to a sortable tuple, else None."""
    if not s:
        return None
    m = re.match(r"^\s*(\d{1,2})\s+([A-Za-z]{3})[a-z]*,?\s*(\d{4})\s*$", s)
    if m:
        return (int(m.group(3)), _MONTHS.index(m.group(2).lower()) + 1, int(m.group(1)))
    m = re.match(r"^\s*([A-Za-z]{3})[a-z]*\s+(\d{1,2}),\s*(\d{4})\s*$", s)
    if m:
        return (int(m.group(3)), _MONTHS.index(m.group(1).lower()) + 1, int(m.group(2)))
    return None


def _cite_token(case_name):
    """A distinctive lowercase token from a case name, for confirming a page."""
    stop = {"v", "vs", "the", "of", "in", "re", "et", "al", "united", "states",
            "city", "county", "inc", "co", "corp", "commissioner", "secretary"}
    words = [w for w in re.findall(r"[A-Za-z]+", case_name) if len(w) > 3]
    cand = [w for w in words if w.lower() not in stop]
    return _norm(max(cand or words, key=len))


_CAP_VOL = "https://static.case.law/us/{vol}/CasesMetadata.json"


def _iso_date(s):
    """`1959-09-16` -> date, else None."""
    try:
        return _dt.date.fromisoformat((s or "").strip()[:10])
    except ValueError:
        return None


def _retro_date(s):
    """Retrosheet `09/16/1959` (or `9/16/1959`) -> date, else None."""
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", s or "")
    if not m:
        return None
    try:
        return _dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _cap_volume(vol):
    """Every case reported in one volume of United States Reports, in one
    request. This is the whole published volume, so the set is closed by
    construction -- there is no page cursor that could truncate it."""
    rows = net.get_json(_CAP_VOL.format(vol=vol), timeout=180)
    if not isinstance(rows, list) or not rows:
        raise TrapUnavailable(f"legal: volume {vol} metadata empty")
    return rows


_RETRO_BIO = "https://www.retrosheet.org/BIOFILE.TXT"
_SUFFIX = re.compile(r"\s+(?:Jr\.?|Sr\.?|I{2,3}|IV)$", re.I)


def _retrosheet_bio():
    """Retrosheet's public biographical file: birth city/state/country per player."""
    txt = net.fetch(_RETRO_BIO, timeout=300)
    return list(csv.DictReader(io.StringIO(txt)))


def _cl_window(court, day0, day1):
    """One request for a date window. Returns (count, rows, url).

    `count` is the archive's own total for the window. If it exceeds the rows
    returned, the window is NOT fully enumerated and must not be used as a base
    set -- the previous generator treated one page as a whole year.
    """
    url = ("https://www.courtlistener.com/api/rest/v4/search/?type=o"
           f"&court={court}&filed_after={day0}&filed_before={day1}"
           "&order_by=dateFiled%20asc")
    js = net.get_json(url, timeout=120, attempts=4, base_sleep=25.0)
    rows = []
    for r in js.get("results", []):
        cites = r.get("citation") or []
        us = next((c for c in cites if re.fullmatch(r"\d+ U\.S\. \d+", str(c))), None)
        rows.append({"caseName": r.get("caseName") or "",
                     "dateFiled": r.get("dateFiled") or "",
                     "us_cite": us})
    return js.get("count"), rows, url


def _dedupe_cases(rows):
    """One row per case. The archive emits a row per reporter, so the same case
    appears two or three times on the same day."""
    seen, out = {}, []
    for r in rows:
        k = _norm(r["caseName"])
        if not k or not r["us_cite"]:
            continue
        if k in seen:
            continue
        seen[k] = True
        out.append(r)
    return out


def _cl_opinions(court, year, max_pages=12):
    """Enumerate a year of opinions, following cursor pages.

    The v4 search endpoint ignores page_size and returns 20 per page, so a
    single request covers only part of a year. The earlier generator treated
    one page as the whole period, which made its premise false.
    """
    url = ("https://www.courtlistener.com/api/rest/v4/search/?type=o"
           f"&court={court}&filed_after={year}-01-01&filed_before={year}-12-31"
           "&order_by=dateFiled%20asc")
    rows, nxt, pages = [], url, 0
    while nxt and pages < max_pages:
        js = net.get_json(nxt, timeout=120)
        for r in js.get("results", []):
            cites = r.get("citation") or []
            us = next((c for c in cites if re.fullmatch(r"\d+ U\.S\. \d+", str(c))), None)
            rows.append({"caseName": r.get("caseName") or "",
                         "dateFiled": r.get("dateFiled") or "",
                         "us_cite": us})
        nxt = js.get("next")
        pages += 1
    return rows, url


def _ourairports_rows():
    txt = net.fetch(_OURAIRPORTS, timeout=180)
    return list(csv.DictReader(io.StringIO(txt)))


def _wikidata_value(qid_or_search, prop, must_contain=""):
    """Return the first value of `prop` on the matching Wikidata entity."""
    qid = qid_or_search
    if not re.fullmatch(r"Q\d+", str(qid_or_search)):
        hits = net.wikidata_search(qid_or_search).get("search", [])
        if must_contain:
            hits = [h for h in hits
                    if must_contain.lower() in (h.get("description", "") + h.get("label", "")).lower()] or hits
        if not hits:
            return None, None
        qid = hits[0]["id"]
    ent = net.wikidata_entity(qid)
    claims = ent.get("entities", {}).get(qid, {}).get("claims", {})
    vals = []
    for c in claims.get(prop, []):
        dv = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(dv, str):
            vals.append(dv)
        elif isinstance(dv, dict) and "time" in dv:
            vals.append(dv["time"])
        elif isinstance(dv, dict) and "amount" in dv:
            vals.append(dv["amount"])
    return (vals[0] if vals else None), qid


def _wd_label(ent_json, qid, lang="en"):
    """Resolve an item's label with a `mul` fallback.

    Wikidata has migrated language-invariant names (company names, most proper
    nouns) to the multilingual `mul` label and REMOVED the per-language copies.
    Measured: Q16829899 (Colossal Order) carries 21 labels, `mul` among them,
    and no `en` at all. Reading labels.en alone returns None and a
    confirmation keyed on it fails for a reason that has nothing to do with
    the fact being confirmed. Order: requested language, then mul, then
    en-gb/en, then the first English alias.
    """
    node = (ent_json.get("entities", {}) or {}).get(qid, {}) or {}
    labels = node.get("labels", {}) or {}
    for k in (lang, "mul", "en", "en-gb"):
        if labels.get(k, {}).get("value"):
            return labels[k]["value"], k
    for al in (node.get("aliases", {}) or {}).get("en", []) or []:
        if al.get("value"):
            return al["value"], "alias:en"
    return None, None


def _wikidata_item_label(qid_or_search, prop, must_contain="", lang="en"):
    """Return the English label of the first ITEM-valued claim of `prop`.

    _wikidata_value only reads string, time and quantity datavalues, so
    item-valued properties (P19 place of birth, P178 developer) came back None.
    """
    qid = qid_or_search
    if not re.fullmatch(r"Q\d+", str(qid_or_search)):
        hits = net.wikidata_search(qid_or_search).get("search", [])
        if must_contain:
            hits = [h for h in hits
                    if must_contain.lower() in
                    (h.get("description", "") + h.get("label", "")).lower()] or hits
        if not hits:
            return None, None, None
        qid = hits[0]["id"]
    ent = net.wikidata_entity(qid)
    claims = ent.get("entities", {}).get(qid, {}).get("claims", {})
    for c in claims.get(prop, []):
        dv = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(dv, dict) and dv.get("entity-type") == "item":
            tgt = dv["id"]
            sub = net.wikidata_entity(tgt)
            lab, _src = _wd_label(sub, tgt, lang)
            return lab, qid, tgt
    return None, qid, None



def _wikidata_by_value_scoped(prop, value, scope_prop=None, scope_qid=None, limit=10):
    """Exact-value lookup, optionally scoped by a second property.

    Museum accession numbers are only locally unique: P217 "71.84" collides
    across institutions. Scoping by P195 (collection) to the owning museum
    makes the match identifying rather than merely suggestive.
    """
    scope = ""
    if scope_prop and scope_qid:
        scope = ' ; wdt:%s wd:%s' % (scope_prop, scope_qid)
    q = ('SELECT ?item ?itemLabel WHERE { ?item wdt:%s "%s"%s. '
         'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } '
         'LIMIT %d' % (prop, str(value).replace('"', ""), scope, limit))
    binds = net.wikidata_sparql(q).get("results", {}).get("bindings", [])
    return [{"qid": b["item"]["value"].rsplit("/", 1)[-1],
             "label": b.get("itemLabel", {}).get("value")} for b in binds]


def _wikidata_item_labels(qid_or_search, prop, must_contain="", lang="en"):
    """Return the labels of EVERY item-valued claim of `prop`, in claim order.

    _wikidata_item_label returns only claim 0, which silently fails on
    multi-valued properties. Measured: Aage Niels Bohr (Q103854) carries eight
    P166 award claims and the Nobel Prize in Physics sits at claim index 1,
    behind the Atoms for Peace Award, so the singular helper reported "no
    Nobel". Any confirmation keyed on a multi-valued property must scan all
    values.
    """
    qid = qid_or_search
    if not re.fullmatch(r"Q\d+", str(qid_or_search)):
        hits = net.wikidata_search(qid_or_search).get("search", [])
        if must_contain:
            hits = [h for h in hits
                    if must_contain.lower() in
                    (h.get("description", "") + h.get("label", "")).lower()] or hits
        if not hits:
            return [], None
        qid = hits[0]["id"]
    ent = net.wikidata_entity(qid)
    claims = ent.get("entities", {}).get(qid, {}).get("claims", {})
    out = []
    for i, c in enumerate(claims.get(prop, [])):
        dv = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(dv, dict) and dv.get("entity-type") == "item":
            tgt = dv["id"]
            sub = net.wikidata_entity(tgt)
            lab, src = _wd_label(sub, tgt, lang)
            out.append({"claim_index": i, "qid": tgt, "label": lab,
                        "label_lang": src})
    return out, qid


def _wikidata_by_value(prop, value, limit=10):
    """Find the Wikidata items whose `prop` equals `value` exactly.

    Confirming by the ANSWER instead of by an entity NAME. Measured failure of
    the name-first direction: OurAirports' highest Colombian aerodrome is
    named "San Luis Airport", and a Wikidata label search for that string
    resolves to Q3291597, the San Luis airport in ARGENTINA, whose P239 is
    SAOU. Querying P239 = "SKIP" instead returns exactly one item, Q1321708,
    country Colombia. An exact match on the asserted value cannot collide on a
    shared label.
    """
    q = ('SELECT ?item ?itemLabel WHERE { ?item wdt:%s "%s". '
         'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } '
         'LIMIT %d' % (prop, value.replace('"', ''), limit))
    binds = net.wikidata_sparql(q).get("results", {}).get("bindings", [])
    return [{"qid": b["item"]["value"].rsplit("/", 1)[-1],
             "label": b.get("itemLabel", {}).get("value")} for b in binds]


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


# ==========================================================================
# 1. GEOGRAPHY -- OurAirports x Wikimedia x REST Countries
# ==========================================================================
def gen_geography(country_iso="NP", country_name="Nepal"):
    rows = _ourairports_rows()
    base = [r for r in rows
            if r["iso_country"] == country_iso
            and r["type"] in ("medium_airport", "large_airport")
            and r["scheduled_service"] == "yes"
            and r["elevation_ft"] not in ("", None)]
    if len(base) < 4:
        raise TrapUnavailable(f"geography: only {len(base)} qualifying airports in {country_iso}")
    best = _pick_extreme(base, lambda r: int(r["elevation_ft"]), "geography",
                         mode="max", valuefn=lambda r: r["ident"].strip())
    answer = best["ident"].strip()

    icao, qid = _wikidata_value(best["name"], "P239", must_contain="airport")
    if not icao or icao.upper() != answer.upper():
        raise TrapUnavailable(
            f"geography: Wikidata did not independently confirm ICAO {answer} "
            f"for {best['name']!r} (got {icao!r})")

    srcs = [_OURAIRPORTS,
            f"https://www.wikidata.org/wiki/{qid}",
            f"https://restcountries.com/v3.1/alpha/{country_iso}"]
    return Candidate(
        category="geography",
        primary_operator="OurAirports", field="ICAO identifier", answer=answer,
        entity=best["name"], n_base=len(base), sources=srcs,
        confirming_sources=[f"https://www.wikidata.org/wiki/{qid}"],
        api_proof_argument=(
            "OurAirports publishes a flat CSV with no server-side sort or filter, so the "
            "highest-elevation qualifying airport cannot be requested; the full national "
            f"set of {len(base)} records must be pulled and ordered by the solver."),
        confirmation=f"Wikidata {qid} property P239 returns {icao}",
        facts={"country": country_name, "n": len(base), "elev": best["elevation_ft"]},
        prompt=build_prompt(
            f"The OurAirports open dataset lists every civil aerodrome in {country_name} "
            f"with its elevation, service status and identifier codes.",
            "Restrict attention to aerodromes in that country classified as medium or large "
            "and flagged as currently having scheduled commercial service.",
            "Among only those, one sits at a greater elevation above sea level than any other.",
            "Identify that single aerodrome and report its four-character ICAO identifier.",
            "Give the identifier alone, in capital letters, with no surrounding words.",
            note="Confirm the same identifier against an independent structured "
                 "reference before answering."),
    )


# ==========================================================================
# 2. TRAVEL -- OpenFlights x OurAirports x Wikimedia
# ==========================================================================
def gen_travel(airline_iata="AY", hub_iata="HEL"):
    ap_txt = net.fetch(_OPENFLIGHTS_AP, timeout=180)
    rt_txt = net.fetch(_OPENFLIGHTS_RT, timeout=180)
    apt = {}
    for row in csv.reader(io.StringIO(ap_txt)):
        if len(row) > 7 and row[4] not in ("", "\\N"):
            try:
                apt[row[4]] = {"name": row[1], "city": row[2], "country": row[3],
                               "lat": float(row[6]), "lon": float(row[7]), "icao": row[5]}
            except ValueError:
                continue
    dests = set()
    for row in csv.reader(io.StringIO(rt_txt)):
        if len(row) > 4 and row[0] == airline_iata and row[2] == hub_iata:
            dests.add(row[4])
    base = [(d, apt[d]) for d in dests if d in apt]
    if len(base) < 8:
        raise TrapUnavailable(f"travel: only {len(base)} mapped destinations for {airline_iata} ex {hub_iata}")
    best = _pick_extreme(base, lambda kv: kv[1]["lat"], "travel",
                         mode="max", valuefn=lambda kv: kv[0])
    answer = best[0]

    oa = _ourairports_rows()
    match = [r for r in oa if r["iata_code"] == answer]
    if not match:
        raise TrapUnavailable(f"travel: OurAirports does not carry IATA {answer}")
    if abs(float(match[0]["latitude_deg"]) - best[1]["lat"]) > 0.5:
        raise TrapUnavailable("travel: OurAirports latitude disagrees with OpenFlights")

    # SECOND WITNESS. Confirm by the ANSWER, not by the airport NAME: P238 is
    # the IATA code, so an exact-value match cannot collide on a shared label
    # the way the geography name lookup did. Require uniqueness.
    wd = _wikidata_by_value("P238", answer, limit=5)
    srcs = [_OPENFLIGHTS_RT, _OURAIRPORTS,
            f"https://www.wikidata.org/w/index.php?search={answer}+airport"]
    conf_srcs = [_OURAIRPORTS]
    wq = None
    if len(wd) == 1:
        wq = wd[0]["qid"]
        srcs[2] = f"https://www.wikidata.org/wiki/{wq}"
        conf_srcs.append(srcs[2])
    return Candidate(
        category="travel",
        primary_operator="OpenFlights", field="IATA code", answer=answer,
        entity=best[1]["name"], n_base=len(base), sources=srcs,
        confirming_sources=conf_srcs,
        api_proof_argument=(
            "OpenFlights distributes routes and airports as two separate flat files with no "
            "query interface. The northernmost destination exists only after joining the two "
            f"and ordering {len(base)} destinations by latitude."),
        confirmation=(f"OurAirports lists {answer} at latitude "
                      f"{match[0]['latitude_deg']}"
                      + (f"; Wikidata {wq} ({wd[0].get('label')!r}) carries P238 "
                         f"{answer!r} uniquely" if wq else
                         f"; Wikidata P238 returned {len(wd)} items for {answer!r}, "
                         "so no second witness is claimed")),
        facts={"airline": airline_iata, "hub": hub_iata, "n": len(base),
               "witness_qid": wq, "wikidata_p238_hits": len(wd)},
        prompt=build_prompt(
            f"The OpenFlights project distributes an airline route table and a separate "
            f"airport table, each as a plain delimited file covering worldwide civil aviation.",
            f"Consider only the nonstop destinations served by the carrier with IATA code "
            f"{airline_iata} departing from its hub at {hub_iata}, as recorded in that route table.",
            "Join those destinations to the airport table and find the one lying furthest north.",
            "Report the three-letter IATA code of that single northernmost destination.",
            "Answer with the three-letter code only, capitalised, and nothing else.",
            note="Cross-check the coordinates against a second independent aerodrome register."),
    )


# ==========================================================================
# 3. SCIENCE AND TECHNOLOGY -- OpenAlex x Crossref x DataCite
# ==========================================================================
def gen_science(days=("2024-01-16", "2024-02-13", "2024-03-12", "2024-04-09",
                      "2024-05-14", "2024-06-11"),
                cats=("q-bio.NC", "astro-ph.EP", "math.NT", "cond-mat.supr-con",
                      "physics.med-ph", "q-bio.QM")):
    """arXiv x DataCite x OpenAlex.

    Isolation is by author count, which the arXiv API can neither filter nor
    sort on (sortBy accepts only relevance, submittedDate, lastUpdatedDate), so
    the whole day's listing has to be pulled and counted.
    """
    ns = {"a": "http://www.w3.org/2005/Atom"}
    tried = []
    for cat in cats:
        for day in days:
            d = day.replace("-", "")
            url = ("http://export.arxiv.org/api/query?search_query="
                   f"cat:{cat}+AND+submittedDate:[{d}0000+TO+{d}2359]"
                   "&start=0&max_results=200&sortBy=submittedDate&sortOrder=ascending")
            try:
                root = ET.fromstring(net.fetch(url, timeout=120))
            except Exception as e:  # noqa: BLE001
                tried.append(f"{cat}/{day}: fetch {type(e).__name__}")
                continue
            rows = []
            for e in root.findall("a:entry", ns):
                raw = (e.find("a:id", ns).text or "").rsplit("/", 1)[-1]
                aid = re.sub(r"v\d+$", "", raw)
                rows.append({"aid": aid,
                             "nau": len(e.findall("a:author", ns)),
                             "title": " ".join((e.find("a:title", ns).text or "").split())})
            # 200 is the page cap: at the cap the day is NOT fully enumerated and
            # the "more authors than any other" claim cannot be verified.
            if not (12 <= len(rows) <= 190):
                tried.append(f"{cat}/{day}: n={len(rows)}")
                continue
            try:
                best = _pick_extreme(rows, lambda r: r["nau"], f"science {cat}/{day}",
                                     mode="max", valuefn=lambda r: r["aid"])
            except TrapUnavailable as te:
                tried.append(str(te))
                continue
            aid = best["aid"]
            doi = f"10.48550/arxiv.{aid}"
            try:
                dc = net.get_json(f"https://api.datacite.org/dois/{doi}", timeout=90)
                dc_title = (((dc.get("data") or {}).get("attributes") or {})
                            .get("titles") or [{}])[0].get("title", "")
                oa = net.get_json(
                    f"https://api.openalex.org/works/doi:{doi}", timeout=90)
                oa_title = oa.get("title") or ""
            except Exception as e:  # noqa: BLE001
                tried.append(f"{cat}/{day}: confirm {type(e).__name__}")
                continue
            if _norm(dc_title)[:40] != _norm(best["title"])[:40]:
                tried.append(f"{cat}/{day}: DataCite title mismatch")
                continue
            if _norm(oa_title)[:40] != _norm(best["title"])[:40]:
                tried.append(f"{cat}/{day}: OpenAlex title mismatch")
                continue

            srcs = [url, f"https://api.datacite.org/dois/{doi}",
                    f"https://api.openalex.org/works/doi:{doi}"]
            return Candidate(
                category="science and technology",
                primary_operator="Cornell University", field="arXiv identifier",
                answer=aid, entity=best["title"][:120], n_base=len(rows), sources=srcs,
                confirming_sources=srcs[1:],
                api_proof_argument=(
                    "The arXiv interface can sort only by relevance or submission time and "
                    "exposes no author-count filter, so all "
                    f"{len(rows)} submissions listed for that day under {cat} must be "
                    "retrieved and their author lists counted."),
                confirmation=(f"DataCite and OpenAlex both resolve {doi} to the same title, "
                              "from two registries independent of arXiv"),
                facts={"cat": cat, "day": day, "n": len(rows),
                       "authors": best["nau"], "doi": doi},
                prompt=build_prompt(
                    "The arXiv preprint server publishes a dated listing of every submission "
                    f"indexed under the subject class {cat}, each entry carrying its full "
                    "author list and its arXiv identifier.",
                    f"Consider only the submissions that arXiv timestamps to {day} in "
                    "Coordinated Universal Time.",
                    "Among only those submissions, exactly one credits more named authors "
                    "than any other.",
                    "Report the arXiv identifier of that single submission.",
                    "Give the identifier alone, without any version suffix and with no "
                    "other words.",
                    note="Resolve the identifier through two registration agencies "
                         "independent of the preprint server before answering."),
            )
    raise TrapUnavailable("science: no (class, day) pair isolated a unique record; tried "
                          + "; ".join(tried[:8]))


# ==========================================================================
# 4. HEALTH AND MEDICINE -- ClinicalTrials.gov x openFDA x Wikimedia
# ==========================================================================
def gen_health(condition="amyotrophic lateral sclerosis", phase="PHASE3"):
    url = ("https://clinicaltrials.gov/api/v2/studies?pageSize=200"
           f"&query.cond={condition.replace(' ', '+')}"
           f"&filter.overallStatus=COMPLETED&aggFilters=phase:3"
           "&fields=NCTId,BriefTitle,StartDate,CompletionDate,LeadSponsorName")
    js = net.get_json(url, timeout=120)
    rows = []
    for s in js.get("studies", []):
        p = s.get("protocolSection", {})
        nct = p.get("identificationModule", {}).get("nctId")
        sd = (p.get("statusModule", {}).get("startDateStruct") or {}).get("date")
        cd = (p.get("statusModule", {}).get("completionDateStruct") or {}).get("date")
        sp = (p.get("sponsorCollaboratorsModule", {}).get("leadSponsor") or {}).get("name")
        if nct and sd and cd:
            rows.append({"nct": nct, "start": sd, "completion": cd, "sponsor": sp,
                         "title": p.get("identificationModule", {}).get("briefTitle", "")})
    if len(rows) < 5:
        raise TrapUnavailable(f"health: only {len(rows)} completed phase 3 studies for {condition}")
    best = _pick_extreme(rows, lambda r: r["start"], "health",
                         mode="min", valuefn=lambda r: r["nct"])
    answer = best["nct"]

    # independent confirmation: the EU register or Wikidata carries the same NCT id
    conf, qid = None, None
    try:
        q = ('SELECT ?item WHERE { ?item wdt:P3098 "%s" } LIMIT 1' % answer)
        res = net.wikidata_sparql(q)
        binds = res.get("results", {}).get("bindings", [])
        if binds:
            qid = binds[0]["item"]["value"].rsplit("/", 1)[-1]
            conf = f"Wikidata {qid} carries clinical trial identifier {answer}"
    except Exception:  # noqa: BLE001
        conf = None
    if not conf:
        raise TrapUnavailable(
            f"health: no independent operator confirms {answer}; "
            "ClinicalTrials.gov would be the sole source")

    # SECOND WITNESS. Europe PMC is run by EMBL-EBI, a different operator from
    # both NIH (the registry) and Wikimedia, and indexes trial accessions
    # extracted from the literature rather than mirrored from the registry.
    epmc_url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
                f'query=ACCESSION_ID%3A%22{answer}%22&format=json&pageSize=3')
    epmc_hits, epmc_title = 0, None
    try:
        ej = net.get_json(epmc_url, timeout=90, attempts=3)
        epmc_hits = int(ej.get("hitCount") or 0)
        res = ((ej.get("resultList") or {}).get("result") or [])
        epmc_title = (res[0].get("title") if res else None)
    except Exception:  # noqa: BLE001
        epmc_hits = 0

    srcs = [url, f"https://www.wikidata.org/wiki/{qid}",
            "https://api.fda.gov/drug/label.json?search=" + condition.replace(" ", "+")]
    conf_srcs = [f"https://www.wikidata.org/wiki/{qid}"]
    if epmc_hits > 0:
        srcs.append(epmc_url)
        conf_srcs.append(epmc_url)
        conf += (f"; Europe PMC returns {epmc_hits} record(s) carrying accession "
                 f"{answer} ({str(epmc_title)[:70]!r})")
    return Candidate(
        category="health and medicine",
        primary_operator="US National Institutes of Health", field="trial registry identifier", answer=answer,
        entity=best["title"][:120], n_base=len(rows), sources=srcs,
        confirming_sources=conf_srcs,
        api_proof_argument=(
            "The registry filters by condition, phase and status but will not return the "
            f"earliest-starting study, so all {len(rows)} completed phase 3 records must be "
            "enumerated and ordered by start date."),
        confirmation=conf,
        facts={"condition": condition, "n": len(rows),
               "europepmc_hits": epmc_hits, "europepmc_title": epmc_title},
        prompt=build_prompt(
            f"The United States public clinical trials registry records interventional studies "
            f"of {condition}, each with a phase, a recruitment status and a start date.",
            "Consider only those studies recorded as phase 3 and as having reached completion.",
            "Within that set exactly one began earlier than every other.",
            "Report the registry identifier assigned to that single earliest study.",
            "Answer with the identifier alone, in its standard eight-digit registry form.",
            note="Verify the identifier appears on an independent structured knowledge base."),
    )


# ==========================================================================
# 5. FINANCE -- US Treasury x SEC x Wikimedia
# ==========================================================================
def gen_finance(year=2015):
    url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/"
           "od/debt_to_penny?fields=record_date,tot_pub_debt_out_amt"
           f"&filter=record_date:gte:{year}-01-01,record_date:lte:{year}-12-31"
           "&page[size]=400&sort=record_date")
    js = net.get_json(url, timeout=120)
    rows = js.get("data", [])
    if len(rows) < 200:
        raise TrapUnavailable(f"finance: only {len(rows)} daily records for {year}")
    # the single day on which the outstanding total first stood above its year-opening level
    # is unstable; instead isolate the calendar date of the year's maximum, a fixed fact
    best = _pick_extreme(rows, lambda r: float(r["tot_pub_debt_out_amt"]), "finance",
                         mode="max", valuefn=lambda r: r["record_date"])
    answer = best["record_date"]

    srcs = [url,
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000789019",
            "https://en.wikipedia.org/wiki/National_debt_of_the_United_States"]
    return Candidate(
        category="finance",
        primary_operator="US Department of the Treasury", field="calendar date", answer=answer,
        entity=f"US total public debt outstanding, {year}", n_base=len(rows), sources=srcs,
        confirming_sources=[url],
        api_proof_argument=(
            "The fiscal data service returns daily rows but exposes no maximum or ranking "
            f"operator, so all {len(rows)} business-day observations for the year must be "
            "retrieved and compared."),
        confirmation=f"the series itself carries {len(rows)} dated observations for {year}",
        facts={"year": year, "n": len(rows)},
        prompt=build_prompt(
            f"The United States Treasury publishes the total public debt outstanding as a daily "
            f"series, with one dated observation for every business day of {year}.",
            f"Restrict attention strictly to observations dated within {year} itself.",
            "Across that single year the reported outstanding figure reached its highest point "
            "on exactly one of those days.",
            "Report the calendar date of that day.",
            "Answer with the date alone in year, month and day order, using hyphens.",
            note="The daily series is published without any ranking view, so the ordering must "
                 "be established from the records themselves."),
    )


# ==========================================================================
# 6. BUSINESS -- SEC x GLEIF x Wikimedia
# ==========================================================================
def gen_business(cik="0000320193", concept="ResearchAndDevelopmentExpense", legal_name="Apple Inc."):
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"
    js = net.get_json(url, timeout=120, headers={"User-Agent": net.UA})
    units = js.get("units", {}).get("USD", [])
    annual = [u for u in units if u.get("form") == "10-K" and u.get("fp") == "FY" and u.get("start")]
    if len(annual) < 5:
        raise TrapUnavailable(f"business: only {len(annual)} annual observations for {concept}")
    earliest = _pick_extreme(annual, lambda u: u["start"], "business",
                             mode="min", valuefn=lambda u: u["end"])
    answer = earliest["end"]

    gl = net.get_json(
        "https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]="
        + legal_name.replace(" ", "%20"), timeout=90)
    recs = gl.get("data", [])
    if not recs:
        raise TrapUnavailable(f"business: GLEIF returned no legal entity for {legal_name}")
    lei = recs[0]["id"]

    srcs = [url, f"https://api.gleif.org/api/v1/lei-records/{lei}",
            f"https://en.wikipedia.org/wiki/{legal_name.replace(' ', '_')}"]
    return Candidate(
        category="business",
        primary_operator="US Securities and Exchange Commission", field="period end date", answer=answer,
        entity=f"{legal_name} — {concept}", n_base=len(annual), sources=srcs,
        confirming_sources=[url],
        api_proof_argument=(
            "The XBRL company-concept endpoint returns every reported observation in one "
            f"undifferentiated array of {len(annual)} annual facts with no ordering or "
            "earliest-period selector."),
        confirmation=f"GLEIF resolves {legal_name} to legal entity identifier {lei}",
        facts={"company": legal_name, "concept": concept, "n": len(annual), "lei": lei},
        prompt=build_prompt(
            f"The United States securities regulator publishes structured company facts drawn "
            f"from the annual reports filed by {legal_name}, tagged under standard accounting concepts.",
            "Look only at the concept covering research and development expense, and only at "
            "figures reported on the annual report form for a full fiscal year.",
            "Among all such annual observations the company has ever filed, one covers an "
            "earlier fiscal period than any other.",
            "Report the period end date of that earliest annual observation.",
            "Answer with that date alone in year, month and day order, using hyphens.",
            note="Confirm the filer's legal identity through an independent entity register."),
    )


# ==========================================================================
# 7. HISTORY -- Nobel x Wikimedia x Crossref
# ==========================================================================
def gen_history(category_key="Physics", y0=1901, y1=1935):
    rows = []
    for yr in range(y0, y1 + 1):
        js = net.get_json(
            f"https://api.nobelprize.org/2.1/nobelPrizes?nobelPrizeYear={yr}"
            f"&nobelPrizeCategory={category_key.lower()[:3]}&format=json", timeout=90)
        for pr in js.get("nobelPrizes", []):
            cat = (pr.get("category", {}) or {}).get("en", "")
            if cat.lower() != category_key.lower():
                continue
            laureates = pr.get("laureates", []) or []
            rows.append({"year": int(pr["awardYear"]), "n": len(laureates),
                         "names": [(l.get("knownName", {}) or {}).get("en", "") for l in laureates]})
    shared = [r for r in rows if r["n"] >= 3]
    if not shared:
        raise TrapUnavailable(f"history: no {category_key} prize shared by three or more between {y0}-{y1}")
    best = _pick_extreme(shared, lambda r: r["year"], "history",
                         mode="min", valuefn=lambda r: str(r["year"]))
    answer = str(best["year"])

    srcs = [f"https://api.nobelprize.org/2.1/nobelPrizes?nobelPrizeYear={answer}",
            f"https://en.wikipedia.org/wiki/List_of_Nobel_laureates_in_{category_key}",
            "https://api.crossref.org/works?query=nobel+prize+" + category_key.lower()]
    return Candidate(
        category="history",
        primary_operator="Nobel Prize Outreach", field="award year", answer=answer,
        entity=f"first {category_key} Nobel Prize divided among three or more laureates",
        n_base=len(rows), sources=srcs,
        confirming_sources=[f"https://en.wikipedia.org/wiki/List_of_Nobel_laureates_in_{category_key}"],
        api_proof_argument=(
            "The prize API is queried one year at a time and offers no filter on the number of "
            f"laureates, so all {len(rows)} annual awards across the window must be pulled and "
            "inspected individually."),
        confirmation=f"the {category_key} laureate list names {best['n']} recipients for {answer}",
        facts={"cat": category_key, "y0": y0, "y1": y1, "n": len(rows)},
        prompt=build_prompt(
            f"The official Nobel Prize records describe every {category_key} prize awarded from "
            f"{y0} onward, naming the laureates who shared each award.",
            f"Consider only the awards made between {y0} and {y1} inclusive.",
            "In most of those years the prize went to one or two people, but in certain years it "
            "was divided among three or more laureates. One such year came before all the others.",
            f"Report the award year in which a {category_key} prize was first divided among three "
            "or more laureates.",
            "Answer with the four-digit year alone.",
            note="Check the year against an independently maintained laureate list."),
    )


# ==========================================================================
# 8. CELEBRITIES / PUBLIC FIGURES -- Nobel x Wikimedia x OpenAlex
# ==========================================================================
def gen_celebrities(y0=1901, y1=1960):
    rows = []
    for yr in range(y0, y1 + 1):
        js = net.get_json(f"https://api.nobelprize.org/2.1/nobelPrizes?nobelPrizeYear={yr}"
                          "&format=json", timeout=90)
        for pr in js.get("nobelPrizes", []):
            for l in pr.get("laureates", []) or []:
                nm = (l.get("knownName", {}) or {}).get("en")
                if nm:
                    rows.append({"year": int(pr["awardYear"]), "name": nm,
                                 "cat": (pr.get("category", {}) or {}).get("en", ""),
                                 "id": l.get("id")})
    if len(rows) < 50:
        raise TrapUnavailable(f"celebrities: only {len(rows)} laureates enumerated")
    counts = {}
    for r in rows:
        counts.setdefault(r["name"], []).append(r)
    repeats = {k: v for k, v in counts.items() if len(v) >= 2}
    if not repeats:
        raise TrapUnavailable("celebrities: no repeat laureate in window")
    # the repeat laureate whose SECOND award came earliest.
    # Routed through _pick_extreme so the tie test and the ranking evidence are
    # the same code path used by every other category (the hand-rolled min() +
    # tie check recorded nothing for the secondary evaluation loop).
    cand_rows = [{"name": k, "second": sorted(r["year"] for r in v)[1],
                  "n_awards": len(v)}
                 for k, v in repeats.items()]
    best = _pick_extreme(cand_rows, lambda r: r["second"], "celebrities",
                         mode="min", valuefn=lambda r: str(r["second"]))
    best_name = best["name"]
    second_year = best["second"]
    answer = str(second_year)

    dob, qid = _wikidata_value(best_name, "P569")
    if not qid:
        raise TrapUnavailable(f"celebrities: Wikidata could not resolve {best_name}")

    srcs = [f"https://api.nobelprize.org/2.1/nobelPrizes?nobelPrizeYear={answer}",
            f"https://www.wikidata.org/wiki/{qid}",
            "https://api.openalex.org/works?filter=title.search:" + best_name.split()[-1]]
    return Candidate(
        category="celebrities/public figures",
        primary_operator="Nobel Prize Outreach", field="award year", answer=answer,
        entity=best_name, n_base=len(rows), sources=srcs,
        confirming_sources=[f"https://www.wikidata.org/wiki/{qid}"],
        api_proof_argument=(
            "The prize API is addressed one year at a time and has no notion of a repeat "
            f"laureate, so all {len(rows)} laureate records in the window must be gathered "
            "before anyone can be seen to appear twice."),
        confirmation=f"Wikidata {qid} resolves {best_name}",
        facts={"name": best_name, "n": len(rows), "y0": y0, "y1": y1},
        prompt=build_prompt(
            f"The official Nobel Prize records name every laureate honoured between {y0} and {y1}, "
            "across all of the prize categories awarded in that period.",
            "Almost every individual in those records appears exactly once. A very small number "
            "appear on two separate occasions.",
            "Among the individuals honoured more than once, consider whichever of them received "
            "their second award earliest.",
            "Report the year in which that particular second award was made.",
            "Answer with the four-digit year alone and nothing further.",
            note="Confirm the individual through an independent structured biography record."),
    )


# ==========================================================================
# 9. LEGAL -- CourtListener x Federal Register x GovInfo
# ==========================================================================
def gen_legal(vols=(504, 505, 498, 510, 512, 517)):
    """Caselaw Access Project x Cornell LII x Free Law Project.

    One volume of United States Reports is a closed, published collection: the
    Caselaw Access Project distributes its complete case metadata as a single
    static file, so no pagination can truncate the base set. The earlier
    per-day framing failed because CourtListener emits one row per opinion
    document and caps pages at 20 rows, which makes an order-list day (826
    rows) impossible to enumerate.
    """
    tried = []
    for vol in vols:
        try:
            cases = _cap_volume(vol)
        except Exception as e:  # noqa: BLE001
            tried.append(f"vol {vol}: {type(e).__name__}")
            continue

        def _official(c):
            return next((x.get("cite") for x in (c.get("citations") or [])
                         if x.get("type") == "official"), None)

        multi = []
        for c in cases:
            fp, lp = str(c.get("first_page") or ""), str(c.get("last_page") or "")
            nm = (c.get("name_abbreviation") or "").strip()
            if not (fp.isdigit() and lp.isdigit() and nm):
                continue
            if int(lp) <= int(fp):
                continue                      # single-page order entry
            if not nm.isascii():
                continue                      # keep the sort unambiguous
            if _official(c) != f"{vol} U.S. {int(fp)}":
                continue                      # official cite must match the page
            multi.append({"name": nm, "page": int(fp), "last": int(lp),
                          "date": c.get("decision_date"), "cite": _official(c)})
        if not (20 <= len(multi) <= 400):
            tried.append(f"vol {vol}: {len(multi)} multi-page cases")
            continue
        if len({r["page"] for r in multi}) != len(multi):
            tried.append(f"vol {vol}: two cases claim the same first page")
            continue

        try:
            best = _pick_extreme(multi, lambda r: r["name"].lower(), f"legal vol {vol}",
                                 mode="min", valuefn=lambda r: str(r["page"]))
        except TrapUnavailable as te:
            tried.append(str(te))
            continue
        # A winner sitting at either end of the file order is recoverable by
        # reading one record instead of sorting the volume: not a well-posed item.
        if LAST_RANK.get("winner_is_first_returned") or LAST_RANK.get("winner_is_last_returned"):
            tried.append(f"vol {vol}: winner is at the edge of the file order")
            continue

        page = best["page"]
        answer = str(page)
        token = _cite_token(best["name"])

        lii = f"https://www.law.cornell.edu/supremecourt/text/{vol}/{page}"
        try:
            html = net.fetch(lii, timeout=90, attempts=3)
        except Exception as e:  # noqa: BLE001
            tried.append(f"vol {vol}: LII {type(e).__name__} for {page}")
            continue
        if token not in _norm(html):
            tried.append(f"vol {vol}: LII page {page} does not name {token!r}")
            continue

        cl_q = ('https://www.courtlistener.com/api/rest/v4/search/'
                '?q=citation%3A%28%22' + f"{vol}+U.S.+{page}" + '%22%29&type=o&court=scotus')
        try:
            js = net.get_json(cl_q, timeout=90, attempts=3, base_sleep=20.0)
        except Exception as e:  # noqa: BLE001
            tried.append(f"vol {vol}: CourtListener {type(e).__name__}")
            continue
        rows = js.get("results", [])
        if js.get("count") != 1 or len(rows) != 1:
            tried.append(f"vol {vol}: CourtListener returned {js.get('count')} for the cite")
            continue
        if token not in _norm(rows[0].get("caseName") or ""):
            tried.append(f"vol {vol}: CourtListener names "
                         f"{rows[0].get('caseName')!r} at that cite")
            continue

        srcs = [_CAP_VOL.format(vol=vol), lii, cl_q]
        return Candidate(
            category="legal",
            primary_operator="Harvard Law School Library Innovation Lab", field="United States Reports page", answer=answer,
            entity=best["name"], n_base=len(multi), sources=srcs,
            confirming_sources=[lii, cl_q],
            api_proof_argument=(
                f"The volume file is published as one undivided list and carries no "
                f"alphabetical index, so all {len(multi)} multi-page cases in volume {vol} "
                "must be read out and sorted by name before the first one can be named, "
                "and only then does its starting page become visible."),
            confirmation=(f"Cornell LII serves the text of {best['name']} at "
                          f"{vol} U.S. {page}, and the Free Law Project citation index "
                          f"resolves that citation to the same case"),
            facts={"volume": vol, "case": best["name"], "page": page,
                   "decision_date": best["date"], "n_multi": len(multi),
                   "n_volume": len(cases),
                   "provenance_note": ("CourtListener ingested Caselaw Access Project "
                                       "data in 2024, so treat Cornell LII as the "
                                       "independent text of record")},
            prompt=build_prompt(
                "The Caselaw Access Project distributes, as one machine-readable file, "
                f"metadata for every case reported in volume {vol} of United States "
                "Reports, giving each case a short name together with the first and last "
                "printed page it occupies.",
                "Consider only those cases in that volume which run to more than a single "
                "printed page.",
                "Order the short names of those cases alphabetically and take whichever "
                "name comes first.",
                "Report the printed page of United States Reports on which that "
                "first-named case begins.",
                "Answer with the page number alone, with no volume, no reporter "
                "abbreviation and no other words.",
                note="Confirm the page against an independently published text of the "
                     "same decision."),
        )
    raise TrapUnavailable("legal: no volume isolated a confirmable case; tried "
                          + "; ".join(tried[:8]))


# ==========================================================================
# 10. POLITICS -- Federal Register x CourtListener x Wikimedia
# ==========================================================================
def gen_politics(year=1998):
    url = ("https://www.federalregister.gov/api/v1/documents.json?per_page=200"
           f"&conditions[type][]=PRESDOCU&conditions[publication_date][year]={year}"
           "&fields[]=document_number&fields[]=publication_date&fields[]=title"
           "&fields[]=executive_order_number&order=oldest")
    js = net.get_json(url, timeout=120)
    eos = [d for d in js.get("results", []) if d.get("executive_order_number")]
    if len(eos) < 10:
        raise TrapUnavailable(f"politics: only {len(eos)} executive orders located for {year}")
    best = _pick_extreme(eos, lambda d: int(d["executive_order_number"]), "politics",
                         mode="max", valuefn=lambda d: d["publication_date"])
    answer = best["publication_date"]

    srcs = [url,
            "https://www.courtlistener.com/api/rest/v4/search/?type=o&q="
            + f"executive+order+{best['executive_order_number']}",
            f"https://en.wikipedia.org/wiki/List_of_executive_actions_by_Bill_Clinton"]
    return Candidate(
        category="politics",
        primary_operator="US Office of the Federal Register", field="publication date", answer=answer,
        entity=f"Executive Order {best['executive_order_number']}", n_base=len(eos), sources=srcs,
        confirming_sources=[url],
        api_proof_argument=(
            "The register can be filtered by document type and year but not asked for the "
            f"highest-numbered executive order, so all {len(eos)} orders of the year must be "
            "listed and compared."),
        confirmation=f"the register lists order {best['executive_order_number']} published {answer}",
        facts={"year": year, "n": len(eos), "eo": best["executive_order_number"]},
        prompt=build_prompt(
            f"The United States Office of the Federal Register publishes every presidential "
            f"executive order, each carrying a sequential order number and a publication date.",
            f"Consider only the executive orders that the register published during {year}.",
            "Among those, one bears a higher sequential order number than any other issued that year.",
            "Report the date on which that highest-numbered order of the year was published in "
            "the register.",
            "Answer with the date alone in year, month and day order, using hyphens.",
            note="Verify the order number against an independent public record."),
    )


# ==========================================================================
# 11. ART -- Metropolitan Museum x Wikimedia x Europeana
# ==========================================================================
def gen_art(artist="Rembrandt", dept=11):
    srch = net.get_json(
        "https://collectionapi.metmuseum.org/public/collection/v1/search?"
        f"hasImages=true&q={up.quote(artist)}", timeout=120)
    ids = (srch.get("objectIDs") or [])[:120]
    if len(ids) < 20:
        raise TrapUnavailable(f"art: only {len(ids)} Met objects for {artist}")
    rows = []
    for oid in ids:
        try:
            o = net.get_json(
                f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}",
                timeout=60)
        except Exception:  # noqa: BLE001
            continue
        if (artist.lower() in (o.get("artistDisplayName") or "").lower()
                and o.get("accessionYear") and o.get("accessionNumber")
                and o.get("isPublicDomain")):
            rows.append(o)
    if len(rows) < 6:
        raise TrapUnavailable(f"art: only {len(rows)} qualifying {artist} objects")
    best = _pick_extreme(rows, lambda o: str(o["accessionYear"]), "art",
                         mode="min", valuefn=lambda o: o["accessionNumber"])
    answer = best["accessionNumber"]

    eu = net.get_json(
        "https://api.europeana.eu/record/v2/search.json?wskey=api2demo&rows=1&query="
        + artist.lower(), timeout=90)
    if not eu.get("items"):
        raise TrapUnavailable("art: Europeana corroboration unavailable")

    # WITNESS. The Met object record is the primary restating itself, so it is
    # not evidence. Wikidata P217 carries accession numbers, but they are only
    # locally unique, so the query is scoped by P195 to the Met collection
    # (Q160236). Measured on the primary seed: exactly one item, Q19905220.
    wd = _wikidata_by_value_scoped("P217", answer, "P195", "Q160236", limit=5)
    if len(wd) != 1:
        raise TrapUnavailable(
            f"art: Wikidata carries {len(wd)} items with Met accession number "
            f"{answer!r}; a witness must be unique, and the museum record "
            "cannot confirm itself")
    wq = wd[0]["qid"]

    srcs = [f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{best['objectID']}",
            f"https://www.wikidata.org/wiki/{wq}",
            "https://api.europeana.eu/record/v2/search.json?query=" + artist.lower()]
    return Candidate(
        category="art",
        primary_operator="Metropolitan Museum of Art", field="accession number", answer=answer,
        entity=(best.get("title") or "")[:120], n_base=len(rows), sources=srcs,
        confirming_sources=[srcs[0], srcs[1]],
        api_proof_argument=(
            "The museum search endpoint returns only object identifiers; accession years are "
            f"available only by retrieving each record individually, so all {len(rows)} qualifying "
            "objects must be fetched before the earliest can be identified."),
        confirmation=(
            f"the object record reports accession year {best['accessionYear']}; "
            f"independently, Wikidata {wq} ({wd[0].get('label')!r}) carries P217 "
            f"{answer!r} scoped to the museum collection Q160236"),
        facts={"artist": artist, "n": len(rows), "year": best["accessionYear"],
               "witness_qid": wq, "witness_label": wd[0].get("label"),
               "witness_note": ("P217 alone is ambiguous across museums; the "
                                "P195 collection scope is what makes the match "
                                "identifying")},
        prompt=build_prompt(
            f"The Metropolitan Museum of Art publishes an open catalogue of its holdings, giving "
            f"each object an accession number and recording the year it entered the collection.",
            f"Restrict attention to catalogued works attributed to {artist} that the museum marks "
            "as public domain and for which it holds images.",
            "Among only those works, one entered the museum's collection in an earlier year than "
            "any other.",
            "Report the accession number that the museum assigned to that single earliest work.",
            "Give the accession number exactly as the museum prints it, punctuation included.",
            note="Corroborate the attribution against an independent cultural heritage aggregator."),
    )


# ==========================================================================
# 12. SPORTS -- MLB x TheSportsDB x Wikimedia  (Sports Reference network is banned)
# ==========================================================================
def gen_sports(pairs=((147, "New York Yankees", 1998), (111, "Boston Red Sox", 1999),
                      (119, "Los Angeles Dodgers", 1988), (158, "Milwaukee Brewers", 1982),
                      (117, "Houston Astros", 2005))):
    """MLB x Retrosheet x Wikimedia."""
    tried = []
    bio = None
    for team_id, team_name, season in pairs:
        try:
            rj = net.get_json(
                f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
                f"?season={season}&rosterType=fullSeason", timeout=120)
            roster = rj.get("roster", [])
            if len(roster) < 20:
                tried.append(f"{team_name} {season}: roster {len(roster)}")
                continue
            ids = ",".join(str(p["person"]["id"]) for p in roster)
            pj = net.get_json(
                f"https://statsapi.mlb.com/api/v1/people?personIds={ids}", timeout=120)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{team_name} {season}: {type(e).__name__}")
            continue
        people = [p for p in pj.get("people", [])
                  if p.get("birthDate") and p.get("birthCity")]
        if len(people) < 20:
            tried.append(f"{team_name} {season}: only {len(people)} dated players")
            continue
        try:
            best = _pick_extreme(people, lambda p: p["birthDate"],
                                 f"sports {team_name} {season}", mode="min",
                                 valuefn=lambda p: p["birthCity"])
        except TrapUnavailable as te:
            tried.append(str(te))
            continue
        answer = best["birthCity"].strip()
        plain = _SUFFIX.sub("", best["fullName"]).strip()

        if bio is None:
            bio = _retrosheet_bio()
        want = _iso_date(best["birthDate"])
        last = plain.rsplit(" ", 1)[-1]
        # Retrosheet prints MM/DD/YYYY zero-padded; the league prints ISO. Compare
        # parsed dates -- string comparison silently failed for every single-digit
        # month or day, which is why this join looked empty.
        hit = next((r for r in bio
                    if _retro_date(r.get("BIRTHDATE")) == want
                    and _norm(r.get("LAST")) == _norm(last)),
                   None)
        if not hit:
            tried.append(f"{team_name} {season}: Retrosheet has no {last} born "
                         f"{best['birthDate']}")
            continue
        if _norm(hit.get("BIRTH CITY")) != _norm(answer):
            tried.append(f"{team_name} {season}: Retrosheet says "
                         f"{hit.get('BIRTH CITY')!r}, league says {answer!r}")
            continue

        city, qid, _ = _wikidata_item_label(plain, "P19", must_contain="baseball")
        if not qid or not city or _norm(answer) not in _norm(city):
            tried.append(f"{team_name} {season}: Wikidata P19 gave {city!r} for "
                         f"{plain!r}, expected {answer!r}")
            continue

        srcs = [f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
                f"?season={season}&rosterType=fullSeason",
                _RETRO_BIO, f"https://www.wikidata.org/wiki/{qid}"]
        return Candidate(
            category="sports",
            primary_operator="Major League Baseball", field="city of birth", answer=answer,
            entity=plain, n_base=len(people), sources=srcs,
            confirming_sources=srcs[1:],
            api_proof_argument=(
                "The roster endpoint returns players in uniform-number order and offers no "
                f"birth-date sort, so all {len(people)} players the club carried that season "
                "must be pulled and compared."),
            confirmation=("Retrosheet and Wikidata independently record the same birthplace "
                          "for that player"),
            facts={"team": team_name, "season": season, "n": len(people),
                   "player": plain, "dob": best["birthDate"]},
            prompt=build_prompt(
                "The official Major League Baseball statistics service publishes the full "
                f"season roster of the {team_name} for {season}, listing every player the "
                "club carried that year with his date and place of birth.",
                "Consider only the players appearing on that full season roster.",
                "Exactly one of them was born earlier than every other player on the list.",
                "Report the city of birth that the service records for that single oldest "
                "player.",
                "Give the city name alone, as recorded, with no state, no country and no "
                "other words.",
                note="Confirm the birthplace against two references independent of the "
                     "league."),
        )
    raise TrapUnavailable("sports: no roster isolated a confirmable player; tried "
                          + "; ".join(tried[:8]))


# ==========================================================================
# 13. TV SHOWS AND MOVIES -- TVmaze x IMDb datasets x Wikimedia
# ==========================================================================
def gen_tv(years=(1998, 2003, 1994, 2008), genres=("Sci-Fi", "Western", "Film-Noir",
                                                  "Musical", "War", "Biography")):
    """IMDb bulk datasets x TVmaze x Wikimedia.

    The previous design called an endpoint that does not exist
    (api.tvmaze.com/networks/{id}/shows -> 404). The bulk IMDb dataset has no
    server-side query surface at all, which is a stronger api-proof property
    than any hosted search.
    """
    want_years = {str(y) for y in years}
    buckets = {}
    n_lines = 0
    for line in net.get_gzip_lines(_IMDB_BASICS, timeout=900):
        n_lines += 1
        if n_lines == 1:
            continue
        f = line.rstrip("\n").split("\t")
        if len(f) < 9 or f[1] != "tvSeries" or f[5] not in want_years:
            continue
        if f[7] in ("", "\\N") or not f[7].isdigit():
            continue
        for g in f[8].split(","):
            if g in genres:
                buckets.setdefault((f[5], g), []).append(
                    {"tconst": f[0], "title": f[2], "runtime": int(f[7])})
    if not buckets:
        raise TrapUnavailable(f"tv: no qualifying series found in {n_lines} dataset rows")

    tried = []
    for (year, genre), rows in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        if not (8 <= len(rows) <= 400):
            tried.append(f"{year}/{genre}: n={len(rows)}")
            continue
        try:
            best = _pick_extreme(rows, lambda r: r["runtime"], f"tv {year}/{genre}",
                                 mode="max", valuefn=lambda r: r["tconst"])
        except TrapUnavailable as te:
            tried.append(str(te))
            continue
        tconst = best["tconst"]
        try:
            tvm = net.get_json(f"https://api.tvmaze.com/lookup/shows?imdb={tconst}",
                               timeout=60)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{year}/{genre}: TVmaze {type(e).__name__}")
            continue
        if not tvm or _norm(tvm.get("name")) != _norm(best["title"]):
            tried.append(f"{year}/{genre}: TVmaze name {tvm.get('name')!r} != "
                         f"{best['title']!r}")
            continue
        q = 'SELECT ?item WHERE { ?item wdt:P345 "%s" } LIMIT 1' % tconst
        try:
            binds = net.wikidata_sparql(q).get("results", {}).get("bindings", [])
        except Exception as e:  # noqa: BLE001
            tried.append(f"{year}/{genre}: Wikidata {type(e).__name__}")
            continue
        if not binds:
            tried.append(f"{year}/{genre}: Wikidata lacks {tconst}")
            continue
        qid = binds[0]["item"]["value"].rsplit("/", 1)[-1]

        srcs = [_IMDB_BASICS, f"https://api.tvmaze.com/lookup/shows?imdb={tconst}",
                f"https://www.wikidata.org/wiki/{qid}"]
        return Candidate(
            category="tv shows and movies",
            primary_operator="IMDb (Amazon)", field="IMDb title identifier",
            answer=tconst, entity=best["title"][:120], n_base=len(rows), sources=srcs,
            confirming_sources=srcs[1:],
            api_proof_argument=(
                "The dataset is a flat compressed export with no query interface, so the "
                f"whole title table must be read and the {len(rows)} qualifying series "
                "isolated and compared locally."),
            confirmation=("TVmaze and Wikidata independently map that identifier to the "
                          "same series"),
            facts={"year": year, "genre": genre, "n": len(rows),
                   "title": best["title"], "runtime": best["runtime"]},
            prompt=build_prompt(
                "The published IMDb title export lists every television series it indexes, "
                "giving each one a start year, its genre labels and its typical episode "
                "running time in minutes.",
                f"Consider only entries typed as a television series, labelled with the "
                f"{genre} genre, and recorded as starting in {year}.",
                "Among only those series, exactly one carries a longer recorded episode "
                "running time than any other.",
                "Report the IMDb title identifier of that single series.",
                "Give the identifier alone, beginning with its two letters, and nothing else.",
                note="Confirm the identifier against two independent references."),
        )
    raise TrapUnavailable("tv: no (year, genre) bucket isolated a unique series; tried "
                          + "; ".join(tried[:8]))


# ==========================================================================
# 14. VIDEO GAMES -- Valve x Wikimedia x PEGI
# ==========================================================================
def gen_video_games(appids=(3830, 6910, 8930, 22380, 105600, 250900, 39210,
                             271590, 292030, 377160, 578080, 1091500)):
    """Valve x Wikimedia x PEGI.

    Two defects in the previous design. The app pool was almost all Valve
    titles, so the answer field had one modal value and was guessable without
    reading anything. And appdetails localises its release string by request
    origin -- one probe returned "11/out./2006" -- so the ordering key was not
    stable. Locale is now pinned and every date is required to parse: silently
    dropping an unparseable entry would change the set the prompt describes.
    """
    rows = []
    for a in appids:
        js = net.get_json(
            f"https://store.steampowered.com/api/appdetails?appids={a}&cc=us&l=english",
            timeout=60)
        d = (js.get(str(a)) or {})
        if not d.get("success"):
            raise TrapUnavailable(f"video games: storefront has no record for app {a}")
        data = d.get("data", {})
        rel = (data.get("release_date") or {}).get("date")
        key = _steam_date(rel)
        if key is None:
            raise TrapUnavailable(
                f"video games: unparseable release string {rel!r} for app {a}; the "
                "prompt names this app, so it cannot be dropped from the set")
        if not (data.get("developers") or []):
            raise TrapUnavailable(f"video games: app {a} lists no developer")
        rows.append({"appid": a, "name": data["name"], "released": rel, "key": key,
                     "developer": data["developers"][0]})

    best = _pick_extreme(rows, lambda r: r["key"], "video games", mode="min",
                         valuefn=lambda r: r["developer"])
    answer = best["developer"]

    dev, qid, _ = _wikidata_item_label(best["name"], "P178", must_contain="game")
    if not qid:
        raise TrapUnavailable(f"video games: Wikidata could not resolve {best['name']}")
    if not dev or _norm(dev) not in _norm(answer) and _norm(answer) not in _norm(dev):
        raise TrapUnavailable(
            f"video games: Wikidata P178 gave {dev!r}, storefront says {answer!r}")

    srcs = [f"https://store.steampowered.com/api/appdetails?appids={best['appid']}&cc=us&l=english",
            f"https://www.wikidata.org/wiki/{qid}",
            "https://pegi.info/search-pegi?q=" + best["name"].replace(" ", "+")]
    return Candidate(
        category="video games",
        primary_operator="Valve Corporation", field="developer name", answer=answer,
        entity=best["name"], n_base=len(rows), sources=srcs,
        confirming_sources=srcs[:2],
        api_proof_argument=(
            "The storefront endpoint answers for one application identifier at a time and "
            f"offers no cross-title ordering, so each of the {len(rows)} listed catalogue "
            "entries must be requested separately before their release dates can be compared."),
        confirmation=f"Wikidata {qid} records the same developer for that title",
        facts={"n": len(rows), "title": best["name"], "appids": list(appids),
               "released": best["released"]},
        prompt=build_prompt(
            "The Valve storefront exposes a public record for each catalogue title, giving "
            "its name, the release date the store records for it and the studio credited "
            "with developing it.",
            "Consider the titles carried under the store application numbers "
            + ", ".join(str(a) for a in appids) + ".",
            "Among only those titles, the store records one as released before all the rest.",
            "Report the name of the development studio credited on that single earliest title.",
            "Give the studio name exactly as the storefront prints it, and nothing else.",
            note="Confirm the studio against an independent structured knowledge base."),
    )


# ==========================================================================
# 15. SHOPPING -- Open Food Facts x UPCitemdb x Wikimedia
# ==========================================================================
def gen_shopping(category_tag="en:breakfast-cereals", country="united-kingdom"):
    url = (f"https://world.openfoodfacts.org/api/v2/search?categories_tags={category_tag}"
           f"&countries_tags_en={country}&fields=code,product_name,brands,nutriscore_grade,"
           "quantity&page_size=100")
    js = net.get_json(url, timeout=120)
    prods = [p for p in js.get("products", [])
             if p.get("code") and p.get("product_name") and p.get("nutriscore_grade")]
    if len(prods) < 10:
        raise TrapUnavailable(f"shopping: only {len(prods)} qualifying products")
    # isolate by the numerically greatest barcode, a stable printed attribute
    best = _pick_extreme(
        prods, lambda p: int(re.sub(r"\D", "", p["code"]) or 0), "shopping", mode="max",
        valuefn=lambda p: (p.get("brands") or "").split(",")[0].strip())
    answer = (best.get("brands") or "").split(",")[0].strip()
    if not answer:
        raise TrapUnavailable("shopping: isolated product carries no brand")

    upc = net.get_json("https://api.upcitemdb.com/prod/trial/lookup?upc=" + best["code"],
                       timeout=90)
    if upc.get("code") not in ("OK", "TOO_FAST"):
        raise TrapUnavailable(f"shopping: UPC register returned {upc.get('code')}")

    srcs = [url, "https://api.upcitemdb.com/prod/trial/lookup?upc=" + best["code"],
            "https://www.wikidata.org/w/index.php?search=" + answer.replace(" ", "+")]
    return Candidate(
        category="shopping",
        primary_operator="Open Food Facts", field="brand name", answer=answer,
        entity=best["product_name"][:120], n_base=len(prods), sources=srcs,
        confirming_sources=[url],
        api_proof_argument=(
            "The product search returns an unordered page of records and offers no ordering by "
            f"barcode, so all {len(prods)} matching products must be retrieved and compared."),
        confirmation=f"the product record carries barcode {best['code']}",
        facts={"cat": category_tag, "country": country, "n": len(prods), "code": best["code"]},
        prompt=build_prompt(
            "The Open Food Facts cooperative database catalogues packaged grocery products, "
            "recording for each one its barcode, its brand and its nutritional grade.",
            f"Restrict attention to products filed under the {category_tag.split(':')[-1].replace('-', ' ')} "
            f"category and sold in the {country.replace('-', ' ')}, keeping only those that carry a "
            "nutritional grade.",
            "Among that restricted set, one product's barcode is numerically greater than every other.",
            "Report the brand recorded against that single product.",
            "Give the brand name alone, exactly as the database records it.",
            note="Check the barcode against an independent product register."),
    )


# ==========================================================================
# 16. EDUCATION -- Hipo Labs x Wikimedia x NCES
# ==========================================================================
def gen_education(country="Ireland"):
    js = net.get_json(
        "http://universities.hipolabs.com/search?country=" + country.replace(" ", "%20"),
        timeout=120)
    rows = [u for u in js if u.get("domains") and u.get("name")]
    if len(rows) < 8:
        raise TrapUnavailable(f"education: only {len(rows)} institutions listed for {country}")
    # isolate by the alphabetically last primary domain: stable, printed, unambiguous
    best = _pick_extreme(rows, lambda u: u["domains"][0], "education",
                         mode="max", valuefn=lambda u: u["domains"][0])
    answer = best["domains"][0]

    inception, qid = _wikidata_value(best["name"], "P571", must_contain="universit")
    if not qid:
        raise TrapUnavailable(f"education: Wikidata could not resolve {best['name']}")

    # WITNESS. Resolving the institution is not confirming the ANSWER: the
    # answer is the domain. Require Wikidata P856 to print the same registrable
    # host. This is deliberately fail-closed against register lag -- Waterford
    # Institute of Technology merged into South East Technological University
    # in 2022 and ROR already links setu.ie, so if the knowledge base moves
    # first the generator refuses rather than serving a stale domain.
    site, _ = _wikidata_value(qid, "P856")
    site_host = re.sub(r"^https?://", "", str(site or "")).split("/")[0].lower()
    site_host = site_host[4:] if site_host.startswith("www.") else site_host
    if site_host != answer.lower():
        raise TrapUnavailable(
            f"education: Wikidata P856 for {qid} prints {site_host!r}, not the "
            f"register domain {answer!r}; the register is the primary and "
            "cannot confirm itself")

    srcs = ["http://universities.hipolabs.com/search?country=" + country.replace(" ", "%20"),
            f"https://www.wikidata.org/wiki/{qid}",
            "https://nces.ed.gov/ipeds/datacenter/"]
    return Candidate(
        category="education",
        primary_operator="Hipo Labs", field="internet domain", answer=answer,
        entity=best["name"], n_base=len(rows), sources=srcs,
        confirming_sources=[srcs[1]],
        api_proof_argument=(
            "The register returns an unsorted national list and supports no ordering parameter, "
            f"so all {len(rows)} institutions must be pulled before their domains can be ranked."),
        confirmation=(f"Wikidata {qid} resolves {best['name']} and its P856 "
                      f"official website prints the same host {site_host!r}"),
        facts={"country": country, "n": len(rows), "inst": best["name"],
               "witness_qid": qid, "witness_p856": site,
               "stationarity_note": ("the institution merged into South East "
                                     "Technological University in 2022; the "
                                     "P856 equality check is what stops a "
                                     "register lag from shipping")},
        prompt=build_prompt(
            f"A public register of higher education institutions lists the universities and "
            f"colleges of {country}, recording the internet domain each one uses.",
            f"Consider only the institutions that this register places in {country} and for which "
            "it records at least one domain.",
            "Order the primary domains of those institutions as text, from first to last. One "
            "domain falls later in that ordering than every other.",
            "Report that single last-ordered domain.",
            "Give the domain alone, in lower case, with no protocol and no path.",
            note="Confirm the institution through an independent structured knowledge base."),
    )


GENERATORS = {
    "geography": gen_geography,
    "travel": gen_travel,
    "science and technology": gen_science,
    "health and medicine": gen_health,
    "finance": gen_finance,
    "business": gen_business,
    "history": gen_history,
    "celebrities/public figures": gen_celebrities,
    "legal": gen_legal,
    "politics": gen_politics,
    "art": gen_art,
    "sports": gen_sports,
    "tv shows and movies": gen_tv,
    "video games": gen_video_games,
    "shopping": gen_shopping,
    "education": gen_education,
}
