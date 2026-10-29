"""Per-window fill, inventory and incentive accounting from existing records.

WHAT THIS IS NOT. It is not a service, a dashboard, or a second source of
truth. Every number here is derived from records the engine already writes --
pre-submits, order lifecycle events, the settlement ledger, and credited
rebates -- and the settlement figures it reports are the venue's own, copied
rather than recomputed. Nothing here can refuse an order, change a price, or
move a limit.

THE THREE COUNTING TRAPS IT EXISTS TO AVOID.

1. A PARTIAL FILL IS NOT SEVERAL ORDERS. `L5_LIVE_LIFECYCLE` emits
   ORDER_PROGRESS repeatedly for the same order, carrying a CUMULATIVE
   `cum_qty` and the increment `delta`. Counting rows would count one order
   many times; summing `cum_qty` would multiply one fill by the number of
   times we polled it. So: a FILL EVENT is a row with `delta > 0`, a FILLED
   ORDER is a distinct `coid` with any such row, and FILLED CONTRACTS is the
   per-coid FINAL cumulative quantity, taken once.

2. REPEATED EVENTS ARE DEDUPLICATED BY IDENTITY, not by position. Lifecycle
   rows are keyed on (coid, cum_qty) so a re-read that reports the same
   cumulative twice contributes one fill event, not two.

3. AN ESTIMATED REBATE IS NOT INCOME. Estimates and credits live in separate
   fields that are never summed, and a credit is counted only when it carries
   a durable `credit_id`, deduplicated on that id, so replaying the same
   statement twice cannot double-count it. `realized_net_usd` is settlement
   only and contains no rebate of either kind.

MATCHED VERSUS RESIDUAL comes from the settlement record, which reports them
as separate lines and does not sum them. This module preserves that.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: a leg's role within its window, derived from submission order rather than
#: declared, because the planner places the cheaper leg first and the record
#: of which it was lives only in the sequence.
FIRST_LEG = "FIRST_LEG"
COMPLEMENT = "COMPLEMENT"

#: L5's own BTC15m fill rate, as a COMPARISON REFERENCE ONLY.
#:
#: 693,916 fills across 5,546 windows of the frozen Polymarket corpus
#: (2026-03-19 to 08-31) = 32.48 fills per window. Reported beside our own
#: rate so the throughput gap is visible without arithmetic; see
#: analysis/l5/L5_INVENTORY_TRANSLATION.md.
#:
#: IT IS NOT A TARGET AND NOTHING READS IT. His rate is what let thin matched
#: margins accumulate, and the same corpus shows his per-fill edge falling 45x
#: inside it, with 98.0% of BTC15m net earned before the holdout. Copying the
#: rate is separately contraindicated at our size: our residuals sit at the
#: 5.7th-15.3rd percentile of his, where his demonstrated behaviour is
#: EXTEND 0.713 / COMPLEMENT 0.027.
L5_REFERENCE_FILLS_PER_WINDOW = D("32.48")
L5_REFERENCE_BASIS = ("L5 BTC15m frozen corpus: 693,916 fills / 5,546 "
                      "windows = 32.48 per window, 2026-03-19 to 08-31. "
                      "COMPARISON REFERENCE ONLY -- not a target, not read by "
                      "any gate, and his per-fill edge fell 45x inside that "
                      "same corpus.")


def _d(v: Any) -> Optional[D]:
    """A Decimal, or None. Never raises, never guesses a zero: an unreadable
    quantity is UNKNOWN, and a count built on it is reported as unreadable
    rather than silently short."""
    if v is None:
        return None
    try:
        out = D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if out.is_finite() else None


def _own_price(side: Any, wire: Any) -> Optional[D]:
    """Own-outcome price from a WIRE price. PMUS wires everything in YES/UP
    terms, so a DOWN leg's own price is 1 - wire. Getting this backwards is
    the single easiest way to misreport a cost basis."""
    w = _d(wire)
    if w is None:
        return None
    s = str(side or "").strip().upper()
    if s.endswith("DOWN"):
        return D(1) - w
    if s.endswith("UP"):
        return w
    return None


def fill_events(lifecycle: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicated fill increments. One entry per (coid, cumulative) pair
    with a positive delta."""
    seen = set()
    out: List[Dict[str, Any]] = []
    for row in lifecycle or ():
        if not isinstance(row, Mapping):
            continue
        coid = row.get("coid")
        if not coid:
            continue
        delta = _d(row.get("delta"))
        cum = _d(row.get("cum_qty"))
        if cum is None:
            cum = _d(row.get("cum"))
        if delta is None or delta <= 0:
            continue
        key = (str(coid), str(cum))
        if key in seen:
            continue
        seen.add(key)
        out.append({"coid": str(coid), "delta": delta, "cum": cum,
                    "state": row.get("state"),
                    "commission": _d(row.get("commission")),
                    "avg_px_wire": _d(row.get("avg_px"))})
    return out


