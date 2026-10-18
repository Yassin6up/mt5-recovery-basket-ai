# Research and validation

> **No real-market backtest has been run.** The development environment had no broker and no
> tick data (see ENVIRONMENT_INSPECTION.md). Every result below comes from **synthetic** ticks
> and only demonstrates that the machinery works. None of it is evidence that the strategy, or
> the AI filter, makes money.

## 1. Methodology (implemented)

| Requirement | Implementation |
|---|---|
| Bid/ask tick data | `research/data.py` loads `time_msc_utc,bid,ask` CSV (from `LRB_ExportTicks.mq5`) or the MT5 tick-export format with a server→UTC offset. |
| Commissions, swaps, variable spread, slippage | `research/simbroker.py`: per-side commission debited per deal; swap at the 00:00 UTC rollover (triple on Wednesday); the spread is whatever each tick carries; random **adverse** slippage capped by the order deviation. |
| Margin and liquidation | Margin per lot (broker value or contract × price / leverage), conservative hedge treatment, broker **stop-out** that closes the worst position while margin level ≤ stop-out level. |
| Correct intrabar ordering | Every tick is replayed in time order. Legs are never inferred from OHLC bars. |
| No look-ahead | Signals and features use **closed bars only**. Test `test_no_lookahead_future_changes_do_not_alter_past` changes all prices after a cut-off and asserts that every basket closed before the cut-off is identical. |
| Chronological splits and walk-forward | `research/walkforward.py`: expanding train → validation (Platt calibration only) → test, rolled over *n* folds, plus a **final holdout touched once**. |
| Label leakage | `labels.py` records each label's horizon (basket start → close), and `LabelSet.select` **purges** samples whose outcome crosses a split boundary (tested). |
| No fitting on the final test period | The holdout is excluded from training, calibration and the parameter defaults. The defaults were set by hand **before** any run and were never tuned on results. |
| Metrics | Net return after costs, maximum equity drawdown (tick-sampled while a basket is open), worst basket floating loss, number of baskets, win rate, expectancy with a **bootstrap 95% CI**, average win/loss, best/worst basket, profit factor, maximum gross lots, maximum margin utilization, close-reason and legs histograms, stop-outs. |
| A vs B | The same ticks, broker model and configuration, with `ai_mode=OFF` vs `FILTER`, on every test segment. |

### The AI model and its outcome definition

* Outcome: a basket opened at the close of bar *t* in the baseline direction ends with **realized
  P&L ≤ 0**, including commission, swap and slippage. Horizon: until the basket closes, bounded by
  `max_basket_duration_sec` (48 h).
* Features (13, `ai/features.py`): returns over 1/4/16 bars normalized by ATR, EMA gap and slope /
  ATR, ATR(14)/ATR(100), realized-vol ratio, position in the 48-bar range, spread/ATR, hour of day
  (sin/cos), D/ATR, and account drawdown. Direction-dependent features are signed.
* Model: L2 logistic regression (numpy, deterministic), Platt-calibrated on the validation segment.
  The decision rule is ALLOW iff `(1 − p)·avg_win + p·avg_loss > 0`, using training-period averages.
* The service reports `p_basket_loss` **only** for this evaluated model, together with its outcome,
  horizon and calibration method. `rules-v1` never reports probabilities. No LLM is used.

## 2. Test inventory (`cd python && uv run pytest` → 86 passed)

