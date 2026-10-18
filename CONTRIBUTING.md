# Contributing

Anyone is welcome to read, use, modify and improve this project under the [MIT License](LICENSE).
Fork the repository, make your change on a branch and open a pull request.

## Before you open a pull request

```bash
cd python
uv sync
uv run pytest          # all tests must pass
```

* Add or update tests for any behaviour you change. The Python engine in `python/src/lrb/core/`
  is the reference: when you change strategy logic, change `mql5/Experts/LRB/LRB_EA.mq5` (and
  `mql5/Include/LRB/Sizing.mqh`) the same way, and say in the PR whether you compiled the MQL5 code.
* Keep the documentation in `docs/` in step with the code.
* Never commit secrets, broker logins, API keys or account numbers.

## Safety rules for changes

These protect everyone who runs the code. Pull requests that weaken them will not be merged:

* `InpDemoOnly=true` / `demo_only=True` stays the default; live trading stays off by default.
* The EA stays the only component with order-execution authority; the AI may only block.
* AI failure (timeout, invalid data, outage) must block new entries and must never disable exits.
* Lot sizes must never be rounded up past the risk budget, and recovery must stay limited
  (no unlimited martingale).
* Do not claim profitability or AI improvement without out-of-sample results on real broker data,
  and label synthetic results as synthetic.

## Reporting problems

Open a GitHub issue with steps to reproduce, the relevant log lines (with secrets removed) and
whether you were on a demo or live account.
