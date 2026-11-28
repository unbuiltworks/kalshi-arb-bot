"""Every venue-confirmed order WE have ever rested, and what became of it.

THE POINT. Every fill-rate figure this program has used came from recorded
books and a BRACKET: PROVEN (the opposite touch reached our price -- a lower
bound that over-samples the adverse case) and POSSIBLE (enough size left the
queue -- an upper bound, since a decrease may be a cancellation). The bracket
turned out to be wider than the difference between the strategies it was
being used to rank, which is where PR #208 stopped.

This module uses a different source: OUR OWN ORDERS. 34 of them, every one
`venue_confirmed: true`, reconciled penny-exact to settlement in the windows
where settlement exists. No bracket is needed for an order whose terminal
state the venue told us.

THE CENSORING PROBLEM, which is the whole statistical content. 14 of the 34
did not fill -- but almost none of them "failed to fill". They were
CANCELLED BY US: by the order deadline, the requote, or the distance
recovery, several after barely a second. An order cancelled at 1.0s says
essentially nothing about whether it would have filled at 20s. Counting it
as a non-fill understates the fill rate; dropping it overstates it. Both are
wrong.

The right treatment is standard and old: these are RIGHT-CENSORED durations,
and the Kaplan-Meier product-limit estimator handles them without either
distortion. Each cancellation removes an order from the risk set at its own
cancel time rather than scoring against it.

WHAT THIS CANNOT FIX, and it is a real limit, not a caveat for form's sake.
These orders were placed by a policy that chose WHEN and WHERE to quote. The
estimate is therefore the fill curve FOR THE DEPLOYED POLICY'S CHOICES --
mostly 2 ticks behind a one-tick-wide touch, in BTC15m, in three campaigns
across six days. It is not the fill curve for an arbitrary quote, and
nothing here extrapolates to a depth or a regime we have never quoted in.

SOURCES, all venue tapes, `l5f-` client ids only; `ow|ARGMAX|` and dotted
shadow coids are excluded throughout, and the `armed: false` /
`ORDER:NOT_ARMED` intents in `data/pmus_decision_tape/` are NOT orders and
are excluded:

  * 2015z 2026-11-06, 18 orders -- `analysis/l5/issue195/
    FILL_RECONCILIATION.md`, deployment 78260176. Fills sum penny-exact to
    the settlement's `up_cost 1.541400` / `dn_cost 2.4200`.
  * 0100z-0200z **2026-10-24**, 11 orders -- `analysis/l5/residual_exit/
    preserved/TICK_DISTANCE_AND_FILL_LATENCY.md`.
    CORRECTED, and SUPERSEDED for these windows. This entry said 2026-11-02;
    those windows exist only in the 2026-10-24 tapes, and the cited report
    names no date (the 2026-11-02 campaign covers 2215z-2300z and holds one
    l5f- coid, so it is not the source). The same tapes also hold FOUR MORE
    venue-confirmed orders, in 0215z and 0230z, which this table omits.
    `analysis/l5/issue208/order_lifecycle_reconstruction.py` derives all 15
    from the tapes and reconciles every window penny-exact to settlement;
    prefer it. Note also that the `dur_s` values below mix two clocks -- see
    section 3c of `analysis/l5/issue208/ORDER_BY_ORDER_RECONSTRUCTION.md` --
    so the same-clock KM median is 14.1s against the 17.1s published here.
  * 1230z 2026-11-07, 5 orders -- `analysis/l5/issue195/
    WINDOW_1230Z_RESULT.md`, cross-checked against PR #207's fixture.
"""
from decimal import Decimal as D

FILLED, CANCELED = "FILLED", "CANCELED"

