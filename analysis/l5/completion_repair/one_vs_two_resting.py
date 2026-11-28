"""One resting order versus two, measured on the RECORDED campaign books.

Every book below is an `L5_FILL_CALIB_PRESUBMIT` snapshot taken by the live
engine at submission time from the authenticated websocket, age under 10ms.
Nothing here is simulated book data.

THE IDENTITY THAT MAKES THIS DECIDABLE. Two maker legs quoted from ONE book
at depth d have a pair cost that depends only on the spread:

    pair = (bid - d*t) + (1 - ask - d*t) = 1 - spread - 2*d*t

So a pre-positioned pair's cost is FIXED AT QUOTE TIME. A sequential pair's
cost is not: leg one's price is locked when it fills, and the complement is
then bought at whatever the book has become. Any adverse drift in between is
pure loss, and it is measurable from the record.

NOTHING HERE IS A FILL. Two resting orders are two chances to be filled, not
two fills, and a pre-positioned pair that only half-fills leaves exactly the
residual this program keeps paying for. Both are quantified below.

Run: PYTHONPATH=. python3 analysis/l5/completion_repair/one_vs_two_resting.py
"""
from decimal import Decimal as D

T = D("0.01")

# (window, t_first, first_side, first_own, book at first submit (bid, ask),
#  t_comp, comp_own, book at complement submit (bid, ask), qty)
SEQUENCES = [
    ("1430z", "14:38:45", "DOWN", "0.3800", ("0.5900", "0.6000"),
     "14:39:32", "0.6200", ("0.6400", "0.6600"), "1.91"),
    ("1445z", "14:47:40", "DOWN", "0.3700", ("0.6000", "0.6100"),
     "14:47:52", "0.6000", ("0.6200", "0.6300"), "1.91"),
]


def own_at(side, bid, ask, depth):
    """The post-only own-terms price this side can rest at, `depth` ticks
    behind its own touch. UP rests a YES bid; DOWN rests a YES offer."""
    if side == "UP":
        return D(bid) - depth * T
    return D(1) - D(ask) - depth * T


def pair_from_one_book(bid, ask, depth):
    return D(1) - (D(ask) - D(bid)) - 2 * depth * T


def banner(s):
    print("\n" + "=" * 76 + "\n" + s + "\n" + "=" * 76)


banner("1. WHAT THE BOOK DID BETWEEN THE FIRST FILL AND THE COMPLEMENT QUOTE")
print("%-6s %-9s %-9s %-17s %-17s %s" % (
    "win", "t_first", "t_comp", "book at first", "book at complement",
    "drift vs us"))
for (w, t1, side, own1, (b1, a1), t2, own2, (b2, a2), qty) in SEQUENCES:
    # the complement side is the other one; how far did ITS touch move
    # against us over the interval?
    comp_side = "UP" if side == "DOWN" else "DOWN"
    touch_then = own_at(comp_side, b1, a1, 0)
    touch_now = own_at(comp_side, b2, a2, 0)
    drift_ticks = int((touch_now - touch_then) / T)
    print("%-6s %-9s %-9s %-17s %-17s %+d ticks dearer" % (
        w, t1, t2, "%s/%s" % (b1, a1), "%s/%s" % (b2, a2), drift_ticks))
print()
print("The complement got DEARER in both cases while we waited for leg one")
print("to fill. That drift is the cost of deciding the complement late.")

banner("2. PAIR COST: PRE-POSITIONED vs SEQUENTIAL  (same recorded books)")
print("%-6s %-5s %-24s %-24s %s" % (
    "win", "qty", "two-sided, one book", "sequential, as it ran", "delta"))
rows = []
for (w, t1, side, own1, (b1, a1), t2, own2, (b2, a2), qty) in SEQUENCES:
    q = D(qty)
    # PRE-POSITIONED: both legs quoted from the FIRST book, at the opening
    # depth of 2 the engine actually used.
    pair_two = pair_from_one_book(b1, a1, 2)
    # SEQUENTIAL: leg one at the price it really filled, complement at the
    # price it was really quoted from the LATER book.
    pair_seq = D(own1) + D(own2)
    # and what the complement would have cost AT the later touch
    comp_side = "UP" if side == "DOWN" else "DOWN"
    pair_seq_touch = D(own1) + own_at(comp_side, b2, a2, 0)
    rows.append((w, q, pair_two, pair_seq, pair_seq_touch))
    print("%-6s %-5s %-24s %-24s %+0.4f" % (
        w, q, "%s  (spread-only)" % pair_two,
        "%s  (%s + %s)" % (pair_seq, own1, own2), pair_seq - pair_two))
