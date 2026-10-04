# PORTFOLIO_MATCH_V1 — preregistration

Committed **before** the controller was written or run. Next member of the
**BUY BOTH SIDES → EXTEND → SWITCH** family. 28 untouched windows, $1
normalization, canonical fees. No live orders, no scaling, no $200 envelope.

## The single licensed change

Frozen CF_MATCH produced **+$62.66 of matched economics** at a **median
pair cost of $0.8694** with **83.0% of matched shares below $1.00 after
fees**, and failed only because fixed-dollar opposite-side purchases built a
**571.7-share peak residual** and **−$86.18 of residual economics**.

The licensed change is therefore **share-aware portfolio sizing** — nothing
else. Frozen and unchanged: the movement/reversal signal (Cell C's 279
reversal timestamps), the 387 purchase timestamps, the price source
(authenticated tape, executable touch, depth-capped), taker execution, and
`src/pmus_fees.py`.

## Controller state, evaluated before every action

YES shares · NO shares · matched = min(YES, NO) · residual side · residual
shares = |YES − NO| · average YES cost · average NO cost · effective
matched-pair cost · current favorite · current frozen directional state ·
seconds remaining.

## The two actions

At each frozen purchase timestamp the frozen state machine names an outcome
**O** (it starts as the favorite and toggles at each frozen reversal).

**EXTEND** — when residual is zero, or the residual side **is** O, i.e. the
frozen logic calls for continued exposure on the side we already hold.
Sizing is the **frozen extension sizing, unchanged**: $1 notional at the
contemporaneous executable price, depth-capped.

**MATCH** — when the residual side is **not** O, i.e. the frozen reversal
has switched us to the opposite outcome. The fixed-dollar purchase is
replaced by a **share-sized** one:

```
shares = min( $1 / executable price ,  current residual shares )
```

**A MATCH may take residual to zero and may never create residual on the
opposite side.** If the cap binds, only the capped quantity is bought; the
remainder is not carried, deferred or made up later.

**Why the cap and not a full match.** "Buy the whole residual" was
considered and rejected: with a 50-share residual at $0.40 it would be a $20
purchase against a $1 frozen unit, a 20× size increase that this evidence
does not license. Capping the frozen size removes the overshoot and changes
nothing else, which is the minimal change consistent with the finding.

**No silent combination.** Once residual reaches zero, further opposite-side
exposure requires a *subsequent, independently valid* EXTEND at a later
timestamp. MATCH and EXTEND are never merged into one oversized purchase,
and at most one action occurs per frozen timestamp.

## Seed — frozen now, reported separately

Primary arm **PORTFOLIO_MATCH_V1** is **seedless**, so its action stream is
directly comparable to Cell C and CF_MATCH.

Secondary arm **PORTFOLIO_MATCH_V1_SEED** uses the already-documented manual
architecture's sizing: **$5 UP + $5 DOWN at the first mark**, the manual
experiment's observed 5:1 seed-to-extension ratio at this lane's $1 unit,
exactly as V1 used it. That number comes from the manual screenshot's
$0.50/$0.10 ratio, not from any outcome.

## Explicitly not in V1

No pair-cost threshold — the point is whether CF_MATCH's *naturally
observed* pair economics survive correct sizing, not to keep only the pairs
that happened to pay. No maker execution. **No SELL.** No optimization of
residual caps, size multipliers, thresholds, cadence or trail width.

## Accounting

Five terms, reconciling exactly to the cash identity
`net = exit proceeds + settlement − cash out` (with no exits, exit proceeds
are zero):

matched-pair economics · directional residual P&L · spread/execution
economics where separately identifiable · fees · settlement cashflow.

The identity residual is printed per window and must be 0 to six decimals or
the run is void. UP and DOWN shares, cash and average costs stay separate; a
DOWN purchase costs `1 − bid` and consumes UP bid depth and is **never**
treated as selling UP.

## Reported

mean and median net per window; gross; fees; positive fraction; LB1; LB3;
worst window; peak capital; peak residual in **shares and dollars**; matched
shares; pair-cost distribution before and after fees; % matched below $1.00
after fees; EXTEND count and quantity; MATCH count and quantity; number of
times a MATCH reaches flat; turnover; gross/fees; net per dollar of peak
capital — against **Cell C, CF_MATCH, V1 and D0**.

## The critical attribution, with its arithmetic fixed in advance

Report, as fractions:

- **matched economics retained** = V1 matched ÷ **+$62.66**
- **residual loss eliminated** = 1 − (V1 residual ÷ **−$86.18**)

Stated before seeing results so it cannot be renarrated: capping purchases
necessarily buys fewer shares, so matched economics is **expected to fall**
and fees and turnover to fall with it. The experiment succeeds only if the
residual loss falls by **materially more** than the matched economics does.
If both fall in similar proportion, the sizing fix is neutral and the
architecture needs something other than sizing.

## Gate

Positive **mean AND median AND leave-best-1 AND leave-best-3**. Anything
less is reported as a failure of the gate; no scaling study follows.
