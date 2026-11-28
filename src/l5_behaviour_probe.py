"""BEHAVIOUR PROBE, EV UNPROVEN -- one named BTC5m window, by its own approval.

WHAT THIS IS. The Bonereaper-style two-sided mode (issue #247) cannot place
an order through the normal economic gate: the entry-EV gate needs a
conditional-on-fill pair rate of at least 0.751, the best figure anyone has
measured is 0.697, and it belongs to a wallet that lost money. That refusal
is the gate working. The owner nevertheless wants venue-confirmed evidence of
how the mode EXECUTES on PMUS -- fills, requotes, reconciliation -- and has
asked for a way to buy that evidence with a small, bounded, expected loss,
WITHOUT inventing a rate to get through the gate.

This module is that way, and only that. For ONE named window it lets a leg be
admitted when the entry-EV gate refused it SOLELY because no rate is set, and
it substitutes the hard worst-case window-loss ceiling
(`Params.straddle_max_window_loss_usd`) for the missing economics.

WHAT IT IS NOT.

  * NOT an authorization to trade. It does not arm anything. The owner's arm
    token, the campaign approval document, both shared halts, the account
    loss state, the watchdog, the cash and exposure caps, the order limit,
    post-only, exact-id cancellation and reconciliation are all the existing
    ones, all still required, and none is read or changed here.
  * NOT a way round a NEGATIVE verdict. If ANY entry-EV rate is set the probe
    refuses outright. A gate that was given numbers and said no stays no;
    this covers only "nobody has measured this", stated as such.
  * NOT reusable. The document names one market slug and one five-minute
    window and expires at that window's end by construction -- a longer
    expiry is refused, not shortened.
  * NOT part of the profitable-trading path. The controller carries one inert
    field for it; with no document in the environment nothing here runs and
    behaviour is byte-identical.

EVERYTHING HERE IS A READ. No transport, no writes, no variables set.

FAIL CLOSED. Every check must pass. An absent field, an unknown field, an
unreadable value and a contradiction are all refusals with a name. There is
no override argument and no severity ladder.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Mapping, Optional

#: the campaign class, and the token the controller looks for. It reads as
#: what it is so it cannot be set by someone who thinks it is a feature flag.
CLASS = "BEHAVIOUR_PROBE_EV_UNPROVEN"

#: the owner's document, as JSON. Absent means the probe does not exist.
DOC_ENV = "L5_BTC5M_BEHAVIOUR_PROBE"

#: must appear VERBATIM in the document. The owner states the economics in
#: their own approval; the code does not state it for them.
ACKNOWLEDGEMENT = ("ENTRY EV IS UNPROVEN. NO PAIRING RATE HAS BEEN MEASURED "
                   "OR IS ASSERTED. THIS WINDOW IS EXPECTED TO LOSE MONEY, "
                   "UP TO max_window_loss_usd.")

#: CODE CEILINGS. A document may ask for less, never more.
MAX_WINDOW_LOSS_USD = D("0.45")
WINDOW_S = 300
#: a document issued longer ago than this is stale even if its window is
#: still ahead, so a forgotten variable cannot authorize a later day.
MAX_DOC_AGE_S = 6 * 3600
#: the watchdog latches the ACCOUNT-WIDE halt on any order older than 300s
#: (`l5_watchdog.DEFAULT_MAX_ORDER_AGE_S`). The runner steps every 5s, so a
#: leg withdrawn at this age is gone by about 245s -- a 55s margin instead of
#: the ~2s a leg resting from the open to the close would have.
MAX_LEG_AGE_S = 240.0

#: the entry-EV gate's three rate variables, by NAME. Spelled here because
#: this module may not import the executor; a test holds them equal to
#: `l5_btc5m_execution`'s. See `evaluate`.
RATE_ENVS = ("L5_BTC5M_STRADDLE_P_PAIR_NATURAL",
             "L5_BTC5M_STRADDLE_P_COMPLETE",
             "L5_BTC5M_STRADDLE_P_LONE_WIN")

SERIES_PREFIX = "cpc-btc-updown-5m-"
_SLUG = re.compile(r"^cpc-btc-updown-5m-(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})z$")

FIELDS = ("class", "acknowledgement", "entry_ev", "market",
          "window_start_utc", "expires_utc", "issued_utc",
          "max_window_loss_usd", "nonce")

UTC = dt.timezone.utc


def _no(reason: str, **kw: Any) -> Dict[str, Any]:
    return dict(kw, ok=False, reason=reason, present=True)


def _dec(v: Any) -> Optional[D]:
    try:
        d = None if v is None or isinstance(v, bool) else D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d is not None and d.is_finite() else None


def _when(raw: Any) -> Optional[dt.datetime]:
    """ISO-8601 with an explicit offset or Z. A naive time is unreadable."""
    try:
        got = dt.datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if got.tzinfo is None:
        return None
    return got.astimezone(UTC)


def slug_window_start(market: Any) -> Optional[dt.datetime]:
    """The five-minute window a BTC5m slug names, or None."""
    m = _SLUG.match(str(market or ""))
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    try:
        got = dt.datetime(y, mo, d, h, mi, tzinfo=UTC)
    except ValueError:
        return None
    return got if mi % 5 == 0 else None


def read_document(market: Any, now: dt.datetime,
                  env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Is there a valid probe document for THIS market at THIS instant?

    `{"present": False}` when the variable is absent or empty -- the normal
    case, and the only one in which nothing about a step changes.
    """
    env = os.environ if env is None else env
    raw = (env.get(DOC_ENV) or "").strip()
    if not raw:
        return {"ok": False, "present": False, "reason": None}
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return _no("PROBE_DOCUMENT_UNREADABLE")
    if not isinstance(doc, dict):
        return _no("PROBE_DOCUMENT_UNREADABLE")
    missing = [k for k in FIELDS if k not in doc]
    if missing:
        return _no("PROBE_FIELD_MISSING", fields=missing)
    extra = sorted(k for k in doc if k not in FIELDS)
    if extra:
        #: an unknown field is a field somebody expected to mean something
        return _no("PROBE_FIELD_UNKNOWN", fields=extra)
    if doc["class"] != CLASS:
        return _no("PROBE_CLASS_MISMATCH")
    if doc["acknowledgement"] != ACKNOWLEDGEMENT:
        return _no("PROBE_NOT_ACKNOWLEDGED")
    if doc["entry_ev"] != "UNPROVEN":
        return _no("PROBE_EV_NOT_STATED_UNPROVEN")
    if not isinstance(doc["nonce"], str) or len(doc["nonce"].strip()) < 8:
        return _no("PROBE_NONCE_UNREADABLE")

    slug = doc["market"]
    start = slug_window_start(slug)
    if start is None:
        return _no("PROBE_MARKET_NOT_A_BTC5M_WINDOW")
    if str(market or "") != slug:
        #: ONE NAMED WINDOW. Any other market is simply not covered.
        return _no("PROBE_OTHER_MARKET", covers=slug)
    w0, exp, iss = (_when(doc["window_start_utc"]), _when(doc["expires_utc"]),
                    _when(doc["issued_utc"]))
    if w0 is None or exp is None or iss is None:
        return _no("PROBE_TIME_UNREADABLE")
    if w0 != start:
        return _no("PROBE_WINDOW_DOES_NOT_MATCH_MARKET")
    if exp != start + dt.timedelta(seconds=WINDOW_S):
        #: refused, not clamped: a longer expiry is a different approval
        return _no("PROBE_EXPIRY_NOT_THE_WINDOW_END")
    if now.tzinfo is None:
        return _no("PROBE_CLOCK_UNREADABLE")
    now = now.astimezone(UTC)
    if iss > now:
        return _no("PROBE_ISSUED_IN_THE_FUTURE")
    if (now - iss).total_seconds() > MAX_DOC_AGE_S:
        return _no("PROBE_DOCUMENT_STALE")
    if iss >= exp:
        return _no("PROBE_ISSUED_AFTER_ITS_WINDOW")
    if now < start:
        return _no("PROBE_WINDOW_NOT_OPEN")
    if now >= exp:
        return _no("PROBE_EXPIRED")

    cap = _dec(doc["max_window_loss_usd"])
    if cap is None or cap <= 0:
        return _no("PROBE_LOSS_CEILING_UNREADABLE")
    if cap > MAX_WINDOW_LOSS_USD:
        #: refused, not clamped: the owner approved a number, and silently
        #: running a different one is not honouring the approval
        return _no("PROBE_LOSS_CEILING_ABOVE_CODE_MAXIMUM",
                   asked=str(cap), maximum=str(MAX_WINDOW_LOSS_USD))
    return {"ok": True, "present": True, "reason": None, "class": CLASS,
            "market": slug, "max_window_loss_usd": cap,
            "expires_utc": exp.isoformat()}


