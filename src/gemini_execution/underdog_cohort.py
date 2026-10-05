"""BTC5m UNDERDOG COHORT 1 -- LIVE, MONEY-MOVING, BOUNDED, EXPLORATORY.

AUTHORIZATION: OWNER-2026-10-05-GEMINI-BTC5M-UNDERDOG-1, granted explicitly
by the owner, with the $0.60/share acquisition ceiling added before arming.
It is EXPLORATORY LIVE EXECUTION, not validation of an underdog strategy: a
side chosen by relative price expresses no forecast, and five q=1 fills
cannot establish expectancy either way.

WHAT IS DIFFERENT FROM CANARY-1, and why each difference exists:

  SIDE. Canary-1 bought UP always, which took no view. This buys the
  UNDERDOG -- whichever of UP or DOWN has the LOWER executable q1 CUMULATIVE
  VWAP. So the DOWN leg is live for the first time in this programme's BTC5m
  work, and DOWN is bought as outcome NO at its own price, which is what
  consuming the YES bid ladder actually costs.

  PRICE. The limit is the CAPTURED executable VWAP of the chosen side, never
  a touch price and never re-derived at send time: the price that passed the
  gates is the price that is sent, so there is no chasing.

  CEILING. An absolute $0.60/share ceiling on the selected underdog. A
  window whose cheaper side is above it is NO_TRADE and DOES NOT consume an
  attempt. Five is a maximum, not a quota, and the ceiling is never loosened
  to reach it.

EXPOSURE IS DERIVED, NOT ASSERTED. Worst case per trade is the ceiling plus
the venue's reconciled fee at its worst admissible price on the venue's own
0.01 grid, and the cohort cap is five of those. Both are computed here and
printed before arming.

FAIL CLOSED ON: a halted cohort, an envelope mismatch, either authorization
constant not naming this grant, a decision that does not qualify, the
attempt cap, the outlay cap, an outstanding order, an unreadable account or
order state, and attribution uncertainty. Nothing is downgraded to a
warning.
"""
from __future__ import annotations

import json
import os
import time
from decimal import ROUND_CEILING, Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution import arming
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution import payload, transport

LABEL = "GEMINI_BTC5M_UNDERDOG_V1"
UNDERDOG_AUTH_ID = "OWNER-2026-10-05-GEMINI-BTC5M-UNDERDOG-1"

#: THE OWNER'S ENVELOPE, verbatim, including the clarification.
AUTHORIZED_ENVELOPE: Dict[str, Any] = {
    "duration": "BTC05M",
    "quantity": "1",
    "max_acquisition_price": "0.60",       # the added absolute ceiling
    "max_entry_attempts": 5,
    "max_attempts_per_window": 1,
    "max_outstanding_orders": 1,
    "hold_to_settlement": True,
    "sell_permitted": False,
    "averaging_down_permitted": False,
    "size_changes_permitted": False,       # no martingale is expressible
    "price_chasing_permitted": False,
    "side_rule": "UNDERDOG_LOWER_EXECUTABLE_Q1_VWAP",
    "min_checkpoint_s_since_open": 15.0,
    "max_quote_age_s": 2.0,
    "depth_variant": "CUMULATIVE",
    "deadman": True,
    "above_ceiling_is": "NO_TRADE_AND_DOES_NOT_CONSUME_AN_ATTEMPT",
}

VENUE_QUANTITY_MINIMUM = D("0.01")
VENUE_QUANTITY_INCREMENT = D("0.01")
VENUE_PRICE_INCREMENT = D("0.01")
#: the venue's reconciled taker rate: predicted 0.0155 / 0.0168 / 0.0161 /
#: 0.0135 against cohort 1's charged $0.02 each, at cent rounding.
TAKER_RATE = D("0.07")
FEE_QUANTUM = D("0.01")

