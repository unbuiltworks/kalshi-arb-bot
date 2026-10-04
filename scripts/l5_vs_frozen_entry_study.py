"""L5's ENTRIES vs THE FROZEN ENGINE'S STRANDED ONES.

THE QUESTION. The frozen Gemini engine opens readily and strands
directional residuals: on the recorded sample it OPENed UP at 0.64, 0.68,
0.75, 0.80 and 0.89 and completed almost none of them. L5, on the same
instrument family, completes. So what decision variable separates an entry
that can be completed from one that cannot?

THE CONTROL IS NOT TOUCHED. Nothing here changes a frozen parameter, and
nothing here is fitted to our few windows. This reads L5's own history and
our own recorded OPEN states and compares them.

WHAT THE DATA IS. 5,500 authenticated L5 trades (2026-09-14/15), every one
a BUY -- no sells at all, which is itself consistent with the
accumulate-and-settle thesis rather than with flipping. 5,231 are on
`btc-updown-15m`, the direct analogue of Gemini BTC15M. Each row carries
timestamp, price, outcome (Up/Down), size, and a slug whose trailing epoch
is the window START, so time-to-expiry is (start + 900) - timestamp.

WHAT THIS CANNOT ANSWER, stated rather than filled in. Preceding BTC price
movement and excursion/reversal context need a price tape for the
underlying over those windows. L5's own fills give only the path of their
own trading, which is an endogenous proxy and cannot establish that they
entered AFTER an excursion. Those two questions are reported as
UNANSWERABLE from this corpus instead of being answered from a proxy.

Accounting label: historical observation of a third party plus historical
replay of ours. No order was sent.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import statistics
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

LABEL = "L5_VS_FROZEN_ENTRY_STUDY_V1"
L5_TAPE = "analysis/l5/observation/recent/l5_recent_activity.json.gz"
WINDOW_S = {"btc-updown-15m": 900, "btc-updown-5m": 300,
            "eth-updown-15m": 900, "eth-updown-5m": 300}


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def load_l5(path: str, family: str) -> Dict[str, List[Dict[str, Any]]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        rows = json.load(fh)
    by: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        slug = str(r.get("slug") or "")
        if not slug.startswith(family + "-"):
            continue
        tail = slug.rsplit("-", 1)[-1]
        if not tail.isdigit():
            continue
        start = int(tail)
        ts = int(r.get("timestamp") or 0)
        by[slug].append({
            "ts": ts, "tte": start + WINDOW_S[family] - ts,
            "price": _f(r.get("price")),
            "outcome": str(r.get("outcome") or "").upper(),
            "size": _f(r.get("size")) or 0.0,
            "usdc": _f(r.get("usdcSize")) or 0.0,
            "side": str(r.get("side") or ""),
        })
    for s in by:
        by[s].sort(key=lambda x: x["ts"])
    return dict(by)


def sequence(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One window's L5 sequence, described in the engine's own vocabulary."""
    qty = {"UP": 0.0, "DOWN": 0.0}
    cost = {"UP": 0.0, "DOWN": 0.0}
    first = trades[0]
    entry_side = first["outcome"]
    switches = 0
    prev = entry_side
    first_opposite: Optional[Dict[str, Any]] = None
    same_side_adds_before_opposite = 0
    pair_costs: List[float] = []
    imbalance_before_opposite: Optional[Dict[str, Any]] = None

    for t in trades:
        s = t["outcome"]
        if s not in qty:
            continue
        if s != prev:
            switches += 1
            prev = s
        if s != entry_side and first_opposite is None:
            first_opposite = dict(t)
            imbalance_before_opposite = {
                "entry_side_qty": qty[entry_side],
                "entry_side_avg": (cost[entry_side] / qty[entry_side]
                                   if qty[entry_side] else None),
                "opposite_qty": qty[s],
                "tte": t["tte"],
                "seconds_after_entry": first["tte"] - t["tte"],
            }
        elif s == entry_side and first_opposite is None:
            if t is not first:
                same_side_adds_before_opposite += 1
        qty[s] += t["size"]
        cost[s] += t["size"] * (t["price"] or 0.0)
        if qty["UP"] > 0 and qty["DOWN"] > 0:
            pair_costs.append(cost["UP"] / qty["UP"] + cost["DOWN"]
                              / qty["DOWN"])

    matched = min(qty["UP"], qty["DOWN"])
    resid_side = ("UP" if qty["UP"] > qty["DOWN"]
                  else ("DOWN" if qty["DOWN"] > qty["UP"] else None))
    return {
        "n_trades": len(trades),
        "entry_price": first["price"], "entry_side": entry_side,
        "entry_tte": first["tte"], "entry_size": first["size"],
        "entry_favored": (None if first["price"] is None
                          else first["price"] > 0.50),
        "side_switches": switches,
        "alternates_more_than_once": switches > 1,
        "same_side_adds_before_opposite": same_side_adds_before_opposite,
        "first_opposite": first_opposite,
        "imbalance_before_opposite": imbalance_before_opposite,
        "ever_bought_opposite": first_opposite is not None,
        "up_qty": qty["UP"], "down_qty": qty["DOWN"],
        "matched_qty": matched,
        "completed": matched > 0,
        "residual_side": resid_side,
        "residual_qty": abs(qty["UP"] - qty["DOWN"]),
        "final_pair_cost": (pair_costs[-1] if pair_costs else None),
        "best_pair_cost": (min(pair_costs) if pair_costs else None),
        "pair_cost_after_each_fill": [round(p, 4) for p in pair_costs[:40]],
    }


