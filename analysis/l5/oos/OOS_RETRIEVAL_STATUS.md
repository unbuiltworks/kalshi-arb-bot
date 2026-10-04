# OOS retrieval — COMPLETE. See OOS_ECONOMIC_RESULT.md for the answer.

> **SUPERSEDED, AND TWO CLAIMS BELOW WERE WRONG.** Retrieval finished on
> 2026-10-04 and the frozen evaluation has run once. The economic answer is in
> `OOS_ECONOMIC_RESULT.md`. This file is kept for the record of what was tried,
> with its two errors marked, because both cost real time.
>
> **Error 1 — the blocker was not the token.** `railway volume files` needs an
> SSH identity as well; the inventory-only run failed with `No SSH keys found`
> even with a valid `RAILWAY_TOKEN`. The working path already existed on `main`
> as `l5-volume-export`, which pairs the project token with a registered SSH
> key, and it listed the volume on the first attempt.
>
> **Error 2 — the volume does NOT hold `book`/`trade` rows.** The claim below
> that it carries "the same `kind: book` / `kind: trade` rows the development
> tape is made of, so the existing replay and gate code reads them unchanged" is
> false, and it was load-bearing. `pmus_ws_evidence_recorder.append` persists
> `{"kind": "ws", "t", "msg"}` raw frames; the `book`/`trade` rows are derived
> in-process by `apply_events`, and the development tape was built from the
> recorder's PRINTED log lines by `pmus_railway_log_to_tape.py`. A harvest
> written against this claim read all 46 parts and kept zero rows.
>
> One further fact this file could not have known: the 12 GiB retention has since
> swept every 2026-09-30 part, so seven of the development tape's eight windows
> can no longer be re-derived from the volume at all.


Recorder untouched: not modified, not redeployed, not exposed, collection
behaviour unchanged. No second service mounts its volume. The WS pricing
adapter is untouched.

## The path chosen, and why it is the safest supported one

Railway's own documentation gives a read path that needs **no deployment,
no public domain and no change to the running service**:

```
railway volume files list     /state/pmus-ws/raw
railway volume files download /state/pmus-ws/raw/<part>.jsonl  <local>
```

Rejected alternatives, and why:

| option | verdict |
|---|---|
| second service mounting the same volume | **no** — a Railway volume attaches to one service; re-pointing it would interrupt the recorder |
| public domain + the recorder's existing HTTP surface | **last resort only** — exposes the working collector, which you asked to avoid |
| volume backup → restore to a new volume → reader service | viable fallback, several steps, still needs a reader service |
| harvesting the log stream | already established as context-prohibitive and lossy (the earlier harvest truncated at 501 entries per query and manufactured 45 s "gaps") |

**Railway CLI 5.63.1 is installed in this session.** The only missing input
is a **`RAILWAY_TOKEN`** in the environment. A project-scoped token is
enough and is read-only for this use. Once it is set, extraction and
inventory run unattended via
`scripts/pmus_oos_extract_and_inventory.py --download --inventory`.

## What is on the volume, from the recorder's own code

- root `/state/pmus-ws`, raw tape at `raw/pmus_ws_YYYYMMDD_HH.jsonl`
  (hourly parts since 2026-09-08; older daily files remain readable);
- retention budget **12 GiB**, oldest parts swept first, the part being
  written now never deleted — so the post-development sample is intact on a
  50 GB volume;
- the same `kind: book` / `kind: trade` rows the development tape is made
  of, so the existing replay and gate code reads them unchanged.

## The inventory the extraction will produce

`--inventory` reports, per window: start time UTC, book and trade counts,
span, **largest book gap**, settlement outcome, usability under the
**already-established** gate (span ≥ 600 s, last book within 30 s of the
scheduled end, terminal touch 0.99/0.01), the rejection reason where it
fails, and the **DEVELOPMENT / OOS label**.

The label is mechanical, not a judgement: the development cutoff is frozen
in code at `cpc-btc-updown-15m-2026-10-02-2045z`, and any window starting at
or before it is refused the OOS label. **The 28 development windows stay
frozen and are not re-scored.**

## Resolved: 676 vs 666 actions

Both numbers are right; they count different things, and the difference is
exactly ten.

| | count |
|---|---|
| purchases (allocations) | 387 |
| exit **events** | **279** |
| exit **legs** | **289** |
| 387 + 279 | **666** |
| 387 + 289 | **676** |

**Ten exits consumed two book levels instead of one**, so they are one
decision but two executable actions. All ten are two-leg; there are no
three-leg exits. They are:

| window | t | shares wanted | legs |
|---|---|---|---|
| 09-30 1215z | 1790771049.81 | 1.6393 | 1.0000 + 0.6393 |
| 09-30 1315z | 1790774287.55 | 1.6949 | 1.6200 + 0.0749 |
| 09-30 1315z | 1790774356.29 | 1.8519 | 0.6100 + 1.2419 |
| 09-30 1330z | 1790775249.12 | 1.5385 | 1.0000 + 0.5385 |
| 10-02 1100z | 1790939233.90 | 2.3932 | 1.0000 + 1.3932 |
| 10-02 1200z | 1790942883.46 | 1.6129 | 1.0000 + 0.6129 |
| 10-02 1315z | 1790946969.16 | 1.7857 | 1.6100 + 0.1757 |
| 10-02 1430z | 1790951892.97 | 1.0417 | 0.4900 + 0.5517 |
| 10-02 1845z | 1790966999.81 | 1.9608 | 1.7200 + 0.2408 |
| 10-02 2045z | 1790974323.24 | 1.0753 | 1.0000 + 0.0753 |

The hybrid execution study priced **legs**, because each leg is a separate
executable order with its own price and fee. The controller comparison
counted **events**, because each event is one decision. Neither is wrong and
no P&L is affected — the additive cashflow measure sums legs either way, and
it ties to Cell C's net at −1.3858 against −1.3854.

## Limitation preserved verbatim

> **MATCH_ONLY_SEED proves that this particular action-reduction rule
> destroys gross. It does NOT establish that no possible action subset can
> preserve Cell C gross.** The result is about one rule — buy only the
> under-held side — and must not be generalised to all subsets.

Recorded here because the P2 report's framing ("removing the churn removed
the thing that was making money") reads more broadly than the experiment
supports, and the narrower claim is the one the evidence carries.

## Independence from the Grok audit

This retrieval touches no accounting code and re-derives no fee figure. Cell
C, CF_MATCH and P2_MATCH_ONLY_SEED will be run on OOS **exactly as frozen**,
so whatever the audit concludes about the fee/cashflow treatment can be
applied to development and OOS alike afterwards and the two reconciled
without re-running either.

## Not done, and why

The OOS comparison itself. Running the three controllers requires the OOS
tape, and the tape requires the token. I did not substitute a log harvest:
that method has already produced false gaps in this programme, and an
inventory built on it would fail the completeness checks this study exists
to satisfy.
