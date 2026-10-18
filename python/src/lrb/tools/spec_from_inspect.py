"""Convert LRB_InspectEnvironment.mq5 output to a SymbolSpec JSON for the backtester.

    python -m lrb.tools.spec_from_inspect inspect_EURUSD.json symbol_spec.json
"""

from __future__ import annotations

import json
import sys


def convert(d: dict) -> dict:
    s = d["symbol"]
    return {
        "name": s["name"], "digits": int(s["digits"]), "point": s["point"], "contract_size": s["contract_size"],
        "tick_size": s["tick_size"], "tick_value_loss": s["tick_value_loss"] or s["tick_value"],
        "tick_value_profit": s["tick_value_profit"] or s["tick_value"], "volume_min": s["volume_min"],
        "volume_max": s["volume_max"], "volume_step": s["volume_step"],
        "margin_initial_per_lot": s.get("margin_for_1_lot_buy", 0.0), "hedged_margin_ratio": 1.0,
    }


def main() -> int:
    src, dst = sys.argv[1], sys.argv[2]
    with open(src) as f:
        spec = convert(json.load(f))
    with open(dst, "w") as f:
        json.dump(spec, f, indent=2)
    print(json.dumps(spec, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
