"""Read-only MT5 environment probe (Windows only; needs the MT5 terminal on the same host).

    python -m lrb.tools.inspect_mt5 --symbol EURUSD --out inspect.json

Uses the official ``MetaTrader5`` package, which publishes win_amd64 wheels only. It
attaches to the already-running, logged-in terminal (no credentials are passed here) and
NEVER sends orders. Also writes a SymbolSpec JSON usable by ``lrb-backtest --symbol-spec``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--out", default="inspect.json")
    ap.add_argument("--spec-out", default="symbol_spec.json")
    ap.add_argument("--terminal-path", help="path to terminal64.exe if several terminals are installed")
    ap.add_argument("--tick-days", type=int, default=30)
    args = ap.parse_args(argv)
    if sys.platform != "win32":
        print("MetaTrader5 Python package is Windows-only. Use mql5/Scripts/LRB/LRB_InspectEnvironment.mq5 instead.")
        return 2
    import MetaTrader5 as mt5  # type: ignore

    ok = mt5.initialize(path=args.terminal_path) if args.terminal_path else mt5.initialize()
    if not ok:
        print("mt5.initialize failed:", mt5.last_error())
        return 1
    try:
        ti, ai = mt5.terminal_info(), mt5.account_info()
        si = mt5.symbol_info(args.symbol)
        if si is None:
            print("symbol not found:", args.symbol)
            return 1
        mt5.symbol_select(args.symbol, True)
        tick = mt5.symbol_info_tick(args.symbol)
        now = datetime.now(timezone.utc)
        ticks = mt5.copy_ticks_range(args.symbol, now - timedelta(days=args.tick_days), now, mt5.COPY_TICKS_INFO)
        margin_1lot = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, args.symbol, 1.0, tick.ask) if tick else None
        report = {
            "generated_utc": now.isoformat(),
            "terminal": {"build": ti.build, "connected": ti.connected, "trade_allowed": ti.trade_allowed,
                         "path": ti.path, "commondata_path": ti.commondata_path},
            "account": {"company": ai.company, "server": ai.server, "currency": ai.currency,
                        "trade_mode": {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(ai.trade_mode, ai.trade_mode),
                        "margin_mode": {0: "RETAIL_NETTING", 1: "EXCHANGE", 2: "RETAIL_HEDGING"}.get(ai.margin_mode, ai.margin_mode),
                        "leverage": ai.leverage, "margin_so_call": ai.margin_so_call, "margin_so_so": ai.margin_so_so,
                        "balance": ai.balance, "equity": ai.equity},
            "symbol": {k: getattr(si, k) for k in (
                "name", "digits", "point", "trade_contract_size", "trade_tick_size", "trade_tick_value",
                "trade_tick_value_profit", "trade_tick_value_loss", "volume_min", "volume_max", "volume_step",
                "margin_initial", "margin_hedged", "trade_calc_mode", "trade_mode", "trade_exemode", "filling_mode",
                "trade_stops_level", "trade_freeze_level", "spread", "spread_float", "swap_long", "swap_short",
                "swap_mode", "swap_rollover3days", "currency_base", "currency_profit", "currency_margin")},
            "margin_for_1_lot_buy": margin_1lot,
            "tick_history": {"days_requested": args.tick_days, "count": 0 if ticks is None else len(ticks),
                             "has_bid_ask": bool(ticks is not None and len(ticks) and "ask" in ticks.dtype.names)},
            "notes": "Trading sessions are not exposed by the Python API; run LRB_InspectEnvironment.mq5 for sessions.",
        }
        spec = {
            "name": si.name, "digits": si.digits, "point": si.point, "contract_size": si.trade_contract_size,
            "tick_size": si.trade_tick_size, "tick_value_loss": si.trade_tick_value_loss or si.trade_tick_value,
            "tick_value_profit": si.trade_tick_value_profit or si.trade_tick_value, "volume_min": si.volume_min,
            "volume_max": si.volume_max, "volume_step": si.volume_step,
            "margin_initial_per_lot": float(margin_1lot or 0.0), "hedged_margin_ratio": 1.0,
        }
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2, default=str)
        with open(args.spec_out, "w") as f:
            json.dump(spec, f, indent=2)
        print(json.dumps(report, indent=2, default=str))
        if report["account"]["margin_mode"] != "RETAIL_HEDGING":
            print("WARNING: account is not a hedging account - the strategy cannot run on it.")
        if report["account"]["trade_mode"] != "DEMO":
            print("WARNING: this is NOT a demo account. Keep DEMO_ONLY enabled.")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
