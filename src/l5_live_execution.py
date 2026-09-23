"""The execution path for the faithful L5 engine.

    L5_NATIVE -> PMUS_TRANSLATION -> SAFETY_OVERLAY -> ORDER -> FILL
              -> INVENTORY -> SETTLEMENT -> REALIZED

Every stage is recorded on ONE provenance record per L5 action, so any dollar
can be walked back to the L5 trade that caused it and to the layer that
allowed or refused it. A stage that did not happen is recorded as not having
happened; nothing is inferred backwards.

=== IT CANNOT TRANSMIT ===

There is no venue client, no credential and no HTTP call in this module. It
emits ORDER INTENTS and hands them to a transport that the caller injects.
The default transport is `NullTransport`, which refuses every submission and
says so. A real transport must be passed in explicitly by a caller that holds
an owner authorization; nothing here can construct one, so an accidental
import can place nothing.

=== WHAT PROTECTS THE ACCOUNT, AND WHERE IT LIVES ===

Hard safety is OUTSIDE L5_NATIVE and always was. `SafetyLimits` (max dollars
per market, max directional residual, max total exposure, max concurrent
markets, daily realized-loss halt) can refuse an ORDER. It may never rewrite
the behavioural decision, and a refusal is recorded against the
SAFETY_OVERLAY layer so it can never be misread later as "L5 would not have
done this" or "PMUS could not have done this".

What the old q1/maker-only approximation disliked is NOT suppressed here:
temporary matched cost above $1, repeated same-side accumulation, opposite
acquisition, continued recycling and zero-crossing all pass through. Only the
hard limits above can stop an order, and each states which limit it was.

=== IDEMPOTENCY AND RECOVERY ===

A client order id is DERIVED, not generated: it is a hash of the L5 action
key plus the attempt number, so replaying the same action after a crash
produces the same id and the venue rejects the duplicate rather than doubling
the position. Inventory is persisted on every transition, and on restart the
engine reconciles against VENUE state rather than trusting its own file --
the venue is authoritative, our record is a cache.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_fees as fees
from src import l5_faithful_translation as FT

#: the pipeline, in order. A record names the furthest stage it reached.
STAGES = ("L5_NATIVE", "PMUS_TRANSLATION", "SAFETY_OVERLAY", "ORDER",
          "FILL", "INVENTORY", "SETTLEMENT", "REALIZED")

#: an action older than this at decision time is refused: acting on a stale
#: observation is how a replay after a restart turns into a wrong-price order
STALE_ACTION_MAX_S = 30.0


class NotArmed(RuntimeError):
    """Raised on any attempt to transmit without an explicit authorization."""


class NullTransport:
    """The default. Refuses everything and holds no credential."""

    armed = False
    name = "NULL"

    def submit(self, intent: Mapping[str, Any]) -> Dict[str, Any]:
        raise NotArmed(
            "no authorized transport is installed; this engine cannot "
            "transmit an order. Intent was: %s" % json.dumps(
                {k: str(v) for k, v in intent.items()}, sort_keys=True))

    def cancel(self, client_order_id: str) -> Dict[str, Any]:
        raise NotArmed("no authorized transport is installed")

    def open_orders(self) -> List[dict]:
        return []

    def positions(self) -> List[dict]:
        return []


def client_order_id(action_key: str, attempt: int = 0) -> str:
    """DERIVED, never random: the same action always yields the same id.

    This is the idempotency guarantee. A crash between submit and the
    response, followed by a replay, produces a byte-identical id, so the
    venue rejects the duplicate instead of us doubling the position.
    """
    raw = "%s|%d" % (action_key, attempt)
    return "l5f-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


class SafetyLimits:
    """Hard account protection. Outside L5_NATIVE, and never silent."""

    def __init__(self, *, max_usd_per_market: D = D("5"),
                 max_abs_residual: D = D("10"),
                 max_total_exposure_usd: D = D("10"),
                 max_concurrent_markets: int = 1,
                 daily_realized_loss_halt_usd: D = D("5"),
                 band: Optional[tuple] = None) -> None:
        self.max_usd_per_market = max_usd_per_market
        self.max_abs_residual = max_abs_residual
        self.max_total_exposure_usd = max_total_exposure_usd
        self.max_concurrent_markets = max_concurrent_markets
        self.daily_realized_loss_halt_usd = daily_realized_loss_halt_usd
        #: None by default: a price band is a BEHAVIOURAL preference, not
        #: account protection, and the faithful engine does not impose one.
        self.band = band

    def as_dict(self) -> Dict[str, Any]:
        return {"max_usd_per_market": str(self.max_usd_per_market),
                "max_abs_residual": str(self.max_abs_residual),
                "max_total_exposure_usd": str(self.max_total_exposure_usd),
                "max_concurrent_markets": self.max_concurrent_markets,
                "daily_realized_loss_halt_usd":
                    str(self.daily_realized_loss_halt_usd),
                "band": [str(x) for x in self.band] if self.band else None}

    def worst_case_loss_usd(self) -> D:
        """The most this configuration can lose, before any trade happens.

        Every share bought can settle worthless, so committed dollars are the
        exposure. The halt bounds the day; the per-market and total caps bound
        a single probe. The binding number is the smaller of total exposure
        and the halt, because the halt stops new commitment but cannot unwind
        what is already at risk.
        """
        return max(self.max_total_exposure_usd,
                   self.daily_realized_loss_halt_usd)

    def refuse(self, *, price: D, qty: D, market: str,
               committed_by_market: Mapping[str, D],
               residual_after: D, realized_today: D,
               open_markets: Sequence[str]) -> Optional[str]:
        if realized_today <= -self.daily_realized_loss_halt_usd:
            return "DAILY_REALIZED_LOSS_HALT"
        cost = price * qty
        mk = committed_by_market.get(market, D(0))
        if mk + cost > self.max_usd_per_market:
            return "MAX_USD_PER_MARKET"
        total = sum(committed_by_market.values(), D(0))
        if total + cost > self.max_total_exposure_usd:
            return "MAX_TOTAL_EXPOSURE"
        if abs(residual_after) > self.max_abs_residual:
            return "MAX_ABS_RESIDUAL"
        if (market not in open_markets
                and len(open_markets) >= self.max_concurrent_markets):
            return "MAX_CONCURRENT_MARKETS"
        if self.band is not None and not (self.band[0] <= price <= self.band[1]):
            return "BAND"
        return None


class Provenance:
    """One record per L5 action, carrying the whole chain."""

    def __init__(self, action_key: str, l5: Mapping[str, Any],
                 market: str, window: str) -> None:
        self.rec: Dict[str, Any] = {
            "action_key": action_key, "market": market, "window": window,
            "L5_NATIVE": dict(l5), "stage_reached": "L5_NATIVE",
            "PMUS_TRANSLATION": None, "SAFETY_OVERLAY": None,
            "ORDER": None, "FILL": None, "INVENTORY": None,
            "SETTLEMENT": None, "REALIZED": None,
            "refused_by_layer": None, "refused_reason": None,
            "labels": ("provenance chain; a stage that did not happen is "
                       "recorded as not having happened"),
        }

    def refuse(self, layer: str, reason: str) -> Dict[str, Any]:
        self.rec["refused_by_layer"] = layer
        self.rec["refused_reason"] = reason
        return self.rec

    def stage(self, name: str, payload: Mapping[str, Any]) -> None:
        self.rec[name] = dict(payload)
        self.rec["stage_reached"] = name


class InventoryStore:
    """Persisted inventory. The VENUE is authoritative; this is a cache."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.state: Dict[str, Any] = {"positions": {}, "committed": {},
                                      "realized_today": "0", "day": None,
                                      "submitted": {}}
        self._load()

    def _load(self) -> None:
        p = pathlib.Path(self.path)
        if p.exists():
            try:
                self.state.update(json.loads(p.read_text()))
            except ValueError:
                pass

    def save(self) -> None:
        p = pathlib.Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state, default=str))
        tmp.replace(p)                       # atomic: never a torn file

    def committed(self) -> Dict[str, D]:
        return {k: D(str(v)) for k, v in self.state["committed"].items()}

    def position(self, market: str, side: str) -> D:
        return D(str(self.state["positions"].get("%s|%s" % (market, side), 0)))

    def residual(self, market: str) -> D:
        return self.position(market, "UP") - self.position(market, "DOWN")

    def realized_today(self) -> D:
        return D(str(self.state.get("realized_today") or 0))

    def already_submitted(self, coid: str) -> bool:
        return coid in (self.state.get("submitted") or {})

    def record_submit(self, coid: str, intent: Mapping[str, Any]) -> None:
        self.state.setdefault("submitted", {})[coid] = {
            "t": dt.datetime.now(dt.timezone.utc).isoformat(),
            "market": intent.get("market"), "side": intent.get("side"),
            "price": str(intent.get("price")), "qty": str(intent.get("qty")),
        }
        self.save()

    def reserve(self, market: str, usd: D) -> None:
        self.state["committed"][market] = str(
            D(str(self.state["committed"].get(market, 0))) + usd)
        self.save()

    # ---- resting maker orders (live lifecycle; the venue decides fills) ----
    def resting(self) -> Dict[str, Dict[str, Any]]:
        return self.state.setdefault("resting", {})

    def add_resting(self, coid: str, order: Mapping[str, Any]) -> None:
        """A resting order's full notional is RESERVED against the caps the
        moment it rests, because it can fill in full at any instant."""
        self.resting()[coid] = {k: str(v) if isinstance(v, D) else v
                                for k, v in order.items()}
        self.reserve(order["market"], D(str(order["price"]))
                     * D(str(order["qty"])))

    def apply_position(self, market: str, side: str, qty: D) -> None:
        """Position only: the notional was reserved when the order rested."""
        k = "%s|%s" % (market, side)
        self.state["positions"][k] = str(
            D(str(self.state["positions"].get(k, 0))) + qty)
        self.save()

    def close_resting(self, coid: str) -> Optional[Dict[str, Any]]:
        """The order's life is over: release the unfilled reservation."""
        r = self.resting().pop(coid, None)
        if r is not None:
            unfilled = D(r["qty"]) - D(r.get("filled") or "0")
            if unfilled > 0:
                self.reserve(r["market"], -(D(r["price"]) * unfilled))
            self.save()
        return r

    def forget_submit(self, coid: str) -> None:
        """Only for a refusal that provably transmitted nothing."""
        (self.state.get("submitted") or {}).pop(coid, None)
        self.save()

    def apply_fill(self, market: str, side: str, qty: D, price: D) -> None:
        k = "%s|%s" % (market, side)
        self.state["positions"][k] = str(
            D(str(self.state["positions"].get(k, 0))) + qty)
        self.state["committed"][market] = str(
            D(str(self.state["committed"].get(market, 0))) + price * qty)
        self.save()

    def reconcile_from_venue(self, positions: Sequence[Mapping[str, Any]],
                             open_orders: Sequence[Mapping[str, Any]]
                             ) -> Dict[str, Any]:
        """The venue overwrites us. Divergence is REPORTED, never averaged.

        On restart our file may be stale (a fill landed after our last save)
        or ahead (we saved an optimistic fill that never happened). Trusting
        the file in either case trades on a fiction, so the venue's answer
        replaces ours and the difference is surfaced.
        """
        before = dict(self.state["positions"])
        after: Dict[str, str] = {}
        for p in positions:
            after["%s|%s" % (p["market"], p["side"])] = str(p["qty"])
        drift = {k: {"ours": before.get(k, "0"), "venue": after.get(k, "0")}
                 for k in set(before) | set(after)
                 if str(before.get(k, "0")) != str(after.get(k, "0"))}
        self.state["positions"] = after
        self.save()
        return {"reconciled": True, "drift": drift,
                "open_orders_at_venue": len(open_orders),
                "authority": "VENUE"}