LEDGER_DIR = "analysis/gemini/btc5m_underdog"
LEDGER = os.path.join(LEDGER_DIR, "cohort.jsonl")
HALT = os.path.join(LEDGER_DIR, "HALT.json")

SIDE_OUTCOME = {"UP": payload.spec.OUTCOME_YES,
                "DOWN": payload.spec.OUTCOME_NO}


class UnderdogRefused(Exception):
    """Raised before anything is signed. Never downgraded to a warning."""


def _utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _d(v: Any) -> Optional[D]:
    """A Decimal, or None. An unreadable number is never a zero."""
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


# ------------------------------------------------------------- the exposure
def fee_at(price: D, qty: D = D("1")) -> D:
    """The venue's charged taker fee, at the cent rounding it applied.

    Rounded UP for an exposure ceiling: a bound that rounds down is not a
    bound.
    """
    raw = TAKER_RATE * qty * price * (D("1") - price)
    if raw <= 0:
        return D("0")
    return raw.quantize(FEE_QUANTUM, rounding=ROUND_CEILING)


def worst_case_fee() -> Dict[str, Any]:
    """The worst fee ADMISSIBLE under the ceiling, searched on the venue's
    own 0.01 price grid rather than evaluated only at the ceiling.

    The fee is p*(1-p)-shaped, so it PEAKS at 0.50 -- inside the $0.60
    ceiling, not at it. Taking the fee at the ceiling alone would understate
    the bound, which is the sort of quiet optimism an exposure cap exists to
    prevent.
    """
    cap = D(AUTHORIZED_ENVELOPE["max_acquisition_price"])
    q = D(AUTHORIZED_ENVELOPE["quantity"])
    worst, at = D("0"), None
    p = VENUE_PRICE_INCREMENT
    while p <= cap:
        f = fee_at(p, q)
        if f > worst:
            worst, at = f, p
        p += VENUE_PRICE_INCREMENT
    return {"worst_fee_usd": str(worst), "at_price": str(at),
            "searched": "every 0.01 price from 0.01 to the %s ceiling" % cap,
            "note": ("the raw fee is p*(1-p)-shaped and peaks at p=0.50, "
                     "INSIDE the ceiling rather than at it, so evaluating "
                     "it only at the ceiling would understate the bound. "
                     "`at_price` is the FIRST price on the grid that "
                     "attains the ceiling-rounded maximum, not the peak of "
                     "the raw fee.")}


def worst_case_per_trade() -> D:
    """Ceiling principal plus the worst admissible fee."""
    q = D(AUTHORIZED_ENVELOPE["quantity"])
    cap = D(AUTHORIZED_ENVELOPE["max_acquisition_price"])
    return (q * cap + D(worst_case_fee()["worst_fee_usd"])).quantize(
        D("0.0001"))


def max_principal() -> D:
    q = D(AUTHORIZED_ENVELOPE["quantity"])
    cap = D(AUTHORIZED_ENVELOPE["max_acquisition_price"])
    return (q * cap * AUTHORIZED_ENVELOPE["max_entry_attempts"]).quantize(
        D("0.0001"))


def max_fees() -> D:
    return (D(worst_case_fee()["worst_fee_usd"])
            * AUTHORIZED_ENVELOPE["max_entry_attempts"]).quantize(D("0.0001"))


def cohort_cap() -> D:
    return (max_principal() + max_fees()).quantize(D("0.0001"))


def exposure() -> Dict[str, Any]:
    return {
        "quantity_per_trade": AUTHORIZED_ENVELOPE["quantity"],
        "acquisition_ceiling_usd_per_share":
            AUTHORIZED_ENVELOPE["max_acquisition_price"],
        "max_attempts": AUTHORIZED_ENVELOPE["max_entry_attempts"],
        "max_principal_usd": str(max_principal()),
        "worst_case_fee_detail": worst_case_fee(),
        "max_fees_usd": str(max_fees()),
        "exact_cohort_cap_usd": str(cohort_cap()),
        "worst_case_per_trade_usd": str(worst_case_per_trade()),
        "basis": ("principal at the ceiling, plus the venue's reconciled "
                  "taker fee at its worst admissible price, rounded UP to "
                  "the cent"),
    }


