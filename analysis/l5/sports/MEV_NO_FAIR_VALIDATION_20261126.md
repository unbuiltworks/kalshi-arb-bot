# No-fair standing-start rule: validation against production data

26 Nov 2026, read-only. Nothing was deployed, changed or restarted.
Rule under test: `PMUS_RN1_MARGINAL_EV_NO_FAIR` standing start
(`dev/pmus-mev-no-fair-fixes` b61528379, merged with the complete final-gate
backstop as `dev/pmus-mev-no-fair-merged` 909edef5d). The rule lets a market
with no international fair value get its first ladder when a programme is
paying and the THEORETICAL projection of the ladder that would rest
(`ladder_quote_estimate` x `RN1_REWARD_PROJECT_H` = 3 h, no haircut) reaches
`RN1_REWARD_MIN_DAY_USD` = $1, i.e. about **$0.33/h**. Each bid is then
credited 0.5 x its own marginal reward against
`fills/h x qty x (fill_loss + fee + concentration risk)`.

Labels: **THEORETICAL** = the bot's estimate under the published scoring rule;
**MEASURED** = computed from venue-confirmed records (markouts, reward rows);
**PENDING** / **PAID** = venue reward-row status. Nothing below is realized
profit except where a row says PAID or a market is settled.

## Sources

Sports service production logs (`PMUS_TWO_SIDED`):

| Kind | Window held | Use |
|---|---|---|
| `boot` (26 Nov 21:00, 21:06, 22:20) | | production settings: qty 2, one tick behind, fill rate 3.0 / 1.5, fill loss 0.05 / 0.035 (atpdb, wtadb 0.08 / 0.06), maker fee 0, `allow_sports` atp, atpdb, setkamemd, setkameua, utr, wta, wtadb |
| `reward_program` (142) | 23 Nov 16:46 – 26 Nov 22:21 | pool, period, targetSize per competition |
| `RN1_NET_PLACEMENT` (124) | 25 Nov 23:45 – 26 Nov 23:14 (first record ever 23:45) | THEORETICAL reward/h, share and fill cost per market, both placements |
| `RN1_SHARE_RANK` (≈70) | 25 Nov 13:36 – 26 Nov 23:16 | THEORETICAL reward/h of our own quote for the top candidates (qty 1 before 25 Nov 16:32, qty 2 after) |
| `RN1_QUOTE` (2,000) | 25 Nov 14:07 – 26 Nov 23:12 | planned ladder and block per market; time with an unblocked quote |
| `REWARD_CHECK` (232 + latest) | 17 – 26 Nov | venue `/v1/incentives/earnings` rows (only the 50 newest rows per record are logged: 96 of 197 rows recovered) |
| `FILL_MARKOUT` | via `FILL_LOSS_AND_REWARD_RECON_20261126.md` (77 fills, 22–26 Nov) | MEASURED fill loss |

`REWARD_ACCRUAL` (theoretical accrual of resting orders) is not logged in the
current mode, so resting time is approximated by the time a market's
`RN1_QUOTE` state had an unblocked want (upper bound: the reactive pull and
the 5 s book lag make actual scored resting time shorter).

Programmes in force (`reward_program`, per market):

| Competition | Period | Pool | Pool/h | targetSize | maxSpread |
|---|---|---|---|---|---|
| setkameua, setkamemd | day_of / live | $5 / $17 | $0.83 / $5.67 | 3,200 / 4,800 | – |
| atpdb | day_of / live | $10 / $70 | $1.67 / $23.33 | 6,000 / 4,000 | – |
| utr | day_of / live | $10 / $50 | $1.67 / $16.67 | 250 / 500 | 0.07 / 0.06 |
| atp, wta (fair value, not in scope) | day_of / live | $40 / $184 | $6.67 / $61.33 | 19,000 | – |

To clear $0.33/h a ladder needs about 40% of a Setka day_of pool, 6% of a
Setka live pool, 1.4% of an ATP doubles live pool, 2% of a UTR live pool.

