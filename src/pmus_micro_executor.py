"""PMUS MICRO-LIVE executor — penny-notional sizing, DISARMED BY DEFAULT.

Issue #167 comments 5519953967/5519959374 (owner explicitly approved
penny-scale live learning) with venue proof from 5520088122 (NYY/LAA
`minimumTradeQty=0.01`, tick 0.005, qty 0.02 ~= $0.0096). This is a RISK-
SIZE adaptation only: side/price/timing decisions are byte-identical to
the conservative shell (post-only maker BUY at the YES bid inside the
20-80c band, preview-first, fresh-BBO refusal, complement at the fee-safe
bound); the ONLY change is quantity, which is derived per market from
authoritative venue metadata, never assumed.

Isolation: the q=1 executor and v4 remain default and untouched — they
never import this module, and this module has its own arming keys, so
arming q1 cannot arm micro and vice versa:
  PMUS_MICRO_LIVE=YES
  PMUS_MICRO_SESSION_ID=<fresh id>
  PMUS_MICRO_ARM=<same id>
  PMUS_MICRO_MARKET_SLUG=<slug> and PMUS_MICRO_GATE_EVIDENCE=<same slug>
Anything absent/mismatched -> DISARMED before any network call.

Sizing rules (fail closed everywhere):
* GET gateway `/v1/market/slug/{slug}` and read `minimumTradeQty` AND
  `orderPriceMinTickSize`. Missing/unparseable -> SKIP, no mutation.
* qty = floor_to_step(TARGET_NOTIONAL / price); if that floors to zero,
  the venue minimum itself is used ONLY when min*price <= $0.02
  (5519959374 item 3); otherwise SKIP — never a silent q=1 fallback.
* Quantity is an exact multiple of minimumTradeQty, rounded DOWN, so the
  intended notional never exceeds $0.01 except via the venue-minimum
  exception, and never exceeds $0.02 at all.
* The entry price must sit on the venue's real tick grid from metadata
  (no 0.005 assumption for arbitrary props) -> else SKIP.

Partial fills are REAL decimal inventory: any authoritative cumQuantity
> 0 is a fill of exactly that quantity (never NO_FILL), the complement
is sized to the authoritative filled first-leg quantity, and a partial
complement leaves an exact decimal unmatched residual — no int()
truncation anywhere. Every ledger row carries exact decimal qty and
action notional; fees/rebates stay separate and quantity-scaled;
realized_pnl stays null until authoritative settlement reconciliation.

Hard risk caps for the first micro probe (constants, not knobs):
<= $0.01 intended notional per action (venue-minimum exception <= $0.02),
<= $0.02 unmatched directional notional, <= $0.05 cumulative new
first-leg notional per market (from this executor's own durable ledger),
one market/campaign per invocation, no same-side extension, no
auto-restart; the permanent supervisor stays DISARMED and any account
residual still blocks all new first legs via the shared preflight.

Disarmed dry-run (safe anywhere, no network):
    python3 -m src.pmus_micro_executor
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional

from src import pmus_q1_executor as base
from src import pmus_q1_executor_v4 as v4

API = base.API
GATEWAY = base.GATEWAY
Transport = base.Transport
Halt = v4.Halt
RejectedNoFill = v4.RejectedNoFill

LIVE_ENV = "PMUS_MICRO_LIVE"
SID_ENV = "PMUS_MICRO_SESSION_ID"
ARM_ENV = "PMUS_MICRO_ARM"
MARKET_ENV = "PMUS_MICRO_MARKET_SLUG"
GATE_ENV = "PMUS_MICRO_GATE_EVIDENCE"
STATE_ENV = "PMUS_MICRO_STATE_DIR"
DEFAULT_STATE_DIR = "/state/pmus-micro"
FIRST_WAIT_ENV = "PMUS_MICRO_FIRST_WAIT_SECONDS"    # default 120
DEADLINE_ENV = "PMUS_MICRO_DEADLINE_SECONDS"        # default 300

TARGET_NOTIONAL = D("0.01")          # intended first-leg notional
VENUE_MIN_NOTIONAL_CAP = D("0.02")   # venue-minimum exception ceiling
UNMATCHED_NOTIONAL_CAP = D("0.02")
PER_MARKET_FIRST_LEG_CAP = D("0.05")
MAX_COMPLEMENT_ATTEMPTS = 4
POLL_S = base.POLL_S

taker_fee = base.taker_fee
maker_rebate = base.maker_rebate
complement_bound = base.complement_bound
taker_completion_ok = base.taker_completion_ok
entry_ok = base.entry_ok


def armed() -> bool:
    sid = os.getenv(SID_ENV, "").strip()
    return (os.getenv(LIVE_ENV, "").strip().upper() == "YES" and bool(sid)
            and os.getenv(ARM_ENV, "").strip() == sid)


def gate_asserted(slug: str) -> bool:
    return bool(slug) and os.getenv(GATE_ENV, "").strip() == slug


def state_dir() -> Path:
    return Path(os.getenv(STATE_ENV, DEFAULT_STATE_DIR))


def ledger_append(row: Mapping[str, Any]) -> None:
    try:
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "micro_ledger.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(),
                                 **row}, default=str) + "\n")
    except OSError:
        pass          # a dry-run host without /state must never crash


def persist_intent(row: Mapping[str, Any]) -> None:
    ledger_append({"kind": "intent", **row})


# ------------------------- venue-aware sizing -------------------------

def _num(value: Any) -> Optional[D]:
    if isinstance(value, Mapping):
        value = value.get("value")
    if value is None:
        return None
    try:
        d = D(str(value))
        return d if d.is_finite() else None
    except Exception:
        return None


def _find_key(obj: Any, key: str) -> Any:
    """Tolerant lookup of a metadata key anywhere in the response tree."""
    if isinstance(obj, Mapping):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def market_metadata(t: Transport, slug: str) -> Optional[dict]:
    """minimumTradeQty + orderPriceMinTickSize from authoritative market
    metadata. Anything missing or unparseable -> None (caller SKIPs)."""
    try:
        resp = t.request("GET", f"/v1/market/slug/{slug}", base=GATEWAY)
    except Exception:
        return None
    step = _num(_find_key(resp, "minimumTradeQty"))
    tick = _num(_find_key(resp, "orderPriceMinTickSize"))
    if step is None or tick is None or step <= 0 or tick <= 0:
        return None
    mtype = _find_key(resp, "sportsMarketTypeV2") or _find_key(resp, "type")
    return {"min_trade_qty": step, "tick": tick,
            "market_type": str(mtype) if mtype is not None else None}


def on_tick(price: D, tick: D) -> bool:
    return (D(str(price)) / tick) == (D(str(price)) / tick).to_integral_value()


def micro_qty(price: D, step: D,
              target: D = TARGET_NOTIONAL,
              venue_min_cap: D = VENUE_MIN_NOTIONAL_CAP) -> dict:
    """Exact decimal sizing: floor(target/price) to the venue step; a
    zero result may use the venue minimum only when min*price <= cap."""
    price, step = D(str(price)), D(str(step))
    if price <= 0 or step <= 0:
        return {"qty": None, "notional": None, "skip": "bad_inputs"}
    units = (target / price / step).to_integral_value(rounding=ROUND_DOWN)
    qty = units * step
    if qty > 0:
        return {"qty": qty, "notional": qty * price, "skip": None}
    notional = step * price
    if notional <= venue_min_cap:
        return {"qty": step, "notional": notional, "skip": None,
                "venue_min_exception": True}
    return {"qty": None, "notional": notional,
            "skip": f"venue_min_exceeds_cap:{notional}"}


def _json_qty(qty: D) -> float:
    f = float(qty)
    if D(str(f)) != qty:
        raise Halt(f"QTY_NOT_JSON_SAFE:{qty}")
    return f


def micro_eligible(t: Transport, slug: str) -> dict:
    """READ-ONLY preflight row for penny-sizing candidate ranking. GET
    calls only; no mutation exists in this helper."""
    row: dict[str, Any] = {"slug": slug, "labels": "read-only preflight"}
    meta = market_metadata(t, slug)
    if meta is None:
        return {**row, "skip_reason": "metadata_missing_or_unparseable"}
    row.update({"min_trade_qty": str(meta["min_trade_qty"]),
                "tick": str(meta["tick"]),
                "market_type": meta["market_type"]})
    try:
        bbo = v4.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")
    except Exception as exc:
        return {**row, "skip_reason": f"bbo:{type(exc).__name__}"}
    row.update({"bid": str(bbo["bid"]), "ask": str(bbo["ask"])})
    if not entry_ok(bbo["bid"]):
        return {**row, "skip_reason": f"entry_out_of_band:{bbo['bid']}"}
    if not on_tick(bbo["bid"], meta["tick"]):
        return {**row, "skip_reason": "tick_mismatch"}
    sized = micro_qty(bbo["bid"], meta["min_trade_qty"])
    if sized["skip"]:
        return {**row, "skip_reason": sized["skip"]}
    return {**row, "qty": str(sized["qty"]),
            "intended_notional": str(sized["notional"]),
            "venue_min_exception": bool(sized.get("venue_min_exception")),
            "skip_reason": None}


# ------------------------- order mechanics -------------------------

def _body(slug: str, outcome_side: str, price: D, qty: D,
          post_only: bool) -> dict[str, Any]:
    return {
        "marketSlug": slug,
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(price), "currency": "USD"},
        "quantity": _json_qty(qty),
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        "participateDontInitiate": bool(post_only),
        "outcomeSide": outcome_side,
        "action": "ORDER_ACTION_BUY",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }


def place_micro_buy(t: Transport, slug: str, outcome_side: str, price: D,
                    qty: D, stage: str, *, post_only: bool = True) -> str:
    """Preview -> fresh outcome-aware BBO refusal -> persist -> ONE POST.
    Identical safety order to the audited v4 path; only qty differs."""
    price = D(str(price))
    body = _body(slug, outcome_side, price, qty, post_only)
    preview = t.request("POST", "/v1/order/preview", {"request": body})
    po = preview.get("order") if isinstance(preview, Mapping) else None
    if not isinstance(po, Mapping):
        raise Halt(f"PREVIEW_REJECTED:{stage}")
    fresh = v4.outcome_bbo(t, slug, outcome_side)
    if post_only and price >= fresh["ask"]:
        raise Halt(f"POST_ONLY_STALE_REFUSE:{stage}")
    if not post_only and price < fresh["ask"]:
        raise Halt(f"TAKER_PRICE_STALE:{stage}")
    persist_intent({"stage": stage, "slug": slug, "side": outcome_side,
                    "price": str(price), "qty": str(qty),
                    "notional": str(qty * price),
                    "post_only": bool(post_only)})
    resp = t.request("POST", "/v1/orders", body)
    order_id = str((resp.get("id") if isinstance(resp, Mapping) else None) or
                   (resp.get("orderId") if isinstance(resp, Mapping) else None)
                   or "")
    if not order_id:
        raise Halt(f"NO_ORDER_ID:{stage}")
    ledger_append({"kind": "submitted", "stage": stage, "slug": slug,
                   "side": outcome_side, "price": str(price),
                   "qty": str(qty), "notional": str(qty * price),
                   "post_only": bool(post_only), "order_id": order_id})
    return order_id


def fill_snapshot(t: Transport, order_id: str) -> dict:
    """Authoritative partial-aware fill state (decimal-exact)."""
    order = v4._order_status(t, order_id)
    raw = order.get("cumQuantity")
    if raw is None:
        raw = order.get("filledQuantity")
    filled = _num(raw) or D("0")
    px = None
    for key in ("avgPx", "avgPrice", "averagePrice"):
        px = _num(order.get(key))
        if px is not None and px > 0:
            break
        px = None
    state = str(order.get("state") or order.get("status") or "").upper()
    if filled > 0 and px is None:
        raise Halt("AMBIGUOUS_FILL_PRICE")
    return {"filled": filled, "avg_px": px, "state": state}


def _wait_fill(t: Transport, order_id: str, target_qty: D, until: float,
               clock=time.monotonic, sleep=time.sleep) -> dict:
    snap = {"filled": D("0"), "avg_px": None, "state": ""}
    while clock() < until:
        snap = fill_snapshot(t, order_id)
        if snap["filled"] >= target_qty:
            return snap
        sleep(POLL_S)
    return snap


def _cum_first_leg_notional(slug: str) -> D:
    total = D("0")
    path = state_dir() / "micro_ledger.jsonl"
    if not path.exists():
        return total
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("kind") == "submitted" and row.get("slug") == slug and \
                str(row.get("stage", "")).startswith("first_leg"):
            try:
                total += D(str(row.get("notional")))
            except Exception:
                total += PER_MARKET_FIRST_LEG_CAP     # unparseable = block
    return total


def run_campaign(t: Optional[Transport] = None,
                 clock=time.monotonic, sleep=time.sleep) -> dict:
    slug = os.getenv(MARKET_ENV, "").strip()
    report: dict[str, Any] = {
        "campaign": "pmus-micro-v1", "slug": slug,
        "realized_pnl": None,
        "labels": ("MICRO-LIVE decimal sizing; submitted->filled->settled->"
                   "realized; fees/rebates separate and quantity-scaled"),
    }
    if not armed():
        return {**report, "status": "DISARMED",
                "note": "micro dual-key absent/mismatched; no network"}
    if not base.has_credentials():
        return {**report, "status": "NO_CREDENTIALS"}
    if not gate_asserted(slug):
        return {**report, "status": "GATE_NOT_CLEARED"}

    t = t or Transport()
    first_wait = float(os.getenv(FIRST_WAIT_ENV, "120"))
    deadline_s = float(os.getenv(DEADLINE_ENV, "300"))
    start = clock()
    stop_at = start + deadline_s
    first_qty: Optional[D] = None
    first_px: Optional[D] = None
    try:
        meta = market_metadata(t, slug)
        if meta is None:
            return {**report,
                    "status": "MICRO_SKIP_METADATA_UNVERIFIED"}
        reason = v4.preflight(t, slug)
        if reason:
            return {**report, "status": "PREFLIGHT_FAILED",
                    "reason": reason}
        entry = v4.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")["bid"]
        if not entry_ok(entry):
            return {**report, "status": "ENTRY_OUT_OF_BAND",
                    "bid": str(entry)}
        if not on_tick(entry, meta["tick"]):
            return {**report, "status": "MICRO_SKIP_TICK_MISMATCH",
                    "bid": str(entry), "tick": str(meta["tick"])}
        sized = micro_qty(entry, meta["min_trade_qty"])
        if sized["skip"]:
            return {**report, "status": "MICRO_SKIP_SIZE",
                    "reason": sized["skip"]}
        qty, notional = sized["qty"], sized["notional"]
        cum = _cum_first_leg_notional(slug)
        if cum + notional > PER_MARKET_FIRST_LEG_CAP:
            return {**report, "status": "MICRO_SKIP_MARKET_CAP",
                    "cumulative": str(cum), "intended": str(notional)}
        report.update({"qty": str(qty), "intended_notional": str(notional),
                       "min_trade_qty": str(meta["min_trade_qty"]),
                       "tick": str(meta["tick"])})

        try:
            oid = place_micro_buy(t, slug, "OUTCOME_SIDE_YES", entry, qty,
                                  "first_leg", post_only=True)
        except RejectedNoFill as rej:
            return {**report, "status": "REJECTED_NO_FILL",
                    "reason": str(rej), "residual_qty": "0"}
        except Halt as halt:
            return {**report, "status": "NO_FILL_SAFE_REFUSAL",
                    "reason": str(halt), "residual_qty": "0"}

        try:
            snap = _wait_fill(t, oid, qty, min(start + first_wait, stop_at),
                              clock, sleep)
        except RejectedNoFill:
            snap = {"filled": D("0"), "avg_px": None, "state": "REJECTED"}
        if snap["filled"] < qty:
            try:
                v4.cancel_exact(t, oid)
            except RejectedNoFill:
                pass
            try:
                snap = fill_snapshot(t, oid)       # authoritative late look
            except RejectedNoFill:
                snap = {"filled": D("0"), "avg_px": None,
                        "state": "REJECTED"}
        first_qty, first_px = snap["filled"], snap["avg_px"]
        if first_qty == 0:
            status = "REJECTED_NO_FILL" if "REJECT" in snap["state"] \
                else "NO_FILL"
            return {**report, "status": status, "residual_qty": "0"}

        # ANY authoritative fill is real decimal inventory — never NO_FILL
        ledger_append({"kind": "filled", "stage": "first_leg",
                       "qty": str(first_qty), "price": str(first_px),
                       "notional": str(first_qty * first_px),
                       "partial": first_qty < qty,
                       "maker_rebate_separate":
                       str(maker_rebate(first_px) * first_qty)})

        comp_filled, comp_px = _manage_complement(
            t, slug, first_px, first_qty, meta["tick"], stop_at,
            clock=clock, sleep=sleep)
        unmatched = first_qty - comp_filled
        assert unmatched * first_px <= UNMATCHED_NOTIONAL_CAP
        result = {
            **report,
            "first_leg": {"qty": str(first_qty), "px": str(first_px),
                          "notional": str(first_qty * first_px)},
            "complement": {"qty": str(comp_filled),
                           "px": str(comp_px) if comp_px else None},
            "residual_qty": str(unmatched),
            "residual_notional": str(unmatched * first_px),
        }
        if unmatched == 0:
            return {**result, "status": "MICRO_MATCHED",
                    "pair_unit_cost": str(first_px + comp_px)}
        return {**result, "status": "MICRO_PARTIAL_RESIDUAL_HOLD"}
    except Halt as halt:
        ledger_append({"kind": "halt", "reason": str(halt)})
        return {**report, "status": "HALT", "reason": str(halt),
                "residual_qty": str(first_qty or 0)}
    except Exception as exc:
        reason = f"UNEXPECTED_{type(exc).__name__}:{str(exc)[:120]}"
        ledger_append({"kind": "halt", "reason": reason})
        return {**report, "status": "HALT", "reason": reason,
                "residual_qty": str(first_qty or 0)}
    finally:
        v4.cancel_all_deadman(t, slug)


def _manage_complement(t: Transport, slug: str, first_px: D, need: D,
                       tick: D, stop_at: float, *, clock=time.monotonic,
                       sleep=time.sleep) -> tuple[D, Optional[D]]:
    """Bounded complement loop sized to the AUTHORITATIVE filled first-leg
    quantity; partial complement fills tracked decimal-exact."""
    bound = complement_bound(first_px)
    filled = D("0")
    avg_px: Optional[D] = None
    if bound is None:
        return filled, avg_px
    for attempt in range(1, MAX_COMPLEMENT_ATTEMPTS + 1):
        remaining = need - filled
        if remaining <= 0 or clock() >= stop_at:
            break
        try:
            bbo = v4.outcome_bbo(t, slug, "OUTCOME_SIDE_NO")
        except Exception:
            sleep(min(1.0, max(0.0, stop_at - clock())))
            continue
        post_only, px = True, None
        if taker_completion_ok(first_px, bbo["ask"]):
            post_only, px = False, bbo["ask"]
        else:
            raw = min(D(str(bound)), bbo["ask"] - tick)
            raw = (raw / tick).to_integral_value(rounding=ROUND_DOWN) * tick
            if base.MIN_PRICE <= raw <= base.MAX_PRICE:
                px = raw
        if px is None:
            sleep(min(1.0, max(0.0, stop_at - clock())))
            continue
        try:
            oid = place_micro_buy(t, slug, "OUTCOME_SIDE_NO", px, remaining,
                                  f"complement_{attempt}",
                                  post_only=post_only)
        except (RejectedNoFill, Halt):
            continue
        try:
            snap = _wait_fill(t, oid, remaining,
                              min(stop_at, clock() + 20.0), clock, sleep)
        except RejectedNoFill:
            continue
        if snap["filled"] < remaining:
            try:
                v4.cancel_exact(t, oid)
                snap = fill_snapshot(t, oid)
            except RejectedNoFill:
                snap = {"filled": D("0"), "avg_px": None}
        got = snap["filled"]
        if got > 0:
            ledger_append({"kind": "filled", "stage": f"complement_{attempt}",
                           "qty": str(got), "price": str(snap["avg_px"]),
                           "notional": str(got * snap["avg_px"]),
                           "partial": got < remaining,
                           "taker": not post_only})
            avg_px = snap["avg_px"] if avg_px is None else \
                (avg_px * filled + snap["avg_px"] * got) / (filled + got)
            filled += got
    return filled, avg_px


def main() -> int:
    report = run_campaign()
    print(json.dumps(report, indent=1, default=str))
    ledger_append({"kind": "campaign_report", **report})
    safe = {"MICRO_MATCHED", "MICRO_PARTIAL_RESIDUAL_HOLD", "NO_FILL",
            "REJECTED_NO_FILL", "NO_FILL_SAFE_REFUSAL", "DISARMED",
            "MICRO_SKIP_METADATA_UNVERIFIED", "MICRO_SKIP_SIZE",
            "MICRO_SKIP_TICK_MISMATCH", "MICRO_SKIP_MARKET_CAP"}
    return 0 if report.get("status") in safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
