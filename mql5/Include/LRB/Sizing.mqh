//+------------------------------------------------------------------+
//| Boundary distance and base-lot sizing.                           |
//| Port of python/src/lrb/core/sizing.py - keep both in sync.       |
//| See docs/RISK_AND_SIZING.md for the derivation and limitations.  |
//+------------------------------------------------------------------+
#ifndef LRB_SIZING_MQH
#define LRB_SIZING_MQH

#include "Defines.mqh"

struct SizingParams
  {
   double mult[LRB_MAX_LEGS];
   int    n_legs;
   double atr_mult;
   double min_distance_points;
   double max_distance_points;
   double min_distance_spread_mult;
   double stop_distance_mult;
   double risk_pct;
   double sizing_safety;
   double commission_per_lot_side;
   double slippage_allowance_points;
   double spread_stress_mult;
   double max_gross_lots;
   double max_net_lots;
  };

struct SymbolSpecRec
  {
   double point;
   double tick_size;
   double tick_value_loss;
   double volume_min;
   double volume_max;
   double volume_step;
  };

struct SizingResult
  {
   bool   ok;
   string reason;
   double base_lot;
   double volumes[LRB_MAX_LEGS];
   double risk_budget;
   double usable_budget;
   double distance;
   double stop_distance;
   double worst_loss_per_lot;
   double planned_max_loss;
   double raw_lot;
   string explain;
  };

double FloorVolume(double v, const SymbolSpecRec &s)
  {
   if(v <= 0 || !MathIsValidNumber(v)) return 0.0;
   v = MathMin(v, s.volume_max);
   double steps = MathFloor(v / s.volume_step + 1e-9);
   double r = NormalizeDouble(steps * s.volume_step, 8);
   return (r + 1e-12 >= s.volume_min) ? r : 0.0;
  }

int LegSide(int direction, int idx0) { return (idx0 % 2 == 0) ? direction : -direction; }

void LevelsFromAnchor(int direction, double anchor, double distance, double stop_mult, Levels &lv)
  {
   if(direction > 0) { lv.upper = anchor; lv.lower = anchor - distance; }
   else              { lv.lower = anchor; lv.upper = anchor + distance; }
   lv.distance = distance;
   lv.stop_distance = stop_mult * distance;
   lv.stop_low  = lv.lower - lv.stop_distance;
   lv.stop_high = lv.upper + lv.stop_distance;
  }

// Returns D (price units); sets reason to "" if acceptable.
double BoundaryDistance(double atr, double spread, const SizingParams &p, const SymbolSpecRec &s, string &reason)
  {
   reason = "";
   if(!MathIsValidNumber(atr) || atr <= 0) { reason = "ATR_UNAVAILABLE"; return 0.0; }
   double d = MathMax(p.atr_mult * atr, MathMax(p.min_distance_points * s.point, p.min_distance_spread_mult * spread));
   if(d > p.max_distance_points * s.point) reason = "DISTANCE_TOO_WIDE";
   return d;
  }

// Worst-case loss per 1.0 base lot over every reachable leg state, at the ACTIVE stop.
double WorstLossPerLot(int direction, double distance, double spread, const SizingParams &p,
                       const SymbolSpecRec &s, string &explain, double &loss_price_units[], int &stop_sides)
  {
   Levels lv;
   LevelsFromAnchor(direction, direction > 0 ? distance : 0.0, distance, p.stop_distance_mult, lv);
   double slip = p.slippage_allowance_points * s.point;
   double spread_x = MathMax(spread, s.point) * p.spread_stress_mult;
   double v = s.tick_value_loss / s.tick_size;
   double entries[LRB_MAX_LEGS];
   int sides[LRB_MAX_LEGS];
   for(int i = 0; i < p.n_legs; i++)
     {
      sides[i] = LegSide(direction, i);
      double ls = (i == 0) ? 0.0 : slip;
      entries[i] = sides[i] > 0 ? lv.upper + ls : lv.lower - ls;
     }
   double worst = 0.0;
   ArrayResize(loss_price_units, 0);
   stop_sides = 0;
   explain = "";
   for(int k = 1; k <= p.n_legs; k++)
     {
      double net = 0, mult_sum = 0;
      for(int i = 0; i < k; i++) { net += p.mult[i] * sides[i]; mult_sum += p.mult[i]; }
      for(int ss = 0; ss < 2; ss++)
        {
         bool low = (ss == 0);
         if(low && net < 0) continue;
         if(!low && net > 0) continue;
         double bid, ask;
         if(low) { bid = lv.stop_low; ask = bid + spread_x; }
         else    { ask = lv.stop_high; bid = ask - spread_x; }
         double pnl = 0;
         for(int i = 0; i < k; i++)
           {
            if(sides[i] > 0) pnl += p.mult[i] * ((bid - slip) - entries[i]);
            else             pnl += p.mult[i] * (entries[i] - (ask + slip));
           }
         double loss_price = -pnl;
         double money = loss_price * v + 2.0 * p.commission_per_lot_side * mult_sum;
         int n = ArraySize(loss_price_units);
         ArrayResize(loss_price_units, n + 1);
         loss_price_units[n] = loss_price;
         stop_sides++;
         explain += StringFormat("k=%d stop=%s net=%+.2f loss/lot=%.2f; ", k, low ? "LOW" : "HIGH", net, money);
         worst = MathMax(worst, money);
        }
     }
   return worst;
  }

