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

## Update 20:25 UTC — verification

**Regression evidence** (pushed: `analysis/l5/sports/regression/`): release
`6755062ad` code `1508af00e` — 5,675 passed, 24 failed; the failing ids are
identical to the 24-id baseline (`baseline_failures_24.txt`), none in
sports code. 13 further tests (`tests/test_pmus_rn1_worst_case.py`, dev
commit after `1508af00e`) prove that no combination of fills — none,
partial, any subset filling together — exceeds the worst case every cap
and the loss stop use (`RN1.committed`), that a completion fill never
raises unpaired cost, and that a quoted completion is never larger than
what is unpaired. Levels resting behind a completion are opening bids and
are counted as new exposure.

**Reward qualification of a 10-contract quote on live books, 20:23 UTC**
(`rewards/scan_20261125/reward_q10_2015.json`), theoretical, own-pool reading:

| Market | Target | Book passes max-spread test? | 10 at the best bid | 10 one tick behind |
|---|---|---|---|---|
| UTR Feragu–Tistom | 500 | yes | $0.60/h | $0.19/h |
| UTR Measof–Markat (0.84, outside band) | 500 | yes | $0.40/h | $0.08/h |
| ATP doubles Arias–Nijboer | 4,000 | no max spread | $0.37/h | $0.11/h |
| ATP Johnson–Peddia | 19,000 | no max spread | $0.32/h | $0.10/h |
| other ATP/WTA | 19,000 | no max spread | $0.01–0.03/h | ~$0 (outside the walk) |
| other UTR (5 markets) | 500 | **no — nobody paid** | $0 | $0 |
| Setka | 4,800 | no max spread | $0.01–0.02/h | ~$0 |

- Best four in band, 10 a side at the best bid: about **$1.3/h** if each
  market has its own pool; less if pools are shared. One tick behind:
  about **$0.4/h**. Rewards at this size therefore depend on resting AT
  the best bid, which the controller does only when the spread is 2+
  ticks and calm (`PMUS_RN1_JOIN_TOUCH`).
- Five of seven live UTR books fail the max-spread test right now: no one
  is paid in them, whatever we quote.

## Authorization lines (each separate)

A — next inventory campaign on existing limits:
> Authorized: campaign A on 6755062ad — size down to fit and selective
> cancellation on, ladder 3 levels 2 ticks apart, 2 contracts, 4 in-play
> markets, UTR, Setka, ATP, WTA, all limits unchanged, ____ to ____ UTC.

B — reward-qualified test (changes limits):
> Authorized: campaign B on 6755062ad — 10 contracts a side, 1 level, 4
> in-play markets; per-market unpaired $8, per-market spend $20, campaign
> unpaired $20, loss stop $25, daily halt $25, account limit $70, at-risk
> $60, lane cap $70 (crypto stays disarmed), spend $200; ____ to ____ UTC.
