"""One authorization, one market, spent on use.

The 24/7 observer cannot be armed: it holds no object that can POST, and
nothing in it calls `PmusTransport.arm()`. That is deliberate and stays
true. This module is what a SEPARATE, bounded, one-shot live probe uses to
establish that it may trade exactly once.

The owner's three environment variables (`pmus_auth.armed()`) say WHO
authorized and WHETHER. They cannot say HOW MANY TIMES, because an
environment variable is a standing fact: a service that restarts reads the
same YES again, and a 15-minute market rolls over four times an hour. An
env-only arm is therefore a standing licence to trade every future window,
which is not what "one bounded probe" means.

So authorization is also a LEDGER ENTRY. Claiming writes the session id and
the market to an append-only file on the volume BEFORE any order exists,
and a session id that appears in that file is spent forever. The write
happens first on purpose: if the process dies immediately afterwards, the
authorization is already gone, and the failure mode is a probe that did not
run rather than one that ran twice.

Consequences, each of which is a test:

  - no owner token           -> REFUSED, and no POST object is ever built
  - token, first market      -> CLAIMED, once
  - same token, next window  -> REFUSED: AUTHORIZATION_ALREADY_USED
  - crash after claiming     -> REFUSED on restart, same reason
  - a different market       -> REFUSED: AUTHORIZATION_BOUND_TO_MARKET

The ledger is evidence as well as a lock: it records what was authorized,
for which market, when it was claimed, and how it ended.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_auth as Q1

#: where the spent-authorization ledger lives. On the probe's volume, so it
#: survives the process that wrote it -- which is the entire point.
LEDGER_ENV = "L5_LIVE_AUTH_LEDGER"
DEFAULT_LEDGER = "/state/pmus-ws/l5_live_authorizations.jsonl"

#: the market the owner authorized. Optional: absent, the probe binds to the
#: window that is open when it claims, and records which one that was.
MARKET_ENV = "L5_LIVE_PROBE_MARKET"


class Refused(RuntimeError):
    """Authorization denied. Carries the reason and the evidence."""

    def __init__(self, reason: str, detail: Any = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def ledger_path() -> str:
    return os.getenv(LEDGER_ENV, "").strip() or DEFAULT_LEDGER


def _read(path: str) -> List[Dict[str, Any]]:
    p = pathlib.Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            # an unreadable ledger is NOT an empty one: a line we cannot
            # parse might be the very claim that would refuse us, so it is
            # carried through as an opaque record rather than dropped.
            out.append({"session_id": None, "unparsed": line[:200]})
    return out


def _append(path: str, rec: Mapping[str, Any]) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(rec), separators=(",", ":"),
                            default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def session_id() -> str:
    return os.getenv(Q1.SID_ENV, "").strip()


def spent(sid: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The claim that already used this session id, if there is one."""
    if not sid:
        return None
    for rec in _read(path or ledger_path()):
        if rec.get("session_id") == sid and rec.get("event") == "CLAIMED":
            return rec
        if rec.get("unparsed") and sid in str(rec.get("unparsed")):
            return rec
    return None


class Authorization:
    """One claim, one market. Not reusable, by construction."""

    def __init__(self, path: Optional[str] = None, now_fn=None) -> None:
        self.path = path or ledger_path()
        self._now = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))
        self.sid: Optional[str] = None
        self.market: Optional[str] = None
        self.claimed = False
        self.consumed = False

    # ------------------------------------------------------------ claiming

    def claim(self, market: str) -> Dict[str, Any]:
        """Spend the authorization on THIS market, or refuse.

        Written before the caller is allowed to arm anything, so the record
        of the attempt cannot be lost by whatever happens next.
        """
        if not Q1.armed():
            raise Refused("OWNER_AUTHORIZATION_ABSENT",
                          {"needs": [Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV],
                           "note": "no order path is built without all three"})
        sid = session_id()
        if not sid:
            raise Refused("OWNER_AUTHORIZATION_ABSENT", {"needs": [Q1.SID_ENV]})
        if not market:
            raise Refused("NO_MARKET", {"note": "a claim names one market"})

        want = os.getenv(MARKET_ENV, "").strip()
        if want and want != market:
            raise Refused("AUTHORIZATION_BOUND_TO_MARKET",
                          {"authorized": want, "attempted": market})

        prior = spent(sid, self.path)
        if prior is not None:
            raise Refused("AUTHORIZATION_ALREADY_USED",
                          {"session_id": sid,
                           "claimed_market": prior.get("market"),
                           "claimed_at": prior.get("utc"),
                           "note": "one authorization trades one market; "
                                   "a new probe needs a new session id"})

        rec = {"event": "CLAIMED", "session_id": sid, "market": market,
               "utc": self._now().isoformat(),
               "note": "written BEFORE arming, so a crash spends it too"}
        _append(self.path, rec)
        self.sid, self.market, self.claimed = sid, market, True
        return dict(rec)

    def check_market(self, market: str) -> None:
        """Refuse any market but the claimed one. Called before every order."""
        if not self.claimed:
            raise Refused("NOT_CLAIMED", {"attempted": market})
        if market != self.market:
            raise Refused("AUTHORIZATION_BOUND_TO_MARKET",
                          {"claimed": self.market, "attempted": market})

    # ----------------------------------------------------------- consuming

    def consume(self, outcome: Mapping[str, Any]) -> Dict[str, Any]:
        """Close the authorization out with what actually happened."""
        rec = {"event": "CONSUMED", "session_id": self.sid,
               "market": self.market, "utc": self._now().isoformat(),
               "outcome": dict(outcome)}
        _append(self.path, rec)
        self.consumed = True
        return dict(rec)

    def as_dict(self) -> Dict[str, Any]:
        return {"claimed": self.claimed, "consumed": self.consumed,
                "market": self.market, "ledger": self.path,
                "session_id_present": bool(self.sid)}
