"""Owner-approved 2026-11-12 ("approve ACC_MAX_ROUNDS 24"): L5_ACC_MAX_ROUNDS 8 -> 24 (placement-count only; code hard cap 64).
Written at 18:57:30, between windows, so the restart lands before 19:00Z opens."""
import time, datetime as dt
from bal_common import *
t = dt.datetime(2026, 11, 12, 18, 57, 30, tzinfo=dt.timezone.utc).timestamp()
while time.time() < t: time.sleep(5)
c = client(("L5_ACC_MAX_ROUNDS",)); b = c.latest_deployment()
print(c.set_variables({"L5_ACC_MAX_ROUNDS": "24"})["written"], flush=True)
d = c.wait_for_deployment(expect_sha=SHA, not_before=b["createdAt"], timeout_s=420); print("DEPLOYED", d["commitHash"][:9], d["status"], flush=True)
