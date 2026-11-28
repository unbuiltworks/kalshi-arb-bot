# Is there a positive edge? Six ways of quoting on identical windows

**Verdict: NO PROMOTION. No candidate has a positive expected net result per
contract, in sample or out, under the conservative fill rule or the
optimistic one. The evidence does not warrant a live-validation proposal.**

Reproduce with `PYTHONPATH=. python3 analysis/l5/edge_comparison.py`
(20 s; writes `edge_comparison.json`). Tests:
`tests/test_l5_edge_comparison.py` (214). Offline: nothing under `src/`
imports it, no order was placed, no configuration changed, the
own-settled-evidence gate was not waived.

Evidence class: **L5's Polymarket price path with PMUS ticks and fees and a
one-minute Coinbase reference. Not PMUS fills and not our own.** It ranks
candidates on identical paths; it is not a forecast of our fills.

## The ruler

- One contract per order, post-only maker buys, commission 0.0000, **no
  rebate or reward counted**.
- An order fills only when a **later** print trades one full tick **through**
  it. Never at placement, never on a touch. (The optimistic rule, "a touch
  fills", is reported as a sensitivity.)
- A matched pair redeems $1.00; an unmatched contract settles on the venue
  outcome. Net result is cash: it does not depend on any mark.
- Two panels, each split chronologically 60/40, anything tuned is tuned on
  the first part only:
  - **spot panel** - the 312 windows where the Coinbase minute tape gives a
    strike (2026-08-28 to 08-31, **four days, one regime**): all six
    candidates;
  - **wide panel** - all 526 resolved windows (2026-03-19 to 08-31): the
    candidates that need no spot model.

## Result, out of sample

Spot panel, 125 windows, net per filled contract, window-clustered 95%
interval:

| candidate | fills | pairs | unmatched | net / contract | 95% interval | net / window | optimistic fill rule |
|---|---|---|---|---|---|---|---|
| PAIR (deployed: two ticks back, complete to 0.99) | 222 | 101 | 20 | -0.0289 | [-0.0503, -0.0100] | -0.051 | -0.0178 |
| LADDER (depths 2, 3, 4 at once) | 654 | 291 | 72 | -0.0347 | [-0.0564, -0.0149] | -0.181 | -0.0211 |
| GRIND (L5's shape: both sides at the touch, re-quoted, residual <= 3, pair <= 0.99) | 1280 | 504 | 272 | -0.0370 | [-0.0586, -0.0181] | -0.379 | -0.0258 |
| GRIND one tick back | 1159 | 442 | 275 | -0.0440 | [-0.0664, -0.0236] | -0.408 | -0.0259 |
| GRIND, unbounded chase (completes at any price) | 3103 | 1505 | 93 | -0.0386 | [-0.0437, -0.0341] | -0.959 | -0.0225 |
| SPOT (quote a side only when the model puts fair value 10 cents above the quote) | 487 | 124 | 239 | -0.0426 | [-0.0862, +0.0039] | -0.166 | -0.0353 |
| **VETO** (the pair, an opening leg pulled while the model calls it overpriced) | 212 | 97 | 18 | **-0.0183** | [-0.0368, -0.0006] | -0.031 | -0.0119 |
| *L5 himself, his real fills, same windows* | 454,720 contracts | | | *+0.0022* | [-0.0041, +0.0085] | | |

Wide panel, 211 out-of-sample windows: PAIR -0.0364 [-0.0535, -0.0215],
LADDER -0.0380 [-0.0545, -0.0234], GRIND -0.0340 [-0.0501, -0.0192], GRIND
one back -0.0390 [-0.0571, -0.0222], chase -0.0371 [-0.0409, -0.0334]. Every
upper bound is below zero. In sample the same candidates are -0.048 to
-0.059.

**More fills did not help.** The grind produces ten fills a window against
the pair's two and loses seven times as much per window; the ladder triples
the fills and more than triples the loss.

## Where the money goes (out of sample, per filled contract)

| candidate | entry | fill (adverse selection) | completion | unmatched carry | settlement | = net |
|---|---|---|---|---|---|---|
| PAIR | +0.0164 | -0.0382 | -0.0224 | +0.0168 | -0.0015 | -0.0289 |
| LADDER | +0.0222 | -0.0444 | -0.0215 | +0.0097 | -0.0007 | -0.0347 |
| GRIND | +0.0061 | -0.0304 | -0.0174 | +0.0057 | -0.0009 | -0.0370 |
| GRIND one back | +0.0124 | -0.0373 | -0.0158 | +0.0033 | -0.0066 | -0.0440 |
| SPOT | +0.0149 | -0.0429 | -0.0093 | +0.0056 | -0.0109 | -0.0426 |
| VETO | +0.0163 | -0.0372 | -0.0219 | +0.0192 | +0.0053 | -0.0183 |

The five parts add up to the net exactly (tested on every strategy).

- **Entry selection** is the only reliably positive part: quoting behind the
  touch asks for 0.6 to 2.2 cents.
- **Fill selection** takes back two to three times that. A resting bid fills
  when the market moves through it; the contract is 3 to 4.4 cents under
  water at the print that fills it. Quoting deeper asks for more and is hit
  harder (ladder -4.4 against pair -3.8).
- **Completion** costs another 1.6 to 2.2 cents: the matching leg is itself
  an adversely selected maker fill.
- **Unmatched carry** is positive only because a pair completes exactly when
  the price comes back; it is selection, not drift.
- **Settlement** of what is never matched is close to a coin flip on
  average and very wide per contract (below).

## Does the spot model help?

A little, and not enough. The same one-minute model that matches but does
not beat the market (AUC 0.856 against 0.855) improves the pair by
**+$0.020 a window out of sample [-0.001, +0.048]** and +$0.017 in sample
[-0.004, +0.038] - the same sign in both halves, neither clear of zero, and
the vetoed pair still loses 1.8 cents a contract. Used on its own to pick a
side (SPOT) it looked nearly break-even in sample (-0.0043) and lost 4.3
cents out of sample: an in-sample artefact.

The model sees a one-minute tape. Most adverse fills happen inside the
minute, so this measures what that tape can do, not what a sub-second
reference could. That is the one mechanism this corpus cannot test.

## The ceiling: L5's own fills

No fill model at all - what L5's executed fills earned at settlement:

| panel | windows | contracts | net / contract | 95% interval |
|---|---|---|---|---|
| wide, all | 526 | 1,212,381 | -0.0004 | [-0.0036, +0.0029] |
| wide, out of sample | 211 | 789,800 | +0.0012 | [-0.0032, +0.0058] |

With his queue position, speed and size, the trading itself is break-even
to within a third of a cent. Whatever he earns on this market is paid on
top of the trading. The only incentive this venue has ever credited our
account is a maker rebate of about 0.3 cents a contract at mid prices, and
only on prints of three shares or more (`PMUS_INCENTIVE_BREAKEVEN.md`); at
the 0.6-contract size we trade live it has credited $0.00. It is an order
of magnitude smaller than the loss and is not counted here.

So the range of outcomes is bracketed: **about zero if we obtained L5's own
fills, about minus two to four cents a contract with an ordinary maker's
fills.** Nothing in that range is positive.

## The strongest candidate, stated conservatively

**VETO 0.00** - the deployed pair, with an opening leg cancelled while the
one-minute model says it is overpriced.

| | out of sample |
|---|---|
| expected net per filled contract | **-0.0183** |
| conservative figure (lower 95% bound) | **-0.0368** |
| best case (upper bound, optimistic fill rule) | +0.0031 |
| sample | 125 windows, 212 fills, 97 pairs, 18 unmatched contracts |
| worst window | -$0.62 (one contract per order) |
| 5th-percentile window | -$0.42 |
| worst unmatched contract | -$0.62 |
| unmatched contracts that lost | 83%, mean -0.27 |

For comparison the worst residuals elsewhere: pair -0.64 (90% of unmatched
contracts lose, mean -0.37); grind -0.84 a contract and -1.90 a window with
three contracts stranded; chase -2.80 a window.

## Our own confirmed settlements (a separate evidence class)

Venue-settled, this engine: 15:30Z 11-09 **-$0.3080** (one stranded leg),
02:00Z 11-10 **+$0.0060**, 13:00Z 11-10 **+$0.0063**. Three windows with
fills, 1.93 contracts, net -$0.2957: one strand cost 49 times what a
completed pair earned. That is the same shape as the replay (a pair earns
a cent, a strand loses tens of cents) and far too few windows to be a
measurement; the own-settled-evidence gate needs 20 and stays enforced.
All-time realized on the account: -$15.9066.

## What this does not say

- It is Polymarket's path sampled where L5 traded; PMUS books are thinner.
- Queue position is the trade-through rule and nothing finer.
- The spot panel is four days. The wide panel agrees with it for the
  candidates it can score.
- One contract per order: no capacity or size effects.

## Decision

- **BLOCKED on edge, not on execution.** The engine can place, pair,
  replenish and reconcile; none of the ways of using it has positive
  expected settled value.
- **No live-validation proposal.** A live test of a candidate whose
  out-of-sample interval lies below zero would buy confirmation of a loss.
- **$5 a window** needs a positive net per contract before it needs volume.
  At -2 to -4 cents a contract, volume multiplies the loss.
- **The one unmeasured mechanism** is whether a sub-second spot reference
  can pull a quote before the adverse print. The one-minute model's
  consistent-sign +2 cents a window is the only hint that it might. It can
  be measured read-only: record our own PMUS book and a spot feed at one
  second, replay the veto against it, and require a positive lower bound on
  at least 200 windows before anything is proposed live. That is a
  recording job, not a trading one, and it needs its own approval.
