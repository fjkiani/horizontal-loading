"""End-to-end feasibility of the four science-and-technology generator designs.

Each family needs three things to be true at once, and only the first is cheap
to guess:

  1. an EXPLICIT enumerable collection the prompt can name without ambiguity,
  2. a ranking key on which the argmax is UNIQUE and which the serving API
     cannot sort or filter by (otherwise the solver delegates the work),
  3. two witnesses that confirm THE ANSWER, not merely the entity.

Point 3 is where the software-supply-chain design is weakest: Software Heritage
confirms that a project origin was archived, which says nothing about which
release is being asked for. This probe measures that instead of assuming it, and
tests two candidate replacements (ecosyste.ms, ClearlyDefined).

Ranking keys are deliberately DIFFERENT per family -- reference count, page
count, file count, announced-prefix count -- so the four prompts cannot converge
on one sentence the way the four arXiv seeds did.
"""
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

sys.path.insert(0, "/workspace/seal_deploy")
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net  # noqa: E402

OUT = "/workspace/seal_deploy/probe_designs.json"
results = {}


def rec(key, ok, detail, sample=None):
    results[key] = {"ok": bool(ok), "detail": detail, "sample": sample}
    print(f"[{'OK  ' if ok else 'FAIL'}] {key}: {detail}", flush=True)
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2, default=str)


def gj(url, body=None, timeout=60, attempts=2, cache=True):
    try:
        raw = net.fetch(url, body=body, timeout=timeout, attempts=attempts,
                        base_sleep=2.0, use_cache=cache)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return json.loads(raw), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def margin(counts):
    """(top value, runner-up value, number tied at top)."""
    if not counts:
        return None, None, 0
    s = sorted(counts, reverse=True)
    top = s[0]
    return top, (s[1] if len(s) > 1 else None), sum(1 for c in counts if c == top)


# ======================================================================= F1 ==
print("=" * 74)
print("F1  VULNERABILITIES -- NVD day listing ranked by reference count")
print("=" * 74)
NVD_DAYS = ["2023-06-14", "2023-09-12", "2022-11-08", "2024-02-13",
            "2021-07-13", "2023-03-14"]
f1_ok = []
for day in NVD_DAYS:
    url = ("https://services.nvd.nist.gov/rest/json/cves/2.0"
           f"?pubStartDate={day}T00:00:00.000&pubEndDate={day}T23:59:59.999"
           "&resultsPerPage=2000")
    j, err = gj(url, timeout=90)
    if err:
        rec(f"f1_{day}", False, err)
        time.sleep(7)
        continue
    tot = j.get("totalResults")
    vulns = j.get("vulnerabilities") or []
    rows = []
    for v in vulns:
        c = (v or {}).get("cve") or {}
        rows.append({"id": c.get("id"),
                     "nref": len(c.get("references") or []),
                     "desc": next((d.get("value") for d in (c.get("descriptions") or [])
                                   if d.get("lang") == "en"), "")})
    top, second, ntie = margin([r["nref"] for r in rows])
    winner = max(rows, key=lambda r: r["nref"]) if rows else None
    unique = (ntie == 1 and len(rows) == tot and 12 <= len(rows))
    if unique:
        f1_ok.append((day, winner["id"], top, second, len(rows)))
    rec(f"f1_{day}", unique,
        f"n={len(rows)} totalResults={tot} single_page={len(rows) == tot} "
        f"top={top} runnerup={second} tied_at_top={ntie} "
        f"winner={winner['id'] if winner else None}")
    time.sleep(7)  # unauthenticated NVD: 5 requests / 30 s
rec("f1_summary", len(f1_ok) >= 3,
    f"{len(f1_ok)}/{len(NVD_DAYS)} days isolate a unique argmax", sample=f1_ok)

# confirm the two witnesses can bind the ANSWER for one winner
if f1_ok:
    cve = f1_ok[0][1]
    j, err = gj(f"https://cveawg.mitre.org/api/cve/{cve}", timeout=60)
    if err:
        rec("f1_witness_mitre", False, err)
    else:
        cna = ((j or {}).get("containers") or {}).get("cna") or {}
        d = next((x.get("value") for x in (cna.get("descriptions") or [])), "")
        rec("f1_witness_mitre", bool(d), f"{cve} -> {d[:110]!r}")
    j, err = gj(f"https://api.first.org/data/v1/epss?cve={cve}", timeout=60)
    d = ((j or {}).get("data") or [{}])[0] if not err else {}
    rec("f1_witness_first", bool(d.get("cve")),
        err or f"{cve} -> epss={d.get('epss')} percentile={d.get('percentile')}")
    # negative control: a fabricated CVE must NOT resolve
    j, err = gj("https://cveawg.mitre.org/api/cve/CVE-2023-99999", timeout=45,
                attempts=1)
    rec("f1_negative_control", j is None,
        "fabricated CVE-2023-99999 rejected by MITRE" if j is None
        else f"*** MITRE ECHOED A FABRICATED ID: {str(j)[:160]}")

