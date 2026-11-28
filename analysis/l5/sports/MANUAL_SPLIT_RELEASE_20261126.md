# Bot-versus-total split with manual orders found by the venue indicator — release candidate (26 Nov 2026)

Status: **prepared, not approved, not deployed, OFF by default.** Nothing
in production changed. No Railway write call, no environment variable, no
deploy.

## What was built

| item | value |
|---|---|
| base | production ledger release `c111abd67` (`release/l5-pmus-live-anchor2-c111abd67`, code `a4bb80f87`) |
| dev branch | `dev/l5-portfolio-split-on-c111abd67` = `c111abd67` + cherry-pick of `6c9fbdfc6` (now `d39a33c1e`) + real-row test `2d409ed40` |
| release candidate | `release/l5-pmus-live-split-2d409ed40` at `d625b00b1` (one line appended to `deploy/l5_pmus_live/DEPLOY_TRIGGER`: "release candidate for 2d409ed40 (bot-versus-total split, off by default)") |
| cherry-pick conflicts | none (applied cleanly) |

Code changed: `src/l5_portfolio_drawdown.py`, `src/l5_manual_commitment.py`
(the `Discovery` class), tests `tests/test_l5_portfolio_split_auto_manual.py`
(26) and `tests/test_l5_manual_discovery_real_rows.py` (5, new).

## Real production row verification (read-only)

Source: ledger logs `L5_VENUE_BASIS_DETAIL` (identity fields printed
verbatim from an allow-list), `L5_VENUE_BASIS_ORDERS`, `L5_COST_BASIS_AUDIT`
and `L5_VENUE_COST_ATTESTATION`, 25–26 Nov.

Layout of an activity trade row, as served (12 distinct rows, 7 BTC5m
markets):

- `type` = `activity_type_trade`, payload under `trade`;
- `trade.id` = trade id, `trade.isAggressor` = bool;
- `trade.aggressorExecution` / `trade.passiveExecution`, each with
  `tradeId` and `order.id`, `order.manualOrderIndicator`;
- also `trade.aggressor.manualOrderIndicator` and
  `trade.passive.manualOrderIndicator` (party summaries).

Discovery reads only OUR execution (chosen by `isAggressor`) and its
`order.manualOrderIndicator`, and its `order.id`. That is exactly what the
production rows carry; **no Discovery change was needed.** The party
summaries are ignored, which matters: on
`cpc-btc-updown-5m-2026-11-16-0335z` trade `DRD2JDCGD3YJ` we were passive
with `AUTOMATIC` while the counterparty (aggressor) summary said `MANUAL`.
Reading the summary instead of our execution would have misclassified a
bot fill. Indicator values seen: `MANUAL`, `AUTOMATIC`, and `UNDEFINED`
(only on counterparty summaries). `L5_VENUE_BASIS_ORDERS` order rows agree
with the execution's indicator on every listed order. Independent ground
truth (21 Nov record, `analysis/l5/btc5m/LIVE_EXECUTION_TEST_PROPOSAL.md`):
the MANUAL BTC5m orders were placed while the bot lane was not running, and
the AUTOMATIC orders of 16-0335z are the executor's own.

The owner's UConn round trip (`DYSS79F015P6` buy 02:07:50Z trade
`DYSS79FG35Q2`; `DYSW1R1G15P6` sell 02:14:01Z trade `DYSW1R2055Q1`, 469
each) is in `L5_COST_BASIS_AUDIT` with those ids, and was attested by the
round-trip form (−469.00 → −20.63) which uses the same own-execution
selection. UConn is not in the detail-market list, so its indicator VALUE
and its `isAggressor` are **not** in the logs; the test uses the owner's
statement (manual) and exercises both `isAggressor` values. No NHL
(`aec-nhl-veg-edm-2026-11-24`) fill rows are printed in the logs; only its
attestation result (+6.56) is.

Test `tests/test_l5_manual_discovery_real_rows.py` proves: the AUTOMATIC
bot orders stay bot (including the counterparty-MANUAL row and the same row
without `isAggressor`); the MANUAL orders are found; the UConn ids are
found as MANUAL; SINCE excludes earlier rows; the same rows paged through
`Discovery.step` complete one walk.

## Tests

