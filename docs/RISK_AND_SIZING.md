# Risk engine and position sizing

All defaults are **conservative research parameters for demo trading, not validated
recommendations.** The risk engine runs entirely inside the EA and keeps working when Python,
the bridge or the network is unavailable.

## 1. Defaults

| Control | Default | Where |
|---|---|---|
| Max basket risk | 1% of equity at basket start (capped by remaining daily/drawdown room) | `risk_pct` / `InpRiskPct` |
| Basket profit target | 0.5% of equity at basket start | `target_pct` |
| Max positions per basket | 3 (`[1,2,3]`) | `max_positions` |
| Daily loss limit | 2% of start-of-day equity, **including floating** | `daily_loss_pct` |
| Account equity drawdown limit | 6% from the high-water mark; halts until a manual reset | `max_drawdown_pct` |
| Max basket duration | 48 h | `max_basket_duration_sec` |
| Max gross volume / net exposure | 1.0 / 0.5 lots | `max_gross_lots`, `max_net_lots` |
| Projected margin level before any leg | ≥ 500% | `min_margin_level_pct` |
| Free margin after a leg | ≥ 50% of equity | `min_free_margin_after_pct` |
| Emergency close | margin level < 200% | `emergency_margin_level_pct` |
| Risk after losses | **never increased**: the risk is a % of current equity and is further capped by the remaining daily/drawdown room | — |
| Recovery | **limited** to the configured legs; no unlimited martingale | — |

## 2. Base-lot sizing

Inputs: equity at start, ATR, current spread, broker symbol specification (tick size, *loss* tick
value, volume min/max/step), progression, distances, cost estimates and exposure caps.

1. `D` comes from the boundary rule. `Dstop = stop_distance_mult × D`.
2. For **every reachable state** k = 1..n (k legs filled, later legs possibly blocked), compute the
   loss per 1.0 base lot when price reaches the **active** stop. The active stop is `stop_low` when
   net long, `stop_high` when net short, and both when flat. The calculation includes:
   * leg 1 filled at the anchor and legs ≥ 2 filled `slippage_allowance_points` worse than their boundary;
   * an exit spread of `spread_stress_mult × current spread`;
   * adverse exit slippage of `slippage_allowance_points` on every position;
   * round-turn commission `2 × commission_per_lot_side × Σ multipliers`.
3. `W = max over states` (account currency per 1.0 base lot), using `tick_value_loss / tick_size`.
4. `usable = risk_budget × (1 − sizing_safety)`, where `risk_budget = min(risk_pct × equity, risk room)`.
5. `q_raw = usable / W`, then `q = floor_to_step(min(q_raw, max_gross / Σm, max_net / max|net_k|))`.
6. **If `q` < the broker minimum volume, the basket is rejected** (`MIN_VOLUME_EXCEEDS_RISK_BUDGET`).
   Volume is never rounded up. The same applies if any leg volume floors to zero.
7. `planned_max_loss = max price loss × q + round-turn commission of all legs`. This must be
   ≤ `risk_budget`.
8. Before the basket opens, the margin of **all planned legs is summed** (no hedge reduction is
   assumed) and the projected margin level must pass. Each leg is re-checked just before it is sent.

### Worked example (output of `compute_base_lot`, EURUSD-like specification)

Equity 10,000 USD; ATR 0.0020; spread 0.00010; tick value 1 USD per 0.00001 per lot; min/step 0.01;
commission 3.5 per lot per side; slippage allowance 5 points; spread stress ×2.

```
risk_budget=100.00 usable(after safety)=90.00
D=0.002000 Dstop=0.002000
  state k=1 stop=LOW  net=+1 loss/lot=412.00     (≈ 2D + costs)
  state k=2 stop=HIGH net=-1 loss/lot=666.00     (≈ 3D + costs)
  state k=3 stop=LOW  net=+2 loss/lot=1337.00    (≈ 6D + costs)  <- worst
raw lot=0.0673 caps: gross 0.1667, net 0.25 -> base lot=0.06
leg volumes=[0.06, 0.12, 0.18] planned max loss=80.22
```

