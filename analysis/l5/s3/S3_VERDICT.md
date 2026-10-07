# S3 cohort verdict — run 37536917745

**VERDICT: INSUFFICIENT.** 0 of the required 25 COMPLETE-market
evaluations. Shortfall: **25**. A catastrophic-mismatch gate also fired at
market 5 and is recorded below as a *measurement failure*, per the frozen
protocol. Nothing here is realized, filled, or submitted: zero capital,
zero orders, zero signing. Every fill below is **hypothetical** and biased
adverse by construction (prereg §7).

This document evaluates the frozen preregistration as written. No
threshold was tuned, no criterion relaxed, no parameter changed, and the
running workflow was not touched.

---

## 1. Run conclusion (verified first)

| field | value |
| --- | --- |
| workflow | `.github/workflows/s3-cohort.yml` (workflow id 376426374) |
| run id | **37536917745**, run_number 3, attempt 1 |
| head sha | `4ff474cb409d5d129dfa12ffa5c4a68fb3444da4` |
| branch | `claude/august-l5-forensic-replay-788cml` |
| job | `collect-and-measure`, id 112550067790 |
| created / job start | 2026-10-06T21:52:42Z / 2026-10-06T23:20:31Z (queued ~88 min) |
| collection step | 2026-10-06T23:21:25Z → 2026-10-07T04:21:26Z (300 min, as dispatched) |
| **status / conclusion** | **completed / `success`** at 2026-10-07T04:21:36Z |

All 15 steps concluded `success`, including the three pre-collection
guards: the frozen-fingerprint check, `assert_no_execution_surface`, and
`tests/test_s3_instrument.py`.

## 2. Raw artifact uploaded and downloadable (verified second)

Uploads ran `if: always()` **before** any git operation, the fix for the
prior-run failure mode in `S3_COHORT_ARTIFACT_LOST.md`.

| artifact | id | bytes | expired |
| --- | --- | --- | --- |
| `s3-raw-tape-37536917745` | 11461205099 | 304,662 | false |
| `s3-cohort-output-37536917745` | 11461055384 | 19,767 | false |

Upload confirmed in the job log, with the uploaded zip digest:

```
Uploaded bytes 19767
SHA256 digest of uploaded artifact zip is
  05ebecf33fe478c089abcf77a6899641db77b5662fcbea56fb918b7a1df25fe1
Artifact s3-cohort-output-37536917745 ... successfully finalized. Artifact ID 11461055384
```

**Fingerprint assertion: PASSING.** The in-run assertion step
("Assert the instrument actually produced an artifact") printed:

```
artifact OK: COMPLETE=0 completed=5 halt=True
```

which it reaches only after `d['spec_fingerprint'] ==
8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df`. The
committed `S3_COHORT.json` carries that same fingerprint, and
`analysis/l5/s3/spec.py` in the working tree still computes it. The
preregistration's frozen fingerprint is identical. No drift.

**Limitation, stated honestly:** the artifact *zip bytes* could not be
downloaded from this session — egress to
`productionresultssa10.blob.core.windows.net` is denied by proxy policy
(`connect_rejected`). The artifact's existence, size, non-expiry and
upload digest are verified via the Actions API and job log; its content
is verified through the byte-identical copy committed in §3, not through
an independent download. A fully independent artifact-byte check remains
unperformed.

## 3. Push verified and an ancestor of the branch head (verified third)

```
[claude/august-l5-forensic-replay-788cml 2359623] data(s3): ... [skip ci]
 2 files changed, 28345 insertions(+)
 create mode 100644 analysis/l5/s3/S3_COHORT.json
 create mode 100644 data/s3_cohort/pmus_btcupdown15m_20261006T232125Z.jsonl
Rebasing (1/1) ... Successfully rebased
   e6a2d11..5cec58c  HEAD -> claude/august-l5-forensic-replay-788cml
verified on remote: 5cec58ccc414c920504fc091ef4c302f986ad8a4
```

