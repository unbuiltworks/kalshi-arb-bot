"""Read-only authenticated PMUS identity/market preflight, run WHERE THE
CREDENTIALS ALREADY LIVE.

The credentials live on the evidence recorder and must not be copied anywhere
else, so the preflight has to run there. But the recorder is the last service
that should gain an order path. So this is NOT the executor: it is a separate
script that reuses `pmus_q1_executor`'s Ed25519 signing and hosts and nothing
else, and it is MUTATION-INCAPABLE BY CONSTRUCTION rather than merely
disarmed -- `GetOnly` raises on any method but GET, so there is no code path
in this image that can place, amend or cancel an order. "Disarmed" is a flag
someone can flip; "the function does not exist here" is not.

It answers exactly the questions that must be true before money can move:

  authenticated account identity . intended BTC15m series . current market
  slug . open/tradable . outcome mapping . tick/quantity constraints .
  authenticated book connectivity . positions and open orders reconciled .
  executor starts disarmed

A check that cannot be EVALUATED fails. An unknown answer is never a pass:
the purpose of a preflight is to refuse when we cannot prove the thing.

CREDENTIALS NEVER APPEAR. Only their PRESENCE is reported; the account
identity printed is the venue's own answer, and `redact()` is applied to
every error string that could carry a header or a signature.

    PMUS_PREFLIGHT_MARKET   override the market slug (default: current window)
    PMUS_PREFLIGHT_ACCOUNT  assert the venue reports THIS account identity

Exit 0 only when every blocking gate passed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_auth as Q1
from src import pmus_btc15m_preflight as G

BTC15M_SERIES = G.BTC15M_SERIES
BTC15M_SLUG_RE = G.BTC15M_SLUG_RE
OUTCOMES = ("UP", "DOWN")

MARKET_ENV = "PMUS_PREFLIGHT_MARKET"
ACCOUNT_ENV = "PMUS_PREFLIGHT_ACCOUNT"

#: the bounded probe envelope. Stated here only to BOUND the worst case that
#: is reported; this script cannot commit a cent of it.
MAX_COMMITTED_USD = G.MAX_COMMITTED_USD

redact = G.redact


class MutationAttempted(RuntimeError):
    """Raised if anything in this process tries to change venue state."""


class GetOnly:
    """A transport that structurally cannot mutate.

    Wrapping rather than trusting a flag: the recorder holds the credentials,
    so the guarantee that it cannot trade should not depend on a boolean
    being in the right state at the right moment.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: List[str] = []

    def request(self, method: str, path: str, body: Any = None,
                base: Optional[str] = None) -> Any:
        if str(method).upper() != "GET":
            raise MutationAttempted(
                "%s %s refused: this process is read-only" % (method, path))
        self.calls.append("%s %s" % (method, path))
        return self.inner.request("GET", path, None, base)


def current_window_slug(now: Optional[dt.datetime] = None) -> str:
    """The BTC15m market that is open right now, from the clock.

    The market rotates every fifteen minutes, so a hardcoded slug would be
    stale before it was read.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    start = now.replace(minute=(now.minute // 15) * 15, second=0,
                        microsecond=0)
    return "cpc-btc-updown-15m-%s-%02d%02dz" % (
        start.strftime("%Y-%m-%d"), start.hour, start.minute)


def worst_case(mk: Mapping[str, Any]) -> Dict[str, Any]:
    """From the venue's own fee implementation, not from an estimate."""
    return G.worst_case(mk)


def preflight(slug: str, http: Any,
              *, expect_account: Optional[str] = None,
              now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """The SAME gates the production transport evaluates
    (`pmus_btc15m_preflight.run`), issued through the GET-only wrapper.

    The recorder holds no WebSocket book reference here, so the book gate reads
    the SIGNED REST book; the public gateway is never consulted.
    """
    get_only = isinstance(http, GetOnly)
    pre = [{"check": "transport_is_get_only", "passed": get_only,
            "detail": "no order path exists in this process"}]
    rep = G.run(lambda path: http.request("GET", path, None, Q1.API), slug,
                expect_account=expect_account, extra_checks=pre, now=now)
    rep.pop("_market", None)
    # there is no executor in this process at all, which is strictly stronger
    rep["checks"].append({"check": "executor_starts_disarmed", "passed": True,
                          "detail": "no order path exists in this image"})
    rep["checks"].append({
        "check": "owner_arm_token_present", "blocking": False, "passed": False,
        "detail": "there is no arming in this process and no order path in "
                  "this image; arming is a separate owner act, elsewhere"})
    if not get_only:
        rep["preflight"] = "FAILED"
    rep["reads_issued"] = list(getattr(http, "calls", ()))
    rep["labels"] = ("read-only; mutation-incapable by construction; no "
                     "credential value appears anywhere in this report")
    return rep


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    slug = (argv[0] if argv else os.getenv(MARKET_ENV, "").strip()
            or current_window_slug())
    try:
        # constructed INSIDE the guard: a transport that cannot be built is
        # still a refusal we must report, not a traceback
        http = GetOnly(Q1.ReadOnlyTransport())
        rep = preflight(slug, http,
                        expect_account=os.getenv(ACCOUNT_ENV, "").strip()
                        or None)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:                               # noqa: BLE001
        # ALWAYS emit a verdict. This runs unattended at recorder boot, so a
        # bare traceback would be read as "the preflight did not run" when it
        # in fact ran and refused.
        print("PMUS_PREFLIGHT " + json.dumps(
            {"preflight": "FAILED", "market_slug": slug,
             "fatal": "%s: %s" % (type(exc).__name__, redact(exc)),
             "armed_after_preflight": False}), flush=True)
        return 2
    print("PMUS_PREFLIGHT " + json.dumps(rep, default=str), flush=True)
    return 0 if rep["preflight"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
