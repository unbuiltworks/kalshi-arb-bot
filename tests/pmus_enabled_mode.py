"""Test helper: run the sports engine in ENABLED MODE with a paying reward.

Enabled mode = the four settings the no-fair release candidate is meant to
run with:

    PMUS_RN1_MARGINAL_EV=1  PMUS_RN1_MARGINAL_EV_NO_FAIR=1
    PMUS_RN1_FIT_ALL_LEVELS=1  PMUS_RN1_EDGE_GATE=0

In that mode a bid in a market with no international fair value rests only
when its qualifying, haircut, incremental THEORETICAL reward per hour beats
its expected fill cost. Fixtures with no reward programme therefore rest
nothing and every safety assertion after "orders are resting" is never
reached. This helper gives an engine a realistic paying liquidity programme
through the seams the existing tests already use:

* the programme is a venue ``/v1/incentives`` row parsed by the real
  ``pmus_sports_rewards.build_program_map`` and served from ``Engine._prog``,
  so the real ``Engine.rn1_reward`` / ``paying_period`` select it;
* the public book is served through ``Engine._rget`` (what
  ``Engine._reward_get`` returns), built around the venue's current BBO, so
  the real ``pmus_sports_rewards.marginal_ladder_reward`` scores every bid
  under the venue's published rule (nothing in the scoring is stubbed);
* the day's projection (``Engine.rn1_incentive``) qualifies only when the
  projected theoretical reward over ``RN1_REWARD_PROJECT_H`` reaches the
  venue's minimum payout ``RN1_REWARD_MIN_DAY_USD`` -- a programme whose
  projection is below the minimum payout credits nothing.

Profiles are calibrated to observed live estimates (theoretical, never
P&L): ``doubles_live`` (the default) ~ a live ATP doubles market under its
published live programme ($70 over a 3 h live period; ~$2.25/h for a
qty-2 bid each side one tick behind, at most the $9.43/h observed live),
sized so the full ladder clears the activation config's fill costs after
its 0.4 reward multiplier; ``doubles`` ~ a liquid ATP doubles market
under a daily pool ($1-9/h for the market, ~$0.1-0.6/h for one 4-10
contract bid one tick behind; sized for the 0.5 haircut and the default
fill costs); ``thin`` ~ a thin market ($0.01-0.2/h, projection below the
$1 minimum payout).

Every number produced here is THEORETICAL. Nothing here places, cancels or
blocks an order; the engine under test does all of that itself.
"""
from __future__ import annotations

import contextlib
import time
import urllib.parse
from decimal import Decimal as D
from typing import Any, Dict, Optional

from src import pmus_rn1_replica as R
from src import pmus_sports_rewards as RW
from src import pmus_sports_two_sided as M

FLAGS = (("RN1_MARGINAL_EV", M), ("RN1_MARGINAL_EV_NO_FAIR", M),
         ("RN1_FIT_ALL_LEVELS", M))


def set_enabled_flags(monkeypatch) -> None:
    """The four settings on, exactly as the env would set the module
    constants at import (EDGE_GATE is the replica's constant)."""
    for name, mod in FLAGS:
        monkeypatch.setattr(mod, name, True)
    monkeypatch.setattr(R, "EDGE_GATE", False)


def enabled_flags_on() -> bool:
    return bool(M.RN1_MARGINAL_EV and M.RN1_MARGINAL_EV_NO_FAIR
                and M.RN1_FIT_ALL_LEVELS and not R.EDGE_GATE)


# name: (daily pool USD, discount factor, target size, contracts per
#        visible level, visible levels per side, projected market USD/h)
PROFILES: Dict[str, Dict[str, Any]] = {
    # liquid ATP doubles (live 26 Nov: ~$9.4/h theoretical for the market)
    "doubles": dict(pool=150.0, df=0.8, target=150.0, level_qty=12.0,
                    levels=15, max_spread=None, projected_usd_h=3.0),
    # thin market: big resting size ahead of us, small pool
    "thin": dict(pool=40.0, df=0.5, target=200.0, level_qty=60.0,
                 levels=6, max_spread=None, projected_usd_h=0.15),
    # DEFAULT. A live ATP doubles market under the venue's published
    # programme doubles_moneyline_live_20261018 (analysis/l5/rewards/
    # pmus_liquidity_programs_20261121.json; reward_program log lines on
    # 26 Nov: pool 70.0, period "live", period_s 10800 => $23.3/h for the
    # market). The book is thin (3 contracts a level), as in-play doubles
    # books are. With it, a qty-2 bid on each side one tick behind earns
    # ~$2.25/h THEORETICAL two-sided and 10 contracts at each touch
    # ~$9.3/h, both inside what was observed live (atpdb-alvvalflores-
    # alvesperez 26 Nov 21:01: $9.43/h two-sided at qty 2, one behind;
    # atpdb-delanedelane-falcksuarez 25 Nov: $0.74-0.97/h). Never above.
    # Why it exists: under the proposed activation config (calibration
    # table *:0.4, fill rate behind 1.5/h, fill loss behind 0.042 at qty 2
    # => $0.126-0.186/h of fill cost per bid, levels 1-3 ticks behind) the
    # "doubles" book below credits each bid $0.02-0.03/h, so NO bid rests
    # and no safety scenario is reached. Here every level of the fixture's
    # three-level ladder clears its own fill cost by $0.03-0.22/h after the
    # 0.4 multiplier, so each scenario places real orders and its
    # protection acts on them. distance factor 0.8 / target 100 are the
    # fixture's (the programme's own 0.3 / 4000 cannot carry a bid three
    # ticks behind at these fill costs without crediting a one-behind bid
    # far above the observed per-bid magnitudes).
    "doubles_live": dict(pool=70.0, period="live", df=0.8, target=100.0,
                         level_qty=3.0, levels=40, max_spread=None,
                         projected_usd_h=3.0),
}
DEFAULT_PROFILE = "doubles_live"


