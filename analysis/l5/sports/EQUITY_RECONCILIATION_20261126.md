# Account equity vs ledger: reconciliation, gate inventory, permanent fix (26 Nov 2026, 13:30 UTC)

Trading is locked (sports `PMUS_TWO_SIDED_SUBMISSION_LOCK=1`; crypto
disarmed). No limit was changed.

## 1. Venue equity now (source of truth)

| | |
|---|---|
| Cash / buying power (`/v1/account/balances`, ledger 12:52Z; sports `PILOT_PNL` agrees) | **$310.9035** |
| Open positions | none (Setka `slomyk` paid +$2.18 at about 04:00; UTR Garcar–Borvic settled at 0 at about 10:00, −$2.60) |
| Open orders | 0 |
| **Equity** | **$310.9035** |

## 2. Equity rebuilt from the venue's records

The bridge starts from the first flat-account cash read, 16 Nov 00:11Z
($338.3456, the ledger's cash anchor). Earlier history is covered in §2c.

| Item | $ | Source |
|---|---|---|
| Start | 338.3456 | ledger cash anchor; account flat |
| Deposits − withdrawals | 0 | venue activity feed read to its end (to 14 Aug): no deposit or transfer after the anchor |
| Bot (sports), 16–20 Nov | ≈ −38.07 | ledger realized after venue-fill corrections, less manual items |
| Bot (sports), 21 Nov – 25 Nov 03:30 | −3.67 | authorization file §46, venue-reconciled |
| Bot (sports), 25 Nov campaign | −4.05 | `PILOT_PNL` −1.4499 plus Garcar–Borvic −2.60 |
| Bot (sports), 25–26 Nov campaigns B and C | +5.08 | from cash. Venue rows confirm +1.26 (pairs +0.17, slomyk +1.09); **≈ +3.8 UNRESOLVED** (see §2c) |
| Crypto BTC5m | −0.0265 | window packets |
| Crypto BTC15m since 16 Nov | 0 | assumed; UNRESOLVED |
| Manual: UConn | −20.63 | venue: −25.00, then +4.37 |
| Manual: NHL | +6.562 | −24.9974 buy, +31.5594 sell |
| Manual: two settled markets | −1.40 | `MANUAL_FILL_STUDY` |
| Manual: three NBA markets 17 Nov (64 contracts) | UNRESOLVED | listed, no resolution read |
| Fees | inside the figures | the venue's `realizedPnl` includes fees; bot fees $4.36 lifetime (`ACCOUNT_AUDIT`) |
| Rebates | 0 | `ACCOUNT_AUDIT` |
| Liquidity rewards credited | +24.09 | `REWARD_CHECK`: 122 PAID rows |
| Referral bonus | +5.00 | venue row 25 Nov 02:19 |
| **Rebuilt** | **≈ 311.23** | |
| **Venue** | **310.9035** | |
| **Unexplained** | **≈ −0.33** | |

### 2a. P&L by owner

| | Bot | Manual | Combined trading |
|---|---|---|---|
| 26 Nov UTC | +2.34 from cash (rows: Garcar–Borvic −2.60, slomyk +1.09; rest unresolved) | −20.63 | −18.29 |
| Since 16 Nov 00:11 (cash-anchored) | ≈ −40.7 | −15.47, plus the three NBA markets (unresolved) | ≈ −56.2 |
| — after rewards (+24.09) and bonus (+5.00) | | | ≈ −27.4 |

**Profits and rewards are credited.** The bridge carries every gain:
- pairs, slomyk +1.09 and NHL +6.56;
- $24.09 of rewards and the $5 bonus.

The rebuilt equity matches the venue to about 33 cents. The ledger's
lifetime figure is **trading only**: rewards and bonuses are not in it, by
design, and they are not counted twice.

### 2b. What the ledger says today

- **Day and all-time realized:** −471.52 and −516.36. The UConn row is
  still booked at −469; the first deployment's correction refused (see §4).
- **Equity drawdown** (anchor 312.4102 − cash 310.9035): **1.5067**, correct.

### 2c. Gaps (explicitly unresolved, not plugged)

- **Opening capital:** no service logs the amounts of the four deposits,
  eight transfers and one bonus in the feed. The first known balance is
  $350.0526 on 16 Oct.
- **16 Oct – 16 Nov:** −11.71, not decomposed.
- **26 Nov 01:55–02:25:** about +$5.5 of cash is not traced to a row (a
  second promotion credit is possible but unconfirmed).
- **Bot rows:** 1,203 of 1,209 bot trade rows carry no `realizedPnl`. The
  venue nets pairs, so a bot result is confirmed by cash or by settlement
  rows, not by trade rows.

## 3. Risk gates and what they read

V = read from the venue each time; R = rebuilt from the ledger's own history.

| Gate | Reads | Class | Could a $1 bound enter as a confirmed loss? |
|---|---|---|---|
| Ledger daily halt ($50) | sum of today's settled rows | R | yes, before this fix |
| Ledger cumulative ceiling ($75) | all-time realized plus open book worst case | R | yes, before this fix |
| Sports: account daily halt and cumulative ceiling | the ledger's published figures above | R (inherited) | yes, before this fix |
| Account drawdown limit ($20) | buying power + guaranteed pairs vs owner anchor | **V** | no |
| Equity baseline (transfer-adjusted) | buying power vs baseline, plus transfers | **V** | no. Not enforced by either service; its transfer read is not complete |
| Ledger per-market / total exposure, risk floors | book positions at cost; unknown price = $1 | R (worst case of open positions) | as open exposure only |
| Sports lane cap, at-risk budget, unpaired and spend caps | bot book | R (bot's own fills, venue-reconciled hourly) | no |
| Sports loss stop ($10) and pilot latch | bot fills reconciled to venue trades and resolutions | mixed | no |
| Sports two-sided daily loss | mark-to-market estimate | R | no; **not fail-closed** (an unmarked market adds nothing) |
| Position mismatch latch, audit, safety latch, halts | venue positions, audit freshness | V | — |

Other paths by which a bound could enter realized were also reviewed:
- `correct_settled_market` can only deepen a loss and runs only from fixed
  specs.
- `apply_ledger_adjustments` and the one-time cash re-base use fixed
  specs, and the re-base cancels bounds against cash.

**Settlement of a stale adopted position was the only live path.**

## 4. Permanent fix (ledger release, for authorization)

Release `e69e20ac71fcd084183e25c14e8f28f836abc487`, branch
`release/l5-pmus-live-unresolved-e69e20ac7` (code `4115ea9b5` on top of
the running `086380d88`).

Full ledger suite: 21,777 passed, the **same 72 failures and 26 errors by
name** as deployed `c70e244a2` (`regression/ledger_full_unresolved.txt`).
New tests: `test_l5_venue_round_trip.py` (17) and
`test_l5_unresolved_bound.py` (6).

1. **The round trip read from the venue's real rows.** The first
   deployment refused UConn, NHL and two other markets with
   `TRADE_REALIZED_PNL_UNREADABLE`, because the venue leaves `realizedPnl`
   off a trade that realizes nothing (the purchase).
   - Trades are now replayed in time order against the venue's net
     position.
   - A trade that opens or adds may lack the figure; it counts as 0.
   - A trade that closes must carry it, or the form refuses.
   - A market whose net is zero (YES and NO netted away) is closed.
2. **Bound-priced rows are UNRESOLVED and the gates refuse.** Rows settled
   after the 16 Nov cash re-anchor and still booked at the $1 bound are
   published as `unresolved_cost_basis`, with count, booked bound, and
   `confirmed_realized_usd` excluding them.
   - The booked figure stays in the totals, so nothing is hidden.
   - While any remain, the published account-risk record is
     `readable: false` with reason `UNRESOLVED_COST_BASIS:<n>`. The sports
     bot then refuses new exposure (completions continue), and the ledger's
     own order path refuses the same way.
   - A row clears when the venue restates it, or when the owner names the
     market in `L5_ACCEPT_BOUND_MARKETS`. Naming a market accepts the bound
     as booked; it changes no figure.
   - Rows before the re-anchor are inside that cash read and are not listed.
3. **Restarts:** every correction is stored by id in the state file;
   tested.

Expected after deployment:
- UConn is restated to −20.63 and NHL to its venue result (+6.56 per the
  activity study).
- Two older post-anchor rows may stay unresolved:
  - UTR `gaskan-hanbra` (17 Nov, booked 0);
  - test-cricket `afg-zim` (17 Nov, booked −1).

  If they do, the gates stay refused until the venue restates them or the
  owner accepts them by name. That is the intended fail-closed outcome. The
  owner is told which ones.

## 5. The 72 test failures and 26 errors (identical before and after)

| Group | Count | Cause | Accounting or safety? |
|---|---|---|---|
| Gemini lanes (terms gate, transport, settlement, payload, wiring, collector) | 39 (27 failures, 12 errors) | different venue client, not deployed here; imports and fixtures out of date | no |
| `test_pmus_status_live_handoff` | 14 errors | test references `pmus_ws_evidence_recorder._LIVE_STATUS`, which was renamed | no (status publisher) |
| Spread scanner, orderbook | 14 | legacy Kalshi modules | no |
| Image, lineage, deploy and wiring checks (`r1_source_lineage`, `lane_a_probe`, `pmus_live_image_closure`, `deploy_fallthrough_guard`, `chain_validation`, `chain_ca_fallback`, `window_forensics_stream`, `shadow_controller_wiring`) | 11 | checks of build artifacts and file allow-lists against a tree that has moved on | release hygiene, not trading |
| `test_pmus_auth` signature | 4 | byte-for-byte comparison with an older executor's signer | no (production auth works) |
| **Ledger adoption tests** (`multi_market_cycles` 7, `book_coherence` 4, `own_terms_accounting` 2, `uncertainty_recovery`, `potential_exposure`, `ported_safety_fixes`) | 16 | fixtures use bare market names (`1900z`). Since the 15 Nov ownership rule, an untracked order outside the ledger's own series is treated as foreign: listed, never adopted, never cancelled. The tests still expect adoption | **yes.** One asserts that a manual resting order counts against the crypto cash gate; under the new rule it is counted in account-wide exposure at $1 a contract instead. Fixtures should be updated to real series names. Recommended next, not done in this release |

None of the failures is in the venue-cost attestation, settlement, realized
figures, the drawdown limit or the account-risk publication.
