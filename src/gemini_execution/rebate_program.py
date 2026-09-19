"""THE PUBLISHED MAKER REBATE RULES, and what they are worth at our size.

Until now this programme had SEVEN confirmed credits ($0.51) and no rules to
read them against, so `rebates.py` could only ever report an aggregate
effective rate and refuse everything finer. Gemini publishes the rules:

    https://developer.gemini.com/prediction-markets/maker-rebate-program
    https://www.gemini.com/fees/predictions
    (read 2026-09-19)

    Maker Rebate = Rebate Rate x Taker Rate x C x P x (1 - P)
    capped at 5% of fill notional
    eligible execution price band  $0.20 - $0.80
    contracts listed on or after   2026-03-18
    rounded DOWN to the next cent
    paid daily at 5pm ET in USD

WHICH SETTLES THE OWNER'S QUESTION, AND NOT IN OUR FAVOUR. For Crypto from
2026-06-11 the rebate rate is 0.30 and the taker rate is 0.07, so a maker
fill earns

    0.30 x 0.07 x C x P x (1 - P)  =  0.021 x C x P x (1 - P)

At C = 0.01 and P = 0.31 -- CONTINUOUS-8's actual fill -- that is $0.0000449.
The round-down to the next cent means a day's whole accrual must reach $0.01
before ANY cash moves, and at the best possible price (P = 0.50, where
P(1-P) peaks at 0.25) that needs

    0.01 / (0.021 x 0.25) = 1.905 contracts

which at 0.01 per fill is 191 fills in one day, or ~223 at P = 0.31. A run
that produces ten 0.01 fills accrues about $0.0005 and is paid $0.00. Not
"probably nothing" -- arithmetically nothing.

THE PRICE BAND ALSO BITES. Two of this programme's five fills -- C4's YES at
0.92 and NO at 0.14 -- are OUTSIDE $0.20-$0.80 and earn nothing at all. The
L5 OPEN rung follows the bid, and the habitat census puts 19.9% of cycles
with a YES bid at or below 0.10.

WHAT THIS MODULE IS AND IS NOT. It computes an ACCRUAL from published rules.
An accrual is not a rebate. A rebate is CONFIRMED only when an authoritative
Gemini account record shows the credit, which remains `rebates.parse_credits`
over /v1/transfers. Nothing here may ever be added to realized P&L, and
`realized_ledger` keeps it in `estimated_not_counted` where it belongs.

IT ALSO REFUSES PER-DAY ATTRIBUTION. Mapping the seven confirmed credits onto
daily volume was attempted and does not close: same-day mapping makes Aug 28
require an average price above 1.00, and T+1 makes Aug 30 require the same.
Neither is possible, so neither mapping is true, and no per-day attribution is
produced here.
"""
from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

SOURCE = ("https://developer.gemini.com/prediction-markets/"
          "maker-rebate-program + https://www.gemini.com/fees/predictions, "
          "read 2026-09-19")

#: the published fee schedule, effective 2026-03-18
TAKER_RATE = D("0.07")
MAKER_RATE = D("0.0175")

#: published rebate rates by market, with their effective periods. The
#: CURRENT crypto rate is the last row.
RATE_TABLE = (
    ("Crypto & Commodities", "2026-04-09..2026-06-10", D("0.70")),
    ("Crypto & Commodities", "2026-06-11..", D("0.30")),
    ("Politics", "2026-05-10..2026-06-10", D("0.70")),
    ("Politics", "2026-06-11..", D("0.30")),
    ("Sports", "2026-04-09..2026-06-10", D("0.50")),
    ("Sports", "2026-06-11..", D("0.30")),
    ("Economics", "2026-05-10..2026-06-10", D("0.50")),
    ("Economics", "2026-06-11..", D("0.30")),
    ("All other markets", "2026-03-18..", D("0.30")),
)

#: BTC5m is Crypto, and from 2026-06-11 the rate is 0.30
CRYPTO_REBATE_RATE = D("0.30")

