# PREREGISTRATION -- BTC5m directional study anchored to the chained strike

FROZEN ANALYZER: `scripts/gemini_btc5m_chained_study.py`
SELF-SHA256: `b2f6d50a035b56c0edc4efea49d4ade8fc5eec292e2dada2e58e405a142f9bc6`

Written and fingerprinted BEFORE any profitability number from the
prospective recorder was inspected. The script prints its own sha256 on
every run; if it differs from the line above, THE PREREGISTRATION IS VOID
and the run must be discarded rather than reported.

This does NOT replace or alter `gemini_early_window_analysis.py`
(fingerprint f2800e3e...960e90b), which stays frozen and runs first on the
prospective tape. Cohort 1's 1-of-5 UP outcome changes nothing in either.

## The causal state

The venue sets each BTC5m strike to the PREVIOUS window's settlementValue.
Confirmed to the cent twice independently: four consecutive windows in the
cohort-1 settlement audit, and four of four in the recorder tape's own
`window` rows against the prior window's settlement. The analyzer re-checks
it per window and reports the result.

This is a SETTLEMENT-MECHANICS FINDING, not a trading signal. Its only role
is to say what the strike IS, which is why the state is anchored to it:

    D_t = published LRRR_XBT(t) - the current window's authoritative strike

The strike comes from the contract field (`strike_type: reference`) and is
NEVER inferred. The reference is the last value published AT OR BEFORE t;
one older than 10s is STALE and the observation is DROPPED rather than
differenced. D_t IS NOT Z -- the LRRR statistic's mechanics remain NOT
IDENTIFIED, so no sigma and no time scale are applied.

## The question

NOT which side wins more often. Whether any causal state produces a
settlement probability far enough from the CONTEMPORANEOUS EXECUTABLE PRICE
to clear the venue's actual fees. UP and DOWN are evaluated symmetrically:
UP from the YES ask ladder, DOWN from the derived NO ask ladder (1 - YES
bid), which is what buying NO really consumes on this single-ladder venue.

## Commitments

1. CHECKPOINTS, fixed: T+15/30/45/60/90/120/150/180, anchored to the
   window's own availableAt.
2. STATE, at every checkpoint: D_t in USD and bps of strike, its sign, the
   causal trajectory available up to t (first value, step, momentum, sign
   flips so far, count of prior observations), the contemporaneous UP and
   DOWN executable VWAPs at each predeclared size, combined cost, a spread
   proxy, depth, book integrity, quote age, and the leader state -- the
   leader being whichever side is DEARER on the executable ladder, never a
   UI probability or a last trade.
3. SIZES, fixed: 1, 5, 10, 25, 50, 100. VWAP is CUMULATIVE to fill the
   size; an unfillable side is unfillable, never a touch price.
4. BINS, fixed constants: D_t in bps at
   -100/-50/-25/-10/-5/-2/0/2/5/10/25/50/100 and executable price in
   tenths. There is no search, no argmax over cutoffs and no best-cell
   output.
5. FEES: the published taker rate 0.07*q*p*(1-p), quantized to the cent as
   the venue applied it, RECONCILED against cohort 1's own charged fees
   (0.0155 / 0.0168 / 0.0161 / 0.0135 predicted, $0.02 charged each).
   Breakeven for a buy at p is p + 0.07*p*(1-p).
6. CANDIDATE RULES: 32 PREDECLARED combinations (4 checkpoints x 2 sides x
   4 signed D thresholds), each at the 0.90 cap, q=1, requiring an
   economically two-sided book and OK integrity. The SELECTION RULE is also
   predeclared: among candidates whose DISCOVERY ev_per_all_windows exceeds
   $0.01, carry the highest opportunity_rate to OOS -- exactly one, tested
   once. Ties break toward the earlier checkpoint, then toward UP.
7. REPORTING: opportunity frequency, EV per trade, EV per ALL windows,
   maximum drawdown over the window sequence, and the executable capacity
   curve. NO_TRADE windows stay in every per-window denominator.
8. OOS: the last 40% of settled windows are SEALED and unreadable without
   `--unseal-oos`, which exists only to test one already-frozen candidate.
9. NO TUNING ON COHORT 1. Its five windows are excluded by ticker:
   BTC05M2610051550/1555/1600/1605/1610.
10. NO LEAKAGE: a causal feature dict may not even NAME an outcome, and the
    analyzer raises on any key containing won/resolution/settlement/
    settled/eventual/winner/disappear/future/payout.
11. INCORPORATION LAG, descriptive and separate: at each checkpoint, the
    correlation between causal D_t (level, and step into t) and the LATER
    change in the UP executable VWAP. The predictor is causal; the outcome
    is a PRICE, never a settlement. A correlation near zero means the price
    already embeds D_t and there is NO informational lead to trade,
    whatever the settlement tables say. A positive one means the price
    moves after the reference does, which is the only version of this that
    could pay.
12. NO NEW LIVE COHORT until a candidate is frozen, clears OOS, and is
    worth executing. Cohort 1 is closed at -$2.45 realized net.

## Order of operations

1. The prospective recorder keeps running, untouched.
2. When its tape lands: run the ALREADY-FROZEN
   `gemini_early_window_analysis.py` first, unchanged, no `--unseal-oos`.
3. Then run THIS analyzer, unchanged, on the same tape, discovery
   partition only.
4. Only if the predeclared selection rule names a candidate does the OOS
   partition get opened, once.
