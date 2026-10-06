#!/usr/bin/env python3
"""
L5 VENUE MICROSTRUCTURE REQUIREMENTS -- derivation + per-venue scoring.

RESEARCH ONLY. Read-only. No venue writes, no orders, no threshold sweep.
Writes analysis/l5/VENUE_MICROSTRUCTURE.json only.

Derives the MINIMUM microstructure a venue must supply to execute the
established L5 sequential candidate, then scores every lawful venue-category
for which this repository holds measured evidence.

Central derivation note: the candidate works the pair DOWN below $1.00
SEQUENTIALLY. It tolerates pair cost > 1 after a higher-price EXTEND
(measured 1.0137) and compresses later (measured 0.9858). The venue
requirement is therefore "a sequential sub-$1 pair is REACHABLE inside the
complement-latency budget", never "a simultaneous pair < 1".
"""
import gzip, json, glob, os, statistics as st, collections, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
OUT = P("analysis", "l5", "VENUE_MICROSTRUCTURE.json")


def q(v, p):
    if not v:
        return None
    v = sorted(v)
    i = min(len(v) - 1, max(0, int(round(p * (len(v) - 1)))))
    return v[i]


def dist(v, unit=""):
    if not v:
        return {"n": 0, "unit": unit}
    v = sorted(v)
    return {"n": len(v), "unit": unit, "min": v[0], "p10": q(v, .10), "p25": q(v, .25),
            "median": st.median(v), "p75": q(v, .75), "p90": q(v, .90), "max": v[-1],
            "mean": round(sum(v) / len(v), 6)}


def print_density(path, label, window_s):
    """Gemini prints files nest the tape under `rows` per symbol/window."""
    per = {}
    for l in gzip.open(path, "rt"):
        r = json.loads(l)
        per.setdefault(r["symbol"], []).extend(r.get("rows") or [])
    tot = sum(len(v) for v in per.values())
    pw = [len(v) for v in per.values()]
    gaps, allts, amts = [], [], []
    for v in per.values():
        ts = sorted(x["timestampms"] / 1000.0 for x in v)
        gaps += [round(ts[i + 1] - ts[i], 3) for i in range(len(ts) - 1)]
        allts += [x["timestamp"] for x in v]
        amts += [float(x["amount"]) for x in v]
    return {"file": label, "windows": len(per), "prints": tot,
            "range": [iso(min(allts)), iso(max(allts))] if allts else None,
            "span_days": round((max(allts) - min(allts)) / 86400.0, 2) if allts else None,
            "prints_per_window": dist(pw, "count"),
            "prints_per_min_per_window_median": round(st.median(pw) / (window_s / 60.0), 2) if pw else None,
            "inter_print_gap": dist(gaps, "s"),
            "print_amount": dist(amts, "shares"),
            "sides": dict(collections.Counter(x["type"] for v in per.values() for x in v))}


def iso(ts):
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- REQUIREMENTS
# Every minimum is justified by a measurement on the corrected full L5 corpus
# (6,252 pre-incentive events, 336,342 fills, 2026-03-19 -> 2026-08-01).
CORPUS = {
    "label": "L5_PRE_INCENTIVE_CORRECTED_FULL_CORPUS",
    "events": 6252, "fills": 336342,
    "date_range": "2026-03-19 -> 2026-08-01",
    "sells": 2, "sells_of_fills": 760817,
    "matched_pair_pnl": 80652.0, "directional_residual": -30654.0,
    "charged_fees": -29136.0, "measured_cash": 19596.0, "per_day": 195.96,
    "matched_fraction_of_events": 0.816,
    "achieved_pair_cost": {"p25": 0.9195, "median": 0.9679, "p75": 1.0136},
    "median_interfill_gap_s": 2.0, "own_trades_per_min": 30,
    "median_fill_shares": 19.22,
    "event_outlay": {"median": 316.0, "p75": 751.0, "p90": 1131.0},
    "concurrency_peak": 2,
    "firstfill_to_first_matched_s": {"median": 49.0, "p90": 168.0},
    "higher_extend": {"pair_cost_right_after": 1.0137, "pair_cost_at_end": 0.9858,
                      "compression": -0.0280, "compression_ci": [-0.0301, -0.0259],
                      "events": 4128,
                      "matched_fraction_gain": 0.2869,
                      "matched_fraction_gain_ci": [0.2624, 0.3110],
                      "median_s_to_next_complete": 28.4},
    "window_mix_pre_incentive": {"15m": {"events": 3391, "fills": 313774},
                                 "300s": {"events": 2861, "fills": 22566}},
    "all_in_incentive_per_contract": 0.004554,
    "fee_form": "0.07*p*(1-p)*shares, taker-only",
}

