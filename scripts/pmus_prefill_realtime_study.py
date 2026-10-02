#!/usr/bin/env python3
"""P1+P2+P3: fixed-price resting-order shadow, synchronized with the external BTC
reference, and an observational pre-fill study on its defensible first-leg fills.

NOTHING FROZEN IS TOUCHED. scripts/pmus_pair_or_unwind.py, the recorder, and the
pre-registered OOS experiment (82714061) are unchanged. The exit decision below
is a VERBATIM COPY of the frozen arm's exit rules, copied rather than imported
precisely so the frozen file stays as it is; it is what produces the QUALIFYING
vs FORCED labels, which are therefore the unchanged downstream baseline's labels
and not a new proxy. (PMUS_ENTRY_MECHANISM_AUDIT.json used an MAE>MFE proxy;
these labels are not that and the two are not interchangeable.)

TIMING EVIDENCE IS OURS, MEASURED HERE -- no generic venue assumptions:
  cancel RTT, 4,567 in-repo samples   min 0.187 s, p50 0.266 s, p90 0.612 s
  PMUS book inter-arrival             p10 0.098 / p50 0.101 / p90 0.200 s
  PMUS trade `t` microsecond receipt, `trade_time` nanosecond venue
  reference venue ts microsecond, receipt-minus-venue skew recorded per row
Horizons below 0.266 s are NOT_ACTIONABLE by our own measurement; 0.5 s is
OPTIMISTIC_ONLY; 1/2/5 s are ACTIONABLE. This gating is evidence, not taste.

P1 -- SHADOW ORDER LIFECYCLE. An order is
    PLACE(t, price, qty, queue_ahead)
and its price is FIXED until FILL, CANCEL, EXPIRE, or an explicit CANCEL -> new
PLACE. It never silently follows the PMUS touch, and a reprice starts a new
order whose queue_ahead is the new level's displayed size -- all prior queue
position is forfeited. Every transition is persisted with provenance to
data/pmus_shadow_orders/ so the lifecycle can be audited independently of any
summary. TTLs are the pre-registered coarse {5 s, 30 s, WINDOW}; WINDOW places
once and never reprices.

P2 -- SYNCHRONIZATION. Venue/source and local-receipt timestamps are kept
SEPARATELY and never merged into one invented clock: reference rows carry ev_ns
(venue) and t_ns (receipt); PMUS rows carry recorder-local `t` and, for trades,
venue `trade_time`. Cross-feed comparison uses receipt-vs-receipt, which is the
basis an order router actually has. No sub-skew precision is claimed: the
reference feed's own receipt-minus-venue skew (~0.2 s, reported) bounds how
finely the two feeds can be aligned, so "external moved before PMUS" is only
asserted at horizons larger than that bound and is marked UNRESOLVED below it.

P3 -- OBSERVATIONAL ONLY. Features are read strictly before each fill:
    btc_ret_bps, btc_dir, btc_move_ticks, pmus_mid_move, pmus_bbo_move,
    queue_depletion_frac (at OUR fixed resting level), trade_imbalance,
    sweep_seen, spread, spread_change, ext_before_pmus
Primary output is P(signal | TOXIC) vs P(signal | QUALIFYING) at pre-registered
coarse levels, with each signal's lead time to the fill set against the measured
RTT distribution. No threshold is selected, no combination is optimized, no
order is submitted.
"""
from __future__ import annotations
import argparse, bisect, glob, json, os, statistics
from collections import Counter
from decimal import Decimal as D, getcontext

getcontext().prec = 28
REF_GLOB = "data/btc_ref/btcref_*.jsonl"
OUT = "analysis/l5/pmus_rewards/PMUS_PREFILL_REALTIME_STUDY.json"
LIFE_DIR = "data/pmus_shadow_orders"
RTT_MIN, RTT_P50, RTT_P90 = 0.187, 0.266, 0.612
HORIZONS = (0.25, 0.5, 1.0, 2.0, 5.0)
TTLS = (("TTL5", 5.0), ("TTL30", 30.0), ("WINDOW", None))
CEILINGS = (D("0.99"), D("0.98"), D("0.97"), D("0.95"))
Q = 25
DEADLINE_S = 90
MIN_SPAN_S = 600.0
TAKER = D("0.07"); MAKER_REB = D("0.0125")
BTC_TICK = 0.01
# pre-registered coarse presence levels. Reported, never selected.
LEVELS = {"btc_adverse_bps": (1.0, 2.0, 5.0),
          "queue_depletion_frac": (0.25, 0.50),
          "pmus_mid_adverse": (0.005, 0.01),
          "trade_imbalance": (0.60, 0.75),
          "spread_widening": (0.005, 0.01)}

