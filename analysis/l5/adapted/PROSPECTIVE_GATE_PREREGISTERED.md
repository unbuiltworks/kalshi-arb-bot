# ADAPTED prospective experiment — gate PREREGISTERED 2026-09-15T23:0xZ

**Written before the experiment has produced a single prospective observation.**
Every threshold is a number, fixed now. Choosing one after seeing which arm did
well is the failure this document exists to prevent; a later change must be a
new commit that says what changed and why.

## What is being tested, and why only this

Three preregistered residual-control arms and nothing else:

| arm | policy |
|---|---|
| **cap5** | after 5 consecutive evidenced same-side fills, withhold that side's quote until the other side fills |
| **cap8** | the same at 8 |
| **cap13** | the same at 13 |

Chosen because the historical counterfactual found **caps 5, 8 and 13 are the
only members of the preregistered family positive in BOTH regimes** (spring and
August), while cap_2/cap_3 win August and lose spring, and cap_21 goes negative
in August. The cap value is **not** robustly identified — that is precisely why
three are run prospectively rather than one being picked.

**No other arm is added during collection. No threshold moves. Nothing is
tuned.** The 0.96 ceiling gates no quote placement here; it is a reference
only.

## Purpose

To select a **bounded residual-control policy** for a **\$1–\$5 live BTC15m
maker probe**. Not to discover a strategy, not to maximise P&L, not to
reproduce L5.

## Evidence standard

**Strict queue-realistic only.** A fill counts at `VOLUME_AHEAD_CLEARED` and
nowhere else: cumulative volume at or below our price must exceed the size
resting ahead of us AND cover our own quantity. TOUCH, TRADE_AT_PRICE and
TRADE_THROUGH are recorded for contrast and **can never be used as a fill**.
Coverage must be vouched across the quote's life or the verdict is UNKNOWN,
never a fill.

## Economics

**Qualification economics EXCLUDE incentives entirely.** Trading P&L only:
settled matched + settled residual − fees.

Prospective attributable rebates and rewards are recorded **separately**,
wherever our own authenticated per-market record makes them measurable, and are
**never netted into qualification**. Retrospective L5 incentive attribution is
closed as `UNATTRIBUTABLE` and is not revisited.

---

## Gate

| | metric | threshold |
|---|---|---|
| **A-1** | windows with evidenced two-sided inventory, one venue, one arm | **≥ 20** |
| **A-2** | settled trading P&L over those windows | **> \$0** |
| **A-3** | settled **residual** P&L | **> −50%** of settled matched P&L, i.e. the residual may not consume more than half the matched edge |
| **A-4** | matched quantity retained vs an uncapped control run on the same windows | **≥ 70%** |
| **A-5** | evidenced fills whose deciding print predates the quote's placement | **0** |
| **A-6** | max drawdown over the window sequence | **≤ 40%** of settled trading P&L |
| **A-7** | the arm's exact frozen configuration is what a probe would run | **0 differences** |

Arms are **not pooled**, and venues are **not pooled** — Polymarket and Gemini
qualify independently, as under the earlier gate.

**A-3 is the point of the whole experiment.** The historical finding was that
accumulation builds matched edge and then destroys it through residual. An arm
that makes money while still letting the residual eat the matched leg has not
demonstrated residual control.

## Decision rule

A venue+arm pair **PASSES** when A-1 … A-7 hold simultaneously in one report
over one continuous sample.

- **PASS** → proceed to **live-probe preflight** (G-P8 accounting preflight
  plus the \$1–\$5 bounded-probe checklist). **Not** another research branch.
- **FAIL** → the named gate and its number. No probe.
- **INSUFFICIENT EVIDENCE** → A-1 not yet reached. Keep collecting, change
  nothing.

If two or three arms pass, the **tightest passing cap** is selected, because
the objective is bounded residual and not maximum P&L.

## Standing constraints

R/S/D frozen. PMUS halted, no legacy executor restart. No live money until a
PASS and a separate human authorisation. Shadow only: `would_submit` false on
every row.
