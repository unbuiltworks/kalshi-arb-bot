"""Standing authorization and durable RUN/STOP state. FAIL-CLOSED.

WHY THIS EXISTS. The bounded path authorizes a campaign by ENUMERATING
every window by name, so it cannot roll past its own list and expires at
`NOT_AFTER`. That is the right shape for a probe and the wrong shape for
a service meant to run until an operator says stop. This module is the
other shape, and it does NOT replace the first: `l5_campaign_approval`
stays exactly as it is, and a deployment uses one or the other.

WHAT AN ENVELOPE IS. One owner-signed document naming what may happen at
all: which strategies, which market series, which deployment, whether
taker execution is permitted, the aggregate and per-market exposure
ceilings, the daily realized-loss halt, and an optional expiry. It names
no windows. Rollover is then not a new authorization -- it is the same
authorization still being true.

WHAT IT DELIBERATELY IS NOT. It is not permission to trade by itself.
Submission needs the envelope AND durable state at ARMED AND no latched
halt. Three things, each of which an operator can revoke alone.

THE FOUR STATES, and why they are not one flag:

  MONITOR   read, decide, record. Submits nothing. The resting state.
  ARMED     may submit, within the envelope.
  HALTED    a risk limit was breached. LATCHED: it never clears itself,
            not on restart, not on a new UTC day, not on redeploy. Only
            an explicit owner reauthorization carrying the latch's own
            digest clears it.
  STOPPED   an operator said stop. Also latched. New orders are refused
            while outstanding ones are cancelled and reconciled.

NO AUTOMATIC REARMING, EVER. A process that restarts into HALTED or
STOPPED stays there. That rule is the whole reason the state is durable
rather than held in memory: a money-moving service that forgets why it
stopped is a service that stops for nothing.

WHAT MAKES IT FAIL CLOSED. A missing document, one that will not parse,
one whose expiry has passed, one naming another deployment, a state file
that cannot be read or written, a clock that cannot be read, an exposure
figure that cannot be computed -- each refuses. No absent input is ever
read as a permissive one, because every absent input here is one an
attacker or a mistake could produce by deleting something.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import tempfile
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

D = Decimal
ZERO = D("0")

ENVELOPE_ENV = "L5_PERSISTENT_ENVELOPE"
STATE_PATH_ENV = "L5_PERSISTENT_STATE"
DEPLOYMENT_ENV = "L5_DEPLOYMENT_ID"

MONITOR, ARMED, HALTED, STOPPED = "MONITOR", "ARMED", "HALTED", "STOPPED"
STATES = (MONITOR, ARMED, HALTED, STOPPED)
#: the two an operator must clear by hand. Nothing in this module moves
#: out of them on its own.
LATCHED = (HALTED, STOPPED)

#: CODE CEILINGS. The document may ask for less and never for more, so a
#: forged or fat-fingered envelope cannot widen the system beyond what
#: was reviewed here. These are the numbers the owner approved on
#: 2026-11-16 for the first persistent campaign.
MAX_AGGREGATE_USD = D("10.00")
MAX_PER_MARKET_USD = D("5.00")
MAX_DAILY_LOSS_USD = D("5.00")
#: strategies and series this module will recognise at all.
KNOWN_STRATEGIES = ("STRADDLE", "LATE_FAVORITE")


def _dec(x: Any) -> Optional[Decimal]:
    if isinstance(x, Decimal):
        return x if x.is_finite() else None
    if x is None or isinstance(x, bool):
        return None
    try:
        v = D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return v if v.is_finite() else None


def _utc(x: Any) -> Optional[dt.datetime]:
    if isinstance(x, dt.datetime):
        return x if x.tzinfo else None
    if not isinstance(x, str) or not x.strip():
        return None
    try:
        t = dt.datetime.fromisoformat(x.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def digest(text: Any) -> str:
    """A stable short digest, used to pin a latch to its own reason."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:32]


# =============================================================== envelope

