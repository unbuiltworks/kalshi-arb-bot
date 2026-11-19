"""Every protection a live lane needs, checked in one place, fail-closed.

WHY THIS EXISTS, AND WHY IT IS NOT ANOTHER PREFLIGHT.

The gates are all there, and they are all individually correct. The
problem is that they are *independently optional*. Three examples
found by reading production rather than trusting it:

  - `L5_PERSISTENT_ENVELOPE` is read as `if env_raw is not None and
    str(env_raw).strip():`. Unset, the envelope gate is SKIPPED --
    so per-market, worst-case-loss and maker-only stop binding and
    only the $10 reservation remains. Nothing says so at boot.
  - `L5_BTC5M_FOREIGN_EXPOSURE=REPORTED` makes the ceiling bound OUR
    exposure only. That is the correct setting when another lane's
    inventory is not ours to lose, and the WRONG one when the claim
    being made is "account-wide worst case".
  - The watchdog's managed-lane list is a variable on a different
    service. A lane can be live and simply not be in it.

Each of those is a protection that is absent rather than failing, and
an absent protection is invisible. This module makes absence loud: it
takes the environment and the readings a caller has already done, and
returns `ok` only when EVERY protection is present and binding. Every
failure is named, not just the first, so one run tells the operator
the whole list.

IT READS NOTHING ITSELF. No network, no clock, no credential -- the
caller passes what it read. That keeps this module testable against
exact states, and keeps the decision separate from the plumbing that
could fail while making it.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

D = Decimal
ZERO = D(0)
ONE = D(1)

#: the compiled ceilings a live envelope may clamp DOWN to, never past
CEIL_AGGREGATE_USD = D("10.00")
CEIL_PER_MARKET_USD = D("5.00")
CEIL_DAILY_LOSS_USD = D("5.00")

#: the account-wide risk document must be at most this old, matching
#: `l5_btc5m_runner.RISK_MAX_AGE_S`
RISK_MAX_AGE_S = D("30")

#: `L5_BTC5M_FOREIGN_EXPOSURE` must be this for an account-wide claim
FOREIGN_CHARGED = "CHARGED"


def _dec(v: Any) -> Optional[Decimal]:
    if isinstance(v, Decimal):
        return v
    if v is None or isinstance(v, bool):
        return None
    try:
        return D(str(v).strip())
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return None


def strategy_can_fire(*, late_window_s: Any, window_s: Any,
                      cancel_lead_s: Any,
                      step_s: Any) -> Dict[str, Any]:
    """Can a late-window strategy ever be evaluated at all?

    THE DEFECT THIS CATCHES, measured on the fifth four-lane run: zero
    steps at `t >= 270` across 20 windows.

    The session's step loop runs `while clock() < window_start +
    window_s - cancel_lead_s`, so the last evaluation happens strictly
    BEFORE `window_s - cancel_lead_s`. A late strategy fires when
    `window_s - t <= late_window_s`, i.e. from `window_s -
    late_window_s` onward. With `late_window_s=30`, `window_s=300` and
    `cancel_lead_s=30` those are `t >= 270` and `t < 270` -- disjoint.
    The strategy refuses TOO_EARLY on every step of every window, for
    ever, and reports nothing unusual while doing it.

    A strategy that cannot be evaluated is not a safe strategy; it is
    an absent one, and arming a campaign around it would produce a
    clean run with no orders and no explanation.

    `rest_s` is the more useful number for an operator: how long an
    order could actually rest before the cancel sweep takes it. A
    positive overlap with `rest_s` of zero still cannot hold an order.
    """
    lw, w = _dec(late_window_s), _dec(window_s)
    lead, step = _dec(cancel_lead_s), _dec(step_s)
    out: Dict[str, Any] = {"ok": False, "reason": None}
    if lw is None or w is None or lead is None or step is None:
        return dict(out, reason="WINDOW_TIMINGS_UNREADABLE")
    if w <= ZERO or lw < ZERO or lead < ZERO or step <= ZERO:
        return dict(out, reason="WINDOW_TIMINGS_NONSENSE")
    stop_at = w - lead
    fires_from = w - lw
    out.update({"stepping_stops_before_s": str(stop_at),
                "strategy_fires_from_s": str(fires_from),
                "rest_s": str(max(ZERO, stop_at - fires_from))})
    if fires_from >= stop_at:
        return dict(out, reason="STRATEGY_WINDOW_NEVER_STEPPED")
    #: one step is not an opportunity: the loop sleeps `step_s`, so a
    #: window narrower than a step can be skipped entirely by timing.
    if stop_at - fires_from < step:
        return dict(out, reason="STRATEGY_WINDOW_NARROWER_THAN_ONE_STEP")
    return dict(out, ok=True)


def envelope_binds(scope: Any) -> Dict[str, Any]:
    """Does this envelope actually bind all four limits?

    A parsed envelope is not a binding one. Each limit must be PRESENT
    and within its compiled ceiling; a missing figure is treated as
    missing, never as the ceiling, because defaulting to the ceiling
    is the permissive answer and this module exists to refuse those.
    """
    out: Dict[str, Any] = {"ok": False, "missing": [], "exceeds": []}
    if not isinstance(scope, Mapping):
        out["missing"].append("ENVELOPE_SCOPE_UNREADABLE")
        return out
    for key, ceil in (("max_aggregate_usd", CEIL_AGGREGATE_USD),
                      ("max_per_market_usd", CEIL_PER_MARKET_USD),
                      ("max_daily_loss_usd", CEIL_DAILY_LOSS_USD)):
        v = _dec(scope.get(key))
        if v is None:
            out["missing"].append(key)
            continue
        if v <= ZERO:
            out["missing"].append(key)
            continue
        if v > ceil:
            out["exceeds"].append("%s=%s>%s" % (key, v, ceil))
    #: MAKER-ONLY IS A LIMIT, not a preference. Taker left enabled
    #: turns a 97c resting bid into a 97c purchase at the ask.
    mo = scope.get("maker_only")
    if mo is not True:
        out["missing"].append("maker_only")
    out["ok"] = not out["missing"] and not out["exceeds"]
    return out


def check(*, env: Mapping[str, str],
          state: Any,
          envelope_scope: Any,
          reservation_readable: Any,
          halt: Any,
          account_risk: Any,
          watchdog_managed_urls: Any,
          this_lane_url: Any,
          oracle_sees_lane: Any,
          strategy_inputs: Any,
          strategy_timing: Any) -> Dict[str, Any]:
    """`ok` only when every protection is present and binding.

    Returns EVERY failure, not the first. An operator fixing these one
    boot at a time is an operator arming late and guessing; the point
    of a single gate is a single list.
    """
    fail: List[str] = []
    detail: Dict[str, Any] = {}

    def need(cond: Any, name: str, **kw: Any) -> None:
        if not cond:
            fail.append(name)
            if kw:
                detail[name] = kw

    # ---- 1. the standing envelope must be PRESENT, or three of the
    #         four limits silently stop binding.
    raw = env.get("L5_PERSISTENT_ENVELOPE")
    present = raw is not None and str(raw).strip() != ""
    need(present, "ENVELOPE_ABSENT",
         why=("unset leaves the envelope gate skipped: per-market, "
              "worst-case-loss and maker-only do not bind and only the "
              "shared $10 reservation remains"))
    if present:
        eb = envelope_binds(envelope_scope)
        detail["envelope"] = eb
        need(eb["ok"], "ENVELOPE_DOES_NOT_BIND",
             missing=eb["missing"], exceeds=eb["exceeds"])

    # ---- 2. durable state must say ARMED. MONITOR waits, HALTED and
    #         STOPPED are latches and are never walked past.
    need(str(state) == "ARMED", "STATE_NOT_ARMED", state=str(state))

    # ---- 3. the one place the lanes meet.
    need(str(env.get("L5_SHARED_RESERVATION") or "").strip() != "",
         "SHARED_RESERVATION_PATH_UNSET")
    need(reservation_readable is True, "SHARED_RESERVATION_UNREADABLE")

    lanes = _dec(env.get("L5_ACCOUNT_LANES"))
    need(lanes is not None and lanes >= 1, "ACCOUNT_LANES_UNREADABLE",
         raw=str(env.get("L5_ACCOUNT_LANES")))

    # ---- 4. ACCOUNT-WIDE means account-wide.
    #
    #: `REPORTED` makes the ceiling bound OUR exposure and leaves
    #: another service's inventory uncharged. That is the right setting
    #: when a sibling's risk is not ours to lose, and the wrong one
    #: whenever the figure being promised is an account-wide worst
    #: case. `account_exposure()` already reads the venue's own
    #: open_orders() and positions() for the whole account and charges
    #: foreign at $1/contract, so CHARGED is a configuration away --
    #: it just has to actually be set.
    mode = str(env.get("L5_BTC5M_FOREIGN_EXPOSURE") or "").strip().upper()
    need(mode == FOREIGN_CHARGED, "FOREIGN_EXPOSURE_NOT_CHARGED",
         mode=mode or "(unset -> CHARGED by default)",
         why=("an account-wide worst case cannot exclude another live "
              "service's resting orders and positions on the same account"))

    # ---- 5. the shared halt must be readable AND clear.
    need(isinstance(halt, Mapping) and halt.get("ok") is True,
         "HALT_UNREADABLE_OR_ASSERTED",
         halt=(halt if isinstance(halt, Mapping) else str(halt)))

    # ---- 6. the account-wide risk document: reachable, valid, FRESH.
    if not isinstance(account_risk, Mapping):
        fail.append("ACCOUNT_RISK_UNREADABLE")
    else:
        need(account_risk.get("reachable") is True, "ACCOUNT_RISK_UNREACHABLE")
        age = _dec(account_risk.get("age_s"))
        need(age is not None and ZERO <= age <= RISK_MAX_AGE_S,
             "ACCOUNT_RISK_STALE",
             age_s=None if age is None else str(age),
             max_age_s=str(RISK_MAX_AGE_S))
        need(_dec(account_risk.get("account_exposure_usd")) is not None,
             "ACCOUNT_RISK_HAS_NO_EXPOSURE_FIGURE")

    # ---- 7. INDEPENDENT coverage, which is NOT the managed-lane list.
    #
    #: The obvious reading -- "add the lane to
    #: L5_WATCHDOG_MANAGED_LANE_URLS" -- is backwards, and the
    #: watchdog's own docstring says so: registering a lane "EXEMPTS
    #: from the overdue/age latch only; it never makes an order
    #: cancellable". A managed lane is one the watchdog is told to LEAVE
    #: ALONE because that lane manages its own resting orders. Adding
    #: this lane would therefore REMOVE the age-based protection, which
    #: is the only protection that matters here: during normal operation
    #: an order rests ~15s against a 300s threshold, so the latch fires
    #: exactly in the case we want covered -- a crashed lane leaving an
    #: order resting.
    #:
    #: Two further things would have made the registration a no-op
    #: anyway: the watchdog fetches `<base>/l5/live_orders.json`, and
    #: this lane's `serve_fills` answers only `/btc5m_fills` and 404s
    #: everything else; and `lane_ids` requires
    #: `manages_own_resting: true`, which `fills_document` does not
    #: emit. The lane would have read UNREACHABLE and been judged by age
    #: regardless -- the right outcome, reached by accident.
    #:
    #: What coverage ACTUALLY requires is the ownership oracle. Nothing
    #: is cancelled unless `oracle.claimed()` names the venue order id;
    #: anything else is `not_ours: NOT_IN_OWNERSHIP_REGISTRY` and is
    #: left resting while the halt is asserted. So the oracle's registry
    #: must be able to see THIS lane's orders.
    url = str(this_lane_url or "").strip()
    need(url != "", "THIS_LANE_URL_UNKNOWN")
    try:
        managed = [str(u).strip() for u in (watchdog_managed_urls or ())]
    except TypeError:
        managed = []
        fail.append("WATCHDOG_MANAGED_LIST_UNREADABLE")
    #: being ON the list is the failure, not being off it
    need(not (url != "" and any(url == m or url in m for m in managed)),
         "LANE_EXEMPT_FROM_WATCHDOG_AGE_LATCH",
         this_lane_url=url, managed=managed,
         why=("the managed-lane list exempts a lane from the overdue/age "
              "cancel; this lane needs that latch, not an exemption"))
    need(oracle_sees_lane is True, "LANE_NOT_IN_OWNERSHIP_REGISTRY",
         why=("the watchdog cancels nothing it cannot claim: an order "
              "absent from the oracle's registry is left resting while "
              "the halt is asserted"))

    # ---- 8. the strategy must have valid inputs AND be reachable.
    if not isinstance(strategy_inputs, Mapping):
        fail.append("STRATEGY_INPUTS_UNREADABLE")
    else:
        for k in sorted(strategy_inputs):
            v = _dec(strategy_inputs[k])
            need(v is not None and v > ZERO, "STRATEGY_INPUT_UNSET:%s" % k,
                 raw=str(strategy_inputs[k]))
    tim = strategy_can_fire(**strategy_timing) if isinstance(
        strategy_timing, Mapping) else {"ok": False,
                                        "reason": "TIMING_UNREADABLE"}
    detail["strategy_timing"] = tim
    need(tim.get("ok") is True,
         "STRATEGY_CANNOT_FIRE:%s" % tim.get("reason"), **{
             k: v for k, v in tim.items() if k not in ("ok", "reason")})

    return {"ok": not fail, "failures": sorted(set(fail)), "detail": detail,
            "note": ("fail-closed: ok is True only when every protection is "
                     "present and binding. An absent protection is reported "
                     "as a failure, never assumed benign.")}


# --------------------------------------------------------------------------
# AUTHENTICATED DEPTH USED FOR PRICING MUST BE THE SAME BOOK AS THE QUOTE
# --------------------------------------------------------------------------
#: The runner prices against the PUBLIC endpoint's `bestBidQuote` /
#: `bestAskQuote` and takes executable SIZE from the AUTHENTICATED
#: socket, because the public endpoint publishes no size at any level.
#: Those are two different sources, and nothing was checking that they
#: describe the same thing.
#:
#: Four ways they can disagree, each of which would price an order
#: against one book while sizing it from another:
#:
#:   WRONG MARKET -- the snapshot is keyed by slug, so this is right by
#:     construction today, but a reader that trusts it silently inherits
#:     any future change to that keying.
#:   WRONG TOKEN -- a binary market has two tokens whose books are
#:     complements. `apply_events` keeps ONE bid/ask per slug with no
#:     token dimension, and `LF.evaluate` derives the DOWN side as
#:     `1 - up_bid` on the assumption that both sources quote the same
#:     token. If they did not, `ask_shares` would be the size resting on
#:     the OPPOSITE side -- and a 0.97 bid would be sized against depth
#:     that is not there.
#:   WRONG SIDE -- bid and ask transposed reads a 0.03 market as a 0.97
#:     one.
#:   STALE -- a push socket sends nothing when nothing changes, so the
#:     age of the last CHANGE proves nothing; the age of the last FRAME
#:     on a connected socket does. A stale snapshot's sizes beside a
#:     fresh quote is the same error as a wrong token, arriving slowly.
#:
#: ONE CHECK CATCHES ALL FOUR: the authenticated snapshot carries its own
#: bid and ask, which the runner was discarding. If they agree with the
#: quote we price against, the two sources describe the same book. If
#: they do not, nothing is sized and nothing is placed.
#:
#: Tolerance exists because the two are read microseconds apart over
#: different transports, not to paper over disagreement: one tick is the
#: smallest difference the venue can represent.
MAX_QUOTE_DIVERGENCE = D("0.01")
#: a connected socket delivers frames continuously; 2s of silence while
#: we are about to price off it is not freshness
MAX_STREAM_AGE_S = D("2.0")
#: the public quote we price against
MAX_PUBLIC_QUOTE_AGE_S = D("2.0")
#: market states in which an order may rest
TRADABLE_STATES = ("OPEN", "ACTIVE", "TRADING", "MARKET_STATE_OPEN")


def depth_for_pricing(*, public: Any, auth: Any, book: Any = None,
                      max_divergence: Any = MAX_QUOTE_DIVERGENCE,
                      max_stream_age_s: Any = MAX_STREAM_AGE_S,
                      max_public_age_s: Any = MAX_PUBLIC_QUOTE_AGE_S
                      ) -> Dict[str, Any]:
    """Executable size for the quote we price against, or why there is none.

    FAIL CLOSED. Every refusal returns `bid_shares`/`ask_shares` of None,
    which `LF.evaluate` reads as NO_DEPTH and refuses. Absent size is
    never read as unlimited size, and a disagreement is never resolved by
    preferring one source.
    """
    out: Dict[str, Any] = {"ok": False, "reason": None,
                           "bid_shares": None, "ask_shares": None}

    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(out, **kw, reason=reason)

    if not isinstance(auth, Mapping):
        return no("AUTH_BOOK_UNREADABLE")
    if auth.get("read") is not True:
        return no("AUTH_BOOK_NOT_READ", detail=str(auth.get("reason"))[:40])
    if not isinstance(public, Mapping):
        return no("PUBLIC_QUOTE_UNREADABLE")

    #: FRESHNESS FIRST, so a stale pair is never compared and then
    #: accepted because two stale readings happen to agree.
    sa = _dec(auth.get("stream_age_s"))
    if sa is None or sa < ZERO or sa > _dec(max_stream_age_s):
        return no("AUTH_BOOK_STALE", stream_age_s=None if sa is None
                  else str(sa), max_s=str(max_stream_age_s))
    pa = _dec(public.get("age_s"))
    if pa is None or pa < ZERO or pa > _dec(max_public_age_s):
        return no("PUBLIC_QUOTE_STALE", age_s=None if pa is None else str(pa),
                  max_s=str(max_public_age_s))

    ab, aa = _dec(auth.get("bid")), _dec(auth.get("ask"))
    pb, pa_ = _dec(public.get("bid")), _dec(public.get("ask"))
    if ab is None or aa is None:
        return no("AUTH_BOOK_ONE_SIDED")

    #: A CROSSED AUTHENTICATED BOOK IS NOT A BOOK. Ask below bid is a
    #: data fault, and it is the fault that matters: observed at 1915z
    #: on 2026-11-16, ask 0.98 under bid 0.99 with `ask_shares` frozen
    #: at 16.17 across every sample while bid size churned. Pricing
    #: against that phantom offer is exactly the error this function
    #: exists to prevent, so it is checked BEFORE the one-sided branch
    #: below can decide to rely on the authenticated ask.
    if aa < ab:
        #: NAME THE TRANSPOSITION WHEN WE CAN STILL SEE IT. A crossed
        #: book is the symptom; sides swapped against a readable public
        #: quote is the cause, and reporting the symptom would send
        #: someone hunting a venue data fault for a wiring one.
        tol0 = _dec(max_divergence) or ZERO
        if (pb is not None and pa_ is not None
                and abs(ab - pa_) <= tol0 and abs(aa - pb) <= tol0):
            return no("SIDES_TRANSPOSED",
                      auth={"bid": str(ab), "ask": str(aa)},
                      public={"bid": str(pb), "ask": str(pa_)})
        return no("AUTH_BOOK_CROSSED", auth={"bid": str(ab), "ask": str(aa)},
                  why=("ask below bid is a stale or phantom quote, not a "
                       "tradable offer"))

    if pb is None or pa_ is None:
        #: THE PUBLIC FEED DROPS A SIDE NEAR THE CLOSE; THE SOCKET DOES
        #: NOT. Measured across the four windows of 2026-11-16: all 19
        #: qualifying signals were refused PUBLIC_QUOTE_ONE_SIDED while
        #: the authenticated book carried a real, uncrossed ask with
        #: 814-38,728 shares behind it. Refusing there was a decision
        #: about OUR FEED, not about the market.
        #:
        #: So a one-sided public quote no longer refuses outright. It
        #: refuses the AGREEMENT CHECK -- there is nothing to agree
        #: with -- and falls back to the authenticated book alone, which
        #: has already been proved fresh and uncrossed above. The
        #: fallback is reported, never silent, because an order priced
        #: against one source is a weaker claim than one priced against
        #: two and the log must say which it was.
        side = "ask" if pa_ is None else "bid"
        bs, asz = _dec(auth.get("bid_shares")), _dec(auth.get("ask_shares"))
        if bs is None or asz is None or bs < ZERO or asz < ZERO:
            return no("DEPTH_UNREADABLE")
        st_ = auth.get("market_state")
        if st_ is not None and str(st_).strip().upper() not in TRADABLE_STATES:
            return no("MARKET_NOT_TRADABLE", market_state=str(st_)[:40])
        return {"ok": True, "reason": None,
                "bid_shares": str(bs), "ask_shares": str(asz),
                "source": "AUTHENTICATED_ONLY",
                "public_missing_side": side,
                "auth": {"bid": str(ab), "ask": str(aa)},
                "checked": {"stream_age_s": str(sa),
                            "agreement": "NOT_CHECKED_PUBLIC_ONE_SIDED"}}

    tol = _dec(max_divergence)
    if tol is None or tol < ZERO:
        return no("TOLERANCE_UNREADABLE")

    agree = abs(ab - pb) <= tol and abs(aa - pa_) <= tol
    if not agree:
        #: NAME THE COMPLEMENT CASE. If the authenticated book is the
        #: OTHER token's, its bid is about 1 - our ask. Reporting that as
        #: a generic divergence would send someone hunting a latency bug
        #: for a wiring one.
        if abs(ab - (ONE - pa_)) <= tol and abs(aa - (ONE - pb)) <= tol:
            return no("TOKEN_COMPLEMENT_MISMATCH",
                      auth={"bid": str(ab), "ask": str(aa)},
                      public={"bid": str(pb), "ask": str(pa_)},
                      why=("the authenticated book is the complementary "
                           "token's: its sizes belong to the opposite side"))
        #: and the transposition case, for the same reason
        if abs(ab - pa_) <= tol and abs(aa - pb) <= tol:
            return no("SIDES_TRANSPOSED",
                      auth={"bid": str(ab), "ask": str(aa)},
                      public={"bid": str(pb), "ask": str(pa_)})
        return no("QUOTE_DIVERGENCE",
                  auth={"bid": str(ab), "ask": str(aa)},
                  public={"bid": str(pb), "ask": str(pa_)},
                  max_divergence=str(tol))

    st = auth.get("market_state")
    if st is not None and str(st).strip().upper() not in TRADABLE_STATES:
        return no("MARKET_NOT_TRADABLE", market_state=str(st)[:40])

    bs, asz = _dec(auth.get("bid_shares")), _dec(auth.get("ask_shares"))
    if bs is None or asz is None or bs < ZERO or asz < ZERO:
        return no("DEPTH_UNREADABLE")
    return {"ok": True, "reason": None,
            "bid_shares": str(bs), "ask_shares": str(asz),
            "checked": {"stream_age_s": str(sa), "public_age_s": str(pa),
                        "max_divergence": str(tol)}}
