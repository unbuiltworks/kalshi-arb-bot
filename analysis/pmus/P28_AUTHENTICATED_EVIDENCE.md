# PMUS BTC15m — authenticated evidence, the tick defect, and the band question

Read-only throughout. No PMUS order placed or prepared, no secret printed,
copied, rotated or requested. **Gemini B2 frozen** (`bfe7ba23…`, `armed False`),
Kalshi preserved, the closed P10 population untouched. The public 10-hour
collector ran in parallel throughout.

---

## 1. Authenticated recorder — **BLOCKED, and it needs your decision**

The service exists, holds the credentials, and already does the right thing.
`pmus-ws-evidence-recorder` (`71b6ea99-…`) has `PMUS_API_KEY_ID` and
`PMUS_API_SECRET_KEY` set, mounts `/state/pmus-ws`, subscribes to
`SUB_MARKET_DATA = 1` **and** `SUB_TRADE = 3`, and is structurally
mutation-incapable (`OUTBOUND_FRAME_KEYS = {subscribe, unsubscribe,
heartbeat}`, enforced at send time and by tests). Its enriched entrypoint
delegates discovery to the base module, so the `PMUS_WS_SERIES` change would
take effect there unmodified.

**What stops it: the service builds from `feature/l5-transfer-backtest`, and
that branch and mine have _no merge base at all_.** 894 commits on the deployed
branch are absent from mine; 267 of mine are absent from it; `git merge-base`
reports unrelated histories.

That rules out the two obvious moves:

- **Repointing the service's source branch to mine** would replace 894 commits
  of deployed work — including the settlement-liveness cutover, the $7.78
  accounting fix and the matched-pair-ceiling HALT fix — with an unrelated
  tree. I will not do that to a live 24/7 service.
- **Merging** is not possible across unrelated histories, and I am not
  authorized to push to `feature/l5-transfer-backtest`.

I also checked for a no-code path and there isn't a safe one: setting
`PMUS_WS_MARKETS` to a BTC15m slug works, but `discover_universe` returns
**early** on an explicit list, so it would replace the entire sports universe —
killing the evidence collection the service exists for — and the slug dies
within fifteen minutes anyway, because each window is its own market.

### What is ready for you

`deploy/pmus_ws_recorder/CRYPTO_SERIES.patch` — a self-contained patch against
the **deployed** branch's copy of the recorder. One file, one new branch in
`discover_universe`, `series_slugs` inlined (because `src/pmus_book_tape.py`
is not in that image's `COPY` set, and changing an image manifest is a bigger
change than the one being made). It sends no new frame kind, and with
`PMUS_WS_SERIES` unset the behaviour is byte-identical to today's.

I verified the patch's helper against the deployed branch's own
`pmus_sports_collector.http_get_json`: series page resolves, events resolve,
`btc-updown-15m` found.

```
on feature/l5-transfer-backtest:
  git apply deploy/pmus_ws_recorder/CRYPTO_SERIES.patch
then on the service:
  PMUS_WS_SERIES=btc-updown-15m
  PMUS_WS_DISCOVERY_SECONDS=45      # a 15-minute window needs a faster roll
```

Unsetting `PMUS_WS_SERIES` reverts to sports without a redeploy.

**Three options, and this is yours to pick:** (a) apply the patch on that
branch; (b) a separate Railway service from my branch with the same credential
references, leaving the sports recorder alone; (c) run the recorder wherever
else the credentials already live. I have not done any of them.

---

## 2. The crypto tick defect — **FIXED**

`complement_bound` rounded onto a hardcoded `TICK = 0.005`. On a market whose
`orderPriceMinTickSize` is `0.01` it could return `0.475` — arithmetically
inside the bound and **not a price the venue accepts**.

The grid is now a parameter of the market, not of the module:
`complement_bound(first_cost, min_edge, tick=None)`. Omitting `tick` keeps
`TICK`, so **every existing caller is byte-identical** and the sports path is
untouched. `tick_for_market()` reads the venue's authoritative
`orderPriceMinTickSize`. And `PMUSAdapter.complement_price` now passes through
the grid it was *already being handed and discarding* — it rounded the chosen
price onto the caller's tick while computing the bound on the module constant.

Rounding stays **DOWN**, so a coarser grid can only be more conservative, never
looser — regression-tested.

**A correction to the premise, on the record.** `market_micro_spec` already
documents that **PMUS serves `orderPriceMinTickSize = 0.01` for _sports_ too**,
and that a hardcoded equality against `TICK` once refused every sports market
outright. So `0.005` was never the sports grid either — it is a latent
mismatch on the sports path as well. I did **not** change the default, because
that would alter a live execution bound; flagged for separate review, exactly
as the taker coefficient was.

Nine new regression tests; no strategy behaviour altered.

---

## 3. The 0.20–0.80 band — **the evidence says keep it**

The band was **not** widened anywhere in the executor. Instead the frozen model
was replayed under candidate bands as a pure measurement (`--band` reaches
only the shadow; `pmus_q1_executor` is untouched).