def parse_envelope(raw: Any, *, deployment: Any, now: Any) -> Dict[str, Any]:
    """The envelope's terms, or why it authorizes nothing."""
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason, scope=None)

    if raw is None or not str(raw).strip():
        return no("ENVELOPE_ABSENT", env=ENVELOPE_ENV)
    try:
        doc = json.loads(str(raw))
    except (ValueError, TypeError):
        return no("ENVELOPE_UNPARSEABLE")
    if not isinstance(doc, Mapping):
        return no("ENVELOPE_NOT_AN_OBJECT")
    if doc.get("approved") is not True:
        return no("ENVELOPE_NOT_APPROVED")
    if str(doc.get("mode") or "") != "PERSISTENT":
        #: a bounded campaign document must never be read as a standing
        #: one, so the mode is named in the document itself.
        return no("ENVELOPE_NOT_PERSISTENT", mode=str(doc.get("mode"))[:32])

    at = _utc(now)
    if at is None:
        return no("NOW_UNREADABLE")
    #: AN EXPIRY IS OPTIONAL, but an unreadable one is not "absent".
    exp_raw = doc.get("expires_utc")
    expires = None
    if exp_raw is not None:
        expires = _utc(exp_raw)
        if expires is None:
            return no("ENVELOPE_EXPIRY_UNREADABLE", got=str(exp_raw)[:40])
        if at >= expires:
            return no("ENVELOPE_EXPIRED", expires_utc=expires.isoformat())

    #: DEPLOYMENT IDENTITY. An envelope follows the deployment it was
    #: written for; a new image does not inherit it.
    want_dep = str(doc.get("deployment") or "").strip()
    mine = str(deployment or "").strip()
    if not want_dep:
        return no("ENVELOPE_NAMES_NO_DEPLOYMENT")
    if not mine:
        return no("DEPLOYMENT_UNKNOWN", env=DEPLOYMENT_ENV)
    if want_dep != mine:
        return no("ENVELOPE_IS_FOR_ANOTHER_DEPLOYMENT",
                  envelope=want_dep[:40], this=mine[:40])

    strats = doc.get("strategies")
    if not isinstance(strats, Sequence) or isinstance(strats, str) or not strats:
        return no("ENVELOPE_NAMES_NO_STRATEGIES")
    strategies = tuple(str(s).strip().upper() for s in strats)
    unknown = [s for s in strategies if s not in KNOWN_STRATEGIES]
    if unknown:
        return no("ENVELOPE_UNKNOWN_STRATEGY", got=unknown[:4])

    ser = doc.get("series")
    if not isinstance(ser, Sequence) or isinstance(ser, str) or not ser:
        return no("ENVELOPE_NAMES_NO_SERIES")
    series = tuple(str(s).strip() for s in ser)
    try:
        from src import l5_series_registry as _REG
        allowed = set(_REG.prefixes())
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return no("REGISTRY_UNAVAILABLE")
    bad = [s for s in series if s not in allowed]
    if bad:
        return no("ENVELOPE_UNKNOWN_SERIES", got=bad[:4])

    taker = doc.get("taker_allowed")
    if taker not in (True, False):
        #: maker-only is a PERMISSION, so it is stated, never defaulted.
        return no("ENVELOPE_TAKER_PERMISSION_UNSTATED")

    agg = _dec(doc.get("max_aggregate_usd"))
    per = _dec(doc.get("max_per_market_usd"))
    loss = _dec(doc.get("max_daily_loss_usd"))
    if agg is None or per is None or loss is None:
        return no("ENVELOPE_LIMITS_UNREADABLE")
    if agg <= ZERO or per <= ZERO or loss <= ZERO:
        return no("ENVELOPE_LIMITS_NOT_POSITIVE")
    #: CLAMPED DOWN, NEVER UP.
    if agg > MAX_AGGREGATE_USD or per > MAX_PER_MARKET_USD \
            or loss > MAX_DAILY_LOSS_USD:
        return no("ENVELOPE_EXCEEDS_CODE_CEILING",
                  asked={"aggregate": str(agg), "per_market": str(per),
                         "daily_loss": str(loss)},
                  ceiling={"aggregate": str(MAX_AGGREGATE_USD),
                           "per_market": str(MAX_PER_MARKET_USD),
                           "daily_loss": str(MAX_DAILY_LOSS_USD)})
    if per > agg:
        #: a per-market ceiling above the aggregate is a contradiction,
        #: not a generous setting.
        return no("ENVELOPE_PER_MARKET_ABOVE_AGGREGATE")

    nonce = str(doc.get("nonce") or "").strip()
    if len(nonce) < 8:
        return no("ENVELOPE_NONCE_TOO_SHORT")

    return {"ok": True, "reason": None, "scope": {
        "mode": "PERSISTENT", "strategies": strategies, "series": series,
        "deployment": want_dep, "taker_allowed": bool(taker),
        "max_aggregate_usd": agg, "max_per_market_usd": per,
        "max_daily_loss_usd": loss,
        "expires_utc": None if expires is None else expires.isoformat(),
        "nonce": nonce, "digest": digest(json.dumps(doc, sort_keys=True,
                                                    default=str))}}


