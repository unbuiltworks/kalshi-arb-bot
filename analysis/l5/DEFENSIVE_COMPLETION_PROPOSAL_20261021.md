# Defensive completion + eight-window controller — proposal for approval

**Status: PROPOSAL. Nothing implemented in live execution. Nothing activated.**
Prepared 2026-10-21 against deployed SHA `f6b67aa2`.

---

## 1. What actually happened in 2315z, reconstructed from production

First leg: venue order `D7HT1H9G10JH`, **filled 3.39 @ own 0.3700** at
23:20:38, commission 0.0000. Cost **$1.2543**. No complement was ever
obtained. UP settled worthless. Realized **−$1.2543**.

The book after the fill, from the money-path decision records:

| Time | bid | ask | bid size | complement | pair cost |
|---|---|---|---|---|---|
| 23:21:50 | 0.2000 | 0.2100 | 1831.25 | 0.77 | 1.14 |
| 23:22:21 | 0.1800 | 0.1900 | 709.10 | 0.79 | 1.16 |
| 23:22:56 | 0.1200 | 0.1300 | 4681.69 | 0.85 | 1.22 |
| 23:24:00 | 0.1200 | 0.1300 | 7139.31 | 0.85 | 1.22 |
| 23:24:37 | 0.1100 | 0.1200 | 1176.05 | 0.86 | 1.23 |

**Could a defensive exit have executed?** Yes, and this is the strongest
part of the evidence. Selling 3.39 UP into the bid needed **3.39 shares
against 1831–7139 displayed** — a 540× to 2100× depth ratio. That is not an
assumed fill; it is an order two to three orders of magnitude smaller than
the resting size. It is still *displayed* liquidity, not a guarantee, and the
policy below treats it as such.

**What it would have cost** (taker, fee 0.026987/contract from the live
`fee_floor` record):

| Exit at | Realized | vs actual −$1.2543 |
|---|---|---|
| 23:21:50 bid 0.20 | **−$0.6678** | **+$0.5865** |
| 23:22:21 bid 0.18 | −$0.7356 | +$0.5187 |
| 23:22:56 bid 0.12 | −$0.9390 | +$0.3153 |
| 23:24:37 bid 0.11 | −$0.9729 | +$0.2814 |

Every exit beat holding. Earlier beat later.

## 2. The finding that changes the justification

**A defensive exit at the market does not improve expected value. It
converts an uncertain loss into a certain one, and charges a fee for it.**

At 23:21:50 the bid was 0.20. The market was pricing UP at ~20% to pay $1.
Expected value of holding = 0.20 × 3.39 = **$0.6780**. Sale proceeds net of
the taker fee = **$0.5865**. The difference is **−$0.0915 — exactly the
taker fee, and nothing else.**

The $0.5865 we "saved" in 2315z is not edge. It is the realisation of one
draw from a distribution whose mean the market had already priced. UP lost;
had it won, the same policy would have cost us $2.13.

So defensive completion must **not** be sold as profitable. Its real
justifications are narrower and still worth having:

1. **It bounds the tail.** One unmatched leg at $5 exposure can consume the
   entire $5 daily-loss halt and end a campaign.
2. **It frees the exposure envelope** so later windows can trade at all —
   with 8 windows and $5 aggregate, one stranded leg blocks the rest.
3. **It reduces variance** on a programme whose edge per completed pair is
   $0.05–$0.14 per contract, where a single $1.25 residual erases nine
   good pairs.

## 3. Why the policy must NOT fire eagerly — the 2300z counterfactual

2300z looked superficially identical: first leg filled, complement refused,
7 × `COMPLEMENT_PAIR_ABOVE_CAP`, book walking away.

A defensive taker exit at 23:05:44 (bid 0.18) would have realized
**−$0.3262**. The actual outcome, because the frozen complement filled at
0.71 at 23:06:32, was **+$0.1875**.

**Defensive mode in 2300z would have cost −$0.5137.** A policy that fires on
"complement refused and book moving away" destroys the winning case.

## 4. The signal that separates them

Not time — both legs filled with ~9.5 minutes to expiry. The separator is
**how far the frozen complement sits from the executable price**:

