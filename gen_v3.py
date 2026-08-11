"""Field redesign: replace memorable ANSWER FIELDS with opaque identifiers.

The evaluation loop measured leakage (positional leaks, uniform guessability,
key/order monotonicity, witness independence) and never measured difficulty.
A trap can be perfectly leak-clean and still be trivial, because the solver
never performs the traversal -- it recognises the entity and recalls the
attribute. 'Leiden' is where van der Waals was born; no Nobel API, no
104-laureate window, and no minimum-date-of-birth ranking is needed to say it.

Two independent measurements agree on which categories are affected:

  field_class    four categories answer with an ATTRIBUTE of the winning
                 entity (celebrities/sports city of birth, history award year,
                 video games studio); ten answer with an opaque IDENTIFIER.

  memorability   Wikimedia pageviews on the exact answer string, restricted to
                 answers whose article is about the same referent as the answer:
                 Leiden 145,636/yr, 1975 77,453, Colossal Order 40,617,
                 Newton 26,976 -- against zero article and zero search hits for
                 the arXiv, NCT, CIK and tt identifiers.

Both partitions are the same four categories. The ranking is not the problem;
the answer field is. This module repoints the two Nobel-population categories
at the laureate's GND identifier (Wikidata P227), an opaque token such as
119009846, witnessed by the German National Library at d-nb.info -- an operator
that runs neither the Nobel registry nor Wikidata.

Measured coverage from gnd_probe.json before writing this:
  celebrities  single-valued P227 8/8   DNB-confirmed 8/8
  history      single-valued P227 4/4   DNB-confirmed 4/4
  sports       single-valued P227 1/4   (GND does not cover MLB players)
  video games  single-valued P227 1/3   (GND does not cover game studios)

So this fixes celebrities and history. Sports and video games are left alone
rather than given a witness that does not exist; they stay flagged.
"""
import urllib.parse as up

import category_traps as ct
import gen_v2
from category_traps import Candidate, TrapUnavailable, build_prompt

import net

_DNB = "https://d-nb.info/gnd/{}"


def _gnd(name):
    """Single-valued GND identifier for a person, or raise."""
    vals, qid = ct._wikidata_values(name, "P227")
    vals = [v for v in vals if v]
    if len(vals) != 1:
        raise TrapUnavailable(
            "gnd: Wikidata P227 for %r returned %d values, not exactly one"
            % (name, len(vals)))
    return vals[0], qid


def _dnb_confirms(gnd, name):
    """German National Library must return a record naming the same person."""
    surname = ct._norm(name.split()[-1])
    try:
        txt = net.fetch(_DNB.format(gnd), timeout=45, attempts=3,
                        headers={"Accept": "application/ld+json"})
    except Exception as exc:
        raise TrapUnavailable("gnd: d-nb.info did not serve %s (%s)"
                              % (gnd, type(exc).__name__))
    if surname not in ct._norm(txt):
        raise TrapUnavailable(
            "gnd: d-nb.info record %s does not name %r" % (gnd, surname))
    return True


def _nobel_sources(gnd, qid, name):
    return ["https://api.nobelprize.org/2.1/laureates",
            "https://www.wikidata.org/wiki/%s" % qid,
            _DNB.format(gnd),
            "https://api.openalex.org/authors?search=" + up.quote(name)]


