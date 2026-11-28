# Sports bot disarmed deploy - ae5adbe16 (22 Nov 2026)

Owner-approved disarmed deploy. No variable changed. Lock stayed on.

- Release: `release/pmus-sports-rn1-ae5adbe16` = `ae5adbe166679c6b7eba59b8eae4ad8846e3fb67`
  (5658476e9 exact campaign commitment, 5e4f153c2 two-market limit, ae5adbe16 cash-basis meter).
- 03:37:43Z connect-source -> deployment 31ffb55e SKIPPED ("no changes detected in watch
  paths"; watch pattern is deploy/pmus_spring_l5/DEPLOY_TRIGGER). Old release kept running.
- 03:40Z redeploy of 31ffb55e -> deployment 3ecdb865 SUCCESS 03:40:39Z; 1bafc4f9 removed.

Verified from the new deployment (read-only, to 03:45:35Z)
- meta.commitHash ae5adbe166679c6b7eba59b8eae4ad8846e3fb67, branch release/pmus-sports-rn1-ae5adbe16.
- Old container: cancelled_all_own, still_open empty. One start, no traceback.
- Boot: rn1 on, qty 2, spend 16, unmatched 6, per-market 8, residual 3, pair cap 0.97;
  daily loss 5; markets 2; max_taker_qty 0; recovery taker false; intl guard require true;
  combo maker shadow. PILOT_PNL loss_stop_usd 4.
- Lock: 0 ORDER, 0 FILL; place_refused_submission_lock on every attempt.
- Watchdog: every 20 s through the restart, 3 registries OK, open_orders 0, no halt.
- INVENTORY_RECONCILE: reconciled, 0 open orders, 0 unmatched, walk complete.
- PILOT_PNL 03:44:11Z: realized_net_usd 0.3500 (once), outstanding 0, fees 0, rebates 0,
  positions_checked true, unconfirmed [], two markets paired_flat (0.15 + 0.20).
  Cash 321.6638; baseline taken at this reading, so the cash cross-check is trivially 0
  and becomes informative only from the next campaign.

Notes
- The engine is trying to open (rn1_open, bos-sac) and is held only by the lock.
- PMUS_RN1_PILOT_SINCE is still last night's value; reset only when a new campaign is authorized.
- A skipped build is expected on every release; touching DEPLOY_TRIGGER in the release
  commit would make the first build run.
