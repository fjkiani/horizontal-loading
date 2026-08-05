"""
join_engine.py — N+1 join machinery for Project Seal.

Computes ground truths by executing the FULL join over real data:
  BASE table (N candidates)  +  per-candidate SIDEQUEST (external source)
and proves the constraint isolates exactly ONE candidate.

Every compute_*() returns:
  answer    : the derived atomic ground truth (NOT declared — computed)
  trace     : the elimination steps (golden trajectory)
  payload   : the data the blind solver will see
  unique    : bool — True iff exactly one candidate satisfies the constraint
  n_base    : size of the base candidate set
"""
from __future__ import annotations
import hashlib, json, re, ssl, time, urllib.request, urllib.parse, os
from collections import Counter

_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE
_REPO = os.path.dirname(os.path.abspath(__file__))
_CACHE = os.environ.get("SEAL_CACHE_DIR", os.path.join(_REPO, ".seal_cache"))
os.makedirs(_CACHE, exist_ok=True)


def _ck(url):
    # Hash the FULL url(+body) so distinct parameters never collide. The previous
    # 180-char truncation cut off trailing fields (e.g. fiscal_years), causing
    # different queries to share one cache file and return the wrong data.
    return os.path.join(_CACHE, "h_" + hashlib.sha256(url.encode()).hexdigest()[:40])


def get(url, timeout=45, as_json=False, retries=5, sec=False):
    ck = _ck(url)
    if os.path.exists(ck):
        raw = open(ck, "rb").read()
        return json.loads(raw) if as_json else raw.decode("utf-8", "replace")
    last = None
    for i in range(retries):
        try:
            ua = ("SealResearch admin@example.com" if sec else
                  "SealResearchBot/1.0 (contact: research@example.org) Python-urllib")
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                data = r.read()
            open(ck, "wb").write(data)
            return json.loads(data) if as_json else data.decode("utf-8", "replace")
        except Exception as e:
            last = e; time.sleep(1.5 * (i + 1))
    raise last


def post(url, body, timeout=70, retries=4):
    key = _ck(url + json.dumps(body, sort_keys=True))
    if os.path.exists(key):
        return json.loads(open(key, "rb").read())
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "seal/1.0"})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                data = r.read()
            open(key, "wb").write(data)
            return json.loads(data)
        except Exception as e:
            last = e; time.sleep(1.5 * (i + 1))
    raise last


# ---------------------------------------------------------------------------
# BASE: Nobel laureates across a year range (100% coverage, columnar sidequest)
# ---------------------------------------------------------------------------
def nobel_range(y0, y1, cat):
    out = []
    for yr in range(y0, y1 + 1):
        url = (f"https://api.nobelprize.org/2.1/laureates?nobelPrizeYear={yr}"
               f"&nobelPrizeCategory={cat}")
        for l in get(url, as_json=True).get("laureates", []):
            name = l.get("fullName", {}).get("en") or l.get("orgName", {}).get("en", "")
            birth = l.get("birth", {}).get("place", {})
            prize = (l.get("nobelPrizes") or [{}])[0]
            out.append({"year": yr, "name": name,
                        "city": birth.get("city", {}).get("en", ""),
                        "country": birth.get("country", {}).get("en", ""),
                        "portion": prize.get("portion", "")})
        time.sleep(0.2)
    return out


# Historical birth-city -> modern country (the external sidequest a model must resolve).
# This is the N+1 knowledge join: no API returns "modern country of a defunct-state city".
MODERN_COUNTRY = {
    "Lennep": "Germany", "Arnhem": "Netherlands", "Zonnemaire": "Netherlands",
    "Paris": "France", "Warsaw": "Poland", "Langford Grove, Maldon, Essex": "United Kingdom",
    "Pressburg": "Slovakia", "Cheetham Hill": "United Kingdom", "Strelno": "Poland",
    "Hollerich": "Luxembourg", "Fulda": "Germany", "Bologna": "Italy", "Leiden": "Netherlands",
    "Strasbourg": "France", "Munich": "Germany", "Vienna": "Austria", "Gdansk": "Poland",
    "Kiel": "Germany", "Frankfurt": "Germany", "Hamburg": "Germany", "Karlsruhe": "Germany",
    "Würzburg": "Germany", "Ghent": "Belgium", "Zürich": "Switzerland", "Moscow": "Russia",
    "St Petersburg": "Russia", "Budapest": "Hungary", "Prague": "Czech Republic",
}


