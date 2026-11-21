# Money-path status — 2026-09-23

Written against the priority change: *move toward realized income, not research
for its own sake.* Every number below carries its evidence label. Nothing
modelled or shadow is called realized.

---

## 1. What can be live-tested today

**Exactly one thing: Probe B2 on Gemini BTC5m.**

It is fully prepared and frozen (`analysis/gemini/PROBE_B2_MANIFEST.md`): four
windows, q=1, `spec_sha bfe7ba23…` unchanged from Probe B, four fresh cohort
ledgers at 12 actions each, fresh latch, every gate verified fail-closed. It
needs two things this session cannot supply:

1. **owner authorization** — a fresh id (`OWNER-2026-09-23-GEMINI-Q1-B2-1`
   proposed), a fresh expiry, and the three constants installed;
2. **a run host holding the Gemini credentials** — this container has none, so
   the credentialed half of readiness has never passed here.

Nothing else is live-testable. The Kalshi lane is read-only by construction and
no Kalshi authorization is requested. PMUS is not prepared for a probe.

## 2. Maximum dollars at risk

**$10.16 across the whole of Probe B2.**

Per window the order path itself refuses to exceed $2.50 committed (filled +
resting + the frame being sent), plus at most $0.04 of maker fees (four fills
maximum at `ceil_cent(0.0175·C·P·(1−P))`, whose q=1 maximum is $0.01). Four
windows → $10.16. That is a structural ceiling enforced by
`GEMINI_L5_LIVE_ENVELOPE_Q1_V1`, not a forecast; the `--max-loss-usd 10.00` stop
is a second independent brake that sums settled windows only.

For calibration: Probe A actually moved 16 orders / 5 fills / settlement-implied
−$2.19, and Probe B 6 fills / settlement-implied −$0.33.

## 3. What result would justify another probe

B2 is a **mechanism** test, not a profit test, and it must not be read as one —
six windows cannot rank two arms. It earns a successor if **all** of:

1. `placement_reached_driver == "maker_improve"` on every window, with zero
   `REFUSED_PLACEMENT_NOT_DELIVERED`;
2. the placement log shows `native_price → submitted_price` improved by exactly
   one tick on the actions where an improvement was legal, and shows the
   stand-down (no submission) where improving would have crossed the ask;
3. settlement completes and reconciles, with every window labelled SETTLED and
   the cohort economics recomputed from settled rows;
4. cumulative settlement-implied economics no worse than Probe B's −$0.33 per
   four windows.

(1) and (2) are the point. (3) and (4) are hygiene.

## 4. What kills the lane

Any of these ends Gemini BTC5m/BTC15m as a revenue candidate:

- **B2 fills at a materially worse price than B**, i.e. improving by a tick
  buys queue position that is worth less than the tick costs. That is the
  hypothesis failing on its own terms.
- **Repeat of Probe A's asymmetry** — five fills, all on one side, p=0.0128 —
  now with placement actually delivered. One-sided fill under a two-sided quote
  means we are being adversely selected, and no amount of patience fixes that.
- **Venue position representation stays UNRESOLVED.** Matched inventory,
  `matched_pairs`, pair cost, total spend and terminal two-leg P&L cannot be
  reported as Gemini economics until it is resolved. Without it there is no path
  from "fills happened" to "cash moved", and the lane cannot ever be scaled
  responsibly.
- **Capacity**, below, which is the standing problem and is not fixed by any
  probe outcome.

## 5. Realistic capacity if it works

This is the uncomfortable part and it has not changed.

| OBSERVED (24h to 2026-09-23 12:30Z, all participants) | |
|---|---|
| Gemini BTC15m whole tape | **$19,935/day** |
| Gemini BTC5m whole tape | **$36,937/day** |
| Gemini BTC15m prints per window (median) | 4 |
| Gemini BTC5m prints per window (median) | 3 |

| DERIVED / MODELLED — not realized | |
|---|---|
| Gemini BTC15m trading income at 10–20% participation | **$11–23/day (provisional modelled range)** |
| L5's historical Polymarket take | $1,226/day |

