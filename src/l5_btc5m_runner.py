"""The BTC 5-minute live session: ONE approved window, then stop.

WHAT IT DOES, in order, for the one market named in `L5_BTC5M_MARKET`:

  1. refuses to start a market it has already run (a durable marker);
  2. reads the venue market and runs the EXISTING transport's preflight for
     the 5-minute series, then arms the transport only if the owner's arm
     token is present (`pmus_auth.armed()`) -- exactly as BTC15m arms;
  3. installs the adapter's `owns_venue_order_id` as the transport's
     cancel-ownership test, so a cancel can only name an order this ledger
     recorded;
  4. from the window start to `CANCEL_LEAD_S` before its end, every
     `STEP_S`: reads the shared halt (`L5_HALT_URL`, the same record the
     independent watchdog asserts), the operator halt, and the account-wide
     loss state the BTC15m service publishes; if any refuses or cannot be
     read it cancels its own orders and places nothing; otherwise one
     adapter step, on the public book and the venue-index probability;
  5. cancels every order of its own by exact venue id, re-reading each to a
     terminal state, and disarms the transport;
  6. waits for the venue's own settlement, records it once, reads each of
     its orders back for the venue's commission, compares its inventory
     with the venue's position rows, and emits one session report;
  7. writes the marker and idles. It never chooses another window.

EVERY ORDER STILL SPENDS `claim_btc5m_submission` inside the adapter, so the
owner's approval document for this exact window is required as well.

LABELS. The report states submitted, filled, cancelled, settled. Settlement
P&L is settlement arithmetic on the venue's confirmed fills minus the
venue's own commission; it is "realized" only once the venue has credited
the redemption, and the report says so. An order the transport refused
because it was disarmed is reported as WOULD_SUBMIT and never as a trade.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from src import l5_venue_constraints as VC
from src import l5_btc5m_controller as C
from src import l5_behaviour_probe as PROBE
from src import l5_btc5m_execution as X
from src import l5_persistent_mode as PMODE
from src import l5_shared_reservation as RESV
from src import l5_live_safety as SAFE
from src import l5_order_registry_server as REG
from src import l5_live_authorization as AUTH

D = Decimal
UTC = dt.timezone.utc
WINDOW_S = 300.0
STEP_S = 5.0

#: SECONDS LEFT AT WHICH THE LATE-WINDOW DECISION BLOCK IS LOGGED.
#:
#: 45 because that is exactly where the two settlement-variance
#: estimators start to differ: at and above it they agree to three
#: decimals, below it the shipped linear ramp is up to 12x too wide in sd
#: (see src/l5_btc_reference and tests/test_l5_btc_reference_exact_
#: variance.py). Logging the whole window would be mostly rows in which
#: the comparison is identically zero.
#:
#: VOLUME: the loop steps every STEP_S, so this is ceil(45/5) = 9 extra
#: lines per 5-minute window, ~108/hour, ~2,600/day. It adds no venue
#: call, no file, no volume and no order-path branch.
LATE_LOG_S = 45.0

#: STAY RESIDENT AND DISARMED WHEN THE ENVELOPE WILL NOT PERMIT TRADING.
#: OFF unless exactly "YES".
#:
#: WHAT HAPPENS TODAY. `persistent_gate` returns exit=True for any
#: envelope refusal and the loop does `return 0`, so the PROCESS ENDS.
#: `serve_fills` runs on a daemon thread, so the registry dies with it.
#: Observed: l5-btc5m-live exited 2026-11-17T23:05Z on ENVELOPE_EXPIRED
#: with code 0 -- a designed dead-man, which Railway reports as CRASHED
#: -- and has been down since. Every watchdog sweep that day read
#: `l5-btc5m-live...:8090:REGISTRY_UNREACHABLE:URLError`, and because the
#: ownership oracle requires EVERY registry to be readable before it will
#: claim any id, a sports halt at 16:02Z could cancel NOTHING
#: account-wide. A lane that is merely switched off degraded the
#: protection the other lane depends on.
#:
#: THE PRECEDENT IS ALREADY HERE. `STATE_NOT_ARMED` returns exit=False
#: and waits, with the note "an operator may arm it without restarting
#: anything". This extends the same treatment to an envelope that will
#: not permit trading, and to nothing else.
#:
#: IT IS NOT PERMISSION, and three independent things make that true:
#:   1. the executor is constructed `Btc5mExecutor(None, ledger, ...)`
#:      -- transport None. There is no object in the process that can
#:      POST, so submission is impossible rather than merely refused.
#:   2. the loop `continue`s. The trading body is never reached while the
#:      gate refuses, exactly as for STATE_NOT_ARMED.
#:   3. the SAME gate is re-evaluated every 15 s. An expired envelope
#:      keeps refusing; nothing here can arm it.
#:
#: SCOPED TO ENVELOPE REASONS ONLY. A LATCHED state (HALTED, STOPPED) and
#: an unreadable state still END the process, because those are latches
#: and the existing note is explicit: "NOTHING REARMS. The loop ends; it
#: does not wait for somebody to clear a latch."
#:
#: OFF BY DEFAULT because residency is not strictly safer in every sense:
#: a process that exits cleanly is a simpler failure mode than one that
#: stays up holding a volume and a port indefinitely. Enabling it is part
#: of a deployment the owner reviews.
RESIDENT_WHEN_DISARMED_ENV = "L5_BTC5M_RESIDENT_WHEN_DISARMED"

#: THE REASONS THAT MEAN "THE OWNER HAS NOT AUTHORISED TRADING".
#:
#: THE PRINCIPLE, and it is the whole of the scoping: residency is for a
#: lane that is BENIGNLY UNAUTHORISED -- no envelope, an expired one, one
#: for another deployment, one that does not name this strategy. Those are
#: expected states, and the right response is to sit disarmed while
#: remaining readable.
#:
#: EVERYTHING ELSE EXITS, including every envelope reason that means
#: SOMETHING IS BROKEN rather than merely absent: unparseable, not an
#: object, an unreadable expiry or limits, a short nonce, an unknown
#: deployment, an unreadable clock, an unavailable strategy registry. A
#: process that cannot read its own configuration is one whose cleanest
#: failure mode is to stop.
#:
#: MY FIRST VERSION OF THIS LIST WAS WRONG, and its own test caught it: I
#: enumerated the reasons from truncated grep output and got 11 of the 22
#: `parse_envelope` can return, which would have silently treated the
#: other 11 as latches. The test below now asserts this is a SUBSET of
#: the parser's reasons and that each excluded one still exits, so a new
#: reason cannot land in either bucket by accident.
UNAUTHORISED_REASONS = frozenset({
    "ENVELOPE_ABSENT",
    "ENVELOPE_EXPIRED",
    "ENVELOPE_NOT_APPROVED",
    "ENVELOPE_NOT_PERSISTENT",
    "ENVELOPE_IS_FOR_ANOTHER_DEPLOYMENT",
    "ENVELOPE_NAMES_NO_DEPLOYMENT",
    "ENVELOPE_NAMES_NO_SERIES",
    "ENVELOPE_NAMES_NO_STRATEGIES",
    "ENVELOPE_UNKNOWN_SERIES",
    "ENVELOPE_UNKNOWN_STRATEGY",
    "ENVELOPE_TAKER_PERMISSION_UNSTATED",
    "ENVELOPE_PER_MARKET_ABOVE_AGGREGATE",
    "ENVELOPE_EXCEEDS_CODE_CEILING",
    "ENVELOPE_LIMITS_NOT_POSITIVE",
})


def resident_when_disarmed() -> bool:
    return str(os.environ.get(RESIDENT_WHEN_DISARMED_ENV, "")).strip() \
        == "YES"


def demote_stale_armed(armed: Any, now: dt.datetime
                       ) -> Optional[Dict[str, Any]]:
    """A stored ARMED must never stand in for a fresh authorization.

    THE WEAKNESS THIS CLOSES. The loop's gate asks only "is the envelope
    valid and is the stored state ARMED?". The arming ceremony is what
    proves the stored ARMED belongs to the documents now in force -- but
    when it REFUSED (another campaign's digest, a campaign already started)
    or did not run at all (no envelope, no planned market), the runner
    logged that and carried on. A valid envelope plus an ARMED left on the
    volume by an EARLIER campaign would then pass the gate.

    So after the ceremony, a stored ARMED survives only if either
      * this boot's ceremony succeeded, or
      * its digest is exactly the digest of the envelope and campaign
        documents set NOW, and that envelope is valid now (the same
        authorization, re-read after a restart).
    Anything else is written down to MONITOR. A latch is never touched:
    this reads ARMED and nothing else. Returns None when there was nothing
    to decide, else what it did; `demoted: False` means the write failed
    and the caller must not continue.
    """
    st = PMODE.read_state()
    if not st.get("ok") or st.get("state") != PMODE.ARMED:
        return None
    if isinstance(armed, Mapping) and armed.get("ok") is True:
        return None
    raw_env = os.getenv(PMODE.ENVELOPE_ENV) or ""
    raw_camp = os.getenv(AUTH.CAMPAIGN_APPROVAL_ENV) or ""
    stored = (st.get("detail") or {}).get("latch_digest")
    if raw_env.strip() and raw_camp.strip() and stored:
        env = PMODE.envelope(now=now)
        if env.get("ok") and stored == PMODE.arm_digest(raw_env, raw_camp):
            return {"kept": True, "state": PMODE.ARMED,
                    "why": "the stored ARMED carries the digest of the "
                           "documents in force"}
    why = (str(armed.get("reason")) if isinstance(armed, Mapping)
           else "NO_CEREMONY_THIS_BOOT")
    out = PMODE.write_state(PMODE.MONITOR, why="STALE_ARMED:%s" % why,
                            now=now)
    return {"demoted": bool(out.get("ok")), "from": PMODE.ARMED,
            "to": PMODE.MONITOR, "ceremony": why,
            "write": out.get("reason"),
            "note": ("a stored ARMED from an earlier authorization was "
                     "removed; trading needs a ceremony that succeeds")}


def resident_state_fields() -> Dict[str, Any]:
    """The stored state, for the resident line. Never raises.

    `would_refuse_arming` is the point: while the envelope is expired this
    lane never reaches the state check, so without this a latch is silent.
    """
    try:
        rep = PMODE.stored_state_report()
        return {"stored_state": rep.get("state"),
                "latched": rep.get("latched"),
                "latch_sha256": rep.get("latch_sha256"),
                "would_refuse_arming": rep.get("would_refuse_arming")}
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"stored_state": None, "latched": None,
                "would_refuse_arming": True}


def stay_resident(gate: Mapping[str, Any]) -> bool:
    """Should this refusal keep the lane resident instead of ending it?

    True only when the switch is on AND the reason means the owner has
    not AUTHORISED trading. A latch, anything unreadable or unparseable,
    and any reason this module does not recognise end the process as
    before.
    """
    if not gate.get("exit"):
        return False                      # already staying
    if not resident_when_disarmed():
        return False
    return str(gate.get("reason") or "") in UNAUTHORISED_REASONS
#: stop placing and start cancelling this long before the window ends
CANCEL_LEAD_S = 30.0
#: how long after the window end the cancel loop may keep re-reading
CANCEL_GRACE_S = 60.0
#: how long to wait for the venue's settlement before reporting without it
#: PREFLIGHT RETRY INSIDE THE WINDOW. A 5-minute market reads CLOSED until
#: the venue flips it open, and the flip is not instantaneous at the window
#: start: the 03:45Z window of 2026-11-12 preflighted 0.6 ms after its start,
#: failed, and -- with a single attempt -- never armed. The session now
#: retries with the BTC15m runner's backoff until PREFLIGHT_UNTIL_S into the
#: window. Arming still happens only through `authorize_arming`, only after
#: a PASSED preflight, and never after the deadline.
PREFLIGHT_BACKOFF_S = (3.0, 6.0, 12.0, 24.0)
PREFLIGHT_UNTIL_S = 90.0
SETTLE_WAIT_S = 1800.0
SETTLE_POLL_S = 15.0
#: the account-wide risk document must be at most this old
RISK_MAX_AGE_S = 30.0
GATEWAY = "https://gateway.polymarket.us"

LEDGER_ENV = "L5_BTC5M_LEDGER"
RISK_URL_ENV = "L5_ACCOUNT_RISK_URL"
HALT_URL_ENV = "L5_HALT_URL"
FILLS_PORT_ENV = "L5_BTC5M_FILLS_PORT"
SESSION_DIR = "btc5m_sessions"
#: OPT-IN to running every window the owner's approval ENUMERATES, instead
#: of the single window named in `L5_BTC5M_MARKET`. Anything but exactly
#: "ON" keeps the one-window behaviour, so a deployment changes nothing
#: until the owner asks. This GRANTS NOTHING: the list of windows comes
#: from the owner's own approval document and this runner can neither add
#: to it nor invent one.
CAMPAIGN_ENV = "L5_BTC5M_CAMPAIGN"
#: a hard ceiling on a ROLLING campaign, independent of the document. The
#: approval layer already caps `max_windows` at 8, and this refuses to plan
#: past that even if a future document were to carry more.
CA_MAX_ROLLING_WINDOWS = 8


def order_table(mine: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One row per order of this window, from the ledger. Never raises.

    `sent_post_only` is what this lane SENT (the intent it recorded);
    `venue` is what the venue's own order row said at the last read.
    """
    rows: List[Dict[str, Any]] = []
    for coid in sorted(mine):
        r = mine[coid]
        try:
            intent = r.get("intent") if isinstance(r.get("intent"),
                                                   Mapping) else {}
            rows.append({
                "venue_order_id": r.get("venue_order_id"),
                "side": r.get("side"), "price": r.get("price"),
                "qty": r.get("qty"), "filled_qty": r.get("filled_qty"),
                "state": r.get("state"),
                "venue_state": r.get("venue_state"),
                "cancel_requested": bool(r.get("cancel_requested")),
                "cancel_sent_at": r.get("cancel_sent_at"),
                "cancel_error": r.get("cancel_error"),
                "sent_post_only": (r.get("post_only")
                                   if r.get("post_only") is not None
                                   else intent.get("post_only")),
                "venue_fees": r.get("venue_fees"),
                "venue": r.get("venue_evidence") or {}})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            rows.append({"client_order_id_unreadable": True,
                         "error": type(exc).__name__})
    return rows


def last_traded_orders(ex: Any) -> Dict[str, Any]:
    """The per-order table of the latest market this lane sent orders in."""
    orders = ex.orders()
    sent = {c: r for c, r in orders.items() if r.get("venue_order_id")}
    if not sent:
        return {"market": None, "orders": []}
    market = max(str(r.get("market")) for r in sent.values())
    mine = {c: r for c, r in sent.items() if str(r.get("market")) == market}
    rows = order_table(mine)
    return {"market": market, "count": len(rows),
            "cancel_requested": sum(1 for r in rows
                                    if r.get("cancel_requested")),
            "ended_without_our_cancel": [
                r.get("venue_order_id") for r in rows
                if r.get("state") == X.CANCELLED
                and not r.get("cancel_requested")],
            "orders": rows}


def emit(kind: str, doc: Mapping[str, Any]) -> None:
    print("%s %s" % (kind, json.dumps(dict(doc), sort_keys=True,
                                      default=str)), flush=True)


def _dec(v: Any) -> Optional[Decimal]:
    if isinstance(v, Mapping):
        v = v.get("value")
    if v is None or isinstance(v, bool):
        return None
    try:
        d = D(str(v).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def window_start(market: str) -> Optional[dt.datetime]:
    try:
        d = dt.datetime.strptime(str(market)[-16:], "%Y-%m-%d-%H%Mz")
    except ValueError:
        return None
    return d.replace(tzinfo=UTC)


# ------------------------------------------------------------ the inputs

def public_book(market: str, *, fetch: Optional[Callable[[str], Any]] = None
                ) -> Dict[str, Any]:
    """YES best bid/ask from the venue's public market record. GET only."""
    def get(url: str) -> Any:
        req = urllib.request.Request(url, headers={"Accept":
                                                   "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as r:     # noqa: S310
            return json.loads(r.read().decode("utf-8"))
    t0 = time.time()
    doc = (fetch or get)("%s/v1/market/slug/%s" % (GATEWAY, market))
    m = doc.get("market") if isinstance(doc, Mapping) and isinstance(
        doc.get("market"), Mapping) else doc
    if not isinstance(m, Mapping) or str(m.get("slug")) != str(market):
        return {"bid": None, "ask": None, "age_s": None,
                "reason": "BOOK_FOR_ANOTHER_MARKET"}
    #: CARRY THE VENUE'S OWN ORDER CONSTRAINTS. They arrive in THIS response
    #: -- `minimumTradeQty`, `orderPriceMinTickSize`, `feeCoefficient` -- and
    #: were being discarded, which left `Params.tick` and `Params.min_qty` as
    #: unverified defaults. No extra request is made: these are three fields
    #: of a document we already fetched.
    out = {"bid": _dec(m.get("bestBidQuote")),
           "ask": _dec(m.get("bestAskQuote")),
           "age_s": max(0.0, time.time() - t0),
           "closed": bool(m.get("closed"))}
    out.update(VC.carry(m))
    return out


BOOK_READ_FAILED = "BOOK_READ_FAILED"


def read_book(book_fn: Callable[[str], Any], market: str) -> Dict[str, Any]:
    """One book read that cannot end the trading process.

    THE DEFECT THIS FIXES. Live window 2026-11-20 14:20Z: the public market
    record GET in `public_book` hit its 3 s read timeout at 14:24:27Z and
    the `TimeoutError` rose through `trade()` and out of `main`. The process
    exited 33 s before the window closed, skipping `close()`, with ARMED
    still on disk.

    WHAT A FAILED READ MEANS. This request is a GET of public data: it
    places, cancels and changes nothing, so its failure says nothing about
    any order. It means only that this step has NO BOOK. A step with no
    book is one the controller already handles, and handles closed:
    `decide` returns HOLD `BOOK_UNUSABLE` with cancel=True, so resting
    quotes are pulled rather than left priced off a book nobody can see.
    Nothing is retried here; the next step, STEP_S later, reads again, and
    the bound on each attempt stays the request's own timeout.

    The answer is never a guessed or remembered quote: bid, ask and age are
    None, which no pricing path accepts.
    """
    try:
        book = book_fn(market)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"bid": None, "ask": None, "age_s": None,
                "reason": "%s:%s" % (BOOK_READ_FAILED, type(exc).__name__)}
    if not isinstance(book, Mapping):
        return {"bid": None, "ask": None, "age_s": None,
                "reason": "%s:NOT_A_BOOK" % BOOK_READ_FAILED}
    return dict(book)


def clear_latch_if_owner_asked(ex: Any, now: dt.datetime
                                ) -> Optional[Dict[str, Any]]:
    """Run the owner's latch reset, if a reset document is set. Else None.

    The conditions `PMODE.clear_latch` requires are established HERE, from
    this lane's own ledger, switches and the watchdog's record, and any one
    that cannot be read is simply left out -- which is a refusal there, not
    a pass. It never arms: the lane must be unable to submit (no arm token
    AND the shadow lock engaged) for a reset to be accepted at all.
    """
    raw = os.getenv(PMODE.LATCH_RESET_ENV)
    if not (raw or "").strip():
        return None
    safe: Dict[str, Any] = {}
    wd = shared_halt((os.getenv(HALT_URL_ENV) or "").strip())
    safe["watchdog_halt_clear"] = wd.get("ok") is True
    try:
        safe["own_live_orders"] = len(ex.live_orders())
        exp = account_exposure_of(ex)
        if exp is not None:
            safe["own_exposure_usd"] = exp
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    safe["lane_can_submit"] = bool(
        os.environ.get(X.LIVE_ENV) == X.ARM_TOKEN
        or not AUTH.shadow_lock_engaged())
    out = PMODE.clear_latch(raw=raw, now=now, safe=safe)
    return dict(out, watchdog=wd.get("reason"),
                conditions={k: str(v) for k, v in safe.items()})


def account_exposure_of(ex: Any) -> Optional[Decimal]:
    """This lane's own resting plus filled, unsettled notional, or None."""
    try:
        got = _dec((ex.account_exposure() or {}).get("own_usd"))
        return got
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None


def step_book(book: Mapping[str, Any],
              sized: Mapping[str, Any]) -> Dict[str, Any]:
    """The book the executor is stepped with: prices from the public record,
    depth from the freshness-checked sizing, and the venue's own order
    constraints carried through UNCHANGED.

    THE DEFECT THIS FIXES. `public_book` has carried the venue's minimum
    quantity and tick since the venue-constraints gate was added, and the
    gate reads them from the book it is stepped with. This dict was built
    by hand from five keys and dropped them, so with
    `L5_BTC5M_VENUE_CONSTRAINTS=REQUIRE` every production decision held on
    VENUE_CONSTRAINTS_UNVERIFIED: the gate could be switched on only by
    switching trading off. The end-to-end test stepped the executor with
    `public_book`'s output directly and so never saw it.

    Only keys the public record actually carried are passed, so "the venue
    did not say" stays distinguishable from "nobody looked", and with the
    gate off nothing reads them.
    """
    out: Dict[str, Any] = {"bid": book.get("bid"), "ask": book.get("ask"),
                           "age_s": book.get("age_s"),
                           "bid_shares": sized.get("bid_shares"),
                           "ask_shares": sized.get("ask_shares")}
    for k in (VC.BOOK_MIN_QTY, VC.BOOK_TICK, VC.BOOK_FEE):
        if k in book:
            out[k] = book[k]
    return out


#: the venue-equity blocks the account-risk record must carry, and the
#: prefix each one's refusal is reported under.
EQUITY_BLOCKS = (("portfolio_drawdown", "ACCOUNT_DRAWDOWN"),
                 ("equity_baseline", "ACCOUNT_EQUITY_BASELINE"))


class OwnSettled(Decimal):
    """This strategy's settled result today, WITH the markets it came from.

    A Decimal, so every existing reader of the total is unchanged; the
    per-market breakdown rides along as `by_market` for the one reader
    that needs it.

    THE DEFECT THIS CARRIES THE FIX FOR. `account_risk` added this lane's
    settled losses to the account ledger's figures on the assumption that
    the other service never books them. It does: it adopts this lane's
    venue position, retires it at settlement and restates it from venue
    cost. Live 2026-11-20 17:08Z the first window's -0.0114 was therefore
    counted twice -- the day read -0.0228 against a true -0.0114.
    """
    by_market: Optional[Dict[str, Decimal]] = None
    #: every market ever settled: {market: {"at": datetime, "pnl": Decimal}}
    history: Optional[Dict[str, Dict[str, Any]]] = None

    @classmethod
    def of(cls, by_market: Mapping[str, Decimal],
           history: Optional[Mapping[str, Mapping[str, Any]]] = None
           ) -> "OwnSettled":
        out = cls(sum(by_market.values(), D(0)))
        out.by_market = dict(by_market)
        out.history = None if history is None else {
            str(m): dict(r) for m, r in history.items()}
        return out


def _own_not_in_account_ledger(own: Any, risk: Mapping[str, Any]
                               ) -> Tuple[Decimal, List[str], Dict[str, str]]:
    """(loss to add, markets added in full, {market: shortfall added}).

    THE RULE, per market this lane settled today, with `o` its own result
    from its own authenticated fills and `b` what the account service says
    it booked that market at:

        b not published for that market   add  min(0, o)       -- all of it
        b published and readable          add  min(0, o - b)   -- the gap

    WHY THE GAP AND NOT "SKIP IF LISTED". A market being retired there does
    not mean its loss is in that service's figure. It adopts only this
    lane's NET position and first books it at a $1-a-share bound: when the
    residual side wins that books ZERO, while matched pairs bought above
    1.00 lost money here. It also retires rows at zero when an account is
    closed flat. Skipping on the name alone would have hidden those losses.

    WHAT THIS GUARANTEES. The account figure contains b. After the
    addition the market counts for  b + min(0, o - b) = min(b, o),  which
    is never better than this lane's own result -- whatever b is, and
    however stale, because b and the day figure it is part of come from the
    same record. So the correction can remove only what is provably counted
    twice. It cannot make a loss disappear.

    NOTHING PUBLISHED ADDS EVERY LOSS, MARKET BY MARKET: no
    `retired_results_utc_day`, one for another UTC day, or an unreadable
    one adds  sum of min(0, o)  over this lane's markets. An amount that
    cannot be read for ONE market adds that market in full.

    A GAIN IS NEVER ADDED AND NEVER OFFSETS ANOTHER MARKET'S LOSS. Before
    this the fallback was min(0, total), which let a gain here cancel a
    loss here -- while the account service could ALSO have booked that
    gain. A fully matched window bought above 1.00 (a loss that service
    never sees) beside a window whose winning residual it booked as a gain
    then read as no loss at all. Flooring each market closes that with or
    without the publication; it can only add loss.

    Only when this lane's result arrives with no breakdown at all is the
    total used, as `min(0, total)`.
    """
    total = min(D(0), Decimal(own))
    by = getattr(own, "by_market", None)
    if not isinstance(by, Mapping):
        return total, [], {}
    floor = sum((min(D(0), v) for v in by.values()), D(0))
    pub = risk.get("retired_results_utc_day")
    booked = pub.get("results") if isinstance(pub, Mapping) else None
    if not isinstance(booked, Mapping) \
            or str(pub.get("utc_day")) != str(risk.get("utc_day")):
        return floor, sorted(by), {}
    add, full, gaps = D(0), [], {}
    for m in sorted(by):
        o = by[m]
        b = _dec(booked.get(m)) if m in booked else None
        if b is None:
            add += min(D(0), o)
            full.append(m)
        else:
            gap = min(D(0), o - b)
            add += gap
            gaps[m] = str(gap)
    return add, full, gaps


#: A CAMPAIGN DOES NOT TRADE PAST A WINDOW THE ACCOUNT LEDGER HAS NOT
#: RECONCILED.
#:
#: With one window per arming, the owner compared the lane's settled result
#: with the account ledger's before the next window was armed. A campaign
#: document covers many windows in one arming, so that comparison has to be
#: made by the code or it is not made.
#:
#: While a campaign document is SET (valid or not), nothing may be submitted
#: unless EVERY market this lane has traded today is
#:   (a) settled in this lane's own ledger -- `Session.gates` refuses while
#:       an earlier window is still awaiting the venue's settlement; and
#:   (b) published by the account service with a result within
#:       CAMPAIGN_RECON_TOLERANCE_USD of this lane's own.
#: One that is missing, unreadable or further away refuses. THERE IS NO
#: GRACE (owner decision 2026-11-21, replacing a 40-minute allowance): a
#: reconciliation that is merely pending is a refusal too, so the lane
#: WAITS -- it does not trade through the uncertainty. The account service
#: takes 10-17 minutes to restate a window from the venue's fills, so a
#: campaign trades roughly one window in four or five. That is accepted.
#:
#: It can only ADD a refusal, and only under a campaign document: with none
#: set this returns None and nothing reads it.
CAMPAIGN_RECON_GRACE_S = 0.0

#: =================== CONSECUTIVE WINDOWS, ON VERIFIED EXPOSURE ==========
#:
#: Owner decision 2026-11-21 (after a campaign in which every second window
#: was sat out): each listed window should be ELIGIBLE to trade, provided
#: what is still open from the window before it is independently verified
#: and bounded. OFF unless the switch is exactly the token; off, everything
#: above applies unchanged (wait for settlement AND reconciliation).
#:
#: ON, a window may trade while the window before it is unsettled, when
#: ALL of these hold -- and any one failing sits the window out as before:
#:   * AT MOST ONE earlier market is unsettled;
#:   * this lane has no order still open in it;
#:   * the VENUE'S own position row for it agrees with this lane's ledger
#:     on both legs (or the venue already reports it settled), read fresh
#:     once per window;
#:   * its worst case comes off the campaign's loss room before anything
#:     is reserved (`Btc5mExecutor.pending_worst_case_usd`, always on);
#:   * every market settled more than CAMPAIGN_RECON_DEADLINE_S ago is in
#:     the account ledger within the tolerance. A younger settlement is
#:     still being restated and does not refuse; an older one that is
#:     missing or different does.
#: The earlier window is settled DURING the next one (a read every
#: MIDWINDOW_SETTLE_S), so its cost stops occupying the exposure limit as
#: soon as the venue settles it.
CAMPAIGN_OVERLAP_ENV = "L5_BTC5M_CAMPAIGN_OVERLAP"
CAMPAIGN_OVERLAP_TOKEN = "ONE_VERIFIED_UNSETTLED_WINDOW"
#: how long the account service is given to restate a settled window.
#: Measured 2026-11-21: 2 minutes in both campaign windows.
CAMPAIGN_RECON_DEADLINE_S = 600.0
MIDWINDOW_SETTLE_S = 15.0


#: WHAT THIS LANE VERIFIED AGAINST THE VENUE WHEN A WINDOW CLOSED.
#:
#: Observed 2026-11-21 18:40:30Z: the 18:35 window had closed, the venue had
#: already dropped its position row, and the venue had not yet reported the
#: market settled (it did 13 s later). The overlap check found neither a row
#: to compare nor a settlement, refused, and the 18:40 window was sat out.
#:
#: But five seconds earlier this same process had read that row for the
#: closing window's own reconciliation, and it agreed. That read is kept
#: here -- in memory only, so a restart forgets it and the strict check
#: applies again -- and the overlap check accepts it, for
#: CLOSE_VERIFIED_MAX_AGE_S, when the venue has no row to show any more.
#: It is recorded ONLY when the venue's row was read and both legs equal
#: this lane's ledger.
CLOSE_VERIFIED: Dict[str, Dict[str, Any]] = {}
CLOSE_VERIFIED_MAX_AGE_S = 300.0


def note_close_verification(market: str, recon: Any, legs: Any,
                            at: float) -> Optional[Dict[str, Any]]:
    """Record a close-time agreement on BOTH legs, or nothing. Never raises."""
    try:
        if not (isinstance(recon, Mapping) and recon.get("read")
                and recon.get("agrees") is True):
            return None
        rows = recon.get("venue_rows") or []
        up_q, dn_q = legs[C.UP]["qty"], legs[C.DOWN]["qty"]
        if len(rows) == 1:
            up = _dec(rows[0].get("qtyBoughtDecimal"))
            dn = _dec(rows[0].get("qtySoldDecimal"))
            if up is None or dn is None or up != up_q or dn != dn_q:
                return None
        elif rows or up_q != 0 or dn_q != 0:
            #: several rows, or none while we hold something: not verified
            return None
        rec = {"at": float(at), "up": str(up_q), "down": str(dn_q)}
        CLOSE_VERIFIED[str(market)] = rec
        return rec
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None


def close_verified(market: str, legs: Any, now: float) -> bool:
    """Was `market` verified at its close, recently, at the SAME legs the
    ledger holds now?"""
    rec = CLOSE_VERIFIED.get(str(market))
    if not rec:
        return False
    try:
        age = float(now) - float(rec["at"])
        return (0 <= age <= CLOSE_VERIFIED_MAX_AGE_S
                and rec["up"] == str(legs[C.UP]["qty"])
                and rec["down"] == str(legs[C.DOWN]["qty"]))
    except (TypeError, ValueError, KeyError):
        return False


def campaign_overlap() -> bool:
    return (os.environ.get(CAMPAIGN_OVERLAP_ENV) or "").strip() \
        == CAMPAIGN_OVERLAP_TOKEN
CAMPAIGN_RECON_TOLERANCE_USD = D("0.005")


def campaign_reconciliation(own: Any, risk: Mapping[str, Any],
                            now: dt.datetime,
                            env: Optional[Mapping[str, str]] = None
                            ) -> Optional[Dict[str, Any]]:
    """None when no campaign document is set; else the verdict."""
    if not PROBE.campaign_configured(env):
        return None
    grace = (CAMPAIGN_RECON_DEADLINE_S if campaign_overlap()
             else CAMPAIGN_RECON_GRACE_S)
    by = getattr(own, "by_market", None)
    hist = getattr(own, "history", None)
    if not isinstance(by, Mapping) or not isinstance(hist, Mapping):
        #: without the per-market breakdown nothing can be compared, and a
        #: comparison that cannot be made is not a pass
        return {"ok": False, "why": "OWN_RESULTS_HAVE_NO_BREAKDOWN"}
    pub = risk.get("retired_results_utc_day")
    booked = pub.get("results") if isinstance(pub, Mapping) else None
    readable = isinstance(booked, Mapping) and str(pub.get("utc_day")) \
        == str(risk.get("utc_day"))
    due, waiting, bad = [], [], {}
    for m in sorted(by):
        at = (hist.get(m) or {}).get("at") if isinstance(
            hist.get(m), Mapping) else None
        try:
            age = (now - at).total_seconds()
        except (TypeError, ValueError):
            bad[m] = "SETTLED_TIME_UNREADABLE"
            continue
        if age < grace:
            waiting.append(m)
            continue
        due.append(m)
        if not readable:
            bad[m] = "ACCOUNT_RESULTS_NOT_PUBLISHED"
            continue
        b = _dec(booked.get(m)) if m in booked else None
        if b is None:
            bad[m] = "NOT_IN_THE_ACCOUNT_LEDGER"
        elif abs(b - by[m]) > CAMPAIGN_RECON_TOLERANCE_USD:
            bad[m] = "DIFFERS:own=%s,account=%s" % (by[m], b)
    return {"ok": not bad, "why": None if not bad else
            "CAMPAIGN_LEDGER_NOT_RECONCILED",
            "checked": due, "within_grace": waiting, "failed": bad,
            "grace_s": grace,
            "tolerance_usd": str(CAMPAIGN_RECON_TOLERANCE_USD)}


NOT_THIS_LANE_ENV = "L5_BTC5M_NOT_THIS_LANE_MARKETS"
_SLUG_OK = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")


def not_this_lane_markets() -> Tuple[str, ...]:
    """Exact market slugs the owner attests this lane never traded.
    Comma-separated. A token that is not a plain slug is dropped, so a
    wildcard or a prefix can never be smuggled in: only whole, exact names
    are ever exempted."""
    raw = os.environ.get(NOT_THIS_LANE_ENV) or ""
    out = []
    for tok in raw.split(","):
        t = tok.strip()
        if 12 <= len(t) <= 120 and set(t) <= _SLUG_OK:
            out.append(t)
    return tuple(sorted(set(out)))


def _own_not_in_lifetime(own: Any, risk: Mapping[str, Any],
                          life: Decimal) -> Optional[Dict[str, Any]]:
    """What to add to the account's LIFETIME figure for this lane's whole
    settled history. None when the lane's history was not supplied.

    THE GAP THIS CLOSES. Only TODAY's own losses were added to the lifetime
    figure. A loss from an earlier UTC day that the account service never
    booked -- a fully matched window bought above 1.00 is invisible to it
    -- was then in neither service's cumulative total, and the cumulative
    ceiling was judged without it.

    The account service's lifetime figure is its last cash re-anchor plus
    every row it has retired since, and it publishes exactly that as
    `lifetime_basis`. For each market this lane ever settled, with `o` its
    own result:

      settled at or before the re-anchor's cash read   0. The cash that
                                                       anchor was taken
                                                       from contained it.
      retired there since, at a published amount b     min(0, o - b)
      anything else                                    min(0, o), all of it

    THE BASIS IS USED ONLY WHEN IT RECONCILES: present, marked
    `reconciled` (re-anchor plus rows equals its lifetime figure), and
    stating the SAME lifetime figure this record carries. Absent,
    unreconciled, for another figure, or malformed, NOTHING is treated as
    booked and every loss this lane has ever settled is added. An
    incomplete map is used for the markets it names and no others.

    So a market whose booking is published counts for at most
    min(booked, own); the only results left out are ones the anchor's cash
    provably held; and every failure of reconciliation errs toward a larger
    loss. A gain is never added.

    WHAT IS ASSUMED OF A BOOKING THIS LANE IS NOT SHOWN: that it is no
    better than the larger of zero and this lane's own result. Both ways
    the account service books a market satisfy it -- the $1-a-share bound
    is never above zero, and a restatement from venue cost is the result
    itself. Nothing published here can test that from this side.
    """
    hist = getattr(own, "history", None)
    if not isinstance(hist, Mapping):
        return None
    basis = risk.get("lifetime_basis")
    why = None
    booked: Mapping[str, Any] = {}
    cash_read = None
    if not isinstance(basis, Mapping):
        why = "BASIS_NOT_PUBLISHED"
    elif basis.get("reconciled") is not True:
        why = "BASIS_NOT_RECONCILED"
    elif _dec(basis.get("lifetime_realized_usd")) != life:
        why = "BASIS_FOR_ANOTHER_FIGURE"
    elif not isinstance(basis.get("results_since_rebase"), Mapping):
        why = "BASIS_UNREADABLE"
    else:
        raw = basis.get("cash_read_utc")
        if raw is not None:
            try:
                cash_read = dt.datetime.fromisoformat(
                    str(raw).replace("Z", "+00:00"))
                if cash_read.tzinfo is None:
                    raise ValueError("naive")
            except (TypeError, ValueError):
                why, cash_read = "BASIS_UNREADABLE", None
        if why is None:
            booked = basis["results_since_rebase"]
    add = D(0)
    n = {"inside_cash_anchor": 0, "gap_to_booked": 0, "in_full": 0}
    for m in sorted(hist):
        o, at = hist[m]["pnl"], hist[m]["at"]
        if why is None and cash_read is not None and at <= cash_read:
            n["inside_cash_anchor"] += 1
            continue
        b = _dec(booked.get(m)) if (why is None and m in booked) else None
        if b is None:
            add += min(D(0), o)
            n["in_full"] += 1
        else:
            add += min(D(0), o - b)
            n["gap_to_booked"] += 1
    #: IS THIS LANE'S OWN LEDGER WHOLE? The daily and cumulative figures
    #: above, and the executor's own daily-loss room, all start from the
    #: lane's durable ledger. A ledger that is unreadable refuses; one that
    #: is simply ABSENT -- a lost or replaced volume -- loads as empty and
    #: reads as "nothing ever lost". The account service's reconciled basis
    #: is an independent record of what settled on this account: a market
    #: of THIS lane's series that it lists and this lane's history does not
    #: contain means the history has a hole. Named here; refused by the
    #: caller. Only a reconciled basis can say so; without one this check
    #: is silent and every own loss is already being added in full.
    missing: List[str] = []
    if why is None:
        #: the executor refuses at import unless its strategy is in this
        #: table, so the lookup cannot miss
        prefix = str(X.ORIGIN.STRATEGY_SERIES[X.STRATEGY])
        missing = sorted(str(m) for m in booked
                         if str(m).startswith(prefix) and m not in hist)
    #: MARKETS OF THIS SERIES THAT SOMETHING ELSE TRADED. The account is
    #: shared: on 2026-11-17 and 11-18 six BTC5m windows were traded on it
    #: by something other than this lane (the account service's own alert
    #: reads "venue holds a market we never booked"; this lane logged
    #: nothing in those windows). They are in the account's ledger and its
    #: loss limits already count them, but they will never be in this
    #: lane's, and a hole and a stranger look the same from here. The
    #: OWNER names such markets, exactly, in a variable only the owner
    #: sets; a named market is reported and not refused. Anything not
    #: named still refuses, so a market lost from the ledger tomorrow is
    #: caught exactly as before.
    foreign = set(not_this_lane_markets())
    attested = [m for m in missing if m in foreign]
    missing = [m for m in missing if m not in foreign]
    return {"add": add, "basis_used": why is None, "basis_refusal": why,
            "basis_complete": (None if why is not None
                               else basis.get("complete") is True),
            "markets": n, "own_series_markets_not_in_own_history": missing,
            "owner_attested_not_this_lane": attested}


def account_risk(*, url: str, now: dt.datetime, own_unaccounted: Decimal,
                 fetch: Optional[Callable[[str], Any]] = None
                 ) -> Dict[str, Any]:
    """The ACCOUNT-WIDE loss state, as the BTC15m service computes it, plus
    this strategy's settled results that service has not yet booked.

    Refuses (ok False) on: no URL, an unreadable or stale document, a
    document that says it is unreadable, either halt already tripped, the
    operator halt, or the combined figure reaching either limit. Only this
    strategy's LOSSES are added (`own_unaccounted` is <= 0 by construction),
    so the combination can only bind earlier, never later.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason)

    if not url:
        return no("ACCOUNT_RISK_URL_NOT_SET", env=RISK_URL_ENV)
    try:
        if fetch is not None:
            doc = fetch(url)
        else:
            with urllib.request.urlopen(url, timeout=3.0) as r:  # noqa: S310
                doc = json.loads(r.read().decode("utf-8"))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return no("ACCOUNT_RISK_UNREADABLE:%s" % type(exc).__name__)
    risk = doc.get("account_risk") if isinstance(doc, Mapping) else None
    if not isinstance(risk, Mapping):
        return no("ACCOUNT_RISK_NOT_PUBLISHED")
    try:
        as_of = dt.datetime.fromisoformat(
            str(doc.get("as_of_utc")).replace("Z", "+00:00"))
        age = (now - as_of).total_seconds()
    except (TypeError, ValueError):
        return no("ACCOUNT_RISK_UNDATED")
    if age > RISK_MAX_AGE_S or age < -5:
        return no("ACCOUNT_RISK_STALE", age_s=round(age, 1))
    if not risk.get("readable"):
        return no("ACCOUNT_RISK_SAYS_UNREADABLE", why=risk.get("reason"))
    # THE OTHER SERVICE'S OPERATOR HALT IS ITS OWN KILL SWITCH, NOT AN
    # ACCOUNT LIMIT. `L5_LIVE_HALT` is a per-service variable: the BTC15m
    # service's says "BTC15m may not trade" and is deliberately ON while it
    # stays disarmed. Reading it here would stop BTC5m for as long as BTC15m
    # is off. What binds across services is the LOSS state below and the
    # watchdog's halt record (`shared_halt`); BTC5m's own `L5_LIVE_HALT` is
    # read by the session itself. The value is reported, never acted on.
    other_halt = bool(risk.get("operator_halt"))
    day, halt = (_dec(risk.get("utc_day_realized_usd")),
                 _dec(risk.get("daily_halt_usd")))
    life, cum = (_dec(risk.get("lifetime_realized_usd")),
                 _dec(risk.get("cumulative_loss_ceiling_usd")))
    if None in (day, halt, life, cum):
        return no("ACCOUNT_RISK_FIGURES_UNREADABLE")
    if str(risk.get("utc_day")) != now.strftime("%Y-%m-%d"):
        return no("ACCOUNT_RISK_FOR_ANOTHER_DAY", utc_day=risk.get("utc_day"))
    _recon = campaign_reconciliation(own_unaccounted, risk, now)
    if _recon is not None and not _recon.get("ok"):
        return no("CAMPAIGN_LEDGER_NOT_RECONCILED",
                  campaign_reconciliation=_recon)
    add, _added, _gaps = _own_not_in_account_ledger(own_unaccounted, risk)
    _lt = _own_not_in_lifetime(own_unaccounted, risk, life)
    #: with no history supplied the lifetime figure gets today's addition,
    #: as it always did
    life_add = add if _lt is None else _lt["add"]
    #: LOSSES THE VENUE'S EXECUTION HISTORY SHOWS AND THE ACCOUNT LEDGER
    #: LACKS. The account service builds its ledger from net-position
    #: snapshots and can miss trades entirely (2026-11-16: ten matched
    #: pairs, one booked wrongly and nine not at all). When it publishes a
    #: venue-basis report, the total by which the venue's fills are WORSE
    #: than the booked results is added to the lifetime figure here, so the
    #: cumulative ceiling binds on it. Only a negative figure is read: an
    #: upward difference is never applied by anyone without the owner, and
    #: an absent report adds nothing, but a published unreadable REPORT
    #: refuses order admission (fail closed). Venue-equity drawdown below,
    #: measured on the venue's balance, independently checks the ledger.
    vb_add, vb_state = D(0), "NOT_PUBLISHED"
    _vb = risk.get("venue_basis")
    if isinstance(_vb, Mapping):
        vb_state = str(_vb.get("state"))[:24]
        _sm = _vb.get("summary")
        if vb_state == "REPORT":
            _adj = _dec(_sm.get("downward_adjustment_usd")) \
                if isinstance(_sm, Mapping) else None
            if _adj is None or not _adj.is_finite():
                return no("ACCOUNT_VENUE_BASIS_REPORT_UNREADABLE",
                          venue_basis_state="REPORT_NO_READABLE_FIGURE")
            vb_add = min(D(0), _adj)
    day_all, life_all = day + add, life + life_add + vb_add
    room = min(halt + day_all, cum + life_all)
    out = {"utc_day_realized_usd": str(day_all), "daily_halt_usd": str(halt),
           "lifetime_realized_usd": str(life_all),
           "cumulative_loss_ceiling_usd": str(cum),
           "btc5m_losses_not_yet_in_account_ledger_usd": str(add),
           "btc5m_lifetime_losses_added_usd": str(life_add),
           "venue_basis_state": vb_state,
           "venue_basis_losses_added_usd": str(vb_add),
           "btc5m_lifetime_basis": (None if _lt is None else
                                    {k: v for k, v in _lt.items()
                                     if k != "add"}),
           "btc5m_markets_added_in_full": _added,
           "btc5m_shortfall_added_by_market": _gaps,
           # what may still be LOST before either account-wide limit binds
           "loss_room_usd": str(room),
           "btc15m_operator_halt": other_halt,
           "age_s": round(age, 1)}
    if _lt is not None and _lt.get("own_series_markets_not_in_own_history"):
        return no("OWN_LEDGER_MISSING_SETTLED_MARKETS", **out)
    if day_all <= -halt:
        return no("ACCOUNT_DAILY_LOSS_HALT", **out)
    if -life_all >= cum:
        return no("ACCOUNT_CUMULATIVE_LOSS_CEILING", **out)
    # THE VENUE-EQUITY CONTROLS, IN ADDITION TO THE TWO ABOVE (owner
    # direction 2026-11-20). The 15-minute service enforces a portfolio
    # drawdown limit measured on the venue's own balance; this lane never
    # read it, so one strategy could go on losing after the account-wide
    # limit had stopped the other. Both blocks are REQUIRED in the record:
    # a publisher that does not carry them is refused, never assumed clean.
    # `OFF` is the publisher saying the owner has configured no such limit
    # -- the same meaning it has there. Anything else must be `ON` with no
    # refusal and positive room. These can only ADD a refusal: the daily and
    # cumulative checks above have already run and are unchanged.
    for key, tag in EQUITY_BLOCKS:
        blk = risk.get(key)
        if not isinstance(blk, Mapping):
            return no("%s_NOT_PUBLISHED" % tag, **out)
        state, why = blk.get("state"), blk.get("refusal")
        out["%s_state" % key] = state
        if why:
            return no("%s:%s" % (tag, str(why)[:80]), **out)
        if state == "OFF":
            continue
        if state != "ON":
            return no("%s_STATE_%s" % (tag, str(state)[:24]), **out)
        left = _dec(blk.get("room_usd"))
        if left is None:
            return no("%s_ROOM_UNREADABLE" % tag, **out)
        if left <= 0:
            return no("%s_NO_ROOM" % tag, **out)
        out["%s_room_usd" % key] = str(left)
        room = min(room, left)
    out["loss_room_usd"] = str(room)
    if _recon is not None:
        out["campaign_reconciliation"] = _recon
    return dict(out, ok=True, reason=None)


def submission_budget() -> Dict[str, Any]:
    """What the owner's per-class submission budgets allow and what is
    already spent. READ-ONLY: it composes `AUTH.max_of_class` with
    `AUTH.class_spent`, both of which only read, and claims nothing.

    WHY IT EXISTS. Accumulation requotes on every tick of drift, so
    `_order_class` charges most of its orders to REPLACE rather than OPEN.
    If `L5_LIVE_AUTH_MAX_REPLACE` is sized for the one-entry pair strategy,
    a live window stalls after its first requote and the step records say
    `AUTHORIZATION:...` -- which reads like a strategy failure and is not
    one. Reporting the budget at the start of the session and again in its
    report makes that distinguishable from the outside, before a window is
    spent finding out.

    `max` None means the owner configured no budget for that class, which
    does not bind. A directory we cannot read reports `spent` None rather
    than 0: an absence we cannot see is not an absence.
    """
    out: Dict[str, Any] = {}
    try:
        exp = AUTH.experiment_id()
        path = AUTH.ledger_path()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"readable": False, "why": type(exc).__name__}
    for k in AUTH.SUBMISSION_CLASSES:
        try:
            cap = AUTH.max_of_class(k)
        except BaseException:                                  # noqa: BLE001
            cap = None
        try:
            spent: Optional[int] = len(AUTH.class_spent(path, exp, k))
        except BaseException:                                  # noqa: BLE001
            spent = None
        out[k] = {
            "max": cap, "spent": spent,
            "remaining": (None if cap is None or spent is None
                          else max(0, cap - spent)),
            "binds": cap is not None,
        }
    out["readable"] = True
    out["experiment"] = exp
    out["note"] = ("accumulation charges most orders to REPLACE, not OPEN; "
                   "a REPLACE budget sized for the one-entry pair strategy "
                   "stalls a window after its first requote")
    return out


def shared_halt(url: str) -> Dict[str, Any]:
    """The halt record the independent watchdog asserts. Unreadable or
    unconfigured refuses: a live session needs the watchdog's hand on it."""
    if not url:
        return {"ok": False, "reason": "HALT_URL_NOT_SET", "env": HALT_URL_ENV}
    try:
        from src import l5_halt_gate as HG
        st = HG.HaltGate(HG.HttpHaltStore(url)).state()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"ok": False, "reason": "HALT_UNREADABLE:%s" % type(exc).__name__}
    # `HaltGate.state()`: `permitted` True only for a present, fresh,
    # parseable, NOT-halted record; every other case names its `why`
    if not isinstance(st, Mapping) or st.get("permitted") is not True:
        return {"ok": False, "reason": "HALT:%s" % (
            (st or {}).get("why") if isinstance(st, Mapping) else "UNREADABLE")}
    return {"ok": True, "reason": None}


# ------------------------------------------------------- the fills feed

def fills_document(ex: "X.Btc5mExecutor") -> Dict[str, Any]:
    """This ledger's own orders and fills, for the account-wide ledger to
    check against the venue. READ-ONLY; publishes no credential."""
    try:
        rows = ex.orders()
    except BaseException:                                      # noqa: BLE001
        return {"readable": False, "orders": []}
    if not ex._ledger_ok:                                      # noqa: SLF001
        return {"readable": False, "orders": []}
    return {"readable": True, "strategy": X.STRATEGY,
            "as_of_utc": dt.datetime.now(UTC).isoformat(),
            "orders": [{"client_order_id": c, "venue_order_id":
                        r.get("venue_order_id"), "market": r.get("market"),
                        "side": r.get("side"), "price": r.get("price"),
                        "filled_qty": r.get("filled_qty"),
                        "filled_cost": r.get("filled_cost"),
                        "state": r.get("state")}
                       for c, r in sorted(rows.items())]}


def serve_fills(ex: "X.Btc5mExecutor", port: int):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:                              # noqa: N802
            #: THE OWNERSHIP REGISTRY, at the path the watchdog reads.
            #:
            #: The watchdog cancels nothing it cannot claim: an order
            #: absent from `oracle.claimed()` is `not_ours:
            #: NOT_IN_OWNERSHIP_REGISTRY`, the halt is asserted and the
            #: order is LEFT RESTING. This lane served only
            #: `/btc5m_fills`, so its venue ids could never be claimed
            #: and the watchdog could halt for it but never cancel.
            #:
            #: `l5_order_registry_server.registry` is reused verbatim
            #: rather than shaped here, so this document is the same one
            #: the other executor publishes -- same terminal filtering
            #: (`live_orders()` is NEW/LIVE/UNCERTAIN only, because
            #: publishing a terminal id would let the watchdog act on an
            #: order that no longer exists), same `readable: false` when
            #: the ledger cannot be read, same `as_of_utc` so a wedged
            #: publisher reads as STALE rather than as a fresh empty
            #: claim, and the same `submission_disabled` lock.
            #:
            #: `risk` is deliberately NOT passed: the account-wide risk
            #: block stays the responsibility of the service that
            #: enforces those limits, and this lane is its consumer.
            #: Publishing our own would give the account two answers.
            #:
            #: It does NOT claim `manages_own_resting`, so this lane is
            #: never exempted from the watchdog's overdue/age latch. The
            #: aim is coverage, and that flag would remove it.
            if self.path.split("?", 1)[0].lstrip("/") == REG.REGISTRY_KEY:
                body, code = json.dumps(REG.registry(ex),
                                        default=str).encode(), 200
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            _p = self.path.split("?", 1)[0].lstrip("/")
            #: LIVENESS, and nothing more. "this process is answering" is
            #: the only claim, so it cannot be mistaken for "this lane is
            #: trading" or "this lane is well".
            if _p in ("health", "healthz"):
                body, code = b'{"ok":true,"serving":true}', 200
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            #: THE LANE'S OWN STATE, read from the durable record rather
            #: than asserted. A DISARMED lane must SAY so here: reporting
            #: a fabricated healthy or armed state is the failure this
            #: endpoint exists to prevent, and `can_submit` is derived
            #: from the absence of a transport rather than from a flag.
            if _p in ("status", "l5/status.json"):
                try:
                    _st = PMODE.read_state()
                except BaseException as _e:                    # noqa: BLE001
                    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
                        raise
                    _st = {"ok": False,
                           "reason": "STATE_READ_FAILED:%s"
                                     % type(_e).__name__}
                doc = {
                    "strategy": X.STRATEGY,
                    "state": _st.get("state"),
                    "state_readable": bool(_st.get("ok")),
                    "state_reason": _st.get("reason"),
                    #: TWO SEPARATE FACTS, because conflating them is
                    #: how a status endpoint overstates safety.
                    #:
                    #: `arm()` is what puts a POST-capable object in this
                    #: process, so its ABSENCE is conclusive: nothing can
                    #: be sent, whatever any gate says. Its PRESENCE is
                    #: NOT conclusive the other way -- a transport is a
                    #: capability, not a permission, and the campaign
                    #: approval, the per-order authorization claim, the
                    #: market lock, the shared halt and the account-wide
                    #: risk gate each still refuse independently and are
                    #: NOT evaluated here.
                    #:
                    #: So the endpoint reports the structural fact under
                    #: its own name and makes only the claim it can
                    #: establish. A single `can_submit` field invited
                    #: exactly the wrong inference and is gone.
                    "transport_present": bool(
                        getattr(ex, "transport", None) is not None),
                    "submission_structurally_impossible": bool(
                        getattr(ex, "transport", None) is None),
                    "armed_per_durable_state": _st.get("state") == PMODE.ARMED,
                    "authorization_evaluated_here": False,
                    "labels": ("the lane's durable state as recorded. "
                               "submission_structurally_impossible true "
                               "means nothing can be sent at all. FALSE "
                               "does NOT mean submission is authorised: a "
                               "transport is a capability, and the "
                               "campaign approval, per-order claim, "
                               "market lock, shared halt and account-wide "
                               "risk gate are each separate and are not "
                               "evaluated by this endpoint."),
                }
                body, code = json.dumps(doc, default=str).encode(), 200
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if _p != "btc5m_fills":
                body, code = b'{"error":"no such key"}', 404
            else:
                body, code = json.dumps(fills_document(ex),
                                        default=str).encode(), 200
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a) -> None:
            return
    httpd = ThreadingHTTPServer(("0.0.0.0", int(port)), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# ----------------------------------------------- the one arming boundary

class ArmingView:
    """The 5-minute ledger as `authorize_arming` reads a store: whether any
    order's state is uncertain, what is live, what holds cost, what is
    settled. READ-ONLY; it computes nothing the ledger does not state."""

    def __init__(self, ex: "X.Btc5mExecutor") -> None:
        self.ex = ex

    def orders_uncertain(self) -> Optional[str]:
        if not self.ex._ledger_ok:                             # noqa: SLF001
            return "BTC5M_LEDGER_UNREADABLE"
        return self.ex.halted_reason

    def live_orders(self) -> Dict[str, Dict[str, Any]]:
        out = {}
        for c, r in self.ex.live_orders().items():
            out[c] = dict(r, remaining=str(D(r["qty"]) - D(r["filled_qty"])))
        return out

    def committed(self) -> Dict[str, Decimal]:
        out: Dict[str, Decimal] = {}
        for r in self.ex.orders().values():
            if D(r["filled_cost"]) > 0:
                out[r["market"]] = out.get(r["market"], D(0)) + D(
                    r["filled_cost"])
        return out

    @property
    def state(self) -> Dict[str, Any]:
        return {"retired_markets": dict(self.ex._ledger["settled"])}  # noqa


class RunningLimits:
    """The session's capital scope in the shape the boundary reads: the
    same three attributes the BTC15m service's limits object carries, and
    `as_dict()` for the record. Values are the owner-approved figures, so
    the boundary's own CAMPAIGN_LIMITS_WIDER_THAN_APPROVED check compares
    like with like."""

    def __init__(self, *, max_usd_per_market: Decimal,
                 max_total_exposure_usd: Decimal,
                 daily_realized_loss_halt_usd: Decimal) -> None:
        self.max_usd_per_market = max_usd_per_market
        self.max_total_exposure_usd = max_total_exposure_usd
        self.daily_realized_loss_halt_usd = daily_realized_loss_halt_usd

    def as_dict(self) -> Dict[str, str]:
        return {"max_usd_per_market": str(self.max_usd_per_market),
                "max_total_exposure_usd": str(self.max_total_exposure_usd),
                "daily_realized_loss_halt_usd":
                    str(self.daily_realized_loss_halt_usd)}


def venue_window_bounds(market: str, *,
                        fetch: Optional[Callable[[str], Any]] = None
                        ) -> Optional[Dict[str, Any]]:
    """This market's window AS THE VENUE STATES IT (`assetPriceTerms`),
    offered only when it confirms the slug's own start and is five minutes
    long. None otherwise; the boundary then refuses WINDOW_BOUNDS_UNKNOWN."""
    def get(url: str) -> Any:
        req = urllib.request.Request(url, headers={"Accept":
                                                   "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as r:     # noqa: S310
            return json.loads(r.read().decode("utf-8"))
    try:
        doc = (fetch or get)("%s/v1/market/slug/%s" % (GATEWAY, market))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None
    m = doc.get("market") if isinstance(doc, Mapping) and isinstance(
        doc.get("market"), Mapping) else doc
    if not isinstance(m, Mapping) or str(m.get("slug")) != str(market):
        return None
    terms = m.get("assetPriceTerms") or {}
    try:
        st = dt.datetime.fromisoformat(
            str(terms.get("windowStart")).replace("Z", "+00:00"))
        en = dt.datetime.fromisoformat(
            str(terms.get("windowEnd")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    ws = window_start(market)
    if ws is None or st != ws or (en - st).total_seconds() != WINDOW_S:
        return None
    return {"start": st.isoformat(), "end": en.isoformat(),
            "source": AUTH.WINDOW_SOURCE_VENUE}


def wanted_5m_slugs(now: float, lead_s: float) -> List[str]:
    """The 5-minute window trading now, plus the next once it is within
    `lead_s`. The same shape as the 15-minute `wanted_slugs` the
    authenticated book feed defaults to, for the 5-minute series.

    WHY THIS EXISTS. `PmusTransport.preflight`'s `book_readable` gate asks
    whether this market's book can be read. The venue's REST book route
    404s for the first minutes of a window -- measured on BTC5m at
    2026-11-12 15:15z, 15:45z and 16:15z, failing every attempt from t=3s
    to t=88s and costing the window. The transport already handles that:
    `_ws_book_substitute` accepts the AUTHENTICATED SOCKET's book in place
    of a 404, provided the snapshot is fresh, two-sided and the market
    reads OPEN. BTC15m supplies that socket and so recovers; BTC5m supplied
    NOTHING, so `ws_book` was None, the substitute returned None
    immediately, and every 404 failed the preflight outright.
    """
    ws = int(now // WINDOW_S) * int(WINDOW_S)
    out = [slug_for_start(ws)]
    nxt = ws + int(WINDOW_S)
    if now >= nxt - lead_s:
        out.append(slug_for_start(nxt))
    return out


def slug_for_start(ws_epoch: int) -> str:
    d = dt.datetime.fromtimestamp(ws_epoch, UTC)
    return "%s%s-%02d%02dz" % (X.MARKET_PREFIX, d.strftime("%Y-%m-%d"),
                               d.hour, d.minute)


def approved_markets(now: dt.datetime) -> Dict[str, Any]:
    """The BTC5m windows the OWNER's approval document enumerates, oldest
    first, as market slugs. READ-ONLY and it reserves nothing.

    The approval layer already carries `window_starts` and `max_windows`
    and validates both against the code ceilings, the document's own
    expiry and its last-open bound; this only reads the result. A window
    that is not in that list can never be returned, so the runner cannot
    widen what the owner authorized -- it can only consume it.
    """
    from src import l5_campaign_approval as CA
    out: Dict[str, Any] = {"markets": [], "max_windows": None,
                           "readable": False, "why": None}
    try:
        v = CA.campaign_decision(None, now=now, reserve=False)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        out["why"] = "APPROVAL_UNREADABLE:%s" % type(exc).__name__
        return out
    if not isinstance(v, Mapping) or not v.get("present"):
        out["why"] = "NO_APPROVAL_DOCUMENT"
        return out
    scope = dict(v.get("scope") or {})
    if str(scope.get("series") or "") != X.MARKET_PREFIX:
        out["why"] = "APPROVAL_IS_FOR_ANOTHER_SERIES"
        return out
    mw = scope.get("max_windows")
    cap = int(mw) if isinstance(mw, int) else None

    def to_slug(t: dt.datetime) -> str:
        t = t.astimezone(UTC)
        return "%s%s-%sz" % (X.MARKET_PREFIX, t.strftime("%Y-%m-%d"),
                             t.strftime("%H%M"))

    starts = scope.get("window_starts")
    if isinstance(starts, (list, tuple)) and starts:
        # ENUMERATED: the owner named each window.
        slugs = []
        for raw in starts:
            try:
                t = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                out["why"] = "APPROVAL_WINDOW_START_UNREADABLE"
                return out
            slugs.append(to_slug(t))
        out.update(markets=sorted(set(slugs)), readable=True, mode="ENUMERATED",
                   max_windows=(cap if cap is not None else len(slugs)))
        return out

    # ROLLING: the owner bounded the campaign with `last_open_utc` instead
    # of naming each window -- the approval layer's own alternative, and
    # the same shape the 15-minute lane uses. We take the NEXT windows in
    # order, and the bound is whichever of `last_open_utc` and
    # `max_windows` binds first. Absent BOTH forms the approval layer has
    # already refused the document, so there is no permissive fallback here.
    last_open = scope.get("last_open_utc")
    if not last_open:
        out["why"] = "APPROVAL_ENUMERATES_NO_WINDOWS"
        return out
    try:
        lo = dt.datetime.fromisoformat(str(last_open).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        out["why"] = "APPROVAL_LAST_OPEN_UNREADABLE"
        return out
    step = dt.timedelta(seconds=WINDOW_S)
    t = now.astimezone(UTC).replace(second=0, microsecond=0)
    t -= dt.timedelta(minutes=t.minute % 5)
    if t <= now:                       # never a window already under way
        t += step
    rolled = []
    limit = cap if cap is not None else CA_MAX_ROLLING_WINDOWS
    while t <= lo and len(rolled) < min(limit, CA_MAX_ROLLING_WINDOWS):
        rolled.append(to_slug(t))
        t += step
    out.update(markets=rolled, readable=True, mode="ROLLING",
               last_open_utc=lo.isoformat(),
               max_windows=(cap if cap is not None else len(rolled)))
    if not rolled:
        out["why"] = "NO_WINDOW_OPENS_BEFORE_LAST_OPEN_UTC"
    return out


def arm_through_boundary(session: "Session") -> Dict[str, Any]:
    """ARM ONLY THROUGH `authorize_arming`, the one shared boundary every
    arming path in src/ uses. Every input is this session's own reading:
    the owner's campaign verdict for this exact window, the preflight that
    just ran, the 5-minute ledger, the watchdog's halt and this service's
    own operator halt, a reconciled book, venue-confirmed window bounds,
    the owner's arm token, the shadow lock and the approved capital scope.
    A refusal raises `ArmingRefusal` and the transport stays disarmed."""
    from src import l5_campaign_approval as CA
    from src import pmus_auth as Q1
    now = session._now()                                       # noqa: SLF001
    market = session.market
    h = session.halt_fn()
    scope = session.approved_scope()
    cap = session.exposure_ceiling()
    loss = _dec(scope.get("max_daily_loss_usd"))
    limits = None
    if cap is not None and loss is not None:
        limits = RunningLimits(max_usd_per_market=cap,
                               max_total_exposure_usd=cap,
                               daily_realized_loss_halt_usd=loss)
    rec = session.reconcile()
    claim = AUTH.authorize_arming(
        campaign=CA.campaign_decision(market, now=now, reserve=False),
        market=market,
        preflight=session.last_preflight,
        store=ArmingView(session.ex),
        halted=bool(not h.get("ok") or AUTH.operator_halt_engaged()),
        halt_gate_present=bool((os.getenv(HALT_URL_ENV) or "").strip()),
        reconciled=bool(rec.get("read") and rec.get("agrees")),
        unresolved=(),
        transport_armable=Q1.armed(),
        limits=limits,
        window_bounds=session.bounds_fn(market),
        nominal_window_s=WINDOW_S,
        series=X.MARKET_PREFIX,
        # the owner's own figure, clamped by the approval layer against the
        # code ceiling. One when the document names one.
        max_windows=max(1, int(session.max_windows or 1)),
        now=now,
        shadow_locked=AUTH.shadow_lock_engaged())
    session.t.arm()
    return {"claim": claim}


# ------------------------------------------------------ budget reach

def budget_reach(n_windows: int, orders_per_window: int) -> Dict[str, Any]:
    """How many approved windows the OPEN budget can actually pay for.

    These two numbers are set in different places and nothing compared
    them. Tonight they disagreed in silence: an approval enumerated 8
    windows, the ladder spent 8 OPEN submissions a window, and
    `L5_LIVE_AUTH_MAX_OPEN` was 48 -- sized long ago for a one-entry pair
    strategy that spent two. Windows 1-6 traded, window 7 was refused for
    an exhausted budget, and the campaign reported 8 approved windows
    throughout. The operator had no way to see it coming.

    This WIDENS NOTHING and refuses nothing: the budget stays the binding
    control and an exhausted one still stops every submission. It only
    says, before the first window, which window will be the last -- so a
    short campaign is a stated fact rather than a surprise at window 7.
    """
    out: Dict[str, Any] = {
        "open_budget": None, "orders_per_window": int(orders_per_window),
        "windows_approved": int(n_windows), "windows_payable": None,
        "short_by": None, "last_payable_window": None, "ok": None,
        "why": None}
    try:
        cap = AUTH.max_of_class("OPEN")
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        out["why"] = "UNREADABLE:%s" % type(exc).__name__
        return out
    out["open_budget"] = cap
    if cap is None:
        #: unconfigured is not a shortfall -- the class simply does not bind
        out["why"] = "OPEN_BUDGET_NOT_CONFIGURED"
        return out
    per = max(1, int(orders_per_window))
    payable = int(cap) // per
    out["windows_payable"] = payable
    out["ok"] = payable >= int(n_windows)
    if not out["ok"]:
        out["short_by"] = int(n_windows) - payable
        out["last_payable_window"] = payable
        out["why"] = ("OPEN_BUDGET_COVERS_%d_OF_%d_WINDOWS"
                      % (payable, int(n_windows)))
    return out


# ------------------------------------------------- settlement recovery

def recover_settlements(ex, transport_for, *, limit: int = 64) -> Dict[str, Any]:
    """Record the venue's own settlement for markets a dead window left open.

    `settle()` is reached at the END of a window. A process killed in the
    middle of one -- a redeploy, a crash, an OOM -- therefore never records
    that window, and `_own_exposure` keeps charging its filled cost against
    the account cap FOREVER, because it only skips markets present in the
    ledger's `settled` block. Observed live 2026-11-12: $4.97 of exposure on
    markets the venue reported holding nothing for, which refused the two
    outermost rungs of every straddle ladder for the rest of the day.

    This is bookkeeping, not trading. It reads the venue's `settlementPx`
    with a GET, and the only thing it writes is the ledger's `settled`
    block, through the same `ex.settle()` the live path uses -- so a market
    with an unresolved order stays provisional and unrecorded, and a market
    already settled the other way still raises rather than being reversed.
    It creates no order, cancels nothing and claims no authorization.

    A market the venue has NOT finally settled is left alone: an open or
    half-expired window must keep counting as exposure.
    """
    out: Dict[str, Any] = {
        "considered": [], "recovered": [], "left": [],
        "exposure_before_usd": None, "exposure_after_usd": None,
        "freed_usd": None, "error": None}
    try:
        before = ex.account_exposure()
        out["exposure_before_usd"] = str(before.get("own_usd"))
        # unsettled markets this ledger has ever had an order in, oldest
        # first, so a long backlog recovers deterministically.
        seen = []
        for r in ex._ledger["orders"].values():          # noqa: SLF001
            m = r.get("market")
            if m and m not in ex._ledger["settled"] and m not in seen:  # noqa: SLF001
                seen.append(m)
        out["considered"] = sorted(seen)[:limit]
        for market in out["considered"]:
            try:
                t = transport_for(market)
                verdict = t.settlement(market)
            except Exception as exc:                           # noqa: BLE001
                out["left"].append({"market": market,
                                    "why": "READ_FAILED:%s" % type(exc).__name__})
                continue
            why = verdict.get("not_terminal_reason")
            if why is not None:
                out["left"].append({"market": market, "why": why,
                                    "state": verdict.get("state")})
                continue
            px = verdict.get("settlement_px")
            if px is None or px not in (D(0), D(1)):
                out["left"].append({"market": market,
                                    "why": "SETTLEMENT_NOT_BINARY"})
                continue
            try:
                rec = ex.settle(market, px == D(1))
            except Exception as exc:                           # noqa: BLE001
                out["left"].append({"market": market,
                                    "why": "SETTLE_REFUSED:%s" % type(exc).__name__})
                continue
            # `settlement_arithmetic` is PROVISIONAL while an order of
            # ours is unresolved, and `ex.settle` deliberately records
            # nothing in that case -- the market must keep counting.
            if rec.get("provisional") or market not in ex._ledger["settled"]:  # noqa: SLF001
                out["left"].append({"market": market,
                                    "why": "PROVISIONAL",
                                    "unresolved": rec.get("unresolved_orders")})
                continue
            out["recovered"].append({
                "market": market, "up_won": bool(px == D(1)),
                "settlement_px": str(px),
                "settlement_pnl": str(rec.get("settlement_pnl")),
                "already_settled": bool(rec.get("already_settled"))})
        after = ex.account_exposure()
        out["exposure_after_usd"] = str(after.get("own_usd"))
        b, a = (_dec(out["exposure_before_usd"]),
                _dec(out["exposure_after_usd"]))
        if b is not None and a is not None:
            out["freed_usd"] = str(b - a)
    except Exception as exc:                                   # noqa: BLE001
        # A bookkeeping sweep must never stop the service from trading.
        out["error"] = "%s:%s" % (type(exc).__name__, exc)
    return out


# ------------------------------------------------------------ the session

#: ======================= PERSISTENT ROLLOVER =========================
#:
#: The bounded path gets its window list from the approval, which
#: ENUMERATES every window by name -- so it cannot roll past its own list
#: and stops at NOT_AFTER. Persistent rollover takes the window from the
#: CLOCK instead, and asks before each one whether it may still trade.
#:
#: The latch is re-read EVERY window, not once at start. A STOP or a
#: HALT written while window N is running stops window N+1, and the loop
#: exits rather than idling: a process that keeps looping after a safety
#: halt is one that will resume the moment somebody clears the latch for
#: an unrelated reason.
#:
#: NOTHING HERE REARMS. The only exits are a latch, a lost envelope, or
#: the operator stopping the process, and none of them is followed by a
#: retry.
ROLLOVER_GRACE_S = 20.0


def _utc_or_none(v: Any) -> Optional[dt.datetime]:
    """An aware UTC datetime, or None. An unreadable deadline is never
    read as 'no deadline': the caller halts on None."""
    try:
        d = dt.datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def persistent_next(*, now: dt.datetime, prefix: str,
                    horizon_s: float = 300.0,
                    grace_s: float = ROLLOVER_GRACE_S) -> Dict[str, Any]:
    """The window this lane should trade now, or why there is none.

    A window with less than `grace_s` left is NOT offered: entering one
    about to close buys the worst of both ends -- no time to work an
    order, and a settlement immediately after. The next one is offered
    instead, with the seconds to wait for it.
    """
    if not isinstance(now, dt.datetime) or now.tzinfo is None:
        return {"ok": False, "reason": "NOW_UNREADABLE", "market": None}
    if not isinstance(prefix, str) or not prefix:
        return {"ok": False, "reason": "PREFIX_UNREADABLE", "market": None}
    try:
        h = float(horizon_s)
        g = float(grace_s)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "HORIZON_UNREADABLE", "market": None}
    if h <= 0 or g < 0 or g >= h:
        return {"ok": False, "reason": "HORIZON_UNREADABLE", "market": None}
    epoch = now.timestamp()
    start = epoch - (epoch % h)
    left = (start + h) - epoch
    wait = 0.0
    if left < g:
        start += h
        wait = start - epoch
    at = dt.datetime.fromtimestamp(start, UTC)
    return {"ok": True, "reason": None,
            "market": "%s%s-%sz" % (prefix, at.strftime("%Y-%m-%d"),
                                     at.strftime("%H%M")),
            "window_start_utc": at.isoformat(),
            "wait_s": round(wait, 2),
            "seconds_left_now": round(left, 2)}


def persistent_gate(*, now: dt.datetime) -> Dict[str, Any]:
    """May this lane trade another window at all?

    Re-read every window. The envelope AND the durable state must both
    permit it, and a LATCHED state exits the loop rather than pausing
    it -- see the module note above.
    """
    env = PMODE.envelope(now=now)
    if not env.get("ok"):
        return {"ok": False, "exit": True, "reason": str(env.get("reason"))}
    st = PMODE.read_state()
    if not st.get("ok"):
        #: an unreadable state could be hiding a latch
        return {"ok": False, "exit": True, "reason": str(st.get("reason"))}
    if st["state"] in PMODE.LATCHED:
        return {"ok": False, "exit": True, "reason": "STATE_%s" % st["state"],
                "state": st["state"]}
    if st["state"] != PMODE.ARMED:
        #: MONITOR observes and submits nothing, so it waits rather than
        #: exiting: an operator may arm it without restarting anything.
        return {"ok": False, "exit": False, "reason": "STATE_NOT_ARMED",
                "state": st["state"]}
    return {"ok": True, "exit": False, "reason": None,
            "state": st["state"], "scope": env["scope"]}


#: HOW MUCH RECONCILIATION ONE ITERATION MAY DO.
#:
#: A backlog must never delay the next five-minute window. If the venue
#: goes quiet for an hour, four lanes accumulate twelve pending markets
#: each; attempting all of them with a slow venue would spend the gap
#: between windows on settlement and miss the next market -- the stall
#: this decoupling exists to remove, reappearing as a backlog instead of
#: a single blocking poll.
#:
#: So a pass is bounded two ways and NEW MARKET ROLLOVER TAKES PRIORITY:
#: at most RECONCILE_MAX_MARKETS attempts and at most RECONCILE_BUDGET_S
#: of wall clock, whichever runs out first. Nothing is dropped -- the
#: pending set is DERIVED from the ledger, so a market not reached this
#: pass is simply reached in a later one.
#:
#: OLDEST FIRST, which is why starvation is impossible: slugs sort
#: chronologically, a settled market leaves the set, so every pass makes
#: progress on the front of the queue and no market can be skipped
#: forever.
RECONCILE_MAX_MARKETS = 8
RECONCILE_BUDGET_S = 5.0


def reconcile_pending(ex: Any, transport: Any, *,
                      max_markets: int = RECONCILE_MAX_MARKETS,
                      budget_s: float = RECONCILE_BUDGET_S,
                      clock: Callable[[], float] = time.time
                      ) -> Dict[str, Any]:
    """ONE non-blocking settlement attempt per unsettled market.

    This is what makes rollover possible: a window closes, stays
    PENDING, and is settled here later while the loop trades the next
    one. It reuses `Btc5mExecutor.settle`, which is already idempotent --
    the same outcome twice writes nothing and the OTHER outcome raises,
    so a duplicate settlement event cannot double-count and a
    contradictory one cannot reverse a settled result.

    THE PENDING SET IS DERIVED from the durable ledger -- filled orders
    in a market absent from `settled` -- so a crash between closing and
    settling loses nothing and a restart picks the window up again.

    NOTHING IS RELEASED WITHOUT THE VENUE. A market the venue cannot
    settle, or settles to something that is not 0 or 1, stays pending
    and keeps its cost reserved. `up_won` is only ever the venue's own
    settlement price, never a mark or a guess.
    """
    out: Dict[str, Any] = {"pending": [], "settled": [],
                           "unreadable": [], "deferred": []}
    try:
        markets = list(ex.pending_settlement())
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"pending": [], "settled": [], "unreadable": [],
                "deferred": [], "error": type(exc).__name__}
    started = clock()
    out["considered"] = len(markets)
    #: OLDEST FIRST: slugs sort chronologically, so the front of the
    #: queue is the most overdue and progress is guaranteed.
    for n, m in enumerate(sorted(markets)):
        if n >= max(1, int(max_markets)):
            out["deferred"] = [{"market": x, "why": "WORKLOAD_BUDGET"}
                               for x in sorted(markets)[n:]]
            break
        if n and (clock() - started) >= float(budget_s):
            out["deferred"] = [{"market": x, "why": "TIME_BUDGET"}
                               for x in sorted(markets)[n:]]
            break
        verdict = None
        try:
            verdict = transport.settlement(m)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["pending"].append({"market": m,
                                   "why": "VENUE_ERROR:%s" % type(exc).__name__})
            continue
        if not (isinstance(verdict, Mapping) and verdict.get("terminal")):
            out["pending"].append({"market": m,
                                   "why": "VENUE_SETTLEMENT_NOT_SEEN"})
            continue
        px = _dec(verdict.get("settlement_px"))
        if px not in (D("0"), D("1")):
            #: not binary is not a settlement
            out["unreadable"].append({"market": m, "settlement_px": str(px)})
            continue
        try:
            rec = ex.settle(m, px == D("1"))
        except ValueError as exc:
            out["unreadable"].append({"market": m, "error": str(exc)[:120]})
            continue
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["unreadable"].append({"market": m,
                                      "error": type(exc).__name__})
            continue
        if rec.get("provisional"):
            #: an order of ours is still open in it, so it is not over
            out["pending"].append({"market": m, "why": "ORDERS_UNRESOLVED"})
            continue
        out["settled"].append({"market": m,
                               "settlement_pnl": str(rec.get("settlement_pnl")),
                               "already": bool(rec.get("already_settled"))})
    return out


#: how often the resident-after-campaign loop looks again
RESIDENT_POLL_S = 15.0
#: how often it repeats its line when nothing changed
RESIDENT_REPORT_S = 300.0


def resident_after_campaign(ex: Any, emit: Callable[[str, Any], None], *,
                            sleep: Callable[[float], None] = time.sleep,
                            clock: Callable[[], float] = time.time,
                            now: Callable[[], dt.datetime] = (
                                lambda: dt.datetime.now(UTC)),
                            halt: Optional[Callable[[], Mapping[str, Any]]]
                            = None,
                            max_passes: Optional[int] = None) -> int:
    """After a campaign's NOT_AFTER: stay up, DISARMED, and keep reading.

    WHY. Observed 2026-11-21: the 06:20Z window closed, NOT_AFTER passed,
    the loop returned 0 and the fleet stopped. For six hours the watchdog
    read this lane's registry as REGISTRY_UNREACHABLE, and the window's
    settlement (the venue settled it at 06:26:56Z) was not recorded in
    this lane's ledger until the next boot. A lane that has just traded
    is the one the watchdog most needs to see.

    WHAT THIS DOES. Nothing that can send an order: it never calls a
    window, and the transport it reads through was disarmed by the
    session close. Each pass it
      * exits on a latched or unreadable state (a latch is never idled
        over), and turns a watchdog halt into the durable latch and an
        unwind, exactly as the trading loop does;
      * makes ONE settlement attempt per pending market and releases only
        what the venue settled.
    The stored state is already MONITOR (written before this is entered)
    and this function never writes ARMED, so nothing here can resume
    trading: re-arming needs a new process and its startup ceremony.
    Returns the process exit code. `max_passes` exists for tests.
    """
    last_report = None
    last_key = None
    passes = 0
    #: GIVE UP THE ARM AGAIN, and refuse to stay if it does not go. The
    #: session close already did this; a transport that still reads armed
    #: here is one this loop must not sit next to.
    t0 = getattr(ex, "transport", None)
    if t0 is not None:
        try:
            t0.disarm("BTC5M_RESIDENT_AFTER_CAMPAIGN")
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        if bool(getattr(t0, "armed", False)):
            emit("L5_BTC5M_RESIDENT_EXIT",
                 {"reason": "TRANSPORT_STILL_ARMED",
                  "utc": now().isoformat()})
            return 0
    while True:
        passes += 1
        now_ = now()
        st = PMODE.read_state()
        if not st.get("ok") or st.get("state") in PMODE.LATCHED:
            emit("L5_BTC5M_RESIDENT_EXIT",
                 {"reason": ("STATE_%s" % st.get("state") if st.get("ok")
                             else str(st.get("reason"))),
                  "utc": now_.isoformat()})
            return 0
        if st.get("state") != PMODE.MONITOR:
            #: this loop is only ever entered after MONITOR was written.
            #: Anything else means something outside wrote the state; it
            #: is not this function's to interpret, and never to trade on.
            emit("L5_BTC5M_RESIDENT_EXIT",
                 {"reason": "STATE_NOT_MONITOR:%s" % st.get("state"),
                  "utc": now_.isoformat()})
            return 0
        wd = (halt() if halt is not None
              else shared_halt((os.getenv(HALT_URL_ENV) or "").strip()))
        if not wd.get("ok"):
            PMODE.write_state(PMODE.HALTED,
                              why="WATCHDOG:%s" % wd.get("reason"), now=now_)
            unwound = ex.stop(reason="WATCHDOG:%s" % wd.get("reason"),
                              now=now_)
            emit("L5_BTC5M_PERSISTENT_HALTED",
                 {"reason": wd.get("reason"), "source": "WATCHDOG",
                  "stop": unwound, "utc": now_.isoformat()})
            return 0
        t = getattr(ex, "transport", None)
        recon = ({"pending": [], "settled": [], "unreadable": [],
                  "deferred": [], "error": "NO_TRANSPORT"}
                 if t is None else reconcile_pending(ex, t))
        try:
            freed = ex.sync_reservation()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            freed = {"ok": False, "reason": type(exc).__name__}
        key = json.dumps([recon.get("pending"), recon.get("unreadable"),
                          recon.get("deferred"), recon.get("error")],
                         sort_keys=True, default=str)
        due = (last_report is None or recon.get("settled")
               or key != last_key
               or (clock() - last_report) >= RESIDENT_REPORT_S)
        if due:
            emit("L5_BTC5M_RESIDENT_AFTER_CAMPAIGN",
                 {"can_submit": False, "stored_state": st.get("state"),
                  "transport_armed": bool(getattr(t, "armed", False)),
                  "serving_registry": bool(
                      (os.getenv(FILLS_PORT_ENV) or "").strip()),
                  "settlement": recon,
                  "pending_count": len(recon.get("pending") or []),
                  "reservation": freed,
                  "why": ("the campaign is over; staying up so the shared "
                          "watchdog can read this lane's registry and so "
                          "its settlements are recorded. Nothing here can "
                          "place an order"),
                  "utc": now_.isoformat()})
            last_report = clock()
            last_key = key
        if max_passes is not None and passes >= max_passes:
            return 0
        sleep(RESIDENT_POLL_S)


#: the refusal `_submit` emits when the shared hold cannot be taken
BUDGET_REFUSAL = "SHARED_RESERVATION:RESERVATION_WOULD_EXCEED_CAP"


def _budget_exhausted(events: Any) -> Dict[str, Any]:
    """Every order this window lost to the shared budget, with its EV.

    Read from the step records already emitted, so nothing new is
    stored and a window that refused nothing reports a plain zero.
    """
    out: Dict[str, Any] = {"count": 0, "notional_usd": "0", "orders": [],
                           "note": ("orders this lane was authorized to "
                                    "place and could not fund because a "
                                    "sibling lane held the room; the "
                                    "allocation is arrival-order, so a "
                                    "high count with high EV is the case "
                                    "for ranking by expected value")}
    total = D(0)
    for rec in (events or ()):
        if not isinstance(rec, Mapping):
            continue
        for r in (rec.get("refused") or ()):
            if not isinstance(r, Mapping):
                continue
            if str(r.get("reason")) != BUDGET_REFUSAL:
                continue
            detail = r.get("detail") or {}
            intent = r.get("order") or {}
            n = _dec(detail.get("adding"))
            if n is not None:
                total += n
            out["count"] += 1
            out["orders"].append({
                "side": intent.get("side"),
                "price": str(intent.get("price")),
                "qty": str(intent.get("qty")),
                "strategy": intent.get("strategy") or intent.get("reason"),
                "notional_usd": None if n is None else str(n),
                "aggregate_at_the_time_usd": detail.get("aggregate_usd"),
                "cap_usd": detail.get("cap_usd"),
                "t_in_window_s": rec.get("t_in_window_s"),
            })
    out["notional_usd"] = str(total)
    return out


def _attribution_block(ex: Any, market: Any, settled: Any,
                       fees: Any) -> Dict[str, Any]:
    """`Btc5mExecutor.attribution` plus each strategy's own venue fees.

    Returns {} rather than guessing when the market, the settlement
    outcome or the fee set is unreadable: a wrong attribution is worse
    than an absent one.
    """
    try:
        won = (settled or {}).get("up_won")
        rows = ex.attribution(str(market),
                              won if isinstance(won, bool) else None)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"error": type(exc).__name__}
    per_order = (fees or {}).get("per_order") or {}
    complete = bool((fees or {}).get("complete"))
    orders = getattr(ex, "_ledger", {}).get("orders") or {}
    out: Dict[str, Any] = {}
    for who, row in rows.items():
        fee = D(0)
        unreadable = []
        for coid, r in orders.items():
            if r.get("market") != str(market):
                continue
            if str(r.get("strategy") or "") != who:
                continue
            if coid in per_order:
                try:
                    fee += D(str(per_order[coid]))
                except Exception:                              # noqa: BLE001
                    unreadable.append(coid)
        net = row.get("settled_net_usd")
        out[who] = {
            "filled_qty": str(row["filled_qty"]),
            "cost_total_usd": str(row["cost_total"]),
            "up_qty": str(row[C.UP]["qty"]),
            "down_qty": str(row[C.DOWN]["qty"]),
            "redemption_total_usd": (None if row["redemption_total"] is None
                                     else str(row["redemption_total"])),
            "venue_fees_usd": str(fee),
            "fees_complete": complete and not unreadable,
            #: settled NET of this strategy's own fees, and None unless
            #: both the outcome and every fee it owes are readable.
            "settled_net_usd": (None if net is None or not complete
                                or unreadable else str(net - fee)),
        }
    return out


class Session:
    """One window. Every collaborator is injectable; nothing is global."""

    def __init__(self, *, market: str, executor: "X.Btc5mExecutor",
                 transport: Any, root: str,
                 book_fn: Callable[[str], Mapping[str, Any]],
                 p_fn: Callable[[str, float, float], Mapping[str, Any]],
                 risk_fn: Callable[[dt.datetime, Decimal], Mapping[str, Any]],
                 halt_fn: Callable[[], Mapping[str, Any]],
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 armed_fn: Optional[Callable[[], bool]] = None,
                 arm_fn: Optional[Callable[[Any], Any]] = None,
                 bounds_fn: Optional[Callable[[str], Any]] = None,
                 max_windows: int = 1,
                 settle_wait_s: float = SETTLE_WAIT_S) -> None:
        #: HOW LONG CLOSING MAY WAIT FOR THE VENUE.
        #:
        #: The bounded campaign keeps the original 1800s: it trades a
        #: fixed list and has nothing to miss by waiting. PERSISTENT
        #: rollover passes 0, because `one_window` was SYNCHRONOUS
        #: THROUGH SETTLEMENT and that made the loop unable to advance
        #: while a prior window waited -- observed 2026-11-16: four lanes
        #: entered 1520z, closed clean, and then sat in this poll for the
        #: rest of a 23-minute run, completing 4 of 16 windows. Windows
        #: are 5 minutes apart and this wait was bounded at 30, so any
        #: settlement slower than the gap silently skipped windows.
        #:
        #: At 0 the window closes and the market stays PENDING; the loop
        #: reconciles it out of band. Its cost stays reserved until the
        #: venue settles it, so advancing never frees risk.
        self.settle_wait_s = float(settle_wait_s)
        self.market, self.ex, self.t = market, executor, transport
        self.root = root
        self.book_fn, self.p_fn = book_fn, p_fn
        self.risk_fn, self.halt_fn = risk_fn, halt_fn
        self.clock, self.sleep = clock, sleep
        self.armed_fn = armed_fn
        #: WHO MAY ARM THE TRANSPORT: `arm_through_boundary`, i.e. only
        #: through `l5_live_authorization.authorize_arming`, the one shared
        #: boundary every arming path in src/ uses (three guard tests pin
        #: it). `arm_fn` exists so a test can substitute the venue double's
        #: own arm; production never passes one.
        self.arm_fn = arm_fn if arm_fn is not None else (
            lambda t: arm_through_boundary(self))
        self.bounds_fn = bounds_fn or venue_window_bounds
        #: how many windows the OWNER's approval enumerates. It is passed
        #: to `authorize_arming` so the boundary checks this session against
        #: the figure the owner actually wrote, not a hardcoded 1. It
        #: authorizes nothing by itself.
        self.max_windows = int(max_windows or 1)
        self.last_preflight: Any = None
        self.events: List[Dict[str, Any]] = []
        self.would_submit = 0

    def _now(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.clock(), UTC)

    def _log(self, kind: str, doc: Mapping[str, Any]) -> None:
        rec = dict(doc, market=self.market, utc=self._now().isoformat())
        self.events.append(dict(rec, kind=kind))
        emit(kind, rec)

    def marker(self) -> str:
        return os.path.join(self.root, SESSION_DIR, "%s.done" % self.market)

    def _own_unaccounted(self) -> Decimal:
        """This strategy's settled result today, losses only. The BTC15m
        ledger books BTC5m results only if it ever adopted the position, so
        none of this is assumed to be inside its figure already."""
        got = self.ex.day_settled_pnl(self._now())
        if got is None:
            raise ValueError("own settled result unreadable")
        #: the same rows, by market, so `account_risk` can leave out a
        #: market the account service has already booked. If the breakdown
        #: cannot be read the total alone is passed and all of it is added.
        by = getattr(self.ex, "day_settled_by_market", None)
        by = by(self._now()) if callable(by) else None
        if isinstance(by, Mapping) and sum(by.values(), D(0)) == got:
            #: the WHOLE settled history too, for the lifetime figure. A
            #: history that cannot be read refuses: it is the only record
            #: of what this lane lost on earlier days.
            hist = getattr(self.ex, "settled_history", None)
            hist = hist() if callable(hist) else None
            if hist is None:
                raise ValueError("own settled history unreadable")
            return OwnSettled.of(by, hist)
        return min(D(0), got)

    def _overlap_refusal(self, pending: List[str]) -> Optional[Dict[str, Any]]:
        """Why an unsettled earlier window forbids trading this one, or None.
        See CAMPAIGN_OVERLAP_ENV. A market verified once in this window is
        not read again; anything unreadable is a refusal."""
        if len(pending) > 1:
            return {"reason": "CAMPAIGN_TOO_MANY_UNSETTLED_WINDOWS"}
        m = pending[0]
        seen = getattr(self, "_overlap_verified", None)
        if seen is None:
            seen = self._overlap_verified = set()
        if m in seen:
            return None
        try:
            if any(str(r.get("market")) == m
                   for r in self.ex.live_orders().values()):
                return {"reason": "CAMPAIGN_PREVIOUS_WINDOW_HAS_OPEN_ORDERS"}
            legs = self.ex.inventory(m)
            rows = [r for r in (self.t.positions() or ())
                    if isinstance(r, Mapping) and str(
                        r.get("marketSlug") or r.get("market")) == m]
            if len(rows) == 1:
                up = _dec(rows[0].get("qtyBoughtDecimal"))
                dn = _dec(rows[0].get("qtySoldDecimal"))
                if up is None or dn is None:
                    return {"reason":
                            "CAMPAIGN_PREVIOUS_WINDOW_VENUE_ROW_UNREADABLE"}
                if up != legs[C.UP]["qty"] or dn != legs[C.DOWN]["qty"]:
                    return {"reason":
                            "CAMPAIGN_PREVIOUS_WINDOW_DISAGREES_WITH_VENUE",
                            "venue": {"up": str(up), "down": str(dn)},
                            "ours": {"up": str(legs[C.UP]["qty"]),
                                     "down": str(legs[C.DOWN]["qty"])}}
            else:
                #: no row (or more than one): only acceptable when the
                #: venue itself says the market has settled, in which case
                #: the mid-window read below records it
                v = self.t.settlement(m)
                if not (isinstance(v, Mapping) and v.get("terminal")):
                    #: ...or when this process verified both legs against
                    #: the venue as that window closed, moments ago
                    if rows or not close_verified(m, legs, self.clock()):
                        return {"reason":
                                "CAMPAIGN_PREVIOUS_WINDOW_NOT_VERIFIABLE",
                                "venue_rows": len(rows)}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"reason": "CAMPAIGN_PREVIOUS_WINDOW_READ_FAILED",
                    "error": type(exc).__name__}
        seen.add(m)
        return None

    def settle_earlier_windows(self, now: float) -> Optional[Dict[str, Any]]:
        """Under a campaign with overlap on: one settlement attempt for each
        earlier window, at most every MIDWINDOW_SETTLE_S. Reads only, and
        releases only what the venue settled. Never raises."""
        if not (PROBE.campaign_configured() and campaign_overlap()):
            return None
        last = getattr(self, "_midwindow_settle_at", None)
        if last is not None and now - last < MIDWINDOW_SETTLE_S:
            return None
        self._midwindow_settle_at = now
        try:
            if not [m for m in self.ex.pending_settlement()
                    if m != self.market]:
                return None
            recon = reconcile_pending(self.ex, self.t)
            freed = self.ex.sync_reservation()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"error": type(exc).__name__}
        if recon.get("settled") or recon.get("unreadable"):
            self._log("L5_BTC5M_MIDWINDOW_SETTLEMENT",
                      {"settlement": recon, "reservation": freed})
        return recon

    def gates(self) -> Dict[str, Any]:
        h = self.halt_fn()
        if not h.get("ok"):
            return {"ok": False, "reason": "SHARED_HALT:%s" % h.get("reason")}
        if AUTH.operator_halt_engaged():
            return {"ok": False, "reason": "OPERATOR_HALT"}
        try:
            own = self._own_unaccounted()
        except ValueError:
            return {"ok": False, "reason": "OWN_SETTLED_RESULT_UNREADABLE"}
        #: UNDER A CAMPAIGN DOCUMENT, AN EARLIER WINDOW STILL AWAITING THE
        #: VENUE'S SETTLEMENT STOPS NEW EXPOSURE. Its result is not known,
        #: so it cannot have been reconciled. See CAMPAIGN_RECON_GRACE_S.
        #:
        #: AND A WINDOW THAT WAITED IS NOT JOINED PART-WAY (owner decision
        #: 2026-11-21). Starting a window at, say, 150 s is a different
        #: exposure and a different execution problem from starting it at
        #: the open, and it has never been tested. So once one of the
        #: campaign's own waits has refused in THIS window, the window is
        #: sat out to its end, even if the wait clears a moment later. The
        #: flag lives on this session object, which exists for one window.
        #: Only the campaign's waits set it: the brief account-risk
        #: refusals a window already rides through are unchanged.
        if PROBE.campaign_configured():
            _sat = getattr(self, "_campaign_sat_out", None)
            if _sat:
                return {"ok": False, "reason": "CAMPAIGN_WINDOW_SAT_OUT",
                        "first_refusal": _sat}
            try:
                _pend = sorted(m for m in self.ex.pending_settlement()
                               if m != self.market)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self._campaign_sat_out = \
                    "CAMPAIGN_PENDING_SETTLEMENT_UNREADABLE"
                return {"ok": False,
                        "reason": "CAMPAIGN_PENDING_SETTLEMENT_UNREADABLE"}
            if _pend and campaign_overlap():
                _why = self._overlap_refusal(_pend)
                if _why:
                    self._campaign_sat_out = _why["reason"]
                    return dict(_why, ok=False, pending=_pend)
            elif _pend:
                self._campaign_sat_out = \
                    "CAMPAIGN_PREVIOUS_WINDOW_NOT_SETTLED"
                return {"ok": False,
                        "reason": "CAMPAIGN_PREVIOUS_WINDOW_NOT_SETTLED",
                        "pending": _pend}
        r = self.risk_fn(self._now(), own)
        if not r.get("ok"):
            if r.get("reason") == "CAMPAIGN_LEDGER_NOT_RECONCILED" \
                    and PROBE.campaign_configured():
                self._campaign_sat_out = "CAMPAIGN_LEDGER_NOT_RECONCILED"
            return {"ok": False, "reason": "ACCOUNT_RISK:%s" % r.get("reason"),
                    "risk": dict(r)}
        # THE WORST CASE MUST FIT INSIDE WHAT THE ACCOUNT MAY STILL LOSE.
        # The account-wide halts are judged on SETTLED results, so a position
        # opened with less room than it could lose would overshoot them
        # after the fact. The bound used is this strategy's whole exposure
        # ceiling -- the most it can have at risk at once -- so nothing it
        # is allowed to open can take the account past either limit.
        room = _dec(r.get("loss_room_usd"))
        cap = self.exposure_ceiling()
        if room is None or cap is None:
            return {"ok": False, "reason": "LOSS_ROOM_UNREADABLE",
                    "risk": dict(r)}
        if cap > room:
            return {"ok": False, "reason": "EXPOSURE_CEILING_EXCEEDS_LOSS_ROOM",
                    "exposure_ceiling_usd": str(cap),
                    "loss_room_usd": str(room), "risk": dict(r)}
        return {"ok": True, "risk": dict(r)}

    def approved_scope(self) -> Dict[str, Any]:
        """The owner's approval for THIS window as the campaign layer reads
        it (already clamped to the code ceilings), or {} when there is no
        valid approval naming it. Read-only: reserves nothing. The halts are
        NOT applied here, so a refusal downstream is named for its cause."""
        from src import l5_campaign_approval as CA
        try:
            v = CA.campaign_decision(self.market, now=self._now(),
                                     reserve=False)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {}
        if not (v.get("present") and v.get("ok")):
            return {}
        scope = dict(v.get("scope") or {})
        if str(scope.get("series") or "") != X.MARKET_PREFIX:
            return {}
        return scope

    def exposure_ceiling(self) -> Optional[Decimal]:
        """The smaller of the adapter's own ceiling and the owner's approved
        exposure for this window. None when the approval cannot be read."""
        scope = self.approved_scope()
        approved = _dec(scope.get("max_total_exposure_usd"))
        own = _dec(self.ex.params.max_exposure_usd)
        if own is None:
            return None
        if approved is None:
            # no readable approval: the claim refuses every order anyway;
            # the adapter's own ceiling is the bound that applies
            return own
        return min(own, approved)

    def start(self) -> Dict[str, Any]:
        """Preflight and arm, AT THE WINDOW START. Never raises.

        A 5-minute market reads MARKET_STATUS_CLOSED until its window opens
        (observed on the venue 2026-11-12), and preflight requires an open
        market, so a preflight run at launch could never pass. The session
        therefore waits for the window, then preflights and arms. A window
        already past its last trading second is refused, never chased."""
        if os.path.exists(self.marker()):
            return {"ok": False, "reason": "MARKET_ALREADY_RUN",
                    "marker": self.marker()}
        ws = window_start(self.market)
        if ws is None or not self.market.startswith(X.MARKET_PREFIX):
            return {"ok": False, "reason": "NOT_A_BTC5M_MARKET"}
        if self.clock() >= ws.timestamp() + WINDOW_S - CANCEL_LEAD_S:
            return {"ok": False, "reason": "WINDOW_ALREADY_PAST"}
        while self.clock() < ws.timestamp():
            self.sleep(min(STEP_S, ws.timestamp() - self.clock()))
        attempts: List[Dict[str, Any]] = []
        deadline = ws.timestamp() + PREFLIGHT_UNTIL_S
        while True:
            pf = self._preflight_once()
            attempts.append({
                "t_in_window_s": round(self.clock() - ws.timestamp(), 2),
                "preflight": (pf or {}).get("preflight"),
                "failed": self._failed_checks(pf),
                "error": (pf or {}).get("error")})
            if (pf or {}).get("preflight") == "PASSED":
                break
            wait = PREFLIGHT_BACKOFF_S[min(len(attempts) - 1,
                                           len(PREFLIGHT_BACKOFF_S) - 1)]
            if self.clock() + wait > deadline:
                break
            self.sleep(wait)
        self.t.cancel_owner_oracle = self.ex.owns_venue_order_id
        self.last_preflight = pf
        armed = False
        arm_error = None
        if (pf or {}).get("preflight") == "PASSED":
            try:
                self.arm_fn(self.t)
                armed = bool(getattr(self.t, "armed", False))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                arm_error = "%s: %s" % (
                    getattr(exc, "reason", None) or type(exc).__name__,
                    str(getattr(exc, "detail", "") or exc)[:200])
        rep = {"ok": True, "preflight": (pf or {}).get("preflight"),
               "preflight_failed": self._failed_checks(pf),
               "preflight_attempts": attempts,
               "transport_armed": armed, "arm_error": arm_error,
               # the owner's per-class submission budgets, BEFORE the
               # window is spent finding out that one of them binds
               "submission_budget": submission_budget(),
               "window_start_utc": ws.isoformat()}
        self._log("L5_BTC5M_START", rep)
        return rep

    def _preflight_once(self) -> Dict[str, Any]:
        """One preflight. The transport RAISES on any failed gate and keeps
        the report on `preflight_report`; that report is returned so the
        failing check is named instead of lost."""
        try:
            return self.t.preflight()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            rep = getattr(self.t, "preflight_report", None)
            out = dict(rep) if isinstance(rep, Mapping) else {}
            out["preflight"] = "FAILED"
            out["error"] = type(exc).__name__
            return out

    @staticmethod
    def _failed_checks(pf: Any) -> List[str]:
        return [str(c.get("check")) for c in (pf or {}).get("checks") or ()
                if isinstance(c, Mapping) and not c.get("passed")
                and c.get("blocking", True)]

    def trade(self) -> None:
        ws = window_start(self.market).timestamp()
        stop_at = ws + WINDOW_S - CANCEL_LEAD_S
        while self.clock() < ws:
            self.sleep(min(STEP_S, ws - self.clock()))
        while self.clock() < stop_at:
            now = self.clock()
            self.settle_earlier_windows(now)
            g = self.gates()
            if not g["ok"]:
                rep = self.ex.end_of_window(self.market)
                #: `gates()` already computed WHY it refused -- the
                #: exposure ceiling it was judged against, the account's
                #: remaining loss room, and the whole risk payload -- and
                #: this record was throwing all of it away, leaving only a
                #: reason code. Observed 2026-11-12 18:53Z: a campaign
                #: blocked on EXPOSURE_CEILING_EXCEEDS_LOSS_ROOM said
                #: nothing about which of the TWO daily-loss limits bound
                #: it (the campaign approval's max_daily_loss_usd, or the
                #: account-wide risk service's loss_room_usd), so the wrong
                #: one was raised first. The numbers cost nothing to carry.
                self._log("L5_BTC5M_BLOCKED", {
                    "reason": g["reason"],
                    "exposure_ceiling_usd": g.get("exposure_ceiling_usd"),
                    "loss_room_usd": g.get("loss_room_usd"),
                    "risk": dict(g.get("risk") or {}),
                    "cancelled": [c["venue_order_id"]
                                  for c in rep.get("cancelled") or ()]})
            else:
                #: A FAILED READ IS A STEP WITH NO BOOK, never the end of
                #: the process. See `read_book`.
                book = read_book(self.book_fn, self.market)
                est = self.p_fn(self.market, ws, now) or {}
                p_up = est.get("p_up") if est.get("ok") else None
                #: TOP-OF-BOOK SIZE, PROMOTED FROM THE LOG TO THE
                #: DECISION. The PUBLIC market endpoint publishes no size
                #: at any level -- depth is null in all 27,425 aligned
                #: rows -- so a strategy that requires executable size on
                #: the side it is buying could never be satisfied from it.
                #: `book_depth()` reads the AUTHENTICATED socket this
                #: session already runs, which does carry size, and until
                #: now nothing but the log read it.
                #:
                #: Only the SIZES are taken. bid/ask still come from the
                #: public endpoint, because replacing them would change
                #: what the straddle prices against, and that is a
                #: different change needing its own evidence. The auth
                #: book's own quotes are recorded beside them so a
                #: divergence is visible rather than silent.
                #:
                #: FAIL CLOSED: an unreadable socket yields no sizes, the
                #: late favourite refuses NO_DEPTH, and nothing is placed.
                #: Absent size is never read as unlimited size.
                #: AND THE TWO SOURCES MUST DESCRIBE THE SAME BOOK.
                #: `depth_for_pricing` compares the authenticated
                #: snapshot's OWN bid/ask -- which this call used to
                #: discard -- against the public quote we price against,
                #: and enforces the freshness of both. A complementary
                #: token, transposed sides, another market or a stale
                #: socket each size NOTHING and are named individually,
                #: so a wiring error is never reported as latency.
                depth = self.book_depth()
                sized = SAFE.depth_for_pricing(book=None, public=book,
                                               auth=depth)
                rep = self.ex.step(
                    market=self.market,
                    book=step_book(book, sized),
                    t_in_window_s=now - ws, p_up=p_up,
                    p_source=("VENUE_INDEX" if p_up is not None else None),
                    #: the index age the estimator already measured, and
                    #: the account-wide loss room `gates()` already read
                    p_age_s=est.get("index_age_s"),
                    loss_room_usd=g.get("loss_room_usd"),
                    exposure_ceiling_usd=self.exposure_ceiling())
                self.would_submit += len(rep.get("would_submit") or ())
                #: THE LATE-WINDOW DECISION RECORD, reporting only.
                #:
                #: Everything here is already in hand: `est` is the
                #: estimate this step ALREADY made and carries the strike,
                #: the BRTI bucket and its age; `sized` is the depth the
                #: pricing path ALREADY read; `rep` is the decision that
                #: has ALREADY been taken. `corrected_p_up` is a pure
                #: function of `est`. So this costs one dict and no venue
                #: call, and it cannot alter a decision: it is built AFTER
                #: `self.ex.step` has returned.
                #:
                #: The corrected probability is REPORTED, NEVER USED. The
                #: engine priced this step on `p_up` whatever this says,
                #: and `L5_BTC_REF_LATE_VARIANCE` stays off.
                _late = None
                try:
                    #: LAZY, and inside the guard. `l5_btc_reference` is
                    #: already COPYd into deploy/l5_btc5m_live (the runner
                    #: imports it to build the poller), so this is shipped
                    #: -- test_lazy_src_imports_on_the_live_path_are_
                    #: shipped checks exactly that. If it ever were not,
                    #: the except below drops the field and trading is
                    #: untouched.
                    from src import l5_btc_reference as REF_P
                    _sl = est.get("seconds_left")
                    if _sl is not None and 0 < float(_sl) <= LATE_LOG_S:
                        _late = {
                            "seconds_left": round(float(_sl), 2),
                            "strike": est.get("strike"),
                            "index": est.get("index"),
                            "distance": est.get("distance"),
                            #: official BRTI bucket and its freshness
                            "bucket_t": est.get("bucket_t"),
                            "index_age_s": est.get("index_age_s"),
                            "receive_lag_s": est.get("receive_lag_s"),
                            "source": est.get("source"),
                            #: the two probabilities, side by side
                            "p_up": p_up,
                            "p_up_corrected": REF_P.corrected_p_up(est),
                            "sd": est.get("sd"),
                            "sigma_used": est.get("sigma_used"),
                            #: executable prices and the depth behind them
                            "yes_bid": book.get("bid"),
                            "yes_ask": book.get("ask"),
                            "bid_shares": sized.get("bid_shares"),
                            "ask_shares": sized.get("ask_shares"),
                            "book_age_s": book.get("age_s"),
                            #: every candidate opening and its verdict
                            "candidates": [
                                {"side": w.get("side"),
                                 "price": w.get("price"),
                                 "qty": w.get("qty"),
                                 "passed": False,
                                 "reason": w.get("blocked_by")}
                                for w in (rep.get("would_submit") or ())
                            ] + [
                                {"side": pl.get("side"),
                                 "price": pl.get("price"),
                                 "qty": pl.get("qty"),
                                 "passed": True, "reason": None}
                                for pl in (rep.get("placed") or ())
                            ],
                            "refusals": [r.get("reason")
                                         for r in rep.get("refused") or ()],
                        }
                except BaseException as exc:            # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    #: REPORTING MUST NEVER BREAK TRADING. The decision is
                    #: already made and already acted on by this point; a
                    #: log field that cannot be built is named and dropped.
                    _late = {"error": type(exc).__name__}
                self._log("L5_BTC5M_STEP", {
                    "t_in_window_s": round(now - ws, 2),
                    "late": _late,
                    "bid": book.get("bid"), "ask": book.get("ask"),
                    "book_reason": book.get("reason"),
                    "p_up": p_up, "p_reason": est.get("reason"),
                    "action": rep.get("action"), "why": rep.get("why"),
                    "placed": [(p["side"], p["price"], p["qty"],
                                p["venue_order_id"])
                               for p in rep.get("placed") or ()],
                    "cancelled": [c.get("venue_order_id")
                                  for c in rep.get("cancelled") or ()],
                    "fills": [(f["side"], str(f["qty"]), str(f["price"]),
                               f["venue_order_id"])
                              for f in rep.get("fills") or ()],
                    "refused": [r.get("reason")
                                for r in rep.get("refused") or ()],
                    "would_submit_not_sent": len(rep.get("would_submit")
                                                 or ()),
                    "halted_reason": rep.get("halted_reason"),
                    "arming_refusal": rep.get("arming_refusal"),
                    # WHAT IS CONSUMING THE ACCOUNT CEILING. `_place`
                    # computes this on every placement attempt and it was
                    # being thrown away, so a window refused
                    # ACCOUNT_EXPOSURE_CAP could not say WHY from the logs
                    # (observed live 2026-11-12 15:55Z). Foreign rows are
                    # charged $1.00 a contract and positions count GROSS on
                    # both legs, so a matched pair of 0.17+0.17 elsewhere on
                    # the account consumes $0.34 of the ceiling while
                    # carrying almost no risk. No extra venue call: this is
                    # the figure the placement already read.
                    "account": {k: (((rep.get("exposure") or {})
                                     .get("account")) or {}).get(k)
                                for k in ("total_usd", "own_usd", "other_usd",
                                          "other_open_order_contracts",
                                          "other_position_contracts",
                                          "unknown", "unknown_reason")},
                    #: top-of-book SIZE from the authenticated socket, the
                    #: only feature class the gate sweep left untested and
                    #: the one the public tape cannot carry. Observational.
                    "depth": self.book_depth(),
                    #: WHY SIZE WAS WITHHELD, when it was. Without this a
                    #: window that placed nothing for want of trustworthy
                    #: depth looks identical to one with no opportunity.
                    "depth_for_pricing": {
                        k: v for k, v in (sized or {}).items()
                        if k != "checked"},
                    "entry": {k: (rep.get("entry_eval") or {}).get(k) for k in
                              ("why", "cap_usd", "completion_reserve_usd",
                               "room_usd", "price", "qty")},
                    "hedge": {k: (rep.get("hedge_eval") or {}).get(k) for k in
                              ("why", "price", "qty", "pair_cost", "room_usd")}})
            self.sleep(max(0.0, min(STEP_S, stop_at - self.clock())))

    def close(self) -> Dict[str, Any]:
        """Cancel every own order by exact id until each reads terminal."""
        ws = window_start(self.market).timestamp()
        deadline = ws + WINDOW_S + CANCEL_GRACE_S
        while True:
            rep = self.ex.end_of_window(self.market)
            left = (rep.get("still_open") or []) + (rep.get("unresolved") or [])
            self._log("L5_BTC5M_CLOSE", {
                "cancelled": [c["venue_order_id"]
                              for c in rep.get("cancelled") or ()],
                "filled_while_cancelling": [c["venue_order_id"] for c in
                                            rep.get("filled") or ()],
                "still_open": [c.get("venue_order_id") for c in left]})
            if not left or self.clock() >= deadline:
                break
            self.sleep(2.0)
        try:
            self.t.disarm("BTC5M_SESSION_CLOSED")
        except BaseException:                                  # noqa: BLE001
            pass
        return {"clean": not left,
                "unresolved": [c.get("venue_order_id") for c in left]}

    def settle(self) -> Dict[str, Any]:
        ws = window_start(self.market).timestamp()
        until = ws + WINDOW_S + self.settle_wait_s
        verdict = None
        #: ALWAYS ONE ATTEMPT, then poll until the deadline. With
        #: `settle_wait_s` 0 the deadline is already past, so a
        #: while-loop would make NO attempt at all and report the window
        #: unsettled without having asked -- which would leave every
        #: persistent window pending even when the venue was ready.
        while True:
            try:
                verdict = self.t.settlement(self.market)
            except BaseException:                              # noqa: BLE001
                verdict = None
            if verdict and verdict.get("terminal"):
                break
            if self.clock() >= until:
                break
            self.sleep(SETTLE_POLL_S)
        if not (verdict and verdict.get("terminal")):
            return {"settled": False, "reason": "VENUE_SETTLEMENT_NOT_SEEN",
                    "last": verdict}
        px = _dec(verdict.get("settlement_px"))
        if px not in (D(0), D(1)):
            return {"settled": False, "reason": "SETTLEMENT_NOT_BINARY",
                    "settlement_px": str(px)}
        return dict(self.ex.settle(self.market, px == D(1)), settled=True,
                    settlement_px=str(px))

    def reconcile(self) -> Dict[str, Any]:
        """Our inventory beside the venue's own position row for the
        market. Agreement is stated, never assumed."""
        inv = self.ex.inventory(self.market)
        try:
            rows = [r for r in (self.t.positions() or ())
                    if isinstance(r, Mapping) and str(
                        r.get("marketSlug") or r.get("market")) == self.market]
        except BaseException as exc:                           # noqa: BLE001
            return {"read": False, "error": type(exc).__name__}
        net_ours = inv[C.UP]["qty"] - inv[C.DOWN]["qty"]
        venue_net = None
        for r in rows:
            venue_net = _dec(r.get("netPositionDecimal")
                             if r.get("netPositionDecimal") is not None
                             else r.get("netPosition"))
        return {"read": True, "venue_rows": rows,
                "ours": {k: {"qty": str(v["qty"]), "cost": str(v["cost"])}
                         for k, v in inv.items()},
                "ours_net_up_minus_down": str(net_ours),
                "venue_net": None if venue_net is None else str(venue_net),
                "agrees": (venue_net == net_ours) if venue_net is not None
                else (net_ours == 0 and not rows),
                "note": ("the venue nets the two outcome tokens; a matched "
                         "pair reads as zero there")}

    def book_depth(self) -> Dict[str, Any]:
        """SIZE at the top of the authenticated book, for the record only.

        WHY THIS EXISTS. `replay_gates.py` closed the entry-state filter
        family: across 17 gates, selectivity and per-share result are
        rank-correlated the WRONG way (rho +0.833, t +5.84) because a
        resting maker buy is only reached when the market comes down to it,
        so any state that raises the fill probability is the state in which
        the price is moving against us. The one feature class that is about
        the FILL rather than the pre-fill state -- queue ahead of us and
        order-flow imbalance -- has never been testable, because the
        PUBLIC market endpoint publishes no size at any level
        (`btc5m_aligned_recorder.DEPTH_REASON`; depth is null in all 27,425
        aligned rows). The AUTHENTICATED socket does carry it, and this
        session already runs that socket for the preflight substitute, so
        the sizes are reachable at zero extra venue cost -- they were
        simply never written down.

        STRICTLY OBSERVATIONAL. Nothing reads this but the log. It takes no
        decision, places no order and cannot fail the step: any error is
        recorded as a reason and the step proceeds.
        """
        #: the INSTANCE DICT, not getattr. The runner assigns
        #: `t.ws_book = auth_book` itself, so the instance dict is exactly
        #: where it lives, and reading it directly cannot trip a
        #: transport's guarded `__getattr__` (the execution fake asserts on
        #: unexpected attribute access, by design). An observational field
        #: must not be able to raise through a guard it has no business
        #: touching.
        try:
            bk = vars(self.t).get("ws_book")
        except BaseException:                                  # noqa: BLE001
            bk = None
        if bk is None:
            return {"read": False, "reason": "NO_AUTH_BOOK"}
        try:
            snap = bk.snapshot(self.market)
        except BaseException as exc:                           # noqa: BLE001
            return {"read": False, "reason": type(exc).__name__}
        if not isinstance(snap, Mapping):
            return {"read": False, "reason": "NO_SNAPSHOT"}
        out = {"read": True}
        for k in ("bid", "ask", "bid_shares", "ask_shares", "book_updates",
                  "stream_age_s", "market_state", "source"):
            v = snap.get(k)
            out[k] = None if v is None else str(v)
        return out

    def fees(self) -> Dict[str, Any]:
        """The venue's own commission on each of our orders, read back."""
        total, per, unread = D(0), {}, []
        for coid, r in sorted(self.ex.orders().items()):
            vid = r.get("venue_order_id")
            if not vid or r.get("market") != self.market:
                continue
            try:
                row = self.t.order(vid) or {}
            except BaseException:                              # noqa: BLE001
                unread.append(vid)
                continue
            fee = _dec(row.get("commissionNotionalTotalCollected"))
            if fee is None:
                unread.append(vid)
                continue
            per[vid] = str(fee)
            total += fee
        return {"total_usd": str(total), "per_order": per,
                "unreadable": unread, "complete": not unread}

    def run(self) -> Dict[str, Any]:
        st = self.start()
        if not st.get("ok"):
            self._log("L5_BTC5M_NOT_STARTED", st)
            return {"started": False, **st}
        self.trade()
        closed = self.close()
        # RECONCILE BEFORE SETTLEMENT. The venue redeems a settled position
        # and then reports no row for it, while this ledger still states
        # what was held -- so a reconciliation run AFTER `settle()` compares
        # our inventory against an empty venue and can only "agree" when we
        # held NOTHING. Observed live 2026-11-12: every zero-fill window
        # reported agrees true, and both windows that actually filled
        # (16:20z holding 0.29 UP / 0.37 DOWN, 16:25z holding 0.58 DOWN)
        # reported agrees FALSE with venue_rows empty. The check could not
        # pass in exactly the case it exists for.
        recon = self.reconcile()
        note_close_verification(self.market, recon, self.ex.inventory(
            self.market), self.clock())
        settled = self.settle()
        #: the same read AFTER settlement, kept for the record. An empty
        #: venue here is expected -- the position has been redeemed -- so
        #: this one is never what `agrees` is judged on.
        recon_after = self.reconcile()
        fees = self.fees()
        orders = self.ex.orders()
        mine = {c: r for c, r in orders.items() if r["market"] == self.market}
        pnl = _dec(settled.get("settlement_pnl")) if settled.get(
            "settled") else None
        fee_total = _dec(fees["total_usd"])
        report = {
            "market": self.market,
            "transport_armed": st.get("transport_armed"),
            "submitted": sum(1 for r in mine.values()
                             if r.get("venue_order_id")),
            "submit_uncertain": sum(1 for r in mine.values()
                                    if r["state"] == X.UNCERTAIN),
            "filled_orders": sum(1 for r in mine.values()
                                 if D(r["filled_qty"]) > 0),
            "fills_qty": str(sum((D(r["filled_qty"]) for r in mine.values()),
                                 D(0))),
            "fills_cost_usd": str(sum((D(r["filled_cost"])
                                       for r in mine.values()), D(0))),
            "cancelled": sum(1 for r in mine.values()
                             if r["state"] == X.CANCELLED),
            "would_submit_not_sent": self.would_submit,
            "submission_budget": submission_budget(),
            "close": closed, "settlement": settled, "reconcile": recon,
            "reconcile_after_settlement": recon_after,
            "venue_fees": fees,
            "settled_net_usd": (None if pnl is None or not fees["complete"]
                                else str(pnl - fee_total)),
            #: PER STRATEGY, and EXACT rather than apportioned: every
            #: share was bought by one order belonging to one strategy,
            #: and a winning share redeems $1 whoever bought it. Fees are
            #: keyed by order id, so they attribute exactly too. Pairing
            #: is deliberately absent -- a matched pair can be one
            #: strategy's leg against another's, so `matched_qty` and
            #: `pair_cost` stay market-level facts above.
            "by_strategy": _attribution_block(self.ex, self.market,
                                              settled, fees),
            #: EVERY ORDER, ONE ROW. Observed 2026-11-21: the report
            #: said 20 cancelled while the step lines named seven, and
            #: nothing showed how each order ended or that it went out
            #: post-only. `cancel_requested` separates the cancels this
            #: lane sent from the ones the venue ended by itself.
            "orders": order_table(mine),
            #: OPPORTUNITIES LOST TO AN EXHAUSTED SHARED BUDGET.
            #:
            #: The four lanes are separate processes competing for one
            #: reservation, so the room goes to whichever ASKS FIRST --
            #: not to the best expected value. That is first-come-first-
            #: served, and it is neither equal allocation nor EV
            #: allocation. Before changing it, measure whether it costs
            #: anything: these are the orders a lane wanted, was
            #: authorized to place, and could not fund because a sibling
            #: had the room.
            #:
            #: A high count with high rejected EV is the case for
            #: cross-lane allocation. A count near zero means arrival
            #: order never mattered and the work would be wasted.
            "budget_exhausted": _budget_exhausted(self.events),
            "labels": (
                "venue-confirmed orders and fills only. settled_net_usd is "
                "settlement arithmetic on confirmed fills minus the venue's "
                "own commission; it is REALIZED once the venue credits the "
                "redemption. would_submit_not_sent counts decisions a "
                "disarmed transport did not send: they are not trades."),
        }
        os.makedirs(os.path.dirname(self.marker()), exist_ok=True)
        with open(self.marker(), "w") as fh:
            json.dump(report, fh, default=str, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        self._log("L5_BTC5M_SESSION_REPORT", report)
        return report


def main() -> int:            # pragma: no cover - the service entrypoint
    from src import l5_btc_reference as REF
    from src import l5_series_registry as REG
    from src import l5_pmus_transport as T
    market = (os.getenv(X.MARKET_ENV) or "").strip()
    root = (os.getenv(AUTH.LEDGER_ENV) or "/state/btc5m/auth.jsonl")
    root = os.path.dirname(root)
    ledger = (os.getenv(LEDGER_ENV) or os.path.join(root, "btc5m_ledger.json"))
    # The executor's live-order bound follows the entry rule: 2 as before,
    # and in ACCUMULATE whatever `L5_BTC5M_MAX_RESTING_ENTRIES` allows, so
    # the controller's plan is not silently clipped by a smaller bound.
    #: `ev_waiver_fn` is the behaviour probe's ONLY entry into the executor.
    #: It is a read: with no `L5_BTC5M_BEHAVIOUR_PROBE` document set it
    #: returns "not present" and nothing about a step changes.
    ex = X.Btc5mExecutor(None, ledger,
                         max_live_orders=X.live_order_bound(),
                         ev_waiver_fn=PROBE.evaluate)
    port = (os.getenv(FILLS_PORT_ENV) or "").strip()
    if port:
        serve_fills(ex, int(port))
    #: THE FEED FOR THIS LANE'S ASSET. One Reference polls one index
    #: series; the asset and its index symbol come from the one registry
    #: table, together, because an index that did not belong to the asset
    #: would price the wrong thing while looking healthy. BTC resolves to
    #: btc/BRTI, so the existing lane is unchanged; ETH/SOL/XRP were
    #: reporting p_up null with UNEXPECTED_INDEX purely because this
    #: construction could only ever ask for Bitcoin's.
    _row = REG.BY_STRATEGY[X.STRATEGY]
    ref = REF.Reference(window_s=WINDOW_S, asset=_row.asset,
                        index_symbol=_row.index_symbol)
    print("L5_BTC5M_REFERENCE %s" % {
        "strategy": X.STRATEGY, "asset": _row.asset,
        "index_symbol": _row.index_symbol, "window_s": WINDOW_S}, flush=True)

    # SETTLEMENT RECOVERY, before the first window. A window whose process
    # died in the middle never recorded its settlement, and that market's
    # filled cost keeps counting against the account cap until it does --
    # which on 2026-11-12 cost the ladder its two outermost rungs on every
    # window of the evening. GET only; records the venue's own settlementPx.
    try:
        rec = recover_settlements(
            ex, lambda slug: T.PmusTransport(market_slug=slug, series=X.STRATEGY))
        emit("L5_BTC5M_SETTLEMENT_RECOVERY", rec)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        emit("L5_BTC5M_SETTLEMENT_RECOVERY", {"error": type(exc).__name__})

    # THE LAST WINDOW THAT SENT ORDERS, one row per order, from the durable
    # ledger. Read-only and local: no venue call. It is what lets an
    # earlier window's cancels be told apart (ours vs the venue's) after
    # the process that traded it has gone.
    try:
        emit("L5_BTC5M_LAST_TRADED_WINDOW_ORDERS", last_traded_orders(ex))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        emit("L5_BTC5M_LAST_TRADED_WINDOW_ORDERS",
             {"error": type(exc).__name__})

    # THE AUTHENTICATED BOOK FEED, subscribed to the 5-minute series. It is
    # what lets `book_readable` survive the venue's early-window 404s, via
    # the transport's existing `_ws_book_substitute`. Daemon thread,
    # read-only, no order path.
    auth_book = None
    try:
        from src import l5_pmus_auth_book as AB
        auth_book = AB.AuthBook(slugs_fn=wanted_5m_slugs).start()
        emit("L5_BTC5M_AUTH_BOOK", {"started": True,
                                    "series": X.MARKET_PREFIX})
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        emit("L5_BTC5M_AUTH_BOOK", {"started": False,
                                    "why": type(exc).__name__})

    def one_window(slug: str, max_windows: int,
                   settle_wait_s: float = SETTLE_WAIT_S
                   ) -> Dict[str, Any]:
        # THE EXECUTOR'S OWN LOCK NAMES THIS WINDOW. `arming_refusal`
        # refuses every submission unless `L5_BTC5M_MARKET` is set AND
        # equals the market being traded -- an ADDITIONAL lock in front of
        # the authorization claim. A campaign deliberately leaves that
        # variable empty so the runner can take its windows from the
        # owner's approval, which made the two incompatible: observed live
        # 2026-11-12 15:42Z, the controller chose OPEN_FAVORITE at 0.76,
        # 0.80, 0.82, 0.85 and every one was refused MARKET_NOT_CONFIGURED.
        #
        # Naming the current window here keeps the lock's meaning exactly:
        # at any instant it names ONE market, the one this session is
        # running, and it is narrowed per window rather than widened. What
        # AUTHORIZES the window is unchanged -- the owner's approval
        # enumerating it, and the per-order claim.
        prev = os.environ.get(X.MARKET_ENV)
        os.environ[X.MARKET_ENV] = slug
        try:
            return _run_one(slug, max_windows, settle_wait_s)
        finally:
            if prev is None:
                os.environ.pop(X.MARKET_ENV, None)
            else:
                os.environ[X.MARKET_ENV] = prev

    def _run_one(slug: str, max_windows: int,
                 settle_wait_s: float = SETTLE_WAIT_S) -> Dict[str, Any]:
        t = T.PmusTransport(market_slug=slug, series=X.STRATEGY)
        # the socket stands in for a 404ing REST book, exactly as it does
        # for BTC15m. None leaves the old behaviour: the 404 simply fails.
        if auth_book is not None:
            t.ws_book = auth_book
        ex.transport = t
        sess = Session(
            market=slug, executor=ex, transport=t, root=root,
            book_fn=public_book,
            p_fn=lambda m, ws, now: ref.estimate(m, ws, now),
            risk_fn=lambda now, own: account_risk(
                url=(os.getenv(RISK_URL_ENV) or "").strip(), now=now,
                own_unaccounted=own),
            halt_fn=lambda: shared_halt(
                (os.getenv(HALT_URL_ENV) or "").strip()),
            max_windows=max_windows,
            settle_wait_s=settle_wait_s)
        return sess.run()

    # WHICH WINDOWS. Either the ONE the owner named, which is the original
    # behaviour and the default, or -- only when the owner opts in -- every
    # window the owner's own approval document ENUMERATES. This runner can
    # neither add to that list nor invent a window.
    plan = {"markets": [market] if market else [], "max_windows": 1,
            "source": "L5_BTC5M_MARKET"}
    if not market and (os.getenv(CAMPAIGN_ENV) or "").strip() == "ON":
        got = approved_markets(dt.datetime.now(UTC))
        plan = {"markets": got["markets"],
                "max_windows": got["max_windows"] or 1,
                "source": "OWNER_APPROVAL_WINDOW_STARTS",
                "why": got["why"]}
    # WILL THE BUDGET REACH THE LAST APPROVED WINDOW? Stated here,
    # before the first one, rather than discovered at window 7.
    reach = budget_reach(len(plan["markets"]) or 1,
                         X.live_order_bound())
    emit("L5_BTC5M_PLAN", dict(plan, budget_reach=reach,
                               utc=dt.datetime.now(UTC).isoformat()))

    #: THE EFFECTIVE CONFIGURATION, EMITTED BEFORE THE ARMING GATE.
    #:
    #: WHY IT HAD TO EXIST. On 2026-11-17 this lane exited at boot with
    #: ENVELOPE_EXPIRED -- code 0, a designed dead-man -- which Railway
    #: reports as CRASHED. It has been down since. Asking "is bounded
    #: repetition actually enabled in production?" was then unanswerable
    #: from outside: the variables are present but their VALUES are
    #: redacted to anything but a session token, and the lane exits
    #: before it writes a single step report, so no effective-parameter
    #: record ever reached the log.
    #:
    #: That is the whole point of placing it HERE, ahead of the arming
    #: ceremony: the case where the configuration most needs reading is
    #: exactly the case where the process is about to stop.
    #:
    #: IT PRINTS WHAT THE EXECUTOR WOULD RESOLVE, not what the dataclass
    #: defaults to -- `effective_params` after the env readers, so a
    #: default and a deployed value can be told apart. Probabilities are
    #: reported as SET or NOT SET rather than by value: whether the gate
    #: can run is the operational fact, and the numbers themselves are
    #: the owner's.
    #:
    #: REPORTING ONLY. It reads no venue, writes nothing, and runs before
    #: anything is armed.
    try:
        _ep = ex.effective_params()
        emit("L5_BTC5M_EFFECTIVE_CONFIG", {
            "entry_mode": X.entry_mode(),
            "straddle_repeat_enabled": X.straddle_repeat_enabled(),
            "straddle_max_side_usd": str(X.straddle_max_side_usd()),
            "straddle_max_unmatched_usd":
                str(X.straddle_max_unmatched_usd()),
            #: the book-relative / requote / loss-ceiling settings, read
            #: off the SAME effective params the controller is handed, so
            #: "is the requote actually on in production?" is answerable
            #: from the log and a clamped value shows as what it became.
            "clip_usd": str(_ep.clip_usd),
            "straddle_book_relative": bool(_ep.straddle_book_relative),
            "straddle_pair_ceiling": str(_ep.straddle_pair_ceiling),
            "straddle_join_ticks": int(_ep.straddle_join_ticks or 0),
            "straddle_reprice_ticks_per_clip":
                int(_ep.straddle_reprice_ticks_per_clip or 0),
            "straddle_requote_ticks": int(_ep.straddle_requote_ticks or 0),
            "straddle_one_sided_control":
                bool(_ep.straddle_one_sided_control),
            "straddle_max_completed_pair_cost":
                str(_ep.straddle_max_completed_pair_cost),
            "straddle_ev_basis": str(_ep.straddle_ev_basis or ""),
            "straddle_fill_edge_per_share": (
                None if _ep.straddle_fill_edge_per_share is None
                else str(_ep.straddle_fill_edge_per_share)),
            "straddle_pace_side_cap": bool(_ep.straddle_pace_side_cap),
            #: whether the owner's campaign loss halt is SET and readable,
            #: never its contents; the verdict is in every step report
            "campaign_loss_halt": (
                "NOT_SET" if X.campaign_loss_halt_doc() is None else
                "UNREADABLE" if not isinstance(X.campaign_loss_halt_doc(),
                                               dict) else "SET"),
            "straddle_stop_adding_heavy_underdog":
                bool(_ep.straddle_stop_adding_heavy_underdog),
            "straddle_heavy_underdog_margin":
                str(_ep.straddle_heavy_underdog_margin),
            "straddle_entry_bid_band": "%s:%s" % (
                _ep.straddle_entry_bid_min, _ep.straddle_entry_bid_max),
            "straddle_leg_max_age_s": float(_ep.straddle_leg_max_age_s or 0),
            #: whether a behaviour-probe document is SET, never what it
            #: says. Whether it is valid is judged per step, against the
            #: market being stepped, and reported in that step's report.
            "behaviour_probe_document_set": bool(
                (os.environ.get(PROBE.DOC_ENV) or "").strip()),
            "behaviour_probe_campaign_document_set":
                PROBE.campaign_configured(),
            "straddle_max_window_loss_usd":
                str(_ep.straddle_max_window_loss_usd),
            "straddle_completion_pair_cost":
                str(_ep.straddle_completion_pair_cost),
            "straddle_fill_model_measured":
                bool(_ep.straddle_fill_model_measured),
            "max_resting_entries": getattr(_ep, "max_resting_entries", None),
            "live_order_bound": X.live_order_bound(),
            "account_cap_usd": (None if X.account_cap() is None
                                else str(X.account_cap())),
            "account_lanes": X.account_lanes(),
            "max_exposure_usd": str(_ep.max_exposure_usd),
            "no_new_entry_after_s": _ep.no_new_entry_after_s,
            "min_complement_shares": str(_ep.min_complement_shares),
            #: set / not set, never the value
            "p_pair_natural_set": _ep.straddle_p_pair_natural is not None,
            "p_complete_set": _ep.straddle_p_complete is not None,
            "p_lone_win_set": _ep.straddle_p_lone_win is not None,
            "inventory_ranking": X.inventory_ranking_enabled(),
            "taker_completion": X.taker_completion_allowed(),
            "labels": ("what the executor WOULD resolve this window, "
                       "emitted before the arming gate so it is on the "
                       "record even when the lane exits at boot. "
                       "Reporting only."),
        })
    except BaseException as _exc:                              # noqa: BLE001
        if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
            raise
        #: an observable must never stop a lane from starting -- or from
        #: refusing to
        emit("L5_BTC5M_EFFECTIVE_CONFIG",
             {"error": type(_exc).__name__})
    if reach.get("ok") is False:
        emit("L5_BTC5M_BUDGET_SHORT", reach)

    #: ============ THE OWNER'S ARMING CEREMONY, ONCE AT STARTUP ========
    #:
    #: `may_submit` has always required the durable state to be ARMED and
    #: nothing ever wrote it, so configuring an envelope made this lane
    #: unable to submit at all -- permanently, because MONITOR is the
    #: birth state and no path left it.
    #:
    #: THE AUTHORIZATION IS NOT NEW AND NOT HERE. It is the same two
    #: documents the existing ceremony already sets: the standing
    #: envelope and the campaign approval. `PMODE.arm` re-derives every
    #: limit from them rather than restating any, so this adds no second
    #: arming mechanism and no figure that could drift from the document
    #: that authorized it.
    #:
    #: It runs where the state file is -- inside this process, on the
    #: volume -- because an operator ceremony running elsewhere cannot
    #: write it. Idempotent for the identical pair, so a restart finds
    #: itself ARMED and proceeds; a latch refuses here as everywhere.
    #:
    #: With no envelope configured this is skipped entirely and the
    #: bounded campaign below is byte-for-byte what it always was.
    #: THE OWNER'S LATCH RESET, IF ONE IS SET -- before the ceremony, never
    #: instead of it. It can only move a latch to MONITOR, and only while
    #: this lane cannot submit, so a reset and an arming never share a boot.
    try:
        _reset = clear_latch_if_owner_asked(ex, dt.datetime.now(UTC))
        if _reset is not None:
            emit("L5_BTC5M_LATCH_RESET", _reset)
    except BaseException as _exc:                              # noqa: BLE001
        if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
            raise
        emit("L5_BTC5M_LATCH_RESET", {"ok": False, "cleared": False,
                                      "reason": "RESET_FAILED:%s"
                                      % type(_exc).__name__})
    #: WHAT IS STORED, SAID AT EVERY BOOT WHATEVER THE ENVELOPE SAYS. A
    #: latch used to be read only once a valid envelope was presented, so
    #: a lane could rest "disarmed" for days carrying a STOPPED that would
    #: refuse the next campaign -- and did, on 2026-11-20.
    try:
        emit("L5_BTC5M_STORED_STATE", PMODE.stored_state_report())
    except BaseException as _exc:                              # noqa: BLE001
        if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
            raise
        emit("L5_BTC5M_STORED_STATE", {"readable": False,
                                       "reason": type(_exc).__name__})
    _armed: Any = None
    if (os.getenv(PMODE.ENVELOPE_ENV) or "").strip() and plan["markets"]:
        _first = window_start(plan["markets"][0])
        _armed = PMODE.enter_armed(
            #: THE SAME VARIABLE THE AUTHORIZATION GATE READS. The fleet
            #: maps each lane's `L5_CAMPAIGN_APPROVAL_<STRATEGY>` into its
            #: child as this one, so resolving it any other way here could
            #: arm against a document the gate does not use.
            raw_campaign=os.getenv(AUTH.CAMPAIGN_APPROVAL_ENV),
            first_window_utc=_first,
            not_after_utc=(os.getenv(X.NOT_AFTER_ENV) or "").strip(),
            now=dt.datetime.now(UTC))
        emit("L5_BTC5M_ARM", {k: v for k, v in _armed.items()
                              if k not in ("scope", "written")})
        if not _armed.get("ok"):
            #: an unarmed lane is not an error to march past: it observes
            #: and submits nothing, exactly as MONITOR is meant to.
            emit("L5_BTC5M_NOT_ARMED", {"reason": _armed.get("reason")})
    #: A STORED ARMED THAT THIS BOOT DID NOT EARN IS REMOVED, whether the
    #: ceremony refused or never ran. See `demote_stale_armed`.
    try:
        _stale = demote_stale_armed(_armed, dt.datetime.now(UTC))
    except BaseException as _exc:                              # noqa: BLE001
        if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
            raise
        _stale = {"demoted": False, "error": type(_exc).__name__}
    if _stale is not None:
        emit("L5_BTC5M_STALE_ARMED", _stale)
        if _stale.get("demoted") is False:
            #: it could not be removed, so this process must not go on to
            #: a loop that would honour it
            return 0

    #: ============== PERSISTENT MODE TAKES OVER THE LOOP ===============
    #:
    #: Only when an envelope is configured. With none, everything below
    #: is byte-for-byte the bounded campaign it has always been.
    #:
    #: It rolls on the CLOCK and asks `persistent_gate` before every
    #: window, so a STOP or HALT written mid-window stops the next one.
    #: A latch EXITS -- it does not idle and retry -- because a loop that
    #: survives a safety halt resumes the moment the latch is cleared for
    #: some other reason. MONITOR waits, submitting nothing, so an
    #: operator can arm without a restart.
    #:
    #: THE PRIOR WINDOW IS RECONCILED BEFORE THE NEXT ONE GETS ANY
    #: BUDGET: `sync_reservation` releases only holds the ledger shows
    #: finished, so an uncertain order or unsettled inventory still
    #: occupies the shared account cap when the next window asks for
    #: room. Rolling forward never frees money that is still at risk.
    if str(os.environ.get(PMODE.ENVELOPE_ENV) or "").strip():
        row = REG.BY_STRATEGY[X.STRATEGY]
        emit("L5_BTC5M_PERSISTENT_START",
             {"strategy": X.STRATEGY, "series": row.prefix,
              "utc": dt.datetime.now(UTC).isoformat()})
        ref.start()
        while True:
            now_ = dt.datetime.now(UTC)
            gate = persistent_gate(now=now_)
            if not gate["ok"]:
                emit("L5_BTC5M_PERSISTENT_GATE",
                     dict(gate, utc=now_.isoformat()))
                if gate.get("exit") and not stay_resident(gate):
                    #: NOTHING REARMS. The loop ends; it does not wait
                    #: for somebody to clear a latch.
                    return 0
                if gate.get("exit"):
                    #: THE EXPLICIT TRANSITION, through the SAME single
                    #: writer the campaign-expiry path uses:
                    #: write_state(MONITOR). Two things follow from it,
                    #: and the second is the one that matters.
                    #:
                    #: 1. the lane's state SAYS disarmed, durably, so the
                    #:    status endpoint and the next startup both read
                    #:    it rather than inferring it.
                    #: 2. NOTHING SILENTLY RESUMES. `persistent_gate`
                    #:    requires state == ARMED. Once this is MONITOR,
                    #:    a refreshed envelope is no longer sufficient:
                    #:    the gate returns STATE_NOT_ARMED and keeps
                    #:    waiting. Without this write the lane WOULD have
                    #:    resumed the moment a new envelope appeared,
                    #:    because the envelope was the only thing
                    #:    refusing. Re-arming needs the startup ceremony.
                    #:
                    #: NEVER OVER A LATCH. HALTED and STOPPED exit above
                    #: (`stay_resident` is False for them), and this
                    #: re-checks rather than trusting that: a latch must
                    #: not be downgraded to MONITOR by this path.
                    try:
                        _st_now = PMODE.read_state()
                        _cur = _st_now.get("state")
                        if _st_now.get("ok") and _cur not in PMODE.LATCHED \
                                and _cur != PMODE.MONITOR:
                            PMODE.write_state(
                                PMODE.MONITOR,
                                why="ENVELOPE:%s" % gate.get("reason"),
                                now=now_)
                            emit("L5_BTC5M_DISARMED_STATE",
                                 {"from": _cur, "to": PMODE.MONITOR,
                                  "why": "ENVELOPE:%s" % gate.get("reason"),
                                  "note": ("re-arming now requires the "
                                           "startup ceremony; a refreshed "
                                           "envelope alone will not "
                                           "resume trading")})
                    except BaseException as _se:               # noqa: BLE001
                        if isinstance(_se, (KeyboardInterrupt, SystemExit)):
                            raise
                        #: a state write we cannot make is not a licence to
                        #: carry on resident with state ARMED -- that is the
                        #: one combination that could resume. Exit instead.
                        emit("L5_BTC5M_DISARM_WRITE_FAILED",
                             {"error": type(_se).__name__,
                              "action": "exiting rather than staying "
                                        "resident while still ARMED"})
                        return 0
                    #: RESIDENT AND DISARMED. The registry keeps serving
                    #: so the watchdog can still claim and cancel this
                    #: lane's ids; the transport is None so nothing can
                    #: be sent; and this same gate refuses again in 15 s.
                    emit("L5_BTC5M_RESIDENT_DISARMED",
                         {"reason": gate.get("reason"),
                          **resident_state_fields(),
                          "serving_registry": bool(
                              (os.getenv(FILLS_PORT_ENV) or "").strip()),
                          "can_submit": False,
                          "why": ("staying up so the shared watchdog can "
                                  "read this lane's registry; an expired "
                                  "envelope is not permission to trade"),
                          "utc": now_.isoformat()})
                time.sleep(15.0)
                continue
            #: THE WATCHDOG'S HAND, TURNED INTO A DURABLE LATCH.
            #:
            #: The watchdog is a separate service and a Railway volume
            #: attaches to ONE service, so it cannot write this lane's
            #: state file -- its own docstring says so. What it CAN do is
            #: assert the halt record over HTTP and cancel the exact ids
            #: it observed resting, and it can never clear either.
            #:
            #: So its assertion is read here and converted into the
            #: persistent latch plus a full unwind. That makes the
            #: watchdog able to enforce the same safe halt an operator
            #: can, through the only channel the architecture allows,
            #: and the latch then outlives the HTTP record: a halt that
            #: later becomes readable-and-clear does NOT resume trading.
            wd = shared_halt((os.getenv(HALT_URL_ENV) or "").strip())
            if not wd.get("ok"):
                PMODE.write_state(
                    PMODE.HALTED,
                    why="WATCHDOG:%s" % wd.get("reason"),
                    now=now_)
                unwound = ex.stop(reason="WATCHDOG:%s" % wd.get("reason"),
                                  now=now_)
                emit("L5_BTC5M_PERSISTENT_HALTED",
                     {"reason": wd.get("reason"), "source": "WATCHDOG",
                      "stop": unwound, "utc": now_.isoformat()})
                return 0
            #: ============ A FINISHED CAMPAIGN IS NOT AN ARMED LANE ======
            #:
            #: Observed 2026-11-16: after NOT_AFTER 19:30Z the campaign
            #: was over -- authorization refused every window and
            #: `transport_armed` stayed false -- but the durable state
            #: still read ARMED and this loop kept rolling windows for
            #: fifty minutes, because the standing envelope was valid
            #: until 20:30Z. No order could be placed, so nothing was at
            #: risk; what was wrong is that the lane REPORTED ARMED
            #: while being structurally unable to trade, and an operator
            #: reading that state would have drawn the wrong conclusion
            #: about what the system was doing.
            #:
            #: So a passed NOT_AFTER returns the lane to MONITOR -- the
            #: existing disarmed-but-watching state -- and ends the
            #: loop. MONITOR rather than a new state because that is
            #: exactly what MONITOR means, and because ARMED must keep
            #: its single writer. The latch states are untouched: this
            #: never runs over a HALT or a STOP, which exit earlier.
            _na = (os.getenv(X.NOT_AFTER_ENV) or "").strip()
            if _na:
                _end = _utc_or_none(_na)
                if _end is None:
                    PMODE.write_state(PMODE.HALTED, why="NOT_AFTER_UNREADABLE",
                                      now=now_)
                    emit("L5_BTC5M_PERSISTENT_HALTED",
                         {"reason": "NOT_AFTER_UNREADABLE", "raw": _na[:40]})
                    return 0
                if now_ >= _end:
                    PMODE.write_state(PMODE.MONITOR,
                                      why="CAMPAIGN_EXPIRED:%s" % _na,
                                      now=now_)
                    emit("L5_BTC5M_CAMPAIGN_ENDED",
                         {"not_after": _na, "state": PMODE.MONITOR,
                          "armed": False,
                          "note": ("the campaign window has passed; the lane "
                                   "is DISARMED and stops rolling windows"),
                          "resident": resident_when_disarmed()})
                    if not resident_when_disarmed():
                        return 0
                    #: STAY READABLE. See `resident_after_campaign`: no
                    #: window is ever called from there.
                    return resident_after_campaign(ex, emit)
            nxt = persistent_next(now=now_, prefix=row.prefix,
                                  horizon_s=row.horizon_s)
            if not nxt["ok"]:
                emit("L5_BTC5M_PERSISTENT_GATE",
                     {"reason": nxt["reason"], "utc": now_.isoformat()})
                time.sleep(5.0)
                continue
            if nxt["wait_s"] > 0:
                time.sleep(min(nxt["wait_s"], 30.0))
                continue
            #: settle_wait_s 0: close the window and move on. The market
            #: stays PENDING and is settled out of band below, which is
            #: what lets the loop reach the next five-minute window at
            #: all. Its cost stays reserved meanwhile.
            rep = one_window(nxt["market"], 1, settle_wait_s=0.0)
            #: SETTLE WHAT THE VENUE IS READY TO SETTLE, for every window
            #: still pending -- including ones from earlier iterations
            #: and from before a restart. One attempt each, never a wait.
            #: `ex.transport` -- `_run_one` builds a transport per window
            #: and leaves it on the executor. `settlement(slug)` takes the
            #: market as an ARGUMENT, so one instance can read a previous
            #: window too, which is what reconciling a backlog needs.
            recon = ({"pending": [], "settled": [], "unreadable": [],
                      "deferred": [], "error": "NO_TRANSPORT"}
                     if getattr(ex, "transport", None) is None
                     else reconcile_pending(ex, ex.transport))
            #: RECONCILE BEFORE THE NEXT WINDOW MAY SPEND ANYTHING. Only
            #: markets the venue actually settled give their room back.
            freed = ex.sync_reservation()
            emit("L5_BTC5M_PERSISTENT_WINDOW",
                 {"market": nxt["market"], "started": rep.get("started"),
                  "reason": rep.get("reason"),
                  "settlement": recon,
                  "pending_count": len(recon.get("pending") or []),
                  "reservation": freed,
                  "reserved_usd": (RESV.aggregate() or {}).get("usd"),
                  "utc": dt.datetime.now(UTC).isoformat()})
            #: A LEDGER HALT IS A SAFETY HALT: latch it so every lane and
            #: every restart sees it, then stop. It is never walked past.
            if ex.halted_reason:
                PMODE.write_state(
                    PMODE.HALTED,
                    why="LEDGER_HALTED:%s" % ex.halted_reason,
                    now=dt.datetime.now(UTC))
                unwound = ex.stop(
                    reason="LEDGER_HALTED:%s" % ex.halted_reason,
                    now=dt.datetime.now(UTC))
                emit("L5_BTC5M_PERSISTENT_HALTED",
                     {"reason": ex.halted_reason, "source": "LEDGER",
                      "stop": unwound,
                      "utc": dt.datetime.now(UTC).isoformat()})
                return 0
            if not (rep.get("close") or {}).get("clean", True):
                PMODE.write_state(PMODE.HALTED, why="CLOSE_NOT_CLEAN",
                                  now=dt.datetime.now(UTC))
                unwound = ex.stop(reason="CLOSE_NOT_CLEAN",
                                  now=dt.datetime.now(UTC))
                emit("L5_BTC5M_PERSISTENT_HALTED",
                     {"reason": "CLOSE_NOT_CLEAN", "source": "CLOSE",
                      "stop": unwound,
                      "utc": dt.datetime.now(UTC).isoformat()})
                return 0

    if not plan["markets"]:
        emit("L5_BTC5M_IDLE", {"reason": "NO_MARKET_CONFIGURED",
                               "why": plan.get("why")})
    else:
        ref.start()
        # NEVER MORE THAN THE OWNER APPROVED, whatever the list says.
        for slug in plan["markets"][:max(1, int(plan["max_windows"]))]:
            rep = one_window(slug, plan["max_windows"])
            # ALREADY RUN OR ALREADY OVER IS A SKIP, NOT A STOP. A restart
            # inside a campaign re-plans the whole approved list and so meets
            # the windows it already finished; treating that as a refusal
            # ended the campaign on its FIRST entry and never reached the
            # windows still ahead of it (observed live, 2026-11-12 15:20Z:
            # the 15:10z marker stopped a campaign that still had 15:20z and
            # 15:25z to run). The durable marker still guarantees no window
            # is traded twice -- that is exactly what lets this be a skip.
            skip = {"MARKET_ALREADY_RUN", "WINDOW_ALREADY_PAST"}
            # A halt, a refusing account-risk state or an unresolved order
            # ENDS THE CAMPAIGN. It never marches on to the next window.
            stop = None
            if not rep.get("started"):
                reason = rep.get("reason")
                if reason in skip:
                    emit("L5_BTC5M_WINDOW_SKIPPED",
                         {"market": slug, "reason": reason,
                          "utc": dt.datetime.now(UTC).isoformat()})
                    continue
                stop = reason
            elif not (rep.get("close") or {}).get("clean", True):
                stop = "CLOSE_NOT_CLEAN"
            elif ex.halted_reason:
                stop = "LEDGER_HALTED:%s" % ex.halted_reason
            if stop:
                emit("L5_BTC5M_CAMPAIGN_STOPPED",
                     {"after": slug, "reason": stop,
                      "utc": dt.datetime.now(UTC).isoformat()})
                break
    # NEVER CHOOSES A WINDOW OF ITS OWN. It idles until the owner changes
    # the configuration; a restart finds each marker and does not re-trade.
    while True:
        emit("L5_BTC5M_IDLE", {"market": market or None,
                               "planned": len(plan["markets"]),
                               "utc": dt.datetime.now(UTC).isoformat()})
        time.sleep(60.0)


if __name__ == "__main__":    # pragma: no cover
    raise SystemExit(main())
