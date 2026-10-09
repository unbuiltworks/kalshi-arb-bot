# README — what to upload, to which AI, in what order

Independent forensic research package on the public execution history of
Polymarket account `l5zn1bwom8etsk` — **760,817 fills across 17,046
short-dated BTC/ETH up-or-down markets, 2026-03-19 to 2026-08-31**, with
inventory reconstructed after every fill.

**This package contains no conclusion of ours.** That is deliberate. Our
own analysis is held in a separate file outside this directory
(`../verification_HOLD_BACK/OUR_HYPOTHESES_UPLOAD_LAST.md`) and must not
be uploaded in the first round — if Grok or Perplexity is told what we
already believe, its agreement stops being evidence.

Nothing here is our account, our credentials, or our infrastructure. The
subject's address is public and is the subject of the research.

---

## Round 1 — GROK: infer the decision process

Upload in this order. Stop whenever you hit the platform's file or
context limit; the ordering is chosen so that each stage is useful on its
own.

| # | file | MB | why this order |
|---|---|---|---|
| 1 | `PROMPT_GROK.md` | 0.01 | the brief. Paste as the message, or upload it first. |
| 2 | `DATA_DICTIONARY.md` | 0.01 | every field and how it was produced. Includes the `outcome_index` trap. |
| 3 | `LIMITS_OF_PUBLIC_FILLS.md` | 0.01 | what the data cannot show. Prevents confident wrong answers. |
| 4 | `CORPUS_PROFILE.json` | 0.01 | descriptive distributions, for orientation. |
| 5 | `case_studies/CASE_STUDIES_ALL.md` | 1.1 | 29 complete windows, every fill with inventory. **This is where the reasoning should start.** |
| 6 | `dataset/l5_window_summary.csv.gz` | 1.4 | all 17,046 windows, one row each. |
| 7 | `dataset/l5_fills_enriched_SAMPLE.csv.gz` | 6.1 | 56,154 fills — whole windows, every 14th, spanning the full period. |
| 8 | `dataset/fills/l5_fills_enriched_part01…part14` | 5.1–6.0 each | all 760,817 fills, chronological. Load as many as fit. |

**If only a few files fit:** 1, 2, 3, 5, 6. That alone supports a serious
answer — the case studies carry the sequential texture and the window
summary carries the population.

**If you want depth over breadth:** add parts 1–4 (early regime) and
11–14 (late regime) rather than parts 1–8, so both ends of the period are
represented. The corpus is heavily weighted to August and a prefix-only
load will quietly become an August-only analysis.

## Round 1 — PERPLEXITY: investigate the explanatory space

Perplexity is being asked a *research* question, not a pattern-inference
question, so it needs less data and more context.

| # | file | MB | why |
|---|---|---|---|
| 1 | `PROMPT_PERPLEXITY.md` | 0.01 | the brief. Paste as the message. |
| 2 | `LIMITS_OF_PUBLIC_FILLS.md` | 0.01 | **early on purpose** — several of its discriminating tests live or die on this. |
| 3 | `DATA_DICTIONARY.md` | 0.01 | field semantics and provenance. |
| 4 | `CORPUS_PROFILE.json` | 0.01 | the distributions behind the brief's summary numbers. |
| 5 | `case_studies/CASE_STUDIES_ALL.md` | 1.1 | so it can see the texture rather than trust our summary. |
| 6 | `cashflow.csv.gz` | 1.5 | the REDEEM / rebate / reward records, including the 203 unattributable ones. Central to its incentive questions. |
| 7 | `dataset/l5_window_summary.csv.gz` | 1.4 | all 17,046 windows. |

The full fill table is available in `dataset/fills/` but Perplexity's
questions are mostly answerable without it.

**Run the two independently.** Do not paste Grok's answer into Perplexity
or vice versa. Their value is that they are not correlated.

## Round 2 — comparison, after both have answered

1. Save both answers verbatim, outside this package.
2. Then, and only then, upload
   `../verification_HOLD_BACK/OUR_HYPOTHESES_UPLOAD_LAST.md` to each,
   with the round-two prompt printed at the top of that file.