import importlib.util
_s = importlib.util.spec_from_file_location("pou", "scripts/pmus_pair_or_unwind.py")
pou = importlib.util.module_from_spec(_s); _s.loader.exec_module(pou)


def actionability(h):
    if h < RTT_P50:
        return "NOT_ACTIONABLE"
    return "OPTIMISTIC_ONLY" if h < RTT_P90 else "ACTIONABLE"


# ----------------------------------------------------------------- reference
def load_ref():
    """Returns receipt ts, venue ts, mids, skews, and the session/error rows.

    Session and error rows are loaded because coverage has to be PROVEN rather
    than assumed: a restart or a reconnect is a candidate gap, and a gap that is
    not reported is a gap that silently becomes a fill.
    """
    rows = []
    skews = []
    seen = set()
    meta = []
    for p in sorted(glob.glob(REF_GLOB)):
        for line in open(p):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("kind") in ("session", "error", "clock"):
                meta.append(r)
                continue
            if r.get("kind") != "ref_bbo" or r.get("ev_ns") is None:
                continue
            if not r.get("bid") or not r.get("ask"):
                continue
            # the same feed can reach disk twice (a local run and the harvested
            # copy of the deployed collector overlap in time), so dedupe on the
            # venue event plus receipt instant rather than counting it twice
            k = (r["ev"], r["t_ns"], r["bid"], r["ask"])
            if k in seen:
                continue
            seen.add(k)
            rows.append((r["t_ns"] / 1e9, r["ev_ns"] / 1e9,
                         (float(r["bid"]) + float(r["ask"])) / 2))
            if r.get("skew_ns") is not None:
                skews.append(r["skew_ns"] / 1e9)
    rows.sort()
    return ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows],
            skews, meta)


def ref_at(ts, mids, t):
    """Last reference mid DELIVERED at or before t. Never interpolated."""
    i = bisect.bisect_right(ts, t) - 1
    return mids[i] if i >= 0 else None


