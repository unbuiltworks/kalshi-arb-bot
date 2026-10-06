# FROZEN — L5 microstructure findings

**Frozen at commit `08f07b3df84ee3cf26366e9869166dc20d641951`** on 2026-10-06T03:27:20Z.

A git tag (`l5-microstructure-frozen-2026-10-06`) exists locally but **could
not be pushed**: this session's credentials are scoped to branch refs and the
remote returns HTTP 403 on tag pushes (the repository carries no remote tags
at all). This file is the durable marker instead, which is in any case more
useful — it is in the branch history and readable without fetching tags.

## What is frozen

| artifact | content |
|---|---|
| `L2_RESOLUTION.json` | archive resolution: whole seconds, zero duplicate (token, second) rows, event-driven, median 49 bid levels — identical on both contract lengths |
| `EVENTS_15m.jsonl` / `EVENTS_5m.jsonl` | 3,166 + 373 per-fill microstructure reconstructions; 0 chunk holes; every fill with a real block timestamp |
| `CONTROL_15m.jsonl` / `CONTROL_5m.jsonl` | non-fill observation seconds backing every movement claim |
| `BEHAVIOR_15m.json` / `BEHAVIOR_5m.json` | the eight sequential-behaviour questions, discovery separated from confirmation |
| `OPENING30_15m.json` / `OPENING30_5m.json`, `OPENING30.html` | T=0…T+30 s for all 35 windows |
| `CLIPS_*.jsonl`, `CLIP_ANALYSIS_*.json` | the **80-share clip lattice** (excess 26–30× / 31–32×) and placement-time bounds |
| `MICROSTRUCTURE_FINDINGS.md`, `OPENING30_FINDINGS.md`, `LADDER_MODEL.md`, `LIMITS.md` | the written findings and the evidence grading |

## The frozen headline claims

* Archive resolution is **whole seconds**, event-driven, zero duplicate timestamps.
* The passive clip lattice is **80 shares** (not 25/50/75); 20-grid secondary.
* Irregular fill quantities are **partial consumption** of those clips.
* **100 is a per-fill ceiling**, not an order size.
* Par pairs are **atomic, single-transaction, L5 taker on both legs**, price sum exactly 1.0000.
* First fill at **6/7/7 s** (p25/p50/p75), identical on both contract lengths.

## Corrections applied after the freeze

The freeze covers the artifacts above. One **statistic** stated in
`LADDER_MODEL.md` has since been corrected, and the correction is recorded
here rather than hidden by a silent edit:

* **"22 of 23 BTC5m windows open with an atomic pair" was wrong.** It counted
  any same-second two-sided activity. Keyed on the TRANSACTION the correct
  figures are **10 of 23 (BTC5m)** and **2 of 12 (BTC15m)**. No other frozen
  claim is affected: the pair definition, the taker-on-both-legs result, the
  volume shares and the 80-share lattice are unchanged.

## Rule

Superseding work **cites this commit** rather than silently revising these
files. Anything downstream (`PARPAIR_AND_STATE.md` and later) treats them as
input and does not re-estimate them.

Prospective S1 is untouched throughout: `441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b6fe`.