| File | What is covered |
|---|---|
| `test_sizing.py` (11) | Volume flooring never rounds up; BUY/SELL leg sides; anchors; distance rules; worst case = 2D/3D/6D for `[1,2,3]`; budget respected; minimum volume → reject; exposure caps; custom progression used verbatim; config validation. |
| `test_accounting.py` (3) | Floating/swap/realized/commission separation, duplicate deal rows ignored, foreign deals ignored, close-cost estimate, final realized P&L. |
| `test_state_machine.py` (17) | BUY progression with exact bid/ask trigger prices; SELL mirror; no duplicate legs on boundary chatter; one basket per duplicated signal; prolonged oscillation ≤ 3 positions; target closes winners **and** losers; trend → price stop within budget; gap beyond trigger → stop adding; gap through stop → loss > budget (documented); spread spike blocks entry and defers leg; insufficient margin; max duration; partial fill; send timeout reconciled without double send; rejected leg 1 aborts the basket (no stuck OPENING state); rejected later leg retried then abandoned; manual and foreign positions untouched. |
| `test_resilience.py` (21) | Failed closes retried until flat, with realized ≠ trigger; `CLOSING` blocks entries; restart with persisted state continues the progression; **restart with an empty DB adopts positions and adds nothing**; closed-while-offline; external close → emergency close; unexpected position; AI outage blocks entries while the basket is still managed; AI BLOCK/WAIT/expired/stale/direction mismatch; AI-blocked leg leaves the basket exposed with exits; daily-loss halt and next-day clear (manual trade untouched); drawdown halt needs manual reset; kill switch HALT vs FLATTEN; demo-only and hedging-only; news fail-safe; stale price; broker stop-out detection. |
| `test_service.py` (27) | Signed responses; bad signature / key / skew / secret; tampered body; idempotent replay and 409 on reuse; schema rejection (ask<bid, unknown fields, symbol, timeframe, leg index); unclosed bar rejected; stale bars → WAIT; insufficient bars → WAIT; recovery-leg semantics; probability metadata only for the logistic model; telemetry + dashboard; dashboard token; remote bind without TLS refused; migrations and online backup; log redaction; **EA wire-format contract tests** (request built like `WriteDecisionRequest()`, status fixture like `WriteStatus()`); RFC 4231 HMAC vector used by `HmacSelfTest()`. |
| `test_research.py` (7) | CSV round trip; MT5 export format with UTC offset; **no-look-ahead**; equity = Σ realized basket P&L; label purging; stress invariants; walk-forward plumbing and split ordering. |

Also verified once by hand: `lrb-service` started under uvicorn on loopback, `lrb.tools.bridge_emulator`
received a decision whose response signature verified, and the secret did not appear in the logs.

## 3. Stress scenarios (`uv run lrb-backtest stress`, scripted paths through the full engine)

```
scenario                     baskets  realized     worst maxpos open >risk  reasons
sideways_oscillation               0      0.00      0.00      3    3 False
strong_trend_down                  1     50.85     50.85      2    0 False  PROFIT_TARGET
strong_trend_after_3_legs          1    -77.39    -77.39      3    0 False  PRICE_STOP
sudden_reversal                    0      0.00      0.00      2    2 False
gap_through_stop                   1   -206.79   -206.79      2    0  True  BASKET_LOSS_LIMIT
spread_spike_at_signal             0      0.00      0.00      0    0 False
insufficient_margin                0      0.00      0.00      0    0 False
ai_outage_mid_basket               1    -80.33    -80.33      3    0 False  PRICE_STOP
vps_restart_mid_basket             1     51.67     51.67      3    0 False  PROFIT_TARGET
duplicate_signals                  1     50.55     50.55      1    0 False  PROFIT_TARGET
partial_fill_leg2                  1    -13.63    -13.63      2    0 False  PRICE_STOP
failed_basket_close                1     58.95     58.95      1    0 False  PROFIT_TARGET
```

How to read this:

* **sideways_oscillation**: price oscillates across both boundaries. All 3 legs fill, adding
  stops, and the basket is still open at the end of the path with 0.36 gross lots, neither at
  target nor at a stop. Only the duration limit would end it.
* **gap_through_stop**: a weekend gap jumps far past the active stop. The realized loss is
  **−206.79 against a 100 risk budget**. The software stop does not cap gap losses.
* **ai_outage_mid_basket**: the AI goes down after leg 1. The basket keeps its legs and exits
  (it is closed by the price stop), and the later signal opens **no** new basket.