def envelope(*, now: Any, raw: Any = None,
             deployment: Any = None) -> Dict[str, Any]:
    """`parse_envelope` on the environment's own document."""
    return parse_envelope(
        os.environ.get(ENVELOPE_ENV) if raw is None else raw,
        deployment=(os.environ.get(DEPLOYMENT_ENV) if deployment is None
                    else deployment),
        now=now)


# ============================================================ durable state

def state_path() -> Optional[str]:
    p = str(os.environ.get(STATE_PATH_ENV) or "").strip()
    return p or None


def read_state(path: Optional[str] = None) -> Dict[str, Any]:
    """The durable state, or why it is unusable.

    AN UNREADABLE STATE FILE IS NOT "MONITOR". It is a refusal: the one
    thing it could be hiding is a latch.
    """
    p = path or state_path()
    if not p:
        return {"ok": False, "reason": "STATE_PATH_UNSET",
                "env": STATE_PATH_ENV, "state": None}
    try:
        text = pathlib.Path(p).read_text(encoding="utf-8")
    except FileNotFoundError:
        #: NEVER TRADED HERE. MONITOR is the only safe birth state.
        return {"ok": True, "reason": None, "state": MONITOR,
                "born": True, "detail": {}}
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"ok": False, "reason": "STATE_UNREADABLE",
                "error": type(exc).__name__, "state": None}
    try:
        doc = json.loads(text)
    except (ValueError, TypeError):
        return {"ok": False, "reason": "STATE_CORRUPT", "state": None}
    if not isinstance(doc, Mapping):
        return {"ok": False, "reason": "STATE_CORRUPT", "state": None}
    st = str(doc.get("state") or "")
    if st not in STATES:
        return {"ok": False, "reason": "STATE_UNKNOWN_VALUE",
                "got": st[:24], "state": None}
    return {"ok": True, "reason": None, "state": st, "born": False,
            "detail": {k: v for k, v in doc.items() if k != "state"}}


def write_state(state: str, *, why: Any, now: Any,
                path: Optional[str] = None,
                latch_digest: Optional[str] = None) -> Dict[str, Any]:
    """Persist a state transition durably, or say why it did not happen.

    Written to a temporary file in the same directory and renamed, so a
    crash mid-write leaves the previous state rather than a truncated
    one. fsynced, because the state this protects is the memory of why
    the system stopped.
    """
    if state not in STATES:
        return {"ok": False, "reason": "STATE_UNKNOWN_VALUE", "got": str(state)[:24]}
    at = _utc(now)
    if at is None:
        return {"ok": False, "reason": "NOW_UNREADABLE"}
    p = path or state_path()
    if not p:
        return {"ok": False, "reason": "STATE_PATH_UNSET", "env": STATE_PATH_ENV}
    doc = {"state": state, "why": str(why)[:400],
           "at_utc": at.isoformat(), "latch_digest": latch_digest}
    try:
        folder = os.path.dirname(os.path.abspath(p))
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".l5state.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"ok": False, "reason": "STATE_NOT_WRITTEN",
                "error": type(exc).__name__}
    return {"ok": True, "reason": None, "state": state, "written": doc}