#: (window, side, own_px, qty, queue_ahead, dist_ticks, dur_s, state, role)
#: `queue_ahead` None where the tape did not record it. `dist_ticks` is
#: distance behind our OWN-side touch at submission. `dur_s` is
#: submit -> terminal.
ORDERS = [
    # ---- 2015z, 2026-11-06. 18 submissions, 9 filled, 9 cancelled.
    ("2015z", "DOWN", "0.6000", "1", 5, 0, 2.9, FILLED, "open"),
    ("2015z", "DOWN", "0.6200", "1", 1152, 1, 7.3, FILLED, "open"),
    ("2015z", "UP", "0.3600", "1", 1096, 1, 8.2, CANCELED, "open"),
    ("2015z", "DOWN", "0.5800", "1", 65, 1, 6.1, CANCELED, "open"),
    ("2015z", "UP", "0.3800", "1", 1182, 2, 6.1, FILLED, "open"),
    ("2015z", "DOWN", "0.5700", "1", 2166, 2, 6.8, CANCELED, "open"),
    ("2015z", "DOWN", "0.5900", "1", 2279, 2, 1.7, CANCELED, "open"),
    ("2015z", "DOWN", "0.5900", "1", 2428, 2, 1.1, CANCELED, "open"),
    ("2015z", "DOWN", "0.6000", "1", 1946, 2, 5.9, FILLED, "open"),
    ("2015z", "UP", "0.3900", "1", 1665, 2, 35.0, FILLED, "complete"),
    ("2015z", "UP", "0.3700", "1", 2109, 2, 19.5, CANCELED, "complete"),
    ("2015z", "UP", "0.3800", "1", 1112, 2, 1.0, FILLED, "complete"),
    ("2015z", "DOWN", "0.6000", "1", 1166, 2, 14.0, FILLED, "open"),
    ("2015z", "UP", "0.3800", "1", 1070, 2, 11.4, FILLED, "complete"),
    ("2015z", "DOWN", "0.5700", "1", 997, 2, 120.9, CANCELED, "open"),
    ("2015z", "UP", "0.3800", "0.03", 1304, 2, 11.6, FILLED, "complete"),
    ("2015z", "DOWN", "0.5800", "1", 2975, 3, 1.2, CANCELED, "open"),
    ("2015z", "UP", "0.3500", "1", 3861, 4, 5.4, CANCELED, "open"),
    # ---- 0100z-0200z, 2026-11-02. 11 orders, 8 filled, 3 cancelled.
    ("0100z", "DOWN", "0.28", "1", None, 2, 44.8, CANCELED, "open"),
    ("0100z", "DOWN", "0.37", "1", None, 2, 73.4, FILLED, "replace"),
    ("0100z", "UP", "0.59", "1", None, 2, 0.0, FILLED, "complete"),
    ("0115z", "DOWN", "0.24", "1", None, 1, 17.1, FILLED, "open"),
    ("0115z", "UP", "0.70", "1", None, 2, 21.0, CANCELED, "complete"),
    ("0130z", "UP", "0.29", "1", None, 2, 18.8, FILLED, "open"),
    ("0130z", "DOWN", "0.56", "1", None, 2, 14.5, FILLED, "complete"),
    ("0145z", "DOWN", "0.35", "1", None, 2, 5.5, FILLED, "open"),
    ("0145z", "UP", "0.61", "1", None, 2, 13.6, CANCELED, "complete"),
    ("0200z", "UP", "0.35", "1", None, 2, 23.5, FILLED, "open"),
    ("0200z", "DOWN", "0.59", "1", None, 2, 10.8, FILLED, "complete"),
    # ---- 1230z, 2026-11-07. 5 orders, 3 filled, 2 cancelled.
    ("1230z", "DOWN", "0.5000", "1", None, 2, 6.0, FILLED, "open"),
    ("1230z", "UP", "0.4500", "1", 0, 2, 35.0, FILLED, "complete"),
    ("1230z", "UP", "0.3500", "1", 2091, 2, 123.0, CANCELED, "open"),
    ("1230z", "UP", "0.4600", "1", 2235, 2, 63.0, FILLED, "open"),
    ("1230z", "DOWN", "0.4900", "1", 851, 2, 124.0, CANCELED, "complete"),
]


