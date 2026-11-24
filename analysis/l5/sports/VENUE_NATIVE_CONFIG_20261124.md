# Venue-native execution configuration — prepared for approval, not active

24 Nov 2026, 23:50 UTC. Production: sports `766a965ba`, plain lock. This is
a SETTINGS profile on the running build. No code change, no new build.

Owner, 23:35 UTC: "Strategic direction has changed. We want to actively
trade live sports events without requiring international reference prices.
Use the existing inventory-aware controller. Do not build another trading
system." ... "Removes international pricing as a mandatory entry
requirement, subject to explicit release approval." ... "Preserve all
existing capital protections, unpaired caps, loss stops, reconciliation
and independent watchdog controls." "Do not arm or deploy money-capable
changes without explicit authorization."

## The profile (existing settings only)

| Setting | Now | Profile | Effect |
|---|---|---|---|
| `PMUS_TWO_SIDED_INTL_GUARD_REQUIRE` | 1 | 0 | a market no longer needs an international price to be selected. The guard stays ON: where an international price exists it still withholds a bid it reads as not cheap |
| `PMUS_RN1_EDGE_GATE` | 1 | 0 | the fair-value test is not applied; bids are priced from the venue book (one tick behind each side's best bid) |
| `PMUS_RN1_EXTEND_EV` | 1 | 0 | a same-side addition no longer needs a fair value; it is limited by the extend price rule, the 60 s refill cool-down and the caps |
| `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS` | approved seven | `setkameua,setkamemd,utr,dota2` | thin, reward-paying competitions (owner to confirm the list) |
| `L5_PORTFOLIO_MAX_DRAWDOWN_USD` (ledger) | 3 | 5 | account limit |
| `PMUS_RN1_TOTAL_SPEND_USD` | 16 | 30 | campaign spend |
| slate, size, markets | both, 1, 4 | unchanged | |

Unchanged and still enforced: account limit with reservations, $6 / $3
unpaired caps, $8 per-market spend, $4 campaign loss stop, $5 daily halt,
maker-only, $0.97 pair cap, 10-contract minimum depth at the touch,
0.30–0.70 selection band, 6-tick maximum spread, price-jump pause, 3-cent
volatility pause, final order gate, stale-audit block, safety latch,
reconciliation, watchdog, no cricket, esports blocked when the match
format is unknown.

What "venue-native" uses, all already in the controller: depth at the
touch, spread, two-tick jump pause, 30-second volatility pause, price
band, inventory caps and the worst-case account count. Ranking among
allowed markets is by the venue's published reward pool rate; ranking by
OUR estimated share would need code and is not in this profile, which is
why the allow-list is restricted to thin competitions.

## Evidence that the existing controller runs this way

- **Dry runs against live public data, no orders possible**
  (`venue_native_20261124/`, 23:38–23:49 UTC, fills modelled only when the
  venue price moves THROUGH a bid):
  - all competitions allowed: the six slots went to ATP (largest pools);
  - thin competitions only, depth gate 10: selected 3 Setka Ukraine, 1
    Setka Moldova (in play) and 3 UTR (pre-game); 20 bids, 8 modelled
    fills, 4 modelled pairs each at $0.97, no error, nothing refused;
  - same with depth gate 1: no material difference, so the 10-contract
    gate does not need lowering.
  - with `EXTEND_EV` on, same-side additions read `extend_no_fair`.
- **Regression:** the quoting path without the fair-value test is what the
  existing suite runs by default (the test is switched on only for the
  profit-first tests). Targeted run of the RN1, controller, final-gate,
  reservation, two-sided, reselect, gap-hold and price-guard files: 474
  passed. Full suite at this build: 5,561 passed, 24 pre-existing failures.

## What the record says about these markets (confirmed, 14–18 Nov)

| Competition | Markets | Rewards paid | Trading | Net |
|---|---|---|---|---|
| Dota 2 | 10 | +2.29 | +0.10 | +2.39 |
| Table tennis, Setka Moldova | 10 | +1.92 | -0.33 | +1.59 |
| Table tennis, Setka Ukraine | 37 | +6.30 | -8.06 | -1.76 |
| UTR tennis | few | +0.01 | +1.15 to +1.55 | positive, tiny sample |
| CS2 | 32 | +3.10 | -6.28 | -3.18 (not proposed) |

That was the older controller: 5-contract quotes, pairs completed above
$1, taker exits. This profile is 1 contract, pairs capped at $0.97, no
taker. Fills in these markets were marked 3 to 4 cents against us within
30 seconds. This profile reverses the 22 Nov rule "Where a reliable fair
value cannot be established, fail closed on additional risk"; the owner
has now directed that.

## Not in this profile

- Ranking by our estimated reward share (code).
- The double-count pull, the "unreadable" pull and the audit heartbeat
  gap (unchanged). The completing-bid churn seen today came from the
  fair-value test and should not occur with it off; unproven live.
- The signed emergency stop: unverified.
