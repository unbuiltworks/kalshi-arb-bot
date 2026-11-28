"""Pure pricing helper. Caller must independently match event and settlement rules.
No network, orders, or production integration."""
from decimal import Decimal as D
from src.pmus_sports_sharp import devig, norm, _ts, MAX_ODDS_AGE_S

def line_fair(book, *, kind, selection, point, now, opponent=None):
    """Return fair probability for an exact two-way half-point line.
    Integer lines can push and are deliberately unsupported."""
    try:
        p = D(str(point))
        if not p.is_finite() or abs(p) % 1 != D("0.5"):
            return None
        if kind not in ("spreads", "totals"):
            return None
        name = norm(selection)
        other = norm(opponent) if kind == "spreads" else (
            "under" if name == "over" else "over")
        if not name or not other or name == other:
            return None
        if kind == "totals" and name not in ("over", "under"):
            return None
        matches = []
        for market in book.get("markets", []):
            if market.get("key") != kind:
                continue
            stamp = _ts(market.get("last_update") or book.get("last_update"))
            if stamp is None or not 0 <= now - stamp <= MAX_ODDS_AGE_S:
                continue
            outcomes = market.get("outcomes", [])
            a = [o for o in outcomes if norm(o.get("name")) == name
                 and D(str(o.get("point"))) == p]
            b = [o for o in outcomes if norm(o.get("name")) == other
                 and D(str(o.get("point"))) == (-p if kind == "spreads" else p)]
            if len(a) == len(b) == 1:
                probs = devig([a[0].get("price"), b[0].get("price")])
                if probs is not None:
                    matches.append(probs[0])
        return matches[0] if len(matches) == 1 else None
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        return None


def prop_fair(book, *, market_key, player, selection, point, now):
    """Price an already event-matched player prop. Exact provider stat key,
    player and half-point threshold required. Settlement equivalence is
    the caller's responsibility; no production integration."""
    try:
        p = D(str(point))
        name = norm(player)
        side = norm(selection)
        if (not isinstance(market_key, str) or not market_key.startswith("player_")
                or not name or side not in ("over", "under")
                or not p.is_finite() or p < 0 or p % 1 != D("0.5")):
            return None
        other = "under" if side == "over" else "over"
        matches = []
        for market in book.get("markets", []):
            if market.get("key") != market_key:
                continue
            stamp = _ts(market.get("last_update") or book.get("last_update"))
            if stamp is None or not 0 <= now - stamp <= MAX_ODDS_AGE_S:
                continue
            outcomes = [o for o in market.get("outcomes", [])
                        if norm(o.get("description")) == name]
            a = [o for o in outcomes if norm(o.get("name")) == side
                 and D(str(o.get("point"))) == p]
            b = [o for o in outcomes if norm(o.get("name")) == other
                 and D(str(o.get("point"))) == p]
            if len(a) == len(b) == 1:
                probs = devig([a[0].get("price"), b[0].get("price")])
                if probs is not None and all(x.is_finite() for x in probs):
                    matches.append(probs[0])
        return matches[0] if len(matches) == 1 else None
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        return None
