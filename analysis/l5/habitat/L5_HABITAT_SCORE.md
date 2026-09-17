# L5_HABITAT_SCORE — can a passive quote surface actually live here?

`analysis/l5/habitat/score.py` · `habitat_v1.json` · real observations only ·
**no composite score**

The primary diagnostic is **post-fill continuation opportunity**: after
acquiring one side, does a credible **opposite-side** interaction actually
arrive, and how soon? `MATCHED_SHARE_LINEAGE_V1` found that the complement has
to arrive within tens of seconds to be worth anything, so a venue with plenty
of volume and no timely opposite interaction is one where L5's mechanism cannot
run — however busy it looks.

**Identical definitions everywhere, different economic mappings.** An
*interaction* is a trade that actually printed: prints where the venue
publishes them, a rise in cumulative traded quantity priced at the venue's own
`last` where it does not. **Book movement is never counted as interaction** —
that conflation already cost us a 97%-vs-46% error once. What a *pair* means is
not flattened:

| venue | mapping |
|---|---|
| PMUS / Polymarket | **two tokens, both bought**: cost = `b_yes + b_no` |
| Gemini | **one contract** (the `-DOWN` book does not exist): `BUY b + SELL s`, the sale a **synthetic** complement worth `1 − s` |

## The primary diagnostic

P(opposite interaction within Δ), median across markets. `harder_side` is the
minimum of the two directions — a surface needs whichever side it did **not**
just fill.

| Δ | **Gemini BTC05M** buy / sell / **harder** | **PMUS sports** buy / sell / **harder** |
|---|---|---|
| 5 s | 0.0000 / 0.0188 / **0.0000** | 0.0238 / 0.0121 / **0.0086** |
| 30 s | 0.0000 / 0.1129 / **0.0000** | 0.0562 / 0.0357 / **0.0192** |
| 60 s | 0.0000 / 0.2177 / **0.0000** | 0.1087 / 0.0714 / **0.0257** |
| 120 s | 0.0726 / 0.2820 / **0.0000** | 0.1698 / 0.1217 / **0.0595** |

**Gemini BTC05M is 0.0000 on the harder side at every horizon.** After buying
the bid, no trade printed at or above the ask within two minutes in the median
market. The complement does not arrive.

**PMUS sports is low but non-zero and monotone** on both directions and on the
harder side.

## Every other component, reported separately

| component | Gemini BTC05M | PMUS sports | Polymarket BTC15m |
|---|---|---|---|
| markets / quotes / interactions | 18 / 5,478 / **67** | 168 / 20,110 / **1,694** | instrumented 16:04Z, pending |
| expected credible interactions remaining | **0.63** | n/a (no uniform window) | pending |
| two-sided uptime | 0.7612 | **0.8897** | pending |
| reversal frequency / min | **5.835** | **0.000** | pending |
| spread (median) | **0.220 = 22 ticks** | 0.050 = 5 ticks | pending |
| touch depth bid / ask / harder | 10 / 10 / **10** | 5,100 / 4,900 / **2,400** | pending |
| interaction frequency / min | 0.500 | 0.044 | pending |
| maker/post-only validity | 1.0000 | 1.0000 | pending |
| known fees | **venue_declared_unknown** — not estimated | 0.07 taker-only, rebate 0.2, tick 0.01, min 5, rewards min 50 / max spread 4.5 | same as PMUS |
| **capacity** (median touch notional) | **$4.40** | **$1,650.00** | pending |

**`composite: null`, deliberately.** A composite lets a failed component hide
behind a passing one. Gemini's 22-tick spread and perfect maker validity would
carry an average straight past a continuation probability of zero.

## Reading

**Gemini BTC05M has an excellent price and nobody to trade with.** A 22-tick
spread implies a pair at the touch near 0.71–0.80 against ~0.98 on PMUS — and
it is unreachable: **$4.40 at the touch**, 67 interactions across 18 markets,
and a **zero** probability that the harder side prints within two minutes. It
is an empty room with a very good price tag.

**This does not contradict the preregistered Gemini verdict, it sharpens it.**
That verdict returned `VIABLE_FOR_NEXT_L5_STAGE` for BTC05M on flow floors
(11.32 seq/hr upper bound, 0.7903 trades/min) and its own label said *"VIABLE
means the venue clears the floor, never that the strategy is profitable
there"*, with sequence counts that *"assume we win every print"*. Continuation
is the test the floor cannot do: it asks whether the **second** leg arrives.
Here it does not.

**PMUS sports is the opposite trade-off.** A 5-tick spread and 0.044
interactions/min per market look thin, but there are 168 of them, the touch
carries **$1,650**, two-sided uptime is 0.89, and the harder-side continuation
is small yet **real and monotone** (0.0086 → 0.0595). Capacity is ~**375×**
Gemini's.

**One PMUS sports property matters for the lineage result: reversals are
0.000/min.** Pre-event sports books barely oscillate. `MATCHED_SHARE_LINEAGE_V1`
found that *reversals*, not drift, generate matched pairs (0 reversals →
+14.8 bp/$ OOS; 6+ → +93.9). If that relationship survives prospectively, a
book that never reverses is structurally poor for pair formation — and Gemini,
which reverses 5.8×/min, has the choppiness but not the counterparties.

**Neither venue currently looks like L5's habitat**, and the reasons differ:
Gemini lacks counterparties and capacity; PMUS sports lacks oscillation. That
is the most direct evidence yet that what we fail to reproduce is **the
habitat**, not the decision rule.

## Caveats, stated rather than buried

* Gemini rests on **67 interactions**; per-market medians are small-n.
* The PMUS book tape is **still collecting** (closes ~17:15Z). These are
  partial figures. The frozen preregistered sweep verdict is a separate
  artifact and is untouched.
* `expected_credible_interactions_remaining` is unavailable for PMUS sports:
  those markets have no uniform window length, and inventing one would be
  worse than the gap.
* Polymarket BTC15m had **no measurable habitat** until 16:04Z because the
  recorder only wrote where L5 traded — exactly the selection bias a habitat
  measure must not have. It now emits a periodic public book summary; the
  recorder was restarted once for this and the gap is disclosed here.
* L5's own fills are the only interaction stream visible on the Polymarket
  recorder. They are a **lower bound** — everyone else's trades are invisible
  to it — and the row carries no price, so they cannot feed the continuation
  test. Counted and named, never silently used.
