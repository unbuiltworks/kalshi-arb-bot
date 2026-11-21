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
from src import pmus_fees as _fees

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
    """The coefficient in force NOW, from the one authoritative schedule.

    This was a local `D("0.06")`. PMUS republished the taker coefficient at
    0.0695 effective 12 AM ET 2026-09-17 and the local copy never moved, so
    every fee-safe bound derived from it -- `complement_bound` below, and the
    all-in check in `pair_ok` -- was computed against a taker cost 15.8% too
    small. Understating a cost LOOSENS a risk bound, which is the direction
    that matters. The schedule now lives in `src/pmus_fees.py` with its
    provenance and is effective-dated, so pre-17 analyses stay correct.
    """
    return _fees.taker_fee(p)


def maker_rebate(p: D, *, when=None) -> D:
    """What the venue CREDITS a maker at the point of trade. Positive, and
    never netted against a cost here -- it is reported on its own line.

    `when` exists so evidence recorded under an earlier schedule can be
    priced at the coefficient that applied to it. PMUS re-tiered on
    2026-11-14 and this account is Maker Tier 1, which is credited
    NOTHING, so the present-day answer is zero.
    """
    return _fees.maker_rebate(p, when=when)


def armed() -> bool:
    sid = os.getenv(SID_ENV, "").strip()
    return (os.getenv(LIVE_ENV, "").strip().upper() == "YES" and bool(sid)
            and os.getenv(ARM_ENV, "").strip() == sid)


def has_credentials() -> bool:
    return bool(os.getenv(KEY_ENV, "").strip()) and \
        bool(os.getenv(SECRET_ENV, "").strip())


def gate_asserted(slug: str) -> bool:
    return bool(slug) and os.getenv(GATE_ENV, "").strip() == slug


def sign_path(path: str) -> str:
    """The path the signature covers: the URL path WITHOUT its query string.
    LIVE 15:26Z (OWNER P0 5560183912): every authenticated GET carrying a
    query string (?limit / ?cursor / ?marketSlug ...) answered 401 while
    signed over path+query; no mutation path ever carries a query."""
    return path.split("?", 1)[0]


def signed_headers(method: str, path: str,
                   now_ms: Optional[int] = None) -> dict:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    ts = str(now_ms if now_ms is not None else int(time.time() * 1000))
    seed = base64.b64decode(os.environ[SECRET_ENV].strip())[:32]
    key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    sig = base64.b64encode(
        key.sign(f"{ts}{method}{sign_path(path)}".encode())).decode()
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

def complement_bound(first_cost: D, min_edge: D = MIN_EDGE,
                     tick: Optional[D] = None) -> Optional[D]:
    """Max complement price locking >= min_edge pre-rebate: cost1 + cost2
    <= 1 - min_edge, ON THE VENUE'S GRID, inside the fee-safe band.

    THE GRID IS A PROPERTY OF THE MARKET, NOT OF THIS MODULE. `TICK` was
    applied unconditionally, so on a market whose `orderPriceMinTickSize` is
    0.01 this returned prices like 0.475 -- arithmetically inside the bound
    and NOT A PRICE THE VENUE ACCEPTS. PMUS BTC15m is exactly such a market.

    `tick` is therefore a parameter. Omitting it keeps `TICK` and every
    existing caller byte-identical, which is why the sports path is unchanged;
    a caller that knows its market's grid passes it and gets a quotable price.
    Rounding is still DOWN, so a coarser grid can only make the bound more
    conservative, never looser.
    """
    grid = TICK if tick is None else D(str(tick))
    if grid <= 0 or grid >= ONE:
        return None
    px = (ONE - D(str(first_cost)) - min_edge)
    px = (px / grid).to_integral_value(rounding=ROUND_DOWN) * grid
    if px < MIN_PRICE or px > MAX_PRICE:
        return None
    return px


#: the venue's own per-market grid field
TICK_FIELD = "orderPriceMinTickSize"


def tick_for_market(market: Any) -> Optional[D]:
    """The venue's authoritative grid for one market, or None.

    `market_micro_spec` already treats this field as authoritative and
    records why: PMUS serves `orderPriceMinTickSize = 0.01`, and a hardcoded
    equality against `TICK` once refused every sports market outright. This
    is the same read, exposed so a bound can be computed on the same grid the
    order will have to sit on.
    """
    if not isinstance(market, Mapping):
        return None
    try:
        v = D(str(market.get(TICK_FIELD)))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return v if 0 < v < ONE else None


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


class StaleRefuse(RuntimeError):
    """Local refusal: the intended maker price would cross the live book
    (post-only race observed on order C8N87XC00MVV). Never a venue error."""


