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

from src.gemini_execution import bundle as BUNDLE
from src.gemini_execution import continuous_preflight as PF
from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import metadata as MD
from src.gemini_execution import spec
from src.gemini_execution import ws_continuous as WC

LABEL = "GEMINI_L5_CONTINUOUS_RUN_V1"

#: reached the boundary, refused only by the authorization gate. Defined
#: HERE and not in ws_continuous because adding a name there would change
#: the authorized executable_sha, and the authorization names that hash.
READY_BUT_UNAUTHORIZED = "READY_TO_MUTATE_BUT_UNAUTHORIZED"

#: a window is worth entering only if the normal surface can still act, i.e.
#: at least one full maker-rest cycle above the OPEN/EXTEND floor
MIN_ENTRY_TTE_S = CP.NO_NEW_OPEN_EXTEND_TTE_S + CP.STALE_ORDER_S   # 90.0
MAX_ENTRY_TTE_S = 300.0


# --------------------------------------------------------------- WIRE CAPTURE
# The successful `order.place` acknowledgement shape has never been observed.
# CONTINUOUS-2 placed two accepted orders, but the driver parsed them with code
# recovered from the canary and the shape went into the record as INFERRED. The
# cost of inferring was already paid once: the first live cancel sent
# `clientOrderId` and the venue rejected it -1013, because the id is nested
# under `result`. This captures the answer instead of guessing it again.
#
# WHY IT IS HERE AND NOT IN THE DRIVER. `ws_continuous.Driver.place` already
# returns the ack, but `run_window` discards that return value. Capturing it
# there would mean editing the driver, and the owner authorization names the
# driver's exact `executable_sha`. Evidence capture is not worth invalidating
# the hash that makes the authorization meaningful, so the capture is taken at
# the session seam THIS FILE owns and the reviewed executable stays byte-for-
# byte what was authorized.

#: Keys whose VALUE is never written to an artifact. The key and the value's
#: TYPE survive, because the point of this capture is the venue's response
#: SHAPE -- redacting the shape would defeat the whole exercise.
_SENSITIVE = ("apikey", "secret", "signature", "nonce", "password", "token",
              "authorization", "cookie", "session", "account", "acct",
              "payload")
_MAX_STR = 512
_MAX_ITEMS = 200
#: recursion caps for the two walkers. Deliberately NOT envelope-shaped
#: numbers: a loose 12 here reads as MAX_ACTIONS to the next person, and
#: the invoker restating an envelope bound is the drift this file forbids.
_MAX_DEPTH = 16
_MAX_SHAPE_DEPTH = 9
_MAX_SEQ = 5


def _is_sensitive(key: Any) -> bool:
    k = str(key).lower().replace("-", "").replace("_", "")
    return any(s.replace("_", "") in k for s in _SENSITIVE)


def sanitize(obj: Any, _depth: int = 0) -> Any:
    """Redact credential-shaped VALUES; keep the structure around them."""
    if _depth > _MAX_DEPTH:
        return "<truncated:depth>"
    if isinstance(obj, Mapping):
        out: Dict[str, Any] = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= _MAX_ITEMS:
                out["<truncated>"] = "%d more keys" % (len(obj) - i)
                break
            out[str(k)] = ("<redacted:%s>" % type(v).__name__
                           if _is_sensitive(k) else sanitize(v, _depth + 1))
        return out
    if isinstance(obj, (list, tuple)):
        return [sanitize(v, _depth + 1) for v in list(obj)[:_MAX_ITEMS]]
    if isinstance(obj, str):
        return obj if len(obj) <= _MAX_STR else obj[:_MAX_STR] + "<truncated>"
    if isinstance(obj, (bool, int, float)) or obj is None:
        return obj
    return str(obj)[:_MAX_STR]


def shape_of(obj: Any, _depth: int = 0) -> Any:
    """Types only. `orderId` arriving as int vs str is the kind of difference
    that produced a -1013, so the type is recorded next to the value."""
    if _depth > _MAX_SHAPE_DEPTH:
        return "..."
    if isinstance(obj, Mapping):
        return {str(k): shape_of(v, _depth + 1)
                for k, v in list(obj.items())[:_MAX_ITEMS]}
    if isinstance(obj, (list, tuple)):
        return [shape_of(v, _depth + 1) for v in list(obj)[:_MAX_SEQ]]
    return type(obj).__name__


