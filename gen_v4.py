"""gen_v4: the finance trap, rescued from a self-witnessing refusal.

WHY FINANCE WAS REFUSED
-----------------------
The previous finance generator ranked the Daily Treasury Statement and answered
with a dollar balance. Every source that could confirm that balance was run by
the Treasury, so R3c rejected it: "a source restating itself is not a witness".
The category shipped as `unavailable`.

WHY THIS VERSION IS DIFFERENT
-----------------------------
Rank Treasury *auctions* by bid-to-cover ratio and answer with the security's
CUSIP. A CUSIP is a market-wide identifier, so operators who are not the
Treasury independently restate it:

  SEC EDGAR full-text   filers quote the CUSIP in their own filings
                        (285 hits for 912796N54)
                        -> US Securities and Exchange Commission
  NY Fed SOMA           the System Open Market Account holdings file lists the
                        CUSIP on its weekly as-of dates
                        -> Federal Reserve Bank of New York
  OpenFIGI              maps the CUSIP to a FIGI (912796N54 -> BBG011PSFJG2)
                        -> Bloomberg L.P.

T5 needs two independent confirming operators for a `ship` verdict. Measured
three for the 2021 slice, so finance clears gold rather than the silver the
other rescued categories reach.

WHY THIS TRAP SURVIVES THE DIFFICULTY PROBES, WHICH MOST SHIPPED TRAPS DO NOT
----------------------------------------------------------------------------
T9  (memorize.py)  asks whether the population is a canonical enumerated list
    and whether the ranking key is a string function of the member label. No
    Wikipedia list enumerates the 445 Treasury auctions held in 2021, and a
    bid-to-cover ratio cannot be computed from a CUSIP string.

T9b (recall.py)    asks whether the key value is printed in the member's own
    Wikipedia article. Individual Treasury bills have no article at all, so the
    ranking value is unavailable to a solver working from memory. This is the
    property celebrities and sports both lack: their keys are dates of birth,
    printed in 100% of member articles, which is why re-pointing their answer
    to an opaque identifier never restored the traversal. It is also why the
    answer survives -- measured `answer_in_winner_article` is true for
    education, travel, business and video games, whose IATA code, domain, CIK
    and studio are all printed in the winner's own article.

T8  (famerank.py)  asks whether fame approximates the key. Treasury bills carry
    no pageview distribution, so there is nothing to approximate.

Every witness is verified at generation time. A witness that does not actually
return the CUSIP is dropped from confirming_sources rather than asserted, and
the generator refuses the seed if fewer than two survive.
"""
import datetime as _dt
import json as _json
import urllib.parse as up

import category_traps as ct
import net
import source_gate as sg
from category_traps import Candidate, TrapUnavailable, build_prompt

_AUCTIONS = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
             "/v1/accounting/od/auctions_query")
_EFTS = "https://efts.sec.gov/LATEST/search-index?q=%22{}%22"
_SOMA = "https://markets.newyorkfed.org/api/soma/tsy/get/all/asof/{}.json"
_FIGI_API = "https://api.openfigi.com/v3/mapping"
_FIGI_WEB = "https://www.openfigi.com/id/{}"

# Slices measured viable by finance_rescue.py against all four gates (unique
# max, interior argmax, |rho| <= 0.45, p_uniform <= 0.10), ordered by ascending
# p_answer_by_uniform_guess so the tightest guess space is tried first.
# 2013/Bill is deliberately last: its rho of -0.4384 sits just inside the 0.45
# ceiling and is the most likely to drift on a re-pull.
SEEDS = ((2021, "Bill"), (2021, None), (2019, "Bill"), (2017, "Bill"),
         (2017, None), (2015, None), (2013, None), (2013, "Bill"))

MIN_ROWS = 150
DEPTH_LO, DEPTH_HI = 0.08, 0.92
MAX_RHO = 0.45
MAX_UNIFORM = 0.10
MIN_WITNESSES = 2


