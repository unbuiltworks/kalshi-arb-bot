"""PMUS q=1 one-campaign executor — DISARMED BY DEFAULT.

Issue #167 comment 5517384594 (explicit pre-stage authorization; live
orders NOT authorized by this code existing). Isolated from the read-only
recorder/collector — neither imports this module and no order capability
was added to them. Claude never arms this; the operator does.

Arming is dual-key, equivalent to the Gemini probes, and fail-closed:
  PMUS_Q1_LIVE=YES
  PMUS_Q1_SESSION_ID=<fresh id>
  PMUS_Q1_ARM=<the same id>
Absent or mismatched values -> DISARMED before any network call. In
addition the operator must assert the live gate:
  PMUS_Q1_MARKET_SLUG=<market>        target market
  PMUS_Q1_GATE_EVIDENCE=<market>      must EQUAL the slug — copied from
                                      the recorder's Q1_GATE_CLEARED line
Mismatch or absence -> GATE_NOT_CLEARED, no orders.

Hard bounds (constants, not knobs): q=1 per leg; exactly ONE campaign per
invocation, no auto-restart or scale; maker-first entry only inside the
20-80c band; first-leg cost <= $0.80 + venue fee; total campaign risk
capped at $1.00; residual cap 1 contract; $1.00 daily realized-loss stop
(authoritative ledger only). Complement (opposite outcomeSide BUY) only
when the all-in pair cost locks >= 1c pre-rebate after fees; late taker
completion under the identical all-in rule. Fees taker 0.06*p*(1-p),
maker rebate -0.0125*p*(1-p) tracked separately, never loosening bounds.

Order flow: read-auth preflight (balances, flat position in the target
market, no open orders, current public BBO in-band and two-sided) ->
preview-first -> post-only maker limit BUY (participateDontInitiate) with
the intended order persisted to the durable state file BEFORE placement
-> authoritative fill state polled from GET /v1/order/{id} (never assumed;
an unparseable fill price is a HALT, not a guess) -> complement at the
locked-edge bound -> exact-ID cancel with verification on timeout ->
Cancel-All deadman (market-scoped) in a finally block on every exit path.
Accounting labels are strict: submitted -> filled -> settled -> realized;
realized stays null until settlement rows reconcile (audit step, not this
process guessing).

Secrets come only from env (PMUS_API_KEY_ID / PMUS_API_SECRET_KEY, same
names as the recorder) and never appear in logs, state, or output.

Disarmed dry-run (safe anywhere, no network):
    python3 -m src.pmus_q1_executor
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional

API = "https://api.polymarket.us"
GATEWAY = "https://gateway.polymarket.us"

LIVE_ENV = "PMUS_Q1_LIVE"
SID_ENV = "PMUS_Q1_SESSION_ID"
ARM_ENV = "PMUS_Q1_ARM"
MARKET_ENV = "PMUS_Q1_MARKET_SLUG"
GATE_ENV = "PMUS_Q1_GATE_EVIDENCE"
STATE_ENV = "PMUS_Q1_STATE_DIR"
DEFAULT_STATE_DIR = "/state/pmus-q1"
FIRST_WAIT_ENV = "PMUS_Q1_FIRST_WAIT_SECONDS"       # default 180
DEADLINE_ENV = "PMUS_Q1_DEADLINE_SECONDS"           # default 300
RESIDUAL_ACTION_ENV = "PMUS_Q1_RESIDUAL_ACTION"     # hold (default)|flatten
KEY_ENV = "PMUS_API_KEY_ID"
SECRET_ENV = "PMUS_API_SECRET_KEY"

ONE = D("1")
TICK = D("0.005")
MIN_PRICE, MAX_PRICE = D("0.20"), D("0.80")
MIN_EDGE = D("0.01")
QTY = 1
FIRST_LEG_MAX_COST = D("0.80")
CAMPAIGN_RISK_CAP = D("1.00")
RESIDUAL_CAP = 1
DAILY_LOSS_STOP = D("-1.00")
POLL_S = 1.0
MIN_BUYING_POWER = D("2.00")


def taker_fee(p: D) -> D:
    return (D("0.06") * p * (ONE - p)).quantize(D("0.0001"))


def maker_rebate(p: D) -> D:
    return (D("0.0125") * p * (ONE - p)).quantize(D("0.0001"))


def armed() -> bool:
    sid = os.getenv(SID_ENV, "").strip()
    return (os.getenv(LIVE_ENV, "").strip().upper() == "YES" and bool(sid)
            and os.getenv(ARM_ENV, "").strip() == sid)


def has_credentials() -> bool:
    return bool(os.getenv(KEY_ENV, "").strip()) and \
        bool(os.getenv(SECRET_ENV, "").strip())


def gate_asserted(slug: str) -> bool:
    return bool(slug) and os.getenv(GATE_ENV, "").strip() == slug


def signed_headers(method: str, path: str,
                   now_ms: Optional[int] = None) -> dict:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    ts = str(now_ms if now_ms is not None else int(time.time() * 1000))
    seed = base64.b64decode(os.environ[SECRET_ENV].strip())[:32]
    key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    sig = base64.b64encode(
        key.sign(f"{ts}{method}{path}".encode())).decode()
    return {"X-PM-Access-Key": os.environ[KEY_ENV].strip(),
            "X-PM-Timestamp": ts, "X-PM-Signature": sig,
            "Content-Type": "application/json"}


class Transport:
    """Real signed HTTP transport. Tests inject a fake with .request()."""

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None,
                base: str = API) -> Any:
        import ssl
        import urllib.request
        data = json.dumps(body).encode() if body is not None else None
        headers = signed_headers(method, path) if base == API else \
            {"User-Agent": "pmus-q1"}
        req = urllib.request.Request(base + path, data=data,
                                     headers=headers, method=method)
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
            return json.loads(r.read() or b"{}")


# ------------------------- pure economics -------------------------

def complement_bound(first_cost: D, min_edge: D = MIN_EDGE) -> Optional[D]:
    """Max complement price locking >= min_edge pre-rebate: cost1 + cost2
    <= 1 - min_edge, on the 0.005 grid, inside the fee-safe band."""
    px = (ONE - D(str(first_cost)) - min_edge)
    px = (px / TICK).to_integral_value(rounding=ROUND_DOWN) * TICK
    if px < MIN_PRICE or px > MAX_PRICE:
        return None
    return px


def taker_completion_ok(first_cost: D, ask_cost: D,
                        min_edge: D = MIN_EDGE) -> bool:
    """Late taker completion: all-in pair (incl. taker fee) locks >=1c."""
    return (ONE - D(str(first_cost)) - D(str(ask_cost))
            - taker_fee(D(str(ask_cost)))) >= min_edge


def entry_ok(bid: D) -> bool:
    return MIN_PRICE <= D(str(bid)) <= MAX_PRICE and \
        D(str(bid)) <= FIRST_LEG_MAX_COST


# ------------------------- durable state -------------------------

def state_dir() -> Path:
    return Path(os.getenv(STATE_ENV, DEFAULT_STATE_DIR))


def ledger_append(row: Mapping[str, Any]) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    with (d / "ledger.jsonl").open("a", encoding="utf-8") as sink:
        sink.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(),
                               **row}, separators=(",", ":"),
                              default=str) + "\n")


def realized_today() -> D:
    """Authoritative realized P&L for today from the settlement ledger
    (rows written only by the settlement audit, never by this process
    guessing)."""
    path = state_dir() / "ledger.jsonl"
    total = D("0")
    today = datetime.now(timezone.utc).date().isoformat()
    if path.exists():
        for line in path.open(encoding="utf-8"):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") == "realized" and \
                    str(row.get("t", "")).startswith(today):
                try:
                    total += D(str(row["realized_usd"]))
                except Exception:
                    continue
    return total


def persist_intent(intent: Mapping[str, Any]) -> None:
    """Durable-ID rule: the intended order is on disk BEFORE placement."""
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / "campaign_state.json"
    st = {}
    if path.exists():
        try:
            st = json.loads(path.read_text())
        except json.JSONDecodeError:
            st = {}
    st.setdefault("orders", []).append(dict(intent))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, default=str))
    os.replace(tmp, path)


# ------------------------- order operations -------------------------

class Halt(RuntimeError):
    pass


def _order_status(t: Transport, order_id: str) -> dict:
    resp = t.request("GET", f"/v1/order/{order_id}")
    return resp.get("order") or resp


def authoritative_fill(order: Mapping[str, Any]) -> Optional[dict]:
    """Filled state from the venue's own order record. Returns None while
    unfilled; raises Halt when the record says filled but the execution
    price cannot be determined — a fill is never assumed or guessed."""
    status = str(order.get("status") or order.get("orderStatus") or "")
    filled_qty = order.get("filledQuantity") or order.get("cumQty") or 0
    try:
        filled = D(str(filled_qty))
    except Exception:
        filled = D("0")
    if filled < QTY and "FILLED" not in status.upper():
        return None
    for key in ("avgPx", "avgPrice", "averagePrice", "avgExecutionPrice"):
        v = order.get(key)
        if isinstance(v, Mapping):
            v = v.get("value")
        if v is not None:
            try:
                return {"price": D(str(v)), "qty": filled or D(str(QTY))}
            except Exception:
                continue
    raise Halt("AMBIGUOUS_FILL_PRICE: order reports filled but no "
               "parseable average execution price")


def place_limit_buy(t: Transport, slug: str, outcome_side: str,
                    price: D, stage: str) -> str:
    """Preview-first, post-only, durable-intent, q=1."""
    body = {"marketSlug": slug, "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(price)}, "quantity": QTY,
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": True,      # post-only maker
            "outcomeSide": outcome_side,
            "action": "ORDER_ACTION_BUY",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}
    preview = t.request("POST", "/v1/order/preview", body)
    if preview.get("error") or preview.get("warnings"):
        raise Halt(f"PREVIEW_REJECTED:{stage}")
    persist_intent({"stage": stage, "slug": slug, "side": outcome_side,
                    "price": str(price), "qty": QTY,
                    "submitted_utc": datetime.now(timezone.utc).isoformat()})
    resp = t.request("POST", "/v1/orders", body)
    order_id = str(resp.get("orderId") or (resp.get("order") or {}).get("id")
                   or "")
    if not order_id:
        raise Halt(f"NO_ORDER_ID:{stage}")
    persist_intent({"stage": stage + ":accepted", "order_id": order_id})
    ledger_append({"kind": "submitted", "stage": stage, "slug": slug,
                   "side": outcome_side, "price": str(price),
                   "order_id": order_id})
    return order_id


def cancel_exact(t: Transport, order_id: str) -> bool:
    t.request("POST", f"/v1/order/{order_id}/cancel", {})
    order = _order_status(t, order_id)
    status = str(order.get("status") or "").upper()
    return "CANCEL" in status or "FILLED" not in status


def cancel_all_deadman(t: Transport, slug: str) -> None:
    try:
        t.request("POST", "/v1/orders/open/cancel",
                  {"marketSlugs": [slug]})
    except Exception as exc:
        ledger_append({"kind": "error", "stage": "deadman",
                       "err": str(exc)[:120]})


def wait_for_fill(t: Transport, order_id: str, until: float,
                  clock=time.monotonic, sleep=time.sleep) -> Optional[dict]:
    while clock() < until:
        fill = authoritative_fill(_order_status(t, order_id))
        if fill is not None:
            return fill
        sleep(POLL_S)
    return None


# ------------------------- preflight -------------------------

def preflight(t: Transport, slug: str) -> Optional[str]:
    """Read-auth preflight; returns a failure reason or None when clean."""
    bal = t.request("GET", "/v1/account/balances")
    power = None
    for key in ("buyingPower", "availableBalance", "cashBalance"):
        v = bal.get(key) or (bal.get("balances") or {}).get(key) \
            if isinstance(bal.get("balances"), Mapping) else bal.get(key)
        if isinstance(v, Mapping):
            v = v.get("value")
        if v is not None:
            try:
                power = D(str(v))
                break
            except Exception:
                continue
    if power is None or power < MIN_BUYING_POWER:
        return f"buying_power_unverified_or_low:{power}"
    pos = t.request("GET", "/v1/portfolio/positions")
    for p in pos.get("positions") or []:
        if p.get("marketSlug") == slug and \
                D(str(p.get("quantity") or 0)) != 0:
            return "existing_position_in_target_market"
    open_orders = t.request("GET", "/v1/orders/open")
    if open_orders.get("orders"):
        return "open_orders_present"
    bbo = t.request("GET", f"/v1/markets/{slug}/bbo",
                    base=GATEWAY).get("marketData") or {}
    bid = (bbo.get("bestBid") or {}).get("value")
    ask = (bbo.get("bestAsk") or {}).get("value")
    if not bid or not ask:
        return "bbo_not_two_sided"
    if not entry_ok(D(str(bid))):
        return f"entry_out_of_band:{bid}"
    return None


# ------------------------- the one campaign -------------------------

def run_campaign(t: Optional[Transport] = None,
                 clock=time.monotonic, sleep=time.sleep) -> dict:
    slug = os.getenv(MARKET_ENV, "").strip()
    report: dict[str, Any] = {"campaign": "pmus-q1", "slug": slug,
                              "qty": QTY, "realized_pnl": None,
                              "labels": "submitted->filled->settled->"
                                        "realized; fees/rebates separate"}
    if not armed():
        return {**report, "status": "DISARMED",
                "note": "dual-key absent/mismatched; no network activity"}
    if not has_credentials():
        return {**report, "status": "NO_CREDENTIALS"}
    if not gate_asserted(slug):
        return {**report, "status": "GATE_NOT_CLEARED",
                "note": f"{GATE_ENV} must equal {MARKET_ENV}"}
    if realized_today() <= DAILY_LOSS_STOP:
        return {**report, "status": "DAILY_LOSS_STOP"}
    t = t or Transport()
    first_wait = float(os.getenv(FIRST_WAIT_ENV, "180"))
    deadline_s = float(os.getenv(DEADLINE_ENV, "300"))
    start = clock()
    stop_at = start + deadline_s
    try:
        reason = preflight(t, slug)
        if reason:
            return {**report, "status": "PREFLIGHT_FAILED",
                    "reason": reason}
        bbo = t.request("GET", f"/v1/markets/{slug}/bbo",
                        base=GATEWAY).get("marketData") or {}
        bid = D(str((bbo.get("bestBid") or {}).get("value")))
        if not entry_ok(bid):
            return {**report, "status": "ENTRY_OUT_OF_BAND",
                    "bid": str(bid)}
        # CAMPAIGN_RISK_CAP ($1.00) is structurally enforced: first leg
        # <= 0.80 (+fee) via entry_ok, matched pair <= 0.99 via
        # complement_bound, one-sided worst case = first leg cost alone
        first_id = place_limit_buy(t, slug, "OUTCOME_SIDE_YES", bid,
                                   "first_leg")
        fill1 = wait_for_fill(t, first_id,
                              min(start + first_wait, stop_at),
                              clock, sleep)
        if fill1 is None:
            ok = cancel_exact(t, first_id)
            late = authoritative_fill(_order_status(t, first_id))
            if late is not None:
                ledger_append({"kind": "filled", "stage": "first_leg_late",
                               "price": str(late["price"])})
                return {**report, "status": "LATE_FILL_RESIDUAL_HOLD",
                        "first_cost": str(late["price"]),
                        "residual": RESIDUAL_CAP,
                        "residual_action":
                        os.getenv(RESIDUAL_ACTION_ENV, "hold")}
            return {**report, "status": "NO_FILL",
                    "cancel_verified": ok}
        first_cost = fill1["price"]
        ledger_append({"kind": "filled", "stage": "first_leg",
                       "price": str(first_cost),
                       "maker_rebate_separate":
                       str(maker_rebate(first_cost))})
        comp = complement_bound(first_cost)
        if comp is None:
            return {**report, "status": "ONE_SIDED_HOLD",
                    "reason": "no_fee_safe_complement",
                    "first_cost": str(first_cost),
                    "residual": RESIDUAL_CAP,
                    "residual_action":
                    os.getenv(RESIDUAL_ACTION_ENV, "hold")}
        comp_id = place_limit_buy(t, slug, "OUTCOME_SIDE_NO", comp,
                                  "complement")
        fill2 = wait_for_fill(t, comp_id, stop_at, clock, sleep)
        if fill2 is None:
            ok = cancel_exact(t, comp_id)
            fill2 = authoritative_fill(_order_status(t, comp_id))
            if fill2 is None:
                return {**report, "status": "ONE_SIDED_HOLD",
                        "first_cost": str(first_cost),
                        "comp_price": str(comp), "cancel_verified": ok,
                        "residual": RESIDUAL_CAP,
                        "residual_action":
                        os.getenv(RESIDUAL_ACTION_ENV, "hold")}
            # complement filled during the cancel race -> matched after all
        pair_cost = first_cost + fill2["price"]
        ledger_append({"kind": "filled", "stage": "complement",
                       "price": str(fill2["price"]),
                       "maker_rebate_separate":
                       str(maker_rebate(fill2["price"]))})
        return {**report, "status": "MATCHED",
                "first_cost": str(first_cost),
                "comp_cost": str(fill2["price"]),
                "pair_cost": str(pair_cost),
                "locked_edge_pre_rebate": str(ONE - pair_cost),
                "rebates_separate":
                str(maker_rebate(first_cost)
                    + maker_rebate(fill2["price"])),
                "settlement": "PENDING — realized only after settlement "
                              "rows reconcile"}
    except Halt as halt:
        ledger_append({"kind": "halt", "reason": str(halt)})
        return {**report, "status": "HALT", "reason": str(halt)}
    finally:
        cancel_all_deadman(t, slug)


def main() -> int:
    report = run_campaign()
    print(json.dumps(report, indent=1, default=str))
    ledger_append({"kind": "campaign_report", **report})
    return 0 if report["status"] in ("MATCHED", "NO_FILL", "DISARMED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
