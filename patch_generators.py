#!/usr/bin/env python3
"""patch_generators.py — replace the five generators that could not isolate a
unique answer, with designs whose isolation key is structurally tie-resistant.

Diagnosis of each failure (from the first full run):
  science  : weekly journals date every article in an issue identically, so
             "earliest publication date" ties by construction. -> arXiv, where
             submissions are dated per day and author counts vary widely.
  legal    : the Supreme Court hands down many opinions on one day, so
             "earliest filing date" ties by construction. -> isolate WITHIN a
             single hand-down day by case name, which is unique.
  sports   : MLB expands in pairs, so "most recently founded club" is almost
             always tied. -> isolate a player within one roster by birth date.
  tv       : the TVmaze endpoint does not exist (404). -> IMDb bulk datasets,
             which have no server-side query at all.
  games    : the Valve-only app pool shares release dates and one developer.
             Also: appdetails returns LOCALE-DEPENDENT date strings
             ("11/out./2006"), so the ordering key was unstable. -> pinned
             locale, wider pool, every date required to parse.
"""
import io
import re
import sys

PATH = "category_traps.py"
src = io.open(PATH, encoding="utf-8").read()
orig = src


def replace_func(name, new_src):
    """Replace `def name(...)` up to the next top-level section marker."""
    global src
    m = re.search(r"^def %s\(" % re.escape(name), src, re.M)
    if not m:
        sys.exit(f"FAIL: {name} not found")
    start = m.start()
    tail = src[start:]
    m2 = re.search(r"\n\n\n# =+\n", tail)
    if not m2:
        m2 = re.search(r"\n\n\nGENERATORS", tail)
    if not m2:
        sys.exit(f"FAIL: no terminator after {name}")
    end = start + m2.start()
    src = src[:start] + new_src.rstrip("\n") + src[end:]
    print(f"  ok  {name}")


def insert_after(anchor, block, tag):
    global src
    if src.count(anchor) != 1:
        sys.exit(f"FAIL [{tag}]: anchor not unique")
    src = src.replace(anchor, anchor + block, 1)
    print(f"  ok  {tag}")


# ---------------------------------------------------------------- helper
insert_after(
    '''    return (vals[0] if vals else None), qid
''',
    '''

def _wikidata_item_label(qid_or_search, prop, must_contain="", lang="en"):
    """Return the English label of the first ITEM-valued claim of `prop`.

    _wikidata_value only reads string, time and quantity datavalues, so
    item-valued properties (P19 place of birth, P178 developer) came back None.
    """
    qid = qid_or_search
    if not re.fullmatch(r"Q\\d+", str(qid_or_search)):
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
            lab = (sub.get("entities", {}).get(tgt, {})
                   .get("labels", {}).get(lang, {}).get("value"))
            return lab, qid, tgt
    return None, qid, None


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())
''',
    "helper _wikidata_item_label")

# ---------------------------------------------------------------- science
replace_func("gen_science", '''def gen_science(days=("2024-01-16", "2024-02-13", "2024-03-12", "2024-04-09",
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
                aid = re.sub(r"v\\d+$", "", raw)
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
                category="science and technology", field="arXiv identifier",
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
                          + "; ".join(tried[:8]))''')

