"""Authenticated PMUS transport for the faithful L5 executor.

REUSES the existing authenticated PMUS infrastructure -- `pmus_q1_executor`'s
Ed25519 request signing, its API/GATEWAY hosts and its credential environment
names -- rather than standing up a second execution stack. What is new here is
only the read-only PREFLIGHT and the arming gate that the faithful executor
needs; the signing itself is not reimplemented.

=== IT STARTS DISARMED, AND FAILS CLOSED ===

`PmusTransport.armed` is False on construction and stays False until BOTH:

  1. a full read-only preflight has PASSED in this process, and
  2. an explicit owner arm token is present in the environment AND matches
     the session id, exactly as the existing executor requires.

Any submit or cancel attempted before that raises `NotArmed`. Every preflight
check is a hard gate: identity, market, tradability, outcome mapping, tick and
quantity constraints, book connectivity, and position/open-order
reconciliation. A check that cannot be evaluated FAILS -- an unknown answer is
never treated as a pass, because the whole point of a preflight is to refuse
when we cannot prove the thing.

=== CREDENTIALS NEVER APPEAR ===

Nothing in this module returns, logs or stores a key or a secret. The
preflight reports only whether credentials are PRESENT, and the account
identity it prints comes from the venue's own response. `redact()` is applied
to every error string that could carry a header or a signature.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_auth as Q1
from src.l5_live_execution import NotArmed

#: the series the faithful probe is scoped to. A different series fails.
BTC15M_SERIES = "btc-updown-15m"
BTC15M_SLUG_RE = re.compile(r"^cpc-btc-updown-15m-\d{4}-\d{2}-\d{2}-\d{4}z$")

#: the two outcomes, and the ONLY mapping this engine accepts
OUTCOMES = ("UP", "DOWN")

#: owner arm token, reusing the existing executor's environment contract
ARM_ENV, SID_ENV, LIVE_ENV = Q1.ARM_ENV, Q1.SID_ENV, Q1.LIVE_ENV

_SECRETISH = re.compile(
    r"(X-PM-Access-Key|X-PM-Signature|X-PM-Timestamp|Authorization)"
    r"\s*[:=]\s*\S+", re.I)


def redact(text: Any) -> str:
    """Strip anything header- or signature-shaped from a string."""
    s = str(text)
    s = _SECRETISH.sub(r"\1: [REDACTED]", s)
    for env in (Q1.KEY_ENV, Q1.SECRET_ENV):
        v = os.getenv(env, "")
        if v and len(v) > 6:
            s = s.replace(v, "[REDACTED]")
    return s[:600]


class PreflightFailed(RuntimeError):
    pass


class _SignedHTTP:
    """Signed GET and POST. Constructed ONLY by arm().

    While DISARMED this transport's http is `pmus_auth.ReadOnlyTransport`,
    which raises on any method but GET -- so a disarmed process does not
    merely decline to send an order, it holds no object able to send one.
    Arming installs this. That is why `pmus_q1_executor` is not imported
    here: it carries place_limit_buy, cancel_all_deadman and run_campaign,
    and the probe needs exactly one bounded order, not a campaign engine.
    """

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None,
                base: str = Q1.API) -> Any:
        import json as _json
        import ssl
        import urllib.request
        m = str(method).upper()
        data = _json.dumps(body).encode() if body is not None else None
        headers = (Q1.signed_headers(m, path) if base == Q1.API
                   else {"User-Agent": "pmus-l5-probe"})
        req = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=m)
        with urllib.request.urlopen(
                req, context=ssl.create_default_context(), timeout=15) as r:
            return _json.loads(r.read() or b"{}")


class PmusTransport:
    """GET-only until armed. Mutations exist but are gated behind preflight."""

    name = "PMUS"

    def __init__(self, *, market_slug: str, http: Optional[Any] = None,
                 now_fn=None) -> None:
        self.market_slug = market_slug
        self._injected = http is not None
        self.http = http or Q1.ReadOnlyTransport()
        self._now = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))
        self.armed = False
        self.preflight_report: Optional[Dict[str, Any]] = None
        self.identity_probe: Dict[str, Any] = {}

    # ------------------------------------------------------------ read only

    def _get(self, path: str, base: str = Q1.API) -> Any:
        return self.http.request("GET", path, None, base)

    def account(self) -> Dict[str, Any]:
        """/v1/account/balances is the route this venue serves.

        An earlier version read /v1/account, /v1/positions and /v1/orders and
        got 404, 404 and 501. The 501 is the tell: a collection that exists
        but does not answer GET. Those are the paths pmus_q1_executor has
        always used for live campaigns; these now match it.
        """
        return self._get("/v1/account/balances") or {}

    def market(self, slug: str) -> Dict[str, Any]:
        r = self._get("/v1/markets/%s/bbo" % slug, base=Q1.GATEWAY) or {}
        return r.get("marketData") or r.get("market") or r

    def book(self, slug: str) -> Dict[str, Any]:
        r = self._get("/v1/markets/%s/book" % slug, base=Q1.GATEWAY) or {}
        return r.get("marketData") or r

    #: endpoints that could carry a stable account identifier. Probed
    #: read-only and in order; the FIRST that yields one wins. This list is
    #: not a guess at what exists -- an unreachable path simply contributes
    #: nothing and is reported, so the gate stays failed until the venue
    #: actually hands us an identifier.
    IDENTITY_PATHS = ("/v1/account", "/v1/account/profile", "/v1/me",
                      "/v1/user", "/v1/account/info")

    #: keys that count as an account identifier wherever they appear
    IDENTITY_KEYS = ("accountId", "account_id", "userId", "user_id",
                     "id", "email", "walletAddress", "proxyWallet")

    @staticmethod
    def _find_identity(doc: Any, depth: int = 0) -> Optional[str]:
        """The first identifier-shaped field, anywhere in the document."""
        if depth > 4 or not isinstance(doc, (Mapping, list)):
            return None
        if isinstance(doc, list):
            for item in doc:
                got = PmusTransport._find_identity(item, depth + 1)
                if got:
                    return got
            return None
        for k in PmusTransport.IDENTITY_KEYS:
            v = doc.get(k)
            if isinstance(v, (str, int)) and str(v).strip():
                return str(v)
        for v in doc.values():
            got = PmusTransport._find_identity(v, depth + 1)
            if got:
                return got
        return None

    def account_identity(self, balances: Mapping[str, Any]) -> Optional[str]:
        """An identifier the VENUE gave us, or None -- never one we made up.

        `/v1/account/balances` is proven readable but carried no identifier in
        its top level or its balances entries, so the gate failed with a null
        detail and no way to tell whether the field was missing or merely
        somewhere else. This searches the whole balances document first, then
        probes the candidate account endpoints read-only. `identity_probe`
        records exactly what each one answered, so the next run binds to a
        real field instead of another guess.
        """
        self.identity_probe = {"balances_keys": sorted(
            [str(k) for k in balances.keys()])[:20] if isinstance(
                balances, Mapping) else None}
        got = self._find_identity(balances)
        if got:
            self.identity_probe["source"] = "/v1/account/balances"
            return got
        for path in self.IDENTITY_PATHS:
            try:
                doc = self._get(path) or {}
            except Exception as exc:                           # noqa: BLE001
                self.identity_probe[path] = "%s" % type(exc).__name__
                continue
            keys = sorted([str(k) for k in doc.keys()])[:20] \
                if isinstance(doc, Mapping) else "non-mapping"
            self.identity_probe[path] = {"ok": True, "keys": keys}
            got = self._find_identity(doc)
            if got:
                self.identity_probe["source"] = path
                return got
        return None

    def positions(self) -> List[dict]:
        r = self._get("/v1/portfolio/positions") or {}
        return (r.get("positions") or []) if isinstance(r, Mapping) \
            else (r or [])

    def open_orders(self) -> List[dict]:
        r = self._get("/v1/orders/open") or {}
        if isinstance(r, Mapping):
            return r.get("orders") or r.get("openOrders") or []
        return r or []

    # ------------------------------------------------------------ preflight

    def preflight(self, *, expect_account: Optional[str] = None,
                  known_positions: Optional[Mapping[str, Any]] = None
                  ) -> Dict[str, Any]:
        """Every gate, read-only. Any failure leaves the transport DISARMED."""
        checks: List[Dict[str, Any]] = []

        def gate(name: str, ok: bool, detail: Any = None) -> bool:
            checks.append({"check": name, "passed": bool(ok),
                           "detail": redact(detail) if detail is not None
                           else None})
            return bool(ok)

        ok = True
        # 1. credentials PRESENT (never their value)
        ok &= gate("credentials_present", Q1.has_credentials(),
                   "env %s / %s" % (Q1.KEY_ENV, Q1.SECRET_ENV))

        # 2. authenticated account identity, from the venue's own answer
        acct: Dict[str, Any] = {}
        if ok:
            try:
                acct = self.account()
            except Exception as exc:                           # noqa: BLE001
                return self._fail(checks, gate, "account_readable", exc)
            ident = self.account_identity(acct)
            ok &= gate("account_identity_resolved", bool(ident), ident)
            if expect_account is not None:
                ok &= gate("account_matches_expected",
                           str(ident) == str(expect_account),
                           "expected %s got %s" % (expect_account, ident))

        # 3/4. intended series and current market slug
        ok &= gate("slug_is_btc15m_series",
                   bool(BTC15M_SLUG_RE.match(self.market_slug or "")),
                   self.market_slug)
        mk: Dict[str, Any] = {}
        if ok:
            try:
                mk = self.market(self.market_slug)
            except Exception as exc:                           # noqa: BLE001
                return self._fail(checks, gate, "market_readable", exc)
            ok &= gate("market_resolves", bool(mk), mk.get("slug"))
            ok &= gate("market_series_is_btc15m",
                       BTC15M_SERIES in str(mk.get("seriesSlug")
                                            or mk.get("slug") or ""),
                       mk.get("seriesSlug") or mk.get("slug"))

        # 5. open / tradable
        state = str(mk.get("state") or mk.get("status") or "")
        ok &= gate("market_open_tradable", state.upper().endswith("OPEN"),
                   state)

        # 6. outcome mapping
        outs = mk.get("outcomes") or mk.get("tokens") or []
        names = tuple(str((o.get("outcome") if isinstance(o, Mapping) else o)
                          or "").upper() for o in outs) if outs else ()
        ok &= gate("outcome_mapping_is_up_down",
                   set(OUTCOMES).issubset(set(names)) if names else False,
                   names)

        # 7. tick and quantity constraints, read from the MARKET not assumed
        tick = mk.get(Q1.TICK_FIELD) or mk.get("tickSize")
        minq = mk.get("orderMinQuantity") or mk.get("minOrderSize")
        ok &= gate("tick_constraint_known", tick is not None, tick)
        ok &= gate("quantity_constraint_known", minq is not None, minq)

        # 8. authenticated book / read connectivity
        bk: Dict[str, Any] = {}
        if ok:
            try:
                bk = self.book(self.market_slug)
            except Exception as exc:                           # noqa: BLE001
                return self._fail(checks, gate, "book_readable", exc)
        ok &= gate("book_two_sided",
                   bool(bk.get("bid")) and bool(bk.get("ask")),
                   {"bid": bk.get("bid"), "ask": bk.get("ask")})

        # 9. venue positions / open orders reconciled
        try:
            pos = self.positions()
            oo = self.open_orders()
        except Exception as exc:                               # noqa: BLE001
            return self._fail(checks, gate, "positions_readable", exc)
        ok &= gate("no_unexpected_open_orders", len(oo) == 0, len(oo))
        if known_positions is not None:
            venue = {"%s|%s" % (p.get("market"), p.get("side")): str(p.get("qty"))
                     for p in pos}
            drift = {k: {"ours": str(v), "venue": venue.get(k, "0")}
                     for k, v in known_positions.items()
                     if str(v) != venue.get(k, "0")}
            ok &= gate("positions_reconciled", not drift, drift)

        # 10. starts disarmed, and the owner arm is a separate act
        ok &= gate("executor_starts_disarmed", self.armed is False)
        owner_armed = Q1.armed()
        checks.append({"check": "owner_arm_token_present",
                       "passed": owner_armed, "blocking": False,
                       "detail": "arming is a separate owner act; preflight "
                                 "passing does NOT arm"})

        report = {
            "preflight": "PASSED" if ok else "FAILED",
            "utc": self._now().isoformat(),
            "market_slug": self.market_slug,
            "checks": checks,
            "worst_case": self.worst_case_from_venue(mk),
            "identity_probe": self.identity_probe,
            "armed_after_preflight": False,
            "labels": ("read-only; no credential value appears anywhere in "
                       "this report"),
        }
        self.preflight_report = report
        if not ok:
            raise PreflightFailed(json.dumps(report))
        return report

    def _fail(self, checks, gate, name, exc) -> Dict[str, Any]:
        gate(name, False, "%s: %s" % (type(exc).__name__, redact(exc)))
        report = {"preflight": "FAILED", "checks": checks,
                  "market_slug": self.market_slug,
                  "armed_after_preflight": False}
        self.preflight_report = report
        raise PreflightFailed(json.dumps(report))

    def worst_case_from_venue(self, mk: Mapping[str, Any]) -> Dict[str, Any]:
        """Worst case from the ACTUAL constraints, not from an estimate.

        Principal: every share can settle worthless, so the cap on committed
        dollars IS the principal at risk. Fee: the venue's own taker formula
        is maximised at p = 0.5, so the bound is computed there against the
        largest contract count the cap can fund at that price.
        """
        from src import pmus_fees as fees
        cap = D("5")
        worst_p = D("0.5")
        contracts = cap / worst_p
        fee = fees.taker_fee(worst_p, contracts)
        # format(..., "f") not str(): Decimal renders 5/0.5 as "1E+1",
        # which is correct and unreadable in a report a human has to check
        # before money is at risk.
        f = lambda d: format(D(str(d)), "f")
        return {
            "max_committed_usd": f(cap),
            "fee_maximising_price": f(worst_p),
            "contracts_at_that_price": f(contracts),
            "max_taker_fee_usd": f(fee),
            "worst_case_total_usd": f(cap + fee),
            "tick_from_market": str(mk.get(Q1.TICK_FIELD)
                                    or mk.get("tickSize")),
            "basis": ("principal is the committed cap because a share can "
                      "settle worthless; no leverage exists on this venue"),
        }

    # ------------------------------------------------------------ mutations

    def _require_armed(self) -> None:
        if not self.armed:
            raise NotArmed(
                "transport is DISARMED. Preflight status: %s; owner arm: %s"
                % ((self.preflight_report or {}).get("preflight", "NOT_RUN"),
                   "present" if Q1.armed() else "absent"))

    def arm(self) -> Dict[str, Any]:
        """Arm ONLY if preflight passed in this process AND the owner armed."""
        pf = (self.preflight_report or {}).get("preflight")
        if pf != "PASSED":
            raise NotArmed("preflight has not passed in this process (%s)" % pf)
        if not Q1.armed():
            raise NotArmed("owner arm token absent or does not match the "
                           "session id (%s / %s / %s)"
                           % (LIVE_ENV, SID_ENV, ARM_ENV))
        if not self._injected:
            # only now does an object capable of POST exist in this process
            self.http = _SignedHTTP()
        self.armed = True
        return {"armed": True, "market": self.market_slug,
                "preflight": pf,
                "note": "arming is revocable by unsetting the owner token"}

    def submit(self, intent: Mapping[str, Any]) -> Dict[str, Any]:
        self._require_armed()
        body = {"clientOrderId": intent["client_order_id"],
                "marketSlug": intent.get("market") or self.market_slug,
                "outcome": intent["side"], "side": "BUY",
                "price": str(intent["price"]), "quantity": str(intent["qty"]),
                "type": "LIMIT" if intent.get("mode") == "MAKER" else "MARKET"}
        return self.http.request("POST", "/v1/orders", body, Q1.API) or {}

    def cancel(self, order_id: str) -> Dict[str, Any]:
        """Cancel by the VENUE order id, via the route the venue serves.

        POST /v1/order/{id}/cancel, not DELETE /v1/orders/{id}. The argument
        is the venue's own order id returned by submit, not our deterministic
        client order id -- passing the latter would silently cancel nothing.
        """
        self._require_armed()
        return self.http.request(
            "POST", "/v1/order/%s/cancel" % order_id, {}, Q1.API) or {}