# -------------------------------------------- P1 shadow order lifecycle arm
def resting_arm(rows, dead, ttl, life):
    """PLACE once -> fixed price -> FILL / EXPIRE / explicit CANCEL+new PLACE."""
    order = {"buy": None, "sell": None}      # dict with fixed px and queue
    lots = []
    oid = [0]

    def place(side, px, qsz, t, why):
        oid[0] += 1
        o = {"id": oid[0], "side": side, "px": px, "qty": D(Q), "filled": D(0),
             "queue_ahead": qsz, "queue0": qsz, "placed_t": t}
        order[side] = o
        life.append({"ev": "PLACE", "t": t, "oid": o["id"], "side": side,
                     "px": str(px), "qty": Q, "queue_ahead": str(qsz), "why": why})
        return o

    for i, x in enumerate(rows):
        t = x["t"]; past = t >= dead
        px = D(str(x["px"])); bid = D(str(x["bid_at_trade"])); ask = D(str(x["ask_at_trade"]))
        bq = D(str(x["book"]["bid_qty"])); aq = D(str(x["book"]["ask_qty"]))
        consumed = "sell" if x["dir"] == "buy" else "buy"
        avail = D(str(x["qty"]))

        if ttl is not None:                       # EXPIRE before anything else
            for side in ("buy", "sell"):
                o = order[side]
                if o is not None and t - o["placed_t"] >= ttl:
                    life.append({"ev": "EXPIRE", "t": t, "oid": o["id"],
                                 "age_s": t - o["placed_t"]})
                    order[side] = None

        o = order.get(consumed)
        if o is not None:
            ok = (px <= o["px"]) if consumed == "buy" else (px >= o["px"])
            if ok:                                 # evidenced queue consumption
                # TRADE-THROUGH, the rule already established on the Gemini tape
                # and missing from the first revision of this arm: a print
                # STRICTLY through our price proves our level was cleared, so a
                # quote there fills regardless of queue position. Without this
                # the arm kept charging the queue measured AT PLACEMENT long
                # after the market had traded past the order -- one toxic lot
                # showed a 0.44 bid still "queued" behind 2383 shares while the
                # book walked down to 0.37 over 424 s. That is not a resting
                # order, it is an unreachable one, and it inflated both the
                # quote ages and the toxic count.
                through = (px < o["px"]) if consumed == "buy" else (px > o["px"])
                if through:
                    o["queue_ahead"] = D(0)
                if o["queue_ahead"] > 0:
                    eat = min(o["queue_ahead"], avail)
                    o["queue_ahead"] -= eat; avail -= eat
                if avail > 0 and o["filled"] < o["qty"]:
                    take = min(avail, o["qty"] - o["filled"]); o["filled"] += take
                    life.append({"ev": "FILL", "t": t, "oid": o["id"], "side": consumed,
                                 "px": str(o["px"]), "qty": str(take),
                                 "quote_age_s": t - o["placed_t"],
                                 "queue0": str(o["queue0"])})
                    lots.append({"px": o["px"], "qty": take, "open": take,
                                 "side": consumed, "t": t, "idx": i,
                                 "quote_age_s": t - o["placed_t"],
                                 "queue0": o["queue0"],
                                 "exit_side": "sell" if consumed == "buy" else "buy",
                                 "rest": (None, D(0)), "rest_kind": "PAIR_MAKER",
                                 "exit_kind": None, "exit_t": None,
                                 "qualifying_seen": False, "pair_cost_at_rest": D(1)})
                if o["filled"] >= o["qty"]:
                    life.append({"ev": "CANCEL", "t": t, "oid": o["id"],
                                 "why": "fully_filled"})
                    order[consumed] = None

        if not past:
            for side, qp, qsz in (("buy", bid, bq), ("sell", ask, aq)):
                if order.get(side) is None:
                    place(side, qp, qsz, t, "no_resting_order")
                # NOTE: no branch moves a live order's price. A reprice would be
                # an explicit CANCEL + PLACE and would forfeit queue position;
                # this arm does not reprice at all, which is the point.
    return lots


# ------------- frozen exit rules, VERBATIM copy, to produce baseline labels
def label_exits(rows, lots, dead, ceiling):
    by_idx = {}
    for l in lots:
        by_idx.setdefault(l["idx"], []).append(l)
    live = []
    for i, x in enumerate(rows):
        t = x["t"]
        px = D(str(x["px"])); bid = D(str(x["bid_at_trade"])); ask = D(str(x["ask_at_trade"]))
        bq = D(str(x["book"]["bid_qty"])); aq = D(str(x["book"]["ask_qty"]))
        consumed = "sell" if x["dir"] == "buy" else "buy"
        avail = D(str(x["qty"]))
        past = t >= dead
        for lot in live:
            if lot["open"] <= 0 or avail <= 0 or lot["rest"][0] is None:
                continue
            if lot["exit_side"] != consumed:
                continue
            epx, ahead = lot["rest"]
            if consumed == "buy" and px > epx:
                continue
            if consumed == "sell" and px < epx:
                continue
            if ahead > 0:
                eat = min(ahead, avail); ahead -= eat; avail -= eat
            if avail > 0:
                take = min(avail, lot["open"]); avail -= take
                lot["open"] -= take
                lot["exit_kind"] = lot["rest_kind"]; lot["exit_t"] = t
            lot["rest"] = (epx, ahead)
        live += by_idx.get(i, [])
        for lot in live:
            if lot["open"] <= 0:
                continue
            long_ = lot["side"] == "buy"
            ex_px = bid if long_ else ask
            ex_depth = bq if long_ else aq
            pair_cost = (lot["px"] + (D(1) - ex_px)) if long_ else ((D(1) - lot["px"]) + ex_px)
            gross = D(1) - pair_cost
            f = TAKER * ex_px * (D(1) - ex_px) * min(lot["open"], ex_depth)
            net_now = gross * min(lot["open"], ex_depth) - f
            if pair_cost <= ceiling:
                lot["qualifying_seen"] = True
            if past:
                take = min(lot["open"], ex_depth)
                if take > 0:
                    lot["open"] -= take
                    lot["exit_kind"] = "FORCED"; lot["exit_t"] = t
                continue
            if pair_cost <= ceiling and net_now > 0 and ex_depth > 0:
                take = min(lot["open"], ex_depth)
                lot["open"] -= take
                lot["exit_kind"] = "PAIR_TAKER"; lot["exit_t"] = t
                continue
            need = (D(1) - ceiling + lot["px"]) if long_ else (lot["px"] - (D(1) - ceiling))
            rest_px = max(need, ask) if long_ else min(need, bid)
            lot["rest"] = (rest_px, (aq if long_ else bq))
            lot["rest_kind"] = "PAIR_MAKER"
    for l in lots:
        l["label"] = "TOXIC" if l["exit_kind"] in (None, "FORCED") else "QUALIFYING"
    return lots