def merge_fill_sources(lifecycle: Sequence[Mapping[str, Any]],
                       fills: Sequence[Mapping[str, Any]] = (),
                       ) -> List[Dict[str, Any]]:
    """Lifecycle rows plus any fill the lifecycle stream never reported.

    A FILL THAT RACES ITS OWN CANCELLATION DOES NOT PRODUCE A LIFECYCLE
    DELTA. Campaign 6's 1630z DOWN leg (l5f-4fad3a63e7, 2.15 at wire 0.4900)
    filled microseconds before the requote's cancel took. The venue reported
    it on the cancel re-read, the inventory book took it with its cost basis,
    and the cash moved -- but its only ORDER_PROGRESS row said cum 0, so
    `fill_events` and `qty_filled` both scored it zero. The campaign counted
    11 fills and 21.67 contracts where the venue had settled 12 and 23.82.

    Money was never wrong: the pair matched, the residual was zero and the
    cash agreed. Only the counters were, which is worse than it sounds --
    those counters are what a campaign is judged repeatable by.

    So the fill-record stream is merged in as a second source. A fill already
    carried by the lifecycle is dropped on its cumulative quantity, which is
    why running this twice is identical to running it once. Keys are read
    loosely because the two producers disagree on them: the recorder writes
    `cumulative_qty`/`price`, the cancel path writes `qty`/`avg_px`.
    """
    merged: List[Dict[str, Any]] = [dict(r) for r in (lifecycle or ())
                                    if isinstance(r, Mapping)]
    best: Dict[str, D] = {}
    for row in merged:
        coid = row.get("coid")
        if not coid:
            continue
        cum = _d(row.get("cum_qty"))
        if cum is None:
            cum = _d(row.get("cum"))
        if cum is None:
            continue
        key = str(coid)
        if key not in best or cum > best[key]:
            best[key] = cum
    for f in (fills or ()):
        if not isinstance(f, Mapping):
            continue
        coid = f.get("coid") or f.get("client_order_id")
        if not coid:
            continue
        cum = _d(f.get("cumulative_qty"))
        if cum is None:
            cum = _d(f.get("cum_qty"))
        if cum is None:
            # The cancel path reports the whole fill with leaves at zero, so
            # its qty IS the cumulative. Nothing is inferred beyond that.
            cum = _d(f.get("qty"))
        if cum is None or cum <= 0:
            continue
        key = str(coid)
        seen = best.get(key)
        if seen is not None and seen >= cum:
            continue            # the lifecycle already has this fill
        delta = cum - (seen if seen is not None else D(0))
        px = _d(f.get("price"))
        if px is None:
            px = _d(f.get("avg_px"))
        merged.append({
            "coid": key,
            "cum_qty": cum,
            "delta": delta,
            "state": "ORDER_STATE_FILLED",
            "terminal": True,
            "avg_px": px,
            "commission": _d(f.get("commission")),
            "event": "FILL_RECONSTRUCTED",
            "source": "fill_record",
            "labels": ("booked from the venue's own fill record because no "
                       "lifecycle delta carried it; not an assumed fill"),
        })
        best[key] = cum
    return merged


