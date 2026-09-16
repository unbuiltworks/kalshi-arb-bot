"""The live execution mandate: one immutable authorization, enforced at the wire.

WHAT WENT WRONG ON 2026-09-16, because the design here is a direct answer to it.
The owner authorised ONE market at $0.05. Market #1 settled, `advance_gate`
reported clean, and the campaign emitted `campaign_continuation_start` three
times, each loading an owner approval dated **2026-09-06** -- R2C-RUN, R2C2-RUN,
R3L-RUN. The per-market envelope became $7.50 and the daily stop -$15.00, and
eleven more markets traded. Nothing was compromised and no gate was "bypassed":
the dual key only ARMS, and once armed the campaign followed its own stored
ladder. Authorization to *start* was being read as authorization to *continue*.

Then, inside one of those markets, `counts_against_market_cap()` -- an
ALLOW-LIST OF STAGE NAMES -- admitted `spring_open*` and `spring_extend*` but
not `spring_complete_*`. A COMPLETE on this venue is a BUY of the complement:
real cash out. The counter read $7.4476 against its $7.50 cap and kept
admitting while actual money-moving notional reached $19.3295. The pair-cost
invariant that was supposed to bound completes bounds the PRICE PER PAIR, not
the DOLLAR VOLUME -- 20.44 pairs at 0.9457 satisfies `<= 1.00` perfectly while
spending nineteen dollars.

So this module refuses both mistakes structurally:

  1. THE MANDATE IS THE AUTHORIZATION, and it is immutable. Continuation,
     settlement, restart, resume, `advance_gate` and historical rung approvals
     cannot enlarge it, replace it, or extend its life. A market beyond
     `max_total_markets` needs a NEW authorization, which means a human.

  2. THERE IS NO STAGE ALLOW-LIST. Admission reads the ORDER BODY and asks
     what it does economically: if an accepted order can move cash or create
     exposure, it consumes envelope. `spring_complete_*`, an acceptance probe,
     a retry, a resumed session and a stage name invented next year are all the
     same question, because none of them is asked.

  3. ENFORCEMENT IS AT THE TRANSPORT, not at the call sites. Every order in
     this codebase reaches the venue through `t.request("POST", "/v1/orders",
     body)` -- four call sites today, and any number tomorrow. `guard()` wraps
     a transport so the check sits on the wire, where a new call site inherits
     it instead of having to remember it.

  4. IT FAILS CLOSED, EVERYWHERE. No mandate, malformed mandate, expired
     mandate, wrong service, unreadable ledger, unparseable order body, a
     submission whose outcome we never learned -- every one of these refuses or
     charges the full proposed notional. The only way to spend is to be
     positively, currently authorised.

CONSUMPTION IS READ FROM THE DURABLE LEDGER, never from an in-process counter,
which is what makes a restart unable to reset it and a race unable to
double-spend. Admission takes an exclusive lock, re-reads the ledger, decides,
and writes its commitment inside that same critical section.
"""
from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal as D, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Optional

MANDATE_ENV = "PMUS_LIVE_MANDATE"
MANDATE_FILE_ENV = "PMUS_LIVE_MANDATE_FILE"
#: THE AUTHORIZATION LIVES IN THE REPOSITORY BY DEFAULT.
#:
#: A Railway variable was the wrong home for this. It cannot be reviewed in a
#: diff, it has no history, and on 2026-09-16 it could not even be read or
#: written by the session that needed to stop the service. A committed file is
#: reviewable, versioned, survives every restart, and -- because it is COPYed
#: into the image -- changing it requires a build, which is the correct amount
#: of friction for "how much money may this spend".
#:
#: Its ABSENCE is the safe state: no file, no mandate, no orders.
DEFAULT_MANDATE_FILE = "analysis/l5/authorization/live_mandate.json"
ORDER_PATH = "/v1/orders"
ONE = D("1")

#: Every field is required. A mandate missing any of them is not a weaker
#: mandate, it is not a mandate -- there is no default for "how much may this
#: spend", because a default is exactly how $0.05 became $7.50.
REQUIRED_FIELDS = (
    "authorization_id", "authorized_at", "service", "strategy",
    "strategy_version", "max_markets", "max_total_markets",
    "max_spend_per_market", "max_total_spend", "max_residual",
    "max_daily_realized_loss", "expiry",
)