## 1. How often the rule would have fired (THEORETICAL)

Threshold: two-sided theoretical reward of the ladder ≥ $0.333/h.

**`RN1_NET_PLACEMENT`, 25 Nov 23:45 – 26 Nov 23:14**, each record weighted by
its time to the next record of the same market (cap 1 h). Qty-1 records are
scaled to qty 2 with the record's own share (pool x 2/(X+2)). "Ladder upper"
doubles the level-0 figure for the deeper levels (the true value is between
the two; deeper levels are discounted by the venue's distance factor).

| Competition | Markets priced | Hours | Fires (level 0, qty 2) | Fires (ladder upper) | Median two-sided reward/h |
|---|---|---|---|---|---|
| atpdb | 11 | 7.9 | 2 markets, 2.8 h (35%) | 2 markets, 2.9 h | $0.31 |
| setkameua | 12 | 6.0 | 5 markets, 1.3 h (22%) | 7 markets, 3.5 h (58%) | $0.19 |
| setkamemd | 5 | 1.7 | 0 | 0 | $0.017 |
| utr | 3 | 2.1 | 0 | 0 | $0.095 |
| wtadb, itf* | 0 | – | – | – | no records |

**`RN1_SHARE_RANK`, 25 Nov 13:36 – 26 Nov 23:16** (top candidates only, out of
59–89 programme markets per round). No-fair markets at or above $0.33/h:

| Market | When (UTC) | Reward/h (THEORETICAL) | Phase |
|---|---|---|---|
| atpdb-alvvalflores-alvesperez | 26 Nov 21:01 | 9.43 | live |
| setkamemd-oanmih-spiole | 25 Nov 13:43 | 1.49 (qty 1) | day_of |
| utr-ciugab-mentad | 25 Nov 17:44 | 1.28 | – |
| atpdb-delanedelane-falcksuarez | 25 Nov 23:49 / 26 Nov 00:28 | 0.97 / 0.74 | pregame |
| setkameua-slomyk-fasiva | 26 Nov 00:40 | 0.62 | pregame |
| setkameua-kovvea-misole | 25 Nov 23:49 / 26 Nov 00:28 | 0.57 / 0.33 | pregame |
| setkameua-kimdmy-elfiva | 25 Nov 23:48 | 0.54 | pregame |
| setkamemd-iuriur-iusevg | 25 Nov 18:46 | 0.45 | – |
| setkameua-hnorom-abaspa | 26 Nov 23:16 | 0.44 | pregame |
| setkameua-tebyar-paryur | 26 Nov 00:41 | 0.34 | pregame |

Borderline below: utr-viebru-jarnic 0.31 (qty 1, likely above at qty 2),
setkameua-perand-elfiva 0.28, -pruser-mosvas 0.24, setkamemd-ivaana-gridmi
0.23 (qty 1). Every other ranked no-fair market was ≤ $0.15/h, most ≤ $0.01.

**Reading:** about **10 no-fair markets in ~34 h of slate** (roughly 1 in 7
of the programme markets that were priced) would have passed the standing
start. They are almost all thin books before the start (Setka day_of, ATP
doubles pregame) plus live ATP doubles. UTR passed once; WTA doubles and ITF
never appeared (ITF is not in `allow_sports`, so the rule cannot fire there in
the current configuration).

**The per-bid test is the binding constraint, not the threshold.** At
production settings a qty-2 one-behind bid costs 1.5 x 2 x 0.035 = $0.105/h
(atpdb 1.5 x 2 x 0.06 = $0.18/h). With a 0.5 haircut and roughly half the
two-sided reward per side, a bid passes only if the two-sided reward is
≥ **$0.42/h** (Setka, UTR) or ≥ **$0.72/h** (atpdb), i.e. a 3 h projection of
$1.26 or $2.16, above the $1 threshold. So the threshold only unlocks markets
where the per-bid test then mostly decides; on the list above
tebyar-paryur (0.34) and the late kovvea-misole reading (0.33) would be
unlocked and then refused per bid, and delanedelane is the only pregame atpdb
case clearing $0.72.

## 2. Theoretical vs venue reward (calibrating the 0.5 haircut)

Venue rows dated 25 Nov (all **PENDING**, total $0.86, not credited) against
THEORETICAL reward/h x quoted hours (unblocked `RN1_QUOTE` time, 25 Nov
05:00 – 26 Nov 05:00 UTC, which is 25 Nov ET):

| Market | Quoted h | Reward/h (THEORETICAL, source) | THEORETICAL $ | Venue row (PENDING) | Ratio |
|---|---|---|---|---|---|
| atpdb-delanedelane-falcksuarez | 1.46 | ~0.85 (0.97 / 0.74, qty 2) | 1.24 | 0.59 | 0.48 |
| setkameua-slomyk-fasiva | 0.50 | 0.62 | 0.31 | 0.10 | 0.32 |
| setkameua-tebyar-paryur | 0.11 | 0.34 | 0.038 | 0.01 | 0.26 |
| setkameua-kimdmy-elfiva | 0.12 | 0.54 | 0.065 | none | ~0 |
| setkameua-kovvea-misole | 0.03 | 0.57 / 0.33 | 0.013 | none | ~0 |
| utr-kawgab-duadan | 2.78 | 0.032 | 0.089 | 0.01 | 0.11 |
| utr-feragu-tistom | 1.37 | 0.022 | 0.030 | 0.05 | 1.7 |
| utr-boymel-gobvic | 0.92 | ~0.016 (0.008 at qty 1) | 0.015 | 0.02 | 1.3 |
| atp-jaskub-carove (fair value, for reference) | 0.01 | 7.43 | 0.074 | 0.01 | 0.13 |
| **Total (rows with an estimate)** | | | **1.87** | **0.79** | **0.42** |

utr-measof-markat (0.91 h, $0.04) and setkameua-shyole-tymole ($0.01) had no
estimate logged.

- **MEASURED ratio actual/theoretical ≈ 0.42 overall; 0.26–0.48 on the Setka
  and ATP doubles markets the rule is about**, driven by one market
  (delanedelane, $0.59 of the $0.79). The UTR estimates are taken hours away
  from the quoting time and are not usable.
- The quoted-hours proxy overstates scored resting time, so the true ratio
  per scored hour is somewhat higher than 0.42; the 0.5 haircut is **at the
  optimistic edge but not refuted**.
- None of it is money yet: every 25 Nov row is PENDING.
- The $1 minimum payout: on the rows recovered, 17 Nov has six PAID rows
  totalling $0.40 (plus four SKIPPED, $0.48, mostly esports) and single PAID
  rows of $0.01 exist on 17 and 18 Nov. A per-account per-day $1 floor does
  not explain which rows were paid. Whether the 25 Nov total ($0.86) is PAID
  or SKIPPED will be the first direct test for these competitions.

## 3. Fill loss: measured vs setting (MEASURED, openings + extensions, 300 s)

From `FILL_LOSS_AND_REWARD_RECON_20261126.md` (no new fills since 21:40):

| Competition | n | Loss at 300 s | Setting behind / touch | Verdict |
|---|---|---|---|---|
| atpdb | 4 | −1.6c (no loss at any horizon) | 6.0c / 8.0c | insufficient; setting conservative |
| setkameua | 4 | 13.8c; one fill −44.5c, the other three 3.6c | 3.5c / 5.0c | insufficient; one tail fill |
| utr | 17 | 0.7c [−2.2, 3.7] | 3.5c / 5.0c | looks too high |
| setkamemd, wtadb, itf* | 0 | – | 3.5c / 5.0c (wtadb 6 / 8) | no data |

The pregame fill rate, where most standing starts happen, is not measured;
the in-play 1.5/h is used.

## 4. Would the per-bid totals have been positive? (THEORETICAL x calibration)

Per bid, qty 2, one tick behind, reward per bid ≈ half the two-sided figure:

| Case | Reward/bid THEORETICAL | x0.5 (setting) | x0.42 (MEASURED ratio) | Cost at setting | Cost at measured loss | Net: setting / calibrated reward + setting cost / calibrated + measured |
|---|---|---|---|---|---|---|
| atpdb pregame (delanedelane 0.97/h) | 0.485 | 0.243 | 0.204 | 0.180 | ~0 (use 0.105 floor) | **+0.06 / +0.02 / +0.10** |
| atpdb live (alvvalflores 9.43/h) | 4.7 | 2.36 | 1.98 | 0.180 | 0.105 | strongly positive under every reading |
| setkameua pregame (slomyk 0.62/h) | 0.31 | 0.156 | 0.130 | 0.105 | 0.414 (13.8c) / 0.108 (3.6c ex-tail) | **+0.05 / +0.03 / −0.28 (≈0 ex-tail)** |
| setkameua at the threshold (0.33/h) | 0.167 | 0.083 | 0.070 | 0.105 | | refused per bid (−0.02 / −0.04) |
| utr (ciugab 1.28/h, once) | 0.64 | 0.32 | 0.27 | 0.105 | 0.021 (0.7c) | **+0.21 / +0.16 / +0.25** |
| utr typical (0.095/h) | 0.048 | 0.024 | 0.020 | 0.105 | 0.021 | refused; ≈0 even at measured loss |

Concentration risk is extra cost in every row (not quantified here). For
reference, settled trading in the 25 Nov reward markets was about −$0.82 plus
+$0.05 locked pair edge, against $0.86 of PENDING reward (recon doc, Part 2):
**MEASURED net ≈ 0 before the reward is paid.**

## Conclusion

- **About right for ATP doubles; slightly too many for Setka; UTR almost
  never.** The rule unlocks about 10 markets in 34 h. Doubles cases are
  positive under the settings, the calibrated reward and the measured
  (thin) loss. Setka pregame starts net +$0.03 to +$0.05 per bid-hour under
  the settings, inside the error of the reward calibration, and negative if
  the one large measured Setka loss is representative.
- The standing-start threshold is looser than the per-bid test at current
  settings, so it does not by itself start losing bids. Its weakness is that
  the projection carries no haircut, while the venue paid about 0.42 of
  THEORETICAL on these markets.
- Nothing here is realized: 25 Nov rewards are PENDING, the pregame fill rate
  is unmeasured, and fill loss for these competitions rests on 4–17 fills.

## Recommended settings changes (proposals only; nothing changed)

1. **Apply the reward haircut to the standing-start projection too** (code
   change, or equivalently `RN1_REWARD_MIN_DAY_USD` 1 → 2 for the standing
   start only). Effect on the list above: keeps alvvalflores, delanedelane,
   ciugab-mentad and oanmih-spiole, drops the marginal Setka pregame starts
   (slomyk, kimdmy, kovvea, hnorom, tebyar, iuriur), which are also the starts with
   the worst measured fill loss.
2. **Haircut `PMUS_RN1_RANK_REWARD_HAIRCUT` 0.5 → 0.4** until the 25 Nov rows
   turn PAID. Return to 0.5 if they are paid in full. If they are SKIPPED,
   treat sub-$1 days as zero reward and raise the threshold rather than the
   haircut.
3. **Fill loss:** keep the atpdb/wtadb override (0.06 / 0.08) as caution; it
   is neither supported nor refuted by 4 fills. UTR behind 0.035 → 0.02 is
   supported by 17 fills but changes nothing in practice: UTR rewards are too
   small for the rule to fire. Add a setkameua override of 0.05 behind
   if the next 10 Setka openings confirm the tail; do not set it from one fill.
4. **Measure before scaling:** log `REWARD_ACCRUAL` (or the per-market
   resting estimate) in this mode so the next calibration uses scored
   resting time instead of quoted time, and record pregame fill rate per
   resting contract-hour for Setka and doubles.
5. ITF is outside `allow_sports`; adding it would be a separate decision with
   no markout or reward evidence behind it.
