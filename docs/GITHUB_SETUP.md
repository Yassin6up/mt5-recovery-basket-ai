# GitHub repository setup (for the owner)

These settings can only be changed on github.com by the repository owner.

1. **Make it public:** Settings → General → Danger Zone → *Change repository visibility* → Public.
2. **Default branch:** Settings → General → Default branch → `main`. Then delete old working branches if you like.
3. **About box (top right of the repo page, gear icon)** — this is what search engines and GitHub search index first:
   - *Description:* `Open-source MetaTrader 5 (MQL5) Expert Advisor: limited hedging recovery basket with a Python FastAPI AI filter, tick backtester, walk-forward validation and VPS deployment. Demo-only by default.`
   - *Topics:* `metatrader5` `mt5` `mql5` `expert-advisor` `forex` `trading-bot` `algorithmic-trading` `hedging` `risk-management` `backtesting` `walk-forward` `fastapi` `python` `machine-learning` `quantitative-finance` `open-source`
   - Tick *Releases* and untick *Packages* / *Environments* if unused.
4. **Social preview image:** Settings → General → Social preview → upload a 1280×640 image (shown when the link is shared).
5. **Protect `main`:** Settings → Branches → Add rule for `main`: require a pull request, require the `tests` status check, block force pushes.
6. **Community features:** Settings → General → Features → enable *Issues* and *Discussions*.
   Settings → Code security → enable *Private vulnerability reporting* (used by SECURITY.md).
7. **Release:** create a release `v0.1.0` (Releases → Draft a new release) so people can download a fixed version.
8. **Reach people:** share the repo link on MQL5/forex/algo-trading communities (r/algotrading, MQL5 forum,
   Hacker News "Show HN", dev.to). Be clear it is demo/research software with no profit guarantee.
