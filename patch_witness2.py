"""Wire the two witnesses probe_t5e confirmed. Idempotent; markers asserted.

politics    GovInfo does NOT carry EO 13292 -- the full 130-granule page of
            FR-2003-03-28 (all of it this time, not the 100 measured before)
            has zero references and only one presidential granule. Wikidata
            does: Q5419886 "Executive Order 13292". That is one independent
            operator, so politics moves from refused to silver, not to gold.
video games PCGamingWiki independently credits Colossal Order as developer of
            Cities: Skylines -- a third operator, neither Valve nor Wikimedia.
            It also credits Tantalus Media as a co-developer; that is recorded
            in facts rather than suppressed, because a reader who checks the
            witness will see it.
"""
import re


def repl(path, old, new, label, marker):
    s = open(path).read()
    assert marker not in s.replace(new, ""), f"{label}: marker is not unique to the new text"
    if marker in s:
        print(f"[skip] {label} (already applied)")
        return
    assert old in s, f"{label}: anchor not found"
    open(path, "w").write(s.replace(old, new, 1))
    print(f"[ok]   {label}")


# ---------------------------------------------------------------- operator map
repl("source_gate.py",
     '    "federalregister.gov": "US Office of the Federal Register",',
     '    "federalregister.gov": "US Office of the Federal Register",\n'
     '    # A community wiki, but a distinct controlling entity from Valve and\n'
     '    # from Wikimedia, and it records developer credits editorially rather\n'
     '    # than by mirroring either one.\n'
     '    "pcgamingwiki.com": "PCGamingWiki",',
     "OPERATOR_MAP += pcgamingwiki", '"pcgamingwiki.com": "PCGamingWiki"')

# ------------------------------------------------------------------- politics
repl("gen_v2.py",
     '''    srcs = [u,
            f"https://www.federalregister.gov/documents/{best['publication_date']}",
            "https://www.govinfo.gov/app/collection/FR",
            "https://www.wikidata.org/wiki/Q737808"]''',
     '''    # GovInfo publishes the same issue but its granule index does not name
    # individual order numbers: all 130 granules of FR-2003-03-28 were paged and
    # none reference 13292, and only one granule is presidential at all. So the
    # witness has to be an operator that catalogues the ORDER, not the issue.
    wd_eo = ct.net.wikidata_search(f"Executive Order {answer}").get("search", [])
    eo_hit = next((h for h in wd_eo
                   if answer in str(h.get("label", ""))
                   or answer in str(h.get("description", ""))), None)
    srcs = [u,
            f"https://www.federalregister.gov/documents/{best['publication_date']}",
            "https://www.govinfo.gov/app/collection/FR",
            "https://www.wikidata.org/wiki/Q737808"]
    conf_srcs = [srcs[1]]
    conf_extra = ""
    if eo_hit:
        srcs[3] = f"https://www.wikidata.org/wiki/{eo_hit['id']}"
        conf_srcs.append(srcs[3])
        conf_extra = (f"; Wikidata item {eo_hit['id']} is titled "
                      f"{eo_hit.get('label')!r} and independently binds the "
                      f"number to the order")''',
     "politics: Wikidata EO witness", "wd_eo = ct.net.wikidata_search")

repl("gen_v2.py",
     '''        entity=best["title"], n_base=len(base), sources=srcs,
        confirming_sources=[srcs[1]],
        api_proof_argument=(
            f"The Federal Register document API returns the {len(base)} executive "''',
     '''        entity=best["title"], n_base=len(base), sources=srcs,
        confirming_sources=conf_srcs,
        api_proof_argument=(
            f"The Federal Register document API returns the {len(base)} executive "''',
     "politics: use conf_srcs", "confirming_sources=conf_srcs,")

repl("gen_v2.py",
     '''                      f"{best.get('start_page')} to {best.get('end_page')}"),''',
     '''                      f"{best.get('start_page')} to {best.get('end_page')}"
                      + conf_extra),''',
     "politics: confirmation text", "+ conf_extra),")

