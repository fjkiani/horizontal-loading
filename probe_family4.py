"""Resolve the fourth science-and-technology family.

ARIN RDAP was disproved as an independent witness: a RIPE-region ASN queried at
rdap.arin.net redirects to rdap.db.ripe.net and returns RIPE's own record, so
"ARIN confirms" is RIPE restating itself. This probe tests replacements on two
competing tracks and prints enough structure to write a generator against.

Track A -- internet numbers with a non-RIR second witness
  primary  RIPE NCC (stat.ripe.net)
  witness  PeeringDB          (independent nonprofit, already verified)
  witness  CAIDA AS Rank      (UC San Diego) -- UNVERIFIED, the point of this probe
  fallback bgp.tools / bgpview -- commercial, weaker provenance

Track B -- macromolecular structures, which would take Crossref away from the RFC
family and force that family onto Semantic Scholar + IANA
  primary  RCSB PDB (Rutgers/UCSD/UCSF)
  witness  PDBj     (Osaka University)
  witness  Crossref (10.2210 DOIs live at Crossref, NOT DataCite -- verified)

Also re-measures Semantic Scholar RFC coverage over a wider sample, because S2
404'd on RFC 9293 and a conditional witness is only usable if the miss rate is
low enough to skip past.
"""
import json
import os
import sys
import time

sys.path.insert(0, "/workspace/seal_deploy")
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net  # noqa: E402

OUT = "/workspace/seal_deploy/probe_family4.json"
results = {}


def rec(key, ok, detail, sample=None):
    results[key] = {"ok": bool(ok), "detail": detail, "sample": sample}
    flag = "OK  " if ok else "FAIL"
    print(f"[{flag}] {key}: {detail}", flush=True)
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2, default=str)


def try_json(url, body=None, timeout=30):
    try:
        raw = net.fetch(url, body=body, timeout=timeout, attempts=2,
                        base_sleep=1.0, use_cache=False)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return json.loads(raw), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- Track A ---
print("=" * 72)
print("TRACK A -- internet number resources")
print("=" * 72)

# AS3333 is RIPE NCC's own network (RIPE region). AS7018 is AT&T (ARIN region).
# AS2914 is NTT (multi-region). A witness must cover all three or it is a
# regional echo like ARIN turned out to be.
ASNS = [3333, 7018, 2914]

for asn in ASNS:
    j, err = try_json(f"https://api.asrank.caida.org/v2/restful/asn/{asn}")
    if err:
        rec(f"caida_asn_{asn}", False, err)
        continue
    data = (j or {}).get("data", {}).get("asn")
    if not data:
        rec(f"caida_asn_{asn}", False, f"no data node; keys={list((j or {}).keys())}",
            sample=str(j)[:300])
        continue
    rec(f"caida_asn_{asn}", True,
        f"asn={data.get('asn')} name={data.get('asnName')!r} "
        f"country={(data.get('country') or {}).get('iso')} "
        f"rank={(data.get('rank'))} org={(data.get('organization') or {}).get('orgName')!r}",
        sample={k: data.get(k) for k in ("asn", "asnName", "rank", "cone", "asnDegree")})
    time.sleep(0.4)

# PeeringDB re-verification across the same three, since it is the one witness
# already believed good.
for asn in ASNS:
    j, err = try_json(f"https://www.peeringdb.com/api/net?asn={asn}")
    if err:
        rec(f"peeringdb_asn_{asn}", False, err)
        continue
    d = (j or {}).get("data") or []
    if not d:
        rec(f"peeringdb_asn_{asn}", False, "empty data list (network not registered)")
        continue
    rec(f"peeringdb_asn_{asn}", True,
        f"name={d[0].get('name')!r} org={d[0].get('org_id')} "
        f"info_type={d[0].get('info_type')!r}",
        sample={k: d[0].get(k) for k in ("asn", "name", "aka", "info_type", "created")})
    time.sleep(0.4)

# RIPEstat as primary: does it expose an enumerable collection with a field the
# service cannot sort on? announced-prefixes per ASN is the candidate.
for asn in ASNS:
    j, err = try_json(
        f"https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS{asn}")
    if err:
        rec(f"ripestat_prefixes_{asn}", False, err)
        continue
    pfx = ((j or {}).get("data") or {}).get("prefixes") or []
    rec(f"ripestat_prefixes_{asn}", True, f"{len(pfx)} announced prefixes",
        sample=[p.get("prefix") for p in pfx[:5]])
    time.sleep(0.4)

