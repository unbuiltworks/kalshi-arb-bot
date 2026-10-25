# `ts_min_entry_runway_s` 300 → 720: **FAIL**

**VERDICT: FAIL.** The rule is not supported. My own correction from the previous turn is
withdrawn, and the **+$0.09/window estimate is withdrawn** — on venue-confirmed data the
effect is **−$0.2130**, opposite in sign.

Production untouched: cap 0.96, all inventory and risk limits unchanged, `armed=false`,
`shadow_lock=true`, `campaign_id=null`.

## 1. The test — venue-confirmed records only

14 venue-confirmed fills (`L5_LIVE_LIFECYCLE`, `ORDER_STATE_FILLED`, each with a
`venue_order_id`) across **7 BTC15m windows**; opening = first fill per window; realized P&L
from the attested settlement list in `l5_baseline`.

| window | s into window | tte | side | price | qty | realized | 720s rule |
|---|---|---|---|---|---|---|---|
| 1245z | 107.9 | 792.1 | UP | 0.57 | 1.4 | −0.2940 | keep |
| 1300z | 101.0 | 799.0 | UP | 0.63 | 1.26 | −0.7938 | keep |
| 1315z | 105.0 | 795.0 | UP | 0.46 | 1.63 | +0.0815 | keep |
| **1330z** | **247.9** | **652.1** | UP | 0.33 | 1.29 | **+0.2130** | **BLOCKED** |
| 1345z | 96.1 | 803.9 | DOWN | 0.58 | 1.5 | −0.7206 | keep |
| 1400z | 97.3 | 802.7 | UP | 0.23 | 1.11 | +0.0555 | keep |
| 1945z | 116.8 | 783.2 | UP | 0.44 | 1.56 | −0.6864 | keep |

| | |
|---|---|
| eligible windows / opening fills | 7 / 7 |
| fill rate on openings | 7 of 7 windows produced an opening fill |
| completed vs stranded | 1945z stranded (venue-confirmed, −$0.6864); the rest reached settlement with the residual resolved |
| **participation reduction** | **1 of 7 = 14.3%** |
| **venue-confirmed realized, current policy** | **−$2.1448** |
| **venue-confirmed realized, with the rule** | **−$2.3578** |
| **effect of the rule** | **−$0.2130** |

**The rule blocks exactly one window — the second-most profitable of the seven — and keeps
every loser.**

## 2. Why my premise was wrong

I argued our engine "is authorised to open in L5's worst regime." True of the **permission**,
false of the **behaviour**. Our openings cluster at **96–117s** into the window (median 105s,
median tte 795). **Six of seven already satisfy the proposed rule.** The gate's permissiveness
is not being exercised — we were never entering late, so there was nothing for the rule to fix.

L5's 82.89% bucket was **not** transferred, as instructed. Nothing above uses it.

## 3. Does the effect survive accounting for extra time to complete? No

1330z opened **latest** (247.9s in, 652s remaining) and still **completed** — three
venue-confirmed fills and a positive settlement. The one case where less runway could have
mattered is the one case that worked. Extra completion time is not what separates these
outcomes.

## 4. Expected reduction in participation

**14.3% (1 of 7)** on this sample, and the cost of that reduction is **−$0.2130 of
venue-confirmed realized P&L.**

## 5. Data limitation, stated rather than papered over

n = 7 venue-confirmed openings is the **entire** sample. `analysis/l5/l5_episode_ledger.json`
contains **zero** BTC15m episodes (all 202 are sports) and records tte only as the single
degenerate bucket `tte>10m`, which cannot discriminate 180s from 300s. No shadow estimate is
substituted.

## 6. Why the account-wide daily gate is $5 — answered, and NOT changed

`src/l5_pmus_live_runner.py:94`:

```python
def daily_realized_loss_halt() -> D:
    raw = (os.getenv("L5_DAILY_REALIZED_LOSS_HALT_USD") or "").strip()
    value = D(raw) if raw else D("5")
    if not value.is_finite() or value < D("5") or value > D("50"):
        return D("5")
    return value
```

It reads **`L5_DAILY_REALIZED_LOSS_HALT_USD`**, which is **absent from the Railway store**
(confirmed against the variable list). The approved $20 was written to
`L5_MAX_L5_DAILY_LOSS_USD` — a different variable, the L5-scoped ceiling. So the account-wide
halt is unset and takes its code default of **$5**, and it runs **first**.

**The gate is neither broken nor weakened — it was never given the owner's number**, because
the instruction named the L5-scoped variable. It is settable to $20 (the clamp admits
[5, 50]), but that is a one-variable weakening of an independent control and **it has not been
touched. It requires separate authorization.**

## 7. A correction to my own live plan

I told the owner a credited rebate is observable at our clip, citing $0.80 / 0.44 = 1.82
contracts → $0.01. **True at 0.44 and false at and above 0.50.** A fixed $0.80 notional buys
fewer contracts as price rises, and banker's rounding sends the credit to zero:

| price | contracts | raw | credited |
|---|---|---|---|
| 0.23 | 3.4783 | 0.0077 | **$0.01** |
| 0.44 | 1.8182 | 0.0056 | **$0.01** |
| 0.49 | 1.6327 | 0.0051 | **$0.01** |
| **0.50** | 1.6000 | **0.0050** | **$0.00** |
| 0.57 | 1.4035 | 0.0043 | **$0.00** |
| 0.63 | 1.2698 | 0.0037 | **$0.00** |