class RewardFeed:
    """A paying liquidity programme + public book for every market of one
    engine. Switches the tests flip (each one models a real failure):

    * ``present``   -- False: the programme disappears from the feed;
    * ``status``    -- e.g. "ended": the venue marks the period not active;
    * ``read_fails``-- True: every gateway read raises (reward feed lost);
    * ``projected_usd_h`` -- the day's projection; below the minimum payout
      over RN1_REWARD_PROJECT_H it stops qualifying.
    """

    def __init__(self, engine, profile: str = None, **over):
        profile = profile or DEFAULT_PROFILE
        self.e = engine
        self.p = dict(PROFILES[profile], **over)
        self.present = True
        self.status = "active"
        self.read_fails = False
        self.projected_usd_h = float(self.p["projected_usd_h"])
        self.reads = []

    # ------------------------------------------------ venue payloads
    def incentive_row(self, slug: str) -> Dict[str, Any]:
        tp = {"programType": "liquidityProgram", "programId": "LP-" + slug,
              "rewardPool": str(self.p["pool"]),
              "period": self.p.get("period", "daily"),
              "discountFactor": str(self.p["df"]),
              "targetSize": str(self.p["target"]),
              "status": self.status}
        # no "start": the period began before any clock a test sets (several
        # tests run the engine at t=4e6, i.e. 1970)
        if self.p["max_spread"] is not None:
            tp["maxSpread"] = str(self.p["max_spread"])
        return {"marketSlug": slug, "instrumentState": "OPEN",
                "eventStartTime": None, "timePeriods": [tp]}

    def programme(self, slug: str) -> Optional[Dict[str, Any]]:
        if not self.present:
            return None
        return RW.build_program_map({slug: self.incentive_row(slug)}
                                    ).get(slug)

    def book(self, slug: str) -> Dict[str, Any]:
        """Public book in YES terms around the venue's current BBO: the
        profile's size at the touch and at each tick behind it."""
        bbo = self.e.v.bbo(slug) or {}
        spec = self.e.v.spec(slug) or {}
        t = D(str(spec.get("tick") or "0.01"))
        if bbo.get("bid") is None or bbo.get("ask") is None:
            return {}
        b, a = D(str(bbo["bid"])), D(str(bbo["ask"]))
        q = str(self.p["level_qty"])
        bids = [{"px": {"value": str(b - i * t)}, "qty": q}
                for i in range(int(self.p["levels"])) if b - i * t > 0]
        offers = [{"px": {"value": str(a + i * t)}, "qty": q}
                  for i in range(int(self.p["levels"])) if a + i * t < 1]
        return {"bids": bids, "offers": offers}

    def get(self, path: str):
        self.reads.append(path)
        if self.read_fails:
            raise ConnectionError("reward feed unreachable")
        if path.startswith("/v1/incentives?"):
            asked = [urllib.parse.unquote(x[len("symbols="):])
                     for x in path.split("?", 1)[1].split("&")
                     if x.startswith("symbols=")]
            return {"programs": [self.incentive_row(s) for s in asked]
                    if self.present else []}
        if path.startswith("/v1/markets/") and path.endswith("/book"):
            return {"marketData": self.book(path.split("/")[3])}
        return {}

    # ------------------------------------------------ engine seams
    def rn1_reward(self, slug: str) -> Optional[Dict[str, Any]]:
        """Engine.rn1_reward on this feed: the real paying_period."""
        prog = self.programme(slug)
        if not prog:
            return None
        return RW.paying_period(prog, time.time(),
                                event_live=self.e.event_live(slug))

    def incentive(self, slug: str) -> Dict[str, Any]:
        """Engine.rn1_incentive's answer for this feed: qualifies only with
        a paying period (as the ENGINE sees it: its own rn1_reward, exactly
        as the real rn1_incentive asks) AND a projection at or above the
        venue's minimum payout."""
        out: Dict[str, Any] = {"ticks": 0, "why": "no_paying_programme"}
        try:
            if not self.e.rn1_reward(slug) or \
                    M.RN1_REWARD_CREDIT_TICKS <= 0:
                return out
        except Exception as exc:                      # noqa: BLE001
            return {"ticks": 0, "why": "reward_estimate_error:%s"
                    % type(exc).__name__}
        proj = self.projected_usd_h * M.RN1_REWARD_PROJECT_H
        out.update(projected_usd=round(proj, 4),
                   min_payout_usd=M.RN1_REWARD_MIN_DAY_USD,
                   label="THEORETICAL reward projection; not a payment")
        if proj + 1e-9 < M.RN1_REWARD_MIN_DAY_USD:
            out["why"] = "below_minimum_payout"
            return out
        out.update(ticks=M.RN1_REWARD_CREDIT_TICKS, why="qualifies")
        return out

    def install(self, monkeypatch, slugs=None) -> "RewardFeed":
        """Serve this feed to the engine for the whole test, through the
        engine's own programme discovery: ``Engine._rget`` answers the
        gateway reads, ``Engine.reward_programs`` fetches and parses the
        programme into ``Engine._prog`` and the real ``Engine.rn1_reward``
        reads it from there. Only the day's projection is this feed's."""
        e = self.e
        monkeypatch.setattr(e, "_rget", self.get)
        monkeypatch.setattr(e, "rn1_incentive",
                            lambda slug, mk=None, tick=None:
                            self.incentive(slug))
        # RN1_REWARD_QUAL_SCOPE "account_day": the same day projection
        # serves the account/day qualification (every site reads it), so a
        # test that lowers projected_usd_h lowers it there too
        monkeypatch.setattr(e, "rn1_reward_day_projection",
                            lambda slug=None, extra_usd_h=0.0, now=None:
                            self.day_projection())
        monkeypatch.setattr(e, "rn1_reward_pool_projection",
                            lambda slug=None, extra_usd_h=0.0, now=None:
                            dict(self.day_projection(),
                                 scope="program_period",
                                 pool="feed", accrued_pool_usd=0.0))
        self.refresh(slugs)
        return self

    def day_projection(self) -> Dict[str, Any]:
        """Engine.rn1_reward_day_projection's answer for this feed."""
        h = M.RN1_REWARD_PROJECT_H
        return {"scope": "account_day", "accrued_today_usd": 0.0,
                "rate_usd_per_hour": self.projected_usd_h, "horizon_h": h,
                "projected_usd": round(self.projected_usd_h * h, 4),
                "markets": 1,
                "label": "THEORETICAL reward projection; not a payment"}

    def refresh(self, slugs=None) -> None:
        """The engine's programme cache expires (REWARD_REFRESH_S passes)
        and is re-read from the venue through Engine.reward_programs."""
        e = self.e
        slugs = list(slugs or e.active())
        for s in slugs:
            if s in e._prog:
                t, prog = e._prog[s]
                e._prog[s] = (t - M.REWARD_REFRESH_S - 1, prog)
        e.reward_programs(slugs)

    @contextlib.contextmanager
    def scoped(self, slug: str):
        """Serve this feed to ONE marginal-EV evaluation only (see
        tests/conftest.py): rn1_reward, the book read and the day's
        projection; every other engine path keeps the test's own state."""
        e = self.e
        saved = {k: e.__dict__[k] for k in ("rn1_reward", "_rget")
                 if k in e.__dict__}
        inc = getattr(e, "_rn1_inc", None)
        if inc is None:
            inc = e._rn1_inc = {}
        had_inc, old_inc = slug in inc, inc.get(slug)
        cache = getattr(e, "_mev_book", None)
        had_book = bool(cache) and slug in cache
        e.rn1_reward = self.rn1_reward
        e._rget = self.get
        inc[slug] = self.incentive(slug)
        try:
            yield
        finally:
            for k in ("rn1_reward", "_rget"):
                if k in saved:
                    e.__dict__[k] = saved[k]
                else:
                    e.__dict__.pop(k, None)
            if had_inc:
                inc[slug] = old_inc
            else:
                inc.pop(slug, None)
            cache = getattr(e, "_mev_book", None)
            if cache and not had_book:
                cache.pop(slug, None)


def feed_for(engine, profile: str = None) -> RewardFeed:
    f = engine.__dict__.get("_enabled_mode_feed")
    if f is None:
        f = engine.__dict__["_enabled_mode_feed"] = RewardFeed(engine,
                                                               profile)
    return f
