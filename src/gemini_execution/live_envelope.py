"""The q=1 LIVE envelope: the bounds that hold on the actual order path.

WHY THIS IS A SEPARATE MODULE, AND NOT AN EDIT TO `continuous_probe`.
Two reasons, both load-bearing.

  1. `continuous_probe.py` is in the watchPatterns of gemini-l5-faithful and
     gemini-econ-shadow. Editing it REDEPLOYS both, resetting the frozen
     prospective cohort. The shadow envelope is therefore left byte-for-byte
     alone, which also keeps every shadow window comparable with every other.
  2. The shadow envelope is sized in units of the venue minimum, 0.01. The
     live test is q=1 -- a hundred times the size -- and a bound that means
     "four cents" at one size and "four dollars" at another is a bound nobody
     can reason about. The live numbers are stated here in their own units.

WHAT IT FIXES THAT THE SHADOW ENVELOPE DOES NOT BOUND.

  * `continuous_probe.would_breach` measures the prospective fill with
    `q = ACTION_SIZE`, the MODULE CONSTANT, not the quantity of the rung being
    sent. The gate and the frame can therefore disagree about size. Here the
    rung's own qty is required, and a qty that is not exactly ACTION_SIZE is
    refused rather than approximated.
  * `inv.spend()` is FILLED cost. Nothing in the shadow envelope bounds
    COMMITTED cost -- the rungs currently resting, every one of which may fill.
    Each rung can pass a per-rung check while the set of them exceeds the cap
    together. `MAX_COMMITTED_USD` bounds filled + resting + the one being
    sent, which is the number that actually answers "how much cash can this
    lose while I am not looking".
  * Residual is bounded in CONTRACTS but not in DOLLARS. At q=1 a two-contract
    residual is worth between nothing and $2.00 depending on entry price, and
    the directional leg is the part L5's own history measures as negative.
    `MAX_RESIDUAL_USD` bounds the cost basis actually at directional risk.
  * Nothing bounds how many orders may rest at once. `MAX_WORKING_ORDERS`
    does, because "two rungs" is an observed habit of the surface, not a
    property anything enforced.

A CORRECTION TO THIS REPOSITORY'S OWN RECORD. `analysis/PRE_LIVE_CHECKLIST.md`
states that `would_breach` does not check `MAX_TOTAL_SPEND`. That is WRONG and
has been since the file was written: the spend check is present at
`continuous_probe.py` and `git log -L` shows it in the commit that created the
function. The real gaps are the four above. A safety document that overstates a
gap is repaired in the open, the same as one that understates it.

NOTHING HERE DECIDES A TRADE. There is no side, price, timing or sizing
opinion in this module -- FROZEN_L5_NATIVE keeps every one of those. This only
ever REFUSES, and a refusal is always attributed to a named bound.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_L5_LIVE_ENVELOPE_Q1_V1"

# ------------------------------------------------------------- the bounds

@dataclass(frozen=True)
class Envelope:
    """One set of hard bounds. Frozen: a bound that can be reassigned at
    runtime is a suggestion, and the authorization hash would not see it move.

    THE NUMBERS DIFFER BY SIZE; THE CHECKS DO NOT. Both instances below run
    the same gate. That is deliberate -- the defects this module exists to
    close (a gate that tests a size other than the one sent, a cap that
    ignores resting cash, a residual bounded only in contracts, an unbounded
    number of live orders) were never properties of q=1. They were always
    there, and at 0.01 they were simply too cheap to notice.
    """

    name: str
    action_size: D
    max_same_side_run: int
    max_yes: D
    max_no: D
    #: FILLED cost
    max_total_spend_usd: D
    #: FILLED + RESTING + the rung being sent -- the real cash bound
    max_committed_usd: D
    #: cost basis carried on a directional outcome
    max_residual_usd: D
    max_working_orders: int
    max_concurrent_markets: int
    max_actions: int
    cohort_prefix: str

    @property
    def max_residual(self) -> D:
        return self.action_size * self.max_same_side_run

    @property
    def max_imbalance(self) -> D:
        return self.max_residual

    @property
    def worst_case_loss_usd(self) -> D:
        """The most cash this envelope can lose. The committed cap is the most
        that can leave the account, and a contract that resolves against pays
        nothing back."""
        return self.max_committed_usd

    def as_dict(self) -> Dict[str, str]:
        return {
            "name": self.name,
            "action_size": str(self.action_size),
            "max_actions": str(self.max_actions),
            "max_same_side_run": str(self.max_same_side_run),
            "max_residual_contracts": str(self.max_residual),
            "max_imbalance_contracts": str(self.max_imbalance),
            "max_yes_contracts": str(self.max_yes),
            "max_no_contracts": str(self.max_no),
            "max_total_spend_usd": str(self.max_total_spend_usd),
            "max_committed_usd": str(self.max_committed_usd),
            "max_residual_usd": str(self.max_residual_usd),
            "max_working_orders": str(self.max_working_orders),
            "max_concurrent_markets": str(self.max_concurrent_markets),
            "cohort_prefix": self.cohort_prefix,
            "worst_case_loss_usd": str(self.worst_case_loss_usd),
        }

    def sha(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True).encode("utf-8")
        ).hexdigest()


#: A binary contract redeems at 1.00, so one contract can never cost or lose
#: more than this. Every dollar bound is derived from it rather than guessed.
MAX_CONTRACT_VALUE_USD = D("1.00")

COHORT_PREFIX = "GEMI-BTC05M"

#: THE DEFAULT, and it is the SHADOW size. The numbers mirror
#: `continuous_probe` exactly so that nothing which runs today changes
#: behaviour; what changes is that the checks above now actually hold. A
#: driver gets the q=1 size only by being handed it, which is the point: the
#: bigger size is a decision somebody makes in a reviewed commit, never a
#: default that arrives by upgrade.
SHADOW_Q001 = Envelope(
    name="GEMINI_L5_SHADOW_ENVELOPE_Q001",
    action_size=D("0.01"),
    max_same_side_run=4,
    max_yes=D("0.10"),
    max_no=D("0.10"),
    max_total_spend_usd=D("0.15"),
    max_committed_usd=D("0.15"),
    max_residual_usd=D("0.04"),
    max_working_orders=2,
    max_concurrent_markets=1,
    max_actions=12,
    cohort_prefix=COHORT_PREFIX,
)

#: THE q=1 LIVE MANIFEST.
#:
#:   action_size 1        one contract: a settlement cashflow that is a
#:                        cent-visible fact rather than a rounding artifact
#:   same-side run 2      admits ONE EXTEND after the OPEN. The modal L5 run
#:                        is 2.9, so this reaches the behaviour without
#:                        funding it; a cap of 1 would make EXTEND impossible
#:                        and would silently turn the frozen policy into a
#:                        one-shot
#:   committed $2.50      the most cash that can leave the account, and
#:                        therefore the most this test can lose
#:   residual $2.00       the directional leg is the part L5's own history
#:                        measures as NEGATIVE, so it is bounded in dollars
#:                        and not only in contracts
LIVE_Q1 = Envelope(
    name="GEMINI_L5_LIVE_ENVELOPE_Q1_V1",
    action_size=D("1"),
    max_same_side_run=2,
    max_yes=D("2"),
    max_no=D("2"),
    max_total_spend_usd=D("2.50"),
    max_committed_usd=D("2.50"),
    max_residual_usd=D("2.00"),
    max_working_orders=2,
    max_concurrent_markets=1,
    max_actions=12,
    cohort_prefix=COHORT_PREFIX,
)

DEFAULT = SHADOW_Q001

#: module-level aliases kept for readers; the ENVELOPE is what gates use
MAX_CONCURRENT_MARKETS = LIVE_Q1.max_concurrent_markets


class EnvelopeBreach(ValueError):
    """A bound refused something. Never a strategy opinion."""


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def rung_cost(qty: Any, price: Any) -> Optional[D]:
    """Cash this rung takes if it fills. None when either side is unreadable.

    UNREADABLE IS NOT ZERO. A rung whose cost cannot be computed must refuse
    the action, never contribute 0 to a total that is then compared to a cap.
    """
    q, p = _dec(qty), _dec(price)
    if q is None or p is None or q <= 0 or p < 0:
        return None
    return q * p


def committed_usd(resting: Sequence[Mapping[str, Any]]) -> Optional[D]:
    """Cash the currently-resting orders would take if they ALL filled."""
    total = D("0")
    for o in resting or ():
        c = rung_cost(o.get("qty"), o.get("price"))
        if c is None:
            return None
        total += c
    return total


def residual_usd(inv: Any, env: Envelope = DEFAULT) -> D:
    """Cost basis of the unmatched leg -- the money on a directional outcome.

    Priced at the AVERAGE COST of the leg actually held, because that is the
    cash already spent on it. A mark is not used: a mark is not money, and
    this envelope bounds money.
    """
    yes, no = D(str(inv.yes_qty)), D(str(inv.no_qty))
    res = yes - no
    if res == 0:
        return D("0")
    avg = inv.avg_yes() if res > 0 else inv.avg_no()
    if avg is None:
        # a leg is held with no average cost, which should be impossible.
        # Charge the maximum rather than nothing.
        return abs(res) * MAX_CONTRACT_VALUE_USD
    return abs(res) * D(str(avg))


def would_breach(leg: str, price: Any, inv: Any, *, qty: Any,
                 resting: Sequence[Mapping[str, Any]] = (),
                 env: Envelope = DEFAULT) -> Optional[str]:
    """Would resting THIS rung, and having it fill, breach a bound?

    EVERY breach is named, not just the first, so a refusal cannot hide a
    second reason behind the one that happened to be checked earliest.

    The caller MUST pass the rung's own qty. There is deliberately no default:
    a gate that assumes a size is a gate that can test something other than
    what is sent.
    """
    hits: List[str] = []
    q, p = _dec(qty), _dec(price)
    if q is None or p is None:
        return ("rung size or price is unreadable (qty=%r price=%r); an "
                "unreadable rung is refused, never priced at zero"
                % (qty, price))
    if str(leg).upper() not in ("YES", "NO"):
        return "leg %r is neither YES nor NO" % (leg,)
    leg = str(leg).upper()
    if q > env.action_size:
        # LARGER IS REFUSED; SMALLER IS NOT. A rung above the envelope size is
        # the 100x-by-typo failure and must never reach the venue. A rung
        # BELOW it is the remainder of a partial fill, which is a legitimate
        # thing to still be working, costs less cash, and is bounded by every
        # dollar cap below anyway. Refusing it would leave a real position
        # half-worked with no way to finish.
        hits.append("rung qty %s is larger than the envelope size %s; %s "
                    "authorises no rung above that size"
                    % (q, env.action_size, env.name))

    yes = D(str(inv.yes_qty)) + (q if leg == "YES" else D("0"))
    no = D(str(inv.no_qty)) + (q if leg == "NO" else D("0"))
    if abs(yes - no) > env.max_imbalance:
        hits.append("a fill would take imbalance to %s, past %s"
                    % (abs(yes - no), env.max_imbalance))
    if yes > env.max_yes:
        hits.append("a fill would take YES to %s, past %s" % (yes, env.max_yes))
    if no > env.max_no:
        hits.append("a fill would take NO to %s, past %s" % (no, env.max_no))

    cost = rung_cost(q, p)
    if cost is None:
        return "rung cost is unreadable; refused"
    spend = D(str(inv.spend()))
    if spend + cost > env.max_total_spend_usd:
        hits.append("a fill would take FILLED spend to %s, past %s"
                    % (spend + cost, env.max_total_spend_usd))

    # THE ONE NOTHING BOUNDED BEFORE. Resting orders are cash the venue may
    # take at any moment without asking again.
    rest = committed_usd(resting)
    if rest is None:
        return ("a resting order has an unreadable size or price, so total "
                "committed cash cannot be established; refused")
    if spend + rest + cost > env.max_committed_usd:
        hits.append("filled %s + resting %s + this rung %s = %s committed, "
                    "past %s" % (spend, rest, cost, spend + rest + cost,
                                 env.max_committed_usd))

    n_resting = len(list(resting or ()))
    if n_resting + 1 > env.max_working_orders:
        hits.append("%d order(s) already resting; one more would pass %d"
                    % (n_resting, env.max_working_orders))

    # residual DOLLARS after the fill, priced at what the leg actually cost
    proj_res = yes - no
    if proj_res != 0:
        held_qty = abs(proj_res)
        if (proj_res > 0) == (leg == "YES"):
            # the new rung lands ON the leg that is left over, so its own
            # price is part of that leg's average
            prior_qty = (D(str(inv.yes_qty)) if proj_res > 0
                         else D(str(inv.no_qty)))
            prior_cost = (D(str(inv.yes_cost)) if proj_res > 0
                          else D(str(inv.no_cost)))
            tot_qty, tot_cost = prior_qty + q, prior_cost + cost
            avg = (tot_cost / tot_qty) if tot_qty > 0 else None
        else:
            avg = inv.avg_yes() if proj_res > 0 else inv.avg_no()
        unit = D(str(avg)) if avg is not None else MAX_CONTRACT_VALUE_USD
        if held_qty * unit > env.max_residual_usd:
            hits.append("a fill would put %s of cost basis on a directional "
                        "residual, past %s"
                        % (held_qty * unit, env.max_residual_usd))

    return "; ".join(hits) if hits else None


def gates(inv: Any, *, actions_taken: int,
          resting: Sequence[Mapping[str, Any]] = (),
          concurrent_markets: int = 1,
          env: Envelope = DEFAULT) -> List[str]:
    """Every hard bound checked on CURRENT state, all breaches named."""
    hit: List[str] = []
    if actions_taken >= env.max_actions:
        hit.append("max_actions %d reached" % env.max_actions)
    if D(str(inv.imbalance())) > env.max_imbalance:
        hit.append("inventory_imbalance %s exceeds %s"
                   % (inv.imbalance(), env.max_imbalance))
    if D(str(inv.yes_qty)) > env.max_yes:
        hit.append("yes inventory %s exceeds %s" % (inv.yes_qty, env.max_yes))
    if D(str(inv.no_qty)) > env.max_no:
        hit.append("no inventory %s exceeds %s" % (inv.no_qty, env.max_no))
    spend = D(str(inv.spend()))
    if spend > env.max_total_spend_usd:
        hit.append("filled spend %s exceeds %s"
                   % (spend, env.max_total_spend_usd))
    rest = committed_usd(resting)
    if rest is None:
        hit.append("a resting order is unreadable; committed cash is not "
                   "established")
    elif spend + rest > env.max_committed_usd:
        hit.append("committed cash %s exceeds %s"
                   % (spend + rest, env.max_committed_usd))
    if len(list(resting or ())) > env.max_working_orders:
        hit.append("%d resting orders exceeds %d"
                   % (len(list(resting or ())), env.max_working_orders))
    if residual_usd(inv, env) > env.max_residual_usd:
        hit.append("residual cost basis %s exceeds %s"
                   % (residual_usd(inv, env), env.max_residual_usd))
    if int(concurrent_markets) > env.max_concurrent_markets:
        hit.append("%d concurrent markets exceeds %d"
                   % (concurrent_markets, env.max_concurrent_markets))
    return hit


def envelope(env: Envelope = DEFAULT) -> Dict[str, str]:
    """The bounds in force, for the authorization hash and for the manifest."""
    return env.as_dict()


def envelope_sha(env: Envelope = DEFAULT) -> str:
    return env.sha()