# ------------------------------------------------- the campaign document
#
# WHY IT EXISTS (owner decision 2026-11-21). The one-window document needs
# a redeploy before every window, so consecutive windows cannot be measured
# at all: the fastest it allows is every other market. The owner asked for a
# bounded two-hour measurement of the SAME behaviour.
#
# WHAT IT IS. A SECOND document, in its own variable, that names EVERY
# window it covers by its exact slug. It is not a duration and not a series:
# a window that is not in the list is not covered, exactly as before.
#
# WHAT STAYS TRUE.
#   * It grants nothing. Arming, the campaign approval (which must itself
#     enumerate the same windows), the envelope, both halts, the account
#     loss state, the caps, post-only and reconciliation are the existing
#     ones and are not read or changed here.
#   * Every rule of the one-window document that is not about there being
#     one window still applies: the class, a verbatim acknowledgement, EV
#     stated UNPROVEN, no rate set, the per-window loss ceiling under the
#     code maximum, a document no older than six hours.
#   * Bounded in code: at most MAX_CAMPAIGN_WINDOWS windows, all inside
#     MAX_CAMPAIGN_SPAN_S from the first one's start, expiring at the last
#     one's end -- a longer expiry is refused, not shortened.
#   * It REQUIRES the code-enforced cumulative loss halt
#     (L5_BTC5M_CAMPAIGN_LOSS_HALT) to be set, readable, and no looser than
#     this document's own `max_campaign_loss_usd`, which is itself capped.
#   * With both documents set, NEITHER is honoured.
CAMPAIGN_CLASS = "BEHAVIOUR_PROBE_CAMPAIGN_EV_UNPROVEN"
CAMPAIGN_DOC_ENV = "L5_BTC5M_BEHAVIOUR_PROBE_CAMPAIGN"
CAMPAIGN_ACKNOWLEDGEMENT = (
    "ENTRY EV IS UNPROVEN. NO PAIRING RATE HAS BEEN MEASURED OR IS "
    "ASSERTED. EVERY LISTED WINDOW IS EXPECTED TO LOSE MONEY, UP TO "
    "max_window_loss_usd EACH AND max_campaign_loss_usd IN TOTAL.")
