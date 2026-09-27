# CHANNEL_A — the distinction exists and is L5-supported. It still does not reach $5.

**Verdict: CHANNEL_A UNRESOLVED.** A genuine, pre-trade-observable distinction was
found and is independently supported by two measurements over L5's 66,949 BTC15m
states. Implemented as one challenger on frozen PROFIT_LOCK_5 it turns passive
matched gross from **−7.5988 to +0.4669** per window at q=50 — the leakage
actually reverses sign — and still delivers **+4.2227 SHADOW TRADING NET, short of
the ≥+$5 criterion.** Worse, **it flips sign between clips**: at q=25 it makes
things worse, −1.0120 against LOCK5's +1.1087. At n=12 that is not a robust
result, and it is reported as unresolved rather than tuned into compliance.

PROFIT_LOCK_5 stays frozen at $5. Its threshold was not touched. The frozen n=25
arm is byte-identical (ledger regenerated and diffed; 336 configs, 0 failures).

## 1. Every passive action in the four NEVER_LOCKED windows

37 passive adds across 1915z, 1930z, 2045z, 2245z at q=50. Full per-action records
(side, price, inventory before/after, imbalance, held-side move, floor before/after,
marginal floor effect, averaging direction, EC availability) are in
`analysis/l5/p1/p1_channel_a_forensics.json` via
`scripts/p1_channel_a_forensics.py`. The pattern, by pre-trade state only:

| | count | total marginal floor effect |
|---|---|---|
| on the **deficient** side (floor-increasing, identity `+n(1−p)`) | 13 | **+165.00** |
| on the **surplus or balanced** side (floor-decreasing, identity `−n·p`) | **24** | **−668.50** |

Averaging direction among the 28 adds that had a basis to compare against:
**18 UP, 10 DOWN.**

**An EC opportunity was available on exactly zero rows in all four windows.** That
is why they never lock: with no θ-clearing completion ever executable, the floor
never rises, so there is nothing to lock. These windows are not "locks that were
missed", they are windows with no lockable economics at all.

The signature adds, every one of them from a **balanced** book (imbalance 0) at a
price far above the basis already held on that side:

| window | t | side | px | basis on that side | Δfloor |
|---|---|---|---|---|---|
| 1915z | 19:24:18 | down | 0.8500 | 0.5000 | **−42.50** |
| 1915z | 19:26:20 | down | 0.9600 | 0.6744 | **−48.00** |
| 1915z | 19:29:20 | down | 0.9900 | 0.7696 | **−49.50** |
| 1930z | 19:42:22 | up | 0.9900 | 0.6740 | **−49.50** |
| 1930z | 19:43:23 | up | 0.9900 | 0.7267 | **−49.50** |
| 2045z | 20:56:09 | down | 0.9700 | 0.9033 | **−48.50** |
| 2045z | 20:57:09 | down | 0.9800 | 0.9200 | **−49.00** |
| 2245z | 22:53:32 | up | 0.7600 | 0.6050 | **−38.00** |
| 2245z | 22:59:03 | up | 0.9800 | 0.7344 | **−49.00** |

## 2. The answer to the question

> What observable pre-trade state distinguishes passive additions that destroy
> locked economics from those that contribute positively?

**Two quantities, both known before the order is placed, and neither requiring a
forecast:**

1. **Which side of the imbalance the add sits on.** Because the floor is
   `min(q_up,q_down) − cost − fees`, a clip of n at p on the **deficient** side
   moves it by `+n(1−p)`, which is positive for every p<1, and the same clip on
   the **surplus or balanced** side moves it by `−n·p`, which is negative for
   every p>0. This is an identity of the book state, not a judgement, and it is
   the cleanest separator in the data: +165.00 against −668.50.
2. **Whether a floor-decreasing add averages the basis DOWN or UP.** All nine
   signature adds above average UP, and the further above basis the price is, the
   larger the floor destruction.

## 3. Independent L5 support — measured, not assumed

Both from `src/l5_sequence_corpus.py` over 66,949 BTC15m states, reconstructed
from the raw activity corpus rather than read from the aggregate module:

