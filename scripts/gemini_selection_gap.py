"""P0: why the 60s leader settles 91.1% but the executable favorite only 61.5%.

DIAGNOSTIC ONLY. Nothing here is threshold-searched, no strategy is derived,
and the rejected price-only favorite/underdog study is neither modified nor
re-tuned. The question is mechanical: which observations carry an executable
>=50c favorite, and how do they differ from the ones that do not?

THE ANSWER, in one line: at 60s the book quotes an ask ONLY on the side that
is losing. The leader's 94.6% accuracy and "the only executable side loses
94.6% of the time" are the SAME number from the same cause, because in 100%
of one-sided 60s observations the leader has no ask at all.

BOOK STATE is the primary classifier and is preserved explicitly:
    TWO_SIDED  both a YES ask and a YES bid
    ONE_SIDED  exactly one of them
    EMPTY      neither
On this venue DOWN is bought at 1 - the YES bid, so a bid-only book still
offers a purchase -- of DOWN. "One-sided" never means "nothing to buy"; it
means only one of the two outcomes can be bought.

WHAT THIS TAPE CANNOT SUPPORT, stated rather than approximated:
  * 180s is NOT in the recorded grid (1/2/5/10/15/20/30/45/60/90/120s), so it
    is reported NOT COLLECTED and never interpolated.
  * QUOTE AGE is absent. ie_tte_ladder carries our receive stamp `t` but no
    venue event time and no snapshot age, so staleness cannot be measured
    here -- which is itself a P2 recorder requirement.
  * LEADER TENURE is not resolvable on an 11-point grid whose spacing is
    30-60s in this range: every cell returns the full span, so the figure is
    reported as NOT RESOLVABLE instead of as 60.0s.
  * The reference series is a COINBASE SPOT PROXY, not Gemini's settlement
    index (LS-LRRR-XBTUSD in this era). Movement and distance features are
    labelled proxy throughout and carry the Z NOT IDENTIFIED caveat.

Accounting label: historical replay on Gemini's own recorded books and
settlements. No order was sent.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
from typing import Any, Dict, List, Optional, Sequence

LABEL = "GEMINI_SELECTION_GAP_V1"
REQUESTED = (180, 120, 90, 60, 45, 30)


def book_state(r: Dict[str, Any]) -> str:
    a = r.get("up_ask") is not None
    b = r.get("bid") is not None
    return "TWO_SIDED" if (a and b) else ("ONE_SIDED" if (a or b) else "EMPTY")


def leader(r: Dict[str, Any]) -> Optional[str]:
    """Fixed precedence MID -> BID_ONLY -> ASK_ONLY -> None."""
    a, b = r.get("up_ask"), r.get("bid")
    if a is not None and b is not None:
        return "UP" if (a + b) / 2.0 > 0.50 else "DOWN"
    if b is not None:
        return "UP" if b > 0.50 else "DOWN"
    if a is not None:
        return "UP" if a > 0.50 else "DOWN"
    return None


def executable_sides(r: Dict[str, Any]) -> List[tuple]:
    out = []
    if r.get("up_ask") is not None:
        out.append(("UP", float(r["up_ask"]),
                    None if r.get("up_ask_sz") is None
                    else float(r["up_ask_sz"])))
    if r.get("bid") is not None:
        out.append(("DOWN", round(1.0 - float(r["bid"]), 6),
                    None if r.get("down_sz") is None
                    else float(r["down_sz"])))
    return out


def classify(r: Dict[str, Any], prior: Sequence[Dict[str, Any]]
             ) -> Dict[str, Any]:
    """One checkpoint, fully classified. `prior` is strictly EARLIER rows."""
    sides = executable_sides(r)
    ld = leader(r)
    winner = (None if r.get("up_won") is None
              else ("UP" if r["up_won"] else "DOWN"))
    dear = max(sides, key=lambda s: s[1]) if sides else None
    fav = dear if (dear and dear[1] >= 0.50) else None
    seq = [leader(x) for x in prior]
    seq = [x for x in seq if x is not None]
    flips = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])
    mv30 = mv60 = None
    px = r.get("coinbase")
    if px is not None:
        for back, slot in ((30.0, "mv30"), (60.0, "mv60")):
            want = float(r["tte"]) + back
            cand = [x for x in prior
                    if x.get("coinbase") is not None
                    and abs(float(x["tte"]) - want) <= 15.0]
            if cand:
                v = float(px) - float(cand[0]["coinbase"])
                if slot == "mv30":
                    mv30 = v
                else:
                    mv60 = v
    return {
        "window": r["ticker"], "checkpoint_s": float(r["tte"]),
        "book_state": book_state(r),
        "leader": ld,
        "leader_basis": ("MID" if book_state(r) == "TWO_SIDED"
                         else ("BID_ONLY" if r.get("bid") is not None
                               else ("ASK_ONLY" if r.get("up_ask") is not None
                                     else "NONE"))),
        "executable_sides": [s[0] for s in sides],
        "executable_favorite_side": None if fav is None else fav[0],
        "executable_favorite_price": None if fav is None else fav[1],
        "cheapest_executable_side": (None if not sides else
                                     min(sides, key=lambda s: s[1])[0]),
        "cheapest_executable_price": (None if not sides else
                                      min(sides, key=lambda s: s[1])[1]),
        "leader_has_an_ask": (None if ld is None else
                              ld in [s[0] for s in sides]),
        "quote_age_s": None,
        "quote_age_status": "NOT_RECORDED_BY_THIS_TAPE",
        "spread": (None if book_state(r) != "TWO_SIDED"
                   else round(float(r["up_ask"]) - float(r["bid"]), 6)),
        "depth_contracts": (None if dear is None else dear[2]),
        "leader_tenure_s": None,
        "leader_tenure_status": "NOT_RESOLVABLE_ON_AN_11_POINT_GRID",
        "prior_flips": flips,
        "prior_flip_checkpoints": [float(x["tte"]) for i, x in
                                   enumerate(prior)
                                   if i and leader(x) is not None
                                   and leader(prior[i - 1]) is not None
                                   and leader(x) != leader(prior[i - 1])],
        "proxy_reference": px,
        "proxy_move_30s": None if mv30 is None else round(mv30, 3),
        "proxy_move_60s": None if mv60 is None else round(mv60, 3),
        "proxy_distance_from_strike": r.get("cb_dist"),
        "winner": winner,
        "leader_correct": (None if (ld is None or winner is None)
                           else ld == winner),
        "favorite_correct": (None if (fav is None or winner is None)
                             else fav[0] == winner),
        "only_executable_side_is_the_loser": (
            None if (len(sides) != 1 or winner is None)
            else sides[0][0] != winner),
        "reference_feed_caveat": ("proxy is Coinbase spot; Gemini's "
                                  "settlement index is LS-LRRR-XBTUSD and Z "
                                  "is NOT IDENTIFIED"),
    }


def _med(xs: Sequence[Any], absval: bool = False) -> Optional[float]:
    v = [abs(x) if absval else x for x in xs if x is not None]
    return None if not v else round(statistics.median(v), 3)


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder",
                    default="analysis/gemini/info_edge/ie_tte_ladder.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    raw = [r for r in json.load(open(a.ladder, encoding="utf-8"))
           if str(r.get("ticker", "")).startswith("BTC05M")]
    by_w: Dict[str, Dict[float, Dict[str, Any]]] = collections.defaultdict(
        dict)
    for r in raw:
        if r.get("tte") is not None:
            by_w[r["ticker"]][float(r["tte"])] = r
    grid = sorted({float(r["tte"]) for r in raw}, reverse=True)
    recs: List[Dict[str, Any]] = []
    for tk, rows in by_w.items():
        order = sorted(rows, reverse=True)
        for i, cp in enumerate(order):
            recs.append(classify(rows[cp], [rows[c] for c in order[:i]]))

    out: Dict[str, Any] = {
        "label": LABEL, "duration": "BTC5M",
        "diagnostic_only": ("no threshold search, no strategy derived, the "
                            "rejected price-only study untouched"),
        "windows": len(by_w), "observations": len(recs),
        "recorded_checkpoints": grid,
        "requested_not_collected": [c for c in REQUESTED
                                    if float(c) not in grid],
        "unmeasurable_here": {
            "quote_age_s": "no venue event time or snapshot age in this tape",
            "leader_tenure_s": ("grid spacing is 30-60s in this range; every "
                                "cell returns the full span"),
            "settlement_state_Z": "NOT IDENTIFIED (LS-LRRR-XBTUSD unknown)"},
        "book_state_census": {},
        "by_checkpoint": {},
        "the_selection_gap": {},
        "observations_sample": recs[:3],
        "accounting_label": ("historical replay on Gemini's own recorded "
                             "books and settlements; no order was sent"),
    }
    for cp in grid:
        at = [r for r in recs if r["checkpoint_s"] == cp]
        lab = "%ds" % int(cp)
        ao = sum(1 for r in at if r["executable_sides"] == ["UP"])
        bo = sum(1 for r in at if r["executable_sides"] == ["DOWN"])
        out["book_state_census"][lab] = dict(
            collections.Counter(r["book_state"] for r in at),
            ask_only=ao, bid_only=bo, observations=len(at))
        one = [r for r in at if r["book_state"] == "ONE_SIDED"
               and r["winner"]]
        two = [r for r in at if r["book_state"] == "TWO_SIDED"
               and r["winner"]]
        known = [r for r in at if r["leader_correct"] is not None]
        favs = [r for r in at if r["favorite_correct"] is not None]
        out["by_checkpoint"][lab] = {
            "observations": len(at),
            "leader_correct_pct_ALL": (
                None if not known else
                round(100.0 * sum(1 for r in known
                                  if r["leader_correct"]) / len(known), 2)),
            "leader_correct_pct_WHERE_EXEC_FAVORITE_EXISTS": (
                None if not favs else
                round(100.0 * sum(1 for r in favs
                                  if r["leader_correct"]) / len(favs), 2)),
            "favorite_correct_pct": (
                None if not favs else
                round(100.0 * sum(1 for r in favs
                                  if r["favorite_correct"]) / len(favs), 2)),
            "one_sided": {
                "observations": len(one),
                "only_executable_side_is_the_loser_pct": (
                    None if not one else round(
                        100.0 * sum(1 for r in one
                                    if r["only_executable_side_is_the_loser"])
                        / len(one), 2)),
                "its_avg_price": (None if not one else round(
                    statistics.fmean([r["cheapest_executable_price"]
                                      for r in one
                                      if r["cheapest_executable_price"]
                                      is not None]), 4)),
                "leader_has_no_ask_pct": (None if not one else round(
                    100.0 * sum(1 for r in one
                                if r["leader_has_an_ask"] is False)
                    / len(one), 2)),
                "leader_correct_pct": (None if not one else round(
                    100.0 * sum(1 for r in one
                                if r["leader_correct"]) / len(one), 2)),
            },
            "two_sided": {
                "observations": len(two),
                "leader_correct_pct": (None if not two else round(
                    100.0 * sum(1 for r in two
                                if r["leader_correct"]) / len(two), 2)),
                "median_spread": _med([r["spread"] for r in two]),
                "median_abs_proxy_distance_WINNERS": _med(
                    [r["proxy_distance_from_strike"] for r in two
                     if r["leader_correct"]], True),
                "median_abs_proxy_distance_LOSERS": _med(
                    [r["proxy_distance_from_strike"] for r in two
                     if r["leader_correct"] is False], True),
            },
        }
    at60 = [r for r in recs if r["checkpoint_s"] == 60.0 and r["winner"]]
    cells: Dict[str, Any] = {}
    for key in ("ONE_SIDED", "TWO_SIDED", "EMPTY"):
        sub = [r for r in at60 if r["book_state"] == key]
        if not sub:
            continue
        w = [r for r in sub if r["leader_correct"]]
        l = [r for r in sub if r["leader_correct"] is False]
        cells[key] = {
            "observations": len(sub),
            "leader_correct_pct": round(100.0 * len(w) / len(sub), 2),
            "median_abs_proxy_distance_winners": _med(
                [r["proxy_distance_from_strike"] for r in w], True),
            "median_abs_proxy_distance_losers": _med(
                [r["proxy_distance_from_strike"] for r in l], True),
            "median_abs_proxy_move_60s_winners": _med(
                [r["proxy_move_60s"] for r in w], True),
            "median_abs_proxy_move_60s_losers": _med(
                [r["proxy_move_60s"] for r in l], True),
            "median_depth": _med([r["depth_contracts"] for r in sub]),
            "median_prior_flips": _med([float(r["prior_flips"])
                                        for r in sub]),
        }
    out["the_selection_gap"] = {
        "at_60s": cells,
        "explanation": (
            "the 91.1% figure is carried almost entirely by ONE_SIDED "
            "observations, where the leader is right ~94.6% of the time and "
            "has NO ASK in 100% of cases -- so the accurate leader cannot be "
            "bought at any price, and the single executable side is the loser "
            "in 94.6% of them at an average 0.186. The executable-favorite "
            "subset is the TWO_SIDED minority, where both outcomes are "
            "buyable across a ~0.35 spread and the leader is close to a coin "
            "flip. The book goes two-sided precisely when the outcome is "
            "undecided: within TWO_SIDED, winners sit a median $7.59 from the "
            "strike on the proxy and losers $1.58."),
        "not_a_strategy": (
            "the tradeable expressions of 'the one-sided leader is right' are "
            "both already measured dead ends: resting a bid on the leader is "
            "a maker fill at 0.6-1.6% per cycle with +0.12 adverse selection, "
            "and selling the executable loser would be a naked short whose "
            "venue semantics are undetermined and which sell_path refuses."),
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(out, indent=2, sort_keys=True,
                                default=str) + "\n")
    print("%s windows=%d obs=%d not_collected=%s"
          % (LABEL, out["windows"], out["observations"],
             out["requested_not_collected"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