repl("gen_v2.py",
     '''               "single_witness": True,
               "provenance_note": ("Executive order numbers are assigned and "
                                   "published only by the Office of the Federal "
                                   "Register, so no second operator independently "
                                   "witnesses the number.")},''',
     '''               "single_witness": not bool(eo_hit),
               "wikidata_eo_qid": (eo_hit or {}).get("id"),
               "govinfo_granule_check": (
                   "all 130 granules of the issue were paged; none reference the "
                   "order number and only one granule is presidential, so GovInfo "
                   "is a co-publisher of the issue but not a witness of the order"),
               "provenance_note": (
                   "Executive order numbers are assigned and published only by "
                   "the Office of the Federal Register. Wikidata catalogues the "
                   "order as a subject in its own right, which is why it can "
                   "witness the number without restating the register's ranking; "
                   "the page-length ranking itself has no second witness.")},''',
     "politics: facts", '"govinfo_granule_check"')

# ---------------------------------------------------------------- video games
repl("gen_v2.py",
     '''    srcs = [f"https://store.steampowered.com/api/appdetails?appids={best['appid']}",
            f"https://www.wikidata.org/wiki/{qid}",
            "https://pegi.info/search-pegi?q=" + up.quote(best["name"])]''',
     '''    # Third operator. PCGamingWiki keeps developer credits editorially, so a
    # match there is not Wikidata restated. Co-developers are recorded, not
    # hidden: a reader who opens the witness will see them.
    pcgw = ("https://www.pcgamingwiki.com/w/api.php?action=query&prop=revisions"
            "&rvprop=content&rvslots=main&format=json&titles="
            + up.quote(best["name"]))
    pcgw_ok, pcgw_devs = False, []
    try:
        pages = (ct.net.get_json(pcgw, timeout=90).get("query") or {}).get("pages") or {}
        wtxt = ""
        for _p in pages.values():
            _r = _p.get("revisions") or []
            if _r:
                wtxt = ((_r[0].get("slots") or {}).get("main") or {}).get("*", "")
        pcgw_devs = re.findall(r"Infobox game/row/developer\\|([^}|]+)", wtxt)
        pcgw_ok = any(ct._norm(answer) == ct._norm(d) for d in pcgw_devs)
    except Exception:  # noqa: BLE001
        pcgw_ok = False

    srcs = [f"https://store.steampowered.com/api/appdetails?appids={best['appid']}",
            f"https://www.wikidata.org/wiki/{qid}",
            "https://pegi.info/search-pegi?q=" + up.quote(best["name"])]
    conf_vg = [f"https://www.wikidata.org/wiki/{qid}",
               f"https://www.wikidata.org/wiki/{hit['qid']}"]
    if pcgw_ok:
        srcs.append(pcgw)
        conf_vg.append(pcgw)''',
     "video games: PCGamingWiki witness", "pcgw_ok, pcgw_devs = False, []")

repl("gen_v2.py",
     '''        confirming_sources=[f"https://www.wikidata.org/wiki/{qid}",
                            f"https://www.wikidata.org/wiki/{hit['qid']}"],''',
     '''        confirming_sources=conf_vg,''',
     "video games: use conf_vg", "confirming_sources=conf_vg,")

repl("gen_v2.py",
     '''        facts={"n": len(base), "title": best["name"], "appid": best["appid"],
               "n_distinct_studios": len(set(devs_norm)),''',
     '''        facts={"n": len(base), "title": best["name"], "appid": best["appid"],
               "n_distinct_studios": len(set(devs_norm)),
               "pcgamingwiki_confirms": pcgw_ok,
               "pcgamingwiki_developers_listed": pcgw_devs,
               "co_developer_note": (
                   "PCGamingWiki lists more than one developer for this title; "
                   "the answer is the studio the storefront prints first, and the "
                   "witness is that PCGamingWiki also credits it, not that it is "
                   "the sole developer." if len(pcgw_devs) > 1 else ""),''',
     "video games: facts", '"pcgamingwiki_confirms"')

# gen_v2 needs `re` for the infobox scrape
s = open("gen_v2.py").read()
if not re.search(r"^import re$", s, re.M):
    i = s.index("\n", s.index("import "))
    open("gen_v2.py", "w").write(s[:i + 1] + "import re\n" + s[i + 1:])
    print("[ok]   gen_v2: import re")
else:
    print("[skip] gen_v2: import re (already present)")
print("done")