_MONEY = ("max_spend_per_market", "max_total_spend", "max_residual",
          "max_daily_realized_loss")
_COUNT = ("max_markets", "max_total_markets")


class MandateRefusal(Exception):
    """A refusal to submit. Carries the verdict so it can be logged whole."""

    def __init__(self, verdict: Mapping[str, Any]):
        self.verdict = dict(verdict)
        super().__init__(str(verdict.get("reason") or "MANDATE_REFUSED"))


@dataclass(frozen=True)
class Mandate:
    """One authorization. Frozen: nothing in the running system may edit it.

    `frozen=True` is not decoration. The 2026-09-16 failure was a live limit
    being replaced at runtime by a stored approval, so the object that carries
    the limit is made physically unable to accept a new value.
    """
    authorization_id: str
    authorized_at: str
    service: str
    strategy: str
    strategy_version: str
    max_markets: int
    max_total_markets: int
    max_spend_per_market: D
    max_total_spend: D
    max_residual: D
    max_daily_realized_loss: D
    expiry: str

    def as_json(self) -> dict:
        return {k: str(v) for k, v in asdict(self).items()}

    def expired(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        try:
            exp = _parse_ts(self.expiry)
        except Exception:                                    # noqa: BLE001
            return True          # an unreadable expiry is an expired one
        return now >= exp


def _parse_ts(s: str) -> datetime:
    t = str(s).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(t)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_mandate(raw: Any) -> tuple[Optional[Mandate], Optional[str]]:
    """Strict parse. Returns (mandate, refusal_reason); never both."""
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None, "mandate_unparseable_json"
    if not isinstance(raw, Mapping):
        return None, "mandate_not_an_object"
    missing = [f for f in REQUIRED_FIELDS if raw.get(f) in (None, "")]
    if missing:
        return None, "mandate_missing_fields:" + ",".join(sorted(missing))
    vals: dict[str, Any] = {}
    for f in REQUIRED_FIELDS:
        v = raw[f]
        try:
            if f in _MONEY:
                vals[f] = D(str(v))
                if vals[f] < 0:
                    return None, f"mandate_negative:{f}"
            elif f in _COUNT:
                vals[f] = int(v)
                if vals[f] < 0:
                    return None, f"mandate_negative:{f}"
            else:
                vals[f] = str(v)
        except (InvalidOperation, ValueError, TypeError):
            return None, f"mandate_unreadable_field:{f}"
    for f in ("authorized_at", "expiry"):
        try:
            _parse_ts(vals[f])
        except Exception:                                    # noqa: BLE001
            return None, f"mandate_unreadable_timestamp:{f}"
    if vals["max_total_markets"] < vals["max_markets"]:
        return None, "mandate_total_markets_below_concurrent"
    if vals["max_total_spend"] < vals["max_spend_per_market"]:
        return None, "mandate_total_spend_below_per_market"
    return Mandate(**vals), None


def load_mandate(env: Optional[Mapping[str, str]] = None
                 ) -> tuple[Optional[Mandate], Optional[str]]:
    """The ONLY way a mandate enters the process: explicit, current, ours.

    There is deliberately no fallback to a rung table, a campaign record or a
    previous mandate. "We could not read today's authorization, so we used
    yesterday's" is the entire incident in one sentence.
    """
    env = os.environ if env is None else env
    raw = (env.get(MANDATE_ENV) or "").strip()
    if not raw:
        path = (env.get(MANDATE_FILE_ENV) or "").strip() or DEFAULT_MANDATE_FILE
        p = Path(path)
        if not p.exists():
            return None, "no_mandate"
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError as exc:
            return None, f"mandate_file_unreadable:{type(exc).__name__}"
    return parse_mandate(raw)


# --------------------------------------------------------------------------
# economic effect -- what an ORDER does, never what it is called

def order_cash_effect(body: Mapping[str, Any]) -> tuple[Optional[D], Optional[D], Optional[str]]:
    """(cash_at_risk, quantity, error) for one order body.

    NO STAGE, NO ACTION ALLOW-LIST, NO CALLER-SUPPLIED NOTIONAL. The body is
    what the venue will act on, so the body is what gets priced.

    THE VENUE'S PRICE CONVENTION MATTERS HERE AND IS EASY TO GET BACKWARDS.
    `price.value` is always the LONG side's price: a NO order at NO-price X
    goes on the wire as 1 - X. Cash paid for that NO buy is X * qty, i.e.
    (1 - wire) * qty. Pricing a NO buy off the wire value would have
    understated this incident's completes -- they went out at wire 0.57 for a
    NO leg actually costing 0.43... and in the other direction elsewhere. So
    the conversion is undone explicitly rather than assumed away.

    A SELL is not free either: it can create or change exposure, so it is
    charged at its worst case rather than treated as a refund. Cash never
    comes back through this function -- only settlement returns cash, and
    settlement is not an order.
    """
    if not isinstance(body, Mapping):
        return None, None, "order_body_not_an_object"
    qty = _qty(body.get("quantity"))
    if qty is None or qty <= 0:
        return None, None, "order_quantity_unreadable"
    wire = _price(body.get("price"))
    if wire is None or not (D("0") <= wire <= ONE):
        return None, None, "order_price_unreadable"
    side = str(body.get("outcomeSide") or "").upper()
    if not side:
        return None, None, "order_side_missing"
    # outcome-terms price: undo the venue's long-side wire convention
    px = (ONE - wire) if side.endswith("NO") else wire
    action = str(body.get("action") or "").upper()
    if action.endswith("SELL"):
        # worst case for a sell that opens or flips exposure is the whole
        # complement of the price, per share
        return (ONE - px) * qty, qty, None
    if not action.endswith("BUY"):
        # an action we do not recognise is not assumed harmless
        return ONE * qty, qty, None
    return px * qty, qty, None


def _qty(v: Any) -> Optional[D]:
    """Quantity is encoded three different ways across the executors (int,
    float, {"value": str}). All three are read, because a guard that only
    understands one of them silently lets the other two past."""
    if isinstance(v, Mapping):
        v = v.get("value")
    if v is None or isinstance(v, bool):
        return None
    try:
        return D(str(v))
    except InvalidOperation:
        return None


def _price(v: Any) -> Optional[D]:
    if isinstance(v, Mapping):
        v = v.get("value")
    if v is None or isinstance(v, bool):
        return None
    try:
        return D(str(v))
    except InvalidOperation:
        return None


# --------------------------------------------------------------------------
# consumption, read from the durable order ledger

COMMIT_KIND = "mandate_commit"
LINK_KIND = "mandate_order"


def _ledger_path(state_dir: Path) -> Path:
    return Path(state_dir) / "ledger.jsonl"


def _read_rows(state_dir: Path) -> tuple[list[dict], bool]:
    """(rows, readable). An unreadable ledger is not an empty one."""
    p = _ledger_path(state_dir)
    if not p.exists():
        return [], True
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return [], False
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            # a row we cannot read might be a commitment; fail closed
            return out, False
        if isinstance(r, dict):
            out.append(r)
    return out, True


def consumption(state_dir: Path, authorization_id: str) -> dict:
    """What this authorization has already spent, per market and in total.

    CHARGED FROM THE VENUE/ORDER LEDGER, NOT A COUNTER. Every commitment this
    guard wrote is charged at its full proposed notional until a terminal row
    from the venue reconciliation proves a smaller fill. That ordering is what
    makes the accounting safe in both directions:

      - an order whose outcome we never learned keeps costing its full size,
        so an ambiguous submission can never quietly free budget;
      - a FILL is permanent. Cancels and retries release only the UNFILLED
        remainder of their own reservation, so no sequence of retrying,
        cancelling or partially filling can replenish cash already spent.

    Scoped by authorization_id: a previous mandate's spending is not this
    mandate's, and this mandate cannot inherit a previous one's headroom.
    """
    rows, ok = _read_rows(Path(state_dir))
    commits: dict[str, dict] = {}
    links: dict[str, str] = {}              # commit_id -> order_id
    terminal: dict[str, dict] = {}
    settled: set[str] = set()
    fills: dict[str, list[dict]] = {}
    for r in rows:
        k = r.get("kind")
        if k == COMMIT_KIND and r.get("authorization_id") == authorization_id:
            commits[str(r.get("commit_id"))] = r
        elif k == LINK_KIND and r.get("authorization_id") == authorization_id:
            links[str(r.get("commit_id"))] = str(r.get("order_id") or "")
        elif k == "order_terminal":
            terminal[str(r.get("order_id"))] = r
        elif k == "settlement":
            if r.get("slug"):
                settled.add(str(r["slug"]))
        elif k == "filled":
            fills.setdefault(str(r.get("slug")), []).append(r)

    per_market: dict[str, D] = {}
    unknown = 0
    for cid, c in commits.items():
        slug = str(c.get("slug") or "")
        proposed = _dec(c.get("cash_at_risk"))
        if proposed is None:
            unknown += 1
            continue
        oid = links.get(cid) or ""
        term = terminal.get(oid) if oid else None
        if term is None:
            charge = proposed          # never confirmed: charge in full
        else:
            got = _dec(term.get("filled_qty"))
            q = _dec(c.get("quantity"))
            if got is None or q is None or q <= 0:
                charge = proposed
            else:
                # charge the filled fraction of what we reserved; a partial
                # fill keeps its filled part forever, the rest is released
                charge = (proposed * got / q) if got < q else proposed
        per_market[slug] = per_market.get(slug, D("0")) + charge

    # ------------------------------------------------------------------
    # CONCURRENCY vs TOTAL: two different counters, and conflating them is
    # what the 2026-09-16 CP1 probe exposed (OWNER AUTHORIZATION, "fix
    # zero-exposure market concurrency accounting").
    #
    # `markets_open` used to be "committed AND not settled". A market with six
    # admitted orders, ZERO fills, every order cancelled and no inventory then
    # counted against max_concurrent_markets forever -- because a market with
    # nothing in it never settles, so it could never leave the set. The live
    # probe hit exactly that: six maker attempts at qty 0.22 filled nothing,
    # market 1 stayed "open" permanently, market 2 was refused
    # max_concurrent_markets_exhausted, and the campaign stopped.
    #
    # THE INVARIANT NOW: a market is concurrently OPEN while it can still lose
    # money there -- it has a live or unresolved order, or it has fills that
    # have not settled. Zero live orders AND zero fills AND no exposure means
    # it is eligible to leave the CONCURRENCY set.
    #
    # WHAT DELIBERATELY DID NOT CHANGE:
    #   * `markets_touched` / `n_markets_touched` still count every market
    #     that was ever committed, settled or not, filled or not. A zero-fill
    #     attempt still consumes one of max_total_markets. Releasing it from
    #     BOTH counters would turn a 3-market authorization into unlimited
    #     market scanning, which is a far worse failure than the one being
    #     fixed.
    #   * The CHARGE arithmetic above. A terminal order with filled_qty 0
    #     already released its reservation and a fill is still permanent.
    #   * The durable ledger. Nothing is deleted; every commit, link and
    #     terminal row stays exactly where it was. This is a different READING
    #     of the same rows, which is why the audit trail is untouched.
    #
    # FAIL-CLOSED ON ANYTHING UNRESOLVED. An order with no terminal row, or a
    # commit that never got linked to an order id at all, counts as OPEN. We
    # do not know that it is dead, and "we never found out" must never free
    # concurrency -- that is the same rule the charge path uses when it charges
    # an unconfirmed submission in full.
    live_or_unknown: set[str] = set()
    for cid, c in commits.items():
        oid = links.get(cid) or ""
        if not oid or terminal.get(oid) is None:
            live_or_unknown.add(str(c.get("slug") or ""))

    filled_qty: dict[str, D] = {}
    for slug, rows in fills.items():
        filled_qty[str(slug)] = sum(
            (_dec(r.get("qty")) or D("0")) for r in rows)

    def _concurrently_open(slug: str) -> bool:
        if slug in settled:
            return False                     # reconciled: it is finished
        if slug in live_or_unknown:
            return True                      # a live or unresolved order
        # ANY fill, not just an unmatched one. A fully MATCHED position still
        # carries money to settlement, so residual == 0 does not mean flat.
        return (filled_qty.get(slug) or D("0")) > 0

    open_markets = sorted(s for s in per_market if _concurrently_open(s))

    return {
        "readable": ok and unknown == 0,
        "per_market": {k: str(v) for k, v in per_market.items()},
        "total": str(sum(per_market.values(), D("0"))),
        "markets_touched": sorted(per_market),
        "markets_open": open_markets,
        "n_markets_touched": len(per_market),
        "n_markets_open": len(open_markets),
        "residual_usd": {k: str(v) for k, v in _residuals(fills).items()},
        "filled_qty": {k: str(v) for k, v in filled_qty.items()},
        "commits": len(commits), "unreadable_commits": unknown,
        "open_basis": ("a live or unresolved order, or unsettled fills; "
                       "zero-fill fully-terminal markets release CONCURRENCY "
                       "but still count toward markets_touched"),
    }


def _residuals(fills: Mapping[str, list]) -> dict[str, D]:
    """Worst-case dollars on UNMATCHED shares, per market.

    Unmatched inventory is the part that can actually lose the full contract
    value, so it is priced at $1/share rather than at what it cost.
    """
    out: dict[str, D] = {}
    for slug, rows in fills.items():
        up = dn = D("0")
        for r in rows:
            q = _dec(r.get("qty")) or D("0")
            side = str(r.get("side") or r.get("outcome") or "").upper()
            if side.endswith("NO"):
                dn += q
            else:
                up += q
        out[slug] = abs(up - dn) * ONE
    return out


def _dec(v: Any) -> Optional[D]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return D(str(v))
    except InvalidOperation:
        return None


# --------------------------------------------------------------------------
# admission

def admit(body: Mapping[str, Any], *, state_dir: Path, mandate: Mandate,
          slug: Optional[str] = None, now: Optional[datetime] = None,
          realized_today: Optional[D] = None) -> dict:
    """Decide and, if admitted, COMMIT -- atomically, under an exclusive lock.

    The read of consumption and the write of the commitment happen inside one
    critical section. Two processes (or two threads racing a resumed session)
    therefore cannot both observe the same remaining budget and both spend it;
    the loser re-reads a ledger that already contains the winner's commitment.

    The caller gets a verdict dict either way. A refusal nobody can audit is a
    refusal nobody believes.
    """
    now = now or datetime.now(timezone.utc)
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    lock = d / "mandate.lock"
    with lock.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            return _admit_locked(body, d, mandate, slug, now, realized_today)
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _verdict(ok: bool, reason: str, mandate: Mandate, **kw: Any) -> dict:
    return {"admits": ok, "reason": reason,
            "authorization_id": mandate.authorization_id,
            "decided_utc": datetime.now(timezone.utc).isoformat(), **kw}


def _admit_locked(body: Mapping[str, Any], d: Path, m: Mandate,
                  slug: Optional[str], now: datetime,
                  realized_today: Optional[D]) -> dict:
    if m.expired(now):
        return _verdict(False, "mandate_expired", m, expiry=m.expiry,
                        now=now.isoformat())
    svc = os.getenv("PMUS_SERVICE_ROLE", "")
    if m.service and svc and m.service != svc:
        return _verdict(False, f"mandate_service_mismatch:{svc}", m)

    cash, qty, err = order_cash_effect(body)
    if err or cash is None or qty is None:
        return _verdict(False, f"order_unpriceable:{err}", m)
    slug = str(slug or body.get("marketSlug") or "")
    if not slug:
        return _verdict(False, "order_market_missing", m)

    used = consumption(d, m.authorization_id)
    if not used["readable"]:
        return _verdict(False, "consumption_unreadable", m, used=used)

    spent_here = _dec(used["per_market"].get(slug)) or D("0")
    total = _dec(used["total"]) or D("0")

    if slug not in used["per_market"]:
        if used["n_markets_touched"] + 1 > m.max_total_markets:
            return _verdict(False, "max_total_markets_exhausted", m,
                            markets_touched=used["n_markets_touched"],
                            max_total_markets=m.max_total_markets,
                            note=("a market beyond the authorized total needs "
                                  "a NEW authorization, not a continuation"))
        if used["n_markets_open"] + 1 > m.max_markets:
            return _verdict(False, "max_concurrent_markets_exhausted", m,
                            markets_open=used["n_markets_open"],
                            max_markets=m.max_markets)

    if spent_here + cash > m.max_spend_per_market:
        return _verdict(False, "max_spend_per_market_exhausted", m, slug=slug,
                        already_spent=str(spent_here), proposed=str(cash),
                        cap=str(m.max_spend_per_market),
                        remaining=str(m.max_spend_per_market - spent_here))
    if total + cash > m.max_total_spend:
        return _verdict(False, "max_total_spend_exhausted", m,
                        already_spent=str(total), proposed=str(cash),
                        cap=str(m.max_total_spend),
                        remaining=str(m.max_total_spend - total))

    resid = _dec(used["residual_usd"].get(slug)) or D("0")
    if resid > m.max_residual:
        return _verdict(False, "max_residual_exceeded", m, slug=slug,
                        residual_usd=str(resid), cap=str(m.max_residual))

    if realized_today is not None:
        rt = D(str(realized_today))
        if rt <= -abs(m.max_daily_realized_loss):
            return _verdict(False, "daily_realized_loss_stop", m,
                            realized_today=str(rt),
                            stop=str(-abs(m.max_daily_realized_loss)))

    commit_id = f"{m.authorization_id}:{slug}:{now.timestamp():.6f}:{qty}"
    _append(d, {"kind": COMMIT_KIND, "authorization_id": m.authorization_id,
                "commit_id": commit_id, "slug": slug,
                "cash_at_risk": str(cash), "quantity": str(qty),
                "price_wire": str(_price(body.get("price"))),
                "side": str(body.get("outcomeSide") or ""),
                "action": str(body.get("action") or ""),
                "labels": ("committed BEFORE the venue call; charged in full "
                           "until a terminal row proves a smaller fill")})
    return _verdict(True, "admitted", m, commit_id=commit_id, slug=slug,
                    cash_at_risk=str(cash), quantity=str(qty),
                    market_spent_before=str(spent_here),
                    market_remaining_after=str(
                        m.max_spend_per_market - spent_here - cash),
                    total_remaining_after=str(m.max_total_spend - total - cash))


def link_order(state_dir: Path, mandate: Mandate, commit_id: str,
               order_id: str) -> None:
    """Tie a commitment to the order the venue actually created.

    Written AFTER the POST returns. If it never gets written -- the process
    dies mid-call, the response is ambiguous -- the commitment stays charged at
    full notional forever, which is the safe direction.
    """
    _append(Path(state_dir), {
        "kind": LINK_KIND, "authorization_id": mandate.authorization_id,
        "commit_id": str(commit_id), "order_id": str(order_id)})


def _append(d: Path, row: Mapping[str, Any]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    with (d / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(),
                             **row}, separators=(",", ":"),
                            default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# --------------------------------------------------------------------------
# the wire

class GuardedTransport:
    """Wraps a transport so the mandate sits on the wire, not at a call site.

    Every order in this codebase becomes `POST /v1/orders` eventually. Putting
    the check here means a new executor, a retry path, a resumed session or a
    stage invented next year inherits enforcement instead of having to opt in
    -- which is precisely what `counts_against_market_cap()` failed to do.

    Anything that is not an order submission passes straight through; this is a
    spending guard, not a firewall.
    """

    def __init__(self, inner: Any, mandate: Mandate, state_dir: Path,
                 realized_today=None, log=None):
        self._inner = inner
        self._mandate = mandate
        self._state_dir = Path(state_dir)
        self._realized_today = realized_today
        self._log = log
        self.refusals: list[dict] = []
        self.admissions: list[dict] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None, **kw: Any) -> Any:
        if not (str(method).upper() == "POST" and str(path) == ORDER_PATH):
            return self._inner.request(method, path, body, **kw)
        rt = None
        if self._realized_today is not None:
            try:
                rt = self._realized_today()
            except Exception:                                # noqa: BLE001
                rt = None
        v = admit(body or {}, state_dir=self._state_dir,
                  mandate=self._mandate, realized_today=rt)
        self._emit(v)
        if not v["admits"]:
            self.refusals.append(v)
            raise MandateRefusal(v)
        self.admissions.append(v)
        resp = self._inner.request(method, path, body, **kw)
        oid = ""
        if isinstance(resp, Mapping):
            oid = str(resp.get("id") or resp.get("orderId")
                      or (resp.get("order") or {}).get("id") or "")
        link_order(self._state_dir, self._mandate, v["commit_id"], oid)
        return resp

    def _emit(self, v: Mapping[str, Any]) -> None:
        if self._log:
            self._log("mandate_admission", **dict(v))
        else:
            print("PMUS_MANDATE " + json.dumps(dict(v), separators=(",", ":"),
                                               default=str), flush=True)


def guard(inner: Any, mandate: Mandate, state_dir: Path, **kw: Any) -> GuardedTransport:
    return GuardedTransport(inner, mandate, state_dir, **kw)


def is_guarded(t: Any) -> bool:
    return isinstance(t, GuardedTransport)
