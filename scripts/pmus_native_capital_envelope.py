"""EXACT capital envelope of the frozen q50 PROFIT_LOCK_5 arm.

Measured by re-walking the frozen scorer's own book over the 25 usable windows
and sampling, at EVERY decision point, the four quantities a live mandate has
to cover. Nothing here is estimated from a contract count.

  committed principal      cash actually paid for inventory held right now,
                           c[up] + c[down], net of completion retirement.
  outstanding-order        cash our resting acquisition would cost if every
                           resting clip filled at its own price.
  directional principal    cash at risk in the UNMATCHED leg:
                           |residual| * basis of the held side.
  combined worst case      committed + outstanding, the most the account can
                           be short of cash before any settlement.
  taker fees               fees actually incurred, cumulative, per window.
  in-flight peak           the transactional worst case the live overlay must
                           survive: committed + a marketable completion already
                           on the wire + resting maker orders NOT YET CONFIRMED
                           cancelled. That race is the one the simulator never
                           had, and it is the number a live mandate must cover.

CASH USED IS NOT LOSS. Committed principal buys contracts that pay out; the
worst-case LOSS is bounded by the unmatched leg plus fees, because a matched
pair returns $1 per pair whichever way the market settles. Both are reported
and they are never added together.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal as D

sys.path.insert(0, __import__("os").path.dirname(
    __import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from scripts.audit_economic_complete import load, USABLE       # noqa: E402
from src import pmus_completion_shadow as S                    # noqa: E402
from src import pmus_l5_native_engine as E                     # noqa: E402
from src import pmus_native_parity as P                        # noqa: E402

OUT = "analysis/l5/rung_a/CAPITAL_ENVELOPE_q50.json"


def walk(rows, winner):
    """Replay with the parity engine, sampling capital at every decision."""
    eng = E.NativeEngine(clip=E.CLIP, prospective_cap=False)
    ven = P.ReplayVenue()
    ladder = None
    peak = {"committed": D(0), "outstanding": D(0),
            "directional": D(0), "combined": D(0),
            "fees": D(0), "in_flight": D(0), "worst_case_loss": D(0)}
    inflight = {"cost": D(0)}          # a taker leg on the wire, unconfirmed

    def sample():
        bk = eng.bk
        committed = bk.c["up"] + bk.c["down"]
        outstanding = D(0)
        for st in eng.resting.values():
            if st:
                outstanding += D(str(st["px"])) * D(str(st["qty"]))
        r = bk.residual()
        held = "up" if r > 0 else ("down" if r < 0 else None)
        directional = abs(r) * bk.avg(held) if held else D(0)
        combined = committed + outstanding
        # THE LIVE RACE the simulator never had: a marketable completion is on
        # the wire while the maker orders it is meant to retire are not yet
        # CONFIRMED cancelled, so both can consume cash at once.
        in_flight = committed + inflight["cost"] + outstanding
        # cash used is not loss. A matched pair pays $1 whichever way it
        # settles, so the loss exposure is the unmatched leg plus fees.
        worst_case_loss = directional + bk.fees
        for k, v in (("committed", committed), ("outstanding", outstanding),
                     ("directional", directional), ("combined", combined),
                     ("fees", bk.fees), ("in_flight", in_flight),
                     ("worst_case_loss", worst_case_loss)):
            if v > peak[k]:
                peak[k] = v

    for row in rows:
        if row.get("kind") == "book":
            ladder = row
            eng.ladder = row
            continue
        if row.get("kind") != "bbo":
            continue
        if str(row.get("state") or "") != "MARKET_STATE_OPEN":
            continue
        def _take(it):
            # the leg is on the wire and unconfirmed at this instant
            inflight["cost"] = D(str(it["cost"]))
            sample()
            inflight["cost"] = D(0)
            return it["qty"], it["cost"], it["fee"]

        try:
            eng.on_book(row, ladder, execute_taker=_take)
        except E.StaleBook:
            continue
        sample()
        if eng.locked_at is not None:
            continue
        for side in ("up", "down"):
            st = eng.resting.get(side)
            if not st:
                continue
            got = ven.fills_for(eng, row, side, D(str(st["px"])),
                                D(str(st["qty"])), ladder)
            if got is None:
                continue
            q, fpx = got
            if not eng._cap_permits(side, q):
                continue
            eng.on_fill(side, q, q * fpx, maker=True)
            sample()
    return peak


def main() -> int:
    by = load()
    rows, agg = [], {"committed": D(0), "outstanding": D(0),
                     "directional": D(0), "combined": D(0),
                     "fees": D(0), "in_flight": D(0), "worst_case_loss": D(0)}
    for slug, winner in USABLE.items():
        pk = walk(by[slug], winner)
        rows.append({"window": slug[-5:], **{k: str(v) for k, v in pk.items()}})
        for k, v in pk.items():
            if v > agg[k]:
                agg[k] = v
    doc = {
        "arm": "frozen q50 PROFIT_LOCK_5, clip=50 theta=0.96 variant=B "
               "hold_s=60 residual_cap_k=2 profit_lock_usd=5",
        "n_windows": len(rows),
        "units": "USD of principal, own-outcome terms, fees excluded",
        "max_committed_principal_usd": str(agg["committed"]),
        "max_outstanding_order_principal_usd": str(agg["outstanding"]),
        "max_directional_principal_usd": str(agg["directional"]),
        "max_combined_worst_case_capital_usd": str(agg["combined"]),
        "max_taker_fees_incurred_usd": str(agg["fees"]),
        "max_in_flight_cash_requirement_usd": str(agg["in_flight"]),
        "max_worst_case_loss_usd": str(agg["worst_case_loss"]),
        "cash_vs_loss": ("committed/in-flight are CASH USED, recoverable at "
                         "settlement; worst_case_loss is the unmatched leg "
                         "plus fees. They are different quantities and are "
                         "never summed."),
        "per_window": rows,
        "labels": "peaks across every decision point in each window, then the "
                  "max across windows. NOT an estimate from a contract count.",
    }
    with open(OUT, "w") as fh:
        json.dump(doc, fh, indent=2)
    for k in ("max_committed_principal_usd",
              "max_outstanding_order_principal_usd",
              "max_directional_principal_usd",
              "max_combined_worst_case_capital_usd",
              "max_taker_fees_incurred_usd",
              "max_in_flight_cash_requirement_usd",
              "max_worst_case_loss_usd"):
        print("%-42s %s" % (k, doc[k]))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
