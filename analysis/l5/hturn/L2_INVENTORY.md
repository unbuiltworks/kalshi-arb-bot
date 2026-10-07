# L2_INVENTORY — every candidate two-token book source, and what it actually covers

**HISTORICAL REPLAY.** Nothing below is shadow, submitted, filled, settled or
realized. L5's cash is never our profit. No git command was run; no
credential, order or signing path was touched; `analysis/l5/s3/**`,
`live_exec*`, `deploy/`, `scripts/l5_exec*` and every Railway variable and
service were left alone (a live authorized order probe is running there).

Searched exhaustively with `rg`/`find` over `analysis/l5/historical_l2/**`,
`analysis/l5/live_tape_*/**`, `data/**` (including `data/l5/raw/**`,
`data/zenodo_22084977/**`, `data/pmus_book_tape/**`, `data/policy_d_port*/**`,
`data/transfer_backtest/**`), `analysis/pmus/**`, `analysis/l5/band_semantics/**`,
`analysis/l5/l5_postfill_crossvenue.json`, `analysis/l5/l5_preentry_gradient.json`,
every `*.parquet` in the worktree, and `src/pmus_book_tape.py` and its manifests.

---

## 0. The headline correction

`analysis/l5/complete_vs_extend/FORENSIC.md` §4 states the only L2 on disk
(Zenodo 22084977, BTC15m 2026-08-21..24) "overlaps the canonical sequence in
**ZERO** windows". That is true **of the canonical scorecard corpus**
(`l5_canonical_sequence.csv.gz`, 2026-03-27..04-21 + 08-28..31) and it is the
reason the forensic stopped. It is **not** true of L5's fills on disk:
`data/l5/raw/activity_gap.jsonl.gz` already holds L5's own activity for exactly
those days, which `analysis/l5/BOOK_ACQUISITION.md` §2.2 noted and nothing had
yet used for the turn question.

**The usable overlap is therefore real and measured, not zero:**

| | measured |
|---|---:|
| book markets in L5's coins (btc, eth), **both outcome tokens present** | **184 / 184** |
| L5 BUY fills inside those markets (exact-identity de-duplicated) | **3,257** |
| markets L5 actually traded in the overlap | **65** |
| transitions built | **3,192** |
| transitions with `prev_rel == extend` (the decision cohort) | **1,675** |
| of those, **EXTEND→COMPLETE — the TURN** | **430** |
| of those, EXTEND→EXTEND — the **matched control pool** | **1,245** |
| base rate `P(turn \| prev_rel==extend)` | **0.2567** |
| rows where **both** token books are readable at the decision instant | **1,663 / 1,675 (99.3%)** |

Every one of the 430 turns and 1,245 controls falls inside
**`REGIME_2026-08-19_ON`** — the forward-relevant cohort, and the one where
`analysis/l5/residual_frontier/OOS_RESULT.md` §3 finds L5's ex-incentive
baseline **negative** (−$5,851, −30.4 bp). The regime block and the pooled
block are therefore numerically identical, which is stated rather than
duplicated as if it were a second population.

L5 fills by day in the overlap: **08-21 → 2,302 · 08-22 → 926 · 08-24 → 29**
(none on 08-23; `ZENODO_OVERLAP_FINDINGS.md` §1 establishes independently from
the chain that this is a real absence — L5 stopped trading BTC15m after
~08-22 03:30Z).

By contract: **15m 3,074 · 5m 184**. The 5m slice was the "never analysed"
gain flagged as Rank 1 in `BOOK_ACQUISITION.md` and is now joined.

---

## 1. The inventory

Grades: **BOOK** = resting liquidity; **FILLS-ONLY** = prints, never a book.

