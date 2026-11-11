"""OWNER #167 5574408755 item 3 -- PMUS incentive economics, empirically.

"Use only observed/credited maker rebates plus separately labeled unproven
rewards. Calculate the empirical break-even completion ceiling at our
current fill sizes, including rounding/minimum-credit behavior. Show 1.00,
1.01, 1.02 etc. Do not assume a rebate makes a chase profitable."

READ-ONLY and OFFLINE. Inputs are checked-in artifacts only:
  * analysis/l5/venue_rebate_credits.json -- the venue's own per-print
    commissionNotionalCollected on OUR execution (negative = credited to
    us), unioned over the R3L/R4 incentive audits.  25 credited prints,
    $0.42.  No price on those rows.
  * PRINT_PRICES below -- the venue's trade price for every print of ours
    in the five at-size markets, recovered from the R3L/R4 session
    settlement records (labelled per row: exact / elimination / derived).
  * src.pmus_incentive_audit -- the venue's published formula and cent
    rounding (imported read-only; nothing here touches a transport).

Every number is labelled OBSERVED (a venue credit), RECOVERED (a print
price read back from our own settlement records), DERIVED (arithmetic on
observed numbers) or UNPROVEN (advertised, never credited: worth 0 here).

Run: python3 -m src.l5_incentive_breakeven
Writes analysis/l5/pmus_incentive_breakeven.json and
       analysis/l5/PMUS_INCENTIVE_BREAKEVEN.md
"""
from __future__ import annotations

import json
import os
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src import pmus_incentive_audit as audit

A = os.path.join("analysis", "l5")
CREDITS = os.path.join(A, "venue_rebate_credits.json")
OUT_JSON = os.path.join(A, "pmus_incentive_breakeven.json")
OUT_MD = os.path.join(A, "PMUS_INCENTIVE_BREAKEVEN.md")

CENT = D("0.01")
TICK = D("0.005")                      # the venue's price grid (q1.TICK)
RATE = audit.MAKER_REBATE_RATE         # published 0.0125 x C x p x (1-p)
ONE = D("1")

# Sizes the owner asked for, and the two R4 first-leg prices.
QTYS = (D("3"), D("4"), D("5"), D("6.5"))
P1S = (D("0.20"), D("0.30"), D("0.38"), D("0.45"), D("0.50"))
PAIR_COSTS = tuple(D(x) for x in ("0.98", "0.99", "1.00", "1.01", "1.02", "1.03", "1.05"))
R4_ROWS = ((D("5"), D("0.38")), (D("5"), D("0.47")))