print()
print("Both pre-positioned pairs land at 0.95 -- under the 0.96 opening cap")
print("AND under the 0.9532 settled break-even -- because on a 1-tick book")
print("the two-sided pair cost is 1 - 0.01 - 0.04 regardless of price level.")

banner("3. P&L PER PAIR IF BOTH LEGS FILL  (counterfactual, not realized)")
print("%-6s %-5s %-12s %-14s %-12s %s" % (
    "win", "qty", "pair", "capital out", "redeems", "locked P&L"))
tot_two = tot_seq = D(0)
for (w, q, pair_two, pair_seq, pair_seq_touch) in rows:
    for label, pair in (("two-sided", pair_two), ("sequential", pair_seq)):
        cash = (pair * q).quantize(D("0.0001"))
        pnl = (q * (D(1) - pair)).quantize(D("0.0001"))
        print("%-6s %-5s %-12s %-14s %-12s %+0.4f  %s" % (
            w, q, pair, "$%s" % cash, "$%s" % q, pnl, label))
    tot_two += q * (D(1) - pair_two)
    tot_seq += q * (D(1) - pair_seq)
print()
print("across these two sequences: two-sided %+0.4f vs sequential %+0.4f"
      % (tot_two, tot_seq))
print("COUNTERFACTUAL. Conditional on BOTH legs filling, which is exactly")
print("the thing two resting orders do not guarantee.")

banner("4. WHAT TWO RESTING ORDERS COST IN RISK")
for (w, t1, side, own1, (b1, a1), t2, own2, (b2, a2), qty) in SEQUENCES:
    q = D(qty)
    up_own = own_at("UP", b1, a1, 2)
    dn_own = own_at("DOWN", b1, a1, 2)
    both = ((up_own + dn_own) * q).quantize(D("0.0001"))
    print("\n%s, qty %s per leg, quoted from the %s book %s/%s:"
          % (w, q, t1, b1, a1))
    print("  legs             UP own %s  +  DOWN own %s" % (up_own, dn_own))
    print("  capital if BOTH fill            $%s   (the matched pair)" % both)
    print("  capital if ONE fills (worst)    $%s   (the dearer leg alone)"
          % max(up_own * q, dn_own * q).quantize(D("0.0001")))
    print("  worst-case residual if ONE fills and its outcome LOSES:")
    for s, o in (("UP", up_own), ("DOWN", dn_own)):
        print("      %-4s filled alone -> -$%s" % (
            s, (o * q).quantize(D("0.0001"))))
    print("  one-resting-order exposure for comparison: one leg at a time,")
    print("  so at most $%s committed, and the complement is not yet priced."
          % max(up_own * q, dn_own * q).quantize(D("0.0001")))

banner("5. THE HONEST COMPARISON")
print("""
PRE-POSITIONED TWO-SIDED
  + pair cost fixed at quote time by the SPREAD alone: 0.95 on a 1-tick
    book at depth 2, which is inside both the 0.96 cap and the 0.9532 target
  + the complement rests for the WHOLE window, not only the minutes after
    leg one fills
  + no adverse-drift exposure between the first fill and the complement
    decision -- which cost 2 ticks on 1445z and 5 ticks on 1430z
  - commits up to the full pair cost (about $1.81-1.85 per pair here) when
    both fill, against at most one leg today
  - a half-filled pair still strands a leg; this does not remove residual
    risk, it only prices the complement earlier and better
  - two resting orders is two orders, so the one-order limit the owner
    approved would have to change

SEQUENTIAL (what we run)
  + at most one leg of capital committed, one order to cancel, simplest
    reconciliation
  + a first leg that does not fill costs nothing
  - the complement's price is unknown until leg one fills, and the measured
    drift was adverse in BOTH recorded sequences
  - the complement gets only the REMAINDER of the window to fill. On 1445z
    that was 12 minutes and it filled zero.

WHAT THIS DOES NOT SETTLE. Joint fill probability. Two resting orders at
depth 2 are two quotes behind the touch; whether both fill inside one window
is not estimable from our data, because PMUS publishes no print endpoint and
only a cumulative sharesTraded lower bound exists. The campaign's own
evidence is mixed and thin: 1430z filled both legs of two pairs sequentially,
1445z filled one leg of one and never the other.

So the case for two-sided is a PRICE case, and it is strong and measured:
0.95 against 0.97-1.00 on the same books. The case against is a FILL case,
and it is unmeasured. That asymmetry is what a bounded live test exists to
resolve, and it is why the test's metric must be matched pairs per filled
first leg, not pair cost.
""")
