# The execution mechanism: we cancel at 8 seconds

Read-only, from Campaign 7's own submit, terminal and fill tape.

## Queue depth does NOT explain our fill rate

Earlier this was reported as "we rest 600x-1,300x deeper than our own size,
and that is the binding constraint". **The data refute it.**

    queue-ahead / quantity-needed at placement
      FILLED     n=18  median   987x   min 367   max 2843
      CANCELLED  n=18  median  1013x   min 291   max 2010

Cancelled quotes sat in SHALLOWER queues than filled ones. The deepest quote
of the campaign (2843x, 2000z T+309) FILLED. Queue depth has no explanatory
power here, and the earlier claim is withdrawn.

## What does explain it: dwell time

    time a quote spent resting
      FILLED     n=18  min 4  median 11  max 107 s
      CANCELLED  n=18  min 4  median  8  max 126 s

A quote that fills does so in a median of **11 s**. A quote that has not
filled in about **8 s is pulled**. The cancel threshold sits INSIDE the
fill-time distribution -- we withdraw quotes at roughly the moment fills
arrive.

    total quote-seconds resting, whole campaign      622 s
    eligible entry time (6 traded windows x 600 s)  3600 s
    fraction of eligible time with ANY quote resting  17%

For 83% of the time we could have been quoting, nothing of ours was on the
book.

## The four strandings, per order

Each line is one submission: time from window open, side, own price, the
queue ahead of us, and what became of it.

**2000z** (completion side UP, terminal T+600)

    T+38   DOWN 0.24  q2496  FILLED
    T+72   UP   0.76  q2160  FILLED      <- pair closed at 1.00
    T+273  DOWN 0.22  q3477  CANCELLED
    T+309  DOWN 0.33  q5260  FILLED      <- opens the stranded leg
    T+375  UP   0.42  q1805  CANCELLED   <- pair 0.71, profitable
    T+409  UP   0.32  q1867  CANCELLED   <- pair 0.61, profitable

**2015z** (completion DOWN) -- ONE completion attempt, then nothing for the
remaining 445 s of the window:

    T+87   UP   0.37  q799   FILLED
    T+155  DOWN 0.55  q2518  CANCELLED   <- pair 0.92, clears the ceiling

**2100z** (completion DOWN)

    T+12   UP   0.40  q1236  FILLED
    T+44   DOWN 0.54  q2543  FILLED      <- pair closed at 0.94
    T+79   DOWN 0.45  q1801  CANCELLED
    T+146  UP   0.45  q1964  FILLED      <- opens the stranded leg
    T+280  DOWN 0.26  q1283  CANCELLED   <- pair 0.68, profitable
    T+313  DOWN 0.34  q987   CANCELLED   <- pair 0.76, profitable

**2115z** (completion DOWN) -- eight completion attempts:

    T+53   UP   0.46  q1024  FILLED
    T+117  DOWN 0.33  q1549  CANCELLED   <- pair 0.79
    T+149  DOWN 0.33  q642   CANCELLED   <- same price, BEST queue of the window
    T+185  DOWN 0.47  q2179  CANCELLED   <- worse price AND 3.4x worse queue
    T+219  DOWN 0.46  q2884  CANCELLED
    T+353  DOWN 0.26  q2084  CANCELLED
    T+388  DOWN 0.27  q596   CANCELLED   <- second-best queue, abandoned
    T+419  DOWN 0.31  q2574  CANCELLED
    T+452  DOWN 0.43  q2769  FILLED      <- pair 0.89, at the WORST queue
    T+486  UP   0.62  q3049  FILLED      <- opens the stranded leg, T+486
    T+554  DOWN 0.26  q2425  CANCELLED   <- 46 s before the terminal

## Did 2115z's repricing discard useful priority?

It discarded priority, but priority was not what it needed. The order that
eventually filled did so from the WORST queue position of the window
(2,769 ahead). Fills here come from the price reaching us, not from queue
advancement.

The repricing was still harmful, on price rather than priority: at T+149 it
held own 0.33 (pair **0.79**) and requoted to 0.47 (pair 0.93), finally
filling at 0.43 (pair **0.89**). Holding the 0.33 quote through the move
that later took DOWN to 0.26-0.31 would have completed ten cents cheaper on
2.05 contracts, about **+$0.205** on that pair alone.

## Late entries

2115z's third position opened at **T+486** with the completion terminal at
T+600 -- 114 s, against a requote cycle of ~33 s and a median fill time of
11 s. Its single completion attempt went out at T+554. But late entry is
not the general cause: the other three strandings opened at T+309, T+87 and
T+146 and had 291 s, 513 s and 454 s of runway.

## One change, and why it is the right one

**Hold an economically valid completion quote instead of pulling it after
~8 s.** Specifically: do not cancel a resting COMPLETION whose pair still
clears the ceiling, unless the quote has become unpriceable or the deadline
demands it.

- It targets completion PROBABILITY, which is the stated goal.
- It cannot increase residual exposure: a completion only ever reduces
  |residual|. Dwell on OPENING quotes is untouched.
- It REDUCES submissions, easing the backstop that bound at 36/40.
- Measured support: cancels occur at a median 8 s while fills arrive at a
  median 11 s, and 83% of eligible time has nothing resting.

NOT PROPOSED FOR DEPLOYMENT HERE. The fill-time distribution is 18 and 18
observations, the two medians are 3 s apart, and a longer dwell is not free:
a stale quote can be picked off when the book moves, which is the adverse
selection already measured at 78-92% in shadow. It needs a preregistration
with the dwell threshold fixed in advance, and Campaign 8 records the
baseline (quote dwell, fill time, and the 17% resting fraction) to test
against.
