# Fill-loss measurement and the 26 Nov +$1.31 cash step

26 Nov 2026, read-only analysis. Source: sports-bot production logs
(`PMUS_TWO_SIDED` kinds `FILL_MARKOUT`, `FILL`, `PILOT_PNL`,
`ACCOUNT_AUDIT`, `REWARD_CHECK`, `MANUAL_FILL_STUDY`, `COMBO_MAKER_STATUS`,
`boot`) and ledger logs (`L5_LIVE_CASH`). Nothing was changed or deployed.

**Every number in Part 1 is a measured markout, not realized P&L.** A
markout is where the mid of our side stood h seconds after a venue-confirmed
fill, minus our fill price. Settlement decides the result.

## Part 1. Fill loss

### Data

- 77 venue-confirmed fills with markouts (`FILL_MARKOUT`,
  `t_source: venue_order_row`), 96.83 contracts, 22 Nov 21:41 to 26 Nov
  21:40 UTC. That is everything the logs hold: no `FILL_MARKOUT` before
  22 Nov 21:41, none for 21 Nov, none after 21:40 on 26 Nov.
- Sign: `markout = mid(our side, t+h) − px`, checked against the logged
  book on every record (0 mismatches). Below, **loss = −markout**, so a
  positive loss is against us. Units: cents per contract.
- Placement is inferred from `book_before` (age 0–13 s): our price against
  our side's best bid (YES bid, or 1 − YES ask for NO). 0 ticks = touch,
  1 = one behind, 2+ = ladder levels. It is inferred, not read from the
  order record.
- Phase is on records from 23 Nov on; the 22 Nov NBA fills have none.
- Intervals are mean ± 1.645·sd/√n, for n ≥ 5 only. Fills are weighted
  equally (the quantity-weighted means are close and do not change any
  conclusion). Per-fill sd: 3.6c at 5 s, 5.3c at 30 s, 8.0c at 120 s, 12.0c
  at 300 s.
- The existing studies are consistent with this one and are not
  recomputed here: `adverse/ADVERSE_SELECTION_20261123.md` (17 fills, 22–23 Nov)
  and the 25 Nov table in `CAMPAIGN_B_NET_20261125.md` (15 fills, 13:58–19:58).

### Openings and extensions vs completions (all competitions)

Loss in cents per contract, mean [90% interval].

| Fills | n | 5 s | 30 s | 120 s | 300 s |
|---|---|---|---|---|---|
| Openings + extensions | 44 | 1.0 [0.3, 1.8] | 1.0 [0.0, 2.1] | 2.0 [0.6, 3.4] | **3.5 [1.1, 5.8]** |
| Completions | 33 | 0.6 [−0.6, 1.9] | −0.6 [−2.5, 1.3] | −1.4 [−4.3, 1.5] | −4.5 [−8.4, −0.7] |
| All fills | 77 | 0.9 [0.2, 1.5] | 0.3 [−0.7, 1.3] | 0.5 [−1.0, 2.0] | 0.0 [−2.2, 2.3] |

Openings lose more the longer you look: the move against a new leg
continues rather than reversing. Completions gain by about the same amount.
This is the pair mechanism: the completion fills because the price moved
toward it, which is the same move that hurt the opening. So the all-fill
average near zero is not "no adverse selection"; it is the two legs of one
move cancelling.

### By placement (openings + extensions)

| Placement | n | 5 s | 30 s | 120 s | 300 s | Setting |
|---|---|---|---|---|---|---|
| One tick behind | 34 | 0.8 [0.0, 1.7] | 1.2 [−0.1, 2.4] | 1.6 [0.0, 3.2] | **4.2 [1.3, 7.1]** | 3.5c |
| Two or more behind | 7 (small) | 0.9 [−0.7, 2.4] | −0.7 [−2.5, 1.1] | 2.2 [−1.2, 5.6] | −1.0 [−4.3, 2.3] | (behind 3.5c) |
| At the touch | 3 (small) | 3.5 | 3.2 | 5.5 | 5.5 | 5.0c |

All fills by placement (openings and completions together), for the
net-rank formula that charges every fill:

