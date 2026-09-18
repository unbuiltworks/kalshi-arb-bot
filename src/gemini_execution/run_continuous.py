"""Invoker for the reviewed continuous driver. Thin by design.

THIS FILE HOLDS NO SAFETY LOGIC AND MUST NOT ACQUIRE ANY. Every bound --
the 12-slot mutation budget, the $0.15 exposure ceiling, the 0.04 residual and
imbalance caps, the 0.10 per-leg caps, the end-of-window floors, the terminal
one-window binding, the ambiguity handling -- lives inside
`ws_continuous.Driver` and is covered by the authorized `executable_sha`. What
happens here is only: pick the eligible contract, read authoritative venue
state, hand both to the Driver, and write down what it did.

THE ONE THING THIS FILE DECIDES THAT THE DRIVER CANNOT CHECK is WHICH SYMBOL
and WHAT ITS CLOCK SAYS. A wrong symbol or a wrong time-to-expiry would make
the end-of-window floors fire at the wrong moment, so `tte_of` is derived from
the venue's own published expiry on every call and returns a NEGATIVE number
rather than a guess when the expiry cannot be read -- which drives the Driver
straight to WITHDRAWN, the fail-closed direction.

It is NOT covered by the authorized executable_sha, which covers the driver.
That is stated plainly in the run report rather than left to be discovered.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import metadata as MD
from src.gemini_execution import ws_continuous as WC

LABEL = "GEMINI_L5_CONTINUOUS_RUN_V1"

#: a window is worth entering only if the normal surface can still act, i.e.
#: at least one full maker-rest cycle above the OPEN/EXTEND floor
MIN_ENTRY_TTE_S = CP.NO_NEW_OPEN_EXTEND_TTE_S + CP.STALE_ORDER_S   # 90.0
MAX_ENTRY_TTE_S = 300.0


def _expiry_epoch(symbol: str) -> Optional[float]:
    t = MD.terms_for(symbol) or {}
    raw = t.get("expiry") or t.get("expiryDate")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(
            str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def tte_of(symbol: str):
    """A live clock read from the venue's own expiry, never a local timer.

    Returns -1.0 when the expiry cannot be read. That is deliberate: the
    Driver treats a TTE under the withdraw floor as WITHDRAWN, so an
    unreadable clock fails closed instead of quoting into an unknown one.
    """
    def _now() -> float:
        exp = _expiry_epoch(symbol)
        if exp is None:
            return -1.0
        return exp - time.time()
    return _now


def eligible_symbol(now: Optional[float] = None) -> Dict[str, Any]:
    """The ONE BTC5m contract this window will be bound to, or a refusal."""
    t = now if now is not None else time.time()
    tried: List[Dict[str, Any]] = []
    ticker = MD.current_btc5m_ticker(t)
    for step in range(4):
        tk = ticker if step == 0 else MD.sibling_ticker(ticker, 5 * step)
        if not tk:
            continue
        sym = "GEMI-%s-UP" % tk
        terms = MD.terms_for(sym) or {}
        exp = _expiry_epoch(sym)
        tte = None if exp is None else exp - t
        row = {"symbol": sym, "tte_s": tte,
               "contract_status": terms.get("contract_status")
               or terms.get("status"),
               "market_state": terms.get("market_state")
               or terms.get("marketState")}
        if (tte is not None and MIN_ENTRY_TTE_S <= tte <= MAX_ENTRY_TTE_S
                and row["contract_status"] == "active"
                and row["market_state"] == "open"):
            return {"eligible": True, "chosen": row, "considered": tried + [row]}
        row["why"] = ("tte %s outside [%s, %s]" % (tte, MIN_ENTRY_TTE_S,
                                                   MAX_ENTRY_TTE_S)
                      if tte is None or not (MIN_ENTRY_TTE_S <= tte
                                             <= MAX_ENTRY_TTE_S)
                      else "not active/open")
        tried.append(row)
    return {"eligible": False, "considered": tried,
            "why": "no BTC5m contract is inside the entry band right now"}


def _legs_from_orders(orders: Sequence[Mapping[str, Any]]
                      ) -> List[Dict[str, Any]]:
    """Filled venue orders -> the fill shape `CP.recover` consumes."""
    out = []
    for o in orders:
        q = o.get("filledQuantity") or o.get("filled_quantity")
        if q in (None, "", "0"):
            continue
        out.append({"leg": ("YES" if str(o.get("outcome", "")).lower() == "yes"
                            else "NO"),
                    "qty": q, "price": o.get("price")})
    return out


async def authoritative_state(symbol: str) -> Dict[str, Any]:
    """Fills, open orders and the order count for this symbol.

    Each value is None when its read did not succeed. None is UNREADABLE and
    stops the probe inside the Driver; it is never turned into an empty list
    here, because that is the substitution that makes a restart look flat.
    """
    from src.gemini_execution import transport as T

    out: Dict[str, Any] = {"symbol": symbol}
    try:
        hist = await MD.order_history(symbol=symbol, statuses=("filled",),
                                      limit=100)
        rows = None
        if hist.get("readable") is not False:
            per = hist.get("per_status") or []
            rows = []
            for p in per:
                rows.extend(p.get("orders") or [])
        out["fills"] = None if rows is None else _legs_from_orders(rows)
        out["orders_seen"] = None if rows is None else rows
        out["order_history_raw"] = {k: hist.get(k) for k in
                                    ("readable", "verdict", "total_rows")}
    except Exception as exc:                                   # noqa: BLE001
        out["fills"] = None
        out["orders_seen"] = None
        out["order_history_error"] = "%s: %s" % (type(exc).__name__, exc)

    try:
        act = await T.active_orders(symbol)
        out["open_orders"] = (T._orders_of(act) if act.get("ok") else None)
        out["active_raw"] = {k: act.get(k) for k in
                             ("ok", "status", "request_shape")}
    except Exception as exc:                                   # noqa: BLE001
        out["open_orders"] = None
        out["active_error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


async def main_async(auth_id: str, *, state_dir: str, ledger_path: str,
                     out_path: str) -> Dict[str, Any]:
    from src.gemini_execution.ws_session import GeminiMakerSession

    rep: Dict[str, Any] = {
        "label": LABEL, "started_utc": datetime.now(timezone.utc).isoformat(),
        "authorization_id": auth_id,
        "driver_executable_sha": WC.executable_sha(),
        "driver_envelope_sha": WC.envelope_sha(),
        "invoker_note": ("this invoker is NOT covered by the authorized "
                         "executable_sha, which covers the driver. It holds "
                         "no bound of its own; every limit is enforced inside "
                         "ws_continuous.Driver."),
    }

    sel = eligible_symbol()
    rep["symbol_selection"] = sel
    if not sel.get("eligible"):
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = sel.get("why")
        return rep
    symbol = sel["chosen"]["symbol"]

    auth = WC.authorization_state()
    rep["authorization"] = auth
    if not auth["authorized"]:
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = "not authorized: %s" % "; ".join(auth["why_not"])
        return rep

    # ---------------------------------------------------------------- LATCH
    # ONE AUTHORIZATION, ONE WINDOW, AND THE LATCH IS WHAT MAKES THAT DURABLE.
    # The driver's MutationLedger bounds mutations WITHIN a window; it cannot
    # stop a SECOND window being run under the same authorization on a fresh
    # runner with a clean disk. This does, and it is consumed BEFORE the first
    # mutation -- before the session is even connected -- because a lost order
    # is preferred to a double order: only one of them is recoverable.
    #
    # It is consumed HERE rather than inside the driver so that the reviewed
    # executable stays byte-for-byte what was authorized. The guarantee is the
    # same: nothing mutates until the latch is durably spent.
    from src.gemini_execution import latch
    from src.gemini_execution.run_canary import durable_check, make_durable

    intent = {
        "intent_id": "cont-%s" % datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"),
        "symbol": symbol, "side": "buy", "outcome": "both",
        "price": None, "amount": str(CP.ACTION_SIZE),
        "max_orders": CP.MAX_ACTIONS, "max_residual": str(CP.MAX_RESIDUAL),
        "maker_only": True, "transport": "WEBSOCKET",
        "executable_sha": WC.executable_sha(),
        "envelope_sha": WC.envelope_sha(),
    }
    try:
        rec0 = latch.consume(auth_id, intent, state_dir,
                             durable_check=durable_check)
        make_durable(auth_id, rec0)
        rep["latch"] = {"consumed": True, "intent_id": rec0.get("intent_id"),
                        "state": rec0.get("state"),
                        "when": "BEFORE the session connected"}
    except latch.AlreadyConsumed as exc:
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = ("authorization already consumed: %s. This is "
                      "reconciliation work, never a re-run." % exc)
        rep["latch"] = {"consumed": False, "already": True}
        return rep
    except Exception as exc:                                   # noqa: BLE001
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = ("the latch could not be made durable (%s: %s); nothing "
                      "is sent, because a mutation whose authorization is not "
                      "durably spent is the one state we cannot reconcile"
                      % (type(exc).__name__, exc))
        rep["latch"] = {"consumed": False, "durable": False}
        return rep

    st = await authoritative_state(symbol)
    rep["authoritative_state"] = {
        "fills": st.get("fills"), "open_orders": st.get("open_orders"),
        "order_history": st.get("order_history_raw"),
        "active": st.get("active_raw")}

    driver = WC.Driver(auth_id, symbol, ledger_path=ledger_path)
    session = GeminiMakerSession(id_prefix="l5cp")
    try:
        await session.connect()
        rep["session"] = "connected"
        out = await driver.run_window(
            session, tte_of=tte_of(symbol),
            venue_fills=st.get("fills"),
            venue_open=st.get("open_orders"),
            venue_orders=st.get("orders_seen"),
            interval_s=5.0)
        rep["window"] = out
    except Exception as exc:                                   # noqa: BLE001
        rep["window_error"] = "%s: %s" % (type(exc).__name__, exc)
        rep["state"] = driver.state
        rep["why"] = driver.why
    finally:
        try:
            await session.close()
        except Exception:                                      # noqa: BLE001
            pass

    # authoritative AFTER-state, for reconciliation
    rep["after"] = await authoritative_state(symbol)
    rep["resolution"] = MD.resolution_for(symbol)
    rep["finished_utc"] = datetime.now(timezone.utc).isoformat()
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    return rep


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth-id", required=True)
    ap.add_argument("--state-dir", default="analysis/gemini/canary_latch")
    ap.add_argument("--ledger", default="analysis/gemini/continuous_ledger.jsonl")
    ap.add_argument("--out", default="analysis/gemini/continuous_run.json")
    a = ap.parse_args(argv)
    rep = asyncio.run(main_async(a.auth_id, state_dir=a.state_dir,
                                 ledger_path=a.ledger, out_path=a.out))
    print("GEMINI_CONTINUOUS " + json.dumps(
        {k: rep.get(k) for k in ("state", "why", "symbol_selection",
                                 "driver_executable_sha")}, default=str),
          flush=True)
    w = rep.get("window") or {}
    print("GEMINI_CONTINUOUS_WINDOW " + json.dumps(
        {k: w.get(k) for k in ("state", "why", "cycles", "budget_consumed",
                               "budget_left", "inventory", "turns")},
        default=str)[:6000], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
