# Full-inventory P&L after settlement — the answer

**No. Neither P6 nor P3 has positive full-inventory expected net P&L.**
Both are negative in-sample *and* out-of-sample, and the loss is the
**residual**, not the pair.

Source: cycle 1704, 227 BTC up/down markets, 7,697 observations from the
shadow's own tape, replayed chronologically; settlement
`INFERRED_FROM_TERMINAL_BOOK`, never venue-confirmed; bootstrap 90%
intervals on net per entry; every losing residual charged at its own cost
basis.

## In-sample (113 markets, 38 entries per policy)

| policy | matched pair P&L/entry | **residual P&L/entry** | **FULL net/entry** [p05, p95] | losing residuals |
|---|---|---|---|---|
| P1 immediate taker | −$0.0716 | $0.000 | **−$0.0833** [−0.109, −0.057] | 0 of 38 |
| P6 inventory-aware | **+$0.0174** | **−$0.1097** | **−$0.0924** [−0.184, +0.004] | **25 of 38 (65.8%)**, −$7.53 |
| P2 delayed maker | −$0.0213 | −$0.0768 | −$0.0982 [−0.173, −0.019] | 17 of 38, −$4.94 |
| P5 selective entry | −$0.0217 | −$0.0769 | −$0.0986 [−0.160, −0.029] | 16 of 35, −$3.89 |
| P4 accumulate | −$0.0608 | −$0.0755 | −$0.1363 [−0.237, −0.014] | 13 of 38, −$5.53 |
| P3 both sides resting | **+$0.0050** | **−$0.1768** | **−$0.1718** [−0.230, −0.113] | 19 of 38 (50%), −$6.72 |

## Out-of-sample (114 markets, 10 entries; P5 has 8)

| policy | matched/entry | residual/entry | **FULL net/entry** [p05, p95] | losing residuals |
|---|---|---|---|---|
| P1 | −$0.075 | −$0.002 | **−$0.0857** [−0.131, −0.045] | 1 of 10 |
| P5 | −$0.046 | −$0.074 | −$0.1200 [−0.176, −0.071] | 5 of 8 |
| P6 | **+$0.032** | **−$0.160** | **−$0.1280** [−0.228, −0.030] | **8 of 10** |
| P2 | −$0.030 | −$0.109 | −$0.1390 [−0.208, −0.082] | 6 of 10 |
| P3 | +$0.003 | −$0.144 | −$0.1410 [−0.231, −0.057] | 6 of 10 |
| P4 | −$0.053 | −$0.159 | −$0.2120 [−0.341, −0.112] | 5 of 10 |

**Not one 90% interval reaches positive out-of-sample.** P6's upper tail
touches +$0.004 in-sample and −$0.030 out-of-sample.

## The arithmetic that kills it

P6 earns **+1.74¢** per entry of matched-pair edge and loses **−10.97¢**
per entry on residuals — the residual destroys **6.3×** what the pair
earns, and out-of-sample **5×**. P3 is worse: +0.5¢ against −17.7¢, a
**35×** ratio. The residual loses roughly its full cost basis on average,
which is coherent with the separately measured adverse selection: the side
that gets filled is the side that then loses.

P1 (immediate taker) is the *least* bad full-inventory policy precisely
because it carries **no residual at all** — its −8.33¢ is pure pair cost
plus 1.2¢ of fees. Being cheap on pairs while leaving inventory open is
strictly worse than being expensive and flat.

**So the earlier "P6/P3 are positive" reading was an artifact of scoring
matched legs only.** With settlement attached, the ranking inverts: the
policies that looked best on pair cost are the worst on net, because pair
cost was never the binding term.

## Honesty limits

- **Settlement coverage: 51 of 227 markets decidable (22.5%)**; 176
  undecidable because our tape stops before convergence. All 38 in-sample
  and 10 out-of-sample entries happened to fall in decidable markets, which
  is a selection (entry-taking markets have longer tapes) — it does not
  change the sign, given losses this large and this consistent, but a wider
  tape would settle it properly.
- Out-of-sample n is 8–10 entries. The *sign* is consistent across every
  policy and both splits; the magnitudes are not precise.
- Settlement is inferred from terminal prices, not from CF BRTI. No BRTI
  history exists in the repository and no external price host is reachable
  from the analysis container.

## Verdict

**PMUS BTC up/down NO-GO stands, now on measured full-inventory grounds**
rather than on an unmeasured gap. The question "is a sub-$1 pair possible"
was answered yes and is no longer the question. The question is whether an
unhedged residual can be avoided or exited, and on this venue, at 1 share
per side with 1–3 fills per market, it cannot.
