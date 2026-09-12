"""Build-time safety fix: enforce the residual cap on projected EXTEND fill.

The canonical session historically checked only the residual BEFORE an EXTEND.
At 5.944 behavioral units with a 6-unit cap, a full ~1-unit EXTEND could pass
that check and leave ~6.97 units. The declared cap therefore was not a hard
bound. This strict transform changes only the cap predicate: same-side EXTEND
is capped whenever its full planned fill would put behavioral residual above
the active rung cap. COMPLETE remains untouched and can always reduce risk.
"""
from pathlib import Path

PATH = Path("src/pmus_spring_l5_session.py")

OLD = '''            elif relation == "extend" and abs(
                    inv.behavioral_residual()) >= max_residual_units():
                # bounded imbalance (same bound as the shared planner):
                # no further same-side EXTEND past the residual cap; a
                # COMPLETE is still allowed, and nothing is flattened
                relation, refusal = "hold", (
                    f"residual_cap:{abs(inv.behavioral_residual())}"
                    f">={max_residual_units()}")
'''

NEW = '''            elif relation == "extend" and (
                    abs(inv.behavioral_residual()) >= max_residual_units()
                    or (inv.unit is not None and inv.unit > 0
                        and abs(inv.behavioral_residual())
                        + float(spec["qty"] / inv.unit)
                        > max_residual_units())):
                # Hard PROJECTED imbalance bound. The old predicate checked
                # only inventory_before, so 5.94/6 units could submit another
                # ~1-unit EXTEND and fill through the cap. Size is already
                # known here; refuse before submission if a full fill would
                # leave more than the active rung's residual limit. COMPLETE
                # is still allowed and nothing is forcibly flattened.
                projected = (abs(inv.behavioral_residual())
                             + (float(spec["qty"] / inv.unit)
                                if inv.unit is not None and inv.unit > 0
                                else 0.0))
                relation, refusal = "hold", (
                    f"residual_cap:{abs(inv.behavioral_residual())}"
                    f"->{projected}>{max_residual_units()}")
'''

text = PATH.read_text(encoding="utf-8")
count = text.count(OLD)
if count != 1:
    raise SystemExit(
        f"R5FT projected residual transform refused: expected block exactly once; found {count}")
patched = text.replace(OLD, NEW, 1)
if patched.count("Hard PROJECTED imbalance bound") != 1:
    raise SystemExit("R5FT projected residual transform verification failed")
PATH.write_text(patched, encoding="utf-8")
print("R5FT_PROJECTED_RESIDUAL_CAP_PATCHED")