| Placement | n | 5 s | 30 s | 120 s | 300 s |
|---|---|---|---|---|---|
| One tick behind | 56 | 1.2 [0.4, 1.9] | 1.0 [−0.1, 2.1] | 1.2 [−0.4, 2.8] | 0.5 [−1.9, 2.9] |
| Two or more behind | 16 | −0.6 [−2.2, 1.0] | −2.9 [−5.5, −0.2] | −3.3 [−7.5, 0.8] | −4.4 [−10.3, 1.6] |
| At the touch | 5 (small) | 2.2 [−0.8, 5.2] | 3.0 [0.9, 5.1] | 4.9 [1.9, 7.9] | 8.9 [3.5, 14.3] |

Edge at fill (mid before − our price) averaged 1.6c one behind, 4.2c two or
more behind, 1.5c at the touch; the mid then moved 2.8c, 3.6c and 3.7c
against us within 5 s. The fill happens on the jump, at every placement.

### By competition (openings + extensions; loss, cents)

| Competition | n | 5 s | 30 s | 120 s | 300 s | Setting (behind / touch) | Verdict |
|---|---|---|---|---|---|---|---|
| atp | 15 (13 one behind) | 1.7 [0.7, 2.7] | 1.3 [−0.1, 2.6] | 3.7 [1.3, 6.2] | **6.3 [3.6, 9.1]**; one behind 5.8 [2.8, 8.9] | 3.5 / 5.0 | **too low** |
| utr | 17 (13 one behind) | 0.9 [−0.4, 2.2] | 0.4 [−0.9, 1.8] | 1.0 [−0.9, 2.8] | **0.7 [−2.2, 3.7]**; one behind 0.9 [−2.7, 4.6] | 3.5 / 5.0 | **looks too high** (interval reaches 3.5 only at its edge) |
| atpdb | 4 (small) | 0.0 | −0.5 | −1.1 | −1.6 | 6.0 / 8.0 | insufficient data (nothing supports the higher override; nothing refutes it) |
| setkameua | 4 (small) | −1.4 | 3.9 | 3.6 | 13.8 (one fill −44.5c) | 3.5 / 5.0 | insufficient data (sign points to too low) |
| nba | 4 (small, 22 Nov, no phase) | 3.2 | 1.2 | 1.0 | −0.7 | 3.5 / 5.0 | insufficient data |
| wta, wtadb, setkamemd, itf*, cfb, others | 0 | – | – | – | – | – | no markout data |

All fills by competition (300 s loss): utr −0.1 (n 28), atp 0.1 (n 26),
atpdb −7.9 (n 10, most of it four Campaign D completions on 26 Nov), nba 3.2
(n 8), setkameua 10.9 (n 5).

### By phase

| Phase | n | 5 s | 30 s | 120 s | 300 s |
|---|---|---|---|---|---|
| live | 65 | 0.8 [0.1, 1.6] | 0.3 [−0.8, 1.5] | 0.7 [−1.0, 2.4] | −0.3 [−2.9, 2.2] |
| pregame | 4 (small) | −1.0 | −0.8 | −1.0 | −0.5 |
| not recorded (22 Nov) | 8 | 2.1 | 0.8 | −0.1 | 3.2 |

Almost all fills are live (in-play). Pregame cannot be assessed (4 fills,
all Setka / ATP doubles at 1–2 ticks behind, all within 1.5c).

### Which horizon is the right proxy

**300 s, checked against 120 s.**

- 5 s measures only the jump that filled us (about 1c, i.e. the bot's one-tick
  adverse allowance). Opening loss keeps growing to 2.0c at 120 s and 3.5c at
  300 s, so 5 s understates the cost by about 3×. The loss is informational
  (the price kept going), not a bounce.
- The median time from an opening or extension to the next completion in the
  same market is 358 s (quartiles 178 s and 1,071 s, n 31). 300 s is the
  horizon at which the bot actually decides what the leg is worth (complete,
  extend or hold).
- Settlement is the true result but is ±50c per contract of outcome noise;
  with tens of fills it cannot estimate a 3–5c cost. If the mid is fair, the
  300 s markout is an unbiased estimate of the settlement result of the leg,
  with far less noise.
- 300 s is noisy too (sd 12c per fill): per-competition figures need roughly
  50 fills each before the interval is ±3c.

