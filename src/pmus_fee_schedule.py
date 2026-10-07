"""PMUS fee schedule as a FROZEN, EFFECTIVE-DATED, FINGERPRINTED object.

WHAT THIS CORRECTION CHANGES. `src/pmus_incentive_audit.py` pinned a single
scalar `TAKER_RATE = D("0.06")`. PMUS republished the taker coefficient at
**0.0695**, so that scalar was stale -- and a scalar is the wrong shape
anyway: our fill corpus straddles the change, and a corpus that straddles a
schedule change cannot be priced by one number. A fill is now priced by the
schedule in force AT ITS OWN TIMESTAMP, so pre-change evidence stays exactly
as correct as it was when recorded and post-change fills are priced correctly
too. Understating a taker cost LOOSENS every bound derived from it, which is
the direction that matters, so the correction is made explicitly and with its
own fingerprint rather than as a silent edit.

WHAT THIS CORRECTION DOES **NOT** CHANGE. The MAKER coefficient did not move:
theta_maker = -0.0125 before and after. Therefore **no maker-rebate figure
moves**: not the $9.14 of credited maker rebates, not the $7.04 of liquidity
rewards, not the $16.18 confirmed-incentive total, not
`estimated_rebate_on_our_fills`, not the rebate cent-floor (1.62 shares at
p=0.45), not any break-even ceiling derived from the maker rebate. This
correction affects **TAKER cost accounting only**. Our own corpus is
maker-only by owner rule (`venue_charged_us_on_our_fills_usd 0.0000` across
every audit), so the realized-figure impact is nil; see
`analysis/l5/incentives/FEE_SCHEDULE_CORRECTION.md` for the figure-by-figure
statement.

THE EFFECTIVE DATE, AND A SOURCE CONFLICT KEPT RATHER THAN RESOLVED.

  * PRIMARY, captured: `analysis/pmus/pmus_published_fee_schedule.json` is our
    verbatim capture of `docs.polymarket.us/fees` -- taker theta 0.0695, maker
    theta -0.0125, "12 AM ET, Thursday September 17, 2026", plus the published
    99-row price table, every row of which reproduces exactly from those two
    coefficients with banker's rounding (`tests/test_pmus_fees.py`). That is
    the strongest provenance available to us: it pins the coefficients AND the
    rounding rule.
  * SECONDARY, 2026-10-07 WebSearch summary (recorded in
    `analysis/l5/incentives/PMUS_INCENTIVES_CURRENT.md` Q1): the taker rise
    was "effective 2026-10-01".

The two disagree only about WHEN, never about the coefficient. This schedule
takes the PRIMARY date (2026-09-17T04:00Z), which is also the conservative
choice: the higher taker cost starts EARLIER, so no fill is ever priced with a
cost lower than the venue may have charged. The secondary claim is recorded in
`SCHEDULE["discrepancies"]` so it cannot be lost, and a fill in the 09-17 ->
10-01 window is flagged by `date_conflict_window()` as date-ambiguous rather
than quietly rated.

A REBATE IS NOT A NEGATIVE FEE. `maker_rebate` returns a POSITIVE credit and
nothing here ever nets it against a cost. It is income only once the venue has
credited it (on PMUS: a negative `commissionNotionalCollected` on our own
execution, per fill).

FREEZING. `FINGERPRINT` is a hard-coded literal and `assert_frozen()` compares
a freshly computed sha256 over canonical sorted-key JSON against it, following
`analysis/l5/live_exec5/spec.py`. The assertion cannot be vacuous: it does not
recompute the expected value from the same object it is checking.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal as D, ROUND_HALF_EVEN
from typing import Any, Dict, Optional

SOURCE = "https://docs.polymarket.us/fees"
CENT = D("0.01")

#: The frozen schedule. Entries are ordered oldest-first; each one applies
#: from its own `effective_from_utc` until the next entry's.
SCHEDULE: Dict[str, Any] = {
    "label": "PMUS_FEE_SCHEDULE_V1",
    "source": SOURCE,
    "formula": "fee = theta * C * p * (1 - p)",
    "rounding": "banker's rounding (ROUND_HALF_EVEN) to the cent, as published",
    "price_domain": {"min": "0.01", "max": "0.99"},
    "maker_coefficient_moved": False,
    "affects": "TAKER cost accounting only",
    "entries": [
        {
            "effective_from_utc": "2026-07-01T04:00:00+00:00",
            "effective_label": "12 AM ET, Wednesday July 1, 2026",
            "taker_theta": "0.06",
            "maker_rebate_theta": "0.0125",
            "provenance": ("docs.polymarket.us/fees as quoted verbatim in "
                           "analysis/l5/PMUS_SPORTS_INCENTIVES.md, retrieved "
                           "2026-09-10T17:14Z: 'Effective exchange-wide from "
                           "12 AM ET, Wednesday July 1, 2026'"),
        },
        {
            "effective_from_utc": "2026-09-17T04:00:00+00:00",
            "effective_label": "12 AM ET, Thursday September 17, 2026",
            "taker_theta": "0.0695",
            "maker_rebate_theta": "0.0125",
            "provenance": ("analysis/pmus/pmus_published_fee_schedule.json -- "
                           "our verbatim capture of docs.polymarket.us/fees, "
                           "whose 99 published price rows all reproduce "
                           "exactly from these coefficients "
                           "(tests/test_pmus_fees.py)"),
        },
    ],
    "taker_rebate_tiers_prior_month_notional_usd": [
        {"floor": "250000", "share_of_taker_fee_rebated": "0.10"},
        {"floor": "1000000", "share_of_taker_fee_rebated": "0.25"},
        {"floor": "10000000", "share_of_taker_fee_rebated": "0.50"},
    ],
    "taker_rebate_available_to_this_account": False,
    "discrepancies": [
        {
            "about": "effective date of the taker theta rise to 0.0695",
            "primary": "2026-09-17T04:00:00+00:00 (captured venue page + "
                       "99-row published table, all rows reproduce)",
            "secondary": "2026-10-01, WebSearch summary recorded in "
                         "analysis/l5/incentives/PMUS_INCENTIVES_CURRENT.md Q1",
            "resolution": "PRIMARY used. It is also the conservative choice: "
                          "the higher taker cost begins earlier, so no fill "
                          "is priced below what the venue may have charged.",
            "ambiguous_window_utc": ["2026-09-17T04:00:00+00:00",
                                     "2026-10-01T04:00:00+00:00"],
        },
    ],
}


def canonical_json(spec: Dict[str, Any] = SCHEDULE) -> str:
    return json.dumps(spec, sort_keys=True, separators=(",", ":"))


def fingerprint(spec: Dict[str, Any] = SCHEDULE) -> str:
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


#: HARD-CODED LITERAL so assert_frozen() cannot be vacuous.
FINGERPRINT = \
    "ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629"


def assert_frozen(spec: Dict[str, Any] = SCHEDULE) -> None:
    got = fingerprint(spec)
    if got != FINGERPRINT:
        raise AssertionError(
            "PMUS fee schedule is not the frozen one: %s != %s" %
            (got, FINGERPRINT))


# ------------------------- effective-dated lookup -------------------------

def _at(when: Optional[dt.datetime]) -> dt.datetime:
    if when is None:
        return dt.datetime.now(dt.timezone.utc)
    if when.tzinfo is None:
        return when.replace(tzinfo=dt.timezone.utc)
    return when


def parse_ts(ts: str) -> dt.datetime:
    """A venue timestamp (RFC3339, nanosecond precision, trailing Z) as an
    aware datetime. The venue serves 9 fractional digits; Python takes 6."""
    s = str(ts).strip().replace("Z", "+00:00")
    if "." in s:
        head, _, tail = s.partition(".")
        frac, sign, off = tail, "", ""
        for c in ("+", "-"):
            if c in tail:
                frac, sign, off = tail.partition(c)[0], c, tail.partition(c)[2]
                break
        s = f"{head}.{frac[:6]}{sign}{off}"
    out = dt.datetime.fromisoformat(s)
    return out if out.tzinfo else out.replace(tzinfo=dt.timezone.utc)


def entry_at(when: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """The schedule entry in force at `when` (now by default).

    Before the first entry's effective date the venue published no formula we
    hold, so the oldest entry is returned and the caller is told.
    """
    t = _at(when)
    chosen = SCHEDULE["entries"][0]
    for e in SCHEDULE["entries"]:
        if t >= _at(dt.datetime.fromisoformat(e["effective_from_utc"])):
            chosen = e
    return chosen


def taker_theta(when: Optional[dt.datetime] = None) -> D:
    return D(entry_at(when)["taker_theta"])


def maker_theta(when: Optional[dt.datetime] = None) -> D:
    return D(entry_at(when)["maker_rebate_theta"])


def date_conflict_window(when: Optional[dt.datetime] = None) -> bool:
    """True when `when` falls in the 09-17 -> 10-01 window where our primary
    and secondary sources disagree about which taker theta applied. Such a
    fill's taker cost is rated at the HIGHER coefficient and flagged, never
    quietly rated."""
    lo, hi = SCHEDULE["discrepancies"][0]["ambiguous_window_utc"]
    t = _at(when)
    return (_at(dt.datetime.fromisoformat(lo)) <= t
            < _at(dt.datetime.fromisoformat(hi)))


def _fee(theta: D, contracts: D, price: D, *, cents: bool) -> D:
    raw = theta * D(str(contracts)) * D(str(price)) * (D(1) - D(str(price)))
    q = CENT if cents else D("0.0001")
    return raw.quantize(q, rounding=ROUND_HALF_EVEN)


def taker_fee(price: D, contracts: D = D(1), *,
              when: Optional[dt.datetime] = None, cents: bool = False) -> D:
    """What a TAKER PAYS at `price` for `contracts`, under the schedule in
    force at `when`. Positive."""
    return _fee(taker_theta(when), contracts, price, cents=cents)


def maker_rebate(price: D, contracts: D = D(1), *,
                 when: Optional[dt.datetime] = None,
                 cents: bool = False) -> D:
    """What a MAKER RECEIVES. Positive, a CREDIT, never netted against a cost
    here, and income only once the venue has credited it."""
    return _fee(maker_theta(when), contracts, price, cents=cents)


def price_fill(price: Any, contracts: Any, *, fill_ts: Optional[str] = None,
               is_taker: bool = False) -> Dict[str, Any]:
    """Price ONE fill under the schedule in force at ITS OWN timestamp.

    This is the shape a straddling corpus needs: the answer carries the
    schedule it was rated under, so a figure can always be traced to a
    coefficient and a date.
    """
    when = parse_ts(fill_ts) if fill_ts else None
    e = entry_at(when)
    side = "taker" if is_taker else "maker"
    amt = (taker_fee(D(str(price)), D(str(contracts)), when=when, cents=True)
           if is_taker else
           maker_rebate(D(str(price)), D(str(contracts)), when=when,
                        cents=True))
    return {
        "side": side,
        "usd_after_cent_rounding": str(amt),
        "sign": "charged_to_us" if is_taker else "credited_to_us",
        "theta": str(D(e["taker_theta"]) if is_taker
                     else D(e["maker_rebate_theta"])),
        "schedule_effective_from_utc": e["effective_from_utc"],
        "fill_ts": fill_ts,
        "effective_date_ambiguous": bool(is_taker
                                         and date_conflict_window(when)),
        "label": "ESTIMATED from the published formula; not a credit",
    }


def taker_rebate_pct(prior_month_taker_notional_usd: Any) -> D:
    v = D(str(prior_month_taker_notional_usd))
    best = D("0")
    for tier in SCHEDULE["taker_rebate_tiers_prior_month_notional_usd"]:
        if v >= D(tier["floor"]):
            best = max(best, D(tier["share_of_taker_fee_rebated"]))
    return best


def provenance(when: Optional[dt.datetime] = None) -> Dict[str, Any]:
    e = entry_at(when)
    return {
        "label": SCHEDULE["label"],
        "fingerprint": FINGERPRINT,
        "source": SOURCE,
        "formula": SCHEDULE["formula"],
        "rounding": SCHEDULE["rounding"],
        "in_force_entry": dict(e),
        "maker_coefficient_moved": SCHEDULE["maker_coefficient_moved"],
        "affects": SCHEDULE["affects"],
        "discrepancies": SCHEDULE["discrepancies"],
        "correction": ("replaces the stale scalar TAKER_RATE=0.06 in "
                       "src/pmus_incentive_audit.py; maker theta unchanged, "
                       "so no maker-rebate figure moves"),
    }


def assert_consistent_with_pmus_fees() -> None:
    """The execution path's own schedule (`src/pmus_fees.py`) and this one
    must not disagree. Two schedules that drift apart are worse than one
    stale number, because each looks authoritative on its own."""
    from src import pmus_fees as f
    latest = SCHEDULE["entries"][-1]
    oldest = SCHEDULE["entries"][0]
    change = _at(dt.datetime.fromisoformat(latest["effective_from_utc"]))
    if f.THETA_CHANGE_UTC != change:
        raise AssertionError("effective date disagrees with src/pmus_fees.py: "
                             f"{f.THETA_CHANGE_UTC} != {change}")
    if (f.THETA_BEFORE[0], f.THETA_BEFORE[1]) != (D(oldest["taker_theta"]),
                                                  D(oldest["maker_rebate_theta"])):
        raise AssertionError("pre-change thetas disagree with src/pmus_fees.py")
    if (f.THETA_FROM[0], f.THETA_FROM[1]) != (D(latest["taker_theta"]),
                                              D(latest["maker_rebate_theta"])):
        raise AssertionError("post-change thetas disagree with src/pmus_fees.py")
