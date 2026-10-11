"""Same-market economics for the owner's real PMUS sequences. Offline.

READ-ONLY. No credentials, no transport, no executor. Every input is a venue
record: executions from the account's own activity feed, commission from the
venue's own `commissionNotionalCollected`, resolution from the venue.

THE PRICE CONVENTION, ONCE. PMUS states price in LONG (YES) token terms, so a
NO purchase arrives as ORDER_SIDE_SELL at a WIRE price of 1 - own. Every
number below is in OWN terms, converted with the engine's own
`l5_order_body.venue_price`, and direction comes from the engine's own
`l5_venue_accounting.direction` -- which raises rather than guessing LONG,
because guessing understates a short's settlement liability.

WHAT IS KEPT APART, AND NEVER SUMMED INTO ONE NUMBER:

  matched pairs      min(YES, NO) shares, which pay $1 per pair whatever the
                     outcome. Their P&L is known before resolution.
  residual           the unmatched remainder. Directional, and its P&L is
                     NOT known until the market resolves.
  fees               venue-confirmed commission only.
  rebates            venue-CREDITED only. A zero commission is NOT a rebate
                     and nothing is modelled here; absent means absent.
  settled realized   stated only where the venue gave a resolution. A market
                     without one is reported UNSETTLED with its exposure,
                     never as 0.

A cross-check runs against `l5_venue_accounting.venue_cashflow`, which works
in the venue's signed-cash view (a short CREDITS cash). The two must agree;
a disagreement is reported, not reconciled away.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

ONE = D("1")


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def _direction(fill: Dict[str, Any]) -> str:
    from src import l5_venue_accounting as va
    #: LIQUIDATION INTENTS, which the shared module does not know.
    #: `l5_venue_accounting.LONG_INTENTS/SHORT_INTENTS` cover only
    #: ORDER_INTENT_BUY_LONG and _BUY_SHORT. The venue also sends
    #: ORDER_INTENT_SELL_LONG and _SELL_SHORT (6 of 106 owned orders on this
    #: account), and for those the module falls through to its side-based
    #: guess and returns SHORT -- reading a SALE OF A LONG as OPENING A
    #: SHORT. `settlement_cash` then debits qty*settlement for a position
    #: that was closed and owes nothing. That is conservative for a risk
    #: bound and WRONG for cash, and it is reported rather than patched
    #: here: that module is consulted by live code and this audit is
    #: read-only.
    intent = str(fill.get("intent") or "").upper()
    if intent in ("ORDER_INTENT_SELL_LONG", "ORDER_INTENT_SELL_SHORT"):
        return "LIQUIDATION"
    try:
        return va.direction({"intent": fill.get("intent"),
                             "side": fill.get("side"),
                             "outcomeSide": fill.get("outcomeSide")})
    except Exception:                                          # noqa: BLE001
        return "UNKNOWN"


def _own(fill: Dict[str, Any]) -> Optional[D]:
    from src import l5_order_body as ob
    px = _d(fill.get("wire_px"))
    if px is None:
        return None
    outcome = fill.get("outcomeSide") or (
        "OUTCOME_SIDE_NO" if _direction(fill) == "SHORT"
        else "OUTCOME_SIDE_YES")
    return ob.venue_price(outcome, px)


def market_economics(seq: Dict[str, Any],
                     settlement_px: Optional[D] = None,
                     state_read: bool = False) -> Dict[str, Any]:
    qty = {"YES": D("0"), "NO": D("0")}
    cost = {"YES": D("0"), "NO": D("0")}
    fees = D("0")
    unreadable: List[Dict[str, Any]] = []
    liquidations: List[Dict[str, Any]] = []
    roles = {"AGGRESSOR": 0, "PASSIVE": 0}
    origins: Dict[str, int] = {}

    for f in seq.get("fills") or []:
        dirn = _direction(f)
        own = _own(f)
        shares = _d(f.get("shares"))
        if dirn == "LIQUIDATION":
            #: a sale of inventory already held. Netting it needs the
            #: position it closed, which the activity row does not state, so
            #: it is counted apart rather than signed into a leg.
            liquidations.append({"trade_id": f.get("trade_id"),
                                 "intent": f.get("intent"),
                                 "shares": f.get("shares"),
                                 "wire_px": f.get("wire_px")})
            c = _d(f.get("commission"))
            if c is not None:
                fees += c
            continue
        if dirn == "UNKNOWN" or own is None or shares is None:
            unreadable.append({"trade_id": f.get("trade_id"),
                               "why": "direction or price unreadable"})
            continue
        leg = "YES" if dirn == "LONG" else "NO"
        qty[leg] += shares
        cost[leg] += own * shares
        c = _d(f.get("commission"))
        if c is not None:
            fees += c
        roles[f.get("role", "PASSIVE")] = \
            roles.get(f.get("role", "PASSIVE"), 0) + 1
        mi = str(f.get("manualOrderIndicator") or "").replace(
            "MANUAL_ORDER_INDICATOR_", "") or "UNKNOWN"
        origins[mi] = origins.get(mi, 0) + 1

    # ---- FIFO PAIRING, in venue time order.
    #
    # Averaging the two legs and multiplying is ALLOCATION-DEPENDENT, and
    # this programme has already withdrawn a +$62.66 matched-economics claim
    # for exactly that reason. On the ATP market below, average pairing says
    # one number for two pairs that were really 1.10 (a locked loss) and 0.99
    # (a small win). Both views are reported; neither is presented alone.
    pend = {"YES": [], "NO": []}          # [shares, own_price] queues
    pairs: List[Dict[str, Any]] = []
    for f in sorted(seq.get("fills") or [],
                    key=lambda x: str(x.get("t") or "")):
        dirn = _direction(f)
        own = _own(f)
        sh = _d(f.get("shares"))
        if dirn not in ("LONG", "SHORT") or own is None or sh is None:
            continue
        leg = "YES" if dirn == "LONG" else "NO"
        other = "NO" if leg == "YES" else "YES"
        while sh > 0 and pend[other]:
            take = min(sh, pend[other][0][0])
            pairs.append({
                "shares": str(take),
                "YES_price": str(own if leg == "YES"
                                 else pend[other][0][1]),
                "NO_price": str(pend[other][0][1] if leg == "YES"
                                else own),
                "pair_cost": str(own + pend[other][0][1]),
                "pair_margin": str(ONE - (own + pend[other][0][1])),
                "t": f.get("t"),
            })
            pend[other][0][0] -= take
            sh -= take
            if pend[other][0][0] == 0:
                pend[other].pop(0)
        if sh > 0:
            pend[leg].append([sh, own])

    fifo_matched = sum((D(p2["shares"]) for p2 in pairs), D("0"))
    fifo_margin = sum((D(p2["shares"]) * D(p2["pair_margin"])
                       for p2 in pairs), D("0"))

    matched = min(qty["YES"], qty["NO"])
    avg = {k: (cost[k] / qty[k] if qty[k] else D("0")) for k in qty}
    paired_cost = matched * (avg["YES"] + avg["NO"])
    matched_pnl = matched - paired_cost
    res_leg = ("YES" if qty["YES"] > qty["NO"]
               else ("NO" if qty["NO"] > qty["YES"] else None))
    res_qty = abs(qty["YES"] - qty["NO"])
    res_cost = res_qty * avg[res_leg] if res_leg else D("0")

    out: Dict[str, Any] = {
        "slug": seq.get("slug"),
        "n_fills": len(seq.get("fills") or []),
        "fills_unreadable": unreadable,
        "liquidation_fills": liquidations,
        "liquidation_note": ("ORDER_INTENT_SELL_* closes inventory; netting "
                             "it needs the position it closed, which the "
                             "activity row does not state. Counted apart, "
                             "fees included, never signed into a leg."),
        "execution_roles": roles,
        "fill_origins": origins,
        "YES_shares": str(qty["YES"]), "NO_shares": str(qty["NO"]),
        "YES_avg_own_price": str(avg["YES"]),
        "NO_avg_own_price": str(avg["NO"]),
        "matched_pairs": str(matched),
        "fifo_pairs": pairs,
        "fifo_matched_shares": str(fifo_matched),
        "fifo_paired_margin": str(fifo_margin),
        "fifo_pair_costs": [p2["pair_cost"] for p2 in pairs],
        "pairing_note": ("fifo_* pairs the legs in VENUE TIME ORDER; the "
                         "matched_*/paired_acquisition_cost lines use leg "
                         "averages. Averaging is allocation-dependent -- a "
                         "+$62.66 matched claim was withdrawn over it -- so "
                         "both are reported and neither stands alone."),
        "paired_acquisition_cost": str(paired_cost),
        "paired_cost_per_pair": (str(paired_cost / matched) if matched
                                 else None),
        "matched_pair_pnl": str(matched_pnl),
        "residual_side": res_leg, "residual_shares": str(res_qty),
        "residual_cost": str(res_cost),
        "fees_venue_confirmed": str(fees),
        "rebates_credited": "0",
        "rebates_note": ("venue-CREDITED only; none read. A zero commission "
                         "is not a rebate and nothing is modelled."),
    }

    if settlement_px is None:
        #: THREE STATES, NOT TWO. "no settlement evidence was read" is not
        #: "the market is still open", and rolling the first into the second
        #: is the same fail-open that made the BTC15m engine print 0 for 24
        #: traded windows. `state_read` says which this is.
        out["settlement"] = ("UNSETTLED_VENUE_SAYS_OPEN" if state_read
                             else "SETTLEMENT_NOT_READ")
        out["unsettled_exposure_worst_case"] = str(
            -(res_cost) if res_leg else D("0"))
        out["realized_net"] = None
        out["labels"] = (
            ("UNSETTLED. matched_pair_pnl is known; the residual is not, and "
             "is NOT reported as 0.") if state_read else
            ("SETTLEMENT NOT READ for this market -- no venue state was "
             "fetched for it. This is NOT evidence that it is open, and it "
             "is excluded from the settled AND the unsettled totals."))
    else:
        # a YES share pays settlement_px; a NO share pays 1 - settlement_px
        pay = (settlement_px if res_leg == "YES"
               else (ONE - settlement_px)) if res_leg else D("0")
        res_pnl = (res_qty * pay) - res_cost if res_leg else D("0")
        out["settlement_px"] = str(settlement_px)
        out["directional_residual_pnl"] = str(res_pnl)
        out["realized_net"] = str(matched_pnl + res_pnl - fees)
        out["labels"] = ("SETTLED. matched and residual are reported apart "
                         "and never summed into one number before fees.")
    return out


def run(audit: Dict[str, Any]) -> Dict[str, Any]:
    state = audit.get("market_state") or {}
    rows: List[Dict[str, Any]] = []
    for seq in audit.get("sequences") or []:
        slug = str(seq.get("slug"))
        st = state.get(slug)
        state_read = st is not None and "unreadable" not in (st or {})
        spx = _d((st or {}).get("settlementPx"))
        rows.append(market_economics(seq, spx, state_read=state_read))

    settled = [r for r in rows if r.get("realized_net") is not None]
    unsettled = [r for r in rows
                 if r.get("settlement") == "UNSETTLED_VENUE_SAYS_OPEN"]
    not_read = [r for r in rows if r.get("settlement") == "SETTLEMENT_NOT_READ"]
    return {
        "label": "PMUS_SEQUENCE_ECONOMICS_V1",
        "accounting": ("venue records only. Settled realized net is stated "
                       "only where the venue gave a resolution; an "
                       "unsettled market is reported with its exposure and "
                       "never as 0."),
        "markets": len(rows),
        "markets_settled": len(settled),
        "markets_unsettled_venue_says_open": len(unsettled),
        "markets_settlement_not_read": len(not_read),
        "markets_with_owned_fills": sum(1 for r in rows if r["n_fills"]),
        "not_read_note": ("a market whose venue state was never fetched is "
                          "in NEITHER total. It is not evidence of being "
                          "open and its economics are unknown, not zero."),
        "settled_realized_net": str(sum((D(r["realized_net"])
                                         for r in settled), D("0"))),
        "settled_fees": str(sum((D(r["fees_venue_confirmed"])
                                 for r in settled), D("0"))),
        "settled_matched_pairs": str(sum((D(r["matched_pairs"])
                                          for r in settled), D("0"))),
        "unsettled_residual_shares": str(sum((D(r["residual_shares"])
                                              for r in unsettled), D("0"))),
        "unsettled_exposure_worst_case": str(sum(
            (D(r.get("unsettled_exposure_worst_case", "0"))
             for r in unsettled), D("0"))),
        "rebates_credited_total": "0",
        "per_market": rows,
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("audit_json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    r = run(json.loads(Path(a.audit_json).read_text()))
    print(json.dumps({k: v for k, v in r.items() if k != "per_market"},
                     indent=2))
    hdr = ("%-44s %6s %8s %8s %9s %9s %8s %10s"
           % ("market", "fills", "YES", "NO", "pairs", "pair$", "fees",
              "realized"))
    print()
    print(hdr)
    print("-" * len(hdr))
    for m in r["per_market"]:
        print("%-44s %6d %8s %8s %9s %9s %8s %10s"
              % (str(m["slug"])[:44], m["n_fills"], m["YES_shares"],
                 m["NO_shares"], m["matched_pairs"],
                 m["paired_cost_per_pair"] or "-",
                 m["fees_venue_confirmed"],
                 m["realized_net"] if m["realized_net"] is not None
                 else "UNSETTLED"))
    if a.out:
        Path(a.out).write_text(json.dumps(r, indent=2, default=str))
        print("\nwrote %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
