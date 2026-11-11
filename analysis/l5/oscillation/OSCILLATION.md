# Why L5's inventory oscillates and ours strands

**Production unchanged.** `f7a3e224`, cap 6, 160 seats, watch 160, existing
band, maker-only, existing envelope. Read-only throughout.

**The short answer is that the premise is only half right, and the half that
is right is not where we have been looking.** Complement reachability is
*not* the binding constraint — the complement was reachable in 7 of 8
markets, at pair costs of 0.48–0.91, usually within seconds. Our
market-level oscillation already matches L5's. What differs is **fill
conversion at essentially the same price**, and the policy that makes us
wait for it.

---

## 1. What fraction of our first fills ever see a profitable complement?

**Seven of eight.** Five immediately, two later, one with too little
evidence to say. **Zero never-reachable.**

| market | class | pair @ opp ask | min observed pair | t→pair ≤0.97 | our maker px touched | any opposite fill | matched | realized |
|---|---|---|---|---|---|---|---|---|
| aec-mlb-lad-mia | **R1** | 1.000 | **0.48** | 121.6s | yes | yes | 0.750 | **+6.0034** |
| aec-nfl-buf-hou | **R1** | 0.815 | **0.515** | 0.0s | yes | **no** | **0.000** | **−2.5896** |
| aec-atp-jayfri-maxbas | **R1** | 1.000 | **0.64** | 32.2s | yes | **no** | **0.000** | unsettled |
| asc-lal-lev-fcb-pos-2pt5 | **R1** | 0.820 | 0.67 | 0.0s | yes | yes | 0.981 | unsettled |
| aec-mlb-sea-ath | **R1** | 0.925 | 0.90 | 0.0s | yes | yes | 1.000 | unsettled |
| aec-setkameua-sapole-salvas | **R2** | — | 0.61 | 2.6s | yes | yes | 0.615 | −1.0779 |
| atc-epl-cov-bha | **R2** | 1.010 | 0.48 | 56.2s | yes | yes | 0.903 | unsettled |
| aec-setkamecz-snimic-kruluk | R4 | 0.920 | 0.91 | 0.0s | no | no | 0.000 | unsettled |

**Every market reached a pair cost at or below 0.97 within 0–122 seconds of
the first fill**, and **our own maker complete price was touched in 7 of 8**.
Two of those markets still finished with **zero opposite-side fills and zero
matched inventory** — including buf-hou, whose confirmed realized is
−$2.5896 and whose complement was available at a pair of **0.515**.

*(The opposite-ask series is sparse — it exists only where an order was
resting — so every minimum is a floor over what we SAW. That biases toward
UNDER-stating reachability, which makes the finding stronger, not weaker.)*

## 2. What predicts a never-reachable complement?

**Nothing, because there are none.** R3 is empty at n=8. Every univariate
split has an empty "never" arm, so no admission signal can be fitted and
**the A1 admission hypothesis has nothing to separate in this corpus.**

That is a negative result, not a missing one. It says the problem is not
*which markets we enter* — it is what happens after we are in them.

## 3. How often does L5 switch sides compared with us?

Size-normalised, compared on **switches per fill** (his windows are 5–15
minute crypto settlements, ours multi-hour sports markets, so per-minute is
not comparable and is not compared):

| | L5 (n=2,201 windows) | OURS (n=9 markets) |
|---|---|---|
| switch rate per fill, p50 | 0.2857 | **0.2500** |
| median run length, p50 | 2.0 | **1.5** |
| **matched fraction at settlement, p50** | **0.625** | **0.7502** |
| fills per window, p50 | 6 | 4 |
| zero-switch windows | 595/2,201 = **27%** | 3/9 = **33%** |

**We are not failing to oscillate.** Our median market matches more of its
inventory than his does, and our stranding rate (33%) is statistically
indistinguishable from his (27%) at n=9. What we have is his distribution
with nine draws and the losses concentrated in the bad tail.

This corrects the framing the last several cycles have been built on. The
action-label fidelity work was not wasted, but **trajectory was never the
gap it was assumed to be.**

## 4. At what economic pair price does L5 actually switch sides?

n = 21,699 switches. `avg_pair` = running average cost of the held side +
price of the first opposite fill — the analogue of our COMPLETE gate.

| band | share |
|---|---|
| ≤ .95 | 41.3% |
| .95–.97 | 4.2% |
| .97–.98 | 2.3% |
| .98–.99 | 2.4% |
| .99–1.00 | 2.6% |
| **> 1.00** | **47.2%** |

**Median 0.99. p75 1.1113. p90 1.2266.**

Window-level effective matched pair cost (avg_up + avg_dn over the whole
window, n=1,606 two-sided windows): **median 0.9648, p75 1.0491, and 36.2%
of his completed windows end ABOVE 1.00.**

