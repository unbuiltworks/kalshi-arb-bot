"""Account-wide PORTFOLIO drawdown limit (owner decision 2026-11-13).

WHY. The lifetime realized-loss ceiling reads the account's realized ledger,
which still carries October settlements booked at an UNKNOWN_COST_UPPER_BOUND
basis: it reported -$49 while the account's buying power moved about -$2
over the same day. The owner chose to bound what the account is actually
worth instead: "Build it, start $336.68, stop at -$15".

WHAT IS MEASURED. A WORST-CASE portfolio value:

    value = buying_power + sum over markets of min(UP qty, DOWN qty)

`buying_power` is the venue's own figure (/v1/account/balances). A matched
pair redeems $1 whichever side wins, so min(UP, DOWN) per market is the
redemption the account is GUARANTEED. An unmatched contract is valued at 0
(the outcome that loses), and any funds the venue holds against a resting
order are not added back. Both choices can only make the value LOWER than
the truth, so the limit can trip early and never late.

THE LIMIT.

    drawdown = anchor - value      refuse when drawdown >= max_drawdown

DEPOSITS AND WITHDRAWALS move the anchor by the same amount. They are NOT
detected here: the owner updates L5_PORTFOLIO_ANCHOR_USD when money moves in
or out. A deposit left unrecorded only makes the limit looser by that
amount; a withdrawal left unrecorded makes it trip early (the safe
direction).

ADDITIVE. Configured, it can only ADD a refusal. Every existing gate,
including the lifetime realized ceiling, still runs. Unset (both variables
empty) it is OFF and nothing changes. Half-configured or unreadable is a
refusal, never "off": a limit someone tried to set must not silently vanish.

BOT-ONLY AND TOTAL-ACCOUNT FIGURES (owner decision 2026-11-25: "separate
bot-only and total-account risk accounting ... Manual trading must not be
misclassified as bot trading losses, but all positions and commitments must
remain subject to portfolio-level protection. Do not simply reset the anchor
or exclude manual exposure from all risk controls.").

OFF unless L5_PORTFOLIO_TOTAL_MAX_DRAWDOWN_USD is set; unset, every figure
and every refusal here is exactly what it was. Set, there are two figures
and two limits, and BOTH bind:

    total drawdown = anchor - value          (everything, manual included)
                     refuse at >= L5_PORTFOLIO_TOTAL_MAX_DRAWDOWN_USD
    bot drawdown   = total drawdown - manual commitment
                     refuse at >= L5_PORTFOLIO_MAX_DRAWDOWN_USD

THE MANUAL COMMITMENT IS MEASURED, never attested as an amount. The owner
attests ORDER IDS (L5_MANUAL_ATTESTED_ORDERS); the venue's own row must say
the order was manual, and its filled quantity x average price + commission
is the cost. It counts only while the account still holds those contracts,
pro rata when it holds fewer, and never for more than the total drawdown.
Every doubt (unreadable, not manual, a sell, a stale reading, no position)
counts 0, which makes the BOT figure LARGER -- the safe direction.

PUBLISHED SO AN UNCHANGED READER ENFORCES BOTH. A reader computes
max_drawdown_usd - drawdown_usd, so `max_drawdown_usd` stays the bot limit
and `drawdown_usd` becomes the EFFECTIVE figure, bot limit minus the
tighter of the two rooms. The two real figures are in the `split` block.

This module stays PURE: it reads environment variables and does arithmetic.
The venue reads live in `l5_manual_commitment`.
"""
from __future__ import annotations

import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

ANCHOR_ENV = "L5_PORTFOLIO_ANCHOR_USD"
MAX_DRAWDOWN_ENV = "L5_PORTFOLIO_MAX_DRAWDOWN_USD"
#: the TOTAL-account limit. Setting it turns the bot/total split on.
TOTAL_MAX_DRAWDOWN_ENV = "L5_PORTFOLIO_TOTAL_MAX_DRAWDOWN_USD"
#: venue order ids the owner attests were placed by hand, comma-separated
MANUAL_ORDERS_ENV = "L5_MANUAL_ATTESTED_ORDERS"

OFF = "OFF"
BOT, TOTAL = "BOT", "TOTAL"
#: a stored manual measurement older than this counts 0
MANUAL_MAX_AGE_S = 180.0
#: how many attested ids are read; the rest are listed as rejected
MANUAL_MAX_ORDERS = 25
LIMIT_PREFIX = "PORTFOLIO_DRAWDOWN_LIMIT"