REQS = [
    dict(id="R1", requirement="Two-sided fill frequency",
         minimum="Both sides executable at the candidate's median clip (>=20 shares) for a MAJORITY of in-window time; >=50% of time at q20+",
         justification="81.6% of events reach matched inventory and median fill is 19.22 shares; a venue that is one-sided most of the time cannot produce a matched majority.",
         source="corpus: 6,252 events / 336,342 fills"),
    dict(id="R2", requirement="Complement / switch latency budget",
         minimum="A sub-$1 pair must be SEQUENTIALLY reachable within ~168s of the first fill (p90), with median ~49s. A venue whose sequential sub-$1 reach median exceeds 168s FAILS.",
         justification="first-fill -> first matched increase: median 49s, p90 168s. After a higher-price EXTEND the next COMPLETE lands in a median 28.4s.",
         source="corpus; higher-extend subset n=4,128 events"),
    dict(id="R3", requirement="Trade density",
         minimum=">=30 own-trade opportunities per minute, i.e. a venue tape supporting a ~2.0s inter-fill cadence. <=5 prints/min of TOTAL venue flow cannot host it.",
         justification="median inter-fill gap 2.0s => ~30 own trades/min sustained.",
         source="corpus: 336,342 fills over 6,252 events"),
    dict(id="R4", requirement="Window duration",
         minimum=">=900s (15m) windows dominant. 300s windows are a minority habitat and cannot carry a 168s p90 complement budget plus patient compression.",
         justification="pre-incentive 15m: 3,391 events / 313,774 fills vs 300s: 2,861 events / 22,566 fills -- 93% of fills are 15m.",
         source="corpus window census"),
    dict(id="R5", requirement="Depth at L5's observed sizes",
         minimum="Touch depth >=20 shares both sides (median clip 19.22) and ladder capacity for a $316 median / $1,131 p90 event outlay.",
         justification="median fill 19.22 shares; median event outlay $316, p90 $1,131.",
         source="corpus"),
    dict(id="R6", requirement="Spread",
         minimum="Top-of-book single-leg spread <=0.02 so that a higher-price EXTEND costs ~1 tick, not ~20c. A median >=0.10 spread FAILS: it exceeds the entire 0.0280 compression the EXTEND buys.",
         justification="higher-extend compression is -0.0280 [-0.0301,-0.0259]; achieved pair-cost median 0.9679 i.e. 3.2c of total edge per pair.",
         source="corpus; n=4,128 higher-extend events"),
    dict(id="R7", requirement="Maker adverse selection",
         minimum="Unrebated maker P&L per contract must be better than -$0.0046 (the all-in incentive). Measured maker toxicity worse than that FAILS.",
         justification="L5 all-in incentive is $0.004554/contract; it cannot fund a materially negative maker edge.",
         source="corpus incentive accounting"),
    dict(id="R8", requirement="Fees",
         minimum="Taker-only fee no worse than 0.07*p*(1-p)*shares (<=1.75c/contract at p=0.5), and ZERO maker fee. A flat 10% notional fee FAILS outright.",
         justification="charged fees -$29,136 against +$80,652 matched-pair economics; fee form measured as 0.07*p*(1-p)*shares, taker-only.",
         source="corpus: $29,136 charged fees over 336,342 fills"),
    dict(id="R9", requirement="Incentives / rewards",
         minimum="Not required to be positive, but any venue relying on rebates to clear R7 must have them CREDITED and source-accounted. $0 credited => rebates contribute $0.",
         justification="L5 all-in incentive $0.004554/contract -- small; the candidate's +$19,596 measured cash is not incentive-dependent.",
         source="corpus"),
    dict(id="R10", requirement="Executable capacity",
         minimum="Sustains concurrency 2 and a $316 median event outlay without moving the book; i.e. the sub-$1 sequential reach must hold at q20-q25, not only q1.",
         justification="concurrency peak 2; median event outlay $316, p90 $1,131; median clip 19.22 shares.",
         source="corpus"),
]

RESULT = {"label": "L5_VENUE_MICROSTRUCTURE_V1",
          "generated_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
          "research_only": True,
          "nothing_here_is_realized_profit_for_our_account": True,
          "candidate_corpus": CORPUS, "requirements": REQS, "venues": {}}


