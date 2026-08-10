#!/usr/bin/env python3
"""Round-3 repair of gen_legal and gen_sports.

gen_sports
  Retrosheet BIOFILE prints BIRTHDATE zero-padded (`09/16/1959`). The round-2
  probe happened to sample `12/27/1981`, where padding is invisible, so the
  generator compared a formatted string `9/16/1959` that can never match for
  any single-digit month or day. Comparing formatted date STRINGS was the
  defect; the fix parses both sides to date objects.

gen_legal
  The per-hand-down-day framing cannot be enumerated. CourtListener returns
  every separate opinion document as a row, so a SCOTUS order-list day reaches
  826 rows (1994-01-10) against a hard 20-row page, and the days small enough
  to enumerate in one page are order pages whose U.S. Reports cites Cornell LII
  does not carry. Replaced with a genuinely closed collection: one volume of
  United States Reports, distributed by the Caselaw Access Project as a single
  static file (volume 504 = 913 cases, one request, no paging).
"""
import re
import sys

SRC = "category_traps.py"
src = open(SRC).read()
orig_len = len(src)


def sub_once(old, new, label):
    global src
    if old not in src:
        print(f"  MISS {label}", file=sys.stderr)
        return False
    src = src.replace(old, new, 1)
    print(f"  ok  {label}")
    return True


# --------------------------------------------------------------------------
# 1. sports: compare parsed dates, never formatted date strings
# --------------------------------------------------------------------------
OLD_SPORTS = '''        y, m, d = best["birthDate"].split("-")
        want = f"{int(m)}/{int(d)}/{y}"
        last = plain.rsplit(" ", 1)[-1]
        hit = next((r for r in bio
                    if r.get("BIRTHDATE") == want and _norm(r.get("LAST")) == _norm(last)),
                   None)
        if not hit:
            tried.append(f"{team_name} {season}: Retrosheet has no {last} born {want}")
            continue'''

NEW_SPORTS = '''        want = _iso_date(best["birthDate"])
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
            continue'''

sub_once(OLD_SPORTS, NEW_SPORTS, "gen_sports date parsing")

# --------------------------------------------------------------------------
# 2. shared date helpers next to the Retrosheet loader
# --------------------------------------------------------------------------
ANCHOR = '_RETRO_BIO = "https://www.retrosheet.org/BIOFILE.TXT"'
HELPERS = '''_CAP_VOL = "https://static.case.law/us/{vol}/CasesMetadata.json"


def _iso_date(s):
    """`1959-09-16` -> date, else None."""
    try:
        return _dt.date.fromisoformat((s or "").strip()[:10])
    except ValueError:
        return None


def _retro_date(s):
    """Retrosheet `09/16/1959` (or `9/16/1959`) -> date, else None."""
    m = re.match(r"^\\s*(\\d{1,2})/(\\d{1,2})/(\\d{4})\\s*$", s or "")
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


_RETRO_BIO = "https://www.retrosheet.org/BIOFILE.TXT"'''

sub_once(ANCHOR, HELPERS, "legal/sports date + CAP helpers")

if "import datetime as _dt" not in src:
    sub_once("import re\n", "import re\nimport datetime as _dt\n", "datetime import")

# --------------------------------------------------------------------------
# 3. replace gen_legal wholesale
# --------------------------------------------------------------------------
NEW_LEGAL = '''def gen_legal(vols=(504, 505, 498, 510, 512, 517)):
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
            category="legal", field="United States Reports page", answer=answer,
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


'''

i = src.find("def gen_legal(")
if i < 0:
    print("  MISS gen_legal anchor", file=sys.stderr)
    sys.exit(1)
j = src.find("\n# ====", i)
if j < 0:
    print("  MISS gen_legal terminator", file=sys.stderr)
    sys.exit(1)
src = src[:i] + NEW_LEGAL + src[j + 1:]
print("  ok  gen_legal replaced")

open(SRC, "w").write(src)
print(f"\npatched {SRC}: {orig_len} -> {len(src)} chars")
