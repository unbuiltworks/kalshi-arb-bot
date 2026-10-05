#!/usr/bin/env python3
"""Lane 4 - venue/category structural selector.

RESEARCH ONLY. Read-only over existing on-disk corpora. No network, no venue
writes, no collectors. Scores each available lawful prediction-market
environment on the structural properties L5's observed policy requires.

THE KEY QUESTION per environment:
  Does it permit repeated inventory cycling AND pair-cost compression
  similar to L5?

Every emitted number carries n and a date range. Anything not derivable from
on-disk data is emitted literally as "UNAVAILABLE" - never estimated silently.

Outputs:
  analysis/gemini/updown/VENUE_SCORECARD.json
  analysis/gemini/updown/VENUE_SCORECARD.md
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import gzip
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "analysis", "gemini", "updown")
L5_HANDLE = "l5Zn1bWoM8eTsK"
UNAVAIL = "UNAVAILABLE"


# ----------------------------------------------------------------- helpers
def iso(ts):
    return dt.datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%dT%H:%M:%SZ")


def q(vals, name="v", unit=None):
    """Distribution summary with explicit n. Returns UNAVAILABLE if empty."""
    vals = [float(v) for v in vals if v is not None]
    if not vals:
        return {"n": 0, "status": UNAVAIL}
    vals.sort()
    out = {"n": len(vals), "min": round(vals[0], 6), "median": round(st.median(vals), 6),
           "max": round(vals[-1], 6), "mean": round(st.fmean(vals), 6)}
    if len(vals) >= 10:
        dec = st.quantiles(vals, n=10)
        out["p10"] = round(dec[0], 6)
        out["p90"] = round(dec[8], 6)
    if unit:
        out["unit"] = unit
    return out


def frac(vals, pred):
    vals = list(vals)
    if not vals:
        return {"n": 0, "status": UNAVAIL}
    return {"n": len(vals), "pct": round(100.0 * sum(1 for v in vals if pred(v)) / len(vals), 2)}


def jread(path):
    with open(path) as fh:
        return json.load(fh)


def lines(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                yield json.loads(ln)
            except Exception:
                continue


def p(*a):
    print(*a, file=sys.stderr)


# ------------------------------------------------------- sequence analytics
def sequence_metrics(events):
    """events: {event_key: [ {ts, outcome_idx, price, size}, ... ] }

    Measures the L5 mechanics directly: complement reach, first-inventory ->
    first-complement lag, side-switch rate, realized VWAP pair cost.
    """
    if not events:
        return {"status": UNAVAIL}
    fills, lags, pair, switch, notional = [], [], [], [], []
    complemented = 0
    for seq in events.values():
        seq = sorted(seq, key=lambda x: x["ts"])
        outs = [x["outcome_idx"] for x in seq]
        fills.append(len(seq))
        notional.append(sum(x["price"] * x["size"] for x in seq))
        if len(outs) > 1:
            switch.append(sum(1 for i in range(1, len(outs)) if outs[i] != outs[i - 1]) / (len(outs) - 1))
        if len(set(outs)) > 1:
            complemented += 1
            first = outs[0]
            for x in seq:
                if x["outcome_idx"] != first:
                    lags.append(x["ts"] - seq[0]["ts"])
                    break
            legs = collections.defaultdict(list)
            for x in seq:
                legs[x["outcome_idx"]].append((x["price"], x["size"]))
            vw = {}
            for k, v in legs.items():
                tot = sum(s for _, s in v)
                vw[k] = sum(pr * s for pr, s in v) / tot if tot else None
            ks = sorted(vw)
            if len(ks) >= 2 and vw[ks[0]] and vw[ks[1]]:
                pair.append(vw[ks[0]] + vw[ks[1]])
    return {
        "events": len(events),
        "own_fills_per_event": q(fills),
        "notional_per_event_usd": q(notional, unit="USD"),
        "complement_reach_pct": round(100.0 * complemented / len(events), 2),
        "first_inventory_to_complement_seconds": q(lags, unit="s"),
        "side_switch_rate_per_adjacent_fill": q(switch),
        "realized_vwap_pair_cost_usd": q(pair, unit="USD YES+NO"),
        "pct_pair_cost_below_1_00": frac(pair, lambda x: x < 1.00),
        "pct_pair_cost_at_or_below_0_96": frac(pair, lambda x: x <= 0.96),
    }


# ============================================================ ENVIRONMENTS
def env_l5_polymarket_updown():
    """Reference environment: Polymarket crypto UP/DOWN, as actually traded by L5.

    Source is L5's own fill tape, so sequence metrics are the TRADER's realized
    behaviour inside that environment - which is exactly the target to match.
    Market-wide tape (all participants) for these markets is not on disk, so
    environment-level trades/minute and BBO/depth are UNAVAILABLE.
    """
    path = os.path.join(ROOT, "data", "l5", "raw", "trades.jsonl")
    if not os.path.exists(path):
        return None
    seen, rows = set(), []
    for d in lines(path):
        pl = d.get("payload")
        if not isinstance(pl, list):
            continue
        for x in pl:
            if x.get("name") != L5_HANDLE:
                continue
            k = (x.get("transactionHash"), x.get("asset"), x.get("timestamp"))
            if k in seen:
                continue
            seen.add(k)
            rows.append(x)
    if not rows:
        return None
    rows.sort(key=lambda x: x["timestamp"])
    events = collections.defaultdict(list)
    for r in rows:
        events[r["eventSlug"]].append({
            "ts": float(r["timestamp"]), "outcome_idx": int(r["outcomeIndex"]),
            "price": float(r["price"]), "size": float(r["size"])})
    span = rows[-1]["timestamp"] - rows[0]["timestamp"]
    fam = collections.Counter(r.get("eventSlug", "").rsplit("-", 1)[0] for r in rows)
    seq = sequence_metrics(events)
    return {
        "env_id": "POLYMARKET_CRYPTO_UPDOWN_L5",
        "venue": "Polymarket",
        "category": "crypto up/down (BTC/ETH 5m + 15m)",
        "role": "REFERENCE (the environment L5 actually operated in)",
        "sample": {"source": "data/l5/raw/trades.jsonl",
                   "n_fills": len(rows), "n_events": len(events),
                   "date_range_utc": [iso(rows[0]["timestamp"]), iso(rows[-1]["timestamp"])],
                   "span_hours": round(span / 3600.0, 2),
                   "scope": "L5's OWN fills only; market-wide tape not on disk",
                   "families": fam.most_common(6)},
        "window_duration_seconds": {"value": [300, 900], "basis": "5m and 15m event families", "status": "measured"},
        "trades_per_minute": {"own_fills_per_min": round(len(rows) / (span / 60.0), 3),
                              "market_wide": UNAVAIL,
                              "note": "own-fill rate is a LOWER BOUND on venue print rate"},
        "spread": {"status": UNAVAIL, "note": "no BBO snapshots for these markets on disk"},
        "depth_q1_q5_q10_q25": {"status": UNAVAIL, "note": "no book snapshots on disk"},
        "simultaneous_pair_cost": {"status": UNAVAIL,
                                   "note": "no synchronous two-sided book; realized sequential VWAP pair cost measured instead"},
        "maker_economics": {"status": UNAVAIL,
                            "note": "incentive-era rebate/reward terms for this account not source-confirmed; locked per CLAUDE.md"},
        "adverse_selection": {"status": UNAVAIL, "note": "requires book + print alignment; not on disk"},
        "executable_capacity_usd": {"observed_per_event_median": seq["notional_per_event_usd"].get("median"),
                                    "observed_total": round(sum(float(r["price"]) * float(r["size"]) for r in rows), 2),
                                    "basis": "L5's own realized notional, not a book-depth capacity measure"},
        "sequence": seq,
    }


def env_polymarket_sports():
    """Polymarket sports (MLB/soccer/tennis/EPL/WNBA), full market tape.

    This is a CENSUS of every participant's prints inside 71 events, so
    two-sided-fill frequency and complement lag are measured across the whole
    participant population - directly comparable to L5's mechanics.
    """
    path = os.path.join(ROOT, "data", "transfer_backtest", "sports_trades.jsonl.gz")
    if not os.path.exists(path):
        return None
    n_ev, n_tr = 0, 0
    tmin, tmax = None, None
    tags, smt, fees = collections.Counter(), collections.Counter(), collections.Counter()
    # per (event, wallet) sequences -> does the venue let a participant cycle?
    per_actor = collections.defaultdict(list)
    per_event_prints = collections.defaultdict(list)
    prices = []
    vols = []
    for d in lines(path):
        n_ev += 1
        tags[d.get("tag")] += 1
        smt[d.get("sportsMarketType")] += 1
        fees[(d.get("maker_fee_bps"), d.get("taker_fee_bps"))] += 1
        if d.get("volume") is not None:
            vols.append(float(d["volume"]))
        cid = d.get("conditionId")
        for x in (d.get("trades") or []):
            ts = x.get("timestamp")
            pr, sz = x.get("price"), x.get("size")
            if ts is None or pr is None or sz is None:
                continue
            n_tr += 1
            ts = float(ts)
            tmin = ts if tmin is None else min(tmin, ts)
            tmax = ts if tmax is None else max(tmax, ts)
            prices.append(float(pr))
            oi = x.get("outcomeIndex")
            if oi is None:
                continue
            rec = {"ts": ts, "outcome_idx": int(oi), "price": float(pr), "size": float(sz)}
            per_actor[(cid, x.get("proxyWallet"))].append(rec)
            per_event_prints[cid].append(ts)
    if not n_tr:
        return None

    # trades/minute: measured per event over that event's own observed print span
    rates = []
    for cid, tss in per_event_prints.items():
        if len(tss) < 2:
            continue
        span = max(tss) - min(tss)
        if span > 0:
            rates.append(len(tss) / (span / 60.0))
    # Restrict sequence metrics to actors with >=2 fills (an actor with 1 fill
    # cannot complement by construction; reporting both is honest).
    active = {k: v for k, v in per_actor.items() if len(v) >= 2}
    seq_all = sequence_metrics(per_actor)
    seq_active = sequence_metrics(active)
    spans = [max(t for t in tss) - min(t for t in tss) for tss in per_event_prints.values() if len(tss) > 1]
    return {
        "env_id": "POLYMARKET_SPORTS",
        "venue": "Polymarket",
        "category": "sports moneyline/totals/spreads (MLB, soccer, tennis, EPL, WNBA)",
        "role": "CANDIDATE",
        "sample": {"source": "data/transfer_backtest/sports_trades.jsonl.gz",
                   "n_events": n_ev, "n_prints": n_tr,
                   "date_range_utc": [iso(tmin), iso(tmax)],
                   "span_days": round((tmax - tmin) / 86400.0, 2),
                   "scope": "CENSUS of all participants' prints within these 71 events",
                   "tags": tags.most_common(), "market_types": smt.most_common(),
                   "n_distinct_actor_event_pairs": len(per_actor),
                   "n_actor_event_pairs_with_2plus_fills": len(active)},
        "window_duration_seconds": {"observed_print_span_per_event": q(spans, unit="s"),
                                    "note": "sports markets are multi-day, not fixed short windows; "
                                            "this is the OPPOSITE of L5's 300/900s cadence"},
        "trades_per_minute": {"per_event_over_own_print_span": q(rates, unit="prints/min"),
                              "note": "census-based; includes long idle stretches between game days"},
        "trade_price_distribution": q(prices, unit="USD"),
        "spread": {"status": UNAVAIL, "note": "no BBO in this file; see POLYMARKET_SPORTS_PMUS_BBO env"},
        "depth_q1_q5_q10_q25": {"status": UNAVAIL, "note": "no book snapshots in this file"},
        "simultaneous_pair_cost": {"status": UNAVAIL, "note": "no synchronous book"},
        "maker_economics": {"maker_fee_bps_reported": sorted({k[0] for k in fees}),
                            "taker_fee_bps_reported": sorted({k[1] for k in fees}),
                            "rebates_or_rewards": UNAVAIL,
                            "note": "fee bps are the values carried in the market metadata on disk "
                                    "(1000 bps on every one of 71 events). NOT independently confirmed as "
                                    "the effective charged rate; treat as REPORTED, not measured economics. "
                                    "If genuinely 10%/side this environment is uneconomic for cycling."},
        "adverse_selection": {"status": UNAVAIL, "note": "requires book state at fill time; not on disk"},
        "executable_capacity_usd": {"event_volume_usd": q(vols, unit="USD"),
                                    "basis": "lifetime event volume from metadata; NOT instantaneous book capacity"},
        "sequence_all_actors": seq_all,
        "sequence_actors_with_2plus_fills": seq_active,
    }


def env_pmus_bbo():
    """PMUS sports-moneyline BBO snapshot sample. The 0.0050 spread question."""
    path = os.path.join(ROOT, "data", "transfer_backtest", "pmus_live_sample.jsonl")
    if not os.path.exists(path):
        return None
    rows = list(lines(path))
    if not rows:
        return None

    def f(v):
        return float(v) if v not in (None, "") else None

    ts = sorted(r["t"] for r in rows)
    spreads, bidq, askq = [], [], []
    bid_ticks = set()
    per_slug = collections.defaultdict(list)
    for r in rows:
        b, a = f(r.get("bid")), f(r.get("ask"))
        if b is not None and a is not None:
            spreads.append(round(a - b, 6))
        if f(r.get("bid_qty")) is not None:
            bidq.append(f(r["bid_qty"]))
        if f(r.get("ask_qty")) is not None:
            askq.append(f(r["ask_qty"]))
        if b is not None:
            bid_ticks.add(round(b, 4))
        per_slug[r["slug"]].append(r)

    # ---- ARTIFACT FORENSICS -------------------------------------------
    spread_hist = collections.Counter(spreads)
    # tick grid: are all quotes multiples of 0.005?
    on_half_cent = all(abs(round(x * 200) - x * 200) < 1e-6 for x in bid_ticks)
    quote_churn = {}
    for slug, rs in per_slug.items():
        rs = sorted(rs, key=lambda x: x["t"])
        uniq = len({(x.get("bid"), x.get("ask")) for x in rs})
        quote_churn[slug] = {
            "n_snapshots": len(rs),
            "n_distinct_bid_ask_pairs": uniq,
            "frozen": uniq == 1,
            "bid_min": f(rs[0]["bid"]) and min(f(x["bid"]) for x in rs),
            "bid_max": max(f(x["bid"]) for x in rs),
            "shares_traded_delta": round((f(rs[-1]["shares_traded"]) or 0) - (f(rs[0]["shares_traded"]) or 0), 2),
            "bid_qty_median": round(st.median([f(x["bid_qty"]) for x in rs if f(x["bid_qty"]) is not None]), 2),
        }
    modal = spread_hist.most_common(1)[0]
    verdict = (
        "ARTIFACT (tick floor), NOT a measured spread distribution. "
        f"{modal[1]}/{len(spreads)} snapshots sit at the modal {modal[0]:.4f}; every observed bid is an exact "
        "multiple of 0.0050, so 0.0050 is one minimum tick and the quote is simply touching. "
        f"The sample is {len(rows)} snapshots of only {len(per_slug)} distinct markets over "
        f"{round((dt.datetime.fromisoformat(ts[-1]) - dt.datetime.fromisoformat(ts[0])).total_seconds() / 60.0, 1)} minutes, "
        "and one of those two markets is quote-FROZEN for the entire sample "
        "(1 distinct bid/ask pair across 116 snapshots), so most rows are the same quote re-sampled. "
        "It is neither a single snapshot repeated nor a genuine cross-market distribution: it is a "
        "1-tick-wide touch on two markets, one of them static."
        if on_half_cent else
        "INCONCLUSIVE: bids are not all on a 0.0050 grid; re-examine."
    )
    return {
        "env_id": "POLYMARKET_SPORTS_PMUS_BBO",
        "venue": "Polymarket (PMUS feed)",
        "category": "sports moneyline (MLB)",
        "role": "CANDIDATE (sample too small to rank on)",
        "sample": {"source": "data/transfer_backtest/pmus_live_sample.jsonl",
                   "n_snapshots": len(rows), "n_distinct_markets": len(per_slug),
                   "n_distinct_timestamps": len({r["t"] for r in rows}),
                   "date_range_utc": [ts[0], ts[-1]],
                   "span_minutes": round((dt.datetime.fromisoformat(ts[-1]) -
                                          dt.datetime.fromisoformat(ts[0])).total_seconds() / 60.0, 1),
                   "SMALL_SAMPLE_WARNING": "232 snapshots / 2 markets / 12 minutes. "
                                           "NOT comparable to the 24,520-observation Gemini census or the "
                                           "47,120-print sports census. Cannot support a ranking position."},
        "window_duration_seconds": {"status": UNAVAIL, "note": "daily sports markets, no fixed short window"},
        "trades_per_minute": {"status": UNAVAIL,
                              "note": "shares_traded is a cumulative counter, not a print tape; "
                                      "deltas observed were " +
                                      ", ".join(f"{s}:{v['shares_traded_delta']}" for s, v in quote_churn.items()) +
                                      " shares over 12 min, but print count and side are unknown"},
        "spread": {"distribution": q(spreads, unit="USD"),
                   "histogram": {str(k): v for k, v in sorted(spread_hist.items())},
                   "all_bids_on_0.0050_tick_grid": on_half_cent,
                   "n_distinct_bid_levels_observed": len(bid_ticks),
                   "ARTIFACT_VERDICT": verdict},
        "depth_q1_q5_q10_q25": {"bid_qty_shares": q(bidq, unit="shares"),
                                "ask_qty_shares": {"n": len(askq), "status": UNAVAIL,
                                                   "note": "ask_qty is null in 232/232 rows"},
                                "q5_q10_q25_levels": {"status": UNAVAIL,
                                                      "note": "feed carries touch only, no level-2 ladder"},
                                "both_sides_executable": {"status": UNAVAIL,
                                                          "note": "CANNOT be computed: ask size never populated. "
                                                                  "The one-sided 167k-share median bid depth is NOT "
                                                                  "evidence of two-sided capacity."}},
        "simultaneous_pair_cost": {"status": UNAVAIL,
                                   "note": "feed carries one outcome per slug; no complement quote to pair with"},
        "maker_economics": {"status": UNAVAIL},
        "adverse_selection": {"status": UNAVAIL},
        "executable_capacity_usd": {"status": UNAVAIL,
                                    "note": "one-sided only; a pair-cycling strategy needs both sides"},
        "per_market_forensics": quote_churn,
    }


def env_gemini_btc5m():
    """Gemini BTC5M - current incumbent baseline, from the frozen census."""
    path = os.path.join(OUT_DIR, "STRUCTURAL_EXPANDED.json")
    if not os.path.exists(path):
        return None
    d = jread(path)
    prints_path = os.path.join(ROOT, "data", "transfer_backtest", "gemini_prints_btc05m.jsonl.gz")
    pr_per_win, pr_tmin, pr_tmax, n_pr, n_sym = [], None, None, 0, 0
    sizes = []
    if os.path.exists(prints_path):
        for rec in lines(prints_path):
            rr = rec.get("rows") or []
            n_sym += 1
            n_pr += len(rr)
            pr_per_win.append(len(rr))
            for x in rr:
                sizes.append(float(x["amount"]))
                t = float(x["timestamp"])
                pr_tmin = t if pr_tmin is None else min(pr_tmin, t)
                pr_tmax = t if pr_tmax is None else max(pr_tmax, t)
    bse = d.get("both_sides_executable_pct", {})
    pc = d.get("pair_cost_at_depth", {})
    return {
        "env_id": "GEMINI_BTC5M",
        "venue": "Gemini",
        "category": "crypto up/down 5-minute",
        "role": "INCUMBENT BASELINE (explicitly allowed to lose)",
        "sample": {"source": "analysis/gemini/updown/STRUCTURAL_EXPANDED.json (frozen census)",
                   "independent_windows": d.get("independent_windows"),
                   "observations": d.get("observations"),
                   "print_tape_source": "data/transfer_backtest/gemini_prints_btc05m.jsonl.gz",
                   "print_symbols": n_sym, "n_prints": n_pr,
                   "print_date_range_utc": [iso(pr_tmin), iso(pr_tmax)] if pr_tmin else UNAVAIL,
                   "AGE_WARNING": "Gemini BBO/print corpora on disk are AUGUST-SEPTEMBER 2026 HISTORICAL, "
                                  "not current. Structural conclusions are as-of that period."},
        "window_duration_seconds": {"value": 300, "status": "measured"},
        "trades_per_minute": {"prints_per_5min_window": q(pr_per_win, unit="prints/window"),
                              "implied_prints_per_min": round(st.median(pr_per_win) / 5.0, 3) if pr_per_win else UNAVAIL,
                              "note": "median ~4 prints per 5-minute window = the thinnest tape of any candidate"},
        "spread": {"distribution": d.get("spread_proxy_q1"),
                   "note": "spread proxy = (pair cost - 1.00) at q1"},
        "depth_q1_q5_q10_q25": {"both_sides_executable_pct": bse,
                                "pair_cost_at_depth": pc,
                                "print_size_distribution": q(sizes, unit="contracts")},
        "simultaneous_pair_cost": {"distribution": d.get("pair_cost_q1"),
                                   "pct_below_1_00": {"n": d.get("observations"), "pct": 0.0},
                                   "note": "NEVER below $1.00 across 24,520 observations (min 1.01). "
                                           "Simultaneous pair-cost compression is structurally impossible here."},
        "maker_economics": {"status": UNAVAIL,
                            "note": "no Gemini maker rebate / liquidity reward program sourced on disk"},
        "adverse_selection": {"status": UNAVAIL,
                              "note": "not measurable from the frozen census; no fill-level attribution"},
        "executable_capacity_usd": {"touch_contracts_median": 500,
                                    "basis": "median touch size 500 contracts (from BBO corpus, see GEMINI_BTC15M)",
                                    "note": "nominal depth is adequate; the binding constraint is pair cost, not size"},
        "sequence": {"complement_reach_pct_underdog": (d.get("UNDERDOG") or {}).get("complement_reach_pct", UNAVAIL),
                     "complement_reach_pct_favored": (d.get("FAVORED") or {}).get("complement_reach_pct", UNAVAIL),
                     "note": "carried from the frozen census: ~45.9% underdog / ~50.8% favored complement reach "
                             "at median 117s into a 300s window"},
    }


def env_gemini_btc15m():
    """Gemini BTC15M BBO - matches L5's 900s window length."""
    path = os.path.join(ROOT, "data", "transfer_backtest", "gemini_bbo_btc15m.jsonl.gz")
    if not os.path.exists(path):
        return None
    spreads, touch, tmin, tmax = [], [], None, None
    syms = set()
    n = 0
    for r in lines(path):
        n += 1
        syms.add(r.get("symbol"))
        t = float(r["t"])
        tmin = t if tmin is None else min(tmin, t)
        tmax = t if tmax is None else max(tmax, t)
        try:
            b, a = float(r["bid"]), float(r["ask"])
        except (TypeError, ValueError, KeyError):
            continue
        spreads.append(round(a - b, 6))
        for k in ("bid_sz", "ask_sz"):
            if r.get(k) not in (None, ""):
                touch.append(float(r[k]))
    if not n:
        return None
    return {
        "env_id": "GEMINI_BTC15M",
        "venue": "Gemini",
        "category": "crypto up/down 15-minute",
        "role": "CANDIDATE (window length matches L5's 900s)",
        "sample": {"source": "data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz",
                   "n_bbo_rows": n, "n_symbols": len(syms),
                   "date_range_utc": [iso(tmin), iso(tmax)],
                   "span_hours": round((tmax - tmin) / 3600.0, 2),
                   "AGE_WARNING": "AUGUST 2026 HISTORICAL, not current."},
        "window_duration_seconds": {"value": 900, "status": "measured",
                                    "note": "matches L5's 900s window - the one dimension where Gemini aligns"},
        "trades_per_minute": {"status": UNAVAIL,
                              "note": "gemini_prints_15m_tail.jsonl.gz holds only 46 symbol records; "
                                      "too thin to state a rate"},
        "spread": {"distribution": q(spreads, unit="USD"),
                   "note": "single-outcome (UP leg) quoted spread, directly measured"},
        "depth_q1_q5_q10_q25": {"touch_size_distribution": q(touch, unit="contracts"),
                                "q5_q10_q25": {"status": UNAVAIL,
                                               "note": "BBO feed is touch-only; no level-2 ladder on disk"}},
        "simultaneous_pair_cost": {"status": UNAVAIL,
                                   "note": "this file carries the UP leg only; no synchronous DOWN quote to pair"},
        "maker_economics": {"status": UNAVAIL},
        "adverse_selection": {"status": UNAVAIL},
        "executable_capacity_usd": {"touch_notional_usd_approx": round(st.median(touch) * 0.5, 2) if touch else UNAVAIL,
                                    "basis": "median touch contracts x ~$0.50 mid; approximation, not a sweep"},
    }


