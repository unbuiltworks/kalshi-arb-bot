"""CAN WE ACQUIRE THE EVENTUAL LEADING SIDE BEFORE ITS ASK DISAPPEARS?

NOT a favorite-prediction study. The prediction is already good: the one-sided
leader settles correctly ~94%. The open question is purely executional, and it
splits into three mechanisms that are measured SEPARATELY because they have
different evidence requirements.

  A  TAKER BEFORE DISAPPEARANCE -- at each checkpoint, IF the leader's own
     side still has an executable ask, buy it and hold. Strictly causal: the
     decision uses only the book at that checkpoint. A separate descriptive
     row reports the subset that turned out to be the LAST such checkpoint,
     and it is labelled hindsight-selected because "last" is not knowable at
     the time.
  B  RESTING MAKER -- conditioned on leading side AND book state. This tape
     cannot support it and the script says so rather than estimating: an
     11-point checkpoint grid carries no queue position, no per-level
     arrival sequence and no trade-by-trade flow, so a fill cannot be
     evidenced conservatively. It is specified here and left to the recorder.
  C  EARLIER STATE ENTRY -- at 240/180/120/90s, while BOTH sides are still
     executable, does a causally available precursor identify the side that
     later becomes the one-sided leader, and the eventual winner?

THE $21-26/WINDOW FIGURE IS NOT ASSUMED CAPTURABLE. It was computed at a
price nobody offers; this script exists to find out whether any executable
path reaches it, and reports failure as failure.

NO THRESHOLD OPTIMIZATION. The checkpoints are the recorded grid, the sides
are the book's own, and nothing is tuned on these 133 windows.

Accounting label: historical replay on Gemini's own recorded books and
settlements. No order was sent.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import statistics
from typing import Any, Dict, List, Optional, Sequence

LABEL = "GEMINI_LEADER_ACQUISITION_V1"
TAKER_RATE = 0.07
C_CHECKPOINTS = (240.0, 180.0, 120.0, 90.0)


def breakeven(p: float) -> float:
    return p + TAKER_RATE * p * (1.0 - p)


def book_state(r: Dict[str, Any]) -> str:
    a = r.get("up_ask") is not None
    b = r.get("bid") is not None
    return "TWO_SIDED" if (a and b) else ("ONE_SIDED" if (a or b) else "EMPTY")


def leader(r: Dict[str, Any]) -> Optional[str]:
    a, b = r.get("up_ask"), r.get("bid")
    if a is not None and b is not None:
        return "UP" if (a + b) / 2.0 > 0.50 else "DOWN"
    if b is not None:
        return "UP" if b > 0.50 else "DOWN"
    if a is not None:
        return "UP" if a > 0.50 else "DOWN"
    return None


def ask_for(r: Dict[str, Any], side: str):
    """Executable ask and depth for one side, or (None, None)."""
    if side == "UP":
        return ((None, None) if r.get("up_ask") is None
                else (float(r["up_ask"]),
                      None if r.get("up_ask_sz") is None
                      else float(r["up_ask_sz"])))
    return ((None, None) if r.get("bid") is None
            else (round(1.0 - float(r["bid"]), 6),
                  None if r.get("down_sz") is None
                  else float(r["down_sz"])))


def _boot(wins: Sequence[bool], n: int = 4000,
          seed: int = 909) -> List[Optional[float]]:
    if len(wins) < 2:
        return [None, None]
    rng = random.Random(seed)
    rates = []
    for _ in range(n):
        s = [wins[rng.randrange(len(wins))] for _ in wins]
        rates.append(100.0 * sum(s) / len(s))
    rates.sort()
    return [round(rates[int(0.025 * len(rates))], 2),
            round(rates[min(len(rates) - 1, int(0.975 * len(rates)))], 2)]


def score(trades: Sequence[Dict[str, Any]], name: str) -> Dict[str, Any]:
    if not trades:
        return {"arm": name, "trades": 0}
    wins = [bool(t["won"]) for t in trades]
    asks = [t["ask"] for t in trades]
    avg = statistics.fmean(asks)
    wp = 100.0 * sum(wins) / len(wins)
    be = 100.0 * breakeven(avg)
    ci = _boot(wins)
    nets = [t["net"] for t in trades]
    deps = [t["depth"] for t in trades if t["depth"] is not None]
    return {
        "arm": name, "trades": len(trades),
        "distinct_windows": len({t["window"] for t in trades}),
        "avg_executable_ask": round(avg, 4),
        "settlement_win_pct": round(wp, 2),
        "win_pct_ci95_window_bootstrap": ci,
        "breakeven_pct_zero_fees": round(100.0 * avg, 2),
        "breakeven_pct_taker_fees": round(be, 2),
        "win_minus_breakeven_pp": round(wp - be, 2),
        "ci_low_minus_breakeven_pp": (None if ci[0] is None
                                      else round(ci[0] - be, 2)),
        "established_positive": bool(ci[0] is not None and ci[0] > be),
        "net_pnl_per_trade_usd": round(statistics.fmean(nets), 4),
        "net_pnl_total_usd": round(sum(nets), 4),
        "depth_median": (None if not deps
                         else round(statistics.median(deps), 1)),
        "capacity_usd_at_median_depth": (
            None if not deps
            else round(statistics.median(deps) * avg, 2)),
        "ev_per_window_at_median_depth_usd": (
            None if not deps else round(
                statistics.fmean(nets) * statistics.median(deps), 2)),
    }


def trade(r: Dict[str, Any], side: str, px: float, depth) -> Dict[str, Any]:
    f = TAKER_RATE * px * (1.0 - px)
    won = side == ("UP" if r["up_won"] else "DOWN")
    return {"window": r["ticker"], "checkpoint_s": float(r["tte"]),
            "side": side, "ask": px, "depth": depth, "won": won,
            "net": (1.0 - px - f) if won else (-px - f)}


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder",
                    default="analysis/gemini/info_edge/ie_tte_ladder.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    raw = [r for r in json.load(open(a.ladder, encoding="utf-8"))
           if str(r.get("ticker", "")).startswith("BTC05M")
           and r.get("tte") is not None]
    by: Dict[str, Dict[float, Dict[str, Any]]] = collections.defaultdict(dict)
    for r in raw:
        by[r["ticker"]][float(r["tte"])] = r
    grid = sorted({float(r["tte"]) for r in raw}, reverse=True)

    # ---------------- the transition record, one row per window
    transitions: List[Dict[str, Any]] = []
    for tk, rows in by.items():
        order = sorted(rows, reverse=True)
        if not any(rows[c].get("up_won") is not None for c in order):
            continue
        winner = None
        for c in order:
            if rows[c].get("up_won") is not None:
                winner = "UP" if rows[c]["up_won"] else "DOWN"
                break
        seq = []
        for c in order:
            r = rows[c]
            ld = leader(r)
            px, dep = (ask_for(r, ld) if ld else (None, None))
            seq.append({"tte": c, "state": book_state(r), "leader": ld,
                        "leader_ask": px, "leader_depth": dep,
                        "yes_bid": r.get("bid"), "yes_ask": r.get("up_ask"),
                        "spread": (None if (r.get("up_ask") is None
                                            or r.get("bid") is None)
                                   else round(float(r["up_ask"])
                                              - float(r["bid"]), 6)),
                        "proxy_ref": r.get("coinbase"),
                        "proxy_dist": r.get("cb_dist")})
        with_ask = [s for s in seq if s["leader_ask"] is not None]
        last = with_ask[-1] if with_ask else None
        first_one = next((s for s in seq if s["state"] == "ONE_SIDED"), None)
        last_two = None
        for s in seq:
            if s["state"] == "TWO_SIDED":
                last_two = s
        transitions.append({
            "window": tk, "winner": winner,
            "state_sequence": [(s["tte"], s["state"]) for s in seq],
            "leader_sequence": [(s["tte"], s["leader"]) for s in seq],
            "last_checkpoint_with_an_executable_leader_ask": (
                None if last is None else {
                    "tte": last["tte"], "ask": last["leader_ask"],
                    "depth": last["leader_depth"], "side": last["leader"],
                    "yes_bid": last["yes_bid"], "yes_ask": last["yes_ask"],
                    "spread": last["spread"],
                    "proxy_reference": last["proxy_ref"],
                    "proxy_distance_from_strike": last["proxy_dist"],
                    "leader_settled_correct": (None if winner is None
                                               else last["leader"] == winner),
                    "elapsed_s_until_ask_gone": round(last["tte"], 1),
                    "elapsed_note": ("the ask is absent at every LATER "
                                     "checkpoint, so disappearance happened "
                                     "within this many seconds of expiry; "
                                     "the grid cannot date it finer")}),
            "last_two_sided_tte": None if last_two is None else last_two["tte"],
            "first_one_sided_tte": (None if first_one is None
                                    else first_one["tte"]),
            "transition_shape": (
                "TWO_SIDED_THEN_ONE_SIDED"
                if (last_two and first_one and first_one["tte"]
                    < last_two["tte"]) else
                ("NEVER_TWO_SIDED" if last_two is None else
                 ("NEVER_ONE_SIDED" if first_one is None else "INTERLEAVED"))),
        })

    # ---------------- A: causal taker on the leader, per checkpoint
    A: Dict[str, Any] = {}
    for cp in grid:
        tr = []
        for tk, rows in by.items():
            r = rows.get(cp)
            if r is None or r.get("up_won") is None:
                continue
            ld = leader(r)
            if ld is None:
                continue
            px, dep = ask_for(r, ld)
            if px is None:
                continue
            tr.append(trade(r, ld, px, dep))
        A["%ds" % int(cp)] = score(
            tr, "A buy the LEADER at its own ask, tte=%ds" % int(cp))
    hind = []
    for t in transitions:
        last = t["last_checkpoint_with_an_executable_leader_ask"]
        if not last or t["winner"] is None:
            continue
        px = last["ask"]
        f = TAKER_RATE * px * (1.0 - px)
        won = last["side"] == t["winner"]
        hind.append({"window": t["window"], "checkpoint_s": last["tte"],
                     "side": last["side"], "ask": px,
                     "depth": last["depth"], "won": won,
                     "net": (1.0 - px - f) if won else (-px - f)})
    A["HINDSIGHT_last_available_checkpoint"] = dict(
        score(hind, "A' buy the leader at the LAST checkpoint its ask existed"),
        leakage_warning=("HINDSIGHT-SELECTED and NOT a strategy: 'the last "
                         "checkpoint at which the ask existed' is not knowable "
                         "at that checkpoint. Descriptive upper bound only."))

    # ---------------- C: earlier state entry from a two-sided book
    C: Dict[str, Any] = {}
    for cp in C_CHECKPOINTS:
        if cp not in grid:
            C["%ds" % int(cp)] = {"status": "NOT_COLLECTED"}
            continue
        rows = [(tk, by[tk][cp]) for tk in by if cp in by[tk]
                and by[tk][cp].get("up_won") is not None]
        two = [(tk, r) for tk, r in rows if book_state(r) == "TWO_SIDED"]
        agree_winner = agree_future = n_future = 0
        tr = []
        for tk, r in two:
            ld = leader(r)
            winner = "UP" if r["up_won"] else "DOWN"
            if ld == winner:
                agree_winner += 1
            later = [by[tk][c] for c in sorted(by[tk], reverse=True)
                     if c < cp and book_state(by[tk][c]) == "ONE_SIDED"]
            if later:
                n_future += 1
                if leader(later[0]) == ld:
                    agree_future += 1
            px, dep = ask_for(r, ld)
            if px is not None:
                tr.append(trade(r, ld, px, dep))
        C["%ds" % int(cp)] = {
            "status": "COLLECTED",
            "two_sided_windows": len(two),
            "precursor": ("the book's own leader by mid, which is the only "
                          "causally available candidate in this tape"),
            "leader_predicts_eventual_winner_pct": (
                None if not two else round(100.0 * agree_winner
                                           / len(two), 2)),
            "leader_predicts_the_later_ONE_SIDED_leader_pct": (
                None if not n_future else round(100.0 * agree_future
                                                / n_future, 2)),
            "windows_that_later_go_one_sided": n_future,
            "buying_that_leader_now": score(
                tr, "C buy the two-sided leader at tte=%ds" % int(cp)),
        }

    rep = {
        "label": LABEL, "duration": "BTC5M",
        "windows": len(transitions),
        "recorded_grid": grid,
        "no_threshold_optimization": True,
        "descriptive_value_is_not_capturable_until_proven": (
            "the $21-26/window one-sided figure was computed at a price "
            "nobody offers; nothing here assumes it is reachable"),
        "transition_shapes": dict(collections.Counter(
            t["transition_shape"] for t in transitions)),
        "last_executable_leader_ask": {
            "windows_with_one": sum(
                1 for t in transitions
                if t["last_checkpoint_with_an_executable_leader_ask"]),
            "tte_distribution": dict(collections.Counter(
                t["last_checkpoint_with_an_executable_leader_ask"]["tte"]
                for t in transitions
                if t["last_checkpoint_with_an_executable_leader_ask"])),
        },
        "MECHANISM_A_taker_before_disappearance": A,
        "MECHANISM_B_resting_maker": {
            "testable_on_this_tape": False,
            "why": ("an 11-point checkpoint grid carries no queue position, "
                    "no per-level arrival sequence and no trade-by-trade "
                    "flow, so no fill can be EVIDENCED conservatively. The "
                    "existing 960-cycle census measured 0.6-1.6% fill with "
                    "+0.12 adverse move but did NOT condition on leading side "
                    "or book state, which is exactly the conditioning that "
                    "matters."),
            "what_the_recorder_must_capture": [
                "every depth diff with U/u so queue-ahead at our price is "
                "reconstructible",
                "every trade with venue E so a reaching print can be matched "
                "to a resting level",
                "book_state and leader identity at the instant of each print",
                "the leading side's own ask depth, which this tape never "
                "recorded",
            ],
            "status": "SPECIFIED, BLOCKED ON THE RECORDER",
        },
        "MECHANISM_C_earlier_state_entry": C,
        "transitions": transitions,
        "accounting_label": ("historical replay on Gemini's own recorded "
                             "books and settlements; no order was sent"),
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    print("%s windows=%d shapes=%s" % (LABEL, rep["windows"],
                                       rep["transition_shapes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