def order_outcomes(submits: Sequence[Mapping[str, Any]],
                   lifecycle: Sequence[Mapping[str, Any]]
                   ) -> List[Dict[str, Any]]:
    """One row per SUBMITTED order, in submission order, with its final state.

    Submission order is what assigns FIRST_LEG and COMPLEMENT: the planner
    places the cheaper leg first, and nothing in an individual record says
    which role it held.
    """
    finals: Dict[str, Dict[str, Any]] = {}
    for row in lifecycle or ():
        if not isinstance(row, Mapping):
            continue
        coid = row.get("coid")
        if not coid:
            continue
        cum = _d(row.get("cum_qty"))
        if cum is None:
            cum = _d(row.get("cum"))
        cur = finals.get(str(coid))
        # the FINAL cumulative is the largest seen; a terminal row wins
        # outright because it is the venue's last word on the order.
        if (cur is None
                or bool(row.get("terminal"))
                or (cum is not None and cur.get("cum") is not None
                    and cum > cur["cum"])):
            finals[str(coid)] = {
                "cum": cum, "state": row.get("state"),
                "terminal": bool(row.get("terminal")),
                "commission": _d(row.get("commission")),
                "avg_px_wire": _d(row.get("avg_px"))}
    ev = fill_events(lifecycle)
    ev_by_coid: Dict[str, int] = {}
    for e in ev:
        ev_by_coid[e["coid"]] = ev_by_coid.get(e["coid"], 0) + 1
    out: List[Dict[str, Any]] = []
    # ROLE IS AN INVENTORY QUESTION, NOT A POSITION IN A LIST.
    #
    # Assigning FIRST_LEG to submits[0] and COMPLEMENT to everything after it
    # is wrong the moment a first leg is cancelled and re-placed, which is
    # exactly what stale recovery does: 0100z submitted DOWN, cancelled it,
    # re-placed DOWN, then completed with UP. Index order called the
    # replacement a complement and reported 0 first-leg contracts against
    # 2.08 complement contracts for a window that filled 1.04 of each.
    #
    # The economic rule: a leg is a COMPLEMENT when the OPPOSITE side already
    # holds filled inventory at the moment it is submitted. Otherwise it is a
    # first leg -- including a replacement of one, which is still the window's
    # opening exposure.
    held: Dict[str, D] = {"UP": D(0), "DOWN": D(0)}
    for s in (submits or ()):
        if not isinstance(s, Mapping):
            continue
        coid = str(s.get("coid") or "")
        f = finals.get(coid, {})
        side = str(s.get("side") or "").strip().upper()
        other = "DOWN" if side.endswith("UP") else "UP"
        role = COMPLEMENT if held.get(other, D(0)) > 0 else FIRST_LEG
        got_now = f.get("cum")
        if side.endswith("UP"):
            held["UP"] += (got_now or D(0))
        elif side.endswith("DOWN"):
            held["DOWN"] += (got_now or D(0))
        asked = _d(s.get("qty"))
        got = f.get("cum")
        state = str(f.get("state") or "").replace("ORDER_STATE_", "") or None
        out.append({
            "coid": coid,
            "role": role,
            "side": s.get("side"),
            "own_px": _d(s.get("own_px")),
            "qty_submitted": asked,
            "qty_filled": got,
            "state": state,
            "terminal": f.get("terminal", False),
            "acknowledged": bool(f),
            "fill_events": ev_by_coid.get(coid, 0),
            "partial": (asked is not None and got is not None
                        and D(0) < got < asked),
            "mode": s.get("mode") or "MAKER",
            "commission": f.get("commission"),
            "rebate_estimate_usd": _d(
                ((s.get("fees") or {}).get("schedule_estimate") or {})
                .get("maker_rebate_usd")),
        })
    return out


