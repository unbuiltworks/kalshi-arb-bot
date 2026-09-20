"""PHASE 1 -- what execution surface does L5 actually run, and what is identifiable?

Historical L5 public corpus ONLY (analysis/l5/l5_canonical_sequence.csv.gz,
source polymarket_public_activity). Nothing here touches PMUS, Gemini, live
execution or Model A. No parameter is fitted against any PMUS outcome.

THE IDENTIFYING EVIDENCE IS ON-CHAIN ATOMICITY. Polymarket settles a taker order
against the maker orders it crosses inside ONE transaction. So two L5 fills that
share a transaction_hash but carry DIFFERENT prices on the SAME outcome cannot be
one order that was cancelled and repriced -- there is no point inside an atomic
transaction at which a cancel/replace could occur. Those fills prove that at least
two of L5's orders were resting SIMULTANEOUSLY at different prices.

  H1  one order repeatedly cancelled and repriced     FALSIFIED where atomic
                                                       multi-price fills exist
  H2  multiple simultaneous resting rungs             PROVEN (lower bound 2 rungs)
  H3  persistent ladder + selective repricing         SUPPORTED: rungs persist
                                                       (45.4% of consecutive
                                                       same-side fills are at the
                                                       SAME tick) and also drift
                                                       +/-1 tick (31.3%)

WHAT IS NOT IDENTIFIABLE FROM PUBLIC FILL DATA, and is labelled as such everywhere:
  - the true ladder DEPTH. We observe only rungs that FILLED. Atomic sweeps give a
    LOWER bound of 2 (3 in one case). The posted ladder may be deeper.
  - posted rung SIZE. We observe filled quantity, which may be a partial fill.
  - orders that never filled, and every cancellation. Invisible.
  - queue position, and the book L5 was resting in.
Every quantity below is therefore reported as a LOWER BOUND on the resting surface
unless explicitly stated otherwise.
"""
import gzip, csv, collections, statistics as stt

CORPUS = "/home/user/kalshi-arb-bot/analysis/l5/l5_canonical_sequence.csv.gz"


def load():
    rows = []
    with gzip.open(CORPUS, "rt") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)
    w = collections.defaultdict(list)
    for r in rows:
        w[r["window_id"]].append(r)
    for k in w:
        w[k].sort(key=lambda r: (int(r["trade_seq"]), float(r["timestamp"])))
    return rows, w


def atomic_ladders(rows):
    """Transactions in which L5 was filled at >1 price on ONE outcome.

    These are the H1 falsifiers. Returns the groups, newest key first."""
    tx = collections.defaultdict(list)
    for r in rows:
        tx[r["transaction_hash"]].append(r)
    return [v for v in tx.values()
            if len(v) > 1 and len({x["price"] for x in v}) > 1]


def maker_share(fills):
    return sum(1 for x in fills if x["maker_taker"] == "maker") / len(fills)


def mode_of(fills):
    """The corpus contains TWO execution modes and they must never be averaged.

    MAKER_LADDER  maker-dominant, dense, multi-rung, inventory oscillating
    TAKER         taker-dominant, sparse, single-price, little oscillation

    The split by maker share and the split by the atomic ladder signature select
    the SAME windows (311 of 314 agree), so the label is not an artefact of which
    sweeps happened to be large enough to observe."""
    return "MAKER_LADDER" if maker_share(fills) >= 0.5 else "TAKER"


def required_rungs(fills, bucket_s=1.0):
    """LOWER BOUND on simultaneously resting rungs, per window.

    Within a bucket of `bucket_s` seconds, the number of DISTINCT same-side prices
    that filled is a floor on how many of L5's orders were live at once: one order
    cannot occupy two prices. The per-window maximum over buckets is the floor for
    that window. It is a floor, never an estimate of the posted ladder."""
    best = 1
    i = 0
    while i < len(fills):
        j = i
        ps = {fills[i]["price"]}
        side = fills[i]["outcome"]
        t0 = float(fills[i]["timestamp"])
        while (j + 1 < len(fills) and fills[j + 1]["outcome"] == side
               and float(fills[j + 1]["timestamp"]) - t0 <= bucket_s):
            j += 1
            ps.add(fills[j]["price"])
        best = max(best, len(ps))
        i = j + 1
    return best


def size_by_price(fills, nbands=10):
    """Empirical rung size as a function of the price paid, in that side's own
    price. Reported as measured; no functional form is imposed."""
    b = collections.defaultdict(list)
    for x in fills:
        b[min(nbands - 1, int(float(x["price"]) * nbands))].append(float(x["quantity"]))
    return {k: stt.median(v) for k, v in sorted(b.items()) if len(v) >= 50}


def switch_hazard(windows, cap=6):
    """P(next acquisition is the opposite outcome | same-side run length).
    Conditioned on fills 0..i-1 only, so the label cannot leak backwards."""
    b = collections.defaultdict(lambda: [0, 0])
    for f in windows:
        run = 1
        for i in range(1, len(f)):
            e = b[min(run, cap)]
            e[1] += 1
            if f[i]["outcome"] != f[i - 1]["outcome"]:
                e[0] += 1
                run = 1
            else:
                run += 1
    return {k: (v[0] / v[1], v[1]) for k, v in sorted(b.items()) if v[1] > 30}


def zero_crossings(fills):
    """How often the signed residual changes sign -- the recycling that turns
    inventory over instead of accumulating it."""
    s = 0
    c = 0
    for x in fills:
        r = float(x["residual_qty_signed"] or 0)
        ns = (r > 0) - (r < 0)
        if ns and s and ns != s:
            c += 1
        if ns:
            s = ns
    return c
