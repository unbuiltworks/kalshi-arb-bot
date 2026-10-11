# The L5 invariant (OWNER 5570474405)

**L5 DECIDES → EXECUTE WELL → RECYCLE WHEN L5 COMPLETES → MEASURE TOTAL ECONOMICS → SCALE WHAT WORKS.**

Pairing is not the strategy. L5 is the strategy. Every module, report and
rung in this branch is read against the hierarchy below; anything that
would violate it is a defect, not an optimisation.

1. **L5 behavioral model decides** OPEN / EXTEND / COMPLETE, side,
   approximate size, timing and aggressiveness from the sequential state
   (frozen model sha `bbb55bf90638d2bd`, lookup v3, stochastic arm
   `40e28081226917a9`). Nothing in the execution or safety layers may draw
   a different action.
2. **Execution makes that decision efficient.** If COMPLETE: seek the best
   maker complement (pair cost < 0.98 preferred, then < 1.00; 1.00 is
   break-even before incentives and the hard R4 ceiling) and recycle when
   filled. If EXTEND: execute EXTEND unless an explicit safety bound
   prevents it. COMPLETE is never substituted for an L5 EXTEND because a
   pair would look safer.
3. **The safety shell bounds translation risk, classified separately.**
   Every refusal carries its own class (`safety_adaptation:unmatched_usd_cap`,
   `spend_cap_reached`, `entry_band_left`, `complement_not_priceable`,
   `off_distribution_hold`, ...). A refusal is fidelity evidence, not a
   success. The shell never redefines L5.
4. **Profitability is judged across the full L5 sequence:**
   pair contribution + venue-credited maker rebates + venue-credited
   liquidity rewards − residual losses − fees, on settled cohorts, venue
   numbers only. Individual L5-consistent actions may carry temporary
   residual or poor standalone economics.
5. **Scale what works:** environment selection and turnover without
   behavioral distortion first; repetitions; then size toward L5's median;
   concurrency last; never automatically.

Where it lives in the code and reports:
- `src/pmus_l5_continuous_session.py`: the model draws the action; the
  shell only refuses and logs the class (`divergence_classes` in
  `r4_action_comparison.json`: 44 `safety-cap override`, 24 `genuinely
  off-distribution state`, 44 `no disagreement`).
- `src/l5_r4_action_comparison.py`: sampled vs acted relation mix, every
  hold by refusal, `par_band` tracker.
- `src/l5_economic_scoreboard.py` / `src/l5_profitability.py`: the
  full-sequence equation with every term separate and labelled.
- `src/l5_market_scorecard.py`, `src/l5_opportunity_utilization.py`:
  selection and rotation are ranked for L5-process executability, never
  for direction.
- `src/l5_next_rung.py`: levers are scored in
  `(pair edge + credited incentives − residual − fees)` per bankroll-dollar
  per hour, each with its downside and whether it moves a behaviour or a
  bound; nothing is promoted by a file.