def env_kalshi_15m_series():
    """Kalshi 15-minute series: the only candidate with SOURCED maker rewards.

    Two independent corpora:
      analysis/kalshi/leg_tape.jsonl.gz  - synchronous two-sided pair cost + depth bands
      analysis/kalshi/venue_census.json  - liquidity reward programs (reward_usd/target_size)
      data/policy_d_port_ext/*.jsonl     - Kalshi print tape (trades/minute, side switching)
    """
    tape = os.path.join(ROOT, "analysis", "kalshi", "leg_tape.jsonl.gz")
    census = os.path.join(ROOT, "analysis", "kalshi", "venue_census.json")
    if not os.path.exists(tape):
        return None
    rows = list(lines(tape))
    pair = [r["pair_cost"] for r in rows if r.get("pair_cost") is not None]
    bands = {}
    for lbl, keys in (("q1", ("band1_yes", "band1_no")), ("q2", ("band2_yes", "band2_no")),
                      ("q5", ("band5_yes", "band5_no")), ("q10", ("band10_yes", "band10_no")),
                      ("touch", ("touch_yes", "touch_no")), ("full", ("full_yes", "full_no"))):
        vals = [r[k] for r in rows for k in keys if r.get(k) is not None]
        bands[lbl] = q(vals, unit="contracts (YES and NO pooled)")
        both = [1 if (r.get(keys[0]) and r.get(keys[1])) else 0 for r in rows]
        bands[lbl + "_both_sides_executable_pct"] = round(100.0 * sum(both) / len(both), 2) if both else UNAVAIL
    series = collections.Counter(r["series"] for r in rows)

    # ---- reward programs (SOURCED) ----
    rew = {"status": UNAVAIL}
    if os.path.exists(census):
        c = jread(census)
        want = set(series)
        hits = [s for s in c.get("series", []) if s.get("series") in want]
        rew = {"source": "analysis/kalshi/venue_census.json",
               "observed_utc": c.get("observed_utc"),
               "total_programs_scanned": c.get("total_programs"),
               "active_now_at_observation": c.get("active_now"),
               "programs_matching_this_tape": [
                   {"series": s["series"], "reward_usd": s.get("reward_usd"),
                    "target_size": s.get("target_size"), "period_minutes": s.get("period_minutes"),
                    "types": s.get("types"), "pool_per_hour_usd": round(s.get("pool_per_hour", 0), 2)}
                   for s in hits],
               "program_active_fraction_in_tape": round(
                   100.0 * sum(1 for r in rows if r.get("program_active")) / len(rows), 2),
               "status": "MEASURED/SOURCED (liquidity-reward program terms published by venue and captured on disk). "
                         "NOTE: per CLAUDE.md these are NOT credited economics - no reward may be assumed until "
                         "actually credited and source-accounted."}

    # ---- print tape from policy_d_port_ext ----
    kp_tmin = kp_tmax = None
    kp_mkt = collections.defaultdict(list)
    n_kp = 0
    seen_tid = set()
    dup_tid = 0
    gem_bbo_rows = 0
    for fp in sorted(glob.glob(os.path.join(ROOT, "data", "policy_d_port_ext", "*.jsonl"))):
        for d in lines(fp):
            if d.get("kind") == "bbo" and d.get("venue") == "gemini":
                gem_bbo_rows += 1
            if d.get("kind") != "print" or d.get("venue") != "kalshi":
                continue
            tid = d.get("trade_id")
            if tid is not None:
                if tid in seen_tid:
                    dup_tid += 1
                    continue
                seen_tid.add(tid)
            n_kp += 1
            t = float(d["t"])
            kp_tmin = t if kp_tmin is None else min(kp_tmin, t)
            kp_tmax = t if kp_tmax is None else max(kp_tmax, t)
            kp_mkt[d.get("market")].append((t, d.get("taker_side"), float(d.get("count") or 0),
                                            float(d.get("yes_price") or 0)))
    # Rate basis: prints per minute over the market's NOMINAL 900s window.
    # Using the observed first-to-last print span instead inflates the rate
    # wildly for markets whose prints all land in a few seconds, so the
    # nominal window is the honest denominator for a 15-minute series.
    rates, switches, sizes = [], [], []
    for m, v in kp_mkt.items():
        v.sort()
        rates.append(len(v) / 15.0)
        if len(v) >= 2:
            sd = [x[1] for x in v]
            switches.append(sum(1 for i in range(1, len(sd)) if sd[i] != sd[i - 1]) / (len(sd) - 1))
        sizes.extend(x[2] for x in v)
    taker_sides = collections.Counter(x[1] for v in kp_mkt.values() for x in v)

    return {
        "env_id": "KALSHI_15M_MULTI_SERIES",
        "venue": "Kalshi",
        "category": "15-minute crypto/commodity series (BTC, ETH, SOL, XRP, DOGE, GOLD, SILVER, WTI, COPPER, NATGAS, CRYPTOLEAD)",
        "role": "CANDIDATE",
        "sample": {"pair_cost_and_depth_source": "analysis/kalshi/leg_tape.jsonl.gz",
                   "n_leg_tape_rows": len(rows), "n_tickers": len({r["ticker"] for r in rows}),
                   "leg_tape_date_range_utc": [rows[0]["iso"], rows[-1]["iso"]],
                   "leg_tape_span_minutes": 83,
                   "series_breakdown": series.most_common(),
                   "print_tape_source": "data/policy_d_port_ext/*.jsonl (17 files)",
                   "n_kalshi_prints_deduped_by_trade_id": n_kp,
                   "n_duplicate_trade_ids_dropped": dup_tid,
                   "n_print_markets": len(kp_mkt),
                   "print_date_range_utc": [iso(kp_tmin), iso(kp_tmax)] if kp_tmin else UNAVAIL,
                   "print_span_hours": round((kp_tmax - kp_tmin) / 3600.0, 2) if kp_tmin else UNAVAIL,
                   "SAMPLE_NOTE": "leg_tape is 2,269 synchronous two-sided snapshots over ~83 minutes on "
                                  "2026-09-09 - two orders of magnitude smaller in time than the Gemini "
                                  "census. The print tape is independent, larger (40,965 prints over ~2 days, "
                                  "2026-08-31 to 2026-09-01), and covers SOL/XRP/DOGE only."},
        "window_duration_seconds": {"value": 900, "status": "measured",
                                    "note": "matches L5's 900s window"},
        "trades_per_minute": {"per_market_over_nominal_900s_window": q(rates, unit="prints/min"),
                              "rate_basis": "prints per market divided by the nominal 15-minute window",
                              "taker_side_mix": dict(taker_sides),
                              "print_size_distribution": q(sizes, unit="contracts")},
        "side_switch_frequency": {"taker_side_switch_rate_per_adjacent_print": q(switches),
                                  "note": "venue-level flow two-sidedness proxy; per-ACTOR switching is "
                                          "UNAVAILABLE (Kalshi prints are anonymous)"},
        "spread": {"pair_cost_minus_1_00": q([x - 1.0 for x in pair], unit="USD"),
                   "note": "YES_bid + NO_bid pair cost minus $1.00, the directly comparable spread proxy"},
        "depth_q1_q5_q10_q25": bands,
        "simultaneous_pair_cost": {"distribution": q(pair, unit="USD YES+NO"),
                                   "pct_below_1_00": frac(pair, lambda x: x < 1.00),
                                   "pct_at_or_below_0_96": frac(pair, lambda x: x <= 0.96),
                                   "note": "min 1.001 - never below $1.00 in this 2,269-row sample either, "
                                           "but the distribution is far tighter than Gemini "
                                           "(median 1.02 vs 1.24)"},
        "maker_economics": rew,
        "adverse_selection": {"status": UNAVAIL,
                              "note": "prints carry taker_side but no own-fill attribution; "
                                      "post-fill mark drift not computable from these files"},
        "executable_capacity_usd": {"q5_band_contracts_median": bands["q5"].get("median"),
                                    "q10_band_contracts_median": bands["q10"].get("median"),
                                    "note": "band depth is in contracts at <=$1 each, so median q5 band "
                                            "~1,570 contracts ~= low-thousands USD per side per leg; "
                                            "ample for the bounded probe stage"},
        "sequence": {"status": UNAVAIL,
                     "note": "Kalshi prints are anonymous, so per-actor OPEN/EXTEND/COMPLETE sequences "
                             "cannot be reconstructed. Complement reach and first-inventory-to-complement "
                             "lag are NOT measurable for this venue from on-disk data."},
        "_gemini_bbo_rows_in_same_corpus": gem_bbo_rows,
    }


