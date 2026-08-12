"""Repair the two designs the first probe falsified.

F2 was a PARSER BUG, not a data gap. <page-count> is a SIBLING of <format> in
rfc-index.xml, not a child of it, so the probe read 0 page counts out of a
document containing 19,646 of them. Re-run with the correct path.

F3 was a real design failure. Ranking PyPI releases by number of distributed
files ties everywhere because the modern default is exactly two artefacts per
release (one sdist, one pure-Python wheel): flask tied 46 ways, rich tied 207
ways, 8 of 10 packages unusable. The key is not wrong, the SEED ROSTER was --
pure-Python projects have no wheel matrix to vary. Retest on packages that ship
compiled platform wheels, where the per-release artefact count is genuinely
dispersed, and measure the dispersion rather than assuming it.

Also finishes the third-witness question for F3, which the first probe left
open: deps.dev and ecosyste.ms both bind a VERSION, Software Heritage binds only
an ORIGIN. A witness that cannot see the answer is not a witness.
"""
import json
import os
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

sys.path.insert(0, "/workspace/seal_deploy")
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net  # noqa: E402

OUT = "/workspace/seal_deploy/probe_designs2.json"
results = {}


def rec(key, ok, detail, sample=None):
    results[key] = {"ok": bool(ok), "detail": detail, "sample": sample}
    print(f"[{'OK  ' if ok else 'FAIL'}] {key}: {detail}", flush=True)
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2, default=str)


def gj(url, timeout=60, attempts=2, cache=True):
    try:
        raw = net.fetch(url, timeout=timeout, attempts=attempts, base_sleep=2.0,
                        use_cache=cache)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return json.loads(raw), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def margin(vals):
    if not vals:
        return None, None, 0
    s = sorted(vals, reverse=True)
    return s[0], (s[1] if len(s) > 1 else None), sum(1 for c in vals if c == s[0])


# ======================================================================= F2 ==
print("=" * 74)
print("F2  RFC month listing -- page-count read from the CORRECT element path")
print("=" * 74)
raw = net.fetch("https://www.rfc-editor.org/rfc-index.xml", timeout=180)
if isinstance(raw, bytes):
    raw = raw.decode("utf-8", "replace")
root = ET.fromstring(raw)


def strip_ns(tag):
    return tag.rsplit("}", 1)[-1]


entries = [e for e in root.iter() if strip_ns(e.tag) == "rfc-entry"]
bymonth = defaultdict(list)
npages = 0
for e in entries:
    kids = {strip_ns(k.tag): k for k in e}
    docid = (kids.get("doc-id").text or "").strip() if "doc-id" in kids else ""
    if not docid.startswith("RFC"):
        continue
    pc = kids.get("page-count")
    pages = int(pc.text.strip()) if pc is not None and (pc.text or "").strip().isdigit() else None
    if pages is not None:
        npages += 1
    dn = kids.get("date")
    if dn is None:
        continue
    dk = {strip_ns(k.tag): (k.text or "").strip() for k in dn}
    mon, yr = dk.get("month"), dk.get("year")
    authors = [k for k in e if strip_ns(k.tag) == "author"]
    doi = kids.get("doi").text.strip() if "doi" in kids else ""
    status = kids.get("current-status").text.strip() if "current-status" in kids else ""
    stream = kids.get("stream").text.strip() if "stream" in kids else ""
    title = kids.get("title").text.strip() if "title" in kids else ""
    if mon and yr:
        bymonth[(yr, mon)].append({"id": docid, "num": docid[3:].lstrip("0"),
                                   "pages": pages, "nau": len(authors), "doi": doi,
                                   "status": status, "stream": stream, "title": title})
rec("f2_index", npages > 5000,
    f"{len(entries)} entries, {npages} carry page-count, {len(bymonth)} months")

good = []
for (yr, mon), rows in bymonth.items():
    rows = [r for r in rows if r["pages"] is not None]
    if not (8 <= len(rows) <= 60) or int(yr) < 2010:
        continue
    top, second, ntie = margin([r["pages"] for r in rows])
    if ntie == 1:
        w = max(rows, key=lambda r: r["pages"])
        good.append({"month": mon, "year": yr, "n": len(rows), "rfc": w["num"],
                     "pages": top, "runnerup": second, "doi": w["doi"],
                     "title": w["title"][:58]})
good.sort(key=lambda g: (-int(g["year"]), g["month"]))
rec("f2_unique_months", len(good) >= 10,
    f"{len(good)} months (2010+, 8-60 RFCs) isolate a UNIQUE page-count argmax",
    sample=good[:10])

# witness binding on several winners, incl. the Semantic Scholar coverage gate
f2_ready = []
for g in good[:10]:
    n = g["rfc"]
    j, _ = gj(f"https://api.crossref.org/works/10.17487/RFC{n}", timeout=45, attempts=1)
    cr = ((j or {}).get("message", {}).get("title") or [None])[0] if j else None
    time.sleep(0.6)
    j2, _ = gj("https://api.semanticscholar.org/graph/v1/paper/"
               f"DOI:10.17487/RFC{n}?fields=title,year", timeout=45, attempts=1)
    s2 = (j2 or {}).get("title") if j2 else None
    time.sleep(1.2)
    ok = bool(cr) and bool(s2)
    if ok:
        f2_ready.append({**g, "crossref_title": cr, "s2_title": s2})
    rec(f"f2_witness_{g['month']}{g['year']}_RFC{n}", ok,
        f"n={g['n']} pages={g['pages']}/{g['runnerup']} crossref={str(cr)[:40]!r} "
        f"s2={str(s2)[:40]!r}")