MAX_CAMPAIGN_WINDOWS = 24
MAX_CAMPAIGN_SPAN_S = 2 * 3600
#: owner decision 2026-11-21: raised from 0.60 so a 24-window campaign is not
#: stopped by its own ceiling part-way (observed loss 0.02-0.03 a window)
MAX_CAMPAIGN_LOSS_USD = D("1.50")
#: named here because this module may not import the executor; a test holds
#: it equal to `l5_btc5m_execution.CAMPAIGN_LOSS_HALT_ENV`.
LOSS_HALT_ENV = "L5_BTC5M_CAMPAIGN_LOSS_HALT"
CAMPAIGN_FIELDS = ("class", "acknowledgement", "entry_ev", "markets",
                   "issued_utc", "expires_utc", "max_window_loss_usd",
                   "max_campaign_loss_usd", "nonce")


def campaign_configured(env: Optional[Mapping[str, str]] = None) -> bool:
    """Is a campaign document SET (valid or not)? Callers that tighten a
    check for a campaign key on this, so an invalid document still gets the
    tighter check rather than none."""
    env = os.environ if env is None else env
    return bool((env.get(CAMPAIGN_DOC_ENV) or "").strip())


def read_campaign_document(market: Any, now: dt.datetime,
                           env: Optional[Mapping[str, str]] = None
                           ) -> Dict[str, Any]:
    """Is there a valid CAMPAIGN document covering THIS market NOW?"""
    env = os.environ if env is None else env
    raw = (env.get(CAMPAIGN_DOC_ENV) or "").strip()
    if not raw:
        return {"ok": False, "present": False, "reason": None}
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return _no("PROBE_DOCUMENT_UNREADABLE")
    if not isinstance(doc, dict):
        return _no("PROBE_DOCUMENT_UNREADABLE")
    missing = [k for k in CAMPAIGN_FIELDS if k not in doc]
    if missing:
        return _no("PROBE_FIELD_MISSING", fields=missing)
    extra = sorted(k for k in doc if k not in CAMPAIGN_FIELDS)
    if extra:
        return _no("PROBE_FIELD_UNKNOWN", fields=extra)
    if doc["class"] != CAMPAIGN_CLASS:
        return _no("PROBE_CLASS_MISMATCH")
    if doc["acknowledgement"] != CAMPAIGN_ACKNOWLEDGEMENT:
        return _no("PROBE_NOT_ACKNOWLEDGED")
    if doc["entry_ev"] != "UNPROVEN":
        return _no("PROBE_EV_NOT_STATED_UNPROVEN")
    if not isinstance(doc["nonce"], str) or len(doc["nonce"].strip()) < 8:
        return _no("PROBE_NONCE_UNREADABLE")
    slugs = doc["markets"]
    if not isinstance(slugs, list) or not slugs \
            or not all(isinstance(x, str) for x in slugs):
        return _no("PROBE_MARKETS_UNREADABLE")
    if len(slugs) > MAX_CAMPAIGN_WINDOWS:
        return _no("PROBE_TOO_MANY_WINDOWS", count=len(slugs),
                   maximum=MAX_CAMPAIGN_WINDOWS)
    starts = [slug_window_start(x) for x in slugs]
    if any(x is None for x in starts):
        return _no("PROBE_MARKET_NOT_A_BTC5M_WINDOW")
    if any(b <= a for a, b in zip(starts, starts[1:])):
        #: strictly ascending: no repeat, and the list reads as a schedule
        return _no("PROBE_MARKETS_NOT_ASCENDING")
    first, last_end = starts[0], starts[-1] + dt.timedelta(seconds=WINDOW_S)
    if (last_end - first).total_seconds() > MAX_CAMPAIGN_SPAN_S:
        return _no("PROBE_CAMPAIGN_TOO_LONG",
                   span_s=(last_end - first).total_seconds(),
                   maximum=MAX_CAMPAIGN_SPAN_S)
    exp, iss = _when(doc["expires_utc"]), _when(doc["issued_utc"])
    if exp is None or iss is None:
        return _no("PROBE_TIME_UNREADABLE")
    if exp != last_end:
        return _no("PROBE_EXPIRY_NOT_THE_LAST_WINDOW_END")
    if now.tzinfo is None:
        return _no("PROBE_CLOCK_UNREADABLE")
    now = now.astimezone(UTC)
    if iss > now:
        return _no("PROBE_ISSUED_IN_THE_FUTURE")
    if (now - iss).total_seconds() > MAX_DOC_AGE_S:
        return _no("PROBE_DOCUMENT_STALE")
    if iss >= first + dt.timedelta(seconds=WINDOW_S):
        #: issued before the campaign's first window has ended: a document
        #: cannot be written to cover windows after the fact
        return _no("PROBE_ISSUED_AFTER_ITS_FIRST_WINDOW")
    cap = _dec(doc["max_window_loss_usd"])
    if cap is None or cap <= 0:
        return _no("PROBE_LOSS_CEILING_UNREADABLE")
    if cap > MAX_WINDOW_LOSS_USD:
        return _no("PROBE_LOSS_CEILING_ABOVE_CODE_MAXIMUM",
                   asked=str(cap), maximum=str(MAX_WINDOW_LOSS_USD))
    total = _dec(doc["max_campaign_loss_usd"])
    if total is None or total <= 0:
        return _no("PROBE_CAMPAIGN_LOSS_CEILING_UNREADABLE")
    if total > MAX_CAMPAIGN_LOSS_USD:
        return _no("PROBE_CAMPAIGN_LOSS_CEILING_ABOVE_CODE_MAXIMUM",
                   asked=str(total), maximum=str(MAX_CAMPAIGN_LOSS_USD))
    #: THE CUMULATIVE STOP MUST EXIST IN CODE, not only in this document.
    try:
        halt = json.loads((env.get(LOSS_HALT_ENV) or "").strip() or "null")
    except (ValueError, TypeError):
        halt = None
    hmax = _dec(halt.get("max_loss_usd")) if isinstance(halt, dict) else None
    if hmax is None or hmax <= 0:
        return _no("PROBE_CAMPAIGN_NEEDS_THE_LOSS_HALT", env=LOSS_HALT_ENV)
    if hmax > total:
        return _no("PROBE_CAMPAIGN_LOSS_HALT_LOOSER_THAN_APPROVED",
                   halt=str(hmax), approved=str(total))
    if str(market or "") not in slugs:
        return _no("PROBE_OTHER_MARKET", covers="%d listed windows"
                   % len(slugs))
    start = slug_window_start(market)
    end = start + dt.timedelta(seconds=WINDOW_S)
    if now < start:
        return _no("PROBE_WINDOW_NOT_OPEN")
    if now >= end:
        return _no("PROBE_EXPIRED")
    return {"ok": True, "present": True, "reason": None,
            "class": CAMPAIGN_CLASS, "market": str(market),
            "max_window_loss_usd": cap, "expires_utc": end.isoformat(),
            "campaign_windows": len(slugs),
            "campaign_expires_utc": last_end.isoformat(),
            "max_campaign_loss_usd": str(total)}


