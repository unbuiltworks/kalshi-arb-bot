"""Second, independent settlement-evidence path for markets the account's
activity history can no longer reach.

THE LIVENESS BUG THIS CLOSES (production, every cycle since >= 2026-10-26).
The settlement audit confirms a market by finding its POSITION_RESOLUTION (or
its flat-at-resolution trades) in /v1/portfolio/activities. The venue's
activity history now reaches back only to ~2026-11-04, so the 96 September
markets the ledger still carries can NEVER be confirmed that way: every audit
raises ``activity_walk_short_of_since``, the markets stay PENDING forever,
their -17.26 unsettled floor never clears, and the owner-approved R5FB rung
refuses to arm on ``combined_loss_above_stop``.

THE EVIDENCE RULE. Used ONLY when the activity walk cannot cover the market's
``since`` (exactly the ``activity_walk_short_of_since`` case). A market is
settled here only if ALL of:

  (a) PUBLIC FINAL PRICE. GET {GATEWAY}/v1/markets/{slug}/bbo (keyless)
      answers with ``state`` in PUBLIC_RESOLVED_STATES and a ``settlementPx``
      of EXACTLY 0 or 1 for the long/YES contract. Missing, 0.5, any other
      value, a 404 or any error -> PENDING.
  (b) ZERO VENUE POSITION, NO OPEN ORDERS. The account's CURRENT
      /v1/portfolio/positions (the probe's existing authenticated read, via
      the same bounded GET helper; no new credential path) carries no
      non-zero row for the market in ``positions`` or ``availablePositions``,
      and /v1/orders/open carries no order for it. An unreadable, malformed,
      paginated-and-unfinished or unattributable response -> PENDING.
  (c) COHERENT LEDGER. The probe's own durable ledger carries the market's
      filled quantities and cost basis per side, they agree with the rebuild
      the unsettled floor itself uses (held_inventory_from_ledger) and with
      the ledger's effective quantity (corrections included), no migration
      close may have filled in it, and no side was sold beyond what was
      bought. Anything else -> PENDING.

  realized_derived_usd = sell_proceeds
                         + held_long_qty * px + held_short_qty * (1 - px)
                         - buy_cost

using the LEDGER's net held quantities at settlement, never the venue's
(now-zero) position.

PROVENANCE, AND WHAT THIS IS NOT. The row is written with its own ledger kind
(``realized_derived``) and provenance
``SETTLED_BY_PUBLIC_SETTLEMENT_PX_AND_ZERO_VENUE_POSITION``. It is NOT a
``realized`` row, never sets ``realized_confirmed``, never fills
``realized_usd``, and is therefore absent from every sum labelled
venue-confirmed (``_sum_realized``, the continuation preflight, the offline
tools that read ``kind == "realized"`` as "the venue's own number"). It is
counted in exactly two places, so the market's dollars leave the unsettled
floor and enter the combined budget in the SAME transition (the invariant
``combined_loss`` documents): ``unsettled_slugs`` closes the market on it and
``realized_today`` sums it. Fees are not in the ledger's fill rows and are
not modelled here.

READ-ONLY toward the venue: two GETs that already exist on this path plus one
keyless public GET. No order verb, no transport construction, no credential
read. Never raises: every failure is PENDING with a reason.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Mapping, Optional
from urllib.parse import quote

from src import pmus_settlement_auditor as sa

PROVENANCE = "SETTLED_BY_PUBLIC_SETTLEMENT_PX_AND_ZERO_VENUE_POSITION"
KIND = "realized_derived"
TRIGGER_PREFIX = "activity_walk_short_of_since"
# account-scoped reads are shared across one bounded batch; an EXPIRED market
# cannot gain a position or an order, so a 30 s old read is still evidence
ACCOUNT_READ_TTL_S = 30.0
# a migration close in one of these states never filled
_MIGRATION_NO_FILL_STATES = ("REFUSED_PRECONDITION", "REFUSED_NOT_EXECUTABLE",
                             "SUBMIT_FAILED", "UNFILLED")
_SELL_FIELDS = ("action", "intent", "order_intent", "order_action")
ZERO, ONE = D("0"), D("1")

_cache: dict[str, Any] = {}


def reset_cache() -> None:
    _cache.clear()


def is_short_walk_error(err: Any) -> bool:
    """True exactly for the activity_walk_short_of_since case."""
    return str(err or "").startswith(TRIGGER_PREFIX)


def _dec(v: Any) -> Optional[D]:
    try:
        return sa._amount(v)
    except Exception:                                   # noqa: BLE001
        return None


# ------------------------------------------------------------- (a) public px

def public_settlement(t: Any, slug: str) -> dict:
    """The market's PUBLIC final settlement price, or a reason it is not."""
    out: dict[str, Any] = {"final": False, "px": None, "state": None}
    try:
        resp = t.request("GET", f"/v1/markets/{quote(slug)}/bbo",
                         base=sa.q1.base.GATEWAY)
    except Exception as exc:                            # noqa: BLE001
        return {**out, "reason": f"bbo_error:{type(exc).__name__}:"
                                 f"{getattr(exc, 'code', '')}"}
    md = resp.get("marketData") if isinstance(resp, Mapping) else None
    if not isinstance(md, Mapping):
        return {**out, "reason": "bbo_no_marketData"}
    if md.get("marketSlug") not in (None, slug):
        return {**out, "reason": "bbo_slug_mismatch"}
    state = md.get("state")
    raw = md.get("settlementPx")
    px = _dec(raw)
    out.update({"state": state, "px": str(px) if px is not None else None})
    if state not in sa.PUBLIC_RESOLVED_STATES:
        return {**out, "reason": f"state_not_final:{state}"}
    if px is None:
        return {**out, "reason": "settlementPx_missing"}
    if px != ZERO and px != ONE:
        return {**out, "reason": f"settlementPx_not_binary:{px}"}
    return {**out, "final": True, "px": "1" if px == ONE else "0"}


