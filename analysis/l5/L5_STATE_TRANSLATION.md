# Mapping B — translating sports state into the frozen L5 key

_#167 comment 5583906276. no network, no venue, no orders. The live key, the live shell, every cap, gate and band, the frozen model and the frozen lookup are untouched._

Frozen model `l5_action_model_v2.json` sha `bbb55bf90638d2bd`, 136 states — unchanged, and keyed by both mappings.

## The two translations, each derived from L5's tape alone

- time: `L5_REMAINING_FRACTION_TO_TTE_v1` sha `5c4a26cd9c123ddb` — 71975 L5 trades binned by remaining fraction of their own window
- inventory: `L5_INVENTORY_RATIO_EDGES_v1` sha `244b7ab865c9dff4`
- price: unchanged

_a translated bucket is NOT a seconds-to-settlement claim about a sports market; it says 'as far through its event as L5 was at that point of its window'._

## Pre-registered promotion gate

`L5_STATE_TRANSLATION_v1_PROMOTION_GATE` sha `6a474b81b397c2bb` — frozen any forward paired-shadow observation is read. The historical comparison in this module is descriptive and explicitly cannot satisfy this gate.

Minimums: forward_decisions_with_event_progress 400, distinct_markets 40, distinct_leagues 4, forward_fills 60

## Historical A/B — DESCRIPTIVE_ONLY

_these decisions were already observed and analysed. They show what the translation DOES; they cannot promote it, and the pre-registered gate requires decisions observed after the gate was frozen._

665 decisions, event-progress coverage 0.9263.

| | A (live mapping) | B (translated) |
|---|---:|---:|
| median state support | 234 | 83 |
| off-distribution rate | 0.0 | 0.1669 |
| unkeyable | 0.0 | 0.1669 |
| relation distance to L5 | 0.4584 | 0.415 |

Time-bucket occupancy — live: {"tte>10m": 665}; translated: {"<unkeyable>": 49, "tte2-5m": 23, "tte30-120s": 125, "tte5-10m": 299, "tte>10m": 169}

_not used in mapping B. Per the owner it remains a logged candidate feature; the evidence did not establish it as a superior clock._

## Forward cohort against the frozen gate

Decisions logged after 2026-09-08T11:07:49+00:00: **0**. Verdict: **NOT_YET_ENOUGH_FORWARD_EVIDENCE**.

| minimum | required | have | met |
|---|---:|---:|---|
| forward_decisions_with_event_progress | 400 | 0 | False |
| distinct_markets | 40 | 0 | False |
| distinct_leagues | 4 | 0 | False |
| forward_fills | 60 | 0 | False |

_REQUIRES_OWNER_DECISION -- this module never promotes and never edits the live key._
