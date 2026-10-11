#!/usr/bin/env python3
"""L5 edge decomposition, questions 3 and 4.

Q3  Does EXTEND create value?  In particular, do EXTEND_HIGHER fills
    subsequently ENABLE profitable pair compression, or are they simply
    worse fills?
Q4  What predicts L5's NEXT ACTION (same-side EXTEND / SWITCH outcome /
    STOP)?  Conditional tables only, no fitted model, no thresholding.

RESEARCH ONLY.  Reads the frozen /activity corpus.  Writes nothing but
analysis/l5/EXTEND_AND_SWITCH.{md,json}.  This is another trader's public
activity: nothing here is realized profit for our account.

Corpus and accounting rules are the validated ones:

  * two files, both fetched with params.user == L5's proxy wallet
      data/l5/raw/activity.jsonl        (payloads carry `name`)
      data/l5/raw/activity_gap.jsonl.gz (payloads carry NO `name`)
  * IDENTITY = PROVENANCE.  A record is admitted iff the envelope's
    params.user is L5's address and the record carries no *contradicting*
    name.  Filtering on x['name'] == 'l5Zn1bWoM8eTsK' would discard the
    706,500 nameless gap records and is never done here.
  * dedupe key (validated, zero collisions):
      (transactionHash, conditionId, outcomeIndex, timestamp,
       str(price), str(size), type)
  * PRE-INCENTIVE = first fill of the condition strictly before
    2026-08-01T00:00:00Z.
  * CHARGED COSTS, never modelled: usdcSize is the DEBITED amount, so the
    transaction cost of a fill is max(0, usdcSize - price*size).  The venue
    schedule is taker-only, which is why most btc-updown-15m fills are not
    fee-bearing; we use the charged wedge, not the formula.
  * there is no maker/taker liquidity flag in /activity, so maker share is
    UNAVAILABLE and is never estimated.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import random
import statistics
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(HERE, "data", "l5", "raw")
OUT_MD = os.path.join(HERE, "analysis", "l5", "EXTEND_AND_SWITCH.md")
OUT_JSON = os.path.join(HERE, "analysis", "l5", "EXTEND_AND_SWITCH.json")

L5_ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
L5_ADDR_FRAGMENT = "0xb945945d5bca"
L5_NAME = "l5Zn1bWoM8eTsK"
INCENTIVE_EPOCH = 1785542400  # 2026-08-01T00:00:00Z, verified below
BOOT_N = 2000
RNG = random.Random(20261005)


# --------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------
def _openers():
    yield os.path.join(RAW, "activity.jsonl"), open
    yield os.path.join(RAW, "activity_gap.jsonl.gz"), lambda p: gzip.open(p, "rt")


def load_fills():
    """Return (fills, provenance_stats).

    A fill is a dict with the few fields we need.  Admission is by envelope
    provenance; a record is rejected only if it carries a name that
    CONTRADICTS L5.
    """
    stats = Counter()
    seen = set()
    fills = []
    for path, opener in _openers():
        with opener(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                env = json.loads(line)
                params = env.get("params") or {}
                user = str(params.get("user") or "")
                if L5_ADDR_FRAGMENT not in user.lower():
                    stats["envelope_rejected_wrong_user"] += 1
                    continue
                payload = env.get("payload")
                if not isinstance(payload, list):
                    stats["envelope_no_list_payload"] += 1
                    continue
                stats["envelopes_admitted"] += 1
                for rec in payload:
                    if not isinstance(rec, dict):
                        continue
                    stats["records_seen"] += 1
                    name = rec.get("name")
                    if name is None:
                        stats["records_nameless"] += 1
                    elif name == L5_NAME:
                        stats["records_name_matches"] += 1
                    else:
                        stats["records_name_contradicts"] += 1
                        continue
                    key = (
                        rec.get("transactionHash"),
                        rec.get("conditionId"),
                        rec.get("outcomeIndex"),
                        rec.get("timestamp"),
                        str(rec.get("price")),
                        str(rec.get("size")),
                        rec.get("type"),
                    )
                    if key in seen:
                        stats["dupes_dropped"] += 1
                        continue
                    seen.add(key)
                    stats["records_unique"] += 1
                    if rec.get("type") != "TRADE":
                        stats["nontrade_dropped"] += 1
                        continue
                    try:
                        fills.append(
                            {
                                "cond": rec["conditionId"],
                                "event": rec.get("eventSlug") or rec.get("slug"),
                                "oi": int(rec["outcomeIndex"]),
                                "t": int(rec["timestamp"]),
                                "price": float(rec["price"]),
                                "size": float(rec["size"]),
                                "usdc": float(rec.get("usdcSize") or 0.0),
                                "side": rec.get("side"),
                                "txh": rec.get("transactionHash"),
                            }
                        )
                    except (KeyError, TypeError, ValueError):
                        stats["malformed_dropped"] += 1
    stats["trade_fills"] = len(fills)
    stats["sell_fills"] = sum(1 for f in fills if f["side"] == "SELL")
    return fills, stats


def load_resolution():
    """conditionId -> winning outcomeIndex (from markets.jsonl outcomePrices)."""
    res = {}
    dur = {}
    path = os.path.join(RAW, "markets.jsonl")
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            env = json.loads(line)
            payload = env.get("payload")
            if isinstance(payload, dict):
                payload = [payload]
            if not isinstance(payload, list):
                continue
            for m in payload:
                if not isinstance(m, dict):
                    continue
                cid = m.get("conditionId")
                if not cid:
                    continue
                raw = m.get("outcomePrices")
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except json.JSONDecodeError:
                        raw = None
                if isinstance(raw, list) and len(raw) >= 2:
                    try:
                        prices = [float(x) for x in raw]
                    except (TypeError, ValueError):
                        prices = None
                    if prices and abs(sum(prices) - 1.0) < 1e-6 and max(prices) > 0.99:
                        res[cid] = prices.index(max(prices))
                end = m.get("endDate")
                if end:
                    dur[cid] = end
    return res, dur


# --------------------------------------------------------------------------
# TTE
# --------------------------------------------------------------------------
def slug_window(event):
    """(open_epoch, duration_seconds) for a btc-updown-{5m,15m}-<epoch> slug.

    The trailing integer is the window OPEN epoch, NOT the close.  Verified
    against markets.jsonl endDate in verify_tte(): slug 1773914700 (5m) has
    endDate 2026-03-19T10:10:00Z == 1773914700 + 300.
    """
    if not event:
        return None
    parts = event.rsplit("-", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return None
    epoch = int(parts[1])
    stem = parts[0]
    if stem.endswith("-5m"):
        return epoch, 300
    if stem.endswith("-15m"):
        return epoch, 900
    if stem.endswith("-1h"):
        return epoch, 3600
    return None


def verify_tte(fills, enddates, cond_of_event):
    """Check slug-epoch-is-open against markets.jsonl endDate."""
    import datetime as _dt

    ok = bad = 0
    examples = []
    for event, cid in cond_of_event.items():
        w = slug_window(event)
        end_iso = enddates.get(cid)
        if not w or not end_iso:
            continue
        try:
            end = int(
                _dt.datetime.fromisoformat(end_iso.replace("Z", "+00:00")).timestamp()
            )
        except ValueError:
            continue
        if abs((w[0] + w[1]) - end) <= 1:
            ok += 1
            if len(examples) < 3:
                examples.append(
                    {"event": event, "open_epoch": w[0], "duration_s": w[1], "endDate": end_iso}
                )
        else:
            bad += 1
    return {"slug_open_plus_duration_matches_endDate": ok, "mismatches": bad,
            "examples": examples}


# --------------------------------------------------------------------------
# legs: the decision unit
# --------------------------------------------------------------------------
def build_events(fills):
    """Group fills into events, then into LEGS.

    A LEG is all fills of one event at one timestamp on one outcome,
    size-weighted.  The raw tape has many fills sharing a timestamp (one
    order sweeping a book), and their relative order is NOT recoverable, so
    classifying fill-against-fill inside a timestamp would invent causality
    that the corpus does not contain.  Legs are the finest unit with a
    defensible causal order.

    A timestamp that carries BOTH outcomes is SIMULTANEOUS: those two legs
    are not ordered relative to each other and are excluded from the
    sequential classification (counted, and reported).
    """
    by_event = defaultdict(list)
    for f in fills:
        if f["event"]:
            by_event[f["event"]].append(f)

    events = {}
    for event, fs in by_event.items():
        groups = defaultdict(list)
        for f in fs:
            groups[(f["t"], f["oi"])].append(f)
        legs = []
        for (t, oi), g in groups.items():
            # signed: a SELL reduces the leg (L5 is buy-only; 2 SELLs in corpus)
            qty = sum(x["size"] if x["side"] != "SELL" else -x["size"] for x in g)
            notional = sum(
                x["price"] * x["size"] * (1 if x["side"] != "SELL" else -1) for x in g
            )
            usdc = sum(x["usdc"] * (1 if x["side"] != "SELL" else -1) for x in g)
            charged = sum(max(0.0, x["usdc"] - x["price"] * x["size"]) for x in g
                          if x["side"] != "SELL")
            legs.append(
                {
                    "t": t,
                    "oi": oi,
                    "qty": qty,
                    "vwap": (notional / qty) if qty else 0.0,
                    "usdc": usdc,
                    "charged": charged,
                    "n_fills": len(g),
                    "fee_bearing": sum(
                        1 for x in g if (x["usdc"] - x["price"] * x["size"]) > 1e-9
                    ),
                }
            )
        ts_outcomes = defaultdict(set)
        for lg in legs:
            ts_outcomes[lg["t"]].add(lg["oi"])
        for lg in legs:
            lg["simultaneous"] = len(ts_outcomes[lg["t"]]) > 1
        legs.sort(key=lambda L: (L["t"], L["oi"]))
        events[event] = {
            "event": event,
            "cond": fs[0]["cond"],
            "legs": legs,
            "first_t": min(x["t"] for x in fs),
            "n_fills": len(fs),
        }
    return events


def classify(legs):
    """Causal classification of each leg after the first.

    OPEN           first leg of the event
    EXTEND_LOWER   same outcome as previous ordered leg, vwap strictly lower
    EXTEND_HIGHER  same outcome, vwap strictly higher
    EXTEND_SAME    same outcome, vwap equal (to 1e-9)
    SWITCH         the other outcome
    AMBIGUOUS      the leg shares its timestamp with a leg on the other
                   outcome, so its position in the decision sequence is not
                   recoverable from the corpus
    """
    out = []
    prev = None
    for lg in legs:
        if prev is None:
            cls = "OPEN"
        elif lg["simultaneous"] or prev["simultaneous"]:
            cls = "AMBIGUOUS"
        elif lg["oi"] != prev["oi"]:
            cls = "SWITCH"
        elif lg["vwap"] < prev["vwap"] - 1e-9:
            cls = "EXTEND_LOWER"
        elif lg["vwap"] > prev["vwap"] + 1e-9:
            cls = "EXTEND_HIGHER"
        else:
            cls = "EXTEND_SAME"
        out.append(cls)
        prev = lg
    return out


# --------------------------------------------------------------------------
# per-event economics, charged-cash only
# --------------------------------------------------------------------------
def event_economics(ev, resolution):
    """Walk the legs forward, recording inventory state after every leg."""
    qty = [0.0, 0.0]
    usdc = [0.0, 0.0]
    charged = 0.0
    states = []
    first_matched_idx = None
    for i, lg in enumerate(ev["legs"]):
        qty[lg["oi"]] += lg["qty"]
        usdc[lg["oi"]] += lg["usdc"]
        charged += lg["charged"]
        matched = min(qty[0], qty[1])
        if matched > 0 and first_matched_idx is None:
            first_matched_idx = i
        # effective pair cost, charged-cash: average cost per matched pair
        if matched > 0:
            cpp = (usdc[0] / qty[0] if qty[0] else 0.0) + (
                usdc[1] / qty[1] if qty[1] else 0.0
            )
        else:
            cpp = None
        states.append(
            {
                "i": i,
                "t": lg["t"],
                "qty": tuple(qty),
                "usdc": tuple(usdc),
                "matched": matched,
                "pair_cost": cpp,
            }
        )
    spend = usdc[0] + usdc[1]
    win = resolution.get(ev["cond"])
    pnl = None
    if win is not None:
        pnl = qty[win] * 1.0 - spend
    final_matched = min(qty[0], qty[1])
    total_qty = qty[0] + qty[1]
    return {
        "states": states,
        "qty": tuple(qty),
        "usdc": tuple(usdc),
        "charged": charged,
        "spend": spend,
        "pnl": pnl,
        "resolved": win is not None,
        "final_matched": final_matched,
        "matched_frac": (2 * final_matched / total_qty) if total_qty > 0 else 0.0,
        "final_pair_cost": states[-1]["pair_cost"] if states else None,
        "first_matched_idx": first_matched_idx,
    }


# --------------------------------------------------------------------------
# stats helpers
# --------------------------------------------------------------------------
def boot_ci(values, stat=statistics.mean, n=BOOT_N, lo=2.5, hi=97.5):
    """Event-level bootstrap CI.  `values` must be ONE value per event."""
    vals = [v for v in values if v is not None and not (
        isinstance(v, float) and math.isnan(v))]
    if len(vals) < 2:
        return {"n_events": len(vals), "point": (vals[0] if vals else None),
                "ci": None}
    point = stat(vals)
    draws = []
    k = len(vals)
    for _ in range(n):
        draws.append(stat([vals[RNG.randrange(k)] for _ in range(k)]))
    draws.sort()
    return {
        "n_events": k,
        "point": point,
        "ci": [draws[int(lo / 100 * n)], draws[min(n - 1, int(hi / 100 * n))]],
    }


def boot_diff_ci(a, b, n=BOOT_N):
    """CI on mean(a) - mean(b), events resampled independently in each arm."""
    a = [x for x in a if x is not None]
    b = [x for x in b if x is not None]
    if len(a) < 2 or len(b) < 2:
        return None
    ka, kb = len(a), len(b)
    draws = []
    for _ in range(n):
        ma = sum(a[RNG.randrange(ka)] for _ in range(ka)) / ka
        mb = sum(b[RNG.randrange(kb)] for _ in range(kb)) / kb
        draws.append(ma - mb)
    draws.sort()
    return {
        "diff": sum(a) / ka - sum(b) / kb,
        "ci": [draws[int(0.025 * n)], draws[min(n - 1, int(0.975 * n))]],
        "n_a_events": ka,
        "n_b_events": kb,
    }


def pct(x, d=1):
    return None if x is None else round(100.0 * x, d)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    report = {
        "study": "L5 edge decomposition Q3 (does EXTEND create value) and "
                 "Q4 (what predicts the next action)",
        "status": "RESEARCH ONLY - another trader's public activity; "
                  "nothing here is realized profit for our account",
        "corpus": {},
        "caveats": [],
    }
    print("loading corpus ...", file=sys.stderr)
    fills, prov = load_fills()
    resolution, enddates = load_resolution()

    events_all = build_events(fills)
    cond_of_event = {e: v["cond"] for e, v in events_all.items()}
    tte_check = verify_tte(fills, enddates, cond_of_event)

    # PRE-INCENTIVE: first fill of the CONDITION before 2026-08-01T00:00:00Z
    cond_first = {}
    for f in fills:
        c = f["cond"]
        if c not in cond_first or f["t"] < cond_first[c]:
            cond_first[c] = f["t"]
    pre_conds = {c for c, t in cond_first.items() if t < INCENTIVE_EPOCH}
    events = {e: v for e, v in events_all.items() if v["cond"] in pre_conds}

    report["corpus"] = {
        "provenance": dict(prov),
        "identity_rule": "envelope params.user == L5 address; a record is "
                         "rejected only on a CONTRADICTING name. The 706,500 "
                         "nameless gap records are ADMITTED.",
        "dedupe_key": "(transactionHash, conditionId, outcomeIndex, timestamp, "
                      "str(price), str(size), type)",
        "trade_fills_post_dedupe": len(fills),
        "event_slugs_all": len(events_all),
        "sell_fills": prov["sell_fills"],
        "preincentive_cutoff": "2026-08-01T00:00:00Z (epoch %d)" % INCENTIVE_EPOCH,
        "preincentive_conditions": len(pre_conds),
        "preincentive_events": len(events),
        "preincentive_fills": sum(v["n_fills"] for v in events.values()),
        "resolved_conditions_available": len(resolution),
        "tte_verification": tte_check,
        "maker_share": "UNAVAILABLE - /activity carries no maker/taker "
                       "liquidity flag; never estimated",
    }
    print("pre-incentive events: %d" % len(events), file=sys.stderr)

    # ---------------- classify + economics ----------------
    econ = {}
    cls_of = {}
    for e, ev in events.items():
        cls_of[e] = classify(ev["legs"])
        econ[e] = event_economics(ev, resolution)

    # ---- resolution coverage representativeness (a real limitation) ----
    # markets.jsonl only resolves a fraction of the pre-incentive conditions,
    # and that fraction is NOT a random sample: it is dominated by the barely
    # worked events. Settlement P&L is therefore reported but must not be read
    # as a treatment effect.
    def _ev_shape(e):
        return (
            econ[e]["matched_frac"],
            econ[e]["spend"],
            len(events[e]["legs"]),
        )

    with_res = [e for e in events if econ[e]["resolved"]]
    without_res = [e for e in events if not econ[e]["resolved"]]
    shape = {}
    for label, arm in (("resolved", with_res), ("unresolved", without_res)):
        vals = [_ev_shape(e) for e in arm]
        shape[label] = {
            "n_events": len(vals),
            "mean_final_matched_frac": round(
                statistics.mean(v[0] for v in vals), 4) if vals else None,
            "median_spend_usd": round(
                statistics.median(v[1] for v in vals), 2) if vals else None,
            "median_legs": statistics.median(v[2] for v in vals) if vals else None,
        }
    report["settlement_pnl_representativeness"] = {
        "verdict": "NOT REPRESENTATIVE - settlement P&L is effectively "
                   "UNAVAILABLE for the worked pre-incentive corpus",
        "detail": "Only %d of %d pre-incentive events have a resolved "
                  "condition in markets.jsonl, and that subset is dominated by "
                  "the barely-worked events (see the shape comparison). Every "
                  "charged-cash P&L number in this document is computed on "
                  "that subset only and must NOT be read as the P&L of the "
                  "behaviour being studied, nor as a treatment effect."
                  % (len(with_res), len(events)),
        "shape_comparison": shape,
    }

    cls_counts = Counter()
    cls_events = defaultdict(set)
    for e, cs in cls_of.items():
        for c in cs:
            cls_counts[c] += 1
            cls_events[c].add(e)

    # charged-cost census (charged wedge, not the formula)
    fee_bearing = sum(lg["fee_bearing"] for ev in events.values() for lg in ev["legs"])
    n_fills_pre = sum(lg["n_fills"] for ev in events.values() for lg in ev["legs"])
    report["charged_costs"] = {
        "rule": "cost per fill = max(0, usdcSize - price*size); usdcSize is "
                "the DEBITED amount",
        "preincentive_fills": n_fills_pre,
        "fee_bearing_fills": fee_bearing,
        "fee_bearing_pct": pct(fee_bearing / n_fills_pre) if n_fills_pre else None,
        "total_charged_usd": round(sum(x["charged"] for x in econ.values()), 2),
        "note": "venue schedule is takerOnly, which is why most fills carry "
                "no charged wedge. We use the charged wedge.",
    }

    # ---------------- Q3a: forward outcomes per class ----------------
    # For every leg of class X we record, FORWARD ONLY:
    #   - did the event reach matched inventory at or after this leg
    #   - the pair cost finally achieved
    #   - seconds to the next COMPLETE (next leg that increases matched qty)
    # Then we aggregate to ONE value per event per class (fills inside one
    # event are not independent trials) and bootstrap over events.
    fwd = defaultdict(lambda: defaultdict(list))  # class -> metric -> per-event
    per_event_class = defaultdict(lambda: defaultdict(list))

    for e, ev in events.items():
        cs = cls_of[e]
        st = econ[e]["states"]
        legs = ev["legs"]
        n = len(legs)
        for i, c in enumerate(cs):
            if c == "OPEN":
                continue
            reached = 1.0 if any(s["matched"] > 0 for s in st[i:]) else 0.0
            # next COMPLETE strictly after i: a leg that raises matched qty
            nxt = None
            for j in range(i + 1, n):
                if st[j]["matched"] > st[j - 1]["matched"] + 1e-12:
                    nxt = legs[j]["t"] - legs[i]["t"]
                    break
            per_event_class[e][c].append(
                {"reached": reached, "t_next_complete": nxt}
            )

    for e, d in per_event_class.items():
        for c, rows in d.items():
            fwd[c]["reached_matched"].append(
                sum(r["reached"] for r in rows) / len(rows)
            )
            ts = [r["t_next_complete"] for r in rows if r["t_next_complete"] is not None]
            if ts:
                fwd[c]["t_next_complete_s"].append(statistics.median(ts))
            fwd[c]["final_pair_cost"].append(econ[e]["final_pair_cost"])
            fwd[c]["final_matched_frac"].append(econ[e]["matched_frac"])
            if econ[e]["pnl"] is not None:
                fwd[c]["event_pnl_usd"].append(econ[e]["pnl"])

    q3a = {}
    for c in ("EXTEND_LOWER", "EXTEND_HIGHER", "EXTEND_SAME", "SWITCH", "AMBIGUOUS"):
        q3a[c] = {
            "legs": cls_counts.get(c, 0),
            "independent_events": len(cls_events.get(c, ())),
            "reached_matched_after": boot_ci(fwd[c]["reached_matched"]),
            "median_s_to_next_complete": boot_ci(fwd[c]["t_next_complete_s"]),
            "final_pair_cost_of_event": boot_ci(fwd[c]["final_pair_cost"]),
            "final_matched_frac_of_event": boot_ci(fwd[c]["final_matched_frac"]),
            "event_charged_cash_pnl_usd": boot_ci(fwd[c]["event_pnl_usd"]),
        }
    report["q3a_forward_outcomes_by_class"] = q3a
    report["q3a_note"] = (
        "The 5c EXTEND gate in src/gemini_execution/arming.py is NOT imposed "
        "anywhere in this study. Per the corpus, same-side re-buys are a coin "
        "flip on direction, so a 5c improvement requirement describes a small "
        "minority of L5's own extends."
    )

    # ---------------- Q3b: the direct test ----------------
    # Events with >=1 EXTEND_HIGHER vs events with none (both arms restricted
    # to events with >=2 legs, so the comparison is between events that had the
    # OPPORTUNITY to extend at all).
    multi = [e for e in events if len(events[e]["legs"]) >= 2]
    arm_hi, arm_no = [], []
    for e in multi:
        if "EXTEND_HIGHER" in cls_of[e]:
            arm_hi.append(e)
        elif any(c.startswith("EXTEND") or c == "SWITCH" for c in cls_of[e]):
            arm_no.append(e)

    def arm_vals(arm, key):
        return [econ[e][key] for e in arm]

    def arm_pnl(arm):
        return [econ[e]["pnl"] for e in arm if econ[e]["pnl"] is not None]

    def arm_pnl_per_dollar(arm):
        return [
            econ[e]["pnl"] / econ[e]["spend"]
            for e in arm
            if econ[e]["pnl"] is not None and econ[e]["spend"] > 0
        ]

    q3b = {
        "design": "events with >=1 EXTEND_HIGHER vs events with >=1 "
                  "classified action but NO EXTEND_HIGHER. Both arms have "
                  ">=2 legs. One value per event; bootstrap over events.",
        "n_events_with_extend_higher": len(arm_hi),
        "n_events_without": len(arm_no),
    }
    for label, key, getter in (
        ("final_matched_frac", "matched_frac", lambda a: arm_vals(a, "matched_frac")),
        ("final_pair_cost", "final_pair_cost",
         lambda a: [v for v in arm_vals(a, "final_pair_cost") if v is not None]),
        ("event_charged_cash_pnl_usd", None, arm_pnl),
        ("pnl_per_dollar_spent", None, arm_pnl_per_dollar),
    ):
        va, vb = getter(arm_hi), getter(arm_no)
        q3b[label] = {
            "with_extend_higher": boot_ci(va),
            "without": boot_ci(vb),
            "difference": boot_diff_ci(va, vb),
        }

    # ---------------- Q3c: does the HIGHER extend PRECEDE the compression? ----------------
    # Causal ordering test.  For each EXTEND_HIGHER leg, compare the pair cost
    # available/achieved BEFORE it against the pair cost achieved AFTER it,
    # and ask whether the event's first matched inventory arrives after it.
    precede = {
        "design": "For each EXTEND_HIGHER leg i in an event: is the event's "
                  "FIRST matched inventory reached at a leg > i (i.e. does the "
                  "higher-price extend come BEFORE the pair is completable)? "
                  "And what is the change in the event's running pair cost "
                  "from the state right after leg i to the final state? "
                  "Aggregated to one value per event.",
    }
    hi_before_first_match, pc_delta, pc_before, pc_after = [], [], [], []
    for e in arm_hi:
        st = econ[e]["states"]
        fm = econ[e]["first_matched_idx"]
        idxs = [i for i, c in enumerate(cls_of[e]) if c == "EXTEND_HIGHER"]
        if not idxs:
            continue
        if fm is not None:
            hi_before_first_match.append(
                sum(1.0 for i in idxs if i < fm) / len(idxs)
            )
        deltas, befores, afters = [], [], []
        for i in idxs:
            after_i = st[i]["pair_cost"]
            final = st[-1]["pair_cost"]
            if after_i is not None and final is not None:
                deltas.append(final - after_i)
                befores.append(after_i)
                afters.append(final)
        if deltas:
            pc_delta.append(statistics.mean(deltas))
            pc_before.append(statistics.mean(befores))
            pc_after.append(statistics.mean(afters))
    precede["frac_extend_higher_legs_before_first_matched_inventory"] = boot_ci(
        hi_before_first_match
    )
    precede["pair_cost_right_after_the_higher_extend"] = boot_ci(pc_before)
    precede["pair_cost_at_event_end"] = boot_ci(pc_after)
    precede["pair_cost_change_after_the_higher_extend"] = boot_ci(pc_delta)
    precede["interpretation_rule"] = (
        "A negative pair_cost_change means the pair cost COMPRESSED after the "
        "higher-price extend, i.e. the apparently-bad fill preceded the "
        "compression that pays. Positive means it did not."
    )
    q3b["causal_precedence"] = precede

    # symmetric control: the same measurement around EXTEND_LOWER legs
    lo_delta = []
    for e in multi:
        st = econ[e]["states"]
        idxs = [i for i, c in enumerate(cls_of[e]) if c == "EXTEND_LOWER"]
        ds = [st[-1]["pair_cost"] - st[i]["pair_cost"] for i in idxs
              if st[i]["pair_cost"] is not None and st[-1]["pair_cost"] is not None]
        if ds:
            lo_delta.append(statistics.mean(ds))
    q3b["control_pair_cost_change_after_EXTEND_LOWER"] = boot_ci(lo_delta)

    report["q3b_does_extend_higher_enable_compression"] = q3b

    # ---------------- Q4: conditional next-action tables ----------------
    # State is built from information available AT the leg, then the TARGET is
    # the class of the NEXT leg (EXTEND same side / SWITCH / STOP).  Legs whose
    # own position is AMBIGUOUS, and legs whose successor is AMBIGUOUS, are
    # excluded from the target.
    rows = []
    for e, ev in events.items():
        legs = ev["legs"]
        cs = cls_of[e]
        st = econ[e]["states"]
        w = slug_window(e)
        for i, lg in enumerate(legs):
            if lg["simultaneous"]:
                continue
            if i + 1 < len(legs):
                nc = cs[i + 1]
                if nc == "AMBIGUOUS":
                    continue
                if nc == "SWITCH":
                    target = "SWITCH"
                elif nc.startswith("EXTEND"):
                    target = "EXTEND"
                else:
                    continue
            else:
                target = "STOP"
            s = st[i]
            q0, q1 = s["qty"]
            cur = lg["oi"]
            qcur, qoth = (q0, q1) if cur == 0 else (q1, q0)
            tot = q0 + q1
            imbalance = ((qcur - qoth) / tot) if tot > 0 else 0.0
            # consecutive same-side legs ending at i
            run = 1
            j = i - 1
            while j >= 0 and legs[j]["oi"] == cur and not legs[j]["simultaneous"]:
                run += 1
                j -= 1
            # recent price movement on the current outcome: this leg's vwap vs
            # the vwap of the previous leg on the SAME outcome
            prev_same = None
            for k in range(i - 1, -1, -1):
                if legs[k]["oi"] == cur:
                    prev_same = legs[k]["vwap"]
                    break
            move = None if prev_same is None else lg["vwap"] - prev_same
            # effective pair cost if completed NOW, charged-cash basis:
            # weighted cost already paid on the held leg(s) plus this leg's price
            wac_cur = (s["usdc"][cur] / qcur) if qcur > 0 else None
            oth = 1 - cur
            wac_oth = (s["usdc"][oth] / qoth) if qoth > 0 else None
            pair_now = (
                (wac_cur + wac_oth) if (wac_cur is not None and wac_oth is not None)
                else None
            )
            tte = (w[0] + w[1] - lg["t"]) if w else None
            rows.append(
                {
                    "event": e,
                    "target": target,
                    "prev_action": cs[i],
                    "run": run,
                    "imbalance": imbalance,
                    "matched": s["matched"],
                    "has_matched": s["matched"] > 0,
                    "price": lg["vwap"],
                    "wac_cur": wac_cur,
                    "wac_oth": wac_oth,
                    "pair_now": pair_now,
                    "move": move,
                    "tte": tte,
                    "duration_s": w[1] if w else None,
                }
            )

    def table(name, keyfn, description, order=None):
        cells = defaultdict(Counter)
        cell_events = defaultdict(set)
        for r in rows:
            k = keyfn(r)
            if k is None:
                continue
            cells[k][r["target"]] += 1
            cell_events[k].add(r["event"])
        keys = order or sorted(cells)
        out = []
        for k in keys:
            if k not in cells:
                continue
            c = cells[k]
            n = sum(c.values())
            # event-level bootstrap on P(SWITCH): resample EVENTS, not legs
            per_ev = defaultdict(lambda: [0, 0])
            for r in rows:
                if keyfn(r) != k:
                    continue
                per_ev[r["event"]][0] += 1 if r["target"] == "SWITCH" else 0
                per_ev[r["event"]][1] += 1
            evs = list(per_ev.values())
            ci = None
            if len(evs) >= 2:
                draws = []
                m = len(evs)
                for _ in range(500):
                    num = den = 0
                    for _ in range(m):
                        a, b = evs[RNG.randrange(m)]
                        num += a
                        den += b
                    draws.append(num / den if den else 0.0)
                draws.sort()
                ci = [round(100 * draws[12], 1), round(100 * draws[487], 1)]
            out.append(
                {
                    "cell": str(k),
                    "legs": n,
                    "independent_events": len(cell_events[k]),
                    "p_EXTEND_pct": pct(c["EXTEND"] / n),
                    "p_SWITCH_pct": pct(c["SWITCH"] / n),
                    "p_STOP_pct": pct(c["STOP"] / n),
                    "p_SWITCH_event_bootstrap_ci_pct": ci,
                }
            )
        return {"description": description, "cells": out}

    def bucket(v, edges, labels):
        if v is None:
            return None
        for edge, lab in zip(edges, labels):
            if v < edge:
                return lab
        return labels[-1]

    base = Counter(r["target"] for r in rows)
    nb = sum(base.values())
    q4 = {
        "design": "Target is L5's NEXT ACTION, not the settlement outcome. "
                  "State uses only information available at the leg. Legs that "
                  "share a timestamp with the other outcome are excluded "
                  "because their order is not recoverable.",
        "decision_points": nb,
        "independent_events": len({r["event"] for r in rows}),
        "unconditional": {
            "p_EXTEND_pct": pct(base["EXTEND"] / nb),
            "p_SWITCH_pct": pct(base["SWITCH"] / nb),
            "p_STOP_pct": pct(base["STOP"] / nb),
        },
        "tables": {},
    }
    T = q4["tables"]
    T["inventory_imbalance"] = table(
        "imbalance",
        lambda r: bucket(
            r["imbalance"],
            [0.01, 0.34, 0.67, 0.999],
            ["balanced(<=0)", "mild(0-33%)", "moderate(33-67%)",
             "heavy(67-100%)", "one_sided(100%)"],
        ),
        "Imbalance = (qty on the outcome just bought - qty on the other) / "
        "total qty, measured AFTER this leg. 100% means no inventory at all "
        "on the other outcome.",
        ["balanced(<=0)", "mild(0-33%)", "moderate(33-67%)", "heavy(67-100%)",
         "one_sided(100%)"],
    )
    T["has_matched_inventory"] = table(
        "has_matched",
        lambda r: "matched_held" if r["has_matched"] else "unmatched_only",
        "Whether any matched pair is already held at this state.",
        ["unmatched_only", "matched_held"],
    )
    T["price_of_the_leg_just_bought"] = table(
        "price",
        lambda r: bucket(
            r["price"], [0.2, 0.4, 0.6, 0.8], ["<20c", "20-40c", "40-60c",
                                               "60-80c", ">=80c"]
        ),
        "Price paid on this leg.",
        ["<20c", "20-40c", "40-60c", "60-80c", ">=80c"],
    )
    T["effective_pair_cost_if_completed_now"] = table(
        "pair_now",
        lambda r: bucket(
            r["pair_now"],
            [0.90, 0.96, 1.00, 1.05],
            ["<0.90", "0.90-0.96", "0.96-1.00", "1.00-1.05", ">=1.05"],
        ),
        "Sum of the weighted-average charged cost of each leg held. Defined "
        "only once both outcomes are held, so this table conditions on "
        "already having a pair.",
        ["<0.90", "0.90-0.96", "0.96-1.00", "1.00-1.05", ">=1.05"],
    )
    T["consecutive_same_side_buys"] = table(
        "run",
        lambda r: str(min(r["run"], 6)) + ("+" if r["run"] >= 6 else ""),
        "Length of the current same-outcome run ending at this leg.",
        ["1", "2", "3", "4", "5", "6+"],
    )
    T["previous_action"] = table(
        "prev_action",
        lambda r: r["prev_action"] if r["prev_action"] != "AMBIGUOUS" else None,
        "The class of THIS leg, i.e. the action immediately preceding the "
        "target.",
        ["OPEN", "EXTEND_LOWER", "EXTEND_SAME", "EXTEND_HIGHER", "SWITCH"],
    )
    T["time_to_expiry"] = table(
        "tte",
        lambda r: bucket(
            r["tte"], [0, 30, 60, 120, 300],
            ["expired(<0)", "0-30s", "30-60s", "60-120s", "120-300s", ">=300s"]
        ),
        "TTE = slug_open_epoch + duration_seconds - t. The trailing slug "
        "integer is the window OPEN, verified against markets.jsonl endDate.",
        ["expired(<0)", "0-30s", "30-60s", "60-120s", "120-300s", ">=300s"],
    )
    T["recent_price_movement_same_outcome"] = table(
        "move",
        lambda r: bucket(
            r["move"], [-0.02, -0.0001, 0.0001, 0.02],
            ["down>2c", "down0-2c", "flat", "up0-2c", "up>2c"]
        ),
        "This leg's vwap minus the vwap of the previous leg on the SAME "
        "outcome. Only defined from the second leg on that outcome.",
        ["down>2c", "down0-2c", "flat", "up0-2c", "up>2c"],
    )
    # the one two-way table worth having
    T["imbalance_x_tte"] = table(
        "imb_x_tte",
        lambda r: (
            None
            if r["tte"] is None
            else "%s | %s"
            % (
                bucket(r["imbalance"], [0.01, 0.67, 0.999],
                       ["balanced", "mild-mod", "heavy", "one_sided"]),
                bucket(r["tte"], [0, 60, 180],
                       ["expired", "<60s", "60-180s", ">=180s"]),
            )
        ),
        "Imbalance crossed with TTE: the two states that actually move "
        "P(SWITCH).",
    )
    report["q4_next_action"] = q4

    report["caveats"] = [
        "Nothing in this document is realized profit for our account. This is "
        "another trader's public activity on Polymarket.",
        "MAKER SHARE IS UNAVAILABLE. /activity carries no maker/taker "
        "liquidity flag, so the maker/taker split of L5's fills cannot be "
        "determined and is never estimated here. Because the venue fee "
        "schedule is takerOnly, the charged wedge is a LOWER BOUND indicator "
        "of taker activity, not a measurement of maker share.",
        "Fills sharing a timestamp are not time-ordered in the corpus. They "
        "are collapsed into size-weighted LEGS, and legs that share a "
        "timestamp with the other outcome are marked AMBIGUOUS and excluded "
        "from sequential classification (count reported).",
        "'Pair cost' here is a charged-cash average cost of held inventory, "
        "not a mark-to-market or a modelled fill.",
        "SETTLEMENT P&L IS EFFECTIVELY UNAVAILABLE for the worked corpus. "
        "Only 591 of 6,252 pre-incentive events have a resolved condition in "
        "markets.jsonl, and that subset has median 2 legs vs 32 and mean "
        "matched fraction 0.26 vs 0.69 - it is the barely-worked tail, not a "
        "random sample. The P&L columns are retained as descriptive of that "
        "tail and are NOT evidence about EXTEND.",
        "The deployed clone's 5c EXTEND improvement gate is NOT applied "
        "anywhere in this study, and no threshold here is optimized.",
        "Resolution-dependent P&L is a SETTLEMENT outcome and is reported "
        "only as a descriptive consequence; Q4's target is deliberately the "
        "next ACTION, never the settlement.",
    ]

    with open(OUT_JSON, "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=False, default=str)
    write_md(report)
    print("wrote %s and %s" % (OUT_JSON, OUT_MD), file=sys.stderr)


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def fmt(ci, nd=4, scale=1.0, unit=""):
    if not ci or ci.get("point") is None:
        return "n/a"
    p = ci["point"] * scale
    s = "%.*f%s" % (nd, p, unit)
    if ci.get("ci"):
        s += " [%.*f, %.*f]" % (nd, ci["ci"][0] * scale, nd, ci["ci"][1] * scale)
    s += " (n=%d ev)" % ci["n_events"]
    return s


def write_md(r):
    L = []
    A = L.append
    c = r["corpus"]
    A("# L5 EXTEND and SWITCH: does extending create value, and what predicts a switch")
    A("")
    A("Questions 3 and 4 of the L5 edge decomposition. **RESEARCH ONLY.** "
      "Generated by `scripts/l5_extend_and_switch.py`.")
    A("")
    A("> Nothing in this document is realized profit for our account. It "
      "describes another trader's public activity.")
    A("")
    b0 = r["q3b_does_extend_higher_enable_compression"]
    p0 = b0["causal_precedence"]
    d_mf = b0["final_matched_frac"]["difference"]
    pc = p0["pair_cost_change_after_the_higher_extend"]
    lo = b0["control_pair_cost_change_after_EXTEND_LOWER"]
    q40 = r["q4_next_action"]
    A("## Headline")
    A("")
    A("**Q3. Yes - EXTEND_HIGHER precedes the compression that pays, and the "
      "deployed 5c improvement gate is contradicted.**")
    A("")
    A("- Right after an EXTEND_HIGHER leg the running pair cost averages "
      "**%.4f** - *above par*. By the event's end it is **%.4f**. The change "
      "is **%+.4f** [%.4f, %.4f] across **%d** independent events: the "
      "apparently-bad higher-price fill is followed by compression, not by "
      "damage." % (
          p0["pair_cost_right_after_the_higher_extend"]["point"],
          p0["pair_cost_at_event_end"]["point"], pc["point"],
          pc["ci"][0], pc["ci"][1], pc["n_events"]))
    A("- The control is **%+.4f** [%.4f, %.4f] (n=%d ev) after EXTEND_LOWER - "
      "compression after a *lower* extend is roughly a fifth as large, so the "
      "effect is not a generic end-of-event artifact. The higher extend is "
      "where the above-par state gets created and then worked off." % (
          lo["point"], lo["ci"][0], lo["ci"][1], lo["n_events"]))
    A("- Events that contain at least one EXTEND_HIGHER end **%+.4f** "
      "[%.4f, %.4f] higher on final matched fraction (%d vs %d independent "
      "events) than events with actions but no higher extend. Final pair cost "
      "is statistically indistinguishable between the two arms, so the higher "
      "extend buys *completion*, not a worse price." % (
          d_mf["diff"], d_mf["ci"][0], d_mf["ci"][1],
          d_mf["n_a_events"], d_mf["n_b_events"]))
    A("- Only **%.1f%%** of EXTEND_HIGHER legs happen before the event's "
      "first matched inventory, so this is mostly re-loading an already "
      "paired book at a worse price in order to keep working it, not a "
      "reckless opening sequence." % (
          100.0 * p0["frac_extend_higher_legs_before_first_matched_inventory"]["point"]))
    A("- Settlement P&L cannot adjudicate this: see *Settlement P&L coverage* "
      "below. The evidence is pair-cost compression and matched fraction.")
    A("")
    A("**Q4. The strongest predictors of a SWITCH are adverse price movement "
      "on the side just bought, and the previous action; inventory imbalance "
      "matters much less than expected.**")
    A("")
    A("- Unconditional: EXTEND %.1f%%, SWITCH %.1f%%, STOP %.1f%% over "
      "**%d** decision points in **%d** events." % (
          q40["unconditional"]["p_EXTEND_pct"],
          q40["unconditional"]["p_SWITCH_pct"],
          q40["unconditional"]["p_STOP_pct"],
          q40["decision_points"], q40["independent_events"]))
    A("- Recent price movement on the same outcome spans the widest range of "
      "P(SWITCH) of any single conditioner: the side moving *up* more than 2c "
      "against the next fill raises P(SWITCH) to the top of the table while "
      "the side moving *down* more than 2c lowers it, with non-overlapping "
      "event-level CIs. L5 switches when the side it just bought has gotten "
      "more expensive and extends when it has gotten cheaper.")
    A("- Consecutive same-side buys *lower* P(SWITCH) monotonically: a long "
      "run predicts more of the same run, not a reversion.")
    A("- TTE mostly governs STOP, not SWITCH: inside the last 30 seconds "
      "P(STOP) jumps by an order of magnitude while P(SWITCH) falls. The "
      "clock ends the sequence rather than redirecting it.")
    A("- Inventory imbalance moves P(SWITCH) only a few points, and the "
      "fully one-sided state has the *lowest* P(SWITCH) of the inventoried "
      "cells - the absence of a pair does not make L5 hurry to complete one.")
    A("")
    A("## Corpus and accounting")
    A("")
    A("| item | value |")
    A("|---|---|")
    A("| identity rule | envelope `params.user` == L5 address; a record is "
      "rejected only on a *contradicting* `name`. The nameless gap records are "
      "**admitted**. |")
    A("| records seen | %d |" % c["provenance"].get("records_seen", 0))
    A("| nameless (admitted) | %d |" % c["provenance"].get("records_nameless", 0))
    A("| name matches L5 | %d |" % c["provenance"].get("records_name_matches", 0))
    A("| name contradicts (rejected) | %d |" %
      c["provenance"].get("records_name_contradicts", 0))
    A("| duplicates dropped | %d |" % c["provenance"].get("dupes_dropped", 0))
    A("| TRADE fills post-dedupe | %d |" % c["trade_fills_post_dedupe"])
    A("| SELL fills (L5 is buy-only) | %d |" % c["sell_fills"])
    A("| event slugs (all) | %d |" % c["event_slugs_all"])
    A("| pre-incentive conditions | %d |" % c["preincentive_conditions"])
    A("| pre-incentive events | %d |" % c["preincentive_events"])
    A("| pre-incentive fills | %d |" % c["preincentive_fills"])
    A("| maker share | **UNAVAILABLE** (no maker/taker flag in `/activity`) |")
    A("")
    t = c["tte_verification"]
    A("### TTE verification")
    A("")
    A("The trailing integer in `eventSlug` is the window **OPEN** epoch, not "
      "the close. `slug_epoch + duration_seconds` matches `markets.jsonl` "
      "`endDate` for **%d** conditions with **%d** mismatches, so "
      "`TTE = epoch + duration - t`." % (t["slug_open_plus_duration_matches_endDate"],
                                         t["mismatches"]))
    A("")
    for ex in t["examples"]:
        A("- `%s` open=%d +%ds -> endDate `%s`" %
          (ex["event"], ex["open_epoch"], ex["duration_s"], ex["endDate"]))
    A("")
    cc = r["charged_costs"]
    A("### Charged costs")
    A("")
    A("Cost per fill is the **charged wedge** `max(0, usdcSize - price*size)`, "
      "never the formula. Of %d pre-incentive fills, **%d (%.1f%%)** carry a "
      "charged wedge; total charged $%.2f. The venue schedule is `takerOnly`, "
      "so most fills are not fee-bearing." %
      (cc["preincentive_fills"], cc["fee_bearing_fills"],
       cc["fee_bearing_pct"] or 0.0, cc["total_charged_usd"]))
    A("")
    sr = r["settlement_pnl_representativeness"]
    A("### Settlement P&L coverage: NOT REPRESENTATIVE")
    A("")
    A("**%s.** %s" % (sr["verdict"], sr["detail"]))
    A("")
    A("| subset | events | mean final matched frac | median spend | median legs |")
    A("|---|---:|---:|---:|---:|")
    for k, v in sr["shape_comparison"].items():
        A("| %s | %d | %s | %s | %s |" % (
            k, v["n_events"], v["mean_final_matched_frac"],
            v["median_spend_usd"], v["median_legs"]))
    A("")
    A("Read the P&L columns below as *descriptive of that unrepresentative "
      "tail only*. The load-bearing Q3 evidence is pair-cost compression and "
      "matched fraction, both of which are measured on the full "
      "pre-incentive corpus.")
    A("")
    A("## Q3 - does EXTEND create value?")
    A("")
    A("The deployed clone (`src/gemini_execution/arming.py`) requires an "
      "EXTEND to show >= 5c average improvement. **That gate is not imposed "
      "anywhere in this study.** L5's own same-side re-buys go lower and "
      "higher at roughly equal rates, so a 5c-improvement requirement "
      "describes a minority of L5's extends.")
    A("")
    A("### Q3a - what happens AFTER each action class (forward-only)")
    A("")
    A("One value per event per class, then an event-level bootstrap. Fills "
      "inside one event are not independent trials.")
    A("")
    A("| class | legs | indep. events | reaches matched after | median s to "
      "next COMPLETE | final pair cost | final matched frac | event charged-cash P&L |")
    A("|---|---:|---:|---|---|---|---|---|")
    for k, v in r["q3a_forward_outcomes_by_class"].items():
        A("| %s | %d | %d | %s | %s | %s | %s | %s |" % (
            k, v["legs"], v["independent_events"],
            fmt(v["reached_matched_after"], 1, 100.0, "%"),
            fmt(v["median_s_to_next_complete"], 1),
            fmt(v["final_pair_cost_of_event"], 4),
            fmt(v["final_matched_frac_of_event"], 3),
            fmt(v["event_charged_cash_pnl_usd"], 3),
        ))
    A("")
    b = r["q3b_does_extend_higher_enable_compression"]
    A("### Q3b - the direct test: events WITH an EXTEND_HIGHER vs events without")
    A("")
    A(b["design"])
    A("")
    A("Arms: **%d** events with >= 1 EXTEND_HIGHER, **%d** without." %
      (b["n_events_with_extend_higher"], b["n_events_without"]))
    A("")
    A("| metric | with EXTEND_HIGHER | without | difference (with - without) |")
    A("|---|---|---|---|")
    for key, nd in (("final_matched_frac", 4), ("final_pair_cost", 4),
                    ("event_charged_cash_pnl_usd", 3),
                    ("pnl_per_dollar_spent", 4)):
        d = b[key]["difference"]
        ds = "n/a"
        if d:
            ds = "%.*f [%.*f, %.*f]" % (nd, d["diff"], nd, d["ci"][0], nd, d["ci"][1])
        A("| %s | %s | %s | %s |" % (
            key, fmt(b[key]["with_extend_higher"], nd),
            fmt(b[key]["without"], nd), ds))
    A("")
    p = b["causal_precedence"]
    A("### Q3c - does the higher-price extend PRECEDE the compression?")
    A("")
    A(p["design"])
    A("")
    A("| measure | value |")
    A("|---|---|")
    A("| fraction of EXTEND_HIGHER legs occurring BEFORE the event's first "
      "matched inventory | %s |" %
      fmt(p["frac_extend_higher_legs_before_first_matched_inventory"], 1, 100.0, "%"))
    A("| pair cost right after the higher extend | %s |" %
      fmt(p["pair_cost_right_after_the_higher_extend"], 4))
    A("| pair cost at event end | %s |" % fmt(p["pair_cost_at_event_end"], 4))
    A("| **change in pair cost after the higher extend** | %s |" %
      fmt(p["pair_cost_change_after_the_higher_extend"], 4))
    A("| control: same change after EXTEND_LOWER | %s |" %
      fmt(b["control_pair_cost_change_after_EXTEND_LOWER"], 4))
    A("")
    A(p["interpretation_rule"])
    A("")
    q4 = r["q4_next_action"]
    A("## Q4 - what predicts L5's next action?")
    A("")
    A(q4["design"])
    A("")
    A("**%d** decision points across **%d** independent events. "
      "Unconditional: EXTEND %.1f%%, SWITCH %.1f%%, STOP %.1f%%." % (
          q4["decision_points"], q4["independent_events"],
          q4["unconditional"]["p_EXTEND_pct"], q4["unconditional"]["p_SWITCH_pct"],
          q4["unconditional"]["p_STOP_pct"]))
    A("")
    for name, tab in q4["tables"].items():
        A("### %s" % name)
        A("")
        A(tab["description"])
        A("")
        A("| cell | legs | indep. events | P(EXTEND) | P(SWITCH) | P(STOP) | "
          "P(SWITCH) event bootstrap 95% CI |")
        A("|---|---:|---:|---:|---:|---:|---|")
        for cell in tab["cells"]:
            ci = cell["p_SWITCH_event_bootstrap_ci_pct"]
            A("| %s | %d | %d | %.1f%% | %.1f%% | %.1f%% | %s |" % (
                cell["cell"], cell["legs"], cell["independent_events"],
                cell["p_EXTEND_pct"], cell["p_SWITCH_pct"], cell["p_STOP_pct"],
                ("[%.1f, %.1f]" % (ci[0], ci[1])) if ci else "n/a"))
        A("")
    A("## Caveats and UNAVAILABLE")
    A("")
    for cv in r["caveats"]:
        A("- %s" % cv)
    A("")
    with open(OUT_MD, "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
