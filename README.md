# Limited Recovery Basket AI — open-source MT5 Expert Advisor with a Python AI filter

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://github.com/Yassin6up/TRading/actions/workflows/tests.yml/badge.svg)](https://github.com/Yassin6up/TRading/actions/workflows/tests.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5%20(MQL5)-orange.svg)
![Status: demo only](https://img.shields.io/badge/status-research%20%2F%20demo%20only-red.svg)

**Free, open-source MetaTrader 5 (MT5) Expert Advisor in MQL5**, with a Python FastAPI AI
decision service, a tick-level forex backtester with walk-forward validation, and Windows/Linux
VPS deployment scripts. It trades a **limited** hedging recovery basket (not unlimited martingale)
with a deterministic risk engine. Fork it, study it, improve it.

It is a MetaTrader 5 Expert Advisor, plus a Python decision service, research backtester and
deployment scripts, for a **limited** alternating-hedge recovery basket:

* Open BUY 1q. If the bid reaches the lower boundary, add SELL 2q. If the ask returns to the
  upper boundary, add BUY 3q. Then stop. Initial SELL is the mirror image. The progression is
  configurable (default `[1, 2, 3]`) and is **never** silently replaced by doubling.
* **Every** basket position (winners and losers) is closed together when the net basket P&L
  reaches its target, the hard loss limit is hit, or another deterministic rule fires.
* An optional AI layer can only **block** a new basket or a recovery leg. It cannot open, size,
  hold or close anything.

> ⚠️ **Research / demo software.** It does not and cannot guarantee profits. The entry signal is a
> documented *research baseline* with no proven edge. Recovery baskets have a high win rate
> because they hold losers. That is **not** evidence of profitability, and individual losses can be
> large, including losses **beyond** the planned stop (gaps, slippage, outages). Live trading is
> disabled by default and needs a separate, explicit decision.

## Verification status

What exists, what was tested, and what has **not** been verified:

| Component | Implemented | Tested here | Not verified |
|---|---|---|---|
| Strategy core (Python reference): state machine, triggers, sizing, accounting, guards | ✅ | ✅ unit and scenario tests | — |
| Simulated broker with costs, swaps, margin, stop-out, fault injection | ✅ | ✅ | Real broker behaviour |
| Tick backtester, walk-forward, labels with purging, stress scenarios | ✅ | ✅ (synthetic data only) | **No real-market backtest has been run.** No broker tick data was available. |
| AI layer: `rules-v1` filter, numpy logistic regression + Platt calibration, shared feature code | ✅ | ✅ | No out-of-sample evidence on real data that the AI helps |
| FastAPI service: HMAC auth, replay/idempotency, strict schemas, SQLite migrations, backups, dashboard | ✅ | ✅ integration tests + live `uvicorn` smoke test (loopback) | Windows service install |
| MQL5 `LRB_EA` (execution owner), `LRB_AIBridge`, scripts | ✅ | ❌ **not compiled** (no MetaEditor here); contract tests check its JSON format against the service | Compilation, Strategy Tester, demo account behaviour, broker fills |
| Windows PowerShell deployment (tasks, health checks, backup, emergency stop) | ✅ | ❌ (no PowerShell here) | Everything |
| Linux service host option (systemd, Caddy, ufw) | ✅ | `bash -n` only | Everything |
| Connection to any MT5 terminal, broker or VPS | — | — | **Never made.** No deployment has happened. |

Test suite: `cd python && uv run pytest` runs **86 tests** (sizing, accounting, state machine,
resilience, service, research). See [docs/VALIDATION.md](docs/VALIDATION.md).

## Repository layout

```
mql5/Experts/LRB/LRB_EA.mq5          EA: single execution owner, local risk engine, never uses the network
mql5/Experts/LRB/LRB_AIBridge.mq5    bridge EA on a 2nd chart: signed WebRequest <-> files, telemetry; NO trading
mql5/Include/LRB/*.mqh               Defines, Sizing (port of python sizing), Json, Hmac (HMAC-SHA256)
mql5/Scripts/LRB/                    InspectEnvironment, ExportTicks, KillSwitch
mql5/Presets/LRB_EA_demo.set         conservative demo inputs
python/src/lrb/core/                 reference strategy engine (+ tests)
python/src/lrb/research/             sim broker, tick data, backtest, labels, walk-forward, stress, CLI
python/src/lrb/ai/                   features (closed bars only), rules-v1, logistic + Platt, decide()
python/src/lrb/service/              FastAPI app, auth, schemas, SQLite store, backup, entry point
python/src/lrb/tools/                inspect_mt5 (Windows), spec_from_inspect, bridge_emulator
python/uv.lock, requirements.lock    pinned and hashed dependencies
deploy/windows/                      inspect, install, run, health, autostart, time sync, firewall, backup, emergency stop
deploy/linux/                        systemd unit, backup timer, Caddy TLS example, installer
config/strategy.example.json         backtester configuration (mirrors EA inputs)
.env.example                         service configuration (no secrets)
docs/                                specification, risk and sizing, architecture, validation, operations, environment
```

## Architecture (summary)

On the Windows VPS, the MT5 terminal runs two EAs. `LRB_EA` (chart 1) is the only component that
trades. `LRB_AIBridge` (chart 2) runs all network I/O, so a synchronous `WebRequest` can never
block the risk loop. The two exchange atomic JSON files in `<Common>\Files\LRB\<magic>\`. The
Python service listens on `127.0.0.1:8787` with HMAC-signed requests and responses, replay
protection, 60 s decision expiry and a read-only dashboard. Details:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Fail-safe:** if the AI times out, returns invalid data, is stale, or is unreachable, new entries
are blocked and recovery legs stop being added (when approval is required). The existing basket
keeps being managed and closed locally, and stops are never disabled.

## Quick start: research (any OS)

```bash
cd python
uv sync                                   # Python 3.11+, uses uv.lock
uv run pytest                             # 86 tests
uv run lrb-backtest stress                # deterministic stress scenarios
uv run lrb-backtest synth --days 120 --out ../data/synth_ticks.csv      # SYNTHETIC data
uv run lrb-backtest run --ticks ../data/synth_ticks.csv --config ../config/strategy.example.json
uv run lrb-backtest walkforward --ticks ../data/synth_ticks.csv --out ../reports/wf.json
```

For real research, export broker ticks with `LRB_ExportTicks.mq5` and pass the real symbol
specification (`--symbol-spec`, produced from `LRB_InspectEnvironment` output with
`python -m lrb.tools.spec_from_inspect`).

## Quick start: demo deployment (Windows VPS)

See [docs/OPERATIONS.md](docs/OPERATIONS.md). In short:

1. Run the read-only inspection (`deploy\windows\inspect-vps.ps1`, then `LRB_InspectEnvironment` in MT5).
   You need a **hedging demo** account.
2. `install-mql5.ps1`, compile in MetaEditor, then `install-service.ps1` and `time-sync.ps1`.
3. Allow WebRequest to `http://127.0.0.1:8787`. Attach `LRB_EA` (EURUSD M15, `LRB_EA_demo.set`)
   and `LRB_AIBridge` (second chart). Save the profile `LRB`. Set up auto-logon and `mt5-autostart.ps1`.
4. Watch `http://127.0.0.1:8787/dashboard` and the EA journal.

**Emergency:** `deploy\windows\emergency-stop.ps1 -Mode FLATTEN`, or the `LRB_KillSwitch` script.
If the terminal is down, close the positions manually in MT5 or at the broker.

## Key safety properties (enforced in code)

* `InpDemoOnly=true` by default. Live trading needs `InpDemoOnly=false`, `InpAllowLive=true` **and**
  `InpLiveConfirm=I_ACCEPT_LIVE_RISK`. A netting account is refused.
* Base lot is computed from the risk budget and the worst reachable leg state at the hard stop.
  **It is never rounded up**: if the broker minimum is too large, the basket is rejected.
* Conservative defaults: 1% basket risk, 0.5% target, 3 positions, 2% daily loss including
  floating, 6% equity drawdown halt with manual reset, 48 h maximum duration, exposure and margin caps.
* Restart: the EA reconciles against broker positions and deal history before any entry. An empty
  local state never means "no positions".
* Only positions with this EA's magic number on its symbol are ever touched.

## Limitations

* No real-market results exist. Every number produced so far comes from **synthetic** data and
  only shows that the engine works.
* The MQL5 code is an unverified port of the tested Python engine. Compile it and test it on a demo
  account. Use the Strategy Tester with `InpAIMode=AI_OFF`, because the bridge cannot run there.
* The software stop cannot guarantee the maximum loss. The optional broker-side SL protects single
  legs only (see RISK_AND_SIZING §5).
* The economic-calendar filter is implemented but unverified. If it is enabled and data is
  missing, entries are blocked.
* The tick exporter converts server time to UTC with the *current* offset. DST changes inside the
  exported range shift times by one hour.

## Documentation

* [docs/STRATEGY_SPEC.md](docs/STRATEGY_SPEC.md): state machine, exact trigger prices, exits, restart
* [docs/RISK_AND_SIZING.md](docs/RISK_AND_SIZING.md): sizing derivation, worked example, accounting, guards
* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, protocols, authentication, failure modes
* [docs/VALIDATION.md](docs/VALIDATION.md): methodology, test inventory, synthetic results, AI vs no AI
* [docs/OPERATIONS.md](docs/OPERATIONS.md): install, demo setup, MT5 autostart, backups, emergency shutdown
* [docs/ENVIRONMENT_INSPECTION.md](docs/ENVIRONMENT_INSPECTION.md): what was and was not inspected

## License and contributing

This project is open source under the [MIT License](LICENSE). Anyone may use, copy, modify,
merge, publish, distribute and sell copies of it, provided the license notice is kept. It comes
**without any warranty**: the authors are not liable for trading losses or any other damage.

Improvements are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to submit changes and the
safety rules every change must keep.

## Who this is for

* **Algorithmic traders** who want a transparent MT5 hedging EA with real risk limits to study on a demo account.
* **MQL5 developers** looking for an example of a state-machine EA, crash-safe restarts, basket
  accounting and HMAC-signed `WebRequest` communication.
* **Python and quant developers** interested in tick-level backtesting, walk-forward validation,
  calibrated probability models, and honest A/B evaluation of an AI trade filter.

## FAQ

**Is this a martingale EA?** No. It is a *limited* recovery basket: at most the configured number of
legs (default 1×, 2×, 3× the base lot), never automatic doubling, a hard money and price stop,
daily-loss and drawdown halts.

**Does the AI make it profitable?** There is no evidence that it does. On synthetic data the AI
filter showed no skill (see [docs/VALIDATION.md](docs/VALIDATION.md)). Results on real broker data are welcome as contributions.

**Can I run it live?** It is demo-only by default. Live trading needs three deliberate input
changes, and it is entirely at your own risk (see the MIT License: no warranty).

**Does it run on Linux?** The Python service and backtester do. The MT5 terminal and EA need Windows
(the official `MetaTrader5` Python package is Windows-only).

## Keywords

MetaTrader 5, MT5, MQL5, Expert Advisor, EA, forex trading bot, algorithmic trading, hedging EA,
recovery basket, grid and hedge strategy, risk management, position sizing, AI trading filter,
machine learning, FastAPI, Python backtesting, tick data, walk-forward analysis, VPS deployment,
open source trading software.
