"""Default-off execution adapter for PMUS BTC 5-minute up/down.

AUTHORIZED BY THE SAME MECHANISM AS THE 15-MINUTE STRATEGY. Every order
spends `l5_live_authorization.claim_btc5m_submission` immediately before
`submit`: the owner's approval document must name the 5-minute series and
enumerate this window, the strategy's exposure and settled day result must
be inside that document's limits, the shared submission budgets must have
room, and the order's own single-use lock is created and fsynced before the
wire. There is no other way for this module to send an order (owner
decision, 2026-11-11: "do not create a second, weaker authorization path").
The local switches below (`L5_BTC5M_LIVE`, `L5_BTC5M_MARKET`,
`L5_BTC5M_NOT_AFTER`) are an ADDITIONAL lock in front of that claim; they
authorize nothing on their own.

It was a prototype outside src/ until that claim existed. The guards that
kept it out (`tests/test_l5_one_submission_claim.py`) now list it as a
submission site with its claim, and still fail for any path without one.

NOT YET A SERVICE. Nothing imports this module and no image copies it: it
needs a runner that builds a venue transport for the window, installs
`owns_venue_order_id` as that transport's cancel-ownership test, supplies
an INDEPENDENT probability (the controller places nothing on the book mid
alone), and calls `settle` with the venue's own settlement.

`Btc5mExecutor` carries the decisions of the pure controller
(`l5_btc5m_controller.decide`) to a transport for ONE exactly named
5-minute market, and owns only the orders it created itself.

WHAT IT MAY TOUCH. Five transport calls and one attribute, nothing else:
`submit`, `cancel`, `order`, `open_orders`, `positions`, and a read of
`armed`. It holds no credentials and builds no transport. Every cancel names
one venue order id taken from this executor's own ledger; there is no sweep
and no cancel-all. An order whose client id does not start with `b5m-` AND
appear in this ledger is never cancelled, polled or booked, so an order of
the 15-minute executor (`l5f-`) is invisible here except as account
exposure.

OFF UNLESS THE OWNER SAYS SO. A submission needs all of: the arm token, the
exact market slug, a 5-minute slug, an unexpired NOT_AFTER, both shared
halts released, a clean ledger, an armed transport, and a readable shared
account ceiling. Anything else sends nothing; the order that would have
gone is reported under `would_submit`.

THE SHARED HALTS ARE READ AS THE 15-MINUTE SERVICE READS THEM, so the same
variable can never mean "halted" there and "running" here:
`L5_LIVE_HALT` blocks on any non-empty value (so on "1"), and
`L5_SHADOW_ONLY` blocks on anything but exactly "0" (so on "1", and when
unset).

THE LEDGER IS THE RECORD. One JSON file, replaced atomically. An order is
written to it (state NEW) BEFORE `submit` is called, so a crash between the
two leaves a row that says an order may exist rather than no row at all.
A row whose fate cannot be read is UNCERTAIN, and one UNCERTAIN row stops
every new submission until a later poll reads it cleanly. An unreadable
ledger is never overwritten and never trusted: nothing is submitted.

ACCOUNTING LABELS. Fills are booked from the venue's own cumulative
quantity at the order's own limit price (a post-only maker buy fills at its
limit). `settle` is settlement ARITHMETIC over those booked fills; it is
realized only when the venue credits the redemption. No rebate or reward is
assumed anywhere in this module.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional

from src import l5_btc5m_controller as C
from src import l5_live_authorization as AUTH
from src import l5_origin as ORIGIN
from src import l5_persistent_mode as PMODE
from src import l5_shared_reservation as RESV

D = Decimal
ZERO = D("0")
CENT = D("0.01")

#: WHICH 5-MINUTE STRATEGY THIS PROCESS IS.
#:
#: Was the constant ORIGIN.BTC5M. One process still trades ONE series --
#: that has not changed and is not negotiable, because the coid prefix is
#: how the ownership registry tells lanes apart. What changed is that the
#: series is chosen by configuration instead of being welded in, so ETH,
#: SOL and XRP need another SERVICE rather than another executor.
#:
#: UNSET IS BTC5M, so every existing deployment is unchanged.
#:
#: IT REFUSES AT IMPORT, which is deliberate: a service that cannot say
#: which series it trades must not start, and a bad value here would
#: otherwise surface as orders placed under the wrong strategy's coid
#: prefix -- indistinguishable in the ownership registry, which is what
#: the watchdog latches on.
#:   * an unregistered name refuses;
#:   * a name whose horizon is not 300s refuses, so this executor can
#:     never be pointed at the 15-minute lane and write b5m- orders into
#:     the other strategy's series.
STRATEGY_ENV = "L5_5M_STRATEGY"


def _strategy() -> str:
    want = str(os.environ.get(STRATEGY_ENV, "")).strip().upper()
    if not want:
        return ORIGIN.BTC5M
    if want not in ORIGIN.STRATEGY_SERIES:
        raise ValueError(
            "%s=%r is not a registered strategy; known: %s"
            % (STRATEGY_ENV, want, sorted(ORIGIN.STRATEGY_SERIES)))
    if ORIGIN.STRATEGY_HORIZON_S.get(want) != 300.0:
        raise ValueError(
            "%s=%r is a %ss series; this executor is the FIVE-MINUTE one "
            "and must not trade another lane's series"
            % (STRATEGY_ENV, want, ORIGIN.STRATEGY_HORIZON_S.get(want)))
    return want


STRATEGY = _strategy()
COID_PREFIX = ORIGIN.STRATEGY_COID_PREFIX[STRATEGY]
MARKET_PREFIX = ORIGIN.STRATEGY_SERIES[STRATEGY]
LIVE_ENV = "L5_BTC5M_LIVE"
ARM_TOKEN = "ARMED_BY_OWNER"
MARKET_ENV = "L5_BTC5M_MARKET"
NOT_AFTER_ENV = "L5_BTC5M_NOT_AFTER"
HALT_ENV = "L5_LIVE_HALT"
SHADOW_ONLY_ENV = "L5_SHADOW_ONLY"
ACCOUNT_CAP_ENV = "L5_ACCOUNT_MAX_EXPOSURE_USD"
#: WHICH ENTRY RULE THE CONTROLLER RUNS. "ACCUMULATE" selects favorite
#: accumulation; anything else (including unset) keeps the original
#: edge-gated rule, so a deployment changes no behaviour until the owner
#: sets this variable. It selects a STRATEGY; it authorizes nothing: every
#: cap, both halts, the arm token and the per-order authorization claim are
#: unchanged and still apply to each order in either mode.
ENTRY_MODE_ENV = "L5_BTC5M_ENTRY_MODE"
#: ACCUMULATE only: how many of our own entry orders may rest at once.
MAX_RESTING_ENV = "L5_BTC5M_MAX_RESTING_ENTRIES"
#: dollars per entry. Tunable WITHOUT a code change because the clip trades
#: order count against how much of the ceiling actually gets used -- measured
#: on the 142-window tape: $0.06 places 2,239 entries and fills 86.7% of the
#: cap, $0.10 places 1,256 and fills 82.3%, $0.15 places 957 and fills 94.5%.
#: It does NOT change the net: every one of those settles at about -$0.020 a
#: window, because the loss is per share and not a capacity problem.
CLIP_USD_ENV = "L5_BTC5M_CLIP_USD"
#: STRADDLE only; see `straddle_price`. Absent means the mode cannot trade.
STRADDLE_PRICE_ENV = "L5_BTC5M_STRADDLE_PRICE"
#: STRADDLE only: a comma-separated ladder, e.g. "0.05,0.06,0.07". Absent
#: means the single `STRADDLE_PRICE_ENV` rung.
STRADDLE_RUNGS_ENV = "L5_BTC5M_STRADDLE_RUNGS"
#: STRADDLE only: BOUNDED REPEATED ENTRIES. Absent or not "YES" keeps the
#: one-fill-per-side lockout, which is the historical behaviour.
STRADDLE_REPEAT_ENV = "L5_BTC5M_STRADDLE_REPEAT"
#: STRADDLE only, and BOTH are REQUIRED when repetition is enabled: the
#: controller refuses the mode outright without them, because repeated
#: entry with no unmatched ceiling is a directional bet wearing a
#: straddle's clothes.
STRADDLE_MAX_SIDE_USD_ENV = "L5_BTC5M_STRADDLE_MAX_SIDE_USD"
STRADDLE_MAX_UNMATCHED_USD_ENV = "L5_BTC5M_STRADDLE_MAX_UNMATCHED_USD"
#: THE ONE COMPARISON OVER EVERY ALTERNATIVE. Absent or not "YES" keeps
#: the narrower completion branch, which is the historical behaviour.
#: Without a reader this sat at its dataclass default forever -- the
#: defect that kept the entry-EV rates unset and the late favourite
#: silent, and that I reintroduced here.
INVENTORY_RANKING_ENV = "L5_BTC5M_INVENTORY_RANKING"
#: The expected value we will PAY to remove a worst case, in dollars.
#: Absent means NO budget, which `rank` treats as refusing any
#: alternative whose EV give-up cannot be measured.
COMPLETION_EV_GIVEUP_ENV = "L5_BTC5M_COMPLETION_EV_GIVEUP_USD"
#: a give-up larger than this is not a risk decision, it is a donation.
COMPLETION_EV_GIVEUP_MAX = D("0.10")

#: STRADDLE only: THE ENTRY-EV GATE'S THREE MEASURED RATES.
#:
#: These had NO READER. `Params` declares all three as None and
#: documents None as the value that REFUSES -- so the gate returned
#: ENTRY_EV_P_PAIR_NATURAL_NOT_SET on every step of every window and the
#: mode could not open a single leg. It is the same defect that silently
#: disabled the late favourite, in the same module, for the same reason:
#: a param with no env reader sits at its dataclass default forever, and
#: "refused every time" reads identically to "no opportunity" in a log.
#:
#: THEY STAY REFUSING BY DEFAULT. The gate exists so a rung is bought
#: because it is worth buying; a default rate would manufacture the very
#: edge it tests for. The owner sets them deliberately. Measured over
#: 503 shadow windows, for reference and NOT as a default:
#: p_pair_natural 0.070, p_complete 0.131, p_lone_win 0.0981.
STRADDLE_P_PAIR_NATURAL_ENV = "L5_BTC5M_STRADDLE_P_PAIR_NATURAL"
STRADDLE_P_COMPLETE_ENV = "L5_BTC5M_STRADDLE_P_COMPLETE"
STRADDLE_P_LONE_WIN_ENV = "L5_BTC5M_STRADDLE_P_LONE_WIN"
#: the fee charged against a rung in the gate's arithmetic, and the edge
#: a rung must clear after it. Both default to zero, which is the
#: historical value and the conservative direction for the second
#: (a zero minimum admits any non-negative EV; raising it narrows).
STRADDLE_FEE_PER_SHARE_ENV = "L5_BTC5M_STRADDLE_FEE_PER_SHARE"
STRADDLE_MIN_EV_ENV = "L5_BTC5M_STRADDLE_MIN_EV_PER_SHARE"
#: STRADDLE only: BOOK-RELATIVE TWO-SIDED QUOTING. Absent or not "YES"
#: keeps the fixed-rung behaviour, which is the historical one.
STRADDLE_BOOK_RELATIVE_ENV = "L5_BTC5M_STRADDLE_BOOK_RELATIVE"
STRADDLE_JOIN_TICKS_ENV = "L5_BTC5M_STRADDLE_JOIN_TICKS"
#: the COMBINED bound on the two quotes, replacing the per-leg 0.50.
STRADDLE_PAIR_CEILING_ENV = "L5_BTC5M_STRADDLE_PAIR_CEILING"
#: reserve the cost of completing what we are about to buy.
STRADDLE_COMPLETION_RESERVE_ENV = "L5_BTC5M_STRADDLE_COMPLETION_RESERVE"
#: ------------------------------------------------------------------
#: THE MEASUREMENT PERMISSION, and why it is not a plain YES.
#:
#: The entry-EV gate's three rates were measured at 0.08 rungs. At
#: book-relative prices they REFUSE (EV_NOT_POSITIVE), which is the gate
#: working. Declaring them applicable is therefore a statement that
#: someone has measured the fill model at these prices -- or that this is
#: a deliberately bounded discovery run whose decisions are UNMEASURED.
#:
#: The token spells that out so it cannot be set by someone who thinks
#: they are merely switching a feature on, and so it cannot silently
#: become normal autonomous trading: it reads as what it is.
STRADDLE_FILL_MODEL_ENV = "L5_BTC5M_STRADDLE_FILL_MODEL"
FILL_MODEL_TOKEN = "UNMEASURED_DISCOVERY_AUTHORIZED"
#: the ceiling must sit below 1.00 with room for fees, whatever is asked.
STRADDLE_PAIR_CEILING_MAX = D("0.99")
STRADDLE_JOIN_TICKS_CEILING = 3
#: STRADDLE only: inventory-aware repricing, in ticks per clip of
#: imbalance. Absent or zero keeps the fixed limit that cannot drift.
STRADDLE_REPRICE_TICKS_ENV = "L5_BTC5M_STRADDLE_REPRICE_TICKS"
#: STRADDLE only; see `completion_pair_cost`. Absent means no completion,
#: which is the mode's historical behaviour.
COMPLETION_PAIR_COST_ENV = "L5_BTC5M_COMPLETION_PAIR_COST"
#: THE POLICY CEILING ON A COMPLETED PAIR, enforced here rather than left to
#: whoever sets the variable. L5_OPERATING_MODEL keeps near-$1 pair
#: tolerance LOCKED -- it belongs to later incentive-era L5 behaviour and
#: stays locked until equivalent economics are source-confirmed for this
#: account -- and puts spring discipline at "materially cheaper", 0.96.
#: A larger value is clamped to this, so a typo or an eager operator cannot
#: buy the locked behaviour.
#:
#: TIGHTENED 0.96 -> 0.90 on 2026-11-16, because 0.96 is now MEASURED to be
#: worse than not completing at all. On 536 shadow windows, with the cheap
#: opposite rung cancelled when a completion is placed (which is what this
#: engine does since the 1925z defect):
#:
#:     completion OFF                              +7.10 c/share/window
#:     pair 1.00  (complete at 0.92)               +2.17 c
#:     pair 0.96  (complete at 0.88)               +5.64 c
#:     pair 0.93  (complete at 0.85)               +6.83 c
#:     pair 0.88  (complete at 0.80)               +8.04 c
#:     pair 0.83  (complete at 0.75)               +8.76 c
#:
#: Everything at or above a 0.90 pair is worse than leaving completion off,
#: so the clamp now refuses that whole region rather than clamping INTO it.
#: The old 0.96 ceiling let an operator buy a configuration the data says
#: loses 1.5 c a window against doing nothing.
#:
#: WHY THE CHEAP PAIR IS STILL BETTER THAN ANY COMPLETION: it pays +0.84
#: against +0.17 at a 0.83 pair. Completion earns its place by converting
#: the COMMON outcome -- a stranded lone leg, 89% of windows -- not by
#: beating the rare one.
COMPLETION_PAIR_COST_MAX = D("0.90")

#: What a contract that is not ours is charged at. Its cost is unknown to
#: this ledger and a contract cannot cost more than $1, so $1 is the bound.
FOREIGN_USD_PER_CONTRACT = D("1.00")
FOREIGN_BASIS = ("orders and positions this ledger did not create are "
                 "charged at $1.00 per contract: their cost is unknown here, "
                 "so this is the worst case and not a measured cost")

NEW, LIVE, FILLED, CANCELLED, UNCERTAIN = (
    "NEW", "LIVE", "FILLED", "CANCELLED", "UNCERTAIN")
STATES = (NEW, LIVE, FILLED, CANCELLED, UNCERTAIN)
TERMINAL = (FILLED, CANCELLED)
OPEN = (NEW, LIVE, UNCERTAIN)

HALT_ORDER = "ORDER_UNCERTAIN"      # cleared by a clean read of every such order
HALT_WRITE = "LEDGER_WRITE"         # cleared by the next write that succeeds
HALT_LEDGER = "LEDGER"              # unreadable at load: never cleared here

LEDGER_VERSION = 1
#: a cancel already sent is sent again only after this long without a
#: terminal read, or at once when the previous attempt raised
CANCEL_RESEND_S = 5.0
PRICE_MIN, PRICE_MAX = D("0.01"), D("0.99")
MIN_QTY = D("0.01")


# ------------------------------------------------------------------ readers

def _dec(x: Any) -> Optional[Decimal]:
    """A finite Decimal, or None. Accepts the venue's {"value": ...} form."""
    if isinstance(x, Mapping):
        x = x.get("value")
    if x is None or isinstance(x, bool):
        return None
    try:
        d = x if isinstance(x, Decimal) else D(str(x).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


#: HOW AN ORDER'S STRATEGY IS DECIDED. The controller tags the late
#: favourite explicitly; the straddle's own orders are identified by the
#: reasons it has always emitted. An order we cannot place in either is
#: attributed to UNKNOWN rather than silently to one of them, because a
#: wrong attribution is worse than an absent one.
STRATEGY_BY_REASON = {
    "STRADDLE_LEG": "STRADDLE",
    "STRADDLE_COMPLETION": "STRADDLE",
    "LATE_FAVORITE": "LATE_FAVORITE",
}
UNATTRIBUTED = "UNKNOWN"


def _strategy_of(order: Any) -> str:
    """The strategy an order belongs to, from its own tag or its reason."""
    tag = str((order or {}).get("strategy") or "").strip().upper()
    if tag:
        return tag
    return STRATEGY_BY_REASON.get(
        str((order or {}).get("reason") or "").strip().upper(), UNATTRIBUTED)


def _s(d: Decimal) -> str:
    return format(d, "f")


def _canon(d: Decimal) -> str:
    """One spelling per number: 0.50 and 0.5 are the same intent."""
    return format(d.normalize(), "f")


def _utc(x: Any) -> Optional[dt.datetime]:
    """An aware UTC datetime from a datetime or epoch seconds, or None."""
    if isinstance(x, dt.datetime):
        return x.astimezone(dt.timezone.utc) if x.tzinfo is not None else None
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        return dt.datetime.fromtimestamp(float(x), dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_utc(raw: Any) -> Optional[dt.datetime]:
    """ISO-8601 with an explicit offset (or Z). A naive time is unreadable."""
    try:
        got = dt.datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return _utc(got)


def _first(row: Mapping[str, Any], keys) -> Any:
    for k in keys:
        if row.get(k) is not None:
            return row.get(k)
    return None


def live_halt_engaged() -> bool:
    return bool((os.environ.get(HALT_ENV) or "").strip())


def shadow_only_engaged() -> bool:
    return (os.environ.get(SHADOW_ONLY_ENV, "1") or "").strip() != "0"


def entry_mode() -> str:
    """The owner's entry rule, defaulting to the original edge-gated one.

    An unrecognised value reads as EDGE_GATED rather than failing, so a
    typo narrows what trades instead of widening it.
    """
    want = (os.environ.get(ENTRY_MODE_ENV) or "").strip().upper()
    if want in (C.ACCUMULATE, C.STRADDLE):
        return want
    return C.EDGE_GATED


def straddle_price(default: Decimal = D("0")) -> Decimal:
    """STRADDLE only: the fixed own-price both legs rest at.

    Bounded to (0, 0.50) EXCLUSIVE, because a pair costs twice this and
    pays 1.00: at 0.50 or above it is a locked loss whatever happens. An
    unreadable or out-of-range value keeps the default of 0, which the
    controller refuses as PARAMS_UNREADABLE -- so selecting the MODE
    without choosing a price trades nothing rather than guessing one.
    """
    raw = _dec(os.environ.get(STRADDLE_PRICE_ENV))
    if raw is None or not (D("0") < raw < D("0.50")):
        return default
    return raw


#: THE LATE-FAVORITE's inputs. None of these had an environment reader,
#: so every one of them sat at its dataclass default of 0 and the
#: strategy was disabled three ways over: no edge margin (refused as
#: NO_EDGE_MARGIN_SET), no clip (qty 0), and a late window that could
#: never be stepped. The module deliberately has NO safe default for a
#: margin, and these keep that property: unreadable leaves 0, and 0
#: refuses.
LATE_WINDOW_ENV = "L5_LATE_FAVORITE_WINDOW_S"
LATE_CLIP_ENV = "L5_LATE_FAVORITE_CLIP_USD"
LATE_EDGE_ENV = "L5_LATE_FAVORITE_MIN_EDGE_PER_SHARE"
LATE_FEE_ENV = "L5_LATE_FAVORITE_FEE_PER_SHARE"

#: a late window must leave at least this much stepping time, or the
#: strategy is unreachable however its other inputs are set. The session
#: steps `while clock() < window_start + 300 - CANCEL_LEAD_S`, so a
#: window of exactly CANCEL_LEAD_S fires from the first instant the loop
#: has already stopped. `l5_live_safety.strategy_can_fire` is the gate
#: that refuses the unreachable combination; this bound keeps a typo
#: from producing one silently.
LATE_WINDOW_MIN_S = D("36")
#: HALF THE WINDOW, and not a round number chosen for comfort. At 150 the
#: strategy becomes eligible from the window's exact midpoint, which is
#: the widest band in which it is still a LATE strategy; past half the
#: market it is simply a second opener competing with the straddle over
#: the whole entry range, and the side-conflict rule would then refuse
#: almost every candidate anyway because the straddle rests on both
#: sides.
#:
#: Widening the band does NOT lower the bar -- it only gives the EV gate
#: more moments to look at, and the gate is unchanged. What makes the
#: midpoint interesting is the opposite of a weaker test: the settlement
#: average is the 60 seconds ending at the close, so at t=150 none of it
#: has formed and `p_up` still carries real diffusion. A level only
#: clears break-even there when the move is ALREADY large -- and that is
#: exactly when the book may not have repriced to 0.99 yet, so a 0.97
#: maker bid can rest un-crossed. The edge, where it exists, is the gap
#: between our model's confidence and the book's price, and that gap is
#: widest before the book catches up.
LATE_WINDOW_MAX_S = D("150")
LATE_CLIP_MAX_USD = D("5.00")
#: above this a "97c favourite" is not a favourite trade any more
LATE_EDGE_MAX = D("0.20")


def late_favorite_window_s(default: Decimal = D("0")) -> Decimal:
    """How late the favourite may be bought, in seconds before close.

    Bounded BELOW, not just above. A value at or under the cancel lead
    describes a window the session has already stopped stepping in, and
    the strategy would refuse TOO_EARLY on every step of every window
    while reporting nothing unusual -- which is what the shipped default
    of 30 did. Out of range keeps the default of 0, which refuses.
    """
    raw = _dec(os.environ.get(LATE_WINDOW_ENV))
    if raw is None or not (LATE_WINDOW_MIN_S <= raw <= LATE_WINDOW_MAX_S):
        return default
    return raw


def late_favorite_clip_usd(default: Decimal = D("0")) -> Decimal:
    """Dollars one late-favourite order may spend. 0 places nothing."""
    raw = _dec(os.environ.get(LATE_CLIP_ENV))
    if raw is None or not (ZERO < raw <= LATE_CLIP_MAX_USD):
        return default
    return raw


def late_favorite_min_edge_per_share(default: Decimal = D("0")) -> Decimal:
    """The edge a level must clear after fees to be worth resting at.

    THERE IS NO SAFE DEFAULT and this does not invent one. At 0.97 the
    break-even win rate is about 97%: one loss cancels roughly 32 wins,
    so the margin is the only thing standing between a modelled edge and
    a correlated loss. Unreadable leaves 0, and the strategy refuses.
    """
    raw = _dec(os.environ.get(LATE_EDGE_ENV))
    if raw is None or not (ZERO < raw <= LATE_EDGE_MAX):
        return default
    return raw


def late_favorite_fee_per_share(default: Decimal = D("0")) -> Decimal:
    """The venue's per-share commission, charged against every level.

    Zero is legitimate here -- a maker order may genuinely pay no
    commission -- so an unreadable value keeps the default rather than
    refusing. It makes the EV gate STRICTER to overstate it, never
    looser, and the gate still has to clear the margin above.
    """
    raw = _dec(os.environ.get(LATE_FEE_ENV))
    if raw is None or raw < ZERO or raw > D("0.10"):
        return default
    return raw


def straddle_repeat_enabled(default: bool = False) -> bool:
    """Whether a side may take more than one clip. Matches the other
    switches in this module: only an explicit YES turns it on."""
    raw = os.environ.get(STRADDLE_REPEAT_ENV)
    if raw is None:
        return default
    return raw.strip().upper() == "YES"


def straddle_max_side_usd(default: Decimal = D("0")) -> Decimal:
    """Per-side filled+resting ceiling. Unreadable or non-positive keeps
    the default of 0, which REFUSES the repetition mode rather than
    guessing a bound."""
    raw = _dec(os.environ.get(STRADDLE_MAX_SIDE_USD_ENV))
    if raw is None or raw <= ZERO:
        return default
    return min(raw, STRADDLE_MAX_SIDE_USD_CEILING)


def inventory_ranking_enabled(default: bool = False) -> bool:
    """Whether the one-arithmetic ranker decides the residual action."""
    raw = os.environ.get(INVENTORY_RANKING_ENV)
    if raw is None:
        return default
    return raw.strip().upper() == "YES"


def completion_ev_giveup_usd(default: Optional[Decimal] = None
                             ) -> Optional[Decimal]:
    """Dollars of expected value we will pay to remove a worst case.

    None -- the default, and what an absent or negative value gives --
    means no budget, and `rank` then refuses any alternative whose EV
    give-up it cannot measure. Over-asking is CLAMPED DOWN.
    """
    raw = _dec(os.environ.get(COMPLETION_EV_GIVEUP_ENV))
    if raw is None or raw < ZERO:
        return default
    return min(raw, COMPLETION_EV_GIVEUP_MAX)


def straddle_book_relative(default: bool = False) -> bool:
    """Whether both legs are priced from the live book. Explicit YES."""
    raw = os.environ.get(STRADDLE_BOOK_RELATIVE_ENV)
    if raw is None:
        return default
    return raw.strip().upper() == "YES"


def straddle_join_ticks(default: int = 0) -> int:
    """Ticks ABOVE our own bid. 0 joins it. Clamped down, never up; a
    negative or unreadable value keeps the default."""
    raw = os.environ.get(STRADDLE_JOIN_TICKS_ENV)
    if raw is None:
        return default
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if v < 0:
        return default
    return min(v, STRADDLE_JOIN_TICKS_CEILING)


def straddle_pair_ceiling(default: Decimal = D("0")) -> Decimal:
    """The COMBINED cost the two quotes may reach.

    Zero -- the default, and what an absent or non-positive value gives
    -- disables book-relative quoting entirely. Anything at or above
    `STRADDLE_PAIR_CEILING_MAX` is CLAMPED DOWN: at 1.00 a matched pair
    cannot profit before costs, so the owner's intent to quote is
    honoured and the arithmetic is not.
    """
    raw = _dec(os.environ.get(STRADDLE_PAIR_CEILING_ENV))
    if raw is None or raw <= ZERO:
        return default
    return min(raw, STRADDLE_PAIR_CEILING_MAX)


def straddle_completion_reserve(default: bool = False) -> bool:
    raw = os.environ.get(STRADDLE_COMPLETION_RESERVE_ENV)
    if raw is None:
        return default
    return raw.strip().upper() == "YES"


def straddle_fill_model_measured(default: bool = False) -> bool:
    """Whether the fill model at book-relative prices is declared
    applicable. Needs the exact token, not YES -- see
    `STRADDLE_FILL_MODEL_ENV`."""
    raw = os.environ.get(STRADDLE_FILL_MODEL_ENV)
    if raw is None:
        return default
    return raw.strip() == FILL_MODEL_TOKEN


def straddle_probability(env_name: str,
                         default: Optional[Decimal] = None
                         ) -> Optional[Decimal]:
    """One of the entry-EV gate's measured rates.

    An absent, unreadable or out-of-range value returns None, which
    REFUSES the gate rather than guessing a rate. A probability outside
    (0, 1] is not a probability, so it refuses too instead of being
    clamped into one -- a clamped rate is a rate nobody measured.
    """
    raw = _dec(os.environ.get(env_name))
    if raw is None or raw <= ZERO or raw > D("1"):
        return default
    return raw


def straddle_fee_per_share(default: Decimal = D("0")) -> Decimal:
    raw = _dec(os.environ.get(STRADDLE_FEE_PER_SHARE_ENV))
    if raw is None or raw < ZERO:
        return default
    return raw


def straddle_min_ev_per_share(default: Decimal = D("0")) -> Decimal:
    """The edge a rung must clear. A NEGATIVE value would admit a rung
    the gate priced as a loss, so it refuses to the default instead."""
    raw = _dec(os.environ.get(STRADDLE_MIN_EV_ENV))
    if raw is None or raw < ZERO:
        return default
    return raw


def straddle_max_unmatched_usd(default: Decimal = D("0")) -> Decimal:
    """The unmatched-inventory ceiling, which is the real directional
    exposure and the window's worst-case loss. Same discipline."""
    raw = _dec(os.environ.get(STRADDLE_MAX_UNMATCHED_USD_ENV))
    if raw is None or raw <= ZERO:
        return default
    return min(raw, STRADDLE_MAX_UNMATCHED_USD_CEILING)


def straddle_reprice_ticks(default: int = 0) -> int:
    """Ticks per clip of imbalance. Unreadable keeps 0 (disabled)."""
    raw = os.environ.get(STRADDLE_REPRICE_TICKS_ENV)
    if raw is None:
        return default
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if v < 0:
        return default
    return min(v, STRADDLE_REPRICE_TICKS_CEILING)


def completion_pair_cost(default: Decimal = D("0")) -> Decimal:
    """STRADDLE only: what a COMPLETED pair may cost, in total.

    Zero -- the default, and what an absent or unreadable variable gives --
    disables completion entirely and leaves the mode byte-for-byte as it
    was. A value at or below 0.50 is refused rather than clamped up,
    because the cheap rung already rests there and asking for a completion
    below it is a contradiction rather than an aggressive setting.

    Anything above `COMPLETION_PAIR_COST_MAX` is CLAMPED DOWN to it, not
    refused: the owner's intent to complete is honoured, the locked
    near-$1 behaviour is not.
    """
    raw = _dec(os.environ.get(COMPLETION_PAIR_COST_ENV))
    if raw is None or raw <= D("0.50"):
        return default
    return min(raw, COMPLETION_PAIR_COST_MAX)


#: Per-entry clip ceilings, PER MODE. The directional modes keep the
#: original $0.45 -- one clip was the whole early exposure ceiling, and
#: their worst case is the ceiling itself.
#:
#: STRADDLE gets its own, higher bound by owner decision 2026-11-12,
#: because its risk shape is different: the worst case is exactly 2 x clip
#: whatever the ladder's width, and it is bounded further by the approved
#: cap. At $2.25 that is $4.50 a window, against roughly $45 of daily loss
#: room. Raising the shared bound instead would have widened favorite
#: accumulation by 5x as a side effect, which nobody asked for.
CLIP_MAX_USD = D("0.45")
STRADDLE_CLIP_MAX_USD = D("2.25")

#: CODE CEILINGS ON THE REPETITION BOUNDS, enforced here and not left to
#: whoever sets the variable. The unmatched ceiling IS the worst-case loss
#: a window can take -- matched inventory pays $1.00 a pair and cannot
#: lose -- so it is held to one clip's worth, and the per-side cap to two.
#: Over-asking is CLAMPED DOWN, never honoured: a typo must not widen risk.
#: =====================================================================
#: OPTIONAL TAKER PAIR COMPLETION.
#:
#: NOTHING HERE IS NEW EXECUTION MACHINERY. Three gates already existed
#: and all three are kept:
#:   1. `_order_fault` refuses every non-post_only order outright
#:      (`TAKER_NEVER_SENT`) -- the adapter's hard block.
#:   2. `l5_persistent_mode.may_submit` refuses a non-post_only order
#:      unless the standing envelope grants `taker_allowed`.
#:   3. `pmus_q1_executor.taker_completion_ok` prices the ALL-IN pair
#:      including the real taker fee and requires >= 1c locked.
#: The only change is that (1) gains a narrow, completion-only exception,
#: and that the economics of (3) are re-checked here against executable
#: depth and a slippage allowance before it is taken.
#:
#: WHY COMPLETION ONLY. A taker ENTRY pays the spread to acquire
#: directional risk; a taker COMPLETION pays it to retire risk already
#: held, and only when the resulting pair is locked profitable. The
#: former is never permitted by this adapter at any setting.
TAKER_COMPLETION_ENV = "L5_BTC5M_TAKER_COMPLETION"
#: ticks of adverse move allowed for between pricing and arrival. Charged
#: AGAINST us, so a larger value is more conservative, not less.
#: HOW MUCH NOTIONAL A COMPLETION MAY SPEND, which is what makes an
#: owner's LOSS ceiling hold unconditionally. Under the economics gate a
#: completed pair cannot lose, so the loss is just the unmatched residual
#: -- but that argument ASSUMES THE GATE IS RIGHT. This cap bounds the
#: damage if it is not: with it, the worst case is the unmatched clip
#: plus this figure plus the fee, even if every contract settled
#: worthless. Zero is the default and REFUSES completion entirely.
TAKER_COMPLETION_MAX_USD_ENV = "L5_BTC5M_TAKER_COMPLETION_MAX_USD"
TAKER_COMPLETION_MAX_USD_CEILING = D("1.00")
#: the MINIMUM net settlement margin a completed pair must lock, on top
#: of fees and slippage. `pmus_q1_executor.MIN_EDGE` (1c) is the FLOOR:
#: this can only ask for MORE, never less, so a misconfiguration cannot
#: loosen the economics.
#: how much of DISPLAYED depth is believed when walking the book. The
#: pmus-live lane prices against the same 0.25: displayed size is
#: displayed, not guaranteed, and crossing is paying for certainty we
#: have not got.
TAKER_DEPTH_HAIRCUT = D("0.25")
TAKER_MIN_MARGIN_ENV = "L5_BTC5M_TAKER_MIN_MARGIN"
TAKER_MIN_MARGIN_CEILING = D("0.20")
TAKER_SLIPPAGE_TICKS_ENV = "L5_BTC5M_TAKER_SLIPPAGE_TICKS"
TAKER_SLIPPAGE_TICKS_DEFAULT = 1
TAKER_SLIPPAGE_TICKS_CEILING = 5
#: a book older than this cannot price a crossing order
TAKER_BOOK_MAX_AGE_S = 3.0
#: the reasons a completion is the ONLY thing that may cross
TAKER_COMPLETION_REASONS = ("STRADDLE_COMPLETION",)


def taker_completion_allowed(default: bool = False) -> bool:
    """Whether a COMPLETION may cross the spread. Explicit YES only, and
    this is still only one of three gates."""
    raw = os.environ.get(TAKER_COMPLETION_ENV)
    if raw is None:
        return default
    return raw.strip().upper() == "YES"


def taker_min_margin() -> Decimal:
    """Required locked margin per pair. Never below q1's 1c floor."""
    from src import pmus_q1_executor as _q1
    floor = _q1.MIN_EDGE
    raw = _dec(os.environ.get(TAKER_MIN_MARGIN_ENV))
    if raw is None or raw < floor:
        return floor
    return min(raw, TAKER_MIN_MARGIN_CEILING)


def taker_completion_max_usd(default: Decimal = D("0")) -> Decimal:
    """Notional one completion may spend. Unreadable or non-positive keeps
    the default of 0, which refuses completion rather than guessing a
    bound; over-asking is CLAMPED DOWN to the code ceiling."""
    raw = _dec(os.environ.get(TAKER_COMPLETION_MAX_USD_ENV))
    if raw is None or raw <= ZERO:
        return default
    return min(raw, TAKER_COMPLETION_MAX_USD_CEILING)


def taker_slippage_ticks(default: int = TAKER_SLIPPAGE_TICKS_DEFAULT) -> int:
    raw = os.environ.get(TAKER_SLIPPAGE_TICKS_ENV)
    if raw is None:
        return default
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if v < 0:
        return default
    return min(v, TAKER_SLIPPAGE_TICKS_CEILING)


def completion_taker_fault(order: Mapping[str, Any],
                           held_qty: Decimal,
                           basis_px: Optional[Decimal],
                           resting_same_side_qty: Decimal = ZERO
                           ) -> Optional[str]:
    """Why this crossing COMPLETION may not be sent, or None.

    `held_qty` and `basis_px` come from the executor's OWN ledger, never
    from the order, because the first leg's cost is what decides whether
    the pair is profitable and a caller that supplied it could make any
    completion look good. `basis_px` is the DEAREST-LOT basis (see
    `Btc5mExecutor.dearest_basis`), not a blended average that would let
    cheap inventory subsidise dear inventory.

    `resting_same_side_qty` is our own working quantity on the COMPLETING
    side. A maker completion already resting would be retired by this
    cross as well, so counting it is what stops one deficit being
    completed twice and leaving fresh exposure on the other side.

    The order must carry the book it was priced against -- `ask`,
    `ask_shares` and `age_s` -- and all three are checked:
      * a stale book cannot price a crossing order;
      * `ask_shares` must cover the whole clip, because a partial
        completion leaves exactly the unmatched leg we were retiring;
      * the ask is charged `taker_slippage_ticks` WORSE than quoted.
    The economics then reuse `pmus_q1_executor.taker_completion_ok`,
    which includes the venue's real taker fee at this account's tier.
    """
    if str(order.get("reason")) not in TAKER_COMPLETION_REASONS:
        return "TAKER_ONLY_FOR_COMPLETION"
    if held_qty is None or held_qty <= 0:
        return "NOTHING_TO_COMPLETE"
    qty = _dec(order.get("qty"))
    if qty is None or qty <= 0:
        return "ORDER_UNREADABLE"
    #: DUPLICATE EXPOSURE. The deficit this cross may fill is what is
    #: held MINUS whatever we already have working on the completing
    #: side; ignoring our own resting completion would complete the same
    #: deficit twice.
    rest = _dec(resting_same_side_qty) or ZERO
    if rest < 0:
        return "RESTING_UNREADABLE"
    completable = held_qty - rest
    if completable <= 0:
        return "COMPLETION_ALREADY_WORKING"
    if qty > completable:
        #: completing MORE than is held (net of what is working) would
        #: open new directional risk on the other side under the name of
        #: a hedge
        return "COMPLETION_EXCEEDS_HELD"
    book = order.get("book")
    if not isinstance(book, Mapping):
        return "TAKER_BOOK_ABSENT"
    ask = _dec(book.get("ask"))
    shares = _dec(book.get("ask_shares"))
    age = book.get("age_s")
    try:
        age = float(age)
    except (TypeError, ValueError):
        age = None
    if ask is None or not (ZERO < ask < D("1")):
        return "TAKER_ASK_UNREADABLE"
    if age is None or age < 0 or age > TAKER_BOOK_MAX_AGE_S:
        return "TAKER_BOOK_STALE"
    #: EXECUTABLE DEPTH AT THE ACTUAL QUANTITY, walked over the rungs
    #: when the book gives them, rather than trusting a single
    #: top-of-book size. Reuses `l5_inventory_decision.walk_own_side`,
    #: which is the same walk the pmus-live lane prices against.
    rungs = book.get("rungs")
    if rungs:
        from src import l5_inventory_decision as _inv
        try:
            walk = _inv.walk_own_side(
                [(D(str(px_)), D(str(q_))) for px_, q_ in rungs], qty,
                haircut=TAKER_DEPTH_HAIRCUT)
        except Exception:
            return "TAKER_DEPTH_UNREADABLE"
        if not isinstance(walk, Mapping) or not walk.get("ok"):
            return "TAKER_DEPTH_SHORT"
        if not walk.get("clears_in_full"):
            return "TAKER_DEPTH_SHORT"
        vwap = _dec(walk.get("vwap"))
        if vwap is None:
            return "TAKER_DEPTH_UNREADABLE"
        if vwap > ask:
            #: walking the book costs more than the touch, so the
            #: economics are priced on what the walk actually pays
            ask = vwap
    elif shares is None or shares < qty:
        return "TAKER_DEPTH_SHORT"
    #: WHAT WE COULD ACTUALLY PAY, which is the worse of the quoted ask
    #: plus the slippage allowance and OUR OWN LIMIT. Pricing only
    #: against the book would leave a hole: a limit set above that figure
    #: can fill above it, and the economics would have been verified at a
    #: price we never paid.
    own = _dec(order.get("price"))
    if own is None or not (ZERO < own < D("1")):
        return "ORDER_UNREADABLE"
    worst = max(ask + (CENT * taker_slippage_ticks()), own)
    if worst >= D("1"):
        return "TAKER_PRICE_OFF_GRID"
    #: ECONOMICS FIRST, so an unprofitable pair is reported as one rather
    #: than as a notional breach -- the refusal reason is what an operator
    #: reads at 3am.
    if basis_px is None or basis_px <= ZERO:
        return "COST_BASIS_UNKNOWN"
    from src import pmus_q1_executor as _q1
    if not _q1.taker_completion_ok(basis_px, worst,
                                   min_edge=taker_min_margin()):
        return "TAKER_PAIR_NOT_PROFITABLE"
    #: THEN the notional cap, judged at the WORST price we could pay. This
    #: is not an economic test -- it is the bound that makes the owner's
    #: loss ceiling hold even if the economics above are wrong.
    cap = taker_completion_max_usd()
    if cap <= ZERO:
        return "TAKER_COMPLETION_NOTIONAL_UNSET"
    if worst * qty > cap:
        return "TAKER_COMPLETION_NOTIONAL_CAP"
    return None


STRADDLE_MAX_SIDE_USD_CEILING = STRADDLE_CLIP_MAX_USD * D("2")
STRADDLE_MAX_UNMATCHED_USD_CEILING = STRADDLE_CLIP_MAX_USD
STRADDLE_REPRICE_TICKS_CEILING = 5


def clip_usd(default: Decimal = D("0.10"),
             max_usd: Decimal = CLIP_MAX_USD) -> Decimal:
    """Dollars per entry. An unreadable or out-of-range value keeps the
    default: a typo must never widen the size this strategy trades in. The
    floor is $0.02 (two cents buys at least one share at any eligible
    price); the ceiling is `max_usd`, which is per mode."""
    raw = _dec(os.environ.get(CLIP_USD_ENV))
    if raw is None or not (D("0.02") <= raw <= max_usd):
        return default
    return raw


def straddle_rungs(default: tuple = ()) -> tuple:
    """STRADDLE only: the ladder, as a comma-separated list of prices.

    EVERY rung must parse and sit inside (0, 0.50); one bad entry returns
    the default rather than silently dropping it, because a ladder that was
    not specified correctly must not half-trade. The controller applies the
    same bound again and refuses outright, so this is belt and braces.

    A LADDER IS LEVERAGE, NOT DIVERSIFICATION -- see `Params.straddle_rungs`
    for the measured economics. Sizing keeps one side's whole ladder at
    `clip_usd`, so widening it does not widen the worst case.
    """
    raw = (os.environ.get(STRADDLE_RUNGS_ENV) or "").strip()
    if not raw:
        return default
    out = []
    for part in raw.split(","):
        v = _dec(part.strip())
        if v is None or not (D("0") < v < D("0.50")):
            return default
        out.append(v)
    return tuple(sorted(set(out)))


def max_resting_entries(default: int = 4) -> int:
    """How many entry orders may rest at once. Out-of-range reads keep the
    default: a typo must not widen what the strategy may have working."""
    raw = (os.environ.get(MAX_RESTING_ENV) or "").strip()
    if not raw.isdigit():
        return default
    n = int(raw)
    return n if 1 <= n <= 8 else default


#: the executor's hard bound on simultaneously live orders OUTSIDE
#: accumulation. This is the original value and the edge-gated path must
#: keep it: that rule rests at most one entry at a time.
EDGE_GATED_LIVE_ORDERS = 2


def live_order_bound() -> int:
    """The executor's hard cap on simultaneously live orders.

    Each mode gets the bound ITS OWN PLAN NEEDS, because a bound smaller
    than the plan does not shrink the strategy -- it deforms it.

      ACCUMULATE  follows `max_resting_entries`.
      STRADDLE    needs TWO ORDERS PER RUNG, one a side. OBSERVED LIVE
                  2026-11-12 23:10Z: the mode fell through to the
                  EDGE_GATED 2, so a six-rung ladder could rest only two
                  orders, both landed on UP, and the window settled a
                  purely directional -$0.55. The single-rung straddle was
                  unaffected -- it wants exactly 2 -- which is why it had
                  worked all evening and hid this.
      EDGE_GATED  the original 2, unchanged.
    """
    mode = entry_mode()
    if mode == C.ACCUMULATE:
        return max(EDGE_GATED_LIVE_ORDERS, max_resting_entries())
    if mode == C.STRADDLE:
        rungs = len(straddle_rungs()) or 1
        return max(EDGE_GATED_LIVE_ORDERS, 2 * rungs)
    return EDGE_GATED_LIVE_ORDERS


def account_cap() -> Optional[Decimal]:
    """The shared account ceiling in dollars, or None (unset or unreadable)."""
    cap = _dec(os.environ.get(ACCOUNT_CAP_ENV))
    return cap if cap is not None and cap >= 0 else None


ACCOUNT_LANES_ENV = "L5_ACCOUNT_LANES"

#: THE MOST LANES THAT MAY SHARE ONE ACCOUNT. Not a risk limit -- a
#: typo guard. Sixteen $1 lanes already slice a $17 account past the
#: point where any of them can buy a share.
MAX_ACCOUNT_LANES = 16


def account_lanes() -> Optional[int]:
    """How many lanes share ACCOUNT_CAP_ENV, or None if unreadable.

    WHY THIS EXISTS. Under FOREIGN_REPORTED the ceiling is checked
    against `own_usd`, which is read from THIS lane's ledger. Each lane
    of the 5-minute fleet has its own ledger -- that separation is what
    stops two assets overwriting each other's orders -- so `own_usd`
    for the ETH lane does not include the BTC lane's inventory. Four
    lanes therefore each measured themselves against the WHOLE ceiling
    and could commit 4x it between them.

    UNSET IS ONE LANE, which is exactly today's behaviour for a single
    executor and keeps this a no-op for the armed BTC5m deployment.

    A VALUE THAT DOES NOT PARSE REFUSES rather than defaulting to 1,
    because 1 is the most permissive answer: a typo must not quietly
    hand this lane the entire account. `l5_5m_fleet` sets it from the
    child count so the lanes cannot disagree about it.
    """
    raw = str(os.environ.get(ACCOUNT_LANES_ENV, "")).strip()
    if not raw:
        return 1
    if not raw.isdigit():
        return None
    n = int(raw)
    return n if 1 <= n <= MAX_ACCOUNT_LANES else None


def lane_cap() -> Optional[Decimal]:
    """This lane's SHARE of the account ceiling, or None if unreadable.

    FLOORED TO THE CENT, so four lanes under a $17 ceiling get $4.25
    each and never 4 x $4.2500000001. Unreadable either half -- no cap,
    or a lane count that does not parse -- is None, and the caller
    refuses on None exactly as it does for an unreadable cap.

    ONE LANE IS THE CAP ITSELF, returned untouched. Not an optimisation:
    the ceiling is set to sub-cent values in places (a $0.0986 ceiling
    admitting exactly one 0.58 x 0.17 leg is a pinned case), and
    flooring a single lane's share would move that boundary and refuse
    an order the owner's ceiling allows. Dividing by one must not
    change the number.
    """
    cap, lanes = account_cap(), account_lanes()
    if cap is None or lanes is None:
        return None
    if lanes == 1:
        return cap
    return (cap / D(lanes)).quantize(CENT, rounding=ROUND_DOWN)


#: HOW ANOTHER LANE'S INVENTORY COUNTS AGAINST OUR ACCOUNT CEILING.
#:
#: "CHARGED" is the default and today's behaviour: every contract this
#: ledger did not create is charged at FOREIGN_USD_PER_CONTRACT ($1) and
#: counted against ACCOUNT_CAP_ENV. That was right when this was the only
#: automated lane on the account.
#:
#: WHY IT STOPPED BEING RIGHT, measured 2026-11-15T03:30Z. The owner's
#: sports lane held 45 contracts, which this priced at $48.90 against a
#: $17 ceiling, and BTC5m refused every order with ACCOUNT_EXPOSURE_CAP
#: for five consecutive windows at $0 of its own risk. Two different
#: things had been collapsed into one number:
#:   - OUR risk, which the owner's ceiling exists to bound; and
#:   - another lane's inventory, which is bounded by that lane's own
#:     limits and is not ours to lose.
#: The $1 charge is also a 12.5x overstatement at an 8c entry, the same
#: defect COST_BASIS_OVERSTATEMENT.md documents for our own legs.
#:
#: "REPORTED" separates them. The ceiling then bounds OUR exposure, and a
#: SECOND check -- the venue's own buying power -- bounds what the shared
#: account can actually fund. Foreign inventory is still read, still
#: reported in the account block, and still makes the account UNKNOWN when
#: it cannot be read; it just stops being charged to us.
#:
#: FAIL CLOSED EITHER WAY: unreadable buying power refuses, exactly as an
#: unreadable foreign book does. Unknown cash is never read as unlimited.
FOREIGN_EXPOSURE_ENV = "L5_BTC5M_FOREIGN_EXPOSURE"
FOREIGN_CHARGED, FOREIGN_REPORTED = "CHARGED", "REPORTED"


def _buying_power(transport: Any) -> Optional[Decimal]:
    """The venue's own account-scoped funds, or None if unreadable.

    Both halves come from the transport: `account()` for the answer and
    the transport class's own `buying_power` to read it. This module
    deliberately imports nothing that holds a credential or knows a venue
    schema (TestIsolationOfTheModule pins that), so it does not carry a
    second copy of the balances parser -- a transport that cannot parse
    its own answer reads as unreadable.

    ANY failure -- no cash route, a network error, no parser, a shape the
    parser does not know -- reads as unreadable, and an unreadable cash
    figure refuses the order. Unknown cash is never unlimited.
    """
    parse = getattr(type(transport), "buying_power", None)
    if parse is None:
        return None
    try:
        out = parse(transport.account())
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None
    return out if isinstance(out, Decimal) else None


def foreign_exposure_mode() -> str:
    """CHARGED unless EXACTLY "REPORTED" is set. Anything else is CHARGED."""
    raw = str(os.environ.get(FOREIGN_EXPOSURE_ENV, "")).strip().upper()
    return FOREIGN_REPORTED if raw == FOREIGN_REPORTED else FOREIGN_CHARGED


class Btc5mExecutor:
    """The controller's hands for one 5-minute market. See the module doc."""

    def __init__(self, transport: Any, ledger_path: str,
                 params: Optional[C.Params] = None,
                 now_fn: Optional[Callable[[], Any]] = None,
                 max_live_orders: int = 2) -> None:
        self.transport = transport
        self.ledger_path = str(ledger_path)
        self.params = params if params is not None else C.Params()
        self._now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))
        self.max_live_orders = int(max_live_orders)
        self._ledger: Dict[str, Any] = {
            "version": LEDGER_VERSION, "orders": {}, "inventory": {},
            "settled": {}, "halted_reason": None, "halt_kind": None}
        #: False when the file on disk could not be read. It is then never
        #: written by this object, and nothing is submitted or cancelled:
        #: which orders are ours is exactly what could not be read.
        self._ledger_ok = True
        self._on_disk: Optional[str] = None      # the last text written
        self._load()

    # ------------------------------------------------------------- ledger

    @property
    def halted_reason(self) -> Optional[str]:
        return self._ledger.get("halted_reason")

    def _halt(self, kind: str, reason: str) -> None:
        # an order halt outlives a write failure, and nothing replaces an
        # unreadable ledger
        held = self._ledger.get("halt_kind")
        if held == HALT_LEDGER or (held == HALT_ORDER and kind == HALT_WRITE):
            return
        self._ledger["halt_kind"] = kind
        self._ledger["halted_reason"] = reason

    def _fail_ledger(self, reason: str) -> None:
        self._ledger_ok = False
        self._ledger["halt_kind"] = HALT_LEDGER
        self._ledger["halted_reason"] = reason

    def _load(self) -> None:
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except FileNotFoundError:
            return                                    # a fresh ledger
        except OSError as exc:
            self._fail_ledger("LEDGER_UNREADABLE:%s" % type(exc).__name__)
            return
        try:
            self._ledger = self._validated(json.loads(raw))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self._fail_ledger("LEDGER_UNREADABLE:%s" % type(exc).__name__)
            return
        # A NEW row at start means the process stopped between the ledger
        # write and the venue's answer: the order may or may not exist.
        found = [c for c, r in sorted(self._ledger["orders"].items())
                 if r["state"] == NEW]
        for coid in found:
            self._uncertain(coid, "NEW_AT_RESTART_SUBMIT_OUTCOME_UNKNOWN")
        if found:
            self._save_or_halt()

    @staticmethod
    def _validated(doc: Any) -> Dict[str, Any]:
        """The ledger exactly as this module writes it, or raise."""
        if not isinstance(doc, dict) or doc.get("version") != LEDGER_VERSION:
            raise ValueError("not a version-%d ledger" % LEDGER_VERSION)
        orders, inventory = doc.get("orders"), doc.get("inventory")
        settled = doc.get("settled", {})
        if not all(isinstance(x, dict) for x in (orders, inventory, settled)):
            raise ValueError("ledger sections are not objects")
        booked: Dict[tuple, List[Decimal]] = {}
        for coid, r in orders.items():
            if not str(coid).startswith(COID_PREFIX) or not isinstance(r, dict):
                raise ValueError("order row %r is not ours" % (coid,))
            nums = [_dec(r.get(k)) for k in ("price", "qty", "filled_qty",
                                             "filled_cost")]
            if (not isinstance(r.get("market"), str) or not r["market"]
                    or r.get("side") not in C.SIDES
                    or r.get("state") not in STATES
                    or any(n is None or n < 0 for n in nums)
                    or nums[2] > nums[1]
                    # every fill is booked at the order's own price
                    or nums[3] != nums[2] * nums[0]):
                raise ValueError("order row %r is unreadable" % (coid,))
            if r.get("venue_order_id") is not None and not r["venue_order_id"]:
                raise ValueError("order row %r has a blank venue id" % (coid,))
            got = booked.setdefault((r["market"], r["side"]), [ZERO, ZERO])
            got[0] += nums[2]
            got[1] += nums[3]
        # inventory must be exactly what the order rows booked
        stated: Dict[tuple, List[Decimal]] = {}
        for market, legs in inventory.items():
            if not isinstance(legs, dict):
                raise ValueError("inventory for %r is unreadable" % (market,))
            for side, leg in legs.items():
                qty = _dec(leg.get("qty")) if isinstance(leg, dict) else None
                cost = _dec(leg.get("cost")) if isinstance(leg, dict) else None
                if side not in C.SIDES or qty is None or cost is None:
                    raise ValueError("inventory leg is unreadable")
                stated[(market, side)] = [qty, cost]
        for key in set(booked) | set(stated):
            if booked.get(key, [ZERO, ZERO]) != stated.get(key, [ZERO, ZERO]):
                raise ValueError("inventory disagrees with booked fills")
        reason = doc.get("halted_reason")
        if reason is not None and not isinstance(reason, str):
            raise ValueError("halted_reason is unreadable")
        return {"version": LEDGER_VERSION, "orders": orders,
                "inventory": inventory, "settled": settled,
                "halted_reason": reason, "halt_kind": doc.get("halt_kind")}

    def _save(self) -> None:
        """Temp file, flush, then one atomic replace. Raises on failure.
        Nothing is written when the text on disk is already this text."""
        if not self._ledger_ok:
            raise OSError("ledger was unreadable and is never overwritten")
        text = json.dumps(self._ledger, sort_keys=True)
        if text == self._on_disk:
            return
        folder = os.path.dirname(os.path.abspath(self.ledger_path))
        os.makedirs(folder, exist_ok=True)
        tmp = "%s.tmp.%d" % (self.ledger_path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.ledger_path)
        # the RENAME has to be durable too: the row written before a submit
        # is what says an order may exist, and a rename that a crash undoes
        # would leave a resting order this ledger never heard of
        try:
            dfd = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass                    # the file itself is already fsynced
        self._on_disk = text

    def _save_or_halt(self) -> bool:
        """Write the ledger. A failure halts submissions until a later write
        succeeds: memory is then ahead of disk, and a restart would forget."""
        if not self._ledger_ok:
            return False
        held = (self._ledger.get("halt_kind"), self._ledger.get("halted_reason"))
        if held[0] == HALT_WRITE:
            # the file must not say "write failed" once the write succeeded
            self._ledger["halt_kind"] = self._ledger["halted_reason"] = None
        try:
            self._save()
            return True
        except OSError as exc:
            if held[0] == HALT_WRITE:
                self._ledger["halt_kind"], self._ledger["halted_reason"] = held
            self._halt(HALT_WRITE, "LEDGER_WRITE_FAILED:%s" % type(exc).__name__)
            return False

    # -------------------------------------------------------------- reads

    def _now(self) -> Optional[dt.datetime]:
        return _utc(self._now_fn())

    def orders(self) -> Dict[str, Dict[str, Any]]:
        """Every order this executor ever created, by client order id."""
        return {c: dict(r) for c, r in self._ledger["orders"].items()}

    def live_orders(self) -> Dict[str, Dict[str, Any]]:
        """Our orders that may still be working: NEW, LIVE or UNCERTAIN."""
        return {c: dict(r) for c, r in self._ledger["orders"].items()
                if r["state"] in OPEN}

    def owns(self, client_order_id: Any) -> bool:
        return (isinstance(client_order_id, str)
                and client_order_id.startswith(COID_PREFIX)
                and client_order_id in self._ledger["orders"])

    def owns_venue_order_id(self, venue_order_id: Any) -> bool:
        """Is this VENUE order id one this ledger recorded for its own
        order? The venue's rows carry no client order id, so this -- not a
        prefix -- is what proves an order on the venue is ours. A runner
        installs it as the transport's cancel-ownership test."""
        if not self._ledger_ok or not isinstance(venue_order_id, str) \
                or not venue_order_id:
            return False
        return any(r.get("venue_order_id") == venue_order_id
                   for r in self._ledger["orders"].values())

    def day_settled_pnl(self, now: Any = None) -> Optional[Decimal]:
        """This strategy's settled result for the UTC day of `now`, from its
        own settlement records. None when it cannot be established: an
        unreadable ledger, an unreadable clock, or a settled row whose time
        or amount cannot be read. None refuses a submission; it is never
        read as zero."""
        at = _utc(now) if now is not None else self._now()
        if at is None or not self._ledger_ok:
            return None
        total = ZERO
        for rec in self._ledger["settled"].values():
            when = _parse_utc(rec.get("settled_at")) \
                if isinstance(rec, Mapping) else None
            pnl = _dec(rec.get("settlement_pnl")) \
                if isinstance(rec, Mapping) else None
            if when is None or pnl is None:
                return None
            if when.date() == at.date():
                total += pnl
        return total

    def _order_class(self, market: str, side: str, qty: Decimal) -> tuple:
        """(class, reducing) for the shared submission budgets.

        COMPLETE and reducing: the order buys the side we are SHORT of and,
        with what is already working on that side, does not exceed the
        unmatched quantity -- it can only reduce it. REPLACE: an order of
        ours on this side in this market was cancelled before. OPEN:
        everything else, the only class that adds exposure.
        """
        legs = self.inventory(market)
        other = C.DOWN if side == C.UP else C.UP
        unmatched = legs[other]["qty"] - legs[side]["qty"]
        working = sum((D(r["qty"]) - D(r["filled_qty"])
                       for r in self._ledger["orders"].values()
                       if r["market"] == market and r["side"] == side
                       and r["state"] in OPEN), ZERO)
        if unmatched > 0 and qty + working <= unmatched:
            return "COMPLETE", True
        replaced = any(r["market"] == market and r["side"] == side
                       and r["state"] == CANCELLED
                       for r in self._ledger["orders"].values())
        return ("REPLACE" if replaced else "OPEN"), False

    def inventory(self, market: str) -> Dict[str, Dict[str, Decimal]]:
        """{side: {"qty", "cost"}} for one market, from OUR fills only."""
        legs = self._ledger["inventory"].get(market) or {}
        return {side: {"qty": D(str((legs.get(side) or {}).get("qty", "0"))),
                       "cost": D(str((legs.get(side) or {}).get("cost", "0")))}
                for side in C.SIDES}

    def held_lots(self, market: str, side: str) -> List[tuple]:
        """Filled LOTS on one side as (price, qty), DEAREST FIRST.

        Derived from the order rows, the same source `attribution` uses --
        never a second ledger. Each filled order is one lot at its own
        average fill price, which is what makes a lot-level cost basis
        possible at all.
        """
        out = []
        for r in self._ledger["orders"].values():
            if r.get("market") != market or r.get("side") != side:
                continue
            q, c = _dec(r.get("filled_qty")), _dec(r.get("filled_cost"))
            if q is None or c is None or q <= 0:
                continue
            out.append((c / q, q))
        out.sort(key=lambda t: t[0], reverse=True)
        return out

    def dearest_basis(self, market: str, side: str,
                      qty: Decimal) -> Optional[Decimal]:
        """Weighted average price of the DEAREST `qty` contracts held.

        WHY NOT THE BLENDED AVERAGE. Completing N contracts retires N held
        contracts, and a blended average lets cheap inventory subsidise
        dear inventory inside one number: a side holding 5 at 0.08 and 5
        at 0.60 averages 0.34, so a completion priced against 0.34 looks
        profitable while the 0.60 lot it is actually retiring is not. The
        dearest lots are therefore the basis, which can only refuse
        completions the average would have allowed -- never the reverse.
        """
        want = _dec(qty)
        if want is None or want <= 0:
            return None
        taken, cost = ZERO, ZERO
        for px, q in self.held_lots(market, side):
            use = min(q, want - taken)
            if use <= 0:
                break
            taken += use
            cost += px * use
            if taken >= want:
                break
        if taken < want or taken <= 0:
            return None
        return cost / taken

    def attribution(self, market: str,
                    up_won: Optional[bool] = None) -> Dict[str, Any]:
        """Filled quantity, cost, fees and settled result PER STRATEGY.

        DERIVED FROM THE ORDER ROWS, never from a second ledger. Each
        share was bought by one order, which belongs to one strategy, and
        a winning share redeems $1 whoever bought it -- so this is EXACT
        arithmetic, not a pro-rata allocation.

        WHAT IS NOT ATTRIBUTABLE, stated because the omission is
        deliberate: `matched_qty` and `pair_cost`. A matched pair can be
        one strategy's leg against another's, so pairing is a property of
        the MARKET and is reported there, not split between strategies.
        """
        out: Dict[str, Any] = {}
        for r in self._ledger["orders"].values():
            if r.get("market") != market:
                continue
            q, c = _dec(r.get("filled_qty")), _dec(r.get("filled_cost"))
            if q is None or c is None or q <= 0:
                continue
            who = str(r.get("strategy") or UNATTRIBUTED)
            side = r.get("side")
            if side not in C.SIDES:
                continue
            row = out.setdefault(who, {s: {"qty": ZERO, "cost": ZERO}
                                       for s in C.SIDES})
            row[side]["qty"] += q
            row[side]["cost"] += c
        for who, row in out.items():
            cost = row[C.UP]["cost"] + row[C.DOWN]["cost"]
            row["cost_total"] = cost
            row["filled_qty"] = row[C.UP]["qty"] + row[C.DOWN]["qty"]
            if up_won is None:
                row["redemption_total"] = None
                row["settled_net_usd"] = None
            else:
                red = row[C.UP]["qty"] if up_won else row[C.DOWN]["qty"]
                row["redemption_total"] = red
                row["settled_net_usd"] = red - cost
        return out

    def _resting(self, market: str, now: Optional[dt.datetime]
                 ) -> List[Dict[str, Any]]:
        """Our possibly-working orders in `market`, as the controller reads
        them. An UNCERTAIN order is included: it may be resting."""
        out = []
        for coid, r in sorted(self._ledger["orders"].items()):
            if r["market"] != market or r["state"] not in OPEN:
                continue
            left = D(r["qty"]) - D(r["filled_qty"])
            if left <= 0:
                continue
            made = _parse_utc(r.get("created_at"))
            age = ((now - made).total_seconds()
                   if now is not None and made is not None else 0.0)
            out.append({"id": coid, "side": r["side"], "price": D(r["price"]),
                        "qty": left, "age_s": max(0.0, age)})
        return out

    def _seq(self, market: str) -> int:
        """How many of our orders in `market` have ended. The same intent
        keeps its id while its order is open and gets a new one after."""
        return sum(1 for r in self._ledger["orders"].values()
                   if r["market"] == market and r["state"] in TERMINAL)

    def coid(self, market: Any, seq: Any, side: Any, price: Any, qty: Any,
             slot: Any = 0) -> str:
        """This intent's one id. The SAME intent must hash the same, so a
        replayed decision is caught as DUPLICATE_INTENT instead of sent
        twice; a DIFFERENT intent must hash differently, or a legitimate
        order is refused as a duplicate.

        `slot` is which of the strategy's concurrent entries this is --
        always 0 outside ACCUMULATE, where accumulation deliberately rests
        several identical clips at once and `seq` alone cannot tell them
        apart. It is left out of the hashed string when it is 0, so every
        id this method produced before accumulation existed is unchanged.
        A slot is reused only after the order holding it went terminal,
        which has already moved `seq`, so no two orders collide."""
        px, q = _dec(price), _dec(qty)
        parts = [str(market), str(int(seq)), str(side),
                 _canon(px) if px is not None else str(price),
                 _canon(q) if q is not None else str(qty)]
        if int(slot) != 0:
            parts.append("s%d" % int(slot))
        raw = "|".join(parts)
        return COID_PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    # ------------------------------------------------------------- arming

    def arming_refusal(self, market: Any, now: Any) -> Optional[str]:
        """Why nothing may be submitted for `market` at `now`, or None."""
        if os.environ.get(LIVE_ENV) != ARM_TOKEN:
            return "NOT_ARMED"
        configured = os.environ.get(MARKET_ENV)
        if not configured:
            return "MARKET_NOT_CONFIGURED"
        if not isinstance(market, str) or market != configured:
            return "MARKET_MISMATCH"
        if not market.startswith(MARKET_PREFIX):
            return "NOT_A_BTC5M_MARKET"
        raw = os.environ.get(NOT_AFTER_ENV)
        if raw is None or not raw.strip():
            return "NOT_AFTER_MISSING"
        deadline = _parse_utc(raw)
        if deadline is None:
            return "NOT_AFTER_UNREADABLE"
        at = _utc(now)
        if at is None:
            return "NOW_UNREADABLE"
        if at > deadline:
            return "NOT_AFTER_PASSED"
        if live_halt_engaged():
            return HALT_ENV
        if shadow_only_engaged():
            return SHADOW_ONLY_ENV
        if self.halted_reason:
            return "LEDGER_HALTED:%s" % self.halted_reason
        if self.transport is None:
            return "TRANSPORT_MISSING"
        if getattr(self.transport, "armed", None) is not True:
            return "TRANSPORT_NOT_ARMED"
        return None

    # ------------------------------------------------------ account-wide

    def _own_exposure(self) -> Dict[str, Decimal]:
        """Our resting notional and filled cost, unsettled markets only."""
        resting = filled = ZERO
        for r in self._ledger["orders"].values():
            if r["market"] in self._ledger["settled"]:
                continue
            filled += D(r["filled_cost"])
            if r["state"] in OPEN:
                resting += (D(r["qty"]) - D(r["filled_qty"])) * D(r["price"])
        return {"resting": resting, "filled": filled}

    def account_exposure(self) -> Dict[str, Any]:
        """Dollars at risk on the whole account, every market.

        Ours is exact: this ledger's resting notional plus filled cost.
        Everything else the venue lists is charged at $1.00 a contract.
        `unknown` is True when either list could not be read in full, and
        an unknown account refuses every submission.
        """
        own = self._own_exposure()
        out: Dict[str, Any] = {
            "own_usd": own["resting"] + own["filled"],
            "own_resting_usd": own["resting"], "own_filled_cost_usd": own["filled"],
            "other_usd": ZERO, "other_open_order_contracts": ZERO,
            "other_position_contracts": ZERO, "other_basis": FOREIGN_BASIS,
            "unknown": False, "unknown_reason": None}

        def unknown(why: str) -> Dict[str, Any]:
            out["unknown"], out["unknown_reason"] = True, why
            out["total_usd"] = out["own_usd"] + out["other_usd"]
            return out

        t = self.transport
        if t is None:
            return unknown("TRANSPORT_MISSING")
        try:
            open_rows, pos_rows = t.open_orders(), t.positions()
        except Exception as exc:                               # noqa: BLE001
            return unknown("ACCOUNT_READ_FAILED:%s" % type(exc).__name__)
        if not isinstance(open_rows, (list, tuple)) or not isinstance(
                pos_rows, (list, tuple)):
            return unknown("ACCOUNT_ROWS_NOT_A_LIST")
        our_vids = {r["venue_order_id"] for r in self._ledger["orders"].values()
                    if r.get("venue_order_id")}
        for row in open_rows:
            if not isinstance(row, Mapping):
                return unknown("OPEN_ORDER_ROW_UNREADABLE")
            cid = _first(row, ("clientOrderId", "client_order_id"))
            vid = _first(row, ("id", "order_id", "orderId"))
            if self.owns(cid) or (vid is not None and str(vid) in our_vids):
                continue                       # already counted, exactly
            left = _dec(_first(row, ("remainingQuantity", "remaining_qty",
                                     "leavesQuantity", "qty", "quantity")))
            if left is None or left < 0:
                return unknown("OPEN_ORDER_QUANTITY_UNREADABLE")
            out["other_open_order_contracts"] += left
        for row in pos_rows:
            if not isinstance(row, Mapping):
                return unknown("POSITION_ROW_UNREADABLE")
            market = _first(row, ("marketSlug", "market_slug", "market", "slug"))
            if market is None or not str(market).strip():
                return unknown("POSITION_MARKET_UNREADABLE")
            bought, sold = _dec(row.get("qtyBought")), _dec(row.get("qtySold"))
            net = _dec(_first(row, ("netPositionDecimal", "netPosition",
                                    "net_position")))
            if bought is not None and sold is not None:
                held = abs(bought) + abs(sold)   # both outcome legs, gross
            elif net is not None:
                held = abs(net)
            else:
                return unknown("POSITION_QUANTITY_UNREADABLE")
            if str(market) not in self._ledger["settled"]:
                legs = self.inventory(str(market))
                held = max(ZERO, held - legs[C.UP]["qty"] - legs[C.DOWN]["qty"])
            out["other_position_contracts"] += held
        out["other_usd"] = FOREIGN_USD_PER_CONTRACT * (
            out["other_open_order_contracts"] + out["other_position_contracts"])
        out["total_usd"] = out["own_usd"] + out["other_usd"]
        return out

    # --------------------------------------------------------------- poll

    def _uncertain(self, coid: str, why: str) -> None:
        row = self._ledger["orders"][coid]
        row["state"], row["uncertain_reason"] = UNCERTAIN, why
        self._halt(HALT_ORDER, "%s:%s:%s" % (HALT_ORDER, coid, why))

    def _refresh_halt(self) -> None:
        """An order halt lifts only when no order is UNCERTAIN any more."""
        if self._ledger.get("halt_kind") != HALT_ORDER:
            return
        still = [c for c, r in sorted(self._ledger["orders"].items())
                 if r["state"] == UNCERTAIN]
        if not still:
            self._ledger["halt_kind"] = self._ledger["halted_reason"] = None
        else:
            r = self._ledger["orders"][still[0]]
            self._ledger["halted_reason"] = "%s:%s:%s" % (
                HALT_ORDER, still[0], r.get("uncertain_reason"))

    def _read(self, coid: str, fills: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Read one of OUR orders by venue id and book what it says.

        The venue's cumulative quantity is the authority; the delta against
        what is already booked is the fill, so reading twice books once.
        """
        row = self._ledger["orders"][coid]
        vid = row.get("venue_order_id")

        def bad(why: str) -> Dict[str, Any]:
            self._uncertain(coid, why)
            return {"ok": False, "terminal": False, "filled": ZERO, "error": why}

        if not vid:
            return bad("NO_VENUE_ORDER_ID")
        try:
            got = self.transport.order(vid)
        except Exception as exc:                               # noqa: BLE001
            return bad("ORDER_READ_FAILED:%s" % type(exc).__name__)
        if not isinstance(got, Mapping):
            return bad("ORDER_ROW_UNREADABLE")
        qty, booked = D(row["qty"]), D(row["filled_qty"])
        cum = _dec(got.get("cumQuantity"))
        if cum is None or cum < booked or cum > qty:
            return bad("ORDER_ROW_INCOHERENT")
        word = str(got.get("state") or "").upper()
        # a PENDING cancel has not ended anything yet
        ended = "PENDING" not in word and (
            any(w in word for w in ("CANCEL", "EXPIRED", "REJECTED"))
            or ("FILLED" in word and "PARTIAL" not in word))
        if got.get("leavesQuantity") is not None:
            leaves = _dec(got.get("leavesQuantity"))
            if leaves is None or leaves < 0:
                return bad("ORDER_ROW_INCOHERENT")
            if leaves == 0 and not (ended or cum == qty):
                # nothing left, yet not ended and not filled: no such order
                return bad("ORDER_ROW_INCOHERENT")
            terminal = leaves == 0
        else:
            terminal = ended or cum == qty
        # THE VENUE'S OWN COMMISSION ON THIS ORDER, when it states one.
        # Recorded as reported (cumulative per order) and never estimated.
        _fee = _dec(got.get("commissionNotionalTotalCollected"))
        if _fee is not None and _fee >= 0:
            row["venue_fees"] = _s(_fee)
        _avg = _dec(got.get("avgPx"))
        if _avg is not None:
            row["venue_avg_px_wire"] = _s(_avg)
        delta = cum - booked
        if delta > 0:
            price = D(row["price"])
            cost = delta * price
            row["filled_qty"] = _s(cum)
            row["filled_cost"] = _s(D(row["filled_cost"]) + cost)
            leg = self._ledger["inventory"].setdefault(
                row["market"], {}).setdefault(row["side"], {"qty": "0", "cost": "0"})
            leg["qty"] = _s(D(leg["qty"]) + delta)
            leg["cost"] = _s(D(leg["cost"]) + cost)
            fills.append({"client_order_id": coid, "venue_order_id": vid,
                          "market": row["market"], "side": row["side"],
                          "qty": delta, "price": price, "cost": cost})
        row["venue_state"] = word or None
        row["state"] = ((FILLED if cum == qty else CANCELLED) if terminal else LIVE)
        row.pop("uncertain_reason", None)
        return {"ok": True, "terminal": terminal, "filled": delta, "error": None}

    def _locate(self, coid: str) -> None:
        """Find the venue id of an order whose submit gave none, by OUR
        client order id in the venue's open list. A read; sends nothing."""
        try:
            rows = self.transport.open_orders()
        except Exception:                                      # noqa: BLE001
            return
        for row in rows if isinstance(rows, (list, tuple)) else ():
            if (isinstance(row, Mapping) and _first(
                    row, ("clientOrderId", "client_order_id")) == coid):
                vid = _first(row, ("id", "order_id", "orderId"))
                if vid:
                    self._ledger["orders"][coid]["venue_order_id"] = str(vid)
                return

    def poll(self) -> Dict[str, Any]:
        """Read every one of OUR open orders and book new fills."""
        out: Dict[str, Any] = {"fills": [], "read": [], "uncertain": [],
                               "error": None}
        if not self._ledger_ok:
            out["error"] = self.halted_reason
            return out
        open_now = [c for c, r in sorted(self._ledger["orders"].items())
                    if r["state"] in OPEN]
        if not open_now:
            return out
        if self.transport is None:
            out["error"] = "TRANSPORT_MISSING"
            return out
        for coid in open_now:
            row = self._ledger["orders"][coid]
            if not row.get("venue_order_id"):
                self._locate(coid)
            if not row.get("venue_order_id"):
                # nothing to ask the venue by: it stays UNCERTAIN, with the
                # reason it was given when its submit went unanswered
                out["uncertain"].append(coid)
                continue
            res = self._read(coid, out["fills"])
            (out["read"] if res["ok"] else out["uncertain"]).append(coid)
        self._refresh_halt()
        self._save_or_halt()
        return out

    # ------------------------------------------------------------- cancel

    def _cancel(self, coid: Any, now: Optional[dt.datetime],
                fills: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Cancel ONE order of ours by its exact venue id, then re-read it.

        The cancel's return value decides nothing: only the re-read does.
        A fill that beat the cancel is booked by that same re-read.
        """
        if not self.owns(coid):
            return {"id": coid, "refused": "NOT_OWNED"}
        row = self._ledger["orders"][coid]
        out: Dict[str, Any] = {"id": coid, "venue_order_id": row.get("venue_order_id"),
                               "market": row["market"], "side": row["side"],
                               "refused": None, "sent": False, "terminal": False,
                               "filled": ZERO, "cancel_error": None}
        if row["state"] in TERMINAL:
            out["terminal"] = True
            return out
        vid = row.get("venue_order_id")
        if not vid:
            out["refused"] = "NO_VENUE_ORDER_ID"
            return out
        if self.transport is None:
            out["refused"] = "TRANSPORT_MISSING"
            return out
        sent_at = _parse_utc(row.get("cancel_sent_at"))
        due = (sent_at is None or row.get("cancel_error") is not None
               or now is None
               or (now - sent_at).total_seconds() >= CANCEL_RESEND_S)
        if due:
            row["cancel_requested"] = True
            row["cancel_sent_at"] = now.isoformat() if now is not None else None
            try:
                self.transport.cancel(vid, market_slug=row["market"])
                row["cancel_error"] = None
            except Exception as exc:                           # noqa: BLE001
                # not a failure yet: the venue may already have retired it
                row["cancel_error"] = type(exc).__name__
            out["sent"], out["cancel_error"] = True, row["cancel_error"]
        res = self._read(coid, fills)
        out["terminal"], out["filled"] = res["terminal"], res["filled"]
        out["state"], out["read_error"] = row["state"], res["error"]
        return out

    def _cancel_pending(self, market: str) -> List[str]:
        """Our orders in `market` asked to cancel and not yet read terminal."""
        return [c for c, r in sorted(self._ledger["orders"].items())
                if r["market"] == market and r["state"] in OPEN
                and r.get("cancel_requested")]

    # --------------------------------------------------------------- place

    @staticmethod
    def _order_fault(order: Mapping[str, Any],
                     taker_ok: bool = False) -> Optional[str]:
        """Why this PLACE can never be sent by this adapter, or None.

        `taker_ok` defaults False, so every existing caller keeps the
        unconditional maker-only block. It is True only when the env
        switch is on, the order is a COMPLETION, and its all-in pair
        economics passed against executable depth -- see
        `completion_taker_fault`. The standing envelope refuses a
        crossing order separately in `may_submit`, so this is one of
        three gates and not the only one.
        """
        if order.get("post_only") is not True and not taker_ok:
            return "TAKER_NEVER_SENT"
        if order.get("intent") != "BUY":
            return "ONLY_MAKER_BUY_SENT"
        if order.get("side") not in C.SIDES:
            return "SIDE_UNREADABLE"
        price, qty = _dec(order.get("price")), _dec(order.get("qty"))
        if price is None or qty is None:
            return "ORDER_UNREADABLE"
        if not (PRICE_MIN <= price <= PRICE_MAX) or price % CENT != 0:
            return "PRICE_OFF_GRID"
        if qty < MIN_QTY:
            return "QTY_BELOW_MIN"
        if qty % CENT != 0:
            return "QTY_OFF_GRID"
        return None

    def _place(self, market: str, order: Mapping[str, Any],
               now: Optional[dt.datetime], report: Dict[str, Any]) -> None:
        def refuse(why: str, **more: Any) -> None:
            report["refused"].append(dict(more, order=dict(order), reason=why))

        #: THE CROSSING PERMISSION, computed here and nowhere else. A
        #: maker order never reaches any of this. Held inventory comes
        #: from our OWN ledger so the first leg's cost cannot be supplied
        #: by the caller.
        #: ONLY A COMPLETION ENTERS THE NEW PATH. Any other crossing order
        #: -- a SELL, an exit, a flip, a taker entry -- falls through to
        #: `_order_fault` and keeps its original TAKER_NEVER_SENT refusal,
        #: unchanged. Narrowing it here rather than inside the gate keeps
        #: every pre-existing refusal reason exactly as it was.
        taker_ok = False
        if (order.get("post_only") is not True
                and str(order.get("reason")) in TAKER_COMPLETION_REASONS):
            if not taker_completion_allowed():
                return refuse("TAKER_COMPLETION_DISABLED")
            inv = self.inventory(market)
            opp = C.DOWN if order.get("side") == C.UP else C.UP
            leg = inv.get(opp) or {}
            held_qty = _dec(leg.get("qty")) or ZERO
            want = _dec(order.get("qty")) or ZERO
            #: DEAREST-LOT basis for exactly the quantity being completed
            basis = self.dearest_basis(market, opp, want)
            #: our own working quantity on the COMPLETING side
            working = sum((o["qty"] for o in self._resting(market, now)
                           if o.get("side") == order.get("side")), ZERO)
            tf = completion_taker_fault(order, held_qty, basis, working)
            if tf:
                return refuse(tf)
            taker_ok = True
        fault = self._order_fault(order, taker_ok)
        if fault:
            return refuse(fault)
        side, price, qty = order["side"], _dec(order["price"]), _dec(order["qty"])
        slot = order.get("slot") or 0
        coid = self.coid(market, self._seq(market), side, price, qty, slot)
        if coid in self._ledger["orders"]:
            return refuse("DUPLICATE_INTENT", client_order_id=coid)
        pending = self._cancel_pending(market)
        if pending:
            return refuse("CANCEL_PENDING", waiting_on=pending)
        if len(self.live_orders()) >= self.max_live_orders:
            return refuse("MAX_LIVE_ORDERS")
        exp = C.exposure(self.inventory(market), self._resting(market, now))
        notional = price * qty
        if exp is None:
            return refuse("EXPOSURE_UNREADABLE")
        if exp["total_exposure_usd"] + notional > self.params.max_exposure_usd:
            return refuse("BTC5M_EXPOSURE_CAP",
                          exposure_usd=exp["total_exposure_usd"],
                          cap_usd=self.params.max_exposure_usd)
        intent = {"client_order_id": coid, "market": market, "side": side,
                  "price": _s(price), "qty": _s(qty), "mode": "MAKER",
                  "post_only": True, "action": "BUY",
                  "reason": order.get("reason")}
        blocked = self.arming_refusal(market, now)
        if blocked:
            report["would_submit"].append(dict(intent, blocked_by=blocked))
            return None
        cap = account_cap()
        if cap is None:
            return refuse("ACCOUNT_CAP_UNSET_OR_UNREADABLE")
        lanes = account_lanes()
        lcap = lane_cap()
        if lanes is None or lcap is None:
            return refuse("ACCOUNT_LANES_UNREADABLE",
                          lanes_raw=str(os.environ.get(ACCOUNT_LANES_ENV, "")))
        acct = self.account_exposure()
        report["exposure"]["account"] = acct
        if acct["unknown"]:
            return refuse("ACCOUNT_STATE_UNKNOWN", detail=acct["unknown_reason"])
        mode = foreign_exposure_mode()
        report["exposure"]["foreign_mode"] = mode
        if mode == FOREIGN_REPORTED:
            #: OUR exposure against the owner's ceiling, and the venue's own
            #: buying power against what we are about to commit. Foreign
            #: inventory is reported above and charged to nobody here.
            #: THIS LANE'S SHARE, not the whole ceiling. `own_usd` is read
            #: from this lane's own ledger and sees none of its siblings'
            #: inventory, so checking it against the undivided cap let
            #: every lane claim the account independently. The CHARGED
            #: branch below keeps the undivided cap on purpose: there a
            #: sibling lane IS foreign inventory and is already counted.
            report["exposure"]["account_lanes"] = lanes
            report["exposure"]["lane_cap_usd"] = _s(lcap)
            if acct["own_usd"] + notional > lcap:
                return refuse("ACCOUNT_EXPOSURE_CAP",
                              total_usd=acct["own_usd"], cap_usd=lcap,
                              account_cap_usd=_s(cap), account_lanes=lanes,
                              foreign_mode=mode,
                              other_usd_reported=_s(acct["other_usd"]))
            bp = _buying_power(self.transport)
            report["exposure"]["buying_power_usd"] = (None if bp is None
                                                      else _s(bp))
            if bp is None:
                return refuse("ACCOUNT_CASH_UNREADABLE")
            if acct["own_usd"] + notional > bp:
                return refuse("ACCOUNT_CASH_SHORT",
                              own_usd=_s(acct["own_usd"]),
                              buying_power_usd=_s(bp))
        elif acct["total_usd"] + notional > cap:
            return refuse("ACCOUNT_EXPOSURE_CAP", total_usd=acct["total_usd"],
                          other_usd=acct["other_usd"], cap_usd=cap)
        # THE AUTHORIZATION CLAIM. The owner's approval for THIS 5-minute
        # window, the strategy's own exposure and day-result limits from
        # that approval, the shared submission budgets, and this order's
        # single-use lock -- created and fsynced before anything is sent,
        # and never returned by a cancel, a fill, a restart or a rejection.
        # A refusal sends nothing and writes no order row.
        klass, reducing = self._order_class(market, side, qty)
        try:
            claim = AUTH.claim_btc5m_submission(
                market, coid,
                {"side": side, "price": _s(price), "qty": _s(qty),
                 "reason": order.get("reason")},
                worst_case_usd=exp["total_exposure_usd"] + notional,
                day_realized_usd=self.day_settled_pnl(now),
                klass=klass, reducing=reducing, now=now)
        except AUTH.Refused as exc:
            return refuse("AUTHORIZATION:%s" % exc.reason,
                          client_order_id=coid, klass=klass,
                          detail=exc.detail)
        except Exception as exc:                               # noqa: BLE001
            return refuse("AUTHORIZATION_UNAVAILABLE", client_order_id=coid,
                          detail=type(exc).__name__)
        #: THE STANDING ENVELOPE, when one is configured. Checked BEFORE
        #: the reservation because it has no side effect: a scope or
        #: daily-loss refusal then leaks no hold. It enforces what the
        #: reservation cannot -- which strategy, which market, which
        #: DEPLOYMENT, maker-only, the per-market ceiling, and the
        #: worst-case daily loss counting realized losses PLUS everything
        #: already at risk PLUS this order.
        #:
        #: THE ACCOUNT-WIDE FIGURE IT JUDGES IS THE SHARED RESERVATION's,
        #: not this lane's ledger: that is the whole point, since this
        #: lane's books cannot see its siblings.
        #:
        #: An envelope that is absent leaves the bounded path exactly as
        #: it was; one that is present and UNUSABLE refuses.
        env_raw = os.environ.get(PMODE.ENVELOPE_ENV)
        if env_raw is not None and str(env_raw).strip():
            agg = RESV.aggregate()
            if not agg.get("ok"):
                return refuse("SHARED_RESERVATION:%s" % agg.get("reason"))
            gate = PMODE.may_submit(
                market=market, strategy=_strategy_of(order),
                post_only=intent.get("post_only"),
                notional_usd=notional,
                market_exposure_usd=exp["total_exposure_usd"],
                aggregate_exposure_usd=agg["usd"],
                day_realized_usd=self.day_settled_pnl(now),
                now=now)
            report["exposure"]["envelope"] = {
                k: v for k, v in gate.items() if k != "ok"}
            if not gate.get("ok"):
                return refuse("ENVELOPE:%s" % gate.get("reason"),
                              client_order_id=coid,
                              detail={k: v for k, v in gate.items()
                                      if k not in ("ok", "reason")})
        #: AFTER THE OWNER'S CLAIM, BEFORE THE VENUE. Authorization
        #: precedes risk: shared budget is not spent on an order
        #: nobody authorized. The cost is that a full budget burns
        #: one single-use claim, which is bounded and shows in the
        #: submission budget -- the other order would consume the
        #: budget on unauthorized attempts instead.
        #: THE ACCOUNT-WIDE RESERVATION, and the only place every lane
        #: meets. The four lanes are separate processes with separate
        #: ledgers, so `own_usd` above is this lane's alone: four lanes
        #: each clearing a $10 cap against their own books commit $40
        #: between them. This takes the worst case -- the WHOLE outlay,
        #: because a losing contract pays nothing -- out of one shared
        #: budget under an exclusive lock, before the venue is called.
        #:
        #: It is released by syncing to this ledger each step, never by a
        #: timer: an order whose outcome is unknown keeps holding its
        #: risk, because an uncertain order IS exposure.
        #: THE CAP THE HOLD ENFORCES IS THE EFFECTIVE ONE, and this is
        #: the difference between a bound and a hope.
        #:
        #: OBSERVED in the four-process test: `may_submit` checks the
        #: worst-case daily loss against an aggregate it READ A MOMENT
        #: AGO, outside the lock. Four lanes each passed that check on a
        #: stale figure and together reserved $8 against a $5 daily-loss
        #: budget. Every individual check was correct and the collective
        #: result was not.
        #:
        #: So the daily-loss room is folded into the cap the RESERVATION
        #: enforces, where it is evaluated under the same exclusive hold
        #: as the aggregate. Only then is "$5 daily loss" a ceiling on
        #: worst-case loss rather than a statement about settled losses.
        _cap = RESV.MAX_AGGREGATE_USD
        _env = PMODE.envelope(now=now)
        if _env.get("ok"):
            _scope = _env["scope"]
            _day = self.day_settled_pnl(now)
            if _day is None:
                return refuse("DAY_RESULT_UNREADABLE", client_order_id=coid)
            _lost = -_day if _day < ZERO else ZERO
            _room = _scope["max_daily_loss_usd"] - _lost
            _cap = min(_scope["max_aggregate_usd"], _room)
            if _cap <= ZERO:
                return refuse("WORST_CASE_DAILY_LOSS_EXHAUSTED",
                              client_order_id=coid,
                              detail={"realized_today_usd": _s(_day),
                                      "halt_usd": _s(_scope["max_daily_loss_usd"])})
        resv = RESV.reserve(lane=STRATEGY, key=coid, usd=notional,
                            now=now, cap_usd=_cap)
        report["exposure"]["reservation"] = {
            k: v for k, v in resv.items() if k != "ok"}
        if not resv.get("ok"):
            return refuse("SHARED_RESERVATION:%s" % resv.get("reason"),
                          client_order_id=coid,
                          detail={k: v for k, v in resv.items()
                                  if k not in ("ok", "reason")})
        # THE LEDGER NEXT. If this write fails nothing is sent.
        self._ledger["orders"][coid] = {
            "client_order_id": coid, "market": market, "side": side,
            "price": _s(price), "qty": _s(qty), "venue_order_id": None,
            "state": NEW, "filled_qty": "0", "filled_cost": "0",
            "created_at": now.isoformat() if now is not None else None,
            "reason": order.get("reason"), "slot": int(slot),
            "klass": klass, "claim_sid": claim.get("sid"),
            #: WHICH STRATEGY BOUGHT THIS. On the durable order row, not
            #: in memory, so attribution survives a restart -- and on the
            #: ORDER rather than the inventory aggregate, so a partial
            #: fill attributes exactly what filled and a fill that races a
            #: cancel attributes to whoever placed it.
            "strategy": _strategy_of(order)}
        if not self._save_or_halt():
            del self._ledger["orders"][coid]
            return refuse("LEDGER_WRITE_FAILED")
        try:
            ack = self.transport.submit(dict(intent))
        except Exception as exc:                               # noqa: BLE001
            # sent or not, we cannot tell. Never sent again.
            self._uncertain(coid, "SUBMIT_RAISED:%s" % type(exc).__name__)
            self._save_or_halt()
            return refuse("SUBMIT_UNCERTAIN", client_order_id=coid,
                          detail=type(exc).__name__)
        vid = None
        if isinstance(ack, Mapping):
            vid = _first(ack, ("order_id", "id", "orderId"))
            if vid is None and isinstance(ack.get("order"), Mapping):
                vid = _first(ack["order"], ("id", "order_id", "orderId"))
        if not vid:
            self._uncertain(coid, "SUBMIT_RETURNED_NO_ORDER_ID")
            self._save_or_halt()
            return refuse("SUBMIT_UNCERTAIN", client_order_id=coid,
                          detail="NO_ORDER_ID")
        row = self._ledger["orders"][coid]
        row["venue_order_id"], row["state"] = str(vid), LIVE
        self._save_or_halt()
        report["placed"].append(dict(intent, venue_order_id=str(vid)))
        return None

    # ---------------------------------------------------------------- step

    def effective_params(self, ceiling_usd: Any = None) -> "C.Params":
        """The controller's parameters with the ceiling that actually binds.

        The controller budgets entries against `max_exposure_usd`; left at
        its $5.00 default under a $0.45 account cap, entries could use the
        whole real cap and leave nothing for the completing order, which
        `_place` would then refuse. So the controller is given the smallest
        of: its own ceiling, the account cap (`L5_ACCOUNT_MAX_EXPOSURE_USD`)
        and the caller's approved ceiling for this window, and it reserves
        the completion cost inside that ("COMPLETION" mode). This only ever
        LOWERS the budget the controller plans with; every hard check in
        `_place` (strategy cap, account-wide cap, the authorization claim)
        is unchanged and still applies to each order."""
        eff = self.params.max_exposure_usd
        for c in (account_cap(), _dec(ceiling_usd)):
            if c is not None:
                eff = min(eff, c)
        over: Dict[str, Any] = {"max_exposure_usd": max(ZERO, eff)}
        mode = entry_mode()
        if mode == C.ACCUMULATE:
            # FAVORITE ACCUMULATION. A directional inventory: no completing
            # order is ever placed, so there is nothing to reserve for and
            # entries may use the whole binding ceiling. The ceiling itself
            # is NOT raised -- it is still the smallest of the adapter's
            # own, the account cap and the owner's approved figure, and the
            # worst case of a one-sided book is exactly that ceiling, which
            # is what the runner already checks against the account's loss
            # room before it arms.
            over.update(entry_mode=C.ACCUMULATE, hedge_enabled=False,
                        reserve_mode="NONE",
                        clip_usd=clip_usd(self.params.clip_usd),
                        max_resting_entries=max_resting_entries(
                            self.params.max_resting_entries))
        elif mode == C.STRADDLE:
            # FIXED TWO-SIDED LIMITS. No favorite, no band, no requote, and
            # no completion: both legs are opened deliberately, so there is
            # nothing to reserve for. The price is NOT derived from the
            # book -- that is the point, since a fixed limit cannot drift
            # and the pair cost is therefore known before either leg fills.
            # `hedge_enabled` stays False ON PURPOSE even when a
            # completion is configured. The hedge path CANCELS anything on
            # the hedge side that is not at its own price, which would kill
            # the cheap rung; measured on 183 windows that is worse than
            # doing nothing (-1.33 c/window against +2.86). The completion
            # below is additive and lives in the straddle branch instead.
            over.update(entry_mode=C.STRADDLE, hedge_enabled=False,
                        reserve_mode="NONE",
                        #: THE GATE'S RATES, which had no reader and so
                        #: refused the mode on every step. Still refusing
                        #: unless the owner sets them.
                        straddle_p_pair_natural=straddle_probability(
                            STRADDLE_P_PAIR_NATURAL_ENV,
                            self.params.straddle_p_pair_natural),
                        straddle_p_complete=straddle_probability(
                            STRADDLE_P_COMPLETE_ENV,
                            self.params.straddle_p_complete),
                        straddle_p_lone_win=straddle_probability(
                            STRADDLE_P_LONE_WIN_ENV,
                            self.params.straddle_p_lone_win),
                        straddle_fee_per_share=straddle_fee_per_share(
                            self.params.straddle_fee_per_share),
                        straddle_min_ev_per_share=straddle_min_ev_per_share(
                            self.params.straddle_min_ev_per_share),
                        straddle_completion_pair_cost=completion_pair_cost(
                            self.params.straddle_completion_pair_cost),
                        #: HOW that completion is executed. Driven by the
                        #: SAME owner switches as the adapter's crossing
                        #: gate on purpose: one variable to set, two
                        #: independent places that enforce it. The
                        #: controller decides against the book it was
                        #: given; `completion_taker_fault` re-verifies
                        #: against the live book at submit time with its
                        #: own depth haircut and notional cap, and either
                        #: refusing is enough to keep the order resting.
                        #: BOOK-RELATIVE PLACEMENT. Readers exist so
                        #: these cannot sit at their dataclass defaults
                        #: forever -- the defect that kept the entry-EV
                        #: rates unset and the late favourite silent.
                        inventory_ranking_enabled=(
                            inventory_ranking_enabled(
                                self.params.inventory_ranking_enabled)),
                        max_completion_ev_giveup_usd=(
                            completion_ev_giveup_usd(
                                self.params.max_completion_ev_giveup_usd)),
                        straddle_book_relative=straddle_book_relative(
                            self.params.straddle_book_relative),
                        straddle_join_ticks=straddle_join_ticks(
                            self.params.straddle_join_ticks),
                        straddle_pair_ceiling=straddle_pair_ceiling(
                            self.params.straddle_pair_ceiling),
                        straddle_completion_reserve=(
                            straddle_completion_reserve(
                                self.params.straddle_completion_reserve)),
                        straddle_fill_model_measured=(
                            straddle_fill_model_measured(
                                self.params.straddle_fill_model_measured)),
                        completion_cross_enabled=taker_completion_allowed(),
                        completion_cross_slippage_ticks=taker_slippage_ticks(),
                        completion_cross_min_margin=taker_min_margin(),
                        straddle_price=straddle_price(
                            self.params.straddle_price),
                        straddle_rungs=straddle_rungs(
                            self.params.straddle_rungs),
                        #: THE REPETITION BOUNDS RIDE HERE for the same
                        #: reason the late-favorite's four do: a param
                        #: with no env reader sits at its dataclass
                        #: default forever, and "refused every time"
                        #: reads identically to "no opportunity" in a log.
                        straddle_repeat_enabled=straddle_repeat_enabled(
                            self.params.straddle_repeat_enabled),
                        straddle_max_side_usd=straddle_max_side_usd(
                            self.params.straddle_max_side_usd),
                        straddle_max_unmatched_usd=(
                            straddle_max_unmatched_usd(
                                self.params.straddle_max_unmatched_usd)),
                        straddle_reprice_ticks_per_clip=(
                            straddle_reprice_ticks(
                                self.params.straddle_reprice_ticks_per_clip)),
                        clip_usd=clip_usd(self.params.clip_usd,
                                          STRADDLE_CLIP_MAX_USD),
                        #: THE LATE-FAVORITE RIDES IN THIS BRANCH, because
                        #: `decide()` evaluates it inside the straddle arm
                        #: and the arbiter chooses between the two. Without
                        #: these four it is evaluated and refuses every
                        #: time, which reads identically to "no
                        #: opportunity" in a log.
                        late_favorite_window_s=late_favorite_window_s(
                            self.params.late_favorite_window_s),
                        late_favorite_clip_usd=late_favorite_clip_usd(
                            self.params.late_favorite_clip_usd),
                        late_favorite_min_edge_per_share=(
                            late_favorite_min_edge_per_share(
                                self.params.late_favorite_min_edge_per_share)),
                        late_favorite_fee_per_share=late_favorite_fee_per_share(
                            self.params.late_favorite_fee_per_share))
        else:
            over.update(entry_mode=C.EDGE_GATED, reserve_mode="COMPLETION")
        return type(self.params)(**dict(vars(self.params), **over))

    def stop(self, *, reason: Any, now: Optional[dt.datetime] = None
             ) -> Dict[str, Any]:
        """The operator STOP. Latch first, then unwind. IDEMPOTENT.

        THE LATCH IS WRITTEN BEFORE ANYTHING IS CANCELLED, and the order
        matters more than it looks. Cancellation talks to a venue and can
        fail, hang or race a fill; if the latch came second, a STOP whose
        cancels failed would leave a lane still able to submit. Latching
        first means the worst case is "stopped with orders we could not
        withdraw" -- bad, and visible -- rather than "still trading".
        The latch is durable, so it survives the restart that a hung
        cancel might otherwise invite.

        IT NEVER BELIEVES A CANCEL REQUEST. `_cancel` sends by exact
        venue id and then RE-READS the order; only the re-read decides,
        and a fill that beat the cancel is booked by that same re-read.
        So a partial fill or a fill racing the cancellation is reconciled
        rather than lost, and an order the venue will not confirm
        terminal is reported as still open instead of assumed gone.

        HOLDS ARE NOT RELEASED BY STOPPING. An order whose state is
        uncertain, and inventory that has not settled, both still carry
        risk; freeing their budget here would let a restart re-spend
        money that is still at stake. `sync_reservation` releases only
        what the ledger shows is genuinely finished.

        FAILS CLOSED. `ok` is True only when every order of ours reached
        a terminal state AND the latch was written. Anything else leaves
        `ok` False with the orders named, and the lane stays latched.
        """
        at = _utc(now) if now is not None else self._now()
        latched = PMODE.write_state(PMODE.STOPPED, why=reason, now=at)
        out: Dict[str, Any] = {
            "ok": False, "latched": bool(latched.get("ok")),
            "latch_reason": latched.get("reason"),
            "cancelled": [], "still_open": [], "refused": [],
            "fills_during_stop": [], "reservation": None,
            "label": ("STOP latches first and never treats a cancel request "
                      "as a confirmed cancellation: only the re-read of each "
                      "order decides, and holds stay on anything still at "
                      "risk")}
        fills: List[Dict[str, Any]] = []
        #: a snapshot, because cancelling mutates the ledger as it goes
        open_now = [c for c, r in sorted(self._ledger["orders"].items())
                    if r.get("state") in OPEN]
        for coid in open_now:
            res = self._cancel(coid, at, fills)
            if res.get("refused"):
                out["refused"].append({"id": coid,
                                       "reason": res["refused"]})
                continue
            if res.get("filled", ZERO) > 0:
                out["fills_during_stop"].append(
                    {"id": coid, "qty": _s(res["filled"])})
            if res.get("terminal"):
                out["cancelled"].append(coid)
            else:
                out["still_open"].append(coid)
        if fills:
            out["fills_during_stop"].append({"booked": len(fills)})
        self._refresh_halt()
        saved = self._save_or_halt()
        out["ledger_saved"] = bool(saved)
        #: release only what is genuinely finished; uncertain orders and
        #: unsettled inventory keep their holds.
        out["reservation"] = self.sync_reservation()
        out["ok"] = bool(latched.get("ok")) and saved \
            and not out["still_open"] and not out["refused"]
        return out

    def pending_settlement(self) -> List[str]:
        """Markets we hold fills in that the venue has not settled for us.

        DERIVED, NOT STORED. A window is pending exactly when it has
        filled orders and is absent from `settled`, both of which are
        already on the durable ledger -- so the pending set survives a
        restart with no second place to forget to write, and a crash
        between closing a window and settling it loses nothing.
        """
        settled = self._ledger["settled"]
        out = set()
        for r in self._ledger["orders"].values():
            q = _dec(r.get("filled_qty")) or ZERO
            m = r.get("market")
            if q > 0 and m and m not in settled:
                out.add(str(m))
        return sorted(out)

    def sync_reservation(self) -> Dict[str, Any]:
        """Release this lane's holds whose orders are no longer live.

        A HOLD OUTLIVES ITS ORDER. An order that FILLED has left OPEN,
        but its cost is now inventory we still own, and a losing contract
        pays nothing -- so releasing on terminal order state would drop
        exactly the risk that just became real. A hold is kept while the
        order is open OR it filled into a market that has not settled.

        ONE RULE FOR EVERY AWKWARD ENDING. A partial fill, a cancel that
        raced a fill, a submission that timed out, a process that
        restarted -- all are just "does this order still carry risk,
        according to the durable ledger", so none needs its own release
        call and none can be forgotten on a path nobody thought about. It
        touches only this lane's holds, so a sibling's room is never
        freed here.
        """
        settled = self._ledger["settled"]
        live = []
        for c, r in self._ledger["orders"].items():
            if r.get("state") in OPEN:
                live.append(c)                       # may still fill
                continue
            filled = _dec(r.get("filled_qty")) or ZERO
            if filled > 0 and r.get("market") not in settled:
                live.append(c)                       # inventory at risk
        return RESV.reconcile(lane=STRATEGY, live_keys=live)

    def step(self, *, market: str, book: Any, t_in_window_s: Any,
             p_up: Any = None, p_source: Any = None,
             p_age_s: Any = None, loss_room_usd: Any = None,
             exposure_ceiling_usd: Any = None) -> Dict[str, Any]:
        """Poll, decide, cancel, place. One pass; returns what happened."""
        now = self._now()
        report: Dict[str, Any] = {
            "market": market, "action": None, "why": None, "placed": [],
            "cancelled": [], "refused": [], "would_submit": [], "fills": [],
            "inventory": None, "exposure": {}, "halted_reason": None,
            "arming_refusal": None}

        def done() -> Dict[str, Any]:
            report["arming_refusal"] = self.arming_refusal(market, now)
            if self._ledger_ok:
                report["inventory"] = self.inventory(market)
                report["exposure"]["btc5m"] = C.exposure(
                    report["inventory"], self._resting(market, now))
            report["halted_reason"] = self.halted_reason
            #: WHERE THE RESIDUAL IS, as one named phase. REPORTED, not
            #: enforced: uncertainty is already enforced upstream by the
            #: HALT_ORDER latch and the CANCEL_PENDING refusal, and a
            #: second enforcement point over the same facts is how two
            #: records of one truth start disagreeing. This is the
            #: observable an operator reads.
            if self._ledger_ok:
                try:
                    from src import l5_residual_phase as _rp
                    report["residual_phase"] = _rp.phase(
                        orders=[r for r in self._ledger["orders"].values()
                                if r.get("market") == market],
                        inventory=report["inventory"] or {},
                        halted=bool(self.halted_reason),
                        window_closed=bool(report.get("window_closed")),
                        chosen_action=report.get("action"))
                except Exception as exc:                   # noqa: BLE001
                    #: an observable must never be able to fail a step
                    report["residual_phase"] = {
                        "phase": None,
                        "why": "PHASE_UNAVAILABLE:%s" % type(exc).__name__}
            return report

        if not self._ledger_ok or not isinstance(market, str) or not market:
            report["action"] = "HALTED" if not self._ledger_ok else "MARKET_UNREADABLE"
            report["why"] = self.halted_reason
            return done()
        if self._ledger.get("halt_kind") == HALT_WRITE:
            self._save_or_halt()                  # the disk may be back
        # 1. the venue's word on our orders, before anything is decided
        report["fills"].extend(self.poll()["fills"])
        # 2. the pure decision, on the ledger's inventory and resting orders
        try:
            decision = C.decide(
                book=book, t_in_window_s=t_in_window_s,
                inventory=self.inventory(market),
                resting=self._resting(market, now),
                params=self.effective_params(exposure_ceiling_usd),
                p_up=p_up, p_source=p_source,
                #: BOTH OF THESE WERE DROPPED HERE, and the late
                #: favourite cannot clear its own gates without them.
                #: `decide` defaults each to None; `LF.evaluate` then
                #: refuses PROBABILITY_STALE on an unreadable age and
                #: BUDGET_UNREADABLE on an unreadable loss room. Worse
                #: than refusing, it refused INVISIBLY: `why_s` keeps
                #: whatever the straddle decided, so the step reported
                #: ENTRY_EV_P_PAIR_NATURAL_NOT_SET and pointed the
                #: investigation at the straddle's parameters while the
                #: real cause was a value this signature never carried.
                p_age_s=p_age_s, loss_room_usd=loss_room_usd)
        except Exception as exc:                               # noqa: BLE001
            report["action"], report["why"] = "CONTROLLER_ERROR", type(exc).__name__
            return done()
        report["action"], report["why"] = decision["action"], decision["why"]
        report["entry_eval"] = {k: (_s(v) if isinstance(v, Decimal) else v)
                                for k, v in ((decision.get("evaluation") or {})
                                             .get("entry") or {}).items()}
        report["hedge_eval"] = {k: (_s(v) if isinstance(v, Decimal) else v)
                                for k, v in ((decision.get("evaluation") or {})
                                             .get("hedge") or {}).items()}
        #: THE MISSED OPPORTUNITIES, which were being thrown away.
        #:
        #: Only `entry` and `hedge` were lifted out of the decision, so
        #: the late favourite's own evaluation never reached the step
        #: report or the log. That evaluation is the ONLY record of a
        #: qualifying opportunity we did not take: it carries the side,
        #: the favourite's ask and depth, and `considered` -- every one
        #: of 0.97/0.98/0.99 with its EV per share, its break-even
        #: probability and, when refused, whether it WOULD_CROSS or fell
        #: EDGE_BELOW_MARGIN.
        #:
        #: Without it a window that passed up a fill is indistinguishable
        #: from a window that had nothing to pass up, and the adverse-
        #: selection question -- were the ones we skipped the ones that
        #: would have lost? -- cannot be asked of the tape afterwards.
        #: The arbiter's choice goes beside it for the same reason: a
        #: candidate refused because a straddle rung outranked it is a
        #: different fact from one that never qualified.
        _ev = decision.get("evaluation") or {}
        report["late_eval"] = {
            k: (_s(v) if isinstance(v, Decimal) else v)
            for k, v in (_ev.get("late_favorite") or {}).items()}
        report["arbiter_eval"] = {
            k: (_s(v) if isinstance(v, Decimal) else v)
            for k, v in (_ev.get("arbiter") or {}).items()}
        if decision["action"] == C.DISABLED:
            return done()
        todo = [o for o in decision["orders"] if isinstance(o, Mapping)]
        # 3. cancels: only ours, by exact id, each proved by a re-read
        raced = False
        for op in (o for o in todo if o.get("op") == "CANCEL"):
            res = self._cancel(op.get("id"), now, report["fills"])
            if res.get("refused"):
                report["refused"].append({"order": dict(op),
                                          "reason": res["refused"]})
                continue
            report["cancelled"].append(res)
            raced = raced or res["filled"] > 0
        if report["cancelled"]:
            self._refresh_halt()
            self._save_or_halt()
        # 4. places. A fill found while cancelling changed the inventory the
        #    decision was made on, so that decision's orders are not sent.
        for op in (o for o in todo if o.get("op") == "PLACE"):
            if raced:
                report["refused"].append({
                    "order": dict(op),
                    "reason": "DECISION_STALE_FILL_DURING_CANCEL"})
                continue
            self._place(market, op, now, report)
        return done()

    # ------------------------------------------------------ window's end

    def end_of_window(self, market: str) -> Dict[str, Any]:
        """Cancel every one of OUR open orders in `market`, each by its own
        venue id, and say what each one read as afterwards."""
        now = self._now()
        report: Dict[str, Any] = {
            "market": market, "cancelled": [], "filled": [], "still_open": [],
            "unresolved": [], "fills": [], "inventory": None,
            "halted_reason": None}
        if self._ledger_ok:
            for coid, row in sorted(self.live_orders().items()):
                if row["market"] != market:
                    continue
                res = self._cancel(coid, now, report["fills"])
                if res.get("refused") or res.get("read_error"):
                    report["unresolved"].append(res)
                elif not res["terminal"]:
                    report["still_open"].append(res)
                elif res["state"] == FILLED:
                    report["filled"].append(res)
                else:
                    report["cancelled"].append(res)
            self._refresh_halt()
            self._save_or_halt()
            report["inventory"] = self.inventory(market)
        report["halted_reason"] = self.halted_reason
        return report

    # ---------------------------------------------------------- settlement

    def settlement_arithmetic(self, market: str, up_won: bool
                              ) -> Dict[str, Any]:
        """What `market` would settle to from OUR booked fills. READ-ONLY.

        Each winning share redeems $1, each losing share nothing. This is
        arithmetic, not a credit: label it realized only once the venue has
        paid. With an order still unresolved the result is provisional.
        """
        if not isinstance(up_won, bool):
            raise ValueError("up_won must be True or False")
        legs = self.inventory(market)
        up, down = legs[C.UP], legs[C.DOWN]
        matched = min(up["qty"], down["qty"])
        pair = (up["cost"] / up["qty"] + down["cost"] / down["qty"]
                if matched > 0 else None)
        if up["qty"] > down["qty"]:
            residual_side, residual = C.UP, up["qty"] - down["qty"]
        elif down["qty"] > up["qty"]:
            residual_side, residual = C.DOWN, down["qty"] - up["qty"]
        else:
            residual_side, residual = None, ZERO
        cost = up["cost"] + down["cost"]
        redemption = up["qty"] if up_won else down["qty"]
        unresolved = sorted(c for c, r in self._ledger["orders"].items()
                            if r["market"] == market and r["state"] in OPEN)
        return {"market": market, "up_won": up_won, "matched_qty": matched,
                "pair_cost": pair, "residual_side": residual_side,
                "residual_qty": residual, "cost_total": cost,
                "redemption_total": redemption,
                "settlement_pnl": redemption - cost,
                "unresolved_orders": unresolved,
                "provisional": bool(unresolved),
                "origin": STRATEGY,
                "cost_basis": ORIGIN.BASIS_AUTHENTICATED,
                "label": ("settlement arithmetic from booked fills; realized "
                          "only when the venue credits the redemption")}

    def settle(self, market: str, up_won: bool) -> Dict[str, Any]:
        """Record the settlement of `market`, ONCE. `up_won` must be the
        venue's own settlement, never a mark or a guess.

        With an order still unresolved nothing is recorded and the market
        keeps counting as exposure. Once recorded, the same outcome returns
        the same figures and writes nothing; the OTHER outcome raises --
        a settled result is not something a later call may reverse.
        """
        out = self.settlement_arithmetic(market, up_won)
        prior = self._ledger["settled"].get(market)
        if prior is not None:
            if prior.get("up_won") is not up_won:
                raise ValueError("%s is already settled with the other "
                                 "outcome" % market)
            return dict(out, already_settled=True,
                        settled_at=prior.get("settled_at"))
        if self._ledger_ok and not out["provisional"]:
            now = self._now()
            out["settled_at"] = now.isoformat() if now is not None else None
            self._ledger["settled"][market] = {
                k: (_s(v) if isinstance(v, Decimal) else v)
                for k, v in out.items()}
            self._save_or_halt()
        return out