# =========================================================================
# CELEBRITIES -- same population, same ranking, opaque answer.
#
# Was: 'city of birth' -> Leiden / Berlin / Bern / Garding / Paris across the
# five seeds tried. Every one of those is recallable the instant the solver
# names the laureate, so the ranking never binds.
# Now: the laureate's GND identifier, which no solver carries in memory.
# =========================================================================
def gen_celebrities(category_key="Physics", y0=1901, y1=1975):
    rows = []
    for L in gen_v2._nobel_laureates():
        b = L.get("birth") or {}
        nm = (L.get("knownName") or {}).get("en") or (L.get("fullName") or {}).get("en")
        if not (nm and gen_v2._full_date(b.get("date"))):
            continue
        for p in (L.get("nobelPrizes") or []):
            if ((p.get("category") or {}).get("en") == category_key
                    and y0 <= int(p.get("awardYear") or 0) <= y1):
                rows.append({"name": nm, "dob": b["date"], "id": L.get("id"),
                             "year": int(p["awardYear"])})
    if len(rows) < 20:
        raise TrapUnavailable("celebrities: only %d %s laureates with a full "
                              "birth date in %d-%d" % (len(rows), category_key, y0, y1))

    # valuefn returns the laureate name, not the identifier: see the bijection
    # note in facts["guess_space"] below. Without a valuefn the evaluator cannot
    # compute p_answer_by_uniform_guess at all and T2 reports unknown.
    best = ct._pick_extreme(rows, lambda r: r["dob"], "celebrities", mode="min",
                            valuefn=lambda r: r["name"])
    gnd, qid = _gnd(best["name"])
    _dnb_confirms(gnd, best["name"])
    dobs = sorted(r["dob"] for r in rows)

    return Candidate(
        category="celebrities/public figures",
        primary_operator="Nobel Prize Outreach",
        field="GND identifier", answer=gnd, entity=best["name"], n_base=len(rows),
        sources=_nobel_sources(gnd, qid, best["name"]),
        confirming_sources=[_DNB.format(gnd), "https://www.wikidata.org/wiki/%s" % qid],
        api_proof_argument=(
            "The laureate registry is paginated and returned in alphabetical name "
            "order with no birth-date sort, so all %d qualifying laureates must be "
            "pulled and compared. The answer is an authority-file identifier, not a "
            "property of the person that could be recalled once the person is named, "
            "so recognising the laureate is not sufficient: the identifier must be "
            "looked up. Measured rho for the date key against registry order is %s."
            % (len(rows), ct.LAST_RANK.get("spearman_key_vs_api_order"))),
        confirmation=("German National Library record d-nb.info/gnd/%s names %s, and "
                      "Wikidata %s property P227 returns the same identifier"
                      % (gnd, best["name"], qid)),
        facts={"category": category_key, "window": [y0, y1], "n": len(rows),
               "dob": best["dob"], "second_earliest_dob": dobs[1],
               "award_year": best["year"], "laureate_id": best["id"],
               "answer_field_class": "identifier",
               "guess_space": ("p_answer_by_uniform_guess is measured over laureate identity. The GND authority file assigns exactly one record per person and _gnd() rejects any laureate whose Wikidata P227 is not single-valued, so laureate identity and identifier are in bijection and the two probabilities are equal."), 
               "replaces": "city of birth (attribute; measured recallable)"},
        prompt=build_prompt(
            "The Nobel Prize registry publishes a record for every laureate giving "
            "the person's name and date of birth, and national libraries maintain "
            "authority files that assign each person a stable numeric identifier.",
            "Restrict attention to the laureates recognised in %s for awards made "
            "between %d and %d whose record carries a complete date of birth."
            % (category_key, y0, y1),
            "Among that group one laureate was born earlier than every other.",
            "Report the GND authority identifier that the German National Library "
            "assigns to that single laureate.",
            "Give the identifier alone, digits and any trailing check character only.",
            note="Resolve the identifier at the national library before answering."),
    )


