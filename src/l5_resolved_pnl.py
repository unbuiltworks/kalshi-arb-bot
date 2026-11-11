"""Resolved-market P&L that cannot silently drop the losers.

OWNER 2026-09-10, after the survivorship bug: "resolved-market P&L must
include zero-redemption losers by construction."

THE BUG THIS EXISTS TO MAKE IMPOSSIBLE. September trading P&L was reported
as +$7,125.74 from a set filtered on `redeem_qty > 0`, described as
"settlement-observed markets only". A market lost completely redeems nothing
and therefore has no redemption row, so that filter excluded losers *by
construction* -- 708 of 4,217 resolved markets, 16.8%, every one a total
loss. The corrected figure is -$5,047.88. The sign flipped.

The failure was not arithmetic. Every number was right; the POPULATION was
wrong, and it was wrong in a way that looked like diligence ("observed
only"). Analyst discipline is not a sufficient control for that, which is why
it lives in code with a test rather than in a checklist.

THE RULE. Membership is decided by RESOLUTION, never by redemption:

    a market belongs if it has resolved
    a market that resolved with no redemption contributes  -spend
    a market that redeemed contributes                     redeem - spend

`redeemed` is an OUTCOME. It is never an admission criterion.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Iterable, Mapping, Optional


class SurvivorshipFilterError(ValueError):
    """Raised when the caller hands over a set already filtered on
    redemption. The whole point is that such a set cannot be repaired
    downstream -- the losers are simply not there any more."""


def market_pnl(spend, redeem) -> D:
    """One market's trading P&L. `redeem` None or 0 means a total loss, not
    a missing observation: the position resolved worthless and the cash
    spent is gone."""
    s = D(str(spend or 0))
    r = D(str(redeem or 0))
    return r - s


def is_resolved(market: Mapping[str, Any], now_ts: float,
                settle_buffer_s: float = 600.0) -> bool:
    """Resolution decides membership. `resolution_ts` is the market's own
    clock, not ours, and the buffer only allows the venue time to post the
    redemption -- it never depends on whether one exists."""
    ts = market.get("resolution_ts")
    if ts is None:
        return False
    return float(ts) < float(now_ts) - float(settle_buffer_s)


def resolved_pnl(markets: Iterable[Mapping[str, Any]], now_ts: float,
                 settle_buffer_s: float = 600.0) -> dict:
    """Trading P&L over every RESOLVED market, losers included.

    Each market: {"slug", "spend", "redeem" (0/None allowed),
                  "resolution_ts"}.
    """
    rows = list(markets)
    _refuse_prefiltered(rows)
    spend = D("0"); redeem = D("0"); n = 0; zero = 0
    for m in rows:
        if not is_resolved(m, now_ts, settle_buffer_s):
            continue
        s = D(str(m.get("spend") or 0))
        if s <= 0:
            continue
        r = D(str(m.get("redeem") or 0))
        spend += s; redeem += r; n += 1
        if r <= 0:
            zero += 1
    return {
        "n_resolved": n,
        "n_zero_redemption": zero,
        "spend_usd": str(spend),
        "redeem_usd": str(redeem),
        "trading_pnl_usd": str(redeem - spend),
        "pct_of_spend": (str((redeem - spend) / spend * 100) if spend else None),
        "labels": ("membership by RESOLUTION; zero-redemption counted as "
                   "-spend; redemption is an outcome, never a filter"),
    }


def _refuse_prefiltered(rows: list) -> None:
    """Fail closed on a set that has already had its losers removed.

    A caller that filtered on redemption upstream hands us a population that
    cannot be repaired here -- so the only safe response is to refuse rather
    than to return a confident, wrong number. The tell is a resolved set with
    NO zero-redemption markets at all: real ones always contain some,
    because roughly one market in six resolves worthless.
    """
    resolved = [m for m in rows if m.get("resolution_ts") is not None
                and D(str(m.get("spend") or 0)) > 0]
    if len(resolved) < MIN_ROWS_FOR_SURVIVORSHIP_CHECK:
        return
    zero = sum(1 for m in resolved if D(str(m.get("redeem") or 0)) <= 0)
    if zero == 0:
        raise SurvivorshipFilterError(
            f"{len(resolved)} resolved markets and not one zero-redemption "
            "loser: this set looks pre-filtered on redemption. September's "
            "real rate was 708/4217 = 16.8%. Pass the unfiltered set."
        )


MIN_ROWS_FOR_SURVIVORSHIP_CHECK = 200
