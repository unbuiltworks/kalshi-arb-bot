# The 8c straddle's edge is not established, and the measurement says why

Written 2026-11-14 after campaign 3. Supersedes this session's earlier
EV claims, which were too generous.

## Campaign 3, settled

8 shares a side at 0.08, 7 windows 03:45Z..04:15Z.

| window | filled | cost | up_won | settled | lone leg |
|--------|--------|------|--------|---------|----------|
| 0345z | 1 | 0.64 | True | -0.64 | redeemed 0 |
| 0350z | 1 | 0.64 | True | -0.64 | redeemed 0 |
| 0355z | 1 | 0.64 | False | -0.64 | redeemed 0 |
| 0400z | 1 | 0.64 | True | -0.64 | redeemed 0 |
| 0405z | 0 | 0 | True | 0 | no fill |
| 0410z | 1 | 0.64 | False | -0.64 | redeemed 0 |
| 0415z | 1 | 0.64 | True | -0.64 | redeemed 0 |

**6 filled, 0 pairs, settled net -3.84.**

## 1. Adverse selection is total, not partial

Every filled window redeemed at ZERO. Across campaign 2's seven visible
windows (all -0.40, their full 5-share cost) and campaign 3's six, that is
**19 consecutive lone-leg losses**. `up_won` was mixed throughout, so this
is not a directional run: whichever side won, we held the other.

P(19 losses in a row | fills independent of outcome) = 1.9e-6.

The mechanism is not subtle. We rest a BID at 8c. A resting bid is hit
when someone wants to SELL at 8c, and people sell the side that is going
to zero. The winning side's holders never sell to us at 8c because it is
worth more than that. So the straddle buys the loser, structurally.

**This corrects an earlier figure in this session.** The lone leg was
valued at ~2.5c on the argument that a losing leg retains some value. It
does not. It redeems at 0, so a lone fill costs the FULL 8c per share.

## 2. The pair is a property of the window, not of our execution

All 8 shadow BOTH windows had `deepest_up` AND `deepest_down` at or below
0.07 -- both sides traded down to our price at some point. The pair
happens when BTC chops enough that each side in turn looks worthless.
Nothing we do to our orders creates one.

Today's shadow pair windows were 0055z and 0255z. Our live windows were
0145-0215 and 0345-0415. **We were halted on the daily loss limit during
0255z.** 0/20 live against 8/77 shadow is therefore not a contradiction:
we ate losing windows and were switched off through a paying one.

GAP IN THE RECORDER, logged rather than papered over:
`min_touch_shares_up/down` is None on every BOTH window, so whether 5 or 8
shares could actually have filled on both sides is UNVERIFIED. The pair
rate below is a rate of price events, not of confirmed fillable pairs, and
it may therefore be optimistic.

## 3. The number that decides the strategy

    breakeven pair rate = lone / (pair + lone) = 0.08 / (0.84 + 0.08) = 8.70%

    measured pair rate  = 8/77 = 10.39%
    95% Wilson CI       = 5.36% .. 19.18%

**Breakeven sits inside the confidence interval.** The sign of the edge is
not established:

| pair rate | EV per share per window | |
|-----------|------------------------|---|
| 5.36% (CI low) | -0.0307 | LOSS |
| 10.39% (point) | +0.0156 | profit |
| 19.18% (CI high) | +0.0965 | profit |

Everything rests on 8 observed pairs. One pair more or fewer moves the
point estimate by ~1.3 points against a 1.69-point margin over breakeven.

## 4. Size is the opposite lever from the intuitive one

Monte Carlo, 200k trials, p = 10.39%, $11 daily cap, run until the cap
stops it:

| shares | losing windows the cap allows | P(reach a pair) | E[net/day] | E[net]/share |
|--------|------|-------|-------|-------|
| 4 | 34 | 0.976 | $2.38 | 0.595 |
| 5 | 27 | 0.949 | $2.34 | 0.469 |
| 6 | 22 | 0.910 | $2.30 | 0.384 |
| 8 | 17 | 0.844 | $2.37 | 0.296 |
| 10 | 13 | 0.759 | $2.25 | 0.225 |
| 16 | 8 | 0.582 | $2.19 | 0.137 |

Expected net is FLAT in share size, because the cap bounds the loss while
the pair payoff scales -- the two cancel. What changes is the probability
of ever reaching the payoff: 97.6% at 4 shares, 58.2% at 16.

**So larger clips do not buy more expected profit under a fixed daily cap.
They buy a higher chance of being stopped out before the 10% event that
pays for the other 90%.** Campaign 3 at 8 shares is exactly that: stopped
at 17 windows' worth of room, 0 pairs.

## 5. What $480/day would require