# ---------------------------------------------------------- GEMINI BTC5M
def gemini_btc5m():
    v = {"venue": "Gemini", "category": "BTC 5-minute up/down", "window_s": 300}
    f = P("analysis", "gemini", "updown", "STRUCTURAL_EXPANDED.json")
    s = json.load(open(f))
    v["structural_source"] = {"file": "analysis/gemini/updown/STRUCTURAL_EXPANDED.json",
                              "independent_windows": s["independent_windows"],
                              "observations": s["observations"]}
    v["simultaneous_pair_cost_q1"] = s["pair_cost_q1"]
    v["both_sides_executable_pct"] = s["both_sides_executable_pct"]
    v["pair_cost_at_depth"] = {k: {"median": d["median"], "min": d["min"], "n": d["n"]}
                               for k, d in s["pair_cost_at_depth"].items()}
    v["complement_reach_any_price_pct"] = {
        "UNDERDOG": s["UNDERDOG"]["complement"]["q1"]["reach_pct"],
        "FAVORED": s["FAVORED"]["complement"]["q1"]["reach_pct"]}
    v["complement_seconds_to_first_median"] = {
        "UNDERDOG": s["UNDERDOG"]["complement"]["q1"]["seconds_to_first"]["median"],
        "FAVORED": s["FAVORED"]["complement"]["q1"]["seconds_to_first"]["median"]}
    # The load-bearing SEQUENTIAL sub-$1 result lives in the P0 forensic.
    v["sequential_sub1_survival"] = {
        "source": "analysis/gemini/updown/P0_COMPLEMENT_FORENSIC.md",
        "independent_windows": 17,
        "note": "MARKETABLE sub-$1 complement while holding a causal T+15 first leg, 1Hz book, 0.197s measured latency applied.",
        "P_within_1s": 0.0, "P_within_5s": 0.0, "P_within_10s": 0.0,
        "P_within_30s": 0.0, "P_within_60s": 0.0, "P_within_120s": 0.118,
        "P_through_settlement": 0.471,
        "first_lock_time_s": {"min": 71.0, "median": 174.6, "max": 249.0},
        "size_invariance": "identical at q1/q5/q10/q25 (8/17 windows, 532.1 open seconds at every size)",
        "required_completion_rate_for_positive_EV": 0.712,
        "observed_completion_rate": 0.471,
        "shortfall_pp": 24.1}
    # spread + trade density, recomputed from raw tape
    sp = []
    nrows = 0
    tmin = tmax = None
    for l in gzip.open(P("data", "transfer_backtest", "gemini_bbo_btc05m.jsonl.gz"), "rt"):
        r = json.loads(l)
        nrows += 1
        t = r.get("t")
        if t:
            tmin = t if tmin is None or t < tmin else tmin
            tmax = t if tmax is None or t > tmax else tmax
        try:
            b, a = float(r["bid"]), float(r["ask"])
        except (TypeError, ValueError, KeyError):
            continue
        if b > 0 and a > 0:
            sp.append(round(a - b, 4))
    v["raw_bbo_tape"] = {"file": "data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz",
                         "rows": nrows,
                         "range": [iso(tmin), iso(tmax)] if tmin else None,
                         "single_leg_spread": dist(sp, "$")}
    v["trade_density"] = print_density(P("data", "transfer_backtest", "gemini_prints_btc05m.jsonl.gz"),
                                       "data/transfer_backtest/gemini_prints_btc05m.jsonl.gz", 300)
    v["trade_density"]["L5_comparison"] = {
        "L5_fills_per_event": round(336342 / 6252, 1),
        "venue_prints_per_window_median": v["trade_density"]["prints_per_window"]["median"],
        "statement": ("L5 averages 53.8 own fills per event. The ENTIRE venue-wide flow of a Gemini BTC5M "
                      "window is a median of 5 prints. To trade at L5's cadence our account would have to BE "
                      "roughly 11x the whole venue's print count, and ~30x its per-minute flow. "
                      "Note the median inter-print gap is 1.95s, superficially close to L5's 2.0s inter-fill "
                      "gap, but that is burst clustering (p25 0.04s, p75 21.0s) inside a window holding only "
                      "5 prints in total -- it is NOT a sustainable 30 trades/min.")}
    v["maker_adverse_selection"] = {
        "source": "analysis/gemini/updown/MAKER_TOXICITY.json via analysis/kalshi/KALSHI_ECONOMICS_GATE.md",
        "unrebated_maker_pnl_per_contract": -0.2380,
        "window_bootstrap_ci": [-0.2831, -0.1935],
        "evidenced_maker_fills": 10698, "windows": 46,
        "non_toxic_states": 0}
    v["scores"] = {
        "R1_two_sided_fill_frequency": ["FAIL", "both-sides executable falls to 68.2% at q5 and 61.7% at q25 (n=24,520 obs / 61 windows); but the binding fact is that a sub-$1 pair is available in only 8/17 windows, so matched inventory cannot reach 81.6%"],
        "R2_complement_switch_latency": ["FAIL", "sequential sub-$1 reach is 0.0% within 60s and 11.8% within 120s; median first-lock 174.6s vs the 168s p90 budget and 49s median (n=17 windows)"],
        "R3_trade_density": ["FAIL", "~4 prints per 5-minute window venue-wide (~0.8/min) against a >=30 own-trades/min requirement -- short by ~1.5 orders of magnitude"],
        "R4_window_duration": ["FAIL", "300s windows; 93% of L5's pre-incentive fills are in 15m windows and a 300s window cannot hold a 168s p90 complement budget plus compression"],
        "R5_depth": ["PASS", "sub-$1 availability is size-invariant across q1/q5/q10/q25 (8/17 windows, 532.1 open seconds at every size); median touch 500 on the 15m tape"],
        "R6_spread": ["PASS", "CORRECTION: recomputed single-leg top-of-book spread from the raw tape is median 0.02 (p90 0.02, n=61,497 rows, 2026-08-30 -> 2026-09-02), which MEETS <=0.02. The widely-quoted 0.14 is STRUCTURAL_EXPANDED's own field `spread_proxy_q1`, unit '$ over 1.00' -- that is the TWO-LEG pair-cost excess (pair_cost - 1.00), not a single-leg spread. This dimension passes; the venue fails elsewhere."],
        "R7_maker_adverse_selection": ["FAIL", "unrebated maker P&L -$0.2380/contract [-0.2831,-0.1935] on 10,698 evidenced maker fills / 46 windows, against a -$0.0046 tolerance -- 52x worse; 0 non-toxic states in every bin of every pre-fill feature"],
        "R8_fees": ["UNAVAILABLE", "no per-fill charged-fee schedule sourced for our account on this venue in this corpus"],
        "R9_incentives": ["UNAVAILABLE", "no credited rebate or reward evidence for our account at Gemini BTC5M"],
        "R10_executable_capacity": ["FAIL", "depth is adequate (R5) but capacity is moot: the sequential sub-$1 pair exists in only 47.1% of windows and only after ~175s, so a $316 median outlay at concurrency 2 has nothing to execute into"]}
    v["binding_requirement"] = "R3 (trade density) and R2 (complement/switch latency), jointly. R3 is the more absolute of the two: venue-wide flow is a median 5 prints per 5-minute window against a >=30 own-trades/min requirement, so the cadence is unreachable by ~30x no matter how the policy is tuned. R2 then independently blocks it: the sub-$1 pair is NEVER reachable inside 60s, median first lock 174.6s, so 71.2% completion is required against 47.1% available. Depth (R5) and single-leg spread (R6) are explicitly NOT the constraints."
    v["can_work_pair_down_below_1_sequentially"] = "PARTIALLY, BUT TOO SLOWLY -- yes in 8/17 windows (47.1%), median $0.93 best lock, but 0% inside 60s and median 174.6s, outside the 168s p90 budget. Simultaneous pair is NEVER below $1.00 (min 1.01, median 1.24, n=24,520)."
    return v


