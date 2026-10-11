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
                     settlement_px: Optional[D] = None) -> Dict[str, Any]:
    qty = {"YES": D("0"), "NO": D("0")}
    cost = {"YES": D("0"), "NO": D("0")}
    fees = D("0")
    unreadable: List[Dict[str, Any]] = []
    roles = {"AGGRESSOR": 0, "PASSIVE": 0}
    origins: Dict[str, int] = {}

    for f in seq.get("fills") or []:
        dirn = _direction(f)
        own = _own(f)
        shares = _d(f.get("shares"))
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
        "execution_roles": roles,
        "fill_origins": origins,
        "YES_shares": str(qty["YES"]), "NO_shares": str(qty["NO"]),
        "YES_avg_own_price": str(avg["YES"]),
        "NO_avg_own_price": str(avg["NO"]),
        "matched_pairs": str(matched),
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
        out["settlement"] = "UNSETTLED"
        out["unsettled_exposure_worst_case"] = str(
            -(res_cost) if res_leg else D("0"))
        out["realized_net"] = None
        out["labels"] = ("UNSETTLED. matched_pair_pnl is known; the residual "
                         "is not, and is NOT reported as 0.")
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
        st = state.get(slug) or {}
        spx = _d(st.get("settlementPx"))
        terminal = bool(spx is not None and str(st.get("state", "")).upper()
                        .find("EXPIRED") >= 0) or spx is not None
        rows.append(market_economics(seq, spx if terminal else None))

    settled = [r for r in rows if r.get("realized_net") is not None]
    unsettled = [r for r in rows if r.get("realized_net") is None]
    return {
        "label": "PMUS_SEQUENCE_ECONOMICS_V1",
        "accounting": ("venue records only. Settled realized net is stated "
                       "only where the venue gave a resolution; an "
                       "unsettled market is reported with its exposure and "
                       "never as 0."),
        "markets": len(rows),
        "markets_settled": len(settled),
        "markets_unsettled": len(unsettled),
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