TRANSIENT_CODES = {404, 429, 500, 502, 503, 504}
GET_RETRY_MAX = 5
GET_RETRY_SLEEP_S = 1.0
# LIVE 2026-09-07T08:21:02Z: five retries one second apart is 5 s of
# patience, and a venue 429 on /v1/orders/open outlasted it (the gateway's
# rate budget recovers on the order of a minute: the same read cleared 70 s
# later at 08:07). Doubling: 1+2+4+8+16 = 31 s before GET_RETRIES_EXHAUSTED.
# Read-only GETs only; a mutation is still never retried.
GET_RETRY_BACKOFF = 2.0


def _get_with_retry(t: Transport, path: str, base: str = API,
                    sleep=time.sleep) -> Any:
    """Bounded retry for READ-ONLY GETs on transient 404/429/5xx
    (eventual consistency after an accepted order id, rate limiting).
    Mutations are NEVER retried or replayed anywhere in this module."""
    last: Optional[Exception] = None
    for attempt in range(GET_RETRY_MAX):
        try:
            return t.request("GET", path, base=base)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code not in TRANSIENT_CODES:
                raise
            last = exc
            sleep(GET_RETRY_SLEEP_S * (GET_RETRY_BACKOFF ** attempt))
    raise Halt(f"GET_RETRIES_EXHAUSTED:{path}:{last}")


def _order_status(t: Transport, order_id: str, sleep=time.sleep) -> dict:
    resp = _get_with_retry(t, f"/v1/order/{order_id}", sleep=sleep)
    return resp.get("order") or resp


def order_state_str(order: Mapping[str, Any]) -> str:
    return str(order.get("state") or order.get("status")
               or order.get("orderStatus") or "").upper()


def _filled_qty(order: Mapping[str, Any]) -> D:
    for key in ("cumQuantity", "filledQuantity", "cumQty"):
        v = order.get(key)
        if v is not None:
            try:
                return D(str(v))
            except Exception:
                continue
    return D("0")


def terminal_rejection(order: Mapping[str, Any]) -> bool:
    """Authoritative REJECTED with zero fill is a TERMINAL no-fill
    outcome (first live probe: ORDER_STATE_REJECTED, cumQuantity=0,
    avgPx=0.0000) — never a HALT, never retried."""
    return "REJECT" in order_state_str(order) and _filled_qty(order) == 0


def authoritative_fill(order: Mapping[str, Any]) -> Optional[dict]:
    """Filled state from the venue's own order record. Returns None while
    unfilled; raises Halt when the record says filled but the execution
    price cannot be determined — a fill is never assumed or guessed."""
    status = order_state_str(order)
    filled = _filled_qty(order)
    if filled < QTY and "FILLED" not in status:
        return None
    for key in ("avgPx", "avgPrice", "averagePrice", "avgExecutionPrice"):
        v = order.get(key)
        if isinstance(v, Mapping):
            v = v.get("value")
        if v is not None:
            try:
                px = D(str(v))
                # venue serves the long-side price; a NO order's fill is
                # 1 - avgPx in NO terms (docs.polymarket.us, Create Order)
                if (str(order.get("outcomeSide") or "").upper().endswith("NO")
                        or "SHORT" in str(order.get("intent") or "").upper()):
                    px = ONE - px
            except Exception:
                continue
            if px > 0:
                return {"price": px, "qty": filled or D(str(QTY))}
    raise Halt("AMBIGUOUS_FILL_PRICE: order reports filled but no "
               "parseable average execution price")


def fresh_bbo(t: Transport, slug: str, sleep=time.sleep) -> dict:
    md = _get_with_retry(t, f"/v1/markets/{slug}/bbo", base=GATEWAY,
                         sleep=sleep).get("marketData") or {}
    return {"bid": (md.get("bestBid") or {}).get("value"),
            "ask": (md.get("bestAsk") or {}).get("value")}


def would_cross(outcome_side: str, price: D, bbo: Mapping[str, Any]) -> bool:
    """Post-only race guard: a maker BUY that meets/crosses the live
    opposite quote will be rejected by PDI — refuse locally instead.
    BUY YES at p crosses when p >= YES ask; BUY NO at p_no crosses when
    p_no >= NO ask = 1 - YES bid."""
    bid, ask = bbo.get("bid"), bbo.get("ask")
    if outcome_side == "OUTCOME_SIDE_YES":
        return bool(ask) and D(str(price)) >= D(str(ask))
    return bool(bid) and D(str(price)) >= ONE - D(str(bid))