Independently re-verified from this checkout after a fresh fetch:

- data commit `5cec58ccc414c920504fc091ef4c302f986ad8a4`
- `git merge-base --is-ancestor 5cec58c origin/<branch>` → **true**
- branch head at verification time `0ec22424f3f4cadfa3441fa6b77b04746730b1c7`
  (later commits by other writers on this branch; the data commit is
  reachable beneath it)
- tape: `data/s3_cohort/pmus_btcupdown15m_20261006T232125Z.jsonl`,
  16,353 rows; `analysis/l5/s3/S3_COHORT.json`, 11,992 lines

The race that destroyed run 37469519509 (four non-fast-forward pushes
recorded as green) did occur again here — the fetch moved
`5d24217..e6a2d11` — and the rebase absorbed it on the first attempt.
Green CI in this run does **not** mean the evidence disappeared
successfully: the evidence exists in two independent places.

(b) and (c) both hold, so the preregistration is evaluated below.

## 4. The frozen preregistration and its own criteria

Prereg: `analysis/l5/s3/PREREGISTRATION_S3.md`. Frozen spec fingerprint
`8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df`
(`analysis/l5/s3/spec.py`; `spec_commit` recorded in output as `28c619df`).

Quoted verbatim, §9 "Cohort size and the catastrophic-mismatch gate":

> **Cohort: 25 BTC15m markets**, contiguous, not cherry-picked. The cohort
> may **not** be extended to reach a metric, and results may not be read
> before the 25th market closes — with one exception, the gate below.
>
> **Catastrophic-mismatch gate**, evaluated every market. Its purpose is to
> abort early if the *instrument is broken*, **not** to peek at economics.
> Abort collection if any of:
>
> | condition | threshold |
> | orders never evidenced across the first N markets | N = 5 |
> | markets with two-sided evidenced acquisition | < 20 % |
> | share of records with queue-ahead unobservable | > 50 % |
> | share of BBO staleness breaches | > 10 % |
> | reservation violations | > 0 |
> | any order submitted or signed | > 0 |
>
> A gate breach **halts collection and is reported as a measurement
> failure. It is never grounds for retuning a placement parameter and
> re-running.**

And §10:

> A positive result authorizes **nothing beyond the next named stage of
> review**. ... A negative result is a complete answer and is to be
> reported as one.

COMPLETE-market definition, disclosed at runner level (`cohort.py`) and
explicitly *not* a frozen-spec parameter:

> RUNNER-LEVEL criterion, not a frozen-spec parameter: span >= 840s of the
> 900s window AND no observation gap > 60s. Partially observed markets are
> reported but do not count toward the 25.

## 5. Counts, exactly as measured

| quantity | value |
| --- | --- |
| cohort required | **25** |
| `cohort_completed` (markets processed) | **5** |
| **`markets_COMPLETE`** | **0** |
| shortfall against the 25-market requirement | **25** |
| `cohort_ready_to_evaluate` | **false** |
| gate `halt` | **true** |
| gate breach | `two_sided_evidenced_acquisition_below`, threshold 0.20, observed **0.0** |
| orders submitted / signed | **0 / 0** |
| reservation stand-downs | 0 in every market (reservation violations: 0) |
| tape rows | 16,353 |

Per market (all five `market_COMPLETE: false`, all
`complement_never_observed: true`):

| market | snapshots | span_s | max gap_s | M1 hypothetical fills | M2 two-sided | M3 matched | M4 residual | M5 pair cost | orders | stand-downs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| …2026-10-06-2315z | 246 | 537.1 | 4.6 | 4 | false | 0.0 | 80.0 | None | 10 | 40 |
| …2026-10-06-2330z | 328 | 719.1 | 6.0 | 4 | false | 0.0 | 80.0 | None | 11 | 152 |
| …2026-10-06-2345z | 280 | 613.3 | 4.9 | 4 | false | 0.0 | 80.0 | None | 7 | 102 |
| …2026-10-07-0000z | 357 | 787.2 | 5.3 | 4 | false | 0.0 | 80.0 | None | 5 | 263 |
| …2026-10-07-0015z | 308 | 679.8 | 5.3 | 4 | false | 0.0 | 80.0 | None | 4 | 253 |

