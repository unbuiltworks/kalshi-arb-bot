# Falsification audit — 2026-09-21

Checkpoint: **`4778d686`**. Every claim below was attacked, not defended. Withdrawn
claims are dead unless independently re-established by a non-circular test. No live
orders, no parameter tuning, no refit; frozen artefact `f9dcd886…861959` untouched.

Status vocabulary: **PROVEN DIRECTLY** (raw evidence, no inference) · **SUPPORTED**
(survives a null) · **UNVERIFIED** (not established either way) · **FALSIFIED**.

---

## Register

| # | CLAIM | RAW EVIDENCE | FALSIFICATION / NULL TEST | STATUS |
|---|---|---|---|---|
| 1 | L5 held **≥2 orders resting simultaneously at different prices** | 3,434 transactions with fills at ≥2 distinct prices on one outcome; **all carry ONE timestamp, zero spread, consecutive `trade_seq`** | NULL-1 cancel/replace: impossible inside an atomic tx. NULL-2 partial fill: occurs at one price. NULL-3 venue batching across time: rejected by the single-timestamp test. **NULL-5 two independent bots, one order each: NOT rejected** | **PROVEN DIRECTLY** — as *≥2 simultaneous orders on this account*, not as "one strategy running a ladder" |
| 2 | Tick spacing between simultaneously-filled levels is 1 | 3,432 of 3,434 at exactly 1 tick | direct count | **PROVEN DIRECTLY** |
| 3 | Ladder **depth beyond 2–3** levels | only 2 levels ever filled atomically (3 once) | we observe only levels that FILLED; deeper rungs are unobservable | **UNVERIFIED** |
| 4 | Ladder **persistence / replenishment / repricing**; **posted** quantities | 45.4% of consecutive same-side fills at the same tick | same-tick refill is equally consistent with re-posting; no order-book feed exists for L5's venue; filled qty bounds posted size from below only | **UNVERIFIED** |
| 5 | L5 rests on **both sides simultaneously** | — | **every one of 3,434 multi-fill transactions is a single outcome.** No atomic observation shows two-sided resting | **UNVERIFIED** |
| 6 | **PMUS flow is ~4× more one-sided than L5's habitat** (0.315 vs 0.083) | — | like-for-like, identical units (fill counts), per-market absolute, **matched sampling**: L5 subsampled to PMUS's per-market n gives **I = 0.3286 [0.2948, 0.3700]** vs PMUS **0.3148**. Intervals overlap | **FALSIFIED** — a sample-size artifact (170 fills/window vs **4 acquisitions/market**) |
| 7 | "**More reach did not repair PMUS balance**" | UB R=20 ±10t zero-queue I = 0.327 vs arm4b 0.315 | decomposed one lever at a time: **R=1 ZERO-QUEUE gives I = 0.1857 [0.1533, 0.2236]; R=4 zero-queue 0.1705 [0.1422, 0.2042]** — both overlap L5's 0.1774. Removing the **queue** repairs balance; excessive ±10-tick reach breaks it again | **FALSIFIED** — the original arm changed two things at once and I read the sum as "nothing helps" |
| 8 | **STOP CONDITION B**: the PMUS geometry gap is structurally impossible | — | its two supports are #6 (falsified) and #7 (falsified). The identity "proof" was withdrawn at `4778d686` | **UNVERIFIED** — not re-established, and not disproven either |
| 9 | L5 economics decompose as matched + residual = net | `payout − spend = net = matched + residual`, **diff 0.000000** on 2,216 windows | exact arithmetic identity check against raw per-window cashflows | **PROVEN DIRECTLY** |
| 10 | **L5 standalone expectancy is indistinguishable from zero** | net **−$1,050.57** = −0.1442% of $728,500 spend | cluster bootstrap over windows: **95% CI [−$6,060, +$3,883]**, straddles zero | **SUPPORTED** |
| 11 | Incentives ≈ **+$68,208 (936 bps)** were L5's profit engine | — | **not re-derivable from the corpus** — the windows carry no incentive field. And 936 bps (L5's venue, vs spend) and 97.59 bps (our PMUS account, vs maker notional) are **different venues and different denominators**; carrying both forward as comparable was wrong | **UNVERIFIED** |
| 12 | Fees | — | not separately recorded in the window corpus | **UNVERIFIED** |
| 13 | L5 shows real **same-side run structure** | mean run **3.00**, p50 2 | shuffle-within-window null gives mean **2.01**, p50 1 — observed clearly exceeds it | **SUPPORTED** |
| 14 | L5 is **maker-dominant (77.3%)** | `maker_taker` is a **venue-supplied per-fill field**: 55,647 maker / 11,068 taker / 5,269 unknown | not inferred by us, so construction cannot force it | **PROVEN DIRECTLY** |
| 15 | The corpus contains **two execution modes** | maker-mode 314 windows / taker-mode 1,902 | label came from maker share, so tested an **independent** separator: fills/window p50 **170 vs 5** — a dimension the label never used | **SUPPORTED** |
| 16 | Phase 2 proved multi-rung fidelity (1.229 → 0.410) | — | `band(R)` is a band width and `covered()` a band test; a single widened rung scores **0.410, identical** | **FALSIFIED** (withdrawn `4778d686`) |
| 17 | `R/M = 2I/(1−I)` shows execution captures all available pairing | — | algebraic identity, 20,000 random trials, 0 exceptions; holds for any execution | **FALSIFIED** (withdrawn `4778d686`) |
| 18 | Kalshi pooled flow imbalance | — | signed cancellation across markets; KXCRYPTOLEAD15M 0.0032 → **0.4144**, KXBTC15M 0.0212 → **0.1054** | **FALSIFIED** (corrected `9ad1a853`) |
| 19 | Habitat selector has prospective predictive power | OOS **valid-continuous windows only**: Spearman **+0.245**, separation **−0.172**, n=43 / 7 markets | re-run of the unchanged frozen selector after removing all gap-bridging windows | **SUPPORTED, WEAK** — TRAIN was 96% bridged, so the fit behind it rests on data we cannot stand behind |
| 20 | Instant two-sided acquisition on PMUS costs 1 + spread | `NO_ask = 1 − YES_bid`, so take-both = 1 + spread identically; **0 of 9,342** observations below $1.00 | algebraic identity plus exhaustive check across 48 markets | **PROVEN DIRECTLY** |