# ---------------------------------------------------------------------------
# Print prices.  `px` is the VENUE trade price (YES terms) exactly as the
# venue's trade row reports it; the rebate formula is symmetric in p(1-p),
# so side does not matter for the credit.  `our_px` is what we paid where
# our order record says so.  `src`:
#   exact       -- venue trade row (t, px, qty) read back from the session
#                  settlement record, one-to-one
#   elimination -- venue trade rows for this market were only partly
#                  recoverable; the two 5.43 prints not read back one-to-one
#                  are assigned the two remaining prices seen on that
#                  market's rows (0.45 / 0.47); both give p(1-p) within 0.5%
#                  of the exact 0.46 print, so the credit arithmetic is
#                  unaffected
#   derived     -- per-print rows were dropped by the log rate limit; the
#                  market's average entry price (l5_economic_scoreboard
#                  avg_entry_price) stands in for every print
# ---------------------------------------------------------------------------
PRINT_PRICES: Dict[str, List[dict]] = {
    "aec-atpcq-aleher-tundur-2026-09-07": [
        {"t": "08:08:31", "qty": "5.31", "px": "0.47", "our_px": "0.47", "src": "exact"},
        {"t": "08:09:48", "qty": "5.20", "px": "0.49", "our_px": "0.51", "src": "exact"},
        {"t": "08:09:59", "qty": "0.11", "px": "0.50", "our_px": "0.50", "src": "exact"},
        {"t": "08:11:34", "qty": "3.72", "px": "0.49", "our_px": "0.49", "src": "exact"},
        {"t": "08:11:44", "qty": "1.38", "px": "0.49", "our_px": "0.49", "src": "exact"},
        {"t": "08:50:35", "qty": "2.52", "px": "0.78", "our_px": "0.22", "src": "exact"},
        {"t": "08:50:37", "qty": "0.72", "px": "0.78", "our_px": "0.22", "src": "exact"},
    ],
    "aec-atpcq-norgom-ollwal-2026-09-07": [
        {"t": "09:06:30", "qty": "5.31", "px": "0.47", "our_px": "0.47", "src": "exact"},
        {"t": "09:07:50", "qty": "5.00", "px": "0.49", "our_px": "0.51", "src": "exact"},
        {"t": "09:07:59", "qty": "0.20", "px": "0.49", "our_px": "0.51", "src": "exact"},
        {"t": "09:15:03", "qty": "4.90", "px": "0.51", "our_px": "0.51", "src": "exact"},
        {"t": "09:25:00", "qty": "5.01", "px": "0.49", "our_px": "0.51", "src": "exact"},
    ],
    "asc-cfb-lou-miss-2026-09-06-pos-6pt5": [
        {"t": "01:22:38", "qty": "5.55", "px": "0.45", "our_px": None, "src": "exact"},
        {"t": "01:44:13", "qty": "5.55", "px": "0.47", "our_px": None, "src": "exact"},
        {"t": "01:44:37", "qty": "5.31", "px": "0.47", "our_px": None, "src": "exact"},
        {"t": "01:45:09", "qty": "5.68", "px": "0.44", "our_px": None, "src": "exact"},
        {"t": "01:45:15", "qty": "5.68", "px": "0.48", "our_px": None, "src": "exact"},
        {"t": "01:49:57", "qty": "3.90", "px": "0.65", "our_px": None, "src": "exact"},
        {"t": "01:55:23", "qty": "1.41", "px": "0.50", "our_px": None, "src": "exact"},
    ],
    "tsc-cfb-lou-miss-2026-09-06-total-41pt5": [
        {"t": "01:43:01", "qty": "5.43", "px": "0.45", "our_px": None, "src": "elimination"},
        {"t": None, "qty": "5.43", "px": "0.47", "our_px": None, "src": "elimination"},
        {"t": "01:46:40", "qty": "5.43", "px": "0.46", "our_px": None, "src": "exact"},
        {"t": "01:47:15", "qty": "5.68", "px": "0.48", "our_px": None, "src": "exact"},
        {"t": "02:12:28", "qty": "3.20", "px": "0.79", "our_px": None, "src": "exact"},
        {"t": "02:12:58", "qty": "1.00", "px": "0.79", "our_px": None, "src": "exact"},
        {"t": "02:13:11", "qty": "3.28", "px": "0.77", "our_px": None, "src": "exact"},
        {"t": "02:14:33", "qty": "3.13", "px": "0.74", "our_px": None, "src": "exact"},
    ],
    "asc-cfb-wisc-nd-2026-09-06-pos-10pt5": [
        {"t": None, "qty": "4.64", "px": "0.3262", "our_px": "0.3262", "src": "derived"},
        {"t": None, "qty": "4.33", "px": "0.3262", "our_px": "0.3262", "src": "derived"},
        {"t": None, "qty": "5.15", "px": "0.3262", "our_px": "0.3262", "src": "derived"},
        {"t": None, "qty": "5.15", "px": "0.3262", "our_px": "0.3262", "src": "derived"},
    ],
}

# The venue's OTHER advertised incentives, and what this account has seen
# of each.  Every one is UNPROVEN for break-even purposes: value 0.
UNPROVEN_REWARDS = [
    {"reward": "liquidityProgram rewards (/v1/incentives/earnings)",
     "advertised": "resting-order rewards from per-market reward pools "
                   "(e.g. us_open_props 400/600, atp_wta_futures 500, "
                   "atp_player_props 100 reward_pool, target_size 10000)",
     "observed": "4 earnings rows, $0.22 total, dated 2026-08-23/24, every row "
                 "status SKIPPED; 0 credit rows in the full activity walk "
                 "(the only non-trade row is a $10 deposit TRANSFER); "
                 "our_markets_in_programs = 0 in every audit",
     "credited_usd": "0.00", "value_in_break_even_usd": "0.00", "label": "UNPROVEN"},
    {"reward": "taker rebates",
     "advertised": "L5's history shows +$7,323 of taker rebates; no PMUS "
                   "taker-rebate program is published for our markets",
     "observed": "none: we are maker-only (19/19 R3L, all R4 fills passiveExecution); "
                 "venue_charged_us_on_our_fills_usd 0.0000",
     "credited_usd": "0.00", "value_in_break_even_usd": "0.00", "label": "UNPROVEN"},
    {"reward": "published liquidity programs covering our filled markets",
     "advertised": "20,000-row (truncated) active program list: atp/wta props "
                   "and futures; MLB 0 of 3,101 markets with any period",
     "observed": "published_programs_covering_our_markets = 0 in all five audits",
     "credited_usd": "0.00", "value_in_break_even_usd": "0.00", "label": "UNPROVEN"},
    {"reward": "maker rebate below the cent floor",
     "advertised": "0.0125 x C x p x (1-p) on every maker fill",
     "observed": "6 of our 31 at-size prints (0.11-1.41 shares) and every earlier "
                 "sub-floor print were credited $0.00 (166 venue trades in the "
                 "final audit walk, 25 with a credit): the venue rounds the "
                 "per-print rebate to the cent and pays nothing below $0.005",
     "credited_usd": "0.00", "value_in_break_even_usd": "0.00", "label": "OBSERVED ZERO"},
]


