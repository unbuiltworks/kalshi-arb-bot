# Fresh Polymarket account preflight deployment

Force a new source-triggered Railway deployment after changing the dormant smoke service start command to `python -m src.polymarket_account_preflight_now`. This check is read-only and contains no order-entry call.
