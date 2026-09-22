# EP016 — COHORT-1: ten authorized windows, six observed, stopped by a cap that measured the wrong thing

**Authorization:** `OWNER-2026-09-21-GEMINI-BTC15M-Q1-CAP4-COHORT-1` — SPENT, DISARMED.
**Spec:** `btc15m_q1_cap4_w60_880`, `spec_sha 223b81ed…`, quote interval TTE [60, 880].
**Envelope:** `GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_CAP4_V1`, `3aed2098…` — unchanged throughout.

Two dispatches under one grant:

| run | step ran | outcome |
|---|---|---|
| [35671834127](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35671834127) | 00:28:31–00:54:54Z | 2 windows, then `STOPPED_MUTATION_BUDGET` on a defect; **all artifacts lost** |
| [35677907781](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35677907781) | 02:05:08–03:02:11Z | 4 windows, then `STOPPED_AGGREGATE_LOSS`; artifacts preserved |

## The scoreboard

```
windows observed   6 of 10        windows traded   6
mutation slots     31 of 120      loss measure     10.63 of 10.00 cap
stop               STOPPED_AGGREGATE_LOSS
orders live        0 after every window          rollover gate CLEAR every time
unreadable holds   0 across all four recorded windows
```

## The four recorded windows

Every one traded. All 18 fills were **maker**, every one with `fee: 0.0000`.

| # | contract | entry tte | cycles | slots | fills | spend | stop |
|---|---|---:|---:|---:|---:|---:|---|
| 3 | …2609220230-UP | 865.8 | 55 | 8 | 7 | 2.52 | risk gate: a fill would take NO to 5, past 4 |
| 4 | …2609220245-UP | 867.8 | 14 | 12 | 2 | 0.71 | no mutation slots left to cancel with |
| 5 | …2609220300-UP | 870.5 | 43 | 7 | 6 | 2.40 | risk gate: a fill would take YES to 5, past 4 |
| 6 | …2609220315-UP | 879.2 | 9 | 12 | 3 | 1.23 | no mutation slots left to cancel with |

Fills, in order, all maker at zero fee:

```
…0230  NO 0.45 · NO 0.40 · YES 0.45 · YES 0.37 · YES 0.33 · NO 0.33 · NO 0.19
       -> yes 3, no 4, matched 3, residual -1
…0245  YES 0.37 · YES 0.34                      -> yes 2, no 0, matched 0, residual 2
…0300  YES 0.42 · YES 0.43 · NO 0.54 · NO 0.40 · YES 0.36 · YES 0.25
       -> yes 4, no 2, matched 2, residual 2
…0315  NO 0.52 · YES 0.39 · YES 0.32            -> yes 2, no 1, matched 1, residual 1
```

**Two windows hit the CAP-4 inventory bound** (`a fill would take NO/YES to 5, past 4`) — the first time this programme has reached the widened cap on either leg. Under CAP-2 both would have stopped earlier.

## Settlement — and the finding

All four contracts settled.

| contract | resolutionSide | yes | no | spend | payout | net |
|---|---|---:|---:|---:|---:|---:|
| …2609220230-UP | yes | 3 | 4 | 2.52 | 3.00 | **+0.48** |
| …2609220245-UP | yes | 2 | 0 | 0.71 | 2.00 | **+1.29** |
| …2609220300-UP | no | 4 | 2 | 2.40 | 2.00 | −0.40 |
| …2609220315-UP | no | 2 | 1 | 1.23 | 1.00 | −0.23 |
| **total** | | | | **6.86** | **8.00** | **+1.14** |

**The stop fired on a number that is not the loss.** `window_loss_usd` returns cash
OUT of the account from confirmed fills, with no settlement credit, no
matched-pair offset and no rebate assumed. I built it that way deliberately and
called it conservative. For a strategy that buys both legs and is paid on one,
that measure accumulates roughly half of turnover as "loss" whether the cohort
is making money or losing it. It is a **turnover cap wearing a loss cap's
name**, and it halted an authorized cohort at 4 of its 8 remaining windows
while the recorded windows were net positive.

Two things this is NOT:

- it is **not REALIZED**. +1.14 is settlement-implied from venue-confirmed fills
  and public resolution; no attributable cash movement has been read back.
- it says **nothing** about expectancy. Four windows is four windows.

## What is missing, and stays missing

The first dispatch's two windows contributed **3.77** to the cap and have no
settlement record: their symbols died with the runner. Their twelve slots are
tombstoned in W01 as `TOMBSTONE_UNRECOVERABLE` and reconstruct no trade. So the
settlement-inclusive result above covers 4 of the 6 observed windows, and the
other 2 cannot be completed.

## The second defect: a window did not get twelve slots

`ledger_for` returns the first UNSPENT ledger. Window 3 spent 8 of W02, so
window 4 inherited W02's remaining 4 and stopped at `no mutation slots left to
cancel with` after 14 cycles; the same happened to window 6 on W03 with 5.
Cleanup still ran on its separate safety budget — `orders_live_after_stop: 0`
on all four — but the grant's "12 actions per window" was not honoured for two
of the six.

## Orchestration fixes proved in production

- **`always()` evidence commit**: run 35676429611 FAILED and still committed its
  artifact, which is the only reason its verdict could be read.
- **Authorized stops exit 0**: this run's `STOPPED_AGGREGATE_LOSS` produced a
  green job and a complete artifact set — the first dispatch's identical class
  of stop produced a red job and nothing at all.
- **Latch continuation**: the resume spent no second latch; window records carry
  `held_by_cohort` against the original.
- **Chain wiring**: 4 windows drew on W02 and W03 rather than all on W01.

## Two decisions for the owner, neither taken here

1. Should the aggregate cap measure **settlement-inclusive net** rather than
   gross cash outflow? As built it stops a profitable cohort.
2. Should a window get a **full 12 slots** — one ledger per window, remainder
   discarded — rather than inheriting a partial one?

No strategy parameter was changed at any point: q=1, CAP-4, $4.00 per market,
2 working orders, one rung per outcome, maker/post-only, the 30s floor, the
frozen controller and TTE [60, 880] are exactly as authorized.