With 1,000 USD equity the budget is 10 USD, the raw lot is 0.0067 < 0.01, and the basket is
**rejected** rather than rounded up.

### Limitations

* This is a **planned** loss for a **software** stop. Gaps (weekend or news), slippage beyond the
  allowance, requotes, rejected closes, terminal or VPS outages and broker stop-outs can all make
  the realized loss **larger**. The stress test `gap_through_stop` shows this deliberately.
* Tick value is read at the time of sizing. Currency conversion drift during the basket is not modelled.
* The margin check assumes the broker's `OrderCalcMargin` is representative. Hedged-margin
  reductions are ignored on purpose (conservative).
* Commission is an estimate. Realized P&L uses broker deal values.

## 3. Basket accounting (no double counting)

| Component | Source | Notes |
|---|---|---|
| floating | `POSITION_PROFIT` of open basket positions | MT5 marks longs at the bid and shorts at the ask, so the **closing spread is already included**. No separate spread cost is added. |
| open swap | `POSITION_SWAP` of open positions | Moves to "realized" when the position closes. |
| realized | `DEAL_PROFIT + DEAL_SWAP` of OUT deals of basket positions | Partial and complete closes. |
| commissions | `DEAL_COMMISSION + DEAL_FEE` of **all** basket deals (IN and OUT) | Charged once, per deal. |
| est. closing costs | remaining lots × exit commission + slippage allowance | Covers only what has **not** been charged yet. |

`net = floating + open_swap + realized + commissions − est_close_costs`

Deals are matched by `DEAL_POSITION_ID ∈ basket position ids`, and duplicate deal rows are ignored
(tested). Final basket P&L is the sum of profit, swap, commission and fee over all basket deals.

## 4. Guards and filters

| Guard | Behaviour |
|---|---|
| Daily loss | Closes the basket and halts entries until the next UTC trading day (`day_rollover_utc_hour`). |
| Drawdown | Closes the basket and halts until an operator sets `InpResetDrawdownHalt=true` once (or calls `Engine.reset_drawdown_halt`). |
| Kill switch | `HALT`: no new baskets or legs, exits stay active. `FLATTEN`: close everything and halt. Sources: input, terminal global variable `LRB_KILL_<magic>`, or file `<Common>\Files\LRB\<magic>\KILL`. |
| Spread | Entry: `max_spread_points`. Legs: `max_leg_spread_points` (deferred, see the strategy spec). |
| Stale price | Tick older than `max_tick_age_sec`: no entry, and legs wait. |
| Market hours | Broker trade sessions (`SymbolInfoSessionTrade`), and no new baskets within `InpAvoidMinBeforeSessionEnd` minutes of the session end. |
| Volatility | `D` must lie within [min, max] distance. If ATR is unavailable, the entry is rejected. |
| Execution | More than `max_exec_errors_per_hour` errors blocks new baskets. |
| News (optional) | Uses the MT5 economic calendar for both currencies of the symbol. HIGH-impact events within ±window block entries. **Missing or failed calendar data blocks entries (fail-safe).** Unverified on a live terminal. |
| Account | Demo-only by default. A hedging margin mode is required. Algo trading must be enabled. The symbol must be in full trade mode. |

## 5. Optional broker-side catastrophic stop

`InpCatastrophicSL=true` sets a per-position SL at `stop_low − extra` (BUY legs) or
`stop_high + extra` (SELL legs), with `extra = InpCatastrophicExtraMult × Dstop`. This sits beyond
the software stop, so it should only trigger if the EA or terminal is down.

**Implications:** a broker SL closes **one leg**, not the basket. The remaining legs become an
unhedged exposure until the EA runs again. When it does, it detects `EMERGENCY_EXTERNAL_CLOSE` and
closes the rest. With hedged legs on opposite sides, one leg's SL can make the remaining exposure
*larger* in the adverse direction. Because of that trade-off it is **off by default**. Read this
section before enabling it.