# ------------------------------------------------------- P3 pre-fill features
def features(lot, rows, rts, mids, skew_bound):
    t = lot["t"]; i = lot["idx"]; side = lot["side"]
    qk = "bid_qty" if side == "buy" else "ask_qty"
    cur = rows[i]
    out = {}
    for h in HORIZONS:
        prev = None
        for y in rows[:i][::-1]:
            if t - y["t"] >= h:
                prev = y; break
        f = {"actionability": actionability(h)}
        a = ref_at(rts, mids, t - h); b = ref_at(rts, mids, t - 1e-6)
        if a and b and a != 0:
            ret = (b - a) / a * 10000.0
            f["btc_ret_bps"] = round(ret, 3)
            f["btc_dir"] = 1 if ret > 0 else (-1 if ret < 0 else 0)
            f["btc_move_ticks"] = round(abs(b - a) / BTC_TICK, 1)
            f["btc_adverse_bps"] = round(-ret if side == "buy" else ret, 3)
        else:
            f["btc_ret_bps"] = f["btc_adverse_bps"] = None
        if prev is not None:
            pb = float(prev["bid_at_trade"]); pa = float(prev["ask_at_trade"])
            cb = float(cur["bid_at_trade"]); ca = float(cur["ask_at_trade"])
            dmid = ((cb + ca) / 2) - ((pb + pa) / 2)
            f["pmus_mid_move"] = round(dmid, 4)
            f["pmus_mid_adverse"] = round(-dmid if side == "buy" else dmid, 4)
            f["pmus_bbo_move"] = round((cb - pb) if side == "buy" else (ca - pa), 4)
            f["spread"] = round(ca - cb, 4)
            f["spread_change"] = round((ca - cb) - (pa - pb), 4)
            f["spread_widening"] = f["spread_change"]
            q0 = float(prev["book"][qk] or 0)
            f["queue_depletion_frac"] = (round(max(0.0, (q0 - float(lot["queue0"]))) / q0, 4)
                                         if q0 > 0 else None)
            seg = [y for y in rows[:i] if y["t"] >= t - h]
            tb = sum(float(y["qty"]) for y in seg if y["dir"] == "buy")
            tsl = sum(float(y["qty"]) for y in seg if y["dir"] == "sell")
            tot = tb + tsl
            adv = tsl if side == "buy" else tb      # taker hitting our side
            f["trade_imbalance"] = round(adv / tot, 4) if tot > 0 else None
            f["sweep_seen"] = any(
                (float(y["px"]) < float(y["bid_at_trade"]) or
                 float(y["px"]) > float(y["ask_at_trade"])) for y in seg)
            # did the EXTERNAL feed move before PMUS? Only assertable above the
            # reference feed's own receipt-vs-venue skew bound.
            if h <= skew_bound:
                f["ext_before_pmus"] = "UNRESOLVED_CLOCK"
            elif f.get("btc_adverse_bps") is None or f.get("pmus_mid_adverse") is None:
                f["ext_before_pmus"] = None
            else:
                f["ext_before_pmus"] = bool(f["btc_adverse_bps"] > 0
                                            and f["pmus_mid_adverse"] <= 0)
        out[str(h)] = f
    return out