# An explicit enumerable RIPE collection: all ASNs allocated to one country.
# country-resource-list gives the roster the generator would rank over.
j, err = try_json(
    "https://stat.ripe.net/data/country-resource-list/data.json?resource=IS")
if err:
    rec("ripestat_country_list_IS", False, err)
else:
    res = ((j or {}).get("data") or {}).get("resources") or {}
    asns = res.get("asn") or []
    rec("ripestat_country_list_IS", True,
        f"Iceland: {len(asns)} ASN entries, {len(res.get('ipv4') or [])} ipv4 blocks",
        sample=asns[:10])

# ---------------------------------------------------------------- Track B ---
print()
print("=" * 72)
print("TRACK B -- macromolecular structures")
print("=" * 72)

PDB_IDS = ["1TUP", "6P5Z", "7BV2"]

for pid in PDB_IDS:
    j, err = try_json(f"https://data.rcsb.org/rest/v1/core/entry/{pid.lower()}")
    if err:
        rec(f"rcsb_entry_{pid}", False, err)
        continue
    ai = (j or {}).get("rcsb_accession_info") or {}
    cite = ((j or {}).get("citation") or [{}])[0]
    rec(f"rcsb_entry_{pid}", True,
        f"released={ai.get('initial_release_date')} "
        f"title={((j or {}).get('struct') or {}).get('title')!r:.70} "
        f"cite_doi={cite.get('pdbx_database_id_doi')}",
        sample={"release": ai.get("initial_release_date"),
                "resolution": ((j or {}).get("rcsb_entry_info") or {})
                .get("resolution_combined")})
    time.sleep(0.3)

for pid in PDB_IDS:
    j, err = try_json(
        f"https://pdbj.org/rest/newweb/search/pdb?query={pid.lower()}")
    if err:
        rec(f"pdbj_{pid}", False, err)
        continue
    rows = (j or {}).get("results") or []
    hit = [r for r in rows if r and str(r[0]).lower() == pid.lower()]
    rec(f"pdbj_{pid}", bool(hit),
        f"total={j.get('total')} exact_hit={bool(hit)} "
        f"title={(hit[0][1] if hit else None)!r:.60}")
    time.sleep(0.3)

for pid in PDB_IDS:
    j, err = try_json(
        f"https://api.crossref.org/works/10.2210/pdb{pid.lower()}/pdb")
    if err:
        rec(f"crossref_pdb_{pid}", False, err)
        continue
    m = (j or {}).get("message") or {}
    rec(f"crossref_pdb_{pid}", True,
        f"title={(m.get('title') or [None])[0]!r:.60} "
        f"publisher={m.get('publisher')!r}")
    time.sleep(0.3)

# ------------------------------------------------- Semantic Scholar on RFCs --
print()
print("=" * 72)
print("SEMANTIC SCHOLAR RFC COVERAGE (conditional-witness feasibility)")
print("=" * 72)

RFCS = [9110, 9111, 9112, 9113, 9114, 9293, 8949, 7748, 8446, 6749,
        7231, 5246, 9000, 8259, 3986]
s2_hit, s2_miss = [], []
for n in RFCS:
    j, err = try_json(
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:10.17487/RFC{n}"
        "?fields=title,year,externalIds")
    if err or not j:
        s2_miss.append(n)
    else:
        s2_hit.append(n)
    time.sleep(1.1)  # S2 unauthenticated is ~1 rps
rec("s2_rfc_coverage", len(s2_hit) >= 10,
    f"{len(s2_hit)}/{len(RFCS)} RFCs present at Semantic Scholar; "
    f"missing={s2_miss}", sample={"hit": s2_hit, "miss": s2_miss})

# IANA per-RFC cross-reference: is there a machine-readable per-RFC index?
j, err = try_json("https://www.iana.org/assignments/media-types/media-types.xml")
if err:
    # xml, not json -- fetch raw
    try:
        raw = net.fetch("https://www.iana.org/assignments/media-types/media-types.xml",
                        timeout=40, attempts=2, use_cache=False)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        needle = 'type="rfc"'
        rec("iana_media_types_xml", True,
            f"{len(raw)} bytes, {raw.count(needle)} rfc xrefs",
            sample=raw[:200])
    except Exception as exc:  # noqa: BLE001
        rec("iana_media_types_xml", False, f"{type(exc).__name__}: {exc}")

print()
print("=" * 72)
print("SUMMARY")
print("=" * 72)
for k, v in results.items():
    print(f"  {'OK  ' if v['ok'] else 'FAIL'}  {k}")
print(f"\nwrote {OUT}")