def compute_nobel_modern_country(y0, y1, cat, target_modern_country):
    """Unique laureate whose historical birth city lies in target_modern_country today."""
    base = nobel_range(y0, y1, cat)
    matches = [l for l in base if MODERN_COUNTRY.get(l["city"]) == target_modern_country]
    trace = [f"List all {cat} laureates {y0}-{y1} ({len(base)} candidates)",
             f"For each, resolve the MODERN country of their historical birth city",
             f"Keep those whose birth city is today in {target_modern_country}",
             f"Survivors: {[m['name'] for m in matches]}"]
    payload = "\n".join(f"{l['year']} | {l['name']} | born {l['city']}, {l['country']}"
                        for l in base)
    return {"answer": matches[0]["name"] if len(matches) == 1 else None,
            "trace": trace, "payload": payload,
            "unique": len(matches) == 1, "n_base": len(base),
            "survivors": [m["name"] for m in matches]}


def compute_nobel_portion(year, cat, target_portion):
    """Unique laureate holding a specific fractional share in a shared-prize year."""
    base = nobel_range(year, year, cat)
    matches = [l for l in base if l["portion"] == target_portion]
    trace = [f"List {year} {cat} laureates ({len(base)})",
             f"Compare portions", f"The {target_portion} share: {[m['name'] for m in matches]}"]
    payload = "\n".join(f"{l['name']} | portion {l['portion']} | born {l['city']}" for l in base)
    return {"answer": matches[0]["name"] if len(matches) == 1 else None,
            "trace": trace, "payload": payload,
            "unique": len(matches) == 1, "n_base": len(base),
            "survivors": [m["name"] for m in matches]}


# ---------------------------------------------------------------------------
# BASE: Wikipedia list/table -> per-row member-page infobox (true N+1)
# ---------------------------------------------------------------------------
def wiki_wikitext(page):
    url = ("https://en.wikipedia.org/w/api.php?action=parse&page="
           + urllib.parse.quote(page) + "&format=json&prop=wikitext")
    return get(url, as_json=True)["parse"]["wikitext"]["*"]


def wiki_birthdate(pagename):
    try:
        wt = wiki_wikitext(pagename)
    except Exception:
        return None
    for pat in [r"\{\{[Bb]irth date and age\|(\d+)\|(\d+)\|(\d+)",
                r"\{\{[Bb]irth date\|(\d+)\|(\d+)\|(\d+)"]:
        m = re.search(pat, wt)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def wikidata_dob(name, must_contain=""):
    q = urllib.parse.quote(name)
    try:
        j = get(f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={q}"
                f"&language=en&format=json&limit=5", as_json=True)
    except Exception:
        return None
    for ent in j.get("search", []):
        desc = (ent.get("display", {}).get("description", {}).get("value", "") or "").lower()
        if must_contain and must_contain not in desc:
            continue
        qid = ent.get("id")
        try:
            ej = get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", as_json=True)
            c = ej["entities"][qid]["claims"]
            if "P569" in c:
                t = c["P569"][0]["mainsnak"]["datavalue"]["value"]["time"]
                m = re.match(r"\+(\d+)-(\d+)-(\d+)T", t)
                if m:
                    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# BASE: NIH RePORTER full-row set (ranking / row-skip trap)
# ---------------------------------------------------------------------------
def nih_projects(fy, terms, limit=500):
    body = {"criteria": {"fiscal_years": fy,
                         "advanced_text_search": {"operator": "and",
                                                  "search_field": "projecttitle,terms",
                                                  "search_text": terms},
                         "include_fields": ["ProjectNum", "ProjectTitle", "FiscalYear",
                                            "AwardAmount", "Organization"]},
            "limit": min(limit, 500)}
    j = post("https://api.reporter.nih.gov/v2/projects/search", body)
    return j.get("results", []), j.get("meta", {})