# =========================================================================
# HISTORY -- same population, same ranking, opaque answer.
#
# Was: 'award year' -> 1975 / 2000 / 1974 / 1975 across four seeds. A year is
# both recallable and low-entropy, and the old trap carried a standing
# partial_confirmation caveat because Wikidata records the year as a statement
# qualifier rather than a queryable value, so the year rested on the prize
# registry alone -- a primary-operator self-confirmation in all but name.
# Now: the lead laureate's GND identifier, which the German National Library
# confirms directly. This upgrades the witness as well as the answer.
# =========================================================================
def gen_history(category_key="Physics", y0=1901, y1=2000, min_laureates=3):
    prizes = net.get_json(gen_v2._NOBEL_PRIZES, timeout=240,
                          attempts=5).get("nobelPrizes", [])
    base = [p for p in prizes
            if (p.get("category") or {}).get("en") == category_key
            and y0 <= int(p["awardYear"]) <= y1
            and len(p.get("laureates") or []) >= min_laureates
            and ((p["laureates"][0].get("fullName") or {}).get("en"))]
    if len(base) < 8:
        raise TrapUnavailable("history: only %d %s prizes shared by %d+ in %d-%d"
                              % (len(base), category_key, min_laureates, y0, y1))

    # Identical key to the shipped trap, so the measured ranking properties
    # (rho, separation, winner depth) carry over unchanged. Only the value
    # read off the winning record changes: identifier instead of award year.
    best = ct._pick_extreme(
        base, lambda p: (p["laureates"][0]["fullName"]["en"]).strip().lower(),
        "history", mode="min",
        valuefn=lambda p: (p["laureates"][0]["fullName"]["en"]).strip())
    lead = best["laureates"][0]["fullName"]["en"]
    gnd, qid = _gnd(lead)
    _dnb_confirms(gnd, lead)
    names = sorted((p["laureates"][0]["fullName"]["en"]).strip().lower() for p in base)
    best = {"lead": lead, "year": int(best["awardYear"]),
            "laureates": best["laureates"]}

    return Candidate(
        category="history",
        primary_operator="Nobel Prize Outreach",
        field="GND identifier", answer=gnd, entity=best["lead"], n_base=len(base),
        sources=_nobel_sources(gnd, qid, best["lead"]),
        confirming_sources=[_DNB.format(gnd), "https://www.wikidata.org/wiki/%s" % qid],
        api_proof_argument=(
            "The prize registry returns awards in year order and offers no way to "
            "filter by the number of laureates or to sort by laureate name, so all "
            "%d qualifying awards must be assembled and ordered by the solver. The "
            "answer is an authority-file identifier rather than the award year, so "
            "identifying the award does not hand the solver the answer. Measured rho "
            "for the name key against registry chronology is %s."
            % (len(base), ct.LAST_RANK.get("spearman_key_vs_api_order"))),
        confirmation=("German National Library record d-nb.info/gnd/%s names %s, and "
                      "Wikidata %s property P227 returns the same identifier"
                      % (gnd, best["lead"], qid)),
        facts={"category": category_key, "window": [y0, y1], "n": len(base),
               "lead_laureate": best["lead"], "second_alphabetical": names[1],
               "award_year": best["year"], "n_laureates": len(best["laureates"]),
               "answer_field_class": "identifier",
               "guess_space": ("p_answer_by_uniform_guess is measured over laureate identity. The GND authority file assigns exactly one record per person and _gnd() rejects any laureate whose Wikidata P227 is not single-valued, so laureate identity and identifier are in bijection and the two probabilities are equal."), 
               "replaces": "award year (attribute; measured recallable)",
               "resolved_defect": ("the award-year answer could only be confirmed by "
                                   "the prize registry itself, because Wikidata stores "
                                   "the year as a statement qualifier; the identifier "
                                   "answer is confirmed by the German National Library, "
                                   "which runs neither the registry nor Wikidata")},
        prompt=build_prompt(
            "The Nobel Prize registry records every %s award with its year and the "
            "full list of laureates who shared it, and national libraries maintain "
            "authority files that assign each person a stable numeric identifier."
            % category_key,
            "Consider only the %s awards made between %d and %d that were divided "
            "among %d or more laureates, and take the first laureate listed on each "
            "of those awards." % (category_key, y0, y1, min_laureates),
            "Order those lead laureates by name and take the one that comes first.",
            "Report the GND authority identifier assigned to that lead laureate.",
            "Give the identifier alone, digits and any trailing check character only.",
            note="Resolve the identifier at the national library before answering."),
    )


