"""Second pass on family 4, plus a mechanical ECHO test for every witness.

Two things drove this probe.

1. CAIDA AS Rank returned HTTP 500 on the /v2/restful/asn/<n> path for all three
   test ASNs. Before abandoning it, try the plural path and the GraphQL endpoint,
   and try one commercial fallback (bgpview) so the decision is made on evidence.

2. The PDB track came back fully green, but the Crossref record for 10.2210 says
   publisher "Worldwide Protein Data Bank" -- i.e. Crossref is holding a record
   that wwPDB DEPOSITED. That is the same class of problem as ARIN, one step
   weaker: not a redirect to the primary's own server, but a copy the primary
   handed over. The existing gate counts operators, not provenance, so it cannot
   see the difference. This probe measures the distinction directly by following
   redirects and recording where the bytes actually came from.

ECHO TEST: request the witness URL, follow redirects, compare the registrable
domain of the FINAL url against the requested one. ARIN failed this (arin.net ->
db.ripe.net). A witness that silently resolves to the primary's own host carries
zero independent information and must not be counted.
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.request

sys.path.insert(0, "/workspace/seal_deploy")
os.environ.setdefault("SEAL_NET_CACHE", "/workspace/seal_cache")

import net  # noqa: E402
import source_gate as sg  # noqa: E402

OUT = "/workspace/seal_deploy/probe_family4b.json"
results = {}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def rec(key, ok, detail, sample=None):
    results[key] = {"ok": bool(ok), "detail": detail, "sample": sample}
    print(f"[{'OK  ' if ok else 'FAIL'}] {key}: {detail}", flush=True)
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2, default=str)


def echo_test(url, label):
    """Follow redirects; report the final host. Catches ARIN-style proxying."""
    req = urllib.request.Request(url, headers={"User-Agent": "seal-probe/1.0",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_CTX) as resp:
            final = resp.geturl()
            body = resp.read(400)
    except Exception as exc:  # noqa: BLE001
        rec(f"echo::{label}", False, f"{type(exc).__name__}: {exc}")
        return None
    req_dom = sg.registrable_domain(url)
    fin_dom = sg.registrable_domain(final)
    same = (req_dom == fin_dom)
    rec(f"echo::{label}", same,
        f"requested {req_dom} -> served {fin_dom} "
        f"{'(no cross-operator redirect)' if same else '*** ECHO: REDIRECTED OFF-OPERATOR ***'}",
        sample={"final_url": final, "head": body[:200].decode("utf-8", "replace")})
    return fin_dom


def try_json(url, body=None, timeout=30, attempts=2):
    try:
        raw = net.fetch(url, body=body, timeout=timeout, attempts=attempts,
                        base_sleep=1.0, use_cache=False)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return json.loads(raw), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


ASNS = [3333, 7018, 2914]

print("=" * 72)
print("CAIDA -- alternate paths")
print("=" * 72)
for path in ["https://api.asrank.caida.org/v2/restful/asns/3333",
             "https://api.asrank.caida.org/v2/restful/asn/3333/",
             "https://api.asrank.caida.org/v2/restful/asns/3333/"]:
    j, err = try_json(path, attempts=1)
    rec(f"caida_alt::{path.rsplit('/v2', 1)[1]}", j is not None,
        err or f"keys={list(j.keys())}", sample=str(j)[:300])
    time.sleep(0.5)

# GraphQL form
gql = {"query": "{ asn(asn:\"3333\"){ asn asnName rank organization{ orgName } } }"}
j, err = try_json("https://api.asrank.caida.org/v2/graphql", body=gql, attempts=1)
rec("caida_graphql", j is not None, err or str(j)[:250])

print()
print("=" * 72)
print("BGPVIEW -- commercial fallback")
print("=" * 72)
for asn in ASNS:
    j, err = try_json(f"https://api.bgpview.io/asn/{asn}", attempts=1)
    if err:
        rec(f"bgpview_{asn}", False, err)
        continue
    d = (j or {}).get("data") or {}
    rec(f"bgpview_{asn}", bool(d.get("asn")),
        f"name={d.get('name')!r} desc={d.get('description_short')!r} "
        f"rir={d.get('rir_allocation', {}).get('rir_name')!r}")
    time.sleep(1.0)

print()
print("=" * 72)
print("ECHO TEST -- every witness endpoint in play")
print("=" * 72)
ECHO_TARGETS = [
    # the disproved control: must FAIL, proving the test has power
    ("https://rdap.arin.net/registry/autnum/3333", "arin_rdap_ripe_asn"),
    ("https://rdap.arin.net/registry/autnum/7018", "arin_rdap_arin_asn"),
    # family 1 -- vulnerabilities
    ("https://services.nvd.nist.gov/rest/json/cves/2.0?cveId=CVE-2023-34095", "nvd"),
    ("https://cveawg.mitre.org/api/cve/CVE-2023-34095", "mitre_cve"),
    ("https://api.first.org/data/v1/epss?cve=CVE-2023-34095", "first_epss"),
    # family 2 -- internet standards
    ("https://www.rfc-editor.org/rfc/rfc9110.json", "rfc_editor"),
    ("https://api.crossref.org/works/10.17487/RFC9110", "crossref_rfc"),
    ("https://api.semanticscholar.org/graph/v1/paper/DOI:10.17487/RFC9110?fields=title",
     "s2_rfc"),
    # family 3 -- software supply chain
    ("https://pypi.org/pypi/flask/json", "pypi"),
    ("https://api.deps.dev/v3alpha/systems/pypi/packages/flask", "depsdev"),
    ("https://archive.softwareheritage.org/api/1/origin/search/flask/?limit=5", "swh"),
    # family 4 track A -- internet numbers
    ("https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS3333", "ripestat"),
    ("https://www.peeringdb.com/api/net?asn=3333", "peeringdb"),
    # family 4 track B -- structures
    ("https://data.rcsb.org/rest/v1/core/entry/1tup", "rcsb"),
    ("https://pdbj.org/rest/newweb/search/pdb?query=1tup", "pdbj"),
    ("https://api.crossref.org/works/10.2210/pdb1tup/pdb", "crossref_pdb"),
]
for url, label in ECHO_TARGETS:
    echo_test(url, label)
    time.sleep(0.4)

print()
print("=" * 72)
print("DEPOSIT-vs-ORIGINATION -- who authored the witness record?")
print("=" * 72)
# Crossref exposes the depositing member. If the member IS the primary, the
# witness is a deposit copy (tier 1), not an independent derivation (tier 2+).
for doi, label in [("10.17487/RFC9110", "crossref_rfc_member"),
                   ("10.2210/pdb1tup/pdb", "crossref_pdb_member"),
                   ("10.48550/arXiv.2203.11011", "crossref_arxiv_member")]:
    j, err = try_json(f"https://api.crossref.org/works/{doi}", attempts=1)
    if err:
        rec(label, False, err)
        continue
    m = (j or {}).get("message") or {}
    rec(label, True,
        f"publisher={m.get('publisher')!r} member={m.get('member')!r} "
        f"prefix={m.get('prefix')!r} deposited="
        f"{(m.get('deposited') or {}).get('date-time')!r}")
    time.sleep(0.5)

# PeeringDB self-registration check: is the record authored by the network, or
# harvested from an RIR? PeeringDB records carry their own org and timestamps.
j, err = try_json("https://www.peeringdb.com/api/net?asn=3333", attempts=1)
if not err:
    d = ((j or {}).get("data") or [{}])[0]
    rec("peeringdb_provenance", True,
        f"created={d.get('created')} updated={d.get('updated')} "
        f"policy_url={d.get('policy_general')!r} "
        f"fields_absent_from_ripe={sorted(set(d) & {'info_traffic','info_ratio','policy_general','irr_as_set'})}")

print()
print("=" * 72)
for k, v in results.items():
    print(f"  {'OK  ' if v['ok'] else 'FAIL'}  {k}")
print(f"\nwrote {OUT}")
