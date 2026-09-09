# Pre-registration: does pre-entry drift predict completion opportunity?

**Frozen 2026-09-09, BEFORE the measuring field exists and before any outcome
under it has been observed.** Committed ahead of the implementation commit on
purpose: if this document were written after the first read, every threshold
in it would be suspect.

OWNER (#167): *"I've required Claude to preregister the drift definition
before looking at outcomes. If multiple thresholds/lookbacks are explored, the
winner must subsequently survive held-out/forward confirmation. No picking
whichever threshold makes historical results look prettiest."*

---

## 1. The hypothesis, stated so it can fail

**Mechanism under test.** First side fills → the market moves against the
complement we still need → the complement becomes unreachable → the residual
strands.

**Established (post-entry, therefore NOT tradable):** when the complement
moved adversely during a window, opportunity rate collapsed from **2.9487%**
to **0.6944%** — a **4.25×** gap, on 83 resolvable windows. Corroborated in
the settled record: 27 of 33 `entry_band_left` firings (82%) landed in the
three R5F markets that lost money.

**The untested link, and the whole point of this experiment:** that a market
already drifting *before* entry keeps drifting *after* it. If it does not,
this lever is dead and we kill it immediately.

---

## 2. The drift definition — frozen

Computed from the shadow's own mid series, `mid = (bid + ask) / 2`, sampled on
the same 5-second poll that prices everything else.

**PRIMARY metric.** One number, chosen before any outcome is seen:

```
drift_abs = | mid(t_open) - mid(t_open - L) |      L = 60 s
```

Absolute, not signed. **Why unsigned:** ex ante we cannot know which direction
will hurt — a complement that gets cheaper helps us. But the loss function is
asymmetric (mean acceptable completion **+$0.5492**, mean other outcome
**-$1.2782**), so variance itself is costly regardless of direction. Unsigned
movement is the right signal under an asymmetric loss, and signing it would
require knowing the answer in advance.

**SECONDARY metrics** — recorded and reported, explicitly NOT decisive, and
never substituted for the primary if the primary fails:

| metric | definition |
|---|---|
| `excursion` | `max(mid) - min(mid)` over L — catches chop that ends where it began |
| `drift_abs` at L = 30 s | shorter lookback |
| `drift_abs` at L = 120 s | longer lookback |

**Resolvability, fail closed.** Drift requires at least
`MIN_DRIFT_SAMPLES = 6` mid samples spanning at least `0.9 * L` seconds
(**amended 2026-09-09T16:55Z — see §7**). Below
that the window reports `drift_resolvable: false` with a reason and is
**excluded from the analysis** — never folded in as zero drift. A market that
has just been pinned may not yet carry the history; "we could not measure it"
and "it was not moving" are different claims, as everywhere else in this
campaign.

---

## 3. The decision rule — frozen

**Split: the observed median of `drift_abs`.** Distribution-free, and it
cannot be tuned, which is the point. No threshold is named here because
naming one now would be a guess and naming one later would be fishing.

**PASS requires ALL THREE:**

| # | requirement | value |
|---|---|---|
| P1 | total opportunities across both halves | **>= 30** |
| P2 | opportunity-rate ratio, low-drift / high-drift | **>= 1.50** |
| P3 | share of all opportunities retained by the low-drift half | **>= 55%** |

**FAIL** if P1 is met and P2 or P3 is not. **UNDERPOWERED** if P1 is not met —
report the exact deficit and nothing else, per the standing rule.

### Why 1.50

Arithmetic, fixed in advance. Current P(acceptable completion | first leg) =
**0.6429**; break-even = **0.6994**. Under a median split, if the halves have
completion rates `p_low` and `p_high` averaging 0.6429, reaching `p_low >=
0.6994` needs `p_low - p_high >= 0.113`, i.e. a completion-rate ratio of about
**1.19**.

We require **1.50 on opportunity rate**, deliberately stricter, because
opportunity rate is a **PROXY** for completion and the proxy will lose fidelity.
The margin is the price of using a proxy.

### What passing does and does not authorize

Passing authorizes **designing a live test**. It authorizes nothing about
completion itself: an opportunity is the touch reaching our price with a print
in the same bucket, never a fill, and no fill is ever invented here. It moves
no gate, no cap and no threshold, and it does not arm anything.

---

## 4. Multiple comparisons, handled in advance

The primary metric at L = 60 s is **the** test. If it PASSES, the secondaries
are reported as description only.

If the primary FAILS, **the hypothesis is dead** and the secondaries do not
rescue it. Exploring three lookbacks and reporting the best would be a
four-way search reported as one test.

If the primary passes and we then go looking for a *usable threshold* (rather
than the median), that threshold is **provisional** until it survives a
**held-out forward window**: a later, disjoint block of shadow data on which
the threshold is applied without refitting. A threshold that only works on the
block it was chosen from does not count.

---

## 5. Kill condition

Per the owner: *"If it doesn't predict the subsequent problem, we kill it
immediately. No weeks of tweaking."*

A FAIL at the frozen definition closes this lever the same day, the same way
persistence was closed. The next step is then the next largest measured
discrepancy, not a variant of this one.

---

## 6. What is being deployed to measure it

One additional field per closed persistence window carrying `drift_abs`, the
secondaries, and the resolvability flag. It adds no order path, no credential
and no gate. It touches `src/pmus_completion_shadow.py`, a watched path, so
the shadow restarts and the roster begins a fresh survey — accepted, and
authorized by the owner, now that the persistence cohort has formally closed
POWERED.

**Standard unchanged: filled → settled → realized. Nothing here is a fill.**

---

## 7. AMENDMENT, 2026-09-09T16:55Z — span tolerance 0.5·L → 0.9·L

**No outcome had been analysed when this was written.** The median split had
not been run, no opportunity rate had been computed for either half, and no
PASS/FAIL had been evaluated. Only the mechanics of the first ten emitted rows
were inspected. This is a correction to a defective definition, not a
threshold moved after seeing a result — and if it had come after an outcome it
would not have been permissible.

**The defect.** §2 defines the primary as
`drift_abs = |mid(t_open) - mid(t_open - 60s)|`, and the original
resolvability clause allowed a span of `0.5 * L`. A window opens the instant
its drift becomes resolvable, so it always opened at exactly that boundary.
Every one of the first live rows shows `span_s: 30.001` for the 60 s lookback,
and the 30 s and 60 s figures are **identical on every row** — the same six or
seven samples serving both. The stated 60-second primary was in fact a
30-second measurement, and the two lookbacks could not disagree.

A metric that cannot differ from its own secondary is not measuring what it
claims to measure.

**The correction.** `MIN_SPAN_FRACTION = 0.9`: the sample span must cover at
least 90% of the lookback. The 60 s primary now requires ~54 s of history, so
it measures roughly what §2 says it measures, and the 30 / 60 / 120 s figures
become genuinely distinct quantities. `MID_HISTORY_S` rises 180 → 240 so the
120 s secondary can resolve at all.

**Cost.** A window now opens about 55 s into a pinned phase rather than 30 s,
so each market yields marginally fewer windows per pin. Accepted: a smaller
sample of the stated metric is worth more than a larger sample of a different
one.

**Nothing else changes.** The primary is still `drift_abs` at L = 60 s,
unsigned. The split is still the observed median. P1 >= 30 opportunities,
P2 >= 1.50 ratio, P3 >= 55% retention are all unchanged. The kill condition is
unchanged.

**Rows already emitted under the 0.5 tolerance are DISCARDED**, not merged:
they carry a 30-second measurement under a 60-second label, and pooling them
with corrected rows would silently average two different metrics. The analysis
starts from the corrected deployment.

A regression test now fails if the 30 s and 60 s lookbacks ever return the
same value on a mid path that moved only outside the shorter window.