rec("f2_ready", len(f2_ready) >= 4,
    f"{len(f2_ready)}/10 candidate months pass BOTH witnesses "
    f"(Semantic Scholar coverage is the binding constraint)",
    sample=[{k: v for k, v in r.items() if k in ("month", "year", "rfc", "n", "pages")}
            for r in f2_ready])

# ======================================================================= F3 ==
print()
print("=" * 74)
print("F3  PyPI releases -- compiled-wheel packages have a dispersed file count")
print("=" * 74)
# pure-Python projects publish sdist+wheel and nothing else, so the key is
# constant. These ship a per-platform wheel matrix that changes release to
# release, which is exactly the dispersion the ranking key needs.
PKGS = ["numpy", "scipy", "pandas", "cryptography", "pillow", "lxml",
        "pyzmq", "grpcio", "psycopg2-binary", "matplotlib", "scikit-learn",
        "coverage", "aiohttp", "msgpack", "regex"]
f3_ok = []
for pkg in PKGS:
    j, err = gj(f"https://pypi.org/pypi/{pkg}/json", timeout=90)
    if err:
        rec(f"f3_{pkg}", False, err)
        continue
    rel = (j or {}).get("releases") or {}
    rows = [{"v": v, "nfiles": len(f)} for v, f in rel.items() if f]
    vals = [r["nfiles"] for r in rows]
    top, second, ntie = margin(vals)
    w = max(rows, key=lambda r: r["nfiles"]) if rows else None
    disp = round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0
    uniq = ntie == 1 and 12 <= len(rows) <= 400
    if uniq:
        f3_ok.append({"pkg": pkg, "v": w["v"], "top": top, "runnerup": second,
                      "n": len(rows), "sd": disp,
                      "distinct": len(set(vals))})
    rec(f"f3_{pkg}", uniq,
        f"{len(rows)} releases, top={top} runnerup={second} tied={ntie} "
        f"sd={disp} distinct_counts={len(set(vals))} winner={w['v'] if w else None}")
    time.sleep(0.4)
rec("f3_summary", len(f3_ok) >= 4,
    f"{len(f3_ok)}/{len(PKGS)} compiled-wheel packages isolate a unique argmax "
    f"(pure-Python roster scored 2/10)", sample=f3_ok)

# third witness: which candidates can bind a VERSION?
print()
print("-- F3 third-witness shootout (must confirm the VERSION, not the project) --")
for cand in f3_ok[:3]:
    pkg, ver = cand["pkg"], cand["v"]
    j, err = gj(f"https://api.deps.dev/v3alpha/systems/pypi/packages/{pkg}/versions/{ver}",
                timeout=45, attempts=1)
    rec(f"f3w_depsdev_{pkg}", j is not None,
        err or f"{pkg} {ver} -> {((j or {}).get('versionKey') or {}).get('version')!r} "
               f"licenses={(j or {}).get('licenses')}")
    time.sleep(0.4)
    j, err = gj(f"https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/"
                f"{pkg}/versions/{ver}", timeout=45, attempts=1)
    rec(f"f3w_ecosystems_{pkg}", j is not None,
        err or f"{pkg} {ver} -> number={(j or {}).get('number')!r} "
               f"published={(j or {}).get('published_at')!r}")
    time.sleep(0.4)
    j, err = gj(f"https://api.clearlydefined.io/definitions/pypi/pypi/-/{pkg}/{ver}",
                timeout=45, attempts=1)
    d = ((j or {}).get("described") or {}) if j else {}
    rec(f"f3w_clearlydefined_{pkg}", bool(d.get("releaseDate")),
        err or f"{pkg} {ver} -> released={d.get('releaseDate')!r}")
    time.sleep(0.4)

# negative controls: do the witnesses ECHO a fabricated version?
for pkg, badver in [("numpy", "99.98.97"), ("pandas", "0.0.0-nonexistent")]:
    j, _ = gj(f"https://api.deps.dev/v3alpha/systems/pypi/packages/{pkg}/versions/{badver}",
              timeout=30, attempts=1)
    rec(f"f3neg_depsdev_{pkg}", j is None,
        "fabricated version rejected" if j is None else f"*** ECHOED: {str(j)[:120]}")
    j, _ = gj(f"https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/"
              f"{pkg}/versions/{badver}", timeout=30, attempts=1)
    rec(f"f3neg_ecosystems_{pkg}", j is None,
        "fabricated version rejected" if j is None else f"*** ECHOED: {str(j)[:120]}")

print()
print("=" * 74)
for k, v in results.items():
    print(f"  {'OK  ' if v['ok'] else 'FAIL'}  {k}: {str(v['detail'])[:92]}")
print(f"\nwrote {OUT}")
