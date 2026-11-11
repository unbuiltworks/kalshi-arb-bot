# Where the conservative net losses come from

Eight settled BTC15m windows (1700z–1845z, 2026-10-19), three policies,
CONSERVATIVE scenario, authenticated ladder observations, queue-aware fill
model, authoritative venue `settlementPx`. Every settled window is
included — winning, losing and no-completion alike. No replay money is
used anywhere in this document.

The decomposition reproduces production's own reported nets exactly
(−$2.50 / −$1.93 / −$1.96 against the heartbeat's net-per-window × 8),
which is the cross-check that the attribution below is arithmetic rather
than narrative.

---

## Answer: adverse selection, and it is structural rather than a policy
artifact

| | ARGMAX | RESIDUAL_URGENCY | PAIR_AVAILABLE |
|---|---|---|---|
| conservative net, 8 windows | **−$2.5000** | **−$1.9300** | **−$1.9600** |
| matched-pair net | +$1.2550 | +$0.7400 | +$0.4550 |
| directional residual net | −$3.7550 | −$2.6700 | −$2.4150 |
| *of which attributable to selection* | *−$1.2600* | *−$1.2300* | *−$0.9700* |
| fills | 27 | 22 | 14 |
| adverse fills | 70% | 68% | 64% |
| mean adverse move per fill | **$0.0467** | **$0.0559** | **$0.0693** |
| median adverse move | $0.0200 | $0.0200 | $0.0200 |

Pair net is positive for all three. Residual net is negative for all
three. Selection explains about **half** of the total loss and is not an
additive third term — it is an attribution of part of the pair/residual
outcome to systematically poor entry prices.

## The maker's ledger, per unit

    spread captured joining the bid    +$0.0050   half of one 0.01 tick
    maker fee                          +$0.0000   CONFIRMED over 293 fills
    maker rebate                       +$0.0000   UNCREDITED at q=1
    adverse selection                  -$0.0467   measured over 27 fills
    ---------------------------------------------
    net per fill before settlement     -$0.0417
    adverse cost : spread captured       9.3 : 1

**We are providing liquidity for half a tick and paying about nine times
that in selection.** Fees are confirmed zero and the rebate has never
credited at q=1, so there is nothing else on the credit side.

## The mechanism, end to end

1. **We quote at the touch.** 63 of 69 conservative fills had
   `ticks_behind_touch == 0`; only 6 were further back (3–11 ticks).
2. **We sit behind a deep queue.** `queue_ahead_at_placement`: median
   **1,500** contracts, max 16,555, min 2.
3. **With that much ahead of us, we fill only when a print sweeps the
   whole level** — which is what the queue-aware model records: the fills
   are sweeps.
4. **A level gets swept when the market moves decisively through it.** So
   our fills are concentrated in exactly the moments the market is
   leaving: 70% adverse, mean 4.7¢ against us.
5. **We are paid half a tick for that.**

This is why it is structural. It does not depend on which relation the
policy picks, which is precisely why `ADVERSE_EXTEND_REFUSAL` failed: the
adverse fills are spread across all three relations — ARGMAX's 27 fills
were 9 open, 13 extend, 5 complete — so refusing one relation cannot
address it.

## The three sources, distinguished as asked

**1. Fill uncertainty — $0, and currently unmeasurable.** In **0 of 24**
policy-windows did CONSERVATIVE, BASE and OPTIMISTIC differ at all. Every
fill cleared the strictest bar, and every queue was known, so the
optimistic-only unknown-queue sweep path never fired. Fill uncertainty is
therefore **not** a loss source here. The corollary is less comfortable:
the conservative column currently carries **no safety margin** over
optimistic, so it is not yet doing the job it exists to do.

**2. Unfavourable trade selection — about half the loss.** −$1.26 of
ARGMAX's −$2.50, measured directly as the wire moving away from our
outcome after we filled. Mechanism above. This is the largest single
identified source.

**3. Unavoidable directional risk — the remainder, roughly −$1.24.** A
residual held to settlement is a near-zero-expectation coin flip; over
eight windows it ran against us. Per `L5_OPERATING_MODEL`, residual
exposure has no demonstrated predictive skill, so its expectation is
approximately zero minus whatever was overpaid at entry — which is item 2
again. It is variance, not edge, and eight windows cannot separate its
mean from zero.

## What this does and does not license

It does **not** license a policy change. All three policies lose, and the
per-fill adverse cost is *worst* for the one that trades least
(PAIR_AVAILABLE, 6.9¢/fill on 14 fills), so "trade less" is not the
answer either.

It does identify one **specific, testable economic mechanism** for when a
change is wanted, with the evidence already in hand:

> We always quote at the touch (63/69 fills) and are therefore filled
> only on sweeps, which is when the market is leaving. Quoting one or
> more ticks BEHIND the touch would widen the captured spread from half a
> tick and should reduce the share of fills that are sweeps — at the cost
> of a lower fill rate.

`ticks_behind_touch` is already computed and already reported, so the
mechanism is measurable before and after. It would need its own
pre-registration, a predefined offset chosen without reference to results,
and far more than eight settled windows. **It is not proposed here.**

The other credit-side lever — the maker rebate — stays locked: it has
never credited at q=1 on this account and `CLAUDE.md` forbids assuming it.

## Limits

- Eight settled windows. Nothing here establishes expectancy; it
  identifies where the money goes.
- `moved_against_us` measures the wire's travel after our fill. It is the
  venue's own prices, not a model, but it attributes value change to
  selection on a short horizon and cannot separate that from ordinary
  volatility in a single fill. The 70% adverse rate and the 9.3:1 ratio
  are what carry the argument, not any single fill.
- Fee zero is CONFIRMED (293 fills). Rebate zero is UNCREDITED, not
  measured as absent.