def place_limit_buy(t: Transport, slug: str, outcome_side: str,
                    price: D, stage: str, sleep=time.sleep) -> str:
    """Preview-first, fresh-BBO-guarded, post-only, durable-intent, q=1."""
    # venue convention: price.value is the long-side price; a NO order at
    # NO price X is sent as 1 - X (docs.polymarket.us, Create Order)
    wire_px = (ONE - D(str(price))) if str(outcome_side).upper().endswith("NO") \
        else D(str(price))
    body = {"marketSlug": slug, "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(wire_px)}, "quantity": QTY,
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": True,      # post-only maker
            "outcomeSide": outcome_side,
            "action": "ORDER_ACTION_BUY",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}
    preview = t.request("POST", "/v1/order/preview", body)
    if preview.get("error") or preview.get("warnings"):
        raise Halt(f"PREVIEW_REJECTED:{stage}")
    # fresh BBO check IMMEDIATELY before the mutation: a maker price that
    # meets the live opposite quote is refused locally, never submitted
    if would_cross(outcome_side, price, fresh_bbo(t, slug, sleep=sleep)):
        raise StaleRefuse(f"WOULD_CROSS:{stage}:{price}")
    persist_intent({"stage": stage, "slug": slug, "side": outcome_side,
                    "price": str(price), "qty": QTY,
                    "submitted_utc": datetime.now(timezone.utc).isoformat()})
    resp = t.request("POST", "/v1/orders", body)
    order_id = str(resp.get("id") or resp.get("orderId")
                   or (resp.get("order") or {}).get("id") or "")
    if not order_id:
        raise Halt(f"NO_ORDER_ID:{stage}")
    persist_intent({"stage": stage + ":accepted", "order_id": order_id})
    ledger_append({"kind": "submitted", "stage": stage, "slug": slug,
                   "side": outcome_side, "price": str(price),
                   "order_id": order_id})
    return order_id


def cancel_exact(t: Transport, order_id: str, slug: str,
                 sleep=time.sleep) -> bool:
    t.request("POST", f"/v1/order/{order_id}/cancel",
              {"marketSlug": slug})
    order = _order_status(t, order_id, sleep=sleep)
    status = order_state_str(order)
    return "CANCEL" in status or "REJECT" in status or \
        "FILLED" not in status


def cancel_all_deadman(t: Transport, slug: str) -> None:
    try:
        t.request("POST", "/v1/orders/open/cancel", {"slugs": [slug]})
    except Exception as exc:
        ledger_append({"kind": "error", "stage": "deadman",
                       "err": str(exc)[:120]})


def wait_for_fill(t: Transport, order_id: str, until: float,
                  clock=time.monotonic, sleep=time.sleep) -> dict:
    """-> {"fill": {...}} | {"terminal": "REJECTED_NO_FILL"} |
    {"timeout": True}. GET-only; transient 404/429/5xx retried bounded;
    the mutation is never replayed on status ambiguity."""
    while clock() < until:
        order = _order_status(t, order_id, sleep=sleep)
        if terminal_rejection(order):
            return {"terminal": "REJECTED_NO_FILL"}
        fill = authoritative_fill(order)
        if fill is not None:
            return {"fill": fill}
        sleep(POLL_S)
    return {"timeout": True}


# ------------------------- preflight -------------------------

def _buying_power(bal: Mapping[str, Any]) -> Optional[D]:
    """Current schema: /v1/account/balances returns `balances` as a LIST
    of entries carrying buyingPower; older mapping/top-level shapes are
    still accepted."""
    entries: list = []
    raw = bal.get("balances")
    if isinstance(raw, list):
        entries.extend(raw)
    elif isinstance(raw, Mapping):
        entries.append(raw)
    entries.append(bal)
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        for key in ("buyingPower", "availableBalance", "cashBalance"):
            v = entry.get(key)
            if isinstance(v, Mapping):
                v = v.get("value")
            if v is not None:
                try:
                    return D(str(v))
                except Exception:
                    continue
    return None


def _position_qty(pos: Mapping[str, Any], slug: str) -> D:
    """Current schema: `positions` is a MAP keyed by market slug; list
    shape still accepted."""
    raw = pos.get("positions")
    rows: list = []
    if isinstance(raw, Mapping):
        entry = raw.get(slug)
        if entry is not None:
            rows.append(entry if isinstance(entry, Mapping)
                        else {"quantity": entry})
    elif isinstance(raw, list):
        rows.extend(p for p in raw if p.get("marketSlug") == slug)
    for p in rows:
        for key in ("quantity", "positionQuantity", "qty"):
            v = p.get(key)
            if v is not None:
                try:
                    return D(str(v))
                except Exception:
                    continue
    return D("0")


