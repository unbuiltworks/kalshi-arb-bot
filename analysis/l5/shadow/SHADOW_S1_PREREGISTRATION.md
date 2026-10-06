# SHADOW S1 — pre-registration

**Candidate fingerprint
`441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b6fe`**,
written before any evaluation window exists.

**Authorized scope: zero-capital realtime shadow measurement only. No
order submission, no wallet signing, no live capital, no simulated
touch=fill.** Nothing here is realized profit for our account. A separate
explicit owner authorization is required before any order is submitted,
and shadow success does not constitute one.

## Why this module exists

Four prior kills (Candidate V1, V2, Ladder V1, the arming rule) all failed
on the same unobservable: **we have never known which orders were L5's.**
No archive attributes orders to a trader, and the vendor survey confirmed
that holds for paid sources too. The only way to obtain placement,
cancellation, queue position and fill attribution is to own the orders —
hypothetically, at zero capital, in real time.

## Safety, enforced in code rather than by convention

* reuses `polymarket_l5.http_client`: **HTTPS-only, GET-only**,
  allow-listed hosts (`data-api`, `gamma-api`, `clob.polymarket.com`), and
  **no Authorization / API-key / signature header on any code path**;
* this module contains **no signing and no private-key handling**;
* `tests/test_owned_order_shadow.py` walks the module's AST and fails on
  any write verb (`post/put/delete/patch/sign/send_transaction/
  place_order/create_order`) or credential-ish identifier.

One self-inflicted bug caught and fixed en route: the preflight originally
asserted on a **substring of its own source**, so it matched the text of
its own assertion. It now checks AST identifiers. That is the third time
this session a substring check has matched my own prose, and the fix is
the same each time.

## The fill standard — graded, and never touch=fill

A hypothetical resting bid at price `p`, placed when size `Q` already sat
at `p`, joins **behind** that size.

| verdict | condition | counts as a fill? |
|---|---|---|
| `EVIDENCED_LEVEL_CONSUMED` | best bid falls **strictly below** `p` | **yes** |
| `EVIDENCED_TRADE_THROUGH` | prints at ≤ `p` with cumulative size ≥ `Q + our qty` | **yes** |
| `QUEUE_CLEARED_FRONT_OF_BOOK` | size at `p` drains to 0 | **no** |
| `UNCERTAIN_PARTIAL` | size at `p` falls, but by less than `Q` | **no — separate ledger** |
| (none) | anything else, including the touch merely reaching `p` | no |

### A real conceptual error, corrected in the conservative direction

An earlier draft treated queue depletion past `Q + our qty` as evidence.
**It is not.** Our hypothetical order is *not in the observed book*, so the
size at our level counts only the queue **ahead** of us. That queue
draining to zero proves the queue cleared — it does **not** prove our own
quantity traded; we would be at the front, still resting. Nine hand-built
cases pin this, including the two that matter most: a touch that merely
reaches our price is **never** a fill, and a cleared queue is **never** a
fill.

`UNCERTAIN_PARTIAL` exists because a level shrinking can be cancellations
ahead of us rather than trades. Those are recorded and **excluded from
primary economics**.

## Candidate S1 — the smallest possible two-sided passive policy

Informed by L5 observation, **not optimized against P&L**. Parameterized
explicitly so later variants are comparable:

| parameter | value | basis |
|---|---|---|
| levels | **1** | smallest possible |
| offset from bid | **0 ticks** (join the bid) | smallest possible |
| quantity | **23.61 shares** | L5's observed median leg size, loaded from `L5_BENCHMARKS.json` |
| replenish after **evidenced** fill | yes | — |
| replenish after **uncertain** fill | **no** | uncertain fills must not drive behaviour |
| order lifetime | rest until filled or window end | smallest possible |
| residual cap | **80 shares** | Ladder V1's measured cap, which bounded residual at 94.4 shares |
| residual control | **QUOTE_SUPPRESSION_ONLY** | no aggressive opposite-side buying |
| TTE shutdown | **60 s** | — |
| 5¢ EXTEND rule | **absent** | contradicted on 90.6% of L5's real EXTENDs |

## Benchmarks — recomputed from canonical artifacts, not hard-coded

`analysis/l5/shadow/L5_BENCHMARKS.json` is generated from the committed
JSONs with a `source` field on every entry:

| benchmark | value | source |
|---|---|---|
| fill toxicity | **−$0.0041** CI [−0.0251, +0.0194], 3,069 fills / 27 mkts | `FILL_TOXICITY.json` |
| leg qty median | **23.61** | `LADDER_V1.json` |
| fill classes | resting 62.0% / mint 29.0% / inside 9.0% | `LADDER_V1.json` |
| pair cost | median **0.9679** (p25 0.9195, p75 1.0136) | `EDGE_DECOMPOSITION.json` |
| matched fraction | median **0.7034** | `EDGE_DECOMPOSITION.json` |
| matched economics | **+$80,652** | `EDGE_DECOMPOSITION.json` |
| residual economics | **−$30,654** | `EDGE_DECOMPOSITION.json` |
| two-outcome burst incidence (≥3 fills) | 71.6% | `LADDER_V1.md` |
| maker fraction | 0.883 | `ZENODO_OVERLAP_FINDINGS.md` — **still prose-sourced**, flagged |

Two of these are not yet machine-sourced (maker fraction, burst incidence
are read from markdown). They are labelled as such rather than presented
as recomputed.

## First gate

Collect enough **independent markets** to decide whether hypothetical
passive orders receive **evidenced** fills at anything near L5's frequency
and toxicity. **If fill opportunity is nowhere close, KILL quickly** —
that is a cheap and decisive answer, and the graded standard means a
near-zero evidenced-fill count is informative rather than a bug.

Only if comparable, continue long enough to measure matched-inventory and
residual behaviour.

## Comparison set (mechanics, not P&L)

fill rate · two-sided burst frequency · fill cadence · fill price
distribution · maker/passive fraction · adverse selection · matched
fraction · residual · pair cost · capital deployed.

**The purpose is to learn placement and replenishment mechanics, not to
manufacture shadow P&L.** No shadow P&L figure will be presented as
expectancy.

## Execution environment

`clob.polymarket.com` and `gamma-api.polymarket.com` both answer
**CONNECT → 000/403** from this container, so collection runs in CI, as
every other collector in this programme does. The preflight path runs
locally and is green.

## Discipline

Each candidate is pre-registered and fingerprinted before its evaluation
window is collected; no candidate is tuned on the windows used to score
it; raw book and trade data are kept. Gemini BTC15M's ≥100-window regime
test runs independently and does not gate this. Gemini BTC5M is not
reopened. No further historical L5 arming/switch/ladder fitting unless
prospective evidence raises a specific new hypothesis.
