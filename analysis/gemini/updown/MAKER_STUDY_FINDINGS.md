# BTC5m inside-spread maker study -- RESEARCH ONLY, PLUMBING PROOF ONLY

`scripts/gemini_btc5m_maker_study.py`. No live maker orders are authorized
or sent. It changes nothing: not the live UNDERDOG cohort, not the recorder,
not either frozen analyzer (f2800e3e...960e90b, b2f6d50a...142f9bc6), not
any authorization, and it opens no venue surface.

## THE NUMBERS BELOW ARE NOT A RESULT

The prospective early-window tape does not exist yet: the window census
returns **0 genuinely complete windows**, and recorder run 1's tape is still
on its runner. So this ran on the short validation tapes -- **23 settled
opportunities across 6 markets with 19 trade prints in total**. That is a
proof the harness runs end-to-end on real tape, and nothing more. It will be
re-run unchanged when the snapshot tape lands.

## Design, as asked

**Side selection is separate from execution method.** `--side` fixes the
DIRECTION (underdog / up / down) and is held constant while only the
execution method varies, so the comparison answers the maker-vs-taker
question at a constant directional decision.

**Placements** are 25%, 50% and 75% through the observed spread from OUR
passive side, snapped AWAY from aggression (a buy floors, a sell ceilings)
so a placement is never better than its fraction implies. Buying UP rests on
the bid; buying DOWN is a YES sell resting on the ask, priced at 1 - that
level.

**NO TOUCH = FILL.** Two tiers, never pooled:
* `TRADE_THROUGH` -- a later print at or through our price, **with the
  aggressor on the side that could have hit us** (`buyer_is_maker` decides;
  a print whose side is unknown is not evidence), and cumulative qualifying
  size exceeding the queue ahead of us plus our own. The only tier counted.
* `BOOK_EVOLUTION` -- the passive side later moved through our price but no
  qualifying print was seen. Reported separately, never counted as a fill.
  10 such cases here.

**Conservative queue**: we arrive BEHIND every contract displayed at a
better-or-equal price on our side, and our own size must trade too. No
partial fills, no invented queue position.

**Fees separate, rebates zero**: maker 0.0175*q*p*(1-p), taker
0.07*q*p*(1-p), both cent-rounded up. **No rebate or liquidity reward is
assumed, modelled or credited anywhere** -- none has been credited to this
account and source-accounted, so none appears.

## What the proof run shows

| method | n | fills | fill rate | settle win | EV/fill | EV/opportunity | fees |
|---|---|---|---|---|---|---|---|
| MAKER p25 | 23 | 7 | 30.4% | **0.0%** | -0.2629 | -0.0800 | 0.07 |
| MAKER p50 | 23 | 7 | 30.4% | **0.0%** | -0.3114 | -0.0948 | 0.07 |
| MAKER p75 | 23 | 7 | 30.4% | **0.0%** | -0.3600 | -0.1096 | 0.07 |
| TAKER immediate | 23 | 23 | 100% (by construction) | 43.5% | +0.0065 | **+0.0065** | 0.44 |

Time-to-fill median 82-106s; adverse move after fill median -0.18 to -0.26
and worst -0.33.

**The pattern, stated as a hypothesis the real tape must test rather than as
a finding: every maker fill in this sample lost.** 0 of 7 settled in our
favour against 43.5% for the taker on the same directional decisions. The
mechanism is visible in the adverse-move column -- the resting order fills
precisely when the market runs through it, so the fills are selected for
being wrong, and more aggressive placement (p75) fills at a worse price with
a worse post-fill excursion without filling any more often. That is textbook
adverse selection, and the maker fee being an order of magnitude smaller
than the taker fee ($0.07 vs $0.44 in total) does not come close to paying
for it.

**I am not reporting that as the answer.** 7 fills off 19 prints is not a
sample, the fill rate is bounded by how many prints the tape happens to
carry, and five of the 28 opportunities had no settlement at all.

## Two of my own errors, fixed before any number was reported

1. **The aggressor side was ignored.** The first version counted any print
   through our price as evidence in BOTH directions, which credits a resting
   bid with fills that only a resting ask could have taken. `buyer_is_maker`
   now decides, and an undetermined side is not evidence.
2. **Unsettled windows were counted as unfilled orders.** That showed TAKER
   -- whose fill is immediate by construction -- with an "82% fill rate".
   Windows with no settlement are now excluded from BOTH denominators and
   reported separately (5 of 28), and the taker rate is labelled as 100% by
   construction rather than measured.