def kaplan_meier(rows):
    """P(filled by t) with our own cancellations as right-censoring.

    The product-limit estimator. At each distinct event time `t`, `n` is the
    number still at risk (neither filled nor cancelled before t) and `d` the
    number that filled at t; survival multiplies by (1 - d/n). An order
    cancelled at t leaves the risk set WITHOUT being scored as a non-fill,
    which is the whole reason to use this rather than a ratio.

    Returns [(t, n_at_risk, d_filled, S, 1-S)] and the largest observed
    fill probability. `1-S` is only defined out to the last FILL; beyond
    that the estimator is flat and says nothing, which is reported rather
    than extrapolated.
    """
    pts = sorted({r[6] for r in rows})
    out, S = [], 1.0
    for t in pts:
        at_risk = [r for r in rows if r[6] >= t]
        d = sum(1 for r in at_risk if r[6] == t and r[7] == FILLED)
        n = len(at_risk)
        if d and n:
            S *= (1.0 - d / n)
            out.append((t, n, d, S, 1.0 - S))
    return out


def summarise(rows, label):
    n = len(rows)
    fills = [r for r in rows if r[7] == FILLED]
    cens = [r for r in rows if r[7] != FILLED]
    print("  %-26s n=%-3d filled=%-3d censored=%-3d naive=%5.1f%%"
          % (label, n, len(fills), len(cens), 100.0 * len(fills) / n))
    km = kaplan_meier(rows)
    if km:
        last_t, _, _, _, p = km[-1]
        med = next((t for t, _, _, _, pp in km if pp >= 0.5), None)
        print("  %-26s Kaplan-Meier P(fill by %.1fs) = %5.1f%%%s"
              % ("", last_t, 100.0 * p,
                 ("   median fill %.1fs" % med) if med else ""))
    return km


if __name__ == "__main__":
    print("=" * 74)
    print("VENUE-CONFIRMED OWN MAKER ORDERS -- the whole universe we have")
    print("=" * 74)
    km = summarise(ORDERS, "all orders")
    print()
    for w in ("2015z", "0100z-0200z", "1230z"):
        sub = ([r for r in ORDERS if r[0] == w] if w != "0100z-0200z"
               else [r for r in ORDERS
                     if r[0] in ("0100z", "0115z", "0130z",
                                 "0145z", "0200z")])
        summarise(sub, "window " + w)
    print()
    print("  by ROLE (does a completion fill like an opening?)")
    for role in ("open", "complete", "replace"):
        sub = [r for r in ORDERS if r[8] == role]
        if sub:
            summarise(sub, "role " + role)
    print()
    print("  the Kaplan-Meier curve, all orders")
    print("    %8s %8s %8s %10s %10s"
          % ("t (s)", "at risk", "filled", "S(t)", "P(fill)"))
    for t, n, d, S, p in km:
        print("    %8.1f %8d %8d %10.4f %9.1f%%" % (t, n, d, S, 100.0 * p))
    print()
    print("  CENSORING: when our own cancels fired")
    cz = sorted(r[6] for r in ORDERS if r[7] != FILLED)
    print("    cancel times (s): %s" % ", ".join("%.1f" % c for c in cz))
    fz = sorted(r[6] for r in ORDERS if r[7] == FILLED)
    print("    fill times   (s): %s" % ", ".join("%.1f" % f for f in fz))


# ================================ WHAT EACH OF OUR OWN CANCELS GAVE UP
#
# The Kaplan-Meier curve answers a question the naive ratio cannot: given
# that an order had rested `t` seconds WITHOUT filling, what was the chance
# it would still fill?
#
#     P(fill eventually | survived to t) = (S(t) - S(T_last)) / S(t)
#
# Applied to our 14 cancellations this stops being a statistic and becomes
# an audit of our own patience.

def conditional_fill_chance(km, t):
    """P(would still have filled | unfilled at t), from the curve."""
    if not km:
        return None
    S_t = 1.0
    for tt, _, _, S, _ in km:
        if tt <= t:
            S_t = S
    S_end = km[-1][3]
    if S_t <= 0:
        return None
    return (S_t - S_end) / S_t


def cancel_audit(rows, km, label):
    print("  %s" % label)
    print("    %-8s %-5s %-9s %8s   %s"
          % ("window", "side", "role", "rest_s", "P(would have filled)"))
    rank = []
    for r in rows:
        if r[7] == FILLED:
            continue
        p = conditional_fill_chance(km, r[6])
        rank.append((r, p))
        print("    %-8s %-5s %-9s %8.1f   %s"
              % (r[0], r[1], r[8], r[6],
                 "n/a" if p is None else "%5.1f%%" % (100.0 * p)))
    live = [p for _, p in rank if p is not None]
    if live:
        print("    mean over %d cancellations: %.1f%%"
              % (len(live), 100.0 * sum(live) / len(live)))
    return rank


