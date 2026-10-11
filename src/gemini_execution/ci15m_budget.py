"""The bounded BTC15m CONTINUOUS-INVENTORY probe: durable windows and slots.

WHAT THIS BOUNDS. How many times we may touch the venue, and it survives a
process death. It does NOT decide anything: every decision comes from the
frozen policy in `analysis/gemini/info_edge/btc15m_shadow_policy.py`, which
is imported and never reimplemented.

WHAT THIS PROBE IS FOR. To validate that OPEN -> EXTEND -> COMPLEMENT -> LOCK
EXECUTES CORRECTLY against the real BTC15m venue, with actual fills and
authoritative inventory reconciliation. It is an execution-mechanics audit.
It CANNOT and MUST NOT be read as evidence that EXTEND is profitable: three
windows of q=1 cannot distinguish any edge from zero, and the prospective
EXTEND-vs-NO-EXTEND shadow is kept entirely separate from it.

THE OWNER'S BOUNDS, and where each one lives:

  one BTC15m market at a time         `open_window()` refuses a second
  q = 1 per action                    QUANTITY; checked in `consume_slot`
  OPEN + 2 EXTENDs = 3 unmatched      MAX_UNMATCHED_SHARES
  max acquisition price 0.90          MAX_ACQUISITION_PRICE (= policy's
                                      HARD_MAX_PRICE, imported, not restated)
  max unmatched principal $2.70       MAX_UNMATCHED_PRINCIPAL, derived
  max 2 EXTENDs                       the frozen policy's own
                                      MAX_EXTENDS_BEFORE_COMPLEMENT
  max 6 submitted orders per window   ORDERS_PER_WINDOW
  complements only toward LOCK        the frozen policy's `want_complement`
  one outstanding order               checked against the VENUE, in the driver
  ambiguous consumes its slot         the row is fsynced BEFORE the send
  restart recovers consumed slots     every count is READ FROM DISK
  $2.00 confirmed realized loss stop  `settled.jsonl`, confirmed cashflow only
  3 completed live windows            WINDOW_BUDGET
  no automatic increase               no function here raises any bound

WHY A TORN ROW COUNTS AS CONSUMED. If the process dies mid-write the order
may or may not have reached the venue. Counting it spent can waste one slot;
counting it free can send an extra order. `cohort._consumed_in` already makes
that choice correctly and is reused rather than re-derived.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.gemini_execution import cohort
from src.gemini_execution.continuous_probe import MAX_ACTIONS as CP_MAX_ACTIONS
from src.gemini_execution.ci15m_policy import POLICY

LABEL = "GEMINI_CI15M_BUDGET_V1"

#: completed live windows. There is no replenishment.
WINDOW_BUDGET = 3

#: submitted orders per window. OPEN + 2 EXTENDs + up to 3 complement
#: attempts; a maker-or-cancel CANCELLATION still spends one.
ORDERS_PER_WINDOW = 6

# ---------------------------------------------------------------- profiles
#
# CI15M-2 STARTS SMALLER, DELIBERATELY. CI15M-1 had a 12-order budget and
# spent all of it without once reaching an EXTEND or a COMPLEMENT, because
# the executor could not see its own fills. There is no point buying a wider
# budget until the mechanics are proven, so the first CI15M-2 cohort exists
# to prove exactly one chain:
#
#   OPEN fill -> terminal detection -> authoritative reconciliation ->
#   correct next inventory decision
#
# A CANCELLED OR UNFILLED OPEN ENDS THAT MARKET'S EXPERIMENT. The three
# slots are not three attempts at an OPEN; they are OPEN plus two
# subsequent INVENTORY actions. `may_open` already forbids a second OPEN
# intent, so the budget cannot be spent retrying one.
MECHANICS = "MECHANICS"
#: CI15M-4. TWO windows, because OPEN is no longer the experiment: we need
#: enough opportunity to observe at least one POST-FILL state transition
#: without changing the policy to force one. Still tiny: 6 orders total,
#: 3 per window, q=1.
MECHANICS2 = "MECHANICS2"
FULL = "FULL"

#: PERSIST4 is the PERSISTENT-ROLLOVER envelope: four consecutive windows,
#: two submissions each, eight in total. It is TIGHTER per window than any
#: earlier profile (2, not 3 or 6) because the question it answers is
#: whether the executor survives four rollovers, not how much it can trade.
PERSIST4 = "PERSIST4"

PROFILES = {
    MECHANICS: {"windows": 1, "orders_total": 3, "orders_per_window": 3},
    MECHANICS2: {"windows": 2, "orders_total": 6, "orders_per_window": 3},
    PERSIST4: {"windows": 4, "orders_total": 8, "orders_per_window": 2},
    FULL: {"windows": 3, "orders_total": 12, "orders_per_window": 6},
}

#: THE TOTAL ORDER CEILING, AND IT IS NOT MINE TO SET. The pre-existing live
#: envelope caps mutations at `continuous_probe.MAX_ACTIONS`, and
#: `cohort.create` refuses a budget above it. 3 windows x 6 orders would be
#: 18, so the envelope binds BEFORE the owner's per-window cap does, and it is
#: honoured rather than widened: a window may still spend its 6, but the probe
#: as a whole cannot exceed this. A full 6-order window therefore leaves 6 for
#: the remaining two. This is TIGHTER than what was authorized, deliberately.
TOTAL_ORDER_BUDGET = min(WINDOW_BUDGET * ORDERS_PER_WINDOW, CP_MAX_ACTIONS)

#: q is fixed. Not a parameter, not a minimum, not a starting point.
QUANTITY = D("1")

#: IMPORTED from the frozen policy rather than restated, so the two can never
#: drift apart. A local copy of this number would be a second policy.
MAX_ACQUISITION_PRICE = POLICY["HARD_MAX_PRICE"]            # 0.90
MAX_EXTENDS = POLICY["MAX_EXTENDS_BEFORE_COMPLEMENT"]       # 2
MAX_PAIR_COST = POLICY["MAX_PAIR_COST"]                     # 0.96

#: OPEN + MAX_EXTENDS, all unmatched in the worst case.
MAX_UNMATCHED_SHARES = QUANTITY * (1 + MAX_EXTENDS)         # 3
MAX_UNMATCHED_PRINCIPAL = MAX_UNMATCHED_SHARES * MAX_ACQUISITION_PRICE  # 2.70

#: the venue's taker fee formula peaks at P=0.5: ceil(0.07*1*0.25) = 0.02.
MAX_FEE_PER_ORDER = D("0.02")
MAX_FEES_PER_WINDOW = MAX_FEE_PER_ORDER * ORDERS_PER_WINDOW            # 0.12

#: the structural ceiling, DERIVED. Stated per window and in total; the
#: realized-loss stop below bites well before the total.
STRUCTURAL_MAX_LOSS_PER_WINDOW = MAX_UNMATCHED_PRINCIPAL + MAX_FEES_PER_WINDOW
STRUCTURAL_MAX_LOSS = STRUCTURAL_MAX_LOSS_PER_WINDOW * WINDOW_BUDGET   # 8.46

#: the stop that bites first, on CONFIRMED realized principal loss only.
REALIZED_LOSS_STOP = D("2.00")

#: C -- THE OPEN LIFECYCLE, DURABLE AND PER MARKET.
#:
#: CI15M-1 submitted SIX OPENs into one market because the only thing
#: stopping a second OPEN was `if inv.fills: continue` in the policy -- a
#: test on whether an OPEN had FILLED, not on whether one had been
#: ATTEMPTED. Nothing filled, so inventory stayed flat, so the policy kept
#: returning OPEN and the loop kept re-submitting at the re-read ask (0.70,
#: 0.70, 0.70, 0.71, 0.73, 0.72). There was no chase logic; the chase was
#: emergent, and that is worse, because nothing named it.
#:
#: A market now gets ONE OPEN INTENT. A cancellation does NOT return it to
#: never-attempted.
OPEN_NOT_ATTEMPTED = "OPEN_NOT_ATTEMPTED"
OPEN_SUBMITTED = "OPEN_SUBMITTED"
OPEN_CANCELLED = "OPEN_CANCELLED"
OPEN_FILLED = "OPEN_FILLED"
OPEN_STATES = (OPEN_NOT_ATTEMPTED, OPEN_SUBMITTED, OPEN_CANCELLED,
               OPEN_FILLED)

WINDOWS = "windows.jsonl"
SETTLED = "settled.jsonl"
EXITS = "exits.jsonl"
TELEMETRY = "telemetry.jsonl"
HALT = "HALT"

#: the key an intent must carry to prove its inventory was reconciled
#: against the venue. An intent without it is REFUSED: CI15M-1's caps were
#: evaluated against a locally inferred, and wrong, fill list.
RECONCILED_KEY = "reconciled_venue_inventory"

#: the statuses this probe reports, in order. Nothing skips forward and
#: nothing later is claimed on the evidence of something earlier.
STATUSES = ("SUBMITTED", "FILLED", "INVENTORY_RECONCILED",
            "COMPLEMENTED", "RESIDUAL", "SETTLED", "REALIZED")

#: every condition that stops the probe dead. A HALT is never cleared by
#: code; a human removes the file.
HALT_CONDITIONS = {
    "INVENTORY_DISAGREEMENT": "our inventory and the venue's authoritative "
                              "position for this symbol differ",
    "SIDE_MAPPING_DISAGREEMENT": "the venue's eventOutcome for a fill does "
                                 "not map to the leg we intended",
    "FILL_ABOVE_LIMIT": "a fill printed at a price worse than the limit we "
                        "submitted",
    "UNKNOWN_POSITION": "the venue reports a position we have no record of, "
                        "or we cannot read the position at all",
    "SEMANTIC_CONTRADICTION": "the venue's answer contradicts what our "
                              "parser asserted about the book or the order",
    "POLICY_FINGERPRINT_MISMATCH": "the frozen policy bytes are not the ones "
                                   "this authorization was granted against",
    "QUANTITY_NOT_ONE": "a submission was constructed at a quantity other "
                        "than exactly 1",
    "EXTEND_BUDGET_EXCEEDED": "a third EXTEND was reached before a complement",
    "LOCKED_MUTATION": "an action was proposed on an inventory that is "
                       "already LOCKED",
    "UNATTRIBUTABLE_FILL": "the venue reports a fill on this symbol that "
                           "matches no authorized submission in this "
                           "cohort's durable ledger",
}


class BudgetRefused(RuntimeError):
    """A bound said no. Never retried, never relaxed, never widened."""


class Halted(RuntimeError):
    """A HALT file is present. Only a human clears it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_durable(path: str, row: Dict[str, Any]) -> None:
    """Append one row and FSYNC IT, file and directory. The caller may only
    touch the venue after this returns, so a crash leaves the slot SPENT."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(row, sort_keys=True, default=str) + "\n"
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    d = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(d)
    finally:
        os.close(d)


def _paths(cohort_id: str, root: str) -> Dict[str, str]:
    d = cohort.directory(cohort_id, root=root)
    return {"dir": d,
            "ledger": os.path.join(d, cohort.LEDGER),
            "windows": os.path.join(d, WINDOWS),
            "settled": os.path.join(d, SETTLED),
            "exits": os.path.join(d, EXITS),
            "telemetry": os.path.join(d, TELEMETRY),
            "halt": os.path.join(d, HALT)}


def _rows(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue            # a torn row is still a spent slot
    except OSError:
        pass
    return out


# ------------------------------------------------------------------- HALT
def halted(cohort_id: str, *, root: str = cohort.COHORT_ROOT) -> Optional[str]:
    """The HALT reason, or None. AN UNREADABLE HALT FILE IS A HALT."""
    p = _paths(cohort_id, root)["halt"]
    if not os.path.exists(p):
        return None
    try:
        return open(p, "r", encoding="utf-8").read().strip() or "HALTED"
    except OSError:
        return "HALTED (halt file present but unreadable -- fail closed)"


def halt(cohort_id: str, condition: str, detail: Any = None, *,
         root: str = cohort.COHORT_ROOT) -> str:
    """Write the HALT. Idempotent, durable, and never cleared by code."""
    if condition not in HALT_CONDITIONS:
        condition = "SEMANTIC_CONTRADICTION"
    p = _paths(cohort_id, root)
    os.makedirs(p["dir"], exist_ok=True)
    msg = "%s %s: %s | detail=%s" % (_now(), condition,
                                     HALT_CONDITIONS[condition], detail)
    fd = os.open(p["halt"], os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, (msg + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return msg


# ------------------------------------------------------------- the counts
def profile_of(cohort_id: str, *,
               root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """The bounds this cohort was CREATED with, read from its manifest.

    A profile is chosen once, by the human who creates the cohort, and is
    then read from disk like every other bound. Nothing here can widen one.
    """
    try:
        note = str(cohort.load(cohort_id, root=root).note or "")
    except Exception:                                          # noqa: BLE001
        note = ""
    # DERIVED FROM `PROFILES`, NEVER HARDCODED, and longest-first because
    # one profile name can be a prefix of another (MECHANICS / MECHANICS2).
    #
    # THE DEFECT THIS REPLACES was a hardcoded (MECHANICS2, MECHANICS)
    # list. PERSIST4 was added to PROFILES and not to that list, so a
    # cohort whose manifest plainly said `profile=PERSIST4` resolved to
    # FULL -- THE WIDEST PROFILE -- and the four-window shadow run spent 3
    # submissions in its first window against a 2-per-window envelope.
    # Live, that would have been 6 per window and 12 total instead of the
    # authorized 2 and 8: a risk-envelope breach produced by a reader that
    # could not see a bound the writer had recorded.
    #
    # An UNRECOGNISED explicit profile now REFUSES rather than widening. A
    # cohort with no tag at all is a pre-existing one -- `create` has
    # always appended the tag -- and keeps the historical FULL reading so
    # closed cohorts still read as they did.
    name = None
    for cand in sorted(PROFILES, key=len, reverse=True):
        if ("profile=" + cand) in note:
            name = cand
            break
    if name is None:
        if "profile=" in note:
            raise BudgetRefused(
                "cohort %r names a profile this build does not know (%r). "
                "Refusing rather than falling back to a wider envelope."
                % (cohort_id, note.strip()))
        name = FULL
    out = dict(PROFILES[name])
    out["name"] = name
    # the pre-existing envelope still binds, and is never raised
    out["orders_total"] = min(out["orders_total"], CP_MAX_ACTIONS)
    return out


def create(cohort_id: str, *, root: str = cohort.COHORT_ROOT,
           note: str = "", profile: str = FULL) -> Dict[str, Any]:
    """Bring the cohort into being ONCE. A second call is a refusal."""
    if profile not in PROFILES:
        raise BudgetRefused("unknown profile %r; expected one of %s"
                            % (profile, sorted(PROFILES)))
    note = (note or "") + " profile=%s" % profile
    cohort.create(cohort_id, PROFILES[profile]["orders_total"], root=root,
                  note=note or ("BTC15m CONTINUOUS-INVENTORY EXECUTION "
                                "VALIDATION ONLY. Not a profitability "
                                "estimate and never pooled with the "
                                "prospective EXTEND shadow."))
    return state(cohort_id, root=root)


def consumed(cohort_id: str, *, root: str = cohort.COHORT_ROOT) -> int:
    """Order slots spent on THIS disk, read every time. Never cached.

    NOT SUFFICIENT ON ITS OWN. A fresh Actions runner has an empty disk, so
    this returns 0 for a cohort that has already spent every slot. Use
    `durable_consumed`, which is what every bound below actually consults.
    """
    return cohort._consumed_in(_paths(cohort_id, root)["ledger"])


LATCH_REF_PREFIX = "refs/heads/ci15m-latch/"


def latch_slots(cohort_id: str, *, remote: str = "origin",
                timeout: float = 60.0) -> Dict[str, Any]:
    """Slots this cohort has spent ACCORDING TO GIT, which outlives the host.

    THIS IS THE FIX FOR THE WORST DEFECT CI15M-1 EXPOSED. The slot hook
    pushes `refs/heads/ci15m-latch/<cohort>-slot<N>` before the venue is
    touched, and the refs survived -- but nothing ever read them back. The
    restart recovery that was tested covered a process dying on the SAME
    filesystem; a NEW runner starts with an empty disk, counts zero consumed
    slots and would happily spend the budget again. A second dispatch of an
    exhausted cohort would therefore have sent twelve more orders.

    `readable` False means the refs could not be listed. That is NOT zero:
    callers must fail closed on it, and `durable_consumed` does.
    """
    import subprocess
    pat = "%s%s-slot*" % (LATCH_REF_PREFIX, cohort_id)
    try:
        r = subprocess.run(["git", "ls-remote", remote, pat],
                           capture_output=True, text=True, timeout=timeout)
    except Exception as exc:                                   # noqa: BLE001
        return {"readable": False, "why": "git ls-remote failed: %s" % exc,
                "slots": [], "count": None}
    if r.returncode != 0:
        return {"readable": False,
                "why": "git ls-remote rc=%d: %s" % (r.returncode,
                                                    (r.stderr or "")[-200:]),
                "slots": [], "count": None}
    slots = []
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        ref = parts[1]
        tail = ref.rsplit("-slot", 1)
        if len(tail) != 2:
            continue
        try:
            slots.append(int(tail[1]))
        except ValueError:
            continue
    return {"readable": True, "slots": sorted(set(slots)),
            "count": len(set(slots)), "why": None, "ref_pattern": pat}


def durable_consumed(cohort_id: str, *, root: str = cohort.COHORT_ROOT,
                     remote: Optional[str] = "origin") -> Dict[str, Any]:
    """The authoritative consumed-slot count: the MAXIMUM of this disk and
    git. Fails closed when git cannot be read.

    `remote=None` disables the git read and is for tests and offline
    inspection ONLY -- `may_submit` refuses when the git side is unreadable,
    so a live path cannot reach the venue on a local count alone.
    """
    local = consumed(cohort_id, root=root)
    if remote is None:
        return {"consumed": local, "local": local, "latch": None,
                "readable": None,
                "why": "git side not consulted (remote=None)"}
    lat = latch_slots(cohort_id, remote=remote)
    if not lat["readable"]:
        return {"consumed": None, "local": local, "latch": lat,
                "readable": False,
                "why": "the durable latch refs could not be read, and an "
                       "unreadable budget is never an empty one: %s"
                       % lat["why"]}
    n = max(local, lat["count"])
    return {"consumed": n, "local": local, "latch": lat, "readable": True,
            "why": None,
            "note": ("the maximum of this disk and git. A fresh runner has "
                     "local=0 and must still see the spent slots.")}


def submissions_in_window(cohort_id: str, ticker: str, *,
                          root: str = cohort.COHORT_ROOT) -> int:
    """Orders submitted into THIS market, read from disk."""
    return sum(1 for r in _rows(_paths(cohort_id, root)["ledger"])
               if r.get("kind") == "PLACE" and r.get("ticker") == ticker)


def windows(cohort_id: str, *,
            root: str = cohort.COHORT_ROOT) -> Dict[str, str]:
    """Every window this probe has opened, and its disposition."""
    out: Dict[str, str] = {}
    for r in _rows(_paths(cohort_id, root)["windows"]):
        if r.get("ticker"):
            out[r["ticker"]] = r.get("outcome", "?")
    return out


def open_window_ticker(cohort_id: str, *,
                       root: str = cohort.COHORT_ROOT) -> Optional[str]:
    """The one market currently open, or None. ONE AT A TIME."""
    for tk, outcome in windows(cohort_id, root=root).items():
        if outcome == "OPEN":
            return tk
    return None


#: a window we merely LOOKED at. It consumed no slot, nothing was sent, and
#: it must NOT consume the one-window budget. CI15M-2 opened
#: BTC15M2610031845, halted on an adapter defect before any submission, and
#: closed NO_FILL -- which counted as a completed window and would have made
#: a retry refuse at "WINDOW BUDGET reached". A cancelled or unfilled OPEN
#: still ends that market's experiment; a window with ZERO submissions is a
#: different thing and is not an experiment at all.
NOT_ATTEMPTED = "NOT_ATTEMPTED"

#: window dispositions that do NOT count against the window budget
UNCOUNTED_WINDOWS = ("OPEN", NOT_ATTEMPTED)


def completed_windows(cohort_id: str, *,
                      root: str = cohort.COHORT_ROOT) -> int:
    """Windows that actually ran an experiment. A window nothing was
    submitted into is not one of them."""
    return sum(1 for v in windows(cohort_id, root=root).values()
               if v not in UNCOUNTED_WINDOWS)


def realized_principal_loss(cohort_id: str, *,
                            root: str = cohort.COHORT_ROOT) -> D:
    """CONFIRMED realized principal loss, as a positive number.

    Only rows whose cashflow is confirmed from an AUTHENTICATED account read
    count. A resolution side is not cash; a mark is not cash; a
    formula-derived fee is not cash. Unconfirmed contributes ZERO, which is
    safe only because the structural ceiling bounds the rest independently.
    """
    tot = D("0")
    for r in _rows(_paths(cohort_id, root)["settled"]):
        if not r.get("cashflow_confirmed"):
            continue
        try:
            net = D(str(r["realized_principal_net"]))
        except Exception:                                      # noqa: BLE001
            continue
        if net < 0:
            tot += -net
    return tot


def expired(cohort_id: str, *, root: str = cohort.COHORT_ROOT,
            remote: Optional[str] = "origin") -> Optional[str]:
    """Why this probe is over, or None. Checked before every submission."""
    h = halted(cohort_id, root=root)
    if h:
        return "HALTED: %s" % h
    prof = profile_of(cohort_id, root=root)
    dur = durable_consumed(cohort_id, root=root, remote=remote)
    if dur["readable"] is False:
        return "DURABLE_BUDGET_UNREADABLE: %s" % dur["why"]
    used = dur["consumed"]
    if used >= prof["orders_total"]:
        return ("ORDER_BUDGET_EXHAUSTED: %d of %d order slots consumed "
                "(profile %s; disk %d, latch refs %s). There is no "
                "replenishment."
                % (used, prof["orders_total"], prof["name"], dur["local"],
                   (dur["latch"] or {}).get("count")))
    if used >= TOTAL_ORDER_BUDGET:
        return ("TOTAL_ORDER_BUDGET_EXHAUSTED: %d of %d order slots "
                "consumed (disk %d, latch refs %s). This is the pre-existing "
                "live envelope's ceiling and is not raised here."
                % (used, TOTAL_ORDER_BUDGET, dur["local"],
                   (dur["latch"] or {}).get("count")))
    done = completed_windows(cohort_id, root=root)
    if done >= prof["windows"]:
        return ("WINDOW_BUDGET_EXHAUSTED: %d of %d live windows completed "
                "(profile %s). There is no replenishment."
                % (done, prof["windows"], prof["name"]))
    # THE SECOND CHECK IS GONE, AND IT IS WHY THE FOUR-WINDOW PROOF COULD
    # NEVER PASS. It re-tested the MODULE CONSTANT `WINDOW_BUDGET` (3)
    # after the profile check above had already allowed PERSIST4's 4, so a
    # correctly configured 4-window cohort stopped at 3 with
    # "WINDOW_BUDGET_EXHAUSTED: 3 of 3 live windows completed" -- measured
    # in run 37169250593, whose only failing acceptance check was
    # `stayed_alive_across_windows` (3 seen, 4 required). Every other
    # check passed.
    #
    # This is the same failure as `profile_of`: a bound read from a module
    # constant instead of from the cohort's own profile. It bound TIGHTER
    # than authorized (3 < 4) so nothing unsafe happened, and that is
    # exactly why it was invisible until a cohort asked for more than the
    # old default.
    #
    # `prof["windows"]` is now the SINGLE source for the window ceiling.
    # WINDOW_BUDGET remains the FULL profile's value and the basis of the
    # legacy derived constants below; it is no longer a second gate.
    loss = realized_principal_loss(cohort_id, root=root)
    if loss >= REALIZED_LOSS_STOP:
        return ("REALIZED_LOSS_STOP: confirmed realized principal loss $%s "
                ">= $%s." % (loss, REALIZED_LOSS_STOP))
    return None


def open_window(cohort_id: str, ticker: str, *,
                root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Bind the ONE market. Refuses while another window is open."""
    why = expired(cohort_id, root=root)
    if why:
        raise BudgetRefused(why)
    cur = open_window_ticker(cohort_id, root=root)
    if cur is not None:
        raise BudgetRefused(
            "ONE_MARKET_AT_A_TIME: %s is still open; %s is refused"
            % (cur, ticker))
    seen = windows(cohort_id, root=root)
    if ticker in seen:
        raise BudgetRefused(
            "WINDOW_ALREADY_USED: %s was already dispositioned as %s and is "
            "never revisited" % (ticker, seen[ticker]))
    row = {"utc": _now(), "ticker": ticker, "outcome": "OPEN"}
    _append_durable(_paths(cohort_id, root)["windows"], row)
    return row


