# Correction to the research record — the +$62.66 matched-economics figure

**Withdrawn: the claim that CF_MATCH discovered a ~13¢/share matching
engine.** It did not.

## What was claimed

`CF_MATCH_RESULT.md` reported "+$62.66 matched economics", a "median pair
cost of $0.8694", "92.7% of matched shares acquired below $1.00" and
"matched inventory earns about 13¢ per matched share". `PREREGISTRATION_
PORTFOLIO_MATCH_V1.md` then took those figures as the motivating evidence,
and `PORTFOLIO_MATCH_V1_RESULT.md` measured itself against them.

## What is true

Those figures are **allocation-dependent**. They come from valuing matched
inventory at each side's *average* cost. Attributing the same shares FIFO —
earliest shares on each side matched first — gives:

| | FIFO | average cost (as previously reported) |
|---|---|---|
| matched shares | 635.3 | 635.3 |
| cost of those shares | **$309.47** | $255.00 |
| implied pair cost | **$0.974** | $0.803 |
| **matched-pair economics** | **+$8.19** | +$62.66 |
| residual economics | **−$31.71** | −$86.18 |
| **portfolio net** | **−$35.69** | **−$35.69** |

**The portfolio economics are identical. Only the attribution moves.** The
cheap inventory exists either way; calling it "matched" rather than
"residual" is a bookkeeping choice, and the average-cost convention
flattered the matched line by moving the cheap shares into it.

## Consequences, applied

- **The +$62.66 figure must not be cited as evidence of a superior matching
  engine.** Where a single number is needed, FIFO's **+$8.19** at a pair
  cost of **$0.974** is the conservative reading, and the honest statement
  is that the number is convention-dependent.
- PORTFOLIO_MATCH_V1 was built to "preserve the matched economics". Its
  premise was therefore partly a bookkeeping entry. Its *result* stands
  unchanged — it failed on portfolio net, which no convention affects — but
  its motivation is retracted along with the figure.
- Nothing changes in Cell C, V1, D0–D4 or the capacity report: none of them
  holds two-sided inventory at settlement, so none has a matched line to
  re-attribute.

## Frozen alongside this correction

- `CELL_C_CAPACITY_REPORT.md` — **frozen**. At the defensible ~$100
  capacity Cell C produces **$4.28/window gross before any costs**, so $5 is
  unreachable there even at zero friction. The Cell C $5 path is closed.
- `CF_MATCH_EXTEND_ATTRIBUTION.md` — **frozen**. Favourite-side purchases
  **−$0.307 per dollar** across $231; underdog-side **+$0.304 per dollar**
  across $155.

Both are evidence, not programmes. Neither is to be re-tuned.