# ---------------------------------------------------------- GEMINI BTC15M
def gemini_btc15m():
    v = {"venue": "Gemini", "category": "BTC 15-minute up/down", "window_s": 900}
    sp, touch, syms = [], [], collections.Counter()
    tmin = tmax = None
    n = 0
    legs = collections.Counter()
    for l in gzip.open(P("data", "transfer_backtest", "gemini_bbo_btc15m.jsonl.gz"), "rt"):
        r = json.loads(l)
        n += 1
        s = r.get("symbol", "")
        syms[s] += 1
        legs["UP" if s.endswith("-UP") else ("DOWN" if s.endswith("-DOWN") else "other")] += 1
        t = r.get("t")
        if t:
            tmin = t if tmin is None or t < tmin else tmin
            tmax = t if tmax is None or t > tmax else tmax
        try:
            b, a = float(r["bid"]), float(r["ask"])
            bs, as_ = float(r["bid_sz"]), float(r["ask_sz"])
        except (TypeError, ValueError, KeyError):
            continue
        if b > 0 and a > 0:
            sp.append(round(a - b, 4))
            touch.append(min(bs, as_))
    v["raw_bbo_tape"] = {"file": "data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz",
                         "rows": n, "distinct_symbols": len(syms),
                         "leg_census": dict(legs),
                         "range": [iso(tmin), iso(tmax)] if tmin else None,
                         "span_hours": round((tmax - tmin) / 3600, 2) if tmin else None,
                         "single_leg_spread": dist(sp, "$"),
                         "touch_min_side": dist(touch, "shares")}
    # Can we form a pair at all? need both UP and DOWN for the same window-stamp
    books = collections.defaultdict(dict)
    for l in gzip.open(P("data", "transfer_backtest", "gemini_bbo_btc15m.jsonl.gz"), "rt"):
        r = json.loads(l)
        s = r.get("symbol", "")
        if "-" not in s:
            continue
        base, leg = s.rsplit("-", 1)
        if leg not in ("UP", "DOWN"):
            continue
        try:
            a = float(r["ask"])
        except (TypeError, ValueError, KeyError):
            continue
        if a <= 0:
            continue
        books[(base, round(float(r["t"])))][leg] = a
    pairs = [d["UP"] + d["DOWN"] for d in books.values() if "UP" in d and "DOWN" in d]
    v["simultaneous_pair_cost"] = dist(pairs, "$")
    v["simultaneous_pair_cost"]["sub_1_00"] = sum(1 for x in pairs if x < 1.0)
    v["simultaneous_pair_cost"]["le_0_96"] = sum(1 for x in pairs if x <= 0.96)
    v["simultaneous_pair_cost"]["pairable_second_buckets"] = len(pairs)
    v["pairability_note"] = ("CORRECTION TO PRIOR FRAMING: this tape carries the UP LEG ONLY "
                             "(leg census %s; 0 DOWN rows in 23,318). Pair cost is therefore NOT computable "
                             "from it, exactly the same defect as data/policy_d_port_ext (46,692 Gemini rows, "
                             "45,931 UP-suffixed, 0 DOWN -- prior claim VERIFIED). The reported "
                             "simultaneous_pair_cost has n=0 for that reason, not because pairs were expensive. "
                             "Separately, data/transfer_backtest/gemini_prints_15m_tail.jsonl.gz is mislabelled: "
                             "all 46 of its symbols are BTC05M, not BTC15M." % dict(legs))
    v["scores"] = {
        "R1_two_sided_fill_frequency": ["UNAVAILABLE", "BBO-only tape, UP leg only, no fills and no per-actor identity; two-sided FILL frequency cannot be measured, and with 0 DOWN rows not even two-sided QUOTE presence can be measured"],
        "R2_complement_switch_latency": ["UNAVAILABLE", "STRUCTURALLY unmeasurable from this file: the complement (DOWN) leg is absent from all 23,318 rows. No first-leg/complement episode study exists for BTC15M; the 17-window sequential forensic was run on BTC5M only"],
        "R3_trade_density": ["UNAVAILABLE", "the only candidate prints file, gemini_prints_15m_tail.jsonl.gz, is mislabelled -- all 46 symbols in it are BTC05M (235 prints, 2.8 days). No BTC15M print tape exists on disk"],
        "R4_window_duration": ["PASS", "900s windows, matching the habitat holding 93% of L5's pre-incentive fills (3,391 events / 313,774 fills)"],
        "R5_depth": ["PASS", "median touch 500 shares (min side) vs a 19.22-share median clip -- 26x headroom"],
        "R6_spread": ["PASS", "median single-leg spread 0.02, meeting the <=0.02 requirement"],
        "R7_maker_adverse_selection": ["UNAVAILABLE", "no maker-fill evidence for BTC15M; the -$0.2380/contract toxicity figure is a BTC5M measurement and transfer is UNMEASURED"],
        "R8_fees": ["UNAVAILABLE", "no sourced per-fill fee schedule for our account"],
        "R9_incentives": ["UNAVAILABLE", "no credited rebate/reward evidence"],
        "R10_executable_capacity": ["UNAVAILABLE", "depth is adequate but sequential sub-$1 reach at q20-q25 is unmeasured for this venue"]}
    v["sample_caveat"] = "Aug 2026 HISTORICAL, 23,318 rows, single contiguous slice -- not a live or current reading."
    v["binding_requirement"] = ("R2 (complement/switch latency) is the gating unknown, and it is not merely unmeasured but "
                                "UNMEASURABLE FROM EXISTING DATA: the DOWN leg is absent from all 23,318 rows. "
                                "BTC15M passes every dimension this tape CAN measure (R4 window 900s, R5 depth median touch 500, "
                                "R6 spread median 0.02) and is the only venue-category on disk that passes all three -- but 7 of 10 "
                                "requirements are UNAVAILABLE, including both mechanism requirements, so it is UNDETERMINED, not passing. "
                                "The single highest-value next measurement in this whole scoring is a two-leg (UP+DOWN) BTC15M book "
                                "and print recorder.")
    v["can_work_pair_down_below_1_sequentially"] = ("UNKNOWN -- and not measurable from this file at all, because the DOWN leg is "
                                                   "absent from 23,318/23,318 rows. No pair cost, simultaneous or sequential, can be "
                                                   "computed. Sequential reach requires a two-leg episode study that does not exist for this venue.")
    return v


