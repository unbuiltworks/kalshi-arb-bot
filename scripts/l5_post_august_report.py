#!/usr/bin/env python3
"""POST-AUGUST L5 behavior/economics report. READ-ONLY, NO NETWORK.

RECONCILIATION, NOT A REWRITE. The behavioral model is not reimplemented here.
This script IMPORTS the canonical pipeline -- `scripts.l5_regime_detect`'s
`parse_slug`, `day_of`, `build_events`, `event_features`, `cash_ledger` -- the
same functions that produced `analysis/l5/CANON_REGIMES.md`. What is new is
only the INGEST ADAPTER, because the post-August harvests are stored in three
different shapes while the canonical corpus is envelope-JSONL:

  1. `data/l5/raw/post_august/*.jsonl[.gz]`        envelope-per-line (this lane)
  2. `data/l5/sep2026/l5_activity_*_raw.json.gz`   {..., "rows": [...]}
  3. `data/l5/raw/sep/l5_sep23_25_daily.json`      {day: {type: [...]}}

All three are normalized into the canonical row schema, so every downstream
number is computed by the canonical code on the canonical fields.

DEDUPE KEY -- the Section-1-validated one, not the originally prescribed one:

    (transactionHash, conditionId, outcomeIndex, timestamp, str(price),
     str(size), type)

`(transactionHash, asset, timestamp)` is DEFECTIVE on this feed: the /activity
pages carry no `asset` field, so it degenerates to `(txh, None, ts)` and
silently collapses records that differ in price/size/side/outcome/type. Price
and size are in the key because one transactionHash demonstrably contains
several distinct fills at different prices. Collisions-with-differing-fields
are COUNTED and reported, not assumed to be zero.

FEES. Taker cost is priced by `src/pmus_fee_schedule.py` at each fill's OWN
timestamp (taker theta 0.0695 from 2026-09-17T04:00Z, primary captured
source), never by the stale 6% scalar. Fills inside the recorded
09-17..10-01 date-conflict window are flagged date-ambiguous rather than
quietly rated. The schedule fingerprint is asserted before anything is priced.

THE TAPE'S LIMITS, STATED NOT PAPERED OVER.
  * `execution.isAggressor` DOES NOT EXIST on this tape. The liquidity-role
    field present is CHAIN_TOPIC_POSITION (an on-chain CTF topic position).
    No maker/taker role is claimed, inferred or estimated anywhere. Taker fee
    is therefore reported as a BOUND under an explicit all-taker counterfactual
    and labelled as such -- it is not a measurement of what L5 paid.
  * Order placements and cancels are not public, so patience-in-book is
    UNAVAILABLE.
  * Spread capture is not observable without the contemporaneous book; it is
    reported UNAVAILABLE rather than modelled.

NO PROFIT CLAIM FOR US. Every figure is another trader's public activity:
historical observation only. L5's REWARD / MAKER_REBATE / TAKER_REBATE rows
are HIS incentive cashflows and are never netted into trading P&L, never
annualized for us, and never treated as evidence that an equivalent program
applies to our account.
"""
from __future__ import annotations

import datetime as dt
import glob
import gzip
import json
import os
import re
import statistics as st
import sys
from collections import Counter, defaultdict
from decimal import Decimal as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.l5_regime_detect import (  # noqa: E402  canonical pipeline
    L5_ADDR, L5_NAME, TRADE_TYPES, build_events, cash_ledger, day_of,
    event_features, parse_slug,
)
from src.pmus_fee_schedule import (  # noqa: E402
    FINGERPRINT, assert_frozen, date_conflict_window, maker_rebate, taker_fee,
)

EXPECT_FP = "ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629"

# Canonical regime change points in this repo. REGIME_2026-08-19_ON is the
# CURRENT regime and is reported separately; the pooled cross-regime figure
# (+$174.56/day) is NOT current expectancy and is not advertised as one.
REGIME_CPS = ["2026-04-25", "2026-06-23", "2026-08-19"]
CORPUS_ENDS = "2026-08-31"       # canonical corpus end, per CANON_REGIMES.md
PAIR_TARGET = 0.96               # spring-discipline pair-completion target
OUTDIR = os.path.join(ROOT, "analysis", "l5", "post_august")


# ------------------------------------------------------------ ingest adapter

