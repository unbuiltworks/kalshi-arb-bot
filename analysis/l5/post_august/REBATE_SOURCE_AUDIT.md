# INDEPENDENT SOURCE AUDIT — the MAKER_REBATE claim

Audited `2026-10-07` on branch `claude/august-l5-forensic-replay-788cml`.
Auditor: a separate lane from the one that produced
`analysis/l5/post_august/POST_AUGUST_REPORT.md`. Nothing in that report was
taken on trust; every figure below was recomputed from the committed raw
envelopes by an independently written loader before the original code was run
for comparison.

## Profit attribution

**NONE.** Every dollar figure in this document is another trader's public
activity. Nothing here is *submitted*, *filled*, *settled* or *realized*
profit for our account. L5's `REWARD` / `MAKER_REBATE` / `TAKER_REBATE` rows
are **his** credited incentive cashflows. They are not evidence that any
equivalent program applies to us, and they are never netted into trading P&L.

## Method and read-only discipline

- No raw envelope, committed result or report was overwritten, deleted or
  rewritten. This file is the only artifact added.
- No parameter was tuned. No order was placed.
- `analysis/l5/stage6/`, `analysis/l5/measurement/`, the XR2W executor, A6 and
  the running `l5-book-shadow` collector were not touched.
- **No re-fetch was required**; the audit is satisfied entirely from committed
  evidence. For the record, local egress to the venue is blocked as documented:
  `curl https://data-api.polymarket.com/activity?...` returns
  `curl: (56) CONNECT tunnel failed, response 403`. Had a re-fetch been needed
  it would have gone through `.github/workflows/l5-post-august-harvest.yml` as
  prior lanes did.
- Frozen fee schedule verified live, not assumed:
  `src/pmus_fee_schedule.FINGERPRINT` ==
  `ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629` and
  `assert_frozen()` passes. Effective thetas at 2026-10-01: taker **0.0695**,
  maker **0.0125**. The stale 6% scalar is not in play.
  (Note: the fingerprint is a sha256 over the canonicalized `SCHEDULE` spec,
  **not** over the file bytes — `sha256sum src/pmus_fee_schedule.py` is
  `a657cbcc…` and that is not a discrepancy.)

### Independent reproduction, before touching the original code

An independently written loader over
`data/l5/raw/post_august/*.jsonl.gz`, `data/l5/sep2026/l5_activity_*_raw.json.gz`,
`data/l5/raw/sep/l5_sep23_25_daily.json` and `data/l5/ops/activity_*.jsonl`
produced:

| quantity | independent recompute | report | agree |
|---|---:|---:|:--:|
| `MAKER_REBATE` total | **$34,822.79** | $34,822.79 | yes |
| `REWARD` total | **$8,229.66** | $8,229.66 | yes |
| `TAKER_REBATE` total | **$2,799.98** | $2,799.98 | yes |
| incentive stack | **$45,852.44** | $45,852.44 | yes |
| TRADE fills kept | **437,827** | 437,827 | yes |
| notional | **$5,035,469.75** | $5,035,470 | yes |
| duplicates dropped | **10,468** | 10,468 | yes |
| collisions on differing fields | **0** | 0 | yes |

The per-day `REWARD` / `MAKER_REBATE` / `TAKER_REBATE` table in §3.1 of the
report was reproduced **line for line, all 37 rows**, to the cent.

---

## 1. ACTUALLY CREDITED vs MODELED — **VERIFIED**

**$34,822.79 is 100% credited cashflow. $0.00 of it is modeled, computed or
imputed by our code.**

| | |
|---|---|
| credited | **$34,822.79** |
| modeled / imputed | **$0.00** |

Evidence:

- **Endpoint.** `GET https://data-api.polymarket.com/activity?user=<addr>&limit=500&offset=<n>&type=MAKER_REBATE&start=<ts>&end=<ts>`, built at
  `scripts/l5_post_august_harvest.py:102-104`, with `TYPES` including
  `MAKER_REBATE` at line 57. The venue is asked for rows of that type and
  returns them; our code never synthesises one.
- **Field.** The only arithmetic is a sum of the venue's own `usdcSize`:
  `scripts/l5_post_august_report.py:123` — `usdc = float(rec.get("usdcSize") or 0.0)`.
  No theta, no price, no size-times-rate anywhere in the incentive path.
