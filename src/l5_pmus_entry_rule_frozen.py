"""The PMUS entry-selection rule, FROZEN before any untouched window is read.

WHY FREEZE. The only finding in this programme that replicated across
independent halves is that an opening-price FLOOR helps: on a chronological
13/13 split of the 26-window PMUS corpus it raised the completion rate on
both halves (40%->50% fit, 53%->67% validate) and beat no floor on both. But
the best VALUE differed -- 0.30 on the fit half, 0.45 on the validation half
-- which is the signature of threshold fitting. A value chosen on data and
then evaluated on the same data proves nothing, so the rule is fixed here,
with its selection criterion stated, and tested ONCE on windows recorded
later.

THE SELECTION CRITERION, STATED RATHER THAN IMPLIED: MAXIMIN across the two
halves -- the floor whose WORSE half is least bad. On the examined corpus:

    floor   fit        validate    min(fit, validate)
    none    -0.8112    -1.7153     -1.7153
    0.20    -0.6112    -1.0753     -1.0753
    0.30    +0.5418    -1.0753     -1.0753
    0.35    +0.2418    -1.1885     -1.1885
    0.40    +0.2018    -1.1885     -1.1885
    0.45    +0.2018    -0.4673     -0.4673   <-- maximin
    0.50    -2.3147    -2.4172     -2.4172

HONEST CAVEAT, AND IT IS NOT A SMALL ONE. 0.45 is also the validation half's
own optimum, so maximin partly selects on it. Both halves of the 26-window
corpus have now been examined, so NO value can be called out-of-sample on
them. That is exactly why this file exists: the number is fixed now, and the
test that matters runs on windows this file has never seen.

WHAT IS NOT FROZEN HERE, BECAUSE IT IS NOT OURS TO MOVE. The LIVE floor
stays at its deployed `L5_TS_MIN_FIRST_LEG_PRICE` (default 0.20), the live
pair ceiling stays 0.96, and `L5_MAX_LIVE_ORDERS` stays 1. This module is a
research rule evaluated offline; it changes no gate and authorizes nothing.

WHY THE OTHER KNOBS TAKE THEIR BASELINE VALUES. On PMUS no completion policy
beat plain JOIN, every policy's per-window CI spanned zero, and residual
REDUCTION was WORSE (-$3.05 against -$2.53) -- the opposite of the Gemini
result, which is withdrawn and must not be used for PMUS decisions. So the
frozen rule changes exactly ONE thing against the baseline: the entry floor.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Sequence

#: the frozen entry floor, by the maximin criterion above. RESEARCH ONLY.
FROZEN_FIRST_LEG_FLOOR = D("0.45")

#: the live floor, which this does NOT change. Default of
#: `l5_two_sided_probe.min_first_leg_own_price()`.
LIVE_FLOOR_UNCHANGED = D("0.20")

#: everything else is the baseline, unchanged.
FROZEN_PAIR_CEILING = D("0.96")
FROZEN_FILL_MODE = "STRICT"
FROZEN_COMPLETION_POLICY = "JOIN"
FROZEN_IMPROVE_TICKS = 0
FROZEN_REDUCE_AFTER_S = None          # reduction HURT on PMUS
FROZEN_MAX_LIVE_ORDERS = 1
FROZEN_OPEN_MAKER = True

#: the windows the rule was selected on. Anything here is IN-SAMPLE and a
#: result on it is not evidence. `evaluate()` refuses to score them.
SELECTION_WINDOWS = (
    "cpc-btc-updown-15m-2026-10-26-0830z",
    "cpc-btc-updown-15m-2026-10-26-0845z",
    "cpc-btc-updown-15m-2026-10-26-0915z",
    "cpc-btc-updown-15m-2026-10-26-0930z",
    "cpc-btc-updown-15m-2026-10-26-0945z",
    "cpc-btc-updown-15m-2026-10-26-1000z",
    "cpc-btc-updown-15m-2026-10-26-1015z",
    "cpc-btc-updown-15m-2026-10-26-1030z",
    "cpc-btc-updown-15m-2026-10-26-1045z",
    "cpc-btc-updown-15m-2026-10-26-1100z",
    "cpc-btc-updown-15m-2026-10-26-1115z",
    "cpc-btc-updown-15m-2026-10-26-1130z",
    "cpc-btc-updown-15m-2026-10-26-1145z",
    "cpc-btc-updown-15m-2026-10-26-1245z",
    "cpc-btc-updown-15m-2026-10-26-1300z",
    "cpc-btc-updown-15m-2026-10-26-1315z",
    "cpc-btc-updown-15m-2026-10-26-1530z",
    "cpc-btc-updown-15m-2026-10-26-1545z",
    "cpc-btc-updown-15m-2026-10-26-1600z",
    "cpc-btc-updown-15m-2026-10-26-1615z",
    "cpc-btc-updown-15m-2026-10-26-1630z",
    "cpc-btc-updown-15m-2026-10-26-1645z",
    "cpc-btc-updown-15m-2026-10-26-1700z",
    "cpc-btc-updown-15m-2026-10-26-1715z",
    "cpc-btc-updown-15m-2026-10-26-1730z",
    "cpc-btc-updown-15m-2026-10-26-1745z",
)

#: the earlier corpora, also examined, also not evidence for this rule.
PRIOR_EXAMINED_WINDOWS = (
    "cpc-btc-updown-15m-2026-10-26-1330z",
    "cpc-btc-updown-15m-2026-10-26-1345z",
    "cpc-btc-updown-15m-2026-10-26-1400z",
    "cpc-btc-updown-15m-2026-10-26-1415z",
    "cpc-btc-updown-15m-2026-10-26-1430z",
    "cpc-btc-updown-15m-2026-10-26-1445z",
    "cpc-btc-updown-15m-2026-10-26-1500z",
    "cpc-btc-updown-15m-2026-10-26-1515z",
)

EXAMINED = frozenset(SELECTION_WINDOWS) | frozenset(PRIOR_EXAMINED_WINDOWS)


def untouched(rows: Sequence[Mapping[str, Any]]) -> list:
    """Only rows from windows this rule has never been scored on."""
    return [r for r in rows if str(r.get("market")) not in EXAMINED]


def evaluate(rows: Sequence[Mapping[str, Any]],
             outcomes: Mapping[str, Mapping[str, Any]],
             *, floor: Any = None,
             allow_examined: bool = False) -> Dict[str, Any]:
    """Run the frozen rule. Drops examined windows unless told not to.

    `floor` exists only so the SAME code path can produce the no-floor
    control on the same opportunities. It does not reopen the sweep.
    """
    from src import l5_maker_placement_replay as M

    sub = list(rows) if allow_examined else untouched(rows)
    fl = FROZEN_FIRST_LEG_FLOOR if floor is None else (
        None if floor == "none" else D(str(floor)))
    out = M.replay(
        sub, outcomes,
        ceiling=FROZEN_PAIR_CEILING,
        tight_ceiling=FROZEN_PAIR_CEILING,
        fill_mode=M.FILL_STRICT,
        open_maker=FROZEN_OPEN_MAKER,
        complete_improve_ticks=FROZEN_IMPROVE_TICKS,
        reduce_after_s=FROZEN_REDUCE_AFTER_S,
        min_first_leg_own_px=fl,
        policy_label="FROZEN_FLOOR_%s" % ("none" if fl is None else fl))
    out["frozen_rule_sha"] = "see git log for src/l5_pmus_entry_rule_frozen.py"
    out["windows_scored"] = sorted({str(r["market"]) for r in sub})
    out["examined_windows_excluded"] = bool(not allow_examined)
    out["live_floor_unchanged"] = str(LIVE_FLOOR_UNCHANGED)
    return out
