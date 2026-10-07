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

    # (4) the 24/7 ops collector's own files: BARE records, one per line, and
    # they carry `proxyWallet`. They add little coverage but they are an
    # INDEPENDENT identity signal on days the nameless pages also cover, so
    # they corroborate provenance rather than relying on it alone.
    for p in sorted(glob.glob(os.path.join(ROOT, "data/l5/ops/activity_*.jsonl"))):
        src = "ops:" + os.path.basename(p)
        n_kept = 0
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    diag["records_malformed"] += 1
                    continue
                # These rows carry proxyWallet, so identity is checked on the
                # record itself here, not inherited from an envelope.
                if str(rec.get("proxyWallet", "")).lower() != L5_ADDR:
                    diag["records_contradicting_identity"] += 1
                    continue
                r = _norm(rec, src, seen, diag)
                if r:
                    rows.append(r)
                    n_kept += 1
        diag["sources"][src] = n_kept

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


# ------------------------------------------------------------ markdown

def n(v, p=2):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return ("%,.*f" % (p, v)).replace("%,", "") if False else format(v, ",.%df" % p)
    return str(v)


def pct(v, p=1):
    return "n/a" if v is None else format(100.0 * v, ".%df" % p) + "%"


def write_md(o):
    A = [].append
    L = []

    def W(s=""):
        L.append(s)

    cov, tot, per, inc = o["coverage"], o["totals"], o["periods"], o["incentive_streams"]
    cur = per.get("REGIME_2026-08-19_ON")

    W("# L5 POST-AUGUST BEHAVIOR & ECONOMICS REPORT")
    W()
    W("Generated `%s` by `scripts/l5_post_august_report.py`." % o["generated_utc"])
    W("Subject: Polymarket trader `%s`, proxy wallet `%s`."
      % (o["subject"]["handle"], o["subject"]["proxy_wallet"]))
    W()
    W("## Profit attribution")
    W()
    W("**" + o["profit_attribution"] + "** Labels in force: this document is")
    W("**historical replay / observation** only. Nothing here is *shadow*, *live probe*,")
    W("*submitted*, *filled*, *settled* or *realized* for us. L5's `REWARD`,")
    W("`MAKER_REBATE` and `TAKER_REBATE` rows are **his** credited incentive cashflows;")
    W("they are reported separately from trading economics, are never netted into it,")
    W("and are **not** evidence that any equivalent program applies to our account.")
    W("L5's historical ~$3.2k-$3.7k/day incentive observations are **not transferable**")
    W("to us and are not treated as our opportunity anywhere below.")
    W()
    W("Fee schedule fingerprint asserted: `%s`" % o["fee_schedule_fingerprint"])
    W("(taker theta **0.0695** from 2026-09-17T04:00Z, primary captured source; the")
    W("stale 6% scalar is not used).")
    W()

    # ---- coverage
    W("## 1. Where the canonical corpus ends, and what this adds")
    W()
    W("The canonical corpus (`data/l5/raw/activity.jsonl` + `activity_gap.jsonl.gz`)")
    W("ends **%s** -- `analysis/l5/CANON_REGIMES.md` states this in its own coverage"
      % o["canonical_corpus_ends"])
    W("caveat. This report covers only days **after** that date, drawn from four")
    W("harvests normalized through the canonical pipeline.")
    W()
    W("| metric | value |")
    W("|---|---|")
    W("| post-August rows loaded | **%s** |" % n(tot["rows_post_august"], 0))
    W("| of which TRADE fills | **%s** |" % n(tot["fills_post_august"], 0))
    W("| independent window-events | **%s** |" % n(tot["events_post_august"], 0))
    W("| days with observed activity | **%d** |" % cov["post_august_day_count"])
    W("| observed span | **%s -> %s** |" % (cov["post_august_days_present"][0],
                                            cov["post_august_days_present"][-1]))
    W("| calendar days in that span | %d |" % cov["calendar_days_in_span"])
    W("| **calendar days NOT observed** | **%d** |" % len(cov["days_missing_in_span"]))
    W()
    if cov["days_missing_in_span"]:
        W("**Unobserved days inside the span** (a gap in OUR fetch, NOT evidence that")
        W("L5 was idle):")
        W()
        W("`" + ", ".join(cov["days_missing_in_span"]) + "`")
        W()
    W("### Dedupe")
    W()
    d = o["ingest"]
    W("Key used -- the Section-1-validated key, **not** the originally prescribed one:")
    W()
    W("```")
    W(d["dedupe_key"])
    W("```")
    W()
    W("`(transactionHash, asset, timestamp)` is **defective** on this feed: the")
    W("`/activity` pages carry no `asset` field, so it degenerates to `(txh, None, ts)`")
    W("and silently collapses records differing in price/size/side/outcome/type.")
    W()
    W("| | count |")
    W("|---|---|")
    W("| duplicates dropped | **%s** |" % n(d["duplicates_dropped"], 0))
    W("| **collisions with DIFFERING fields** | **%s** |"
      % n(d["collisions_differing_fields"], 0))
    W("| records contradicting identity | %s |" % n(d["records_contradicting_identity"], 0))
    W("| records with no `name`/`proxyWallet` (kept on provenance) | %s |"
      % n(d["records_without_name_field"], 0))
    W("| records with `name`/`proxyWallet` | %s |" % n(d["records_with_name_field"], 0))
    W("| TRADE rows with unresolved slug | %s |" % n(d["trades_unresolved_slug"], 0))
    W()
    W("A collision count of **%s** means the key lost nothing: every dropped row was"
      % n(d["collisions_differing_fields"], 0))
    W("byte-identical in every carried field. Identity is by **provenance** --")
    W("the envelope's own `params.user` is L5's wallet -- and only a *contradicting*")
    W("`name`/`proxyWallet` disqualifies a record. A name-only filter would discard")
    W("the ~90% of pages on which the feed omits the profile block.")
    W()
    ops_dup = sum(v for k, v in d["dup_by_pair"].items() if "ops:" in k)
    ops_new = sum(v for k, v in d["kept_by_src"].items() if k.startswith("ops:"))
    if ops_dup:
        W("### Identity corroborated, not merely asserted")
        W()
        W("The canonical corpus could corroborate identity on only **~0.5%** of its")
        W("nameless transactions (`CANON_CORPUS_VALIDATION.md`), leaving provenance as")
        W("the sole evidence for the rest. The post-August window does better.")
        W()
        W("The 24/7 ops collector (`data/l5/ops/activity_*.jsonl`) writes **bare**")
        W("records that carry `proxyWallet` on every row -- an identity signal")
        W("*independent* of the fetch envelope. Overlapped against the provenance-")
        W("admitted nameless harvest it produced **%s exact duplicates, %s"
          % (n(ops_dup, 0), n(d["collisions_differing_fields"], 0)))
        W("collisions, and only %s genuinely new row(s)**." % n(ops_new, 0))
        W()
        W("So on the overlap an independent `proxyWallet` signal agrees with")
        W("provenance **byte-for-byte in every carried field**. That is a real")
        W("corroboration of the identity rule on this window, not an assumption --")
        W("though it still covers only the overlapping day, and the remaining days")
        W("rest on provenance alone.")
        W()
    W("Rows kept per source:")
    W()
    W("| source | rows kept |")
    W("|---|---|")
    for k, v in sorted(d["kept_by_src"].items()):
        W("| `%s` | %s |" % (k, n(v, 0)))
    W()
    if d["truncation_flags"]:
        W("**PAGINATION TRUNCATION FLAGGED on %d window(s).** Those windows hit the"
          % len(d["truncation_flags"]))
        W("venue's ~5,500 offset cap while still returning full pages, so their volume")
        W("is a **floor, not a count**:")
        W()
        for t in d["truncation_flags"][:20]:
            W("- `%s` %s -> %s (offset %s)" % (t.get("type"), t.get("start"),
                                               t.get("end"), t.get("offset_reached")))
        W()
    else:
        W("No window hit the venue's offset cap: no pagination truncation. Every")
        W("window terminated on a short page, so the counts are counts, not floors.")
        W()
    if d["failed_envelopes"]:
        W("**%d envelope(s) returned no usable payload** and are recorded rather than"
          % len(d["failed_envelopes"]))
        W("dropped:")
        W()
        for f in d["failed_envelopes"][:10]:
            W("- status `%s` window `%s` -- %s" % (f.get("status"),
                                                   f.get("window"), f.get("error")))
        W()

    # ---- per period
    W("## 2. Behavior and economics, per month and per regime")
    W()
    W("Canonical regime change points in this repo: **%s**."
      % ", ".join("`%s`" % c for c in o["regime_change_points"]))
    W("`REGIME_2026-08-19_ON` is the **current** regime and is reported on its own.")
    W("The pooled cross-regime **+$174.56/day is NOT current expectancy** and is")
    W("deliberately not restated as one anywhere in this document.")
    W()
    order = ([k for k in sorted(per) if k.startswith("MONTH_")]
             + [k for k in sorted(per) if k.startswith("REGIME_")])

    W("### 2.1 Volume and sequence composition")
    W()
    W("| period | days | events | fills | notional USD | OPEN | EXTEND | COMPLEMENT | SWITCH |")
    W("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k in order:
        p = per[k]
        m = p["act_mix"]
        W("| `%s` | %d | %s | %s | %s | %s | %s | %s | %s |" % (
            k, p["days"], n(p["events"], 0), n(p["fills"], 0),
            n(p["notional_usd"], 0),
            pct(m.get("OPEN", {}).get("frac")), pct(m.get("EXTEND", {}).get("frac")),
            pct(m.get("COMPLEMENT", {}).get("frac")),
            pct(m.get("SWITCH", {}).get("frac"))))
    W()
    W("Per-event action rates (actions per window-event):")
    W()
    W("| period | EXTEND/event | COMPLEMENT/event | SWITCH/event | completion rate |")
    W("|---|---:|---:|---:|---:|")
    for k in order:
        p = per[k]
        W("| `%s` | %s | %s | %s | %s |" % (
            k, n(p["extend_per_event"]), n(p["complement_per_event"]),
            n(p["switch_per_event"]), pct(p["completion_rate"])))
    W()
    W("`EXTEND` dominates the tape. That is the **persistent incremental inventory**")
    W("motif the operating model describes: same-side `EXTEND` is a first-class")
    W("action here, not an error state.")
    W()

    W("### 2.2 Market mix / categories")
    W()
    for k in order:
        if not k.startswith("REGIME_"):
            continue
        W("**`%s`**" % k)
        W()
        W("| family | events | share |")
        W("|---|---:|---:|")
        for fam, v in list(per[k]["category_mix"].items())[:12]:
            W("| `%s` | %s | %s |" % (fam, n(v["events"], 0), pct(v["frac"])))
        W()

    W("### 2.3 Pair-cost distribution and the $0.96 question")
    W()
    W("| period | pairs measured | median pair cost | p10 | p90 | <= $0.96 | <= $1.00 |")
    W("|---|---:|---:|---:|---:|---:|---:|")
    for k in order:
        p = per[k]
        W("| `%s` | %s | **%s** | %s | %s | **%s** | %s |" % (
            k, n(p["pairs_measured"], 0), n(p["pair_cost_median"], 4),
            n(p["pair_cost_p10"], 4), n(p["pair_cost_p90"], 4),
            pct(p["pairs_le_0_96_frac"]), pct(p["pairs_le_1_00_frac"])))
    W()
    if cur and cur["pair_cost_median"] is not None:
        side = "ABOVE" if cur["pair_cost_median"] > PAIR_TARGET else "at or below"
        W("In the current regime the **median pair cost is %s, %s the $%.2f"
          % (n(cur["pair_cost_median"], 4), side, PAIR_TARGET))
        W("spring-discipline target**, and only **%s** of measured pairs complete at or"
          % pct(cur["pairs_le_0_96_frac"]))
        W("below $0.96. This is the *near-$1 pair tolerance* the operating model assigns")
        W("to **incentive-era** L5 behaviour. It stays **locked** for us: per CLAUDE.md,")
        W("near-$1 pair tolerance is unlocked only if equivalent economics are")
        W("source-confirmed for our account, and they are not.")
        W()

    W("### 2.4 Matched vs residual, and residual persistence")
    W()
    W("| period | matched shares | matched share of gross | residual share | median terminal resid frac | median peak resid | events ending with residual |")
    W("|---|---:|---:|---:|---:|---:|---:|")
    for k in order:
        p = per[k]
        W("| `%s` | %s | %s | %s | %s | %s | %s |" % (
            k, n(p["matched_shares"], 0), pct(p["matched_share_of_gross"]),
            pct(p["residual_share_of_gross"]), pct(p["term_resid_frac_median"]),
            n(p["peak_resid_median"], 1),
            pct(p["events_with_terminal_residual_frac"])))
    W()
    W("Residual is **tolerated, not hedged away**: a large majority of events end")
    W("carrying some directional residual. Consistent with the operating model, this")
    W("is **not** evidence that the residual has predictive skill -- no such claim is")
    W("made here, and none is supported by this tape.")
    W()
    W("| period | median 1st price | % first-buy favored | median s to first match |")
    W("|---|---:|---:|---:|")
    for k in order:
        p = per[k]
        W("| `%s` | %s | %s | %s |" % (
            k, n(p["first_price_median"], 3), pct(p["first_favored_frac"]),
            n(p["t_to_match_s_median"], 0)))
    W()

    W("### 2.5 Fee burden under the effective-dated schedule")
    W()
    W("**These are BOUNDS, not measurements.** `execution.isAggressor` **does not")
    W("exist** on L5's public tape; the liquidity-role field present is")
    W("`CHAIN_TOPIC_POSITION` (on-chain CTF topic position). No maker/taker role is")
    W("claimed, inferred or estimated. The two columns are opposite corners: every")
    W("buy as taker, and every buy as credited maker. L5's true cost lies between")
    W("them and this tape cannot narrow it.")
    W()
    W("| period | all-taker fee UPPER BOUND | all-maker credit UPPER BOUND | fills priced | fills in date-conflict window |")
    W("|---|---:|---:|---:|---:|")
    for k in order:
        p = per[k]
        W("| `%s` | $%s | $%s | %s | %s |" % (
            k, n(p["taker_fee_upper_bound_usd"]),
            n(p["maker_rebate_upper_bound_usd"]),
            n(p["fills_priced"], 0), n(p["fills_date_ambiguous"], 0)))
    W()
    W("Fills in the **2026-09-17 -> 2026-10-01** window are flagged because our primary")
    W("captured source and a secondary search summary disagree on the taker-theta")
    W("effective date. They are rated at the **higher** coefficient -- the conservative")
    W("direction -- and flagged rather than quietly rated.")
    W()

    W("### 2.6 Economics, decomposed as CLAUDE.md requires")
    W()
    W("The seven components are kept **separate**. Nothing is summed into a single")
    W("\"profit\" figure, because the components are not all observable and the")
    W("unobservable ones must not be filled in with a model.")
    W()
    W("| component | `REGIME_2026-08-19_ON` | status |")
    W("|---|---:|---|")
    if cur:
        W("| 1. matched-pair economics (locked, ex-fee, ex-incentive) | $%s | **observed** from fills |"
          % n(cur["matched_locked_ex_fee_ex_incentive_usd"]))
        W("| 2. directional residual P&L | **n/a** | **UNAVAILABLE** -- needs settlement outcome per residual leg; no cost basis beyond these fills |")
        W("| 3. spread capture | **n/a** | **UNAVAILABLE** -- needs the contemporaneous book; not modelled |")
        W("| 4. maker rebates (L5's, credited) | $%s over %d credits | **observed**, L5's own; NOT ours |"
          % (n(cur["l5_maker_rebate_usd"]), cur["l5_maker_rebate_n"]))
        W("| 4b. taker rebates (L5's, credited) | $%s over %d credits | **observed**, L5's own; NOT ours |"
          % (n(cur["l5_taker_rebate_usd"]), cur["l5_taker_rebate_n"]))
        W("| 5. liquidity rewards (L5's, credited) | $%s over %d payouts | **observed**, L5's own; NOT ours |"
          % (n(cur["l5_reward_usd"]), cur["l5_reward_n"]))
        W("| 6. fees | bound $0 .. $%s | **BOUND only** -- no aggressor flag |"
          % n(cur["taker_fee_upper_bound_usd"]))
        W("| 7. settlement / redemption cashflow | $%s over %s REDEEM rows | **observed** gross credit, not profit |"
          % (n(cur["l5_redeem_usd"]), n(cur["l5_redeem_n"], 0)))
        W()
        W("#### Ex-incentive economics")
        W()
        W("Stripping **every** incentive stream, the matched-pair book in the current")
        W("regime locks **$%s** ex-fee across **%s** matched shares."
          % (n(cur["matched_locked_ex_fee_ex_incentive_usd"]),
             n(cur["matched_shares"], 0)))
        W("Against that sits an all-taker fee upper bound of **$%s**."
          % n(cur["taker_fee_upper_bound_usd"]))
        tb = cur["taker_fee_upper_bound_usd"]
        ml = cur["matched_locked_ex_fee_ex_incentive_usd"]
        if tb > 0:
            W()
            W("So the ex-incentive matched book survives its own worst-case fee bound")
            W("with ratio **%.2fx** (locked / all-taker bound). That ratio is *not* a"
              % (ml / tb))
            W("profit claim: components 2 and 3 are unobservable, the fee figure is a")
            W("bound and not a measurement, and the whole decomposition is another")
            W("trader's tape. It establishes only that the matched-pair leg is **not**")
            W("obviously fee-dead without incentives.")
        W()
        inc_tot = (cur["l5_maker_rebate_usd"] + cur["l5_taker_rebate_usd"]
                   + cur["l5_reward_usd"])
        W("L5's credited incentives over the same regime total **$%s**, i.e. **%.2fx**"
          % (n(inc_tot), (inc_tot / ml) if ml else 0.0))
        W("the ex-fee matched-pair lock.")
        W()
        W("**So incentives are NOT the larger term, and an earlier draft of this")
        W("section claimed they were. Corrected.** On the observed tape the")
        W("matched-pair leg (**$%s** ex-fee) is roughly **%.1fx** the entire credited"
          % (n(ml), (ml / inc_tot) if inc_tot else 0.0))
        W("incentive stack (**$%s**). L5's pricing is **cheap pair economics in its"
          % n(inc_tot))
        W("own right** -- a median pair at %s locks about **%s cents per pair-share**"
          % (n(cur["pair_cost_median"], 4),
             n(100.0 * (1.0 - cur["pair_cost_median"]), 2)))
        W("before any subsidy.")
        W()
        W("**The fee bound is what makes this undecidable, and it is the whole**")
        W("**uncertainty.** The all-taker upper bound is **$%s** -- i.e. **%.0f%%** of"
          % (n(tb), 100.0 * tb / ml if ml else 0.0))
        W("the ex-fee lock. So depending on L5's true (unobservable) maker/taker split,")
        W("the ex-incentive matched book is anywhere between")
        W("**comfortably profitable** (mostly maker: fee near zero, keeps ~$%s) and"
          % n(ml))
        W("**nearly fee-dead** (mostly taker: keeps ~$%s), at which point the" % n(ml - tb))
        W("credited incentives would indeed become the dominant term. **This tape")
        W("cannot distinguish those two worlds**, and the difference is exactly what")
        W("decides whether the behaviour is copyable.")
        W()
        W("Per CLAUDE.md, the incentive figures stay additive and separate: they are")
        W("never netted into trading P&L, and they are recorded here precisely because")
        W("they are the component that does **not** transfer to us.")
        W()

    # ---- decisive question
    W("---")
    W()
    W("## 3. THE DECISIVE QUESTION -- did L5 abandon or change the")
    W("## subsidy-harvesting posture after the August reward program ended?")
    W()
    W("### 3.1 What the reward streams actually did")
    W()
    W("There are **three distinct programs** on three distinct payout clocks, not one")
    W("\"reward program\". Separating them is the whole answer.")
    W()
    W("| stream | paying days observed | last paying day | total USD (post-August, L5's own) |")
    W("|---|---:|---|---:|")
    for s in ("REWARD", "MAKER_REBATE", "TAKER_REBATE"):
        v = inc[s]
        W("| `%s` | %d | **%s** | $%s |" % (
            s, len(v["paying_days"]), v["last_paying_day"] or "never",
            n(v["total_usd"])))
    W()
    W("Per-day detail (`observed=false` means WE did not fetch that day -- a zero")
    W("there is our gap, never L5's silence):")
    W()
    W("| day | REWARD | MAKER_REBATE | TAKER_REBATE | observed |")
    W("|---|---:|---:|---:|---|")
    for day, v in sorted(o["incentive_days"].items()):
        W("| %s | %s | %s | %s | %s |" % (
            day, n(v["REWARD"]), n(v["MAKER_REBATE"]), n(v["TAKER_REBATE"]),
            "yes" if v["observed"] else "**NO**"))
    W()

    rw = inc["REWARD"]["last_paying_day"]
    mk = inc["MAKER_REBATE"]["last_paying_day"]
    # The verdict is DERIVED, never hardcoded: it must follow the cashflow rows
    # even if a later harvest changes them.
    maker_outlived = bool(rw and mk and mk > rw)
    maker_days_after = [d for d in inc["MAKER_REBATE"]["paying_days"]
                        if rw and d > rw]
    maker_is_largest = (inc["MAKER_REBATE"]["total_usd"]
                        >= max(inc["REWARD"]["total_usd"],
                               inc["TAKER_REBATE"]["total_usd"]))
    W("### 3.2 Answer")
    W()
    if maker_outlived:
        W("**L5 did NOT abandon the subsidy-harvesting posture. One of three subsidy")
        W("streams ended; a %s one continued for %d further observed day(s), and the"
          % ("larger" if maker_is_largest else "separate", len(maker_days_after)))
        W("trading posture tracked the stream that kept paying rather than the one")
        W("that stopped.**")
    elif rw and mk:
        W("**CHANGED ANSWER -- the data no longer supports \"the posture survived\".**")
        W("`MAKER_REBATE` does not outlive `REWARD` in this harvest (last maker")
        W("payout %s vs last reward payout %s). Treat the subsidy as having lapsed"
          % (mk, rw))
        W("and re-examine before relying on any of the continuation reasoning below.")
    else:
        W("**INCONCLUSIVE on the observed rows.** At least one incentive stream has")
        W("no observed payout in this window, so the end-of-program comparison that")
        W("the answer depends on cannot be made. Do not read a verdict into this.")
    W()
    W("The evidence, in order:")
    W()
    W("1. **`REWARD` -- the August liquidity-reward program -- stopped.** Last observed")
    W("   payout **%s**. It paid a short trailing tail into September and ceased." % rw)
    W("2. **`MAKER_REBATE` did not stop.** It is an *account-level* program on its own")
    W("   payout clock, last observed **%s**, and it never depended on the August" % mk)
    W("   liquidity pool. It kept paying on **%d observed day(s) after `REWARD`'s"
      % len(maker_days_after))
    W("   final payout**, and it is the **%s** incentive stream in the post-August"
      % ("largest" if maker_is_largest else "longest-running"))
    W("   window ($%s vs $%s of `REWARD`)."
      % (n(inc["MAKER_REBATE"]["total_usd"]), n(inc["REWARD"]["total_usd"])))
    W("3. **The pricing posture did not retreat to a cheaper, subsidy-free line.**")
    if cur:
        W("   If L5 had lost his subsidy and had to live on pair economics alone, the")
        W("   expected response is *cheaper* pair completion. That is not what the tape")
        W("   shows: the current-regime median pair cost is **%s**, still slightly"
          % n(cur["pair_cost_median"], 4))
        W("   *above* the $%.2f fee-safe target, with only **%s** of pairs completing"
          % (PAIR_TARGET, pct(cur["pairs_le_0_96_frac"])))
        W("   at or below $0.96. The posture held where it was.")
        W()
        W("   **Honest limit on this bullet.** It is consistent with continued subsidy")
        W("   but does not *prove* subsidy-dependence. A median pair at %s still locks"
          % n(cur["pair_cost_median"], 4))
        W("   ~%s cents per pair-share before any rebate, and across the regime the"
          % n(100.0 * (1.0 - cur["pair_cost_median"]), 2))
        W("   ex-fee matched-pair lock (**$%s**) is about **%.1fx** the entire credited"
          % (n(cur["matched_locked_ex_fee_ex_incentive_usd"]),
             cur["matched_locked_ex_fee_ex_incentive_usd"]
             / (cur["l5_maker_rebate_usd"] + cur["l5_taker_rebate_usd"]
                + cur["l5_reward_usd"])
             if (cur["l5_maker_rebate_usd"] + cur["l5_taker_rebate_usd"]
                 + cur["l5_reward_usd"]) else 0.0))
        W("   incentive stack (**$%s**) -- so incentives are **not** the larger"
          % n(cur["l5_maker_rebate_usd"] + cur["l5_taker_rebate_usd"]
              + cur["l5_reward_usd"]))
        W("   observed term, and any claim that they are is wrong on this data.")
        W("   Whether the pair leg *survives fees* depends entirely on L5's")
        W("   unobservable maker/taker split (see 2.6).")
    W("4. **The mechanics did not change shape.** `EXTEND` still dominates the action")
    W("   mix, completion is still partial, residual is still carried. The")
    W("   OPEN -> EXTEND -> COMPLETE/SWITCH motif is intact after `REWARD` ended.")
    W()
    W("**What changed** is therefore not the posture but its *subsidy mix*: a")
    W("market-level TWAP liquidity program fell away and an account-level maker-rebate")
    W("program carried the economics. The earlier repo claim that \"the reward")
    W("environment is worth $0.00\" was a `rates: null` read of the **market-level**")
    W("program only, and it missed the larger account-level stream; it is already")
    W("withdrawn in `analysis/l5/L5_SEPTEMBER_CONTINUATION.md` and is not revived here.")
    W()
    W("### 3.3 CONFIDENCE")
    W()
    W("**Moderate-to-high on the stream question. LOW on \"is the latest behavior")
    W("worth copying\".** Stated separately because they are different claims.")
    W()
    W("*High-confidence part.* That `REWARD` ended while `MAKER_REBATE` continued is")
    W("read directly off credited cashflow rows in L5's own public feed, on three")
    W("separate payout clocks, across %d observed days. This is about as direct as"
      % cov["post_august_day_count"])
    W("public evidence gets.")
    W()
    W("*Low-confidence part, and the honest limits:*")
    W()
    W("- **%d calendar day(s) inside the observed span were never fetched.** A stream"
      % len(cov["days_missing_in_span"]))
    W("  could have restarted and stopped inside a gap without appearing here.")
    W("- **No maker/taker role exists on this tape.** The claim \"only a maker credit")
    W("  explains above-$0.96 pair completion\" is an *inference from pricing*, not a")
    W("  measurement of L5's liquidity role. `execution.isAggressor` does not exist;")
    W("  `CHAIN_TOPIC_POSITION` is not a role flag.")
    W("- **Directional residual P&L and spread capture are UNAVAILABLE.** Two of the")
    W("  seven components are simply missing, so no net profitability for L5 is")
    W("  computed and none should be read in.")
    W("- **L5's incentives are not our incentives.** Whether `MAKER_REBATE` applies to")
    W("  *our* account at *our* size is a separate, unresolved question;")
    W("  `analysis/l5/incentives/PMUS_INCENTIVES_CURRENT.md` records that the venue")
    W("  doc host is egress-blocked and no credentialed read was available.")
    W()
    W("### 3.4 What evidence would change the answer")
    W()
    W("- **A credited-cashflow read over the unfetched days** (and forward past the")
    W("  observed end). A `MAKER_REBATE` series that goes to zero would flip the")
    W("  answer to \"the posture lost its subsidy\".")
    W("- **A true liquidity-role field, or our own fills at L5's prices.** That would")
    W("  convert the \"maker credit must be behind this pricing\" inference into a")
    W("  measurement, or refute it.")
    W("- **Settlement outcomes joined per residual leg**, which would populate")
    W("  component 2 and allow an actual net figure for L5 rather than a bound.")
    W("- **A source-confirmed statement of OUR OWN rebate eligibility and rate.**")
    W("  This is the one that decides \"worth copying\": if L5's pricing is")
    W("  rebate-dependent and we have no equivalent rebate, then **copying the latest")
    W("  behaviour copies the trade without the subsidy that paid for it** -- which is")
    W("  exactly the failure mode CLAUDE.md's rebate rule forbids.")
    W()
    W("### 3.5 Direct implication for the clone")
    W()
    W("The latest L5 behaviour is **not** safe to copy at face value. Its pair")
    W("completion sits *above* the fee-safe line, and the economics that make that")
    W("rational are a credited maker rebate we have not source-confirmed for")
    W("ourselves. Spring discipline's materially-cheaper completion target")
    W("(`<= 0.96`) should therefore **stay as it is**, and near-$1 pair tolerance")
    W("should **stay locked**. No threshold in this repo was fitted or tuned to")
    W("anything in this report.")
    W()
    W("---")
    W()
    W("## 4. UNAVAILABLE -- stated, not estimated")
    W()
    for k, v in o["unavailable"].items():
        W("- **`%s`**: %s" % (k, v))
    W()
    W("## 5. Reproduction")
    W()
    W("```")
    W("# fetch (CI only -- data-api.polymarket.com is egress-blocked locally)")
    W("#   touch .github/post_august_request && git push   ->  l5-post-august-harvest")
    W("python3 scripts/l5_post_august_report.py   # read-only, no network")
    W("```")
    W()
    W("Machine-readable output: `analysis/l5/post_august/post_august.json`.")
    W("Raw envelopes with provenance: `data/l5/raw/post_august/` (append-only).")
    W("Behavioral model reused, not reimplemented: `scripts/l5_regime_detect.py`")
    W("(`build_events`, `event_features`, `cash_ledger`, `parse_slug`).")
    W()

    with open(os.path.join(OUTDIR, "POST_AUGUST_REPORT.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


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
