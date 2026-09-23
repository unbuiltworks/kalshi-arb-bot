"""P29 -- the realtime copyability report, from the observer's own tape.

Answers one question with numbers: by the time we LEARN what L5 did, is a
comparable PMUS action still there? Everything is computed from rows that were
priced at receipt against a PMUS snapshot taken at receipt, so no figure here
benefits from hindsight.

BACKLOG IS REPORTED SEPARATELY AND NEVER MIXED IN. Actions first seen after
their window closed carry a latency that measures when the observer started.
They are real L5 behaviour and count toward his relation statistics; they are
not evidence about copyability and are excluded from it.

The three tracks stay apart. ACTUAL_L5 is what he did. MIRROR_RAW is what
survived the trip to us. CLONE_MODEL is what the frozen engine would do
knowing only PMUS. No total sums across them.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics as st
import sys
from decimal import Decimal as D
from typing import Any, Dict, List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src import pmus_fees as fees                              # noqa: E402

TAPE = "data/l5_pmus_realtime/l5_pmus_realtime.jsonl"


def load(path: str) -> List[dict]:
    out = []
    with open(path) as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def pct(n: int, d: int) -> Any:
    return round(100.0 * n / d, 1) if d else None


def q(a: List[float], p: float) -> Any:
    if not a:
        return None
    b = sorted(a)
    return round(b[min(len(b) - 1, int(p * len(b)))], 2)


def report(rows: List[dict]) -> Dict[str, Any]:
    acts = [r for r in rows if r.get("kind") == "l5_action"]
    rt = [r for r in acts if not r.get("backlog")]
    bl = [r for r in acts if r.get("backlog")]
    clone = [r for r in rows if r.get("kind") == "clone_tick"]

    lat = [float(r["latency_s"]) for r in rt]
    cop = collections.Counter(r["copyability"]["klass"] for r in rt)
    n = len(rt)

    # side availability: a readable two-sided PMUS book on our side
    side_ok = sum(1 for r in rt if r["copyability"]["klass"] != "NO_BOOK")

    # source -> PMUS price difference, where both exist
    deltas = []
    for r in rt:
        d = r["copyability"].get("price_delta_vs_our_touch")
        if d is not None:
            deltas.append(float(d))

    # windows
    by_w = collections.defaultdict(list)
    for r in acts:
        by_w[r["window_start_utc"]].append(r)

    # L5's own behaviour (all actions, backlog included -- it IS his behaviour)
    rels = collections.Counter(r["l5"]["relation_actual"] for r in acts)
    sides = collections.Counter(r["l5"]["pmus_side"] for r in acts)
    sells = sum(1 for r in acts if r["l5"].get("is_sell"))

    # MIRROR_RAW economics, at q=1, from the last inventory snapshot per window
    mirror_pairs = mirror_resid = 0
    mirror_cost = D("0")
    rebate = D("0")
    for w, rs in by_w.items():
        last = rs[-1]["inventory"]["MIRROR_RAW"]
        mirror_pairs += int(last["matched"])
        mirror_resid += abs(int(last["residual"]))
        pc = last.get("effective_pair_cost")
        if pc and int(last["matched"]):
            mirror_cost += D(pc) * int(last["matched"])
    for r in rt:
        if r.get("mirror_relation") is not None:
            rebate += fees.maker_rebate(D(r["l5"]["l5_price"]))

    # complete mirrored sequences: a window where MIRROR_RAW got both sides
    complete_seq = 0
    for w, rs in by_w.items():
        last = rs[-1]["inventory"]["MIRROR_RAW"]
        if int(last["up"]) > 0 and int(last["dn"]) > 0:
            complete_seq += 1

    # opposite / repair availability: after a copyable action, was a copyable
    # action on the OTHER side available later in the same window?
    repair_avail = 0
    repair_windows = 0
    for w, rs in by_w.items():
        cps = [r for r in rs if not r.get("backlog")
               and r["copyability"]["klass"] == "MAKER_COPYABLE"]
        if not cps:
            continue
        repair_windows += 1
        first = cps[0]["l5"]["pmus_side"]
        if any(c["l5"]["pmus_side"] != first for c in cps[1:]):
            repair_avail += 1

    clone_acts = collections.Counter(r.get("action") for r in clone)
    clone_rel = collections.Counter(r.get("relation") for r in clone
                                    if r.get("action") == "QUOTE")
    clone_hold = collections.Counter(r.get("reason") for r in clone
                                     if r.get("action") == "HOLD")

    return {
        "label": "L5_PMUS_REALTIME_COPYABILITY_V1",
        "integrity": {
            "no_backdating_ok_all": all(r.get("no_backdating_ok") for r in acts),
            "window_alignment_verified_all": all(
                (r.get("alignment") or {}).get("aligned") is True for r in acts),
            "pmus_snapshot_age_s_p90": q(
                [float(r["pmus_snapshot_age_s"]) for r in rt], 0.9),
        },
        "sample": {
            "actions_total": len(acts),
            "actions_realtime": n,
            "actions_backlog": len(bl),
            "windows_seen": len(by_w),
            "actions_per_window": (round(len(acts) / len(by_w), 1)
                                   if by_w else None),
        },
        "observation_latency_s": {
            "n": len(lat), "p50": q(lat, 0.5), "p90": q(lat, 0.9),
            "p99": q(lat, 0.99), "max": round(max(lat), 2) if lat else None,
            "note": "realtime rows only; backlog excluded",
        },
        "copyability": {
            "counts": dict(cop),
            "pct_same_pmus_side_available": pct(side_ok, n),
            "pct_still_maker_copyable": pct(cop.get("MAKER_COPYABLE", 0), n),
            "pct_requiring_crossing": pct(cop.get("REQUIRES_CROSSING", 0), n),
            "pct_economically_inadmissible": pct(
                cop.get("BAND_INADMISSIBLE", 0) + cop.get("NO_BOOK", 0)
                + cop.get("PRICE_MOVED_AWAY", 0), n),
            "source_to_pmus_price_delta": {
                "n": len(deltas),
                "p10": q(deltas, 0.1), "p50": q(deltas, 0.5),
                "p90": q(deltas, 0.9),
                "mean": round(st.mean(deltas), 4) if deltas else None,
                "note": "L5's price minus our own touch, in dollars; "
                        "positive means his price is above our bid",
            },
        },
        "l5_actual_behaviour": {
            "relations": dict(rels),
            "p_extend": (round(rels.get("extend", 0) / sum(rels.values()), 3)
                         if rels else None),
            "p_extend_l5_historical_btc15m": 0.66,
            "sides": dict(sides),
            "sells": sells,
            "sells_note": "L5's BTC15m corpus contains ZERO sells; a nonzero "
                          "count here is news, not a translation artefact",
        },
        "repair_availability": {
            "windows_with_a_copyable_action": repair_windows,
            "windows_where_the_opposite_side_later_became_copyable":
                repair_avail,
            "pct": pct(repair_avail, repair_windows),
        },
        "mirror_raw_shadow_economics_q1": {
            "complete_mirrored_sequences": complete_seq,
            "matched_pairs": mirror_pairs,
            "matched_cost_usd": str(mirror_cost.quantize(D("0.0001"))),
            "matched_pnl_usd": str((D(mirror_pairs) - mirror_cost)
                                   .quantize(D("0.0001"))),
            "residual_units": mirror_resid,
            "residual_marked": None,
            "maker_rebate_accrual_usd": str(rebate.quantize(D("0.0001"))),
            "taker_fees_usd": "0.0000",
            "liquidity_rewards_usd": "0.00",
            "liquidity_rewards_why": "eligibility and allocation not "
                                     "established, so counted at zero",
            "label": "SHADOW. No order existed.",
            "ASSUMPTION": (
                "MIRROR_RAW advances its inventory on every action that was "
                "MAKER_COPYABLE at receipt. That is an assumption -- that we "
                "could have rested at L5's price AND been filled as he was -- "
                "not evidence. No PMUS fill has been observed, and the public "
                "book cannot supply one. Every pair and P&L figure on this "
                "line inherits that assumption and none of it is realized."),
            "residual_note": ("deliberately unmarked: its value is a "
                              "settlement outcome and L5's own residual line "
                              "is negative"),
            "rebate_note": "an accrual under the same assumption, not income",
        },
        "clone_model": {
            "ticks": dict(clone_acts),
            "quote_relations": dict(clone_rel),
            "hold_reasons": dict(clone_hold),
            "note": ("CLONE_MODEL advances inventory only on fill evidence, "
                     "and the public book supplies none, so it stays at OPEN "
                     "by construction. That is a limit of the FEED, not a "
                     "finding about the model."),
        },
        "interaction_evidence": {
            "classes_reachable_now": ["NO_INTERACTION", "QUEUE_AMBIGUOUS"],
            "why": "no authenticated PMUS trade tape yet; the three richer "
                   "classes are structurally unreachable and their absence "
                   "is not evidence of absence",
            "any_fill": False,
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape", default=TAPE)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = report(load(a.tape))
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
