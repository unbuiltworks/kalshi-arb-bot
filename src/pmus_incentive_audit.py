"""OWNER 5561142758 #2/#3 (RUN: establish our own realized economics):
READ-ONLY audit of the incentive economics this exact Polymarket US
account actually receives, from the venue's own records -- never from
estimates.

Canonical L5's money was 83% incentives (maker rebates +$33,929,
liquidity rewards +$26,956, taker rebates +$7,323 of +$81,895 total) on
$7.4M of turnover; its trading edge net of residual/timing was 0.18% of
turnover. Whether that transfers to THIS account is the money question,
and the venue publishes the answer in three places this module reads:

  * /v1/incentives/earnings (authenticated, docs.polymarket.us
    "Incentives API Overview": "your earned rewards") -- OBSERVED credits.
  * /v1/portfolio/activities (authenticated, full cursor walk) -- any row
    whose type is not a trade / resolution / deposit is a credit; the
    venue says maker rebates are "credited to your balance at the time of
    the fill", so the trade rows and the balances response are scanned
    for fee / rebate / commission fields too.
  * /v1/incentives (public gateway) -- the PUBLISHED programs, their
    market slugs, pools, target size and spread; reconciled against the
    markets this ledger filled.

Every number is labelled observed / published_not_observed / estimated;
only observed, venue-confirmed credits may ever be called realized.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from decimal import Decimal as D, ROUND_HALF_EVEN
from typing import Any, Mapping, Optional

from src import pmus_settlement_auditor as auditor
from src import pmus_q1_executor as q1

EARNINGS_PATHS = ("/v1/incentives/earnings", "/v1/incentives/earnings?limit=200")
TRADE_TYPES = ("ACTIVITY_TYPE_TRADE", "ACTIVITY_TYPE_POSITION_RESOLUTION",
               "ACTIVITY_TYPE_ACCOUNT_DEPOSIT", "ACTIVITY_TYPE_ACCOUNT_WITHDRAWAL")
CREDIT_WORDS = ("REBATE", "REWARD", "INCENTIVE", "CREDIT", "BONUS", "REFERRAL")
FEE_WORDS = ("fee", "rebate", "commission", "reward", "incentive", "credit")
# docs.polymarket.us/fees: taker 0.06 * C * p * (1-p); maker rebate
# 0.0125 * C * p * (1-p) credited at fill; fees rounded to the nearest
# cent, banker's rounding; small trades round to $0.00
TAKER_RATE = D("0.06")
MAKER_REBATE_RATE = D("0.0125")
CENT = D("0.01")


def _cents(x: D) -> D:
    return x.quantize(CENT, rounding=ROUND_HALF_EVEN)


def rebate_at(qty: D, px: D) -> dict:
    """The venue's own maker-rebate arithmetic at one fill size/price:
    the raw amount and what survives cent rounding (ESTIMATED from the
    published formula, never a credit)."""
    raw = MAKER_REBATE_RATE * qty * px * (D(1) - px)
    return {"qty": str(qty), "px": str(px), "raw_usd": str(raw.quantize(D("0.000001"))),
            "credited_after_rounding_usd": str(_cents(raw))}


def min_qty_for_nonzero_rebate(px: D) -> str:
    """Smallest fill (shares) whose rebate rounds up to one cent."""
    per_share = MAKER_REBATE_RATE * px * (D(1) - px)
    if per_share == 0:
        return "inf"
    return str((D("0.005") / per_share).quantize(D("0.01")))


def _scan_keys(obj: Any, path: str = "") -> list[str]:
    """Every key path whose name mentions a fee / rebate / credit word."""
    out: list[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if any(w in str(k).lower() for w in FEE_WORDS):
                out.append(f"{p}={json.dumps(v, default=str)[:80]}")
            out.extend(_scan_keys(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            out.extend(_scan_keys(v, f"{path}[{i}]"))
    return out


def _get(t: q1.Transport, path: str, base: Optional[str] = None) -> dict:
    try:
        resp = t.request("GET", path, base=base) if base else t.request("GET", path)
        return {"ok": True, "response": resp}
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


def earnings(t: q1.Transport, detail_max: int = 50) -> dict:
    """OBSERVED credits from the venue's earnings endpoint. `detail_max`
    bounds `rows_detail` (default 50, as before)."""
    out: dict = {"endpoint": None, "ok": False, "rows": 0, "total_usd": None,
                 "by_program": {}, "sample": None, "error": None}
    for path in EARNINGS_PATHS:
        r = _get(t, path)
        if not r["ok"]:
            out["error"] = r["error"]
            continue
        resp = r["response"]
        out.update({"endpoint": path, "ok": True, "top_level_keys":
                    sorted(resp.keys()) if isinstance(resp, Mapping) else type(resp).__name__})
        rows = None
        if isinstance(resp, Mapping):
            for key in ("earnings", "rewards", "data", "items", "results"):
                if isinstance(resp.get(key), list):
                    rows = resp[key]
                    break
        elif isinstance(resp, list):
            rows = resp
        rows = [x for x in (rows or []) if isinstance(x, Mapping)]
        total = D("0")
        by_prog: Counter = Counter()
        n_amount = 0
        for row in rows:
            amt = None
            for key in ("amount", "amountUsd", "usd", "value", "reward", "earned", "total"):
                amt = auditor._amount(row.get(key))
                if amt is not None:
                    break
            if amt is not None:
                total += amt
                n_amount += 1
            # LIVE 19:08Z: the earnings rows carry programType + marketSlug
            # but no programId, so the type is the key when the id is absent
            prog = str(row.get("programId") or row.get("program") or row.get("type")
                       or row.get("programType") or "?")
            by_prog[prog] += 1
        # A REWARD ROW IS NOT MONEY UNTIL THE VENUE SAYS IT PAID. Summing
        # every row into one total_usd reported $0.93 of "earnings" while
        # every row observed was SKIPPED or PENDING -- an accrual quoted as
        # income. The status split is emitted so the headline can never be
        # read as credited again, and the bridge consumes THIS, not total.
        # guarded: an audit that cannot split statuses must degrade to
        # "unknown", never halt the lane it is only observing
        try:
            from src import l5_realized_bridge as bridge
            status_split = bridge.rewards_by_status(rows)
        except Exception as exc:                       # noqa: BLE001
            status_split = {"credited_usd": None,
                            "accrued_but_not_credited_usd": None,
                            "by_status": {"UNAVAILABLE": repr(exc)[:120]}}
        out.update({"rows": len(rows), "rows_with_amount": n_amount,
                    "total_usd_all_statuses": str(total) if n_amount else None,
                    "credited_usd": status_split["credited_usd"],
                    "accrued_not_credited_usd":
                        status_split["accrued_but_not_credited_usd"],
                    "by_status": status_split["by_status"],
                    "rows_detail": [dict(r) for r in rows[:detail_max]],
                    "total_usd": str(total) if n_amount else None,
                    "by_program": dict(by_prog),
                    "sample": json.loads(json.dumps(rows[0], default=str)) if rows else None,
                    "raw_head": json.dumps(resp, default=str)[:600]})
        break
    return out


def activity_credits(t: q1.Transport) -> dict:
    """OBSERVED: every activity row of the account that is not a trade,
    resolution or deposit, verbatim (bounded), plus the type histogram."""
    walk = auditor.activity_pages(t, since=None, max_pages=200)
    types: Counter = Counter(str(r.get("type")) for r in walk["rows"])
    credits = [r for r in walk["rows"]
               if str(r.get("type")) not in TRADE_TYPES
               or any(w in str(r.get("type")).upper() for w in CREDIT_WORDS)]
    fee_paths: Counter = Counter()
    for r in walk["rows"]:
        for p in _scan_keys(r):
            fee_paths[p.split("=")[0]] += 1
    return {"rows": len(walk["rows"]), "pages": walk["pages"], "ended": walk["ended"],
            "oldest_time": walk["oldest_time"], "newest_time": walk["newest_time"],
            "types": dict(types),
            "credit_rows": [json.loads(json.dumps(r, default=str)) for r in credits[:20]],
            "credit_rows_n": len(credits),
            "fee_or_rebate_key_paths_in_activities": dict(fee_paths),
            "_rows": walk["rows"]}


def trade_fee_fields(rows: list[dict]) -> dict:
    """The venue's trade rows scanned for fee / rebate / commission fields
    (rebates are 'credited at the time of the fill'); one complete trade
    row verbatim so the schema is on the record."""
    trades = [r.get("trade") for r in rows
              if isinstance(r, Mapping) and isinstance(r.get("trade"), Mapping)]
    paths: Counter = Counter()
    sums: dict = {}
    for tr in trades:
        for p in _scan_keys(tr):
            key, _, val = p.partition("=")
            paths[key] += 1
            try:
                sums[key] = str(D(str(sums.get(key, "0"))) + D(json.loads(val)))
            except Exception:                         # noqa: BLE001
                pass
    return {"trades": len(trades),
            "fee_or_rebate_key_paths": dict(paths), "sums": sums,
            "sample_row": json.loads(json.dumps(trades[0], default=str)) if trades else None,
            "passive_execution_keys": sorted((trades[0].get("passiveExecution") or {}).keys())
            if trades and isinstance(trades[0].get("passiveExecution"), Mapping) else None}


REBATE_FLOOR_SHARES = D("1.62")     # >= this credits >= $0.01 at p=0.45


def credited_on_fills(rows: list[dict]) -> dict:
    """LIVE 2026-09-07 (R3L): the one number the whole incentive question
    turns on -- what the venue ACTUALLY credited on our own fills, split
    by whether the fill cleared the cent-rounding floor.

    Every `commissionNotional*` value on both executions is summed with
    its sign exactly as served; no sign convention is assumed and nothing
    here is called a rebate unless the venue's own number is non-zero.
    Trades are ours by construction (the account's own activity walk)."""
    trades = [r.get("trade") for r in rows
              if isinstance(r, Mapping) and isinstance(r.get("trade"), Mapping)]
    credited = D("0")        # venue paid US (negative commission = rebate)
    charged = D("0")         # venue took FROM US
    counterparty = D("0")    # the OTHER side's commission; never ours
    n_floor = 0
    n_credited = 0
    rows_out: list[dict] = []
    for tr in trades:
        qty = _d_or_none(tr.get("qtyDecimal")) or _d_or_none(tr.get("qty"))
        at_floor = qty is not None and abs(qty) >= REBATE_FLOOR_SHARES
        n_floor += 1 if at_floor else 0
        # LIVE 02:09Z: the trade row carries BOTH sides. `isAggressor` says
        # which one is US -- summing both conflates our maker rebate with
        # the taker's fee and inverts the sign of the answer.
        ours_key = "aggressorExecution" if tr.get("isAggressor") else "passiveExecution"
        theirs_key = "passiveExecution" if tr.get("isAggressor") else "aggressorExecution"
        ours = _commission(tr.get(ours_key))
        theirs = _commission(tr.get(theirs_key))
        counterparty += theirs
        if ours < 0:
            credited += -ours
            n_credited += 1
        else:
            charged += ours
        if ours != 0:
            rows_out.append({"id": tr.get("id"), "market": tr.get("marketSlug"),
                             "qty": str(qty), "our_side": ours_key,
                             "our_commission": str(ours),
                             "credited_to_us": ours < 0,
                             "at_or_above_floor": at_floor})
    return {"trades": len(trades),
            "trades_at_or_above_rebate_floor": n_floor,
            "rebate_floor_shares": str(REBATE_FLOOR_SHARES),
            "our_fills_with_a_credit": n_credited,
            "credited_to_us_usd": str(credited),
            "charged_to_us_usd": str(charged),
            "net_to_us_usd": str(credited - charged),
            "counterparty_side_usd_not_ours": str(counterparty),
            "our_rows": rows_out[:20],
            "labels": ("OBSERVED, OUR SIDE ONLY: the venue's own "
                       "commissionNotionalCollected on whichever execution is "
                       "ours per `isAggressor`. NEGATIVE on the venue = credited "
                       "to us (the maker rebate), so it is reported here as a "
                       "positive credited_to_us_usd. counterparty_side_usd is "
                       "the OTHER trader's fee and is never our income.")}


def _commission(ex: Any) -> D:
    if not isinstance(ex, Mapping):
        return D("0")
    raw = ex.get("commissionNotionalCollected")
    v = _d_or_none(raw.get("value") if isinstance(raw, Mapping) else raw)
    return v if v is not None else D("0")


def _d_or_none(v: Any) -> Optional[D]:
    try:
        return None if v is None else D(str(v))
    except Exception:                                 # noqa: BLE001
        return None


def balances(t: q1.Transport) -> dict:
    r = _get(t, "/v1/account/balances")
    if not r["ok"]:
        return {"ok": False, "error": r["error"]}
    resp = r["response"]
    return {"ok": True, "raw": json.loads(json.dumps(resp, default=str)),
            "fee_or_rebate_key_paths": _scan_keys(resp)}


PROGRAM_LIST_PATH = "/v1/incentives"
# 200 pages = 20,000 rows ~= 50 s of bounded GETs, run hourly. Measured
# 2026-09-07T07:3xZ the live list had not exhausted by 1,000 pages /
# 100,000 rows -- every one of them ATP or WTA across four US-Open programs
# -- so a bounded walk is a SAMPLE. That is why truncation is reported and
# why a truncated walk can never support an absence claim.
PROGRAM_LIST_MAX_PAGES = int(os.getenv("PMUS_PROGRAM_LIST_MAX_PAGES", "200"))
# LIVE 2026-09-07T08:20:23-08:21:02Z: two back-to-back 200-page walks of
# this list (published_programs and mlb_program_scan each walked it) fired
# ~400 GETs in ~35 s at the gateway, and the authenticated preflight read
# that followed (/v1/orders/open) answered 429 until the transport's
# retries ran out -- a HALT that stopped the campaign with a leg held.
# The walk is now paced, and one audit walks the list ONCE.
PROGRAM_LIST_PAGE_PAUSE_S = float(os.getenv("PMUS_PROGRAM_LIST_PAGE_PAUSE_S", "0.3"))


def program_list(t: q1.Transport, max_pages: Optional[int] = None,
                 sleep=time.sleep) -> dict:
    """The WHOLE published program list, not page one.

    OWNER 5561482606: "do not equate 'MLB games exist' with 'no MLB
    incentive program,' and do not state 'MLB has none' from a
    partial/current program snapshot."

    Two limits of this endpoint, both measured against the live gateway at
    2026-09-07T07:2xZ, and both of which a single-page read hides:

    1. PAGINATION. It serves a fixed 100 rows per page behind a
       nextPageToken and ignores limit / pageSize (all return 100). The
       previous single `_get` is exactly what produced the reading "MLB
       entries in the program list: 0 of 100" -- a statement about one
       page, not about the venue. Walked properly it runs to tens of
       thousands of rows.
    2. ACTIVE-ONLY. Every timePeriod it returns carries status "active",
       and none of ?status=ended, ?status=expired, ?includeInactive=true
       or ?active=false changes the response at all. So absence of a
       league from this list means "no program running right now". It can
       never mean "this league never had one", and this function reports
       that limitation inline so no caller can read it the other way.

    Bounded and fail-visible: a page error stops the walk and is returned
    with everything gathered so far, and `exhausted` says whether the end
    was actually reached. Paced: PROGRAM_LIST_PAGE_PAUSE_S between pages, so
    the walk never spends the gateway's rate budget that the trading
    session's own reads need right after it.
    """
    cap = PROGRAM_LIST_MAX_PAGES if max_pages is None else max_pages
    entries: list[dict] = []
    by_league: Counter = Counter()
    statuses: Counter = Counter()
    by_program: dict = {}
    token, pages, error = None, 0, None
    while pages < cap:
        path = PROGRAM_LIST_PATH + (f"?pageToken={token}" if token else "")
        r = _get(t, path, base=q1.GATEWAY)
        if not r["ok"]:
            error = r["error"]
            break
        resp = r["response"]
        rows = resp.get("programs") if isinstance(resp, Mapping) else resp
        rows = [x for x in (rows or []) if isinstance(x, Mapping)]
        pages += 1
        for p in rows:
            slug = str(p.get("marketSlug") or p.get("slug") or "")
            parts = slug.split("-")
            league = parts[1] if len(parts) > 1 else "?"
            by_league[league] += 1
            periods = [x for x in (p.get("timePeriods") or []) if isinstance(x, Mapping)]
            for x in periods:
                statuses[str(x.get("status") or "?").lower()] += 1
                pid = str(x.get("programId") or x.get("id") or "?")
                b = by_program.setdefault(pid, {
                    "program_id": pid, "program_type": x.get("programType"),
                    "reward_pool": x.get("rewardPool"), "period": x.get("period"),
                    "start": x.get("start"), "end": x.get("end"),
                    "market_rows": 0, "leagues": Counter()})
                b["market_rows"] += 1
                b["leagues"][league] += 1
            entries.append({"slug": slug, "league": league,
                            "instrumentState": p.get("instrumentState"),
                            "periods": periods})
        token = (resp.get("nextPageToken") if isinstance(resp, Mapping) else None)
        if not token or not rows:
            break
        if pages < cap and PROGRAM_LIST_PAGE_PAUSE_S > 0:
            sleep(PROGRAM_LIST_PAGE_PAUSE_S)
    exhausted = error is None and not token
    return {
        "ok": bool(entries) or error is None,
        "error": error, "pages": pages, "rows": len(entries),
        "exhausted": exhausted, "truncated_at_page_cap": pages >= cap and not exhausted,
        "page_cap": cap,
        "by_league": dict(by_league.most_common()),
        "period_statuses": dict(statuses),
        "programs": [{**b, "leagues": dict(b["leagues"])}
                     for b in sorted(by_program.values(),
                                     key=lambda x: -x["market_rows"])],
        "entries": entries,
        "limits": [
            "fixed 100 rows per page; limit/pageSize are ignored by the venue",
            "ACTIVE-ONLY view: every period returned has status 'active' and no "
            "status/includeInactive parameter surfaces ended or expired ones",
            "absence of a league means 'no program running now', NEVER 'never had one'",
        ],
    }


def published_programs(t: q1.Transport, our_slugs: list[str],
                       census: Optional[dict] = None) -> dict:
    """PUBLISHED (not observed): the venue's incentive programs and which
    of this ledger's filled markets they cover. `census` is a program_list()
    result the caller already walked, so one audit walks the list ONCE."""
    census = program_list(t) if census is None else census
    if census["error"] and census["pages"] == 0:
        return {"ok": False, "error": census["error"]}
    progs = [{"marketSlug": e["slug"], "instrumentState": e["instrumentState"],
              "timePeriods": e["periods"]} for e in census["entries"]]
    ours = set(our_slugs)
    # LIVE 18:36Z: the venue lists ONE ENTRY PER MARKET -- marketSlug,
    # instrumentState, category and timePeriods[{programId, programType,
    # rewardPool, status, discountFactor, targetSize, period, start}] --
    # so programs are aggregated from the periods and reconciled by slug
    matched: list[dict] = []
    by_program: dict = {}
    markets_active = 0
    for p in progs:
        slug = str(p.get("marketSlug") or p.get("slug") or "")
        periods = [x for x in (p.get("timePeriods") or []) if isinstance(x, Mapping)]
        active = [x for x in periods if str(x.get("status") or "").lower() == "active"]
        if active:
            markets_active += 1
        for x in periods:
            pid = str(x.get("programId") or x.get("id") or "?")
            b = by_program.setdefault(pid, {
                "program_id": pid, "program_type": x.get("programType"),
                "reward_pool": x.get("rewardPool"), "target_size": x.get("targetSize"),
                "discount_factor": x.get("discountFactor"), "period": x.get("period"),
                "start": x.get("start"), "markets": 0, "markets_active": 0,
                "categories": Counter()})
            b["markets"] += 1
            b["markets_active"] += 1 if str(x.get("status") or "").lower() == "active" else 0
            b["categories"][str(p.get("category") or slug.split("-")[0])] += 1
        if slug in ours and periods:
            matched.append({"market": slug, "periods": [
                {k: x.get(k) for k in ("programId", "programType", "rewardPool", "status",
                                        "targetSize", "discountFactor", "period", "start")}
                for x in periods]})
    summary = []
    for b in by_program.values():
        b["categories"] = dict(b["categories"])
        summary.append(b)
    summary.sort(key=lambda b: (-(b["markets_active"]), str(b["program_id"])))
    return {"ok": True, "programs": len(progs), "markets_listed": len(progs),
            "markets_with_active_period": markets_active,
            "keys_of_first": sorted(progs[0].keys()) if progs else None,
            "by_program": summary[:40], "our_markets_in_programs": matched,
            # the list is PAGINATED and ACTIVE-ONLY; carry both so no reader
            # mistakes one page, or one moment, for the venue's whole history
            "census": {k: census[k] for k in
                       ("pages", "rows", "exhausted", "truncated_at_page_cap",
                        "by_league", "period_statuses", "limits", "error")},
            "raw_head": json.dumps(progs[:2], default=str)[:500]}


MLB_SCAN_EVENT_PATHS = (
    "/v1/events?live=true&active=true&closed=false&categories=sports&limit=500",
    "/v1/events?active=true&closed=false&categories=sports&limit=500",
    "/v1/events?active=true&closed=false&categories=sports&limit=500&offset=500",
)
MLB_ROW_LIMIT = 80


def _positive(value: Any) -> bool:
    try:
        return value is not None and D(str(value)) > 0
    except Exception:                                 # noqa: BLE001
        return False


def _is_mlb(slug: str, market: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    s = slug.lower()
    if "-mlb-" in s or s.startswith("mlb-"):
        return True
    for src in (market, event):
        for k in ("sportsMarketType", "league", "sport", "subcategory", "title", "question"):
            v = str(src.get(k) or "").lower()
            if v.startswith("mlb") or "baseball" in v or " mlb " in f" {v} ":
                return True
    return False


def mlb_program_scan(t: q1.Transport, census: Optional[dict] = None) -> dict:
    """OWNER 5561482606: the FULL current MLB market universe (live and
    pregame, from the venue's own events listing with embedded markets)
    reconciled BY SLUG against the public program list, with the three
    things kept apart per market: (a) live/open status, (b) maker-rebate
    eligibility -- the venue's rebate is its published formula on
    fee-charging markets, so the market's own feeCoefficient is recorded
    as the eligibility evidence, (c) a liquidity-reward program period
    (programId, status, pool, target, effective start, period,
    createdAt). Everything is timestamped; nothing is concluded from a
    category label. Read-only (GETs on the public gateway)."""
    from datetime import datetime, timezone
    scanned_at = datetime.now(timezone.utc).isoformat()
    markets: dict[str, dict] = {}
    errors: list[str] = []
    events_seen = 0
    for path in MLB_SCAN_EVENT_PATHS:
        r = _get(t, path, base=q1.GATEWAY)
        if not r["ok"]:
            errors.append(f"{path}: {r['error']}")
            continue
        resp = r["response"]
        evs = resp.get("events") if isinstance(resp, Mapping) else resp
        for ev in (evs or []):
            if not isinstance(ev, Mapping):
                continue
            events_seen += 1
            live_ev = bool(ev.get("live"))
            for m in (ev.get("markets") or []):
                if not isinstance(m, Mapping):
                    continue
                slug = str(m.get("slug") or "")
                if not slug or not _is_mlb(slug, m, ev):
                    continue
                row = markets.setdefault(slug, {
                    "slug": slug, "event": ev.get("slug"),
                    "live": live_ev or bool(m.get("live")),
                    "status": m.get("status"), "active": m.get("active"),
                    "closed": m.get("closed"),
                    "game_start": m.get("gameStartTime") or ev.get("startTime"),
                    "fee_coefficient": m.get("feeCoefficient"),
                    "rebate_eligible_by_fee_coefficient": _positive(m.get("feeCoefficient")),
                    "program_periods": [], "in_program_list": False})
                row["live"] = row["live"] or live_ev
    # the WHOLE list, walked page by page: "0 of 100" was a statement about
    # page one, and the owner asked for a census, not a snapshot
    census = program_list(t) if census is None else census
    program_entries = census["rows"]
    mlb_program_entries: list[dict] = []
    for e in census["entries"]:
        slug = e["slug"]
        periods = [{k: x.get(k) for k in ("programId", "programType", "status", "rewardPool",
                                          "targetSize", "discountFactor", "period",
                                          "start", "end", "createdAt")}
                   for x in e["periods"]]
        if slug in markets:
            markets[slug]["in_program_list"] = True
            markets[slug]["program_periods"] = periods
        if _is_mlb(slug, {"instrumentState": e.get("instrumentState")}, {}):
            mlb_program_entries.append({"slug": slug, "instrumentState": e.get("instrumentState"),
                                        "in_current_universe": slug in markets,
                                        "periods": periods})
    if census["error"]:
        errors.append(f"/v1/incentives: {census['error']}")
    # LIVE 19:31Z: 6,704 MLB markets, 3,818 live -- player props (astatc-)
    # swamp a slug-sorted list, so main markets (moneyline aec-, totals
    # tsc-, spreads asc-) come first and every GAME gets one summary row
    def _main(r: dict) -> int:
        s = r["slug"]
        return 0 if s.startswith("aec-") else 1 if s.startswith("tsc-") else \
            2 if s.startswith("asc-") else 3
    rows = sorted(markets.values(),
                  key=lambda r: (not r["live"], _main(r), str(r["game_start"]), r["slug"]))
    games: dict[str, dict] = {}
    for r in rows:
        g = games.setdefault(str(r["event"]), {
            "event": r["event"], "live": False, "game_start": r["game_start"],
            "markets": 0, "main_markets": 0, "rebate_eligible": 0,
            "pool_any_period": 0, "pool_active_period": 0, "main_slugs": []})
        g["live"] = g["live"] or bool(r["live"])
        g["markets"] += 1
        if _main(r) < 3:
            g["main_markets"] += 1
            if len(g["main_slugs"]) < 3:
                g["main_slugs"].append(r["slug"])
        g["rebate_eligible"] += 1 if r["rebate_eligible_by_fee_coefficient"] else 0
        g["pool_any_period"] += 1 if r["program_periods"] else 0
        g["pool_active_period"] += 1 if any(
            str(x.get("status") or "").lower() == "active" for x in r["program_periods"]) else 0
    game_rows = sorted(games.values(), key=lambda g: (not g["live"], str(g["game_start"]), str(g["event"])))
    live = [r for r in rows if r["live"]]
    rebate = [r for r in rows if r["rebate_eligible_by_fee_coefficient"]]
    pooled = [r for r in rows if r["program_periods"]]
    pooled_active = [r for r in pooled
                     if any(str(x.get("status") or "").lower() == "active" for x in r["program_periods"])]
    n = len(rows)
    live_games = [g for g in game_rows if g["live"]]
    walk_reach = ("exhausted" if census["exhausted"] else
                  "TRUNCATED -- a sample, which cannot support an absence claim")
    statement = (f"MLB markets present: {n} ({len(live)} live) across {len(game_rows)} games "
                 f"({len(live_games)} live) at {scanned_at}; "
                 f"{len(rebate)}/{n} carry a positive feeCoefficient (maker-rebate formula "
                 f"applies to fee-charging markets; the rebate itself is credited per fill "
                 f"and is $0.00 below 1.62 shares after cent rounding); "
                 f"{len(pooled_active)}/{n} currently have an ACTIVE liquidity-reward "
                 f"program period, {len(pooled)}/{n} any period; MLB entries in the "
                 f"program list: {len(mlb_program_entries)} of {program_entries} "
                 f"({census['pages']} pages walked, {walk_reach}; and the list is "
                 f"an ACTIVE-ONLY view, so it is what is running now and says "
                 f"nothing about what has run before)")
    return {"ok": not errors or bool(rows), "scanned_at": scanned_at, "errors": errors,
            "events_seen": events_seen, "mlb_markets": n, "mlb_live": len(live),
            "mlb_games": len(game_rows), "mlb_games_live": len(live_games),
            "games": game_rows[:MLB_ROW_LIMIT],
            "rebate_eligible_by_fee_coefficient": len(rebate),
            "liquidity_pool_any_period": len(pooled),
            "liquidity_pool_active_period": len(pooled_active),
            "program_list_entries": program_entries,
            "program_list_census": {k: census[k] for k in
                                    ("pages", "rows", "exhausted",
                                     "truncated_at_page_cap", "by_league",
                                     "period_statuses", "limits", "error")},
            "program_list_programs": census["programs"][:MLB_ROW_LIMIT],
            "mlb_program_entries": mlb_program_entries[:MLB_ROW_LIMIT],
            "statement": statement,
            "discovery_gap": (
                "MLB markets are present but NONE appears in the program list. The "
                "August accrual ($0.22, MLB/NFL) proves MLB pools HAVE existed, and "
                "the list is an ACTIVE-ONLY view (every period it returns has status "
                "'active'; no status/includeInactive parameter surfaces ended ones), "
                "so it is structurally incapable of showing a finished MLB program. "
                "This is a program-lifecycle statement at this timestamp, never "
                "'MLB has none'"
                if rows and not mlb_program_entries else None),
            "markets": [{k: r[k] for k in ("slug", "live", "status", "game_start",
                                            "fee_coefficient",
                                            "rebate_eligible_by_fee_coefficient",
                                            "in_program_list", "program_periods")}
                        for r in rows[:MLB_ROW_LIMIT]]}


def ledger_filled(our_fills: list[dict]) -> dict:
    """ESTIMATED: what the published maker rebate would be on this
    ledger's own fills, before and after the venue's cent rounding."""
    raw = D("0"); rounded = D("0"); n = 0
    for f in our_fills:
        try:
            q, px = D(str(f["qty"])), D(str(f["price"]))
        except Exception:                             # noqa: BLE001
            continue
        r = MAKER_REBATE_RATE * q * px * (D(1) - px)
        raw += r; rounded += _cents(r); n += 1
    return {"fills": n, "rebate_raw_usd": str(raw.quantize(D("0.000001"))),
            "rebate_after_per_fill_rounding_usd": str(rounded),
            "labels": "ESTIMATED from the published formula; not a credit"}


def our_fills_from_ledger() -> list[dict]:
    path = q1.state_dir() / "ledger.jsonl"
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:                             # noqa: BLE001
            continue
        if isinstance(r, Mapping) and r.get("kind") == "filled" and r.get("price") and r.get("qty"):
            out.append({"slug": r.get("slug"), "price": r["price"], "qty": r["qty"], "t": r.get("t")})
    return out


def incentive_audit(t: q1.Transport) -> dict:
    """The whole read-only audit; every block contained so one venue
    surprise never hides the others."""
    fills = our_fills_from_ledger()
    slugs = sorted({f["slug"] for f in fills if f.get("slug")})
    out: dict = {"our_filled_markets": slugs, "our_fills": len(fills)}
    # ONE paced walk of the program list per audit, shared by the two
    # consumers below (each used to walk it on its own: ~400 GETs back to
    # back, which is what starved the 08:21Z preflight read of rate budget)
    try:
        census: Optional[dict] = program_list(t)
    except Exception as exc:                          # noqa: BLE001
        census = {"ok": False, "error": str(exc)[:200], "pages": 0,
                  "rows": 0, "entries": [], "exhausted": False,
                  "truncated_at_page_cap": False, "page_cap": 0,
                  "by_league": {}, "programs": [], "period_statuses": {},
                  "limits": []}
    out["program_list_walks"] = 1
    for name, fn in (("earnings", lambda: earnings(t)),
                     ("activities", lambda: activity_credits(t)),
                     ("balances", lambda: balances(t)),
                     ("programs", lambda: published_programs(t, slugs, census)),
                     # OWNER 5561482606: the full current MLB universe by slug
                     ("mlb_scan", lambda: mlb_program_scan(t, census))):
        try:
            out[name] = fn()
        except Exception as exc:                      # noqa: BLE001
            out[name] = {"ok": False, "error": str(exc)[:200]}
    rows = (out.get("activities") or {}).pop("_rows", []) if isinstance(out.get("activities"), dict) else []
    try:
        out["trade_fields"] = trade_fee_fields(rows)
    except Exception as exc:                          # noqa: BLE001
        out["trade_fields"] = {"ok": False, "error": str(exc)[:200]}
    try:
        out["credited_on_fills"] = credited_on_fills(rows)
    except Exception as exc:                          # noqa: BLE001
        out["credited_on_fills"] = {"ok": False, "error": str(exc)[:200]}
    out["estimated_rebate_on_our_fills"] = ledger_filled(fills)
    typical = D("0.45")
    out["size_arithmetic"] = {
        "rebate_at_our_size": rebate_at(D("0.02"), typical),
        "rebate_at_l5_p50_size": rebate_at(D("10"), typical),
        "min_shares_for_one_cent_rebate_at_p0.45": min_qty_for_nonzero_rebate(typical),
        "taker_fee_at_l5_p50_size": str(_cents(TAKER_RATE * D("10") * typical * (D(1) - typical))),
        "source": "docs.polymarket.us/fees: 0.06 / 0.0125 * C * p * (1-p), banker's rounding to the cent",
    }
    e, a = out.get("earnings") or {}, out.get("activities") or {}
    observed_total = e.get("total_usd") if e.get("ok") else None
    out["verdict"] = {
        "observed_credits_earnings_endpoint": (
            f"{e.get('rows')} rows, total {observed_total}" if e.get("ok") else f"unavailable: {e.get('error')}"),
        "observed_credit_rows_in_activities": a.get("credit_rows_n"),
        "trade_rows_carry_fee_or_rebate_fields": bool((out.get("trade_fields") or {}).get("fee_or_rebate_key_paths")),
        "published_programs_covering_our_markets": len((out.get("programs") or {}).get("our_markets_in_programs") or []),
        "rebate_at_our_executed_size_after_rounding": out["size_arithmetic"]["rebate_at_our_size"]["credited_after_rounding_usd"],
        "mlb": (out.get("mlb_scan") or {}).get("statement"),
        # the verdict line for the sized rung: what the venue actually paid US
        "venue_credited_on_our_fills_usd": (out.get("credited_on_fills") or {}).get(
            "credited_to_us_usd"),
        "venue_charged_us_on_our_fills_usd": (out.get("credited_on_fills") or {}).get(
            "charged_to_us_usd"),
        "our_fills_with_a_credit": (out.get("credited_on_fills") or {}).get(
            "our_fills_with_a_credit"),
        "our_fills_at_or_above_rebate_floor": (out.get("credited_on_fills") or {}).get(
            "trades_at_or_above_rebate_floor"),
        "reading": ("only observed / venue-confirmed credits are realized; published programs and "
                    "formula estimates are labelled and never counted"),
    }
    return out
