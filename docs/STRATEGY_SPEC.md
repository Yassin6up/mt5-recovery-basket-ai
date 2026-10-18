# Strategy specification — Limited Recovery Basket

> Research software for **demo trading**. The strategy is a *limited* recovery/hedge basket. It is
> not a proven edge and **can lose money**. A high basket win rate does not mean it is profitable.

Reference implementation: `python/src/lrb/core/engine.py` (with tests). Production port:
`mql5/Experts/LRB/LRB_EA.mq5`. When the two differ, the Python tests define the intended behaviour.

## 1. Vocabulary

| Term | Definition |
|---|---|
| Basket | All positions with this EA's **magic number** on the configured **symbol**. At most one basket per symbol and magic number. |
| Leg *k* | The k-th position of a basket (1-based). Volume = `progression[k-1] × base_lot`, floored to the broker volume step. |
| Progression | Configurable multipliers. The default is `[1, 2, 3]`, used exactly as given and never replaced by doubling. `max_positions` (default 3) must be ≤ the length of the progression. |
| `D` | Boundary distance. Fixed when the basket opens. |
| `upper`, `lower` | Boundaries, with `upper − lower = D`. |
| `stop_low`, `stop_high` | Hard price stops: `lower − Dstop` and `upper + Dstop`, where `Dstop = stop_distance_mult × D`. |

## 2. Boundary definition (before the basket opens)

```
D = max(atr_mult × ATR(atr_period, signal TF, Wilder, closed bars),
        min_distance_points × point,
        min_distance_spread_mult × current_spread)
reject the basket if D > max_distance_points × point
```

The boundaries stay fixed for the life of the basket. `FIXED` is the only boundary mode implemented, and
any other value is rejected by config validation.

### Anchors

| Initial direction | Leg 1 executes at | Anchor | Other boundary |
|---|---|---|---|
| BUY | ask | `upper = leg-1 fill price` | `lower = upper − D` |
| SELL | bid | `lower = leg-1 fill price` | `upper = lower + D` |

The anchor is the **actual fill price** reported by the broker, not the requested price.

## 3. Which price triggers which leg

* **BUY legs** execute at the **ask** and trigger on the **ask** reaching `upper`.
* **SELL legs** execute at the **bid** and trigger on the **bid** reaching `lower`.

Initial BUY with the default `[1,2,3]`:

| Leg | Side | Volume | Trigger |
|---|---|---|---|
| 1 | BUY | 1q | baseline signal (section 6), executed at the ask (this defines `upper`) |
| 2 | SELL | 2q | bid crosses down to `lower` (`bid ≤ lower`) |
| 3 | BUY | 3q | ask crosses up to `upper` (`ask ≥ upper`) |
| — | — | — | no more legs (`MAX_LEGS_REACHED`) |

Initial SELL is the exact mirror: SELL 1q at the bid (this defines `lower`), then BUY 2q when the ask
reaches `upper`, then SELL 3q when the bid reaches `lower`.

### Crossing detection (no duplicate or repeated triggers)

Each pending trigger has an **armed** flag:

1. When a leg fills, the next trigger is armed only if the current quote is strictly on the near side
   of it. For the next BUY trigger that means `ask < upper`; for the next SELL trigger, `bid > lower`.
2. An unarmed trigger becomes armed the first time the quote is strictly on the near side.
3. A trigger fires only when it is armed and the quote is at or through the boundary.
4. Firing disarms it. The next leg's trigger is re-evaluated by rule 1.

As a result, chatter around a boundary cannot add a second leg, because a leg index can only be
filled once. A quote that is already beyond a boundary when a leg fills cannot fire the next leg
until price comes back and crosses again.

### Unique identifiers and execution acknowledgement

* Basket ID: `<magic mod 10000><hex UTC seconds>`. Leg request ID: `<basket>:L<k>:<attempt>`.
  Order comment: `LRB|<basket tail>|L<k>`. Comments are informational only, because some brokers
  rewrite them. Ownership is decided by **magic number + symbol**.
* Each leg is a small state machine: `PLANNED → SENT → FILLED | PARTIAL | FAILED | SKIPPED`.
* The state is persisted as `SENT` **before** the order is sent, so a restart cannot double-send.
* While any leg is `SENT`, no other leg can be sent.
* If the outcome is unknown (timeout, `PLACED`, or the position is not visible yet), the leg stays
  `SENT`. Reconciliation then attributes the first new position on the same side opened after the
  send time. After `pending_ack_timeout_sec` with nothing found, the leg becomes `FAILED` and adding
  stops.
* A rejected send is retried at most `leg_max_attempts` times, only while the trigger is still valid.
  After that, leg 1 aborts the basket and legs ≥ 2 stop adding.

## 4. Per-tick evaluation order

Every tick, plus a 500 ms timer in the EA, is processed in this order:

1. Account guards: day rollover (UTC), equity high-water mark, daily-loss and drawdown halts.
2. Startup reconciliation (once). No entries are allowed until it has completed.
3. Position reconciliation: acknowledge pending legs, and detect external closes or unknown positions.
4. **Deterministic exits** (section 5).
5. If `CLOSING`, close every remaining basket position, retrying until none are left.
6. If `ACTIVE`, run recovery-leg trigger detection, then filters, then send.
7. If `FLAT`, run the baseline signal, then filters, sizing, the margin check, the optional AI check,
   and finally send leg 1.

Exits are evaluated before any new exposure. **The EA never waits for the AI**: AI responses are
read from files that a separate bridge EA writes on another chart.

## 5. Exits — the whole basket closes together

These are checked in order. The first match starts `CLOSING`, and every basket position is closed,
winners and losers alike.

| # | Reason | Condition |
|---|---|---|
| 1 | `KILL_SWITCH` | kill mode `FLATTEN` |
| 2 | `DAILY_LOSS_LIMIT` | equity ≤ start-of-day equity × (1 − daily_loss_pct) |
| 3 | `ACCOUNT_DRAWDOWN_LIMIT` | equity ≤ peak equity × (1 − max_drawdown_pct) |
| 4 | `BASKET_LOSS_LIMIT` | net basket P&L ≤ −risk_money |
| 5 | `PRICE_STOP` | net long and bid ≤ `stop_low`, or net short and ask ≥ `stop_high`; when flat, both apply |
| 6 | `EMERGENCY_MARGIN` | margin level < emergency_margin_level_pct |
| 7 | `MAX_DURATION` | basket age ≥ max_basket_duration |
| 8 | `PROFIT_TARGET` | net basket P&L ≥ target_money |
| — | `EMERGENCY_EXTERNAL_CLOSE` | a basket position disappeared without our close (broker stop-out, manual close, broker-side SL) |
| — | `EMERGENCY_UNEXPECTED_POSITION` | a position with our magic and symbol cannot be attributed to a pending leg |

Net basket P&L is defined in `RISK_AND_SIZING.md` §3.

### Closing is not atomic

* While `CLOSING`, no entries or legs are allowed, and the kill switch cannot be cleared into a new basket.
* Every remaining position gets a close request, retried every `close_retry_sec` until none remain.
  This handles requotes, partial closes, closed markets and disconnections, and it continues after a
  restart because `CLOSING` is persisted.
* The final report uses **realized P&L from broker deal history**. This can differ from the trigger
  value (slippage, gaps, and partial closes at different prices), and both numbers are logged.

## 6. Baseline entry signal (RESEARCH BASELINE, not a proven edge)

This is evaluated once per **closed** bar of `signal_timeframe_sec` (default M15, bid bars), using
the most recent 300 closed bars:

```
f = EMA(close, 20)  (seeded with the SMA of the first 20 values in the window)
s = EMA(close, 50)
a = Wilder ATR(14)
BUY  if f − s ≥ 0.10·a
SELL if s − f ≥ 0.10·a
```

A signal is valid only until the next bar closes. The rule is reproducible, and it exists so that
the AI filter has something well-defined to filter.

## 7. Recovery leg blocked or skipped — the resulting state

A leg can be blocked by the AI, the spread limit (deferred), a gap beyond the trigger, margin,
exposure caps, send failures or a partial fill. When it is blocked:

* `adding_allowed = false`, `block_reason` is recorded, and all remaining `PLANNED` legs become `SKIPPED`.
* **The basket stays exposed with its current positions.** Every deterministic exit in section 5
  remains active, including the price stop for the current net exposure.
* The engine **never assumes a later leg will rescue the basket.** No further legs are added to
  that basket.

A spread that is too wide only **defers** the leg while price stays within `max_trigger_slippage_points`
of the boundary. Beyond that distance the leg is skipped.

Partial fill: the filled volume is accepted and no top-up is sent. Adding stops, because an
under-hedged state is not covered by the sizing scenarios.

## 8. Restart and reconciliation

* State (basket, legs, levels, guards) is persisted after every change to
  `<Common>\Files\LRB\<magic>\state.txt` (EA) or as JSON (`Engine.to_json`, Python).
* On start the phase is `RECONCILE`. The EA waits for the terminal to be connected and the deal
  history to load, then compares with the **actual broker positions**:
  * Local state present and positions present: continue (pending legs are acknowledged by matching).
  * Local state present and no positions: the basket was closed while offline. The EA finalizes
    from deal history and reports `CLOSED_WHILE_OFFLINE`.
  * **No local state but positions present**: the EA adopts them as an *orphan basket*. Boundaries
    are unknown, so price stops are off, but money, duration, guard and kill exits stay on. No legs
    are added. Risk and target are based on the current balance.
  * No local state and no positions: `FLAT`.
* An empty local database is never taken to mean there are no open positions.

## 9. Ownership boundaries

* Only positions with **this magic number on this symbol** are ever modified or closed. Manual
  trades and other EAs are never touched (covered by tests).
* The EA is the **only** component with execution authority. The Python service and the bridge EA
  contain no order code.
