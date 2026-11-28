"""Coverage and staleness for the REST book tape, as a refusal.

WHY THIS MODULE EXISTS. The 2026-11-02 2230z "missed completion" was not a
missed completion. It was a reading of the REST book tape in
`scratchpad/tape/*.jsonl` as though it were a continuous book. It is not:
measured over 20 campaign and research windows, that tape carries a median
of 50 BBO observations per 15-minute window, a median inter-sample gap of
14.9s, a median largest gap of 34.7s, and is DARK (no observation within 5s)
for a median 93.0% of each window. It samples in pairs about 2s apart and
then goes quiet for about 29s.

Used as a second-by-second book, a sampler like that does not merely lose
precision -- it invents opportunities. At 2230z the last observation before
our fill read UP 0.5200/0.5300 at 22:32:10.345, which implies a DOWN
complement at 0.4700 own and a pair of 0.9400, comfortably inside the 0.96
ceiling. The executor's own authenticated websocket book, 229ms later at
22:32:12.184, read UP 0.4000/0.4100: a DOWN complement at 0.5900 own and a
pair of 1.0600. The tape then recorded NOTHING for 16.6s, and its next
observation at 22:32:28.992 read 0.3700/0.3800. The 0.94 pair never existed
at a price anyone could trade; it was a snapshot taken 273ms before the fill
and never refreshed across a 16-cent slide.

Two independent checks say the tape, not the websocket, was wrong. Its
22:32:12.419 row repeats the 22:32:10.345 row byte for byte with
`shares_traded_delta_lower_bound: 0.0`, claiming nothing traded, while 3,599
shares traded before the next observation. And our own resting maker bid was
UP at 0.4700: a 0.47 bid does not fill while the bid is 0.52, so the fill
itself places the touch at or below 0.47 at that instant.

So this module makes the sampler state its own coverage, and makes pricing a
completion from a stale observation a REFUSAL rather than a number. Nothing
here is part of the money path; it guards ANALYSIS, which is where the error
was made.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

UP = "UP"
DOWN = "DOWN"
ONE = D(1)

#: The longest a book observation may be, at the instant priced, before this
#: module refuses to price a completion from it. 5s is not a comfort
#: threshold: it is already far looser than the 0.004-0.125s stream ages the
#: live executor decided on at 2230z, and it is here only so that a tape
#: whose median gap is 14.9s cannot silently answer a second-level question.
MAX_OBSERVATION_AGE_S = 5.0

#: Below this, a window's tape may answer "what was the book around here";
#: above it, only "what was the book at some point in this window".
DARK_FRACTION_UNUSABLE = 0.50

STALE = "BOOK_OBSERVATION_STALE"
NO_OBS = "NO_OBSERVATION_AT_OR_BEFORE"
UNREADABLE = "UNREADABLE_OBSERVATION"
NOT_SECOND_LEVEL = "TAPE_NOT_SECOND_LEVEL"

LABELS = (
    "COVERAGE AND STALENESS ONLY. A pair priced here is conditional on an "
    "observation that may be seconds old; it is never a fill, never "
    "executable and never realized. A refusal means the tape cannot answer "
    "the question asked of it, NOT that no opportunity existed -- and an "
    "answer means only that the tape's own observation said so.")


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _d(v: Any) -> Optional[D]:
    try:
        if v is None or v == "":
            return None
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def _t(row: Any) -> Optional[float]:
    """A tape row's observation time, as epoch seconds."""
    if not isinstance(row, Mapping):
        return None
    for k in ("t_epoch", "observed_t", "ts"):
        v = _f(row.get(k))
        if v is not None:
            return v
    t = row.get("t")
    v = _f(t)
    if v is not None:
        return v
    if isinstance(t, str):
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(t).timestamp()
        except Exception:                                      # noqa: BLE001
            return None
    return None


# ------------------------------------------------------------- coverage

