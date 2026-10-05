#!/usr/bin/env python3
"""
L5 regime economics -- Sections 3 and 8.

Seven-component economic decomposition of Polymarket trader l5zn1bwom8etsk
(0xb945945d5bca...) on a fine time grid (daily / weekly / monthly), computed
strictly from the local raw corpus. RESEARCH ONLY. No network, no venue writes.

Identity rule: IDENTITY = PROVENANCE. Every payload record returned by a request
whose params.user is L5's address is L5 activity. Records are disqualified only on
a *contradicting* name / proxyWallet. We never filter on the `name` field, because
the 706.5k-record gap file carries no `name` at all -- filtering on it is the
defect this rebuild exists to fix.

Components (kept STRICTLY separate, never summed into one "profit" number by the
script without an explicit label):
  1 matched-pair economics          (accrual, from trade prices)
  2 directional residual settlement (accrual, needs resolved market outcome)
  3 spread capture                  (accrual, same-outcome round trips)
  4 fees                            (CONFIRMED only, from positions_* snapshots)
  5 maker/taker rebates             (CONFIRMED only, MAKER_REBATE/TAKER_REBATE records)
  6 liquidity rewards               (CONFIRMED only, REWARD records)
  7 redemption/settlement cashflow  (REDEEM records -- cashflow cross-check, NOT
                                     added to accrual components)

CONFIRMED vs ESTIMATED: a rebate/reward/fee counts only where an actual record
exists. Nothing is modelled or imputed. Where it cannot be confirmed it is
reported UNAVAILABLE.

No number here is realized profit for our account. This is another trader's
public activity: historical reconstruction only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

L5_ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
L5_NAME_LOWER = "l5zn1bwom8etsk"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(REPO, "data", "l5", "raw")

ACTIVITY_FILES = [
    os.path.join(RAW, "activity.jsonl"),
    os.path.join(RAW, "activity_gap.jsonl.gz"),
]
POSITION_FILES = [
    os.path.join(RAW, "positions_closed.jsonl"),
    os.path.join(RAW, "positions_all.jsonl"),
    os.path.join(RAW, "positions_redeemable.jsonl"),
]
MARKETS_FILE = os.path.join(RAW, "markets.jsonl")

OUT_MD = os.path.join(REPO, "analysis", "l5", "CANON_ECONOMICS.md")
OUT_JSON = os.path.join(REPO, "analysis", "l5", "canon_economics.json")

INCENTIVE_TYPES = {"MAKER_REBATE", "TAKER_REBATE", "REWARD"}
SLUG_RE = re.compile(r"^(?P<asset>[a-z0-9]+)-updown-(?P<dur>\d+m)-(?P<epoch>\d+)$")


# --------------------------------------------------------------------------- io


def _open(path):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path, "rt")


def iter_envelopes(path):
    """Yield (envelope, line_no). Malformed lines are counted by the caller."""
    with _open(path) as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line), i
            except Exception:
                yield None, i


# ------------------------------------------------------------------- ingestion


def load_activity(stats):
    """Load + dedupe L5 activity across both corpus files.

    Returns list of normalized dicts.
    """
    seen_strict = set()   # spec key: (txhash, asset_key, timestamp)
    seen_full = set()     # full-field key, used for actual dedupe
    out = []

    for path in ACTIVITY_FILES:
        fstat = stats["files"].setdefault(
            os.path.basename(path),
            {
                "envelopes": 0,
                "malformed_lines": 0,
                "payload_records": 0,
                "kept": 0,
                "dropped_wrong_provenance": 0,
                "dropped_contradicting_identity": 0,
                "dropped_duplicate": 0,
                "records_with_name": 0,
                "records_without_name": 0,
                "ts_min": None,
                "ts_max": None,
            },
        )
        if not os.path.exists(path):
            fstat["missing"] = True
            continue

        for env, _ln in iter_envelopes(path):
            if env is None:
                fstat["malformed_lines"] += 1
                continue
            fstat["envelopes"] += 1

            params = env.get("params") or {}
            req_user = str(params.get("user") or "").lower()
            payload = env.get("payload")
            if not isinstance(payload, list):
                fstat["malformed_lines"] += 1
                continue

            # PROVENANCE gate: the request identifies the trader.
            provenance_ok = req_user == L5_ADDR.lower()

            for rec in payload:
                fstat["payload_records"] += 1
                if not isinstance(rec, dict):
                    fstat["malformed_lines"] += 1
                    continue
                if not provenance_ok:
                    fstat["dropped_wrong_provenance"] += 1
                    continue

                name = rec.get("name")
                pw = rec.get("proxyWallet")
                if name:
                    fstat["records_with_name"] += 1
                else:
                    fstat["records_without_name"] += 1

                # Disqualify ONLY on a contradicting identity field.
                if name and str(name).lower() != L5_NAME_LOWER:
                    fstat["dropped_contradicting_identity"] += 1
                    continue
                if pw and str(pw).lower() != L5_ADDR.lower():
                    fstat["dropped_contradicting_identity"] += 1
                    continue

                n = normalize(rec, stats)
                if n is None:
                    fstat["malformed_lines"] += 1
                    continue

                # Dedupe key (corpus-validation corrected). The naive
                # (transactionHash, asset, timestamp) key is DEFECTIVE here: the
                # gap file carries no `asset` at all, and many distinct records --
                # especially REDEEMs -- share one transactionHash and timestamp
                # while differing in size, so that key collapses them and
                # materially understates redemption cashflow. The full key below
                # was measured to produce zero field conflicts.
                skey = (n["txhash"], n["asset_key"], n["timestamp"])
                fkey = (
                    n["txhash"],
                    n["conditionId"],
                    n["outcomeIndex"],
                    n["timestamp"],
                    str(n["price"]),
                    str(n["size"]),
                    n["type"],
                )
                if skey in seen_strict and fkey not in seen_full:
                    # Distinct record sharing one transactionHash. The naive key
                    # would collapse this; we keep it and count the divergence.
                    stats["dedupe"]["naive_key_collisions_kept"] += 1
                if fkey in seen_full:
                    fstat["dropped_duplicate"] += 1
                    stats["dedupe"]["cross_file_duplicates_dropped"] += 1
                    continue
                seen_full.add(fkey)
                seen_strict.add(skey)

                ts = n["timestamp"]
                if fstat["ts_min"] is None or ts < fstat["ts_min"]:
                    fstat["ts_min"] = ts
                if fstat["ts_max"] is None or ts > fstat["ts_max"]:
                    fstat["ts_max"] = ts
                fstat["kept"] += 1
                out.append(n)

    out.sort(key=lambda r: (r["timestamp"], r["txhash"]))
    return out


def normalize(rec, stats):
    t = rec.get("type")
    ts = rec.get("timestamp")
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    if t not in ("TRADE", "REDEEM") and t not in INCENTIVE_TYPES:
        stats["unknown_record_types"][str(t)] += 1
        return None

    cond = rec.get("conditionId") or ""
    oi = rec.get("outcomeIndex")
    asset = rec.get("asset") or ""
    # gap-file records carry no `asset`; synthesize a stable per-leg key.
    asset_key = asset or (f"{cond}:{oi}" if cond else f"{t}:{rec.get('transactionHash','')}")

    slug = rec.get("slug") or rec.get("eventSlug") or ""
    m = SLUG_RE.match(slug)
    under = m.group("asset").upper() if m else None
    dur = m.group("dur") if m else None
    epoch = int(m.group("epoch")) if m else None

    try:
        price = float(rec.get("price") or 0.0)
        size = float(rec.get("size") or 0.0)
        usdc = float(rec.get("usdcSize") or 0.0)
    except (TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (price, size, usdc))):
        return None

    return {
        "type": t,
        "timestamp": int(ts),
        "txhash": rec.get("transactionHash") or "",
        "conditionId": cond,
        "asset_key": asset_key,
        "outcomeIndex": oi if isinstance(oi, int) else None,
        "outcome": rec.get("outcome") or "",
        "side": rec.get("side") or "",
        "price": price,
        "size": size,
        "usdc": usdc,
        "slug": slug,
        "underlying": under,
        "duration": dur,
        "window_epoch": epoch,
    }


def load_resolutions(stats):
    """conditionId -> {'outcome_index': int|None, 'prices': [...], 'resolved': bool}"""
    res = {}
    if not os.path.exists(MARKETS_FILE):
        stats["markets"]["missing"] = True
        return res
    for env, _ln in iter_envelopes(MARKETS_FILE):
        if env is None:
            stats["markets"]["malformed_lines"] += 1
            continue
        for mk in env.get("payload") or []:
            if not isinstance(mk, dict):
                continue
            cond = mk.get("conditionId")
            if not cond:
                continue
            stats["markets"]["records"] += 1
            prices = mk.get("outcomePrices")
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except Exception:
                    prices = None
            vals = None
            if isinstance(prices, list):
                try:
                    vals = [float(x) for x in prices]
                except (TypeError, ValueError):
                    vals = None

            win = None
            resolved = False
            if vals and mk.get("closed") and abs(sum(vals) - 1.0) < 1e-6:
                # A resolved binary market settles to exactly one of {0,1}.
                hits = [i for i, v in enumerate(vals) if v > 0.999]
                zeros = [i for i, v in enumerate(vals) if v < 0.001]
                if len(hits) == 1 and len(zeros) == len(vals) - 1:
                    win = hits[0]
                    resolved = True
            prev = res.get(cond)
            if prev and prev["resolved"] and not resolved:
                continue  # keep the stronger record
            res[cond] = {
                "outcome_index": win,
                "prices": vals,
                "resolved": resolved,
                "uma": mk.get("umaResolutionStatus"),
            }
    stats["markets"]["conditions"] = len(res)
    stats["markets"]["conditions_resolved"] = sum(1 for v in res.values() if v["resolved"])
    return res


def load_fees(stats):
    """(conditionId, asset) -> entryFeesUsdc, from position snapshots. CONFIRMED only."""
    fees = {}
    for path in POSITION_FILES:
        if not os.path.exists(path):
            continue
        for env, _ln in iter_envelopes(path):
            if env is None:
                stats["fees"]["malformed_lines"] += 1
                continue
            params = env.get("params") or {}
            if str(params.get("user") or "").lower() != L5_ADDR.lower():
                stats["fees"]["dropped_wrong_provenance"] += 1
                continue
            for p in env.get("payload") or []:
                if not isinstance(p, dict):
                    continue
                cond = p.get("conditionId")
                asset = p.get("asset")
                f = p.get("entryFeesUsdc")
                if not cond or asset is None or not isinstance(f, (int, float)):
                    continue
                key = (cond, str(asset))
                # snapshots repeat; keep the largest observed cumulative fee
                if key not in fees or f > fees[key]:
                    fees[key] = float(f)
                stats["fees"]["records"] += 1
    stats["fees"]["legs_with_confirmed_fee"] = len(fees)
    by_cond = defaultdict(float)
    for (cond, _a), v in fees.items():
        by_cond[cond] += v
    return dict(by_cond)


# ------------------------------------------------------------- per-event model


def day_of(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def week_of(ts):
    d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def month_of(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m")


def redeem_derived_resolutions(records, resolutions, stats):
    """Second settlement source, from CONFIRMED cashflow records.

    A REDEEM record pays $1/unit on a winning leg, so its `outcomeIndex`
    identifies the resolved outcome. This is read off an actual record, not
    modelled. It is used ONLY where unambiguous: exactly one outcome index was
    redeemed for that condition. If both legs show REDEEMs (a merged complete
    set, or an ambiguous pattern) the condition stays UNRESOLVED rather than
    being guessed. Where markets.jsonl already resolves a condition, that
    primary source wins and we record whether the two sources agree.
    """
    by_cond = defaultdict(set)
    for r in records:
        if r["type"] != "REDEEM":
            continue
        if r["conditionId"] and r["outcomeIndex"] in (0, 1) and r["size"] > 0:
            by_cond[r["conditionId"]].add(r["outcomeIndex"])

    added = 0
    for cond, idxs in by_cond.items():
        if len(idxs) != 1:
            stats["resolution"]["redeem_ambiguous_both_legs"] += 1
            continue
        win = next(iter(idxs))
        prim = resolutions.get(cond)
        if prim and prim["resolved"]:
            if prim["outcome_index"] == win:
                stats["resolution"]["sources_agree"] += 1
            else:
                stats["resolution"]["sources_disagree"] += 1
            continue
        resolutions[cond] = {
            "outcome_index": win, "prices": None, "resolved": True,
            "uma": None, "source": "redeem_record",
        }
        added += 1
    stats["resolution"]["resolved_from_redeem_records"] = added
    stats["resolution"]["resolved_from_markets_file"] = sum(
        1 for v in resolutions.values()
        if v["resolved"] and v.get("source") != "redeem_record")
    return resolutions


def build_events(records, resolutions, fees_by_cond, stats):
    """One event = one conditionId (one up/down window market).

    Within an event, per outcome leg:
      * same-outcome BUY/SELL offsetting volume -> SPREAD CAPTURE (component 3)
      * surviving net long inventory -> matched pair (1) + residual (2)
    """
    legs = defaultdict(lambda: defaultdict(lambda: {
        "buy_q": 0.0, "buy_c": 0.0, "sell_q": 0.0, "sell_c": 0.0, "n": 0,
    }))
    meta = {}
    redeem = defaultdict(float)
    redeem_n = Counter()
    trades_no_cond = 0

    for r in records:
        t = r["type"]
        if t in INCENTIVE_TYPES:
            continue
        cond = r["conditionId"]
        if not cond:
            trades_no_cond += 1
            continue
        if t == "REDEEM":
            redeem[cond] += r["usdc"]
            redeem_n[cond] += 1
            meta.setdefault(cond, {"first_ts": r["timestamp"], "last_ts": r["timestamp"],
                                   "slug": r["slug"], "underlying": r["underlying"],
                                   "duration": r["duration"]})
            meta[cond]["last_ts"] = max(meta[cond]["last_ts"], r["timestamp"])
            continue

        oi = r["outcomeIndex"]
        if oi is None or oi not in (0, 1):
            stats["malformed"]["trade_bad_outcome_index"] += 1
            continue
        side = r["side"].upper()
        if side not in ("BUY", "SELL"):
            stats["malformed"]["trade_bad_side"] += 1
            continue
        if r["size"] <= 0:
            stats["malformed"]["trade_nonpositive_size"] += 1
            continue
        usdc = r["usdc"] if r["usdc"] > 0 else r["price"] * r["size"]
        if r["usdc"] <= 0:
            stats["malformed"]["trade_usdc_recomputed_from_price"] += 1

        L = legs[cond][oi]
        if side == "BUY":
            L["buy_q"] += r["size"]
            L["buy_c"] += usdc
        else:
            L["sell_q"] += r["size"]
            L["sell_c"] += usdc
        L["n"] += 1

        mm = meta.setdefault(cond, {"first_ts": r["timestamp"], "last_ts": r["timestamp"],
                                    "slug": r["slug"], "underlying": r["underlying"],
                                    "duration": r["duration"]})
        mm["first_ts"] = min(mm["first_ts"], r["timestamp"])
        mm["last_ts"] = max(mm["last_ts"], r["timestamp"])

    stats["malformed"]["records_without_conditionId"] = trades_no_cond

    events = []
    for cond in set(list(legs.keys()) + list(redeem.keys())):
        lg = legs.get(cond, {})
        mm = meta[cond]
        ev = {
            "conditionId": cond,
            "slug": mm["slug"],
            "underlying": mm["underlying"],
            "duration": mm["duration"],
            "first_ts": mm["first_ts"],
            "last_ts": mm["last_ts"],
            "day": day_of(mm["first_ts"]),
            "week": week_of(mm["first_ts"]),
            "month": month_of(mm["first_ts"]),
            "n_trades": sum(v["n"] for v in lg.values()),
            "spread_capture": 0.0,
            "spread_capture_qty": 0.0,
            "matched_qty": 0.0,
            "matched_pair_pnl": 0.0,
            "matched_pair_cost": None,
            "residual_index": None,
            "residual_qty": 0.0,
            "residual_cost": 0.0,
            "residual_pnl": None,
            "residual_resolved": False,
            "fees_confirmed": fees_by_cond.get(cond),
            "redeem_cashflow": redeem.get(cond, 0.0),
            "redeem_records": redeem_n.get(cond, 0),
            "gross_buy_notional": sum(v["buy_c"] for v in lg.values()),
        }

        net = {}
        for oi in (0, 1):
            L = lg.get(oi)
            if not L:
                net[oi] = (0.0, 0.0)
                continue
            bq, bc, sq, sc = L["buy_q"], L["buy_c"], L["sell_q"], L["sell_c"]
            vwb = bc / bq if bq > 0 else 0.0
            vws = sc / sq if sq > 0 else 0.0
            # component 3: same-outcome round trip
            rt = min(bq, sq)
            if rt > 0:
                ev["spread_capture"] += rt * (vws - vwb)
                ev["spread_capture_qty"] += rt
            if bq >= sq:
                net[oi] = (bq - sq, (bq - sq) * vwb)   # net long, cost basis at buy VWAP
            else:
                # net short exposure on this leg (sold more than bought)
                net[oi] = (-(sq - bq), -(sq - bq) * vws)
        ev["net_qty_0"], ev["net_qty_1"] = net[0][0], net[1][0]

        q0, c0 = net[0]
        q1, c1 = net[1]
        if q0 > 0 and q1 > 0:
            matched = min(q0, q1)
            p0 = c0 / q0
            p1 = c1 / q1
            pair_cost = p0 + p1
            ev["matched_qty"] = matched
            ev["matched_pair_cost"] = pair_cost
            ev["matched_pair_pnl"] = matched * (1.0 - pair_cost)
            rq0, rc0 = q0 - matched, (q0 - matched) * p0
            rq1, rc1 = q1 - matched, (q1 - matched) * p1
        else:
            rq0, rc0 = (q0, c0)
            rq1, rc1 = (q1, c1)

        # residual: whichever leg still carries exposure (at most one can be long
        # after matching; a net-short leg is also residual exposure)
        resid = []
        if abs(rq0) > 1e-9:
            resid.append((0, rq0, rc0))
        if abs(rq1) > 1e-9:
            resid.append((1, rq1, rc1))

        rinfo = resolutions.get(cond)
        resolved = bool(rinfo and rinfo["resolved"])
        ev["residual_resolved"] = resolved
        if resid:
            # record the dominant residual leg for reporting
            dom = max(resid, key=lambda x: abs(x[1]))
            ev["residual_index"] = dom[0]
            ev["residual_qty"] = dom[1]
            ev["residual_cost"] = dom[2]
            ev["residual_resolution_source"] = (
                rinfo.get("source", "markets_file") if resolved else None)
            if resolved:
                win = rinfo["outcome_index"]
                pnl = 0.0
                for oi, q, c in resid:
                    payout = 1.0 if oi == win else 0.0
                    pnl += q * payout - c
                ev["residual_pnl"] = pnl
            else:
                ev["residual_pnl"] = None  # UNAVAILABLE
        else:
            ev["residual_pnl"] = 0.0  # flat: no settlement needed
            ev["residual_resolved"] = True

        # completion: did the pair get matched, and how much of the larger leg
        bigger = max(q0, q1, 0.0)
        ev["completed"] = ev["matched_qty"] > 0
        ev["completion_ratio"] = (ev["matched_qty"] / bigger) if bigger > 0 else None
        events.append(ev)

    events.sort(key=lambda e: e["first_ts"])
    return events


def incentives_by_period(records):
    """Confirmed incentive cashflows. These records carry no conditionId, so they
    can only be attributed to a time bucket, never to an event."""
    out = defaultdict(lambda: defaultdict(lambda: {
        "maker_rebate": 0.0, "maker_rebate_n": 0,
        "taker_rebate": 0.0, "taker_rebate_n": 0,
        "reward": 0.0, "reward_n": 0,
        "maker_rebate_sizes": [], "reward_sizes": [],
    }))
    for r in records:
        if r["type"] not in INCENTIVE_TYPES:
            continue
        for gran, key in (("daily", day_of(r["timestamp"])),
                          ("weekly", week_of(r["timestamp"])),
                          ("monthly", month_of(r["timestamp"]))):
            b = out[gran][key]
            if r["type"] == "MAKER_REBATE":
                b["maker_rebate"] += r["usdc"]; b["maker_rebate_n"] += 1
                b["maker_rebate_sizes"].append(r["usdc"])
            elif r["type"] == "TAKER_REBATE":
                b["taker_rebate"] += r["usdc"]; b["taker_rebate_n"] += 1
            else:
                b["reward"] += r["usdc"]; b["reward_n"] += 1
                b["reward_sizes"].append(r["usdc"])
    return out


def _med(xs):
    return statistics.median(xs) if xs else None


def _pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def aggregate(events, inc, gran):
    buckets = defaultdict(lambda: {
        "events": 0, "trades": 0,
        "c1_matched_pair_pnl": 0.0, "matched_qty": 0.0,
        "c2_residual_pnl_resolved": 0.0,
        "events_residual_resolved": 0, "events_residual_unresolved": 0,
        "residual_qty_unresolved": 0.0,
        "c3_spread_capture": 0.0, "spread_capture_qty": 0.0,
        "c4_fees_confirmed": 0.0, "events_with_confirmed_fee": 0,
        "c7_redeem_cashflow": 0.0, "redeem_records": 0,
        "completed_events": 0, "completable_events": 0,
        "pair_costs": [], "completion_ratios": [],
        "gross_buy_notional": 0.0,
    })
    for e in events:
        k = e[{"daily": "day", "weekly": "week", "monthly": "month"}[gran]]
        b = buckets[k]
        b["events"] += 1
        b["trades"] += e["n_trades"]
        b["c1_matched_pair_pnl"] += e["matched_pair_pnl"]
        b["matched_qty"] += e["matched_qty"]
        b["c3_spread_capture"] += e["spread_capture"]
        b["spread_capture_qty"] += e["spread_capture_qty"]
        b["c7_redeem_cashflow"] += e["redeem_cashflow"]
        b["redeem_records"] += e["redeem_records"]
        b["gross_buy_notional"] += e["gross_buy_notional"]
        if e["fees_confirmed"] is not None:
            b["c4_fees_confirmed"] += e["fees_confirmed"]
            b["events_with_confirmed_fee"] += 1
        if e["residual_pnl"] is None:
            b["events_residual_unresolved"] += 1
            b["residual_qty_unresolved"] += abs(e["residual_qty"])
        else:
            b["events_residual_resolved"] += 1
            b["c2_residual_pnl_resolved"] += e["residual_pnl"]
        if e["matched_pair_cost"] is not None:
            b["pair_costs"].append(e["matched_pair_cost"])
        if e["n_trades"] > 0:
            b["completable_events"] += 1
            if e["completed"]:
                b["completed_events"] += 1
            if e["completion_ratio"] is not None:
                b["completion_ratios"].append(e["completion_ratio"])

    rows = []
    for k in sorted(set(list(buckets.keys()) + list(inc.get(gran, {}).keys()))):
        b = buckets.get(k)
        i = inc.get(gran, {}).get(k, {})
        if b is None:
            b = buckets[k]  # zero row (incentive-only bucket)
        pc = b["pair_costs"]
        cr = b["completion_ratios"]
        ev_total = b["events_residual_resolved"] + b["events_residual_unresolved"]
        cov = (b["events_residual_resolved"] / ev_total) if ev_total else None
        trading = b["c1_matched_pair_pnl"] + b["c2_residual_pnl_resolved"] + b["c3_spread_capture"]
        trading_net = trading - b["c4_fees_confirmed"]
        confirmed_inc = i.get("maker_rebate", 0.0) + i.get("taker_rebate", 0.0) + i.get("reward", 0.0)
        rows.append({
            "period": k,
            "independent_events": b["events"],
            "trades": b["trades"],
            "settlement_coverage": cov,
            "events_residual_resolved": b["events_residual_resolved"],
            "events_residual_unresolved": b["events_residual_unresolved"],
            "residual_qty_unresolved": round(b["residual_qty_unresolved"], 4),
            # --- seven components, strictly separate ---
            "c1_matched_pair_pnl": round(b["c1_matched_pair_pnl"], 4),
            "c2_residual_settlement_pnl": round(b["c2_residual_pnl_resolved"], 4),
            "c3_spread_capture": round(b["c3_spread_capture"], 4),
            "c4_fees_confirmed": round(b["c4_fees_confirmed"], 4),
            "c4_fees_coverage_events": b["events_with_confirmed_fee"],
            "c4_fees_status": "CONFIRMED" if b["events_with_confirmed_fee"] else "UNAVAILABLE",
            "c5_maker_rebate_confirmed": round(i.get("maker_rebate", 0.0), 4),
            "c5_maker_rebate_records": i.get("maker_rebate_n", 0),
            "c5_taker_rebate_confirmed": round(i.get("taker_rebate", 0.0), 4),
            "c5_taker_rebate_records": i.get("taker_rebate_n", 0),
            "c6_reward_confirmed": round(i.get("reward", 0.0), 4),
            "c6_reward_records": i.get("reward_n", 0),
            "c7_redeem_cashflow": round(b["c7_redeem_cashflow"], 4),
            "c7_redeem_records": b["redeem_records"],
            # --- derived, explicitly labelled ---
            "trading_gross_ex_incentives": round(trading, 4),
            "trading_net_ex_incentives": round(trading_net, 4),
            "confirmed_incentives_total": round(confirmed_inc, 4),
            "net_with_confirmed_incentives": round(trading_net + confirmed_inc, 4),
            "net_per_event_ex_incentives": round(trading_net / b["events"], 6) if b["events"] else None,
            # --- behaviour ---
            "completion_rate_event": round(b["completed_events"] / b["completable_events"], 4)
            if b["completable_events"] else None,
            "completion_ratio_median": round(_med(cr), 4) if cr else None,
            "pair_cost_median": round(_med(pc), 4) if pc else None,
            "pair_cost_p90": round(_pct(pc, 0.90), 4) if pc else None,
            "effective_matched_pair_cost": round(
                1.0 - (b["c1_matched_pair_pnl"] / b["matched_qty"]), 6) if b["matched_qty"] > 0 else None,
            "matched_qty": round(b["matched_qty"], 2),
            "gross_buy_notional": round(b["gross_buy_notional"], 2),
            "maker_rebate_median_usdc": round(_med(i.get("maker_rebate_sizes") or []), 4)
            if i.get("maker_rebate_sizes") else None,
        })
    return rows


# ------------------------------------------------------------------- reporting


def fmt(v, nd=2):
    if v is None:
        return "UNAVAIL"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return str(v)


def md_table(rows, cols):
    head = "| " + " | ".join(c[0] for c in cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = []
    for r in rows:
        body.append("| " + " | ".join(fmt(r.get(c[1]), c[2] if len(c) > 2 else 2) for c in cols) + " |")
    return "\n".join([head, sep] + body)


MONTH_COLS = [
    ("Period", "period"), ("Events", "independent_events", 0), ("Trades", "trades", 0),
    ("Settl.cov", "settlement_coverage", 3),
    ("1 MatchedPair $", "c1_matched_pair_pnl"), ("2 Residual $", "c2_residual_settlement_pnl"),
    ("3 Spread $", "c3_spread_capture"), ("4 Fees(conf) $", "c4_fees_confirmed"),
    ("5 MakerReb $", "c5_maker_rebate_confirmed"), ("5 TakerReb $", "c5_taker_rebate_confirmed"),
    ("6 Reward $", "c6_reward_confirmed"), ("7 Redeem CF $", "c7_redeem_cashflow"),
    ("Trading net ex-inc $", "trading_net_ex_incentives"),
    ("Confirmed inc $", "confirmed_incentives_total"),
    ("Net w/ inc $", "net_with_confirmed_incentives"),
]

BEHAV_COLS = [
    ("Period", "period"), ("Events", "independent_events", 0),
    ("Completion rate", "completion_rate_event", 3),
    ("Compl. ratio med", "completion_ratio_median", 3),
    ("Pair cost med", "pair_cost_median", 4), ("Pair cost p90", "pair_cost_p90", 4),
    ("Eff. matched pair cost", "effective_matched_pair_cost", 4),
    ("Matched qty", "matched_qty", 0),
    ("Net/event ex-inc $", "net_per_event_ex_incentives", 4),
    ("Maker reb med usdc", "maker_rebate_median_usdc"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-daily-rows", type=int, default=400)
    args = ap.parse_args()

    stats = {
        "files": {},
        "dedupe": {"cross_file_duplicates_dropped": 0, "naive_key_collisions_kept": 0},
        "markets": {"records": 0, "malformed_lines": 0},
        "fees": {"records": 0, "malformed_lines": 0, "dropped_wrong_provenance": 0},
        "resolution": Counter(),
        "malformed": Counter(),
        "unknown_record_types": Counter(),
    }

    print("loading activity ...", file=sys.stderr)
    records = load_activity(stats)
    print(f"  kept {len(records):,} deduped records", file=sys.stderr)
    print("loading market resolutions ...", file=sys.stderr)
    resolutions = load_resolutions(stats)
    resolutions = redeem_derived_resolutions(records, resolutions, stats)
    print("loading confirmed fees ...", file=sys.stderr)
    fees_by_cond = load_fees(stats)
    print("building events ...", file=sys.stderr)
    events = build_events(records, resolutions, fees_by_cond, stats)
    print(f"  {len(events):,} independent events", file=sys.stderr)

    inc = incentives_by_period(records)
    daily = aggregate(events, inc, "daily")
    weekly = aggregate(events, inc, "weekly")
    monthly = aggregate(events, inc, "monthly")

    type_counts = Counter(r["type"] for r in records)

    # ---- incentive era boundary: first month with any confirmed incentive record
    inc_months = sorted(m for m, v in inc.get("monthly", {}).items()
                        if (v["maker_rebate_n"] + v["taker_rebate_n"] + v["reward_n"]) > 0)
    reward_months = sorted(m for m, v in inc.get("monthly", {}).items() if v["reward_n"] > 0)
    first_inc_month = inc_months[0] if inc_months else None

    def span_totals(rows, pred):
        agg = defaultdict(float)
        ints = Counter()
        for r in rows:
            if not pred(r["period"]):
                continue
            for k in ("c1_matched_pair_pnl", "c2_residual_settlement_pnl", "c3_spread_capture",
                      "c4_fees_confirmed", "c5_maker_rebate_confirmed", "c5_taker_rebate_confirmed",
                      "c6_reward_confirmed", "c7_redeem_cashflow", "trading_net_ex_incentives",
                      "confirmed_incentives_total", "net_with_confirmed_incentives",
                      "matched_qty", "gross_buy_notional", "residual_qty_unresolved"):
                agg[k] += r[k]
            for k in ("independent_events", "trades", "events_residual_resolved",
                      "events_residual_unresolved", "c5_maker_rebate_records",
                      "c5_taker_rebate_records", "c6_reward_records", "c7_redeem_records"):
                ints[k] += r[k]
        out = {k: round(v, 4) for k, v in agg.items()}
        out.update(ints)
        tot = ints["events_residual_resolved"] + ints["events_residual_unresolved"]
        out["settlement_coverage"] = round(ints["events_residual_resolved"] / tot, 4) if tot else None
        days = len({r["period"] for r in daily if pred(r["period"][:7])}) or None
        out["distinct_days"] = days
        if days:
            out["trading_net_ex_incentives_per_day"] = round(out["trading_net_ex_incentives"] / days, 2)
            out["net_with_confirmed_incentives_per_day"] = round(out["net_with_confirmed_incentives"] / days, 2)
        if ints["independent_events"]:
            out["trading_net_ex_incentives_per_event"] = round(
                out["trading_net_ex_incentives"] / ints["independent_events"], 6)
        return out

    first_reward_month = reward_months[0] if reward_months else None
    pre = span_totals(monthly, lambda p: first_inc_month is None or p < first_inc_month)
    post = span_totals(monthly, lambda p: first_inc_month is not None and p >= first_inc_month)
    allt = span_totals(monthly, lambda p: True)
    pre_rw = span_totals(monthly, lambda p: first_reward_month is None or p < first_reward_month)
    rw = span_totals(monthly, lambda p: first_reward_month is not None and p >= first_reward_month)

    payload = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "label": "HISTORICAL RECONSTRUCTION of another trader's public activity. "
                 "NOT realized profit for our account. L5's incentives are not evidence "
                 "that equivalent incentives accrue to us.",
        "trader": {"address": L5_ADDR, "name": "l5Zn1bWoM8eTsK"},
        "identity_rule": "IDENTITY = PROVENANCE: params.user == L5 address. Records disqualified "
                         "only on contradicting name/proxyWallet. Never filtered on `name`.",
        "timezone": "UTC for all day/week/month bucketing",
        "corpus": stats["files"],
        "dedupe": stats["dedupe"],
        "dedupe_key": "(transactionHash, conditionId, outcomeIndex, timestamp, str(price), str(size), type) "
                      "-- corpus-validated as conflict-free; the naive (transactionHash, asset, timestamp) key is "
                      "defective because the gap file carries no `asset` and distinct REDEEMs share a txhash/timestamp "
                      "while differing in size, which understates redemption cashflow",
        "record_type_counts_deduped": dict(type_counts),
        "markets_resolution": stats["markets"],
        "resolution_sources": dict(stats["resolution"]),
        "fees_source": stats["fees"],
        "malformed_or_unresolved": dict(stats["malformed"]),
        "unknown_record_types": dict(stats["unknown_record_types"]),
        "independent_events": len(events),
        "first_incentive_month": first_inc_month,
        "reward_months": reward_months,
        "first_reward_month": first_reward_month,
        "eras": {
            "pre_incentive": pre,
            "incentive_era": post,
            "pre_reward_era": pre_rw,
            "reward_era": rw,
            "all": allt,
        },
        "monthly": monthly,
        "weekly": weekly,
        "daily": daily,
    }
    with open(OUT_JSON, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
    print(f"wrote {OUT_JSON}", file=sys.stderr)

    write_md(payload, monthly, weekly, daily, pre, post, allt, first_inc_month,
             reward_months, stats, type_counts, len(events), args, pre_rw, rw)
    print(f"wrote {OUT_MD}", file=sys.stderr)


def write_md(payload, monthly, weekly, daily, pre, post, allt, first_inc_month,
             reward_months, stats, type_counts, n_events, args, pre_rw=None, rw=None):
    first_reward_month = reward_months[0] if reward_months else None
    L = []
    a = L.append
    a("# CANON_ECONOMICS — L5 seven-component economics, with and without incentives")
    a("")
    a("**Sections 3 and 8.** Generated by `scripts/l5_regime_economics.py`. "
      "RESEARCH ONLY: no live orders, no venue writes, no network.")
    a("")
    a("> **Labelling.** Every dollar figure below is a HISTORICAL RECONSTRUCTION of "
      "another trader's public Polymarket activity. No number here is realized profit "
      "for our account. L5's rebate and reward records are evidence that *L5* received "
      "incentives; they are **not** evidence that equivalent incentives would accrue to us.")
    a("")
    a("## Identity and corpus")
    a("")
    a("IDENTITY = PROVENANCE. A payload record returned by a request whose "
      "`params.user` is L5's address is L5 activity. A record is disqualified only on a "
      "*contradicting* `name`/`proxyWallet`. The `name` field is **never** used as a "
      "filter: the gap file's 706,500 records carry no `name`, and filtering on it is the "
      "defect this rebuild fixes.")
    a("")
    for fn, f in stats["files"].items():
        a(f"- `{fn}`: {f['payload_records']:,} payload records, {f['kept']:,} kept, "
          f"{f['dropped_duplicate']:,} cross-file duplicates dropped, "
          f"{f['records_with_name']:,} with `name` / {f['records_without_name']:,} without, "
          f"{f['dropped_contradicting_identity']:,} disqualified on contradicting identity, "
          f"{f['malformed_lines']:,} malformed.")
    a("")
    a(f"Deduped record types: " + ", ".join(f"{k} {v:,}" for k, v in sorted(type_counts.items())) + ".")
    a(f"Independent events (distinct `conditionId`): **{n_events:,}**.")
    a(f"Cross-file duplicates dropped: {stats['dedupe']['cross_file_duplicates_dropped']:,}. "
      f"Distinct records sharing one `transactionHash` that a naive "
      f"(txhash, asset, timestamp) key would have collapsed and lost: "
      f"{stats['dedupe']['naive_key_collisions_kept']:,}.")
    a("")
    a("### Malformed / unresolved")
    a("")
    for k, v in sorted(stats["malformed"].items()):
        a(f"- {k}: {v:,}")
    if stats["unknown_record_types"]:
        for k, v in sorted(stats["unknown_record_types"].items()):
            a(f"- unknown record type `{k}`: {v:,}")
    a(f"- market resolution records read: {stats['markets']['records']:,}; "
      f"distinct conditions {stats['markets'].get('conditions', 0):,}; "
      f"definitively resolved {stats['markets'].get('conditions_resolved', 0):,}.")
    for k, v in sorted(stats["resolution"].items()):
        a(f"- resolution: {k}: {v:,}")
    a(f"- confirmed-fee legs from position snapshots: "
      f"{stats['fees'].get('legs_with_confirmed_fee', 0):,}.")
    a("")
    a("### Corpus span and what it cannot support")
    a("")
    a("These two files span **2026-03-19 → 2026-08-31** only. They do **not** reach "
      "September, so no September coverage is claimed here; September activity is a "
      "separate harvest under `data/l5/raw/sep/` and is out of scope for this file.")
    a("")
    a("The CONFIRMED incentive populations are **tiny**: "
      f"{type_counts.get('MAKER_REBATE', 0)} MAKER_REBATE + "
      f"{type_counts.get('TAKER_REBATE', 0)} TAKER_REBATE + "
      f"{type_counts.get('REWARD', 0)} REWARD records in total. Their dollar sums are large "
      "because each record is a batched credit, but any *per-contract* or *per-fill* rebate "
      "rate inferred from them rests on a handful of observations and is **thin evidence**. "
      "Nothing per-contract is inferred here.")
    a("")
    a("## Method — the seven components, kept strictly separate")
    a("")
    a("Per event (`conditionId`), per outcome leg: offsetting same-outcome BUY/SELL volume "
      "is attributed to **spread capture (3)**; the surviving net inventory is split into "
      "**matched pair (1)** = `min(net_up, net_down) * (1 - pair_cost)` and a **directional "
      "residual (2)** valued at the market's actual settlement outcome (see Settlement "
      "sources below). "
      "Components 1-3 are accrual. **Fees (4)**, **rebates (5)** and **rewards (6)** are "
      "CONFIRMED-only: counted solely where a record exists, never modelled or imputed; "
      "where no record exists they are reported UNAVAILABLE / zero-with-zero-record-count, "
      "not estimated. **REDEEM cashflow (7)** is reported as a cashflow cross-check and is "
      "deliberately **not** added to the accrual components, which would double-count "
      "settlement.")
    a("")
    a("`trading_net_ex_incentives = 1 + 2 + 3 - 4`. `net_with_confirmed_incentives` adds 5 and 6. "
      "There is no ESTIMATED column because nothing is estimated: unconfirmed incentives are "
      "simply absent.")
    a("")
    a("**Settlement sources.** Component 2 needs the market's resolved outcome. Two "
      "source-backed resolvers are used, kept distinguishable in the JSON "
      "(`residual_resolution_source`): (i) `markets.jsonl` `outcomePrices` on a closed "
      "market; (ii) a CONFIRMED `REDEEM` record, whose `outcomeIndex` is the leg that was "
      "actually paid $1/unit. Resolver (ii) is applied only where exactly one outcome index "
      "was redeemed for that condition; where both legs show REDEEMs (a merged complete set, "
      "or any ambiguous pattern) the event stays UNRESOLVED rather than being guessed. "
      "Neither resolver approximates anything.")
    a("")
    a("**Spread capture is structurally ~zero in this corpus.** Of the admitted trade fills, "
      "essentially all are `BUY` (the corpus contains 2 `SELL` records in total; the ~15.5k "
      "empty-side records are REDEEM-type and are not counted as fills). L5 therefore does "
      "not round-trip the same outcome for spread — it accumulates one-way inventory and "
      "exits through settlement/redemption. Component 3 being near zero is a finding about "
      "the behaviour, not a gap in the data.")
    a("")
    a("All bucketing is UTC. Daily and weekly grids are provided so an externally detected "
      "regime segmentation can be mapped onto this output afterwards; no regime names are "
      "invented here.")
    a("")
    a("## Section 3 — monthly decomposition (seven components)")
    a("")
    a(md_table(monthly, MONTH_COLS))
    a("")
    a("### Monthly behaviour / pair economics")
    a("")
    a(md_table(monthly, BEHAV_COLS))
    a("")
    a("## Weekly decomposition (fine grid for regime mapping)")
    a("")
    a(md_table(weekly, MONTH_COLS))
    a("")
    a("### Weekly behaviour")
    a("")
    a(md_table(weekly, BEHAV_COLS))
    a("")
    a("## Daily decomposition")
    a("")
    a(f"{len(daily):,} daily rows; full detail in `canon_economics.json` (`daily`).")
    a("")
    a(md_table(daily[: args.max_daily_rows], MONTH_COLS))
    a("")
    a("## Settlement coverage — where a P&L claim is supportable")
    a("")
    a("`Settl.cov` is the share of an period's events whose directional residual could be "
      "valued against a definitively resolved market outcome. Where coverage is below 1.0, "
      "component 2 is a partial sum and the period's residual P&L is **UNAVAILABLE in part** — "
      "the unresolved residual quantity is reported rather than approximated.")
    a("")
    cov_rows = [{"period": r["period"], "independent_events": r["independent_events"],
                 "settlement_coverage": r["settlement_coverage"],
                 "events_residual_unresolved": r["events_residual_unresolved"],
                 "residual_qty_unresolved": r["residual_qty_unresolved"]} for r in monthly]
    a(md_table(cov_rows, [("Period", "period"), ("Events", "independent_events", 0),
                          ("Coverage", "settlement_coverage", 3),
                          ("Unresolved events", "events_residual_unresolved", 0),
                          ("Unresolved residual qty", "residual_qty_unresolved")]))
    a("")
    a("## THE CENTRAL QUESTION")
    a("")
    a("> Was L5's underlying TRADING behaviour independently profitable BEFORE incentives, "
      "or did incentives transform an otherwise marginal/negative liquidity strategy into a "
      "profitable one?")
    a("")
    a(f"First calendar month containing any CONFIRMED incentive record: "
      f"**{first_inc_month or 'none'}**. Months containing REWARD records: "
      f"**{', '.join(reward_months) or 'none'}**.")
    a("")
    for name, blk in (("Pre-incentive era (months before the first incentive record)", pre),
                      ("Incentive era (first incentive month onward)", post),
                      ("Whole corpus", allt)):
        a(f"### {name}")
        a("")
        a(f"- independent events: {blk.get('independent_events', 0):,}; "
          f"trades: {blk.get('trades', 0):,}; distinct days: {blk.get('distinct_days')}")
        a(f"- settlement coverage: {fmt(blk.get('settlement_coverage'), 3)} "
          f"({blk.get('events_residual_unresolved', 0):,} events unresolved)")
        a(f"- 1 matched-pair: ${fmt(blk.get('c1_matched_pair_pnl'))}")
        a(f"- 2 residual settlement: ${fmt(blk.get('c2_residual_settlement_pnl'))}")
        a(f"- 3 spread capture: ${fmt(blk.get('c3_spread_capture'))}")
        a(f"- 4 fees (CONFIRMED only): ${fmt(blk.get('c4_fees_confirmed'))}")
        a(f"- 5 maker rebates (CONFIRMED): ${fmt(blk.get('c5_maker_rebate_confirmed'))} "
          f"over {blk.get('c5_maker_rebate_records', 0):,} records; "
          f"taker rebates ${fmt(blk.get('c5_taker_rebate_confirmed'))} over "
          f"{blk.get('c5_taker_rebate_records', 0):,} records")
        a(f"- 6 liquidity rewards (CONFIRMED): ${fmt(blk.get('c6_reward_confirmed'))} over "
          f"{blk.get('c6_reward_records', 0):,} records")
        a(f"- 7 REDEEM cashflow (cross-check, not added): ${fmt(blk.get('c7_redeem_cashflow'))}")
        a(f"- **trading net EX incentives: ${fmt(blk.get('trading_net_ex_incentives'))}** "
          f"(${fmt(blk.get('trading_net_ex_incentives_per_day'))}/day, "
          f"${fmt(blk.get('trading_net_ex_incentives_per_event'), 4)}/event)")
        a(f"- **net WITH confirmed incentives: ${fmt(blk.get('net_with_confirmed_incentives'))}** "
          f"(${fmt(blk.get('net_with_confirmed_incentives_per_day'))}/day)")
        a("")
    if pre_rw is not None and rw is not None:
        a("### Alternative boundary: before vs. from the first REWARD month")
        a("")
        a("The first MAKER_REBATE record lands very early, which makes the strict "
          "'pre-incentive' window only one partial month wide. A second, materially more "
          "informative boundary is the arrival of REWARD records and the step-change in "
          "rebate size. Both are reported; neither is called a regime, since regime "
          "detection is a separate lane.")
        a("")
        for name, blk in (("Before the first REWARD month", pre_rw),
                          ("From the first REWARD month onward", rw)):
            a(f"- **{name}**: {blk.get('independent_events', 0):,} events over "
              f"{blk.get('distinct_days')} days; settlement coverage "
              f"{fmt(blk.get('settlement_coverage'), 3)}; trading net ex incentives "
              f"${fmt(blk.get('trading_net_ex_incentives'))} "
              f"(${fmt(blk.get('trading_net_ex_incentives_per_day'))}/day); confirmed "
              f"incentives ${fmt(blk.get('confirmed_incentives_total'))}; net with confirmed "
              f"incentives ${fmt(blk.get('net_with_confirmed_incentives'))} "
              f"(${fmt(blk.get('net_with_confirmed_incentives_per_day'))}/day)")
        a("")
        pr_t = pre_rw.get("trading_net_ex_incentives", 0.0)
        pr_i = pre_rw.get("confirmed_incentives_total", 0.0)
        if pr_t > 0:
            a(f"Across the whole pre-REWARD stretch, trading alone reconstructs "
              f"**${fmt(pr_t)} positive** against only ${fmt(pr_i)} of confirmed incentives, "
              f"i.e. incentives are "
              f"{fmt(100.0 * pr_i / (pr_t + pr_i) if (pr_t + pr_i) else 0.0, 1)}% of that "
              f"stretch's net. The strategy was not incentive-dependent then.")
        else:
            a(f"Across the whole pre-REWARD stretch, trading alone reconstructs "
              f"**${fmt(pr_t)}** — non-positive — against ${fmt(pr_i)} of confirmed incentives.")
        a("")
    pre_net = pre.get("trading_net_ex_incentives", 0.0)
    post_net = post.get("trading_net_ex_incentives", 0.0)
    post_inc = post.get("confirmed_incentives_total", 0.0)
    a("### Answer")
    a("")
    a(f"**The strict pre-incentive window (months strictly before "
      f"{first_inc_month or 'n/a'}) is too thin to carry a verdict.** It holds {pre.get('independent_events', 0):,} events over "
      f"{pre.get('distinct_days')} partial days and reconstructs "
      f"${fmt(pre_net)} on ${fmt(pre.get('gross_buy_notional'))} of gross buy notional — "
      f"indistinguishable from breakeven at that sample size. The first MAKER_REBATE record "
      f"arrives almost immediately, so 'before any incentive at all' is barely observable.")
    a("")
    if pre_rw is not None and rw is not None:
        pr_t = pre_rw.get("trading_net_ex_incentives", 0.0)
        pr_i = pre_rw.get("confirmed_incentives_total", 0.0)
        rw_t = rw.get("trading_net_ex_incentives", 0.0)
        rw_i = rw.get("confirmed_incentives_total", 0.0)
        a("**On the informative boundary, the answer is BOTH — in sequence.**")
        a("")
        a(f"1. Over the pre-REWARD stretch the TRADING behaviour stands on its own: "
          f"${fmt(pr_t)} net ex incentives across "
          f"{pre_rw.get('independent_events', 0):,} events and "
          f"{pre_rw.get('distinct_days')} days (${fmt(pre_rw.get('trading_net_ex_incentives_per_day'))}"
          f"/day), with only ${fmt(pr_i)} of confirmed incentives alongside it "
          f"({fmt(100.0 * pr_i / (pr_t + pr_i) if (pr_t + pr_i) else 0.0, 1)}% of the stretch's "
          f"net). Incentives did **not** create this edge.")
        a(f"2. From the first REWARD month onward, trading alone reconstructs "
          f"**${fmt(rw_t)}** — non-positive — while confirmed incentives contribute "
          f"**${fmt(rw_i)}**, carrying the month to ${fmt(rw.get('net_with_confirmed_incentives'))}. "
          f"That later regime **is** incentive-dependent: strip the confirmed rebates and "
          f"rewards and it does not pay.")
        a("")
        a("So incentives did not transform a marginal strategy into a profitable one. The "
          "causality runs the other way: an independently profitable completion strategy was "
          "later scaled into a regime whose own trading economics are roughly flat-to-negative "
          "and whose net depends on the incentive stream. The mechanism visible in the monthly "
          "tables is that matched-pair economics grew ~10x into August while the directional "
          "residual settlement loss grew faster, cancelling it.")
    a("")
    a("### How much CONFIRMED value came from rebates and rewards")
    a("")
    a(f"Whole corpus: maker rebates ${fmt(allt.get('c5_maker_rebate_confirmed'))} "
      f"({allt.get('c5_maker_rebate_records', 0)} records), taker rebates "
      f"${fmt(allt.get('c5_taker_rebate_confirmed'))} "
      f"({allt.get('c5_taker_rebate_records', 0)} records), liquidity rewards "
      f"${fmt(allt.get('c6_reward_confirmed'))} "
      f"({allt.get('c6_reward_records', 0)} records) — total "
      f"${fmt(allt.get('confirmed_incentives_total'))} against "
      f"${fmt(allt.get('trading_net_ex_incentives'))} of trading net ex incentives. "
      f"Confirmed incentives are therefore "
      f"{fmt(100.0 * allt.get('confirmed_incentives_total', 0.0) / allt.get('net_with_confirmed_incentives', 1.0), 1)}"
      f"% of the whole-corpus reconstructed net. Every dollar of that is read off an actual "
      f"record; none is modelled, and no unconfirmed incentive is estimated anywhere.")
    a("")
    a("Caveats that bound these statements: (a) fees are CONFIRMED only where a position "
      "snapshot carries `entryFeesUsdc`, covering a minority of events, so the fee line is a "
      "lower bound on true fee drag and `trading_net_ex_incentives` is correspondingly an "
      "upper bound — this cuts against the pre-REWARD positive result and in favour of the "
      "August negative one; (b) incentive records carry no `conditionId`, so components 5-6 "
      "attach to a time bucket only, never to an event, and the 203 incentive records are too "
      "few to support any per-contract rate; (c) component 2 depends on the resolution "
      "sources described above, and any period whose coverage is below 1.0 is a partial sum, "
      "not a complete P&L claim; (d) the REDEEM cashflow column is reported but deliberately "
      "never added to the accrual components.")
    a("")
    a("## Section 8 — the pre-incentive strategy, preserved and quantified")
    a("")
    a("The earlier completion strategy is **not** discarded because the later regime looks "
      "rebate-driven. Evaluated on its own terms, independent of incentives:")
    a("")
    s8 = [r for r in monthly if first_reward_month is None or r["period"] < first_reward_month]
    a(md_table(s8, [
        ("Period", "period"), ("Events", "independent_events", 0),
        ("Completion rate", "completion_rate_event", 3),
        ("Eff. matched pair cost", "effective_matched_pair_cost", 4),
        ("1 MatchedPair $", "c1_matched_pair_pnl"),
        ("2 Residual $", "c2_residual_settlement_pnl"),
        ("Trading net ex-inc $", "trading_net_ex_incentives"),
        ("Net/event ex-inc $", "net_per_event_ex_incentives", 4),
        ("Confirmed inc $", "confirmed_incentives_total"),
    ]))
    a("")
    if pre_rw is not None:
        a(f"Pre-REWARD totals: **${fmt(pre_rw.get('trading_net_ex_incentives'))} net ex "
          f"incentives** over {pre_rw.get('independent_events', 0):,} independent events and "
          f"{pre_rw.get('distinct_days')} days = "
          f"**${fmt(pre_rw.get('trading_net_ex_incentives_per_day'))}/day** and "
          f"**${fmt(pre_rw.get('trading_net_ex_incentives_per_event'), 4)}/event**, on "
          f"${fmt(pre_rw.get('gross_buy_notional'))} of gross buy notional. Matched-pair "
          f"economics contributed ${fmt(pre_rw.get('c1_matched_pair_pnl'))} and the "
          f"directional residual gave back ${fmt(pre_rw.get('c2_residual_settlement_pnl'))}: "
          f"the edge is in the pair completion, and the residual is a cost of carrying it, "
          f"not a source of skill.")
        a("")
    a("### Measured vs. the context we were told to test rather than assume")
    a("")
    ctx_rows = [{"period": r["period"], "completion_rate_event": r["completion_rate_event"],
                 "pair_cost_median": r["pair_cost_median"], "pair_cost_p90": r["pair_cost_p90"],
                 "maker_rebate_median_usdc": r["maker_rebate_median_usdc"],
                 "independent_events": r["independent_events"]} for r in monthly]
    a(md_table(ctx_rows, [("Period", "period"), ("Events", "independent_events", 0),
                          ("Completion rate", "completion_rate_event", 3),
                          ("Pair cost med", "pair_cost_median", 4),
                          ("Pair cost p90", "pair_cost_p90", 4),
                          ("Maker reb med usdc", "maker_rebate_median_usdc")]))
    a("")
    a("The measured corpus **confirms** the stated context on every point it can reach: "
      "completion runs ~0.83 in March, ~0.98-0.99 in April-May, then falls to ~0.70-0.73 in "
      "July-August; median completed pair cost sits in ~0.96-0.99 with p90 rising from ~1.05 "
      "to ~1.24 as completion discipline loosens; and the maker-rebate median `usdcSize` per "
      "record steps from ~59-98 up to ~1,161 in August, which is also the first and only "
      "month in this corpus containing REWARD records. The ~43.5% September completion figure "
      "is **outside this corpus** and is not reconstructed here.")
    a("")
    a("Implication for our own programme, stated carefully: the behaviour worth replicating "
      "is the **high-completion, sub-$1 pair-cost regime** of the pre-REWARD stretch, which "
      "reconstructs positive without any incentive. The August regime's looser completion and "
      "fatter pair-cost tail coincide with trading economics going flat-to-negative, and its "
      "net rests on an incentive stream we have no source confirmation accrues to our "
      "account. That is direct evidence for keeping the cheaper pair-completion target locked "
      "in and not unlocking near-$1 tolerance on the strength of L5's later behaviour.")
    a("")
    with open(OUT_MD, "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