# ==================================================== the owner's latch reset
#
# WHY THIS EXISTS. HALTED and STOPPED are latched and NOTHING cleared them:
# every writer in the system writes a latch, none removes one. So a lane
# halted once could never trade again without someone editing the state
# file by hand, which leaves no record of who did it, when, or what the
# latch said. On 2026-11-20 an owner-approved window armed into a STOPPED
# written on 2026-11-17 and traded nothing.
#
# WHAT IT IS. ONE document from the owner that names ONE latch by the hash
# of its stored record. It moves that latch to MONITOR -- never to ARMED --
# and only after the original record has been appended, verbatim, to an
# audit file beside the state. Trading afterwards still needs the envelope,
# the campaign approval and the arming ceremony, exactly as before.
#
# WHAT CANNOT CLEAR A LATCH, and tests hold each: a restart; a new or
# expired envelope; a new or expired campaign approval; `enter_armed`; the
# resident loop; a reset document that is malformed, expired, for another
# latch, already used, or presented while the lane could trade or while the
# watchdog's halt is not readable-and-clear. The watchdog is not involved
# and is not weakened: it asserts halts, this does not touch its record.

LATCH_RESET_ENV = "L5_PERSISTENT_LATCH_RESET"
RESET_ACTION = "CLEAR_LATCH_TO_MONITOR"
RESET_ACKNOWLEDGEMENT = (
    "I HAVE READ WHY THIS LATCH WAS SET. THE CONDITION IS RESOLVED. THIS "
    "CLEARS THAT ONE LATCH TO MONITOR. IT DOES NOT ARM THE LANE AND "
    "AUTHORIZES NO ORDER.")
RESET_FIELDS = frozenset((
    "approved", "action", "latch_sha256", "latch_state", "reason",
    "issued_utc", "expires_utc", "nonce", "acknowledgement"))
#: a reset document is good for one hour at most
MAX_RESET_LIFETIME_S = 3600.0
RESET_LOG_SUFFIX = ".latch_resets.jsonl"
#: what the caller must establish, each exactly, before a latch may clear
RESET_SAFE_KEYS = ("watchdog_halt_clear", "own_live_orders",
                   "own_exposure_usd", "lane_can_submit")