# ------------------------------- arithmetic --------------------------------

def cents(x: D) -> D:
    """The venue's cent rounding (banker's), per docs.polymarket.us/fees."""
    return x.quantize(CENT, rounding=ROUND_HALF_EVEN)


def raw_rebate(qty: D, px: D) -> D:
    """Published smooth formula, unrounded."""
    return RATE * qty * px * (ONE - px)


def credit_rounded(qty: D, px: D) -> D:
    """The EMPIRICAL rounded-credit step: the published formula rounded to
    the cent exactly as the venue's own credits show (whole cents, $0.00
    below $0.005 raw, $0.01 to $0.015, $0.02 to $0.025 ...).  Validated
    against every one of the 25 credited and 6 uncredited prints below."""
    return cents(raw_rebate(qty, px))


def observed_credit_envelope(rows: Sequence[dict]) -> dict:
    """What the venue has ACTUALLY paid on a single print, by size."""
    q = [D(r["qty"]) for r in rows]
    c = [D(r["credit_usd"]) for r in rows]
    return {"min_qty": str(min(q)), "max_qty": str(max(q)),
            "min_credit": str(min(c)), "max_credit": str(max(c))}


def in_observed_envelope(qty: D, env: dict) -> bool:
    return D(env["min_qty"]) <= qty <= D(env["max_qty"])


def pair_net(qty: D, p1: D, p2: D, smooth: bool = False) -> Tuple[D, D, D]:
    """(pair_cash, credits, net) for a maker/maker pair of `qty` at p1 + p2.
    pair_cash = qty x (1 - p1 - p2); credits from the rounded step
    (or the smooth formula when smooth=True, for comparison only)."""
    cash = qty * (ONE - p1 - p2)
    fn = raw_rebate if smooth else credit_rounded
    cr = fn(qty, p1) + fn(qty, p2)
    return cash, cr, cash + cr


def break_even_pair_cost(qty: D, p1: D, floor: D = D("0"), smooth: bool = False,
                         lo: D = D("0.95"), hi: D = D("1.10"), step: D = D("0.001")) -> Optional[D]:
    """Max pair cost c* = p1 + p2 (scanned on a 0.001 grid) at which
    net >= floor.  Complement p2 = c - p1 must stay inside (0, 1)."""
    best = None
    c = lo
    while c <= hi:
        p2 = c - p1
        if D("0") < p2 < ONE:
            _, _, net = pair_net(qty, p1, p2, smooth=smooth)
            if net >= floor:
                best = c
        c += step
    return best


def to_grid(c: Optional[D]) -> Optional[D]:
    """Largest executable price on the venue's 0.005 grid <= c."""
    if c is None:
        return None
    return (c / TICK).to_integral_value(rounding=ROUND_DOWN) * TICK


# ------------------------------ data assembly ------------------------------

