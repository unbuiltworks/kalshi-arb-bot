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
    bot drawdown   = total drawdown - manual adjustment
                     refuse at >= L5_PORTFOLIO_MAX_DRAWDOWN_USD

THE MANUAL ADJUSTMENT IS MEASURED, never attested as an amount. The owner
attests ORDER IDS (L5_MANUAL_ATTESTED_ORDERS); the venue's own row must say
the order was manual, and its filled quantity x average price + commission
is the cost.

    manual adjustment = open commitment + settled net (signed)

Per market the attested orders are signed cash: a buy is +cost, a closing
sell is -proceeds. OPEN: the net cash still out while the account holds
the contracts; it never takes the bot figure below zero. CLOSED (bought and
sold by hand) or SETTLED (cost - payout from the ledger's retired row): the
result stays manual without the anchor being moved. A manual LOSS stays
off the bot figure. A manual GAIN is added back to it -- its cash would
otherwise hide the same amount of bot losses -- so the bot figure can
exceed the total. A manual result that cannot be measured credits no loss
and is counted as a possible full win. Every doubt therefore makes the BOT
figure LARGER, the safe direction; the TOTAL figure is never adjusted.

MANUAL ORDERS FOUND BY THE VENUE'S OWN INDICATOR (owner 2026-11-26:
"prepare the existing bot-versus-total account risk split so future manual
trades do not unnecessarily interrupt L5"). OFF by default. With
L5_MANUAL_BY_VENUE_INDICATOR=1 AND L5_MANUAL_BY_VENUE_INDICATOR_SINCE_UTC
set to an ISO instant, the ids need not be attested: the account's own
activity feed is walked (bounded pages per pass) and every trade row whose
OWN execution's order says MANUAL_ORDER_INDICATOR_MANUAL, at or after that
instant, adds its order id to the attested ones. Each id is then read and
verified exactly as an attested one (the venue's order row must say
MANUAL) and costed by the same arithmetic. A row whose indicator cannot be
read is NOT manual: it stays on the bot figure. A row that says MANUAL but
cannot be costed (no order id, no time) is unclassified: a possible full
win. Until one discovery walk has completed, or when the last one is too
old, new exposure is refused, as for an attested id not read yet.
Half-configured is a refusal. It changes nothing unless the split is ON,
never adjusts the TOTAL figure and changes no limit. Move the SINCE
instant only together with the anchor.

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

#: "1" identifies manual orders from the venue's own indicator, no id list
AUTO_MANUAL_ENV = "L5_MANUAL_BY_VENUE_INDICATOR"
#: required with it: only manual trades at or after this ISO instant count
AUTO_MANUAL_SINCE_ENV = "L5_MANUAL_BY_VENUE_INDICATOR_SINCE_UTC"

OFF = "OFF"
BOT, TOTAL = "BOT", "TOTAL"
#: a stored manual measurement older than this counts 0
MANUAL_MAX_AGE_S = 180.0
#: how many attested ids are read; the rest are listed as rejected
MANUAL_MAX_ORDERS = 25
#: with the venue indicator on: attested + found ids read in all
AUTO_MANUAL_MAX_ORDERS = 100
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


def iso_epoch(raw: Any) -> Optional[float]:
    """An ISO-8601 instant WITH a zone (or Z) as epoch seconds, else None."""
    import datetime as _dt
    s = str(raw or "").strip().replace("Z", "+00:00").replace("z", "+00:00")
    try:
        t = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return None if t.tzinfo is None else t.timestamp()


def auto_manual_config(env: Optional[Mapping[str, str]] = None
                       ) -> Dict[str, Any]:
    """{"state": OFF} | {"state": "ON", "since_epoch", "since_utc"} |
    {"state": "INVALID", "why"}.

    OFF when neither variable is set, or the switch is "0". ON only for
    exactly "1" with a readable SINCE instant (zone required). Anything
    else is INVALID, which refuses while the split is ON."""
    e = os.environ if env is None else env
    raw = str(e.get(AUTO_MANUAL_ENV) or "").strip()
    since = str(e.get(AUTO_MANUAL_SINCE_ENV) or "").strip()
    if raw == "0" or (not raw and not since):
        return {"state": OFF}
    if raw and raw != "1":
        return {"state": "INVALID", "why": "AUTO_MANUAL_SWITCH_UNREADABLE"}
    if not raw or not since:
        return {"state": "INVALID", "why": "AUTO_MANUAL_HALF_CONFIGURED"}
    t = iso_epoch(since)
    if t is None:
        return {"state": "INVALID", "why": "AUTO_MANUAL_SINCE_UNREADABLE"}
    return {"state": "ON", "since_epoch": t, "since_utc": since}


def order_tail(order_id: Any) -> str:
    """The only part of an order id this module ever publishes."""
    return str(order_id or "")[-4:]


#: retired-row labels that say an automated strategy made the position
AUTOMATED_ORIGINS = ("BTC15M", "BTC5M", "SPORTS")


def _settled_payout(row: Any, side: str, remaining: D,
                    all_terminal: bool) -> Dict[str, Any]:
    """{"per_contract": D} from one `retired_markets` row, or {"why"}.

    Reads `yes_value` (the venue's binary settlement the ledger retired the
    market at: an UP contract paid it, a DOWN contract 1 - it), `up_qty` /
    `dn_qty` (what the ledger held when it settled) and `origin`."""
    if not all_terminal:
        return {"why": "ORDER_NEVER_READ_TERMINAL"}
    if not isinstance(row, Mapping):
        return {"why": "RETIRED_ROW_UNREADABLE"}
    if row.get("origin") in AUTOMATED_ORIGINS:
        return {"why": "RETIRED_ROW_LABELLED_%s" % row.get("origin")}
    v = _dec(row.get("yes_value")) if row.get("yes_value") is not None \
        else None
    if v is None or v not in (D(0), D(1)):
        return {"why": "RETIRED_ROW_HAS_NO_PAYOUT"}
    qty = row.get("up_qty" if side == "UP" else "dn_qty")
    qty = _dec(qty) if qty is not None else None
    if qty is None:
        return {"why": "RETIRED_ROW_QUANTITY_UNREADABLE"}
    if qty != remaining:
        return {"why": "SETTLED_QUANTITY_NOT_ONLY_MANUAL"}
    return {"per_contract": v if side == "UP" else D(1) - v}


def manual_commitment(measurement: Optional[Mapping[str, Any]],
                      positions: Optional[Mapping[str, Mapping[str, Any]]],
                      now: Optional[float],
                      retired: Optional[Mapping[str, Any]] = None
                      ) -> Dict[str, Any]:
    """What the attested manual orders account for, from STORED readings.

    `measurement` is what `l5_manual_commitment.Reader` stored on the tick
    (its docstring has the shape); `positions` is the mapping the value is
    computed from; `retired` is the ledger's `retired_markets`. No venue
    request here.

    SIGNED CASH PER MARKET. Every attested manual order is a cash flow:

        BUY  -> +cost      and +filled on its outcome side
        SELL -> -proceeds  and -filled on its outcome side (a closing sell)

        cash_out  = buys' cost - sells' proceeds - $1 x pairs
        remaining = what the orders leave on each side; an UP and a DOWN
                    remainder in one market are a PAIR, worth exactly $1
                    whichever way it settles (and already counted at $1 by
                    the value), so pairs are netted out at $1 first.

    Then, per market, `manual_net` (positive = manual money out or lost,
    negative = a manual gain):

      CLOSED       nothing remains            -> cash_out, exactly
      OPEN_HELD    the account holds at least the remainder, market not
                   retired                    -> cash_out (the remainder is
                   valued 0, as the total figure values it)
      SETTLED      market retired, the retired row measurable (every order
                   read terminal, binary `yes_value`, not an automated
                   strategy's row, its quantity on that side EXACTLY the
                   remainder)                 -> cash_out - payout
      UNMEASURED   anything else: fewer held than the remainder, position
                   gone with no measurable retired row, more sold than
                   bought, an unclassified manual row in the market.
                   NO manual loss is credited AND the largest possible
                   manual gain is charged: every remaining (and every
                   unclassified) contract paying $1, i.e.
                   min(0, cash_out - remainder). The bot figure can then
                   only be OVERSTATED.

    `manual_commitment_usd` is the sum of the positive OPEN_HELD nets (a
    commitment still on the table); everything else, signed, is
    `manual_settled_net_usd`.

    A STALE OR ABSENT TICK READING (older than MANUAL_MAX_AGE_S), or
    unreadable positions: every positive contribution counts 0; every
    negative one -- measured gains and worst cases, from the stored
    readings and the positions and retired rows read now -- still counts.
    A stale reading can therefore never make the bot figure smaller.
    """
    out: Dict[str, Any] = {
        "manual_commitment_usd": D(0), "manual_settled_net_usd": D(0),
        "manual_orders": [], "manual_markets": [], "rejected": [],
        "unmeasured": [], "age_s": None, "why": None}
    if not isinstance(measurement, Mapping):
        out["why"] = "MANUAL_NOT_MEASURED"
        return out
    out["rejected"] = [dict(r) for r in (measurement.get("rejected") or ())
                       if isinstance(r, Mapping)]
    if isinstance(measurement.get("discovery"), Mapping):
        out["discovery"] = dict(measurement["discovery"])
    try:
        age = float(now) - float(measurement.get("t"))
    except (TypeError, ValueError):
        age = None
    out["age_s"] = None if age is None else round(age, 1)
    if age is None or age > MANUAL_MAX_AGE_S or age < -5:
        out["why"] = "MANUAL_MEASUREMENT_STALE"
    elif positions is None:
        out["why"] = "POSITIONS_UNREADABLE"
    rows = retired if isinstance(retired, Mapping) else {}
    mk: Dict[str, Dict[str, Any]] = {}

    def slot(market: Any) -> Dict[str, Any]:
        return mk.setdefault(str(market), {
            "cash": D(0), "qty": {"UP": D(0), "DOWN": D(0)},
            "terminal": True, "extra": D(0), "why": None, "tails": []})

    for o in (measurement.get("orders") or ()):
        if not isinstance(o, Mapping):
            continue
        act = str(o.get("action") or "BUY").upper()
        side = str(o.get("side") or "").upper()
        row = {"order_id_tail": order_tail(o.get("order_id_tail")),
               "market": o.get("market"), "side": o.get("side"),
               "action": act, "filled": str(o.get("filled")),
               "state": o.get("state"),
               "settled": str(o.get("market")) in rows}
        key = "cost_usd" if act == "BUY" else "proceeds_usd"
        row[key] = str(o.get(key))
        out["manual_orders"].append(row)
        filled, cash = _dec(o.get("filled")), _dec(o.get(key))
        m = slot(o.get("market"))
        m["tails"].append(row["order_id_tail"])
        m["terminal"] = m["terminal"] and bool(o.get("terminal"))
        if filled is None or cash is None or filled < 0 or cash < 0 \
                or side not in ("UP", "DOWN") or act not in ("BUY", "SELL"):
            m["why"] = m["why"] or "ORDER_READING_UNUSABLE"
            m["extra"] += max(D(0), filled) if filled is not None else D(0)
            continue
        sign = D(1) if act == "BUY" else D(-1)
        m["cash"] += sign * cash
        m["qty"][side] += sign * filled
    for u in (measurement.get("unclassified") or ()):
        if not isinstance(u, Mapping):
            continue
        tail = order_tail(u.get("order_id_tail"))
        q = _dec(u.get("filled"))
        q = max(D(0), q) if q is not None else D(0)
        out["manual_orders"].append({
            "order_id_tail": tail, "market": u.get("market"),
            "filled": str(u.get("filled")), "state": u.get("state"),
            "not_counted_why": u.get("why")})
        if u.get("market") is None:
            # no market to charge it to: its whole quantity, at $1
            out["unmeasured"].append({
                "order_id_tail": tail, "market": None, "why": u.get("why"),
                "counted_as_possible_win_usd": str(-q)})
            out["manual_settled_net_usd"] -= q
            continue
        m = slot(u.get("market"))
        m["tails"].append(tail)
        m["why"] = m["why"] or str(u.get("why"))
        m["extra"] += q
    open_total = D(0)
    for market in mk:
        m = mk[market]
        up, dn = m["qty"]["UP"], m["qty"]["DOWN"]
        pair = max(D(0), min(up, dn))
        cash = m["cash"] - pair
        up, dn = up - pair, dn - pair
        side = "UP" if up != 0 else "DOWN" if dn != 0 else None
        rem = up if up != 0 else dn
        line: Dict[str, Any] = {
            "market": market, "order_id_tails": list(m["tails"]),
            "net_cash_out_usd": str(cash), "paired_qty": str(pair),
            "remaining_side": side, "remaining_qty": str(rem),
            "settled": market in rows}
        why, net, status = m["why"], cash, None
        if why is None and (up < 0 or dn < 0 or (up != 0 and dn != 0)):
            why = "MORE_SOLD_THAN_ATTESTED_BOUGHT"
        if why is None and rem == 0:
            status = "CLOSED"
        elif why is None and market in rows:
            got = _settled_payout(rows.get(market), side, rem, m["terminal"])
            if "per_contract" in got:
                payout = rem * got["per_contract"]
                line["payout_usd"] = str(payout)
                net, status = cash - payout, "SETTLED"
            else:
                why = got["why"]
        elif why is None:
            held = None if positions is None else _dec(
                ((positions or {}).get(market) or {}).get(side, 0))
            line["held_qty"] = None if held is None else str(held)
            if held is None:
                why = "POSITIONS_UNREADABLE"
            elif held >= rem:
                status = "OPEN_HELD"
            elif held > 0:
                why = "HOLDS_LESS_THAN_ATTESTED_REMAINDER"
            else:
                why = "POSITION_NOT_HELD_AND_MARKET_NOT_RETIRED"
        if why is not None:
            status = "UNMEASURED"
            net = min(D(0), cash - max(D(0), up) - max(D(0), dn)
                      - m["extra"])
            out["unmeasured"].append({
                "market": market, "order_id_tails": list(m["tails"]),
                "why": why, "counted_as_possible_win_usd": str(net)})
            line["why"] = why
        if out["why"] and net > 0:
            # a stale reading never makes the bot figure smaller
            line["not_counted_why"] = out["why"]
            net = D(0)
        line["status"] = status
        line["manual_net_usd"] = str(net)
        out["manual_markets"].append(line)
        if status == "OPEN_HELD" and net > 0:
            open_total += net
        else:
            out["manual_settled_net_usd"] += net
    out["manual_commitment_usd"] = open_total
    return out


def split_figures(total_drawdown: D, manual_commitment_usd: Any,
                  bot_max: D, total_max: D, *,
                  settled_net_usd: Any = None) -> Dict[str, Any]:
    """The two figures, the two rooms, and what a reader must see.

        adjustment = open commitment + settled net (signed)
        bot drawdown = total drawdown - adjustment

    The OPEN part is never negative and never takes the bot figure below
    zero. The SETTLED part is signed: a manual loss (positive) comes off
    the bot figure, but never below zero either; a manual win (negative)
    is added to it and is NOT capped, so the bot figure can exceed the
    total. The effective drawdown is the bot limit minus the tighter room,
    so `bot_max - effective` IS min(bot room, total room)."""
    dd = D(str(total_drawdown))
    m = _dec(manual_commitment_usd)
    m = max(D(0), D(0) if m is None else m)
    st = _dec(settled_net_usd) if settled_net_usd is not None else D(0)
    st = D(0) if st is None else st
    if st > 0:
        st = min(st, max(D(0), dd))
    m = min(m, max(D(0), dd - st))
    adj = m + st
    bot_dd = dd - adj
    bot_room, total_room = bot_max - bot_dd, total_max - dd
    room = min(bot_room, total_room)
    return {"total_drawdown_usd": dd, "total_max_drawdown_usd": total_max,
            "total_room_usd": total_room, "bot_drawdown_usd": bot_dd,
            "bot_max_drawdown_usd": bot_max, "bot_room_usd": bot_room,
            "manual_commitment_usd": m,
            "manual_open_commitment_usd": m,
            "manual_settled_net_usd": st,
            "manual_adjustment_usd": adj,
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


def _manual_parts(manual: Any):
    """(open commitment, settled net) from the block or a bare number."""
    if isinstance(manual, Mapping):
        return (manual.get("manual_commitment_usd"),
                manual.get("manual_settled_net_usd"))
    return manual, None


def _figures(dd: D, manual: Any, c: Mapping[str, Any],
             s: Mapping[str, Any]) -> Dict[str, Any]:
    op, st = _manual_parts(manual)
    return split_figures(dd, op, c["max_drawdown_usd"],
                         s["total_max_drawdown_usd"], settled_net_usd=st)


SPLIT_LABELS = (
    "TOTAL = anchor - worst-case value: everything on the account, manual "
    "included, against the total limit. BOT = TOTAL - manual adjustment, "
    "against the bot limit. Both bind; the published drawdown_usd is the "
    "bot limit minus the tighter room, so max_drawdown_usd - drawdown_usd "
    "is what may still be lost. manual adjustment = open commitment + "
    "settled net, measured from the venue's own rows for the order ids in "
    + MANUAL_ORDERS_ENV + " (only rows the venue marks manual): a buy is "
    "+cost, a closing sell is -proceeds, netted per market. OPEN_HELD: "
    "the net cash still out while the contracts are held; it never takes "
    "the bot figure below 0. CLOSED (bought and sold) and SETTLED (cost - "
    "payout from the ledger's retired row, whose quantity must equal the "
    "attested remainder exactly) are signed: a manual loss stays off the "
    "bot figure; a manual GAIN is added to it, so the bot figure can "
    "exceed the total and the gain cannot hide bot losses. `unmeasured` "
    "lists every manual result that could NOT be measured: no loss is "
    "credited and each is counted as a possible full win ($1 a contract), "
    "which can only OVERSTATE the bot figure -- attest the missing order "
    "(for example the sell), or remove the ids and correct " + ANCHOR_ENV
    + " by the documented deposit/withdrawal path. With " + AUTO_MANUAL_ENV
    + "=1 ids are also found from the activity feed's own manual "
    "indicator (trades at or after " + AUTO_MANUAL_SINCE_ENV + "); a row "
    "whose indicator is unreadable is bot. Not-manual or "
    "unreadable orders count 0. A reading older than "
    + str(int(MANUAL_MAX_AGE_S)) + "s: nothing is taken off the bot "
    "figure; gains and worst cases still count.")


#: an attested id the venue says is not manual is simply not manual money
REJECTED_HARMLESS = ("NOT_MANUAL_PER_VENUE",)


def manual_unaccounted(manual: Any,
                       env: Optional[Mapping[str, str]] = None
                       ) -> Optional[str]:
    """A refusal when the owner attested manual orders and one of them has
    NOT been accounted for, else None.

    WHY THIS REFUSES INSTEAD OF COUNTING 0. An attested order that was
    never read (first moments after a restart, a venue read that fails, an
    id the venue does not find) may be a manual GAIN. Leaving it out would
    make the bot figure SMALLER than the truth and so loosen the bot
    limit. Nothing can be assumed about its size, so new exposure is
    refused until every attested id is either measured or stated by the
    venue itself not to be manual. The reason does not start with the
    limit prefix, so every reader treats it as the publisher refusing.
    With no attested ids, and the venue-indicator mode off, there is
    nothing to account for. That mode half-configured or unreadable is a
    refusal: a split someone tried to configure must not silently change."""
    auto = auto_manual_config(env)
    if auto.get("state") not in (OFF, "ON"):
        return "PORTFOLIO_MANUAL_%s" % auto.get("why")
    if not attested_order_ids(env) and auto.get("state") != "ON":
        return None
    if manual is None:
        return "PORTFOLIO_MANUAL_ORDERS_NOT_MEASURED"
    if not isinstance(manual, Mapping):
        return None            # a caller's own figure: it accounts for it
    if manual.get("why") == "MANUAL_NOT_MEASURED":
        return "PORTFOLIO_MANUAL_ORDERS_NOT_MEASURED"
    for r in (manual.get("rejected") or ()):
        why = str((r or {}).get("why") if isinstance(r, Mapping) else r)
        if why not in REJECTED_HARMLESS:
            return "PORTFOLIO_MANUAL_ORDER_UNREADABLE:%s" % why[:40]
    return None


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
        gap = manual_unaccounted(manual)
        if gap is not None:
            return gap
        f = _figures(dd, manual, c, s)
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
            f = _figures(c["anchor_usd"] - D(str(value)), manual, c, s)
            out.update(drawdown_usd=str(f["effective_drawdown_usd"]),
                       room_usd=str(f["room_usd"]))
            for k in ("total_drawdown_usd", "total_room_usd",
                      "bot_drawdown_usd", "bot_room_usd",
                      "manual_commitment_usd",
                      "manual_open_commitment_usd",
                      "manual_settled_net_usd", "manual_adjustment_usd"):
                blk[k] = str(f[k])
            blk["binding"] = f["binding"]
        blk["manual_orders"] = [dict(o) for o in
                                (m.get("manual_orders") or ())]
        blk["rejected"] = [dict(r) for r in (m.get("rejected") or ())]
        blk["manual_markets"] = [dict(r) for r in
                                 (m.get("manual_markets") or ())]
        #: PROMINENT: each of these is counted as a possible full win
        blk["unmeasured"] = [dict(r) for r in (m.get("unmeasured") or ())]
        blk["manual_measurement"] = {
            "age_s": m.get("age_s"), "max_age_s": MANUAL_MAX_AGE_S,
            "counted_zero_because": (m.get("why") if isinstance(
                manual, Mapping) else (None if manual is not None
                                       else "MANUAL_NOT_MEASURED"))}
        auto = auto_manual_config()
        blk["manual_identification"] = {
            "attested_ids": len(attested_order_ids()),
            "by_venue_indicator": auto.get("state"),
            "since_utc": auto.get("since_utc"), "why": auto.get("why"),
            "discovery": (dict(m["discovery"]) if isinstance(
                m.get("discovery"), Mapping) else None)}
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
