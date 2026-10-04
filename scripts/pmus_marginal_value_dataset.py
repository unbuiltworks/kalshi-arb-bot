"""P2 step 1 — the action-level marginal-value dataset. Read-only.

At every decision opportunity Cell C actually had, this prices all three
candidate actions — BUY UP, BUY DOWN, WAIT — from the authenticated tape,
and records the portfolio state each would produce.

THE DECISION NEVER SEES THE OUTCOME. Settlement enters only as a realized
*label* attached after the feature row is built, so the dataset can be used
to ask where gross comes from without any rule here having peeked.

Marginal value is exact and additive, which avoids the lot-convention trap
that made `matched economics` ambiguous:

    value(purchase) = shares x 1{side settles in the money}
                      - cash paid - fee

Summed over every purchase, that IS the portfolio's net. No allocation
between `matched` and `residual` is required or performed.

WAIT is a first-class action and costs exactly zero.
"""
from __future__ import annotations
import bisect, json, os, sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util                                           # noqa: E402
from src import pmus_fees as FEES                               # noqa: E402

_s = importlib.util.spec_from_file_location(
    "dca", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pmus_favorite_dca.py"))
DCA = importlib.util.module_from_spec(_s)
_s.loader.exec_module(DCA)
ONE = D(1)
ALLOC = D(1)


def book_at(books, bt, t):
    j = bisect.bisect_left(bt, t) - 1
    while j >= 0 and (books[j]["bid"] is None or books[j]["ask"] is None):
        j -= 1
    return books[j] if j >= 0 else None


def main() -> int:
    by = DCA.load(DCA.TAPE)
    d = json.load(open("analysis/l5/favorite_dca/replay_summary.json"))
    rows = []
    for slug in sorted(d["windows"]):
        w = by[slug]
        books = w["book"]
        bt = [b["t"] for b in books]
        c = d["windows"][slug]["CELL_C_reversal_only"]
        oc = c["settlement_outcome"]
        start = DCA.window_start(slug)
        # the portfolio Cell C actually held, rebuilt purchase by purchase
        up = dn = up_cash = dn_cash = D(0)
        for a in c["allocation_log"]:
            t = a["t"]
            b = book_at(books, bt, t)
            if b is None:
                continue
            bid, ask = D(b["bid"]), D(b["ask"])
            bq, aq = (D(b["bid_qty"]) if b["bid_qty"] is not None else D(0),
                      D(b["ask_qty"]) if b["ask_qty"] is not None else D(0))
            matched0 = min(up, dn)
            resid0 = up - dn
            cu = up_cash / up if up > 0 else D(0)
            cd = dn_cash / dn if dn > 0 else D(0)
            cand = {}
            for side in ("UP", "DOWN"):
                px = ask if side == "UP" else ONE - bid
                depth = aq if side == "UP" else bq
                if not (D("0.01") <= px <= D("0.99")) or depth <= 0:
                    continue
                sh = min(ALLOC / px, depth)
                fee = FEES.taker_fee(px, sh)
                up2 = up + sh if side == "UP" else up
                dn2 = dn + sh if side == "DOWN" else dn
                matched1 = min(up2, dn2)
                new_matched = matched1 - matched0
                # cost of the shares that just became matched: the new share
                # at its own price, plus the counterpart share it paired
                # with, taken at the counterpart side's average cost
                counter = cd if side == "UP" else cu
                eff_pair = (px + counter) if new_matched > 0 else None
                resid1 = up2 - dn2
                cand[side] = {
                    "px": float(px), "shares": float(sh),
                    "cash": float(sh * px), "fee": float(fee),
                    "depth": float(depth),
                    "up_before": float(up), "dn_before": float(dn),
                    "up_after": float(up2), "dn_after": float(dn2),
                    "matched_before": float(matched0),
                    "matched_after": float(matched1),
                    "newly_matched": float(new_matched),
                    "eff_matched_pair_cost": (None if eff_pair is None
                                              else float(eff_pair)),
                    "residual_before": float(resid0),
                    "residual_after": float(resid1),
                    "residual_change_abs": float(abs(resid1) - abs(resid0)),
                    "residual_cost_basis": float(
                        abs(resid0) * (cu if resid0 > 0 else cd)),
                    # LABEL ONLY -- never an input to any rule here
                    "realized_value": float(
                        (sh if side == oc else D(0)) - sh * px - fee),
                }
            rows.append({
                "slug": slug, "t": t, "s_into_window": round(t - start, 1),
                "tte_s": round(start + 900 - t, 1),
                "up_px": float(ask), "dn_px": float(ONE - bid),
                "spread": float(ask - bid),
                "chosen_by_cellC": a["side"],
                "candidates": cand,
                "wait_value": 0.0,
            })
            # advance the real portfolio along Cell C's own choice
            side = a["side"]
            sh, px = D(a["sh"]), D(a["px"])
            if side == "UP":
                up += sh; up_cash += sh * px
            else:
                dn += sh; dn_cash += sh * px
        # Cell C's exits remove inventory; the dataset tracks purchases, and
        # the exits are carried as their own rows so turnover is complete
        for e in c["exit_log"]:
            for l in e["legs"]:
                rows.append({"slug": slug, "t": l["t"], "kind": "EXIT_LEG",
                             "side": e["side_exited"],
                             "shares": float(l["sh"]), "px": float(l["px"]),
                             "fee": float(l["fee"]),
                             "realized_value": float(
                                 D(l["sh"]) * D(l["px"]) - D(l["fee"])
                                 - D(l["sh"]) * (ONE if e["side_exited"] == oc
                                                 else D(0)))})
    json.dump(rows, open("/tmp/claude-0/-home-user-kalshi-arb-bot/"
                         "671883cb-e673-5581-ae31-5d97cf169fd1/scratchpad/"
                         "marginal.json", "w"))
    dec = [r for r in rows if "candidates" in r]
    print(json.dumps({"decision_opportunities": len(dec),
                      "exit_legs": len(rows) - len(dec),
                      "both_sides_priceable": sum(
                          1 for r in dec if len(r["candidates"]) == 2)},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
