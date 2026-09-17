# MLB same-day cohort — pre-activation evidence

> **SUPERSEDED 2026-09-17T10:40Z — DO NOT ACTIVATE.**
> The owner withdrew this league-specific authorization in favour of
> `LIVE_SHORT_HORIZON_COHORT_2026-09-17.md`, a generic live-sports cohort.
> The eligibility LAYER below was kept and generalised (the league equality is
> the only clause relaxed); the AUTHORIZATION below is void. `PMUS_L5_COHORT`
> must not be set to `mlb` on the strength of this document.
>
> Everything below is retained as the measurement record that produced the
> gate, not as a live proposal.

Prepared 2026-09-17T02:10Z. **Nothing is activated.** No deploy, no mandate, no
arming. `PMUS_L5_COHORT` is unset, and with it unset the gate refuses nothing,
so the running probe is bit-for-bit unchanged.

## 1. Existing exposure — accounted separately

Held on the **current** authorization `OWNER-2026-09-17-CP1-BREADTH10-EXECUTION-PROBE`:

| | |
|---|---|
| market | `aec-itfwo-erishi-jiahan-2026-09-17` (ITF women's tennis) |
| position | **0.48 NO**, avg **0.3825**, cost **$0.1836** |
| fills | 3 — 0.18 @ 0.43, 0.18 @ 0.45, 0.12 @ 0.21 |
| venue reconciliation | `venue_qty 0.4800` vs `ledger_qty 0.48`, delta **0.0000**, agrees |
| residual | **−0.48** units against a cap of 3.00 |
| campaign spend | $0.1836 of $15.00 — **$14.7574 remaining** |
| market state | `MARKET_STATUS_OPEN`, event **live, set S2**, `ended false` |
| settled | **NO** |
| realized | **NO** |

Venue quote at 02:10Z: `["Eri Shimizu","Jiangxue HAN"]` → `["0.9600","0.05"]`. We
hold the Shimizu-NO side, marked near **0.05** against our 0.3825 average.

**This is mark-to-market and is not realized P&L.** It is not a loss until the
venue resolves the market and the settlement audit confirms it. Worst case is
the **$0.1836 already spent**. The position is preserved exactly: no
force-close, no manual hedge, no manufactured COMPLETE.

**Accounting separation is structural, not clerical.** `consumption()` is scoped
by `authorization_id`, so this $0.1836 stays charged to the breadth-10 mandate
and cannot consume any future MLB mandate's budget.

## 2. Eligible MLB markets and their horizons

The prepared gate, run against the live venue slate at **02:08:54Z**: **2600
markets across 6 games** eligible; **247** non-MLB markets refused.

| period | proximity | elapsed | est. remaining | full-game winner | bid/ask |
|---|---|---|---|---|---|
| Top 9th | 1.000 | 2.48 h | ~0.0 h | `aec-mlb-atl-chc-2026-09-16` | —/0.0050 |
| Bot 7th | 0.778 | 1.98 h | ~0.7 h | `aec-mlb-kc-hou-2026-09-16` | 0.5750/0.5800 |
| Mid 7th | 0.778 | 2.06 h | ~0.7 h | `aec-mlb-bos-tex-2026-09-16` | 0.5300/0.5350 |
| Top 5th | 0.556 | 1.48 h | ~1.3 h | `aec-mlb-sd-col-2026-09-16` | 0.9000/0.9050 |
| Top 2nd | 0.222 | 0.48 h | ~2.3 h | `aec-mlb-mia-az-2026-09-16` | 0.3950/0.4000 |
| Bot 1st | 0.111 | 0.52 h | ~2.7 h | `aec-mlb-sea-laa-2026-09-16` | 0.5800/0.5850 |

**Every eligible market settles tonight.** The best lifecycle candidates are the
two youngest games — SEA-LAA and MIA-AZ — which should resolve around
**04:45–05:00Z**.

> **"est. remaining" is a DISCLOSED ASSUMPTION, not a venue fact.** It is
> `(1 − proximity) × 3.0 h`, using a typical MLB game length. The venue serves
> no expected duration and the game clock counts *down*, so no duration can be
> read from it. This column is for your judgement only — **the gate does not use
> it**, and nothing in the code manufactures a settlement time.

### Two things this shows that MLB-only alone would have missed

- The venue serves **3227** MLB markets. **627 are `futures`** — season and
  postseason markets like `aqc-mlb-postseason-2026-09-27-q-bos`, whose
  `gameStartTime` is **98 days in the past** and which settle in weeks.
  **"MLB-only" without a horizon gate would admit those.** The horizon clause is
  what excludes them, not the league clause.
- The next MLB slate is **>24 h out**: there are currently **zero** MLB games
  starting in the next 24 hours. Tonight's six games are the entire eligible
  universe until the 09-17 slate posts.

## 3. Blocker you should know about before authorizing

**The MLB cohort cannot start a new market while the tennis position is held.**

The campaign log reads:

```
CAMPAIGN_HOLD:POSITION_HELD (1/1 markets held, worked one session at a time;
                             residual held to settlement)   concurrency: 1
```

With `max_markets: 1`, one held market fills the whole concurrency budget. So an
MLB cohort would sit idle until the tennis resolves — which, at set S2, is
likely **within the hour**, and well before any activation could complete.

If you want MLB started *before* tennis settles, that needs `max_markets: 2` in
the authorization. That is a mandate parameter, not controller logic — but it is
a genuine risk increase (two concurrent open markets), so it is yours to decide.
**I have not assumed it.** The proposal below keeps concurrency at 1.

## 4. Proposed bounded authorization

Nothing here is self-authorized; these are the numbers I would run.

```
authorization_id       OWNER-2026-09-17-CP1-MLB-SAMEDAY-COHORT
service                l5-r1-probe
strategy_version       CP1                     (unchanged)
cohort                 mlb                     (NEW - PMUS_L5_COHORT=mlb)
max_markets            1                       (unchanged; see §3)
max_total_markets      6                       (the observed live slate)
max_spend_per_market   5.00                    (unchanged CP1)
max_total_spend        15.00                   (unchanged)
max_residual           3.00                    (unchanged)
max_daily_realized_loss 6.00                   (unchanged)
expiry                 +12 h                   (covers tonight's slate; forces
                                                deliberate re-authorization
                                                before the 09-17 slate)
```

**Unchanged and not up for negotiation in this cohort:** the L5 controller,
sizing, maker-only execution, the authenticated-book freshness gate, rest
behaviour, the pair ceiling, and residual logic. The only new thing is which
markets execution is permitted to look at.

Classified **L5_ADAPTED** (venue/market selection), **not L5_FIDELITY**. A
cohort refusal is recorded like a freshness skip and **must not be scored as a
model WAIT**, preserving `MODEL WANTED ACTION ≠ EXECUTION COULD SAFELY SUBMIT
ACTION`. `cohort_refused` and `cohort_refusal_reasons` are logged on every
selection cycle so the removed opportunity is measurable.

## 5. What must clear before activation

1. **Owner authorization** of §4, including the `max_markets` decision in §3.
2. **R1 lineage review — currently FAILING, correctly.** The gate touches
   `src/pmus_l5_continuous_session.py`, which is **EXECUTION** class:
   ```
   ACCEPTANCE: False
   undisclosed: ["src/pmus_event_progress.py",
                 "src/pmus_l5_continuous_session.py"]
   ```
   `FORBIDDEN_CLASSES` contains EXECUTION, so this needs an explicit review and
   a re-pinned baseline. **I have not re-pinned around it** — doing so would be
   the exact weakening that file exists to prevent.
3. **Image pin.** `PMUS_L5_COHORT=mlb` must be baked into
   `deploy/l5_r1_probe/Dockerfile`, not set as a Railway variable, so no service
   variable decides the cohort — same rule as the rung and market allowance.
4. **A fresh immutable mandate.** The breadth-10 mandate is not to be reused or
   enlarged.
5. **Tennis position preserved** and left on its own authorization.

## 6. Verification state

- Full suite **5032 passed, 27 skipped, 1 xfailed**.
- 24 new tests in `tests/test_cohort_horizon_gate.py`, including: unset cohort
  refuses nothing; every inning 1st–9th is eligible; not-started, ended,
  unrecognised-token, wrong-league, missing-league and feed-outage all refused;
  NPB/KBO refused despite `sport_type == "baseball"`; the gate consults no
  `endDate` and no trading parameter; it can only remove candidates; refusals
  are counted.
- Gate exercised against live venue data, not only fixtures (§2).