def coverage_proof(slug, rows, books, rts, meta, max_gap_s=2.0):
    """PROVE continuous overlapping coverage over one window; never assume it.

    A gap is any inter-arrival longer than max_gap_s on either feed inside the
    window span, and every reference restart (session row) or reconnect (error
    row) inside the span is listed whether or not it produced a visible gap.
    """
    lo, hi = rows[0]["t"], rows[-1]["t"]
    out = {"window": slug, "span_utc": [lo, hi], "span_s": round(hi - lo, 1)}

    def gaps(ts):
        g = []
        for a, b in zip(ts, ts[1:]):
            if b - a > max_gap_s:
                g.append({"from": a, "to": b, "gap_s": round(b - a, 3)})
        return g

    ref_in = [t for t in rts if lo <= t <= hi]
    bk_in = sorted(b["t"] for b in books if lo <= b["t"] <= hi)
    tr_in = sorted(x["t"] for x in rows)
    out["ref_rows_in_window"] = len(ref_in)
    out["pmus_book_rows_in_window"] = len(bk_in)
    out["pmus_trades_in_window"] = len(tr_in)
    out["ref_gaps"] = gaps(ref_in)
    out["pmus_book_gaps"] = gaps(bk_in)
    out["ref_starts_before_window"] = bool(ref_in and ref_in[0] - lo < max_gap_s)
    out["ref_covers_to_window_end"] = bool(ref_in and hi - ref_in[-1] < max_gap_s)
    out["ref_restarts_in_window"] = [m for m in meta if m.get("kind") == "session"
                                     and lo <= m.get("t_ns", 0) / 1e9 <= hi]
    out["ref_errors_in_window"] = [m for m in meta if m.get("kind") == "error"
                                   and lo <= m.get("t_ns", 0) / 1e9 <= hi]
    out["continuous"] = bool(ref_in and not out["ref_gaps"]
                             and not out["pmus_book_gaps"]
                             and out["ref_starts_before_window"]
                             and out["ref_covers_to_window_end"])
    out["max_gap_s_threshold"] = max_gap_s
    return out


