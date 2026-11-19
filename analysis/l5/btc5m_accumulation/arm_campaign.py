"""Arm ONE bounded 5-minute straddle campaign, on one or more assets.

ONE INVOCATION ARMS EVERY LANE, and that is not a convenience. The
assets run as children of one service, so they SHARE L5_BTC5M_LIVE,
L5_BTC5M_NOT_AFTER and the strategy parameters. Arming them one at a
time would refuse the second ("already armed"), and a second deploy
would orphan a window already in flight. So: N approvals, the shared
variables once, ONE deploy.

WHAT IS PER-ASSET is exactly the approval document -- one document
names one series -- written as L5_CAMPAIGN_APPROVAL_<STRATEGY>, which
is the name `l5_5m_fleet.child_env` hands to that child. A lane with no
approval of its own refuses every order, which is the intended failure.

Written after 2026-11-13, where every error was operational rather than
strategic: variables set AFTER a deploy came up on stale values, a
mid-campaign redeploy orphaned the one winning window, and a campaign was
armed into an account that had no loss room and placed nothing.

ORDER IS THE WHOLE POINT. Preconditions -> approval -> variables ->
deploy, once. Railway snapshots env at deployment creation, so a deploy
started before the variables are set comes up on the old ones.

IT REFUSES RATHER THAN GUESSES. No loss room, no exposure headroom, an
approval its own self-check rejects, or an already-armed service, and it
stops without touching anything.

NOTHING HERE IS AUTOMATIC. It arms ONE campaign and exits. It never loops,
never re-arms, and must not be scheduled: a completed or crashed
money-moving session does not relaunch itself.

Usage:  python3 -I analysis/l5/btc5m_accumulation/arm_campaign.py [--go]
            [--strategy BTC5M,ETH5M,SOL5M,XRP5M] [--shares N] [--windows N]
Without --go it runs every check and prints what it WOULD do.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal as D

PROJECT = "50fb7357-e2bc-4d04-a49e-71eb5d73b0d1"
ENVIRON = "f3642d27-26dc-4e21-9170-bc76290c076f"
BTC5M = "09271601-9e58-4add-8343-fdabede5679d"
PMUS15 = "87f1834e-530e-4fc4-a310-91c8d3add196"
URL = "https://backboard.railway.com/graphql/v2"

#: the campaign's shape. One market at a time, `--shares` a side at 0.08.
WINDOWS = 8          # overridden by --windows
PRICE = D("0.08")
#: the campaign's own daily stop-loss, REQUESTED here and clamped by
#: `max_daily_loss_for(series)` in l5_campaign_approval. Raised 5.00 ->
#: 11.00 with the per-series cap on 2026-11-14 by owner decision. The
#: request must MATCH the cap: asking 5.00 under an 11.00 cap gets
#: min(5, 11) = 5 and refuses instantly on a day already at -5.00,
#: which is the trap this comment exists to stop.
DAILY_LOSS = "11.00"
#: windows whose $1 booking may still be uncorrected at any instant
UNCORRECTED_ALLOWANCE = 4


#: when --completion is used, the TOTAL a completed pair may cost. The
#: engine clamps this to COMPLETION_PAIR_COST_MAX (0.96) regardless, so
#: this only has to agree with it, never enforce it.
#: MEASURED OPTIMUM, 536 shadow windows, cheap rung cancelled on
#: completion: a 0.83 pair (complete at 0.75) is worth +8.76 c/share/window
#: against +7.10 for completion OFF. 0.96 -- the previous value, and what
#: the live 2026-11-15 campaign ran -- measures +5.64, i.e. WORSE than not
#: completing. The curve is flat from 0.83 to 0.78, so this is not a knife
#: edge; above a 0.90 pair the engine refuses outright.
COMPLETION_PAIR_COST = D("0.83")


def shape(shares: int, completion: bool = False) -> dict:
    """Every figure the campaign needs, derived from ONE number.

    These were four hand-kept constants and they drifted: a 5-share clip
    with a 1-share exposure approval refuses every order, and the reverse
    silently approves five times the intended size.
    """
    legs = D(shares) * PRICE                 # one side
    both = legs * 2                          # both legs at once
    #: COMPLETION CHANGES THE EXPOSURE SHAPE, and this is the whole reason
    #: it cannot simply be switched on. The worst instant holds the filled
    #: cheap leg, the other cheap leg still resting, AND the completion
    #: order resting at (pair_cost - price):
    #:     shares * (PRICE + PRICE + (COMPLETION_PAIR_COST - PRICE))
    #:   = shares * (PRICE + COMPLETION_PAIR_COST)
    #: At 0.08 and 0.96 that is 1.04 a share -- 6.5x the 0.16 a share the
    #: bare straddle needs. SERIES_MAX_TOTAL_EXPOSURE_USD is $5, so this
    #: is what caps the clip at 4 shares ($4.16) rather than 5 ($5.20).
    worst_instant = (D(shares) * (PRICE + COMPLETION_PAIR_COST)
                     if completion else both)
    return {"shares": int(shares),
            "clip_usd": str(legs),
            #: the approval must cover both legs with a little slack, and
            #: `exposure_ceiling()` = min(adapter, approved) is what the
            #: gate judges against the account's remaining loss room.
            "completion": bool(completion),
            "completion_pair_cost": str(COMPLETION_PAIR_COST),
            "worst_instant_usd": worst_instant,
            #: 1.25x slack on the bare straddle as before; with a
            #: completion the figure is already the true worst instant, so
            #: it takes a thinner 1.05x -- 1.25x would exceed the $5 series
            #: ceiling at any clip worth running.
            "exposure": str((worst_instant * (D("1.05") if completion
                                              else D("1.25"))
                             ).quantize(D("0.01"))),
            "need_exposure_headroom": worst_instant,
            #: WHAT THE LEDGER WILL BOOK, WHICH IS NOT WHAT WE RISK.
            #: `l5-pmus-live` prices BTC5m legs it has no basis for at
            #: $1.00/share (UNKNOWN_COST_UPPER_BOUND), so a losing leg
            #: books `shares` dollars, not `shares * 0.08`. The DAILY HALT
            #: is judged on that booked figure, so the phantom worst case
            #: is the real constraint on how many windows fit. This is the
            #: check whose absence let 2026-11-13 consume 39.27 of a 40.00
            #: halt on 2.25 of actual risk.
            "phantom_worst_usd": D(shares) * WINDOWS,
            #: ONCE VENUE ATTESTATION IS LIVE the $1 bound is transient: a
            #: leg books at 1.00/share and is corrected to its real 0.08
            #: about two minutes after settlement. Windows arrive every
            #: five minutes, so only a window or two is ever uncorrected.
            #: Four is the allowance -- lag, plus the step's correction cap.
            #: The RAW phantom still applies when attestation is not
            #: working, because then nothing ever corrects.
            "attested_worst_usd": (legs * WINDOWS
                                   + D(UNCORRECTED_ALLOWANCE) * D(shares)
                                   * (D(1) - PRICE)),
            "real_worst_usd": legs * WINDOWS}


def gql(query, variables):
    body = json.dumps({"query": query, "variables": variables})
    out = subprocess.run(
        ["curl", "-sS", "-X", "POST", URL,
         "-H", "Content-Type: application/json",
         "-H", "Project-Access-Token: " + os.environ["RAILWAY_PROJECT_TOKEN"],
         "-d", body], capture_output=True, text=True, timeout=90).stdout
    return json.loads(out)


def latest_deployment(service):
    q = ("query($p:String!,$e:String!,$s:String!){ deployments(input:"
         "{projectId:$p,environmentId:$e,serviceId:$s}, first:1)"
         "{ edges{ node{ id status } } } }")
    e = gql(q, {"p": PROJECT, "e": ENVIRON, "s": service})
    rows = e["data"]["deployments"]["edges"]
    return (rows[0]["node"]["id"], rows[0]["node"]["status"]) if rows else (None, None)


def logs(dep, limit=600):
    q = ("query($d:String!){ deploymentLogs(deploymentId:$d, limit:%d)"
         "{ message } }" % int(limit))
    return [x["message"] for x in gql(q, {"d": dep})["data"]["deploymentLogs"]]


def attestation_live():
    """Is venue cost attestation actually correcting, right now?

    Returns (live, detail). Live means the block exists AND has applied
    corrections -- a block that is present but has attested nothing is
    NOT a reason to relax the phantom bound. This is deliberately a
    behavioural check, not a config check: the whole defect it guards
    against was a figure that looked fine and was not.
    """
    dep, _ = latest_deployment(PMUS15)
    hb = [m for m in logs(dep, 400) if m.startswith("L5_LIVE_HB")]
    if not hb:
        return False, "no heartbeat"
    va = json.loads(hb[-1].split(" ", 1)[1]).get("venue_cost_attestation")
    if not isinstance(va, dict):
        return False, "no venue_cost_attestation block"
    applied = va.get("corrections_applied") or 0
    stuck = (va.get("still_upper_bound") or {}).get("pending") or 0
    if int(applied) <= 0:
        return False, "block present but 0 corrections applied"
    return True, "applied %s, %s still pending" % (applied, stuck)


def account_risk():
    """The 15m service's published risk figures, read from its heartbeat."""
    dep, _ = latest_deployment(PMUS15)
    hb = [m for m in logs(dep, 600) if m.startswith("L5_LIVE_HB")]
    if not hb:
        return None
    return json.loads(hb[-1].split(" ", 1)[1]).get("day_realized") or {}