def env_gemini_policy_d():
    """Gemini BBO from the same simultaneous capture as the Kalshi print tape.

    This is the cleanest apples-to-apples slice: same clock, same 2-day span.
    Both legs (UP and DOWN) are present, so simultaneous pair cost IS measurable.
    """
    rows_up = collections.defaultdict(dict)
    tmin = tmax = None
    n = 0
    spreads, touch, depth2 = [], [], []
    for fp in sorted(glob.glob(os.path.join(ROOT, "data", "policy_d_port_ext", "*.jsonl"))):
        for d in lines(fp):
            if d.get("kind") != "bbo" or d.get("venue") != "gemini":
                continue
            n += 1
            t = float(d["t"])
            tmin = t if tmin is None else min(tmin, t)
            tmax = t if tmax is None else max(tmax, t)
            mkt = d.get("market") or ""
            bids, asks = d.get("bids") or [], d.get("asks") or []
            if bids and asks:
                b, a = float(bids[0][0]), float(asks[0][0])
                spreads.append(round(a - b, 6))
                touch.append(float(bids[0][1]))
                touch.append(float(asks[0][1]))
            if len(asks) >= 2:
                depth2.append(float(asks[1][1]))
            # key on event + leg to pair UP/DOWN at the same tick
            if "-" in mkt:
                base, leg = mkt.rsplit("-", 1)
                if asks:
                    rows_up[(base, round(t, 0))][leg] = float(asks[0][0])
    pair = []
    for k, v in rows_up.items():
        if "UP" in v and "DOWN" in v:
            pair.append(v["UP"] + v["DOWN"])
    if not n:
        return None
    return {
        "env_id": "GEMINI_MULTI_ASSET_SHORT_WINDOW",
        "venue": "Gemini",
        "category": "crypto up/down short-window (BTC/ETH 5m, multi-asset), simultaneous capture with Kalshi",
        "role": "INCUMBENT BASELINE - independent corpus, same clock as the Kalshi print tape "
                "(single-leg only; not rankable)",
        "sample": {"source": "data/policy_d_port_ext/*.jsonl (kind=bbo, venue=gemini)",
                   "n_bbo_rows": n,
                   "date_range_utc": [iso(tmin), iso(tmax)],
                   "span_hours": round((tmax - tmin) / 3600.0, 2),
                   "n_paired_up_down_ticks": len(pair),
                   "AGE_WARNING": "2026-08-31 to 2026-09-01 HISTORICAL, not current.",
                   "WHY_THIS_MATTERS": "captured in the SAME collector run as the Kalshi print tape, so the "
                                       "Gemini-vs-Kalshi spread comparison here is not confounded by date. "
                                       "But it carries the UP leg only, so it cannot speak to pair cost."},
        "window_duration_seconds": {"value": 300, "status": "measured"},
        "trades_per_minute": {"status": UNAVAIL, "note": "this corpus has no Gemini print records"},
        "spread": {"distribution": q(spreads, unit="USD"),
                   "note": "ask[0]-bid[0] on a single leg, directly measured"},
        "depth_q1_q5_q10_q25": {"touch_size_distribution": q(touch, unit="contracts"),
                                "level2_size_distribution": q(depth2, unit="contracts"),
                                "q5_q10_q25": {"status": UNAVAIL,
                                               "note": "feed carries ~2 levels per side only"}},
        "simultaneous_pair_cost": {"distribution": q(pair, unit="USD UP_ask+DOWN_ask"),
                                   "pct_below_1_00": frac(pair, lambda x: x < 1.00),
                                   "pct_at_or_below_0_96": frac(pair, lambda x: x <= 0.96),
                                   "LEG_COVERAGE_FINDING": "This corpus contains 45,115 BBO rows and ALL of "
                                           "them are the UP leg - zero DOWN-leg rows. Simultaneous pair cost is "
                                           "therefore genuinely UNAVAILABLE here, and this corpus CANNOT be used "
                                           "as independent confirmation of the frozen census pair-cost result. "
                                           "Its value is the directly measured single-leg quoted spread."},
        "maker_economics": {"status": UNAVAIL},
        "adverse_selection": {"status": UNAVAIL},
        "executable_capacity_usd": {"touch_contracts_median": touch and q(touch)["median"],
                                    "basis": "median touch size; not a sweep"},
    }