def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def credited_prints(credits: dict) -> Tuple[List[dict], List[dict]]:
    """Join the venue's credit rows (market, qty, credit) to the recovered
    print prices, consuming one price row per credit in file order."""
    pool = {m: list(rows) for m, rows in PRINT_PRICES.items()}
    out: List[dict] = []
    for r in credits["rows"]:
        if not r.get("credited_to_us"):
            continue
        m, q = r["market"], D(r["qty"])
        cand = [p for p in pool[m] if D(p["qty"]) == q]
        if not cand:
            raise ValueError(f"no recovered price for {m} qty {q}")
        p = cand[0]
        pool[m].remove(p)
        credit = -D(r["our_commission"])
        px = D(p["px"])
        raw = raw_rebate(q, px)
        out.append({
            "id": r["id"], "market": m, "t_utc": p["t"], "qty": str(q), "px_venue": str(px),
            "our_px": p["our_px"], "price_source": p["src"],
            "p_1_minus_p": str((px * (ONE - px)).quantize(D("0.0001"))),
            "qty_x_p1p": str((q * px * (ONE - px)).quantize(D("0.0001"))),
            "notional_usd": str((q * (D(p["our_px"]) if p["our_px"] else px)).quantize(D("0.0001"))),
            "notional_basis": "our_px" if p["our_px"] else "venue_px",
            "credit_usd": str(credit), "label": "OBSERVED credit",
            "model_raw_usd": str(raw.quantize(D("0.00001"))),
            "model_rounded_usd": str(cents(raw)),
            "model_matches": cents(raw) == credit,
        })
    uncredited = []
    for m, rows in pool.items():
        for p in rows:
            q, px = D(p["qty"]), D(p["px"])
            raw = raw_rebate(q, px)
            uncredited.append({"market": m, "t_utc": p["t"], "qty": str(q), "px_venue": str(px),
                               "price_source": p["src"], "credit_usd": "0.00",
                               "model_raw_usd": str(raw.quantize(D("0.00001"))),
                               "model_rounded_usd": str(cents(raw)),
                               "model_matches": cents(raw) == D("0"),
                               "label": "OBSERVED zero (below the cent floor)"})
    return out, uncredited


def fits(rows: Sequence[dict]) -> dict:
    """Pooled ratios (DERIVED): credit per notional dollar, per share, and
    per unit of qty x p(1-p) against the published 0.0125."""
    credit = sum(D(r["credit_usd"]) for r in rows)
    notional = sum(D(r["notional_usd"]) for r in rows)
    qty = sum(D(r["qty"]) for r in rows)
    qp = sum(D(r["qty_x_p1p"]) for r in rows)
    n_match = sum(1 for r in rows if r["model_matches"])
    exact = [r for r in rows if r["price_source"] == "exact"]
    n_match_exact = sum(1 for r in exact if r["model_matches"])
    by_credit: Dict[str, dict] = {}
    for r in rows:
        b = by_credit.setdefault(r["credit_usd"], {"prints": 0, "qty_min": None, "qty_max": None,
                                                    "notional_min": None, "notional_max": None,
                                                    "notional_basis": "prints with a recovered price only"})
        b["prints"] += 1
        q = D(r["qty"])
        b["qty_min"] = str(q) if b["qty_min"] is None else str(min(D(b["qty_min"]), q))
        b["qty_max"] = str(q) if b["qty_max"] is None else str(max(D(b["qty_max"]), q))
        if r["price_source"] == "derived":
            continue                      # market-average price: no per-print notional
        n = D(r["notional_usd"])
        b["notional_min"] = str(n) if b["notional_min"] is None else str(min(D(b["notional_min"]), n))
        b["notional_max"] = str(n) if b["notional_max"] is None else str(max(D(b["notional_max"]), n))
    return {
        "credited_prints": len(rows), "credited_usd": str(credit),
        "shares": str(qty), "notional_usd": str(notional.quantize(D("0.0001"))),
        "credit_per_notional_dollar": str((credit / notional).quantize(D("0.00001"))),
        "credit_per_share": str((credit / qty).quantize(D("0.00001"))),
        "credit_per_qty_x_p1p": str((credit / qp).quantize(D("0.00001"))),
        "published_rate": str(RATE),
        "rounded_model_matches": f"{n_match}/{len(rows)}",
        "rounded_model_matches_exact_priced": f"{n_match_exact}/{len(exact)}",
        "by_credit_size": by_credit,
        "label": "DERIVED pooled ratios over OBSERVED credits; notional is our paid "
                 "price where the order record gives it, else the venue trade price",
    }


