# Costing: moving E1 from BTC15m to the BTC 1h series

**Decision input only. No code changed, nothing armed.** Written because PMUS
has every one of its 41 upcoming BTC **15m** windows at
`MARKET_STATUS_HALTED`, `btc-updown-5m` carries **zero** events, while
`cpc-btc-updown-1h-2026-10-08-1100z` (`id 1272926`) and
`cpc-btc-updown-1d-2026-10-07-2100z` (`id 1260672`) are both
`MARKET_STATUS_OPEN`, `closed: false`. If the short-duration products are
retired rather than paused, waiting is not a plan.

## What the venue confirms transfers unchanged

Measured on the open 1h market, not assumed:

| field | 15m | 1h |
|---|---|---|
| slug shape | `cpc-btc-updown-15m-YYYY-MM-DD-HHMMz` | `cpc-btc-updown-1h-YYYY-MM-DD-HH00z` |
| outcomes | `["Yes","No"]` | `["Yes","No"]` |
| tick | 0.01 | 0.01 |
| minimum qty | 0.01 | 0.01 |
| markets per event | 1 | 1 |

So the **entire execution path transfers as-is**: AuthBook, the
`l5_order_body` serializer and its long-side wire convention, the preview
echo gate, boundary 1 and boundary 2, the named-slot latch, the $5
forward-looking ceiling, the pacing repair and the side-symmetry rule. None
of that is 15m-specific.

## The code surface is small and contained

| location | pin |
|---|---|
| `pmus_identity.WINDOW_S` | `900` |
| `pmus_identity.slug_for` | `"cpc-btc-updown-15m-%s-%02d%02dz"` |
| `pmus_identity.is_pmus_btc15m_slug` / `assert_pmus_slug` | the 15m shape |
| `pmus_selector.SERIES` | `cpc-btc-updown-15m` |
| `pmus_provider.HANDOVER_TTE_S` | `60.0` |
| `runner.WINDOW_LEN_S` | derived from `PIDENT.WINDOW_S` (so it follows) |
| `SPEC["orders"]["series"]`, `SPEC["timing_explicit"]["window_len_s"]` | XR2W-inherited, `btc-updown-15m` / `900.0` |

A duration/series parameterisation of `pmus_identity` plus `pmus_selector`
covers it. The pacing cap and horizon already derive from `WINDOW_S`, so they
follow automatically. **Engineering cost is low.** That is not the expensive
part.

## The expensive part is the evidence, and it does not transfer

1. **The behavioural thesis is 15m/5m.** `CLAUDE.md` names
   `analysis/gemini/GEMINI_L5_CURRENT.md` as the BTC5m/BTC15m source of
   truth, and the near-touch maker rule — *"the single most strongly
   evidenced L5 behaviour: median offset 0 ticks from the touch"* — was
   measured there. On a 1h window the policy would no longer be validated by
   the evidence that justified it. That is a research regression even if
   every test stays green.
2. **The attempt rate changes by 4x.** 20 attempts over 15 minutes is one
   per ~45 s; over 60 minutes it is one per ~3 min. `$1.56` of per-window
   exposure and 20 attempts/window were sized for the shorter window.
3. **The cohort stretches 4x.** 12 windows x 1h = **12 hours** of wall clock
   instead of 3. A bounded learning cohort becomes an all-day commitment,
   and the $5 ceiling then governs a much longer exposure horizon.
4. **TTE proportions distort.** `HANDOVER_TTE_S = 60` is 6.7% of a 15-minute
   window and 1.7% of an hour; the XR2W-inherited spec says `120.0`, which
   this branch has never reconciled. A 1h migration forces that decision.
5. **Pair economics cut both ways, and honestly I cannot predict the net.**
   More time to complete the complement is *better* for matched-pair
   completion; an hour of BTC movement is *worse* for an uncompleted
   residual. Which dominates is an empirical question we have not measured on
   1h data.

## Recommendation

**Do not migrate to chase availability.** The engineering is cheap, but it
would run an unvalidated policy on an unmeasured microstructure, which is
exactly the drift `CLAUDE.md`'s anti-drift rule exists to stop.

If 1h is wanted, the defensible order is the one the operating model already
prescribes, not a shortcut:

1. point the existing read-only **collectors** at `btc-updown-1h` and gather
   a 1h tape;
2. re-measure the near-touch behaviour and the pair-completion rate on that
   tape, exactly as was done for 15m;
3. re-derive the first-leg ceiling, attempt rate and per-window exposure from
   1h evidence rather than inheriting 15m numbers;
4. only then parameterise the selector and mint a new fingerprint.

The cheap interim that needs no migration at all: keep the watcher on 15m,
and **bracket when the halt started** — if 15m halts daily and resumes,
waiting is correct and migration is wasted work.
