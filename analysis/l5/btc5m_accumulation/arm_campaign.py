"""Arm ONE bounded BTC5m straddle campaign. Checks first, then acts.

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
COMPLETION_PAIR_COST = D("0.96")


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
    completion = "--completion" in argv
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
    booked = sh["attested_worst_usd"] if live else sh["phantom_worst_usd"]
    print("  booked worst case used: %s" % booked)
    if booked > room:
        fits = int(room / D(sh["shares"])) if sh["shares"] else 0
        return refuse(
            "the LEDGER would book up to %s against %s of "
            "room, so the account-wide daily halt would trip after about %d "
            "window(s) and stop every lane. Real risk is only %s. Either run "
            "%d share(s) a side, or fix the cost basis "
            "(COST_BASIS_OVERSTATEMENT.md)."
            % (booked, room, fits, sh["real_worst_usd"],
               max(1, int(room / WINDOWS)) if room >= WINDOWS else 1))

    # 3. account exposure headroom. Foreign contracts are charged at $1.00
    #    GROSS, so another lane's MATCHED inventory can consume the cap.
    #    Shadow cannot see this: the account block reads null unarmed.
    cap = D(str(v.get("L5_ACCOUNT_MAX_EXPOSURE_USD") or "0"))
    NEED_EXPOSURE_HEADROOM = sh["need_exposure_headroom"]
    dep, _ = latest_deployment(BTC5M)
    other = None
    for m in reversed(logs(dep, 400)):
        if m.startswith("L5_BTC5M_STEP"):
            acct = json.loads(m.split(" ", 1)[1]).get("account") or {}
            if acct.get("other_usd") is not None:
                other = D(str(acct["other_usd"]))
                break
    if other is None:
        print("NOTE: no recent armed step, so foreign exposure is unknown. "
              "It is only observable while armed; the first window will "
              "report it and refuse with ACCOUNT_EXPOSURE_CAP if short.")
    elif other + NEED_EXPOSURE_HEADROOM > cap:
        return refuse("foreign exposure %s + %s exceeds cap %s"
                      % (other, NEED_EXPOSURE_HEADROOM, cap))
    else:
        print("foreign exposure %s + %s within cap %s" % (other, NEED_EXPOSURE_HEADROOM, cap))

    # 4. the approval, generated now so it is not stale, and self-checked
    here = os.path.dirname(os.path.abspath(__file__))
    gen = subprocess.run(
        [sys.executable, "-I", os.path.join(here, "make_approval.py"),
         str(WINDOWS), "--exposure", sh["exposure"],
         "--daily-loss", DAILY_LOSS],
        capture_output=True, text=True, timeout=120)
    doc = [x for x in gen.stdout.splitlines() if x.startswith('{"approved"')]
    if "campaign planner accepted: YES" not in gen.stdout or not doc:
        print(gen.stdout[-1500:])
        return refuse("the generator's own self-check rejected the document")
    approval = doc[0]
    starts = json.loads(approval)["window_starts"]
    not_after = starts[-1][:-1]
    import datetime as dt
    na = (dt.datetime.fromisoformat(not_after) + dt.timedelta(minutes=5))
    not_after = na.strftime("%Y-%m-%dT%H:%M:%SZ")
    print("approval ok: %d windows, %s .. %s, NOT_AFTER %s"
          % (len(starts), starts[0], starts[-1], not_after))

    plan = [("L5_BTC5M_COMPLETION_PAIR_COST",
             str(COMPLETION_PAIR_COST) if completion else ""),
            ("L5_CAMPAIGN_APPROVAL", approval),
            ("L5_BTC5M_NOT_AFTER", not_after),
            ("L5_BTC5M_ENTRY_MODE", "STRADDLE"),
            ("L5_BTC5M_STRADDLE_PRICE", str(PRICE)),
            ("L5_BTC5M_STRADDLE_RUNGS", ""),
            ("L5_BTC5M_CLIP_USD", sh["clip_usd"]),
            ("L5_BTC5M_CAMPAIGN", "ON"),
            ("L5_BTC5M_MARKET", ""),
            ("L5_SHADOW_ONLY", "0"),
            ("L5_BTC5M_LIVE", "ARMED_BY_OWNER")]

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
