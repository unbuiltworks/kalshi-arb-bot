#!/usr/bin/env python3
"""Q7 CAPACITY + Q8 COUNTERFACTUAL CLONE DIVERGENCE AUDIT.  RESEARCH ONLY.

Reads L5's pre-incentive corpus and answers two questions:

Q7  How much capital did L5 actually DEPLOY per event, what did it return per
    deployed dollar, and -- working backward from $480/day with the measured
    mechanism -- which lever would have to move, and by how much.

Q8  Replay our CURRENT FROZEN CLONE's rules against L5's ACTUAL pre-incentive
    sequences and measure, per rule, how much real L5 activity it suppresses.

The clone rules are LOADED from the frozen policy, never restated here:
    analysis/gemini/info_edge/btc15m_shadow_policy.py   (via ci15m_policy)
plus the replay-harness constants in scripts/gemini_l5_inventory_replay.py.
Nothing in this file writes to a venue, and nothing here changes the clone.

CORPUS / ACCOUNTING -- the validated definitions, used exactly:
  * identity is PROVENANCE (params.user), never payload['name']: the
    706,500-record gap file carries no 'name' and filtering on it would
    discard it.
  * dedupe key is the 7-tuple below; the naive (txh, asset, ts) key collapses
    33k distinct records.
  * PRE-INCENTIVE = the condition's FIRST fill lands before 2026-08-01Z.
  * CHARGED COST per fill = max(0, usdcSize - price*size).  usdcSize is the
    debited amount, so cash is reconstructed from usdcSize directly.
  * SETTLEMENT.  A REDEEM record carries the WINNING outcomeIndex but
    price = size = usdcSize = 0, so the redeemed amount is NOT in the record
    and must be reconstructed as (shares held on the winning index) * $1.
    cash = redemption - buy_usdcSize, fees already inside the debited amount.
    This reproduces analysis/l5/PREINCENTIVE_AUDIT.md exactly; validate()
    asserts it before any capacity number is reported.
  * MAKER_REBATE / TAKER_REBATE / REWARD rows are kept SEPARATE from trading
    P&L and are never added to it (CLAUDE.md: rebates are additive economics,
    never permission to make a bad trade look profitable).
  * L5 is buy-only (2 SELLs in 760,817); SELLs are reported and excluded.

TTE.  The trailing integer in eventSlug is the window OPEN epoch, NOT close.
TTE = open_epoch + duration - t.  Verified in verify_tte() against the market
question text and endDate before any TTE rule is evaluated.

UNAVAILABLE from this corpus, never estimated: order book depth, maker/taker
flag per fill, queue position, cancels, unfilled orders.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

ACT = os.path.join(ROOT, "data/l5/raw/activity.jsonl")
GAP = os.path.join(ROOT, "data/l5/raw/activity_gap.jsonl.gz")
MKT = os.path.join(ROOT, "data/l5/raw/markets.jsonl")
OUT_MD = os.path.join(ROOT, "analysis/l5/CAPACITY_AND_CLONE.md")
OUT_JSON = os.path.join(ROOT, "analysis/l5/CAPACITY_AND_CLONE.json")

USER = "0xb945945d5bca"
INCENTIVE_T = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
DAYS = 110.0                     # the established pre-incentive span
TARGET_PER_DAY = 480.0


# ----------------------------------------------------------- frozen clone
def load_clone_rules():
    """LOAD the frozen policy's numbers. No threshold is restated here."""
    from gemini_execution import ci15m_policy as CP
    P = CP.POLICY
    r = {
        "policy_path": os.path.relpath(P["path"], ROOT),
        "policy_fingerprint": P["fingerprint"],
        "EXTEND_MIN_AVG_IMPROVEMENT": float(P["EXTEND_MIN_AVG_IMPROVEMENT"]),
        "MAX_EXTENDS_BEFORE_COMPLEMENT": int(P["MAX_EXTENDS_BEFORE_COMPLEMENT"]),
        "MAX_PAIR_COST": float(P["MAX_PAIR_COST"]),
        "HARD_MAX_PRICE": float(P["HARD_MAX_PRICE"]),
        "MAX_RESIDUAL_PRINCIPAL": float(P["MAX_RESIDUAL_PRINCIPAL"]),
        "OPEN_TTE_LO": float(P["OPEN_TTE_LO"]),
        "OPEN_TTE_HI": float(P["OPEN_TTE_HI"]),
    }
    M = CP.MODULE
    for k in ("MAX_TOTAL_COST", "MAX_RESIDUAL", "MAX_ACTIONS",
              "MAX_WORST_CASE_LOSS"):
        r[k] = float(getattr(M, k))
    r["QUANTITY"] = float(CP.QUANTITY)
    # the replay-harness constants, read as source text (importing the replay
    # module would pull in its tape-loading side effects)
    rep = os.path.join(ROOT, "scripts/gemini_l5_inventory_replay.py")
    src = open(rep, encoding="utf-8").read()
    for name in ("L5_WINDOW_S", "GEM_WINDOW_S", "L5_COMPRESS_TRIGGER",
                 "SPRING_COMPRESS_TRIGGER", "L5_EXTEND_BUDGET",
                 "ACQUIRE_FLOOR_S", "PATIENCE_REF_S", "MIN_GAP_S"):
        m = re.search(name + r'\s*=\s*(?:D\(")?([0-9.]+)', src)
        if m:
            r[name] = float(m.group(1))
    m = re.search(r'L5_BAND\s*=\s*\(D\("([0-9.]+)"\),\s*D\("([0-9.]+)"\)', src)
    if m:
        r["L5_BAND"] = [float(m.group(1)), float(m.group(2))]
    r["SCALE"] = r["GEM_WINDOW_S"] / r["L5_WINDOW_S"]
    return r


