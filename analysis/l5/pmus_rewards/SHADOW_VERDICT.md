# PMUS reward shadow — result, and why it does not yet answer the question

2026-10-02. Read-only. No live orders. Kalshi frozen at `1ac07f69`, L5
benchmark unchanged, Gemini audit at `70ccad63`, no SOL infrastructure
touched.

Tape: `data/pmus/reward_shadow/shadow_mix_20261002T001535Z.jsonl.gz`,
00:15:35Z → 01:15:35Z, 4 markets, 9,312 successful samples.

## Headline

**The advancement question is NOT answered, and the reason is a defect in my
own sampling design rather than anything the data says about PMUS.**

The thin-side states did persist for the full hour. But the hour I sampled is
a **dormant pre-game window**, and for the `live` programs that produce the
large numbers the scoring-relevant seconds are game-time — the sampled games
are on 2026-10-03 and 2026-10-04. I averaged over a quiet slice of the period
and would have reported $67–$465 as a period reward on that basis.

## Three defects found, two of them mine

**1. The books were frozen. 6 to 13 distinct states in ~2,300 samples.**
The score percentiles came back identical to twelve significant figures
(p10 = p50 = p90 = 6.38029701647), which is what exposed it. A book that does
not move for an hour is not a persistent opportunity; it is an untraded
market. The per-second sampler was built precisely to tell persistence from
transience and it did — but what it measured is dormancy.

| market / program | samples | distinct states | qualifying-time fraction |
|---|---|---|---|
| `tsc-cfb-ala-mspst-…-total-52pt5` | 2,320 | **7** | 1.000 |
| `astatc-nfl-ari-nyg-…-ptd-1pt5` | 2,409 | **6** | 1.000 |
| `astatc-mlb-…-aarnol-gte1` | 2,329 | 13 | **0.119** |
| `astatc-mlb-…-aarnol-gte2` | 2,254 | 10 | **0.000** |

**2. My analyzer scored the wrong program.** Each slug carries **two** active
programs — the NFL market runs `nfl_player_props_day_of` (pool 450, target
7,500, df 0.30) *and* `nfl_player_props_live` (pool 1,350, target 2,000,
df 0.35). `params()` returned whichever appeared first, so the parameters
used were arbitrary between them and the reward was attributed to a program
whose pool and target were not the ones scored with. Fixed: every
(slug, program) pair is now scored separately, which is why the table below
has eight rows for four markets.

**3. 35% of my fetches were rejected: 5,084 HTTP 429 against 9,312
successes.** The documented limit is 5 req/s and I sampled 4 markets/s; the
real constraint is tighter. Coverage is therefore ~64% of seconds, and 429s
arrive in bursts rather than at random, so the qualifying-time fractions above
are computed over successfully-sampled seconds and carry an unquantified
bias. Any rerun must drop to ~2 req/s.

## What the tape does establish

**Qualification is a severe discriminator, and this is the real result.**
Two of the four markets failed the Target Size gate for 88% and 100% of
sampled seconds. A market can look richly rewarding in a snapshot and pay
nothing because its sides never both qualify. This is a live mechanism that
could explain a large part of why our account earned $0.16 per credited row,
and it is only visible with per-second sampling.

Modelled figures for the sampled (dormant) window, correct per program but
**not valid as period rewards**:

| market / program | q1 | q10 | q10 reward ÷ adverse | q10 net |
|---|---|---|---|---|
| `cfb_t1_totals_live` | $67.30 | $464.57 | 203.2 | $462.28 |
| `nfl_player_props_live` | $102.49 | $407.19 | 178.1 | $404.91 |
| `nfl_player_props_day_of` | $37.49 | $142.95 | 62.5 | $140.66 |
| `cfb_t1_totals_day_of` | $4.49 | $30.97 | 13.5 | $28.69 |
| `mlb_wc_player_props_live` (gte1) | $1.12 | $9.60 | 4.2 | $7.32 |
| `mlb_…_day_of` (gte2) | $0.00 | $0.00 | 0.0 | −$4.25 |

Adverse selection binds on the **transferred** basis ($0.2286/contract) in
every row, because the observed median spread on the qualifying markets is
one cent — the spread-based estimate is $0.005/contract and never dominates.
That is itself notable: my earlier worry that these were wide, lone-touch
books was **wrong for the CFB market** (median spread $0.0100) and right only
for the NFL prop (median $0.1600).

## Reconciliation against the account-observed $7.04

| | |
|---|---|
| Credited, account-lifetime | **$7.04** (44 rows, 3 transfers) |
| Visible PAID rows | 33 of 37, **$4.00 of $4.53** — our own `rows[:50]` truncation, still **UNRESOLVED** |
| Per-row median / max | **$0.04 / $1.15** |
| Credited market types | tennis (atp/wta/setka), soccer (ucl) |
| Comparable current pool | `atp_wta_moneyline_live` **$300/market** |
| Implied historical share, best | **0.77%** of the side pool at clip ~5 |
| Implied historical share, median | **0.027%** |

**The ~400x gap decomposes and is not mysterious:** pool size (tennis $300 vs
NFL/CFB $1,350–$10,350, ~4.5x) and share (historical best 0.77% vs modelled
several percent, ~6–20x per contract). The share term is the part a
reward-optimising controller could actually move. That supports your reading
— our sports activity was not placed where PMUS pays — over "the model is
broken". **But it does not survive defect 1**: the modelled share comes from a
dormant book, and a dormant book is exactly where a share looks large and a
game-time book is where it will not.

## Answers

**Does conservative obtainable compensation exceed our measured $1.14/market
cost?** **Unknown.** Every figure that would say yes comes from the dormant
window. I will not report $462 of net against a $1.14 cost as an advancement
signal when I know the sample is from the wrong part of the period.

**Is there a path to ≥$3 average net per eligible opportunity?** **Still
open, and now better specified.** It requires the qualifying-time fraction
and the score distribution *during game-time*, which is one corrected rerun.

## What to run next — still read-only, still no orders

One rerun of the same sampler, changed in three ways:

1. **sample during an actual live game window**, not a pre-game lull;
2. **~2 req/s**, to eliminate the 429 bias;
3. **include a market whose book is demonstrably moving**, with the distinct
   state count checked early as a sanity gate rather than after the fact.

Until that runs, **no live probe**: the gate is unchanged and nothing here
passes it.
