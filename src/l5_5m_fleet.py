"""Run ONE 5-minute executor per asset, as child processes of one service.

WHY NOT FOUR SERVICES. Each executor needs a durable volume for its
authorization ledger, and this Railway environment already holds 21
volumes, at the platform limit. Three more services may simply not be
creatable. One service with one volume can carry all four.

WHY NOT ONE PROCESS TRADING FOUR SERIES. `l5_btc5m_execution` resolves
STRATEGY, COID_PREFIX and MARKET_PREFIX once at import. The coid prefix
is how the ownership registry tells lanes apart, so four assets sharing
one process would share one prefix and become indistinguishable in that
registry -- the exact confusion the shared watchdog latches on, and the
thing that cost a day of halts on 2026-11-15.

So: one CHILD PROCESS per asset. Each child is still one process trading
one series with its own prefix, which is the invariant that matters.
This module only starts them and keeps their state apart.

WHAT EACH CHILD GETS, and why each must differ:
  L5_5M_STRATEGY        which series it trades
  L5_BTC5M_LEDGER       its own order ledger -- a shared one would let
                        two assets overwrite each other's orders
  L5_LIVE_AUTH_LEDGER   its own authorization ledger -- the single-use
                        submission locks live here, and sharing it would
                        let one asset spend another's budget
  L5_CAMPAIGN_APPROVAL  its own owner approval, naming its own series

IT FAILS CLOSED, THREE WAYS:
  * a child that exits takes the whole fleet down, because a supervisor
    that silently runs three of four lanes is worse than one that stops;
  * NOTHING is restarted. A money-moving process that died must not come
    back by itself -- that rule predates this file and is not relaxed by
    putting four of them behind one parent;
  * a duplicate ledger path, strategy or coid prefix refuses at startup
    rather than being discovered as interleaved orders.

usage:  python3 -u -m src.l5_5m_fleet
        L5_5M_FLEET=BTC5M,ETH5M,SOL5M,XRP5M   (default: BTC5M alone)
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Dict, List

from src import l5_origin as ORIGIN
from src import l5_series_registry as REG

FLEET_ENV = "L5_5M_FLEET"
STATE_ROOT_ENV = "L5_5M_FLEET_STATE_ROOT"
DEFAULT_ROOT = "/state"
#: per-asset approval, e.g. L5_CAMPAIGN_APPROVAL_ETH5M
APPROVAL_PREFIX = "L5_CAMPAIGN_APPROVAL_"
#: EXPLICIT PER-ASSET LEDGER PATHS, e.g. L5_5M_LEDGER_BTC5M. Derived
#: paths are the rule; these exist for the ONE case the rule gets wrong.
#:
#: WHY. A lane that has already been trading has settled rows on the
#: volume under whatever path it was deployed with. `day_settled_pnl`
#: reads that lane's own day result from its ledger, and an EMPTY ledger
#: is readable, so it answers 0 -- not None, which would refuse. Moving
#: a running lane to a derived path therefore resets its day-loss guard
#: to zero rather than failing closed, and it does so at the moment the
#: fleet adds its siblings. BTC5m was deployed at
#: /state/btc5m/btc5m_ledger.json, which no derivation produces.
#:
#: `_distinct` validates these exactly as it validates derived paths, so
#: an override cannot be used to make two lanes share a ledger.
LEDGER_PREFIX = "L5_5M_LEDGER_"
AUTH_PREFIX = "L5_5M_AUTH_"
#: THE ONE PORT A CHILD BINDS. `l5_btc5m_runner.serve_fills` listens on
#: it so the watchdog can read that lane's resting orders.
#:
#: OBSERVED 2026-11-16T11:56Z: all four children inherited the SAME
#: L5_BTC5M_FILLS_PORT, the first bound it, and the rest died with
#: OSError(98) "Address already in use". BTC5M happened to lose the race,
#: so the fleet stopped every sibling and nothing traded. Ledgers, auth
#: ledgers, approvals and coid prefixes were all made distinct; this was
#: not, and it is just as much shared state as a ledger path.
#:
#: Each lane gets BASE + its index. `_distinct` checks the ports it
#: actually hands out, exactly as it checks the paths.
FILLS_PORT_ENV = "L5_BTC5M_FILLS_PORT"
DEFAULT_FILLS_PORT = 8082


def fleet() -> List[str]:
    """The strategies to run. Unset is BTC5M alone, so this module is a
    no-op change for the existing deployment."""
    raw = str(os.environ.get(FLEET_ENV, "")).strip()
    if not raw:
        return [ORIGIN.BTC5M]
    out = []
    for part in raw.split(","):
        name = part.strip().upper()
        if not name:
            continue
        if name not in ORIGIN.STRATEGY_SERIES:
            raise SystemExit("REFUSED: %s names %r, which is not a "
                             "registered strategy" % (FLEET_ENV, name))
        if ORIGIN.STRATEGY_HORIZON_S.get(name) != 300.0:
            raise SystemExit("REFUSED: %s names %r, a %ss series; this "
                             "fleet is the FIVE-MINUTE one"
                             % (FLEET_ENV, name, ORIGIN.STRATEGY_HORIZON_S.get(name)))
        if name in out:
            raise SystemExit("REFUSED: %s names %r twice" % (FLEET_ENV, name))
        out.append(name)
    if not out:
        raise SystemExit("REFUSED: %s is set but names nothing" % FLEET_ENV)
    return out


def fills_port_base() -> int:
    """The first lane's fills port. Unreadable refuses rather than
    guessing: a wrong port is a lane the watchdog cannot read."""
    raw = (os.environ.get(FILLS_PORT_ENV) or "").strip()
    if not raw:
        return DEFAULT_FILLS_PORT
    if not raw.isdigit():
        raise SystemExit("REFUSED: %s is %r, which is not a port"
                         % (FILLS_PORT_ENV, raw))
    n = int(raw)
    if not (1024 <= n <= 65000):
        raise SystemExit("REFUSED: %s is %d, outside 1024..65000"
                         % (FILLS_PORT_ENV, n))
    return n


def child_env(strategy: str, root: str, children: List[str]) -> Dict[str, str]:
    """One child's environment. Everything that must not be shared is
    made distinct here, by construction rather than by configuration.

    `children` is the whole fleet, not a count, because a lane needs both
    HOW MANY there are (to divide the account ceiling) and WHICH ONE IT
    IS (to get its own port). It is REQUIRED rather than defaulted
    because the natural default -- one lane -- is the permissive one: it
    hands every child the whole account ceiling.
    """
    row = REG.BY_STRATEGY[strategy]
    lanes = len(children)
    index = list(children).index(strategy)
    env = dict(os.environ)
    env["L5_5M_STRATEGY"] = strategy
    #: ITS OWN PORT. Sharing one is OSError(98) for every lane but the
    #: first, and under this fleet's fail-closed rule that stops all of
    #: them. See FILLS_PORT_ENV.
    env[FILLS_PORT_ENV] = str(fills_port_base() + index)
    #: EVERY CHILD IS TOLD THE SAME LANE COUNT, from one place, so the
    #: account ceiling is divided consistently. Each lane checks its own
    #: ledger against cap/lanes; a child that disagreed about `lanes`
    #: would over- or under-claim its share. Set here rather than in the
    #: service's variables because the fleet is the only thing that
    #: knows how many children there actually are.
    env["L5_ACCOUNT_LANES"] = str(int(lanes))
    #: an explicit path wins over the derived one; see LEDGER_PREFIX.
    led = (os.environ.get(LEDGER_PREFIX + strategy) or "").strip()
    aut = (os.environ.get(AUTH_PREFIX + strategy) or "").strip()
    env["L5_BTC5M_LEDGER"] = led or "%s/%s/ledger.json" % (root, row.asset)
    env["L5_LIVE_AUTH_LEDGER"] = aut or "%s/%s/auth.jsonl" % (root, row.asset)
    #: each asset carries its OWN owner approval. A fleet-wide approval
    #: would authorize one series and be refused by the other three, or
    #: -- worse, if the series check ever softened -- authorize all four
    #: from one owner decision about one of them.
    per_asset = os.environ.get(APPROVAL_PREFIX + strategy)
    if per_asset is not None:
        env["L5_CAMPAIGN_APPROVAL"] = per_asset
    elif strategy != ORIGIN.BTC5M:
        env["L5_CAMPAIGN_APPROVAL"] = ""     # no approval: it will refuse
    env.pop(FLEET_ENV, None)                 # a child never forks a fleet
    return env


def _distinct(children: List[str], root: str) -> None:
    """Refuse a fleet whose children would collide. Checked BEFORE any
    child starts, because the symptom otherwise is interleaved orders in
    one ledger, which is unpickable after the fact."""
    seen: Dict[str, str] = {}
    for s in children:
        for key in ("L5_BTC5M_LEDGER", "L5_LIVE_AUTH_LEDGER"):
            path = child_env(s, root, children)[key]
            if path in seen:
                raise SystemExit("REFUSED: %s and %s would share %s"
                                 % (seen[path], s, path))
            seen[path] = s
    coids = [REG.BY_STRATEGY[s].coid_prefix for s in children]
    if len(set(coids)) != len(coids):
        raise SystemExit("REFUSED: two children share a client-order-id "
                         "prefix; they would be indistinguishable in the "
                         "ownership registry")
    #: AND THE PORTS. A shared one is OSError(98) at startup for every
    #: lane but the first, which stops the whole fleet. Checked here so
    #: it is a refusal with a reason rather than a race.
    ports = [child_env(s, root, children)[FILLS_PORT_ENV] for s in children]
    if len(set(ports)) != len(ports):
        raise SystemExit("REFUSED: two children share fills port %s; only "
                         "one could bind it and the rest would die with "
                         "OSError(98)" % ",".join(sorted(ports)))


def main(argv: List[str] | None = None) -> int:
    children = fleet()
    root = os.environ.get(STATE_ROOT_ENV, "").strip() or DEFAULT_ROOT
    _distinct(children, root)
    print("L5_5M_FLEET_START %s" % {
        "strategies": children, "state_root": root,
        "account_lanes": len(children),
        "fills_urls": {s: "http://%s:%s" % (
            os.environ.get("RAILWAY_PRIVATE_DOMAIN", "localhost"),
            child_env(s, root, children)[FILLS_PORT_ENV]) for s in children},
        "account_cap_usd": os.environ.get("L5_ACCOUNT_MAX_EXPOSURE_USD", ""),
        "restart_policy": "NONE -- a dead money-moving child is not revived",
        "on_child_exit": "the whole fleet stops"}, flush=True)
    procs: Dict[str, subprocess.Popen] = {}
    for s in children:
        procs[s] = subprocess.Popen(
            [sys.executable, "-u", "-m", "src.l5_btc5m_runner"],
            env=child_env(s, root, children))
        print("L5_5M_FLEET_CHILD %s" % {
            "strategy": s, "pid": procs[s].pid,
            "series": REG.BY_STRATEGY[s].prefix,
            "coid_prefix": REG.BY_STRATEGY[s].coid_prefix}, flush=True)

    def _stop(signum, _frame):
        for s, p in procs.items():
            if p.poll() is None:
                p.send_signal(signum)
        raise SystemExit(128 + int(signum))

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _stop)

    while True:
        for s, p in procs.items():
            code = p.poll()
            if code is not None:
                print("L5_5M_FLEET_CHILD_EXIT %s" % {
                    "strategy": s, "code": code,
                    "action": ("stopping every sibling: a fleet running "
                               "some of its lanes is not the fleet the "
                               "owner approved")}, flush=True)
                for other, q in procs.items():
                    if other != s and q.poll() is None:
                        q.terminate()
                return code if code else 1
        try:
            next(iter(procs.values())).wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass


if __name__ == "__main__":                                 # pragma: no cover
    raise SystemExit(main())