# ======================================================================= F2 ==
print()
print("=" * 74)
print("F2  INTERNET STANDARDS -- RFC month listing ranked by page count")
print("=" * 74)
raw, err = None, None
try:
    raw = net.fetch("https://www.rfc-editor.org/rfc-index.xml", timeout=180)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
except Exception as exc:  # noqa: BLE001
    err = f"{type(exc).__name__}: {exc}"

if err:
    rec("f2_index", False, err)
else:
    root = ET.fromstring(raw)
    NS = {"r": "https://www.rfc-editor.org/rfc-index"}
    entries = root.findall("r:rfc-entry", NS)
    if not entries:  # namespace may be absent
        NS = {}
        entries = [e for e in root if e.tag.endswith("rfc-entry")]

    def txt(e, path):
        n = e.find(path, NS) if NS else e.find(path)
        return (n.text or "").strip() if n is not None else ""

    bymonth = defaultdict(list)
    have_pages = 0
    for e in entries:
        docid = txt(e, "r:doc-id" if NS else "doc-id")
        dnode = e.find("r:date" if NS else "date", NS) if NS else e.find("date")
        if dnode is None:
            continue
        mon = (dnode.find("r:month" if NS else "month", NS).text
               if NS else dnode.find("month").text) if dnode is not None else None
        yr = (dnode.find("r:year" if NS else "year", NS).text
              if NS else dnode.find("year").text) if dnode is not None else None
        fmts = e.findall("r:format" if NS else "format", NS) if NS else e.findall("format")
        pages = None
        for f in fmts:
            pn = f.find("r:page-count" if NS else "page-count", NS) if NS else f.find("page-count")
            if pn is not None and (pn.text or "").strip().isdigit():
                pages = int(pn.text.strip())
                break
        authors = e.findall("r:author" if NS else "author", NS) if NS else e.findall("author")
        status = txt(e, "r:current-status" if NS else "current-status")
        title = txt(e, "r:title" if NS else "title")
        if pages is not None:
            have_pages += 1
        if mon and yr and docid.startswith("RFC"):
            bymonth[(yr, mon)].append(
                {"id": docid, "num": docid[3:].lstrip("0"), "pages": pages,
                 "nau": len(authors), "status": status, "title": title})
    rec("f2_index", have_pages > 5000,
        f"{len(entries)} entries parsed, {have_pages} carry page-count, "
        f"{len(bymonth)} distinct months",
        sample=sorted(bymonth)[-3:])

    # which months isolate a unique page-count argmax at a workable size?
    good = []
    for (yr, mon), rows in sorted(bymonth.items()):
        rows = [r for r in rows if r["pages"] is not None]
        if not (8 <= len(rows) <= 60):
            continue
        top, second, ntie = margin([r["pages"] for r in rows])
        if ntie == 1 and int(yr) >= 2015:
            w = max(rows, key=lambda r: r["pages"])
            good.append({"month": f"{mon} {yr}", "n": len(rows), "rfc": w["num"],
                         "pages": top, "runnerup": second, "title": w["title"][:60]})
    rec("f2_unique_months", len(good) >= 6,
        f"{len(good)} months (2015+) isolate a unique page-count argmax",
        sample=good[:8])

    # witness binding for a few winners
    for g in good[:4]:
        n = g["rfc"]
        j, err = gj(f"https://api.crossref.org/works/10.17487/RFC{n}", timeout=45,
                    attempts=1)
        cr_title = ((j or {}).get("message", {}).get("title") or [None])[0] if j else None
        time.sleep(0.7)
        j2, err2 = gj("https://api.semanticscholar.org/graph/v1/paper/"
                      f"DOI:10.17487/RFC{n}?fields=title,year", timeout=45, attempts=1)
        s2_title = (j2 or {}).get("title") if j2 else None
        time.sleep(1.2)
        rec(f"f2_witness_RFC{n}", bool(cr_title) and bool(s2_title),
            f"crossref={str(cr_title)[:52]!r} s2={str(s2_title)[:52]!r}")

# ======================================================================= F3 ==
print()
print("=" * 74)
print("F3  SOFTWARE SUPPLY CHAIN -- PyPI releases ranked by distributed files")
print("=" * 74)
PKGS = ["flask", "requests", "click", "jinja2", "attrs", "urllib3",
        "packaging", "pytest", "rich", "httpx"]
f3_ok = []
for pkg in PKGS:
    j, err = gj(f"https://pypi.org/pypi/{pkg}/json", timeout=60)
    if err:
        rec(f"f3_{pkg}", False, err)
        continue
    rel = (j or {}).get("releases") or {}
    rows = [{"v": v, "nfiles": len(files)} for v, files in rel.items() if files]
    top, second, ntie = margin([r["nfiles"] for r in rows])
    w = max(rows, key=lambda r: r["nfiles"]) if rows else None
    uniq = ntie == 1 and len(rows) >= 12
    if uniq:
        f3_ok.append((pkg, w["v"], top, second, len(rows)))
    rec(f"f3_{pkg}", uniq,
        f"{len(rows)} releases with files, top={top} runnerup={second} "
        f"tied={ntie} winner={w['v'] if w else None}")
    time.sleep(0.4)