| # | source | venue | tokens | span | resolution | L5 EXTEND→COMPLETE covered | verdict |
|---|---|---|---|---|---|---:|---|
| 1 | `data/zenodo_22084977/updown_15m.parquet` (239,310 rows, 265 mkts) | Polymarket international CLOB | **BOTH** (`outcome` ∈ {Up, Down}, full aligned ladders + `best_bid`/`best_ask`/`crossed`) | 2026-08-21 00:14Z → 08-24 12:29Z | on-change, ~1 s; 60 s REST resync | **398** | **USED — the test** |
| 2 | `data/zenodo_22084977/updown_5m.parquet` (167,941 rows, 471 mkts) | same | **BOTH** | 2026-08-21 00:15Z → 08-24 11:44Z | same | **32** | **USED — the test** |
| 3 | `data/zenodo_22084977/updown_4h.parquet` | same | BOTH | same | same | **0** | L5 never trades 4h (`BOOK_ACQUISITION.md` §2.2); file not even on disk here |
| 4 | `analysis/l5/historical_l2/bbo_offset.json` (305 KB) | derived from #1 | BOTH | same 3.5 days | same | — | **derived, not a source.** Maker-fill BBO offsets only; no relation sequence, no turn labels |
| 5 | `analysis/l5/historical_l2/pre_fill_complement.json` | derived from #1 | BOTH | same | same | — | **NON_DISCRIMINATING by its own §7**: liquidity exists at 1,175/1,176 prices, so "size at price q" is a constant. Reused only as the warning not to repeat it |
| 6 | `analysis/l5/historical_l2/mint_match.json` | derived from #1 | BOTH | same | same | — | derived. Source of the mint-sum feature's definition |
| 7 | `analysis/l5/august_replay/l5_backfill.json` | Polygon chain logs, joined to #1 by token id | n/a | #1's 33 BTC15m windows | per-block | 3,166 fills, **with `l5_role`** | **USED — role only.** FILLS-ONLY |
| 8 | `analysis/l5/microstructure/l5_backfill_5m.json` | same | n/a | #2's 62 windows | per-block | 373 fills, with `l5_role` | **USED — role only.** FILLS-ONLY |
| 9 | `data/l5/raw/activity_gap.jsonl.gz` (706,500 recs, 130 days) | Polymarket data-api | n/a | 2026-04-02 → 08-28 | 1 s | the fill tape itself | **USED — the sequence.** FILLS-ONLY |
| 10 | `data/l5/raw/activity.jsonl`, `trades.jsonl`, `trades_takeronly.jsonl` | same | n/a | 2026-03-19 → 04-21, 08-28..31 | 1 s | — | FILLS-ONLY, and **no book exists for its days at all** |
| 11 | `analysis/pmus/evidence/AUTH_BOOK_FIRST_LIVE_WINDOW.json` | **Polymarket US** | **ONE** token (3 states, bid/ask/qty) | 2026-09-23 22:30Z, one market | ~100 s apart | **0** | wrong venue, after L5's period, one token, 3 observations |
| 12 | `analysis/pmus/evidence/auth_book_tape.jsonl`, `NO_BOOK_INVARIANT_2026-09-23.json` | **Polymarket US** | one token | 2026-09 | — | **0** | wrong venue, no L5 activity |
| 13 | `data/pmus_book_tape/*` + `src/pmus_book_tape.py` (34,142 rows) | **Polymarket US** | one token per row | 2026-09-17 14:14Z → 17:17Z | sub-minute | **0** | our own prospective recorder, wrong venue, after the period |
| 14 | `data/pmus/sports_incentives_2026-09-10/depth.json` | **Polymarket US** | — | 2026-09-10 | — | **0** | wrong venue, wrong period |
| 15 | `data/policy_d_port/*.jsonl` (47 files), `data/policy_d_port_ext/*` (17) | **Kalshi** (`KXBTC15M`, `KXETH15M`) | Kalshi yes/no prints + windows | 2026-08-30 → 09-01 | event | **0** | wrong venue entirely; spans the burned holdout but carries no Polymarket token |
| 16 | `data/transfer_backtest/gemini_bbo_btc{15m,05m}.jsonl.gz` | **Gemini** | Gemini BBO | — | — | **0** | different venue's own instrument, not a Polymarket outcome token |
| 17 | `analysis/l5/live_tape_2026-09-13/r5ft_live_tape.jsonl` (189 rows) | our own session | none | 2026-09-13 | session events | **0** | our session log: `session_start/selection/risk_set/settlement`. No book rows |
| 18 | `analysis/l5/band_semantics/{band_rows,band_arms}.json` | derived | none | — | — | **0** | band labels over the fill tape, not a book |
| 19 | `analysis/l5/l5_postfill_crossvenue.json` | derived | none | 527 BTC15m windows | aggregate | **0** | window-level census (complement rate 0.9602, median seconds-to-first-complement 26.0, median effective pair cost 0.9624). **Aggregates, no per-instant book** |
| 20 | `analysis/l5/l5_preentry_gradient.json` | derived | none | 526/956/702 windows | aggregate | **0** | slice rates only |
| 21 | `analysis/l5/sync_l2/FIRST_WINDOWS_READING.md` (git history only, not read) | Polymarket international | — | 2026-10-01 | ms | **0** | prospective, after the period. Not retrieved — no git command was run |
| 22 | `l5-live-habitat` streams `pmus_books` / `pmus_flow` / `intl_books` / `intl_flow` | Railway services | — | 2026-09/10 live | live | **0** | **NOT INSPECTED BY DESIGN.** Reaching them means touching Railway while a live authorized order probe runs. Prospective anyway: they cannot contain 2026-08 L5 fills |
| 23 | `clob.polymarket.com/orderbook-history`, Zenodo, Marketlens, commercial vendors | Polymarket | BOTH | would cover 162 missing days | 1 s / tick | **unmeasured** | **UNAVAILABLE from this container.** `BOOK_ACQUISITION.md` §4: every bulk-egress host answers **403 to CONNECT**, including `zenodo.org` which worked on 2026-10-02..04. Policy denial, not misconfiguration. Not retried |

