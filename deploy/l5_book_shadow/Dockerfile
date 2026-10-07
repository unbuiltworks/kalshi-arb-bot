# TWO-TOKEN BOOK-STATE SHADOW COLLECTOR. READ-ONLY. NEVER TRADES.
#
# Records BOTH outcome tokens' books, independently, alongside L5's public
# activity, so the EXTEND->COMPLETE turn has the state it needs. It records
# BOOK and ACTIVITY and never a fill; nothing here may be called realized.
#
# DELIBERATELY ABSENT, and the boot gate FAILS if any of them turns out to be
# importable in this image (same rule as deploy/l5_exec1_preflight):
#   src/l5_pmus_transport.py   -- owns the signed POST path
#   src/pmus_q1_executor.py    -- owns place_limit_buy / run_campaign
#   src/pmus_micro_executor.py -- the other order path
#   analysis/l5/live_exec*     -- the armed probe specs/ledgers
#
# The image contains ONE module and its entrypoint. There is no order verb to
# reach, with or without an environment variable, so this image cannot become
# an executor.
FROM python:3.11-slim
WORKDIR /app

COPY src/l5_book_shadow.py src/l5_book_shadow.py
COPY scripts/l5_book_shadow_run.py scripts/l5_book_shadow_run.py
RUN touch src/__init__.py scripts/__init__.py

ENV PYTHONUNBUFFERED=1
# --minutes 0 == continuous. Rows are appended and flushed per write, so a
# restart loses at most one interval rather than the window.
CMD ["python3", "-m", "scripts.l5_book_shadow_run", "--minutes", "0"]
