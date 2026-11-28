# Four reconciliations, 2026-11-18

Read-only. Reproduce the reward split with
`python3 analysis/l5/reconciliation/reward_attribution.py`; the entry
rates with `src/l5_measured_entry_rates.estimate` over
`data/l5/ops/btc15m_settled_20261012_20261105.jsonl`.

## 1. BTC5m: "venue-confirmed fills and -$0.3291" vs "no authenticated rows"

Both statements were about DIFFERENT THINGS, and my second one overreached.

**The fill is real.** `tests/test_l5_balancing_budget.py` records it from
the live 1930z window: **0.52 contracts DOWN at 0.5700, cost $0.2964,
fees $0.0000, and the pair never completed.** Venue-confirmed.

**The -$0.3291 is a TEST FIXTURE, not a measurement.** It appears only in
`tests/test_l5_btc5m_campaign_preflight.py::good_exposure()` as
`cumulative_realized_usd: "-0.3291"`, exercising the `cumulative_loss`
check against a $5 cap (`test_the_lifetime_loss_so_far_does_not_block_a_
five_dollar_cap`). It was written to mirror the lane's lifetime figure at
the time, so it probably reflects a real number -- but the repository
holds it as a fixture, and it must not be quoted as a settled result.

**What my "zero authenticated historical rows" claim actually meant.** It
was true of what I can read and false as stated about BTC5m:

  - the measurement pipeline reads
    `data/l5/ops/btc15m_settled_20261012_20261105.jsonl`, which holds no
    `cpc-btc-updown-5m-*` rows;
  - `l5-pmus-live`'s heartbeat origin split reports `BTC5M: 0 markets` --
    but that is the BTC15m service's OWN ledger, which would not hold
    BTC5m rows in any case;
  - BTC5m's ledger lives on `l5-btc5m-live`'s volume, and the watchdog
    reads that lane as `REGISTRY_UNREACHABLE:URLError` (service disarmed).

So: BTC5m has at least one venue-confirmed fill and its own ledger is
simply not readable from here. The correct statement is a SCOPE
statement -- "no BTC5m rows are visible to the measurement pipeline" --
not an existence claim. Corrected.

## 2. The $14.13 credited rewards: overwhelmingly L5's

Of the 39 PAID rows visible in the 14:07:20Z `REWARD_CHECK` sample,
totalling **$4.84**:

    on markets the L5 sports bot traded   38 rows   $4.82   (99.6%)
    not on any market the bot traded       1 row    $0.02   (aec-cs2-hec-jumbo)

DENOMINATOR, stated: $4.84 is 34% of the venue's $14.13 PAID total (39 of
89 rows). The log prints the most recent rows, so this is NOT a random
sample and the 99.6% must not be extrapolated as a measurement. What is
established is that **at least $4.82 of credited rewards is L5's**, and
that the rows which are not L5's are rare and tiny.

`rows_on_our_markets: 50 of 158` on the same line uses a NARROWER
definition -- markets in the lane's live book -- against the 190-market
`realized_by_market` set used here, which is why the two percentages
differ. Neither is wrong; they count different things.

**It does not change the sign.** Sports settled -$29.3039 (venue rows).
Even crediting the entire $14.13 the lane is **-$15.17**, and rebates
credited are $0. Rewards offset roughly half the trading loss.

## 3. My campaign proposal was internally inconsistent

Both criticisms are correct.

**"One window" vs "20 markets".** A 5-minute window IS one market, so the
proposal costed twenty of the thing it had scoped as one. Either one
market at about -$0.02 expected, or twenty windows at about -$0.43. I
conflated the two words; the figures were not reconcilable as written.

**-$0.0215 vs -$0.0750.** Three different statistics over the SAME 148
authenticated BTC15m markets (2026-10-12..11-05), none of them BTC5m:

    mean_realized_usd    -$0.0215   total settled P&L / n  (-$3.18 / 148)
    ev_per_market_usd    -$0.0190   shrunk mixture
                                    p_pair*margin + (1-p_pair)*residual
    ev_lower_usd         -$0.0750   ev - 1*se, with se = $0.0560

The point estimate is about -$0.02 and the one-standard-error lower bound
is -$0.075. The gate reads the lower bound, by design. My commit message
for `e2f2c564f` wrote "EV -$0.0215", which is the MEAN REALIZED, not the
EV -- a conflation inside the commit text; the module reports both
separately and correctly.

**None of these is measured BTC5m profitability.** BTC5m has no
measurable rate. These are BTC15m rates on the same two-sided maker
mechanism, used as the nearest valid proxy, and labelled as such.

## 4. Smallest change that would make the economics viable

From the measured BTC15m rates, break-even requires ONE of these
(holding the other two fixed). Each reproduces EV = 0 exactly:

    1. p_pair          0.3716 -> 0.4405   +6.9 points  (+18.5% relative)
    2. residual loss  -$0.1375 -> -$0.1033   24.9% less loss per strand
    3. pair margin    +$0.1746 -> +$0.2324   +33.1%

**Lever 1 is the smallest.** 6.9 points of pair rate, against 24.9% of
strand loss or 33.1% of margin.

The cheapest route to it needs no new component: condition an opening on
the COMPLEMENT's executable book rather than on our own rung alone, so an
entry is taken only where the pair is already completable inside the
ceiling. Book-relative quoting already exists in the controller
(`_bookrel` / `straddle_book_relative`); what is missing is making the
complement's executability a PRECONDITION of the first leg rather than a
hope after it. That is a change inside the existing controller.

Levers 2 and 3 are worth noting as measured facts rather than targets:
completion and reduction at the touch are the SAME trade to the cent, and
all seven executable pairs in the recorded-depth tape were above par, so
neither is reachable by completing more aggressively.