**Honest total of usable two-token book evidence: sources #1 + #2 only.**
3.5 of the 166 span days (**2.1%**), **430 EXTEND→COMPLETE transitions** out of
the 3,156 the forensic's holdout cohort contains — **13.6%** of the turn
population, on **different days** from the burned 2026-08-30..31 holdout.

That is small. It is also, per the brief, a small-N direct test of the decisive
variable rather than a large-N test of the wrong one — and it is large enough
to put a window-clustered CI on an AUC, which is what §3 of `HTURN_TEST.md`
does.

---

## 2. Join provenance

- Book ↔ fills: by **`(market_slug, outcome)`**, which is a bijection onto
  `token_id` in both parquets (asserted by
  `tests/test_l5_hturn.py::test_token_id_join_is_consistent_with_the_slug_outcome_join`),
  so it **is** the token-id join. Never by market name alone.
- Fills ↔ role: by **`transactionHash`**. A tx carrying both roles for L5 is
  `mixed` and is excluded from the (A)/(B) split rather than coerced.
- Role field: **`l5_role`**, derived from L5's topic position in the on-chain
  CTF exchange log by `analysis/l5/historical_l2/backfill.py`, labelled
  `CHAIN_TOPIC_POSITION`. **`execution.isAggressor` DOES NOT EXIST on L5's
  public tape** — `rg isAggressor` finds it only in our own account's PMUS
  execution path (`src/pmus_settlement_auditor.py`,
  `src/pmus_l5_continuous_session.py`, `src/l5_live_probe/incentive_join.py`).
  The bare boolean `aggressor` is read nowhere; a test asserts the module
  never subscripts or attributes either name.

## 3. Accuracy bounds, carried not assumed away

A book row is written only when the book **changes**, so an old row is current
state. It is carried at most **60 s** (the collector's own REST resync
interval) and **refused** beyond that — 12 of 1,675 decision rows (0.7%) are
refused on that rule. The feed is ~24k events/s with **no sequence numbers**,
so a dropped delta is undetectable: every read carries up to one resync of
possible drift. Every read is taken strictly **at or before** its reference
instant; a snapshot taken *at* a fill already contains that fill's effect.
