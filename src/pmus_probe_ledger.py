"""Three separate ledgers per probe. No estimated reward ever fills a hole.

OWNER: "every probe reports three completely separate ledgers -- Trading
(matched pair + residual - fees), Maker rebate (actually credited), Liquidity
reward (actually credited). No estimated rewards filling holes."

And, for planning: "I would currently value the economics as trading P&L +
confirmed maker rebates only. Treat liquidity-program rewards as $0 until the
account actually receives them. A displayed program or theoretical reward never
enters expected/realized P&L."

WHY THIS MODULE EXISTS RATHER THAN A SUM

L5's economics were trading -$2,829 against incentives +$71,552 -- about 104%
of the profit came from the incentive layer. That makes it tempting to carry an
incentive number alongside trading and let the two net off. On PMUS our record
is $0.42 of credited maker rebate and $0.00 of credited liquidity reward, on
markets where programs ARE published. The payer exists and has never paid us.

So the one number that must never exist here is a total containing a reward we
have not been paid. planning_total() adds trading and CREDITED maker rebate and
nothing else; accrued-but-not-credited is carried in its own field, visible and
excluded, and a test asserts that moving it into the total turns the suite red.

THE THREE LEDGERS NEVER MERGE. three_ledgers() returns them separately with
their own provenance. A caller that wants one figure has to choose which rule
it is using, and say so.
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

TRADING = "trading"
MAKER_REBATE = "maker_rebate_credited"
LIQUIDITY_REWARD = "liquidity_reward_credited"

PLANNING_RULE = ("trading P&L + CREDITED maker rebates only; liquidity-program "
                 "rewards enter at $0.00 until the account actually receives "
                 "them (owner directive 2026-09-09)")


class EstimatedRewardRefused(RuntimeError):
    """Raised rather than letting an uncredited reward into a total."""


def _d(v: Any) -> Optional[D]:
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except ArithmeticError:
        return None


def trading_ledger(matched_pair_usd: Any, residual_usd: Any,
                   fees_usd: Any) -> dict:
    """Matched pair + residual - fees. Venue numbers only, no model."""
    mp, rs, fe = _d(matched_pair_usd), _d(residual_usd), _d(fees_usd)
    missing = [n for n, v in (("matched_pair_usd", mp), ("residual_usd", rs),
                              ("fees_usd", fe)) if v is None]
    if missing:
        return {"ledger": TRADING, "usd": None, "resolvable": False,
                "missing": missing,
                "why": "a trading ledger with an unknown component is not a "
                       "zero; it is unresolvable and reported as such"}
    return {"ledger": TRADING, "usd": str(mp + rs - fe), "resolvable": True,
            "matched_pair_usd": str(mp), "residual_usd": str(rs),
            "fees_usd": str(fe),
            "basis": "settled venue numbers; matched pair + residual - fees"}


def credited_ledger(kind: str, credited_usd: Any,
                    accrued_not_credited_usd: Any = None) -> dict:
    """A ledger that counts ONLY what the venue actually paid.

    accrued_not_credited is kept, because "we earned it but have not been paid"
    is a real and useful state -- but it is kept OUT of `usd`, which is the
    only field any total may read.
    """
    cr = _d(credited_usd)
    ac = _d(accrued_not_credited_usd)
    return {"ledger": kind,
            "usd": str(cr) if cr is not None else None,
            "resolvable": cr is not None,
            "accrued_not_credited_usd": str(ac) if ac is not None else None,
            "basis": "venue-credited only; accrued-but-uncredited is reported "
                     "beside this figure and is NOT part of it",
            "why_null": None if cr is not None else
                        "the venue returned no credited figure; that is "
                        "UNKNOWN, not zero"}


def three_ledgers(matched_pair_usd: Any, residual_usd: Any, fees_usd: Any,
                  maker_rebate_credited_usd: Any,
                  liquidity_reward_credited_usd: Any,
                  maker_rebate_accrued_usd: Any = None,
                  liquidity_reward_accrued_usd: Any = None) -> dict:
    """The owner's three ledgers, separate, with their provenance."""
    return {
        "ledgers": {
            TRADING: trading_ledger(matched_pair_usd, residual_usd, fees_usd),
            MAKER_REBATE: credited_ledger(MAKER_REBATE,
                                          maker_rebate_credited_usd,
                                          maker_rebate_accrued_usd),
            LIQUIDITY_REWARD: credited_ledger(LIQUIDITY_REWARD,
                                              liquidity_reward_credited_usd,
                                              liquidity_reward_accrued_usd),
        },
        "never": "these three do not merge; a caller wanting one figure must "
                 "name the rule it is using",
    }


def planning_total(ledgers: Mapping[str, Any]) -> dict:
    """Trading + CREDITED maker rebate. Nothing else. Ever.

    Liquidity reward is deliberately excluded even when credited, because the
    owner's planning rule values it at $0.00 until the account is actually
    receiving it as a matter of course. It is reported as `excluded_usd` so the
    exclusion is visible rather than silent.
    """
    L = ledgers.get("ledgers") if "ledgers" in ledgers else ledgers
    tr, mk = L.get(TRADING) or {}, L.get(MAKER_REBATE) or {}
    lq = L.get(LIQUIDITY_REWARD) or {}

    for name, led in ((TRADING, tr), (MAKER_REBATE, mk)):
        if led.get("accrued_not_credited_usd") is not None and \
                led.get("usd") is None:
            raise EstimatedRewardRefused(
                f"{name} has an accrued figure but no credited one; an "
                "uncredited amount may not stand in for a credited total. "
                + PLANNING_RULE)

    t, m = _d(tr.get("usd")), _d(mk.get("usd"))
    if t is None or m is None:
        return {"usd": None, "resolvable": False,
                "rule": PLANNING_RULE,
                "why": "an unresolvable component makes the total unresolvable; "
                       "it does not make it zero"}
    return {"usd": str(t + m), "resolvable": True, "rule": PLANNING_RULE,
            "trading_usd": str(t), "maker_rebate_credited_usd": str(m),
            "excluded_usd": lq.get("usd"),
            "excluded_why": "liquidity-program reward is excluded from planning "
                            "economics until the account actually receives it"}


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    print(json.dumps(three_ledgers(*sys.argv[1:6]), indent=1))