### Against the current settings

Settings (boot record `net_rank.fill_loss`, 26 Nov 21:00 and 21:06): touch
0.05, behind 0.035, atpdb and wtadb 0.08 / 0.06.

- **One tick behind, all competitions: 3.5c is about right** for openings and
  extensions (4.2c measured, [1.3, 7.1]); Campaign B's 25 Nov figure was 1.4c
  for all behind fills and 3.8c for openings, consistent with this one.
- **Touch: 5c is at best a floor.** 8.9c on all 5 touch fills [3.5, 14.3], 5.5c
  on the 3 openings, 6c on the single 25 Nov fill. Too few fills to set a
  number; nothing suggests touch is cheaper than 5c.
- **atp: too low.** 6.3c opening loss at 300 s [3.6, 9.1], and 3.7c already at
  120 s. A behind value nearer 0.06 would match the data.
- **utr: looks too high.** 0.7c [−2.2, 3.7]. 3.5c is near the top of the
  interval; 0.01–0.02 would match the data, with the caveat that 17 fills is
  thin.
- **atpdb / wtadb override (0.06 / 0.08): insufficient data.** Four ATP doubles
  openings, none lost at any horizon; no WTA doubles fill. Keep it as a
  caution setting, not as a measured one.
- **setkameua, setkamemd, wta: insufficient data** (4, 0 and 0 openings).
- What the formula charges matters. The net-rank estimate charges
  `2 sides × size × fill rate × loss` for every fill. Measured over all
  fills (openings and completions) the 300 s loss one behind is only 0.5c,
  because completions gain what openings lose. Charging 3.5c on every fill
  double-counts unless completions are rare; charging it on openings and
  extensions only (as the marginal-EV model does, which does not evaluate
  completions) matches the measurement.

### Fill rate per resting hour

Resting time is not in the markout records. The only measurement is the
existing one (`CAMPAIGN_B_NET_20261125.md`, 25 Nov 13:58–19:58, own order
logs): contracts filled per resting contract-hour

| Placement | Measured (25 Nov) | net-rank setting (boot) | marginal-EV code default |
|---|---|---|---|
| Touch | ~4 (1 fill) | 3.0 | 0.5 |
| One behind | 1.2–2.9 (12 fills) | 1.5 | 0.2 |
| Two or more behind | ~0.5 (2 fills) | (behind 1.5) | (behind 0.2) |

The deployed values (3.0 / 1.5, boot `net_rank.fill_rate`) are about right
behind and possibly low at the touch. The marginal-EV code defaults
(0.5 / 0.2) are 6–15× below the measurement; if they were ever used unset,
the marginal-EV model would understate fill cost by that factor. The boot
record shows the 3.0 / 1.5 table in force, and the marginal-EV model reads the
same `RN1_FILL_RATE` table. A fresh fill-rate measurement for 26 Nov
(Campaigns C and D) needs the order placement and cancel times, which this
pass did not extract.

## Part 2. The +$1.31 cash step on 26 Nov

### What is venue-confirmed

| Time (UTC) | Cash (`/v1/account/balances`) | Source |
|---|---|---|
| 03:24:00 | 308.7235 | sports `PILOT_PNL` baseline |
| 04:24:01 | 310.9035 (+2.18) | `PILOT_PNL`; `ACCOUNT_AUDIT` position resolutions 59 → 60: Setka `slomyk-fasiva` 2.18 YES paid (as in `EQUITY_RECONCILIATION_20261126.md`) |
| 12:00:42 – 14:18:22 | 310.9035 | ledger `L5_LIVE_CASH`, every 60 s |
| **14:19:25** | **312.2135 (+1.3100)** | ledger `L5_LIVE_CASH` |
| 14:24:40, 15:24:43 | 312.2135 | sports `PILOT_PNL` (`unexplained_usd` 3.49 = 2.18 + 1.31) |

So the credit landed between 14:18:22 and 14:19:25 UTC, and is exactly
$1.3100.

### What it is not