# ------------------------------------------------- (b) venue position/orders

def _page_unfinished(resp: Mapping[str, Any], rows: Any) -> bool:
    return bool(resp.get("nextCursor")) and not resp.get("eof") and bool(rows)


def _account_reads(t: Any, clock=time.monotonic) -> dict:
    """positions + open orders, shared across one batch (TTL). Raises on an
    unreadable answer; the caller turns that into PENDING."""
    now = clock()
    hit = _cache.get("snap")
    if hit and hit["t"] is t and now - hit["at"] < ACCOUNT_READ_TTL_S:
        return hit["data"]
    get = sa.q1.base._get_with_retry
    positions = get(t, "/v1/portfolio/positions")
    orders = get(t, "/v1/orders/open")
    data = {"positions": positions, "orders": orders}
    _cache["snap"] = {"t": t, "at": now, "data": data}
    return data


def _rows(resp: Mapping[str, Any], key: str) -> Optional[list]:
    rows = resp.get(key)
    if rows is None:
        return []
    if isinstance(rows, Mapping):
        return [{"marketSlug": k, **v} if isinstance(v, Mapping) else None
                for k, v in rows.items()]
    if isinstance(rows, list):
        return list(rows)
    return None


def venue_flat(t: Any, slug: str, clock=time.monotonic) -> dict:
    """Is the account's CURRENT venue position in `slug` zero, with no open
    order there? Anything not established is `flat: False`."""
    out: dict[str, Any] = {"flat": False}
    try:
        reads = _account_reads(t, clock)
    except Exception as exc:                            # noqa: BLE001
        return {**out, "reason": f"account_read_error:{type(exc).__name__}"}
    pos, opn = reads.get("positions"), reads.get("orders")
    if not isinstance(pos, Mapping) or "positions" not in pos:
        return {**out, "reason": "positions_unreadable"}
    for key in ("positions", "availablePositions"):
        rows = _rows(pos, key)
        if rows is None or any(r is None or not isinstance(r, Mapping)
                               for r in rows):
            return {**out, "reason": f"{key}_malformed"}
        if _page_unfinished(pos, rows):
            return {**out, "reason": f"{key}_paginated_unfinished"}
        for r in rows:
            if r.get("marketSlug") != slug:
                continue
            net = sa._net_position(r)
            if net is None:
                return {**out, "reason": f"{key}_unparseable"}
            if net != ZERO:
                return {**out, "reason": f"{key}_nonzero:{net}",
                        "venue_net": str(net)}
    if not isinstance(opn, Mapping):
        return {**out, "reason": "open_orders_unreadable"}
    orows = _rows(opn, "orders")
    if orows is None or any(not isinstance(o, Mapping) for o in orows):
        return {**out, "reason": "open_orders_malformed"}
    if _page_unfinished(opn, orows):
        return {**out, "reason": "open_orders_paginated_unfinished"}
    for o in orows:
        ms = o.get("marketSlug")
        if not ms:
            return {**out, "reason": "open_order_unattributable"}
        if ms == slug:
            return {**out, "reason": "open_order_present"}
    return {"flat": True, "positions_rows": len(_rows(pos, "positions") or []),
            "open_orders": len(orows)}