def coverage(rows: Sequence[Mapping[str, Any]], *,
             dark_threshold_s: float = MAX_OBSERVATION_AGE_S) -> Dict[str, Any]:
    """What a window's tape actually observed, and what it may be asked.

    `dark_s` counts only the gaps longer than `dark_threshold_s`, so it is
    the time during which no observation was within that threshold of being
    current -- which is exactly the time a second-level question cannot be
    answered from this tape.
    """
    ts = sorted(t for t in (_t(r) for r in rows) if t is not None)
    out: Dict[str, Any] = {"observations": len(ts), "labels": LABELS}
    if len(ts) < 2:
        out.update({"span_s": 0.0, "median_gap_s": None, "max_gap_s": None,
                    "dark_s": 0.0, "dark_fraction": None,
                    "usable_for_second_level": False,
                    "why": "fewer than two observations"})
        return out
    gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    span = ts[-1] - ts[0]
    dark = sum(g for g in gaps if g > dark_threshold_s)
    srt = sorted(gaps)
    mid = len(srt) // 2
    med = srt[mid] if len(srt) % 2 else (srt[mid - 1] + srt[mid]) / 2.0
    frac = (dark / span) if span > 0 else None
    out.update({
        "span_s": round(span, 3),
        "median_gap_s": round(med, 3),
        "max_gap_s": round(max(gaps), 3),
        "dark_s": round(dark, 3),
        "dark_fraction": None if frac is None else round(frac, 4),
        "dark_threshold_s": dark_threshold_s,
        "usable_for_second_level": bool(
            frac is not None and frac < DARK_FRACTION_UNUSABLE
            and med <= dark_threshold_s),
        "why": ("a tape dark for most of a window, or sampling slower than "
                "the staleness bound, answers 'some point in this window' "
                "and not 'at this second'"),
    })
    return out


def frozen_runs(rows: Sequence[Mapping[str, Any]],
                keys: Sequence[str] = ("bid", "ask", "bid_shares",
                                       "ask_shares", "shares_traded"),
                ) -> Dict[str, Any]:
    """Consecutive byte-identical observations -- the duplicate-read mark.

    A sampler that reads the same endpoint twice a couple of seconds apart
    produces pairs of identical rows. That is the signature to look for
    before trusting any one row: at 2230z the pair straddling our fill was
    identical including `shares_traded`, which is how a frozen snapshot
    announces itself.
    """
    runs: List[int] = []
    cur = 1
    prev: Optional[tuple] = None
    for r in rows:
        k = tuple(str(r.get(x)) for x in keys) if isinstance(r, Mapping) else None
        if prev is not None and k == prev:
            cur += 1
        else:
            if cur > 1:
                runs.append(cur)
            cur = 1
        prev = k
    if cur > 1:
        runs.append(cur)
    n = sum(runs)
    return {"runs": len(runs), "rows_in_runs": n, "longest_run": max(runs) if runs else 0,
            "rows": len(rows),
            "fraction_in_runs": round(n / len(rows), 4) if rows else None,
            "labels": LABELS}


# -------------------------------------------------- staleness as refusal

def observation_at(rows: Sequence[Mapping[str, Any]], at_t: Any, *,
                   max_age_s: float = MAX_OBSERVATION_AGE_S) -> Dict[str, Any]:
    """The newest observation at or before `at_t`, or a named refusal.

    Refuses rather than reaching forward: an observation AFTER the instant
    priced is not evidence of what was tradable at it, and silently using
    the nearest row in either direction is how a 16-second hole becomes a
    confident price.
    """
    t = _f(at_t)
    if t is None:
        return {"ok": False, "reason": UNREADABLE, "at_t": None,
                "labels": LABELS}
    best = None
    best_t = None
    for r in rows:
        rt = _t(r)
        if rt is None or rt > t:
            continue
        if best_t is None or rt > best_t:
            best, best_t = r, rt
    if best is None:
        return {"ok": False, "reason": NO_OBS, "at_t": t,
                "observations": len(rows), "labels": LABELS}
    age = t - best_t
    if age > max_age_s:
        return {"ok": False, "reason": STALE, "at_t": t,
                "observation_t": best_t, "age_s": round(age, 4),
                "max_age_s": max_age_s, "row": dict(best), "labels": LABELS}
    return {"ok": True, "at_t": t, "observation_t": best_t,
            "age_s": round(age, 4), "max_age_s": max_age_s,
            "row": dict(best), "frozen_duplicate": _is_duplicate(rows, best),
            "labels": LABELS}


