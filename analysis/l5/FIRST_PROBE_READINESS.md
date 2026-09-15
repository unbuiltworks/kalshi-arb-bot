# First-live-probe readiness — one line per element

**No live authorization is requested or implied.** The engine under assessment
is the **new DesiredInventoryAction engine**, not the halted legacy PMUS
campaign, which stays latched.

| # | element | evidence status | sample | blocker |
|---|---|---|---|---|
| 1 | **behavior** | REPLAY intents restate L5's own fills exactly; FIDELITY v0.2 model intents exist but hardcode `incremental_qty = 1`, below Polymarket's minimum of 5, so **no MODEL intent is placeable on PM at all** | 257 intents | engine defect, PM-side MODEL is dead until fixed |
| 2 | **observation** | feed clock live and continuous; chain clock measured **15.16 s ahead** (n=73, ahead 73/73) but **UNPROMOTED** — gate at **5/8**, blocking G-C1 (sample restarting from epoch 3), G-C4 + G-C8 (accumulating). P1 measures what promotion buys: median recovery **13.54 s**, and **90.8 % of intents are still valid at feed time against 100 % under the chain clock** — so the clock rescues about **one intent in ten** from decay | 696 compared, 36 pre-epoch runs excluded | sample time only |
| 3 | **placement** | maker join computed against full multi-level books on both venues; reachability PM 53.1 % / Gemini 85.5 % on the maker path | 319 quotes placed | — |
| 4 | **fill** | **11 evidenced fills from 364 quotes ≈ 3 %**, and **322 of 364 verdicts are UNKNOWN** (260 of them Gemini); vouched silence now resolves to an evidenced no-fill, so this should fall | 11 | the UNKNOWN fraction makes the fill rate itself unmeasured |
| 5 | **inventory** | **8 positions, all one-sided. Zero matched pairs, ever.** Expected pairs at a 3 % fill rate ≈ 0.06, so this is arithmetic, not a defect | 8 | **primary blocker** |
| 6 | **settlement** | venue resolution read and applied; 7 of 8 positions settled | 7 | — |
| 7 | **realized economics** | `realized_shadow` **−$102.77**: matched-pair **$0.00**, residual **−$102.54**, fees $0.23, rebates/rewards $0 by fact | 7 settled | negative, and composed entirely of exposure with no demonstrated skill |
| 8 | **risk** | live risk shell exists and bounds per-market dollars and residual; **the shadow itself is unbounded** and has been accumulating precisely the residual the operating model says to bound | — | shadow-side bound not built |

**Readiness: NOT READY, and the binding constraint is element 5.** A live probe
whose defining sequence is *OPEN → EXTEND → patient fee-safe COMPLETE* cannot
be justified by a shadow that has never once reached COMPLETE.

**The next milestone is unchanged and is not an architecture feature:** a
prospectively evidenced, settled, economically positive **sequence** — meaning
at minimum one completed pair — that the new engine could actually have
executed.


---

## What changed since the first version of this table

- Element 4's UNKNOWN fraction has a named cause and a fix in flight: a quote
  that rested its full horizon while the Gemini socket watched continuously is
  an **evidenced no-fill**, not an unknown. Quotes placed before that change
  keep their old verdict.
- Element 5's zero is now understood rather than merely observed. At a ~3 %
  fill rate the expected pair count is 0.06; zero is the prediction. **The
  pairing logic is not the problem — the fill rate is**, and it is now
  decomposed into `NO_FLOW` / `QUEUE_BOUND` / `PARTIAL` so the next move is
  chosen from evidence rather than instinct.
- Element 2 now has a number attached to the clock question, which it did not
  before: one intent in ten. That is worth having and is **not** what unblocks
  element 5 — a faster route to an unpaired position is still an unpaired
  position.
