"""Logical relations between Gemini contracts, derived from settlement terms.

THE ONE RULE THIS FILE EXISTS TO ENFORCE. A price difference is an arbitrage
only if the SETTLEMENT RULES make the payoffs related. Two contracts that merely
look alike, or that a human would call "basically the same bet", establish
nothing. Every relation below is emitted with the evidence that justifies it, and
a relation that cannot be justified is emitted as UNVERIFIED rather than dropped
or assumed.

WHAT THE VENUE GIVES US TO WORK WITH. Each contract carries a machine-readable
`strike` object -- `{"type": "above"|"below"|"reference", "value": "<number>"}`
-- plus `expiryDate` and, for index-settled families, `source` (e.g.
`LS-LRRR-XBTUSD`). Relations are built from those fields, not from parsing
English labels, except for the bucket ranges which the venue does not express
structurally and which are parsed strictly or not at all.

THE STRUCTURAL FACT THAT VOIDS ONE WHOLE CATEGORY. Gemini runs ONE book per
contract and quotes the NO side as an exact arithmetic complement of it:
measured over 4,099 active contracts carrying all six quote fields,

    bestAsk == buy.yes,  bestBid == sell.yes,
    buy.no  == 1 - bestBid,  sell.no == 1 - bestAsk

held with ZERO exceptions. So "buy YES and buy NO on the same contract" is not a
second book being crossed, it is buying at the ask and selling at the bid of one
book, and its cost is 1 + spread -- never below 1. Same-contract YES/NO
complement arbitrage is impossible on this venue by construction, not merely
absent today. It is therefore asserted here and not scanned for.
"""
from __future__ import annotations

import re
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

# ----------------------------------------------------------------- relations

LADDER_ABOVE = "LADDER_ABOVE"        # monotone: P(X>=s) non-increasing in s
LADDER_BELOW = "LADDER_BELOW"        # monotone: P(X<=s) non-decreasing in s
COMPLEMENT_PAIR = "COMPLEMENT_PAIR"  # two contracts that exactly partition
PARTITION = "PARTITION"              # n contracts that exactly partition
DUPLICATE = "DUPLICATE"              # identical settlement, different contract

#: a relation we can see but cannot justify from the terms. Reported, never traded.
UNVERIFIED = "UNVERIFIED"

#: Strike types whose YES probability FALLS as the strike value rises, because a
#: higher threshold is a strictly harder event: {X > 4.5} is a subset of
#: {X > 2.5}. The venue spells the same idea three ways depending on family --
#: `above` for index levels, `over` for sports totals and player props, `spread`
#: for margin-of-victory lines -- and all three are the same inequality.
MONOTONE_DECREASING = ("above", "over", "spread")

#: Strike types whose YES probability RISES as the value rises: {X <= 10} is a
#: superset of {X <= 5}.
MONOTONE_INCREASING = ("below", "under_or_equal")

#: `reference` is deliberately in NEITHER list. It carries the index print a
#: window opened at, not a threshold on a common scale, so two `reference`
#: contracts with different values are two different questions and comparing
#: them would be inventing a relation the terms do not support.
NON_COMPARABLE = ("reference",)

_NUM_IN_LABEL = re.compile(r"-?[\d,]+(?:\.\d+)?")


def measure_key(c: Dict[str, Any]) -> str:
    """What quantity this contract is a threshold ON, with the threshold removed.

    WHY THIS IS NOT OPTIONAL. One Gemini event routinely carries thresholds on
    SEVERAL different quantities: `NFL-...-PPPASSTD` holds
    "Tyler Shough {}+ Passing Touchdowns" alongside
    "Michael Penix Jr. {}+ Passing Touchdowns", and a Team Total event holds one
    ladder per team. Measured on the live catalogue, 19 active events mix two or
    more such measures.

    Grouping a ladder by event and strike type ALONE would therefore compare one
    player's touchdowns against another's and call the result a monotonicity
    violation. There is no such relation: the two quantities are independent, and
    any "edge" found that way is pure fiction. So members are grouped by this
    key, which is the label with every number blanked out, and a ladder only ever
    spans one measure.
    """
    lab = (c.get("label") or "").strip().lower()
    return re.sub(r"\s+", " ", _NUM_IN_LABEL.sub("{}", lab))


def strike_of(c: Dict[str, Any]) -> Tuple[Optional[str], Optional[D]]:
    """(type, value) from the structured strike field. (None, None) if absent."""
    s = c.get("strike")
    if not isinstance(s, dict):
        return (None, None)
    t = s.get("type")
    try:
        v = D(str(s.get("value")))
    except (InvalidOperation, TypeError):
        v = None
    return (t, v)


