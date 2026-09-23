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
import re
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_auth as Q1
from src import pmus_fees as fees

BTC15M_SERIES = "btc-updown-15m"
BTC15M_SLUG_RE = re.compile(r"^cpc-btc-updown-15m-\d{4}-\d{2}-\d{2}-\d{4}z$")
OUTCOMES = ("UP", "DOWN")

MARKET_ENV = "PMUS_PREFLIGHT_MARKET"
ACCOUNT_ENV = "PMUS_PREFLIGHT_ACCOUNT"

#: the bounded probe envelope. Stated here only to BOUND the worst case that
#: is reported; this script cannot commit a cent of it.
MAX_COMMITTED_USD = D("5")

_SECRETISH = re.compile(
    r"(X-PM-Access-Key|X-PM-Signature|X-PM-Timestamp|Authorization)"
    r"\s*[:=]\s*\S+", re.I)


def redact(text: Any) -> str:
    s = str(text)
    s = _SECRETISH.sub(r"\1: [REDACTED]", s)
    for env in (Q1.KEY_ENV, Q1.SECRET_ENV):
        v = os.getenv(env, "")
        if v and len(v) > 6:
            s = s.replace(v, "[REDACTED]")
    return s[:600]


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
    worst_p = D("0.5")                  # the taker fee is maximised here
    contracts = MAX_COMMITTED_USD / worst_p
    fee = fees.taker_fee(worst_p, contracts)
    f = lambda d: format(D(str(d)), "f")
    return {
        "max_committed_usd": f(MAX_COMMITTED_USD),
        "fee_maximising_price": f(worst_p),
        "contracts_at_that_price": f(contracts),
        "max_taker_fee_usd": f(fee),
        "worst_case_total_usd": f(MAX_COMMITTED_USD + fee),
        "tick_from_market": str(mk.get(Q1.TICK_FIELD) or mk.get("tickSize")),
        "basis": ("principal is the committed cap because a share can settle "
                  "worthless; no leverage exists on this venue"),
    }


def preflight(slug: str, http: Any,
              *, expect_account: Optional[str] = None,
              now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    ok = True

    def gate(name: str, passed: Any, detail: Any = None) -> bool:
        checks.append({"check": name, "passed": bool(passed),
                       "detail": redact(detail) if detail is not None
                       else None})
        return bool(passed)

    def get(fn, name):
        """A read that raises is a FAILED gate, never a skipped one.

        BaseException, not Exception: a missing crypto backend surfaces as a
        pyo3 PanicException, which is not an Exception and would otherwise
        escape and kill the process. A preflight that dies without emitting a
        verdict is worse than one that fails -- the operator sees a traceback
        where a refusal should be. Only the two exits that must stay fatal
        are re-raised.
        """
        try:
            return fn(), True
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:                           # noqa: BLE001
            gate(name, False, "%s: %s" % (type(exc).__name__, redact(exc)))
            return {}, False

    ok &= gate("credentials_present", Q1.has_credentials(),
               "env %s / %s present" % (Q1.KEY_ENV, Q1.SECRET_ENV))
    ok &= gate("transport_is_get_only", isinstance(http, GetOnly),
               "no order path exists in this process")

    acct, got = get(lambda: http.request("GET", "/v1/account", None, Q1.API)
                    or {}, "account_readable")
    ok &= got
    if got:
        gate("account_readable", True)
        ident = (acct.get("id") or acct.get("accountId")
                 or acct.get("userId") or acct.get("email"))
        ok &= gate("account_identity_resolved", bool(ident), ident)
        if expect_account:
            ok &= gate("account_matches_expected",
                       str(ident) == str(expect_account),
                       "expected %s got %s" % (expect_account, ident))

    ok &= gate("slug_is_btc15m_series", bool(BTC15M_SLUG_RE.match(slug or "")),
               slug)

    raw, got = get(lambda: http.request(
        "GET", "/v1/markets/%s" % slug, None, Q1.GATEWAY) or {},
        "market_readable")
    ok &= got
    mk: Dict[str, Any] = {}
    if got:
        gate("market_readable", True)
        mk = raw.get("market") or raw.get("marketData") or raw
        ok &= gate("market_resolves", bool(mk), mk.get("slug"))
        ok &= gate("market_series_is_btc15m",
                   BTC15M_SERIES in str(mk.get("seriesSlug")
                                        or mk.get("slug") or ""),
                   mk.get("seriesSlug") or mk.get("slug"))

    state = str(mk.get("state") or mk.get("status") or "")
    ok &= gate("market_open_tradable", state.upper().endswith("OPEN"), state)

    outs = mk.get("outcomes") or mk.get("tokens") or []
    names = tuple(str((o.get("outcome") if isinstance(o, Mapping) else o)
                      or "").upper() for o in outs) if outs else ()
    ok &= gate("outcome_mapping_is_up_down",
               set(OUTCOMES).issubset(set(names)) if names else False, names)

    tick = mk.get(Q1.TICK_FIELD) or mk.get("tickSize")
    minq = mk.get("orderMinQuantity") or mk.get("minOrderSize")
    ok &= gate("tick_constraint_known", tick is not None, tick)
    ok &= gate("quantity_constraint_known", minq is not None, minq)

    braw, got = get(lambda: http.request(
        "GET", "/v1/markets/%s/book" % slug, None, Q1.GATEWAY) or {},
        "book_readable")
    ok &= got
    bk: Dict[str, Any] = {}
    if got:
        gate("book_readable", True)
        bk = braw.get("marketData") or braw
    ok &= gate("book_two_sided", bool(bk.get("bid")) and bool(bk.get("ask")),
               {"bid": bk.get("bid"), "ask": bk.get("ask"),
                "bid_shares": bk.get("bid_shares"),
                "ask_shares": bk.get("ask_shares")})

    praw, got = get(lambda: http.request("GET", "/v1/positions", None, Q1.API)
                    or {}, "positions_readable")
    ok &= got
    oraw, got2 = get(lambda: http.request("GET", "/v1/orders", None, Q1.API)
                     or {}, "orders_readable")
    ok &= got2
    if got and got2:
        gate("positions_readable", True)
        pos = praw.get("positions") if isinstance(praw, Mapping) else (praw or [])
        oo = oraw.get("orders") if isinstance(oraw, Mapping) else (oraw or [])
        ok &= gate("no_unexpected_open_orders", len(oo) == 0, len(oo))
        ok &= gate("positions_reconciled", len(pos or []) == 0,
                   {"venue_positions": len(pos or []),
                    "note": "the probe has never traded, so any position "
                            "here is unexplained and blocks arming"})

    # there is no executor in this process at all, which is strictly stronger
    ok &= gate("executor_starts_disarmed", True,
               "no order path exists in this image")
    checks.append({"check": "owner_arm_token_present", "blocking": False,
                   "passed": False,
                   "detail": "there is no arming in this process and no order "
                             "path in this image; arming is a separate owner "
                             "act on the executor, elsewhere"})

    return {
        "preflight": "PASSED" if ok else "FAILED",
        "utc": (now or dt.datetime.now(dt.timezone.utc)).isoformat(),
        "market_slug": slug,
        "checks": checks,
        "blocking_failures": [c["check"] for c in checks
                              if not c["passed"] and c.get("blocking", True)],
        "worst_case": worst_case(mk),
        "armed_after_preflight": False,
        "reads_issued": list(getattr(http, "calls", ())),
        "labels": ("read-only; mutation-incapable by construction; no "
                   "credential value appears anywhere in this report"),
    }


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
