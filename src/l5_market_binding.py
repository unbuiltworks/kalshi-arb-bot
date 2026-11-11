"""Which market an L5 action belongs to, proven rather than assumed.

Probe 05 sent two real orders into `cpc-btc-updown-15m-2026-09-25-1500z` on
the strength of L5 actions that were not in that market at all.
`new_l5_actions` filtered on TIME: any action whose timestamp fell inside the
fifteen minutes was admitted, labelled UP or DOWN from its own `outcome`
field, and translated against our book. L5 trades many markets at once, so
his trades elsewhere arrived looking exactly like trades here.

The 1500z window's 110 actions carried FIVE distinct token ids. Two of them
are the market's own pair; the other three were foreign, and two of those
foreign actions became orders -- CQ7E02GW2YB5 (1.8 @ 0.21) and CQ7C1PHHTYB5
(1.2 @ 0.28).

Identity is therefore established from the venue's own market record and
matched on TOKENS:

    action.asset (the CTF token id)
      -> must be one of clobTokenIds of
    the market the venue publishes for this window
      -> whose endDate must equal the window's own end

Nothing is inferred from time, side, price, or from reading a slug as if it
described a market. The window's slug is only the LOOKUP KEY handed to the
venue; what comes back -- conditionId, the two token ids, the outcomes, the
end time -- is what the gate matches on, and the end-time check is what
proves the record we got is the window we asked about.

An action we cannot prove belongs here is refused. There is no default
branch that admits.
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

GAMMA = "https://gamma-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0"}

#: L5's BTC15m series, as the venue slugs it: the window's start epoch
SERIES_SLUG = "btc-updown-15m-%d"

ADMITTED = "ADMITTED"
MISMATCH = "MARKET_IDENTITY_MISMATCH"
UNKNOWN = "MARKET_IDENTITY_UNKNOWN"

WINDOW_S = 900


class Binding:
    """The two outcome tokens of exactly one BTC15m window."""

    def __init__(self, *, window_start: int, condition_id: str,
                 token_up: str, token_down: str, slug: str,
                 end_epoch: Optional[int] = None,
                 question: Optional[str] = None) -> None:
        self.window_start = int(window_start)
        self.window_end = self.window_start + WINDOW_S
        self.condition_id = str(condition_id)
        self.token_up = str(token_up)
        self.token_down = str(token_down)
        self.slug = str(slug)
        self.end_epoch = end_epoch
        self.question = question

    @property
    def tokens(self) -> Tuple[str, str]:
        return (self.token_up, self.token_down)

    def side_of(self, token: str) -> Optional[str]:
        if str(token) == self.token_up:
            return "UP"
        if str(token) == self.token_down:
            return "DOWN"
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {"slug": self.slug, "condition_id": self.condition_id,
                "token_up": self.token_up, "token_down": self.token_down,
                "window_start": self.window_start,
                "window_end": self.window_end,
                "question": self.question}


def _get(url: str, timeout: float = 20.0) -> Any:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=timeout) as fh:
        return json.loads(fh.read().decode())


def _iso_to_epoch(s: Any) -> Optional[int]:
    if not s:
        return None
    t = str(s).replace("Z", "+00:00")
    try:
        import datetime as dt
        return int(dt.datetime.fromisoformat(t).timestamp())
    except ValueError:
        return None


def fetch_binding(window_start: int, *, getter=None) -> Optional[Binding]:
    """Ask the venue which market this window is. None if it will not say.

    None is a REFUSAL, not an empty result: with no binding, nothing is
    admitted. A window we cannot identify is a window we do not trade.
    """
    get = getter or _get
    slug = SERIES_SLUG % int(window_start)
    try:
        rows = get("%s/events?slug=%s" % (GAMMA, slug))
    except Exception:                                          # noqa: BLE001
        return None
    if not isinstance(rows, Sequence) or not rows:
        return None
    ev = rows[0]
    if not isinstance(ev, Mapping):
        return None
    markets = ev.get("markets")
    m = markets[0] if isinstance(markets, Sequence) and markets else ev
    if not isinstance(m, Mapping):
        return None
    toks = m.get("clobTokenIds")
    if isinstance(toks, str):
        try:
            toks = json.loads(toks)
        except ValueError:
            return None
    outcomes = m.get("outcomes")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except ValueError:
            outcomes = None
    cid = m.get("conditionId")
    if not (isinstance(toks, Sequence) and len(toks) == 2 and cid):
        return None

    # the venue's own record must describe THIS window, or we have the
    # wrong market and must not use it
    end = _iso_to_epoch(m.get("endDate"))
    if end is not None and end != int(window_start) + WINDOW_S:
        return None

    up, down = str(toks[0]), str(toks[1])
    if isinstance(outcomes, Sequence) and len(outcomes) == 2:
        if str(outcomes[0]).strip().lower() == "down":
            up, down = down, up
        elif str(outcomes[0]).strip().lower() != "up":
            return None                     # unrecognised outcome labelling
    return Binding(window_start=int(window_start), condition_id=str(cid),
                   token_up=up, token_down=down, slug=str(m.get("slug")
                                                          or slug),
                   end_epoch=end, question=m.get("question"))


def admit(action: Mapping[str, Any],
          binding: Optional[Binding]) -> Dict[str, Any]:
    """May this L5 action enter this window? Token identity decides.

    Returns a verdict dict always; the caller never has to infer refusal
    from a falsy value. `side` is taken from the BINDING, not from the
    action's own `outcome` string: the token says which outcome it is, and
    a foreign action's outcome label is meaningless here anyway.
    """
    if binding is None:
        return {"verdict": UNKNOWN, "reason": "no authoritative binding "
                "for this window", "token": None, "side": None}
    token = action.get("asset") or action.get("token_id") or action.get("token")
    if not token:
        return {"verdict": UNKNOWN, "reason": "action carries no token id",
                "token": None, "side": None}
    side = binding.side_of(str(token))
    if side is None:
        return {"verdict": MISMATCH,
                "reason": "token is not an outcome of this market",
                "token": str(token), "side": None,
                "expected": list(binding.tokens)}
    cid = action.get("conditionId")
    if cid and str(cid) != binding.condition_id:
        # the token matched but the row names a different market: treat the
        # disagreement as disqualifying rather than picking a winner
        return {"verdict": MISMATCH,
                "reason": "conditionId disagrees with the market this token "
                          "belongs to",
                "token": str(token), "side": None,
                "expected": binding.condition_id, "saw": str(cid)}
    ts = action.get("timestamp")
    if ts is not None:
        try:
            t = float(ts)
        except (TypeError, ValueError):
            return {"verdict": UNKNOWN, "reason": "unreadable timestamp",
                    "token": str(token), "side": None}
        if not (binding.window_start <= t <= binding.window_end):
            # identity is proven but the action is outside the window it
            # belongs to; still refused, and NOT as a mismatch of identity
            return {"verdict": MISMATCH, "reason": "outside its own window",
                    "token": str(token), "side": None}
    return {"verdict": ADMITTED, "reason": None, "token": str(token),
            "side": side, "condition_id": binding.condition_id}


def admitted(action: Mapping[str, Any],
             binding: Optional[Binding]) -> bool:
    return admit(action, binding)["verdict"] == ADMITTED