3. Take every testable claim either model produced and run it through the
   harness below. A claim that was not tested is not a finding.

---

## Testing what they propose

`scripts/50_test_hypothesis.py` evaluates a hypothesis declared as JSON
against the full 760,817-fill table, with bootstrap confidence intervals
and a permutation p-value. It refuses to report a p-value without the
effect size and n, and it never calls a prediction supported on a
confidence interval that spans zero.

```bash
# what columns can I use?
python3 scripts/50_test_hypothesis.py --dataset dataset --columns

# test one hypothesis
python3 scripts/50_test_hypothesis.py \
    --dataset dataset \
    --hypothesis hypothesis_tests/EXAMPLE_H001_pair_cost_by_regime.json \
    --ledger hypothesis_tests/hypothesis_ledger.json \
    --out hypothesis_tests/H001_result.json
```

Start from `hypothesis_tests/HYPOTHESIS_TEMPLATE.json`. Two worked
examples are included with their results, one of which comes back
`NOT_SUPPORTED` — that is the harness working, not a bug.

The `--ledger` flag matters. It counts how many hypotheses have been run
against this one corpus and reports the Bonferroni-adjusted threshold,
because the twentieth test on the same data is not the same evidence as
the first. Use it on every run.

## Rebuilding the package from the raw sources

The whole package regenerates from the two raw envelope files committed on
branch `claude/august-l5-forensic-replay-788cml`
(`data/l5/raw/activity.jsonl` and `data/l5/raw/activity_gap.jsonl.gz`).
Nothing is fetched from the network.

```bash
R=/path/to/that/checkout
python3 scripts/10_normalize_corpus.py     --source-root "$R" --out work
python3 scripts/20_reconstruct_inventory.py --work work --out dataset
python3 scripts/25_reshard.py               --dataset dataset
python3 scripts/30_case_studies.py          --dataset dataset --out case_studies
python3 scripts/40_corpus_profile.py        --dataset dataset --work work --out .
python3 scripts/60_verify_package.py        --package .
```

Stage 1 reproduces the corpus headline exactly: **784,500 admitted →
776,749 unique → 760,817 TRADE fills**, matching the independent
validation on that branch.

## Integrity

`scripts/60_verify_package.py` is the gate. It checks that every
referenced file exists; that the parts hold exactly 760,817 fills over
17,046 windows with no duplicate `(event_slug, trade_seq)` and **no window
split across a part boundary**; that every part is under the size cap;
that the window summary's slug set matches the fills' exactly; that
`matched_qty == min(cum_up_qty, cum_down_qty)` and
`effective_pair_cost == matched_spend_fifo / matched_qty` on **every
row**; that the case-study count matches its index and falls in the
requested 20–30; that no credential, private key, email address or
infrastructure identifier appears in any shipped file; and it writes
`SHA256SUMS` over everything.

It exits non-zero on any failure. Verify before uploading.

---

## Contents

```
PROMPT_GROK.md                  the Grok brief
PROMPT_PERPLEXITY.md            the Perplexity brief
DATA_DICTIONARY.md              every field, how produced, where it misleads
LIMITS_OF_PUBLIC_FILLS.md       what public fills cannot reveal
CORPUS_PROFILE.json             descriptive distributions only
NORMALIZE_REPORT.json           admission, dedupe and validation counts
settlement.csv                  per-window settled outcome and its evidence
cashflow.csv.gz                 REDEEM / rebate / reward records
SHA256SUMS                      checksums over every file

dataset/
  fills/l5_fills_enriched_part01..14_*.csv.gz   760,817 fills, enriched
  l5_fills_enriched_SAMPLE.csv.gz               56,154 fills, whole windows
  l5_window_summary.csv.gz                      17,046 windows
  INVENTORY_REPORT.json                         reconstruction reconciliation
  RESHARD_REPORT.json                           part manifest
  settlement.csv

case_studies/
  CASE_STUDY_INDEX.md           the 29 windows and why each was chosen
  CASE_STUDIES_ALL.md           all 29 in one file (upload this one)
  CASE_01..29_*.md              one file each
  CASE_STUDY_REPORT.json        selection record

hypothesis_tests/
  HYPOTHESIS_TEMPLATE.json      start here
  EXAMPLE_H001_*.json/_result.json
  EXAMPLE_H002_*.json/_result.json
  hypothesis_ledger.json        multiple-comparison bookkeeping

scripts/
  10_normalize_corpus.py        raw envelopes -> normalized fills
  20_reconstruct_inventory.py   per-fill inventory, FIFO pair costing
  25_reshard.py                 upload-sized parts, windows kept whole
  30_case_studies.py            stratified selection and rendering
  40_corpus_profile.py          descriptive profile
  50_test_hypothesis.py         hypothesis harness
  60_verify_package.py          integrity gate
```