def pair_economics(orders: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Split completed pairs into pair edge and DEFENSIVE loss.

    A matched UP/DOWN pair redeems exactly $1.00, so its cost is the sum of
    the two OWN prices and its result is fixed the moment both legs fill.
    Below $1.00 that is pair edge. Above $1.00 it is a loss taken on purpose.

    Those are different events and summing them hides the one worth watching.
    Campaign 6's 1600z window completed 1.94 at own 0.41 + 0.64 = 1.05, a
    certain -$0.0970, because the alternative was carrying 1.94 DOWN
    unmatched against a -$0.7907 downside. The
    completion logic chose correctly and the venue's matched_pnl line then
    reported +$1.5908 for the campaign with that loss folded invisibly in.
    Netted together they read as thinner edge; separated they read as what
    they are -- $1.6878 of pair edge and $0.0970 spent bounding a tail.

    This is OUR decomposition from OUR fills, not a venue line. It never
    feeds realized_net_usd, which stays venue-settlement-only.

    Legs pair FIFO in submission order, the same ordering that assigns
    FIRST_LEG and COMPLEMENT, and a leg with no readable own price is left
    unpaired and counted rather than guessed at.

    FIFO IS A CHOICE AND IT IS NOT THE ENGINE'S BASIS. The inventory store
    carries a weighted-average cost per side, so where lots are uneven --
    two openings of different size against one complement, say -- FIFO pair
    costs and weighted-average pair costs disagree, and only the totals are
    guaranteed to match. Campaign 6's six pairs were all 1:1 and equal
    quantity, so the two agree there exactly; a window with uneven lots
    should be read as a distribution of pair costs under FIFO, not as the
    basis the engine priced against.
    """
    lots: Dict[str, List[List[Any]]] = {"UP": [], "DOWN": []}
    pairs: List[Dict[str, Any]] = []
    unpriceable = 0
    for o in (orders or ()):
        if not isinstance(o, Mapping):
            continue
        got = _d(o.get("qty_filled"))
        if got is None or got <= 0:
            continue
        side = str(o.get("side") or "").strip().upper()
        side = "UP" if side.endswith("UP") else (
            "DOWN" if side.endswith("DOWN") else "")
        own = _d(o.get("own_px"))
        if not side or own is None:
            unpriceable += 1
            continue
        lots[side].append([got, own, o.get("coid")])
        while lots["UP"] and lots["DOWN"]:
            up, dn = lots["UP"][0], lots["DOWN"][0]
            q = min(up[0], dn[0])
            cost = up[1] + dn[1]
            pairs.append({"qty": q, "pair_cost": cost,
                          "up_own": up[1], "dn_own": dn[1],
                          "pnl_usd": q * (D(1) - cost),
                          "defensive": cost > D(1),
                          "up_coid": up[2], "dn_coid": dn[2]})
            up[0] -= q
            dn[0] -= q
            if up[0] == 0:
                lots["UP"].pop(0)
            if dn[0] == 0:
                lots["DOWN"].pop(0)
    edge = sum((p["pnl_usd"] for p in pairs if not p["defensive"]), D(0))
    defensive = sum((p["pnl_usd"] for p in pairs if p["defensive"]), D(0))
    return {
        "pairs": pairs,
        "pairs_completed": len(pairs),
        "matched_qty": sum((p["qty"] for p in pairs), D(0)),
        "pair_edge_usd": edge,
        "defensive_loss_usd": defensive,
        "defensive_pairs": sum(1 for p in pairs if p["defensive"]),
        "worst_pair_cost": (max((p["pair_cost"] for p in pairs))
                            if pairs else None),
        "residual_qty": {s: sum((l[0] for l in lots[s]), D(0))
                         for s in ("UP", "DOWN")},
        "unpriceable_legs": unpriceable,
        "labels": ("DERIVED from our own fills, never a venue line, and "
                   "never summed into realized_net_usd. pair_edge_usd "
                   "counts pairs at or under $1.00; defensive_loss_usd "
                   "counts pairs above it, which are risk-reducing "
                   "completions and are expected to be negative."),
    }


def credited_rebates(credits: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Only ACTUALLY credited rebates, deduplicated on a durable id.

    A credit with no `credit_id` is NOT counted: without a durable identifier
    there is no way to know whether a later statement is the same credit
    again, and double-counting income is worse than reporting none.
    """
    seen = set()
    total = D(0)
    kept: List[Dict[str, Any]] = []
    skipped = 0
    for c in credits or ():
        if not isinstance(c, Mapping):
            skipped += 1
            continue
        cid = str(c.get("credit_id") or "").strip()
        usd = _d(c.get("usd"))
        if not cid or usd is None:
            skipped += 1
            continue
        if cid in seen:
            continue
        seen.add(cid)
        total += usd
        kept.append({"credit_id": cid, "usd": usd, "coid": c.get("coid")})
    return {"credited_usd": total, "credits": kept,
            "unidentifiable_skipped": skipped}


def window_record(*, market: str,
                  submits: Sequence[Mapping[str, Any]] = (),
                  lifecycle: Sequence[Mapping[str, Any]] = (),
                  fills: Sequence[Mapping[str, Any]] = (),
                  settlement: Optional[Mapping[str, Any]] = None,
                  rebate_credits: Iterable[Mapping[str, Any]] = (),
                  rebate_eligibility: str = "UNVERIFIED",
                  ) -> Dict[str, Any]:
    """One window's accounting. PROVISIONAL without a settlement record,
    CONFIRMED with one -- the same window reported twice, never two windows.

    `fills` is the venue's fill-record stream, merged in so a fill that raced
    its own cancellation is counted; see merge_fill_sources. Omitting it
    reproduces the old counts exactly.
    """
    lifecycle = merge_fill_sources(lifecycle, fills)
    orders = order_outcomes(submits, lifecycle)
    ev = fill_events(lifecycle)
    filled = [o for o in orders if (o["qty_filled"] or D(0)) > 0]
    est = sum((o["rebate_estimate_usd"] or D(0) for o in orders), D(0))
    comm = sum((o["commission"] or D(0) for o in filled), D(0))
    reb = credited_rebates(rebate_credits)

    def _role(role: str) -> D:
        return sum(((o["qty_filled"] or D(0))
                    for o in filled if o["role"] == role), D(0))

    rec: Dict[str, Any] = {
        "market": market,
        "phase": "CONFIRMED" if settlement else "PROVISIONAL",
        "orders": {
            "submitted": len(orders),
            "acknowledged": sum(1 for o in orders if o["acknowledged"]),
            "terminal": sum(1 for o in orders if o["terminal"]),
            "filled_orders": len(filled),
            "partial_fills": sum(1 for o in orders if o["partial"]),
            "cancelled": sum(1 for o in orders if o["state"] == "CANCELED"),
            "rejected": sum(1 for o in orders
                            if o["state"] in ("REJECTED", "REJECT")),
            "fill_events": len(ev),
            "labels": ("fill_events counts deduplicated positive deltas; "
                       "filled_orders counts distinct coids. A partial fill "
                       "is ONE order and may be several events."),
        },
        "contracts": {
            "filled_total": sum((o["qty_filled"] or D(0) for o in filled),
                                D(0)),
            "first_leg_filled": _role(FIRST_LEG),
            "complement_filled": _role(COMPLEMENT),
        },
        "execution": {
            "maker_orders": sum(1 for o in orders if o["mode"] == "MAKER"),
            "taker_orders": sum(1 for o in orders if o["mode"] == "TAKER"),
            "actual_fees_usd": comm,
            "labels": ("actual_fees_usd is the venue's own commission on "
                       "filled orders. Maker-only execution charges none."),
        },
        "incentives": {
            "rebate_estimate_usd": est,
            "rebate_credited_usd": reb["credited_usd"],
            "rebate_pending_usd": est - reb["credited_usd"],
            "credits": reb["credits"],
            "unidentifiable_credits_skipped": reb["unidentifiable_skipped"],
            "eligibility": rebate_eligibility,
            "labels": ("an ESTIMATE is never income. Only credits with a "
                       "durable credit_id are counted, deduplicated on it. "
                       "realized_net_usd below excludes BOTH."),
        },
        "pair_economics": pair_economics(orders),
    }
    if settlement is None:
        rec["provisional_inventory"] = {
            "filled_contracts": rec["contracts"]["filled_total"],
            "matched_estimate": min(_role(FIRST_LEG), _role(COMPLEMENT)),
            "unmatched_estimate": abs(_role(FIRST_LEG)
                                      - _role(COMPLEMENT)),
            "labels": ("PROVISIONAL, from our own fills. Matched and "
                       "unmatched are rewritten from the venue's settlement "
                       "record, which is authority."),
        }
        rec["realized_net_usd"] = None
        return rec
    rec["settled"] = {
        "matched_qty": _d(settlement.get("matched_qty")),
        "matched_pnl_usd": _d(settlement.get("matched_pnl")),
        "residual_qty": _d(settlement.get("residual_qty")),
        "residual_side": settlement.get("residual_side"),
        "residual_pnl_usd": _d(settlement.get("residual_pnl")),
        "settlement_pnl_usd": _d(settlement.get("settlement_pnl")),
        "resolution_source": settlement.get("resolution_source"),
        "venue_state": settlement.get("venue_state"),
        "labels": ("the venue's own lines, copied not recomputed. matched and "
                   "residual are separate and are NOT summed here."),
    }
    rec["realized_net_usd"] = _d(settlement.get("settlement_pnl"))
    rec["realized_basis"] = ("venue-confirmed settlement only. Excludes "
                            "estimated rebates, pending rebates and credited "
                            "rebates alike; credited rebates are reported in "
                            "`incentives` and must be added deliberately, "
                            "never implicitly.")
    return rec


def campaign_rolling(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Rolling totals across windows. Confirmed windows only contribute to
    realized; provisional ones are counted and named, never netted in."""
    conf = [r for r in records if r.get("phase") == "CONFIRMED"]
    prov = [r for r in records if r.get("phase") == "PROVISIONAL"]

    def s(rs, *path) -> D:
        t = D(0)
        for r in rs:
            cur: Any = r
            for k in path:
                cur = (cur or {}).get(k) if isinstance(cur, Mapping) else None
            v = _d(cur)
            if v is not None:
                t += v
        return t
    matched = s(conf, "settled", "matched_pnl_usd")
    residual = s(conf, "settled", "residual_pnl_usd")
    realized = s(conf, "realized_net_usd")
    windows_with_fills = sum(
        1 for r in conf if (_d((r.get("contracts") or {}).get("filled_total"))
                            or D(0)) > 0)
    paired = sum(1 for r in conf
                 if (_d((r.get("settled") or {}).get("matched_qty"))
                     or D(0)) > 0)
    return {
        "windows_confirmed": len(conf),
        "windows_provisional": len(prov),
        "windows_with_fills": windows_with_fills,
        "windows_with_a_completed_pair": paired,
        "completion_rate": (None if not windows_with_fills
                            else D(paired) / D(windows_with_fills)),
        "orders_submitted": int(s(conf, "orders", "submitted")
                                + s(prov, "orders", "submitted")),
        "fill_events": int(s(conf, "orders", "fill_events")
                           + s(prov, "orders", "fill_events")),
        "filled_orders": int(s(conf, "orders", "filled_orders")
                             + s(prov, "orders", "filled_orders")),
        "partial_fills": int(s(conf, "orders", "partial_fills")
                             + s(prov, "orders", "partial_fills")),
        "contracts_filled": s(conf, "contracts", "filled_total")
        + s(prov, "contracts", "filled_total"),
        "matched_surplus_usd": matched,
        "residual_pnl_usd": residual,
        # The venue's matched line folds a risk-reducing completion above
        # $1.00 into the same number as pair edge. These two separate it, from
        # our own fills, across every window including provisional ones.
        "pair_edge_usd": s(conf, "pair_economics", "pair_edge_usd")
        + s(prov, "pair_economics", "pair_edge_usd"),
        "defensive_loss_usd": s(conf, "pair_economics", "defensive_loss_usd")
        + s(prov, "pair_economics", "defensive_loss_usd"),
        "defensive_pairs": int(s(conf, "pair_economics", "defensive_pairs")
                               + s(prov, "pair_economics",
                                   "defensive_pairs")),
        "pairs_completed": int(s(conf, "pair_economics", "pairs_completed")
                               + s(prov, "pair_economics",
                                   "pairs_completed")),
        "actual_fees_usd": s(conf, "execution", "actual_fees_usd")
        + s(prov, "execution", "actual_fees_usd"),
        "realized_net_usd": realized,
        "rebate_estimate_usd": s(conf, "incentives", "rebate_estimate_usd")
        + s(prov, "incentives", "rebate_estimate_usd"),
        "rebate_credited_usd": s(conf, "incentives", "rebate_credited_usd")
        + s(prov, "incentives", "rebate_credited_usd"),
        "l5_reference": {
            "fills_per_window": L5_REFERENCE_FILLS_PER_WINDOW,
            "ours_fills_per_window": (
                None if not (len(conf) + len(prov))
                else (D(int(s(conf, "orders", "fill_events")
                             + s(prov, "orders", "fill_events")))
                      / D(len(conf) + len(prov)))),
            "basis": L5_REFERENCE_BASIS,
        },
        "labels": ("realized_net_usd is the sum of venue-confirmed "
                   "settlement lines over CONFIRMED windows only. "
                   "Provisional windows are counted and never netted. No "
                   "rebate of any kind is inside it. pair_edge_usd and "
                   "defensive_loss_usd are OUR decomposition of our own "
                   "fills and are not venue lines: they split what the "
                   "venue's matched line reports as one number, and are "
                   "never added to realized_net_usd."),
    }
