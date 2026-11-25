# Next live campaign: two configurations for review — 25 Nov 2026, 20:15 UTC

Both on release `6755062ad` (deployed locked after 21:58 UTC tonight, as
authorized). Nothing armed, nothing changed.

## Reward rules — verified

From the venue's documentation (Liquidity Incentive Program page):

- Every second, each resting order scores discount^(ticks from best) ×
  size; each side of each book is normalised per snapshot and the period's
  pool is split by score. No cap per trader.
- Only orders inside the walk from the best price to the Target Size
  score; behind a touch that already meets it, nothing scores.
- Max Spread is a test of the book, not of our quotes; a one-sided quote
  earns once the book qualifies. (Corrects my earlier statement.)
- "Rewards under $1.00 are not paid out." Calculated within 5 business
  days of the period, credited within 2 more.

From our account's own earnings rows (venue endpoint, 187 rows, latest
read 19:19 UTC): one row per market per date; 122 PAID ($24.09), 65
SKIPPED ($4.00), none pending. **Many PAID rows are under $1** (down to
$0.01), so the minimum is applied to an aggregate — the rows fit "the
account's total for the date must reach $1" (11-22: $1.30 paid; 11-21:
$0.04 skipped); one date (11-17) does not fit cleanly. No rows yet for
23–25 Nov. Pool allocation (per market or shared across a programme)
remains unproven: the rows carry no programme id or period.

## Configuration A — inventory campaign, existing limits

| Setting | Value |
|---|---|
| `PMUS_RN1_FIT_QTY` | 1 (size down to fit) |
| `PMUS_RN1_PULL_THREATENED_ONLY` | 1 (selective cancellation) |
| Ladder | 3 levels, 2 ticks apart, 2 contracts |
| Cooldown | 10 s |
| Markets / slate / competitions | 4, in-play only, UTR, Setka, ATP, WTA |
| Limits | unchanged: $13 account, $3 / $6 unpaired, $8 a market, $60 spend, $4 loss stop, $5 daily halt |

Consequence to know: the loss stop now counts resting bids, so at $4 the
bot can rest about $4 of worst case at once — roughly one market fully
laddered, or two with one level. To quote four laddered markets the stop
would have to be about $8–10.

## Configuration B — reward-qualified, 10 contracts a side, ~$40 resting

| Setting | Today | B | Why |
|---|---|---|---|
| Order size (`PMUS_RN1_QTY`, pre-game, live) | 2 | 10 | reward share scales with size |
| Ladder levels | 3 | 1 | keeps resting capital at ~$40 |
| Markets | 4 | 4 | 4 × 2 sides × 10 × ~$0.49 ≈ $39 |
| Per-market unpaired cap | $3 | $8 | one 10-lot fill at up to 0.70 ($7); further adds sized down to fit |
| Per-market spend | $8 | $20 | a fill plus the reserve to complete it, plus the other side resting |
| Campaign unpaired | $6 | $20 | about three markets each carrying one fill |
| Campaign loss stop (counts resting) | $4 | $25 | resting worst ≈ $5 a market × 4 = $20, plus room |
| Daily loss halt | $5 | $25 | must not be below the loss stop |
| Account limit (ledger) | $13 | $70 | figure now ≈ $7 + $40 resting + up to $20 held |
| At-risk budget | $16 | $60 | resting plus unpaired |
| Lane cap (shared exposure ceiling) | $20 | $70 | **also raises the crypto lane's ceiling unless the split is changed; crypto is disarmed** |
| Campaign spend | $60 | $200 | |

Expected economics (theoretical, 19:45 UTC books, best four in-scope
markets): reward about $3.25/h if each programme's pool is shared across
its live markets and both sides rest continuously; about $1/h at 30%
resting time (today's resting time was ~20%). Fill cost at today's rates
(−3.4 to −7.6c a contract, about 2–3 fills an hour) ≈ −$0.7 to −$2.3/h.
Net: roughly break-even with wide variance; one lost 10-contract leg at
0.60 is −$6. Positive reward-adjusted economics is not shown. Worst case
for a campaign: the $25 loss stop.

Every row in the B column changes a production limit and needs separate
explicit authorization.
