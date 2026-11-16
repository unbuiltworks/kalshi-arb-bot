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
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional

from src import l5_btc5m_controller as C
from src import l5_live_authorization as AUTH
from src import l5_origin as ORIGIN

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
    def _order_fault(order: Mapping[str, Any]) -> Optional[str]:
        """Why this PLACE can never be sent by this adapter, or None."""
        if order.get("post_only") is not True:
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

        fault = self._order_fault(order)
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
            if acct["own_usd"] + notional > cap:
                return refuse("ACCOUNT_EXPOSURE_CAP",
                              total_usd=acct["own_usd"], cap_usd=cap,
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
        # THE LEDGER NEXT. If this write fails nothing is sent.
        self._ledger["orders"][coid] = {
            "client_order_id": coid, "market": market, "side": side,
            "price": _s(price), "qty": _s(qty), "venue_order_id": None,
            "state": NEW, "filled_qty": "0", "filled_cost": "0",
            "created_at": now.isoformat() if now is not None else None,
            "reason": order.get("reason"), "slot": int(slot),
            "klass": klass, "claim_sid": claim.get("sid")}
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
                        straddle_completion_pair_cost=completion_pair_cost(
                            self.params.straddle_completion_pair_cost),
                        straddle_price=straddle_price(
                            self.params.straddle_price),
                        straddle_rungs=straddle_rungs(
                            self.params.straddle_rungs),
                        clip_usd=clip_usd(self.params.clip_usd,
                                          STRADDLE_CLIP_MAX_USD))
        else:
            over.update(entry_mode=C.EDGE_GATED, reserve_mode="COMPLETION")
        return type(self.params)(**dict(vars(self.params), **over))

    def step(self, *, market: str, book: Any, t_in_window_s: Any,
             p_up: Any = None, p_source: Any = None,
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
                p_up=p_up, p_source=p_source)
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
