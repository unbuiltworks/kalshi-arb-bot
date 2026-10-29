# AWS evaluation — measured against the five stated criteria

**Verdict: AWS does not win on any of the five. Do not migrate, and do not run
the parallel AWS recorder yet.** Not because migration is hard, but because the
measured bottleneck is not in the layer AWS operates on. The one real latency
win available is a *source* change, and it works from Railway today.

Evaluated 2026-09-15 against `analysis/l5/latency/OBSERVABILITY.md`.

## Criterion by criterion

### 1. Public feed arrival latency — **no improvement available**

The dominant term is Polymarket's **~31 s batch publication cadence**
(mean 15.29, median 15.00, max 31.55, n=208 — an almost exact uniform-on-[0,T]
fit). That is server-side and identical for every client.

Our network term is already small and measured: HTTP RTT to the venue 0.27–0.33 s,
clock skew vs the venue's own `Date` header p50 0.68 s — at or below that
header's whole-second resolution. Relocating would contest a sub-second term
inside a 15-second median. Even a perfect zero-latency link leaves the median at
~15 s.

### 2. Websocket continuity — **no problem to fix**

Across a 9-minute run: **471,549 websocket observations, 1 error total** over
all surfaces combined. The connection did not drop, stall, or need a reconnect.
There is no continuity deficit for AWS to improve.

### 3. Timestamp quality — **a property of the source, not the host**

`data-api` stamps **integer seconds** (so ±1 s of quantization is baked in);
CLOB stamps **milliseconds**. Neither changes with where we run. The one
timestamp-quality improvement actually available is switching *which source* we
read — covered in §6 below.

### 4. Raw event retention — **a real constraint, but not an AWS one**

Full websocket capture runs **310 MB per 45 minutes** ≈ 10 GB/day. The Railway
volume is 50 GB, so full retention is ~5 days. This is a genuine limit and worth
deciding about, but it is a storage-policy question (what to retain, at what
resolution, for how long) that S3 and a Railway volume answer equally well. It
is not a reason to move compute.

### 5. Book-change resolution — **already at the source's own limit**

Book updates arrive at **p50 0.01 s** via websocket and 0.02 s via CLOB REST.
The cadence is set by how fast the venue emits `price_change`, not by how fast
we receive it. Nothing to win.

## What the parallel-recorder experiment would have shown

The proposed design — run an AWS recorder alongside, compare
`AWS_receive − Railway_receive` on identical source events plus missing-event
rates — is the right experiment, and it is *specified and ready*. But we can
predict its scale from what is already measured: the difference would be bounded
by the network RTT gap, i.e. **tens of milliseconds against a 15,000 ms median**.
It would measure something real and irrelevant.

It becomes worth running if, and only if, the chain route below moves the
bottleneck into the sub-second regime, where an RTT difference would finally be a
meaningful share of the total.

## 6. The change that does win, and it is not a hosting change

Reading Polygon directly, instead of waiting for Polymarket to publish:

**n = 10 joined observations. The chain won every one.**

| | value |
|---|---|
| chain detection after block | p50 **0.87 s** (min −0.20, max 7.54) |
| chain earlier than `/activity` by | p50 **19.59 s** (min 2.15, max 34.09) |
| chain won | **10 / 10** |

L5's address sits in an **indexed topic** of the exchange fill event, so it is
directly filterable, and keyless public RPCs resolve it in p50 0.24 s. Adding the
measured ~2.9 s from match to block, total observability via the chain is
**≈ 3.8 s against ~15 s** — roughly 4× better, and it beat the feed on every
single observation.

Two caveats stated rather than smoothed over: the chain figures are bounded by
the scan loop's own 0.4 s cadence, so the true floor is lower than measured; and
the −0.20 s is detection slightly *ahead* of the block's declared timestamp,
which is a proposer-clock artifact, not time travel — it does mean the real chain
lag is near zero.

At a ~3.8 s lag the 5 s, 15 s and 30 s horizons all become knowable in time,
where today only 13.5 %, 52.4 % and 95.2 % of anchors are, and only h = 1 s stays
out of reach. **That is a source change. It runs from Railway unchanged.**