def event_sequence(lot, rows, rts, mids, back_s=5.0):
    """Item 7: the ordered BTC -> PMUS book/queue -> trade/fill chain per fill.

    One list, sorted by the clock each event was OBSERVED on (receipt), with the
    source named on every row so the two feeds are never silently merged.
    """
    t = lot["t"]; i = lot["idx"]
    seq = []
    j = bisect.bisect_right(rts, t - back_s)
    k = bisect.bisect_right(rts, t)
    last = None
    for n in range(j, k):
        m = mids[n]
        if last is None or abs(m - last) >= 0.01:     # only actual moves
            seq.append({"src": "BTC_REF", "t_recv": round(rts[n], 6),
                        "dt_to_fill_s": round(t - rts[n], 3), "mid": round(m, 2)})
            last = m
    qk = "bid_qty" if lot["side"] == "buy" else "ask_qty"
    pk = "bid" if lot["side"] == "buy" else "ask"
    prevb = None
    for y in rows[:i + 1]:
        if y["t"] < t - back_s:
            continue
        b = y["book"]
        st = (b.get(pk), b.get(qk))
        if st != prevb:
            seq.append({"src": "PMUS_BOOK", "t_recv": round(b["t"], 6),
                        "dt_to_fill_s": round(t - b["t"], 3),
                        "our_level_px": str(b.get(pk)), "queue_ahead": str(b.get(qk))})
            prevb = st
        seq.append({"src": "PMUS_TRADE", "t_recv": round(y["t"], 6),
                    "t_venue": y.get("trade_time"),
                    "dt_to_fill_s": round(t - y["t"], 3),
                    "px": str(y["px"]), "qty": str(y["qty"]), "dir": y["dir"],
                    "consumed": "sell" if y["dir"] == "buy" else "buy"})
    seq.sort(key=lambda r: r["t_recv"])
    seq.append({"src": "SHADOW_FILL", "t_recv": round(t, 6), "dt_to_fill_s": 0.0,
                "side": lot["side"], "px": str(lot["px"]),
                "quote_age_s": round(lot["quote_age_s"], 3),
                "queue_at_place": str(lot["queue0"])})
    return seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    # INPUT SELECTION ONLY -- restricts WHICH windows are scored, never how.
    # Required because the coverage test alone would admit frozen-discovery and
    # recorder-validation windows that must not be rescored.
    ap.add_argument("--only", default="", help="comma-separated slugs to score")
    a = ap.parse_args()
    rts, vts, mids, skews, meta = load_ref()
    skew_bound = round(sorted(skews)[len(skews) * 9 // 10], 3) if skews else 1.0
    tr, bk = pou.load()
    sc, dropped = pou.scorable(tr, bk)
    span = (rts[0], rts[-1]) if rts else None

    cov = {}
    for slug, rows in sc.items():
        if not rows or not span:
            continue
        lo, hi = rows[0]["t"], rows[-1]["t"]
        ov = max(0.0, min(hi, span[1]) - max(lo, span[0]))
        # MIN_SPAN_S: the 1245z harvest arrived as a 0.7 s fragment and passed a
        # pure overlap test trivially -- the same class of artifact as the 1345z
        # tape truncation. A window must also BE a window to be scored.
        wspan = hi - lo
        cov[slug] = {"pmus_span_s": round(wspan, 1), "overlap_s": round(ov, 1),
                     "long_enough": wspan >= MIN_SPAN_S,
                     "covered": ov > 0.9 * wspan and wspan >= MIN_SPAN_S}
    covered = sorted(s for s, v in cov.items() if v["covered"])
    if a.only:
        keep = {w.strip() for w in a.only.split(",") if w.strip()}
        excluded = [w for w in covered if w not in keep]
        covered = [w for w in covered if w in keep]
        res["excluded_by_only"] = excluded
    proof = {s_: coverage_proof(s_, sc[s_], bk.get(s_) or [], rts, meta)
             for s_ in covered}

    res = {"frozen_refs": {"prefill_study": "4cfc293f", "preregistration": "82714061"},
           "ref_rows": len(rts), "ref_span_recv": span,
           "ref_skew_s_p90": skew_bound,
           "clock_note": ("venue and receipt clocks kept separate; cross-feed claims "
                          "only above the reference feed's own p90 receipt-venue skew"),
           "rtt": {"min": RTT_MIN, "p50": RTT_P50, "p90": RTT_P90},
           "horizons": {str(h): actionability(h) for h in HORIZONS},
           "levels": {k: list(v) for k, v in LEVELS.items()},
           "coverage": cov, "synchronized_windows": covered,
           "coverage_proof": proof,
           "ttls": {k: v for k, v in TTLS}, "ceilings": [str(c) for c in CEILINGS]}

    if not covered:
        res["status"] = ("NO_SYNCHRONIZED_WINDOW -- the reference feed does not yet "
                         "span a complete PMUS window. Nothing is back-dated.")
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        json.dump(res, open(a.out, "w"), indent=2, default=str)
        print(json.dumps({k: res[k] for k in ("ref_rows", "status", "coverage")},
                         indent=1, default=str))
        return res

    os.makedirs(LIFE_DIR, exist_ok=True)
    arms = {}
    per_fill = []
    for ttl_name, ttl in TTLS:
        life = []
        allots = []
        for slug in covered:
            rows = sc[slug]
            dead = pou.window_end(slug) - DEADLINE_S
            lots = resting_arm(rows, dead, ttl, life)
            for c in CEILINGS:
                import copy
                ls = label_exits(rows, copy.deepcopy(lots), dead, c)
                for l in ls:
                    l["ceiling"] = str(c); l["slug"] = slug
                allots += ls
        with open(f"{LIFE_DIR}/lifecycle_{ttl_name}.jsonl", "w") as fh:
            for e in life:
                fh.write(json.dumps(e, default=str) + "\n")
        arms[ttl_name] = {"events": len(life),
                          "places": sum(1 for e in life if e["ev"] == "PLACE"),
                          "fills": sum(1 for e in life if e["ev"] == "FILL"),
                          "expires": sum(1 for e in life if e["ev"] == "EXPIRE"),
                          "cancels": sum(1 for e in life if e["ev"] == "CANCEL"),
                          "repriced": 0,
                          "labels": dict(Counter(f'{l["ceiling"]}:{l["label"]}' for l in allots))}
        if ttl is None:        # the strictest arm carries the pre-fill study
            for l in allots:
                rows = sc[l["slug"]]
                rec = {"slug": l["slug"], "t": l["t"], "side": l["side"],
                       "ceiling": l["ceiling"], "label": l["label"],
                       "quote_age_s": round(l["quote_age_s"], 3),
                       "f": features(l, rows, rts, mids, skew_bound)}
                if l["label"] == "TOXIC":
                    rec["sequence"] = event_sequence(l, rows, rts, mids)
                per_fill.append(rec)

    # P(signal | label) at every pre-registered level, plus lead time
    cond = {}
    for c in CEILINGS:
        cs = str(c)
        sub = [p for p in per_fill if p["ceiling"] == cs]
        n_t = sum(1 for p in sub if p["label"] == "TOXIC")
        n_q = len(sub) - n_t
        blk = {"n_toxic": n_t, "n_qualifying": n_q, "signals": {}}
        for feat, lv in LEVELS.items():
            for L in lv:
                for h in HORIZONS:
                    key = f"{feat}>={L}@{h}s"
                    pt = pq = 0
                    leads_t = []
                    for p in sub:
                        v = p["f"][str(h)].get(feat)
                        hit = isinstance(v, (int, float)) and v >= L
                        if hit:
                            if p["label"] == "TOXIC":
                                pt += 1; leads_t.append(h)
                            else:
                                pq += 1
                    blk["signals"][key] = {
                        "P_signal_given_toxic": round(pt / n_t, 4) if n_t else None,
                        "P_signal_given_qualifying": round(pq / n_q, 4) if n_q else None,
                        "n_toxic_with_signal": pt, "n_qualifying_with_signal": pq,
                        "lead_s": h, "actionability": actionability(h),
                        "avoidable_toxic": pt if h >= RTT_P90 else 0,
                        "sacrificed_good": pq if h >= RTT_P90 else 0}
        # sweep / direction flags are boolean, handled separately
        for h in HORIZONS:
            for feat in ("sweep_seen", "ext_before_pmus"):
                pt = sum(1 for p in sub if p["label"] == "TOXIC" and p["f"][str(h)].get(feat) is True)
                pq = sum(1 for p in sub if p["label"] == "QUALIFYING" and p["f"][str(h)].get(feat) is True)
                unres = sum(1 for p in sub if p["f"][str(h)].get(feat) == "UNRESOLVED_CLOCK")
                blk["signals"][f"{feat}@{h}s"] = {
                    "P_signal_given_toxic": round(pt / n_t, 4) if n_t else None,
                    "P_signal_given_qualifying": round(pq / n_q, 4) if n_q else None,
                    "n_toxic_with_signal": pt, "n_qualifying_with_signal": pq,
                    "unresolved_clock": unres,
                    "lead_s": h, "actionability": actionability(h),
                    "avoidable_toxic": pt if h >= RTT_P90 else 0,
                    "sacrificed_good": pq if h >= RTT_P90 else 0}
        cond[cs] = blk

    res["arms"] = arms
    res["conditional"] = cond
    res["fills_studied"] = len(per_fill)
    res["status"] = "MEASURED"
    res["note"] = ("No threshold selected, no combination optimized, no order "
                   "submitted. Counts are reported at every pre-registered level.")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump({**res, "per_fill": per_fill}, open(a.out, "w"), indent=2, default=str)
    print(json.dumps({"arms": arms, "fills": len(per_fill),
                      "windows": covered}, indent=1, default=str)[:3000])
    return res


if __name__ == "__main__":
    main()
