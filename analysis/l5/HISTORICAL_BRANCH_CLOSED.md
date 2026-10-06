# Historical order-state branch — CLOSED

**Closed 2026-10-06** at the user's direction, accepting
`5816406e` / `a3154b48`:
**PERSISTENT RESTING vs DYNAMIC REPOSTING = CONSISTENT_WITH_REPOSTING**,
with historical **order identity explicitly UNIDENTIFIABLE**.

## Standing rule

**Do not build another fill-only model attempting to infer cancellations or
unfilled L5 orders.** The archive contains executions and anonymous depth
only — no order id, no placement event, no cancel. Three successive studies
converged on that wall:

| study | result |
| --- | --- |
| ladder reconstruction (`f07aec7c` / `bc07c548`) | ~90 % of "sizing" is counterparty partial filling; replenishment refuted |
| coordinate system (`c65bf29f` / `959d9f34`) | levels appear at the touch; apparent depth is accumulated touch travel |
| persistence (`5816406e` / `a3154b48`) | prior use of a price confers ~6 % advantage on return (1.061×) — reposting is the simpler description |

## Preserved behavioral hypothesis — DESCRIPTIVE ONLY

Carried forward as description, **not** as a validated or deployable model:

1. **Repeated maker exposure near the moving touch** — median offset 0 ticks
   at every level rank; the apparent 5–15 level ladder is where the touch
   has been.
2. **Partial, counterparty-determined fills** — 91.0 % of irregular
   quantities by count, 88.3 % by volume, are partial consumption of larger
   latent clips; observed fill size is not a decision.
3. **Inventory tolerance** — large intermediate imbalance is routinely
   carried (residual 102–129 shares), and same-side EXTEND is first-class.
4. **Eventual withdrawal / suppression** — withdrawal associates with TTE
   (Cliff's delta −0.82), accumulated inventory (+0.74) and clip exhaustion
   (+0.48), and with **no** contemporaneous book variable (|delta| ≤ 0.08).

### What this hypothesis is NOT

- **Not predictive.** Next-action lift 1.001 vs persistence (12/12 windows);
  conditional future-inventory `E2_POSITIVE = False` at every horizon.
- **Not a pair-cost controller.** Running pair-cost control is **REFUTED**;
  ~0.98 is ambient complement-sum arithmetic, and L5's running average is
  7× noisier than the market's own and 19× noisier than a no-control null.
- **Not an allocation schedule.** All four allocation bars failed; only a
  *terminal* equal-shares constraint survives, as description.
- **Not evidence of resting-order persistence.** See above.

Anything built from these four points must be labelled descriptive and
carry its own preregistration.