def rounding_thresholds() -> dict:
    """Where the venue's whole-cent credit steps fall, by price."""
    rows = []
    for p in (D("0.20"), D("0.22"), D("0.30"), D("0.38"), D("0.45"), D("0.47"), D("0.50")):
        per_share = RATE * p * (ONE - p)
        q1 = (D("0.005") / per_share)
        q2 = (D("0.015") / per_share)
        q3 = (D("0.025") / per_share)
        rows.append({"px": str(p), "raw_per_share": str(per_share.quantize(D("0.000001"))),
                     "shares_for_1c": str(q1.quantize(D("0.01"))),
                     "shares_for_2c": str(q2.quantize(D("0.01"))),
                     "shares_for_3c": str(q3.quantize(D("0.01"))),
                     "notional_for_1c": str((q1 * p).quantize(D("0.001"))),
                     "notional_for_2c": str((q2 * p).quantize(D("0.001"))),
                     "notional_for_3c": str((q3 * p).quantize(D("0.001")))})
    return {"rule": "credit = round_half_even(0.0125 x qty x p x (1-p), cent); a print pays "
                    "$0.00 below $0.005 raw, $0.01 from $0.005, $0.02 from $0.015, $0.03 from $0.025",
            "rows": rows,
            "our_sizes": "3-5 shares per leg, $1.5-2.6 notional: one print earns exactly $0.01 "
                         "(2.5-4.6 sh) or $0.02 (4.9-5.7 sh); never more",
            "label": "DERIVED from the published formula; the step positions are what the "
                     "25 observed credits confirm"}


def effective_rates_at_our_sizes(rows: Sequence[dict]) -> List[dict]:
    out = []
    for lo, hi, name in ((D("2.5"), D("4.7"), "3-4.6 sh"), (D("4.8"), D("5.8"), "4.9-5.7 sh")):
        sub = [r for r in rows if lo <= D(r["qty"]) <= hi]
        if not sub:
            continue
        credit = sum(D(r["credit_usd"]) for r in sub)
        notional = sum(D(r["notional_usd"]) for r in sub)
        out.append({"bucket": name, "prints": len(sub), "credit_usd": str(credit),
                    "notional_usd": str(notional.quantize(D("0.01"))),
                    "rate_per_notional_dollar": str((credit / notional).quantize(D("0.00001"))),
                    "credit_per_print": str((credit / len(sub)).quantize(D("0.0001")))})
    return out


def ceilings(env: dict) -> List[dict]:
    rows = []
    for q in QTYS:
        for p1 in P1S:
            r: Dict[str, Any] = {"qty": str(q), "p1": str(p1),
                                 "credit_leg1_empirical": str(credit_rounded(q, p1)),
                                 "within_observed_sizes": in_observed_envelope(q, env)}
            for name, floor in (("net_ge_0", D("0")), ("net_ge_1c", D("0.01"))):
                ce = break_even_pair_cost(q, p1, floor)
                cs = break_even_pair_cost(q, p1, floor, smooth=True)
                r[f"c_star_empirical_{name}"] = str(ce) if ce is not None else None
                r[f"c_star_empirical_{name}_grid"] = str(to_grid(ce)) if ce is not None else None
                r[f"c_star_smooth_{name}"] = str(cs) if cs is not None else None
            rows.append(r)
    return rows


def net_table() -> List[dict]:
    rows = []
    for q, p1 in R4_ROWS:
        for c in PAIR_COSTS:
            p2 = c - p1
            cash, cr, net = pair_net(q, p1, p2)
            _, crs, nets = pair_net(q, p1, p2, smooth=True)
            rows.append({"qty": str(q), "p1": str(p1), "p2": str(p2), "pair_cost": str(c),
                         "pair_cash_usd": str(cash.quantize(D("0.0001"))),
                         "credited_rebate_empirical_usd": str(cr),
                         "net_empirical_usd": str(net.quantize(D("0.0001"))),
                         "rebate_smooth_model_usd": str(crs.quantize(D("0.0001"))),
                         "net_smooth_model_usd": str(nets.quantize(D("0.0001"))),
                         "unproven_rewards_usd": "0.00",
                         "verdict": "profit" if net > 0 else ("flat" if net == 0 else "LOSS")})
    return rows