void ComputeBaseLot(int direction, double equity, double atr, double spread, double risk_room,
                    const SizingParams &p, const SymbolSpecRec &s, SizingResult &r)
  {
   r.ok = false; r.reason = ""; r.base_lot = 0; r.planned_max_loss = 0; r.raw_lot = 0; r.explain = "";
   for(int i = 0; i < LRB_MAX_LEGS; i++) r.volumes[i] = 0;
   string why;
   r.distance = BoundaryDistance(atr, spread, p, s, why);
   r.stop_distance = r.distance * p.stop_distance_mult;
   if(why != "") { r.reason = why; return; }
   r.risk_budget = MathMin(p.risk_pct / 100.0 * equity, risk_room);
   if(r.risk_budget <= 0) { r.reason = "NO_RISK_BUDGET"; return; }
   r.usable_budget = r.risk_budget * (1.0 - p.sizing_safety);
   double lpu[];
   int nsc;
   string ex;
   r.worst_loss_per_lot = WorstLossPerLot(direction, r.distance, spread, p, s, ex, lpu, nsc);
   if(r.worst_loss_per_lot <= 0) { r.reason = "SIZING_DEGENERATE"; return; }
   r.raw_lot = r.usable_budget / r.worst_loss_per_lot;
   double msum = 0, max_net = 0, net = 0;
   for(int i = 0; i < p.n_legs; i++)
     {
      msum += p.mult[i];
      net += p.mult[i] * LegSide(direction, i);
      max_net = MathMax(max_net, MathAbs(net));
     }
   double q = r.raw_lot;
   q = MathMin(q, p.max_gross_lots / msum);
   if(max_net > 0) q = MathMin(q, p.max_net_lots / max_net);
   q = FloorVolume(q, s);
   if(q <= 0) { r.reason = "MIN_VOLUME_EXCEEDS_RISK_BUDGET"; r.explain = ex; return; }
   for(int i = 0; i < p.n_legs; i++)
     {
      r.volumes[i] = FloorVolume(q * p.mult[i], s);
      if(r.volumes[i] <= 0) { r.reason = "LEG_VOLUME_BELOW_MIN"; return; }
     }
   // planned max loss with floored volumes (flooring only reduces risk)
   double v = s.tick_value_loss / s.tick_size;
   double planned = 0;
   for(int j = 0; j < ArraySize(lpu); j++) planned = MathMax(planned, lpu[j] * v * q);
   double comm_all = 0;
   for(int i = 0; i < p.n_legs; i++) comm_all += 2.0 * p.commission_per_lot_side * r.volumes[i];
   r.planned_max_loss = planned + comm_all;   // conservative: all legs' commission
   r.base_lot = q;
   r.explain = StringFormat("budget=%.2f usable=%.2f D=%.6f worst/lot=%.2f raw=%.4f q=%.2f planned=%.2f | %s",
                            r.risk_budget, r.usable_budget, r.distance, r.worst_loss_per_lot, r.raw_lot, q,
                            r.planned_max_loss, ex);
   if(r.planned_max_loss > r.risk_budget + 1e-9) { r.reason = "PLANNED_LOSS_EXCEEDS_BUDGET"; return; }
   r.ok = true;
  }

#endif