# ---------------------------------------------------------------- legal
replace_func("gen_legal", '''def gen_legal(court="scotus", years=(1992, 1993, 1991, 1994)):
    """CourtListener x Cornell LII x Justia.

    The Supreme Court hands down many opinions on one day, so a period-wide
    "earliest filing date" is tied by construction. Isolation therefore happens
    WITHIN a single hand-down day, by case name, which is unique on that day.
    """
    tried = []
    for year in years:
        rows, url = _cl_opinions(court, year)
        if len(rows) < 12:
            tried.append(f"{year}: only {len(rows)} opinions enumerated")
            continue
        byday = {}
        for r in rows:
            if r.get("us_cite") and r.get("caseName") and r.get("dateFiled"):
                byday.setdefault(r["dateFiled"], []).append(r)
        for day in sorted(byday):
            day_rows = byday[day]
            if len(day_rows) < 4:
                continue
            if any(not str(r["caseName"]).isascii() for r in day_rows):
                continue
            try:
                best = _pick_extreme(day_rows, lambda r: r["caseName"].lower(),
                                     f"legal {day}", mode="min",
                                     valuefn=lambda r: r["us_cite"])
            except TrapUnavailable as te:
                tried.append(str(te))
                continue
            # the ordering must not depend on the casing convention
            raw_first = min(day_rows, key=lambda r: r["caseName"])
            if raw_first is not best:
                tried.append(f"{day}: alphabetical order is casing-dependent")
                continue

            vol, page = best["us_cite"].split(" U.S. ")
            token = _cite_token(best["caseName"])
            lii_url = f"https://www.law.cornell.edu/supremecourt/text/{vol}/{page}"
            jus_url = f"https://supreme.justia.com/cases/federal/us/{vol}/{page}/"
            try:
                lii = net.fetch(lii_url, timeout=90)
            except Exception as e:  # noqa: BLE001
                tried.append(f"{day}: Cornell {type(e).__name__}")
                continue
            if token not in _norm(lii):
                tried.append(f"{day}: Cornell page for {vol} U.S. {page} lacks {token}")
                continue
            try:
                jus = net.fetch(jus_url, timeout=90)
            except Exception as e:  # noqa: BLE001
                tried.append(f"{day}: Justia {type(e).__name__}")
                continue
            if token not in _norm(jus):
                tried.append(f"{day}: Justia page for {vol} U.S. {page} lacks {token}")
                continue

            srcs = [url, lii_url, jus_url]
            return Candidate(
                category="legal", field="United States Reports citation",
                answer=best["us_cite"], entity=best["caseName"][:120],
                n_base=len(day_rows), sources=srcs, confirming_sources=[lii_url, jus_url],
                api_proof_argument=(
                    "The opinion archive can filter by court and date but cannot order "
                    "results by case name, so all "
                    f"{len(day_rows)} decisions handed down that day must be retrieved "
                    "and sorted by the solver."),
                confirmation=("two independent law publishers serve that volume and page "
                              "as the same case"),
                facts={"court": court, "year": year, "day": day,
                       "n": len(day_rows), "case": best["caseName"]},
                prompt=build_prompt(
                    "The Free Law Project publishes the written decisions of the United "
                    "States Supreme Court, recording for each one the date it was handed "
                    "down and its United States Reports citation.",
                    f"Consider only the decisions that carry such a citation and were "
                    f"handed down on {day}.",
                    "Order that day's decisions alphabetically by the case name as the "
                    "archive prints it; one of them stands first.",
                    "Report the United States Reports citation of that first case.",
                    "Give the citation alone, as volume, the reporter abbreviation and "
                    "page, and nothing else.",
                    note="Verify the citation against two independent law publishers."),
            )
    raise TrapUnavailable("legal: no hand-down day isolated a confirmable case; tried "
                          + "; ".join(tried[:8]))''')