Against that, the only **credited** Gemini cash this programme has ever produced
is **+$0.51** of maker rebates (7 `AdminCredit` rows via `POST /v1/transfers`,
97.59 bp of maker notional, on 61 maker fills / $52.26 maker notional). That
rate is real and credited, but it is measured on $52 of notional; projecting it
onto a $2,000/day maker book is a ~40× extrapolation and is **modelled, not
evidence**. It stays out of pre-rebate P&L and out of strategy selection, and it
does not rescue a losing cohort.

**Honest capacity statement: even a successful B2 leads to a venue whose entire
BTC15m tape is $19,935/day. At a participation share nobody has demonstrated, it
models to tens of dollars a day, against a target of $480/day. Gemini is a
mechanism laboratory. It is not, on current measurement, an income venue.**

Kalshi is where the volume is — a *median* $673k per 15-minute KXBTC15M window,
~1,800 trades·min⁻¹, validated across fourteen consecutive windows with zero
duplicates. But its maker subsidy over KXBTC15M was **withdrawn on 2026-05-12**
and its quadratic fee is maximal exactly where a two-sided recycler quotes. The
subsidised 15-minute crypto series (KXCRYPTOLEAD15M, $20/window live now) trades
3–13 times a window. **On Kalshi today the flow and the subsidy are in different
markets** — see `analysis/kalshi/P22_KALSHI_KXBTC15M.md`.

This matters because L5's Polymarket business was **−$80.20 trading and
+$2,557.28 incentives on $266,229.81 of spend**. The rebate *was* the business.
Kalshi KXBTC15M offers the volume without the rebate — the half that did not
make money.

## 6. What specifically prevents revenue today

In order of how much they block, not how hard they are:

1. **No authorization is installed.** `EXPECTED_AUTH_ID` is
   `NO-AUTHORIZATION-IN-FORCE`, `AUTHORIZED_BUNDLE` / `REQUIRED_AUTH_ID` /
   `AUTHORIZED_SHA` / `AUTHORIZED_ENVELOPE` are all `None`. This is the owner's
   decision and it is the only thing standing between here and the next live
   test. *(Owner action.)*
2. **No Gemini credentials in this container.** `terms: NO_CREDENTIALS` and
   `positions_readable: false`. B2 must run where the credentials live.
   *(Infrastructure, not research.)*
3. **The venue position representation is UNRESOLVED.** Until it is, no Gemini
   fill can be carried to an attributable cash movement, so nothing on Gemini
   can become REALIZED even if every probe succeeds. *(This is the real
   bottleneck and it is not a research question — it is a reconciliation
   question against the venue's own endpoints.)*
4. **Capacity.** §5. No probe fixes this; only a bigger venue does.
5. **Kalshi's subsidy is withdrawn on the liquid market.** Watched continuously
   and at zero cost by `src/kalshi_l5_collector.py`, which logs
   `KALSHI_INCENTIVE_STATE` every cycle. *(Nothing to do but watch.)*

Note what is **not** on this list: the spec→execution integrity defect that
invalidated Probe B. That is fixed, proven end to end, and pinned by tests.

## 7. Recommendation

1. **Authorize B2 or kill the Gemini lane.** It is bounded at $10.16, fully
   frozen, and answers a question that has now cost two probes. There is no
   third preparation step worth doing.
2. **In parallel, resolve the venue position representation.** It is the only
   blocker on this list that no probe outcome can clear, and until it clears,
   Gemini cannot produce a REALIZED number whatever B2 shows.
3. **Leave Kalshi read-only.** The collector runs 24/7 at no cost and watches
   for the one event that would change the verdict — a liquidity program
   reappearing over a liquid 15-minute crypto series.
4. **Do not scale Gemini on a good B2 result.** §5 says the ceiling is tens of
   dollars a day. A good B2 validates the mechanism; it does not find the
   income, and the income is not there.
