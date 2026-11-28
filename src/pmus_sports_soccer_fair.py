"""Regulation-time three-way pricing; caller must verify event and settlement identity."""
from src.pmus_sports_sharp import devig, norm, _ts, MAX_ODDS_AGE_S

def soccer_fair(book, *, home, away, selection, now):
    """Return probability of home win, draw or away win; never infer a binary match."""
    try:
        h, a = norm(home), norm(away)
        if not h or not a or len({h, a, "draw"}) != 3:
            return None
        target = {"home": h, "away": a, "draw": "draw"}.get(selection)
        if target is None:
            return None
        matches = []
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue
            stamp = _ts(market.get("last_update") or book.get("last_update"))
            if stamp is None or not 0 <= now - stamp <= MAX_ODDS_AGE_S:
                continue
            outcomes = market.get("outcomes", [])
            names = [norm(o.get("name")) for o in outcomes]
            if len(names) != 3 or set(names) != {h, a, "draw"}:
                continue
            probs = devig([o.get("price") for o in outcomes])
            if probs is not None:
                matches.append(dict(zip(names, probs))[target])
        return matches[0] if len(matches) == 1 else None
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        return None