# ================================================================= scoring
# Dimensions scored ONLY where data exists. A dimension with no data for an
# environment scores None and is EXCLUDED from that environment's denominator,
# and the coverage count is reported so a thin-data winner cannot hide.
L5_REF = {
    "window_s": 900,
    "pair_cost_median": 0.977,
    "complement_pct": 71.5,
    "complement_lag_s": 52,
    "fills_per_event_median": 6,
    "fills_per_min": 12.64,
    "first_price_median": 0.588,
}


def score(envs):
    out = {}
    for e in envs:
        s, notes = {}, {}

        # 1. pair-cost compression - THE decisive dimension
        spc = e.get("simultaneous_pair_cost", {})
        seq = e.get("sequence") or e.get("sequence_actors_with_2plus_fills") or {}
        realized = (seq or {}).get("realized_vwap_pair_cost_usd")
        med = None
        if isinstance(spc.get("distribution"), dict) and spc["distribution"].get("median") is not None:
            med = spc["distribution"]["median"]
            notes["pair_cost_basis"] = "simultaneous book"
        elif isinstance(realized, dict) and realized.get("median") is not None:
            med = realized["median"]
            notes["pair_cost_basis"] = "realized sequential VWAP"
        if med is None:
            s["pair_cost_compression"] = None
        else:
            # 100 at L5's 0.977, decaying with excess over $1.00
            excess = max(0.0, med - 1.00)
            s["pair_cost_compression"] = round(max(0.0, 100.0 * (1.0 - excess / 0.25)), 1)
            notes["pair_cost_median"] = med

        # 2. inventory cycling (own fills per event vs L5 median 6)
        fpe = (seq or {}).get("own_fills_per_event")
        if isinstance(fpe, dict) and fpe.get("median"):
            s["inventory_cycling"] = round(min(100.0, 100.0 * fpe["median"] / L5_REF["fills_per_event_median"]), 1)
            notes["fills_per_event_median"] = fpe["median"]
        else:
            s["inventory_cycling"] = None

        # 3. complement reach
        cr = (seq or {}).get("complement_reach_pct")
        if cr is None:
            cr = (seq or {}).get("complement_reach_pct_favored")
        if isinstance(cr, (int, float)):
            s["complement_reach"] = round(min(100.0, 100.0 * cr / L5_REF["complement_pct"]), 1)
            notes["complement_reach_pct"] = cr
        else:
            s["complement_reach"] = None

        # 4. complement latency (closer to L5's 52s = better; penalise slower only)
        lag = (seq or {}).get("first_inventory_to_complement_seconds")
        if isinstance(lag, dict) and lag.get("median"):
            r = lag["median"] / L5_REF["complement_lag_s"]
            s["complement_latency"] = round(max(0.0, 100.0 / max(1.0, r)), 1)
            notes["complement_lag_s"] = lag["median"]
        else:
            s["complement_latency"] = None

        # 5. trade frequency
        tpm = e.get("trades_per_minute", {})
        rate = None
        for k in ("own_fills_per_min", "implied_prints_per_min"):
            if isinstance(tpm.get(k), (int, float)):
                rate = tpm[k]
        for k in ("per_market_over_nominal_900s_window", "per_market_over_own_print_span",
                  "per_event_over_own_print_span"):
            if isinstance(tpm.get(k), dict) and tpm[k].get("median"):
                rate = tpm[k]["median"]
        if rate is None:
            s["trade_frequency"] = None
        else:
            s["trade_frequency"] = round(min(100.0, 100.0 * rate / L5_REF["fills_per_min"]), 1)
            notes["prints_or_fills_per_min"] = round(rate, 3)

        # 6. window cadence match (L5 = 900s; 300s is adjacent; multi-day is not)
        wd = e.get("window_duration_seconds", {})
        wv = wd.get("value")
        if wv is None:
            s["window_cadence_match"] = None
        else:
            cands = wv if isinstance(wv, list) else [wv]
            s["window_cadence_match"] = round(max(
                100.0 / max(1.0, max(c, L5_REF["window_s"]) / min(c, L5_REF["window_s"])) for c in cands), 1)
            notes["window_s"] = wv

        # 7. two-sided executable depth
        dep = e.get("depth_q1_q5_q10_q25", {})
        bs = dep.get("both_sides_executable_pct")
        twos = None
        if isinstance(bs, dict) and isinstance(bs.get("q5"), (int, float)):
            twos = bs["q5"]
            notes["both_sides_q5_pct"] = twos
        elif isinstance(dep.get("q5_both_sides_executable_pct"), (int, float)):
            twos = dep["q5_both_sides_executable_pct"]
            notes["both_sides_q5_pct"] = twos
        s["two_sided_depth"] = round(twos, 1) if twos is not None else None

        # 8. maker economics - SOURCED only
        me = e.get("maker_economics", {})
        if str(me.get("status", "")).startswith("MEASURED"):
            s["maker_economics_sourced"] = 100.0
            notes["maker_economics"] = "SOURCED liquidity-reward programs on disk"
        elif me.get("maker_fee_bps_reported"):
            s["maker_economics_sourced"] = 0.0
            notes["maker_economics"] = ("fee bps REPORTED in metadata (%s) but effective rate unconfirmed; "
                                        "no rebate/reward sourced" % me["maker_fee_bps_reported"])
        else:
            s["maker_economics_sourced"] = None
            notes["maker_economics"] = UNAVAIL

        # 9. adverse selection - never guessed
        s["adverse_selection"] = None
        notes["adverse_selection"] = UNAVAIL

        have = {k: v for k, v in s.items() if v is not None}
        out[e["env_id"]] = {
            "dimension_scores": s,
            "dimensions_with_data": len(have),
            "dimensions_total": len(s),
            "structural_similarity_score": round(st.fmean(have.values()), 1) if have else None,
            "evidence_notes": notes,
        }
    return out