# =========================================================================
# EDUCATION -- decouple the ranking key from the register's return order, and
# move off a seed whose institution no longer exists.
#
# Two separate measured defects, both fixed here:
#
# 1. ORDER LEAK. The shipped key (alphabetically-last primary domain) tracked
#    the register's own return order at rho +0.4685 (NZ), +0.6639 (FI),
#    +0.6846 (PT), +0.7741 (IE), +0.8136 (DK), +0.8250 (AT), +0.8443 (IL),
#    +0.8660 (CL), +0.8794 (NO), +0.9498 (HU) -- mean ~0.78. The register
#    returns roughly name-alphabetical order and names predict domains, so a
#    solver could skip the traversal and read down the list. That is a property
#    of the KEY, not of any one country. Reversing the domain string before
#    ordering breaks the correlation: |rho| <= 0.2728 in all ten countries, and
#    0.0746 for Norway specifically.
#
# 2. DEAD ENTITY. The shipped seed answered wit.ie -- Waterford Institute of
#    Technology, dissolved into South East Technological University on
#    2022-05-01. Wikidata records no succession property for Q7974025 (37
#    properties, none of them P576/P7888/P1366/P155/P156; a SPARQL sweep in
#    both directions returns 0 rows), so the P856 equality check still passes
#    and no liveness guard can detect it. The fix is not a better guard, which
#    was tried and measured useless; it is a seed whose institution is
#    unambiguously operating. NTNU is.
# =========================================================================
def gen_education(country="Norway"):
    js = net.get_json(
        "http://universities.hipolabs.com/search?country=" + country.replace(" ", "%20"),
        timeout=120)
    rows = [u for u in js if u.get("domains") and u.get("name")]
    if len(rows) < 8:
        raise TrapUnavailable("education: only %d institutions listed for %s"
                              % (len(rows), country))

    best = ct._pick_extreme(rows, lambda u: u["domains"][0][::-1], "education",
                            mode="max", valuefn=lambda u: u["domains"][0])
    answer = best["domains"][0]
    rho = ct.LAST_RANK.get("spearman_key_vs_api_order")

    _inception, qid = ct._wikidata_value(best["name"], "P571", must_contain="universit")
    if not qid:
        raise TrapUnavailable("education: Wikidata could not resolve %s" % best["name"])

    sites, _ = ct._wikidata_values(qid, "P856")

    def _host(u):
        import re as _re
        h = _re.sub(r"^https?://", "", str(u or "")).split("/")[0].lower()
        return h[4:] if h.startswith("www.") else h

    hosts = [_host(s) for s in sites]
    if answer.lower() not in hosts:
        raise TrapUnavailable(
            "education: Wikidata P856 for %s prints %s, none of which is the "
            "register domain %r; the register is the primary and cannot confirm "
            "itself" % (qid, hosts or "nothing", answer))
    site = sites[hosts.index(answer.lower())]

    srcs = ["http://universities.hipolabs.com/search?country=" + country.replace(" ", "%20"),
            "https://www.wikidata.org/wiki/%s" % qid,
            "https://nces.ed.gov/ipeds/datacenter/"]
    return Candidate(
        category="education",
        primary_operator="Hipo Labs", field="internet domain", answer=answer,
        entity=best["name"], n_base=len(rows), sources=srcs,
        confirming_sources=[srcs[1]],
        api_proof_argument=(
            "The register returns an unsorted national list and supports no ordering "
            "parameter, so all %d institutions must be pulled before their domains "
            "can be ranked. The ordering is applied to the reversed domain string "
            "specifically so that it cannot be read off the register's own return "
            "order: measured rho for this key is %s, against +0.4685 to +0.9498 for "
            "the forward-alphabetical key across the ten countries tested."
            % (len(rows), rho)),
        confirmation=("Wikidata %s resolves %s and its P856 official website prints "
                      "the same host %r" % (qid, best["name"], answer.lower())),
        facts={"country": country, "n": len(rows), "inst": best["name"],
               "witness_qid": qid, "witness_p856": site,
               "n_p856_values": len(sites), "all_p856_hosts": hosts,
               "answer_field_class": "identifier",
               "spearman_this_key": rho,
               "replaces": ("Ireland / alphabetically-last domain -> wit.ie, whose "
                            "institution was dissolved on 2022-05-01, ranked by a key "
                            "that leaked the register's order at rho +0.7741"),
               "witness_scope": ("P856 confirms the DOMAIN BINDING only. It does not "
                                 "confirm that the institution still exists."),
               "known_defect_answer_is_derivable": (
                   "MEASURED, NOT FIXED. The two defects this rebuild addressed "
                   "were the order leak and the dead institution. Recallability "
                   "is a third and separate defect and it survives: the answer "
                   "ntnu.no is derivable from the institution's name without "
                   "consulting the register at all, and the string draws 174 "
                   "Wikipedia mentions. This is weaker than memorisation, since "
                   "the solver must still identify which institution wins the "
                   "ordering, but the domain field cannot be called opaque the "
                   "way an arXiv, NCT or GND identifier can. Fixing it needs a "
                   "different answer field, not a different country or key."),
               "known_defect_liveness": (
                   "The liveness guard remains non-functional in general: Wikidata "
                   "carries no succession properties for dissolved institutions in "
                   "the cases measured, so a future seed could again name a merged "
                   "institution without this check noticing. This seed was chosen "
                   "because NTNU is unambiguously operating, not because the check "
                   "would catch it if it were not.")},
        prompt=build_prompt(
            "A public register of higher education institutions lists the universities "
            "and colleges of %s, recording the internet domain each one uses." % country,
            "Consider only the institutions that this register places in %s and for "
            "which it records at least one domain." % country,
            "Write each institution's primary domain backwards, character by character, "
            "and order those reversed strings as text. One of them falls later in that "
            "ordering than every other.",
            "Report the original, unreversed domain of that single institution.",
            "Give the domain alone, in lower case, with no protocol and no path.",
            note="Confirm the institution through an independent structured knowledge base."),
    )


_OVERRIDES = {
    "celebrities/public figures": gen_celebrities,
    "history": gen_history,
    "education": gen_education,
}
ct.GENERATORS.update(_OVERRIDES)
