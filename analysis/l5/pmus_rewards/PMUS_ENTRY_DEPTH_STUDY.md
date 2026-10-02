# Entry-depth diagnostic — MAE/MFE and what joining deeper actually does

2026-10-02. **Diagnostic only. No depth selected.** `eb706bcc` frozen,
pre-registration `82714061` unaltered. No live orders. 6 new complete windows
(2026-10-02 1045z–1200z), q25, ceiling .99, inferred tick **0.0100**.

## The binding data limit, stated before any number

The tape carries `bid_qty`/`ask_qty` for the **touch only**. Resting size at
bid−1 tick is unobserved, so queue-ahead at a deeper level cannot be
established, and the frozen rule forbids inferring a fill there from a print
*at* that price.

One defensible route exists and is the only one used: **a print strictly
through a level proves every resting order at that level was consumed**, so a
quote at P fills on a print strictly beyond P regardless of queue position.
Deeper fill counts here are therefore **certain but understated** — fills that
would have come from partial queue consumption at the level are UNKNOWN and are
not counted.

## What the tape's price convention actually is — checked, not assumed

| relation of print price to quoted touch | n | share |
|---|---|---|
| at the bid | 10,911 | 51.0% |
| at the ask | 9,073 | 42.4% |
| **strictly below the bid** | **745** | **3.5%** |
| **strictly above the ask** | **678** | **3.2%** |

Every sampled through-print is one or two ticks beyond, with the taker direction
matching (`sell` at 0.1400 against bid 0.1500; `buy` at 0.2800 against ask
0.2600). So `bid_at_trade`/`ask_at_trade` is the **pre-trade** touch and a
through-print is a **sweep** whose reported price is the deepest level reached.
**6.7% of trades are sweeps.**

### This exposes a conservatism in the frozen baseline rule

The baseline subtracts queue-ahead from the *reported* print quantity. On a
sweep, that reported quantity is the portion at the final level, not the whole
sweep — so the baseline **under-fills on sweeps**, counting a cleared level as
if our queue position still protected us. It is reported here and **not
changed**: it biases the baseline toward fewer fills, which is the safe
direction.

## Results

| variant | fills | qual | forced | **MAE mean** | MAE p90 | **MFE mean** | MFE p90 | MFE+MAE | net | net/fill | worst |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **D0_TOUCH** (frozen rule) | **236** | 217 | 19 | **−0.0945** | 0.0000 | **+0.0307** | +0.0600 | −0.0638 | −47.97 | **−0.2033** | −17.55 |
| **D1_DEEPER** (1 tick) | **281** | 238 | 43 | **−0.1216** | −0.0100 | +0.0328 | +0.0800 | −0.0888 | −88.95 | **−0.3165** | −13.71 |
| **D2_DEEPER** (2 ticks) | **148** | 129 | 19 | −0.1011 | 0.0000 | +0.0253 | +0.0600 | −0.0759 | −32.12 | −0.2170 | −12.38 |
| **MOVE_IN** (touch, sweep-only) | **774** | 667 | 107 | −0.1141 | −0.0100 | +0.0311 | +0.0700 | −0.0830 | −169.21 | −0.2186 | −17.03 |

## What this says

**1. The excursion asymmetry is the same at every depth, and it is severe.**
MAE mean runs −0.0945 to −0.1216 while MFE mean runs +0.0253 to +0.0328 —
**adverse excursion is 3.1× to 3.7× favourable excursion in magnitude at every
entry depth.** `MFE + MAE` is negative everywhere (−0.064 to −0.089). Whatever
is wrong is not a function of where we sit in the book.

**2. Joining one tick deeper is worse, not better.** D1 fills *more* often (281
vs 236, +19%) — because a sweep clears a deeper level without needing queue
priority — but MAE worsens from −0.0945 to **−0.1216**, forced exits more than
double (19 → 43), and net/fill degrades from −0.2033 to **−0.3165**. A deeper
resting quote is only reached by flow that is travelling, and flow that is
travelling keeps travelling.

**3. Two ticks deeper trades fills for nothing.** D2 cuts fills 37% (148 vs
236) and lands at −0.2170/fill, statistically indistinguishable from the touch's
−0.2033, with MAE −0.1011 ≈ −0.0945. Less capacity, same toxicity.

**4. Requiring the market to come to us fills 3.3× more, not less.** MOVE_IN's
774 fills against D0's 236 is counter-intuitive and the mechanism is the sweep:
at the touch we sit behind a median 1,500–2,500 contracts and are rarely served,
whereas **every sweep clears the whole level including us**. MOVE_IN is not a
more patient strategy — it is a fill-evidence rule that recognises sweeps, and
it is slightly *more* toxic per fill (−0.2186 vs −0.2033) at 3.3× the volume,
so net goes −47.97 → −169.21.

## The through-line

Every variant lands between −0.20 and −0.32 per fill with an MAE:MFE ratio of
3–4:1. **Entry depth is not the lever.** The lots are adversely selected
wherever they rest, and the one variant that materially increases fill frequency
does so by capturing sweeps — which is precisely the most toxic flow available.

Consistent with the path study: the separating signal was the mark-to-executable
move after entry, not anything about entry placement. This diagnostic says the
same thing from the other side.

**No depth is selected. Nothing is changed.** D1/D2/MOVE_IN are not added to the
pre-registered candidate set, and the baseline's sweep conservatism stays as it
is.