| band | quotes | band holds | relations | avg pair cost | clears < $1 | ≤ 0.96 |
|---|---|---|---|---|---|---|
| **inherited 0.20–0.80** | 12 | 25 | open 4 / complete 3 / extend 5 | **0.9805** | **YES** | no |
| 0.15–0.85 | 15 | 12 | open 5 / complete 4 / extend 6 | 1.0175 | **NO** | no |
| 0.10–0.90 | 17 | 16 | open 6 / complete 4 / extend 7 | 1.0012 | **NO** | no |
| 0.05–0.95 | 20 | 13 | open 6 / complete 4 / extend 10 | 1.0012 | **NO** | no |
| venue full 0.01–0.99 | **33** | 0 | open 6 / complete 4 / **extend 23** | 1.0012 | **NO** | no |

The inherited band suppresses a great deal — the full venue band admits **2.75×
the quotes** and nearly five times the EXTENDs. But every widening makes the
assumed pairs **stop clearing $1.00**, and the residual grows from 6 units to
25. The sports band is, by accident, acting as an economic filter.

**Proposed evidence-based BTC15m eligibility rule: keep 0.20–0.80 for now.** Not
because it was inherited, but because it is the only candidate measured whose
pairs clear. Three caveats, all load-bearing: n = 3 windows; the pair economics
inherit Track B's fill assumption; and **no band tested meets the ≤0.96 spring
discipline**. This is a measurement, not a recommendation to trade, and it is
kept entirely separate from fill logic and from any directional prediction.

---

## 4. Replacing the assumption — **built, and it proves the point**

`src/pmus_interaction_classifier.py` classifies every frozen-model desired
order into the five requested classes, with the causal rule the Kalshi shadow
uses (a YES join at *p* is hit by a print at ≤ *p* whose aggressor is the NO
side, and conversely). **Every row carries `is_fill: False` and
`fill_evidence_source: null`** — a fill is an account fact, and none of these
classes is one.

Run against the public tape, across all 12 desired orders:

| class | count | |
|---|---|---|
| NO_INTERACTION | 0 | |
| **QUEUE_AMBIGUOUS** | **12** | volume moved; nothing says whether it reached our price or our queue position |
| TRADE_AT_PRICE | 0 | **structurally unreachable** — no public trade tape |
| TRADE_THROUGH_PRICE | 0 | **structurally unreachable** |
| OBSERVABLE_FILL_PLAUSIBLE | 0 | **structurally unreachable** |

`evidence_source: public_gateway_volume_counter_only`, and the output names the
unreachable classes itself so their zeros cannot be misread as "did not
happen".

**So `ASSUMPTION_VOLUME_AT_TOUCH` cannot be replaced with evidence from the
public gateway at all — 0 of 12 interactions are resolvable.** The classifier
reaches all five classes the moment it is handed a trade tape (verified against
synthetic prints), which is precisely what item 1 is blocked on.

The required sequence — *first-side interaction → repeated same-side
acquisition → opposite-side opportunity → repair → zero-cross → continued
recycling* — is therefore **measured only as far as its second step**, and
only under assumption: OPEN fires, EXTEND repeats with a run of 4, COMPLETE
fires 3–4 times, and **zero-crossing has never been observed (0 of 4 windows
against L5's 84.3% of episodes)**. Whether PMUS supplies steps 3 onward is
UNKNOWN, not answered.

---

## 5. Advancement gate — **still NOT PASSED. No manifest.**

The gate requires authenticated evidence of recurring opposite-side
acquisition *and* all execution defects closed.

| condition | verdict |
|---|---|
| execution defects closed | **tick defect FIXED**; the band is now an evidence-backed decision to keep rather than an unexamined inheritance |
| authenticated opposite-side acquisition | **NO** — 0 of 12 interactions resolvable; the trade tape does not exist on the public gateway |
| recurring active windows identifiable | PASS (unchanged: recall 0.971, precision 0.630 vs 0.398 base rate, zero latency) |
| accounting correct | PASS (unchanged: 99/99 published fee rows, effective-dated) |

So the manifest is **not** written. Writing one now would rest on exactly the
assumption you asked to have replaced.

### Dead windows, reported separately because they set capacity

Of 89 closed windows, **54 (60.7%) traded nothing**. They are excluded from the
behavioural readings above — they are not used to manufacture a failure — and
they are the binding capacity fact: any per-window figure must be multiplied by
**0.393**, and by the selector's 0.630 precision if it is used to allocate.

### What clears the gate

One thing, and it is item 1: the authenticated trade tape on consecutive
BTC15m windows. Everything else is built and waiting for it — the classifier
reaches all five classes, the shadow records the full decision row, the fee and
tick handling are correct, and the band question has its measurement.

---

## State

- **Gemini B2** — frozen, `spec_sha bfe7ba23…`, `armed False`, $10.16 ceiling.
- **Kalshi** — preserved, unchanged.
- **P10 closed population** — untouched, not restarted.
- **Public collector** — running, raw provenance in `data/pmus_btc15m/`.