| | L5 | our clone (4 never-locked windows) |
|---|---|---|
| acts from a **balanced** book | **627 of 66,949 = 0.94%** | every large floor-destroying add |
| floor-decreasing adds **below** own basis (averaging DOWN) | **21,750 = 64.3%**, median −15.1c | 10 of 28 = 36% |
| floor-decreasing adds **above** own basis (averaging UP) | 11,799 = 34.9%, median +10.6c | **18 of 28 = 64%** |

The ratio is an almost exact mirror image, on n=33,805. **L5 does take
floor-decreasing adds — about half his fills — but he takes them at prices below
his own basis, and he virtually never trades from a balanced book.** Our clone
does the opposite of both.

Also checked and NOT supported as a separator: acquisition cadence and
inventory-normalised sizing. His next fill is a median 1.00 of his own typical
size in every neighbourhood examined, i.e. flat, so sizing carries no signal here;
cadence differences (his 4s against our 60s+) are a resolution ceiling already
recorded in `P1_FIVE_DOLLAR_RETENTION.md` and cannot be expressed from our tape.

## 4. The one challenger: `floor_guard`

**One rule, no free parameter, no threshold to sweep:** a passive add that lowers
the settlement-independent floor is placed only when its price is **below** the
current average basis on that side. The window's first add is exempt, because no
basis exists yet. No pair-cost cap, no θ, no directional forecast, no settlement
outcome, no future price.

| per window | q=50 LOCK5 | **q=50 LOCK5+GUARD** | q=25 LOCK5 | q=25 LOCK5+GUARD |
|---|---|---|---|---|
| EC matched gross | +12.0322 | +4.6089 | +6.6320 | +2.9822 |
| **PASSIVE matched gross** | −7.5988 | **+0.4669** | −4.8222 | −0.4559 |
| TOTAL matched gross | +4.4334 | +5.0758 | +1.8098 | +2.5263 |
| fees | 1.2324 | 0.8530 | 0.7011 | 0.4445 |
| residual P&L | 0.0000 | 0.0000 | 0.0000 | −3.0938 |
| **SHADOW TRADING NET** | +3.2010 | **+4.2227** | +1.1087 | **−1.0120** |
| expected maker rebate | +0.8217 | +0.8217 | +0.4133 | +0.4133 |
| SHADOW MODELED NET | +4.0227 | +5.0444 | +1.5220 | −0.5987 |
| **max peak directional** | 100 | **100** | 50 | **50** |

**Exposure condition met exactly** — unchanged at both clips. **Success criterion
NOT met:** +4.2227 against ≥+5.00 before rebates. The modeled net does cross $5 at
q=50, but expected rebate is upside by instruction and is excluded from the test.

### Why this is reported as unresolved rather than a win

* **It reverses sign across clips.** −1.0120 at q=25 against +1.1087 for LOCK5
  alone. A distinction that helps at one clip and hurts at the other, on 12
  windows, is not established.
* **At q=25 it destroys two windows that LOCK5 saved**: 1830z +5.5719 → −13.7500
  and 1945z +5.3553 → −10.1733, by blocking the very adds that built the
  inventory a lock needed. The guard and the lock are in tension: the lock needs a
  book big enough to floor at $5, and the guard shrinks the book.
* **EC gross falls 62%** at q=50 (12.03 → 4.61). The mechanism that earns is fed
  by the inventory the guard prevents.
* The four never-locked windows improve but do not turn positive: 1915z −4.3004 →
  −3.0000, 2045z −7.6362 → −1.0000, 2245z −10.6526 → −10.0000, 1930z −0.0688 →
  0.0000.

## 5. What would have to be true

Not proposed, not implemented, recorded so it is not rediscovered: closing the gap
needs the EC edge preserved *while* floor-decreasing adds are suppressed, which
means acquiring the deficient side without first acquiring the surplus side — i.e.
frequency at fine granularity, which `P1_FIVE_DOLLAR_RETENTION.md` already shows
our tape cannot express (median 52 bbo observations per window against L5's 123
fills). The two findings are the same ceiling seen from two directions.

**CHANNEL_A is UNRESOLVED. P0 continues unchanged to n=25**, where these arms
re-run automatically on the larger sample.