def close_window(cohort_id: str, ticker: str, outcome: str, detail: Any = None,
                 *, root: str = cohort.COHORT_ROOT) -> None:
    """Disposition the window. Consumes NO order slot."""
    if outcome == "OPEN":
        raise BudgetRefused("close_window cannot reopen a window")
    _append_durable(_paths(cohort_id, root)["windows"],
                    {"utc": _now(), "ticker": ticker, "outcome": outcome,
                     "detail": detail})


def open_state(cohort_id: str, ticker: str, *,
               root: str = cohort.COHORT_ROOT) -> str:
    """Has this market had its ONE OPEN intent? Derived from the durable
    ledger, so a restart cannot forget that an OPEN was attempted.

    A CANCELLATION DOES NOT RETURN THE MARKET TO NEVER-ATTEMPTED. That is
    the entire point: CI15M-1 re-opened six times because the only state it
    consulted was "did an OPEN fill".
    """
    rows = _rows(_paths(cohort_id, root)["ledger"])
    slots = {r["slot"] for r in rows
             if r.get("kind") == "PLACE" and r.get("ticker") == ticker
             and (r.get("intent") or {}).get("action") == "OPEN"}
    if not slots:
        return OPEN_NOT_ATTEMPTED
    state = OPEN_SUBMITTED
    for r in rows:
        if r.get("kind") != "STATUS" or r.get("slot") not in slots:
            continue
        if r.get("status") == "FILLED":
            return OPEN_FILLED
        b = ((r.get("detail") or {}).get("venue") or {}).get("body") or {}
        if b.get("cancelledAt") or str(b.get("status", "")).lower() \
                == "cancelled":
            state = OPEN_CANCELLED
    return state