rec("f3_summary", len(f3_ok) >= 3,
    f"{len(f3_ok)}/{len(PKGS)} packages isolate a unique file-count argmax",
    sample=f3_ok)

# third-witness candidates -- must confirm the VERSION, not just the project
tv_pkg, tv_ver = (f3_ok[0][0], f3_ok[0][1]) if f3_ok else ("flask", "2.0.1")
j, err = gj("https://api.deps.dev/v3alpha/systems/pypi/packages/"
            f"{tv_pkg}/versions/{tv_ver}", timeout=60, attempts=1)
rec("f3_witness_depsdev", j is not None,
    err or f"{tv_pkg} {tv_ver} -> versionKey={((j or {}).get('versionKey') or {})} "
           f"licenses={(j or {}).get('licenses')}")

j, err = gj(f"https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/{tv_pkg}"
            f"/versions/{tv_ver}", timeout=60, attempts=1)
rec("f3_witness_ecosystems", j is not None,
    err or f"{tv_pkg} {tv_ver} -> number={(j or {}).get('number')!r} "
           f"published={(j or {}).get('published_at')!r}")

j, err = gj(f"https://api.clearlydefined.io/definitions/pypi/pypi/-/{tv_pkg}/{tv_ver}",
            timeout=60, attempts=1)
cd = ((j or {}).get("described") or {}) if j else {}
rec("f3_witness_clearlydefined", bool(cd),
    err or f"{tv_pkg} {tv_ver} -> released={cd.get('releaseDate')!r} "
           f"tools={(j or {}).get('_meta', {}).get('schemaVersion')!r}")

# Software Heritage: can it confirm a VERSION, or only the origin?
j, err = gj("https://archive.softwareheritage.org/api/1/origin/search/"
            f"{tv_pkg}/?limit=20", timeout=60, attempts=1)
origins = [o.get("url") for o in (j or [])] if isinstance(j, list) else []
exact = [u for u in origins if u and u.rstrip("/").endswith(f"/{tv_pkg}")]
rec("f3_witness_swh", bool(exact),
    f"{len(origins)} origins, exact project matches={exact[:3]} "
    "-- NOTE origin-level only, does not bind a version")

# ======================================================================= F4 ==
print()
print("=" * 74)
print("F4  INTERNET NUMBERS -- country ASN roster ranked by announced prefixes")
print("=" * 74)
COUNTRIES = [("IS", "Iceland"), ("MT", "Malta"), ("EE", "Estonia"),
             ("LU", "Luxembourg"), ("CY", "Cyprus")]
for cc, name in COUNTRIES[:2]:
    j, err = gj("https://stat.ripe.net/data/country-resource-list/data.json"
                f"?resource={cc}", timeout=90)
    if err:
        rec(f"f4_{cc}", False, err)
        continue
    asns = ((j or {}).get("data") or {}).get("resources", {}).get("asn") or []
    asns = [str(a) for a in asns][:40]  # bound the probe
    counts = []
    for a in asns:
        jj, e2 = gj("https://stat.ripe.net/data/announced-prefixes/data.json"
                    f"?resource=AS{a}", timeout=60, attempts=1)
        if e2:
            continue
        n = len(((jj or {}).get("data") or {}).get("prefixes") or [])
        counts.append((a, n))
        time.sleep(0.25)
    vals = [n for _, n in counts]
    top, second, ntie = margin(vals)
    w = max(counts, key=lambda t: t[1]) if counts else None
    rec(f"f4_{cc}", ntie == 1 and len(counts) >= 12,
        f"{name}: {len(asns)} ASNs listed, {len(counts)} resolved, top={top} "
        f"runnerup={second} tied={ntie} winner=AS{w[0] if w else None}")
    if w:
        jj, e2 = gj(f"https://www.peeringdb.com/api/net?asn={w[0]}", timeout=45,
                    attempts=1)
        d = ((jj or {}).get("data") or [{}])[0] if not e2 else {}
        rec(f"f4_{cc}_peeringdb", bool(d.get("asn")),
            e2 or f"AS{w[0]} -> name={d.get('name')!r}")
        jj, e2 = gj("https://api.asrank.caida.org/v2/restful/asns/"
                    f"{w[0]}", timeout=45, attempts=1)
        dd = ((jj or {}).get("data") or {}).get("asn") or {}
        rec(f"f4_{cc}_caida", bool(dd.get("asn")),
            e2 or f"AS{w[0]} -> name={dd.get('asnName')!r} rank={dd.get('rank')} "
                  f"org={(dd.get('organization') or {}).get('orgName')!r}")

print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
for k, v in results.items():
    print(f"  {'OK  ' if v['ok'] else 'FAIL'}  {k}: {str(v['detail'])[:96]}")
print(f"\nwrote {OUT}")