# ---------------------------------------------------------- KALSHI 15M
def kalshi15m():
    v = {"venue": "Kalshi", "category": "15-minute strike markets (8 series)", "window_s": 900}
    rows = [json.loads(l) for l in gzip.open(P("analysis", "kalshi", "leg_tape.jsonl.gz"), "rt")]
    ts = [r["t"] for r in rows]
    pc = [r["pair_cost"] for r in rows]
    spr = [round((1.0 - r["no_bid"]) - r["yes_bid"], 4) for r in rows]
    bys = collections.defaultdict(list)
    for r in rows:
        bys[r["series"]].append(r["pair_cost"])
    v["leg_tape"] = {
        "file": "analysis/kalshi/leg_tape.jsonl.gz",
        "rows": len(rows), "tickers": len(set(r["ticker"] for r in rows)),
        "series": len(bys),
        "range": [iso(min(ts)), iso(max(ts))],
        "span_minutes": round((max(ts) - min(ts)) / 60, 1),
        "LOAD_BEARING_CAVEAT": "83 minutes of ONE day (2026-09-09). No fills, no maker P&L, no credited rewards. Point-in-time census only; cannot support any per-contract realized number, and program presence on an 83-minute slice is not evidence of program persistence.",
        "pair_cost_definition": "buy-both-asks = (1-no_bid)+(1-yes_bid) = 2 - bidsum; VERIFIED against row 1 (yes_bid 0.21 + no_bid 0.78 -> pair_cost 1.01)",
        "pair_cost": dist(pc, "$"),
        "sub_1_00": sum(1 for x in pc if x < 1.0),
        "le_0_96": sum(1 for x in pc if x <= 0.96),
        "per_series": {k: {"n": len(x), "min": min(x), "median": st.median(x),
                           "sub_1_00": sum(1 for y in x if y < 1.0)} for k, x in sorted(bys.items())},
        "single_leg_spread": dist(spr, "$"),
        "touch_yes": dist([r["touch_yes"] for r in rows], "$ notional"),
        "touch_no": dist([r["touch_no"] for r in rows], "$ notional"),
    }
    # independent corroboration from policy_d_port_ext
    pairs = []
    tk = set()
    t2min = t2max = None
    for f in sorted(glob.glob(P("data", "policy_d_port_ext", "*.jsonl"))):
        for l in open(f):
            try:
                r = json.loads(l)
            except Exception:
                continue
            if r.get("venue") != "kalshi" or r.get("kind") != "bbo":
                continue
            yb, nb = r.get("yes_bids"), r.get("no_bids")
            if not yb or not nb:
                continue
            bidsum = max(float(x[0]) for x in yb) + max(float(x[0]) for x in nb)
            pairs.append(round(2.0 - bidsum, 4))
            tk.add(r.get("market"))
            t = r.get("t")
            if t:
                t2min = t if t2min is None or t < t2min else t2min
                t2max = t if t2max is None or t > t2max else t2max
    v["independent_corroboration_policy_d"] = {
        "files": "data/policy_d_port_ext/*.jsonl (17 files)",
        "kalshi_bbo_rows_with_both_legs": len(pairs), "tickers": len(tk),
        "range": [iso(t2min), iso(t2max)] if t2min else None,
        "span_hours": round((t2max - t2min) / 3600, 2) if t2min else None,
        "buy_pair_cost": dist(pairs, "$"),
        "sub_1_00": sum(1 for x in pairs if x < 1.0),
        "le_0_96": sum(1 for x in pairs if x <= 0.96),
        "verdict": "CORROBORATES the leg tape on an INDEPENDENT day and a different series set: buy-pair cost < 1.00 in 0 of %d rows, min %.4f." % (len(pairs), min(pairs) if pairs else float("nan")),
    }
    v["economics_gate"] = {
        "source": "analysis/kalshi/KALSHI_ECONOMICS_GATE.md",
        "credited_maker_compensation_per_contract": 0.0,
        "breakeven_required_per_contract": 0.2380,
        "program_rows": "1,886/2,269 = 83.12% under an active program, BUT 0 of 383 BTC/ETH rows (the only series with any fill evidence) are under any program; 100% of program coverage sits in series we have no fill evidence for",
        "pool": "$20.00 per 15-minute period, discount_factor_bps 5000",
        "terms_sourcing": "every Kalshi host 403-blocked by egress proxy this session; SOURCED_TERMS figures are inherited from 2026-09-09 / 2026-09-23 pulls and are stale, not live",
    }
    v["scores"] = {
        "R1_two_sided_fill_frequency": ["UNAVAILABLE", "prints are ANONYMOUS on Kalshi, so per-actor two-sided fill frequency cannot be computed. Quote-presence proxy: both-sides q5 91.9%. This is quote presence, not fill frequency."],
        "R2_complement_switch_latency": ["UNAVAILABLE", "anonymous prints make per-actor complement reach structurally UNMEASURABLE. No sequential episode study is possible from this tape."],
        "R3_trade_density": ["UNAVAILABLE", "the leg tape contains book snapshots only -- no fills and no prints -- so print rate is not derivable"],
        "R4_window_duration": ["PASS", "900s windows, matching L5's dominant 15m habitat"],
        "R5_depth": ["PASS", "median touch $136 yes / $120 no notional and band5 $1,570/$855 -- adequate for a 19.22-share clip and a $316 median event outlay (n=2,269, 83 minutes)"],
        "R6_spread": ["PASS", "median single-leg spread 0.02 (p25 0.01, p90 0.09), meeting <=0.02 (n=2,269, 83-minute sample)"],
        "R7_maker_adverse_selection": ["UNAVAILABLE", "no per-contract maker adverse-selection measurement exists for Kalshi. The -$0.2380 hurdle is a GEMINI measurement; applying it to Kalshi assumes comparable toxicity and that assumption is UNMEASURED."],
        "R8_fees": ["UNAVAILABLE", "no sourced live fee schedule this session (all Kalshi hosts 403-blocked); inherited terms are stale"],
        "R9_incentives": ["FAIL", "$0.00/contract maker compensation actually CREDITED, against a $0.2380 break-even. 0 of 383 rows in the two series with fill evidence (KXBTC15M, KXETH15M) are under any reward program."],
        "R10_executable_capacity": ["FAIL", "capacity is moot: a sub-$1 pair is never reachable (see below), so there is nothing to deploy a $316 outlay into"]}
    v["binding_requirement"] = ("R2/R10 via a hard pair-cost floor: buy-pair cost < 1.00 in 0 of 2,269 leg-tape rows "
                                "AND 0 of 2,409 independent policy_d rows, across all eight series, with a MINIMUM of 1.001. "
                                "The candidate's mechanism -- work the pair down below $1.00 -- has no path here. "
                                "Secondarily R2 is permanently UNMEASURABLE because prints are anonymous.")
    v["can_work_pair_down_below_1_sequentially"] = ("NO. The simultaneous pair never goes below $1.00 (min 1.001, median 1.02, "
                                                    "0/2,269 and 0/2,409 on two independent samples), and because prints are ANONYMOUS "
                                                    "the sequential question cannot even be posed from this data. "
                                                    "A venue where the observed pair MINIMUM is 1.001 offers no compression room: "
                                                    "the candidate's achieved median pair cost is 0.9679, which is 4.1c below Kalshi's best-ever observed pair.")
    return v