- Targeted (split, auto-manual, venue exit probe, venue round trip,
  unresolved bound, round trip inside cash anchor, venue cost
  attestation): 244 passed after the cherry-pick; real-row test 5 passed.
- Full ledger suite, sequential, clean worktrees
  (`PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
  --continue-on-collection-errors -rfE`):
  - `c111abd67`: 72 failed, 21785 passed, 28 skipped, 1 xfailed, 26 errors;
  - `2d409ed40`: 72 failed, 21816 passed, 28 skipped, 1 xfailed, 26 errors.
  - Failing/erroring test names identical (98 names); +31 passing = the
    26 + 5 new tests.

## Activation configuration (requires separate owner approval)

Ledger service only. Set together, in one change:

| variable | value |
|---|---|
| `L5_MANUAL_BY_VENUE_INDICATOR` | `1` |
| `L5_MANUAL_BY_VENUE_INDICATOR_SINCE_UTC` | `2026-11-26T03:17:00Z` — the instant the current anchor `312.4102` took effect (deployment `0cd0cd0d`, 03:17 UTC) — **or**, if the anchor is re-set as part of activation, that new anchor's effective instant |
| `L5_PORTFOLIO_TOTAL_MAX_DRAWDOWN_USD` | owner value (the account-wide limit; not chosen here) |
| `L5_PORTFOLIO_MAX_DRAWDOWN_USD` | `20` — unchanged |
| `L5_PORTFOLIO_ANCHOR_USD` | unchanged (`312.4102`) unless the owner re-anchors |
| `L5_MANUAL_ATTESTED_ORDERS` | leave unset (UConn is already excluded via the anchor) |

The release candidate must be deployed first; on `c111abd67` the two
`L5_MANUAL_BY_VENUE_INDICATOR*` variables do nothing.

Why SINCE must not be earlier than the anchor: the anchor 333.0402 →
312.4102 already removed the UConn −20.63 from the bot figure. A SINCE
before 02:07:50Z on 26 Nov would find the UConn orders again and credit
the same loss a second time, making the bot figure $20.63 too SMALL — the
unsafe direction. Rule: **move SINCE only together with the anchor.**

Expected behaviour after activation: until the first discovery walk
completes the publisher refuses new exposure
(`AUTO_MANUAL_DISCOVERY_INCOMPLETE`); a walk older than 600 s refuses
(`AUTO_MANUAL_DISCOVERY_STALE`); a half-set mode refuses. Check the
heartbeat's `split.manual_identification` block (`by_venue_indicator` ON,
`since_utc`, `discovery.status` null) before any campaign.

## Risks

1. **The indicator is the sender's claim.** A bot order without
   `manualOrderIndicator: AUTOMATIC` could be labelled MANUAL by the venue
   and its loss taken off the bot figure. The L5 order body
   (`src/l5_order_body.py`) sets AUTOMATIC; any other sender on this account
   (another service, a script, a future path) must also. Ground truth for
   the verified rows is in `analysis/l5/btc5m/LIVE_EXECUTION_TEST_PROPOSAL.md`
   (six-market exclusion, 21 Nov): the six MANUAL markets, including the
   bot-sized `DS04XZ5014F0` (qty 2, GTC), were placed while `l5-btc5m-live`
   was not running; the two AUTOMATIC orders of 16-0335z are the executor's.
2. **Double count if SINCE precedes the anchor** (above).
3. The TOTAL figure is never adjusted, so the owner's account-wide limit
   still binds every manual loss; with the split ON a large manual loss can
   halt the bot through TOTAL.
4. Manual row MANUAL but uncostable (no time/order id): counted as a
   possible full win — bot figure larger (safe, may halt unnecessarily).
5. Extra read load: up to 2 feed pages per step, ≥5 s apart, ≤400 pages a
   walk, rewalk ≥60 s; ≤100 order reads, ≤3 GETs a tick. The first walk
   after every restart reads back to SINCE; the feed grows with time, so
   re-anchor (and move SINCE) periodically.
6. Discovery state is in memory: a restart refuses until the first walk
   completes (fail closed, not open).
7. UConn's `isAggressor`/indicator values were not visible in logs; the
   layout was verified on other real rows. A first post-activation check
   should confirm no bot order id appears in `discovery.found_ids`.