#: eligible EXECUTION price band. Outside it a maker fill earns nothing.
ELIGIBLE_LO = D("0.20")
ELIGIBLE_HI = D("0.80")

#: the rebate may not exceed this fraction of fill notional
NOTIONAL_CAP = D("0.05")

#: contracts listed before this date are excluded
LISTED_ON_OR_AFTER = "2026-03-18"

#: paid daily at 5pm ET. Our seven confirmed credits are timestamped 21:03Z,
#: which IS 5pm ET under EDT -- the strongest evidence that the credits we
#: hold were paid by this programme and not some other one.
PAID_AT_LOCAL = "17:00 America/New_York"

ACCRUAL_LABEL = ("ACCRUED FROM PUBLISHED RULES. Not a rebate, not realized, "
                 "and never added to trading P&L. A rebate is CONFIRMED only "
                 "when a Gemini account record shows the credit.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def eligible(price: Any) -> Dict[str, Any]:
    """Is this execution price inside the published $0.20-$0.80 band?"""
    p = _dec(price)
    if p is None:
        return {"eligible": None, "why": "price unreadable"}
    ok = ELIGIBLE_LO <= p <= ELIGIBLE_HI
    return {"eligible": ok, "price": str(p),
            "band": [str(ELIGIBLE_LO), str(ELIGIBLE_HI)],
            "why": (None if ok else
                    "execution price %s is outside the eligible band" % p)}


def accrual(contracts: Any, price: Any, *,
            rate: D = CRYPTO_REBATE_RATE) -> Dict[str, Any]:
    """One maker fill's accrual under the published formula. Never rounded.

    Rounding happens at the DAILY payment, not per fill -- see
    `daily_accrual`, which states the evidence for that reading.
    """
    c, p = _dec(contracts), _dec(price)
    out: Dict[str, Any] = {
        "contracts": (str(c) if c is not None else None),
        "price": (str(p) if p is not None else None),
        "rebate_rate": str(rate), "taker_rate": str(TAKER_RATE),
        "formula": "rebate_rate * taker_rate * C * P * (1 - P)",
        "source": SOURCE, "labels": ACCRUAL_LABEL,
    }
    if c is None or p is None:
        out.update({"computable": False, "accrued_usd": None,
                    "why": "contracts or price unreadable"})
        return out
    el = eligible(p)
    out["eligibility"] = el
    if not el["eligible"]:
        out.update({"computable": True, "accrued_usd": "0",
                    "why": el["why"] or "outside the eligible band"})
        return out
    raw = rate * TAKER_RATE * c * p * (D("1") - p)
    cap = NOTIONAL_CAP * c * p
    out.update({"computable": True,
                "accrued_usd": str(min(raw, cap)),
                "uncapped_usd": str(raw), "notional_usd": str(c * p),
                "cap_usd": str(cap), "capped": bool(raw > cap),
                "effective_bps_of_notional": (
                    str(round(min(raw, cap) / (c * p) * D("10000"), 2))
                    if c * p else None)})
    return out


def daily_accrual(fills: Sequence[Mapping[str, Any]], *,
                  rate: D = CRYPTO_REBATE_RATE) -> Dict[str, Any]:
    """A day's accrual, then the published round-DOWN to the next cent.

    WHY THE ROUND HAPPENS HERE AND NOT PER FILL: per-fill rounding would make
    every 0.01 fill worth $0.00 and no credit could ever exist, yet seven do.
    Daily aggregation then flooring is the only reading consistent with
    holding $0.01 credits at all. It is stated as a reading, not a fact -- the
    published text says only "rounded down to the next cent".
    """
    rows = [accrual(f.get("contracts", f.get("qty")), f.get("price"),
                    rate=rate) for f in (fills or [])]
    total = D("0")
    for r in rows:
        v = _dec(r.get("accrued_usd"))
        if v is not None:
            total += v
    paid = total.quantize(D("0.01"), rounding=ROUND_FLOOR)
    n_el = sum(1 for r in rows
               if (r.get("eligibility") or {}).get("eligible"))
    return {
        "n_fills": len(rows), "n_eligible": n_el,
        "n_outside_band": len(rows) - n_el,
        "accrued_usd": str(total),
        "would_pay_usd": str(paid),
        "pays_nothing": paid == 0,
        "shortfall_to_one_cent_usd": (str(D("0.01") - total)
                                      if paid == 0 else None),
        "rounding": "rounded DOWN to the next cent, per the published rules",
        "fills": rows,
        "labels": ACCRUAL_LABEL,
    }


def contracts_for_one_cent(price: Any = "0.50", *,
                           rate: D = CRYPTO_REBATE_RATE) -> Dict[str, Any]:
    """How much volume a day needs before ANY cash moves.

    The number that decides whether a bounded run can observe a rebate at all.
    """
    p = _dec(price)
    if p is None or not (D("0") < p < D("1")):
        return {"computable": False, "why": "price must be strictly 0..1"}
    per = rate * TAKER_RATE * p * (D("1") - p)
    if per <= 0:
        return {"computable": False, "why": "no accrual at this price"}
    c = D("0.01") / per
    return {"computable": True, "price": str(p),
            "per_contract_usd": str(per),
            "contracts_needed": str(round(c, 4)),
            "fills_of_0_01_needed": str(round(c / D("0.01"), 1)),
            "notional_needed_usd": str(round(c * p, 4)),
            "why": ("below this a day's whole accrual rounds down to $0.00 "
                    "and no credit can appear")}


def band_coverage(prices: Sequence[Any]) -> Dict[str, Any]:
    """What share of observed execution prices could earn anything at all."""
    seen: List[Dict[str, Any]] = []
    for x in prices or []:
        seen.append(eligible(x))
    n = len(seen)
    ok = sum(1 for s in seen if s.get("eligible"))
    unk = sum(1 for s in seen if s.get("eligible") is None)
    return {"n": n, "eligible": ok, "outside_band": n - ok - unk,
            "unreadable": unk,
            "eligible_share": (round(ok / n, 3) if n else None),
            "band": [str(ELIGIBLE_LO), str(ELIGIBLE_HI)],
            "why": ("a maker fill outside the band earns nothing, so a "
                    "policy that quotes into the tails is quoting where the "
                    "rebate does not reach")}


def consistency_with_confirmed(effective_bps: Any) -> Dict[str, Any]:
    """Does the CONFIRMED aggregate rate agree with the published formula?

    Under the formula, rebate / notional = rebate_rate x taker_rate x (1 - P),
    so an observed aggregate bps implies a notional-weighted average price.
    If that implied price is inside the eligible band, the confirmed credits
    and the published rules are consistent.

    CONSISTENT IS NOT PROVEN. Fills outside the band sit in the denominator
    and earn nothing, which drags the observed rate down, so the implied
    average price is biased and this check can only fail to contradict.
    """
    obs = _dec(effective_bps)
    if obs is None:
        return {"computable": False, "why": "no observed rate to check"}
    k = CRYPTO_REBATE_RATE * TAKER_RATE
    implied_one_minus_p = (obs / D("10000")) / k
    implied_p = D("1") - implied_one_minus_p
    inside = ELIGIBLE_LO <= implied_p <= ELIGIBLE_HI
    return {
        "computable": True,
        "observed_bps": str(obs),
        "implied_avg_price": str(round(implied_p, 4)),
        "inside_eligible_band": bool(inside),
        "verdict": ("CONSISTENT_WITH_PUBLISHED_RULES" if inside
                    else "NOT_EXPLAINED_BY_PUBLISHED_RULES"),
        "why": ("the implied notional-weighted average fill price is inside "
                "the eligible band, so the confirmed credits do not "
                "contradict the current published rate"
                if inside else
                "the implied average price is outside the eligible band, so "
                "the confirmed credits are not explained by the current rate"),
        "caveat": ("CONSISTENT IS NOT PROVEN. Ineligible fills sit in the "
                   "denominator and earn nothing, biasing the implied price; "
                   "this check can only fail to contradict."),
    }