def may_open(cohort_id: str, ticker: str, *,
             root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """ONE OPEN INTENT PER MARKET, not one open FILL."""
    st = open_state(cohort_id, ticker, root=root)
    if st == OPEN_NOT_ATTEMPTED:
        return {"ok": True, "open_state": st}
    return {"ok": False, "open_state": st,
            "why": ("OPEN_ALREADY_ATTEMPTED: %s is in state %s. A market "
                    "gets one OPEN intent; a cancellation does not reset it "
                    "to never-attempted, and the price is never re-offered."
                    % (ticker, st))}


def may_submit(cohort_id: str, ticker: str, *,
               root: str = cohort.COHORT_ROOT,
               action: Optional[str] = None,
               remote: Optional[str] = "origin") -> Dict[str, Any]:
    """Every budget bound, answered before any venue write."""
    why = expired(cohort_id, root=root, remote=remote)
    if why:
        return {"ok": False, "why": why}
    cur = open_window_ticker(cohort_id, root=root)
    if cur != ticker:
        return {"ok": False,
                "why": "MARKET_NOT_OPEN: the open window is %r, not %s"
                       % (cur, ticker)}
    if str(action) == "OPEN":
        g = may_open(cohort_id, ticker, root=root)
        if not g["ok"]:
            return {"ok": False, "why": g["why"]}
    prof = profile_of(cohort_id, root=root)
    n = submissions_in_window(cohort_id, ticker, root=root)
    if n >= prof["orders_per_window"]:
        return {"ok": False,
                "why": "ORDERS_PER_WINDOW: %d of %d already submitted into "
                       "%s (profile %s)"
                       % (n, prof["orders_per_window"], ticker,
                          prof["name"])}
    if n >= ORDERS_PER_WINDOW:
        return {"ok": False,
                "why": "ORDERS_PER_WINDOW: %d of %d already submitted into "
                       "%s" % (n, ORDERS_PER_WINDOW, ticker)}
    return {"ok": True, "slot": consumed(cohort_id, root=root),
            "submitted_in_window": n,
            "window_orders_remaining": ORDERS_PER_WINDOW - n}


def consume_slot(cohort_id: str, ticker: str, intent: Dict[str, Any], *,
                 root: str = cohort.COHORT_ROOT,
                 remote: Optional[str] = "origin") -> Dict[str, Any]:
    """Spend ONE numbered slot, durably, BEFORE the venue is touched.

    An AMBIGUOUS send therefore finds its slot already spent, which is the
    whole point: it is never retried. There is deliberately NO release,
    refund, unconsume or rollback function in this module.
    """
    gate = may_submit(cohort_id, ticker, root=root,
                      action=str(intent.get("action", "")), remote=remote)
    if not gate["ok"]:
        raise BudgetRefused(gate["why"])
    # 3 -- NO SUBMISSION ON UNRECONCILED INVENTORY. The caps below are
    # computed from the caller's inventory, so that inventory must have been
    # confirmed against the venue. CI15M-1 reached 4 unmatched shares and
    # $2.86 through a 3-share / $2.70 gate precisely because this did not
    # exist: the engine believed it was flat.
    if RECONCILED_KEY not in intent or intent[RECONCILED_KEY] is None:
        raise BudgetRefused(
            "UNRECONCILED_INVENTORY: the intent carries no %r stamp. Every "
            "cap here is computed from the caller's inventory, and an "
            "inventory that was not confirmed against the venue is not "
            "evidence." % RECONCILED_KEY)
    q = D(str(intent.get("quantity", "0")))
    if q != QUANTITY:
        raise BudgetRefused("q must be exactly %s; got %r"
                            % (QUANTITY, intent.get("quantity")))
    price = D(str(intent.get("price", "0")))
    action = str(intent.get("action", ""))
    if action in ("OPEN", "EXTEND"):
        if not (D("0") < price <= MAX_ACQUISITION_PRICE):
            raise BudgetRefused(
                "%s price %s is outside (0, %s]"
                % (action, price, MAX_ACQUISITION_PRICE))
        unmatched = D(str(intent.get("unmatched_shares_after", "0")))
        if unmatched > MAX_UNMATCHED_SHARES:
            raise BudgetRefused(
                "unmatched shares after this action would be %s > %s"
                % (unmatched, MAX_UNMATCHED_SHARES))
        principal = D(str(intent.get("unmatched_principal_after", "0")))
        if principal > MAX_UNMATCHED_PRINCIPAL:
            raise BudgetRefused(
                "unmatched principal after this action would be $%s > $%s"
                % (principal, MAX_UNMATCHED_PRINCIPAL))
    if action == "EXTEND":
        n = D(str(intent.get("extends_before", "0")))
        if n >= MAX_EXTENDS:
            raise BudgetRefused(
                "EXTEND_BUDGET: %s extensions already taken before a "
                "complement; the maximum is %s" % (n, MAX_EXTENDS))
    if action == "COMPLETE":
        pair = intent.get("pair_cost_after")
        if pair is None or D(str(pair)) > MAX_PAIR_COST:
            raise BudgetRefused(
                "COMPLEMENT pair cost after would be %s; the frozen "
                "discipline requires <= %s" % (pair, MAX_PAIR_COST))
    if action not in ("OPEN", "EXTEND", "COMPLETE"):
        raise BudgetRefused("unknown action %r; only OPEN, EXTEND and "
                            "COMPLETE may submit" % action)
    slot = gate["slot"]
    row = {"kind": "PLACE", "slot": slot, "utc": _now(),
           "cohort_id": cohort_id, "ticker": ticker,
           "status": "SUBMITTED", "intent": intent,
           "policy_fingerprint": POLICY["fingerprint"],
           "labels": ("fsynced BEFORE the venue sees anything, so a crash or "
                      "an ambiguous send leaves the slot SPENT rather than "
                      "free. It is never retried. SUBMITTED is not FILLED.")}
    _append_durable(_paths(cohort_id, root)["ledger"], row)
    return row


def record_status(cohort_id: str, ticker: str, *, slot: int, status: str,
                  detail: Any = None,
                  root: str = cohort.COHORT_ROOT) -> None:
    """Advance one slot's status. Each label is recorded SEPARATELY; nothing
    later is inferred from something earlier."""
    if status not in STATUSES:
        raise BudgetRefused("unknown status %r; expected one of %s"
                            % (status, list(STATUSES)))
    _append_durable(_paths(cohort_id, root)["ledger"],
                    {"kind": "STATUS", "slot": slot, "utc": _now(),
                     "ticker": ticker, "status": status, "detail": detail})


def record_settlement(cohort_id: str, ticker: str, *,
                      realized_principal_net: Any, cashflow_confirmed: bool,
                      evidence: Any, fee_observed: Any = None,
                      root: str = cohort.COHORT_ROOT) -> None:
    """Record settled cashflow. `cashflow_confirmed` must come from an
    AUTHENTICATED account read -- never from a resolution side."""
    _append_durable(_paths(cohort_id, root)["settled"],
                    {"utc": _now(), "ticker": ticker,
                     "realized_principal_net": str(realized_principal_net),
                     "cashflow_confirmed": bool(cashflow_confirmed),
                     "fee_observed": (None if fee_observed is None
                                      else str(fee_observed)),
                     "evidence": evidence})


#: every reason the probe may stop before submitting anything. A green
#: workflow that tells us nothing is itself a defect: CI15M-2 exited 0 in
#: seven seconds and the only way to learn why was the Actions log.
EXIT_REASONS = (
    "NO_ELIGIBLE_WINDOW_TIMEOUT",   # the deadline passed, never saw a market
    "WINDOW_BUDGET_REACHED",
    "COHORT_EXPIRED",
    "HALTED",
    "WINDOW_EXPIRED",
    "BOOK_UNREADABLE_AT_DEADLINE",
    "POLICY_HOLD_ALL_WINDOW",       # a legitimate, successful HOLD
    "SUBMITTED",                    # at least one order went out
    "NOT_LIVE",
)


def record_exit(cohort_id: str, reason: str, detail: Any = None, *,
                root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Write WHY the probe stopped, durably, whether or not it traded.

    Called on EVERY termination path, including the ones that send nothing.
    `reason` outside EXIT_REASONS is recorded rather than refused -- losing
    an unexpected reason is worse than recording an unexpected one.
    """
    row = {"utc": _now(), "kind": "EXIT", "reason": reason,
           "known_reason": reason in EXIT_REASONS, "detail": detail}
    _append_durable(_paths(cohort_id, root)["exits"], row)
    return row


def record_telemetry(cohort_id: str, row: Dict[str, Any], *,
                    root: str = cohort.COHORT_ROOT) -> None:
    """One durable row per DECISION, including every HOLD.

    A HOLD is a result, and the opposite side's executable ask at that
    moment is the datum that later answers "how long until the complement
    became reachable, and at what pair cost" -- which cannot be
    reconstructed after the fact. So it is written on every tick the engine
    decides on, not only when it acts.
    """
    _append_durable(_paths(cohort_id, root)["telemetry"], row)


def telemetry(cohort_id: str, *,
              root: str = cohort.COHORT_ROOT) -> List[Dict[str, Any]]:
    return _rows(_paths(cohort_id, root)["telemetry"])


def exits(cohort_id: str, *,
          root: str = cohort.COHORT_ROOT) -> List[Dict[str, Any]]:
    return _rows(_paths(cohort_id, root)["exits"])


def state(cohort_id: str, *,
          root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Everything a report needs. Opens no mutation path."""
    w = windows(cohort_id, root=root)
    return {
        "label": LABEL,
        "cohort_id": cohort_id,
        "purpose": ("BTC15m CONTINUOUS-INVENTORY EXECUTION VALIDATION ONLY. "
                    "It does not answer whether EXTEND is profitable, and it "
                    "is never pooled with the prospective EXTEND shadow."),
        "window_budget": WINDOW_BUDGET,
        "windows": w,
        "window_open": open_window_ticker(cohort_id, root=root),
        "windows_completed": completed_windows(cohort_id, root=root),
        "profile": profile_of(cohort_id, root=root),
        "orders_per_window": ORDERS_PER_WINDOW,
        "total_order_budget": TOTAL_ORDER_BUDGET,
        "total_order_budget_source": ("continuous_probe.MAX_ACTIONS, the "
                                      "pre-existing live envelope; it binds "
                                      "before the per-window cap and is not "
                                      "widened here"),
        "order_slots_consumed": consumed(cohort_id, root=root),
        "durable_consumed": durable_consumed(cohort_id, root=root,
                                             remote=None),
        "open_states": {tk: open_state(cohort_id, tk, root=root)
                        for tk in w},
        "quantity_fixed": str(QUANTITY),
        "max_acquisition_price": str(MAX_ACQUISITION_PRICE),
        "max_extends": str(MAX_EXTENDS),
        "max_pair_cost": str(MAX_PAIR_COST),
        "max_unmatched_shares": str(MAX_UNMATCHED_SHARES),
        "max_unmatched_principal": str(MAX_UNMATCHED_PRINCIPAL),
        "structural_max_loss_per_window": str(STRUCTURAL_MAX_LOSS_PER_WINDOW),
        "structural_max_loss": str(STRUCTURAL_MAX_LOSS),
        "realized_loss_stop": str(REALIZED_LOSS_STOP),
        "confirmed_realized_principal_loss":
            str(realized_principal_loss(cohort_id, root=root)),
        "policy_fingerprint": POLICY["fingerprint"],
        "halted": halted(cohort_id, root=root),
        "exits": exits(cohort_id, root=root),
        "halt_conditions": HALT_CONDITIONS,
        "statuses": list(STATUSES),
        "expired": expired(cohort_id, root=root, remote=None),
        "no_replenishment": ("no function in this module raises any bound. A "
                             "further probe needs a new cohort, created by a "
                             "human."),
    }