| Candidate | Evidence (12:00–15:30 and later) | Result |
|---|---|---|
| Bot trading | trading locked; `PILOT_PNL` execution fills 0; no `FILL` | excluded |
| Trade or position resolution on the account (bot or manual) | `ACCOUNT_AUDIT` unchanged 10:24 → 21:09: 1,270 rows (1,209 TRADE, 61 POSITION_RESOLUTION), report and `realized_by_market` identical at 13:24 and 14:24 | excluded |
| Owner's manual sports fills | `MANUAL_FILL_STUDY` identical 09:24 → 15:24 (13 markets, 37 trades, settled $0.05) | excluded |
| Combo (RFQ) maker | `COMBO_MAKER_STATUS` mode shadow, confirms 0 all day | excluded |
| Liquidity reward shown by `/v1/incentives/earnings` | `REWARD_CHECK` PAID unchanged at 122 rows / $24.09 from 25 Nov to 26 Nov 21:08; no row changed status at 14:23 | **not shown as paid** |

### The reward rows that exist for these markets

`REWARD_CHECK` since 10:22 shows 10 new rows dated 25 Nov, all **PENDING**
(accrued, not credited), total $0.86 by 13:23 and unchanged after. They are
the Campaign B/C markets:

| Market | Reward row (25 Nov, PENDING) | Bot result in that market (`ACCOUNT_AUDIT` realized_by_market) |
|---|---|---|
| aec-atpdb-delanedelane-falcksuarez-2026-11-25 | $0.59 (was $0.52 at 12:22) | $0 (pair 0.34 at 0.86: about +$0.05 locked edge) |
| aec-setkameua-slomyk-fasiva-2026-11-26 | $0.10 | +$1.09 (2.18 YES residual paid; plus a 1-contract pair at 0.97) |
| aec-utr-feragu-tistom-2026-11-24 | $0.05 | no bot fills |
| aec-utr-measof-markat-2026-11-24 | $0.04 | no bot fills |
| aec-utr-boymel-gobvic-2026-11-24 | $0.02 | no bot fills |
| aec-utr-dolhey-monmic-2026-11-24 | $0.02 | −$0.30 |
| aec-atp-jaskub-carove-2026-11-25 | $0.01 | −$1.25 |
| aec-setkameua-shyole-tymole-2026-11-25 | $0.01 | −$0.36 |
| aec-setkameua-tebyar-paryur-2026-11-26 | $0.01 | no bot fills |
| aec-utr-kawgab-duadan-2026-11-24 | $0.01 | no bot fills |
| **Total** | **$0.86 accrued** | settled trading in these markets about −$0.82 plus +$0.05 locked |

The account fees in these markets were zero (maker fee rate 0 in the boot
record). Accrued rewards ($0.86) roughly equal the settled trading loss here
($0.82); none of the rewards is credited yet according to the venue.

### Conclusion

- **Venue-confirmed:** +$1.3100 of cash, credited between 14:18:22 and
  14:19:25 UTC on 26 Nov, with no trade, no resolution and no fill on the
  account's activity feed, and no liquidity-reward row moving to PAID.
- **Not identified from the logs.** It does not match the pending reward
  total ($0.86) or any subset of it, and the incentives endpoint still lists
  every 25 Nov row as PENDING after the credit. It is not explained by
  trading.
- **Inferred, unconfirmed:** most likely a venue credit outside the two
  feeds the bot reads. Precedents: 24 Nov, $1.30 matched two Serie A reward
  rows moving to PAID (section 36 of the authorization log); 25 Nov 02:20, a
  $5.00 step with no row (possibly a promotion). Rewards for 23–24 Nov have no
  rows at all on the incentives endpoint, so a payout for those days cannot be
  excluded, but no row shows one.
- It must stay out of trading P&L and out of reward income until a source
  row names it.

### What would confirm it

1. The venue account's full activity/transactions history for 26 Nov
   14:18–14:20 UTC including non-trade types (reward, promotion, rebate,
   adjustment, deposit). The bot's audit only counts TRADE and
   POSITION_RESOLUTION rows.
2. The next `REWARD_CHECK` runs: if a row (25 Nov or 23–24 Nov) moves to
   PAID with $1.31, or rows for 23–24 Nov appear as PAID summing to $1.31.
3. The venue's reward/promotion e-mail or the account's rewards page for
   26 Nov.
