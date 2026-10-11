"""PRE-ENTRY HABITAT SELECTION: can a window be judged before money is at risk?

NAMED `habitat_selection`, NOT `habitat`. `habitat.py` is the collector that
the soak and the pre-entry reader import for `public_flow`, `ladder_shape`,
`cycle_sample` and `window_summary`. Taking that name would shadow it.

Maker-price optimisation is falsified and that conclusion is frozen. 81% of
missed complements needed a cross and another 13.3% were unreachable at any
maker price -- roughly 94% where the resting price cannot be the answer. What
is left to test is whether SOME windows are materially more likely to support
two-sided recycling, and whether that is visible BEFORE the first order.

ONE ROW PER WINDOW. The independent unit is the window. Not the seed, not the
arm, not the residual state: an earlier pass counted 72 observations where
there were 3 because every arm replayed the same moments, and that must not
happen again.

THE LEAKAGE BOUNDARY IS THE FIRST RECORDED CYCLE. In the sequential replay the
first quote goes live at the first cycle, so that cycle's book is observed
BEFORE any order exists and is admissible. Everything at or after it -- the
current contract's own tape from that instant on, every later book, every fill
-- is OUTCOME and may never appear in a feature. `check_no_leakage()` enforces
this and a test pins it.

The PREDECESSOR contract is the richest admissible source: it has already
expired when our window opens, its whole tape is public, and none of it can
contain anything we did.

NOTHING HERE IS A SELECTOR. No threshold is fitted, no model is trained and no
admission rule is defined. With this many windows none could be.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: below this, a split is arithmetic rather than evidence
MIN_PER_CELL = 30

#: the primary outcome. Not initial fill -- par-constrained recycling.
PRIMARY = "par_constrained_matched_pair"

#: a matched pair above this is a matched LOSS
PAR = D("1")

OUTCOMES = ("initial_maker_fill", "opposite_reachable_print_after_first_fill",
            "complete_fill", "matched_pair", PRIMARY,
            "terminal_residual_usd", "capital_at_risk_usd")

#: every feature is computed from strictly-before-entry evidence
FEATURES = (
    "prior_prints", "prior_notional_usd", "prior_prints_per_min",
    "prior_notional_per_min", "prior_two_sided", "prior_side_balance",
    "prior_seconds_since_last_print", "prior_span_s",
    "entry_spread", "entry_yes_bid", "entry_yes_ask", "entry_mid",
    "entry_touch_depth", "entry_levels", "entry_depth_within_3",
    "own_prints_before_entry", "own_notional_before_entry",
    "own_price_move_before_entry", "entry_tte_s",
    "touch_turnover_before_entry",
)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _f(v: Any) -> Optional[float]:
    d = _dec(v)
    return float(d) if d is not None else None


def entry_instant(cycles: Sequence[Mapping[str, Any]]) -> Optional[D]:
    """The leakage boundary: when our first quote could first have existed."""
    ms = [_dec(c.get("utc_ms")) for c in cycles or []]
    ms = [m for m in ms if m is not None]
    return min(ms) if ms else None


def check_no_leakage(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Every feature must be sourced from strictly before the entry instant.

    A row carries the boundary it was built against and the latest timestamp
    any feature drew on. If the second is not strictly less than the first for
    the current contract, the row is not admissible and says so rather than
    being quietly used.
    """
    b = _dec(row.get("entry_instant_ms"))
    latest = _dec(row.get("latest_feature_source_ms"))
    if b is None:
        return {"clean": False, "why": "no entry instant; the boundary is "
                                       "undefined and nothing can be checked"}
    if latest is None:
        return {"clean": True, "why": "no current-contract evidence was used; "
                                      "predecessor-only features cannot leak"}
    ok = latest <= b
    return {"clean": bool(ok), "entry_instant_ms": str(b),
            "latest_feature_source_ms": str(latest),
            "why": (None if ok else
                    "a feature drew on evidence at %s, at or after the entry "
                    "instant %s" % (latest, b))}