### The fill parts

| part | file | rows | dates | size |
|---|---|---|---|---|
| 1 | `l5_fills_enriched_part01_2026-03-19_to_2026-05-05.csv.gz` | 54,932 | 2026-03-19 → 2026-05-05 | 6.0 MB |
| 2 | `l5_fills_enriched_part02_2026-05-05_to_2026-05-25.csv.gz` | 54,843 | 2026-05-05 → 2026-05-25 | 5.8 MB |
| 3 | `l5_fills_enriched_part03_2026-05-26_to_2026-06-15.csv.gz` | 54,953 | 2026-05-26 → 2026-06-15 | 5.9 MB |
| 4 | `l5_fills_enriched_part04_2026-06-15_to_2026-07-07.csv.gz` | 54,937 | 2026-06-15 → 2026-07-07 | 5.9 MB |
| 5 | `l5_fills_enriched_part05_2026-07-07_to_2026-07-21.csv.gz` | 54,988 | 2026-07-07 → 2026-07-21 | 5.9 MB |
| 6 | `l5_fills_enriched_part06_2026-07-21_to_2026-07-30.csv.gz` | 54,921 | 2026-07-21 → 2026-07-30 | 5.9 MB |
| 7 | `l5_fills_enriched_part07_2026-07-31_to_2026-08-08.csv.gz` | 54,869 | 2026-07-31 → 2026-08-08 | 6.0 MB |
| 8 | `l5_fills_enriched_part08_2026-08-08_to_2026-08-14.csv.gz` | 54,880 | 2026-08-08 → 2026-08-14 | 6.0 MB |
| 9 | `l5_fills_enriched_part09_2026-08-14_to_2026-08-17.csv.gz` | 54,950 | 2026-08-14 → 2026-08-17 | 6.0 MB |
| 10 | `l5_fills_enriched_part10_2026-08-17_to_2026-08-19.csv.gz` | 54,997 | 2026-08-17 → 2026-08-19 | 6.0 MB |
| 11 | `l5_fills_enriched_part11_2026-08-19_to_2026-08-22.csv.gz` | 54,819 | 2026-08-19 → 2026-08-22 | 6.0 MB |
| 12 | `l5_fills_enriched_part12_2026-08-22_to_2026-08-26.csv.gz` | 54,863 | 2026-08-22 → 2026-08-26 | 6.0 MB |
| 13 | `l5_fills_enriched_part13_2026-08-26_to_2026-08-29.csv.gz` | 54,998 | 2026-08-26 → 2026-08-29 | 5.9 MB |
| 14 | `l5_fills_enriched_part14_2026-08-29_to_2026-08-31.csv.gz` | 46,867 | 2026-08-29 → 2026-08-31 | 5.1 MB |
Each part is self-contained for sequential analysis: **a window never
spans two parts.** Parts are therefore not exactly equal in length.

### Two things to know before you read a single row

1. **Do not key on `outcome_index`.** 3,196 fills carry a polarity
   reversed relative to the majority, because the market's `outcomes`
   array is not consistently ordered. Use the `leg` column, which is
   derived from the outcome label. Keying on the index silently swaps the
   two legs in those windows.
2. **`size` is filled size, not order size.** A long run of small fills
   is equally consistent with one large resting order filling
   incrementally. This ambiguity is unresolved and it bears on any
   conclusion about sizing or cadence.
