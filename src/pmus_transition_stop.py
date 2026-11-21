"""The stop the BOOT RE-ARM PREFLIGHT may evaluate, and nothing else.

THE DEADLOCK THIS EXISTS FOR, observed live 2026-09-11T21:52:43Z. The probe
booted, the re-arm gate priced the day at combined_loss -15.1516 against the
ACTIVE rung's stop -- R5F, -15.00 -- and refused. The owner's approved
successor R5FT carries -30.00, but an approval is consumed at the CAMPAIGN
ADVANCE GATE, which the campaign can only reach after the re-arm gate lets it
run. The gate that would authorise the new stop is downstream of the gate the
old stop is blocking. A rung-level number cannot break a rung-level deadlock.

THE FIX THE OWNER SPECIFIED, in his words: "effective stop = the stop
belonging to the currently authorized campaign/rung. At boot, if there is an
exact, unconsumed, signed/recorded approval for transition to R5FT, the re-arm
preflight may evaluate the approved R5FT stop of -$30 before consuming the
approval. Otherwise it must continue using the active rung's existing stop.
Do not make -$30 override R1/R2/R4 or any historical/micro-rung stop."

So this is NOT an account-wide override. `ladder.daily_loss_stop()` is
untouched and every other reader still gets the active rung's own number. Only
the boot preflight calls in here, and only an EXACT pending transition moves
the number it sees.

FAIL CLOSED EVERYWHERE. No approval, an approval already consumed, an unknown
rung, an approval naming a rung other than the transition target, a missing
deployment identity -- each returns the ACTIVE rung's stop with the reason
recorded. "We could not establish the transition" is never "the transition
holds".
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Mapping, Optional, Sequence

from src import pmus_l5_ramp_ladder as ladder

# The transition the owner approved on 2026-09-11 ("go 3" + "Join the touch,
# hard cap <=1.00", then the $30 raise). An approval naming any OTHER rung
# does not move the boot stop, however well formed it is -- widening the
# re-arm budget is not a general capability, it is this one authorised step.
TRANSITION_FROM = "R5F"
TRANSITION_TO = "R5FT"
TRANSITION_APPROVAL_ID = "R5FT-CANARY-2026-09-11"

# The floor on what any approved transition may loosen the boot stop to. The
# owner authorised -$30.00 and no more; an approval or rung asking for more is
# clamped back here rather than honoured, so neither a later ladder edit nor a
# later approval row can widen the account's worst day on its own.
APPROVED_TRANSITION_STOP_LIMIT = D("-30.00")


def _rung_stop(rung_id: Optional[str]) -> Optional[D]:
    """The declared stop of a rung that actually exists in the ladder, else
    None. ladder.rung() falls back to the authorised rung for an unknown id,
    so an id check comes first -- otherwise a typo silently inherits R1."""
    if not rung_id:
        return None
    r = ladder.rung(str(rung_id))
    if str(r.get("id")) != str(rung_id).strip().upper():
        return None
    return ladder.daily_loss_stop(r)


def transition_stop(active_rung_id: str,
                    approval: Optional[Mapping[str, Any]] = None,
                    *,
                    consumed_ids: Sequence[str] = (),
                    deployment: Optional[Mapping[str, Any]] = None,
                    require_deployment: bool = True) -> dict:
    """The stop the boot re-arm preflight may price the day against.

    Returns {"stop", "source", "approval", "active_rung_stop", "refused"}.
    `source` is "approved_transition" only when every condition holds;
    otherwise "active_rung" and `refused` names what failed.
    """
    refused: list = []
    active_stop = _rung_stop(active_rung_id)
    if active_stop is None:
        # an active rung we cannot even identify is not a licence to widen
        refused.append("active_rung_unknown")
        active_stop = ladder.daily_loss_stop(ladder.rung(active_rung_id))

    def _out(stop: D, source: str, appr_id: Optional[str] = None) -> dict:
        return {"stop": str(stop), "source": source, "approval": appr_id,
                "active_rung_stop": str(active_stop),
                "transition_limit": str(APPROVED_TRANSITION_STOP_LIMIT),
                "refused": refused,
                "labels": "the boot re-arm preflight only; ladder."
                          "daily_loss_stop() is unchanged for every other "
                          "reader and no other rung's stop moves"}

    if not approval:
        refused.append("no_pending_approval")
        return _out(active_stop, "active_rung")

    appr_id = str(approval.get("campaign_id") or "")
    appr_rung = str(approval.get("rung") or "")

    # EXACT, and all three parts of it: the approval the owner recorded, the
    # rung it names, and the rung the account is transitioning FROM.
    if appr_id != TRANSITION_APPROVAL_ID:
        refused.append("approval_id_mismatch")
    if appr_rung != TRANSITION_TO:
        refused.append("approval_rung_mismatch")
    if str(active_rung_id).strip().upper() != TRANSITION_FROM:
        refused.append("active_rung_is_not_the_transition_source")
    if appr_id and appr_id in set(consumed_ids):
        refused.append("approval_already_consumed")

    # the approval must be RECORDED, not merely passed in: an approval row
    # carries the owner's own words, and one without them is not evidence
    if not str(approval.get("source") or "").strip():
        refused.append("approval_not_recorded")
    if str(approval.get("approved_by") or "") != "owner":
        refused.append("approval_not_owner")

    if require_deployment:
        d = dict(deployment or {})
        if not (d.get("commit") and d.get("deployment_id")):
            refused.append("deployment_identity_unknown")

    target_stop = _rung_stop(appr_rung)
    if target_stop is None:
        refused.append("approved_rung_not_in_ladder")

    if refused:
        return _out(active_stop, "active_rung", appr_id or None)

    # max() of two negatives is the TIGHTER: the clamp bounds LOOSENING only,
    # so an approval asking for -100 reads back the authorised -30
    stop = max(target_stop, APPROVED_TRANSITION_STOP_LIMIT)
    # and a transition must never TIGHTEN into a surprise either: if the
    # approved rung is stricter than the active one, the stricter number is
    # what the owner approved, so it stands
    return _out(stop, "approved_transition", appr_id)