# ----------------------------------------------------------------- the gates
def envelope_match() -> Dict[str, Any]:
    return {"envelope": dict(AUTHORIZED_ENVELOPE),
            "auth_id": UNDERDOG_AUTH_ID, "exposure": exposure()}


def auth_gate() -> Dict[str, Any]:
    live = arming.auth_id()
    return {
        "live_auth_id_from_env": live,
        "build_expected_auth_id": arming.EXPECTED_AUTH_ID,
        "taker_key_holder": payload.SEMANTICS_PROBE_AUTH_ID,
        "underdog_auth_id": UNDERDOG_AUTH_ID,
        "arming_matches": arming.EXPECTED_AUTH_ID == UNDERDOG_AUTH_ID,
        "taker_key_matches": (payload.SEMANTICS_PROBE_AUTH_ID
                              == UNDERDOG_AUTH_ID),
        "env_matches": live == UNDERDOG_AUTH_ID,
        "ci15m_submit_is_now_refused": (P.PROBE_AUTH_ID
                                        != arming.EXPECTED_AUTH_ID),
        "note": ("exactly one authorization holds the taker key. This grant "
                 "holding it closes both the BTC15m PERSIST4 path and the "
                 "spent CANARY-1 cohort."),
    }


def halted() -> Optional[Dict[str, Any]]:
    if not os.path.exists(HALT):
        return None
    try:
        return json.load(open(HALT, encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {"reason": "HALT_FILE_UNREADABLE"}


def halt(reason: str, detail: Any = None) -> Dict[str, Any]:
    os.makedirs(LEDGER_DIR, exist_ok=True)
    row = {"reason": reason, "detail": detail, "utc": _utc(),
           "auth_id": UNDERDOG_AUTH_ID, "label": LABEL}
    with open(HALT, "w", encoding="utf-8") as fh:
        json.dump(row, fh, indent=2, sort_keys=True, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    _append({"kind": "HALT", **row})
    return row


def _append(row: Dict[str, Any]) -> None:
    os.makedirs(LEDGER_DIR, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(row, utc=row.get("utc") or _utc()),
                            sort_keys=True, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def ledger_rows() -> List[Dict[str, Any]]:
    if not os.path.exists(LEDGER):
        return []
    out = []
    for line in open(LEDGER, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except Exception:                                      # noqa: BLE001
            continue
    return out


def cohort_state() -> Dict[str, Any]:
    rows = ledger_rows()
    slots = [r for r in rows if r.get("kind") == "SLOT"]
    spent = sum((_d(r.get("committed_usd")) or D("0")) for r in slots)
    return {
        "attempts_consumed": len(slots),
        "attempts_remaining": max(
            0, AUTHORIZED_ENVELOPE["max_entry_attempts"] - len(slots)),
        "expired": len(slots) >= AUTHORIZED_ENVELOPE["max_entry_attempts"],
        "committed_usd": str(spent),
        "committed_headroom_usd": str(cohort_cap() - spent),
        "windows_touched": sorted({r.get("market") for r in slots
                                   if r.get("market")}),
        "halted": halted(),
    }


# ----------------------------------------------------------- the frozen rule
def cumulative_vwap(levels: Any, size: D) -> Dict[str, Any]:
    """CUMULATIVE VWAP to fill `size`, as the envelope requires.

    A touch price is NOT accepted as a q1 price here even though at q=1 the
    two usually coincide: `usually` is not an invariant, and a 0.4-contract
    touch cannot fill one contract.
    """
    need, cost, got = size, D("0"), D("0")
    used: List[List[str]] = []
    for lvl in (levels or []):
        px, qty = _d(lvl[0]), _d(lvl[1])
        if px is None or qty is None:
            return {"fillable": False, "why": "unreadable ladder level"}
        take = min(need, qty)
        cost += px * take
        got += take
        used.append([str(px), str(take)])
        need -= take
        if need <= 0:
            break
    if got < size:
        return {"fillable": False, "available": str(got),
                "why": "the ladder cannot fill %s" % size}
    return {"fillable": True, "vwap": cost / size, "available": str(got),
            "levels_consumed": used}


def evaluate(symbol: str, *, elapsed_since_open_s: Optional[float],
             quote_age_s: Optional[float] = None) -> Dict[str, Any]:
    """THE FROZEN UNDERDOG RULE. Deterministic; executable ladder only.

    DOWN IS PRICED FROM THE YES BID LADDER, which is what buying NO
    consumes: this venue publishes ONE ladder per contract, so a NO
    acquisition at a given size costs 1 minus the YES bids that size
    consumes. It is derived per level, never from the touch alone.
    """
    # THE LEVELS COME FROM THE DEPTH PRODUCER ITSELF. book_from_venue
    # returns readable/bid/ask/sizes/snapshot_age_s/ladder -- and NO
    # `ladder_raw`. An earlier draft of this function read one, which would
    # have made both VWAPs unfillable on every poll and the cohort would
    # have refused every window while reporting success. That exact class of
    # bug already cost CANARY-1 a whole live run, so the levels are read
    # from metadata.contract_depth, whose shape is verified: bids/asks each
    # carry `levels` of {price, qty, venue_ts}.
    from src.gemini_execution import metadata
    bk = P.book_from_venue(symbol)
    dep = metadata.contract_depth(symbol)
    q = D(AUTHORIZED_ENVELOPE["quantity"])
    cap = D(AUTHORIZED_ENVELOPE["max_acquisition_price"])
    ladder_age = bk.get("snapshot_age_s")
    if quote_age_s is None:
        quote_age_s = ladder_age
    elif ladder_age is not None:
        quote_age_s = max(float(quote_age_s), float(ladder_age))

    yes_asks = ((dep.get("asks") or {}).get("levels")
                if dep.get("readable") else None)
    yes_bids = ((dep.get("bids") or {}).get("levels")
                if dep.get("readable") else None)
    # BOOK INTEGRITY IS DERIVED, NOT DEFAULTED. An earlier draft read a
    # `book_integrity` key off book_from_venue and defaulted it to "OK" when
    # absent -- and it is always absent, so an integrity requirement the
    # owner asked for would have passed unconditionally. It is now the
    # conjunction of facts the producers actually publish: the depth read
    # succeeded, the venue stamped it, and ExecutableBook did not find the
    # ladder crossed or locked.
    lad = bk.get("ladder")
    integrity = {
        "depth_readable": bool(dep.get("readable")),
        "venue_timestamped": dep.get("venue_ts") not in (None, ""),
        "not_crossed": not bool(getattr(lad, "crossed", False)),
        "both_sides_have_levels": bool(yes_asks and yes_bids),
    }
    integrity_ok = all(integrity.values())
    up = dn = {"fillable": False, "why": "no ladder levels published"}
    if yes_asks:
        up = cumulative_vwap([[lv["price"], lv["qty"]] for lv in yes_asks], q)
    if yes_bids:
        dn = cumulative_vwap(
            [[str(D("1") - D(str(lv["price"]))), lv["qty"]]
             for lv in yes_bids], q)

    checks: Dict[str, Any] = {
        "ladder_readable": bool(bk.get("readable")),
        "structurally_two_sided": bool(bk.get("bid") is not None
                                       and bk.get("ask") is not None),
        "book_integrity_ok": integrity_ok,
        "checkpoint_at_or_after_min": (
            elapsed_since_open_s is not None
            and elapsed_since_open_s
            >= AUTHORIZED_ENVELOPE["min_checkpoint_s_since_open"]),
        "quote_fresh": (quote_age_s is not None
                        and 0 <= float(quote_age_s)
                        <= AUTHORIZED_ENVELOPE["max_quote_age_s"]),
        "economic_two_sided_q1_cumulative": bool(up.get("fillable")
                                                 and dn.get("fillable")),
    }
    side = price = None
    if checks["economic_two_sided_q1_cumulative"]:
        if up["vwap"] < dn["vwap"]:
            side, price = "UP", up["vwap"]
        elif dn["vwap"] < up["vwap"]:
            side, price = "DOWN", dn["vwap"]
        else:
            # A TIE IS AMBIGUOUS AND FAILS CLOSED. There is no underdog when
            # both sides cost the same, and picking one would be a coin flip
            # the authorization does not describe.
            checks["underdog_is_unambiguous"] = False
        if side is not None:
            checks["underdog_is_unambiguous"] = True
            checks["within_acquisition_ceiling"] = price <= cap
            checks["price_on_increment"] = (
                price.quantize(VENUE_PRICE_INCREMENT) == price)
            checks["quantity_on_increment"] = (
                q % VENUE_QUANTITY_INCREMENT == 0)
            checks["quantity_at_or_above_venue_minimum"] = (
                q >= VENUE_QUANTITY_MINIMUM)
    else:
        for k in ("underdog_is_unambiguous", "within_acquisition_ceiling",
                  "price_on_increment", "quantity_on_increment",
                  "quantity_at_or_above_venue_minimum"):
            checks[k] = False

    failed = [k for k, v in checks.items() if not v]
    above_ceiling = bool(
        side is not None and price is not None and price > cap)
    return {
        "symbol": symbol, "utc": _utc(),
        "qualifies": not failed, "failed_checks": failed, "checks": checks,
        "side": side,
        "outcome": (None if side is None else SIDE_OUTCOME[side]),
        "requested_price": None if price is None else str(price),
        "requested_quantity": str(q),
        "up_q1_vwap": (str(up["vwap"]) if up.get("fillable") else None),
        "down_q1_vwap": (str(dn["vwap"]) if dn.get("fillable") else None),
        "up_levels_consumed": up.get("levels_consumed"),
        "down_levels_consumed": dn.get("levels_consumed"),
        "leader": (None if side is None
                   else ("DOWN" if side == "UP" else "UP")),
        "underdog": side,
        "combined_q1_cost": (
            str(up["vwap"] + dn["vwap"])
            if up.get("fillable") and dn.get("fillable") else None),
        "spread_touch": (
            str(D(str(bk["ask"])) - D(str(bk["bid"])))
            if bk.get("ask") is not None and bk.get("bid") is not None
            else None),
        "depth": {"up_available": up.get("available"),
                  "down_available": dn.get("available"),
                  "touch_bid_size": bk.get("bid_size"),
                  "touch_ask_size": bk.get("ask_size")},
        "elapsed_since_open_s": elapsed_since_open_s,
        "quote_age_s": quote_age_s,
        "ladder_snapshot_age_s": ladder_age,
        "above_ceiling_no_trade": above_ceiling,
        "projected_fee_usd": (None if price is None
                              else str(fee_at(price, q))),
        "projected_outlay_usd": (
            None if price is None
            else str((price * q + fee_at(price, q)).quantize(D("0.0001")))),
        "book_integrity_detail": integrity,
        "depth_source": dep.get("source") or "metadata.contract_depth",
        "depth_venue_ts": dep.get("venue_ts"),
        "book_raw": bk,
        "rule": ("first checkpoint >= T+15s with a readable, structurally "
                 "two-sided, integrity-OK ladder, fresh within 2.0s, both "
                 "sides filling q=1 CUMULATIVELY; buy the side with the "
                 "LOWER executable q1 VWAP at that captured price, if it is "
                 "at or below the 0.60 ceiling. A tie fails closed."),
        "labels": ("EXPLORATORY live execution. A side chosen by relative "
                   "price expresses no forecast and five q=1 fills validate "
                   "no strategy."),
    }


# ------------------------------------------------------------- the one write
async def attempt(symbol: str, decision: Dict[str, Any]) -> Dict[str, Any]:
    """Consume a slot DURABLY, then send exactly one capped marketable LIMIT.

    Refusals, in order, every one fail-closed:
      1. HALTED
      2. the envelope does not match this build
      3. either authorization constant does not name this grant
      4. the decision does not qualify under the frozen rule
      5. the cohort has expired at attempt 5
      6. this window already consumed an attempt
      7. the price is above the authorized ceiling
      8. the cohort outlay cap would be breached
      9. an order is already outstanding, or the account is unreadable
    """
    if halted():
        raise UnderdogRefused("the cohort is HALTED: %r" % halted())
    if (AUTHORIZED_ENVELOPE["max_entry_attempts"] != 5
            or AUTHORIZED_ENVELOPE["quantity"] != "1"
            or AUTHORIZED_ENVELOPE["max_acquisition_price"] != "0.60"):
        raise UnderdogRefused("the envelope does not match the authorization")
    g = auth_gate()
    for k in ("arming_matches", "taker_key_matches", "env_matches"):
        if not g[k]:
            raise UnderdogRefused("authorization gate %s is false: %s"
                                  % (k, g))
    if not decision.get("qualifies"):
        raise UnderdogRefused("the decision does not qualify: %r"
                              % decision.get("failed_checks"))
    st = cohort_state()
    if st["expired"]:
        raise UnderdogRefused("the cohort EXPIRED at attempt %d"
                              % AUTHORIZED_ENVELOPE["max_entry_attempts"])
    if symbol in st["windows_touched"]:
        raise UnderdogRefused("this window already consumed an attempt")
    px = D(str(decision["requested_price"]))
    q = D(AUTHORIZED_ENVELOPE["quantity"])
    cap = D(AUTHORIZED_ENVELOPE["max_acquisition_price"])
    if px > cap:
        raise UnderdogRefused("price %s is above the %s ceiling" % (px, cap))
    side = decision.get("side")
    if side not in SIDE_OUTCOME:
        raise UnderdogRefused("no unambiguous underdog side: %r" % side)
    commit = (q * px + fee_at(px, q)).quantize(D("0.0001"))
    if (D(st["committed_usd"]) + commit) > cohort_cap():
        raise UnderdogRefused(
            "the cohort cap would be breached: %s + %s > %s"
            % (st["committed_usd"], commit, cohort_cap()))
    out = await P.one_outstanding_order(symbol)
    if not out["ok"]:
        raise UnderdogRefused(out["why"])

    slot = {"kind": "SLOT", "slot": st["attempts_consumed"] + 1,
            "market": symbol, "side": side,
            "outcome": SIDE_OUTCOME[side],
            "committed_usd": str(commit),
            "requested_price": str(px), "requested_quantity": str(q),
            "auth_id": UNDERDOG_AUTH_ID, "decision": decision,
            "probe_contaminated": True}
    _append(slot)                                  # fsynced BEFORE the venue

    t0 = time.monotonic_ns()
    try:
        res = await transport.place(
            symbol=symbol, side=payload.spec.SIDE_BUY,
            outcome=SIDE_OUTCOME[side],
            price=str(px), quantity=str(q), allow_taker=True)
    except Exception as exc:                                   # noqa: BLE001
        lat = (time.monotonic_ns() - t0) / 1e6
        row = {"kind": "SUBMIT_AMBIGUOUS", "slot": slot["slot"],
               "market": symbol, "side": side, "error": str(exc)[:400],
               "latency_ms": round(lat, 3),
               "why": ("the send outcome is unknown. The slot is SPENT and "
                       "is never retried.")}
        _append(row)
        halt("AMBIGUOUS_SUBMISSION", row)
        return row
    lat = (time.monotonic_ns() - t0) / 1e6
    cls = P.classify_ack(res.get("body") or {})
    row = {"kind": "SUBMITTED", "slot": slot["slot"], "market": symbol,
           "side": side, "outcome": SIDE_OUTCOME[side],
           "latency_ms": round(lat, 3), "venue": res,
           "ack_classification": cls,
           "requested_price": str(px), "requested_quantity": str(q),
           "committed_usd": str(commit),
           "labels": ("SUBMITTED is not FILLED. Hold to settlement; no "
                      "SELL; no averaging; cancellation exists only as the "
                      "deadman unwind.")}
    _append(row)
    return row


# ------------------------------------------------------------- attribution
def authorized_intents(market: str = "") -> List[Dict[str, Any]]:
    """THIS cohort's durable authorizations, from ITS OWN ledger."""
    out: List[Dict[str, Any]] = []
    rows = ledger_rows()
    for r in rows:
        if r.get("kind") != "SLOT":
            continue
        if market and r.get("market") != market:
            continue
        body = {}
        for s_ in rows:
            if (s_.get("kind") == "SUBMITTED"
                    and s_.get("slot") == r.get("slot")):
                body = ((s_.get("venue") or {}).get("body") or {})
                break
        oid = body.get("orderId")
        out.append({"slot": r.get("slot"), "market": r.get("market"),
                    "utc": r.get("utc"), "side": r.get("side"),
                    "price": r.get("requested_price"),
                    "quantity": r.get("requested_quantity"),
                    "outcome": r.get("outcome"),
                    "order_id": (None if oid is None else str(oid)),
                    "client_order_id": body.get("clientOrderId")})
    return out


def attribute_fills(market: str,
                    fills: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """EXACT order_id, as corrected in cohort 1. The invariant is not
    weakened: a fill carrying an order_id MUST match one the venue minted
    for us, the envelope binds on both routes, the envelope route survives
    only for a fill carrying no order_id at all, and one authorization
    absorbs at most one fill."""
    intents = authorized_intents(market)
    used: set = set()
    attributed: List[Dict[str, Any]] = []
    foreign: List[Mapping[str, Any]] = []
    for f in fills:
        px, qty = _d(f.get("price")), _d(f.get("qty"))
        leg = str(f.get("leg") or "").strip().lower()
        f_oid = f.get("order_id")
        f_oid = None if f_oid is None else str(f_oid)
        hit, route = None, None
        for n, i in enumerate(intents):
            if n in used:
                continue
            lim, aq = _d(i.get("price")), _d(i.get("quantity"))
            if lim is None or aq is None or px is None or qty is None:
                continue
            if px > lim or qty > aq:
                continue
            if f_oid is not None:
                if i.get("order_id") == f_oid:
                    hit, route = n, "ORDER_ID"
                    break
                continue
            if str(i.get("outcome") or "").strip().lower() == leg:
                hit, route = n, "INTENT_ENVELOPE"
                break
        if hit is None:
            foreign.append(f)
            continue
        used.add(hit)
        attributed.append(dict(f, _slot=intents[hit]["slot"],
                               _attribution_route=route))
    if foreign:
        halt("UNATTRIBUTABLE_FILL",
             {"market": market, "n_foreign": len(foreign),
              "n_intents": len(intents),
              "foreign": [{k: f.get(k) for k in
                           ("tid", "order_id", "leg", "price", "qty")}
                          for f in foreign[:4]]})
        return {"ok": False, "attributed": attributed, "foreign": foreign,
                "why": "unattributable venue fill(s)"}
    return {"ok": True, "attributed": attributed, "foreign": [],
            "n_intents": len(intents), "n_attributed": len(attributed),
            "routes": sorted({r["_attribution_route"]
                              for r in attributed})}