By price band the dependence is strong: at a held side under 10c his median
switch pair is **1.1742**; above 90c it is **0.7889**.

**Maker/taker is UNKNOWN for L5 and is never inferred. This is economic
price only.**

## 5. Are we demanding a cheaper complement than L5 accepted?

**At the policy level, yes — clearly.** Our `SPRING_PAIR_TARGET` is 0.98
with a hard ceiling at 1.00. L5's median switch is 0.99 and **47% of his
switches are above our hard ceiling**. We refuse roughly the top third to
half of the pair prices he actually pays.

**At the moment of decision, the gap is half a cent.** Over 48
contemporaneous candidates (authenticated quote ≤5s old, inventory held,
model wanting the opposite side, 8 markets):

| | p25 | **p50** | p75 |
|---|---|---|---|
| executable **taker** pair cost | 0.9177 | **0.9677** | 1.0100 |
| our posted **maker** pair cost | 0.9627 | **0.9627** | 0.9950 |

**We forgo about 0.5c of pair cost to remain maker — and that 0.5c is what
costs the fill.** Our COMPLETE fill rate is 6.78%; our EXTEND fill rate is
14.07%.

This is the ~60-tick gap from P0-B seen from the other end. It was never
mainly about *price level*; it is about insisting on the passive side of a
spread that is already thin.

## 6. Is there an admission signal separating oscillating from stranded markets?

**Not in this corpus.** The two stranded markets (buf-hou, jayfri-maxbas)
had complements available at pairs of 0.515 and 0.64, reached 0.97 within
0–32 seconds, and had our own posted price touched. On every pre-entry
variable measured — first-fill price, spread, opposite ask, pair at ask,
distance to par, size, TTE — they are indistinguishable from the markets
that completed.

**The separation is not at admission. It is at fill conversion.**

---

## P2 — the oscillation mechanism, measured

n = 21,699 switches, Mar 27 → Aug 31 regime.

* **Median 9 seconds between side switches** (p25 3s, p75 26s, p90 59s).
* **Median run length before a switch: 2 fills** (p75 4, p90 7).
* **B is the dominant mode, not A.** Measuring the *temporal* drift of the
  held side's own price across the run that preceded each switch:

| held-side drift during the run | share |
|---|---|
| **flat, < 1c** | **58.0%** |
| fell 1–5c | 13.7% |
| fell ≥ 5c | 9.2% |
| rose 1–5c | 12.6% |
| rose ≥ 5c | 6.6% |

Median drift **0.00**. **L5 alternates sides continuously without waiting
for the market to move.** He is not buying the opposite side *because*
price moved; he is working both sides more or less constantly, every few
seconds, in clips of one or two fills.

* **C — near settlement** he switches slightly *less* often, not more:
  median 12s between switches inside 60s of settlement against 9s beyond
  300s.
* **D — inventory magnitude** at a switch: held quantity p50 609 contracts,
  p25 168, p90 1,917. His scale, stated for context only; no PMUS-safe risk
  level is inferred from it.

*(A method correction made during this work: the first version measured the
gap between the last same-side fill price and the first opposite fill price
and called it "price movement". In a binary market the two sides sum to ~1,
so that gap is determined by where in 0–1 the market sits, not by any
temporal move, and it produced a near-symmetric 50/45 split that meant
nothing. The table above uses the held side's own drift over its own run.)*

---

## What this changes

Three cycles of work have now eliminated, in order: the +1-tick maker
adapter (P0-B), EXTEND suppression (P0 forensics), the held-inventory band
(band semantics), and the residual cap (cap arms). This cycle eliminates two
more: **complement reachability** and **market admission**.

What is left standing is narrow and specific: **at a pair price we already
find acceptable, we post passively and do not get filled, roughly 93% of the
time.** L5, at the same economic prices and often worse, changes sides every
nine seconds.

**No production change is proposed and none should be made on this report.**
The obvious next question — whether a bounded, strictly-gated complement
that crosses when the contemporaneous pair is already inside our existing
economic ceiling would convert those fills — is a change to execution
policy on a money-moving service, and it needs its own pre-registered
shadow with fees resolved from source before anything is armed. Fees remain
**UNKNOWN**: the venue does not report them per fill, and no taker economics
here are net of a fee we have not confirmed.

## Files

`l5_switches.json` (P2/P4, 21,699 switches), `reachability.json` (P0/P1),
`ours_vs_l5.json` (P3/P5), `l5_switches.py`, `reachability.py`,
`ours_vs_l5.py`, `src/l5_oscillation.py`, `tests/test_l5_oscillation.py`
(12 regressions).