def cash_available():
    """The venue's own buying power, from the 15m service's heartbeat.

    Same figure, same route (/v1/account/balances) the executor itself
    reads at the submission gate under FOREIGN_EXPOSURE=REPORTED. None is
    no reading and refuses; it is never read as unlimited.
    """
    dep, _ = latest_deployment(PMUS15)
    hb = [m for m in logs(dep, 600) if m.startswith("L5_LIVE_HB")]
    if not hb:
        return None
    ac = json.loads(hb[-1].split(" ", 1)[1]).get("available_cash")
    if not isinstance(ac, dict) or ac.get("usd") is None:
        return None
    try:
        return D(str(ac["usd"]))
    except Exception:                                          # noqa: BLE001
        return None


def btc5m_variables():
    q = ("query($p:String!,$e:String!,$s:String!){ variables(projectId:$p,"
         " environmentId:$e, serviceId:$s) }")
    return gql(q, {"p": PROJECT, "e": ENVIRON, "s": BTC5M})["data"]["variables"]


def upsert(name, value):
    q = "mutation($i:VariableUpsertInput!){ variableUpsert(input:$i) }"
    r = gql(q, {"i": {"projectId": PROJECT, "environmentId": ENVIRON,
                      "serviceId": BTC5M, "name": name, "value": value,
                      "skipDeploys": True}})
    return bool(r.get("data", {}).get("variableUpsert"))


