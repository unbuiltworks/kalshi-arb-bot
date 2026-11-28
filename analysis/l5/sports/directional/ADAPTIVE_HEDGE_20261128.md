# Adaptive underdog hedge (PMUS_RN1_TF_ADAPTIVE_HEDGE) — 28 Nov 2026

**Every number here is THEORETICAL**: recorded books + international fair,
modelled fills (touch / through), 3 s latency, production pauses, maker fee 0,
rewards not modelled. Pre-reward settled trading P&L. Nothing is realized.
Full table: `ADAPTIVE_HEDGE_20261128.txt`; script: `adaptive_hedge_eval.py`.

## Rule (default OFF, not deployed)

`src/pmus_adaptive_hedge.py`, used by the engine and mirrored by the harness
(policy key `adaptive_hedge`). For a completion on the lighter side:

- price = min(today's completion price, floor(fair_side + give)),
  give = WEAKEN_K x max(0, weakening − WEAKEN_DEAD) + EXPO_USD x min(1,
  unpaired cost / unpaired cap) − EDGE_K x max(0, held edge), clamped to
  [GIVE_MIN, GIVE_MAX]; give <= 0 while the held leg has edge and has not
  weakened. weakening = held side's fair at acquisition (qty-weighted) − now.
- size = the q in {0, min_qty..imbalance} maximising
  f_o·W_o + f_s·W_s − risk_aversion·f_o·f_s·(W_o − W_s)², exact two-outcome
  P&L; order = min(clip, q); q = 0 holds the residual.
- never above the pair cap, never above today's price or size, never a lower
  worst case; no fair / allowlisted soccer match / error = today's completion.

Flags (all under `PMUS_RN1_TRADING_FIRST=1`): `PMUS_RN1_TF_ADAPTIVE_HEDGE`
(0), `PMUS_RN1_TF_AH_GIVE_MAX_USD` (0.01), `_GIVE_MIN_USD` (-0.02),
`_WEAKEN_K` (0.5), `_WEAKEN_DEAD_USD` (0.01), `_EXPO_USD` (0.01), `_EDGE_K`
(1), `_RISK_AVERSION` (unset = `PMUS_RN1_DIR_RISK_AVERSION`).

## Data

- In-sample: 21 Nov two-venue tape, 65 settled markets (19 traded by the
  baseline under touch, 5 under trading-first edge 2c).
- Out-of-sample: 28 Nov recordings snapshot 20:53Z, 59 markets recorded, **22
  closed** (12 CFB, 10 CBB; 15 traded by the baseline, 8 under trading-first).
  Most tapes start in-game (17:58Z). Grid fixed before this tape was read;
  the "chosen" rule is the in-sample choice.

## Result: paired difference vs today's live config (edge 2c + fair+1c reprice)

Sum $ and per-market mean ± SE over markets either policy traded.

| rule (admission: trading-first edge 2c) | IS touch (n5) | IS through (n3) | OOS touch (n8) | OOS through (n8) |
|---|---|---|---|---|
| live: fair+1c reprice — settled P&L | +1.61 | +1.06 | +2.27 | +3.47 |
| adaptive give [0, +1c] (in-sample choice) | +0.29, +0.058±0.069 | −1.33, −0.443±0.423 | +0.59, +0.074±0.026 | +3.04, +0.380±0.258 |
| adaptive give [−1c, +1c] | −2.19, −0.438±0.433 | −1.27, −0.423±0.423 | +4.87, +0.608±0.297 | +6.41, +0.802±0.316 |
| adaptive give [−2c, +1c] (code default) | −1.62, −0.324±0.211 | +1.03, +0.343±0.299 | +7.48, +0.935±0.328 | +6.48, +0.810±0.312 |

Same rules under the baseline admission (more inventory; diagnostic):
in-sample choice vs fair+1c: IS touch −2.05 (−0.108±0.180, n19), IS through
+6.07 (+0.434±0.318, n14), OOS touch +0.09 (+0.006±0.029, n15), OOS through
+7.06 (+0.470±0.259, n15).

Fills (TF, touch): live 23 IS / 42 OOS; chosen 31 / 41; default 19 / 29.
Worst single-market settled P&L (TF, touch): live +0.02 IS / −0.15 OOS;
chosen +0.02 / +0.04; default −0.59 / +0.00.
Downside had every residual lost (TF, touch, sum over traded markets):
live +0.91 IS / +2.27 OOS; chosen +1.20 / +2.86; default −0.71 / **−10.04**.

## Reading

1. **The adaptive rule is not shown to beat the fixed fair+1c hedge.** The
   in-sample choice is +/− within one or two SE on four tiny samples (n 3-8
   under trading-first) and its sign flips in-sample between fill models.
2. **The parts that make the rule "adaptive" never acted on either tape.**
   Rows are identical across GIVE_MAX (1c/3c), risk aversion (0.005/0.05)
   and EDGE_K 0 vs 1 at GIVE_MIN 0. In-sample, 0 of 20,446 completion
   decisions had a positive give while today's price was above fair, and
   there were 0 partial sizes. Reason: the international fair leads PMUS, so
   when the held leg has weakened the PMUS completion is already priced
   BELOW the independent fair, and when the held leg has no edge the pair
   cap alone keeps the completion below fair. A larger give for a weakened
   leg therefore has nothing to loosen (it can never bid above today's
   price), and a hedge at or below fair is always taken in full.
3. What remains is a fixed offset: GIVE_MIN 0 is the existing "reprice to
   fair+0" rule; GIVE_MIN < 0 is a discount that hedges less (fills 23 -> 19
   IS, 42 -> 29 OOS) and holds more residual. Its large OOS gain (+7.48) is
   residual P&L (+7.87) on 8 markets where the held side happened to win; in
   sample the same rule lost under touch and its downside is far worse
   (−10.04 vs +2.27). That is directional variance, not hedge skill.

## Recommendation

Keep `PMUS_RN1_TF_ADAPTIVE_HEDGE` OFF and keep the live fixed fair+1c hedge.
No promotion. The flag is safe to leave in the tree (off = unchanged). Worth
re-running on more closed games before any further work; the only lever with
a visible effect is the discount, which trades hedge frequency for residual
exposure and needs a far larger sample to judge.