# ------------------------------------------------------------ (c) the ledger

def _ledger_rows(slug: str) -> list:
    path = sa.q1.state_dir() / "ledger.jsonl"
    if not path.exists():
        return []
    rows = []
    for i, line in enumerate(path.read_text().splitlines()):
        try:
            r = json.loads(line)
        except Exception:                               # noqa: BLE001
            continue
        if isinstance(r, dict) and r.get("slug") == slug:
            rows.append((i, r))
    return rows


def _is_sell(*rows: Mapping[str, Any]) -> bool:
    for r in rows:
        for k in _SELL_FIELDS:
            if "SELL" in str(r.get(k) or "").upper():
                return True
    return False


def ledger_position(slug: str) -> dict:
    """Buys, sells and net held per side from the durable ledger, the same
    two passes held_inventory_from_ledger makes, made sell-aware, and then
    cross-checked against it. `coherent: False` carries the reason."""
    rows = _ledger_rows(slug)
    bad = lambda why: {"coherent": False, "reason": why}   # noqa: E731
    if not rows:
        return bad("no_ledger_rows")
    for _, r in rows:
        if r.get("kind") == "migration_cleanup" and \
                str(r.get("state") or "") not in _MIGRATION_NO_FILL_STATES and \
                r.get("stage") != "preconditions":
            return bad("migration_close_may_have_filled")
    acc = {s: {"buy_qty": ZERO, "buy_cost": ZERO, "sell_qty": ZERO,
               "proceeds": ZERO} for s in ("long", "short")}
    submitted: dict[str, dict] = {}
    filled_ids: set = set()
    events: list = []
    for i, r in rows:
        kind, stage = r.get("kind"), str(r.get("stage", ""))
        oid = str(r.get("order_id") or "")
        if kind == "submitted" and stage.startswith("spring_"):
            submitted[oid] = r
        elif kind == "filled" and stage.startswith("spring_"):
            px, qty = _dec(r.get("price")), _dec(r.get("qty"))
            if px is None or qty is None or qty <= 0:
                continue
            filled_ids.add(oid)
            events.append((str(r.get("side")).upper() != "NO", px, qty,
                           _is_sell(r)))
    for i, r in rows:
        oid = str(r.get("order_id") or "")
        if r.get("kind") != "order_terminal" or oid not in submitted or \
                oid in filled_ids:
            continue
        got = _dec(r.get("filled_qty"))
        px = _dec(r.get("price")) or _dec(submitted[oid].get("price"))
        if got is None or px is None or got <= 0:
            continue
        filled_ids.add(oid)
        events.append((str(submitted[oid].get("side")).upper()
                       != "OUTCOME_SIDE_NO", px, got,
                       _is_sell(r, submitted[oid])))
    if not events:
        return bad("no_filled_quantity")
    for long_, px, qty, sell in events:
        if px < ZERO or px > ONE:
            return bad(f"price_out_of_range:{px}")
        a = acc["long" if long_ else "short"]
        if sell:
            a["sell_qty"] += qty
            a["proceeds"] += px * qty
        else:
            a["buy_qty"] += qty
            a["buy_cost"] += px * qty
    for side, a in acc.items():
        if a["sell_qty"] > a["buy_qty"]:
            return bad(f"sold_more_than_bought:{side}")
    # the SAME rebuild the unsettled floor is priced from; it treats every
    # fill as a buy, so it must equal our gross per side exactly
    try:
        from src import pmus_spring_l5_session as sp
        inv = sp.held_inventory_from_ledger(slug)
    except Exception as exc:                            # noqa: BLE001
        return bad(f"inventory_rebuild_error:{type(exc).__name__}")
    L, S = acc["long"], acc["short"]
    if (inv.up != L["buy_qty"] + L["sell_qty"]
            or inv.dn != S["buy_qty"] + S["sell_qty"]
            or inv.cost_up != L["buy_cost"] + L["proceeds"]
            or inv.cost_dn != S["buy_cost"] + S["proceeds"]):
        return bad("disagrees_with_held_inventory_from_ledger")
    eff = _dec(sa.ledger_qty_total(slug))
    if eff is None or eff != inv.up + inv.dn:
        return bad(f"effective_qty_mismatch:{eff}!={inv.up + inv.dn}")
    return {"coherent": True,
            "held_long_qty": str(L["buy_qty"] - L["sell_qty"]),
            "held_short_qty": str(S["buy_qty"] - S["sell_qty"]),
            "buy_cost_long": str(L["buy_cost"]),
            "buy_cost_short": str(S["buy_cost"]),
            "sell_proceeds": str(L["proceeds"] + S["proceeds"]),
            "cost": str(L["buy_cost"] + S["buy_cost"]),
            "exited_early": bool(L["sell_qty"] or S["sell_qty"])}


