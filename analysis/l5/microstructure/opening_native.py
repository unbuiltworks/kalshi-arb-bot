"""Sections 1 and 8 of the overnight brief.

S1  BTC15m opening reconstructed at the archive's NATIVE resolution,
    window by window for all 12 windows, so no aggregate can hide a window.
S8  Is the opening behaviour L5-SPECIFIC or a generic market-opening effect?
    Answered against the all-trader maker-order legs already backfilled for
    every one of the 12 windows, not against L5 alone.

Native resolution is 1 second: the L2 archive writes a row only when the book
changes and block timestamps are whole seconds. Nothing here interpolates to
a finer grid, and a second with no row means the book did not move, never
that the book was empty.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S
from analysis.l5.microstructure.clips import build_clips
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.parpair import find_pairs, _id
from analysis.l5.microstructure.stats import cliffs_delta, qs

OPEN_S = 60
GAP_S = 10
CLIP80 = 80.0
L5_TOPIC = None  # resolved from the events, never hard-coded


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def load_prints(slug: str) -> Optional[dict]:
    """All-trader maker-order legs for one window, or None if not backfilled.

    A failed chunk is a HOLE. A window whose coverage is not COMPLETE is
    reported as such and excluded, never counted as an absence of prints.
    """
    for p in (os.path.join("analysis/l5/august_replay/prints",
                           "prints_%s.json" % slug),
              "analysis/l5/august_replay/window_prints.json"):
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8"))
        if d.get("market_slug") != slug:
            continue
        return d
    return None


# ------------------------------------------------------------------ S1
def opening_window(slug: str, seq: List[dict], books, tokens,
                   window_s: int) -> Dict[str, Any]:
    start = int(slug.rsplit("-", 1)[1])
    pairs, member = find_pairs(seq)
    passive = [r for r in seq if _id(r) not in member]
    clips = sorted(build_clips(passive, books, gap_s=GAP_S, slug=slug,
                               clen="15m", start=start),
                   key=lambda c: c["t_first"])

    tok = {str(o).lower(): t for t, o in tokens.items()}
    # when does the archive first show each side's book?
    first_book = {}
    for side in ("up", "down"):
        bk = books.get(tok[side])
        ts = [t for t in (bk.t if bk else [])
              if start <= t <= start + window_s]
        first_book[side] = (int(min(ts)) - start) if ts else None

    o_clips = [c for c in clips if c["t_first"] - start < OPEN_S]
    o_pairs = [p for p in pairs if p["up"]["t"] - start < OPEN_S]
    o_fills = [r for r in seq if r["secs_since_open"] < OPEN_S]

    first_action = min([c["t_first"] - start for c in o_clips]
                       + [p["up"]["t"] - start for p in o_pairs] or [10 ** 9])
    sides = [c["outcome"] for c in o_clips]
    seconds = sorted({c["t_first"] - start for c in o_clips}
                     | {p["up"]["t"] - start for p in o_pairs})

    return {
        "market_slug": slug,
        "first_book_row_secs_after_open": first_book,
        "first_L5_action_secs_after_open": (
            None if first_action == 10 ** 9 else first_action),
        "n_fills_0_60": len(o_fills),
        "n_passive_clips_0_60": len(o_clips),
        "n_par_pairs_0_60": len(o_pairs),
        "opened_with_par_pair": bool(
            o_pairs and (not o_clips
                         or o_pairs[0]["up"]["t"] <= o_clips[0]["t_first"])),
        "sides_in_order_0_60": sides,
        "two_sided_within_60s": len(set(sides)) == 2,
        "action_seconds_0_60": seconds,
        "clips_0_30": sum(1 for c in o_clips if c["t_first"] - start < 30),
        "clips_30_60": sum(1 for c in o_clips
                           if 30 <= c["t_first"] - start < OPEN_S),
        "shares_0_60": round(sum(c["clip_total_shares"] for c in o_clips)
                             + sum(p["up"]["quantity"] * 2
                                   for p in o_pairs), 3),
        "shares_whole_window": round(
            sum(c["clip_total_shares"] for c in clips)
            + sum(p["up"]["quantity"] * 2 for p in pairs), 3),
        "offsets_ticks_below_bid_0_60": [
            r.get("offset_ticks_below_bid") for r in o_fills],
        "mechanisms_0_60": dict(Counter(r["mechanism"] for r in o_fills)),
    }


# ------------------------------------------------------------------ S8
def generic_or_specific(slug: str, l5_topic: str, window_s: int
                        ) -> Dict[str, Any]:
    """Opening concentration for L5 against every other trader in the window.

    The statistic is the share of a trader's own maker-order legs in the
    window that land in the first 60 seconds. If every trader is concentrated
    at the open, the effect is generic. If L5 sits in the tail of that
    distribution, it is L5-specific.
    """
    d = load_prints(slug)
    if d is None:
        return {"market_slug": slug, "VERDICT": "NO_PRINTS_BACKFILLED"}
    if d.get("coverage") != "COMPLETE" or d.get("chunk_holes"):
        return {"market_slug": slug, "VERDICT": "COVERAGE_NOT_COMPLETE",
                "coverage": d.get("coverage"),
                "chunk_holes": d.get("chunk_holes")}
    bt = {int(k): int(v) for k, v in d["block_times"].items()}
    start = int(slug.rsplit("-", 1)[1])

    tot: Counter = Counter()
    early: Counter = Counter()
    for leg in d["maker_order_legs"]:
        t = bt.get(int(leg["block"]))
        if t is None:
            continue
        own = leg["owner_topic"]
        tot[own] += 1
        if t - start < OPEN_S:
            early[own] += 1

    rows = [{"topic": o, "n": n, "share_0_60": early[o] / n}
            for o, n in tot.items() if n >= 3]
    l5 = next((r for r in rows if l5_topic and l5_topic.lower() in r["topic"].lower()),
              None)
    others = [r for r in rows if l5 is None or r["topic"] != l5["topic"]]
    oshares = [r["share_0_60"] for r in others]
    pct = (sum(1 for s in oshares if s < l5["share_0_60"]) / len(oshares)
           if l5 and oshares else None)
    return {
        "market_slug": slug,
        "n_traders_with_3plus_maker_legs": len(rows),
        "L5_identified_in_prints": l5 is not None,
        "L5_share_of_own_legs_in_first_60s": (
            round(l5["share_0_60"], 4) if l5 else None),
        "L5_n_maker_legs": l5["n"] if l5 else None,
        "other_traders_share_0_60": qs(oshares),
        "L5_percentile_among_other_traders": (
            round(pct, 4) if pct is not None else None),
        "all_trader_maker_legs_0_60_share": (
            round(sum(early.values()) / sum(tot.values()), 4)
            if sum(tot.values()) else None),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events15", required=True)
    ap.add_argument("--parquet15", required=True)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    ev = jl(a.events15)
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)
    for g in byw.values():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
    books, wins = load(a.parquet15, contract_length="15m", slugs=set(byw))

    # L5's own owner topic, taken from the maker legs of L5's own verified
    # fills rather than hard-coded.
    topic = None
    d0 = load_prints(sorted(byw)[0])
    if d0:
        txs = {r.get("tx") for r in byw[sorted(byw)[0]]}
        c = Counter(leg["owner_topic"] for leg in d0["maker_order_legs"]
                    if leg.get("tx") in txs)
        if c:
            topic = c.most_common(1)[0][0]

    per = [opening_window(s, byw[s], books, wins[s]["tokens"], 900)
           for s in sorted(byw)]
    gen = [generic_or_specific(s, topic, 900) for s in sorted(byw)]

    good = [g for g in gen if "VERDICT" not in g and g["L5_identified_in_prints"]]
    l5_sh = [g["L5_share_of_own_legs_in_first_60s"] for g in good]
    oth_med = [g["other_traders_share_0_60"]["p50"] for g in good]
    out = {
        "protocol": "PREREGISTRATION_OVERNIGHT.md",
        "NATIVE_RESOLUTION": "1 second. A second with no archive row means "
                             "the book did not move, never that it was "
                             "empty. Nothing is interpolated finer.",
        "S1_opening_per_window_BTC15m": per,
        "S1_cross_window": {
            "n_windows": len(per),
            "first_L5_action_secs": qs([p["first_L5_action_secs_after_open"]
                                        for p in per
                                        if p["first_L5_action_secs_after_open"]
                                        is not None]),
            "windows_two_sided_within_60s": sum(
                1 for p in per if p["two_sided_within_60s"]),
            "windows_opening_with_par_pair": sum(
                1 for p in per if p["opened_with_par_pair"]),
            "share_of_window_shares_done_in_first_60s": qs([
                p["shares_0_60"] / p["shares_whole_window"] for p in per
                if p["shares_whole_window"]]),
            "clips_0_30_vs_30_60": {
                "clips_0_30": sum(p["clips_0_30"] for p in per),
                "clips_30_60": sum(p["clips_30_60"] for p in per)},
        },
        "S8_L5_specific_vs_generic_opening": gen,
        "S8_verdict": {
            "n_windows_with_complete_prints_and_L5_identified": len(good),
            "L5_own_opening_share": qs(l5_sh),
            "median_other_trader_opening_share": qs(oth_med),
            "cliffs_delta_L5_minus_other_medians": (
                cliffs_delta(l5_sh, oth_med) if len(good) >= 2 else None),
            "L5_percentile_per_window": [
                g["L5_percentile_among_other_traders"] for g in good],
        },
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "OPENING_NATIVE.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("written OPENING_NATIVE.json; L5 topic=%s" % topic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