def _nih_all_rows(fy, terms, max_pages=6):
    """Fetch ALL matching rows via 500-cap offset pages, DEDUP by project_num.

    The NIH API (a) caps limit at 500, (b) SILENTLY IGNORES sort_field, and
    (c) over-fetches duplicates across offset pages. So a single call can neither
    see the full set nor return it sorted -- the model must paginate + dedup + sort.
    """
    fy_list = fy if isinstance(fy, list) else [fy]
    all_rows, offset, total = [], 0, None
    for _ in range(max_pages):
        # Stop before requesting an offset past the record count (NIH returns 400).
        if total is not None and offset >= total:
            break
        body = {"criteria": {"fiscal_years": fy_list,
                             "advanced_text_search": {"operator": "and",
                                                      "search_field": "projecttitle,terms",
                                                      "search_text": terms},
                             "include_fields": ["ProjectNum", "AwardAmount", "Organization"]},
                "limit": 500, "offset": offset}
        j = post("https://api.reporter.nih.gov/v2/projects/search", body)
        rows = j.get("results", [])
        total = j.get("meta", {}).get("total", 0)
        all_rows.extend(rows)
        if not rows or len(all_rows) >= total:
            break
        offset += 500
        time.sleep(1.0)
    seen = {r.get("project_num"): r for r in all_rows if r.get("project_num")}
    return list(seen.values()), total


def compute_nih_rank(fy, terms, rank_from_bottom):
    """The project with the Nth-lowest award amount (row-skip ranking trap).

    API-proof: requires multi-page fetch + dedup + sort; no single API call returns
    the sorted answer (sort_field is ignored by the API)."""
    rows, total = _nih_all_rows(fy, terms)
    amt = sorted([r for r in rows
                  if isinstance(r.get("award_amount"), (int, float)) and r.get("award_amount") > 0],
                 key=lambda r: (r["award_amount"], r.get("project_num")))
    if not (1 <= rank_from_bottom <= len(amt)):
        return {"answer": None, "unique": False, "n_base": len(rows),
                "trace": ["rank out of range"], "payload": "", "api_proof": False}
    target = amt[rank_from_bottom - 1]
    ties = [r for r in amt if r["award_amount"] == target["award_amount"]]
    org = (target.get("organization") or {}).get("org_name", "")
    trace = [f"Retrieve ALL {terms} projects for FY{fy} (paginate past the 500/call cap; "
             f"the API ignores sort_field, so fetch every page)",
             f"Deduplicate by project_num ({len(rows)} unique projects)",
             f"Sort by award_amount ascending",
             f"Take the {rank_from_bottom}th from the bottom",
             f"= {target.get('project_num')} ({org}) at ${target['award_amount']}"]
    payload = "\n".join(f"{r.get('project_num')} | {(r.get('organization') or {}).get('org_name','')} | {r.get('award_amount')}"
                        for r in amt)
    return {"answer": target.get("project_num"), "trace": trace, "payload": payload,
            "unique": len(ties) == 1, "n_base": len(rows),
            "survivors": [t.get("project_num") for t in ties],
            "api_proof": True}


# ---------------------------------------------------------------------------
# BASE: SEC EDGAR concept (ranking across a time series)
# ---------------------------------------------------------------------------
def sec_concept(cik, concept):
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"
    j = get(url, as_json=True, sec=True)
    return j.get("entityName", ""), j.get("units", {}).get("USD", []), url


def compute_sec_extreme(cik, concept, mode="earliest"):
    name, rows, url = sec_concept(cik, concept)
    dated = [r for r in rows if r.get("end")]
    dated.sort(key=lambda r: r["end"])
    if not dated:
        return {"answer": None, "unique": False, "n_base": 0, "trace": ["no dated rows"], "payload": ""}
    target = dated[0] if mode == "earliest" else dated[-1]
    ties = [r for r in dated if r["end"] == target["end"]]
    trace = [f"Fetch {name} us-gaap {concept} series ({len(dated)} dated entries)",
             f"Sort by 'end' date", f"{mode.capitalize()} end = {target['end']}"]
    payload = "\n".join(f"{r['end']} | {r.get('val')} | {r.get('form')}" for r in dated)
    return {"answer": target["end"], "trace": trace, "payload": payload,
            "unique": True, "n_base": len(dated), "survivors": [target["end"]]}