def _norm(rec, src, seen, diag):
    """Normalize ONE raw /activity record into the canonical row schema."""
    if not isinstance(rec, dict):
        diag["records_malformed"] += 1
        return None
    diag["records_seen"] += 1

    # Identity: provenance admits, only a CONTRADICTING field disqualifies.
    nm, pw = rec.get("name"), rec.get("proxyWallet")
    if nm is None and pw is None:
        diag["records_without_name_field"] += 1
    else:
        diag["records_with_name_field"] += 1
        if (nm is not None and str(nm) != L5_NAME) or (
                pw is not None and str(pw).lower() != L5_ADDR):
            diag["records_contradicting_identity"] += 1
            return None

    ts = rec.get("timestamp")
    if not isinstance(ts, (int, float)):
        diag["records_malformed"] += 1
        return None
    ts = int(ts)
    rtype = rec.get("type")
    slug = rec.get("eventSlug") or rec.get("slug") or None
    if isinstance(slug, str) and not slug.strip():
        slug = None
    cond, oidx = rec.get("conditionId"), rec.get("outcomeIndex")
    if not (isinstance(cond, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", cond or "")):
        diag["records_nonhex_conditionid"] += 1
    try:
        price = float(rec.get("price") or 0.0)
        size = float(rec.get("size") or 0.0)
        usdc = float(rec.get("usdcSize") or 0.0)
    except Exception:
        diag["records_bad_numeric"] += 1
        return None

    key = (str(rec.get("transactionHash") or ""), str(cond or ""), oidx, ts,
           str(rec.get("price")), str(rec.get("size")), rtype)
    if key in seen:
        prev = seen[key]
        # A duplicate is only EXACT if every carried field agrees. Anything
        # else is a collision and is counted as such, never assumed absent.
        cmp_new = (price, size, usdc, rec.get("side"), slug, oidx, rtype)
        if prev["cmp"] != cmp_new:
            diag["collisions_differing_fields"] += 1
            diag["collision_examples"].append(
                {"key": [str(x) for x in key], "a": list(map(str, prev["cmp"])),
                 "b": list(map(str, cmp_new))})
        diag["duplicates_dropped"] += 1
        diag["dup_by_pair"]["%s|%s" % (prev["src"], src)] += 1
        return None
    seen[key] = {"src": src, "cmp": (price, size, usdc, rec.get("side"),
                                     slug, oidx, rtype)}

    diag["type_counts"][rtype] += 1
    diag["kept_by_src"][src] += 1
    asset, dur, epoch = parse_slug(slug)
    if slug is None:
        diag["records_empty_slug"] += 1
    if rtype in TRADE_TYPES:
        if slug is None:
            diag["trades_unresolved_slug"] += 1
        if epoch is None:
            diag["trades_unresolved_epoch"] += 1
        if size <= 0 or not (0.0 < price < 1.0):
            diag["trades_bad_numeric"] += 1
    return {"t": ts, "type": rtype, "side": rec.get("side"), "oidx": oidx,
            "outcome": rec.get("outcome"), "price": price, "size": size,
            "usdc": usdc, "slug": slug, "cond": cond, "asset_sym": asset,
            "dur": dur, "epoch": epoch, "src": src}


def _open(p):
    return gzip.open(p, "rt") if p.endswith(".gz") else open(p, "rt")


def load_post_august():
    diag = {
        "sources": {}, "records_seen": 0, "records_malformed": 0,
        "records_bad_numeric": 0, "records_with_name_field": 0,
        "records_without_name_field": 0, "records_contradicting_identity": 0,
        "records_nonhex_conditionid": 0, "records_empty_slug": 0,
        "trades_unresolved_slug": 0, "trades_unresolved_epoch": 0,
        "trades_bad_numeric": 0, "duplicates_dropped": 0,
        "collisions_differing_fields": 0, "collision_examples": [],
        "dup_by_pair": Counter(), "type_counts": Counter(),
        "kept_by_src": Counter(), "envelopes": Counter(),
        "envelope_statuses": Counter(), "failed_envelopes": [],
        "truncation_flags": [],
        "dedupe_key": "(transactionHash, conditionId, outcomeIndex, timestamp, "
                      "str(price), str(size), type)",
    }
    seen, rows = {}, []

    # (1) this lane's envelope-JSONL harvest
    for p in sorted(glob.glob(os.path.join(
            ROOT, "data/l5/raw/post_august/*.jsonl*"))):
        src = "post_august:" + os.path.basename(p)
        n = 0
        with _open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                env = json.loads(line)
                diag["envelopes"][src] += 1
                diag["envelope_statuses"][str(env.get("status"))] += 1
                if str((env.get("params") or {}).get("user", "")).lower() != L5_ADDR:
                    continue
                payload = env.get("payload")
                if not isinstance(payload, list):
                    diag["failed_envelopes"].append(
                        {"src": src, "status": env.get("status"),
                         "error": str(env.get("error"))[:200],
                         "window": env.get("window_start_utc")})
                    continue
                for rec in payload:
                    r = _norm(rec, src, seen, diag)
                    if r:
                        rows.append(r)
                        n += 1
        diag["sources"][src] = n

    # (2) the Sep 1-10 and Sep 22-23 harvests  {..., "rows": [...]}
    for p in sorted(glob.glob(os.path.join(
            ROOT, "data/l5/sep2026/l5_activity_*_raw.json*"))):
        src = "sep2026:" + os.path.basename(p)
        with _open(p) as fh:
            blob = json.load(fh)
        user = str(blob.get("user") or (blob.get("params") or {}).get("user") or "")
        if user.lower() != L5_ADDR:
            diag["sources"][src] = "REJECTED: provenance user=%r" % user
            continue
        n = 0
        for rec in blob.get("rows") or []:
            r = _norm(rec, src, seen, diag)
            if r:
                rows.append(r)
                n += 1
        diag["sources"][src] = n

    # (3) the Sep 23-25 daily harvest  {day: {type: [...]}}
    p = os.path.join(ROOT, "data/l5/raw/sep/l5_sep23_25_daily.json")
    if os.path.exists(p):
        src = "sep2325:l5_sep23_25_daily.json"
        with open(p) as fh:
            blob = json.load(fh)
        n = 0
        for _day, kinds in blob.items():
            for _k, recs in kinds.items():
                for rec in recs or []:
                    r = _norm(rec, src, seen, diag)
                    if r:
                        rows.append(r)
                        n += 1
        diag["sources"][src] = n

    # Harvest-level truncation flags, carried through so a floor is never
    # read as a count.
    for p in sorted(glob.glob(os.path.join(
            ROOT, "data/l5/raw/post_august/HARVEST_SUMMARY_*.json"))):
        with open(p) as fh:
            s = json.load(fh)
        if s.get("truncated_windows"):
            diag["truncation_flags"].extend(s["truncated_windows"])

    rows.sort(key=lambda r: (r["t"], r["slug"] or "",
                             r["oidx"] if r["oidx"] is not None else -1))
    diag["dup_by_pair"] = dict(diag["dup_by_pair"])
    diag["type_counts"] = dict(diag["type_counts"])
    diag["kept_by_src"] = dict(diag["kept_by_src"])
    diag["envelopes"] = dict(diag["envelopes"])
    diag["envelope_statuses"] = dict(diag["envelope_statuses"])
    diag["collision_examples"] = diag["collision_examples"][:5]
    return rows, diag


# ------------------------------------------------------------ period slicing

def regime_of(day):
    """Canonical regime label for a UTC day string."""
    lo = None
    for cp in REGIME_CPS:
        if day >= cp:
            lo = cp
    return ("REGIME_%s_ON" % lo) if lo else "REGIME_PRE_%s" % REGIME_CPS[0]


def month_of(day):
    return day[:7]


def _f(vals):
    vals = [v for v in vals if v is not None]
    return vals


def med(vals):
    vals = _f(vals)
    return st.median(vals) if vals else None


def frac(vals):
    vals = _f(vals)
    return (sum(vals) / len(vals)) if vals else None


# ------------------------------------------------------------ economics

def all_taker_fee_bound(rows):
    """UPPER BOUND on taker cost: price EVERY buy as if L5 were the taker.

    This is a COUNTERFACTUAL BOUND, not a measurement. The tape carries no
    aggressor flag (see module docstring), so the true split is unknown and
    the real cost lies somewhere in [0, this bound]. The maker-rebate column
    beside it is the opposite-corner bound (every fill a maker credit). Both
    are labelled as bounds and neither is netted into the other.
    """
    out = {"taker_bound_usd": 0.0, "maker_credit_bound_usd": 0.0,
           "fills_priced": 0, "fills_date_ambiguous": 0,
           "fills_unpriceable": 0}
    for r in rows:
        if r["type"] not in TRADE_TYPES or r["side"] != "BUY":
            continue
        if not (0.0 < r["price"] < 1.0) or r["size"] <= 0:
            out["fills_unpriceable"] += 1
            continue
        when = dt.datetime.fromtimestamp(r["t"], dt.timezone.utc)
        try:
            out["taker_bound_usd"] += float(
                taker_fee(D(str(r["price"])), D(str(r["size"])), when=when))
            out["maker_credit_bound_usd"] += float(
                maker_rebate(D(str(r["price"])), D(str(r["size"])), when=when))
            out["fills_priced"] += 1
        except Exception:
            out["fills_unpriceable"] += 1
        # The 09-17 -> 10-01 window where primary and secondary sources
        # disagree on the effective date. Rated at the HIGHER coefficient
        # (the schedule's own conservative choice) and FLAGGED, not quietly
        # rated.
        if date_conflict_window(when):
            out["fills_date_ambiguous"] += 1
    return out


def economics(feats, rows, led_days):
    """Decompose, keeping the seven CLAUDE.md components SEPARATE."""
    matched_notional = 0.0
    matched_locked = 0.0          # matched pair economics, ex-fee, ex-incentive
    resid_shares = 0.0
    gross_shares = 0.0
    pair_costs = []
    for f in feats:
        gross_shares += f["matched"] * 2 + f["term_resid"]
        resid_shares += f["term_resid"]
        if f["pair_cost"] is not None and f["matched"] > 0:
            pair_costs.append(f["pair_cost"])
            matched_notional += f["matched"] * f["pair_cost"]
            matched_locked += f["matched"] * (1.0 - f["pair_cost"])

    cash = defaultdict(float)
    cash_n = Counter()
    for d, v in led_days.items():
        for k, u in v["usdc"].items():
            cash[k] += u
            cash_n[k] += v["n"].get(k, 0)

    fee = all_taker_fee_bound(rows)
    days = sorted({f["day"] for f in feats})
    nd = max(1, len(days))
    return {
        "days": nd, "day_first": days[0] if days else None,
        "day_last": days[-1] if days else None,
        "events": len(feats),
        "fills": sum(1 for r in rows if r["type"] in TRADE_TYPES),
        "notional_usd": sum(f["notional"] for f in feats),
        # (1) matched-pair economics
        "matched_shares": sum(f["matched"] for f in feats),
        "matched_notional_usd": matched_notional,
        "matched_locked_ex_fee_ex_incentive_usd": matched_locked,
        "pair_cost_median": med(pair_costs),
        "pair_cost_p10": (st.quantiles(pair_costs, n=10)[0]
                          if len(pair_costs) > 10 else None),
        "pair_cost_p90": (st.quantiles(pair_costs, n=10)[8]
                          if len(pair_costs) > 10 else None),
        "pairs_measured": len(pair_costs),
        "pairs_le_0_96": sum(1 for p in pair_costs if p <= PAIR_TARGET),
        "pairs_le_0_96_frac": (sum(1 for p in pair_costs if p <= PAIR_TARGET)
                               / len(pair_costs)) if pair_costs else None,
        "pairs_le_1_00_frac": (sum(1 for p in pair_costs if p <= 1.0)
                               / len(pair_costs)) if pair_costs else None,
        # (2) directional residual
        "matched_share_of_gross": (1.0 - resid_shares / gross_shares)
                                   if gross_shares > 0 else None,
        "residual_share_of_gross": (resid_shares / gross_shares)
                                    if gross_shares > 0 else None,
        "term_resid_frac_median": med([f["term_resid_frac"] for f in feats]),
        "peak_resid_median": med([f["peak_resid"] for f in feats]),
        "events_with_terminal_residual_frac":
            frac([1 if f["term_resid"] > 1e-9 else 0 for f in feats]),
        # (3) spread capture
        "spread_capture_usd": None,   # UNAVAILABLE -- no contemporaneous book
        # (4)(5) incentives -- L5's OWN, never ours, never netted
        "l5_maker_rebate_usd": cash.get("MAKER_REBATE", 0.0),
        "l5_taker_rebate_usd": cash.get("TAKER_REBATE", 0.0),
        "l5_reward_usd": cash.get("REWARD", 0.0),
        "l5_maker_rebate_n": cash_n.get("MAKER_REBATE", 0),
        "l5_taker_rebate_n": cash_n.get("TAKER_REBATE", 0),
        "l5_reward_n": cash_n.get("REWARD", 0),
        # (6) fees -- BOUNDS only, no aggressor flag on this tape
        "taker_fee_upper_bound_usd": fee["taker_bound_usd"],
        "maker_rebate_upper_bound_usd": fee["maker_credit_bound_usd"],
        "fills_priced": fee["fills_priced"],
        "fills_date_ambiguous": fee["fills_date_ambiguous"],
        "fills_unpriceable": fee["fills_unpriceable"],
        # (7) settlement cashflow
        "l5_redeem_usd": cash.get("REDEEM", 0.0),
        "l5_redeem_n": cash_n.get("REDEEM", 0),
        # sequence composition
        "act_mix": None,
        "extend_per_event": frac([f["extend"] for f in feats]),
        "switch_per_event": frac([f["switch"] for f in feats]),
        "complement_per_event": frac([f["complement"] for f in feats]),
        "completion_rate": frac([f["completed"] for f in feats]),
        "t_to_match_s_median": med([f["t_to_match"] for f in feats]),
        "first_price_median": med([f["first_price"] for f in feats]),
        "first_favored_frac": frac([f["first_favored"] for f in feats]),
        "first_tte_s_median": med([f["first_tte"] for f in feats]),
        "mean_size_median": med([f["mean_size"] for f in feats]),
        "notional_per_event": (sum(f["notional"] for f in feats) / len(feats))
                              if feats else None,
    }


def act_mix(feats):
    c = Counter()
    for f in feats:
        for s in f["seq"]:
            c[s["act"]] += 1
    tot = max(1, sum(c.values()))
    return {k: {"n": v, "frac": v / tot} for k, v in c.most_common()}


def category_mix(feats):
    c = Counter()
    for f in feats:
        slug = f["slug"] or ""
        if f["asset"]:
            c["crypto_updown:%s/%s" % (f["asset"],
               ("%dm" % (f["dur"] // 60)) if f["dur"] else "?")] += 1
        else:
            head = re.split(r"[-_]", slug)[0] if slug else "UNRESOLVED"
            c["other:%s" % head] += 1
    tot = max(1, sum(c.values()))
    return {k: {"events": v, "frac": v / tot} for k, v in c.most_common(25)}


# ------------------------------------------------------------ main

def main():
    assert_frozen()
    if FINGERPRINT != EXPECT_FP:
        raise SystemExit("FEE SCHEDULE CHANGED -- refusing: %s" % FINGERPRINT)

    rows, diag = load_post_august()
    if not rows:
        raise SystemExit(
            "ZERO post-August rows loaded. That is an INGEST/FETCH failure, "
            "NOT evidence that L5 stopped trading. Refusing to emit a report.")

    post = [r for r in rows if day_of(r["t"]) > CORPUS_ENDS]
    days_present = sorted({day_of(r["t"]) for r in post})
    led = cash_ledger(post)
    events = build_events(post)
    feats = [event_features(e) for e in events]

    # calendar coverage: which post-August days are actually represented
    d0 = dt.datetime.strptime(days_present[0], "%Y-%m-%d")
    d1 = dt.datetime.strptime(days_present[-1], "%Y-%m-%d")
    span, missing = [], []
    d = d0
    while d <= d1:
        s = d.strftime("%Y-%m-%d")
        span.append(s)
        if s not in set(days_present):
            missing.append(s)
        d += dt.timedelta(days=1)

    byday_events = Counter(f["day"] for f in feats)
    byday_fills = Counter(day_of(r["t"]) for r in post if r["type"] in TRADE_TYPES)

    periods = {}
    for label, sel in (
            [("MONTH_" + m, lambda f, m=m: month_of(f["day"]) == m)
             for m in sorted({month_of(f["day"]) for f in feats})]
            + [(r, lambda f, r=r: regime_of(f["day"]) == r)
               for r in sorted({regime_of(f["day"]) for f in feats})]):
        pf = [f for f in feats if sel(f)]
        if not pf:
            continue
        pdays = {f["day"] for f in pf}
        prows = [r for r in post if day_of(r["t"]) in pdays]
        pled = {k: v for k, v in led.items() if k in pdays}
        e = economics(pf, prows, pled)
        e["act_mix"] = act_mix(pf)
        e["category_mix"] = category_mix(pf)
        e["days_represented"] = sorted(pdays)
        periods[label] = e

    # Per-day incentive series. This is the decisive question's evidence: the
    # August liquidity-reward program's payout clock, and whether MAKER_REBATE
    # / TAKER_REBATE kept their own separate clocks after REWARD stopped.
    # L5's cashflows, never ours.
    incentive_days = {}
    for d in sorted(led):
        v = led[d]
        incentive_days[d] = {
            "REWARD": round(v["usdc"].get("REWARD", 0.0), 4),
            "MAKER_REBATE": round(v["usdc"].get("MAKER_REBATE", 0.0), 4),
            "TAKER_REBATE": round(v["usdc"].get("TAKER_REBATE", 0.0), 4),
            "REDEEM": round(v["usdc"].get("REDEEM", 0.0), 2),
            "n": dict(v["n"]),
            "observed": d in set(days_present),
        }
    reward_days = [d for d, v in incentive_days.items() if v["REWARD"] > 0]
    maker_days = [d for d, v in incentive_days.items() if v["MAKER_REBATE"] > 0]
    taker_days = [d for d, v in incentive_days.items() if v["TAKER_REBATE"] > 0]

    out = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "subject": {"handle": L5_NAME, "proxy_wallet": L5_ADDR},
        "profit_attribution": "NONE. Another trader's public activity. No "
            "figure here is submitted, filled, settled or realized profit "
            "for our account.",
        "fee_schedule_fingerprint": FINGERPRINT,
        "canonical_corpus_ends": CORPUS_ENDS,
        "regime_change_points": REGIME_CPS,
        "ingest": diag,
        "coverage": {
            "post_august_days_present": days_present,
            "post_august_day_count": len(days_present),
            "calendar_span": [span[0], span[-1]],
            "calendar_days_in_span": len(span),
            "days_missing_in_span": missing,
            "events_per_day": dict(sorted(byday_events.items())),
            "fills_per_day": dict(sorted(byday_fills.items())),
        },
        "totals": {
            "rows_loaded_all": len(rows),
            "rows_post_august": len(post),
            "fills_post_august": sum(1 for r in post if r["type"] in TRADE_TYPES),
            "events_post_august": len(feats),
        },
        "periods": periods,
        "incentive_days": incentive_days,
        "incentive_streams": {
            "REWARD": {"paying_days": reward_days,
                       "last_paying_day": reward_days[-1] if reward_days else None,
                       "total_usd": sum(incentive_days[d]["REWARD"]
                                        for d in reward_days)},
            "MAKER_REBATE": {"paying_days": maker_days,
                             "last_paying_day": maker_days[-1] if maker_days else None,
                             "total_usd": sum(incentive_days[d]["MAKER_REBATE"]
                                              for d in maker_days)},
            "TAKER_REBATE": {"paying_days": taker_days,
                             "last_paying_day": taker_days[-1] if taker_days else None,
                             "total_usd": sum(incentive_days[d]["TAKER_REBATE"]
                                              for d in taker_days)},
            "note": "L5's OWN credited incentive cashflows, observed in the "
                    "public feed. Never netted into trading P&L, never "
                    "annualized for us, and NOT evidence that any equivalent "
                    "program applies to our account. A stream shows zero on a "
                    "day only if that day was OBSERVED -- check `observed`.",
        },
        "unavailable": {
            "maker_taker_role": "The tape carries NO execution.isAggressor "
                "field. The liquidity-role field present is "
                "CHAIN_TOPIC_POSITION (on-chain CTF topic position). No "
                "maker/taker role is claimed. Fee figures are BOUNDS.",
            "spread_capture": "Requires the contemporaneous book, which this "
                "feed does not carry. UNAVAILABLE -- not modelled.",
            "order_placements_and_cancels": "Not public. Patience-in-book "
                "UNAVAILABLE.",
            "l5_pnl": "No cost basis beyond these fills; settlement credit "
                "for events outside the fetch span UNAVAILABLE.",
        },
    }
    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, "post_august.json"), "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    write_md(out)
    print(json.dumps({"days": out["coverage"]["post_august_day_count"],
                      "missing": missing,
                      "totals": out["totals"],
                      "dupes": diag["duplicates_dropped"],
                      "collisions": diag["collisions_differing_fields"],
                      "periods": sorted(periods)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
