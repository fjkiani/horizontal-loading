#!/usr/bin/env python3
"""patch_fix2.py — repair the last two generators using what the probes showed.

legal
  * v4 ignores page_size and returns 20 per page; cursor pagination gets 429'd
    after a handful of calls. Fixed by asking for a WEEK at a time and only
    accepting a window where the top-level `count` equals the rows returned,
    which proves the enumeration is complete without paging at all.
  * The archive returns the same case twice on cert-stage days (one row per
    reporter), and every order on an order-list day shares ONE U.S. Reports
    page: 1992-01-10 had 16 rows all citing "502 U.S. 1024". A citation shared
    by the whole base set is guessable without reading anything, so the day is
    now required to have pairwise-distinct citations after de-duplication.

sports
  * Wikidata has "Tim Raines", not "Tim Raines Sr." -- the roster suffix broke
    the lookup. Suffixes are now stripped.
  * TheSportsDB carries MLB players but leaves strBirthLocation null, so it
    cannot confirm a birthplace at all. Replaced with Retrosheet's public
    biofile, which publishes birth city, state and country per player.
"""
import io
import re
import sys

PATH = "category_traps.py"
src = io.open(PATH, encoding="utf-8").read()
orig = src


def replace_func(name, new_src):
    global src
    m = re.search(r"^def %s\(" % re.escape(name), src, re.M)
    if not m:
        sys.exit(f"FAIL: {name} not found")
    start = m.start()
    tail = src[start:]
    m2 = re.search(r"\n\n\n# =+\n", tail) or re.search(r"\n\n\nGENERATORS", tail)
    if not m2:
        sys.exit(f"FAIL: no terminator after {name}")
    src = src[:start] + new_src.rstrip("\n") + src[start + m2.start():]
    print(f"  ok  {name}")


def sub1(old, new, tag):
    global src
    if src.count(old) != 1:
        sys.exit(f"FAIL [{tag}]: {src.count(old)} matches")
    src = src.replace(old, new, 1)
    print(f"  ok  {tag}")


# ------------------------------------------------------------ shared helpers
sub1('''def _cl_opinions(court, year, max_pages=12):''',
     '''_RETRO_BIO = "https://www.retrosheet.org/BIOFILE.TXT"
_SUFFIX = re.compile(r"\\s+(?:Jr\\.?|Sr\\.?|I{2,3}|IV)$", re.I)


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
        us = next((c for c in cites if re.fullmatch(r"\\d+ U\\.S\\. \\d+", str(c))), None)
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


def _cl_opinions(court, year, max_pages=12):''',
     "legal/sports shared helpers")

# ------------------------------------------------------------ legal
replace_func("gen_legal", '''def gen_legal(court="scotus", windows=(
        ("1992-03-02", "1992-03-08"), ("1992-03-23", "1992-03-29"),
        ("1992-04-20", "1992-04-26"), ("1993-03-01", "1993-03-07"),
        ("1993-03-29", "1993-04-04"), ("1994-03-07", "1994-03-13"),
        ("1994-04-18", "1994-04-24"), ("1991-03-04", "1991-03-10"),
        ("1995-03-06", "1995-03-12"), ("1990-03-05", "1990-03-11"))):
    """CourtListener x Cornell LII x Justia."""
    tried = []
    for day0, day1 in windows:
        try:
            count, rows, url = _cl_window(court, day0, day1)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{day0}: {type(e).__name__}")
            continue
        if count is None or count != len(rows):
            tried.append(f"{day0}: window incomplete ({len(rows)} of {count})")
            continue
        byday = {}
        for r in _dedupe_cases(rows):
            byday.setdefault(r["dateFiled"], []).append(r)
        for day in sorted(byday):
            day_rows = byday[day]
            if len(day_rows) < 4:
                continue
            # An order-list day gives every case the SAME U.S. Reports page, so
            # the citation would be guessable without identifying any case.
            if len({r["us_cite"] for r in day_rows}) != len(day_rows):
                tried.append(f"{day}: citations are not pairwise distinct")
                continue
            if any(not r["caseName"].isascii() for r in day_rows):
                continue
            try:
                best = _pick_extreme(day_rows, lambda r: r["caseName"].lower(),
                                     f"legal {day}", mode="min",
                                     valuefn=lambda r: r["us_cite"])
            except TrapUnavailable as te:
                tried.append(str(te))
                continue
            if min(day_rows, key=lambda r: r["caseName"]) is not best:
                tried.append(f"{day}: alphabetical order depends on casing")
                continue

            vol, page = best["us_cite"].split(" U.S. ")
            token = _cite_token(best["caseName"])
            lii_url = f"https://www.law.cornell.edu/supremecourt/text/{vol}/{page}"
            jus_url = f"https://supreme.justia.com/cases/federal/us/{vol}/{page}/"
            ok = True
            for label, u in (("Cornell", lii_url), ("Justia", jus_url)):
                try:
                    if token not in _norm(net.fetch(u, timeout=90)):
                        tried.append(f"{day}: {label} page {vol}/{page} lacks {token}")
                        ok = False
                        break
                except Exception as e:  # noqa: BLE001
                    tried.append(f"{day}: {label} {type(e).__name__}")
                    ok = False
                    break
            if not ok:
                continue

            srcs = [url, lii_url, jus_url]
            return Candidate(
                category="legal", field="United States Reports citation",
                answer=best["us_cite"], entity=best["caseName"][:120],
                n_base=len(day_rows), sources=srcs, confirming_sources=[lii_url, jus_url],
                api_proof_argument=(
                    "The archive filters by court and date but cannot order results by "
                    f"case name, so all {len(day_rows)} decisions handed down that day "
                    "must be retrieved and sorted by the solver."),
                confirmation=("two independent law publishers serve that volume and page "
                              "as the same case"),
                facts={"court": court, "day": day, "n": len(day_rows),
                       "case": best["caseName"], "window_count": count},
                prompt=build_prompt(
                    "The Free Law Project publishes the written decisions of the United "
                    "States Supreme Court, recording for each one the date it was handed "
                    "down and its United States Reports citation.",
                    f"Consider only the decisions carrying such a citation that were handed "
                    f"down on {day}.",
                    "Place that day's decisions in alphabetical order by case name as the "
                    "archive prints it; one of them stands first.",
                    "Report the United States Reports citation of that first case.",
                    "Give the citation alone, as volume, reporter abbreviation and page, "
                    "with nothing else.",
                    note="Verify the citation against two independent law publishers."),
            )
    raise TrapUnavailable("legal: no hand-down day isolated a confirmable case; tried "
                          + "; ".join(tried[:10]))''')

# ------------------------------------------------------------ sports
replace_func("gen_sports", '''def gen_sports(pairs=((147, "New York Yankees", 1998), (111, "Boston Red Sox", 1999),
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
        y, m, d = best["birthDate"].split("-")
        want = f"{int(m)}/{int(d)}/{y}"
        last = plain.rsplit(" ", 1)[-1]
        hit = next((r for r in bio
                    if r.get("BIRTHDATE") == want and _norm(r.get("LAST")) == _norm(last)),
                   None)
        if not hit:
            tried.append(f"{team_name} {season}: Retrosheet has no {last} born {want}")
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
            category="sports", field="city of birth", answer=answer,
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
                          + "; ".join(tried[:8]))''')

io.open(PATH, "w", encoding="utf-8").write(src)
print(f"\npatched {PATH}: {len(orig)} -> {len(src)} chars")