def latch_identity(st: Mapping[str, Any]) -> Optional[str]:
    """The SHA-256 of a stored state record, or None if it is unreadable.

    Covers the state, when it was written, why, and its digest, so two
    latches are never the same identity: a document written for one cannot
    clear a later one.
    """
    if not isinstance(st, Mapping) or not st.get("ok"):
        return None
    d = st.get("detail") or {}
    doc = {"state": st.get("state"), "why": d.get("why"),
           "at_utc": d.get("at_utc"), "latch_digest": d.get("latch_digest")}
    return hashlib.sha256(json.dumps(doc, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def stored_state_report(path: Optional[str] = None) -> Dict[str, Any]:
    """What is on the volume, for the log. READ-ONLY.

    Emitted whatever the envelope says, because a latch that is only
    looked at once a valid envelope is presented is a latch that stays
    invisible until the moment a campaign is armed into it.
    """
    st = read_state(path)
    if not st.get("ok"):
        return {"readable": False, "reason": st.get("reason"),
                "state": None, "latched": None, "would_refuse_arming": True}
    d = st.get("detail") or {}
    latched = st["state"] in LATCHED
    return {"readable": True, "state": st["state"], "latched": latched,
            "born": bool(st.get("born")),
            "at_utc": d.get("at_utc"), "why": d.get("why"),
            "latch_sha256": latch_identity(st) if latched else None,
            "would_refuse_arming": latched,
            "note": ("LATCHED: no envelope, approval or restart clears "
                     "this; arming will be refused until the owner clears "
                     "it by its latch_sha256" if latched else None)}


def _reset_log_path(p: str) -> str:
    return p + RESET_LOG_SUFFIX


def _reset_nonces(p: str) -> Optional[set]:
    """Nonces already spent, or None when the audit file cannot be read."""
    try:
        text = pathlib.Path(_reset_log_path(p)).read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None
    out = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            out.add(str(json.loads(line).get("reset", {}).get("nonce")))
        except (ValueError, TypeError, AttributeError):
            return None                 # a corrupt audit trail refuses
    return out


def clear_latch(*, raw: Any, now: Any, safe: Any,
                path: Optional[str] = None) -> Dict[str, Any]:
    """HALTED/STOPPED -> MONITOR for the ONE latch the owner named.

    Every refusal leaves the state file exactly as it was. The order is
    deliberate: nothing is written until every check has passed, and the
    audit record is durable BEFORE the state changes, so a crash between
    the two leaves a latch with a record, never a cleared latch without one.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, cleared=False, reason=reason)

    if raw is None or not str(raw).strip():
        return no("RESET_ABSENT")
    at = _utc(now)
    if at is None:
        return no("NOW_UNREADABLE")
    p = path or state_path()
    if not p:
        return no("STATE_PATH_UNSET")
    st = read_state(p)
    if not st.get("ok"):
        return no("STATE:%s" % st.get("reason"))
    if st["state"] not in LATCHED:
        return no("NOT_LATCHED", state=st["state"])
    ident = latch_identity(st)
    try:
        doc = json.loads(str(raw))
    except (ValueError, TypeError):
        return no("RESET_UNPARSEABLE")
    if not isinstance(doc, dict):
        return no("RESET_NOT_AN_OBJECT")
    if set(doc) != RESET_FIELDS:
        return no("RESET_FIELDS", missing=sorted(RESET_FIELDS - set(doc)),
                  unknown=sorted(set(doc) - RESET_FIELDS))
    if doc["approved"] is not True:
        return no("RESET_NOT_APPROVED")
    if doc["action"] != RESET_ACTION:
        return no("RESET_ACTION")
    if doc["acknowledgement"] != RESET_ACKNOWLEDGEMENT:
        return no("RESET_ACKNOWLEDGEMENT")
    nonce = str(doc["nonce"] or "").strip() \
        if isinstance(doc["nonce"], str) else ""
    if len(nonce) < 8:
        return no("RESET_NONCE_TOO_SHORT")
    why = doc["reason"]
    if not isinstance(why, str) or len(why.strip()) < 20:
        return no("RESET_REASON_TOO_SHORT")
    issued, expires = _utc(doc["issued_utc"]), _utc(doc["expires_utc"])
    if issued is None or expires is None:
        return no("RESET_TIMES_UNREADABLE")
    if issued > at:
        return no("RESET_ISSUED_IN_THE_FUTURE")
    if expires <= issued \
            or (expires - issued).total_seconds() > MAX_RESET_LIFETIME_S:
        return no("RESET_LIFETIME")
    if at >= expires:
        return no("RESET_EXPIRED")
    if doc["latch_state"] != st["state"]:
        return no("RESET_IS_FOR_ANOTHER_STATE", stored=st["state"])
    if not isinstance(doc["latch_sha256"], str) \
            or doc["latch_sha256"] != ident:
        return no("RESET_IS_FOR_ANOTHER_LATCH", stored_latch_sha256=ident)
    # THE CONDITIONS, each established by the caller and each REQUIRED. A
    # missing one is a refusal, never a pass.
    if not isinstance(safe, Mapping) \
            or any(k not in safe for k in RESET_SAFE_KEYS):
        return no("RESET_CONDITIONS_NOT_ESTABLISHED")
    if safe["watchdog_halt_clear"] is not True:
        return no("RESET_WHILE_WATCHDOG_HALT_NOT_CLEAR")
    if isinstance(safe["own_live_orders"], bool) \
            or safe["own_live_orders"] != 0:
        return no("RESET_WHILE_ORDERS_LIVE")
    exp = _dec(safe["own_exposure_usd"])
    if exp is None or exp != ZERO:
        return no("RESET_WHILE_EXPOSED")
    if safe["lane_can_submit"] is not False:
        #: clearing a latch and trading are two decisions on two restarts
        return no("RESET_WHILE_THE_LANE_COULD_SUBMIT")
    spent = _reset_nonces(p)
    if spent is None:
        return no("RESET_AUDIT_UNREADABLE")
    if nonce in spent:
        return no("RESET_ALREADY_USED")

    record = {"t_utc": at.isoformat(), "latch_sha256": ident,
              "original": {"state": st["state"],
                           **dict(st.get("detail") or {})},
              "reset": {k: doc[k] for k in sorted(doc)},
              "conditions": {k: str(safe[k]) for k in RESET_SAFE_KEYS},
              "to": MONITOR}
    try:
        folder = os.path.dirname(os.path.abspath(p))
        os.makedirs(folder, exist_ok=True)
        with open(_reset_log_path(p), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return no("RESET_AUDIT_NOT_WRITTEN", error=type(exc).__name__)
    out = write_state(
        MONITOR, now=at, path=p, latch_digest=None,
        why="LATCH_CLEARED_BY_OWNER nonce=%s was=%s latch=%s" % (
            nonce[:32], st["state"], ident[:16]))
    if not out.get("ok"):
        return no("STATE_NOT_WRITTEN:%s" % out.get("reason"),
                  audit_written=True)
    return {"ok": True, "cleared": True, "reason": None,
            "was": st["state"], "state": MONITOR, "latch_sha256": ident,
            "armed": False, "audit": _reset_log_path(p),
            "note": ("the lane is in MONITOR. It is NOT armed: trading "
                     "still needs the envelope, the campaign approval and "
                     "the arming ceremony.")}


# ============================================================== the gate

def may_submit(*, market: Any, strategy: Any, post_only: Any,
               notional_usd: Any, market_exposure_usd: Any,
               aggregate_exposure_usd: Any, day_realized_usd: Any,
               now: Any, raw: Any = None, deployment: Any = None,
               path: Optional[str] = None) -> Dict[str, Any]:
    """Whether ONE order may be submitted under the standing envelope.

    Every exposure figure is passed IN, already including resting orders
    and held inventory, because a caller that computed its own could not
    be prevented from double-spending the shared budget.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason)

    env = envelope(now=now, raw=raw, deployment=deployment)
    if not env.get("ok"):
        return no(str(env.get("reason")),
                  **{k: v for k, v in env.items()
                     if k not in ("ok", "reason", "scope")})
    scope = env["scope"]

    st = read_state(path)
    if not st.get("ok"):
        return no(str(st.get("reason")), **{k: v for k, v in st.items()
                                            if k not in ("ok", "reason", "state")})
    cur = st["state"]
    if cur in LATCHED:
        #: LATCHED MEANS LATCHED. No restart, no new day, no redeploy.
        return no("STATE_%s" % cur, state=cur,
                  detail=st.get("detail") or {},
                  note="latched; only an explicit owner reauthorization "
                       "clears it")
    if cur != ARMED:
        return no("STATE_NOT_ARMED", state=cur)

    strat = str(strategy or "").strip().upper()
    if strat not in scope["strategies"]:
        return no("STRATEGY_NOT_AUTHORIZED", strategy=strat[:32],
                  authorized=list(scope["strategies"]))
    mkt = str(market or "").strip()
    if not mkt or not any(mkt.startswith(s) for s in scope["series"]):
        return no("MARKET_NOT_AUTHORIZED", market=mkt[:60],
                  authorized=list(scope["series"]))
    if post_only is not True and not scope["taker_allowed"]:
        return no("MAKER_ONLY", note="the envelope permits no taker "
                                     "execution; post_only must be true")

    n = _dec(notional_usd)
    me = _dec(market_exposure_usd)
    ae = _dec(aggregate_exposure_usd)
    day = _dec(day_realized_usd)
    if n is None or me is None or ae is None or day is None:
        return no("EXPOSURE_UNREADABLE")
    if n <= ZERO:
        return no("NOTIONAL_NOT_POSITIVE")
    if me < ZERO or ae < ZERO:
        return no("EXPOSURE_NEGATIVE")
    if me + n > scope["max_per_market_usd"]:
        return no("PER_MARKET_EXPOSURE", market_usd=str(me),
                  adding=str(n), cap_usd=str(scope["max_per_market_usd"]))
    if ae + n > scope["max_aggregate_usd"]:
        return no("AGGREGATE_EXPOSURE", aggregate_usd=str(ae),
                  adding=str(n), cap_usd=str(scope["max_aggregate_usd"]))

    #: THE WORST CASE AGAINST THE DAILY HALT. Losses already realized
    #: today PLUS everything currently at risk PLUS this order, because a
    #: halt that only counts settled losses lets open positions carry the
    #: account through it.
    worst = (-day if day < ZERO else ZERO) + ae + n
    if worst > scope["max_daily_loss_usd"]:
        return no("WORST_CASE_DAILY_LOSS",
                  realized_today_usd=str(day), at_risk_usd=str(ae),
                  adding=str(n), worst_case_usd=str(worst),
                  halt_usd=str(scope["max_daily_loss_usd"]))
    return {"ok": True, "reason": None, "state": cur,
            "strategy": strat, "worst_case_usd": str(worst),
            "envelope_digest": scope["digest"]}


# ================================================ the owner's arming ceremony

#: `why` recorded on an ARMED transition, so the audit trail names the
#: mechanism rather than only the outcome.
ARM_WHY = "OWNER_CEREMONY"


def arm_digest(raw_envelope: Any, raw_campaign: Any) -> str:
    """The identity of ONE approved campaign under ONE envelope.

    Stored on the state record, so an already-ARMED lane can tell the
    campaign it was armed for from a different one. Both documents are
    included because either changing is a different authorization.
    """
    return digest("%s\n%s" % (str(raw_envelope or "").strip(),
                              str(raw_campaign or "").strip()))


def enter_armed(*, raw_campaign: Any, first_window_utc: Any, not_after_utc: Any,
        now: Any, raw_envelope: Any = None, deployment: Any = None,
        path: Optional[str] = None) -> Dict[str, Any]:
    """MONITOR -> ARMED, and ONLY from the owner's approved documents.

    NOT CALLED `arm`, deliberately. `l5_btc5m_runner` holds an invariant
    that exactly ONE `.arm()` appears in it, inside
    `arm_through_boundary` and after `AUTH.authorize_arming` -- that is
    the TRANSPORT arming boundary, and a test walks the AST to keep it
    single. This is a different operation: a durable state transition,
    not permission to talk to a venue. Sharing the name would have made
    that invariant ambiguous, and an ambiguous safety invariant is worse
    than none.

    WHY THIS EXISTS. `may_submit` has always required the durable state
    to be ARMED, and nothing ever wrote it: the only `write_state`
    callers write STOPPED and HALTED. So setting an envelope made a lane
    unable to submit at all -- permanently, because MONITOR is the birth
    state and no path left it. The gate was correct and unreachable.

    WHAT IT IS NOT. It is not a second arming mechanism. The owner's
    authorization still travels as the two documents the existing
    ceremony already sets -- the standing envelope and the campaign
    approval -- and every limit in them is re-derived here rather than
    restated. This only makes the durable transition those documents
    were always meant to cause.

    FAIL CLOSED AT EVERY STEP, and the order is deliberate:

      - the envelope must parse for THIS deployment and be unexpired.
        `parse_envelope` already clamps the three ceilings to the
        compiled maxima and refuses a missing one, so a campaign cannot
        be armed above them;
      - the campaign must be approved, name a series the envelope
        authorizes, and declare limits no looser than the envelope's;
      - its first window must still be AHEAD. An expired or
        part-consumed campaign is never armed, because arming mid-flight
        would authorize windows whose state nobody has audited;
      - its end must not outlive the envelope;
      - A LATCH IS NEVER WALKED PAST. HALTED and STOPPED refuse here as
        they refuse everywhere; this cannot rearm them, cannot clear
        them, and touches no loss accounting.

    IDEMPOTENT FOR THE IDENTICAL CAMPAIGN AND NOTHING ELSE. A restart
    re-runs this and finds ARMED with the same digest, so it proceeds;
    a different envelope or a different approval finds ARMED with
    another digest and REFUSES rather than silently re-arming into a
    campaign the owner did not authorize twice.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason, state=None, armed=False)

    at = _utc(now)
    if at is None:
        return no("NOW_UNREADABLE")

    raw_env = (raw_envelope if raw_envelope is not None
               else os.environ.get(ENVELOPE_ENV))
    dep = (deployment if deployment is not None
           else os.environ.get(DEPLOYMENT_ENV))
    env = parse_envelope(raw_env, deployment=dep, now=at)
    if not env.get("ok"):
        return no("ENVELOPE:%s" % env.get("reason"))
    scope = env["scope"]

    #: THE CAMPAIGN DOCUMENT, read as the owner issued it.
    doc: Any = raw_campaign
    if isinstance(doc, (str, bytes)):
        if not str(doc).strip():
            return no("CAMPAIGN_ABSENT")
        try:
            doc = json.loads(doc)
        except (ValueError, TypeError):
            return no("CAMPAIGN_UNPARSEABLE")
    if not isinstance(doc, Mapping):
        return no("CAMPAIGN_NOT_AN_OBJECT")
    if doc.get("approved") is not True:
        return no("CAMPAIGN_NOT_APPROVED")
    nonce = str(doc.get("nonce") or "").strip()
    if len(nonce) < 8:
        return no("CAMPAIGN_NONCE_TOO_SHORT")
    series = str(doc.get("series") or "").strip()
    if not series:
        return no("CAMPAIGN_NAMES_NO_SERIES")
    if series not in scope["series"]:
        return no("CAMPAIGN_SERIES_NOT_IN_ENVELOPE", series=series[:40],
                  envelope=list(scope["series"]))

    #: THE CAMPAIGN MAY NOT BE LOOSER THAN THE ENVELOPE. Comparing rather
    #: than restating: a figure copied here could drift from the document
    #: that actually authorized it.
    tot = _dec(doc.get("max_total_exposure_usd"))
    loss = _dec(doc.get("max_daily_loss_usd"))
    if tot is None or loss is None:
        return no("CAMPAIGN_LIMITS_UNREADABLE")
    if tot > scope["max_aggregate_usd"]:
        return no("CAMPAIGN_EXPOSURE_ABOVE_ENVELOPE",
                  campaign=str(tot), envelope=str(scope["max_aggregate_usd"]))
    if loss > scope["max_daily_loss_usd"]:
        return no("CAMPAIGN_DAILY_LOSS_ABOVE_ENVELOPE",
                  campaign=str(loss),
                  envelope=str(scope["max_daily_loss_usd"]))

    #: WINDOWS: ahead of us, and inside the envelope's life.
    first = _utc(first_window_utc)
    end = _utc(not_after_utc)
    if first is None or end is None:
        return no("CAMPAIGN_WINDOWS_UNREADABLE")
    if end <= first:
        return no("CAMPAIGN_WINDOWS_INVERTED")
    if first < at:
        return no("CAMPAIGN_ALREADY_STARTED",
                  first_window_utc=first.isoformat(), now=at.isoformat())
    exp = scope.get("expires_utc")
    if exp:
        env_exp = _utc(exp)
        if env_exp is None:
            return no("ENVELOPE_EXPIRY_UNREADABLE")
        if end > env_exp:
            return no("CAMPAIGN_OUTLIVES_ENVELOPE",
                      campaign_end=end.isoformat(),
                      envelope_expires=env_exp.isoformat())

    want = arm_digest(raw_env, raw_campaign)

    st = read_state(path)
    if not st.get("ok"):
        return no("STATE:%s" % st.get("reason"))
    cur = st["state"]
    if cur in LATCHED:
        #: A LATCH IS THE ONE THING THIS MUST NEVER UNDO.
        return no("STATE_%s" % cur, state_was=cur,
                  note="latched; arming cannot clear a halt or a stop")
    if cur == ARMED:
        #: `read_state` nests everything but `state` under `detail`, so the
        #: digest lives there -- reading it off the top level would compare
        #: "" against the wanted value and refuse every restart as if it
        #: were a different campaign.
        _seen = str((st.get("detail") or {}).get("latch_digest") or "")
        if _seen == want:
            return {"ok": True, "reason": None, "state": ARMED,
                    "armed": True, "already": True, "digest": want,
                    "scope": scope, "campaign_nonce": nonce}
        return no("ARMED_FOR_ANOTHER_CAMPAIGN",
                  state_was=ARMED, note="a different envelope or approval "
                                        "is not a restart")
    if cur != MONITOR:
        return no("STATE_NOT_MONITOR", state_was=cur)

    out = write_state(
        ARMED, now=at, path=path, latch_digest=want,
        why="%s dep=%s series=%s nonce=%s %s..%s" % (
            ARM_WHY, str(dep)[:40], series, nonce[:32],
            first.isoformat(), end.isoformat()))
    if not out.get("ok"):
        return no("STATE_NOT_WRITTEN:%s" % out.get("reason"))
    return {"ok": True, "reason": None, "state": ARMED, "armed": True,
            "already": False, "digest": want, "scope": scope,
            "campaign_nonce": nonce, "written": out.get("written")}
