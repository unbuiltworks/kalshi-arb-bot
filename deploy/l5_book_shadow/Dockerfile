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

# The maker-engine LIVE SHADOW. Read-only, like everything else here: it makes
# no network request, holds no credential, and tails the tape this collector
# has already written. Its own boot gate refuses to start if any order-capable
# module turns out to be importable in this image, which is the same rule the
# comment above states for the collector.
#
# These three modules are the WHOLE dependency set, and none of them imports
# numpy -- this image is python:3.11-slim with no pip install, and `fee_safe`
# was moved into l5_maker_engine precisely so that stays true.
COPY src/l5_maker_shadow_runner.py src/l5_maker_shadow_runner.py
COPY src/l5_maker_engine.py        src/l5_maker_engine.py
COPY src/l5_behavior_state.py      src/l5_behavior_state.py
COPY deploy/l5_book_shadow/start.sh deploy/l5_book_shadow/start.sh
RUN touch src/__init__.py scripts/__init__.py

ENV PYTHONUNBUFFERED=1
# --minutes 0 == continuous. Rows are appended and flushed per write, so a
# restart loses at most one interval rather than the window.
CMD ["python3", "-m", "scripts.l5_book_shadow_run", "--minutes", "0"]
