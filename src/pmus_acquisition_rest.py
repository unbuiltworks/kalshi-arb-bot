"""Should a resting ACQUISITION quote keep its place when the bid moves?

NEW FILE ON PURPOSE. The predicate lives here, with its own tests, so that
the change to the canonical engine is a single call site rather than new logic
embedded in a 3,700-line money-moving module. `src/pmus_spring_l5_session.py`
is a shared file and is NOT edited by this commit; the exact one-hunk patch
for it is in `analysis/l5/pmus_manual_audit/acquisition_rest.patch` for an
owner to apply and review.

WHAT IT IS FOR
--------------
The canonical resting rule is a STRICT EQUALITY:

    if relation_ in requote_relations() and touch != px_:   # ('open','extend')
        return f"touch_moved:{touch}"

So an OPEN or EXTEND survives only while its price IS the bid, exactly. Two
consequences, both measured:

  * a quote placed BELOW the bid -- the L5 behaviour the engine exists to
    reproduce -- is cancelled on the next poll, every time, by construction;
  * a still-passive quote is thrown away when the bid merely moves. PR #188
    measured 283 of 301 re-quote transitions reposting the SAME price.

WHAT THIS CHANGES, AND WHAT IT CANNOT
-------------------------------------
It changes only WHEN a quote is abandoned. It touches no price, size, bound,
cap or band, and it cannot make a quote aggressive: in the engine the
`would_cross` guard and the `entry_ok` / band gates run BEFORE this point and
still cancel anything that would breach post-only or leave the admitted
region. A buy resting at or behind the bid is strictly MORE passive than one
that chases the touch.

Everything else the engine already does is untouched and relied upon: the
1.0 s poll (`q1.POLL_S`), cancel by exact order id (`q1.cancel_exact`), the
cancel-verdict / `CANCEL_SETTLE_S` re-read / larger-cumulative-wins
reconciliation, the order-keyed fill ledger that prevents double counting,
the inventory gates (`spring_state_key` -> `l5_action_changed`), and PR #188's
COMPLETE suppression, which this does not enter.

THRESHOLD, AND WHY IT IS A PARAMETER AND NOT A CONSTANT
-------------------------------------------------------
The tolerance is expressed in the MARKET'S OWN TICKS and supplied by the
caller, because the venues disagree about what a tick is worth:

  Gemini BTC15m recorder tape, 5 full windows, 944 best-bid changes in the
  quoting band: tick $0.01, and 57% of changes are exactly ONE tick.

  PMUS authenticated socket (`ws_bid` on PMUS_WS_XCHECK rows), 11 markets,
  1,503 observations, 378 changes: tick $0.0025 on EVERY market, and only
  11% of changes are one tick; 89% are two or more, with modes at 4 ticks
  ($0.01) and 8 ticks ($0.02).

A one-tick tolerance carried from Gemini to PMUS would be $0.0025 and would
decline about 11% of cancels instead of 57% -- the same number of ticks, a
quarter of the money, and almost none of the effect. The two venues agree on
the ECONOMIC size of a typical move and disagree completely on its tick
count, which is exactly why the threshold is not hardcoded here.

SAMPLING BIAS IN THE PMUS FIGURE, STATED BECAUSE IT SETS THE DIRECTION OF THE
ERROR. PMUS_WS_XCHECK samples a market about every 4-5 s while the book
updates about 2.9 times a second (`analysis/pmus/recorder_memory/
bbo_history_rate.json`, p50 2.86/s, itself seated-markets-only). Intermediate
moves therefore collapse into one observed delta, so that sample UNDERCOUNTS
changes and OVERSTATES their size. The true one-tick share on PMUS is higher
than 11% and the true decline rate at any threshold is higher than the table
below. A PMUS per-update measurement is required before a threshold is fixed;
until then $0.01 is the defensible candidate and $0.02 is not.

    threshold          declines (sampled)   reacts at or above
    1 tick  $0.0025     41/378   10.8%       $0.0025
    4 ticks $0.0100    104/378   27.5%       $0.0100
    8 ticks $0.0200    215/378   56.9%       $0.0200

DEFAULT OFF. `enabled` defaults to False and the engine's call site reads the
env flag, so applying the patch changes nothing until an owner arms it.
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from typing import Any, Optional

#: operator flag. Absent or anything but "1" leaves today's behaviour intact.
REST_BEHIND_TOUCH_ENV = "PMUS_SPRING_REST_AT_OR_BEHIND_TOUCH"

#: tolerance in the MARKET'S OWN TICKS. Absent means 0, i.e. no tolerance,
#: so arming the flag without choosing a threshold still only enables the
#: at-or-behind rule and never widens it by accident.
REST_BEHIND_TICKS_ENV = "PMUS_SPRING_REST_BEHIND_TICKS"

#: the relations that ACQUIRE inventory. COMPLETE prices from the pair bound
#: and has its own PR #188 suppression path; it is deliberately not here.
ACQUISITION_RELATIONS = ("open", "extend")


def enabled() -> bool:
    """Is the at-or-behind-the-bid resting rule armed for this process?"""
    return os.getenv(REST_BEHIND_TOUCH_ENV, "").strip() == "1"


def tolerance_ticks() -> int:
    """How many of the market's own ticks the bid may move before repricing.

    Unset, unparseable or negative reads as 0: a bad value must narrow the
    behaviour towards today's, never widen it.
    """
    raw = os.getenv(REST_BEHIND_TICKS_ENV, "").strip()
    if not raw:
        return 0
    try:
        n = int(raw)
    except ValueError:
        return 0
    return n if n > 0 else 0


def _dec(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def keep_resting(relation: str, price: Any, touch: Any, tick: Any,
                 *, armed: Optional[bool] = None,
                 ticks: Optional[int] = None) -> bool:
    """True when a moved touch should NOT cancel this acquisition quote.

    All four conditions must hold, and anything unreadable returns False so
    the caller falls through to its existing cancel:

      1. the rule is armed;
      2. the relation ACQUIRES inventory (open / extend);
      3. the quote is at or BEHIND the touch -- a buy priced above the bid is
         still cancelled, because that is the stale-high case;
      4. the gap is within the tolerance, in the market's own ticks.

    `armed` and `ticks` exist for tests and for a caller that already holds
    the configuration; they default to the environment.
    """
    if not (enabled() if armed is None else armed):
        return False
    if relation not in ACQUISITION_RELATIONS:
        return False
    px, tch, tk = _dec(price), _dec(touch), _dec(tick)
    if px is None or tch is None or tk is None or tk <= 0:
        return False
    if px > tch:
        return False                       # stale-high: cancel as before
    allowed = (tolerance_ticks() if ticks is None else max(0, int(ticks)))
    return (tch - px) <= (tk * D(allowed))


def describe() -> dict:
    """The configuration in force, for a decision record."""
    return {
        "armed": enabled(),
        "tolerance_ticks": tolerance_ticks(),
        "acquisition_relations": list(ACQUISITION_RELATIONS),
        "flag_env": REST_BEHIND_TOUCH_ENV,
        "ticks_env": REST_BEHIND_TICKS_ENV,
        "labels": ("changes only WHEN a quote is abandoned; no price, size, "
                   "bound, cap or band is touched, and would_cross / "
                   "entry_ok / band still run first in the caller"),
    }