- **Field is present on every row.** Across all 82 raw incentive rows,
  `usdcSize` is present 82/82 (zero fallbacks to the `or 0.0` default), and
  `usdcSize == size` on 82/82.
- **Each row is a distinct on-chain credit.** Every kept row carries its own
  `transactionHash`; 80 kept rows, 80 distinct hashes. A representative row:

```json
{"proxyWallet":"0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68","timestamp":1789087504,
 "conditionId":"","type":"MAKER_REBATE","size":1424.6663,"usdcSize":1424.6663,
 "transactionHash":"0xf33431f584d2adf7a5f90381fbc9df8569a20422196a3a341411f9700a6e991b",
 "price":0,"outcomeIndex":999,"name":"l5Zn1bWoM8eTsK"}
```

So CLAUDE.md's rebate rule is satisfied: the figure is a credited, source-
accounted cashflow, not an assumed one, and it may be stated.

### New finding the original report did not flag

The report's §2.6 also carries a **modeled** "all-maker credit UPPER BOUND" of
**$21,552.12** for the same regime — `maker_theta` 0.0125 applied to every one
of L5's fills as if all were credited maker. The **credited** $34,822.79 is
**1.62x that modeled ceiling.**

These two numbers are clearly distinct in the report and are not conflated or
summed, so this is not a double-count. But it is substantively important and
unremarked: L5 was credited more maker rebate than the frozen published
schedule can generate from his entire observed fill set. Either L5 is on a
non-public or volume-tiered rate, or the credit base extends beyond the fills
on this tape. Either way it **strengthens** the non-transferability caveat:
we cannot even reconstruct his rate from the public schedule, let alone assume
it for ourselves.

---

## 2. IDENTITY — **VERIFIED, WITH A CORRECTION THAT STRENGTHENS IT**

**Every incentive row is unambiguously L5's, on the row itself — not by
provenance.** No row belongs to another account. No row lacks an identity
field.

Checked on all 82 raw incentive rows:

| | count |
|---|---:|
| rows with `name` == `l5Zn1bWoM8eTsK` **and** `proxyWallet` == L5's address | **82** |
| rows contradicting identity | **0** |
| rows with **no** identity field at all | **0** |
| rows missing `proxyWallet` | **0** |
| rows missing `name` | **0** |

And across the **whole** post-August corpus, via the original loader's own
diagnostics: `records_with_name_field = 459808`,
`records_without_name_field = 0`, `records_contradicting_identity = 0`.

Envelope provenance independently agrees: `params.user` is L5's address on all
1,360 envelopes, and the harvester only ever sends the address
(`ADDRESS` literal, `scripts/l5_post_august_harvest.py:54`; the handle is
recorded as `handle_never_sent`). The handle-vs-address trap the brief
describes is correctly avoided — a handle would have produced HTTP 400
`required query param 'user' not provided`, which reads as "did not trade",
and no such request was made.

**CORRECTION to the report's prose.** `POST_AUGUST_REPORT.md` §Dedupe says
*"Identity is by **provenance** … A name-only filter would discard the ~90% of
pages on which the feed omits the profile block"*, and the following section
refers to *"the provenance-admitted nameless harvest"*. **That is wrong for
this corpus.** There is no nameless harvest here: 0 of 459,808 records lack
`name`/`proxyWallet`, as the report's own table two rows above states. The
~90%-nameless caveat is inherited from the Apr–Aug canonical corpus
(`CANON_CORPUS_VALIDATION.md`) and does not apply to the post-August window.
The report therefore **understates** its own identity evidence. Corrected
here: identity on the post-August window, and specifically on all 82 incentive
rows, is **on-record and direct**, with envelope provenance as a second,
independent agreeing signal.

The ops-collector corroboration the report cites also reproduces exactly:
4,366 exact duplicates against the main harvest, 0 collisions, 5 genuinely new
rows.

---

## 3. DEDUPE — **VERIFIED**

Recomputed independently and reproduced exactly: **10,468 duplicates dropped,
0 collisions on differing fields.** Key as stated:
`(transactionHash, conditionId, outcomeIndex, timestamp, str(price), str(size), type)`.

### Is the key SUFFICIENT for cashflow rows? Yes — checked, not assumed.

The brief's concern is real in form: on all 82 incentive rows,
`conditionId == ""`, `price == 0` and `outcomeIndex == 999`. Three of the seven
key components are therefore constants, and the key reduces in practice to
`(transactionHash, timestamp, size, type)`.

It does **not** degenerate, and this was tested two ways:

- `transactionHash` is present and **unique** on every kept incentive row: 80
  kept, 80 distinct hashes, max reuse 1.
- Deliberately **removing** `transactionHash` from the key still yields 80
  distinct rows out of 80 — i.e. `(timestamp, size, type)` alone separates
  them. The key is redundantly sufficient; neither over- nor under-collapsing
  is possible here.

The report's justification for abandoning the originally prescribed
`(transactionHash, asset, timestamp)` key also checks out: `asset` is `""` on
these rows, so that key would have collapsed to `(txh, "", ts)`.

---

## 4. COVERAGE — **VERIFIED WITH CORRECTIONS**

The decisive distinction — *observed and $0.00* versus *not observed* — holds.

**All 31 `REWARD`-zero days are MEASURED zeros, not gaps.** Verified per day
against the actual queries:

| days | how `REWARD` was queried | result |
|---|---|---|
| 2026-09-01 .. 09-10 | `sep2026` harvest, **no `type` filter at all** (params: `user,limit,offset,start,end,sortBy=TIMESTAMP,sortDirection=DESC`) — so the unfiltered feed would have returned any `REWARD` row | 6 `REWARD` rows returned, **all on 09-01..09-06**; none on 09-07..09-10 |
| 2026-09-11 .. 10-07 | **184 explicit `type=REWARD` envelopes**, every one HTTP **200** | **0 rows**, every envelope |
| 2026-09-22 .. 09-23 | `sep2223` harvest, unfiltered | 0 `REWARD` rows |
| 2026-09-23 .. 09-25 | `sep2325` daily file carries an explicit `"REWARD"` key per day | `[]` per day — asked and empty |

So every one of the 37 days had a query that would have surfaced a `REWARD`
row, and 31 of them returned none. That is a measured zero. The report's §3.2
bullet 5 ("the August program's absence is a *measured zero on fetched days*")
is correct, and for October it is particularly clean: 7/7 days, explicit
`type=REWARD`, HTTP 200, zero rows.

Envelope health reproduces: `envelope_statuses = {'200': 1360}`,
`failed_envelopes = []`, `truncation_flags = []`, and
`HARVEST_SUMMARY_20261007T043152Z.json` shows `failed_envelopes: 0`,
`truncated_windows: []`, `errors: []`.

### CORRECTION 4a — "0 truncated windows" is verified for 23 of 37 days, not 37

The no-truncation guarantee comes from `harvest_window()`'s offset-ceiling
check and is recorded only in `HARVEST_SUMMARY_*.json`, which exists **only**
for this lane's own harvest — the 23 days 2026-09-11 .. 2026-10-07. The three
pre-existing sources (`l5_activity_sep2026_raw.json.gz`,
`l5_activity_sep2223_raw.json.gz`, `l5_sep23_25_daily.json`) covering
2026-09-01 .. 09-10 and 09-22 .. 09-25 carry **no truncation record**;
`data/l5/sep2026/README.md` documents the ~5,500 offset cap as a known hazard
but the files do not attest that no window hit it. The report's flat claim
*"No window hit the venue's offset cap: no pagination truncation. Every window
terminated on a short page"* is therefore **asserted beyond its evidence for 14
of the 37 days.**

Materiality: **low for the incentive conclusion, not zero for volume.** The
incentive series is structurally self-checking — exactly **one**
`MAKER_REBATE` credit and **one** `TAKER_REBATE` credit per day for **all 37
consecutive days**, with no day missing either. A truncation that silently
dropped an incentive row would show up as a hole in that sequence, and there
is none. Fill counts and notional on those 14 days remain, strictly, floors
rather than counts.

### CORRECTION 4b — "last paid 2026-09-06" is bounded below, not above

`REWARD`'s last *observed* payout is 2026-09-06, but the corpus **starts**
2026-09-01 and the canonical corpus ends 2026-08-31. Nothing here establishes
when the program began winding down, only that it had stopped by 09-07. The
report states this correctly as "last observed payout"; the brief's paraphrase
"last paid 2026-09-06" should keep the word *observed*.

---

## 5. DOUBLE COUNTING — **VERIFIED (no double counting found)**

All four mechanisms in the brief were tested explicitly.

**(a) Same credit under two stream labels — none.** Grouping all 82 raw
incentive rows by `transactionHash`: **0 hashes appear under more than one of
`REWARD` / `MAKER_REBATE` / `TAKER_REBATE`.** The three streams are disjoint
sets of on-chain transfers.