M8 capital tied up (hypothetical): 26.2–54.8 USDC. M10 hypothetical fees
0.707–1.271, accounted separately; `M10_rebates_counted: false`.

## 6. Verdict and reasoning

**INSUFFICIENT.**

1. The standing requirement is **≥ 25 COMPLETE-market evaluations**. The
   run produced **0**. Shortfall **25**. The prereg forbids reading the
   cohort before the 25th market closes, and the output itself records
   `EVALUATION_HELD`. No PASS or FAIL on S3's substantive question is
   available, and none is asserted here.
2. Independently, the catastrophic-mismatch gate fired at market 5 on
   two-sided evidenced acquisition **0.0 % vs a < 20 % abort threshold**.
   Per §9 this is reported as a **measurement failure** — the instrument
   is not obtaining the input it needs — and is explicitly not grounds for
   retuning a placement parameter and re-running. No parameter was tuned.
3. Therefore S3's question ("does current PMUS BTC15m provide enough clean
   maker execution…") is **not answered either way** by this run. This is
   not a negative result about opportunity; it is an absence of admissible
   measurement.

## 7. What looked wrong

Flagged, not fixed — all are instrument/acquisition concerns, and none
licenses a parameter change:

1. **The complement side was never observed.** All five markets report
   `complement_never_observed: true`, which is the direct cause of
   M2 = 0.0 and of the gate breach. A two-sided instrument fed a one-sided
   tape cannot produce matched inventory: M3 = 0.0 and M5 = `None`
   everywhere, and M4 sits pinned at the 80.0 residual cap. The gate
   caught exactly what it was written to catch — a broken instrument, not
   an economic finding. The fix belongs in the collector's token coverage.
2. **No market can reach COMPLETE with the current collector.** Observed
   spans are 537–787 s against the 840 s criterion for a 900 s window;
   max gaps were a healthy 4.6–6.0 s, so this is truncation at the window
   edges (markets entered mid-life), not dropout. At this span distribution
   `markets_COMPLETE` would stay 0 however long collection runs — a
   ~5-hour read-only run yields 0 admissible evaluations. The 25-market
   requirement is currently unreachable by construction. Recording this,
   not relaxing the 840 s criterion.
3. **Only 5 of ~20 windows in the tape were processed**, because the gate
   halted the cohort at market 5. Correct per spec; noted so the 16,353-row
   tape is not mistaken for 5 markets' worth of data.
4. **Tape size dropped ~50×** versus the lost run 37469519509 (304,662
   bytes vs a reported 15.46 MB tape / 8,071 cycles / 16,160 rows), on a
   comparable 300-minute collection. Row count is similar (16,353), so
   this is plausibly per-row payload shrinkage — consistent with the
   missing complement side. Unexplained, and worth a data-quality check
   before the next collection.
5. **`bbo_staleness_breach_share` is `None` in all five markets**, so the
   gate's "> 10 % BBO staleness breaches" condition was evaluated against
   an absent measurement rather than a measured 0. `bbo_freshness_bound_s`
   is 2.0. Not a breach, but one gate condition is effectively unexercised.
6. The branch-writer race recurred (`5d24217..e6a2d11` arrived mid-run).
   The rebase handled it. Left as-is.
7. Node 20 deprecation warning on `checkout@v4`, `setup-python@v5`,
   `upload-artifact@v4`. Cosmetic.

**Could not verify:** artifact zip bytes by independent download (proxy
egress denied, §2); whether the collector's one-sided coverage is a venue
behaviour or a collector defect (that requires reading the tape, outside
this verdict's scope); and anything about settlement, since nothing was
ever submitted.