def _auctions(year):
    u = ("%s?filter=auction_date:gte:%d-01-01,auction_date:lte:%d-12-31"
         "&page[size]=5000&sort=auction_date" % (_AUCTIONS, year, year))
    js = net.get_json(u, timeout=180, attempts=4)
    return js.get("data", []), u


def _num(r, f):
    """Numeric value of field f, or None. Treasury nulls are '', 'null', '*'."""
    v = r.get(f)
    if v in (None, "", "null", "*"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# THE RANKING KEY. bid_to_cover_ratio is dead: it is a served field, so the
# answer was reachable with `&sort=-bid_to_cover_ratio&page[size]=1`. A
# 114-field sortability audit of this endpoint found 228 orderings HONOURED --
# the endpoint is thoroughly sortable, and the old api_proof_argument claiming
# otherwise was false. The replacement key is a ratio of two served fields,
# which the endpoint cannot order on because it has no expression evaluator.
_KEY_NUM = "indirect_bidder_accepted"
_KEY_DEN = "primary_dealer_accepted"


def _clean(rows, security_type):
    out = []
    for r in rows:
        if security_type and (r.get("security_type") or "") != security_type:
            continue
        cu = (r.get("cusip") or "").strip()
        if not cu:
            continue
        x, y = _num(r, _KEY_NUM), _num(r, _KEY_DEN)
        if x is None or y is None or y == 0:
            continue
        rr = dict(r)
        rr["_key"] = x / y
        rr["_btc"] = _num(r, "bid_to_cover_ratio")
        out.append(rr)
    return out


def _equivalent_served_fields(rows, thresh=0.98):
    """Served fields whose own ordering reproduces the composite key.

    If any single field ranks the auctions the same way the composite does, the
    composite is decorative: the solver sorts on that field server-side and
    reads row one. This is checked locally against every numeric field actually
    present in the payload, so it costs no requests.
    """
    vals = [r["_key"] for r in rows]
    fields = set()
    for r in rows[:40]:
        fields.update(r.keys())
    bad = []
    for f in sorted(fields):
        if f.startswith("_") or f in (_KEY_NUM, _KEY_DEN):
            continue
        fv = []
        for r in rows:
            v = _num(r, f)
            if v is None:
                fv = None
                break
            fv.append(v)
        if not fv:
            continue
        rho = ct._spearman(fv, vals) if hasattr(ct, "_spearman") else None
        if rho is None:
            a, b = ct._rankdata(fv), ct._rankdata(vals)
            n = len(a)
            ma, mb = sum(a) / n, sum(b) / n
            num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
            da = sum((x - ma) ** 2 for x in a) ** 0.5
            db = sum((x - mb) ** 2 for x in b) ** 0.5
            rho = (num / (da * db)) if da and db else 0.0
        if abs(rho) >= thresh:
            bad.append({"field": f, "rho": round(rho, 4)})
    return bad


# ------------------------------------------------------------------ witnesses
def _sec_confirms(cusip):
    """Filers restate the CUSIP in EDGAR full-text. Returns hit count or 0."""
    try:
        js = net.get_json(_EFTS.format(up.quote(cusip)), timeout=90, attempts=3)
    except Exception:  # noqa: BLE001
        return 0
    hits = (js.get("hits") or {}).get("total") or {}
    try:
        return int(hits.get("value") or 0)
    except (TypeError, ValueError):
        return 0


def _soma_dates(auction_date, n=8):
    """Wednesday as-of dates in the weeks following the auction."""
    try:
        d0 = _dt.date.fromisoformat(auction_date[:10])
    except Exception:  # noqa: BLE001
        return []
    d = d0 + _dt.timedelta(days=((2 - d0.weekday()) % 7) or 7)
    return [(d + _dt.timedelta(days=7 * i)).isoformat() for i in range(n)]


def _soma_confirms(cusip, auction_date):
    """The Fed's own holdings file lists the CUSIP. Returns matching dates."""
    found = []
    for d in _soma_dates(auction_date):
        try:
            js = net.get_json(_SOMA.format(d), timeout=60, attempts=2)
        except Exception:  # noqa: BLE001
            continue
        hold = (js.get("soma") or {}).get("holdings") or js.get("holdings") or []
        if any((h.get("cusip") or "").strip() == cusip for h in hold):
            found.append(d)
        if len(found) >= 3:
            break
    return found


def _figi_confirms(cusip):
    """Bloomberg's open symbology maps the CUSIP to a FIGI."""
    try:
        # net.fetch serialises the body itself; passing a pre-serialised string
        # double-encodes it and the API answers 400.
        raw = net.fetch(_FIGI_API, body=[{"idType": "ID_CUSIP", "idValue": cusip}],
                        timeout=60, attempts=3)
    except Exception:  # noqa: BLE001
        return None
    try:
        if isinstance(raw, (list, dict)):
            js = raw
        else:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", "replace")
            js = _json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(js, list) or not js:
        return None
    data = (js[0] or {}).get("data") or []
    if not data:
        return None
    d0 = data[0] or {}
    if not d0.get("figi"):
        return None
    return {"figi": d0.get("figi"), "name": d0.get("name"),
            "ticker": d0.get("ticker"), "securityType2": d0.get("securityType2")}


# ------------------------------------------------------------------ generator
def gen_finance(seeds=SEEDS, **_seed_ignored):
    # WITHDRAWN -- the service is too capable to trap, measured three ways.
    #
    # 1. RAW SERVED FIELDS are dead: the endpoint honours 228 of 450 orderings
    #    tried, and 222 of them genuinely reorder. Bid-to-cover died here.
    # 2. THE RATIO KEY IS STRUCTURALLY DEAD, not unlucky. The composite
    #    indirect_bidder_accepted / primary_dealer_accepted put its answer 9th
    #    of 336 on primary_dealer_accepted ASCENDING -- its own denominator.
    #    Sweeping 15 seeds, ALL 15 rejected and the denominator was shallow in
    #    every one. That is a property of the key family: over a heavy-tailed
    #    denominator the argmax of x/y is drawn almost surely from the
    #    smallest-y tail, so "sort ascending on the denominator" reaches it in a
    #    handful of rows whatever year is chosen. The numerator leaked nothing
    #    by comparison (ranks 51 and 67), which is the asymmetry the algebra
    #    predicts.
    # 3. NON-RATIO KEYS FAIL VALIDATION. Two families that are not tail-
    #    dominated by construction -- the least-squares residual of indirect on
    #    primary, and the difference of within-population percentile ranks --
    #    were tried across 11 seeds, 22 configurations. Exactly one passed
    #    (2023 all securities, rank difference, 91282CJR3, shallowest component
    #    depth 0.1332 against a 0.10 bar). One pass in 22 is the signature of a
    #    key hunted until something cleared, so it was validated rather than
    #    adopted: on EIGHT held-out years never touched by the search it passed
    #    ZERO times, depths 0.0037 to 0.094. Bootstrap resampling of the 2023
    #    population reproduced that winner only 63.5% of the time. The single
    #    pass was selection noise.
    #
    # The general lesson, which now gates other categories: a service that will
    # sort on a hundred numeric fields cannot be trapped by any key built from
    # those fields, because some derivable ordering will place the answer near
    # the top. Finance can only return on a collection whose ranking quantity is
    # not a served, sortable column.
    raise ct.TrapUnavailable(
        "finance: withdrawn. The auctions service honours 228 orderings, so raw "
        "fields are one call away; the indirect-over-primary ratio key is "
        "structurally leaky because a ratio's argmax sits in the small-denominator "
        "tail (shallow in 15 of 15 seeds); and the two non-ratio replacements "
        "passed 1 of 22 searched configurations and 0 of 8 held-out years, with a "
        "bootstrap hit rate of 0.635.")
    tried = []
    for year, stype in seeds:
        tag = "%d/%s" % (year, stype or "all")
        try:
            raw, url = _auctions(year)
        except Exception as e:  # noqa: BLE001
            tried.append("%s: fetch %s" % (tag, type(e).__name__))
            continue
        rows = _clean(raw, stype)
        if len(rows) < MIN_ROWS:
            tried.append("%s: only %d priced auctions" % (tag, len(rows)))
            continue

        try:
            best = ct._pick_extreme(rows, lambda r: r["_key"],
                                    "finance %s" % tag, mode="max",
                                    valuefn=lambda r: r["cusip"])
        except TrapUnavailable as te:
            tried.append("%s: %s" % (tag, te))
            continue

        ev = ct.LAST_RANK
        n = ev.get("n_ranked") or len(rows)
        pos = ev.get("winner_position_in_api_order")
        depth = (pos / (n - 1)) if (pos is not None and n > 1) else 0.0
        rho = ev.get("spearman_key_vs_api_order")
        p_unif = ev.get("p_answer_by_uniform_guess")
        if not (DEPTH_LO <= depth <= DEPTH_HI):
            tried.append("%s: argmax depth %.4f at an endpoint" % (tag, depth))
            continue
        if rho is not None and abs(rho) > MAX_RHO:
            tried.append("%s: order leak rho %.4f" % (tag, rho))
            continue
        if p_unif is not None and p_unif > MAX_UNIFORM:
            tried.append("%s: p_uniform %.4f" % (tag, p_unif))
            continue

        # DERIVABLE-KEY GATE. The endpoint honours 228 orderings. The composite
        # only survives if no single served field ranks the auctions the way it
        # does; otherwise the solver sorts on that field and reads row one.
        equiv = _equivalent_served_fields(rows)
        if equiv:
            tried.append("%s: key reproduced by served field %s (rho %.4f)"
                         % (tag, equiv[0]["field"], equiv[0]["rho"]))
            continue

        cusip = best["cusip"].strip()
        adate = (best.get("auction_date") or "")[:10]
        term = best.get("security_term") or best.get("security_type") or ""
        keys = sorted((r["_key"] for r in rows), reverse=True)
        runner = keys[1] if len(keys) > 1 else None
        rel_sep = ((keys[0] - runner) / abs(keys[0])) if runner and keys[0] else None
        # is the answer also the argmax of the DEAD key? if so nothing changed
        _btcs = [(r["_btc"], r["cusip"]) for r in rows if r["_btc"] is not None]
        btc_winner = max(_btcs)[1] if _btcs else None
        if btc_winner == cusip:
            tried.append("%s: new key returns the same CUSIP as the dead "
                         "bid-to-cover key" % tag)
            continue

        # ---- verify each witness rather than asserting it
        conf, notes = [], {}
        hits = _sec_confirms(cusip)
        if hits > 0:
            conf.append(_EFTS.format(up.quote(cusip)))
            notes["sec_edgar_hits"] = hits
        soma = _soma_confirms(cusip, adate)
        if soma:
            conf.append(_SOMA.format(soma[0]))
            notes["soma_asof_dates"] = soma
        figi = _figi_confirms(cusip)
        if figi:
            conf.append(_FIGI_WEB.format(figi["figi"]))
            notes["openfigi"] = figi

        srcs = [url] + conf
        ind = sg.independent_witnesses(srcs, conf, "US Department of the Treasury")
        if len(ind) < MIN_WITNESSES:
            tried.append("%s: only %d independent witnesses for %s (%s)"
                         % (tag, len(ind), cusip, ", ".join(ind) or "none"))
            continue

        wit = []
        if "sec_edgar_hits" in notes:
            wit.append("SEC EDGAR full-text returns %d filings quoting %s"
                       % (notes["sec_edgar_hits"], cusip))
        if "soma_asof_dates" in notes:
            wit.append("the Federal Reserve Bank of New York lists it in the SOMA "
                       "holdings file as of %s" % notes["soma_asof_dates"][0])
        if "openfigi" in notes:
            wit.append("OpenFIGI maps it to FIGI %s" % notes["openfigi"]["figi"])

        return Candidate(
            category="finance",
            primary_operator="US Department of the Treasury",
            field="CUSIP",
            answer=cusip,
            entity="%s auctioned %s" % (term or "security", adate),
            n_base=len(rows),
            sources=srcs,
            confirming_sources=conf,
            api_proof_argument=(
                "This endpoint is highly sortable and the argument does not pretend "
                "otherwise: an audit of its 114 served fields found 228 orderings "
                "honoured, of which 222 genuinely reorder the %d %d auctions. That "
                "is exactly why the earlier bid-to-cover key was worthless -- it was "
                "a served field, so one call with sort and a page size of one "
                "returned the answer. The ranking key here is a ratio of two served "
                "fields, and the service exposes no expression evaluator, so it "
                "cannot be ordered on. No single served field reproduces its "
                "ordering (checked against every numeric field in the payload at "
                "generation time), and the winner is not row one of any honoured "
                "ordering. Measured rho against served order is %s and the winner "
                "sits at relative depth %.4f. The answer is the security's CUSIP, "
                "which appears in no encyclopaedia article and cannot be derived "
                "from the auction date or the term."
                % (len(rows), year, rho, depth)),
            confirmation="; ".join(wit),
            facts={
                "year": year, "security_type": stype or "all", "n": len(rows),
                "cusip": cusip, "auction_date": adate, "security_term": term,
                "key": "indirect_to_primary_ratio",
                "key_gloss": "ratio of indirect-bidder to primary-dealer awards",
                "key_input_fields": [_KEY_NUM, _KEY_DEN],
                "max_key": round(best["_key"], 8),
                "runner_up_key": round(runner, 8) if runner else None,
                "rel_separation": round(rel_sep, 6) if rel_sep else None,
                "equivalent_served_fields": equiv,
                "n_orderings_honoured": 228,
                "n_orderings_that_reorder": 222,
                "winner_of_dead_bid_to_cover_key": btc_winner,
                "replaced_key": "bid_to_cover_ratio",
                "replaced_key_defect": (
                    "served field; &sort=-bid_to_cover_ratio&page[size]=1 "
                    "returned the answer in one call"),
                "argmax_depth": round(depth, 4),
                "spearman_key_vs_api_order": rho,
                "p_answer_by_uniform_guess": p_unif,
                "distinct_cusips": ev.get("answer_field_distinct_values"),
                "independent_witnesses": ind,
                "answer_field_class": "identifier",
                "witness_detail": notes,
                "difficulty_note": (
                    "T9 population coverage is nil: no Wikipedia list enumerates the "
                    "year's auctions. T9b key recall is nil: individual bills have no "
                    "article, so the bid-to-cover ratio cannot be recalled and the "
                    "CUSIP is not printed anywhere a solver could have memorised. "
                    "T8 is undefined: there is no fame distribution over CUSIPs."),
                "replaces": ("Daily Treasury Statement closing balance, refused at "
                             "R3c because every confirming source was run by the "
                             "Treasury itself."),
            },
            prompt=build_prompt(
                "The United States Treasury publishes a record of every marketable "
                "security it auctions, giving each auction its date, its term, the "
                "CUSIP of the security sold, and the amount awarded to each class "
                "of bidder, including indirect bidders and primary dealers.",
                "Consider only the %sauctions the Treasury records during the %d "
                "calendar year whose entry reports an award to both indirect "
                "bidders and primary dealers."
                % (("%s " % stype.lower()) if stype else "", year),
                "Across that year exactly one auction awarded indirect bidders the "
                "largest amount relative to what it awarded primary dealers.",
                "Report the CUSIP of the security sold at that single auction.",
                "Give the nine-character CUSIP alone.",
                note="Confirm the identifier against a market data source before answering."),
        )
    raise TrapUnavailable("finance: no seed produced a witnessed CUSIP: "
                          + "; ".join(tried[:6]))


_OVERRIDES = {"finance": gen_finance}
ct.GENERATORS.update(_OVERRIDES)