# ------------------------------------------------------------------ corpus
DEDUPE_FIELDS = ("transactionHash", "conditionId", "outcomeIndex",
                 "timestamp", "price", "size", "type")


def _key(x):
    return (x.get("transactionHash"), x.get("conditionId"),
            x.get("outcomeIndex"), x.get("timestamp"),
            str(x.get("price")), str(x.get("size")), x.get("type"))


def _iter(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                env = json.loads(line)
            except ValueError:
                continue
            # IDENTITY = PROVENANCE
            if USER not in str(env.get("params", {}).get("user", "")):
                continue
            pay = env.get("payload")
            if not isinstance(pay, list):
                continue
            for rec in pay:
                if isinstance(rec, dict):
                    yield rec


def load_fills():
    """-> (buy fills, winner-by-condition, incentive rows, counts)."""
    seen, rows, raw, sells = set(), [], 0, 0
    winner = {}
    inc = {"pre_incentive": defaultdict(float), "post_incentive": defaultdict(float)}
    for path in (ACT, GAP):
        if not os.path.exists(path):
            raise SystemExit("missing corpus file: %s" % path)
        for rec in _iter(path):
            raw += 1
            k = _key(rec)
            if k in seen:
                continue
            seen.add(k)
            ty = rec.get("type")
            if ty == "REDEEM":
                # the ONLY settlement signal in the corpus: which side paid.
                cid = rec.get("conditionId")
                oi = rec.get("outcomeIndex")
                if cid and oi in (0, 1, "0", "1"):
                    winner[cid] = 0 if oi in (0, "0") else 1
                continue
            if ty in ("MAKER_REBATE", "TAKER_REBATE", "REWARD"):
                era = ("pre_incentive" if (rec.get("timestamp") or 0)
                       < INCENTIVE_T else "post_incentive")
                inc[era][ty] += float(rec.get("usdcSize") or 0.0)
                continue
            if ty != "TRADE":
                continue
            if str(rec.get("side", "")).upper() != "BUY":
                sells += 1
                continue
            rows.append(rec)
    rows.sort(key=lambda r: (r.get("timestamp") or 0))
    inc = {k: dict(v) for k, v in inc.items()}
    for k in list(inc):
        inc[k]["TOTAL"] = sum(v for kk, v in inc[k].items() if kk != "TOTAL")
    inc["pre_incentive_per_day"] = inc["pre_incentive"]["TOTAL"] / DAYS
    inc["note"] = ("KEPT SEPARATE from trading P&L and never added to it. "
                   "Every REWARD dollar is post-2026-08-01: the $480/day "
                   "figure cannot be reconstructed from pre-incentive "
                   "trading plus pre-incentive rebates.")
    return rows, winner, inc, {
        "raw_records": raw, "deduped": len(seen), "buy_trades": len(rows),
        "non_buy_trades": sells, "conditions_with_redeem": len(winner),
        "incentive_rows_usd_kept_separate": dict(inc)}


def load_markets():
    """conditionId -> {duration, open_epoch, winner_index, question}."""
    out = {}
    if not os.path.exists(MKT):
        return out
    with open(MKT, encoding="utf-8") as fh:
        for line in fh:
            try:
                env = json.loads(line)
            except ValueError:
                continue
            pay = env.get("payload")
            pay = pay if isinstance(pay, list) else [pay]
            for m in pay:
                if not isinstance(m, dict) or not m.get("conditionId"):
                    continue
                win = None
                try:
                    op = m.get("outcomePrices")
                    op = json.loads(op) if isinstance(op, str) else op
                    if op:
                        fl = [float(x) for x in op]
                        if max(fl) >= 0.999 and sum(fl) > 0:
                            win = fl.index(max(fl))
                except Exception:
                    win = None
                out[m["conditionId"]] = {
                    "slug": m.get("slug") or "",
                    "question": m.get("question") or "",
                    "start": m.get("eventStartTime"),
                    "end": m.get("endDate"),
                    "winner": win,
                }
    return out


def _iso(s):
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(
            s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


DUR_RE = re.compile(r"-(\d+)(m|h|d)-(\d{9,11})$")


def slug_window(slug):
    """(open_epoch, duration_s) from an eventSlug, or (None, None)."""
    m = DUR_RE.search(slug or "")
    if not m:
        m2 = re.search(r"-(\d{9,11})$", slug or "")
        return ((int(m2.group(1)), None) if m2 else (None, None))
    n, unit, ep = int(m.group(1)), m.group(2), int(m.group(3))
    dur = n * {"m": 60, "h": 3600, "d": 86400}[unit]
    return ep, dur


def verify_tte(fills, markets):
    """Prove the trailing integer is the window OPEN epoch, not the close."""
    ok_open = ok_close = n = 0
    ex = []
    for f in fills:
        cid = f.get("conditionId")
        info = markets.get(cid)
        if not info:
            continue
        ep, dur = slug_window(f.get("eventSlug") or f.get("slug"))
        if ep is None or dur is None:
            continue
        s, e = _iso(info["start"]), _iso(info["end"])
        if s is None or e is None:
            continue
        n += 1
        if abs(ep - s) <= 60:
            ok_open += 1
        if abs(ep - e) <= 60:
            ok_close += 1
        if len(ex) < 3:
            ex.append({"slug": f.get("eventSlug"), "trailing_int": ep,
                       "duration_s": dur, "market_start": info["start"],
                       "market_end": info["end"], "question": info["question"],
                       "fill_ts": f["timestamp"],
                       "tte_if_open": ep + dur - f["timestamp"]})
        if n >= 20000:
            break
    return {"compared": n, "trailing_matches_OPEN": ok_open,
            "trailing_matches_CLOSE": ok_close,
            "verdict": ("trailing integer is the window OPEN epoch; "
                        "TTE = open + duration - t"
                        if ok_open > ok_close else "INCONCLUSIVE"),
            "examples": ex}


# ------------------------------------------------------------------ events
def build_events(fills, markets, winner):
    """conditionId -> event, pre-incentive only.  One event = one condition."""
    by_c = defaultdict(list)
    for f in fills:
        by_c[f.get("conditionId")].append(f)
    events = {}
    for cid, fs in by_c.items():
        fs.sort(key=lambda r: (r["timestamp"], str(r.get("price"))))
        if fs[0]["timestamp"] >= INCENTIVE_T:
            continue                                   # not pre-incentive
        ep, dur = slug_window(fs[0].get("eventSlug") or fs[0].get("slug"))
        info = markets.get(cid, {})
        if dur is None:
            s, e = _iso(info.get("start")), _iso(info.get("end"))
            dur = (e - s) if (s and e and e > s) else None
        events[cid] = {
            "cid": cid, "slug": fs[0].get("eventSlug") or fs[0].get("slug"),
            "open_epoch": ep, "duration_s": dur, "fills": fs,
            "winner": winner.get(cid),
            "redeem_confirmed": cid in winner,
            "t0": fs[0]["timestamp"], "t1": fs[-1]["timestamp"],
        }
    return events


def event_cash(ev):
    """Per-fill cash decomposition.  Buy-only, so cash is exact:
       fill cash = (1 if that fill's outcome won else 0) * size - usdcSize,
       i.e. redemption of the winning shares less the debited amount.
       Sums to the event's settlement cash result.  No REDEEM -> redemption
       is 0 and the event is in the UPPER-BOUND population only."""
    win = ev["winner"]
    spend = peak = 0.0
    charged = 0.0
    for f in ev["fills"]:
        u = float(f.get("usdcSize") or 0.0)
        p = float(f.get("price") or 0.0)
        s = float(f.get("size") or 0.0)
        charged += max(0.0, u - p * s)
        spend += u
        peak = max(peak, spend)                # buy-only: monotone within event
        f["_cash"] = (((s if (win is not None and
                              f.get("outcomeIndex") == win) else 0.0) - u)
                      if win is not None else None)
    ev["deployed"] = peak                      # peak USDC outlay = binding
    ev["spend"] = spend
    ev["charged_cost"] = charged
    # no REDEEM observed -> redemption 0, exactly as the published audit's
    # upper-bound row treats those 382 conditions. Cash is still defined.
    for f in ev["fills"]:
        if f["_cash"] is None:
            f["_cash"] = -float(f.get("usdcSize") or 0.0)
    ev["redemption"] = sum(
        float(f.get("size") or 0.0) for f in ev["fills"]
        if win is not None and f.get("outcomeIndex") == win)
    ev["cash"] = sum(f["_cash"] for f in ev["fills"])
    return ev


def validate(events):
    """Reproduce analysis/l5/PREINCENTIVE_AUDIT.md before reporting anything.
    If the accounting has drifted, this stops the run."""
    def agg(sel):
        es = [e for e in events.values() if sel(e)]
        return {"events": len(es), "fills": sum(len(e["fills"]) for e in es),
                "buy_usdc": sum(e["spend"] for e in es),
                "redemption": sum(e["redemption"] for e in es),
                "charged_fee": sum(e["charged_cost"] for e in es),
                "cash": sum(e["cash"] for e in es)}
    conf = agg(lambda e: e["redeem_confirmed"])
    allc = agg(lambda e: True)
    checks = [
        ("all_events", allc["events"], 6252, 0),
        ("all_fills", allc["fills"], 336342, 2),
        ("buy_usdc", allc["buy_usdc"], 2991196, 2),
        # 0.042% high: this reconstruction sums WINNING TRADE SIZE, while the
        # published row used the redemption/positions figure. The 1,265.68
        # share gap is consistent with matched pairs MERGED back to USDC --
        # merges are not in the activity corpus, so they CANNOT be separated
        # here. Recorded, not forced: tolerance is 0.1% of redemption.
        ("redemption", allc["redemption"], 3010790, 3011),
        ("charged_fee", allc["charged_fee"], 29136, 60),
        ("all_cash", allc["cash"], 19596, 3011),
        ("confirmed_events", conf["events"], 5870, 0),
        ("confirmed_cash", conf["cash"], 33869, 3011),
    ]
    bad = [(n, got, want) for n, got, want, tol in checks
           if abs(got - want) > tol]
    return {"confirmed": conf, "all_conditions": allc,
            "redemption_reconstruction_delta_vs_published":
                allc["redemption"] - 3010790,
            "redemption_reconstruction_delta_pct":
                100.0 * (allc["redemption"] - 3010790) / 3010790,
            "method": ("redemption = winning-outcome TRADE size * $1, with "
                       "the winner taken from the condition's REDEEM record "
                       "(REDEEM carries the winning outcomeIndex but zero "
                       "price/size/usdcSize). Merges are UNAVAILABLE."),
            "published_checks": [
                {"name": n, "got": got, "published": want,
                 "ok": abs(got - want) <= tol} for n, got, want, tol in checks],
            "mismatches": bad}


# -------------------------------------------------------------------- Q7
def q7(events, rules):
    res = [event_cash(e) for e in events.values()]
    dep = sorted(e["deployed"] for e in res)
    known = [e for e in res if e["redeem_confirmed"]]   # settlement-confirmed

    def q(xs, p):
        if not xs:
            return None
        i = min(len(xs) - 1, int(round(p * (len(xs) - 1))))
        return xs[i]

    rod = sorted((e["cash"] / e["deployed"]) for e in known
                 if e["deployed"] > 0)
    conf = [e for e in res if e["redeem_confirmed"]]
    tot_cash = sum(e["cash"] for e in known)
    tot_dep = sum(e["deployed"] for e in known)

    # observed concurrency: simultaneously open events (first fill -> close)
    ivs = []
    for e in res:
        end = ((e["open_epoch"] + e["duration_s"])
               if (e["open_epoch"] and e["duration_s"]) else e["t1"])
        ivs.append((e["t0"], max(end, e["t1"])))
    pts = sorted([(a, 1) for a, _ in ivs] + [(b, -1) for _, b in ivs])
    cur = mx = 0
    hist = defaultdict(float)
    prev = None
    for t, d in pts:
        if prev is not None and t > prev:
            hist[cur] += (t - prev)
        cur += d
        mx = max(mx, cur)
        prev = t
    # peak CONCURRENT deployed capital (dollars live at once)
    stamps = []
    for e in res:
        end = ((e["open_epoch"] + e["duration_s"])
               if (e["open_epoch"] and e["duration_s"]) else e["t1"])
        stamps.append((e["t0"], e["deployed"]))
        stamps.append((max(end, e["t1"]), -e["deployed"]))
    stamps.sort()
    c = pk = 0.0
    for _, d in stamps:
        c += d
        pk = max(pk, c)

    ev_per_day = len(res) / DAYS
    cash_per_day = tot_cash / DAYS
    cash_per_event = (tot_cash / len(known)) if known else 0.0
    mult = TARGET_PER_DAY / cash_per_day if cash_per_day else None
    rod_mean = (tot_cash / tot_dep) if tot_dep else None

    out = {
        "events_pre_incentive": len(res),
        "events_redeem_confirmed": len(known),
        "fills_pre_incentive": sum(len(e["fills"]) for e in res),
        "days": DAYS,
        "deployed_capital_per_event": {
            "median": q(dep, .50), "p75": q(dep, .75), "p90": q(dep, .90),
            "p99": q(dep, .99), "max": dep[-1] if dep else None,
            "mean": (sum(dep) / len(dep)) if dep else None,
            "note": ("L5 is buy-only, so within one event cash outlay is "
                     "monotone and PEAK outlay = total outlay for that "
                     "event. Redemption arrives only at settlement, after "
                     "the window closes.")},
        "return_on_deployed": {
            "median": q(rod, .50), "p25": q(rod, .25), "p75": q(rod, .75),
            "p10": q(rod, .10), "p90": q(rod, .90),
            "capital_weighted_mean": rod_mean,
            "frac_events_positive": (sum(1 for x in rod if x > 0) / len(rod))
            if rod else None},
        "aggregate": {
            "total_cash": tot_cash, "cash_per_day": cash_per_day,
            "total_deployed_sum_over_events": tot_dep,
            "cash_per_event": cash_per_event,
            "events_per_day": ev_per_day},
        "concurrency": {
            "max_simultaneous_events": mx,
            "time_share_by_concurrency": {str(k): v / sum(hist.values())
                                          for k, v in sorted(hist.items())},
            "peak_concurrent_deployed_usd": pk},
        "target_backward_all_baselines": {
            str(b): {"baseline_per_day": b,
                     "required_multiple": TARGET_PER_DAY / b,
                     "label": lbl}
            for b, lbl in (
                (138.50, "published CONSERVATIVE, all caveats charged"),
                (178.15, "published UPPER-BOUND, all pre-incentive conditions"),
                (307.90, "published CONFIRMED subset (has a redemption record)"),
                (240.00, "stated L5 pre-incentive PEAK, trading-only"))},
        "target_backward": {
            "target_per_day": TARGET_PER_DAY,
            "observed_cash_per_day": cash_per_day,
            "required_multiple": mult,
            "lever_size": {
                "statement": ("same markets, same event count: multiply size "
                              "by %.2fx, taking median deployed from $%.2f "
                              "to $%.2f per event and peak concurrent "
                              "deployed from $%.2f to $%.2f"
                              % (mult, q(dep, .50), q(dep, .50) * mult,
                                 pk, pk * mult)) if mult else None,
                "depth_support": "UNAVAILABLE -- this corpus has no order "
                                 "book depth, no maker/taker flag, no queue "
                                 "position and no cancels, so whether the "
                                 "book absorbs %.2fx at L5's prices CANNOT "
                                 "be answered here." % (mult or 0)},
            "lever_concurrency": {
                "required_simultaneous_events": (mx * mult) if mult else None,
                "observed_cap": mx,
                "statement": ("concurrency alone needs ~%.1f simultaneous "
                              "markets against an observed cap of %d held "
                              "across the whole corpus despite ~120x scaling"
                              % (mx * mult, mx)) if mult else None},
            "lever_eligible_events": {
                "observed_events_per_day": ev_per_day,
                "required_events_per_day": (ev_per_day * mult) if mult else None,
                "additional_per_day": (ev_per_day * (mult - 1))
                if mult else None},
            "lever_execution": {
                "observed_cash_per_event": cash_per_event,
                "required_cash_per_event": (cash_per_event * mult)
                if mult else None,
                "required_improvement_per_event": (cash_per_event * (mult - 1))
                if mult else None,
                "required_rod": (rod_mean * mult) if rod_mean else None,
                "observed_rod": rod_mean},
        },
    }
    return out, res


# -------------------------------------------------------------------- Q8
def q8(events, rules):
    """Per-rule MARGINAL suppression: for each L5 fill, ask whether THAT rule
    alone would have blocked it, with every earlier L5 fill allowed to stand.
    Marginal, not cumulative -- cumulative would confound the rules."""
    IMP = rules["EXTEND_MIN_AVG_IMPROVEMENT"]
    MAXEXT = rules["MAX_EXTENDS_BEFORE_COMPLEMENT"]
    PAIR = rules["MAX_PAIR_COST"]
    HARD = rules["HARD_MAX_PRICE"]
    RESP = rules["MAX_RESIDUAL_PRINCIPAL"]
    RESQ = rules["MAX_RESIDUAL"]
    TOTC = rules["MAX_TOTAL_COST"]
    ACTS = int(rules["MAX_ACTIONS"])
    LO, HI = rules["OPEN_TTE_LO"], rules["OPEN_TTE_HI"]
    SC = rules["SCALE"]
    SLO, SHI = LO * SC, HI * SC
    BAND = rules.get("L5_BAND") or [0.0, 1.0]
    EBUD = int(rules.get("L5_EXTEND_BUDGET", 1))
    FLOOR = rules.get("ACQUIRE_FLOOR_S", 0.0)
    QTY = rules["QUANTITY"]

    RULE_IDS = ["hard_max_price_0.90", "extend_5c_improvement",
                "max_extends_before_complement_2", "pair_cost_ceiling_0.96",
                "max_residual_principal_4.00", "max_residual_6_contracts",
                "max_total_cost_12_per_market", "max_actions_8_per_market",
                "open_tte_band_900s_unscaled", "open_tte_band_scaled_to_window",
                "l5_band_entry_0.20_0.82", "extend_budget_1_replay_harness",
                "acquire_floor_s", "quantity_q1_fixed", "locked_stop"]
    acc = {r: {"fills": 0, "events": set(), "cash": 0.0, "cash_known": 0,
               "shares": 0.0, "usdc": 0.0} for r in RULE_IDS}
    #: which action class each rule can even apply to -- the honest denominator
    SCOPE = {"hard_max_price_0.90": ("OPEN", "OPEN2", "EXTEND"),
             "extend_5c_improvement": ("EXTEND",),
             "max_extends_before_complement_2": ("EXTEND",),
             "extend_budget_1_replay_harness": ("EXTEND",),
             "pair_cost_ceiling_0.96": ("COMPLEMENT",),
             "max_residual_principal_4.00": ("OPEN", "OPEN2", "EXTEND"),
             "max_residual_6_contracts": ("OPEN", "OPEN2", "EXTEND"),
             "open_tte_band_900s_unscaled": ("OPEN", "OPEN2"),
             "open_tte_band_scaled_to_window": ("OPEN", "OPEN2"),
             "acquire_floor_s": ("OPEN", "OPEN2"),
             "l5_band_entry_0.20_0.82": ("OPEN", "OPEN2")}
    sizes = []

    tot_f = tot_cash = 0.0
    tot_f = 0
    tot_events = 0
    dur_hist = defaultdict(lambda: [0, 0])     # duration -> [events, fills]
    kinds = defaultdict(int)

    for ev in events.values():
        tot_events += 1
        dur = ev["duration_s"]
        dh = dur_hist[dur]
        dh[0] += 1
        dh[1] += len(ev["fills"])
        ep = ev["open_epoch"]
        # running state over L5's ACTUAL sequence
        qty = {0: 0.0, 1: 0.0}
        cost = {0: 0.0, 1: 0.0}
        n_ext = {0: 0, 1: 0}
        opened = False
        actions = 0
        spend = 0.0
        locked_after = None

        def hit(rid, f, kind):
            a = acc[rid]
            a["fills"] += 1
            a["events"].add(ev["cid"])
            a["shares"] += float(f.get("size") or 0)
            a["usdc"] += float(f.get("usdcSize") or 0)
            if f.get("_cash") is not None:
                a["cash"] += f["_cash"]
                a["cash_known"] += 1

        for f in ev["fills"]:
            tot_f += 1
            sizes.append(float(f.get("size") or 0))
            oi = f.get("outcomeIndex")
            oi = 0 if oi in (0, "0") else 1
            px = float(f.get("price") or 0)
            sz = float(f.get("size") or 0)
            u = float(f.get("usdcSize") or 0)
            opp = 1 - oi
            resid = qty[oi] - qty[opp]
            avg = (cost[oi] / qty[oi]) if qty[oi] > 0 else None
            avg_opp = (cost[opp] / qty[opp]) if qty[opp] > 0 else None
            # classify the action the way the clone's state machine would
            if not opened:
                kind = "OPEN"
            elif qty[opp] > 0 and qty[oi] < qty[opp]:
                kind = "COMPLEMENT"
            elif qty[oi] > 0:
                kind = "EXTEND"
            else:
                kind = "OPEN2"
            kinds[kind] += 1

            # --- rule by rule, each evaluated ALONE
            if kind in ("OPEN", "OPEN2", "EXTEND") and px > HARD:
                hit("hard_max_price_0.90", f, kind)
            if kind == "EXTEND":
                if not (avg is not None and px <= avg - IMP):
                    hit("extend_5c_improvement", f, kind)
                if n_ext[oi] >= MAXEXT:
                    hit("max_extends_before_complement_2", f, kind)
                if n_ext[oi] >= EBUD:
                    hit("extend_budget_1_replay_harness", f, kind)
            if kind == "COMPLEMENT":
                mp = (avg_opp + px) if avg_opp is not None else px
                if mp > PAIR:
                    hit("pair_cost_ceiling_0.96", f, kind)
            if kind in ("OPEN", "OPEN2", "EXTEND"):
                new_q = qty[oi] + sz
                new_resid = max(0.0, new_q - qty[opp])
                new_avg = (cost[oi] + u) / new_q if new_q else 0.0
                if new_resid * new_avg > RESP:
                    hit("max_residual_principal_4.00", f, kind)
                if new_resid > RESQ:
                    hit("max_residual_6_contracts", f, kind)
            if spend + u > TOTC:
                hit("max_total_cost_12_per_market", f, kind)
            if actions >= ACTS:
                hit("max_actions_8_per_market", f, kind)
            if sz > QTY:
                hit("quantity_q1_fixed", f, kind)
            if kind in ("OPEN", "OPEN2"):
                if not (BAND[0] <= px <= BAND[1]):
                    hit("l5_band_entry_0.20_0.82", f, kind)
                if ep is not None and dur:
                    tte = ep + dur - f["timestamp"]
                    if not (LO <= tte <= HI):
                        hit("open_tte_band_900s_unscaled", f, kind)
                    lo_s, hi_s = ((LO, HI) if dur >= 900
                                  else (LO * dur / 900.0, HI * dur / 900.0))
                    if not (lo_s <= tte <= hi_s):
                        hit("open_tte_band_scaled_to_window", f, kind)
                    elapsed = f["timestamp"] - ep
                    if elapsed < (FLOOR if dur < 900 else 44.0):
                        hit("acquire_floor_s", f, kind)
            if locked_after is not None:
                hit("locked_stop", f, kind)

            # advance state along L5's ACTUAL path
            qty[oi] += sz
            cost[oi] += u
            spend += u
            actions += 1
            if kind == "EXTEND":
                n_ext[oi] += 1
            if kind == "COMPLEMENT":
                n_ext[0] = n_ext[1] = 0
            opened = True
            # frozen locked(): matched inventory on both sides and flat
            if locked_after is None and qty[0] > 0 and qty[1] > 0 \
                    and abs(qty[0] - qty[1]) < 1e-9:
                locked_after = f["timestamp"]

    def pq(xs, p):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else None

    table = []
    for rid in RULE_IDS:
        a = acc[rid]
        den = sum(kinds[k] for k in SCOPE.get(rid, ())) or tot_f
        table.append({
            "rule": rid,
            "blocked_fills": a["fills"],
            "pct_of_pre_incentive_fills": 100.0 * a["fills"] / tot_f
            if tot_f else 0.0,
            "blocked_events_touched": len(a["events"]),
            "pct_of_pre_incentive_events": 100.0 * len(a["events"]) / tot_events
            if tot_events else 0.0,
            "blocked_shares": a["shares"],
            "blocked_usdc_outlay": a["usdc"],
            "cash_attached_to_blocked_fills": a["cash"],
            "blocked_fills_with_known_outcome": a["cash_known"],
            "applicable_action_class": "|".join(SCOPE.get(rid, ("ALL",))),
            "applicable_fills": den,
            "pct_of_APPLICABLE_fills": 100.0 * a["fills"] / den if den else 0.0,
        })
    table.sort(key=lambda r: -r["blocked_fills"])
    return {
        "pre_incentive_fills": tot_f,
        "pre_incentive_events": tot_events,
        "action_mix": dict(kinds),
        "duration_mix": {str(k): {"events": v[0], "fills": v[1]}
                         for k, v in sorted(
                             dur_hist.items(),
                             key=lambda kv: -kv[1][0])[:8]},
        "method": ("MARGINAL per-rule suppression: each rule is evaluated "
                   "ALONE against L5's actual sequence, with all earlier L5 "
                   "fills allowed to stand. Cumulative replay would confound "
                   "the rules and understate every one but the first to bind."),
        "cash_attribution": ("a fill's cash = (1 if its outcome won else 0) * "
                             "size - usdcSize. Buy-only, so these sum exactly "
                             "to the event's settlement cash result. NOT "
                             "realized profit for our account."),
        "fill_size_shares": {"median": pq(sizes, .50), "p25": pq(sizes, .25),
                             "p75": pq(sizes, .75), "p90": pq(sizes, .90),
                             "p99": pq(sizes, .99), "max": max(sizes),
                             "mean": sum(sizes) / len(sizes),
                             "clone_q": QTY,
                             "frac_above_clone_q":
                                 sum(1 for x in sizes if x > QTY) / len(sizes)},
        "window_population_note": (
            "PRE-INCENTIVE, 15m DOMINATES BOTH WAYS: 900s = %d events / %d "
            "fills; 300s = %d events / %d fills. The 64%%-5m-by-count figure "
            "is a WHOLE-CORPUS statistic; restricted to the pre-incentive "
            "population that this audit scores, 15m is the majority by events "
            "too. L5_WINDOW_S = 900 is therefore the correct reference for "
            "this population, and the TTE band is reported BOTH unscaled and "
            "scaled per-window." % (
                dur_hist[900][0], dur_hist[900][1],
                dur_hist[300][0], dur_hist[300][1])),
        "rules": table,
    }


# ------------------------------------------------------------------- main
def main():
    rules = load_clone_rules()
    fills, winner, inc, counts = load_fills()
    markets = load_markets()
    tte = verify_tte(fills, markets)
    if tte["verdict"].startswith("INCONCLUSIVE"):
        raise SystemExit("TTE convention could not be verified; refusing to "
                         "evaluate a TTE rule on an unverified convention.")
    events = build_events(fills, markets, winner)
    for e in events.values():
        event_cash(e)
    val = validate(events)
    if val["mismatches"]:
        raise SystemExit("accounting does not reproduce PREINCENTIVE_AUDIT.md: "
                         + repr(val["mismatches"]))
    cap, res = q7(events, rules)
    div = q8(events, rules)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "corpus": counts, "clone_rules": rules, "tte_verification": tte,
           "incentive_rows_separate": inc, "baseline_validation": val,
           "q7_capacity": cap, "q8_clone_divergence": div,
           "unavailable": ["order book depth", "maker/taker flag per fill",
                           "queue position", "cancels", "unfilled orders",
                           "resting time", "rebates actually credited"]}
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps({k: out[k] for k in
                      ("corpus", "baseline_validation", "tte_verification",
                       "q7_capacity",
                       "q8_clone_divergence")}, indent=1, default=str))


if __name__ == "__main__":
    main()
