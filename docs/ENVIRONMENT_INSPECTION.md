# Environment inspection

Inspected on 2026-10-18 (UTC). This file records what was **actually observed**, and lists
separately what could **not** be inspected.

## 1. The development environment (where this code was written)

| Item | Finding |
|---|---|
| Machine | Ephemeral cloud **Linux** container. It is **not** the trading VPS. |
| OS | Ubuntu 24.04.5 LTS, x86_64, 4 vCPU, 15 GiB RAM |
| Repository | `yassin6up/trading`. It was **empty** (no commits) before this work. |
| Python | 3.11.17 and 3.13.16 available; `uv` 0.11.32 used for locking |
| MT5 terminal | **Not installed, not running.** There is no Wine or Windows environment. |
| `MetaTrader5` Python package | `pip download MetaTrader5` on Linux: *"No matching distribution"*. PyPI lists only `win_amd64` wheels (cp36–cp314) for version 5.0.6231. |
| Broker / account / symbol | **Unavailable.** No terminal or credentials exist, and none were requested. |
| Historical bid/ask ticks | **None available.** Research runs used a clearly labelled **synthetic** generator. |
| AI provider / local model | No user AI provider or API key is configured. The system therefore uses a **local statistical model** (`rules-v1` by default, or an optional logistic model trained by `lrb-backtest train`). No LLM is used. |
| MQL5 compiler | **Not available.** The MQL5 sources have **not been compiled**. |
| PowerShell | **Not available.** The `.ps1` scripts have **not been executed**. |

## 2. Official documentation that drove the design

* *MetaTrader 5 Python integration* (mql5.com/en/docs/python_metatrader5): the package gets data
  "via interprocessor communication directly from the MetaTrader 5 terminal", is installed with
  `pip install MetaTrader5`, and has setup steps written for Windows. Together with the PyPI wheel
  list, this means Windows is required for Python↔MT5 on the same host.
* *WebRequest* (mql5.com/en/docs/network/webrequest): the call is synchronous and blocks until a
  response or timeout. It is not allowed in indicators, cannot run in the Strategy Tester, and the
  URL must be added to the terminal's allow-list. This is why all network I/O lives in a separate
  bridge EA (see ARCHITECTURE.md).

## 3. What you must inspect on the real VPS (not done yet)

Run these and keep the outputs with your deployment notes:

1. `deploy\windows\inspect-vps.ps1`: OS, time sync, terminals, sessions, Python, ports.
2. MT5 script `LRB_InspectEnvironment` on the target symbol: broker, exact symbol name (for
   example `EURUSD`, `EURUSD.r` or `EURUSDm`), account currency, demo/real, **hedging support**,
   contract size, tick size and value, volume min/max/step, initial and hedged margin, stop-out
   mode and level, execution and filling modes, swaps, **trade sessions**, tick-history
   availability, and the server-to-UTC offset.
3. `LRB_ExportTicks` for the research period, then run the backtests on **real** broker ticks.

Update `config/strategy.example.json` and the EA inputs (`InpCommissionPerLotSide`,
`InpMaxSpreadPts`, distances) to match the broker. These defaults assume a 5-digit EURUSD.

## 4. Decisions that need your input before demo deployment

* Which VPS will host MT5. It must be Windows, and the inspection above must be run on it.
* The broker and demo account. It must be a **hedging** account.
* The exact symbol, and its commission model (per side or round turn) for the cost estimate.
* Whether the Python service runs on the same Windows host (default) or a separate Linux host (option B).

No services were purchased, no secrets were requested, and no orders were placed.