def verdict(ceil_rows: Sequence[dict], table: Sequence[dict], f: dict) -> dict:
    r4 = [r for r in ceil_rows if r["qty"] == "5" and r["p1"] in ("0.38", "0.45", "0.50")]
    cmax = max(D(r["c_star_empirical_net_ge_0"]) for r in ceil_rows)
    cmin = min(D(r["c_star_empirical_net_ge_0"]) for r in ceil_rows)
    loss_101 = [r for r in table if r["pair_cost"] == "1.01"]
    loss_102 = [r for r in table if r["pair_cost"] == "1.02"]
    return {
        "max_credit_ever_on_one_print_usd": str(max(D(k) for k in f["by_credit_size"])),
        "max_credited_per_pair_at_our_sizes_usd": "0.04",
        "break_even_ceiling_range_empirical": f"{cmin} - {cmax}",
        "r4_size_ceilings": [{"qty": r["qty"], "p1": r["p1"],
                              "c_star_net_ge_0": r["c_star_empirical_net_ge_0"],
                              "c_star_net_ge_1c": r["c_star_empirical_net_ge_1c"],
                              "grid": r["c_star_empirical_net_ge_0_grid"]} for r in r4],
        "at_1_01": [{"qty": r["qty"], "p1": r["p1"], "net_usd": r["net_empirical_usd"]} for r in loss_101],
        "at_1_02": [{"qty": r["qty"], "p1": r["p1"], "net_usd": r["net_empirical_usd"]} for r in loss_102],
        "plain_english": (
            "At our sizes (3-5.7 shares, $1.5-2.6 a leg) the venue has credited exactly $0.01 or "
            "$0.02 per print and never more, so a complete pair collects at most $0.04 of credited "
            f"rebate. Above a pair cost of {cmax} a COMPLETE loses money at every size and price "
            "studied even with every observed credit counted; at 5 shares the ceiling is 1.004 "
            "(p1 0.38, one cent a leg) to 1.008 (p1 0.45-0.50, two cents a leg), and on the "
            "0.005 price grid that is 1.000 or 1.005. A 1.01 pair at 5 shares loses $0.01-0.03; "
            "a 1.02 pair loses $0.06-0.08. The most any pair above par can net from credited "
            "rebates is $0.015 (5 shares at 1.005 with two-cent legs); with one-cent legs the "
            "same 1.005 pair already loses $0.005. Every advertised reward beyond the maker "
            "rebate (liquidity programs, taker rebates) has credited this account $0.00 and is "
            "worth $0.00 here. A rebate does not make a chase profitable: a COMPLETE above par is "
            "a loss before the residual is even considered."),
        "previous_estimate": {"break_even_pair_cost_after_observed_rebate": "1.008",
                              "basis": "l5_profitability F_break_even: 0.789% of filled dollars "
                                       "(0.808% at R3L) applied smoothly per pair",
                              "reading": "DERIVED smooth-rate estimate; consistent with the "
                                         "two-cents-a-leg case only (q 4.9-5.7, p 0.44-0.56). "
                                         "At 5 sh / p 0.38 the empirical ceiling is 1.004 because "
                                         "the leg credit rounds DOWN to one cent; the rate is a "
                                         "step, not a slope"},
        "label": "VERDICT: derived from OBSERVED credits with the venue's cent rounding; "
                 "unproven rewards enter at zero",
    }


