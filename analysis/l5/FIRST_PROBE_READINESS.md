# First-live-probe readiness — one line per element

**No live authorization is requested or implied.** The engine under assessment
is the **new DesiredInventoryAction engine**, not the halted legacy PMUS
campaign, which stays latched.

| # | element | evidence status | sample | blocker |
|---|---|---|---|---|
| 1 | **behavior** | REPLAY intents restate L5's own fills exactly; FIDELITY v0.2 model intents exist but hardcode `incremental_qty = 1`, below Polymarket's minimum of 5, so **no MODEL intent is placeable on PM at all** | 257 intents | engine defect, PM-side MODEL is dead until fixed |
| 2 | **observation** | feed clock live and continuous; chain clock measured **15.16 s ahead** (n=73, ahead 73/73) but **UNPROMOTED** — gate at **5/8**, blocking G-C1 (sample restarting from epoch 3), G-C4 + G-C8 (accumulating) | 36 pre-epoch runs excluded | sample time only |
| 3 | **placement** | maker join computed against full multi-level books on both venues; reachability PM 53.1 % / Gemini 85.5 % on the maker path | 319 quotes placed | — |
| 4 | **fill** | **8 evidenced fills from 319 quotes = 2.5 %**, and **284 of 319 verdicts are UNKNOWN** (230 of them Gemini) | 8 | the UNKNOWN fraction makes the fill rate itself unmeasured |
| 5 | **inventory** | **7 positions, all one-sided. Zero matched pairs, ever.** | 7 | **primary blocker** |
| 6 | **settlement** | venue resolution read and applied; 6 of 7 positions settled | 6 | — |
| 7 | **realized economics** | `realized_shadow` **−$91.14**: matched-pair **$0.00**, residual **−$90.91**, fees $0.23, rebates/rewards $0 by fact | 6 settled | negative, and composed entirely of exposure with no demonstrated skill |
| 8 | **risk** | live risk shell exists and bounds per-market dollars and residual; **the shadow itself is unbounded** and has been accumulating precisely the residual the operating model says to bound | — | shadow-side bound not built |

**Readiness: NOT READY, and the binding constraint is element 5.** A live probe
whose defining sequence is *OPEN → EXTEND → patient fee-safe COMPLETE* cannot
be justified by a shadow that has never once reached COMPLETE.

**The next milestone is unchanged and is not an architecture feature:** a
prospectively evidenced, settled, economically positive **sequence** — meaning
at minimum one completed pair — that the new engine could actually have
executed.