* **vps_restart_mid_basket**: the engine is rebuilt from persisted state three times mid-basket,
  without duplicating positions.
* **failed_basket_close**: 25 injected close rejections. The engine keeps retrying, ends flat, and
  realized P&L differs from the trigger value.

## 4. Synthetic walk-forward: A (no AI) vs B (AI filter)

Data: 150 days of synthetic ticks (1.56 M ticks, `SynthParams(days=150, seed=1)`), 3 folds plus a
20% holdout, labels every ≥ 2 h. Command:
`lrb-backtest walkforward --ticks ../data/synth_ticks.csv --out ../reports/walkforward_synthetic.json`
(about 25 min on 1 CPU).

| Segment | Labels train/val/test | Brier (model) | Brier (base rate) | A: return / maxDD / baskets / expectancy [95% CI] | B |
|---|---|---|---|---|---|
| fold1 | 153 / 127 / 147 | 0.1105 | 0.0916 | +6.71% / 0.72% / 18 / +37.30 [28.19, 46.28] | identical |
| fold2 | 281 / 147 / 143 | 0.0388 | 0.0337 | +7.40% / 1.12% / 21 / +35.23 [18.65, 47.88] | identical |
| fold3 | 439 / 143 / 162 | 0.0874 | 0.0840 | +10.21% / 0.67% / 24 / +42.53 [34.14, 49.64] | identical |
| holdout | 594 / 162 / 212 | 0.0324 | 0.0275 | +14.09% / 0.76% / 31 / +45.46 [38.63, 51.22] | identical |

**Findings:**

1. **The AI filter adds nothing measurable here.** In every segment the calibrated model's Brier
   score is *worse* than always predicting the base rate, so it has no skill. Its predicted loss
   probabilities are small, so the EV rule allowed every basket, and B equals A exactly. No claim
   that AI improves performance is supported.
2. **The positive A results are an artifact of the synthetic generator.** Its trend regimes
   persist for 3–18 h, and the EMA-trend baseline is built to ride exactly that. As a control,
   the same engine on **driftless** synthetic ticks (trend regimes switched off; 90 days, two seeds;
   `reports/driftless_synthetic.txt`) gives:

   | Seed | Return | Max DD | Baskets | Win rate | Expectancy [95% CI] | Worst basket |
   |---|---|---|---|---|---|---|
   | 11 | −0.70% | 1.82% | 41 | 56.1% | −1.71 [−13.38, +8.39] | −94.87 |
   | 12 | −1.77% | 2.87% | 40 | 40.0% | −4.42 [−14.60, +3.62] | **−135.88** (budget 100: a jump exceeded the stop) |

   On a price process with no exploitable structure, the basket loses about its trading costs. That
   is what theory predicts for any recovery scheme, and it is the honest prior for real markets
   until real data says otherwise.
3. The headline win rates (86–100%) come with small sample sizes and wide confidence intervals.
   Most baskets ended by `MAX_DURATION` or `PROFIT_TARGET`, and win rate alone says nothing about
   profitability.

## 5. What must happen before any conclusion

1. Export at least 12 months of real broker bid/ask ticks (`LRB_ExportTicks`), plus the real
   symbol specification (`LRB_InspectEnvironment` → `spec_from_inspect`). Set the commission to
   the broker's actual model.
2. Run `lrb-backtest run` and `walkforward` on those ticks. Report every segment, including bad ones.
3. Only enable `LRB_MODEL_PATH` if the model **beats the base-rate Brier score and improves B over A
   on the holdout** with non-overlapping uncertainty. Otherwise keep `rules-v1` or `AI_OFF`.
4. Compile the EA, run it in the MT5 Strategy Tester ("Every tick based on real ticks",
   `InpAIMode=AI_OFF`) and compare its basket list with the Python backtest on the same period.
   That checks parity of the port.
5. Run on a demo account for weeks, and compare `baskets.csv` (realized) against the expected costs.
