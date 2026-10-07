"""The FROZEN BTC15m window selector. Reuses the proven resolver.

NO NEW DISCOVERY STACK. `src/l5_market_binding.py` already resolves exactly
one BTC15m window to its market identity and its TWO outcome tokens against
the venue's own record, and it already REFUSES rather than guessing: it
returns None when the venue will not confirm the identity, when the token
count is not two, when the outcome labelling is unrecognised, or when the
market's own `endDate` does not match the window. That refusal discipline is
the reason to reuse it rather than write another one.

WHAT THIS ADDS, AND IT IS DELIBERATELY THIN:

  * the 900-second WINDOW GRID, so a window start is never invented;
  * a hard BTC15m SERIES CHECK on the resolved slug, so a resolver change
    cannot silently widen the cohort to another series;
  * one refusal surface with a reason, so a window we cannot identify is
    recorded as refused rather than skipped silently.

It selects. It does not price, size, order or authorize. Authorization is
`analysis.l5.e1_cohort.authorization`, and a selected window is not an
authorized one until that module says so.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from src import l5_market_binding as MB

#: the ONLY series this cohort may trade. CLAUDE.md keeps the engine
#: market-agnostic in general; E1 is pinned to BTC 15m by the owner's
#: authorization, and this constant is what makes that enforceable rather
#: than merely intended.
SERIES = "btc-updown-15m"
WINDOW_S = MB.WINDOW_S                                              # 900

#: a resolved slug must look like the series plus this window's start.
SLUG_RE = re.compile(r"^btc-updown-15m-(\d+)$")


class WindowRefused(RuntimeError):
    """This window may not be traded, with a reason. Never a silent skip."""


def window_start_for(ts: float) -> int:
    """The 900s grid point containing `ts`. A window start is never
    invented: it is floor-divided onto the venue's own grid."""
    return int(ts) - (int(ts) % WINDOW_S)


def next_window_starts(after: float, count: int) -> List[int]:
    """The next `count` DISTINCT window starts strictly after `after`."""
    first = window_start_for(after) + WINDOW_S
    return [first + i * WINDOW_S for i in range(count)]


def _check_series(slug: str, window_start: int) -> None:
    m = SLUG_RE.match(str(slug or ""))
    if not m:
        raise WindowRefused(
            "resolved slug %r is not in the authorized series %r. The "
            "cohort is pinned to BTC 15m and may not trade another series."
            % (slug, SERIES))
    if int(m.group(1)) != int(window_start):
        raise WindowRefused(
            "resolved slug %r does not name window %d; the resolver and "
            "the grid disagree, which is an identity fault"
            % (slug, window_start))


def select_window(window_start: int, *, getter: Any = None) -> Dict[str, Any]:
    """Resolve and PIN exactly one BTC15m window, or refuse with a reason.

    The returned record is the market PIN: slug, market id (the condition
    id), BOTH token ids, and the provenance of the selection. Everything
    downstream -- eligibility, the order body, the complement -- uses this
    record and never re-resolves, so there is one identity per window and
    no path by which a later call could return a different market.
    """
    ws = int(window_start)
    if ws % WINDOW_S != 0:
        raise WindowRefused("window start %d is off the %ds venue grid"
                            % (ws, WINDOW_S))
    binding = MB.fetch_binding(ws, getter=getter)
    if binding is None:
        raise WindowRefused(
            "the venue would not confirm the identity of window %d. A "
            "window we cannot identify is a window we do not trade." % ws)
    _check_series(binding.slug, ws)
    if binding.token_up == binding.token_down:
        raise WindowRefused("window %d resolved to one token twice" % ws)
    if not (binding.token_up and binding.token_down):
        raise WindowRefused("window %d is missing a token id" % ws)
    return {
        "series": SERIES,
        "slug": binding.slug,
        "market_id": binding.condition_id,
        "condition_id": binding.condition_id,
        "token_up": binding.token_up,
        "token_down": binding.token_down,
        "tokens": {binding.token_up: "UP", binding.token_down: "DOWN"},
        "window_start": binding.window_start,
        "window_end": binding.window_end,
        "question": binding.question,
        "selection_provenance": {
            "selector": "analysis.l5.e1_cohort.selector:select_window",
            "resolver": "src.l5_market_binding:fetch_binding",
            "resolver_refuses_unidentified_windows": True,
            "series_pinned": SERIES,
            "window_grid_s": WINDOW_S,
            "selected_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                          time.gmtime()),
            "selected_unix": time.time(),
            "reused_not_rebuilt": True,
        },
        "LABEL": "SELECTED AND PINNED -- selection is not authorization",
    }


def is_selected_slug(slug: str, pin: Optional[Dict[str, Any]]) -> bool:
    """Exact-identity check against a PIN. No prefix matching, no
    normalisation, no 'close enough'."""
    return bool(pin) and str(slug) == str(pin.get("slug"))