def main():
    builders = [env_l5_polymarket_updown, env_polymarket_sports, env_pmus_bbo,
                env_gemini_btc5m, env_gemini_btc15m, env_kalshi_15m_series,
                env_gemini_policy_d]
    envs = []
    for b in builders:
        try:
            r = b()
        except Exception as exc:  # keep one bad corpus from killing the run
            p("WARN %s failed: %r" % (b.__name__, exc))
            continue
        if r:
            envs.append(r)
            p("ok %-34s n-dims" % r["env_id"])
        else:
            p("skip %s (no data on disk)" % b.__name__)

    scores = score(envs)
    # RANKABILITY GATE. An environment is rankable only if it has data on the
    # decisive dimension (pair-cost compression) AND on at least MIN_DIMS
    # dimensions overall. Without this gate an environment scored on window
    # length alone tops the table, which would be meaningless.
    MIN_DIMS = 4
    rankable, not_rankable = [], {}
    for k, v in scores.items():
        why = []
        if v["dimension_scores"].get("pair_cost_compression") is None:
            why.append("no data on the decisive dimension (pair-cost compression)")
        if v["dimensions_with_data"] < MIN_DIMS:
            why.append("only %d/%d dimensions have data (minimum %d)"
                       % (v["dimensions_with_data"], v["dimensions_total"], MIN_DIMS))
        if v["structural_similarity_score"] is None:
            why.append("no scorable dimension at all")
        if why:
            not_rankable[k] = why
            v["rankable"] = False
            v["not_rankable_because"] = why
        else:
            rankable.append(k)
            v["rankable"] = True
    ranked = sorted(rankable, key=lambda k: (-scores[k]["structural_similarity_score"],
                                             -scores[k]["dimensions_with_data"]))

    # LIKE-FOR-LIKE RE-SCORE. The headline mean is computed over whichever
    # dimensions each environment happens to have, so two environments'
    # headline scores are NOT directly comparable. Re-score every rankable
    # environment over only the dimensions ALL of them share, so the ordering
    # can be checked on a common basis.
    common = None
    for k in rankable:
        have = {d for d, v in scores[k]["dimension_scores"].items() if v is not None}
        common = have if common is None else (common & have)
    common = sorted(common or [])
    like_for_like = {}
    for k in rankable:
        vals = [scores[k]["dimension_scores"][d] for d in common]
        like_for_like[k] = round(st.fmean(vals), 1) if vals else None
        scores[k]["like_for_like_score"] = like_for_like[k]
    ranked_lfl = sorted(rankable, key=lambda k: -(like_for_like[k] or 0))

    doc = {
        "label": "VENUE_STRUCTURAL_SCORECARD_V1",
        "lane": "Lane 4 - venue/category structural selector",
        "generated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "RESEARCH ONLY - read-only over existing on-disk corpora; no network, no venue writes",
        "key_question": "Does the environment permit repeated inventory cycling AND pair-cost "
                        "compression similar to L5?",
        "l5_reference_policy": L5_REF,
        "rigour": {
            "measured": "computed from on-disk data; n and date range stated per metric",
            "estimated": "labelled explicitly inline where any derivation is approximate",
            "UNAVAILABLE": "not derivable from on-disk data - emitted literally, never guessed",
            "scoring_rule": "a dimension with no data scores None and is excluded from that "
                            "environment's denominator; dimensions_with_data is reported so a "
                            "thin-data winner cannot hide behind a high mean",
        },
        "environments": {e["env_id"]: e for e in envs},
        "scores": scores,
        "ranking": ranked,
        "not_rankable": not_rankable,
        "rankability_gate": "requires data on pair-cost compression AND >=4 of 9 dimensions",
        "like_for_like": {
            "common_dimensions": common,
            "scores": like_for_like,
            "ranking": ranked_lfl,
            "why": "headline scores average over different dimension sets per environment and are "
                   "therefore not directly comparable; this re-scores all rankable environments over "
                   "only the dimensions every one of them has data for",
        },
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    jp = os.path.join(OUT_DIR, "VENUE_SCORECARD.json")
    with open(jp, "w") as fh:
        json.dump(doc, fh, indent=1, default=str)
    p("wrote %s" % jp)
    write_md(doc, os.path.join(OUT_DIR, "VENUE_SCORECARD.md"))
    p("wrote VENUE_SCORECARD.md")
    for i, k in enumerate(ranked, 1):
        p("%d. %-38s %5.1f  (%d/%d dims)" % (
            i, k, scores[k]["structural_similarity_score"],
            scores[k]["dimensions_with_data"], scores[k]["dimensions_total"]))


def fmt(v):
    if v is None:
        return UNAVAIL
    if isinstance(v, dict):
        if v.get("status") == UNAVAIL or v.get("n") == 0:
            return UNAVAIL
        if "median" in v:
            bits = ["med %s" % v["median"]]
            if "p10" in v:
                bits.append("p10 %s" % v["p10"])
            if "p90" in v:
                bits.append("p90 %s" % v["p90"])
            bits.append("n=%d" % v["n"])
            return ", ".join(bits)
        if "pct" in v:
            return "%.2f%% (n=%d)" % (v["pct"], v["n"])
        return json.dumps(v, default=str)[:160]
    return str(v)


def write_md(doc, path):
    E, S = doc["environments"], doc["scores"]
    L = []
    w = L.append
    w("# Lane 4 - Venue / Category Structural Scorecard")
    w("")
    w("`%s` - generated %s" % (doc["label"], doc["generated_utc"]))
    w("")
    w("**Mode:** %s" % doc["mode"])
    w("")
    w("**Key question per environment:** %s" % doc["key_question"])
    w("")
    w("Generated by `scripts/venue_structural_scorecard.py`. Machine-readable companion: "
      "`VENUE_SCORECARD.json`.")
    w("")
    w("## Rigour contract")
    w("")
    w("- Every number below carries its **n** and **date range**. The sources differ by three orders "
      "of magnitude in size and by weeks in age; they are never silently compared.")
    w("- `UNAVAILABLE` means *not derivable from on-disk data*. It is printed literally rather than "
      "estimated. No ranking dimension is fabricated where data is absent.")
    w("- A scoring dimension with no data for an environment is excluded from that environment's "
      "denominator, and `dimensions_with_data` is reported alongside every score so a thin-data "
      "environment cannot win on a narrow base.")
    w("- **No live orders, no venue writes, no new collectors, no credentials.**")
    w("")
    w("## Ranking")
    w("")
    w("| # | Environment | Venue / category | Similarity | Dims with data | Pair-cost median | Complement | Window |")
    w("|---|---|---|---|---|---|---|---|")
    for i, k in enumerate(doc["ranking"], 1):
        e, s = E[k], S[k]
        n = s["evidence_notes"]
        w("| %d | `%s` | %s / %s | **%.1f** | %d/%d | %s | %s | %s |" % (
            i, k, e["venue"], e["category"][:46], s["structural_similarity_score"],
            s["dimensions_with_data"], s["dimensions_total"],
(("%.4f" % n["pair_cost_median"]) if "pair_cost_median" in n else UNAVAIL),
            ("%.1f%%" % n["complement_reach_pct"]) if "complement_reach_pct" in n else UNAVAIL,
            n.get("window_s", UNAVAIL)))
    unranked = [k for k in S if k not in doc["ranking"]]
    nrk = doc.get("not_rankable", {})
    if unranked:
        w("")
        w("**Not rankable** (gate: %s):" % doc["rankability_gate"])
        w("")
        for u in unranked:
            w("- `%s` (%s / %s) - %s" % (u, E[u]["venue"], E[u]["category"][:50],
                                         "; ".join(nrk.get(u, ["insufficient data"]))))
    w("")
    lfl = doc.get("like_for_like", {})
    if lfl.get("common_dimensions"):
        w("### Like-for-like re-score (common dimensions only)")
        w("")
        w("> The headline **Similarity** column above averages over whichever dimensions each "
          "environment happens to have data for, so two headline scores are **not directly "
          "comparable**. The table below re-scores every rankable environment over only the "
          "dimensions all of them share: %s." % ", ".join("`%s`" % c for c in lfl["common_dimensions"]))
        w("")
        w("| # | Environment | Like-for-like | Headline | Dims with data |")
        w("|---|---|---|---|---|")
        for i, k in enumerate(lfl["ranking"], 1):
            w("| %d | `%s` | **%s** | %s | %d/%d |" % (
                i, k, lfl["scores"][k], S[k]["structural_similarity_score"],
                S[k]["dimensions_with_data"], S[k]["dimensions_total"]))
        w("")
    w("## Sample sizes and date ranges (read this before any comparison)")
    w("")
    w("| Environment | Source | Sample | Date range (UTC) | Scope |")
    w("|---|---|---|---|---|")
    for k, e in E.items():
        sm = e["sample"]
        src = sm.get("source") or sm.get("pair_cost_and_depth_source") or "(multiple)"
        n = next((("%s=%s" % (kk, vv)) for kk, vv in sm.items()
                  if kk.startswith("n_") or kk in ("observations", "independent_windows")), "?")
        dr = sm.get("date_range_utc") or sm.get("leg_tape_date_range_utc") or sm.get("print_date_range_utc") or ["?"]
        w("| `%s` | `%s` | %s | %s | %s |" % (
            k, src, n, " to ".join(str(x) for x in dr),
            (sm.get("scope") or sm.get("SAMPLE_NOTE") or sm.get("AGE_WARNING") or
             sm.get("SMALL_SAMPLE_WARNING") or "")[:150]))
    w("")
    w("## Per-environment detail")
    w("")
    for k in doc["ranking"] + unranked:
        e = E[k]
        s = S.get(k, {})
        w("### `%s` - %s" % (k, e["role"]))
        w("")
        w("**%s / %s**" % (e["venue"], e["category"]))
        w("")
        sm = e["sample"]
        w("Sample: " + "; ".join("%s=%s" % (kk, vv) for kk, vv in sm.items()
                                 if not kk.isupper() and kk not in ("source",))[:900])
        for warn in ("AGE_WARNING", "SMALL_SAMPLE_WARNING", "SAMPLE_NOTE", "WHY_THIS_MATTERS"):
            if sm.get(warn):
                w("")
                w("> **%s** %s" % (warn.replace("_", " "), sm[warn]))
        w("")
        w("| Dimension | Value |")
        w("|---|---|")
        w("| trades/minute | %s |" % fmt(e.get("trades_per_minute", {}).get("per_market_over_nominal_900s_window")
                                         or e.get("trades_per_minute", {}).get("per_market_over_own_print_span")
                                         or e.get("trades_per_minute", {}).get("per_event_over_own_print_span")
                                         or e.get("trades_per_minute", {}).get("prints_per_5min_window")
                                         or e.get("trades_per_minute", {}).get("own_fills_per_min")
                                         or e.get("trades_per_minute", {}).get("status")))
        w("| spread | %s |" % fmt(e.get("spread", {}).get("distribution")
                                  or e.get("spread", {}).get("pair_cost_minus_1_00")
                                  or e.get("spread", {}).get("status")))
        w("| simultaneous pair cost | %s |" % fmt(e.get("simultaneous_pair_cost", {}).get("distribution")
                                                  or e.get("simultaneous_pair_cost", {}).get("status")))
        seq = e.get("sequence") or e.get("sequence_actors_with_2plus_fills") or {}
        w("| realized VWAP pair cost | %s |" % fmt(seq.get("realized_vwap_pair_cost_usd")))
        w("| pair cost below $1.00 | %s |" % fmt(e.get("simultaneous_pair_cost", {}).get("pct_below_1_00")
                                                 or seq.get("pct_pair_cost_below_1_00")))
        w("| two-sided fills / complement reach | %s |" % fmt(
            seq.get("complement_reach_pct") or seq.get("complement_reach_pct_favored")))
        w("| first inventory -> complement | %s |" % fmt(seq.get("first_inventory_to_complement_seconds")))
        w("| side-switch frequency | %s |" % fmt(
            seq.get("side_switch_rate_per_adjacent_fill")
            or (e.get("side_switch_frequency") or {}).get("taker_side_switch_rate_per_adjacent_print")))
        w("| own fills per event | %s |" % fmt(seq.get("own_fills_per_event")))
        w("| window duration | %s |" % fmt(e.get("window_duration_seconds", {}).get("value")
                                           or e.get("window_duration_seconds", {}).get("status")))
        dep = e.get("depth_q1_q5_q10_q25", {})
        w("| depth q1/q5/q10/q25 | %s |" % (json.dumps(
            {kk: (vv.get("median") if isinstance(vv, dict) and "median" in vv
                  else (vv if isinstance(vv, (int, float, str)) else UNAVAIL))
             for kk, vv in dep.items() if not kk.endswith("_distribution")}, default=str)[:300] or UNAVAIL))
        w("| maker economics / rewards | %s |" % str(e.get("maker_economics", {}).get("status", UNAVAIL))[:300])
        w("| adverse selection | %s |" % str(e.get("adverse_selection", {}).get("status", UNAVAIL))[:200])
        w("| executable capacity (USD) | %s |" % json.dumps(e.get("executable_capacity_usd", {}), default=str)[:320])
        if s.get("structural_similarity_score") is not None:
            w("")
            w("Structural similarity **%.1f** over %d/%d dimensions with data. Per-dimension: `%s`" % (
                s["structural_similarity_score"], s["dimensions_with_data"], s["dimensions_total"],
                json.dumps(s["dimension_scores"])))
        if e["env_id"] == "POLYMARKET_SPORTS_PMUS_BBO":
            w("")
            w("#### The 0.0050 spread: real or artifact?")
            w("")
            w("> " + e["spread"]["ARTIFACT_VERDICT"])
            w("")
            w("Per-market forensics: `%s`" % json.dumps(e["per_market_forensics"], default=str))
        w("")
    w("## Findings")
    w("")
    w("1. **Pair-cost compression is the discriminating dimension, and it separates Polymarket from "
      "everything else.** L5's realized sequential VWAP pair cost has median **0.977**, with "
      "**%s** of its complemented events below $1.00 and **%s** at or below $0.96 "
      "(n=%s complemented events of %s, 2026-08-30). Gemini BTC5M's simultaneous pair cost is "
      "**never** below $1.00 across 24,520 observations (min 1.01, median 1.24), and the independent "
      "`policy_d_port_ext` Gemini capture reproduces that. Kalshi's 15-minute series are far tighter "
      "than Gemini (median 1.02, min 1.001) but also never below $1.00 in the 2,269-row sample." % (
          fmt(E["POLYMARKET_CRYPTO_UPDOWN_L5"]["sequence"]["pct_pair_cost_below_1_00"]),
          fmt(E["POLYMARKET_CRYPTO_UPDOWN_L5"]["sequence"]["pct_pair_cost_at_or_below_0_96"]),
          E["POLYMARKET_CRYPTO_UPDOWN_L5"]["sequence"]["realized_vwap_pair_cost_usd"]["n"],
          E["POLYMARKET_CRYPTO_UPDOWN_L5"]["sequence"]["events"]))
    w("   The comparison is not like-for-like in kind: L5's figure is a *sequential realized* VWAP "
      "across a window, the Gemini/Kalshi figures are *simultaneous book* costs. A patient sequential "
      "strategy can beat a simultaneous snapshot. But Gemini's simultaneous floor of 1.01 with a "
      "**median 1.24** leaves no room for sequential patience to recover 24 cents, and only ~4 prints "
      "per 5-minute window to work with.")
    w("")
    w("2. **Gemini's binding constraint is not depth, it is price.** Touch depth is adequate "
      "(median 500 contracts on BTC15M; both sides executable at q5 68.2%). The problem is that the "
      "two legs never sum below par, and the tape is the thinnest of any candidate "
      "(median 4 prints per 300s window vs L5's own 12.6 fills/minute).")
    w("")
    w("3. **Kalshi is the strongest non-Polymarket candidate, and the only one with sourced maker "
      "economics.** `analysis/kalshi/venue_census.json` (observed 2026-09-09, 183,329 programs scanned, "
      "2,790 active) carries published liquidity-reward terms - reward_usd and target_size per series - "
      "and 83.1% of the leg-tape rows were captured while a program was active. Window length is 900s, "
      "matching L5 exactly. Per CLAUDE.md this is **sourced program terms, not credited economics**: no "
      "reward may be assumed until actually credited and source-accounted.")
    w("   Kalshi's gap is behavioural observability: prints are anonymous, so per-actor "
      "OPEN/EXTEND/COMPLETE sequences, complement reach and first-inventory-to-complement lag are "
      "**UNAVAILABLE**. Kalshi cannot currently be validated against the action-by-action standard.")
    w("")
    w("4. **Polymarket sports permits cycling but on the wrong clock, and its fee metadata is a red "
      "flag.** The 47,120-print census over 71 events (2026-08-18 to 2026-09-02) shows real per-actor "
      "two-sided behaviour, but the events are multi-day rather than 300/900s windows, which breaks "
      "L5's window-anchored cadence outright. Separately, every one of the 71 events carries "
      "`maker_fee_bps=1000` / `taker_fee_bps=1000` in its metadata. That is **reported, not confirmed "
      "as effective**. If it is the real charged rate, 10% per side destroys any pair-cycling economics "
      "and this environment is disqualified. Confirming the effective rate is a prerequisite to ranking "
      "it at all.")
    w("")
    w("5. **PMUS's 0.0050 spread is an artifact and must not be used as a headline.** See the "
      "`POLYMARKET_SPORTS_PMUS_BBO` section: 232 snapshots of **2** markets over **12 minutes**, one of "
      "which is quote-frozen for the whole sample, with every bid on an exact 0.0050 grid - so 0.0050 "
      "is one minimum tick with the quote at the touch, not a distribution. Worse for the purpose: "
      "`ask_qty` is null in 232/232 rows, so *both-sides executability cannot be computed at all*, and "
      "the impressive ~167k-share median bid depth is one-sided. This file cannot support a venue "
      "decision.")
    w("")
    w("6. **Adverse selection is UNAVAILABLE for every environment.** No corpus on disk aligns own "
      "fills with contemporaneous book state and subsequent mark. It is not estimated here. It is a "
      "genuine gap in the ranking and the highest-value next measurement, because it is the dimension "
      "most likely to reverse an apparent maker edge on Kalshi.")
    w("")
    w("## What this implies for deployment venue")
    w("")
    w("Gemini is **not** established as the right deployment venue by this scorecard, and nothing here "
      "protects it. Its one genuine structural match is window length (300s/900s short windows); on the "
      "decisive dimension - pair-cost compression - it is the worst-performing environment measured, by "
      "a wide margin, on the largest sample available (24,520 observations). That result rests on the "
      "single frozen census and is **not** independently reproduced: the second Gemini corpus on disk "
      "(`policy_d_port_ext`, 45,115 BBO rows) turns out to carry the UP leg only, with zero DOWN-leg "
      "rows, so it cannot speak to pair cost at all. Independent confirmation of Gemini\'s pair-cost "
      "floor is therefore still outstanding, and that is a caveat against the conclusion, not for it.")
    w("")
    w("The ordering the evidence supports:")
    w("")
    w("- **Polymarket crypto up/down** remains the only environment where L5-like pair-cost compression "
      "is *demonstrated* rather than hoped for - because that is where it was demonstrated, by L5. "
      "Its book/depth/spread structure is UNAVAILABLE on disk, which is the gap to close before it can "
      "be treated as a deployment target rather than a reference.")
    w("- **Kalshi 15-minute series** is the best-evidenced *executable* alternative: 900s windows, pair "
      "cost a full 22 cents tighter than Gemini at the median, two-sided depth in the low thousands of "
      "contracts, and the only sourced maker-reward programs. Blocked on behavioural observability and "
      "on a sample only ~83 minutes long for the two-sided measurements.")
    w("- **Gemini short-window** is retained only as the incumbent plumbing, not as a structural fit.")
    w("- **Polymarket sports / PMUS** is parked: wrong clock, unconfirmed 1000bps fee metadata, and a "
      "BBO sample too small and too one-sided to score.")
    w("")
    w("Next measurements, in priority order: (a) confirm the effective Polymarket sports fee rate; "
      "(b) capture a synchronous two-sided Polymarket crypto up/down book so the reference environment "
      "can be scored on the same dimensions as the candidates; (c) extend the Kalshi leg tape well "
      "beyond 83 minutes; (d) build any corpus at all that permits an adverse-selection measurement.")
    w("")
    with open(path, "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