| Window | Frozen complement | Market complement | Distance | Outcome |
|---|---|---|---|---|
| 2300z | 0.71 | 0.75 → 0.79 | **4 → 8 ticks** | filled at 0.71 |
| 2315z | 0.59 | 0.77 → 0.86 | **18 → 27 ticks** | never filled |

Clean separation, and both quantities are known at decision time. **This is
two windows.** It is a candidate trigger, not a calibrated threshold, and I
am not proposing to fit a cutoff to it.

## 5. Proposed defensive-loss ceiling

Expressed as a bound on the pair cost at exit, because that is what the
existing gate already understands:

```
DEFENSIVE_PAIR_CEILING = 1.20     (vs the profitable ceiling 0.96)
```

Rationale, from the economics rather than from these two windows: holding an
unmatched leg risks losing its **entire cost** (`first_leg_cost × qty`).
Exiting at pair cost `P` loses `(P − 1.00) × qty`. Exiting is therefore the
bounded choice whenever `P − 1.00 < first_leg_cost`, i.e.
`P < 1.00 + first_leg_cost`. For a first leg at 0.37 that is `P < 1.37`;
at 0.24, `P < 1.24`. **1.20 sits inside the bound for every first leg the
0.96 entry ceiling can produce**, and caps the worst defensive loss at
**0.20/contract**.

Hard per-campaign defensive budget: **$2.00 total realized defensive loss**,
inside the existing $5 daily halt, after which new entries stop.

## 6. Eight-window controller — the smallest change

```
src/l5_campaign_approval.py:46   MAX_WINDOWS = 4   ->   8
```

That is the entire change. What it does **not** touch, verified against the
same file and `l5_live_authorization.py`:

| Control | Constant | Changed? |
|---|---|---|
| Max total exposure | `MAX_TOTAL_EXPOSURE_USD = D("5")` | **no** |
| Daily realized-loss halt | `MAX_DAILY_LOSS_USD = D("5")` | **no** |
| Campaign duration ceiling | `MAX_CAMPAIGN_S = 4*3600` | **no** (2h is inside it) |
| Series allow-list | `ALLOWED_SERIES` | **no** |
| Class budgets / backstop | env-driven, no code cap | **no** |
| Completion reserve | `COMPLETION_SUBMISSIONS_PER_OBLIGATION = 2` | **no** |
| Watchdog, HALT latch, cancellation | separate service | **no** |

Exposure is bounded by `$5 aggregate`, not by window count, so eight windows
does not raise the amount at risk at any instant — only the number of
opportunities to deploy it.

## 7. Exact live configuration proposed (NOT installed)

| Parameter | Value |
|---|---|
| Strategy | TWO_SIDED, maker-first |
| Windows | **8** consecutive BTC15m |
| Duration | 2 hours |
| Max outstanding orders | **1** |
| Max aggregate exposure | **$5** |
| Daily realized-loss halt | **$5** |
| Profitable pair ceiling | **$0.96** |
| Defensive pair ceiling | **$1.20** |
| Defensive loss budget | **$2.00 / campaign** |
| OPEN / COMPLETE / REPLACE | **16 / 24 / 32** |
| Total submission backstop | **56** |
| Completion reserve | 2 per obligation |
| Auto-extension | none |

Requires a fresh approval document, nonce and experiment id.

## 8. What is still missing, stated plainly

- **The trigger is calibrated on two windows.** 4–8 ticks vs 18–27 is a
  clean gap in a sample of two.
- **A defensive taker conflicts with a standing restriction.** The operating
  model prohibits "automatic late taker behaviour copied from mature August
  L5". This policy is exactly that, for a different reason. It needs explicit
  owner sign-off as a deliberate exception, not an inference from this brief.
- **Displayed depth is not a fill.** 540×–2100× coverage is strong, but the
  policy must report a defensive order as *submitted* until the venue
  confirms, and never as hedged.
- **No replay across more windows.** The corrected shadow arm (`73c76e98`)
  is not deployed, and no BTC15m recordings exist locally.

## 9. Recommendation

The controller change is safe and I'd proceed with it on approval.

**The defensive policy I recommend approving in a reduced form**: exit only
when the frozen complement is far out of reach *and* the position would
otherwise strand, with the $1.20 / $2.00 bounds above — and judged as tail
control, not as a profit source. On the evidence it is EV-neutral before
fees and EV-negative after them. It earns its place by stopping one window
from consuming the campaign, which is exactly what 2315z did.