# ---------------------------------------------------------------------------
# VERTICAL: LOC newspaper OCR (extraction trap — answer visually obvious, OCR-degraded)
# ---------------------------------------------------------------------------
def loc_page_ocr(resource_url):
    meta = get(resource_url + ("&fo=json" if "?" in resource_url else "?fo=json"), as_json=True)
    fs = meta.get("fulltext_service")
    if not fs:
        return ""
    j = get(fs, as_json=True)
    return list(j.values())[0].get("full_text", "")


def compute_loc_extract(resource_url, needle_regex, answer, context=120):
    """Confirm the answer string is present in the OCR (possibly degraded) and extract context."""
    txt = loc_page_ocr(resource_url)
    m = re.search(needle_regex, txt, re.I)
    present = bool(m)
    snippet = ""
    if m:
        i = max(0, m.start() - context)
        snippet = re.sub(r"\s+", " ", txt[i:m.end() + context])
    trace = [f"Open LOC scan {resource_url}", f"Locate the target notice",
             f"Read the value (OCR-degraded)", f"= {answer}"]
    return {"answer": answer if present else None, "trace": trace, "payload": txt,
            "unique": present, "n_base": 1, "survivors": [answer] if present else [],
            "snippet": snippet}


# ---------------------------------------------------------------------------
# VERTICAL (vision-true): LOC scan IMAGE extraction.
# The answer is readable ONLY in the page image; the OCR text layer is corrupted
# and does NOT contain the answer. API-proof: no text API returns the value.
# The solver payload is the IMAGE FILE PATH, not the OCR text.
# ---------------------------------------------------------------------------
def loc_page_image(resource_url, out_path, pct=20):
    """Download the LOC scan image (binary) for a resource page. Returns out_path."""
    meta = get(resource_url + ("&fo=json" if "?" in resource_url else "?fo=json"), as_json=True)
    img = meta["resources"][0]["image"]
    img = re.sub(r"pct:\d+(\.\d+)?", f"pct:{pct}", img)
    if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
        return out_path
    req = urllib.request.Request(img, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"})
    with urllib.request.urlopen(req, timeout=90, context=_CTX) as r:
        data = r.read()
    open(out_path, "wb").write(data)
    return out_path


def compute_vision_extract(resource_url, image_path, answer, forbidden_strings,
                           field_desc, pct=20):
    """Vision-true vertical trap.

    Asserts API-proofing: the OCR text layer must NOT contain any answer-bearing
    string (so a model that only calls the OCR/text API cannot answer). The answer
    is recorded from the IMAGE (verified by visual inspection). The solver payload
    is the image file path; the OCR is provided only as a (misleading) distractor.
    """
    ocr = loc_page_ocr(resource_url)
    up = ocr.upper()
    leaks = [f for f in forbidden_strings if f.upper() in up]
    api_proof = len(leaks) == 0
    loc_page_image(resource_url, image_path, pct=pct)
    trace = [f"Open the LOC scan IMAGE for {resource_url}",
             f"Read the {field_desc} directly from the masthead (the OCR text layer is corrupted)",
             f"= {answer}"]
    return {"answer": answer, "trace": trace,
            "payload": ocr,                      # distractor text (corrupted)
            "image_path": image_path,            # the REAL payload the solver must read
            "unique": True, "n_base": 1, "survivors": [answer],
            "api_proof": api_proof, "ocr_leaks": leaks}


# ---------------------------------------------------------------------------
# VERTICAL: Google Patents dense-doc extraction
# ---------------------------------------------------------------------------
def patent_text(patent_id):
    url = f"https://patents.google.com/patent/{patent_id}/en"
    html = get(url)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text), url


def compute_patent_field(patent_id, field_regex, answer):
    text, url = patent_text(patent_id)
    present = bool(re.search(field_regex, text, re.I))
    trace = [f"Open patent {patent_id}", f"Locate the target field in the dense text",
             f"= {answer}"]
    return {"answer": answer if present else None, "trace": trace, "payload": text,
            "unique": present, "n_base": 1, "survivors": [answer] if present else []}