---

## Minimal surviving L5 model

Everything here is **PROVEN DIRECTLY** or **SUPPORTED**. Nothing else may be cited as
established.

1. **L5 ran at least two orders resting simultaneously at different prices on one
   outcome**, spaced **one tick** apart, on its own account. (#1, #2)
2. **L5 was maker-dominant: 77.3%** of fills carry the venue's own `maker` flag. (#14)
3. **L5's fill sequence has real same-side run structure** — mean run 3.00 against a
   shuffle null of 2.01. (#13)
4. **The corpus contains two distinct execution modes**, separable on a dimension
   independent of the label: 170 fills/window versus 5. (#15)
5. **L5's economics decompose exactly** as matched + residual = net, and its
   **standalone expectancy is statistically indistinguishable from zero**
   (−$1,050.57, CI [−$6,060, +$3,883]). (#9, #10)
6. **On PMUS, taking both sides always costs 1 + spread** — a venue identity, never
   below $1.00 in 9,342 observations. (#20)

**That is the entire surviving model.** It contains no claim about ladder depth,
persistence, replenishment, two-sided resting, repricing, incentive magnitude, fees,
PMUS habitat deficiency, or structural impossibility.

### What the surviving model does NOT support

- It does **not** establish that L5's profit came from incentives. That figure is
  unverified from raw data.
- It does **not** establish that PMUS is a worse habitat than L5's. That comparison
  failed under matched sampling.
- It does **not** establish that PMUS is structurally unfixable. STOP CONDITION B's
  supports are gone; the question is reopened, not resolved either way.
- It does **not** license the ladder engine, the habitat selector, or any probe.

Per the audit instruction, **no replacement hypothesis is offered in this pass.**