def _tape_stats(prints: Sequence[Mapping[str, Any]], *,
                before_ms: Optional[D] = None) -> Dict[str, Any]:
    rows = []
    for t in prints or []:
        ms, px, amt = (_dec(t.get("utc_ms")), _dec(t.get("price")),
                       _dec(t.get("amount")))
        if ms is None or px is None or amt is None:
            continue
        if before_ms is not None and ms >= before_ms:
            continue
        rows.append((ms, px, amt, str(t.get("type"))))
    if not rows:
        return {"prints": 0, "notional_usd": "0", "contracts": "0",
                "span_s": None, "prints_per_min": None,
                "notional_per_min": None, "two_sided": None,
                "side_balance": None, "seconds_since_last_print": None,
                "price_move": None, "last_ms": None}
    rows.sort(key=lambda r: r[0])
    notional = sum(a * p for _, p, a, _ in rows)
    contracts = sum(a for _, _, a, _ in rows)
    span_ms = rows[-1][0] - rows[0][0]
    span_s = float(span_ms / D("1000"))
    buys = sum(1 for r in rows if r[3] == "buy")
    sells = sum(1 for r in rows if r[3] == "sell")
    per_min = (D(len(rows)) / D(str(span_s)) * D("60")
               if span_s > 0 else None)
    notional_min = (notional / D(str(span_s)) * D("60")
                    if span_s > 0 else None)
    return {
        "prints": len(rows),
        "notional_usd": str(notional), "contracts": str(contracts),
        "span_s": round(span_s, 3),
        "prints_per_min": (str(round(per_min, 3)) if per_min is not None
                           else None),
        "notional_per_min": (str(round(notional_min, 4))
                             if notional_min is not None else None),
        "two_sided": bool(buys > 0 and sells > 0),
        "side_balance": (round(min(buys, sells) / max(buys, sells), 3)
                         if max(buys, sells) else None),
        "seconds_since_last_print": (
            float((before_ms - rows[-1][0]) / D("1000"))
            if before_ms is not None else None),
        "price_move": str(rows[-1][1] - rows[0][1]),
        "last_ms": str(rows[-1][0]),
    }