def order_id_paths(obj: Any, field: Optional[str] = None) -> List[Dict[str, Any]]:
    """WHERE the venue actually put the order id, as a dotted path.

    This is the exact question that cost a live cancel: the driver read
    `ack[orderId]` while the venue nests it under `ack[result][orderId]`.
    """
    f = field or spec.ORDER_ID_FIELD
    found: List[Dict[str, Any]] = []

    def walk(o: Any, path: str) -> None:
        if isinstance(o, Mapping):
            for k, v in o.items():
                pth = "%s.%s" % (path, k) if path else str(k)
                if str(k) == f:
                    found.append({"path": pth, "value": v,
                                  "type": type(v).__name__})
                walk(v, pth)
        elif isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                walk(v, "%s[%d]" % (path, i))

    walk(obj, "")
    return found


class RecordingSession:
    """A TRANSPARENT observer around the real session. It adds no behaviour.

    Every safety property still lives where it lived: the arm gate runs inside
    the real session's `request`, and every bound inside the Driver. This only
    writes down what crossed the wire.

    IT MUST NOT CHANGE EXECUTION, so: `request` returns the venue's own object
    unchanged, every exception propagates untouched (the driver's
    UNKNOWN/RECONCILE path depends on seeing them), and a failure to RECORD is
    noted in the row rather than raised.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.wire: List[Dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        if name == "_inner":                       # never recurse pre-__init__
            raise AttributeError(name)
        return getattr(self._inner, name)

    def _record(self, method: str, params: Any, ack: Any, t0: float,
                error: Optional[str] = None) -> None:
        row: Dict[str, Any] = {
            "utc": datetime.now(timezone.utc).isoformat(),
            "method": method, "elapsed_s": round(time.time() - t0, 4)}
        try:
            row["params"] = sanitize(params)
            row["ack"] = None if ack is None else sanitize(ack)
            row["ack_types"] = None if ack is None else shape_of(ack)
            if error is not None:
                row["error"] = error
        except Exception as exc:                               # noqa: BLE001
            row["record_error"] = "%s: %s" % (type(exc).__name__, exc)
        self.wire.append(row)

    async def request(self, method: str, params: Any, **kw: Any) -> Any:
        t0 = time.time()
        try:
            ack = await self._inner.request(method, params, **kw)
        except BaseException as exc:                           # noqa: BLE001
            self._record(method, params, None, t0,
                         error="%s: %s" % (type(exc).__name__, exc))
            raise
        self._record(method, params, ack, t0)
        return ack


def observed_place_acks(wire: Sequence[Mapping[str, Any]]
                        ) -> List[Dict[str, Any]]:
    """The `order.place` acknowledgements, accepted or not, as OBSERVED."""
    out: List[Dict[str, Any]] = []
    for row in wire:
        if row.get("method") != spec.WS_PLACE_METHOD:
            continue
        ack = row.get("ack")
        status = ack.get("status") if isinstance(ack, Mapping) else None
        out.append({
            "utc": row.get("utc"),
            "status": status,
            "accepted": status == spec.WS_ACK_OK,
            "client_order_id": (row.get("params") or {}).get(
                "clientOrderId") if isinstance(row.get("params"), Mapping)
            else None,
            "order_id_paths": order_id_paths(ack) if ack is not None else [],
            "ack": ack,
            "ack_types": row.get("ack_types"),
            "error": row.get("error"),
            "evidence": ("OBSERVED: this is the venue's own reply, recorded at "
                         "the socket seam" if ack is not None else
                         "NO ACK: the request did not return a reply"),
        })
    return out


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
        # include_rows=True IS LOAD-BEARING. Without it order_history returns
        # counts and samples but never the row list, so this extraction read a
        # key that does not exist and produced [] on every call -- fills always
        # empty, orders_seen always empty, and therefore "a restart never
        # assumes flat" quietly untrue and the venue half of
        # reconstruct_budget vacuous. Found 2026-09-18 while reconciling
        # CONTINUOUS-2 slot 2.
        hist = await MD.order_history(symbol=symbol, statuses=("filled",),
                                      limit=100, include_rows=True)
        rows = None
        if hist.get("readable") is not False:
            per = hist.get("per_status") or []
            rows = []
            extractable = False
            for p in per:
                if isinstance(p.get("orders"), list):
                    extractable = True
                    rows.extend(p["orders"])
            # readable BUT not extractable is UNREADABLE, never empty.
            if not extractable and (hist.get("total_rows") or 0) > 0:
                rows = None
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
                     out_path: str,
                     wire_path: Optional[str] = None) -> Dict[str, Any]:
    from src.gemini_execution.ws_session import GeminiMakerSession

    rep: Dict[str, Any] = {
        "label": LABEL, "started_utc": datetime.now(timezone.utc).isoformat(),
        "authorization_id": auth_id,
        "driver_executable_sha": WC.executable_sha(),
        "driver_envelope_sha": WC.envelope_sha(),
        "bundle_sha": BUNDLE.bundle_sha(),
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

    # ------------------------------------------------- PREFLIGHT, THEN LATCH
    # THE READS ARE DONE HERE AND PASSED IN AS VALUES. MD.terms_status is a
    # COROUTINE FUNCTION; the driver's preflight is synchronous and cannot
    # await it, so handing it the function meant handing it a coroutine whose
    # .get() raises -- which read as "terms unreadable", which is a
    # DISCREPANCY, which failed the probe closed on every run. It cost a live
    # authorization to find, because no test had ever passed the real reader.
    ts_value: Dict[str, Any]
    try:
        ts_value = await MD.terms_status() or {}
    except Exception as exc:                                   # noqa: BLE001
        ts_value = {}
        rep["terms_read_error"] = "%s: %s" % (type(exc).__name__, exc)
    readers = {"terms_for": MD.terms_for,
               "terms_status": (lambda: ts_value),
               "outcome_book": MD.outcome_book}
    rep["terms_status"] = ts_value

    # THE PREFLIGHT RUNS BEFORE THE LATCH IS SPENT. A preflight is a READ, not
    # a mutation, so "consume the latch before the first mutation" does not
    # mean "before the first read" -- and consuming it first meant a
    # fail-closed preflight burned the one authorization without an order ever
    # being attempted. That is exactly what happened on 2026-09-18T13:33Z.
    pf = PF.preflight_now(symbol, readers=readers)
    agrees, bad = PF.check_frozen(pf)
    rep["preflight"] = {"agrees": agrees, "discrepancies": bad,
                        "checked_before_latch": True}
    if not agrees:
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = ("fresh preflight differs from the frozen assumptions: %s"
                      % json.dumps(bad, default=str))
        rep["latch"] = {"consumed": False,
                        "why": "the preflight refused BEFORE the latch was "
                               "spent, so the authorization is untouched"}
        return rep

    # ----------------------------------------------------- AUTHORIZATION
    # THE ORDER IS FIXED AND IT MATTERS:
    #   read-only preflight -> READY -> authorization -> durable latch ->
    #   ledger intent/fsync -> socket mutation
    # Authorization is validated AFTER the preflight because a preflight is a
    # read and must be free, and BEFORE the latch because spending an
    # authorization we were never going to be able to use is pure loss.
    auth = WC.authorization_state()
    rep["authorization"] = auth
    if not auth["authorized"]:
        rep["state"] = READY_BUT_UNAUTHORIZED
        rep["why"] = "not authorized: %s" % "; ".join(auth["why_not"])
        rep["latch"] = {"consumed": False,
                        "why": "authorization was refused BEFORE the latch "
                               "was spent; the authorization is untouched"}
        return rep

    rep["bundle"] = BUNDLE.state()
    if not rep["bundle"]["authorized"]:
        rep["state"] = READY_BUT_UNAUTHORIZED
        rep["why"] = ("bundle not authorized: %s"
                      % "; ".join(rep["bundle"]["why_not"]))
        rep["latch"] = {"consumed": False,
                        "why": "the bundle authorization was refused BEFORE "
                               "the latch was spent; the authorization is "
                               "untouched"}
        return rep

    # ------------------------------------- AUTHORITATIVE READ, THEN RECOVERY
    # BOTH BEFORE THE LATCH, AND THAT ORDERING IS THE WHOLE POINT OF THIS
    # BLOCK. CONTINUOUS-3 reached this line with every gate open, spent its
    # authorization, connected, and then refused inside run_window because a
    # CANCEL from CONTINUOUS-2 was still recorded UNKNOWN in the durable
    # ledger. Nothing was ever sent. A refusal that is DETERMINISTIC and
    # READ-ONLY must not cost an authorization, and recovery is exactly that:
    # it reads the ledger and authoritative venue state and cannot mutate
    # either. The preflight was moved here after CONTINUOUS-1 was lost the
    # same way; this is the same lesson one step further down.
    st = await authoritative_state(symbol)
    rep["authoritative_state"] = {
        "fills": st.get("fills"), "open_orders": st.get("open_orders"),
        "order_history": st.get("order_history_raw"),
        "active": st.get("active_raw")}

    # A THROWAWAY Driver. recover() writes nothing -- not the ledger, not the
    # venue -- it only populates the instance it is called on, so a separate
    # instance can answer "would recovery succeed?" with no side effect. The
    # window below builds its own Driver and recovers again authoritatively;
    # this one exists solely to refuse before the latch.
    probe = WC.Driver(auth_id, symbol, ledger_path=ledger_path)
    pre = probe.recover(venue_fills=st.get("fills"),
                        venue_open=st.get("open_orders"),
                        venue_orders=st.get("orders_seen"))
    rep["pre_latch_recovery"] = {"checked_before_latch": True, **pre}
    if probe.state in WC.TERMINAL or not pre.get("recovered"):
        rep["state"] = probe.state
        rep["why"] = (probe.why or pre.get("why")
                      or "recovery refused before the latch")
        rep["latch"] = {"consumed": False,
                        "why": "recovery refused BEFORE the latch was spent, "
                               "so the authorization is untouched. A known "
                               "stale ledger row never costs an "
                               "authorization again."}
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

    driver = WC.Driver(auth_id, symbol, ledger_path=ledger_path)
    # the observer wraps the real session; the arm gate and every bound
    # still run inside the objects it delegates to.
    session = RecordingSession(GeminiMakerSession(id_prefix="l5cp"))
    try:
        await session.connect()
        rep["session"] = "connected"
        out = await driver.run_window(
            session, tte_of=tte_of(symbol),
            venue_fills=st.get("fills"),
            venue_open=st.get("open_orders"),
            venue_orders=st.get("orders_seen"),
            readers=readers,
            interval_s=5.0)
        rep["window"] = out
    except Exception as exc:                                   # noqa: BLE001
        rep["window_error"] = "%s: %s" % (type(exc).__name__, exc)
        rep["state"] = driver.state
        rep["why"] = driver.why
    finally:
        # BEFORE close(), and in `finally`, because the whole point is to
        # still have the ack when the window ends badly.
        rep["wire"] = session.wire
        rep["place_acks_observed"] = observed_place_acks(session.wire)
        rep["place_ack_note"] = (
            "OBSERVED, not inferred: each ack below is the venue's own "
            "reply recorded at the socket seam, with credential-shaped "
            "values redacted and key names and types preserved. An empty "
            "list means no order.place was ever sent.")
        if wire_path:
            try:
                with open(wire_path, "w", encoding="utf-8") as fh:
                    json.dump({"label": LABEL,
                               "authorization_id": auth_id,
                               "driver_executable_sha": WC.executable_sha(),
                               "wire": session.wire,
                               "place_acks_observed":
                                   rep["place_acks_observed"]},
                              fh, indent=2, default=str)
            except Exception as exc:                           # noqa: BLE001
                rep["wire_write_error"] = "%s: %s" % (
                    type(exc).__name__, exc)
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
    ap.add_argument("--wire", default="analysis/gemini/continuous_wire.json")
    a = ap.parse_args(argv)
    rep = asyncio.run(main_async(a.auth_id, state_dir=a.state_dir,
                                 ledger_path=a.ledger, out_path=a.out,
                                 wire_path=a.wire))
    print("GEMINI_CONTINUOUS " + json.dumps(
        {k: rep.get(k) for k in ("state", "why", "symbol_selection",
                                 "driver_executable_sha")}, default=str),
          flush=True)
    w = rep.get("window") or {}
    print("GEMINI_CONTINUOUS_PLACE_ACKS " + json.dumps(
        rep.get("place_acks_observed") or [], default=str)[:6000],
          flush=True)
    print("GEMINI_CONTINUOUS_WINDOW " + json.dumps(
        {k: w.get(k) for k in ("state", "why", "cycles", "budget_consumed",
                               "budget_left", "inventory", "turns")},
        default=str)[:6000], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