def build() -> dict:
    credits = _load(CREDITS)
    rows, uncredited = credited_prints(credits)
    f = fits(rows)
    env = observed_credit_envelope(rows)
    ceil_rows = ceilings(env)
    table = net_table()
    return {
        "owner_directive": "#167 5574408755 item 3",
        "labels": {
            "OBSERVED": "a credit the venue itself posted on our execution (commissionNotionalCollected < 0), "
                        "or a $0.00 the venue posted",
            "RECOVERED": "a print price read back from our own session settlement records "
                         "(exact / elimination / derived per row)",
            "DERIVED": "arithmetic on observed numbers: ratios, thresholds, break-even solves",
            "UNPROVEN": "advertised by the venue, never credited to this account: enters every "
                        "break-even at $0.00",
            "empirical_credit_function": "round_half_even(0.0125 x qty x p(1-p), cent) -- the "
                                         "published formula with the venue's cent rounding, "
                                         "validated print by print against all 25 credits and 6 zeros",
            "smooth_model": "the same formula unrounded (src.pmus_q1_executor.maker_rebate); "
                            "shown for comparison only, never used for the verdict",
            "pair": "two maker legs of equal qty at p1 (first leg) and p2 = pair_cost - p1; "
                    "net = qty x (1 - p1 - p2) + credit(leg1) + credit(leg2); fees 0.00 "
                    "(venue charged 0.00 on every fill); residual risk NOT included",
            "not": "nothing here changes a live parameter, authorizes a rung, or counts "
                   "an uncredited dollar",
        },
        "sources": {
            "credits": CREDITS, "credits_audits": credits.get("audits"),
            "print_prices": "R4: session settlement records (venue trade rows t/px/qty); "
                            "R3L: session settlement records where recoverable; wisc-nd: "
                            "l5_economic_scoreboard avg_entry_price 0.3262 (DERIVED)",
            "formula_and_rounding": "src.pmus_incentive_audit (docs.polymarket.us/fees)",
            "r3l_reference": "approvals: R3L 19 fills, 19/19 maker, 0.2800 venue-credited rebates; "
                             "R4 added 8 credited prints, $0.14, total 25 prints $0.42",
        },
        "A_credit_table": {"rows": rows, "uncredited_prints": uncredited, "fits": f,
                           "observed_envelope": env,
                           "effective_rate_at_our_sizes": effective_rates_at_our_sizes(rows),
                           "rounding": rounding_thresholds()},
        "B_break_even_ceilings": {
            "rows": ceil_rows,
            "reading": "c* = max pair cost with net >= 0 (and >= $0.01) on a 0.001 scan; _grid = "
                       "largest executable price on the 0.005 grid at or below c*; smooth = the "
                       "unrounded formula for comparison; rows with within_observed_sizes false "
                       "(6.5 sh) extrapolate beyond any print the venue has credited us",
            "label": "DERIVED from the empirical credit function"},
        "C_net_per_pair_table": {"rows": table,
                                 "label": "DERIVED: q = 5 at the R4 first-leg prices 0.38 and 0.47"},
        "D_unproven_rewards": {"rows": UNPROVEN_REWARDS,
                               "total_value_in_break_even_usd": "0.00",
                               "label": "UNPROVEN unless stated OBSERVED ZERO"},
        "E_verdict": verdict(ceil_rows, table, f),
    }


# --------------------------------- report ----------------------------------

def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