def refuse(why):
    print("REFUSED: %s" % why)
    print("Nothing was changed.")
    return 2


def main(argv):
    go = "--go" in argv
    shares = 5
    if "--shares" in argv:
        shares = int(argv[argv.index("--shares") + 1])
    if "--windows" in argv:
        global WINDOWS
        WINDOWS = int(argv[argv.index("--windows") + 1])
    #: THE CAMPAIGN'S OWN DAILY STOP, overridable because the standing
    #: envelope now COMPARES against it. `PMODE.enter_armed` refuses a
    #: campaign whose max_daily_loss_usd exceeds the envelope's, so the
    #: 11.00 default below cannot be armed under a 5.00 envelope -- it
    #: returns CAMPAIGN_DAILY_LOSS_ABOVE_ENVELOPE and writes no state.
    #: The looser default is therefore structurally unusable rather than
    #: merely discouraged, and this flag is how the ceremony issues an
    #: approval that matches the envelope it will be armed under.
    #:
    #: READ THE WARNING ON `DAILY_LOSS` BEFORE CHANGING IT: the request
    #: is clamped by `max_daily_loss_for(series)`, and the figure is
    #: judged against THIS LANE's own `day_settled_pnl` for the UTC day
    #: -- not the account-wide result -- so a lane already past the
    #: requested figure refuses every submission immediately.
    if "--daily-loss" in argv:
        global DAILY_LOSS
        DAILY_LOSS = str(argv[argv.index("--daily-loss") + 1]).strip()
    completion = "--completion" in argv
    #: WHICH ASSETS. A comma list, validated against the one registry
    #: table, five-minute only -- the same rule `l5_5m_fleet` applies, so
    #: a fleet cannot be armed into a shape it would refuse to start.
    import sys as _sys
    _sys.path.insert(0, os.getcwd())
    from src import l5_origin as ORIGIN
    from src import l5_series_registry as REG
    strategies = [ORIGIN.BTC5M]
    if "--strategy" in argv:
        strategies = []
        for part in argv[argv.index("--strategy") + 1].split(","):
            name = part.strip().upper()
            if not name:
                continue
            if name not in REG.BY_STRATEGY:
                return refuse("--strategy names %r, not a registered "
                              "strategy" % name)
            if REG.BY_STRATEGY[name].horizon_s != 300.0:
                return refuse("--strategy names %r, a %ss series; this "
                              "arms the FIVE-MINUTE fleet"
                              % (name, REG.BY_STRATEGY[name].horizon_s))
            if name in strategies:
                return refuse("--strategy names %r twice" % name)
            strategies.append(name)
        if not strategies:
            return refuse("--strategy is set but names nothing")
    lanes = len(strategies)
    print("arming %d lane(s): %s" % (lanes, ",".join(strategies)))
    sh = shape(shares, completion=completion)
    print("shape: %d share(s) a side at %s -> clip %s, approval exposure %s"
          % (sh["shares"], PRICE, sh["clip_usd"], sh["exposure"]))
    if completion:
        print("  COMPLETION ON at pair cost %s: worst instant %s a window"
              % (sh["completion_pair_cost"], sh["worst_instant_usd"]))
    else:
        print("  completion OFF (bare straddle)")
    print("  real worst case over %d windows    %s" % (WINDOWS, sh["real_worst_usd"]))
    print("  BOOKED worst case (at $1/share)   %s   <-- what the halt sees"
          % sh["phantom_worst_usd"])
    v = btc5m_variables()

    # 1. not already armed
    if (v.get("L5_BTC5M_LIVE") or "").strip() or (v.get("L5_SHADOW_ONLY") or "1").strip() == "0":
        return refuse("the service is already armed; disarm before re-arming")

    # 2. account loss room
    dr = account_risk()
    if not dr or dr.get("room_usd") is None:
        return refuse("account risk figures unreadable; a ceiling fed an "
                      "unknown figure is not a ceiling")
    room = D(str(dr["room_usd"]))
    print("account day realized %s of halt %s -> loss room %s"
          % (dr.get("utc_day_realized_usd"), dr.get("halt_usd"), room))
    need = D(sh["exposure"])
    if room < need:
        return refuse("loss room %s < approved exposure %s; the gate would "
                      "refuse every window (EXPOSURE_CEILING_EXCEEDS_LOSS_"
                      "ROOM). The daily figure resets at 00:00Z."
                      % (room, need))
    # AND the phantom booking has to fit, or the account-wide DAILY HALT
    # trips partway through and stops every lane, not just this one.
    live, detail = attestation_live()
    print("venue cost attestation: %s (%s)"
          % ("LIVE" if live else "NOT LIVE", detail))
    #: EVERY LANE BOOKS INTO THE SAME ACCOUNT. `shape` returns ONE lane's
    #: figure; the daily halt this guards is account-wide and sees the sum,
    #: so four lanes book four times as much against the same room. Without
    #: this multiplication a four-lane arm passes here and then halts the
    #: whole account a quarter of the way through its own campaign.
    booked_lane = sh["attested_worst_usd"] if live else sh["phantom_worst_usd"]
    booked = D(str(booked_lane)) * D(lanes)
    print("  booked worst case used: %s a lane x %d = %s"
          % (booked_lane, lanes, booked))
    if booked > room:
        per_lane_room = room / D(lanes)
        fits = (int(per_lane_room / D(WINDOWS))
                if WINDOWS and per_lane_room >= 1 else 0)
        return refuse(
            "the LEDGER would book up to %s (%s a lane x %d lanes) against "
            "%s of room, so the account-wide daily halt would trip partway "
            "through and stop every lane. Real risk is only %s a lane. "
            "Either run %d share(s) a side, arm fewer lanes, or fix the cost "
            "basis (COST_BASIS_OVERSTATEMENT.md)."
            % (booked, booked_lane, lanes, room, sh["real_worst_usd"],
               max(1, fits)))

    # 3. account exposure headroom, under whichever rule the SERVICE will
    #    actually apply -- read from its own variables, not assumed here.
    #
    #    CHARGED (the default): every foreign contract is charged at $1.00
    #    GROSS, so another lane's MATCHED inventory can consume the cap.
    #    Shadow cannot see this: the account block reads null unarmed.
    #
    #    REPORTED: the cap bounds OUR exposure and the venue's own buying
    #    power bounds what the shared account can fund, so the check that
    #    matters is cash, not the other lane's inventory. Checked here too
    #    rather than left to the first window, because arming into an
    #    account that cannot fund the clip is the failure this script
    #    exists to prevent.
    cap = D(str(v.get("L5_ACCOUNT_MAX_EXPOSURE_USD") or "0"))
    NEED_EXPOSURE_HEADROOM = sh["need_exposure_headroom"]
    #: EACH LANE IS BOUND BY ITS SHARE, not the whole ceiling -- see
    #: `l5_btc5m_execution.lane_cap`, which the executor enforces. Checking
    #: the undivided cap here would arm a fleet that then refuses every
    #: window with ACCOUNT_EXPOSURE_CAP, which is the 2026-11-15 failure in
    #: a new costume. Floored to the cent exactly as the executor floors it.
    lane_cap = cap if lanes == 1 else (cap / D(lanes)).quantize(D("0.01"))
    if lanes > 1:
        print("account cap %s / %d lanes -> %s a lane" % (cap, lanes, lane_cap))
    mode = (v.get("L5_BTC5M_FOREIGN_EXPOSURE") or "CHARGED").strip().upper()
    mode = "REPORTED" if mode == "REPORTED" else "CHARGED"
    print("foreign exposure mode: %s" % mode)
    dep, _ = latest_deployment(BTC5M)
    other = None
    for m in reversed(logs(dep, 400)):
        if m.startswith("L5_BTC5M_STEP"):
            acct = json.loads(m.split(" ", 1)[1]).get("account") or {}
            if acct.get("other_usd") is not None:
                other = D(str(acct["other_usd"]))
                break
    if mode == "REPORTED":
        if NEED_EXPOSURE_HEADROOM > lane_cap:
            return refuse("our own worst instant %s exceeds this lane's "
                          "share %s (cap %s / %d lanes). Arm fewer lanes or "
                          "lower --shares."
                          % (NEED_EXPOSURE_HEADROOM, lane_cap, cap, lanes))
        cash = cash_available()
        if cash is None:
            return refuse("buying power unreadable; the service will refuse "
                          "every order with ACCOUNT_CASH_UNREADABLE")
        #: ALL LANES CAN BE AT THEIR WORST AT ONCE. They trade the same
        #: 5-minute boundaries, so their worst instants coincide by
        #: construction rather than by bad luck.
        need_all = NEED_EXPOSURE_HEADROOM * D(lanes)
        if cash < need_all:
            return refuse("buying power %s < %d lanes x worst instant %s = "
                          "%s (ACCOUNT_CASH_SHORT)"
                          % (cash, lanes, NEED_EXPOSURE_HEADROOM, need_all))
        print("ours %s within this lane's share %s (cap %s / %d); buying "
              "power %s covers %d lane(s) at %s; foreign %s reported, not "
              "charged" % (NEED_EXPOSURE_HEADROOM, lane_cap, cap, lanes,
                           cash, lanes, need_all,
                           "unknown" if other is None else other))
    elif other is None:
        print("NOTE: no recent armed step, so foreign exposure is unknown. "
              "It is only observable while armed; the first window will "
              "report it and refuse with ACCOUNT_EXPOSURE_CAP if short.")
    elif other + NEED_EXPOSURE_HEADROOM > cap:
        return refuse("foreign exposure %s + %s exceeds cap %s"
                      % (other, NEED_EXPOSURE_HEADROOM, cap))
    else:
        print("foreign exposure %s + %s within cap %s" % (other, NEED_EXPOSURE_HEADROOM, cap))

    # 4. ONE APPROVAL PER LANE, generated now so none is stale, each
    #    self-checked by the generator before it is used.
    here = os.path.dirname(os.path.abspath(__file__))
    approvals, starts, pinned_start = {}, None, None
    for name in strategies:
        cmd = [sys.executable, "-I", os.path.join(here, "make_approval.py"),
               str(WINDOWS), "--strategy", name,
               "--exposure", sh["exposure"], "--daily-loss", DAILY_LOSS]
        #: PIN THE FIRST LANE'S START FOR EVERY OTHER LANE. Each
        #: generation otherwise picks its own next boundary, so a run that
        #: crosses a 5-minute boundary partway through would approve
        #: DIFFERENT windows per asset -- and the shared NOT_AFTER would
        #: then be wrong for at least one of them.
        if pinned_start:
            cmd += ["--start", pinned_start]
        gen = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        doc = [x for x in gen.stdout.splitlines() if x.startswith('{"approved"')]
        if "campaign planner accepted: YES" not in gen.stdout or not doc:
            print(gen.stdout[-1500:])
            return refuse("the generator's own self-check rejected the "
                          "document for %s" % name)
        approvals[name] = doc[0]
        mine = json.loads(doc[0])["window_starts"]
        if starts is None:
            starts, pinned_start = mine, mine[0]
        elif mine != starts:
            return refuse("%s was approved for different windows than %s; "
                          "they share one NOT_AFTER and cannot disagree"
                          % (name, strategies[0]))
        print("  approval ok: %-6s %d windows" % (name, len(mine)))
    not_after = starts[-1][:-1]
    import datetime as dt
    na = (dt.datetime.fromisoformat(not_after) + dt.timedelta(minutes=5))
    not_after = na.strftime("%Y-%m-%dT%H:%M:%SZ")
    print("approvals ok: %d lane(s), %d windows, %s .. %s, NOT_AFTER %s"
          % (lanes, len(starts), starts[0], starts[-1], not_after))
    print("per-lane approved daily loss %s; the ACCOUNT-WIDE halt is the "
          "one that binds across lanes" % DAILY_LOSS)

    plan = [("L5_BTC5M_COMPLETION_PAIR_COST",
             str(COMPLETION_PAIR_COST) if completion else ""),
            #: THE FLEET IS DECLARED BY WHAT WAS APPROVED, so the service
            #: cannot start a lane this run did not arm, and `child_env`
            #: divides the account ceiling by this same count.
            ("L5_5M_FLEET", ",".join(strategies)),
            ("L5_BTC5M_NOT_AFTER", not_after),
            ("L5_BTC5M_ENTRY_MODE", "STRADDLE"),
            ("L5_BTC5M_STRADDLE_PRICE", str(PRICE)),
            ("L5_BTC5M_STRADDLE_RUNGS", ""),
            ("L5_BTC5M_CLIP_USD", sh["clip_usd"]),
            ("L5_BTC5M_CAMPAIGN", "ON"),
            ("L5_BTC5M_MARKET", ""),
            ("L5_SHADOW_ONLY", "0")]
    #: ONE DOCUMENT PER LANE, under the name its child is handed.
    for name in strategies:
        plan.append(("L5_CAMPAIGN_APPROVAL_" + name, approvals[name]))
    #: AND THE BARE NAME for a single BTC5M lane, which is what a plain
    #: `l5_btc5m_runner` reads when it is not started by the fleet. Same
    #: document, so the two can never disagree.
    if strategies == [ORIGIN.BTC5M]:
        plan.append(("L5_CAMPAIGN_APPROVAL", approvals[ORIGIN.BTC5M]))
    #: LAST, ALWAYS. Nothing is armed until every approval and parameter
    #: above is already set.
    plan.append(("L5_BTC5M_LIVE", "ARMED_BY_OWNER"))

    if not go:
        print("\nDRY RUN. With --go it would set, THEN deploy once:")
        for n, val in plan:
            print("  %-26s = %s" % (n, (val[:58] + "...") if len(val) > 58 else val))
        return 0

    print("\nsetting variables (skipDeploys) ...")
    for n, val in plan:
        if not upsert(n, val):
            return refuse("could not set %s; service left disarmed" % n)
        print("  set %s" % n)
    print("deploying once ...")
    q = ("mutation($s:String!,$e:String!){ serviceInstanceRedeploy("
         "serviceId:$s, environmentId:$e) }")
    print("  ->", json.dumps(gql(q, {"s": BTC5M, "e": ENVIRON}))[:160])
    print("\nARMED. Do NOT redeploy until the campaign ends: it would orphan "
          "an in-flight window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
