# Contributing to Limited Recovery Basket AI

Thanks for helping. This project is open source under the [MIT License](LICENSE): anyone may read,
use, fork, modify and redistribute it. By contributing you agree that your contribution is
released under the same license.

Please also read the [Code of Conduct](CODE_OF_CONDUCT.md).

## How to fork and work on the project

1. **Fork** the repository on GitHub (the *Fork* button, top right).
2. **Clone your fork** and add the original as `upstream`:
   ```bash
   git clone https://github.com/<your-user>/TRading.git
   cd TRading
   git remote add upstream https://github.com/Yassin6up/TRading.git
   ```
3. **Create a branch** for each change. Never work directly on `main`:
   ```bash
   git checkout -b fix/short-description      # or feat/..., docs/..., test/...
   ```
4. **Set up and run the tests** (Python 3.11+ and [uv](https://docs.astral.sh/uv/)):
   ```bash
   cd python
   uv sync
   uv run pytest          # every test must pass before you open a pull request
   ```
5. **Make focused commits** with clear messages, for example
   `fix(engine): abort basket when leg 1 is rejected`.
6. **Keep your fork up to date** before opening a pull request:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```
7. **Push to your fork** and open a **pull request** against `Yassin6up/TRading:main`. Fill in the
   pull request template.

## Rules for every change

* **One topic per pull request.** Small pull requests get reviewed faster.
* **Tests.** Add or update tests for any behaviour you change. Pull requests that reduce test
  coverage of risk logic will not be merged.
* **Python is the reference.** The engine in `python/src/lrb/core/` defines intended behaviour. If
  you change strategy or risk logic, change `mql5/Experts/LRB/LRB_EA.mq5` and
  `mql5/Include/LRB/Sizing.mqh` the same way. In the pull request, say whether you compiled the
  MQL5 code in MetaEditor and whether you ran it on a demo account.
* **Docs.** Update `docs/` and the README when behaviour, inputs or commands change.
* **No secrets.** Never commit broker logins, passwords, account numbers, API keys or HMAC secrets.
* **Style.** Match the surrounding code: type hints and dataclasses in Python, the existing naming
  in MQL5 (`Inp...` for inputs, `g_...` for globals).

## Safety rules (non-negotiable)

These protect everyone who downloads this project. Pull requests that weaken them will be closed:

* Demo-only stays the default (`InpDemoOnly=true`, `demo_only=True`); live trading stays off by default.
* The EA stays the **only** component with order-execution authority. The AI may only block trades.
* An AI failure (timeout, invalid data, outage) must block new entries and must **never** disable
  exits or stops.
* Lot sizes are never rounded up past the risk budget. Recovery stays limited: no unlimited
  martingale, no automatic risk increase after losses.
* Only positions with the EA's own magic number and symbol may be touched.

## Results and claims

* Never claim profitability, or that the AI improves results, without **out-of-sample results on
  real broker tick data**. Include the data period, broker, symbol, costs and every walk-forward segment.
* Label synthetic results clearly as synthetic.
* Reporting bad results is just as valuable as reporting good ones.

## Good first contributions

* Run the EA in the MT5 Strategy Tester or on a demo account and report compile errors or behaviour
  differences against the Python engine.
* Walk-forward results on real broker tick data (`LRB_ExportTicks.mq5` + `lrb-backtest walkforward`).
* More stress scenarios, tests, and documentation translations.

## Reporting bugs and ideas

Use the GitHub issue templates (bug report or feature request). For security problems, follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.
