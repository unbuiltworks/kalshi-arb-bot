# EVIDENCE PACKET 002 — "losing is fine, I just want rebates"

*2026-09-20T20:25Z. Documentation only. No strategy, threshold, cap, policy,
service or deploy changed. Nothing implemented.*

**The reframe is evidence-supported — it is what L5 actually does. But the
arithmetic points at the OTHER venue, and there is one unverified fact worth
more than everything else in this packet.**

---

## 1. The reframe is correct about L5

L5 made **+$68,723 lifetime on −$2,829 of trading P&L**: ~$35,200 maker
rebate, ~$28,800 REWARD, ~$7,500 taker rebate. On his BTC15m maker book,
adverse selection is **−0.77¢/$** against **+0.72¢/$** of modeled maker
rebate — **a wash**. The REWARD pool (~$1,560/day observed) is the actual
profit layer.

**So "rebates carry it" is right, with one correction: for L5 it is REWARDS
that profit, and maker rebates merely cancel his adverse selection.**

## 2. What it changes: you may pay above par

On Gemini crypto the maker rebate rate (`0.30 × 0.07 = 0.021`) **exceeds** the
maker fee rate (`0.0175`) by exactly **1.2×**, so every maker fill is
fee-positive.

| pair basis | net excl. rebate | net incl. rebate |
|---|---:|---:|
| 0.96 | +0.03125 | +0.04175 |
| 0.99 | +0.00125 | +0.01175 |
| 1.00 | −0.00875 | **+0.00175** |
| **1.00175** | −0.01050 | **0.00000** |
| 1.01 | −0.01875 | −0.00825 |

**Break-even pair basis: 0.99125 excluding rebate → 1.00175 including it.**

This independently corroborates `L5_OPERATING_MODEL.md` §2d: **42.75%** of
L5's 33,765 COMPLETEs were above $1.00, and the ≤0.96 discipline scored
**−28/−119 bp** against 1.00–1.02's **+98/+61 bp**. He pays above par because
the rebate pays for it. **A rebate-first objective makes the ≤0.96 and ≤1.00
ceilings actively wrong.**

## 3. What it does NOT change: residual still kills it

`E_cycle = p·(pair net) + (1−p)·(residual settlement + leg rebate)`

| completion | residual may lose at most |
|---|---|
| 30% | 32.5 bp of notional |
| 50% | 52.5 bp |
| 70% | 99.2 bp |
| 90% | 332.5 bp |

Measured Gemini markout@60s: **NO leg −49 to −65 bp** (through the limit at
50% completion), **YES leg +1.5 to +5 bp** (survives).

And the decisive measured comparison, `BREAKEVEN.md`:

> PMUS residual loss **−$0.331449/unit** against a confirmed rebate of
> **$0.002953/leg** — **112×**.

**You cannot farm rebates with unmatched inventory.** Rebates absorb ordinary
adverse selection; they do not absorb a directional leg carried to settlement.
The binding constraint is unchanged: **pairs must form.**

## 4. THE VENUE FINDING — Gemini's fee eats five sixths of the rebate

| venue | maker fee | net incentive per contract @ P=0.50 |
|---|---|---:|
| **Gemini**, fee charged | `0.0175 × C × P(1−P)` | **$0.000875** |
| **PMUS** | **none — Polymarket charges makers nothing** | **$0.003125** |
| Gemini, *if* the maker waiver applies | 0 | $0.005250 |

Our measured Gemini rate is **97.59 bp** of maker notional **gross**. Net of
the maker fee it is **16.27 bp** — the fee takes **5 of every 6 cents**.
PMUS's `credit = 0.0125 × qty × p × (1−p)` is **100% net**.

**For a rebate-first strategy PMUS pays 3.6× more per contract than Gemini,
and it is the venue that already has a reconciled ledger.**

Net $/month by venue and volume:

| contracts/day | Gemini (fee) | PMUS | Gemini (waived) |
|---:|---:|---:|---:|
| 500 | $13 | $47 | $79 |
| 2,000 | $52 | $188 | $315 |
| 5,000 | $131 | $469 | $788 |
| 10,000 | $262 | $938 | $1,575 |

Against **$300–500/month** of hosting (ESTIMATE, `MONEY_AUDIT.md`): Gemini with
the fee charged needs **~20% of all BTC5m venue volume** just to cover hosting,
and only if trading P&L is exactly zero.

## 5. THE UNVERIFIED FACT WORTH MORE THAN THE REST

`src/l5_shadow/terms.py` documents a **Gemini maker fee waiver running
Sep 2 – Dec 31**, deliberately not applied in our pricing because it is
promotional and time-bounded:

> *"The Sep 2 – Dec 31 maker waiver is deliberately NOT applied: it is
> promotional and time-bounded, and pricing against a promo overstates
> Gemini's economics the moment it lapses."*

**Today is 2026-09-20 — inside that window.** And we have never confirmed
whether Gemini actually charged us a maker fee:
`FIRST_SETTLED_CYCLE.md` records fees as **`venue_unreported` — "Not zero,
unknown."** The 97.59 bp effective rate was computed as credits ÷ maker
notional and says nothing about fees charged.

**If the waiver applies to our account, Gemini's net incentive is 6× what this
packet assumes, the break-even pair basis moves further above par, and Gemini
becomes the best rebate venue rather than the worst.** If it does not, PMUS
wins by 3.6×.

**Status: UNRESOLVED. This is a read of the account, not a trade, and it is
the cheapest high-value action available.**

## 6. The rewards question

L5's actual profit layer is **REWARDS (~$28,800)**, not maker rebates. Status
by venue:

- **Gemini** — the rebate/reward endpoint survey found the prediction-market
  reward paths **all 404**; only `/v1/transfers` and `/v1/tradevolume` exist.
  No reachable rewards programme.
- **PMUS** — a rewards programme exists and we have accrued rows, but every
  one is **PENDING or SKIPPED**: **$0.40 PENDING, $1.08 SKIPPED, $0.00
  credited.** Why nothing has ever been credited is **unanswered**, and it is
  the layer that made L5 his money.

## 7. What I am NOT claiming

- Not claiming rebate-first is profitable. At achievable Gemini volume with
  the fee charged it does **not** clear hosting.
- Not claiming the waiver applies. **Unverified.**
- Not claiming PMUS rewards are obtainable. Every row is PENDING/SKIPPED.
- Not claiming residual can be ignored. It is **112×** the rebate on the one
  lane where both are measured.
- No figure here is realized. Accrued rebate is not credited rebate.

## 8. Decisions this puts in front of the owner

1. **Verify the Gemini maker-fee waiver** against the account. Read-only.
   Changes the venue ranking by 6×. Highest value per unit of effort in this
   packet.
2. **Find out why PMUS rewards are never credited.** That is L5's profit
   layer, and ours reads $0.00 against $1.48 accrued.
3. **If rebate-first is adopted, the pair-cost ceilings must go** — break-even
   is 1.00175, not 0.96 or 1.00. That is a strategy change and needs an
   explicit go.
4. **Residual bounds must get tighter, not looser.** A rebate-first objective
   increases the incentive to leave legs unmatched, and residual is 112× the
   rebate.