def derived_realized(ledger: Mapping[str, Any], px: D) -> D:
    px = D(str(px))
    return (D(ledger["sell_proceeds"])
            + D(ledger["held_long_qty"]) * px
            + D(ledger["held_short_qty"]) * (ONE - px)
            - D(ledger["cost"]))


# -------------------------------------------------------------- the decision

def already_closed(slug: str) -> bool:
    for _, r in _ledger_rows(slug):
        if r.get("kind") in ("realized", KIND):
            return True
    return False


def _append(row: Mapping[str, Any]) -> bool:
    """Idempotent: at most one realized_derived row per market, ever."""
    slug = str(row["slug"])
    if already_closed(slug):
        return False
    path = sa.q1.state_dir() / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(),
                            **row}, separators=(",", ":"), default=str) + "\n")
    return True


def settle_by_public_px(t: Any, slug: str, clock=time.monotonic,
                        write: bool = True) -> dict:
    """Apply the evidence rule to one market. Never raises; anything short of
    all three conditions is `settled: False` with the reason."""
    out: dict[str, Any] = {"market": slug, "settled": False,
                           "provenance": None, "evidence_path": PROVENANCE}
    try:
        if t is None:
            return {**out, "reason": "no_transport"}
        ledger = ledger_position(slug)
        out["ledger"] = ledger
        if not ledger.get("coherent"):
            return {**out, "reason": f"ledger:{ledger.get('reason')}"}
        pub = public_settlement(t, slug)
        out["public"] = pub
        if not pub.get("final"):
            return {**out, "reason": f"public:{pub.get('reason')}"}
        flat = venue_flat(t, slug, clock)
        out["venue"] = flat
        if not flat.get("flat"):
            return {**out, "reason": f"venue:{flat.get('reason')}"}
        realized = derived_realized(ledger, D(pub["px"]))
        row = {"kind": KIND, "spring": True, "slug": slug,
               "settlement_key": f"public_px:{pub['px']}:{pub.get('state')}",
               "provenance": PROVENANCE,
               "realized_derived_usd": str(realized),
               "venue_confirmed": False,
               "settlement_px": pub["px"], "public_state": pub.get("state"),
               "held_long_qty": ledger["held_long_qty"],
               "held_short_qty": ledger["held_short_qty"],
               "cost": ledger["cost"],
               "sell_proceeds": ledger["sell_proceeds"],
               "exited_early": ledger["exited_early"],
               "venue_position": "zero (positions + availablePositions)",
               "open_orders_in_market": 0,
               "labels": ("DERIVED settlement P&L: public final settlementPx x "
                          "ledger net held qty - ledger cost (fees not "
                          "modelled). NOT venue-confirmed; never a `realized` "
                          "row; used only because the account activity "
                          "history no longer reaches this market")}
        written = _append(row) if write else False
        return {**out, "settled": True, "provenance": PROVENANCE,
                "realized_derived_usd": str(realized),
                "settlement_px": pub["px"], "ledger_row_written": written}
    except Exception as exc:                            # noqa: BLE001
        return {**out, "settled": False,
                "reason": f"raised:{type(exc).__name__}:{str(exc)[:80]}"}