# ---------------------------------------------------------- PMUS SPORTS (live BBO sample)
def pmus_live():
    v = {"venue": "Polymarket US (PMUS)", "category": "sports moneyline -- live BBO sample"}
    rows = [json.loads(l) for l in open(P("data", "transfer_backtest", "pmus_live_sample.jsonl"))]
    nullask = sum(1 for r in rows if r.get("ask_qty") in (None, "", "null"))
    ts = sorted(r["t"] for r in rows)
    v["sample"] = {"file": "data/transfer_backtest/pmus_live_sample.jsonl",
                   "rows": len(rows),
                   "distinct_markets": len(set(r["slug"] for r in rows)),
                   "range": [ts[0], ts[-1]],
                   "ask_qty_null_rows": nullask,
                   "LOAD_BEARING_CAVEAT": "232 rows, 2 markets, ~12 minutes, ask_qty null in %d/%d. With no ask size there is no executable depth on the buy side at all, so this file cannot score depth, capacity or two-sided fill frequency." % (nullask, len(rows))}
    v["scores"] = {k: ["UNAVAILABLE", "232-row / 2-market / 12-minute sample with ask_qty null in 232/232 -- GATED OUT; no dimension is measurable"]
                   for k in ["R1_two_sided_fill_frequency", "R2_complement_switch_latency", "R3_trade_density",
                             "R5_depth", "R6_spread", "R7_maker_adverse_selection", "R8_fees",
                             "R9_incentives", "R10_executable_capacity"]}
    v["scores"]["R4_window_duration"] = ["FAIL", "sports moneyline markets are multi-hour event-duration, not 900s windows; there is no window to complete a pair inside"]
    v["binding_requirement"] = "Evidence gate, not a venue property: ask_qty is null in 232/232 rows so the buy side has no measurable depth. Nothing about this venue-category is established either way by this file."
    v["can_work_pair_down_below_1_sequentially"] = "UNKNOWN -- ask_qty null in 232/232 rows means pair BUY cost is not computable at all from this file."
    return v


# ---------------------------------------------------------- POLYMARKET SPORTS (trade tape, per-actor)
def polymarket_sports():
    """The one corpus on disk with PER-ACTOR identity (proxyWallet) on both
    outcomes -- so sequential complement reach IS measurable here."""
    v = {"venue": "Polymarket", "category": "sports trade tape (mlb/tennis/epl/soccer/wnba)"}
    events = [json.loads(l) for l in gzip.open(P("data", "transfer_backtest", "sports_trades.jsonl.gz"), "rt")]
    tags = collections.Counter(e.get("tag") for e in events)
    ntr = sum(len(e.get("trades") or []) for e in events)
    allts = [x["timestamp"] for e in events for x in (e.get("trades") or []) if x.get("timestamp")]
    sides = collections.Counter(x["side"] for e in events for x in (e.get("trades") or []))
    fees = set((e.get("maker_fee_bps"), e.get("taker_fee_bps")) for e in events)

    # Per-actor sequential complement reach: for each wallet in each event,
    # did they BUY both outcomes, and how long between first leg and first
    # complement? Did the running pair cost ever get below $1.00?
    reach_s, pair_at_complement, actors_both, actors_total = [], [], 0, 0
    sub1 = 0
    per_actor_trades = []
    for e in events:
        byw = collections.defaultdict(list)
        for x in (e.get("trades") or []):
            if x.get("side") != "BUY":
                continue
            byw[x["proxyWallet"]].append(x)
        for w, tr in byw.items():
            actors_total += 1
            per_actor_trades.append(len(tr))
            tr.sort(key=lambda z: z["timestamp"])
            outs = set()
            first_ts, first_out = tr[0]["timestamp"], tr[0]["outcomeIndex"]
            comp_ts = None
            best = {}
            for x in tr:
                oi = x["outcomeIndex"]
                best[oi] = min(best.get(oi, 9), float(x["price"]))
                outs.add(oi)
                if comp_ts is None and oi != first_out:
                    comp_ts = x["timestamp"]
            if len(outs) >= 2:
                actors_both += 1
                if comp_ts is not None:
                    reach_s.append(comp_ts - first_ts)
                pc = sum(best.values())
                pair_at_complement.append(pc)
                if pc < 1.0:
                    sub1 += 1
    v["corpus"] = {"file": "data/transfer_backtest/sports_trades.jsonl.gz",
                   "events": len(events), "trades": ntr,
                   "tags": dict(tags),
                   "range": [iso(min(allts)), iso(max(allts))],
                   "sides": dict(sides),
                   "fee_bps_maker_taker": sorted(str(f) for f in fees),
                   "note": "This is the ONLY corpus on disk carrying per-actor identity (proxyWallet) on BOTH outcomes, so sequential complement reach is directly measurable."}
    v["per_actor_sequential_pair"] = {
        "actors_event_pairs_with_a_buy": actors_total,
        "actors_buying_BOTH_outcomes": actors_both,
        "both_outcome_rate": round(actors_both / actors_total, 4) if actors_total else None,
        "seconds_first_leg_to_first_complement": dist(reach_s, "s"),
        "best_achievable_pair_cost_per_completing_actor": dist(pair_at_complement, "$"),
        "completing_actors_with_sub_1_00_pair": sub1,
        "sub_1_00_rate_of_completers": round(sub1 / actors_both, 4) if actors_both else None,
        "trades_per_actor_event": dist(per_actor_trades, "count"),
    }
    v["scores"] = {
        "R1_two_sided_fill_frequency": ["FAIL", "only %d of %d actor-events bought both outcomes (%.2f%%); the observed population does not run two-sided inventory at all, against an 81.6%% matched requirement" % (actors_both, actors_total, 100.0 * actors_both / actors_total if actors_total else 0)],
        "R2_complement_switch_latency": ["FAIL", "median first-leg-to-complement is %s s among the few completing actors, against a 168s p90 budget" % (st.median(reach_s) if reach_s else "UNAVAILABLE")],
        "R3_trade_density": ["FAIL", "%d trades across %d events over 15 days venue-wide; nowhere near a 2.0s own inter-fill cadence in any single market" % (ntr, len(events))],
        "R4_window_duration": ["FAIL", "event-duration sports markets (hours to days), not 900s windows"],
        "R5_depth": ["UNAVAILABLE", "trade tape only -- no book, so touch depth is not derivable"],
        "R6_spread": ["UNAVAILABLE", "trade tape only -- no bid/ask, so spread is not derivable"],
        "R7_maker_adverse_selection": ["UNAVAILABLE", "no own-maker fills and no book to mark against"],
        "R8_fees": ["FAIL", "maker_fee_bps 1000 and taker_fee_bps 1000 on all %d events -- a 10%% notional fee on BOTH sides, against a requirement of ZERO maker fee and <=1.75c/contract taker. This alone exceeds the candidate's entire 3.2c pair edge by ~3x." % len(events)],
        "R9_incentives": ["UNAVAILABLE", "no credited rebate or reward evidence for our account"],
        "R10_executable_capacity": ["UNAVAILABLE", "no book; capacity not derivable"]}
    v["binding_requirement"] = ("R8 (fees) is the hard, unambiguous blocker: 1000 bps maker AND 1000 bps taker on all 71 events. "
                                "A 10% notional fee on both legs destroys a strategy whose total measured pair edge is 3.2c. "
                                "R4 (event-duration windows) and R3 (density) also fail independently.")
    v["can_work_pair_down_below_1_sequentially"] = ("TECHNICALLY YES but ECONOMICALLY NO -- %d of %d completing actors reached a sub-$1.00 "
                                                    "best-pair, but at 1000 bps on both legs the fee alone (~10c on a $1 pair) swamps the "
                                                    "3.2c compression the candidate earns." % (sub1, actors_both))
    return v


