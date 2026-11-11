# prototypes/

Code that is tested but is **not deployable source**. No Dockerfile copies
this directory.

- `l5_btc5m_execution.py`: BTC 5-minute execution adapter. It is here, not
  in `src/`, because it submits orders without the authorization claim that
  every submission path in `src/` must spend. See its docstring.