At the point estimate, +0.0156 $/share/window:

    480 / 0.0156 = 30,800 share-windows/day
                 = 107 shares EVERY window, 288 windows/day, 24/7
    lone-fill loss at that size: $8.56 per window

A daily loss tolerance in the hundreds, continuous operation, and the
assumption that p holds at size -- when the depth needed to fill 107
shares on both sides at 8c is precisely what section 2 says is unverified.

## Conclusion

Not "this does not work". **"This is indistinguishable from not working on
the evidence held."** The distinction matters because the fix is the same
either way: resolve the pair rate.

Cheapest path, in order:
1. Populate `min_touch_shares` on BOTH windows so fillable pairs can be
   separated from price events. Read-only, no risk, blocks nothing.
2. Collect shadow windows continuously. 542 windows give t=2.0 on the EV;
   ~83 are held. This costs nothing and needs no arming.
3. Only then size live, and size DOWN, not up -- section 4.

Do not raise the daily cap to "give it room". Under a fixed cap the
expected value is flat in size, so a larger cap buys variance, not edge,
until p is pinned down.

---

# Addendum, same day: the depth question is closed, the edge question is not

## The recorder gap was not fixable, and did not need to be

`min_touch_shares_*` was null on every row ever emitted, and the cause was
not a missed assignment that could be patched. `bshares`/`ashares` are
bound to None and never assigned because `/v1/market/slug/{slug}` -- the
only endpoint that answers for an OPEN window -- carries no depth, and the
`/bbo` endpoint that does carry depth returns 404 until the window EXPIRES.
The field was tracking a quantity the process could not observe.

**The question it existed to answer was then answered another way.**
Post-expiry `/bbo` does serve `sharesTraded`:

| window | | sharesTraded |
|--------|---|-------------|
| 0255z | a shadow pair window | 163,023 |
| 0345z | live, one leg only | 219,628 |

At 8 shares we are **0.005% of a window's volume**, and live agrees: one
leg filled promptly in 19 of 19 placing windows. **Depth is not the
binding constraint at our size.** So the pair rate is a genuine price-path
property, the shadow's rate is NOT inflated by unfillable touches, and the
suspicion recorded in section 2 above was wrong.

The dead block is left in place with that finding written next to it, so
nobody re-opens it.

## A second, independent estimate of the pair rate

`pmus_btc5m_quotes_20261112.jsonl` is a separate 189-window quote tape from
Nov 11-12, recorded by a different process. Replaying the identical rule on
it -- `own_price(UP) = bid`, `own_price(DOWN) = 1 - ask`, fill at <= 0.08 --
with a density filter of >= 60 reads and max gap <= 10s:

| sample | pairs/n | rate | 95% Wilson CI | vs breakeven 8.70% |
|--------|---------|------|---------------|--------------------|
| shadow, live cadence | 10/106 | 9.43% | 5.21% .. 16.50% | straddles |
| independent tape | 21/172 | 12.21% | 8.13% .. 17.94% | straddles |
| **POOLED** | **31/278** | **11.15%** | **7.97% .. 15.39%** | **straddles** |

Pooled EV **+0.0226 $/share/window**. Two independently sourced samples,
278 windows, and breakeven is still inside the interval.

How fine the margin is:

| pairs of 278 | rate | CI low | EV | verdict |
|--------------|------|--------|-----|---------|
| 28 | 10.07% | 7.06% | +0.0127 | undecided |
| 31 | 11.15% | 7.97% | +0.0226 | undecided |
| 34 | 12.23% | 8.89% | +0.0325 | **edge** |
| 37 | 13.31% | 9.81% | +0.0424 | **edge** |

**Three pairs either way flips the verdict.** That is the whole state of
the evidence after three live campaigns.

## What settles it, and what it costs

For the CI low to clear breakeven at the pooled rate: **~631 windows**.
278 are held, so **~353 more**, which is **1.2 days of shadow recording**
at 288 five-minute windows a day.

**Zero cost. Read-only. No arming, no capital at risk.**

That is the entire remaining question for this strategy, and live trading
cannot answer it faster than the shadow can -- the daily loss cap allows
only ~17 windows a day at 8 shares, against 288 a day for free in shadow.
Live trading is 17x slower at buying the one thing still missing, and it
pays for the privilege.

## Standing recommendation

1. Let the shadow run ~1.2 days and re-pool. The verdict will be
   arithmetic, not judgement.
2. If the CI low clears 8.70%, size live from the bottom -- section 4
   shows expected net is flat in size under a fixed cap while P(reaching a
   pair) falls from 0.976 at 4 shares to 0.582 at 16.
3. If it does not clear, the 8c straddle is done and the lone-leg
   adverse-selection finding (19/19, p = 1.9e-6) says why: a resting bid
   is filled by someone selling the loser.