def settlement_identity(event: Dict[str, Any], c: Dict[str, Any]) -> Optional[Tuple]:
    """A tuple that is equal exactly when two contracts must settle identically.

    Deliberately strict. It requires the same expiry instant, the same declared
    index `source`, the same strike type and the same strike value. When any of
    those is missing the identity is None, which means "cannot be established" --
    and callers must not treat two Nones as a match.
    """
    t, v = strike_of(c)
    src = c.get("source")
    exp = c.get("expiryDate")
    if t is None or v is None or not exp or not src:
        return None
    return (src, exp, t, v)


#: `66°F to 67°F`, `$3 - $5`, `70 to 71`. The unit may sit between the first
#: number and the separator, so it is consumed explicitly rather than assumed
#: absent -- the first version of this pattern silently failed on every weather
#: bucket on the platform, which removed all 10 verifiable partitions from the
#: scan without reporting a single parse failure.
_RANGE = re.compile(
    r"^\$?\s*(-?[\d,]+(?:\.\d+)?)\s*(?:°[FC]|[A-Za-z%]{0,12})\s*"
    r"(?:-|–|—|to)\s*\$?\s*(-?[\d,]+(?:\.\d+)?)")


def _num(s: str) -> Optional[D]:
    try:
        return D(s.replace(",", ""))
    except InvalidOperation:
        return None


def bucket_of(c: Dict[str, Any]) -> Optional[Tuple[Optional[D], Optional[D]]]:
    """The half-open interval a contract covers, as (lo, hi); None means unbounded.

    `below s`  -> (None, s)
    `above s`  -> (s, None)
    `a to b`   -> (a, b)      parsed from the label, strictly

    Returns None when the contract's coverage cannot be established, which keeps
    it out of every partition check rather than guessing its extent.
    """
    t, v = strike_of(c)
    if t == "below" and v is not None:
        return (None, v)
    if t == "above" and v is not None:
        return (v, None)
    m = _RANGE.match((c.get("label") or "").strip())
    if m:
        lo, hi = _num(m.group(1)), _num(m.group(2))
        if lo is not None and hi is not None and lo <= hi:
            return (lo, hi)
    return None


#: How far apart two `expiryDate` values may sit and still be treated as ONE
#: settlement instant.
#:
#: WHY THIS TOLERANCE EXISTS, AND WHY IT IS NOT A LOOSENING. Requiring exactly
#: equal expiries voided 307 of 640 otherwise-verified sports ladders. The cause
#: is not a real difference in settlement: measured on
#: `NFL-2610042025-KC-LV-TT`, the thirteen "Las Vegas scores more than {} points"
#: contracts carry expiries spread across 2026-10-28T22:17:29.5Z to
#: ...:31.7Z -- sub-second jitter in the order the ROWS WERE WRITTEN, while all
#: thirteen settle on one number, the final score of one game.
#:
#: The relation does not depend on the timestamps agreeing to the millisecond; it
#: depends on the contracts being thresholds on ONE quantity. That is already
#: enforced, far more strongly, by requiring an identical `measure_key` within a
#: single event. The tolerance only prevents a bookkeeping artifact from
#: destroying a relation that the settlement rules plainly support, and it is
#: deliberately far smaller than the gap between any two genuinely distinct
#: windows on this venue (the shortest family is 5 minutes).
EXPIRY_TOLERANCE_S = 300.0