def pre_entry_row(*, symbol: str, cycles: Sequence[Mapping[str, Any]],
                  prints: Sequence[Mapping[str, Any]],
                  prior_symbol: Optional[str],
                  prior_prints: Sequence[Mapping[str, Any]],
                  live_snapshot: Optional[Mapping[str, Any]] = None,
                  ) -> Dict[str, Any]:
    """Exactly ONE row for this window, from strictly-before-entry evidence."""
    b = entry_instant(cycles)
    if b is None:
        return {"symbol": symbol, "admissible": False,
                "why": "no recorded cycle, so there is no entry instant"}
    first = min((c for c in cycles if _dec(c.get("utc_ms")) == b),
                key=lambda c: str(c.get("utc_ms")))
    book = (first.get("book") or {}).get("yes") or {}
    depth = first.get("depth") or {}
    levels = ((depth.get("bids") or {}).get("levels") or []
              if depth.get("readable") else [])
    bid, ask = _dec(book.get("bid")), _dec(book.get("ask"))

    prior = _tape_stats(prior_prints)            # already expired: cannot leak
    own = _tape_stats(prints, before_ms=b)

    top = _dec(levels[0].get("qty")) if levels else None
    within3 = None
    if levels and bid is not None:
        within3 = sum((_dec(l.get("qty")) or D("0")) for l in levels
                      if (_dec(l.get("price")) or D("0")) >= bid - D("0.03"))

    turn = None
    if live_snapshot:
        t = live_snapshot.get("touch_turnover") or {}
        # the two reads are 3s apart at the touch; the signed change in
        # displayed size is the only depletion measure available pre-entry
        if isinstance(t, Mapping) and t.get("observable"):
            turn = t.get("qty_delta")

    row = {
        "symbol": symbol, "admissible": True,
        "prior_symbol": prior_symbol,
        "entry_instant_ms": str(b),
        "entry_tte_s": first.get("tte_s"),
        "has_live_snapshot": bool(live_snapshot),
        # ---- predecessor contract: expired before we opened, cannot leak
        "prior_prints": prior["prints"],
        "prior_notional_usd": prior["notional_usd"],
        "prior_prints_per_min": prior["prints_per_min"],
        "prior_notional_per_min": prior["notional_per_min"],
        "prior_two_sided": prior["two_sided"],
        "prior_side_balance": prior["side_balance"],
        "prior_span_s": prior["span_s"],
        "prior_seconds_since_last_print": (
            float((b - _dec(prior["last_ms"])) / D("1000"))
            if prior["last_ms"] else None),
        # ---- the book at the instant before our first quote existed
        "entry_spread": (str(ask - bid) if bid is not None and ask is not None
                         else None),
        "entry_yes_bid": book.get("bid"), "entry_yes_ask": book.get("ask"),
        "entry_mid": (str((bid + ask) / 2) if bid is not None
                      and ask is not None else None),
        "entry_touch_depth": (str(top) if top is not None else None),
        "entry_levels": len(levels),
        "entry_depth_within_3": (str(within3) if within3 is not None
                                 else None),
        # ---- this contract's OWN tape, strictly before entry
        "own_prints_before_entry": own["prints"],
        "own_notional_before_entry": own["notional_usd"],
        "own_price_move_before_entry": own["price_move"],
        # ---- the one burst precursor observable pre-entry, where collected
        "touch_turnover_before_entry": turn,
        "book_depletion_available": bool(turn is not None),
        "book_depletion_why": (
            None if turn is not None else
            "depletion needs two book reads before entry; only the live "
            "pre-entry snapshot takes them, and this window has none"),
        "latest_feature_source_ms": own["last_ms"],
        "labels": ("PRE-ENTRY ONLY. Every feature is sourced strictly before "
                   "the first hypothetical order. Nothing here is a selector, "
                   "no threshold is fitted and no admission rule exists."),
    }
    row["leakage_check"] = check_no_leakage(row)
    return row


def outcomes_from(window: Mapping[str, Any]) -> Dict[str, Any]:
    """The seven labels, computed SEPARATELY from the replayed window.

    The primary outcome is par-constrained recycling -- a matched pair at or
    below par. A pair bought above $1.00 is a matched loss and counting it as
    a success would reward the behaviour this programme keeps catching.
    """
    inv = window.get("inventory") or {}
    matched = _dec(inv.get("matched_qty")) or D("0")
    basis = _dec(inv.get("effective_matched_pair_cost"))
    fills = window.get("fills") or []
    resid = abs(_dec(inv.get("directional_residual")) or D("0"))
    avg = (_dec(inv.get("avg_yes_cost"))
           if (_dec(inv.get("directional_residual")) or D("0")) > 0
           else _dec(inv.get("avg_no_cost")))
    return {
        "initial_maker_fill": bool(window.get("initial_fills")),
        "opposite_reachable_print_after_first_fill": bool(
            window.get("complement_fills")
            or any(int(e.get("reaching_prints") or 0) > 0
                   for e in (window.get("missed_complement_episodes") or []))),
        "complete_fill": bool(window.get("complement_fills")),
        "matched_pair": bool(matched > 0),
        PRIMARY: bool(matched > 0 and basis is not None and basis <= PAR),
        "effective_pair_basis": (str(basis) if basis is not None else None),
        "terminal_residual_usd": str(resid * avg if avg is not None
                                     else D("0")),
        "capital_at_risk_usd": inv.get("total_spend_usd"),
        "gross_contracts": str(sum((_dec(f.get("qty")) or D("0"))
                                   for f in fills)),
        "n_fills": len(fills),
    }


# ------------------------------------------------------------- distributions

def _vals(rows: Sequence[Mapping[str, Any]], key: str) -> List[float]:
    out = []
    for r in rows:
        v = r.get(key)
        if isinstance(v, bool):
            out.append(1.0 if v else 0.0)
            continue
        f = _f(v)
        if f is not None:
            out.append(f)
    return out