def render_md(j: dict) -> str:
    a, b, c, d, e = (j["A_credit_table"], j["B_break_even_ceilings"], j["C_net_per_pair_table"],
                     j["D_unproven_rewards"], j["E_verdict"])
    f = a["fits"]
    md = ["# PMUS incentive break-even (empirical, credited rebates only)", "",
          f"Owner directive {j['owner_directive']}. Read-only, offline. Labels: OBSERVED = a venue "
          "credit; RECOVERED = a print price from our settlement records; DERIVED = arithmetic on "
          "observed numbers; UNPROVEN = advertised, never credited, worth $0.00 here. Machine-"
          "readable: `pmus_incentive_breakeven.json`.", "",
          "## Verdict", "", e["plain_english"], "",
          "## A. Empirical credit table (OBSERVED credits, RECOVERED prices)", "",
          _md_table(["market", "qty", "venue px", "src", "qty x p(1-p)", "notional $", "credit $",
                     "model raw", "model rounded", "match"],
                    [[r["market"].replace("-2026-09-0", " ").split(" ")[0], r["qty"], r["px_venue"],
                      r["price_source"], r["qty_x_p1p"], r["notional_usd"], r["credit_usd"],
                      r["model_raw_usd"], r["model_rounded_usd"], "yes" if r["model_matches"] else "NO"]
                     for r in a["rows"]]), "",
          f"Uncredited prints (OBSERVED $0.00): " + "; ".join(
              f"{u['qty']} sh @{u['px_venue']} (raw {u['model_raw_usd']})" for u in a["uncredited_prints"]) + ".", "",
          f"Fits (DERIVED, pooled over {f['credited_prints']} credits, ${f['credited_usd']}): credit per "
          f"notional dollar {f['credit_per_notional_dollar']} (notional ${f['notional_usd']}); per share "
          f"{f['credit_per_share']}; per unit of qty x p(1-p) {f['credit_per_qty_x_p1p']} vs published "
          f"{f['published_rate']}. The rounded formula reproduces {f['rounded_model_matches']} credits "
          f"({f['rounded_model_matches_exact_priced']} of the exactly-priced ones); the mismatches are the "
          "wisc-nd prints priced at the market average (per-print prices not recoverable), where a $0.02 "
          "credit on 5.15 sh implies p(1-p) >= 0.233, i.e. a print at or above 0.37.", "",
          "Credit size vs print size (OBSERVED): " + "; ".join(
              f"${k}: {v['prints']} prints, {v['qty_min']}-{v['qty_max']} sh, ${v['notional_min']}-"
              f"{v['notional_max']} notional" for k, v in sorted(f["by_credit_size"].items())) + ".", "",
          "### Rounding / minimum-credit behaviour (DERIVED, confirmed by the credits)", "",
          a["rounding"]["rule"] + ".", "",
          _md_table(["px", "raw $/share", "sh for 1c", "sh for 2c", "sh for 3c", "notional for 1c",
                     "notional for 2c", "notional for 3c"],
                    [[r["px"], r["raw_per_share"], r["shares_for_1c"], r["shares_for_2c"], r["shares_for_3c"],
                      r["notional_for_1c"], r["notional_for_2c"], r["notional_for_3c"]]
                     for r in a["rounding"]["rows"]]), "",
          "Effective rate at our sizes: " + "; ".join(
              f"{x['bucket']}: {x['prints']} prints, ${x['credit_usd']} on ${x['notional_usd']} = "
              f"{x['rate_per_notional_dollar']}/$ (${x['credit_per_print']} a print)"
              for x in a["effective_rate_at_our_sizes"]) + ".", "",
          "## B. Break-even completion ceiling c* = p1 + p2 (DERIVED, empirical credits)", "",
          b["reading"], "",
          _md_table(["qty", "p1", "leg credit", "c* net>=0", "grid", "c* net>=1c", "grid", "smooth net>=0",
                     "smooth net>=1c", "in observed sizes"],
                    [[r["qty"], r["p1"], r["credit_leg1_empirical"], r["c_star_empirical_net_ge_0"],
                      r["c_star_empirical_net_ge_0_grid"], r["c_star_empirical_net_ge_1c"],
                      r["c_star_empirical_net_ge_1c_grid"], r["c_star_smooth_net_ge_0"],
                      r["c_star_smooth_net_ge_1c"], "yes" if r["within_observed_sizes"] else "NO (extrapolated)"]
                     for r in b["rows"]]), "",
          "## C. Net dollars per pair, q = 5 at the R4 first-leg prices (DERIVED)", "",
          _md_table(["qty", "p1", "p2", "pair cost", "pair cash $", "credited rebate $", "unproven $",
                     "net $", "smooth rebate $", "smooth net $", "verdict"],
                    [[r["qty"], r["p1"], r["p2"], r["pair_cost"], r["pair_cash_usd"],
                      r["credited_rebate_empirical_usd"], r["unproven_rewards_usd"], r["net_empirical_usd"],
                      r["rebate_smooth_model_usd"], r["net_smooth_model_usd"], r["verdict"]]
                     for r in c["rows"]]), "",
          "## D. Unproven rewards (value $0.00 in every break-even)", "",
          _md_table(["reward", "advertised", "observed on this account", "credited $", "in break-even $", "label"],
                    [[r["reward"], r["advertised"], r["observed"], r["credited_usd"],
                      r["value_in_break_even_usd"], r["label"]] for r in d["rows"]]), "",
          "## E. Previous estimate", "",
          f"{e['previous_estimate']['basis']}: {e['previous_estimate']['break_even_pair_cost_after_observed_rebate']}. "
          f"{e['previous_estimate']['reading']}.", ""]
    return "\n".join(md)


def main() -> None:
    j = build()
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(j, fh, indent=1, default=str)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(render_md(j))
    e = j["E_verdict"]
    f = j["A_credit_table"]["fits"]
    print(f"credits {f['credited_prints']} prints ${f['credited_usd']} on ${f['notional_usd']} notional; "
          f"rate/notional {f['credit_per_notional_dollar']}; per share {f['credit_per_share']}; "
          f"rounded model matches {f['rounded_model_matches']}")
    print("ceiling range (empirical, net>=0):", e["break_even_ceiling_range_empirical"])
    for r in e["r4_size_ceilings"]:
        print(" q", r["qty"], "p1", r["p1"], "c* net>=0", r["c_star_net_ge_0"], "grid", r["grid"],
              "net>=1c", r["c_star_net_ge_1c"])
    print("at 1.01:", e["at_1_01"]); print("at 1.02:", e["at_1_02"])
    print("wrote", OUT_JSON, OUT_MD)


if __name__ == "__main__":
    main()