def _is_duplicate(rows: Sequence[Mapping[str, Any]],
                  row: Mapping[str, Any],
                  keys: Sequence[str] = ("bid", "ask", "bid_shares",
                                         "ask_shares", "shares_traded"),
                  ) -> Optional[bool]:
    """Does this observation repeat the one before it, field for field?

    A fresh-LOOKING row that merely repeats its predecessor is the mark of a
    cached endpoint rather than a quiet book, and at 2230z the pair
    straddling our fill was identical including `shares_traded`.
    """
    seq = [r for r in rows if isinstance(r, Mapping)]
    try:
        i = next(n for n, r in enumerate(seq) if r is row)
    except StopIteration:
        return None
    if i == 0:
        return False
    k = lambda r: tuple(str(r.get(x)) for x in keys)           # noqa: E731
    return k(seq[i]) == k(seq[i - 1])


def complement_own_px(row: Mapping[str, Any], held_side: str, *,
                      mode: str = "maker") -> Optional[D]:
    """Own cost of one complement contract at this observation.

    Wire is YES/UP. Buying DOWN as a maker joins the UP ask, so it costs
    1 - ask; crossing for DOWN takes the UP bid, so it costs 1 - bid.
    Buying UP as a maker joins the UP bid at `bid`; crossing takes `ask`.
    Getting this inversion backwards is the classic error, so both modes
    are named rather than inferred.
    """
    bid, ask = _d(row.get("bid")), _d(row.get("ask"))
    held = str(held_side).strip().upper()
    comp = DOWN if held == UP else UP
    if comp == DOWN:
        px = ask if mode == "maker" else bid
        return None if px is None else ONE - px
    return (bid if mode == "maker" else ask)


def price_completion_from_tape(*, rows: Sequence[Mapping[str, Any]],
                               at_t: Any, held_side: str,
                               first_leg_own_px: Any,
                               mode: str = "maker",
                               max_age_s: float = MAX_OBSERVATION_AGE_S,
                               require_second_level: bool = True,
                               ) -> Dict[str, Any]:
    """The pair a completion would have cost, or why the tape cannot say.

    This is the call that should have been used at 2230z, and it refuses
    there TWICE OVER.

    The age bound alone is not enough, and that is the lesson of the
    incident rather than a detail. At the fill instant the nearest
    observation was only 0.27s old, comfortably inside any staleness bound,
    and it still said 0.94 against the authenticated book's 1.06 at the same
    second -- because the REST endpoint serves a LAGGED book, which no
    measurement of observation age can detect. Its very next read, 2s later,
    repeated the row byte for byte and reported zero shares traded while
    3,599 traded before the following observation.

    So the binding refusal is `require_second_level`: a tape dark for 92.6%
    of the window and sampling at a 16.6s median may not be asked what was
    tradable at a named second AT ALL, however fresh the one row nearest to
    it happens to look. Pass False only to ask a window-level question, and
    say so in whatever is reported.
    """
    obs = observation_at(rows, at_t, max_age_s=max_age_s)
    out: Dict[str, Any] = {"held_side": str(held_side).strip().upper(),
                           "mode": mode, "observation": obs, "labels": LABELS}
    if require_second_level:
        cov = coverage(rows, dark_threshold_s=max_age_s)
        out["coverage"] = cov
        if not cov.get("usable_for_second_level"):
            out.update({"ok": False, "reason": NOT_SECOND_LEVEL,
                        "pair_own_cost": None,
                        "why": ("this tape cannot answer a second-level "
                                "question; an authenticated book record is "
                                "the only source that can")})
            return out
    if not obs.get("ok"):
        out.update({"ok": False, "reason": obs.get("reason"),
                    "pair_own_cost": None})
        return out
    comp = complement_own_px(obs["row"], held_side, mode=mode)
    first = _d(first_leg_own_px)
    if comp is None or first is None:
        out.update({"ok": False, "reason": UNREADABLE, "pair_own_cost": None})
        return out
    out.update({"ok": True, "reason": None,
                "complement_own_px": str(comp),
                "first_leg_own_px": str(first),
                "pair_own_cost": str(first + comp),
                "conditional_on": ("a maker fill the displayed book does "
                                   "not guarantee" if mode == "maker"
                                   else "displayed depth at observation"),
                })
    return out