def preconditions(params: Any, grant: Mapping[str, Any]) -> Optional[str]:
    """Why the effective configuration cannot run a probe, or None.

    Read off the SAME effective params the controller will be handed, so a
    clamped or defaulted value is judged as what it became.
    """
    def g(name: str, default: Any = None) -> Any:
        return getattr(params, name, default)

    if g("entry_mode") != "STRADDLE":
        return "PROBE_NEEDS_STRADDLE_MODE"
    if not g("straddle_book_relative", False):
        return "PROBE_NEEDS_BOOK_RELATIVE"
    if int(g("straddle_requote_ticks", 0) or 0) <= 0:
        return "PROBE_NEEDS_REQUOTE"
    #: THE LOSS CEILING IS MANDATORY, and it is the one in force that must
    #: sit inside the document's -- not merely one that was typed somewhere.
    cap = _dec(g("straddle_max_window_loss_usd"))
    if cap is None or cap <= 0:
        return "PROBE_NEEDS_WINDOW_LOSS_CEILING"
    if cap > _dec(grant.get("max_window_loss_usd")):
        return "PROBE_LOSS_CEILING_LOOSER_THAN_APPROVED"
    age = g("straddle_leg_max_age_s", 0) or 0
    try:
        age = float(age)
    except (TypeError, ValueError):
        return "PROBE_NEEDS_LEG_MAX_AGE"
    if not (0 < age <= MAX_LEG_AGE_S):
        return "PROBE_NEEDS_LEG_MAX_AGE"
    #: NO INVENTED RATES. A rate that is set means the gate was given a
    #: number; then the gate's answer stands and there is nothing to probe.
    for name in ("straddle_p_pair_natural", "straddle_p_complete",
                 "straddle_p_lone_win"):
        if g(name) is not None:
            return "PROBE_REFUSES_WHILE_AN_EV_RATE_IS_SET"
    #: ONE BEHAVIOUR. Nothing that takes liquidity, pays up to complete, or
    #: opens a second strategy inside the window.
    if g("taker_allowed", False) or g("completion_cross_enabled", False):
        return "PROBE_REFUSES_TAKER"
    cpc = _dec(g("straddle_completion_pair_cost", 0))
    if cpc is None or cpc != 0:
        return "PROBE_REFUSES_COMPLETION"
    lf = _dec(g("late_favorite_window_s", 0))
    if lf is None or lf != 0:
        return "PROBE_REFUSES_LATE_FAVORITE"
    if g("inventory_ranking_enabled", False):
        return "PROBE_REFUSES_INVENTORY_RANKING"
    if int(g("straddle_reprice_ticks_per_clip", 0) or 0) != 0:
        return "PROBE_REFUSES_INVENTORY_REPRICING"
    return None