def _stats(xs: Sequence[float]) -> Dict[str, Any]:
    s = sorted(xs)
    if not s:
        return {"n": 0}
    return {"n": len(s), "min": s[0], "p25": s[len(s) // 4],
            "median": s[len(s) // 2], "p75": s[(3 * len(s)) // 4],
            "max": s[-1]}


def distributions(rows: Sequence[Mapping[str, Any]],
                  features: Sequence[str] = FEATURES) -> Dict[str, Any]:
    """Every feature's spread, with its coverage. N is stated on every line."""
    out = {}
    for f in features:
        xs = _vals(rows, f)
        out[f] = {**_stats(xs),
                  "coverage": (round(len(xs) / len(rows), 3) if rows
                               else None)}
    return {"independent_windows": len(rows), "features": out}


def _rank(xs: Sequence[float]) -> List[float]:
    """Average ranks, ties shared."""
    idx = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and xs[idx[j + 1]] == xs[idx[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[idx[k]] = r
        i = j + 1
    return out


def spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Rank correlation, written out rather than imported.

    Returns None rather than a number when it would be meaningless: fewer
    than three pairs, or a constant series, where the coefficient is either
    undefined or trivially +-1.
    """
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rx, ry = _rank(list(xs)), _rank(list(ys))
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 4)


def correlations(rows: Sequence[Mapping[str, Any]], *,
                 outcome: str = PRIMARY,
                 features: Sequence[str] = FEATURES) -> Dict[str, Any]:
    """Rank correlation of each feature with the outcome, N on every line.

    WHERE MEANINGFUL, and it says where it is not. With this many windows and
    this few positives a coefficient is a description of these windows, never
    an estimate of anything, and it is labelled that way.
    """
    pos = sum(1 for r in rows if r.get("_outcome", {}).get(outcome))
    out = {}
    for f in features:
        pairs = []
        for r in rows:
            x = _f(r.get(f))
            if isinstance(r.get(f), bool):
                x = 1.0 if r[f] else 0.0
            y = r.get("_outcome", {}).get(outcome)
            if x is None or y is None:
                continue
            pairs.append((x, 1.0 if y else 0.0))
        rho = spearman([p[0] for p in pairs], [p[1] for p in pairs])
        out[f] = {"n_pairs": len(pairs), "rho": rho,
                  "meaningful": bool(rho is not None
                                     and min(pos, len(rows) - pos)
                                     >= MIN_PER_CELL)}
    return {
        "outcome": outcome,
        "independent_windows": len(rows),
        "positives": pos, "negatives": len(rows) - pos,
        "smaller_cell": min(pos, len(rows) - pos),
        "any_meaningful": any(v["meaningful"] for v in out.values()),
        "features": out,
        "why": ("a coefficient here describes these %d windows. With %d "
                "positive it is not an estimate of anything and no threshold "
                "follows from it; %d per cell would be the floor."
                % (len(rows), pos, MIN_PER_CELL)),
    }


def leave_one_out(rows: Sequence[Mapping[str, Any]], *,
                  outcome: str = PRIMARY,
                  features: Sequence[str] = FEATURES) -> Dict[str, Any]:
    """Does any apparent contrast survive dropping one window?

    With a handful of positives a median gap can be one window wearing a
    coefficient. This drops each window in turn and reports how far the
    contrast moves, and whether it ever changes sign.
    """
    out = {}
    for f in features:
        full = _contrast(rows, f, outcome)
        swings, destroyed = [], 0
        for i in range(len(rows)):
            sub = [r for j, r in enumerate(rows) if j != i]
            c = _contrast(sub, f, outcome)
            if c is None:
                # dropping this window empties a cell: there is no contrast
                # left to compare. With ONE positive that happens the moment
                # the positive is dropped, and counting it as "no sign flip"
                # made a one-window contrast look robust.
                destroyed += 1
                continue
            swings.append(c)
        sign_flips = (sum(1 for s in swings if full is not None
                          and s * full < 0) if full is not None else 0)
        out[f] = {
            "contrast_all_windows": full,
            "loo_min": (min(swings) if swings else None),
            "loo_max": (max(swings) if swings else None),
            "sign_flips_when_one_window_is_dropped": sign_flips,
            "drops_that_destroy_the_contrast": destroyed,
            "survives_every_drop": bool(full is not None and swings
                                        and sign_flips == 0
                                        and destroyed == 0),
        }
    return {"independent_windows": len(rows), "outcome": outcome,
            "features": out,
            "why": ("a contrast that changes sign when one window is removed "
                    "was that window, not a habitat characteristic. A drop "
                    "that DESTROYS the contrast -- emptying a cell -- is "
                    "counted too: with one positive window, removing it "
                    "leaves nothing to contrast, and calling that 'no sign "
                    "flip' would make a single observation look robust.")}


def _contrast(rows: Sequence[Mapping[str, Any]], f: str,
              outcome: str) -> Optional[float]:
    a = _vals([r for r in rows if r.get("_outcome", {}).get(outcome)], f)
    b = _vals([r for r in rows if not r.get("_outcome", {}).get(outcome)], f)
    if not a or not b:
        return None
    return round(sorted(a)[len(a) // 2] - sorted(b)[len(b) // 2], 6)


def oracle_bound(rows: Sequence[Mapping[str, Any]], *,
                 outcome: str = PRIMARY) -> Dict[str, Any]:
    """THE CEILING ON HABITAT SELECTION, and it is not deployable.

    If we knew in advance which windows would produce par-constrained
    recycling and entered only those, how much gross exposure would we not
    have taken -- while keeping every good window by construction?

    This is an upper bound with perfect foresight. It says whether habitat
    selection could solve ENOUGH of the problem to be worth collecting a
    larger sample for. It is not a strategy and cannot be run.
    """
    good = [r for r in rows if r.get("_outcome", {}).get(outcome)]
    gross = sum(_dec(r.get("_outcome", {}).get("gross_contracts")) or D("0")
                for r in rows)
    gross_good = sum(_dec(r.get("_outcome", {}).get("gross_contracts"))
                     or D("0") for r in good)
    cap = sum(_dec(r.get("_outcome", {}).get("capital_at_risk_usd"))
              or D("0") for r in rows)
    cap_good = sum(_dec(r.get("_outcome", {}).get("capital_at_risk_usd"))
                   or D("0") for r in good)
    res = sum(_dec(r.get("_outcome", {}).get("terminal_residual_usd"))
              or D("0") for r in rows)
    res_good = sum(_dec(r.get("_outcome", {}).get("terminal_residual_usd"))
                   or D("0") for r in good)
    return {
        "independent_windows": len(rows),
        "windows_with_the_outcome": len(good),
        "base_rate": (round(len(good) / len(rows), 4) if rows else None),
        "gross_contracts_all": str(gross),
        "gross_contracts_retained": str(gross_good),
        "gross_exposure_avoided_share": (str(round((gross - gross_good)
                                                   / gross, 4))
                                         if gross > 0 else None),
        "capital_at_risk_all_usd": str(cap),
        "capital_at_risk_retained_usd": str(cap_good),
        "capital_avoided_share": (str(round((cap - cap_good) / cap, 4))
                                  if cap > 0 else None),
        "residual_usd_all": str(res),
        "residual_usd_retained": str(res_good),
        "residual_avoided_share": (str(round((res - res_good) / res, 4))
                                   if res > 0 else None),
        "outcome_retained_share": 1.0,
        "deployable": False,
        "why": ("PERFECT FORESIGHT. Every good window is kept by "
                "construction, so the retained share is 1.0 by definition and "
                "means nothing on its own. The number that matters is how "
                "much exposure is avoided -- the ceiling on what any real "
                "selector could be worth. It is not a strategy and cannot be "
                "run."),
    }
