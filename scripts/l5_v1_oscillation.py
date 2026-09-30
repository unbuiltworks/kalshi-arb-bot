"""Does this book oscillate through acceptable levels often enough for $5/window?

THE HYPOTHESIS, taken as first-class: the PMUS BTC15m book is deep and fast --
an owner recording shows the touch moving 2c in ~3s with 1,000-3,200 contracts
resting at every cent. If a persistent maker order sits at an economically
acceptable price, repeated oscillation may fill it many times per window, and
$5/window could come from FREQUENCY at a small clip rather than from size.

HOW A FILL IS EARNED HERE, and it is strict:
  * our order rests at a price derived from inventory, never from the touch
    alone, and it does NOT move because the touch moved;
  * queue-ahead is initialised from the depth displayed AT OUR EXACT LEVEL when
    we join, and is decremented ONLY by volume attributable to a print at or
    through our level -- volume printed at a better price never reached us;
  * a fill requires the queue to be exhausted AND a print at or through our
    price in that same interval. Display alone is never a fill, a touch is
    never a fill, and no fill is ever inferred from depth.

WHAT IT MEASURES per window: opportunity cycles (distinct crossings of an
acceptable level), realistic queue-aware fills at q10/q20/q30, the pairs those
fills form, the net edge per pair, and the resulting $/window. Also the share of
opportunity that falls inside the tape's blind gaps, which bounds what any
measurement from this tape can say.

DIAGNOSTIC ONLY on the consumed 49. Nothing here is fitted or validated.
"""
from __future__ import annotations

import json
import statistics
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

import scripts.pmus_completeness_rule as CR
from scripts.audit_economic_complete import USABLE
from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E

OOS = "data/pmus_btc15m_tape_oos/oos_tape_ordered.jsonl"
SETTLE = "analysis/l5/r7_oos_settlement_authoritative.json"
OUT = "analysis/l5/v1_oscillation.json"
CLIPS = (10, 20, 30)
#: a gap longer than this is a hole in our observation, not a quiet market.
BLIND_S = 15.0


def cohort():
    by = CR.load_tape(OOS)
    win = json.load(open(SETTLE))["winners"]
    return by, {s: win[s] for s in sorted(by)
                if CR.evaluate(s, by[s]).get("tape_test_passes")
                and s not in USABLE and s in win}


class Resting:
    """One persistent maker order, with honest queue accounting."""

    def __init__(self, side: str, px: D, ahead: D) -> None:
        self.side, self.px, self.ahead = side, D(str(px)), D(str(ahead))
        self.filled = D(0)

    def observe(self, row, ladder, clip: D) -> D:
        """Return the qty filled on THIS observation. Prints only."""
        last = S._d(row.get("last"))
        vol = S._d(row.get("shares_traded_delta_lower_bound")) or D(0)
        if last is None or vol <= 0:
            return D(0)
        if S.own_price(self.side, last) > self.px:
            return D(0)            # nothing printed at or through our price
        self.ahead -= vol
        if self.ahead > 0:
            return D(0)            # still behind displayed size
        take = -self.ahead
        self.ahead = D(0)
        got = take if take < (clip - self.filled) else (clip - self.filled)
        self.filled += got
        return got


def displayed_at(ladder, side: str, px: D) -> Optional[D]:
    venue = px if side == "up" else (D(1) - px)
    return S.level_displayed_qty(ladder, side, venue)