# ------------------------------------------- POLYMARKET CRYPTO UP/DOWN (reference)
def polymarket_crypto_updown():
    """The environment L5 ACTUALLY operated in. Measured directly from L5's own
    fill tape (data/l5/raw/activity*.jsonl*), recomputed this session rather
    than inherited.

    CIRCULARITY NOTICE, load-bearing: the requirements R1-R10 were DERIVED from
    L5's behaviour on this venue. Scoring it against them is a provenance and
    consistency check, NOT independent validation. It passes the behavioural
    dimensions by construction. What is NOT circular, and is the operative
    finding, is which dimensions remain UNAVAILABLE here: we hold L5's own
    fills but NO book, so spread, depth, maker adverse selection and our own
    fees/incentives are unmeasured on the one venue where the mechanism is
    demonstrated.
    """
    v = {"venue": "Polymarket", "category": "crypto up/down (BTC/ETH 5m + 15m)",
         "window_s": [300, 900], "role": "REFERENCE -- the venue L5 operated in"}
    v["recomputed_this_session"] = {
        "source": "data/l5/raw/activity.jsonl + data/l5/raw/activity_gap.jsonl.gz (paged API payloads, flattened and deduped on (txhash, asset, ts, size, price, side))",
        "deduped_fills_all_time": 763966,
        "pre_incentive_BUY_fills": 339488,
        "pre_incentive_range": "2026-03-19T10:09:37Z -> 2026-07-31T23:57:30Z",
        "sides_all_time": {"BUY": 763964, "SELL": 2},
        "families": {"btc-updown-15m": 63569, "btc-updown-5m": 6229,
                     "eth-updown-5m": 2069, "eth-updown-15m": 117,
                     "_note": "family census from activity.jsonl slice only"},
        "events": 6252,
        "matched_events": 5100, "matched_pct": 81.6,
        "interfill_gap_s": {"n": 333236, "p25": 0, "median": 2.0, "p75": 9, "p90": 22},
        "firstfill_to_first_complement_s": {"n": 5100, "p10": 6, "p25": 16, "median": 40.0, "p75": 87, "p90": 154, "max": 666},
        "realized_VWAP_pair_cost": {"n": 5100, "p10": 0.8492, "p25": 0.9195, "median": 0.9678, "p75": 1.0136, "p90": 1.0933},
        "pair_sub_1_00": 3518, "pair_sub_1_00_pct": 69.0, "pair_le_0_96": 2309,
        "fill_size_shares": {"n": 339488, "p10": 2.95, "p25": 6.67, "median": 19.16, "p75": 30.0, "p90": 30.0},
        "event_outlay_usd": {"n": 6252, "median": 317.90, "p75": 749.93, "p90": 1125.73},
        "fills_per_event": {"n": 6252, "p25": 6, "median": 33.0, "p75": 92, "p90": 136},
        "window_mix": {"15m": 3391, "5m": 2861},
        "reconciliation": ("Independently reproduces the stated corpus: 6,252 events, 81.6% matched, "
                           "inter-fill median 2.0s, pair cost p25 0.9195 / median 0.9679 / p75 1.0136, "
                           "median fill 19.16 vs 19.22 shares, outlay median $317.90 vs $316, "
                           "window mix 3,391/2,861, 2 SELLs. The one definitional gap: "
                           "first-complement median 40s / p90 154s here vs the stated 49s / 168s for "
                           "'first matched INCREASE' -- a slightly different event, same order."),
    }
    v["scores"] = {
        "R1_two_sided_fill_frequency": ["PASS", "81.6% of 6,252 events reach matched inventory (5,100), recomputed from L5's own fills, 2026-03-19 -> 2026-07-31"],
        "R2_complement_switch_latency": ["PASS", "first-fill to first complement median 40s, p90 154s (n=5,100) -- inside the 49s/168s budget. A sub-$1 realized pair is reached in 69.0% of matched events (3,518/5,100) and <=0.96 in 2,309"],
        "R3_trade_density": ["PASS", "median inter-fill gap 2.0s over 333,236 gaps; median 33 own fills per event, p90 136"],
        "R4_window_duration": ["PASS", "both 300s and 900s habitats present; 15m carries 3,391 of 6,252 events and 93% of fills"],
        "R5_depth": ["UNAVAILABLE", "we hold L5's OWN fills only -- no book snapshots on disk for this venue, so touch depth is not derivable. Observed clips reach 30 shares (p75 and p90 both 30.0, suggesting a size cap rather than a depth limit)"],
        "R6_spread": ["UNAVAILABLE", "no book on disk; spread not derivable from a fill tape"],
        "R7_maker_adverse_selection": ["UNAVAILABLE", "no own-maker fills for OUR account and no book to mark against. L5's maker/taker split is not labelled in the public activity feed"],
        "R8_fees": ["PASS", "measured fee form 0.07*p*(1-p)*shares, TAKER-ONLY -- i.e. zero maker fee and <=1.75c/contract at p=0.5. Charged fees -$29,136 against +$80,652 matched-pair economics"],
        "R9_incentives": ["PASS", "all-in incentive measured at $0.004554/contract; the candidate's +$19,596 measured cash does not depend on it"],
        "R10_executable_capacity": ["PASS (OBSERVED, NOT CAPACITY)", "L5 sustained concurrency peak 2 and a $317.90 median / $1,125.73 p90 event outlay. This is L5's realized notional, NOT a book-depth capacity measure -- whether OUR account can take the same size without moving the book is UNAVAILABLE"]}
    v["binding_requirement"] = ("None of R1-R4 or R8-R10 binds -- this is the one environment where the mechanism is "
                                "demonstrated end to end. The binding GAP is evidentiary, not microstructural: "
                                "R5 (depth), R6 (spread) and R7 (maker adverse selection) are all UNAVAILABLE because "
                                "no book tape for this venue exists on disk. We know the strategy worked here for L5; "
                                "we cannot yet show OUR account could execute it here. "
                                "Also note the circularity: R1-R10 were derived from this very corpus.")
    v["can_work_pair_down_below_1_sequentially"] = ("YES -- demonstrated, and this is the only venue on disk where it is. "
                                                    "Realized VWAP pair cost goes below $1.00 in 3,518 of 5,100 matched events (69.0%) "
                                                    "and to <=0.96 in 2,309, with a median of 0.9679, having tolerated p75 1.0136 "
                                                    "en route. The higher-price EXTEND mechanism is visible: pair cost 1.0137 right "
                                                    "after one, 0.9858 at the end, a -0.0280 [-0.0301,-0.0259] compression on 4,128 events.")
    return v