**We collect a rebate only below about p = 0.49.** Scored against our seven venue-confirmed
openings: **4 of 7 would earn a cent, 3 would earn nothing, $0.04 in total.** The live plan's
rebate-observability claim is corrected to that.

## 8. Consequence

**No live validation is prepared for this rule**, because the rule failed. Spending capital to
confirm a negative already visible in venue-confirmed data would be waste.

`L5_LIVE_CAMPAIGN_PLAN.md` stands unchanged — **no runway change** — if live validation of the
existing configuration is wanted.

**One observation, flagged and not proposed.** Entry **price** separates these seven outcomes
where timing does not: the three profitable windows opened at 0.46 / 0.33 / 0.23, the four
losers at 0.57 / 0.63 / 0.58 / 0.44. That is n=7, it is the opposite of L5's near-money
preference, and it coincides exactly with the band where our rebate survives rounding — so it
may be a rebate artifact rather than a selection effect. **It is not a proposal.**

---

# ADDENDUM — the entry-price observation is also withdrawn, and a third gate was unexercised

## The observation I flagged, tested and dead

I flagged (explicitly not proposed) that entry **price** separated the seven outcomes where
timing did not. Two independent tests kill it.

**1. The peer's fee-free Kalshi sweep** (`138a6688`), 15,699 realistic fills, sweep-conditioned
by own price — a genuine discriminator because no fee or rebate coefficient of any venue
appears in that file, so a real microstructure price effect must appear and a rounding
artifact cannot:

| own price | n | half-spread | adverse +30s | NET |
|---|---|---|---|---|
| 0.05–0.25 | 3,106 | 0.0026 | +0.0103 | **−0.0077** |
| 0.25–0.35 | 522 | 0.0050 | +0.0115 | **−0.0065** |
| 0.35–0.45 | 1,019 | 0.0050 | +0.0419 | −0.0368 |
| 0.45–0.55 | 1,248 | 0.0050 | −0.0050 | +0.0100 |
| 0.55–0.65 | 785 | 0.0050 | +0.0558 | −0.0508 |
| 0.65–0.95 | 2,158 | 0.0027 | −0.0085 | +0.0112 |

**Non-monotone, and negative exactly where my two best windows sat.** A real price effect
cannot reverse at the cheap end and reappear at 0.45–0.55 and 0.65–0.95. Boundary respected:
that is **Kalshi** tape, so it does not cover PMUS and is not treated as though it does.

**2. My own sample already broke my ordering.** 1945z entered at **0.44** — cheap by my own
split — and is the **second-worst loss** of the seven:

| | realized | mean |
|---|---|---|
| cheap (p < 0.50) | +0.0815, +0.2130, +0.0555, **−0.6864** | −0.0841 |
| dear (p ≥ 0.50) | −0.2940, −0.7938, −0.7206 | −0.6028 |

I let 0.44 sit on the wrong side of the line without noticing. **Withdrawn.**

## The skew null — right instinct, does not fit, and fails the other way

The peer proposed the better null: at a fair price a cheap binary has **positive skew**, so a
run of seven would show cheap entries as winners with no mechanism at all. Arithmetic before
mechanism is the right order. **But it does not fit, and it fails in the opposite direction.**

Positive skew means you lose **often** and win **rarely-big** — at p=0.23 you lose 77% of the
time. Ours won 3 of 3 cheap-ish windows with **small** gains (+0.0555, +0.2130, +0.0815). That
is not a skew signature. Quantified under that null, with `P(window profitable) = p`:

| | |
|---|---|
| probability of exactly our observed sign pattern | **0.00131** |
| probability of any pattern with every winner cheaper than every loser | **0.01012** |
| is our pattern in that set? | **no** — 0.44 lost while 0.46 won |

**The skew null makes the pattern look more surprising, not less.** So the honest conclusion is
not "skew explains it" but **"n=7 supports none of the three readings"** — mechanism, rebate
artifact, or skew. The 0.00131 is itself post-hoc on a pattern I went looking for and must not
be read as significance.

## The third unexercised gate — the day's most useful process finding (the peer's)

| gate | checked | result |
|---|---|---|
| `MAX_UNMATCHED_CONTRACTS` | our own reconstructed book | **never binds** — the 71.27% was an artifact of importing L5's inventory |
| `ts_min_entry_runway_s` 720 | 7 venue-confirmed openings | **never exercised** — 6 of 7 already satisfied it |
| pair ceilings above 0.99 | 228 OOS frames | **inert** — nothing above 1.0000 is ever offered |

**Three gates checked, three non-binding, and nobody had checked which gates actually fire
until today.** Our configuration is substantially decorative. That sentence is checkable rather
than estimated, which is more than any economic claim either session produced.

## Final ledger

| | |
|---|---|
| surviving diagnosis | the 0.96 cap is a precondition built out of one of L5's outcomes; normalised for capital our clip is ~34x coarse |
| surviving bound | closing that gap needs capital, not configuration — falsifiable: it opens if the venue stops cent-rounding the rebate |
| surviving process findings | (1) four corrections, none from new data, none reachable in one session; (2) three gates checked, three unexercised |
| surviving economic estimates | **zero** |
| withdrawals | **twelve** |

Both survivors predate today and were sitting in committed files. The two withdrawals that
could have done real damage were the **phantom PMUS fee** and the **unlabelled venue**; both
are convention fixes, and both are now done on this lane.

**Production unchanged throughout: cap 0.96, every inventory and risk limit at its deployed
value, `armed=false`, `shadow_lock=true`, `campaign_id=null`, no rule shipped, no gate
changed, no service created.**
