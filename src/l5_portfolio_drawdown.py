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
"""
from __future__ import annotations

import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Mapping, Optional

ANCHOR_ENV = "L5_PORTFOLIO_ANCHOR_USD"
MAX_DRAWDOWN_ENV = "L5_PORTFOLIO_MAX_DRAWDOWN_USD"

OFF = "OFF"


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


def refusal(value: Optional[D], cfg: Optional[Dict[str, Any]] = None
            ) -> Optional[str]:
    """None to allow, else the reason."""
    c = config() if cfg is None else cfg
    st = c.get("state")
    if st == OFF:
        return None
    if st != "ON":
        return "PORTFOLIO_DRAWDOWN_CONFIG_%s" % c.get("why", "INVALID")
    if value is None:
        return "PORTFOLIO_VALUE_UNAVAILABLE"
    dd = c["anchor_usd"] - D(str(value))
    if dd >= c["max_drawdown_usd"]:
        return ("PORTFOLIO_DRAWDOWN_LIMIT:%s>=%s"
                % (dd, c["max_drawdown_usd"]))
    return None


def report(value: Optional[D], cfg: Optional[Dict[str, Any]] = None
           ) -> Dict[str, Any]:
    """Heartbeat block. Reporting only; the gate is `refusal`."""
    c = config() if cfg is None else cfg
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
    out["refusal"] = refusal(value, c)
    out["labels"] = ("value = buying power + min(UP,DOWN) per market; "
                     "unmatched contracts and resting-order holds count 0. "
                     "Anchor moves only when the owner updates "
                     + ANCHOR_ENV + " for a deposit or withdrawal.")
    return out