def _dec(v: Any) -> Optional[D]:
    try:
        d = D(str(v).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def config(env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """{"state": OFF} | {"state": "ON", anchor, max} | {"state": "INVALID", why}."""
    e = os.environ if env is None else env
    a_raw = str(e.get(ANCHOR_ENV) or "").strip()
    m_raw = str(e.get(MAX_DRAWDOWN_ENV) or "").strip()
    if not a_raw and not m_raw:
        return {"state": OFF}
    if not a_raw or not m_raw:
        return {"state": "INVALID", "why": "HALF_CONFIGURED"}
    a, m = _dec(a_raw), _dec(m_raw)
    if a is None or m is None:
        return {"state": "INVALID", "why": "UNREADABLE"}
    if a <= 0 or m <= 0:
        return {"state": "INVALID", "why": "NOT_POSITIVE"}
    return {"state": "ON", "anchor_usd": a, "max_drawdown_usd": m}


def worst_case_value(buying_power: Any,
                     positions: Optional[Mapping[str, Mapping[str, Any]]]
                     ) -> Optional[D]:
    """buying power + guaranteed pair redemption. None when unknowable."""
    bp = _dec(buying_power) if buying_power is not None else None
    if bp is None or positions is None:
        return None
    total = bp
    for _m, sides in positions.items():
        up = _dec((sides or {}).get("UP", 0))
        dn = _dec((sides or {}).get("DOWN", 0))
        if up is None or dn is None:
            return None
        total += max(D(0), min(up, dn))
    return total


# ------------------------------------------------- bot-only vs total account

def split_config(cfg: Optional[Mapping[str, Any]] = None,
                 env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """{"state": OFF} | {"state": "ON", total_max_drawdown_usd} |
    {"state": "INVALID", "why": "TOTAL_LIMIT_INVALID"}.

    ON only when the total limit is set, readable, positive and not smaller
    than the bot limit. Set and anything else is INVALID -- a refusal."""
    e = os.environ if env is None else env
    raw = str(e.get(TOTAL_MAX_DRAWDOWN_ENV) or "").strip()
    if not raw:
        return {"state": OFF}
    c = config(e) if cfg is None else cfg
    bad = {"state": "INVALID", "why": "TOTAL_LIMIT_INVALID"}
    t = _dec(raw)
    if t is None or t <= 0 or c.get("state") != "ON":
        return bad
    if t < c["max_drawdown_usd"]:
        return bad
    return {"state": "ON", "total_max_drawdown_usd": t}


def attested_order_ids(env: Optional[Mapping[str, str]] = None) -> List[str]:
    """The owner's attested order ids, in order, without repeats."""
    e = os.environ if env is None else env
    out: List[str] = []
    for part in str(e.get(MANUAL_ORDERS_ENV) or "").split(","):
        p = part.strip()
        if p and p not in out:
            out.append(p)
    return out


def order_tail(order_id: Any) -> str:
    """The only part of an order id this module ever publishes."""
    return str(order_id or "")[-4:]


def manual_commitment(measurement: Optional[Mapping[str, Any]],
                      positions: Optional[Mapping[str, Mapping[str, Any]]],
                      now: Optional[float]) -> Dict[str, Any]:
    """What the attested manual orders still commit, from STORED readings.

    `measurement` is what `l5_manual_commitment.Reader` stored on the tick:
    {"t": epoch, "orders": [{order_id_tail, market, side, filled, cost_usd,
    state}], "rejected": [{order_id_tail, why}]}. No venue request here.

    An order counts only while the account still holds its contracts:
    cost x min(1, held / filled) against `positions`, the same mapping the
    value is computed from. Orders on one market and side share that
    holding, in order. Anything missing, stale or unreadable counts 0.
    """
    out: Dict[str, Any] = {"manual_commitment_usd": D(0), "manual_orders": [],
                           "rejected": [], "age_s": None, "why": None}
    if not isinstance(measurement, Mapping):
        out["why"] = "MANUAL_NOT_MEASURED"
        return out
    out["rejected"] = [dict(r) for r in (measurement.get("rejected") or ())
                       if isinstance(r, Mapping)]
    try:
        age = float(now) - float(measurement.get("t"))
    except (TypeError, ValueError):
        age = None
    out["age_s"] = None if age is None else round(age, 1)
    stale = age is None or age > MANUAL_MAX_AGE_S or age < -5
    if stale:
        out["why"] = "MANUAL_MEASUREMENT_STALE"
    elif positions is None:
        out["why"] = "POSITIONS_UNREADABLE"
    left: Dict[str, D] = {}
    total = D(0)
    for o in (measurement.get("orders") or ()):
        if not isinstance(o, Mapping):
            continue
        row = {"order_id_tail": order_tail(o.get("order_id_tail")),
               "market": o.get("market"), "side": o.get("side"),
               "filled": str(o.get("filled")),
               "cost_usd": str(o.get("cost_usd")),
               "counted_usd": "0", "state": o.get("state")}
        out["manual_orders"].append(row)
        filled, cost = _dec(o.get("filled")), _dec(o.get("cost_usd"))
        side = str(o.get("side") or "").upper()
        if out["why"] or filled is None or cost is None or filled <= 0 \
                or cost <= 0 or side not in ("UP", "DOWN"):
            continue
        key = "%s|%s" % (o.get("market"), side)
        if key not in left:
            held = _dec(((positions or {}).get(str(o.get("market"))) or {})
                        .get(side, 0))
            left[key] = max(D(0), held) if held is not None else D(0)
        use = min(filled, left[key])
        left[key] -= use
        counted = cost if use >= filled else cost * use / filled
        row["counted_usd"] = str(counted)
        total += counted
    out["manual_commitment_usd"] = total
    return out


def split_figures(total_drawdown: D, manual_commitment_usd: Any,
                  bot_max: D, total_max: D) -> Dict[str, Any]:
    """The two figures, the two rooms, and what a reader must see.

    The manual amount is clamped to [0, max(0, total drawdown)]: it can
    never be negative and never take the bot figure below zero. The
    effective drawdown is the bot limit minus the tighter room, so
    `bot_max - effective` IS min(bot room, total room)."""
    dd = D(str(total_drawdown))
    m = _dec(manual_commitment_usd)
    m = D(0) if m is None else m
    m = min(max(D(0), m), max(D(0), dd))
    bot_dd = dd - m
    bot_room, total_room = bot_max - bot_dd, total_max - dd
    room = min(bot_room, total_room)
    return {"total_drawdown_usd": dd, "total_max_drawdown_usd": total_max,
            "total_room_usd": total_room, "bot_drawdown_usd": bot_dd,
            "bot_max_drawdown_usd": bot_max, "bot_room_usd": bot_room,
            "manual_commitment_usd": m,
            "binding": BOT if bot_room <= total_room else TOTAL,
            "room_usd": room, "effective_drawdown_usd": bot_max - room,
            "bot_tripped": bot_dd >= bot_max,
            "total_tripped": dd >= total_max}


def _resolve(cfg: Optional[Dict[str, Any]],
             split: Optional[Mapping[str, Any]]):
    """(config, split config). An invalid total limit makes the WHOLE
    configuration invalid, so it refuses exactly as any other bad value."""
    c = config() if cfg is None else cfg
    s = split_config(c) if split is None else split
    if s.get("state") not in (OFF, "ON") and c.get("state") in (OFF, "ON"):
        c = {"state": "INVALID", "why": s.get("why", "TOTAL_LIMIT_INVALID")}
    return c, s


def _manual_usd(manual: Any) -> Any:
    if isinstance(manual, Mapping):
        return manual.get("manual_commitment_usd")
    return manual


SPLIT_LABELS = (
    "TOTAL = anchor - worst-case value: everything on the account, manual "
    "included, against the total limit. BOT = TOTAL - manual commitment, "
    "against the bot limit. Both bind; the published drawdown_usd is the "
    "bot limit minus the tighter room, so max_drawdown_usd - drawdown_usd "
    "is what may still be lost. Manual commitment is measured from the "
    "venue's own rows for the order ids in " + MANUAL_ORDERS_ENV + ": "
    "filled quantity x average price + commission, buys only, only when the "
    "venue marks the order manual, only while the contracts are still held "
    "(pro rata if fewer), never more than the total drawdown. Unreadable, "
    "not manual, a sell, or a reading older than "
    + str(int(MANUAL_MAX_AGE_S)) + "s counts 0. AFTER A "
    "MANUAL POSITION SETTLES it counts 0: a manual loss then shows in the "
    "bot figure until the owner corrects " + ANCHOR_ENV + " by the "
    "documented deposit/withdrawal path; a manual win simply returns cash.")


def refusal(value: Optional[D], cfg: Optional[Dict[str, Any]] = None, *,
            split: Optional[Mapping[str, Any]] = None,
            manual: Any = None) -> Optional[str]:
    """None to allow, else the reason.

    `manual` is the measured manual commitment (the `manual_commitment`
    block or a number); it is read only when the split is ON, and None
    counts 0."""
    c, s = _resolve(cfg, split)
    st = c.get("state")
    if st == OFF:
        return None
    if st != "ON":
        return "PORTFOLIO_DRAWDOWN_CONFIG_%s" % c.get("why", "INVALID")
    if value is None:
        return "PORTFOLIO_VALUE_UNAVAILABLE"
    dd = c["anchor_usd"] - D(str(value))
    if s.get("state") == "ON":
        f = split_figures(dd, _manual_usd(manual), c["max_drawdown_usd"],
                          s["total_max_drawdown_usd"])
        if f["bot_tripped"]:
            return ("%s:%s>=%s" % (LIMIT_PREFIX, f["effective_drawdown_usd"],
                                   c["max_drawdown_usd"]))
        if f["total_tripped"]:
            return ("%s:TOTAL:%s>=%s" % (LIMIT_PREFIX, dd,
                                         s["total_max_drawdown_usd"]))
        return None
    if dd >= c["max_drawdown_usd"]:
        return ("PORTFOLIO_DRAWDOWN_LIMIT:%s>=%s"
                % (dd, c["max_drawdown_usd"]))
    return None


def report(value: Optional[D], cfg: Optional[Dict[str, Any]] = None, *,
           split: Optional[Mapping[str, Any]] = None,
           manual: Any = None) -> Dict[str, Any]:
    """Heartbeat block. Reporting only; the gate is `refusal`."""
    c, s = _resolve(cfg, split)
    on = c.get("state") == "ON" and s.get("state") == "ON"
    out: Dict[str, Any] = {"state": c.get("state"),
                           "worst_case_value_usd": (None if value is None
                                                    else str(value))}
    if c.get("state") == "ON":
        out.update(anchor_usd=str(c["anchor_usd"]),
                   max_drawdown_usd=str(c["max_drawdown_usd"]))
        if value is not None:
            dd = c["anchor_usd"] - D(str(value))
            out.update(drawdown_usd=str(dd),
                       room_usd=str(c["max_drawdown_usd"] - dd))
    elif c.get("why"):
        out["why"] = c["why"]
    if on:
        blk: Dict[str, Any] = {
            "state": "ON",
            "total_max_drawdown_usd": str(s["total_max_drawdown_usd"]),
            "bot_max_drawdown_usd": str(c["max_drawdown_usd"])}
        m = manual if isinstance(manual, Mapping) else {}
        if value is not None:
            f = split_figures(c["anchor_usd"] - D(str(value)),
                              _manual_usd(manual), c["max_drawdown_usd"],
                              s["total_max_drawdown_usd"])
            out.update(drawdown_usd=str(f["effective_drawdown_usd"]),
                       room_usd=str(f["room_usd"]))
            for k in ("total_drawdown_usd", "total_room_usd",
                      "bot_drawdown_usd", "bot_room_usd",
                      "manual_commitment_usd"):
                blk[k] = str(f[k])
            blk["binding"] = f["binding"]
        blk["manual_orders"] = [dict(o) for o in
                                (m.get("manual_orders") or ())]
        blk["rejected"] = [dict(r) for r in (m.get("rejected") or ())]
        blk["manual_measurement"] = {
            "age_s": m.get("age_s"), "max_age_s": MANUAL_MAX_AGE_S,
            "counted_zero_because": (m.get("why") if isinstance(
                manual, Mapping) else (None if manual is not None
                                       else "MANUAL_NOT_MEASURED"))}
        blk["labels"] = SPLIT_LABELS
        out["split"] = blk
    out["refusal"] = refusal(value, c, split=s, manual=manual)
    out["labels"] = ("value = buying power + min(UP,DOWN) per market; "
                     "unmatched contracts and resting-order holds count 0. "
                     "Anchor moves only when the owner updates "
                     + ANCHOR_ENV + " for a deposit or withdrawal.")
    if on:
        out["labels"] += (" SPLIT ON: drawdown_usd and room_usd are the "
                          "EFFECTIVE figures binding both the bot and the "
                          "total limit; see `split`.")
    return out