**(b) Daily and cumulative fields double-added — no.** The totals are derived
*from* the per-day map by summation, once:
`scripts/l5_post_august_report.py:1163-1171` sums `incentive_days[d][stream]`.
There is no independent cumulative accumulator that could be added alongside
it. Confirmed numerically: my per-day sums equal the reported totals exactly.

**(c) Overlapping harvest windows summed twice — the risk was real and was
correctly caught.** The brief's suspicion about the Sep 22-25 seam is exactly
where the duplicates are. Of the 82 raw incentive rows, **2 were dropped as
duplicates, and both are the 2026-09-23 seam**:

| stream | day | amount | first seen in | duplicate in |
|---|---|---:|---|---|
| `MAKER_REBATE` | 2026-09-23 | 1,347.795 | `sep2026:l5_activity_sep2223_raw.json.gz` | `sep2325:l5_sep23_25_daily.json` |
| `TAKER_REBATE` | 2026-09-23 | 54.3493 | `sep2026:l5_activity_sep2223_raw.json.gz` | `sep2325:l5_sep23_25_daily.json` |

The corpus-wide `dup_by_pair` confirms the seams are where the overlap lives:
`sep2223|sep2325` 6,101 duplicates, `sep2026|ops` 4,366, within-file 1.
80 incentive rows survive: 37 + 37 + 6, i.e. exactly one credit per stream per
paying day, for a 37-day run with no day doubled and no day missing. **The
Sep 1-10 / Sep 22-25 seams are not double-counted.**

**(d) `realizedPnl` already including commission — MOOT, and now settled.**
This repo's open question does not apply to this computation, because
**neither field exists on this feed.** A full field inventory over the corpus
returns exactly:

```
asset, bio, conditionId, eventSlug, icon, name, outcome, outcomeIndex, price,
profileImage, profileImageOptimized, proxyWallet, pseudonym, side, size,
slug, timestamp, title, transactionHash, type, usdcSize
```

There is no `realizedPnl`, no `commission`, no `fee`. The matched-pair figure
is built from `price`/`size` on TRADE rows, fees are reported only as a
separate $0 .. $110,784.47 **bound**, and the rebate is a separate credited
sum. Additionally, **0** incentive `transactionHash` values appear on any
TRADE or REDEEM row, so no trade is being re-counted as a rebate. No path
exists by which the rebate is counted twice.

---

## 6. THE COMPARISON'S VALIDITY — **NOT VERIFIED AS FRAMED**

This is the one item that does not survive. **The two totals are correct but
they are not measured over the same window, and on every like-for-like
normalization the ranking reverses.**

The report says `MAKER_REBATE` "is the **largest** incentive stream in the
post-August window ($34,822.79 vs $8,229.66 of `REWARD`)" and uses that to
argue "a larger one continued". As a statement about 37-day totals it is
arithmetically true. As a comparison of stream size it is **not valid**:
`REWARD` paid on 6 days and `MAKER_REBATE` on 37, so the totals differ by
roughly the day count, not by rate.

Same window, same basis — the 6 days `REWARD` actually paid (09-01..09-06):

| basis | `REWARD` | `MAKER_REBATE` | larger |
|---|---:|---:|---|
| total over the 6 common days | **$8,229.66** | $6,261.74 | **REWARD**, by 1.31x |
| per paying day | **$1,371.61** | $1,043.62 | **REWARD** |
| bps of same-window notional ($981,860.64) | **83.8 bps** | 63.8 bps | **REWARD** |
| per paying day, each over its own full run | **$1,371.61** (6 d) | $941.16 (37 d) | **REWARD** |

**So while it ran, `REWARD` was the bigger stream on every normalization —
per day, per dollar of notional, and in absolute terms over the shared
window.** `MAKER_REBATE` wins the 37-day total only by outliving it.

**Corrected statement.** `MAKER_REBATE` is not "the larger stream". It is the
**surviving** stream, and it is the larger *cumulative* contributor over the
post-August window purely because it paid on 6.2x as many days. The
conclusion "L5 did not abandon the subsidy posture" does not depend on which
stream was larger — it depends only on `MAKER_REBATE` still paying — so the
conclusion stands while its stated supporting reason does not.

### Further normalization finding the report does not state: the surviving stream is decaying fast