# ---------------------------------------------------------------- sports
replace_func("gen_sports", '''def gen_sports(pairs=((147, "New York Yankees", 1998), (111, "Boston Red Sox", 1999),
                      (119, "Los Angeles Dodgers", 1988), (158, "Milwaukee Brewers", 1982),
                      (117, "Houston Astros", 2005))):
    """MLB x Wikimedia x TheSportsDB.

    "Most recently founded club" is tied in almost every season because the
    league expands in pairs, so isolation moves inside a single roster, where
    birth dates are effectively unique.
    """
    tried = []
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
        people = [p for p in pj.get("people", []) if p.get("birthDate") and p.get("birthCity")]
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

        city, qid, _ = _wikidata_item_label(best["fullName"], "P19",
                                            must_contain="baseball")
        if not qid or not city or _norm(answer) not in _norm(city):
            tried.append(f"{team_name} {season}: Wikidata P19 gave {city!r} for "
                         f"{best['fullName']!r}, expected {answer!r}")
            continue
        try:
            sdb = net.get_json(
                "https://www.thesportsdb.com/api/v1/json/3/searchplayers.php?p="
                + best["fullName"].replace(" ", "%20"), timeout=90)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{team_name} {season}: TheSportsDB {type(e).__name__}")
            continue
        players = sdb.get("player") or []
        hit = next((pl for pl in players
                    if _norm(answer) in _norm(pl.get("strBirthLocation"))), None)
        if not hit:
            tried.append(f"{team_name} {season}: TheSportsDB has no matching birthplace")
            continue

        srcs = [f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
                f"?season={season}&rosterType=fullSeason",
                f"https://www.wikidata.org/wiki/{qid}",
                "https://www.thesportsdb.com/api/v1/json/3/searchplayers.php?p="
                + best["fullName"].replace(" ", "%20")]
        return Candidate(
            category="sports", field="city of birth", answer=answer,
            entity=best["fullName"], n_base=len(people), sources=srcs,
            confirming_sources=srcs[1:],
            api_proof_argument=(
                "The roster endpoint returns players in uniform-number order with no "
                f"birth-date sort, so all {len(people)} players carried by the club that "
                "season must be pulled and compared."),
            confirmation=("Wikidata and TheSportsDB independently record the same "
                          "birthplace for that player"),
            facts={"team": team_name, "season": season, "n": len(people),
                   "player": best["fullName"], "dob": best["birthDate"]},
            prompt=build_prompt(
                "The official Major League Baseball statistics service publishes the full "
                f"season roster of the {team_name} for {season}, listing every player "
                "the club carried that year together with his date and place of birth.",
                "Consider only the players appearing on that full season roster.",
                "Exactly one of them was born earlier than every other player on the list.",
                "Report the city of birth that the service records for that single "
                "oldest player.",
                "Give the city name alone, as recorded, with no country and no other words.",
                note="Confirm the birthplace against two independent references."),
        )
    raise TrapUnavailable("sports: no roster isolated a confirmable player; tried "
                          + "; ".join(tried[:6]))''')

# ---------------------------------------------------------------- tv
replace_func("gen_tv", '''def gen_tv(years=(1998, 2003, 1994, 2008), genres=("Sci-Fi", "Western", "Film-Noir",
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
        f = line.rstrip("\\n").split("\\t")
        if len(f) < 9 or f[1] != "tvSeries" or f[5] not in want_years:
            continue
        if f[7] in ("", "\\\\N") or not f[7].isdigit():
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
            category="tv shows and movies", field="IMDb title identifier",
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
                          + "; ".join(tried[:8]))''')

# ---------------------------------------------------------------- video games
replace_func("gen_video_games", '''def gen_video_games(appids=(3830, 6910, 8930, 22380, 105600, 250900, 39210,
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
        category="video games", field="developer name", answer=answer,
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
    )''')

# ---------------------------------------------------------------- shared bits
insert_after(
    '''_OPENFLIGHTS_RT = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat"
''',
    '''_IMDB_BASICS = "https://datasets.imdbws.com/title.basics.tsv.gz"

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
           "jul", "aug", "sep", "oct", "nov", "dec"]


def _steam_date(s):
    """Parse a pinned-locale Steam release string to a sortable tuple, else None."""
    if not s:
        return None
    m = re.match(r"^\\s*(\\d{1,2})\\s+([A-Za-z]{3})[a-z]*,?\\s*(\\d{4})\\s*$", s)
    if m:
        return (int(m.group(3)), _MONTHS.index(m.group(2).lower()) + 1, int(m.group(1)))
    m = re.match(r"^\\s*([A-Za-z]{3})[a-z]*\\s+(\\d{1,2}),\\s*(\\d{4})\\s*$", s)
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
            us = next((c for c in cites if re.fullmatch(r"\\d+ U\\.S\\. \\d+", str(c))), None)
            rows.append({"caseName": r.get("caseName") or "",
                         "dateFiled": r.get("dateFiled") or "",
                         "us_cite": us})
        nxt = js.get("next")
        pages += 1
    return rows, url
''',
    "shared helpers")

insert_after("import re\n", "import xml.etree.ElementTree as ET\n", "import ET")

io.open(PATH, "w", encoding="utf-8").write(src)
print(f"\npatched {PATH}: {len(orig)} -> {len(src)} chars")
