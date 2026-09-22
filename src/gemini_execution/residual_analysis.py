"""GEMINI_RESIDUAL_ANALYSIS_V1 -- matched economics vs directional residual.

THE QUESTION THIS EXISTS TO ANSWER. A window's settlement-implied net is one
number, and one number cannot say whether the strategy worked. A binary
market pays $1 on a MATCHED pair whatever happens, so matched inventory is a
locked, outcome-independent cashflow; everything else is a DIRECTIONAL BET
the operating model explicitly refuses to assume has skill. Reporting them
together lets a good matched book hide a bad residual habit, and vice versa.

THE DECOMPOSITION, and it is an identity rather than an estimate:

    m  = min(yes_qty, no_qty)                      matched pairs
    r  = |yes_qty - no_qty|                        terminal residual
    matched_pnl  = m x (1 - avg_yes - avg_no)      outcome-INDEPENDENT
    residual_pnl = r x [dominant side won] - r x avg_dominant
    ----------------------------------------------------------------
    matched_pnl + residual_pnl == payout - spend   ALWAYS

`check_identity` asserts that equality on every window rather than trusting
it. A decomposition that does not re-add to the number it decomposes is not
a decomposition, and this programme has been burned once already by an
accounting line that looked reasonable and measured a different quantity.

WHY BUCKET BY TERMINAL RESIDUAL 0/1/2/3/4. Under CAP-4 the residual can only
end at one of five sizes, and the interesting question is not "did we lose
money" but "at which residual size does the directional exposure stop paying
for itself". r=0 is a pure matched window and its P&L is locked at entry;
r=4 is a maximally one-sided window whose outcome is a coin flip on $1.80-ish
of cost. Those are different strategies wearing one name, and the bucket is
what separates them.

NOTHING HERE IS REALIZED. Every figure is settlement-IMPLIED, computed from
venue-confirmed fills and the venue's published resolution. REALIZED requires
an attributable cash movement and is not produced by this module at all.

READ-ONLY AND OFFLINE. It opens no socket, reads no account, and imports
nothing that can mutate. It is a pure function over artifacts already on disk.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from fractions import Fraction as F
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_RESIDUAL_ANALYSIS_V1"

#: a winning contract pays this. The venue's contract definition.
CONTRACT_PAYOUT = D("1")

#: WHY THE ARITHMETIC IS RATIONAL AND NOT DECIMAL. Splitting a leg's spend
#: across matched and residual units needs that leg's AVERAGE price, and an
#: average is a repeating decimal the moment the quantity is not a power of
#: ten: EP016's first window averaged 1.15 over 3 YES, which Decimal carries
#: as 0.3833...33 and multiplies back to 1.1499...99. The decomposition then
#: missed its own total by 1e-28 -- harmless in dollars, fatal to the claim
#: that it re-adds EXACTLY. Fractions make the identity exact by
#: construction, and the quantize happens once, at the edge, for display.
MONEY = D("0.000001")

#: CAP-4 permits exactly these terminal residual sizes.
RESIDUAL_BUCKETS = (0, 1, 2, 3, 4)

UNREALIZED = ("settlement-IMPLIED throughout. REALIZED requires an "
              "attributable cash movement and is never produced here.")


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _f(v: Any) -> Optional[str]:
    """One money string. Accepts Decimal or Fraction; exact values stay exact."""
    if v is None:
        return None
    if isinstance(v, F):
        q = D(v.numerator) / D(v.denominator)
        # an exact rational prints exactly; a repeating one is quantized ONCE
        v = q if q * D(v.denominator) == D(v.numerator) else q.quantize(MONEY)
    return format(D(v).normalize(), "f")


def _fr(v: Any) -> Optional[F]:
    """A value as an exact rational. Decimal strings convert losslessly."""
    try:
        return F(str(v))
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def decompose(*, yes_qty: Any, no_qty: Any,
              avg_yes: Any = None, avg_no: Any = None,
              yes_spend: Any = None, no_spend: Any = None,
              resolution_side: Optional[str] = None,
              spend_usd: Any = None,
              fees_usd: Any = None) -> Dict[str, Any]:
    """One window, split into locked matched economics and a directional bet.

    PREFER `yes_spend`/`no_spend` -- the exact per-leg cash from the fills --
    over `avg_yes`/`avg_no`. An average is a DERIVED, lossy quantity; the
    spend is what left the account. The averages are accepted as a fallback
    and reconstructed as spend = avg x qty.

    `resolution_side` None means UNRESOLVED: the matched leg is still fully
    computable -- that is the whole point of a matched pair -- while the
    residual is NOT, and is reported as unknown rather than guessed.
    """
    y, n = _fr(yes_qty) or F(0), _fr(no_qty) or F(0)
    fees = _fr(fees_usd) or F(0)

    sy = _fr(yes_spend)
    if sy is None:
        sy = (_fr(avg_yes) or F(0)) * y
    sn = _fr(no_spend)
    if sn is None:
        sn = (_fr(avg_no) or F(0)) * n

    # the per-unit cost of each leg, EXACT. Only used to split that leg's
    # own spend between its matched and residual units.
    py = (sy / y) if y else F(0)
    pn = (sn / n) if n else F(0)

    m = min(y, n)
    r = abs(y - n)
    dominant = None if r == 0 else ("yes" if y > n else "no")
    p_dom = F(0) if dominant is None else (py if dominant == "yes" else pn)

    matched_cost = m * (py + pn)
    matched_pnl = m * (_fr(CONTRACT_PAYOUT) - py - pn)
    residual_cost = r * p_dom

    out: Dict[str, Any] = {
        "label": LABEL,
        "yes_qty": _f(y), "no_qty": _f(n),
        "avg_yes_cost": _f(py), "avg_no_cost": _f(pn),
        "cost_basis": ("exact per-leg spend from the fills"
                       if (yes_spend is not None or no_spend is not None)
                       else "reconstructed as average x quantity"),
        "matched_qty": _f(m),
        "yes_spend_usd": _f(sy), "no_spend_usd": _f(sn),
        "matched_pair_unit_cost": _f(py + pn) if m else None,
        "matched_cost_usd": _f(matched_cost),
        # LOCKED AT ENTRY. A matched pair pays $1 whichever way the contract
        # resolves, so this number is known before the outcome is.
        "matched_pnl_usd": _f(matched_pnl),
        "matched_is_outcome_independent": True,
        "residual_qty": int(r),
        "residual_side": dominant,
        "residual_avg_cost": _f(p_dom) if dominant else None,
        "residual_cost_usd": _f(residual_cost),
        "fees_usd": _f(fees),
        "labels": UNREALIZED,
    }

    side = (resolution_side or "").lower()
    if side not in ("yes", "no"):
        out.update({
            "resolved": False, "resolution_side": None,
            "residual_won": None, "residual_pnl_usd": None,
            "payout_usd": None, "net_usd": None,
            "why": ("UNRESOLVED. The matched leg is computable without the "
                    "outcome and is reported above; the residual is a "
                    "directional bet whose result is not yet known and is "
                    "not guessed."),
        })
        # THE KEY IS ALWAYS PRESENT. A caller that checks `identity["holds"]`
        # must not get a KeyError on the one class of row where the identity
        # legitimately cannot be evaluated -- that is how an absent check
        # gets mistaken for a passing one.
        out["identity"] = check_identity(out)
        return out

    residual_won = (dominant is not None and dominant == side)
    residual_payout = (r * _fr(CONTRACT_PAYOUT)) if residual_won else F(0)
    residual_pnl = residual_payout - residual_cost
    payout = (y if side == "yes" else n) * _fr(CONTRACT_PAYOUT)
    # THE DECOMPOSITION'S OWN COST IS AUTHORITATIVE FOR THE IDENTITY. A
    # reported total_spend_usd is carried beside it and any disagreement is
    # REPORTED rather than absorbed -- a split that quietly re-bases itself
    # on a different total cannot detect that the two disagree.
    spend = matched_cost + residual_cost
    reported = _fr(spend_usd)
    net = payout - spend - fees

    out.update({
        "resolved": True, "resolution_side": side,
        "residual_won": (None if dominant is None else residual_won),
        "residual_payout_usd": _f(residual_payout),
        "residual_pnl_usd": _f(residual_pnl),
        "payout_usd": _f(payout),
        "spend_usd": _f(spend),
        "reported_spend_usd": _f(reported),
        "spend_agrees": (None if reported is None else reported == spend),
        "net_usd": _f(net),
    })
    out["identity"] = _identity_exact(matched_pnl, residual_pnl, fees, net)
    return out


def _identity_exact(mp: F, rp: F, fees: F, net: F) -> Dict[str, Any]:
    """The identity, on the EXACT rationals -- never on their display forms.

    THIS IS THE BUG THE CHECK WAS WRITTEN TO CATCH, and it caught it in its
    own implementation. The first version called `check_identity(out)` on the
    dict it had just formatted, so a pair cost like 0.81833...  was quantized
    to six places for display and then re-parsed as if it were the value. The
    check was measuring the rounding it had itself introduced. Exact values
    in, exact answer out; the strings are for humans.
    """
    lhs, rhs = mp + rp - fees, net
    return {"checkable": True, "holds": lhs == rhs,
            "matched_plus_residual_minus_fees": _f(lhs), "net": _f(rhs),
            "delta": _f(lhs - rhs),
            "exact": True,
            "why": ("a decomposition that does not re-add to the number it "
                    "decomposes is not a decomposition")}


def check_identity(row: Mapping[str, Any]) -> Dict[str, Any]:
    """matched_pnl + residual_pnl must re-add to net. Asserted, not assumed.

    Fees sit OUTSIDE the decomposition -- they are a venue charge, not part of
    either leg's economics -- so the identity is checked before fees and the
    fee is carried as its own term.
    """
    mp, rp = _fr(row.get("matched_pnl_usd")), _fr(row.get("residual_pnl_usd"))
    net, fees = _fr(row.get("net_usd")), _fr(row.get("fees_usd")) or F(0)
    if mp is None or rp is None or net is None:
        return {"checkable": False,
                "why": "an unresolved window has no residual P&L to re-add"}
    out = _identity_exact(mp, rp, fees, net)
    # THE PUBLIC ENTRY POINT READS FORMATTED STRINGS, so a repeating value was
    # already quantized before it got here and a tiny delta is display
    # rounding, not a broken split. `decompose` never routes through this.
    out["exact"] = False
    out["note"] = ("computed from DISPLAY strings; a sub-cent delta here is "
                   "rounding. decompose() checks the exact rationals.")
    return out


def leg_spend(events: Optional[Sequence[Mapping[str, Any]]]
              ) -> Dict[str, Any]:
    """Exact per-leg cash out, summed from the venue's own fill events.

    THE AUTHORITATIVE COST BASIS. `avg_*_cost` on the inventory is this
    divided by quantity and then truncated, so reconstructing spend from it
    loses cents-of-a-cent. These are the prices the venue executed at.
    """
    sy, sn, ny, nn, seen = F(0), F(0), 0, 0, 0
    for e in events or []:
        if str(e.get("X") or "").upper() not in ("FILLED", "PARTIALLY_FILLED"):
            continue
        px = _fr(e.get("L") if e.get("L") is not None else e.get("p"))
        qty = _fr(e.get("Z") if e.get("Z") is not None else e.get("q"))
        if px is None or qty is None:
            continue
        seen += 1
        if str(e.get("O") or "").upper() == "YES":
            sy += px * qty
            ny += 1
        elif str(e.get("O") or "").upper() == "NO":
            sn += px * qty
            nn += 1
    return {"readable": bool(seen), "n_fills": seen,
            "yes_spend": sy, "no_spend": sn,
            "n_yes_fills": ny, "n_no_fills": nn}


def from_window(rep: Mapping[str, Any],
                resolution: Optional[Mapping[str, Any]] = None,
                fees_usd: Any = None) -> Dict[str, Any]:
    """Decompose one of OUR cohort window artifacts."""
    win = rep.get("window") or rep
    inv = win.get("inventory") or {}
    res = resolution or {}
    settled = str(res.get("status") or "").lower() == "settled"
    legs = leg_spend(win.get("order_events"))
    out = decompose(
        yes_qty=inv.get("yes_qty"), no_qty=inv.get("no_qty"),
        avg_yes=inv.get("avg_yes_cost"), avg_no=inv.get("avg_no_cost"),
        yes_spend=(legs["yes_spend"] if legs["readable"] else None),
        no_spend=(legs["no_spend"] if legs["readable"] else None),
        resolution_side=(res.get("resolutionSide") if settled else None),
        spend_usd=inv.get("total_spend_usd"), fees_usd=fees_usd)
    out["fills_by_leg"] = {"yes": legs["n_yes_fills"], "no": legs["n_no_fills"]}
    out["symbol"] = win.get("symbol")
    out["n_fills"] = inv.get("n_fills")
    # THE CONTROLLER'S OWN NUMBER, carried for comparison rather than
    # recomputed over it. A disagreement between its matched_qty and ours is
    # a finding, not something to silently prefer one side of.
    out["controller_matched_qty"] = inv.get("matched_qty")
    out["controller_pair_cost"] = inv.get("effective_matched_pair_cost")
    mq, cq = _d(out["matched_qty"]), _d(inv.get("matched_qty"))
    out["agrees_with_controller"] = (None if cq is None else mq == cq)
    return out


def bucket(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Group resolved windows by TERMINAL RESIDUAL SIZE 0..4, and above.

    The question is where directional exposure stops paying for itself, so
    each bucket reports matched and residual P&L SEPARATELY as well as their
    sum. A bucket whose total is positive on matched economics while its
    residual bleeds is a different finding from one that is simply lucky.
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    for k in list(RESIDUAL_BUCKETS) + ["over_cap"]:
        buckets[str(k)] = {"n": 0, "n_resolved": 0, "matched_pnl_usd": D("0"),
                           "residual_pnl_usd": D("0"), "net_usd": D("0"),
                           "residual_cost_usd": D("0"), "n_residual_won": 0,
                           "symbols": []}
    unresolved = 0
    for r in rows:
        rq = r.get("residual_qty")
        key = str(rq) if rq in RESIDUAL_BUCKETS else "over_cap"
        b = buckets[key]
        b["n"] += 1
        if r.get("symbol"):
            b["symbols"].append(r["symbol"])
        b["residual_cost_usd"] += _d(r.get("residual_cost_usd")) or D("0")
        if not r.get("resolved"):
            unresolved += 1
            continue
        b["n_resolved"] += 1
        b["matched_pnl_usd"] += _d(r.get("matched_pnl_usd")) or D("0")
        b["residual_pnl_usd"] += _d(r.get("residual_pnl_usd")) or D("0")
        b["net_usd"] += _d(r.get("net_usd")) or D("0")
        if r.get("residual_won"):
            b["n_residual_won"] += 1

    out: Dict[str, Any] = {"label": LABEL, "n_windows": len(rows),
                           "n_unresolved": unresolved, "buckets": {}}
    for k, b in buckets.items():
        if not b["n"]:
            continue
        nres = b["n_resolved"]
        out["buckets"][k] = {
            "n": b["n"], "n_resolved": nres,
            "matched_pnl_usd": _f(b["matched_pnl_usd"]),
            "residual_pnl_usd": _f(b["residual_pnl_usd"]),
            "net_usd": _f(b["net_usd"]),
            "residual_cost_usd": _f(b["residual_cost_usd"]),
            "n_residual_won": b["n_residual_won"],
            "residual_win_rate": (None if not nres or k == "0"
                                  else _f(D(b["n_residual_won"]) / D(nres))),
            "mean_net_usd": (None if not nres
                             else _f(b["net_usd"] / D(nres))),
            "symbols": b["symbols"][:20],
        }
    tot_m = sum((_d(r.get("matched_pnl_usd")) or D("0"))
                for r in rows if r.get("resolved"))
    tot_r = sum((_d(r.get("residual_pnl_usd")) or D("0"))
                for r in rows if r.get("resolved"))
    out["totals"] = {
        "matched_pnl_usd": _f(tot_m),
        "residual_pnl_usd": _f(tot_r),
        "net_usd": _f(tot_m + tot_r),
        "reading": ("matched is the LOCKED, outcome-independent component; "
                    "residual is the directional bet. If the strategy's edge "
                    "is real it shows up in MATCHED. A positive total carried "
                    "by residual is a coin flip that landed."),
    }
    out["labels"] = UNREALIZED
    return out