| window | `MAKER_REBATE`/day | bps of notional |
|---|---:|---:|
| 2026-09-01 .. 09-06 | **$1,043.62** | 63.8 |
| September (30 d) | $1,070.75 | 72.9 |
| October (7 d, all observed) | **$385.74** | **42.7** |

October's maker rebate is **63% below** the early-September run rate per day,
and **41% below** it per dollar of notional — so the fall is not merely L5
trading less (notional/day fell ~38%, from $146.8k to $90.3k). The peak was
2026-09-13 at $2,470.86; the last observed day, 2026-10-07, is $446.27. The
subsidy that survived is shrinking on both bases. The report's "the subsidy
mix changed; the policy did not" is accurate about the policy but presents the
surviving stream as steady when it is in decline.

---

## The lane's own stated corrections

### (a) "$160,793 is ~3.5x the credited incentive stack $45,852" — **VERIFIED**

$160,792.82 / $45,852.44 = **3.507x**; inverse **0.285x**, reported as 0.29x.
Both recomputed from source and correct. `matched_locked_ex_fee_ex_incentive_usd`
contains no incentive term — confirmed in code
(`scripts/l5_post_august_report.py:368,399`) and by the field inventory above,
which shows the matched figure cannot be contaminated by a rebate field that
does not exist. The lane's self-correction of an earlier draft that claimed
incentives were the larger term is itself correct.

The accompanying honesty also holds: this 3.5x is **not** a profit claim, since
components 2 (residual P&L) and 3 (spread capture) are `UNAVAILABLE` and
component 6 (fees) is a bound of $0 .. $110,784.47 — 69% of the ex-fee lock.

### (b) Above-$0.96 completion presented as INFERENCE, not measurement — **VERIFIED WITH A CORRECTION**

Correct where it matters. §3.3 states it plainly: *"The claim 'only a maker
credit explains above-$0.96 pair completion' is an **inference from pricing**,
not a measurement of L5's liquidity role. `execution.isAggressor` does not
exist; `CHAIN_TOPIC_POSITION` is not a role flag."* §2.5 repeats that the fee
columns are **bounds**, not measurements. The underlying pricing facts — median
pair cost 0.9668, 46.2% of 7,020 pairs at or below $0.96 — are genuine
measurements and are labeled as such. The distinction is drawn correctly.

**Correction:** §3.5 then drops the hedge, stating as fact that *"the economics
that make that rational **are** a credited maker rebate"*. That sentence asserts
causation the tape cannot support, and it is the sentence a reader is most
likely to carry away. It should read *"are plausibly a credited maker rebate —
an inference from pricing, since no liquidity-role field exists on this tape"*.
The audit's §6 finding sharpens the point: the matched-pair leg locks ~3.32
cents per pair-share before any subsidy and is 3.5x the whole incentive stack,
so above-$0.96 completion does **not** require a rebate to be rational. The
rebate is one sufficient explanation, not the demonstrated one.

---

## VERDICT TABLE

| # | item | verdict |
|---|---|---|
| 1 | credited vs modeled | **VERIFIED** — $34,822.79 credited, $0.00 modeled |
| 2 | identity | **VERIFIED WITH CORRECTIONS** — stronger than claimed; report's "nameless/provenance-only" prose is wrong for this window |
| 3 | dedupe | **VERIFIED** — 10,468 / 0 reproduced; key sufficient and redundantly so on cashflow rows |
| 4 | coverage | **VERIFIED WITH CORRECTIONS** — measured zeros confirmed on all 31 days; "0 truncated windows" proven for 23 of 37 days only |
| 5 | double counting | **VERIFIED** — none; seam overlap found and correctly dropped; `realizedPnl` concern moot (field absent) |
| 6 | comparison validity | **NOT VERIFIED AS FRAMED** — different windows; `REWARD` was larger per day and per notional while it ran |
| (a) | 3.5x matched-pair vs incentives | **VERIFIED** — 3.507x |
| (b) | above-$0.96 as inference | **VERIFIED WITH CORRECTIONS** — correct in §3.3, overstated in §3.5 |

## Corrected figures

No credited dollar figure changed. Everything in the per-day table and all
three stream totals reproduced to the cent. The corrections are to **claims**,
not to amounts:

| claim | was | corrected to |
|---|---|---|
| `MAKER_REBATE` is "the **largest** incentive stream" | larger | **the *surviving* stream**; largest only cumulatively, on 6.2x the days. Same-window: `REWARD` $8,229.66 vs `MAKER_REBATE` $6,261.74 — `REWARD` larger by 1.31x, and larger per day ($1,371.61 vs $1,043.62) and per notional (83.8 vs 63.8 bps) |
| the surviving stream carried the economics | implied steady | **in decline**: $1,043.62/day and 63.8 bps early Sep -> $385.74/day and 42.7 bps in October |
| identity rests on provenance; ~90% of pages nameless | provenance-only | **on-record**: 459,808/459,808 rows carry `name` and `proxyWallet`; 82/82 incentive rows do; 0 nameless, 0 contradicting |
| "no window hit the offset cap" (all 37 days) | all 37 days | **23 of 37 days** attested; 09-01..09-10 and 09-22..09-25 carry no truncation record, so their fill counts are floors |
| above-$0.96 completion **is** explained by a maker rebate (§3.5) | assertion | **inference**; the ex-incentive pair leg is 3.5x the incentive stack, so no rebate is required for the pricing to be rational |
| (unremarked) credited maker rebate vs modeled ceiling | not stated | credited **$34,822.79** is **1.62x** the frozen schedule's own all-maker-credit upper bound **$21,552.12** — L5's rate is not reconstructible from the public schedule |

## Does the "L5 still harvests a maker rebate" conclusion survive?

**Yes. It survives, and on better evidence than the report claimed — but one of
its two stated supporting reasons does not, and the conclusion needs
re-wording.**

What survives, firmly:

- `MAKER_REBATE` is **credited**, not modeled: 37 venue-returned cashflow rows,
  each with its own on-chain `transactionHash`, summed on the venue's own
  `usdcSize`. $0.00 imputed. CLAUDE.md's rebate rule is met.
- The rows are **L5's**, by on-record `name` and `proxyWallet` on 82/82 rows,
  corroborated independently by envelope provenance.
- It paid on **every one of 37 consecutive days through 2026-10-07**, including
  all 7 fully-observed October days, with no day doubled and no day missing.
- `REWARD`'s cessation is a **measured zero on fetched days**, not a coverage
  hole — 184 explicit HTTP-200 `type=REWARD` envelopes returning nothing, plus
  an unfiltered feed over 09-01..09-10 that would have shown any such row.

What does not survive: the claim that the surviving stream is "the larger" one,
and the §3.5 assertion that a maker rebate **is** what makes above-$0.96
completion rational.

Re-worded conclusion, as the evidence actually supports it:

> L5 is still being credited an account-level maker rebate — $34,822.79 over
> 37 consecutive observed days through 2026-10-07, every dollar of it credited
> rather than modeled, while the market-level `REWARD` program paid a measured
> $0.00 after 2026-09-06. The subsidy did not end; it changed programs. But the
> surviving program is **smaller per day and per dollar than the one it
> replaced** ($941 vs $1,372 per paying day) and is **decaying** ($1,044/day
> and 63.8 bps in early September to $386/day and 42.7 bps in October). And
> the subsidy is **not** the dominant term in L5's observed economics: the
> ex-fee matched-pair lock of $160,792.82 is ~3.5x the entire $45,852.44
> credited incentive stack.

### Effect on the downstream judgement

**"Not safe to copy" stands, but the reason must change, and it gets stronger,
not weaker.**

The original reasoning was: L5's pricing sits above the fee-safe line *because*
a maker rebate pays for it; we have no such rebate; therefore do not copy. The
first clause is an inference the tape cannot support, and §2.6's own arithmetic
partly undercuts it — the pair leg alone locks ~3.32 cents per pair-share and
dwarfs the subsidy.

The durable reason is simpler and is a measurement rather than an inference:

1. L5 receives a **credited rebate we have not source-confirmed for our own
   account at any rate**, and we cannot even reconstruct his rate — his credited
   $34,822.79 is **1.62x** what the frozen published schedule's maker theta can
   generate from his entire observed fill set. Copying his pricing means
   copying it without an economic term we can neither obtain nor size.
2. Whether his pair leg survives fees at all turns on an **unobservable**
   maker/taker split: the ex-incentive book keeps between ~$160,793 (all maker)
   and ~$50,008 (all taker). This tape cannot distinguish those worlds.

Therefore: spring discipline's `<= 0.96` completion target should **stay as
it is**, and near-$1 pair tolerance should **stay locked**. No threshold was
fitted or tuned to anything in this audit, and nothing here is realized profit
for us.