def preflight(t: Transport, slug: str, sleep=time.sleep) -> Optional[str]:
    """Read-auth preflight; returns a failure reason or None when clean.
    GET-only with bounded transient retry; no mutation here ever."""
    bal = _get_with_retry(t, "/v1/account/balances", sleep=sleep)
    power = _buying_power(bal)
    if power is None or power < MIN_BUYING_POWER:
        return f"buying_power_unverified_or_low:{power}"
    pos = _get_with_retry(t, "/v1/portfolio/positions", sleep=sleep)
    if _position_qty(pos, slug) != 0:
        return "existing_position_in_target_market"
    open_orders = _get_with_retry(t, "/v1/orders/open", sleep=sleep)
    raw_open = open_orders.get("orders")
    if raw_open and (list(raw_open) if not isinstance(raw_open, Mapping)
                     else list(raw_open.values())):
        return "open_orders_present"
    bbo = fresh_bbo(t, slug, sleep=sleep)
    if not bbo.get("bid") or not bbo.get("ask"):
        return "bbo_not_two_sided"
    if not entry_ok(D(str(bbo["bid"]))):
        return f"entry_out_of_band:{bbo['bid']}"
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
        reason = preflight(t, slug, sleep=sleep)
        if reason:
            return {**report, "status": "PREFLIGHT_FAILED",
                    "reason": reason}
        bbo = fresh_bbo(t, slug, sleep=sleep)
        bid = D(str(bbo["bid"]))
        if not entry_ok(bid):
            return {**report, "status": "ENTRY_OUT_OF_BAND",
                    "bid": str(bid)}
        # CAMPAIGN_RISK_CAP ($1.00) is structurally enforced: first leg
        # <= 0.80 (+fee) via entry_ok, matched pair <= 0.99 via
        # complement_bound, one-sided worst case = first leg cost alone
        first_id = place_limit_buy(t, slug, "OUTCOME_SIDE_YES", bid,
                                   "first_leg", sleep=sleep)
        out1 = wait_for_fill(t, first_id,
                             min(start + first_wait, stop_at),
                             clock, sleep)
        if out1.get("terminal"):
            return {**report, "status": "REJECTED_NO_FILL",
                    "stage": "first_leg", "order_id": first_id,
                    "note": "authoritative REJECTED with zero fill — "
                            "terminal, not a halt; mutation never replayed"}
        if out1.get("timeout"):
            ok = cancel_exact(t, first_id, slug, sleep=sleep)
            post = _order_status(t, first_id, sleep=sleep)
            if terminal_rejection(post):
                return {**report, "status": "REJECTED_NO_FILL",
                        "stage": "first_leg", "order_id": first_id}
            late = authoritative_fill(post)
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
        first_cost = out1["fill"]["price"]
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
        try:
            comp_id = place_limit_buy(t, slug, "OUTCOME_SIDE_NO", comp,
                                      "complement", sleep=sleep)
        except StaleRefuse:
            return {**report, "status": "ONE_SIDED_HOLD",
                    "reason": "complement_would_cross_refused_locally",
                    "first_cost": str(first_cost),
                    "residual": RESIDUAL_CAP,
                    "residual_action":
                    os.getenv(RESIDUAL_ACTION_ENV, "hold")}
        out2 = wait_for_fill(t, comp_id, stop_at, clock, sleep)
        fill2 = out2.get("fill")
        if fill2 is None:
            if out2.get("terminal"):
                return {**report, "status": "ONE_SIDED_HOLD",
                        "reason": "complement_rejected_no_fill",
                        "first_cost": str(first_cost),
                        "residual": RESIDUAL_CAP,
                        "residual_action":
                        os.getenv(RESIDUAL_ACTION_ENV, "hold")}
            ok = cancel_exact(t, comp_id, slug, sleep=sleep)
            fill2 = authoritative_fill(_order_status(t, comp_id,
                                                     sleep=sleep))
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
    except StaleRefuse as stale:
        ledger_append({"kind": "stale_refused", "reason": str(stale)})
        return {**report, "status": "STALE_PRICE_REFUSED",
                "reason": str(stale),
                "note": "maker price met the live opposite quote; refused "
                        "locally before submission (post-only race guard)"}
    except Halt as halt:
        ledger_append({"kind": "halt", "reason": str(halt)})
        return {**report, "status": "HALT", "reason": str(halt)}
    finally:
        cancel_all_deadman(t, slug)


def main() -> int:
    report = run_campaign()
    print(json.dumps(report, indent=1, default=str))
    ledger_append({"kind": "campaign_report", **report})
    return 0 if report["status"] in ("MATCHED", "NO_FILL", "DISARMED",
                                     "REJECTED_NO_FILL",
                                     "STALE_PRICE_REFUSED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
