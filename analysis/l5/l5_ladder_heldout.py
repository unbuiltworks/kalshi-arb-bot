"""PHASE 2 -- WITHDRAWN AS A TEST OF MULTI-RUNG STRUCTURE. Corrected 2026-09-21.

THIS MODULE DOES NOT MODEL RUNGS. `band(R)` converts a rung COUNT into a band WIDTH
and `covered()` then asks only whether a price falls inside that band. Nothing here
indexes a rung, holds per-rung queue state, or distinguishes four orders from one
order spanning the same prices. R=4 and "a single order covering -2..+1" are the
SAME PREDICATE.

So the headline result -- held-out distance to L5 falling 1.229 -> 0.410 as R rises --
established only that A WIDER PRICE BAND CAPTURES MORE OF L5's FILLS. That is true by
construction, not a mechanism, and it is not evidence for a ladder. A null arm built
by widening a SINGLE rung to the same fill count scores 0.410, identical to the digit,
because it is the same computation.

WHAT STILL STANDS is Phase 1, which does not depend on this module: 3,434 transactions
in which L5 was filled at DIFFERENT PRICES on the SAME OUTCOME inside ONE atomic
transaction. A cancel/replace cannot occur inside an atomic transaction, so those
prove at least two simultaneously resting orders. The ladder is real. This module
never tested it.

Kept for provenance and for the recapture/coverage measurements, which are valid as
coverage statements. Do not cite its distance figures as fidelity evidence.

Original docstring follows.
--------------------------------------------------------------------------------
PHASE 2 -- does a multi-rung ladder representation explain HELD-OUT L5 windows
better than the one-quote-per-side representation we transferred to PMUS?

The test is run on L5 history alone. If the ladder does not improve L5 held-out
fidelity it is rejected and never reaches PMUS, whatever it might do to PMUS P&L.

CHRONOLOGICAL SPLIT. Maker-ladder windows ordered by first fill timestamp; the
earliest 70% are TRAIN, the latest 30% are OOS. No window spans the boundary.
Every frozen quantity below is computed on TRAIN ONLY and then not touched.

THE TEST IS A COVERAGE TEST, and its assumption is stated up front rather than
buried. We reconstruct each window's print tape from L5's OWN fills: an L5 maker
buy of q shares of outcome O at price p means a counterparty sold q of O at p at
that instant. That tape is a LOWER BOUND on real flow -- everything traded by
other participants is invisible to us. We then ask, for a policy resting R rungs
on the acquiring side:

    was the price of L5's actual fill inside the band our policy was covering
    at that moment?

ASSUMPTION, EXPLICIT AND OPTIMISTIC: coverage is scored as a fill, i.e. we assume
we would have won the queue at that price. That makes every recapture number an
UPPER bound. It is applied IDENTICALLY to the 1-rung and R-rung arms, so the
COMPARISON between arms is not biased by it even though each arm's level is.
The queue-aware lower bound is reported alongside in the audit.

WHAT IS COMPARED. The recaptured subsequence is scored against L5's actual OOS
sequence on the nine statistics that describe the inventory process, not on P&L.
"""
import collections, statistics as stt

TICK = 0.01


def split_chronological(windows, frac=0.7):
    """windows: {window_id: [fills]}. Split on the FIRST fill timestamp."""
    order = sorted(windows, key=lambda k: float(windows[k][0]["timestamp"]))
    cut = int(len(order) * frac)
    return order[:cut], order[cut:]


def tape(fills):
    """Lower-bound print tape implied by L5's own fills, in the filled side's own
    price. Each entry: (t, outcome, price, size)."""
    return [(float(x["timestamp"]), x["outcome"], float(x["price"]), float(x["quantity"]))
            for x in fills]


def band(rungs):
    """The frozen ladder geometry: how many ticks BELOW and ABOVE the touch the
    rungs span. STRADDLING, not one-sided.

    Identified on TRAIN ONLY. For R=4 the TRAIN recapture of L5's own fills is
    69.8% at -2..+1, against 53.7% for the purely passive -3..0 and 51.6% for the
    purely aggressive 0..+3, so the evidence says L5's ladder sits ACROSS the last
    trade rather than behind it. The centred rule below reproduces that TRAIN
    optimum at R=4 and is the single frozen rule used at every R, leaving the rung
    COUNT as the only thing an ablation varies."""
    return rungs // 2, (rungs + 1) // 2 - 1


def covered(px, touch, rungs):
    """Is `px` inside the band a policy resting `rungs` rungs covers?

    One rung reduces to px == touch. Half-tick tolerance for float noise."""
    lo_t, hi_t = band(rungs)
    return (touch - lo_t * TICK - 1e-9) <= px <= (touch + hi_t * TICK + 1e-9)


def replay(fills, rungs, touch_lag=1):
    """Replay one window's tape under an R-rung policy.

    The ONLY thing that differs between arms is `rungs`. Side selection, timing
    and size are identical: the policy is assumed to be working the side L5 was
    working, at the touch L5 was trading against. That isolates the ladder
    mechanic and nothing else, which is the point of the ablation.

    The touch is the last observed trade price on that outcome, lagged by
    `touch_lag` fills so the policy never uses the price of the fill it is
    being scored on. NO FUTURE INFORMATION ENTERS."""
    tp = tape(fills)
    last = {}                       # outcome -> recent prices, most recent last
    got = []
    for t, out, px, sz in tp:
        hist = last.setdefault(out, [])
        if len(hist) >= touch_lag:
            touch = hist[-touch_lag]
            if covered(px, touch, rungs):
                got.append({"t": t, "outcome": out, "price": px, "quantity": sz})
        hist.append(px)
    return got


def geometry(seqs):
    """matched / residual geometry, in shares, aggregated over windows.

    Residual is summed as an ABSOLUTE value PER WINDOW. Summing the signed
    residual across windows lets a long window cancel a short one and collapses
    the ratio toward zero -- that is not a smaller inventory problem, it is two
    inventory problems pointing in opposite directions."""
    m_tot = 0.0
    r_tot = 0.0
    for seq in seqs:
        qu = sum(x["quantity"] for x in seq if x["outcome"] == "Up")
        qd = sum(x["quantity"] for x in seq if x["outcome"] != "Up")
        m_tot += min(qu, qd)
        r_tot += abs(qu - qd)
    return {"matched": m_tot, "residual": r_tot,
            "rm": (r_tot / m_tot) if m_tot else float("inf"),
            "fills": sum(len(s) for s in seqs)}


def run_lengths(seq):
    if len(seq) < 2:
        return []
    out = []
    run = 1
    for i in range(1, len(seq)):
        if seq[i]["outcome"] == seq[i - 1]["outcome"]:
            run += 1
        else:
            out.append(run)
            run = 1
    out.append(run)
    return out


def time_to_opposite(seq):
    out = []
    for i in range(len(seq)):
        for j in range(i + 1, len(seq)):
            if seq[j]["outcome"] != seq[i]["outcome"]:
                out.append(seq[j]["t"] - seq[i]["t"])
                break
    return out


def oscillations(seq):
    s = 0
    c = 0
    r = 0.0
    for x in seq:
        r += x["quantity"] if x["outcome"] == "Up" else -x["quantity"]
        ns = (r > 1e-9) - (r < -1e-9)
        if ns and s and ns != s:
            c += 1
        if ns:
            s = ns
    return c


def distinct_prices(seq):
    return len({round(x["price"], 4) for x in seq})


def fills_per_minute(seq):
    if len(seq) < 2:
        return 0.0
    span = (seq[-1]["t"] - seq[0]["t"]) / 60.0
    return len(seq) / span if span > 0 else 0.0
