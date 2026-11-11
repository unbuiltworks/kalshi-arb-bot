# Does contemporaneous two-sided flow predict completion?

_OWNER P0 (#167). Read-only. The retro and forward questions are answered in different currencies and neither substitutes for the other._

## What cannot be measured retroactively

PMUS serves no historical print endpoint, only a CUMULATIVE `sharesTraded`. For a settled market that yields its lifetime total and nothing about what traded while we sat in it. The contemporaneous comparison is therefore a FORWARD test only.

## Test 1 (retro): lifetime volume

Lifetime volume does NOT separate the cohorts. Medians are indistinguishable (completed 159,272, stranded 144,559, difficulty 98,471) and the extremes run the wrong way: the quietest market we traded completed in full at 25,034 shares while the loudest stranded one held 511,234. A rule built on headline volume would have kept every market that stranded.

| cohort | n | median | quietest | loudest |
|---|---:|---:|---|---|
| FULL | 21 | 159272 | 25034 `aec-cs2-mouzn-bmb-2026-09-08` | 9094164 `aec-atp-fratia-alemic-2026-09-07` |
| PARTIAL | 3 | 98471 | 62545 `aec-setkamecz-tomvoj-ticjir-2026-09-07` | 233201 `aec-atp-pedmar-frafor-2026-09-08` |
| ZERO | 7 | 144559 | 10672 `aec-atp-grilom-filpel-2026-09-07` | 511234 `aec-wta-martre-jenrug-2026-09-08` |

**no threshold separates them: the quietest market that completed is quieter than the loudest market that stranded**

## Test 2 (forward): contemporaneous flow

Contemporaneous flow IS structured. Across 41 polls of the live slate, two-sidedness rises monotonically with first-differenced shares/minute -- 0%, 0%, 0%, 67%, 100% by quintile -- and only 18 of 60 live markets had any measurable flow at all. This measures the VENUE. Whether it predicts OUR completion is the pre-registered forward test, not a conclusion available from this data.

| quintile | markets | min sh/min | max sh/min | two-sided |
|---|---:|---:|---:|---:|
| Q1 | 3 | 0.00 | 0.00 | 0% |
| Q2 | 3 | 0.00 | 0.11 | 0% |
| Q3 | 3 | 0.75 | 3.77 | 0% |
| Q4 | 3 | 5.46 | 36.34 | 67% |
| Q5 | 3 | 39.01 | 190.19 | 100% |

Slate coverage: 60 polled, 18 measurable, 42 under-observed (no usable interval), 5 measured but silent.

## The forward gate, registered before the cohort runs

- **Rule:** admit only markets in the top two flow quintiles AND two_sided true, measured over at least 10 intervals before admission
- **Pass:** stranded share strictly below 7/31 AND complete fill rate at or above the FULL cohort's 23.1%, over at least 15 markets with a first-side fill
- **Fail:** stranded share at or above 7/31, or fill rate below 23.1%
- **Inconclusive:** fewer than 15 markets with a first-side fill
- **Confound named in advance:** the entry-band fix (a9b0b243) landed between the baseline and any forward cohort, so a forward run measures flow selection AND that fix together. Attributing the delta to flow alone requires either a same-period control arm without the flow filter, or accepting the combined claim and saying so.
- passing this gate is evidence for a selection rule, not authority to increase size, concurrency or capital.

Labels: retro = lifetime cumulative volume; the weak measure, reported because it is the only one the venue can serve for a settled market; forward = first-differenced shares/minute over the live slate; contemporaneous, and about the VENUE, not about our fills; descriptive = cohorts are not randomly assigned; no causal claim