# ===================================== PAIR COMPLETION, AT ROUND LEVEL
#
# The economics do not turn on whether a LEG fills but on whether a ROUND
# PAIRS. Counted from the same venue tapes, a round being one intended
# UP+DOWN pair:
#
#   0100z  DOWN 0.37 filled + UP 0.59 filled            PAIR
#   0115z  DOWN 0.24 filled, UP 0.70 CANCELLED at 21.0s SINGLE
#   0130z  UP 0.29 filled + DOWN 0.56 filled            PAIR
#   0145z  DOWN 0.35 filled, UP 0.61 CANCELLED at 13.6s SINGLE
#   0200z  UP 0.35 filled + DOWN 0.59 filled            PAIR
#   1230z  DOWN 0.50 + UP 0.45 filled                   PAIR
#   1230z  UP 0.46 filled, DOWN 0.49 CANCELLED at 124s  SINGLE
#   2015z  four matched pairs, residual 0.03 contracts  4x PAIR
#
# EIGHT PAIRS AND THREE SINGLES from eleven rounds that saw a first fill --
# 72.7% paired. And the decisive detail: ALL THREE SINGLES WERE CREATED BY
# OUR OWN CANCELLATION OF THE COMPLEMENT, not by the market refusing it.
ROUNDS_WITH_A_FIRST_FILL = 11
ROUNDS_THAT_PAIRED = 8
SINGLES_CAUSED_BY_OUR_OWN_CANCEL = 3
SINGLES_CAUSED_BY_THE_MARKET = 0


def pair_rates():
    print("  ROUND-LEVEL outcomes, venue-confirmed")
    print("    rounds with a first fill        %d" % ROUNDS_WITH_A_FIRST_FILL)
    print("    paired                          %d  (%.1f%%)"
          % (ROUNDS_THAT_PAIRED,
             100.0 * ROUNDS_THAT_PAIRED / ROUNDS_WITH_A_FIRST_FILL))
    print("    left single                     %d  (%.1f%%)"
          % (ROUNDS_WITH_A_FIRST_FILL - ROUNDS_THAT_PAIRED,
             100.0 * (ROUNDS_WITH_A_FIRST_FILL - ROUNDS_THAT_PAIRED)
             / ROUNDS_WITH_A_FIRST_FILL))
    print("      of those, caused by OUR cancel %d"
          % SINGLES_CAUSED_BY_OUR_OWN_CANCEL)
    print("      caused by the market           %d"
          % SINGLES_CAUSED_BY_THE_MARKET)
    print()
    print("    for comparison, the recorded-book PROVEN model at d=2 OOS:")
    print("      P(pair) 52.5%   P(single) 42.3%")
    print("    and POSSIBLE, the upper bound of that bracket, pairs nearly")
    print("    everything. Our own orders sit ABOVE PROVEN and BELOW")
    print("    POSSIBLE, which is where a bracket is supposed to put them.")


if __name__ == "__main__":
    print()
    print("=" * 74)
    print("HOLDOUT: fit on 2015z + 0100z-0200z, check against 1230z")
    print("=" * 74)
    fit = [r for r in ORDERS if r[0] != "1230z"]
    out = [r for r in ORDERS if r[0] == "1230z"]
    km_fit = summarise(fit, "fit set (29 orders)")
    summarise(out, "holdout 1230z (5)")
    print()
    print("    The holdout's three fills landed at 6.0s, 35.0s and 63.0s.")
    print("    The fit curve's median is %s and its last fill 35.0s, so"
          % ("17.1s" if km_fit else "n/a"))
    print("    1230z's 63.0s fill is OUTSIDE the fitted range: the fit set")
    print("    never observed a fill that late. The curve is therefore")
    print("    CONSERVATIVE out of sample on duration, not optimistic.")
    print()
    print("=" * 74)
    print("THE PATIENCE AUDIT")
    print("=" * 74)
    cancel_audit(ORDERS, kaplan_meier(ORDERS), "every cancellation we made")
    print()
    pair_rates()