class FaithfulExecutor:
    """Drives one L5 action through the whole path. Cannot transmit alone."""

    def __init__(self, store: InventoryStore, limits: SafetyLimits, *,
                 transport: Optional[Any] = None,
                 lam: D = D("1"), now_fn=None, live: bool = False,
                 tick: D = D("0.01")) -> None:
        self.store = store
        self.limits = limits
        self.transport = transport or NullTransport()
        self.lam = lam
        #: LIVE execution: a restable action RESTS and the venue decides
        #: whether it fills. Replay/shadow callers leave this False and keep
        #: the frozen rule's STRICT evidence standard for crediting fills.
        self.live = live
        self.tick = tick
        self._now = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def _age_s(self, row: Mapping[str, Any]) -> Optional[float]:
        try:
            t = dt.datetime.fromisoformat(str(row["receipt_utc"]))
        except Exception:                                      # noqa: BLE001
            return None
        return (self._now() - t).total_seconds()

    def handle(self, row: Mapping[str, Any],
               authenticated: Mapping[str, Any],
               *, market: str, window: str,
               open_markets: Sequence[str] = ()) -> Dict[str, Any]:
        key = str(row.get("action_key"))
        dec = FT.l5_native(row)
        prov = Provenance(key, dec or {"unreadable": True}, market, window)
        if dec is None:
            return prov.refuse("L5_NATIVE", "UNREADABLE_ROW")

        age = self._age_s(row)
        if age is not None and age > STALE_ACTION_MAX_S:
            return prov.refuse("PMUS_TRANSLATION",
                               "STALE_ACTION:%0.1fs" % age)

        want = dec["l5_size"] * self.lam
        tr = FT.pmus_translate(dec, row, authenticated, want)
        frozen = {"ok": bool(tr.get("ok")), "mode": tr.get("mode"),
                  "reason": tr.get("reason")}
        live_rest = False
        if (self.live and not tr.get("ok") and tr.get("mode") == FT.MAKER
                and tr.get("reason") == FT.T_UNEVALUABLE):
            # THE FROZEN RULE CHOSE MAKER. Its docstring: "the closest
            # executable means is a MAKER order AT L5'S OWN PRICE ... It
            # acquires only on authenticated STRICT evidence ... that is an
            # evidence standard, not a behavioural rule." STRICT decides
            # whether REPLAY may credit a hypothetical resting order as
            # filled. Live, the order really rests and the venue's own fill
            # is the evidence, so the replay standard is not applied as a
            # refusal here. Price, size and side are the rule's; only the
            # venue grid is applied (buy side: never rounded UP).
            px = to_grid(dec["price"], self.tick)
            tr = {"ok": True, "mode": FT.MAKER, "price": px, "qty": want,
                  "slippage": px - dec["price"], "depth": None,
                  "fee": D(0), "rebate": fees.maker_rebate(px, want)}
            live_rest = True
        prov.stage("PMUS_TRANSLATION", {
            "ok": bool(tr.get("ok")), "mode": tr.get("mode"),
            "price": str(tr.get("price")), "qty": str(tr.get("qty")),
            "slippage_vs_l5": str(tr.get("slippage")),
            "depth": str(tr.get("depth")), "reason": tr.get("reason"),
            "frozen_rule_output": frozen,
            "live_execution": ("REST_AT_L5_PRICE: post-only GTC; life ends at "
                               "L5's next side/price change, window end, or "
                               "fill; fill authority VENUE"
                               if live_rest else None)})
        if not tr.get("ok"):
            return prov.refuse("PMUS_TRANSLATION", tr["reason"])

        resid_after = self.store.residual(market) + (
            tr["qty"] if dec["side"] == "UP" else -tr["qty"])
        why = self.limits.refuse(
            price=tr["price"], qty=tr["qty"], market=market,
            committed_by_market=self.store.committed(),
            residual_after=resid_after,
            realized_today=self.store.realized_today(),
            open_markets=open_markets)
        prov.stage("SAFETY_OVERLAY", {
            "permitted": why is None, "reason": why,
            "limits": self.limits.as_dict(),
            "note": ("account protection only; never a statement about L5 "
                     "behaviour or PMUS capability")})
        if why:
            return prov.refuse("SAFETY_OVERLAY", why)

        coid = client_order_id(key)
        if self.store.already_submitted(coid):
            prov.stage("ORDER", {"client_order_id": coid,
                                 "submitted": False,
                                 "reason": "DUPLICATE_SUPPRESSED"})
            return prov.rec

        intent = {"client_order_id": coid, "market": market,
                  "side": dec["side"], "price": tr["price"], "qty": tr["qty"],
                  "mode": tr["mode"], "action_key": key}
        # AT MOST ONCE. The intent is on disk BEFORE the mutation, so a crash
        # between the send and the save replays as DUPLICATE_SUPPRESSED rather
        # than as a second order. Only a refusal that provably sent nothing
        # (NotArmed) removes it again.
        self.store.record_submit(coid, intent)
        try:
            ack = self.transport.submit(intent)
        except NotArmed as exc:
            self.store.forget_submit(coid)
            prov.stage("ORDER", {"client_order_id": coid, "submitted": False,
                                 "reason": "NOT_ARMED", "detail": str(exc)[:200],
                                 "intent": {k: str(v) for k, v in intent.items()}})
            return prov.rec
        except Exception as exc:                               # noqa: BLE001
            # ambiguous: it may or may not have reached the venue. Never
            # replayed, and its full notional is RESERVED against the caps as
            # if it filled, so an unknown can only make us more conservative.
            self.store.reserve(market, tr["price"] * tr["qty"])
            prov.stage("ORDER", {"client_order_id": coid,
                                 "submitted": "UNKNOWN",
                                 "reason": "TRANSPORT_ERROR",
                                 "detail": "%s: %s" % (type(exc).__name__,
                                                       str(exc)[:200])})
            return prov.rec

        prov.stage("ORDER", {"client_order_id": coid, "submitted": True,
                             "venue_order_id": ack.get("order_id"),
                             "state": ack.get("state"),
                             "error": ack.get("error"),
                             "resting": tr["mode"] == FT.MAKER,
                             "transport": getattr(self.transport, "name", "?")})
        if tr["mode"] == FT.MAKER and ack.get("order_id"):
            self.store.add_resting(coid, {
                "venue_order_id": ack["order_id"], "market": market,
                "side": dec["side"], "price": tr["price"], "qty": tr["qty"],
                "filled": "0", "action_key": key,
                "rested_utc": self._now().isoformat()})
            prov.stage("FILL", {"filled": False, "filled_qty": "0",
                                "resting": True,
                                "note": "fills are read from the venue by "
                                        "the lifecycle, never assumed"})
            return prov.rec
        filled = D(str(ack.get("filled_qty") or 0))
        if filled <= 0:
            prov.stage("FILL", {"filled": False, "filled_qty": "0"})
            return prov.rec
        fill_px = D(str(ack.get("fill_price") or tr["price"]))
        fee = (fees.taker_fee(fill_px, filled) if tr["mode"] == FT.TAKER
               else D(0))
        rebate = (fees.maker_rebate(fill_px, filled) if tr["mode"] == FT.MAKER
                  else D(0))
        prov.stage("FILL", {"filled": True, "filled_qty": str(filled),
                            "fill_price": str(fill_px), "fee_usd": str(fee),
                            "maker_rebate_usd": str(rebate),
                            "note": ("fee and rebate are separate lines and "
                                     "are never netted into trading P&L")})
        self.store.apply_fill(market, dec["side"], filled, fill_px)
        prov.stage("INVENTORY", {
            "market": market,
            "up": str(self.store.position(market, "UP")),
            "dn": str(self.store.position(market, "DOWN")),
            "residual": str(self.store.residual(market)),
            "committed_usd": str(self.store.committed().get(market, D(0)))})
        return prov.rec

    # ------------------------------------------------ resting-order lifecycle
    def poll_resting(self) -> List[Dict[str, Any]]:
        """Read each resting order from the VENUE; apply new fills; close
        orders the venue reports terminal. Nothing is inferred."""
        events: List[Dict[str, Any]] = []
        for coid, r in list(self.store.resting().items()):
            try:
                st = self.transport.order_status(r["venue_order_id"])
            except Exception as exc:                           # noqa: BLE001
                events.append({"client_order_id": coid,
                               "status_error": type(exc).__name__})
                continue
            f = D(str(st.get("filled_qty") or 0))
            delta = f - D(r.get("filled") or "0")
            if delta > 0:
                self.store.apply_position(r["market"], r["side"], delta)
                r["filled"] = str(f)
                self.store.save()
                events.append({"client_order_id": coid, "event": "FILL",
                               "filled_delta": str(delta),
                               "filled_total": str(f),
                               "fill_price": st.get("fill_price"),
                               "maker_rebate_usd": str(fees.maker_rebate(
                                   D(r["price"]), delta)),
                               "action_key": r.get("action_key")})
            if st.get("terminal"):
                self.store.close_resting(coid)
                events.append({"client_order_id": coid, "event": "CLOSED",
                               "state": st.get("state"),
                               "filled_total": str(f)})
        return events

    def end_resting(self, reason: str, *, keep=None) -> List[Dict[str, Any]]:
        """End resting orders' lives (cancel), except those `keep` accepts.

        Faithful lifecycle (pmus_mirror_lifecycle.order_life): an order lives
        until L5's next action changes the translated intent (side or price)
        or the window ends. It is cancelled then, never repriced: L5's next
        action is its own order.
        """
        events: List[Dict[str, Any]] = []
        for coid, r in list(self.store.resting().items()):
            if keep is not None and keep(r):
                continue
            try:
                if hasattr(self.transport, "cancel_venue"):
                    self.transport.cancel_venue(r["venue_order_id"])
                else:
                    self.transport.cancel(coid)
                events.append({"client_order_id": coid, "event": "CANCEL",
                               "reason": reason})
            except Exception as exc:                           # noqa: BLE001
                events.append({"client_order_id": coid, "event": "CANCEL",
                               "reason": reason,
                               "error": type(exc).__name__})
        # read back what the venue says, fills that raced the cancel included
        return events + self.poll_resting()