def _parse_ts(v: Any) -> Optional[float]:
    from datetime import datetime
    if not v:
        return None
    try:
        return datetime.strptime(str(v).replace("Z", "+0000"),
                                 "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()
    except ValueError:
        pass
    try:
        return datetime.strptime(str(v).replace("Z", "+0000"),
                                 "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        return None


def _same_settlement_basis(cs: List[Dict[str, Any]]) -> bool:
    """All contracts settle off one quantity at one instant (within tolerance).

    The `source` test stays EXACT. Two different declared indices are two
    different settled quantities however close their expiries are, so a
    disagreement there is never tolerated.
    """
    srcs = {c.get("source") for c in cs}
    if len(srcs) != 1:
        return False
    ts = [_parse_ts(c.get("expiryDate")) for c in cs]
    if any(t is None for t in ts):
        return False
    return (max(ts) - min(ts)) <= EXPIRY_TOLERANCE_S


def verify_partition(cs: List[Dict[str, Any]], *, step: D) -> Dict[str, Any]:
    """Is this contract set mutually exclusive AND exhaustive over the real line?

    A bucket cover qualifies only when, sorted by lower edge, it starts
    unbounded-below, ends unbounded-above, and each interior boundary meets the
    next with no gap and no overlap at the family's quoted granularity `step`
    (1 degree F, 1 cent, ...). Anything else -- a missing tail, a gap, an overlap
    -- fails, because a payoff of exactly 1 is then not guaranteed.

    `step` is required and never defaulted: whether `70 to 71` and `72 to 73`
    abut depends entirely on the granularity of the settled quantity, and
    assuming one would silently manufacture exhaustiveness.
    """
    if len(cs) < 2 or not _same_settlement_basis(cs):
        return {"exhaustive": False, "why": "settlement basis not shared"}
    buckets = []
    for c in cs:
        b = bucket_of(c)
        if b is None:
            return {"exhaustive": False,
                    "why": "coverage of %s not establishable" % c.get("ticker")}
        buckets.append((b, c))
    lows = [b[0] for b, _ in buckets]
    if sum(1 for lo in lows if lo is None) != 1:
        return {"exhaustive": False, "why": "not exactly one unbounded-below tail"}
    if sum(1 for (_, hi), _ in buckets if hi is None) != 1:
        return {"exhaustive": False, "why": "not exactly one unbounded-above tail"}
    ordered = sorted(buckets, key=lambda bc: (bc[0][0] is not None, bc[0][0]))
    for i in range(len(ordered) - 1):
        hi = ordered[i][0][1]
        nxt_lo = ordered[i + 1][0][0]
        if hi is None or nxt_lo is None:
            return {"exhaustive": False, "why": "unbounded edge in the interior"}
        if nxt_lo - hi != step:
            return {"exhaustive": False,
                    "why": "boundary %s->%s is not one step (%s)" % (hi, nxt_lo, step)}
    return {"exhaustive": True,
            "why": "contiguous cover at step %s, both tails unbounded" % step,
            "n": len(cs)}


def ladders(event: Dict[str, Any], contracts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Monotone strike ladders within one event, one measure at a time.

    WHY THIS IS AIRTIGHT. Fix one quantity X settled at one instant. For
    s1 < s2, {X >= s2} is a SUBSET of {X >= s1}, so YES(s1) >= YES(s2) must hold
    at every price, for every state of the world. The mirrored argument gives
    YES(X <= s1) <= YES(X <= s2). This needs NO view on X -- only that both
    contracts settle off the SAME quantity at the SAME instant, which is what
    `measure_key` and `_same_settlement_basis` establish between them.

    A group is emitted as UNVERIFIED, not dropped, when the members share a
    measure but not a settlement basis: that is a relation we can see and cannot
    justify, and silently discarding it would hide a real gap in coverage.
    """
    out: List[Dict[str, Any]] = []
    groups: Dict[Tuple, List[Tuple[D, Dict[str, Any]]]] = {}
    for c in contracts:
        t, v = strike_of(c)
        if t is None or v is None or t in NON_COMPARABLE:
            continue
        if t in MONOTONE_DECREASING:
            direction = LADDER_ABOVE
        elif t in MONOTONE_INCREASING:
            direction = LADDER_BELOW
        else:
            continue
        groups.setdefault((direction, t, measure_key(c)), []).append((v, c))

    for (direction, stype, mkey), members in groups.items():
        if len(members) < 2:
            continue
        cs = [c for _, c in members]
        base = {"event": event.get("ticker"), "strike_type": stype,
                "measure": mkey, "n": len(cs)}
        if len({v for v, _ in members}) != len(members):
            out.append({**base, "relation": UNVERIFIED, "intended": direction,
                        "why": "two members share one strike value on one measure"})
            continue
        if not _same_settlement_basis(cs):
            out.append({**base, "relation": UNVERIFIED, "intended": direction,
                        "why": "members do not share one expiry/source"})
            continue
        members.sort(key=lambda vc: vc[0])
        out.append({**base, "relation": direction,
                    "members": [{"symbol": c.get("instrumentSymbol"),
                                 "ticker": c.get("ticker"), "strike": str(v),
                                 "label": c.get("label")} for v, c in members],
                    "why": ("one measure (%s), one expiry; P(X beyond s) is "
                            "non-increasing in s" % mkey
                            if direction == LADDER_ABOVE
                            else "one measure (%s), one expiry; P(X within s) is "
                                 "non-decreasing in s" % mkey)})
    return out


def duplicates(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Contracts in DIFFERENT events whose settlement identity is identical."""
    seen: Dict[Tuple, List[Dict[str, Any]]] = {}
    for e in events:
        for c in (e.get("contracts") or []):
            if c.get("status") != "active":
                continue
            ident = settlement_identity(e, c)
            if ident is None:
                continue
            seen.setdefault(ident, []).append({
                "event": e.get("ticker"), "event_id": e.get("id"),
                "symbol": c.get("instrumentSymbol"), "ticker": c.get("ticker"),
                "label": c.get("label")})
    out = []
    for ident, members in seen.items():
        if len({m["event_id"] for m in members}) < 2:
            continue
        src, exp, t, v = ident
        out.append({"relation": DUPLICATE, "members": members,
                    "why": ("identical declared settlement: source=%s expiry=%s "
                            "strike=%s %s" % (src, exp, t, v))})
    return out
