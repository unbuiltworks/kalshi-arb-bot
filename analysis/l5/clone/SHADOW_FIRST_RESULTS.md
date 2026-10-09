# Clone shadow — first measured results (2026-10-09, ~8.5 h continuous)

**Service:** `bc7a1f5b-f059-4937-8486-02d68dcb3bf9` (display name still
`r5ft-clean-2ebb50f-buildcheck`), project `arb-ev-wallet-shadow`, production.
**Branch:** `claude/august-l5-forensic-replay-788cml`.
**Commits run:** `705d986ad` 02:4xZ → 11:09Z, then `cc85af6a8` from 11:11Z.
**Label on every number below: SHADOW. Nothing here is realized profit.**

## It is actually running

- 1,308 cycles at 02:23Z → 11:17Z, no crash, `restartPolicyType: ALWAYS`.
- 15,827 evidence rows persisted before the last redeploy; the redeploy
  resumed at **cycle 1294** from `state.json`, so both arms' ledgers and
  open candidates survived it. Durability is demonstrated, not asserted.
- Discovery: **23,183 markets served**, **336 with a resolvable clock**, all
  `crypto`. Of those 336 only ~5 serve a book at any instant
  (`poll_failed: 43`, `slugs_with_failing_books: 331`) — the rest are future
  windows the venue has created but not opened. That is the real tradable
  slate, not a defect.
- Both arms decide on every polled market every cycle: 10–14 decisions per
  cycle = 5–7 markets × 2 policies.

## Early vs late, on identical market states

| | **early** (primary candidate) | **late** (comparison) |
|---|---|---|
| candidates opened | 29 | 34 |
| from the clone / from the entry rule | 3 / 26 | 8 / 26 |
| queue-cleared (would-fill) | 9 (31.0%) | 11 (32.4%) |
| touched but no trade | 0 | 3 |
| never touched | 20 | 20 |
| markets with virtual inventory | 6 | 6 |
| mean / max abs residual (shares) | 1.50 / 4.0 | 1.83 / 4.0 |
| markets where a pair was ever matched | **0** | **0** |
| adverse selection t+30, all candidates | +0.00982/share (25% adverse) | +0.00484/share (28% adverse) |
| **adverse selection t+30, queue-cleared only** | **−0.0175/share** | **−0.0130/share** |
| complement available at t+30 | 100% | 97.0% |
| complement completable ≤0.96 maker at t+30 | 71.4% | 60.6% |
| pair edge/share if completed maker | −0.00375 | 0.000 |

Caveat first: **n = 8–10 queue-cleared candidates with a markout.** These are
directions, not estimates. The shadow keeps running and the numbers are
recomputed every five cycles.

## The one result that matters so far

**PMUS maker adverse selection, measured on our venue for the first time.**
Across all candidates the 30-second mid move is mildly *favourable*
(+0.5 to +1.0¢/share). Restricted to the candidates whose queue was
provably cleared by volume — i.e. the ones that would actually have filled —
it flips to **−1.75¢/share (early) and −1.30¢/share (late)**.

That gap between "all quotes" and "quotes that got hit" *is* adverse
selection, and it is the quantity `CANONICAL_L5_MODEL.md` §I asked for and
`L5_ECONOMIC_DECISION_20261009.md` had to leave open. Direction: the fills a
passive quote receives on PMUS crypto up/down are the ones you'd rather not
have. For scale, L5's own measured own-fill markout at t+30 on Polymarket
was −0.00381/share; this preliminary PMUS figure is ~4× worse, on a sample
small enough that the multiple is not yet meaningful — the sign is.

Against that, the maker pair edge if a completion were posted at the
complement's own-side bid is **−0.4¢ (early) to 0.0¢ (late) per share**
before fees, rebates and rewards. A negative first-leg markout plus a
~zero pair edge is not a strategy; it is a measurement that says the
first-leg entry rule, as written, has no edge on this venue at this hour.

## Behavioral early vs late

- **The late regime is more aggressive**: 8 clone-driven actions to early's
  3, and 34 candidates to 29, on the same observations. Both arms reach the
  same ~31–32% queue-clear rate, so the difference is in how often the
  policy wants to act, not in how often it gets filled.
- **Neither arm ever completed a pair** (`two_sided_markets: 0`), so
  `matched_pair_cost` has no sample yet. Every clone-driven action so far was
  `CLONE_EXTEND`; no `CLONE_COMPLEMENT` candidate has been opened. With
  P(EXTEND) ≈ 0.70 in both regimes that is expected early in a run, and it
  is the main thing the next stretch of shadow needs to produce.
- Residual imbalance stayed within 4 units/market under a 4-unit-per-side
  cap, so the bound held.

## What was wrong and is now fixed (all found by running it)

1. **Clock.** The training extractor's slug regex matches Polymarket's
   `-15m-<unix>`; PMUS serves `-15m-YYYY-MM-DD-HHMMz`. Unfixed, two of the
   policy's fourteen features would have been wrong on every live market
   with no error. `src/pmus_market_clock.py`, plus `startDate` as an alias
   for `startTime` once the live payload showed it.
2. **Discovery cap.** The first cycle served exactly 15,000 markets — 30
   pages of 500 — and resolved a clock for none: all MLB futures. Walking
   `&categories=<c>` per category then the unfiltered slate took it to
   23,183 served and 336 watchable.
3. **An entry gate that could not fire.** It required instantaneous maker
   pair cost ≤0.96, i.e. a 4¢ spread, on books that quote 1–2¢. Zero
   proposals. The pair ceiling belongs on the *complement* decision, which
   is where it now lives; entry requires a two-sided book and a first leg
   ≤0.90.
4. **An invented two-tick spread minimum**, which refused live markets on
   its own. Removed — joining the own-side bid in a one-tick book is
   ordinary maker behavior and nothing in L5's fills supports the gate.
   What remains is real: a crossed or locked book is not a book to rest in.
5. **Slots held by dead books.** Sorted by expiry, 43 of 48 slots held
   future windows that serve no book. Per-slug failure counts now demote
   them.
6. **Silent zeros.** Every cycle line now carries `refusal_reasons`,
   `out_of_domain_reasons`, `poll_failed` and `slugs_with_failing_books`,
   because the first live run could say `proposed: 0` without saying why.

## Blockers to a bounded live strategy

1. **The measured sign of first-leg markout is negative on queue-cleared
   candidates.** Until that reverses, or a completion policy is shown to
   recover more than it costs, a live probe would be funding a measured
   negative edge. This is the binding blocker, and it is economic, not
   engineering.
2. **No completion has been observed in shadow at all.** The pair economics
   that the whole thesis rests on are still unmeasured on PMUS: n = 0.
3. **Sample size.** 8–10 decidable candidates after 8.5 h. The slate is
   ~5 live books; more sample needs more hours, not more slots.
4. **The clone cannot enter.** 100% of entries come from our hand-written
   rule, because the trained model is out of domain with no prior fill.
   Order placement is still not cloned, and no amount of shadow time fixes
   that — it needs the historical-book join described in
   `L5_BEHAVIORAL_CLONE_FINDINGS.md` §5.
5. **Queue position remains unproven by construction.** Every would-fill
   here required volume to exceed the whole visible queue; real maker
   priority could be better (we might fill sooner) or worse (hidden or
   simultaneous orders). The measurement is deliberately pessimistic.

E1 stays disarmed. This service holds no credentials and imports no order
path. No live order follows from anything above.