def evaluate(*, market: Any, now: dt.datetime, params: Any,
             env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """The one question the executor asks each step: may this step's legs be
    admitted under the probe? `ok` is True only when the document is valid
    for this market now AND the effective configuration satisfies every
    precondition. Never raises: an error here is a refusal."""
    try:
        doc = read_document(market, now, env)
        camp = read_campaign_document(market, now, env)
        if doc.get("present") and camp.get("present"):
            #: two approvals for one step is not an approval
            return _no("PROBE_TWO_DOCUMENTS_SET")
        if camp.get("present"):
            doc = camp
        if not doc.get("present"):
            return doc
        if not doc.get("ok"):
            return doc
        #: A RATE VARIABLE THAT IS SET AT ALL REFUSES, even one the gate is
        #: ignoring. Since the conditional-basis attestation was added, a
        #: rate without it reads as None in the effective params -- so a
        #: leftover number from an earlier campaign would pass the check in
        #: `preconditions` while still sitting in the environment. "No
        #: invented rates" is then a statement about the configuration the
        #: owner can see, not only about what the gate happened to read.
        _env = os.environ if env is None else env
        _set = [k for k in RATE_ENVS if (_env.get(k) or "").strip()]
        if _set:
            return _no("PROBE_REFUSES_WHILE_AN_EV_RATE_IS_SET",
                       market=doc["market"], variables=_set)
        why = preconditions(params, doc)
        if why is not None:
            return _no(why, market=doc["market"])
        return {"ok": True, "present": True, "reason": None,
                "class": doc.get("class", CLASS),
                "market": doc["market"],
                "max_window_loss_usd": str(doc["max_window_loss_usd"]),
                "expires_utc": doc["expires_utc"],
                **{k: doc[k] for k in ("campaign_windows",
                                       "campaign_expires_utc",
                                       "max_campaign_loss_usd") if k in doc},
                "label": ("entry EV unproven; legs are admitted on the "
                          "window loss ceiling, not on expected value. Not "
                          "an authorization: arming is unchanged.")}
    except Exception as exc:                                   # noqa: BLE001
        return _no("PROBE_EVALUATION_ERROR", error=type(exc).__name__)


def summary(state: Mapping[str, Any]) -> Dict[str, Any]:
    """Safe to log: never the nonce, never the document."""
    return {k: state.get(k) for k in ("present", "ok", "reason", "class",
                                      "market", "max_window_loss_usd",
                                      "expires_utc", "campaign_windows",
                                      "campaign_expires_utc",
                                      "max_campaign_loss_usd")
            if k in state}