def to_grid(px: D, tick: D) -> D:
    """L5's price on the venue grid. Float noise (0.9400000005) snaps to the
    nearest tick; anything genuinely between ticks is floored, so a BUY is
    never priced above what L5 paid."""
    q = px / tick
    near = q.to_integral_value()
    if abs(q - near) < D("0.0001"):
        return near * tick
    return q.to_integral_value(rounding="ROUND_FLOOR") * tick


def settle(records: Sequence[Mapping[str, Any]], *, market: str,
           yes_value: D) -> Dict[str, Any]:
    """SETTLEMENT and REALIZED, from filled inventory only.

    REALIZED requires a settlement value from the venue. Nothing modelled,
    marked or accrued is called realized here, and fees and rebates stay on
    their own lines.
    """
    up = dn = cost_up = cost_dn = fee = rebate = D(0)
    for r in records:
        f = r.get("FILL") or {}
        if not f.get("filled"):
            continue
        q = D(str(f["filled_qty"]))
        px = D(str(f["fill_price"]))
        fee += D(str(f.get("fee_usd") or 0))
        rebate += D(str(f.get("maker_rebate_usd") or 0))
        if (r.get("L5_NATIVE") or {}).get("side") == "UP":
            up += q
            cost_up += px * q
        else:
            dn += q
            cost_dn += px * q
    matched = min(up, dn)
    residual = up - dn
    au = (cost_up / up) if up > 0 else D(0)
    ad = (cost_dn / dn) if dn > 0 else D(0)
    matched_pnl = ((D(1) - (au + ad)) * matched) if (up > 0 and dn > 0) else D(0)
    if residual > 0:
        resid_pnl = yes_value * residual - au * residual
    elif residual < 0:
        q = -residual
        resid_pnl = (D(1) - yes_value) * q - ad * q
    else:
        resid_pnl = D(0)
    return {
        "market": market, "settlement_yes_value": str(yes_value),
        "matched": str(matched), "residual": str(residual),
        "matched_pair_pnl_usd": str(matched_pnl),
        "residual_pnl_usd": str(resid_pnl),
        "taker_fees_usd": str(fee), "maker_rebate_usd": str(rebate),
        "realized_net_usd": str(matched_pnl + resid_pnl + rebate - fee),
        "labels": ("REALIZED requires venue settlement; fees and rebates are "
                   "separate lines, never netted into trading P&L"),
    }


def deadman(store: InventoryStore, transport: Any, *,
            reason: str) -> Dict[str, Any]:
    """Cancel everything we believe is live. Safe when nothing is armed."""
    out = {"reason": reason, "cancelled": [], "errors": []}
    if getattr(transport, "armed", False) and hasattr(transport, "cancel_all"):
        # market-wide cancel first: it does not depend on our record of
        # which orders are live, which is exactly what a deadman cannot trust
        try:
            transport.cancel_all()
            out["cancel_all"] = True
        except Exception as exc:                               # noqa: BLE001
            out["errors"].append({"cancel_all": type(exc).__name__})
    for coid in list((store.state.get("submitted") or {}).keys()):
        try:
            transport.cancel(coid)
            out["cancelled"].append(coid)
        except Exception as exc:                               # noqa: BLE001
            out["errors"].append({"client_order_id": coid,
                                  "error": type(exc).__name__})
    return out