RESULT["venues"]["polymarket_crypto_updown_REFERENCE"] = polymarket_crypto_updown()
RESULT["venues"]["gemini_btc5m"] = gemini_btc5m()
RESULT["venues"]["gemini_btc15m"] = gemini_btc15m()
RESULT["venues"]["kalshi_15m"] = kalshi15m()
RESULT["venues"]["pmus_sports_live_bbo"] = pmus_live()
RESULT["venues"]["polymarket_sports_trades"] = polymarket_sports()

# ------------------------------------------------------------------- VERDICT
tally = {}
for k, v in RESULT["venues"].items():
    c = collections.Counter(x[0] for x in v["scores"].values())
    tally[k] = dict(c)
RESULT["score_tally"] = tally
RESULT["verdict"] = {
    "any_venue_passes": "ONLY THE REFERENCE VENUE, AND CIRCULARLY",
    "statement": ("Exactly ONE venue-category passes the mechanism requirements -- Polymarket crypto up/down, "
                  "the venue L5 actually operated in -- and it passes CIRCULARLY, because R1-R10 were derived "
                  "from L5's behaviour on it. Even there, three requirements are UNAVAILABLE (R5 depth, "
                  "R6 spread, R7 maker adverse selection) because no book tape for that venue is on disk: "
                  "we can show the strategy worked for L5 there, not that OUR account can execute it. "
                  "NO INDEPENDENT venue passes. Among the alternatives: Gemini BTC5M fails 6 of 10, with "
                  "trade density short by ~30x and the sequential sub-$1 pair never reachable inside 60s; "
                  "Kalshi 15m has a hard pair-cost floor (0 sub-$1 in 2,269 AND in 2,409 independent rows, "
                  "minimum 1.001) and $0 credited maker compensation; Polymarket sports is killed outright by "
                  "1000 bps maker AND taker fees; the PMUS BBO file is an evidence gate, not a venue finding. "
                  "Gemini BTC15M is the only alternative that passes every dimension its data CAN measure "
                  "(R4 window, R5 depth, R6 spread) -- but 7 of 10 are UNAVAILABLE and, decisively, its tape "
                  "carries the UP leg only in 23,318/23,318 rows, so both mechanism requirements are not merely "
                  "unmeasured but UNMEASURABLE from existing data. It is UNDETERMINED, not passing, and it is "
                  "the single highest-value place to point a new two-leg recorder."),
    "binding_requirement_per_venue": {k: v["binding_requirement"] for k, v in RESULT["venues"].items()},
    "nothing_is_realized_profit_for_our_account": True,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(RESULT, open(OUT, "w"), indent=2, default=str)
print("wrote", OUT)
for k, t in tally.items():
    print(" ", k, t)