def _stats(xs: List[float]) -> Dict[str, Any]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "min": round(min(xs), 4),
            "p25": round(sorted(xs)[len(xs) // 4], 4),
            "median": round(statistics.median(xs), 4),
            "p75": round(sorted(xs)[3 * len(xs) // 4], 4),
            "max": round(max(xs), 4),
            "mean": round(statistics.fmean(xs), 4)}


def our_open_states() -> List[Dict[str, Any]]:
    """OUR recorded OPEN/EXTEND states, from the durable cohort ledgers.

    These are the real thing: every one was constructed by the frozen
    policy from a real executable ladder.
    """
    import glob
    import os
    from src.gemini_execution import ci15m_budget as BUD
    out: List[Dict[str, Any]] = []
    for root in ("analysis/gemini/cohorts", "analysis/gemini/shadow_cohorts"):
        for d in sorted(glob.glob(os.path.join(root, "*"))):
            # THE TELEMETRY carries tte and the policy's own action on EVERY
            # decision, which the PLACE row does not. Both are durable
            # records written by the engine, so both are used.
            tel = os.path.join(d, "telemetry.jsonl")
            if os.path.exists(tel):
                for r in BUD._rows(tel):
                    if r.get("policy_action") not in ("OPEN", "EXTEND"):
                        continue
                    px = _f(r.get("submitted_price"))
                    out.append({
                        "cohort": os.path.basename(d),
                        "ticker": r.get("ticker"),
                        "action": r.get("policy_action"),
                        "side": r.get("policy_side"),
                        "price": px,
                        "tte": _f(r.get("tte")),
                        "required_complement_for_0.96": _f(
                            r.get("required_complement_price_for_0.96")),
                        "favored": (None if px is None else px > 0.50),
                        "source": "telemetry",
                    })
            led = os.path.join(d, "ledger.jsonl")
            if not os.path.exists(led):
                continue
            for r in BUD._rows(led):
                if r.get("kind") != "PLACE":
                    continue
                i = r.get("intent") or {}
                out.append({
                    "cohort": os.path.basename(d),
                    "ticker": r.get("ticker"),
                    "action": i.get("action"), "side": i.get("side"),
                    "price": _f(i.get("price")),
                    "tte": _f(r.get("tte")),
                    "required_complement_for_0.96": _f(
                        i.get("required_complement_price_for_0.96")),
                    "favored": (None if i.get("price") is None
                                else _f(i.get("price")) > 0.50),
                })
    seen = set()
    uniq = []
    for o in out:
        k = (o.get("ticker"), o.get("action"), o.get("price"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(o)
    return uniq


def run(family: str = "btc-updown-15m") -> Dict[str, Any]:
    by = load_l5(L5_TAPE, family)
    seqs = {s: sequence(t) for s, t in by.items() if t}
    comp = [v for v in seqs.values() if v["completed"]]
    strand = [v for v in seqs.values() if not v["completed"]]
    ours = our_open_states()
    our_opens = [o for o in ours if o["action"] == "OPEN"]

    def ent(rs: List[Dict[str, Any]], k: str) -> List[float]:
        return [r[k] for r in rs if r.get(k) is not None]

    report: Dict[str, Any] = {
        "label": LABEL, "family": family, "l5_tape": L5_TAPE,
        "l5_windows": len(seqs),
        "l5_completed_windows": len(comp),
        "l5_stranded_windows": len(strand),
        "l5_completion_rate": (None if not seqs
                               else round(len(comp) / len(seqs), 4)),
        "entry_price": {
            "l5_completed": _stats(ent(comp, "entry_price")),
            "l5_stranded": _stats(ent(strand, "entry_price")),
            "ours_open": _stats([o["price"] for o in our_opens]),
        },
        "entry_tte_s": {
            "l5_completed": _stats(ent(comp, "entry_tte")),
            "l5_stranded": _stats(ent(strand, "entry_tte")),
            "ours_open": _stats([o["tte"] for o in our_opens
                                 if o["tte"] is not None]),
        },
        "favored_side_at_entry": {
            "l5_completed_pct_favored": _pct(
                sum(1 for r in comp if r["entry_favored"]), len(comp)),
            "l5_stranded_pct_favored": _pct(
                sum(1 for r in strand if r["entry_favored"]), len(strand)),
            "ours_pct_favored": _pct(
                sum(1 for o in our_opens if o["favored"]), len(our_opens)),
            "definition": "favored = entry price > 0.50, i.e. paying up for "
                          "the side the market already prefers",
        },
        "same_side_adds_before_opposite": {
            "l5_completed": _stats(
                [float(r["same_side_adds_before_opposite"]) for r in comp]),
            "l5_stranded": _stats(
                [float(r["same_side_adds_before_opposite"])
                 for r in strand]),
        },
        "side_switches": {
            "l5_completed": _stats([float(r["side_switches"])
                                    for r in comp]),
            "l5_stranded": _stats([float(r["side_switches"])
                                   for r in strand]),
            "l5_pct_completed_alternating_more_than_once": _pct(
                sum(1 for r in comp if r["alternates_more_than_once"]),
                len(comp)),
            "ours_shape": ("OPEN -> EXTEND -> COMPLEMENT at most once per "
                           "window; the frozen engine cannot alternate, and "
                           "PERSIST4 caps it at 2 submissions per window"),
        },
        "first_opposite_purchase": {
            "l5_tte_at_first_opposite": _stats(
                [r["first_opposite"]["tte"] for r in comp
                 if r.get("first_opposite")]),
            "l5_seconds_after_entry": _stats(
                [r["imbalance_before_opposite"]["seconds_after_entry"]
                 for r in comp if r.get("imbalance_before_opposite")]),
            "l5_entry_side_qty_before_opposite": _stats(
                [r["imbalance_before_opposite"]["entry_side_qty"]
                 for r in comp if r.get("imbalance_before_opposite")]),
        },
        "pair_cost": {
            "l5_final_completed": _stats(ent(comp, "final_pair_cost")),
            "l5_best_completed": _stats(ent(comp, "best_pair_cost")),
            "frozen_ceiling": 0.96,
            "l5_pct_completed_final_pair_cost_at_or_below_0_96": _pct(
                sum(1 for r in comp
                    if r["final_pair_cost"] is not None
                    and r["final_pair_cost"] <= 0.96), len(comp)),
        },
        "would_l5_have_entered_our_states": _analogue(seqs, our_opens),
        "unanswerable_from_this_corpus": [
            "preceding BTC price movement before entry",
            "entry after excursion/reversal vs arbitrary availability",
            ("both need an underlying price tape over those windows; L5's "
             "own fills are an endogenous proxy and cannot establish that "
             "an entry followed an excursion"),
        ],
        "accounting_label": ("historical observation of a third party plus "
                            "historical replay of ours"),
        "control_untouched": ("no frozen parameter was read for tuning and "
                             "none was changed; the Gemini policy remains "
                             "the control"),
    }
    report["l5_sequences_sample"] = {
        s: v for s, v in list(seqs.items())[:5]}
    return report


def _pct(n: int, d: int) -> Optional[float]:
    return None if not d else round(100.0 * n / d, 1)


def _analogue(seqs: Dict[str, Any],
              our_opens: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Did L5 ever FIRST-ENTER near our price and time-to-expiry?

    Our own OPENs are the probe: for each, count L5 windows whose FIRST
    entry was within 5 cents and 120 seconds, and how many of those L5
    windows then completed.
    """
    out = []
    for o in our_opens:
        if o["price"] is None:
            continue
        near = [v for v in seqs.values()
                if v["entry_price"] is not None
                and abs(v["entry_price"] - o["price"]) <= 0.05
                and (o["tte"] is None
                     or abs(v["entry_tte"] - o["tte"]) <= 120)]
        same_side_favored = [v for v in near
                             if v["entry_favored"] == o["favored"]]
        out.append({
            "ours": {k: o[k] for k in ("cohort", "ticker", "side", "price",
                                       "tte", "favored")},
            "l5_first_entries_within_5c_and_120s": len(near),
            "of_those_same_favored_status": len(same_side_favored),
            "of_those_completed": sum(1 for v in near if v["completed"]),
            "l5_completion_rate_in_that_neighbourhood": _pct(
                sum(1 for v in near if v["completed"]), len(near)),
        })
    return {"per_our_open": out,
            "labels": ("a NEIGHBOURHOOD count, not a counterfactual. It "
                       "says whether L5 ever started a window from a "
                       "similar price and time, and how those turned out "
                       "-- not what L5 would have done on OUR book.")}


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="btc-updown-15m")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = run(a.family)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    print("%s  family=%s  L5 windows=%d  completed=%d (%s%%)  stranded=%d"
          % (rep["label"], rep["family"], rep["l5_windows"],
             rep["l5_completed_windows"],
             (rep["l5_completion_rate"] or 0) * 100,
             rep["l5_stranded_windows"]))
    for k in ("entry_price", "entry_tte_s"):
        print("\n%s" % k.upper())
        for who, st in rep[k].items():
            print("   %-14s %s" % (who, st))
    f = rep["favored_side_at_entry"]
    print("\nFAVORED SIDE AT ENTRY (price > 0.50)")
    print("   L5 completed %s%% | L5 stranded %s%% | OURS %s%%"
          % (f["l5_completed_pct_favored"], f["l5_stranded_pct_favored"],
             f["ours_pct_favored"]))
    print("\nSIDE SWITCHES  L5 completed %s | stranded %s | "
          "L5 completed alternating >1: %s%%"
          % (rep["side_switches"]["l5_completed"].get("median"),
             rep["side_switches"]["l5_stranded"].get("median"),
             rep["side_switches"][
                 "l5_pct_completed_alternating_more_than_once"]))
    pc = rep["pair_cost"]
    print("\nL5 PAIR COST (completed): final %s | best %s | "
          "%% at or below .96: %s"
          % (pc["l5_final_completed"].get("median"),
             pc["l5_best_completed"].get("median"),
             pc["l5_pct_completed_final_pair_cost_at_or_below_0_96"]))
    print("\nWOULD L5 HAVE ENTERED OUR STATES (5c / 120s neighbourhood)")
    for r in rep["would_l5_have_entered_our_states"]["per_our_open"]:
        o = r["ours"]
        print("   ours %-5s %-6s @ %-5s tte %-6s -> L5 near: %-3s  "
              "completed %-3s (%s%%)"
              % (o["side"], o["ticker"] or "", o["price"], o["tte"],
                 r["l5_first_entries_within_5c_and_120s"],
                 r["of_those_completed"],
                 r["l5_completion_rate_in_that_neighbourhood"]))
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