def run_window(rows, *, winner: str, clip: D) -> Dict[str, Any]:
    """A persistent two-sided maker book, priced from inventory."""
    bk = S.Book()
    ladder = None
    resting: Dict[str, Optional[Resting]] = {"up": None, "down": None}
    cycles = 0
    fills: List[Dict[str, Any]] = []
    blind_s = 0.0
    prev_t = None
    seen = 0

    for row in rows:
        if row.get("kind") == "book":
            ladder = row
            continue
        if row.get("kind") != "bbo":
            continue
        if str(row.get("state") or "") != "MARKET_STATE_OPEN":
            continue
        seen += 1
        t = S._epoch(row.get("t"))
        if prev_t is not None and t is not None and t - prev_t > BLIND_S:
            blind_s += (t - prev_t)
        prev_t = t

        for side in ("up", "down"):
            other = "down" if side == "up" else "up"
            # the acceptable price for THIS side, from inventory only
            if bk.q[other] > bk.q[side]:
                cap = E.THETA - bk.economic_basis(other)
                cap = (cap / E.PRICE_TICK).to_integral_value(
                    rounding="ROUND_FLOOR") * E.PRICE_TICK
            else:
                own = S._d(row.get("bid")) if side == "up" else \
                    (D(1) - S._d(row.get("ask"))
                     if S._d(row.get("ask")) is not None else None)
                cap = own
            if cap is None or cap <= 0 or cap >= 1:
                resting[side] = None
                continue
            cur = resting[side]
            # PERSISTENCE: only re-price when the acceptable price moved
            # ADVERSELY for us, i.e. downward. A better price does not make us
            # chase, and an unchanged price never sends us to the back of the
            # queue.
            if cur is None or cur.px > cap or cur.filled >= clip:
                shown = displayed_at(ladder, side, cap)
                if shown is None:
                    resting[side] = None      # queue unknowable: do not rest
                    continue
                resting[side] = Resting(side, cap, shown)
                cur = resting[side]
            got = cur.observe(row, ladder, clip)
            if got > 0:
                cycles += 1
                bk.add(side, got, got * cur.px, maker=True)
                fills.append({"t": row.get("t"), "side": side,
                              "px": str(cur.px), "qty": str(got)})
                if cur.filled >= clip:
                    resting[side] = None
    res = bk.settle(winner)
    res["cycles"] = cycles
    res["fills"] = fills
    res["observations"] = seen
    res["blind_s"] = blind_s
    return res


if __name__ == "__main__":
    by, coh = cohort()
    doc: Dict[str, Any] = {"LABEL": "V1_OSCILLATION_DIAGNOSIS",
                           "cohort": "oos49_CONSUMED_diagnostic_only",
                           "clips": list(CLIPS), "rows": []}
    for clip in CLIPS:
        per = []
        for slug, winner in coh.items():
            r = run_window(by[slug], winner=winner, clip=D(str(clip)))
            net = r["matched_pnl"] - r["fees"] + (r["resid_pnl"] or D(0))
            per.append({"w": slug[-5:], "cycles": r["cycles"],
                        "fills": len(r["fills"]),
                        "matched_qty": str(r["matched_qty"]),
                        "resid_qty": str(r["resid_qty"]),
                        "matched_pnl": str(r["matched_pnl"]),
                        "resid_pnl": str(r["resid_pnl"]),
                        "fees": str(r["fees"]),
                        "maker_qty": str(r["maker_qty"]),
                        "net": str(net), "obs": r["observations"],
                        "blind_s": r["blind_s"]})
        n = len(per)
        tot = lambda k: sum(D(x[k]) for x in per)        # noqa: E731
        agg = {"clip": clip, "n": n,
               "cycles_per_window": statistics.mean(x["cycles"] for x in per),
               "fills_per_window": statistics.mean(x["fills"] for x in per),
               "mean_net": str(tot("net") / n),
               "median_net": str(sorted(D(x["net"]) for x in per)[n // 2]),
               "mean_matched_pnl": str(tot("matched_pnl") / n),
               "mean_resid_pnl": str(tot("resid_pnl") / n),
               "mean_fees": str(tot("fees") / n),
               "mean_maker_qty": str(tot("maker_qty") / n),
               "blind_frac": statistics.mean(
                   x["blind_s"] for x in per) / 900.0,
               "per_window": per}
        doc["rows"].append(agg)
        print("q%-3d cycles/win %5.2f  fills/win %5.2f  matched$ %+7.3f  "
              "resid$ %+7.3f  fees %5.3f  NET %+7.4f  median %+7.4f"
              % (clip, agg["cycles_per_window"], agg["fills_per_window"],
                 float(agg["mean_matched_pnl"]), float(agg["mean_resid_pnl"]),
                 float(agg["mean_fees"]), float(agg["mean_net"]),
                 float(agg["median_net"])), flush=True)
    json.dump(doc, open(OUT, "w"), indent=1, default=str)
    print("blind fraction of window (gaps > %.0fs): %.1f%%"
          % (BLIND_S, 100 * doc["rows"][0]["blind_frac"]))
    print("WROTE " + OUT)
