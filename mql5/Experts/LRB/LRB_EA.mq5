//+------------------------------------------------------------------+
//| LRB_EA.mq5 - Limited Recovery Basket AI (execution owner)        |
//|                                                                  |
//| Research / DEMO software. It does NOT guarantee profit.          |
//| - Single execution owner: only this EA sends orders.             |
//| - Never performs network calls; AI decisions arrive as files     |
//|   written by LRB_AIBridge.mq5 running on ANOTHER chart, so a     |
//|   slow network can never delay local risk checks.                |
//| - All exits are local and deterministic.                         |
//| Port of python/src/lrb/core/engine.py (reference + tests).       |
//+------------------------------------------------------------------+
#property copyright "Limited Recovery Basket AI - research software"
#property version   "0.10"
#property strict
#property description "Alternating hedge basket ([1,2,3] x base lot) closed as a whole on net target or hard risk limits."
#property description "DEMO ONLY by default. Live trading requires three explicit inputs."

#include <Trade\Trade.mqh>
#include <LRB\Defines.mqh>
#include <LRB\Json.mqh>
#include <LRB\Sizing.mqh>

//--- Safety
input group "Safety"
input bool   InpDemoOnly            = true;      // Refuse to trade on non-demo accounts
input bool   InpAllowLive           = false;     // Live trading switch (also needs DemoOnly=false + confirmation)
input string InpLiveConfirm         = "";        // Must equal I_ACCEPT_LIVE_RISK to trade live
input long   InpMagic               = 26101801;  // Magic number (one basket per symbol+magic)
input ENUM_LRB_KILL InpKillOnStart  = KILL_NONE; // Kill switch state at start
//--- Progression
input group "Progression"
input string InpProgression         = "1,2,3";   // Lot multipliers (used verbatim, never auto-doubled)
input int    InpMaxPositions        = 3;         // Max positions per basket (<= progression length)
//--- Boundaries
input group "Boundaries (fixed for the life of a basket)"
input ENUM_TIMEFRAMES InpSignalTF   = PERIOD_M15;// Signal / ATR timeframe
input int    InpAtrPeriod           = 14;        // ATR period (Wilder)
input double InpAtrMult             = 1.0;       // D = max(AtrMult*ATR, MinDistance, SpreadMult*spread)
input double InpMinDistancePts      = 150;       // Minimum boundary distance (points)
input double InpMaxDistancePts      = 1500;      // Maximum boundary distance (points) - reject above
input double InpMinDistSpreadMult   = 4.0;       // D >= mult * current spread
input double InpStopDistMult        = 1.0;       // Hard price stop = boundary -/+ mult*D
//--- Baseline signal
input group "Baseline signal (RESEARCH BASELINE - not a proven edge)"
input int    InpEmaFast             = 20;
input int    InpEmaSlow             = 50;
input double InpMinTrendAtrFrac     = 0.10;      // |EMAf-EMAs| >= frac*ATR
//--- Basket risk
input group "Basket risk (research parameters, not recommendations)"
input double InpRiskPct             = 1.0;       // Max basket risk, % of equity at basket start
input double InpTargetPct           = 0.5;       // Basket net profit target, % of equity at start
input double InpSizingSafety        = 0.10;      // Fraction of risk budget held back
input int    InpMaxBasketHours      = 48;        // Max basket duration
input int    InpCooldownMin         = 15;        // Cooldown after a basket closes
//--- Account guards
input group "Account guards"
input double InpDailyLossPct        = 2.0;       // Daily loss limit, % of start-of-day equity (incl. floating)
input double InpMaxDrawdownPct      = 6.0;       // Equity drawdown limit from high-water mark (manual reset)
input int    InpDayRolloverUtcHour  = 0;         // UTC hour that starts a new trading day
input bool   InpResetDrawdownHalt   = false;     // Set true ONCE after review to clear a drawdown halt
//--- Exposure / margin
input group "Exposure / margin"
input double InpMaxGrossLots        = 1.0;
input double InpMaxNetLots          = 0.5;
input double InpMinMarginLevelPct   = 500;       // Projected margin level required before ANY leg
input double InpEmergencyMarginPct  = 200;       // Close basket below this margin level
input double InpMinFreeMarginAfterPct = 50;      // Free margin after leg >= % of equity
//--- Execution filters
input group "Execution filters"
input double InpMaxSpreadPts        = 25;        // Entry spread limit
input double InpMaxLegSpreadPts     = 40;        // Recovery-leg spread limit (leg deferred while wider)
input int    InpMaxTickAgeSec       = 10;        // Stale price limit
input double InpMaxTriggerSlipPts   = 50;        // Trigger beyond boundary by more => skip leg, stop adding
input int    InpDeviationPts        = 10;        // Max slippage for market orders
input int    InpLegMaxAttempts      = 2;
input int    InpPendingAckSec       = 10;        // Unknown order outcome reconciliation timeout
input int    InpCloseRetrySec       = 1;
input int    InpMaxExecErrorsPerHour= 5;
input int    InpAvoidMinBeforeSessionEnd = 15;   // No new baskets close to session end
input bool   InpNewsFilter          = false;     // Use MT5 economic calendar (fail-safe: block if unavailable)
input int    InpNewsWindowMin       = 30;        // +/- minutes around HIGH impact events
//--- Costs
input group "Cost estimates (broker deals are authoritative)"
input double InpCommissionPerLotSide= 3.5;
input double InpSlippageAllowancePts= 5;
input double InpSpreadStressMult    = 2.0;
//--- AI
input group "AI (via LRB_AIBridge on a separate chart)"
input ENUM_LRB_AIMODE  InpAIMode        = AI_OFF;     // AI_FILTER: a valid ALLOW is required for new baskets
input ENUM_LRB_LEGAPPR InpLegApproval   = LEGAPPR_OFF;// REQUIRED: cached ALLOW required per recovery leg
input int    InpAIResponseTimeoutSec= 8;
input int    InpAIMaxDataAgeSec     = 1200;
input int    InpAIBars              = 150;
//--- Broker-side catastrophic stop
input group "Optional broker-side catastrophic SL (per position)"
input bool   InpCatastrophicSL      = false;     // See docs: closes ONE leg -> EA closes the rest
input double InpCatastrophicExtraMult = 1.0;     // SL = stop boundary -/+ mult*Dstop
//--- Telemetry
input group "Telemetry"
input string InpTerminalId          = "vps1";
input int    InpStatusEverySec      = 5;

//=================================================================== state
struct BasketState
  {
   bool     exists;
   string   basket_id;
   int      direction;
   int      phase;
   LegRecord legs[LRB_MAX_LEGS];
   int      n_legs;
   datetime start_utc;
   datetime start_server;
   double   start_equity;
   double   risk_money;
   double   target_money;
   bool     has_levels;
   Levels   lv;
   bool     adding_allowed;
   string   block_reason;
   bool     armed;
   ulong    pos_ids[];
   string   close_reason;
   double   close_trigger_pnl;
   datetime close_started;
   datetime last_close_attempt;
   int      close_attempts;
   bool     orphan;
   double   max_float_loss;
   double   max_gross;
  };

struct GuardState
  {
   long     day_key;
   double   sod_equity;
   double   peak_equity;
   bool     daily_halt;
   bool     dd_halt;
   int      kill;
   datetime cooldown_until;
   datetime last_signal_bar;
   datetime exec_errors[];
  };

struct PnL
  {
   double floating, open_swap, realized, commissions, est_close, open_volume;
   double Net() const { return floating + open_swap + realized + commissions - est_close; }
  };

CTrade        g_trade;
BasketState   g;
GuardState    gd;
int           g_phase = PHASE_RECONCILE;
SizingParams  g_sp;
SymbolSpecRec g_spec;
string        g_dir;
bool          g_dirty = true;
datetime      g_last_status = 0;
string        g_errors[];
string        g_ai_status = "OFF";
string        g_last_ai = "";
bool          g_trading_permitted = false;
string        g_block_init = "";
// pending AI entry request
string        g_entry_rid = "";
datetime      g_entry_deadline = 0;
datetime      g_entry_bar = 0;
int           g_entry_dir = 0;
// cached leg decision
string        g_leg_rid = "";
datetime      g_leg_req_time = 0;
AIDecisionRec g_leg_dec;
long          g_seq = 0;

//=================================================================== utils
void Log(const string event, const string data = "")
  {
   string line = StringFormat("%s,%s,%s,%s", TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS), event,
                              g.exists ? g.basket_id : "-", data);
   Print("LRB ", line);
   int h = FileOpen(g_dir + "journal_" + TimeToString(TimeGMT(), TIME_DATE) + ".csv",
                    FILE_READ | FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI | FILE_SHARE_READ);
   if(h != INVALID_HANDLE) { FileSeek(h, 0, SEEK_END); FileWriteString(h, line + "\r\n"); FileClose(h); }
  }

void AddError(const string e)
  {
   int n = ArraySize(g_errors);
   if(n >= 20) { for(int i = 1; i < n; i++) g_errors[i - 1] = g_errors[i]; n = 19; }
   ArrayResize(g_errors, n + 1);
   g_errors[n] = TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS) + "Z " + e;
  }

void ExecError(const string e)
  {
   int n = ArraySize(gd.exec_errors);
   ArrayResize(gd.exec_errors, n + 1);
   gd.exec_errors[n] = TimeGMT();
   AddError(e);
   Log("EXEC_ERROR", e);
  }

int ExecErrorsLastHour()
  {
   datetime now = TimeGMT();
   int c = 0;
   for(int i = 0; i < ArraySize(gd.exec_errors); i++) if(now - gd.exec_errors[i] < 3600) c++;
   return c;
  }

bool WriteFileAtomic(const string name, const string content)
  {
   string tmp = name + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI);
   if(h == INVALID_HANDLE) return false;
   FileWriteString(h, content);
   FileClose(h);
   return FileMove(tmp, FILE_COMMON, name, FILE_COMMON | FILE_REWRITE);
  }

bool ReadFileText(const string name, string &out)
  {
   if(!FileIsExist(name, FILE_COMMON)) return false;
   int h = FileOpen(name, FILE_READ | FILE_TXT | FILE_COMMON | FILE_ANSI | FILE_SHARE_READ);
   if(h == INVALID_HANDLE) return false;
   out = "";
   while(!FileIsEnding(h)) out += FileReadString(h) + "\n";
   FileClose(h);
   return true;
  }

long ServerUtcOffsetSec()
  {
   return (long)MathRound((double)(TimeTradeServer() - TimeGMT()) / 900.0) * 900;
  }

//=================================================================== inputs
bool ParseProgression()
  {
   string parts[];
   int n = StringSplit(InpProgression, ',', parts);
   if(n < 1 || n > LRB_MAX_LEGS) return false;
   for(int i = 0; i < n; i++)
     {
      double m = StringToDouble(parts[i]);
      if(m <= 0) return false;
      g_sp.mult[i] = m;
     }
   if(InpMaxPositions < 1 || InpMaxPositions > n) return false;
   g_sp.n_legs = InpMaxPositions;
   return true;
  }

void LoadParams()
  {
   g_sp.atr_mult = InpAtrMult;
   g_sp.min_distance_points = InpMinDistancePts;
   g_sp.max_distance_points = InpMaxDistancePts;
   g_sp.min_distance_spread_mult = InpMinDistSpreadMult;
   g_sp.stop_distance_mult = InpStopDistMult;
   g_sp.risk_pct = InpRiskPct;
   g_sp.sizing_safety = InpSizingSafety;
   g_sp.commission_per_lot_side = InpCommissionPerLotSide;
   g_sp.slippage_allowance_points = InpSlippageAllowancePts;
   g_sp.spread_stress_mult = InpSpreadStressMult;
   g_sp.max_gross_lots = InpMaxGrossLots;
   g_sp.max_net_lots = InpMaxNetLots;
   g_spec.point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   g_spec.tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   g_spec.tick_value_loss = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(g_spec.tick_value_loss <= 0) g_spec.tick_value_loss = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   g_spec.volume_min = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   g_spec.volume_max = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   g_spec.volume_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
  }

// Returns "" if trading is permitted on this account, otherwise the reason.
string AccountPermission()
  {
   bool demo = AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO;
   if(!demo)
     {
      if(InpDemoOnly) return "DEMO_ONLY_LIVE_ACCOUNT";
      if(!InpAllowLive || InpLiveConfirm != "I_ACCEPT_LIVE_RISK") return "LIVE_NOT_ENABLED";
     }
   if(AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING) return "ACCOUNT_NOT_HEDGING";
   return "";
  }

//=================================================================== persistence
string KV(const string k, const string v) { return k + "=" + v + "\n"; }

void SaveState()
  {
   string s = KV("version", IntegerToString(LRB_STATE_VER));
   s += KV("phase", IntegerToString(g_phase));
   s += KV("g.day_key", IntegerToString(gd.day_key));
   s += KV("g.sod_equity", DoubleToString(gd.sod_equity, 2));
   s += KV("g.peak_equity", DoubleToString(gd.peak_equity, 2));
   s += KV("g.daily_halt", IntegerToString(gd.daily_halt));
   s += KV("g.dd_halt", IntegerToString(gd.dd_halt));
   s += KV("g.cooldown_until", IntegerToString((long)gd.cooldown_until));
   s += KV("g.last_signal_bar", IntegerToString((long)gd.last_signal_bar));
   s += KV("b.exists", IntegerToString(g.exists));
   if(g.exists)
     {
      s += KV("b.id", g.basket_id);
      s += KV("b.direction", IntegerToString(g.direction));
      s += KV("b.phase", IntegerToString(g.phase));
      s += KV("b.n_legs", IntegerToString(g.n_legs));
      s += KV("b.start_utc", IntegerToString((long)g.start_utc));
      s += KV("b.start_server", IntegerToString((long)g.start_server));
      s += KV("b.start_equity", DoubleToString(g.start_equity, 2));
      s += KV("b.risk_money", DoubleToString(g.risk_money, 2));
      s += KV("b.target_money", DoubleToString(g.target_money, 2));
      s += KV("b.has_levels", IntegerToString(g.has_levels));
      s += KV("b.upper", DoubleToString(g.lv.upper, 8));
      s += KV("b.lower", DoubleToString(g.lv.lower, 8));
      s += KV("b.stop_low", DoubleToString(g.lv.stop_low, 8));
      s += KV("b.stop_high", DoubleToString(g.lv.stop_high, 8));
      s += KV("b.distance", DoubleToString(g.lv.distance, 8));
      s += KV("b.stop_distance", DoubleToString(g.lv.stop_distance, 8));
      s += KV("b.adding_allowed", IntegerToString(g.adding_allowed));
      s += KV("b.block_reason", g.block_reason);
      s += KV("b.armed", IntegerToString(g.armed));
      s += KV("b.close_reason", g.close_reason);
      s += KV("b.close_trigger_pnl", DoubleToString(g.close_trigger_pnl, 2));
      s += KV("b.orphan", IntegerToString(g.orphan));
      string ids = "";
      for(int i = 0; i < ArraySize(g.pos_ids); i++) ids += (i ? "," : "") + IntegerToString((long)g.pos_ids[i]);
      s += KV("b.pos_ids", ids);
      for(int i = 0; i < g.n_legs; i++)
        {
         string p = "leg" + IntegerToString(i) + ".";
         s += KV(p + "side", IntegerToString(g.legs[i].side));
         s += KV(p + "planned", DoubleToString(g.legs[i].planned_volume, 2));
         s += KV(p + "state", IntegerToString(g.legs[i].state));
         s += KV(p + "attempts", IntegerToString(g.legs[i].attempts));
         s += KV(p + "ticket", IntegerToString((long)g.legs[i].ticket));
         s += KV(p + "filled", DoubleToString(g.legs[i].filled_volume, 2));
         s += KV(p + "price", DoubleToString(g.legs[i].fill_price, 8));
         s += KV(p + "sent", IntegerToString((long)g.legs[i].sent_time));
         s += KV(p + "closed", IntegerToString(g.legs[i].closed));
        }
     }
   if(!WriteFileAtomic(g_dir + "state.txt", s)) AddError("state save failed " + IntegerToString(GetLastError()));
   g_dirty = false;
  }

string Get(const string &keys[], const string &vals[], const string k, const string def = "")
  {
   for(int i = 0; i < ArraySize(keys); i++) if(keys[i] == k) return vals[i];
   return def;
  }

bool LoadState()
  {
   string txt;
   if(!ReadFileText(g_dir + "state.txt", txt)) return false;
   string lines[], keys[], vals[];
   int n = StringSplit(txt, '\n', lines);
   for(int i = 0; i < n; i++)
     {
      int p = StringFind(lines[i], "=");
      if(p <= 0) continue;
      int m = ArraySize(keys);
      ArrayResize(keys, m + 1);
      ArrayResize(vals, m + 1);
      keys[m] = StringSubstr(lines[i], 0, p);
      vals[m] = StringSubstr(lines[i], p + 1);
     }
   if((int)StringToInteger(Get(keys, vals, "version")) != LRB_STATE_VER) { AddError("state version mismatch - ignored"); return false; }
   gd.day_key = StringToInteger(Get(keys, vals, "g.day_key", "-1"));
   gd.sod_equity = StringToDouble(Get(keys, vals, "g.sod_equity"));
   gd.peak_equity = StringToDouble(Get(keys, vals, "g.peak_equity"));
   gd.daily_halt = StringToInteger(Get(keys, vals, "g.daily_halt")) != 0;
   gd.dd_halt = StringToInteger(Get(keys, vals, "g.dd_halt")) != 0;
   gd.cooldown_until = (datetime)StringToInteger(Get(keys, vals, "g.cooldown_until"));
   gd.last_signal_bar = (datetime)StringToInteger(Get(keys, vals, "g.last_signal_bar"));
   g.exists = StringToInteger(Get(keys, vals, "b.exists")) != 0;
   if(g.exists)
     {
      g.basket_id = Get(keys, vals, "b.id");
      g.direction = (int)StringToInteger(Get(keys, vals, "b.direction"));
      g.phase = (int)StringToInteger(Get(keys, vals, "b.phase"));
      g.n_legs = (int)StringToInteger(Get(keys, vals, "b.n_legs"));
      g.start_utc = (datetime)StringToInteger(Get(keys, vals, "b.start_utc"));
      g.start_server = (datetime)StringToInteger(Get(keys, vals, "b.start_server"));
      g.start_equity = StringToDouble(Get(keys, vals, "b.start_equity"));
      g.risk_money = StringToDouble(Get(keys, vals, "b.risk_money"));
      g.target_money = StringToDouble(Get(keys, vals, "b.target_money"));
      g.has_levels = StringToInteger(Get(keys, vals, "b.has_levels")) != 0;
      g.lv.upper = StringToDouble(Get(keys, vals, "b.upper"));
      g.lv.lower = StringToDouble(Get(keys, vals, "b.lower"));
      g.lv.stop_low = StringToDouble(Get(keys, vals, "b.stop_low"));
      g.lv.stop_high = StringToDouble(Get(keys, vals, "b.stop_high"));
      g.lv.distance = StringToDouble(Get(keys, vals, "b.distance"));
      g.lv.stop_distance = StringToDouble(Get(keys, vals, "b.stop_distance"));
      g.adding_allowed = StringToInteger(Get(keys, vals, "b.adding_allowed")) != 0;
      g.block_reason = Get(keys, vals, "b.block_reason");
      g.armed = StringToInteger(Get(keys, vals, "b.armed")) != 0;
      g.close_reason = Get(keys, vals, "b.close_reason");
      g.close_trigger_pnl = StringToDouble(Get(keys, vals, "b.close_trigger_pnl"));
      g.orphan = StringToInteger(Get(keys, vals, "b.orphan")) != 0;
      string ids[];
      int k = StringSplit(Get(keys, vals, "b.pos_ids"), ',', ids);
      ArrayResize(g.pos_ids, 0);
      for(int i = 0; i < k; i++) if(StringLen(ids[i]) > 0) AddPosId((ulong)StringToInteger(ids[i]));
      for(int i = 0; i < g.n_legs && i < LRB_MAX_LEGS; i++)
        {
         string p = "leg" + IntegerToString(i) + ".";
         g.legs[i].index = i + 1;
         g.legs[i].side = (int)StringToInteger(Get(keys, vals, p + "side"));
         g.legs[i].planned_volume = StringToDouble(Get(keys, vals, p + "planned"));
         g.legs[i].state = (int)StringToInteger(Get(keys, vals, p + "state"));
         g.legs[i].attempts = (int)StringToInteger(Get(keys, vals, p + "attempts"));
         g.legs[i].ticket = (ulong)StringToInteger(Get(keys, vals, p + "ticket"));
         g.legs[i].filled_volume = StringToDouble(Get(keys, vals, p + "filled"));
         g.legs[i].fill_price = StringToDouble(Get(keys, vals, p + "price"));
         g.legs[i].sent_time = (datetime)StringToInteger(Get(keys, vals, p + "sent"));
         g.legs[i].closed = StringToInteger(Get(keys, vals, p + "closed")) != 0;
        }
     }
   return true;
  }

//=================================================================== positions / accounting
bool IsOurs(const ulong ticket)
  {
   if(!PositionSelectByTicket(ticket)) return false;
   return PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagic;
  }

int OurPositions(ulong &tickets[])
  {
   ArrayResize(tickets, 0);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      int n = ArraySize(tickets);
      ArrayResize(tickets, n + 1);
      tickets[n] = t;
     }
   return ArraySize(tickets);
  }

bool HasPosId(const ulong id)
  {
   for(int i = 0; i < ArraySize(g.pos_ids); i++) if(g.pos_ids[i] == id) return true;
   return false;
  }

void AddPosId(const ulong id)
  {
   if(id == 0 || HasPosId(id)) return;
   int n = ArraySize(g.pos_ids);
   ArrayResize(g.pos_ids, n + 1);
   g.pos_ids[n] = id;
  }

double NetExposure()
  {
   ulong t[];
   double net = 0;
   for(int i = 0; i < OurPositions(t); i++)
     {
      if(!PositionSelectByTicket(t[i])) continue;
      double v = PositionGetDouble(POSITION_VOLUME);
      net += PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? v : -v;
     }
   return net;
  }

double GrossExposure()
  {
   ulong t[];
   double g2 = 0;
   for(int i = 0; i < OurPositions(t); i++) if(PositionSelectByTicket(t[i])) g2 += PositionGetDouble(POSITION_VOLUME);
   return g2;
  }

// Basket accounting - see python/src/lrb/core/accounting.py for the no-double-counting rules.
void BasketPnL(PnL &p)
  {
   p.floating = 0; p.open_swap = 0; p.realized = 0; p.commissions = 0; p.est_close = 0; p.open_volume = 0;
   ulong t[];
   int n = OurPositions(t);
   for(int i = 0; i < n; i++)
     {
      if(!PositionSelectByTicket(t[i])) continue;
      p.floating += PositionGetDouble(POSITION_PROFIT);   // marked at the closing side: spread already included
      p.open_swap += PositionGetDouble(POSITION_SWAP);
      p.open_volume += PositionGetDouble(POSITION_VOLUME);
     }
   if(HistorySelect(g.start_server - 3600, TimeTradeServer() + 3600))
     {
      int nd = HistoryDealsTotal();
      for(int i = 0; i < nd; i++)
        {
         ulong d = HistoryDealGetTicket(i);
         if(d == 0) continue;
         ulong pid = (ulong)HistoryDealGetInteger(d, DEAL_POSITION_ID);
         if(!HasPosId(pid)) continue;
         p.commissions += HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
         long entry = HistoryDealGetInteger(d, DEAL_ENTRY);
         if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY || entry == DEAL_ENTRY_INOUT)
            p.realized += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP);
        }
     }
   double v = g_spec.tick_value_loss / g_spec.tick_size;
   p.est_close = p.open_volume * InpCommissionPerLotSide + p.open_volume * InpSlippageAllowancePts * g_spec.point * v;
  }

double FinalRealized()
  {
   double total = 0;
   if(!HistorySelect(g.start_server - 3600, TimeTradeServer() + 3600)) return 0;
   for(int i = 0; i < HistoryDealsTotal(); i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0 || !HasPosId((ulong)HistoryDealGetInteger(d, DEAL_POSITION_ID))) continue;
      total += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP)
               + HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
     }
   return total;
  }

//=================================================================== guards
void UpdateKill()
  {
   string gv = "LRB_KILL_" + IntegerToString(InpMagic);
   int k = gd.kill;
   if(GlobalVariableCheck(gv)) k = (int)GlobalVariableGet(gv);
   string txt;
   if(ReadFileText(g_dir + "KILL", txt))
     {
      if(StringFind(txt, "FLATTEN") >= 0) k = KILL_FLATTEN;
      else if(StringFind(txt, "HALT") >= 0) k = MathMax(k, KILL_HALT);
     }
   if(k != gd.kill) { gd.kill = k; Log("KILL_SWITCH", KillName(k)); g_dirty = true; }
  }

void UpdateGuards(const datetime now)
  {
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   long day_key = (long)MathFloor((double)(now - InpDayRolloverUtcHour * 3600) / 86400.0);
   if(day_key != gd.day_key)
     {
      if(gd.daily_halt) Log("DAILY_HALT_CLEARED");
      gd.day_key = day_key;
      gd.sod_equity = eq;
      gd.daily_halt = false;
      g_dirty = true;
     }
   if(eq > gd.peak_equity) { gd.peak_equity = eq; g_dirty = true; }
   if(!gd.daily_halt && eq <= gd.sod_equity * (1 - InpDailyLossPct / 100.0))
     { gd.daily_halt = true; Log("DAILY_LOSS_HALT", DoubleToString(eq, 2)); g_dirty = true; }
   if(!gd.dd_halt && eq <= gd.peak_equity * (1 - InpMaxDrawdownPct / 100.0))
     { gd.dd_halt = true; Log("DRAWDOWN_HALT", DoubleToString(eq, 2)); g_dirty = true; }
  }

double RiskRoom()
  {
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double daily_room = gd.sod_equity * InpDailyLossPct / 100.0 - MathMax(0.0, gd.sod_equity - eq);
   double dd_room = gd.peak_equity * InpMaxDrawdownPct / 100.0 - MathMax(0.0, gd.peak_equity - eq);
   return MathMin(daily_room, dd_room);
  }

string EntriesBlockedReason()
  {
   if(g_phase == PHASE_RECONCILE) return "RECONCILING";
   if(!g_trading_permitted) return g_block_init;
   if(gd.kill != KILL_NONE) return "KILL_SWITCH";
   if(gd.dd_halt) return "DRAWDOWN_HALT";
   if(gd.daily_halt) return "DAILY_LOSS_HALT";
   return "";
  }

//=================================================================== market filters
bool InTradeSession(const datetime server_now, int &minutes_to_end)
  {
   MqlDateTime dt;
   TimeToStruct(server_now, dt);
   datetime day_start = server_now - (dt.hour * 3600 + dt.min * 60 + dt.sec);
   datetime from, to;
   minutes_to_end = 0;
   for(uint i = 0; i < 10; i++)
     {
      if(!SymbolInfoSessionTrade(_Symbol, (ENUM_DAY_OF_WEEK)dt.day_of_week, i, from, to)) break;
      datetime s = day_start + ((long)from % 86400);
      datetime e = day_start + (((long)to % 86400) == 0 ? 86400 : ((long)to % 86400));
      if(server_now >= s && server_now < e) { minutes_to_end = (int)((e - server_now) / 60); return true; }
     }
   return false;
  }

// Fail-safe: returns "" if OK, else a block reason. Missing calendar data blocks entries.
string NewsCheck(const datetime server_now)
  {
   if(!InpNewsFilter) return "";
   string ccy[2];
   ccy[0] = SymbolInfoString(_Symbol, SYMBOL_CURRENCY_BASE);
   ccy[1] = SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT);
   for(int c = 0; c < 2; c++)
     {
      MqlCalendarValue vals[];
      ResetLastError();
      int n = CalendarValueHistory(vals, server_now - InpNewsWindowMin * 60, server_now + InpNewsWindowMin * 60, NULL, ccy[c]);
      if(n < 0 || GetLastError() != 0) return "NEWS_DATA_UNAVAILABLE";
      for(int i = 0; i < n; i++)
        {
         MqlCalendarEvent ev;
         if(!CalendarEventById(vals[i].event_id, ev)) return "NEWS_DATA_UNAVAILABLE";
         if(ev.importance == CALENDAR_IMPORTANCE_HIGH) return "NEWS_BLACKOUT";
        }
     }
   return "";
  }

string EntryFilters(const MqlTick &tk)
  {
   string why = AccountPermission();
   if(why != "") return why;
   if(!TerminalInfoInteger(TERMINAL_CONNECTED)) return "TERMINAL_DISCONNECTED";
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED)) return "ALGO_TRADING_DISABLED";
   if(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_FULL) return "SYMBOL_TRADE_MODE";
   if(TimeGMT() < gd.cooldown_until) return "COOLDOWN";
   ulong t[];
   if(OurPositions(t) > 0) return "EXISTING_POSITIONS";
   if(TimeTradeServer() - tk.time > InpMaxTickAgeSec) return "STALE_PRICE";
   int mte;
   if(!InTradeSession(TimeTradeServer(), mte)) return "MARKET_CLOSED";
   if(mte < InpAvoidMinBeforeSessionEnd) return "SESSION_ENDING";
   if(tk.ask - tk.bid > InpMaxSpreadPts * g_spec.point) return "SPREAD_TOO_WIDE";
   why = NewsCheck(TimeTradeServer());
   if(why != "") return why;
   if(ExecErrorsLastHour() >= InpMaxExecErrorsPerHour) return "TOO_MANY_EXEC_ERRORS";
   return "";
  }

//=================================================================== indicators (identical to python/src/lrb/core)
bool BaselineSignal(int &direction, double &atr, double &ef, double &es, datetime &bar_time)
  {
   direction = 0;
   int need = MathMax(InpEmaSlow, InpAtrPeriod + 1) + 5;
   int window = MathMax(need * 4, 300);
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = CopyRates(_Symbol, InpSignalTF, 1, window, r);   // closed bars only, oldest first
   if(got < need) return false;
   bar_time = r[got - 1].time;
   // EMA seeded with SMA of first period values
   double kf = 2.0 / (InpEmaFast + 1.0), ks = 2.0 / (InpEmaSlow + 1.0);
   double sf = 0, ss = 0;
   for(int i = 0; i < InpEmaFast; i++) sf += r[i].close;
   for(int i = 0; i < InpEmaSlow; i++) ss += r[i].close;
   ef = sf / InpEmaFast;
   es = ss / InpEmaSlow;
   for(int i = InpEmaFast; i < got; i++) ef = kf * r[i].close + (1 - kf) * ef;
   for(int i = InpEmaSlow; i < got; i++) es = ks * r[i].close + (1 - ks) * es;
   // Wilder ATR
   double a = 0;
   for(int i = 1; i <= InpAtrPeriod; i++)
      a += MathMax(r[i].high - r[i].low, MathMax(MathAbs(r[i].high - r[i - 1].close), MathAbs(r[i].low - r[i - 1].close)));
   a /= InpAtrPeriod;
   for(int i = InpAtrPeriod + 1; i < got; i++)
     {
      double tr = MathMax(r[i].high - r[i].low, MathMax(MathAbs(r[i].high - r[i - 1].close), MathAbs(r[i].low - r[i - 1].close)));
      a = (a * (InpAtrPeriod - 1) + tr) / InpAtrPeriod;
     }
   atr = a;
   if(a <= 0) return false;
   if(ef - es >= InpMinTrendAtrFrac * a) direction = 1;
   else if(es - ef >= InpMinTrendAtrFrac * a) direction = -1;
   return true;
  }

//=================================================================== AI exchange (files; see LRB_AIBridge.mq5)
string NewRequestId(const string kind)
  {
   g_seq++;
   return StringFormat("%s-%I64d-%I64d-%I64d", kind == "ENTRY" ? "E" : "L", InpMagic % 100000, (long)TimeGMT(), g_seq % 100000);
  }

string AccountJson()
  {
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double dd = gd.peak_equity > 0 ? MathMax(0.0, (1 - eq / gd.peak_equity) * 100) : 0;
   double dp = gd.sod_equity > 0 ? (eq / gd.sod_equity - 1) * 100 : 0;
   return "{" + JNum("equity", eq, 2) + "," + JNum("balance", AccountInfoDouble(ACCOUNT_BALANCE), 2) + "," +
          JNum("margin", AccountInfoDouble(ACCOUNT_MARGIN), 2) + "," + JNum("drawdown_pct", dd, 4) + "," +
          JNum("daily_pnl_pct", dp, 4) + "," + JNum("gross_lots", GrossExposure(), 2) + "," +
          JNum("net_lots", NetExposure(), 2) + "}";
  }

bool WriteDecisionRequest(const string rid, const string kind, const int direction, const int leg_index,
                          const MqlTick &tk, const double distance)
  {
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = CopyRates(_Symbol, InpSignalTF, 1, InpAIBars, r);
   if(got < 1) return false;
   long off = ServerUtcOffsetSec();
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   string bars = "";
   for(int i = 0; i < got; i++)
      bars += (i ? "," : "") + "{" + JInt("t", (long)r[i].time - off) + "," + JNum("o", r[i].open, digits) + "," +
              JNum("h", r[i].high, digits) + "," + JNum("l", r[i].low, digits) + "," + JNum("c", r[i].close, digits) + "}";
   string body = "{" + JStr("request_id", rid) + "," + JStr("kind", kind) + "," + JStr("symbol", _Symbol) + "," +
                 JInt("magic", InpMagic) + "," + JStr("proposed_direction", direction > 0 ? "BUY" : "SELL") + "," +
                 JInt("leg_index", leg_index) + "," + JInt("sent_at_utc", (long)TimeGMT()) + "," +
                 JInt("timeframe_sec", PeriodSeconds(InpSignalTF)) + "," + JNum("bid", tk.bid, digits) + "," +
                 JNum("ask", tk.ask, digits) + "," + JNum("distance", distance, 8) + "," +
                 "\"account\":" + AccountJson() + ",\"bars\":[" + bars + "]}";
   string fname = g_dir + (kind == "ENTRY" ? "req_entry.json" : "req_leg.json");
   return WriteFileAtomic(fname, body);
  }

// Reads and consumes resp_<rid>.json. Returns true when a response file was found (valid or not).
bool ReadDecision(const string rid, AIDecisionRec &d)
  {
   d.valid = false;
   string name = g_dir + "resp_" + rid + ".json";
   string txt;
   if(!ReadFileText(name, txt)) return false;
   FileDelete(name, FILE_COMMON);
   string err;
   if(JsonGetString(txt, "error", err)) { g_ai_status = "ERROR"; AddError("AI bridge: " + err); return true; }
   double li = 0;
   if(!JsonGetString(txt, "request_id", d.request_id) || d.request_id != rid) { g_ai_status = "INVALID"; return true; }
   if(!JsonGetString(txt, "kind", d.kind) || !JsonGetString(txt, "decision", d.decision) ||
      !JsonGetString(txt, "direction", d.direction) || !JsonGetString(txt, "model_version", d.model_version) ||
      !JsonGetNumber(txt, "expires_at_utc_epoch", d.expires_at) ||
      !JsonGetNumber(txt, "data_timestamp_utc_epoch", d.data_ts) || !JsonGetNumber(txt, "leg_index", li))
     { g_ai_status = "INVALID"; return true; }
   d.leg_index = (int)li;
   JsonGetStringArray(txt, "reason_codes", d.reasons);
   if(d.decision != "ALLOW" && d.decision != "BLOCK" && d.decision != "WAIT") { g_ai_status = "INVALID"; return true; }
   if(d.direction != "BUY" && d.direction != "SELL" && d.direction != "NONE") { g_ai_status = "INVALID"; return true; }
   d.valid = true;
   g_ai_status = "OK";
   g_last_ai = d.kind + " " + d.decision + " " + d.direction + " " + d.model_version + " [" + d.reasons + "]";
   Log("AI_DECISION", g_last_ai);
   return true;
  }

// Returns "OK" or a reason (UNAVAILABLE/EXPIRED/STALE/BLOCK/WAIT/DIRECTION_MISMATCH/MISMATCH/INVALID).
string ValidateDecision(const AIDecisionRec &d, const string kind, const int direction, const int leg_index)
  {
   if(!d.valid) return "UNAVAILABLE";
   if(d.kind != kind || (kind == "RECOVERY_LEG" && d.leg_index != leg_index)) return "MISMATCH";
   double now = (double)TimeGMT();
   if(now >= d.expires_at) return "EXPIRED";
   if(now - d.data_ts > InpAIMaxDataAgeSec) return "STALE";
   if(d.decision != "ALLOW") return d.decision;
   if(d.direction != (direction > 0 ? "BUY" : "SELL")) return "DIRECTION_MISMATCH";
   return "OK";
  }

void UpdateBridgeStatus()
  {
   if(InpAIMode == AI_OFF && InpLegApproval == LEGAPPR_OFF) { g_ai_status = "OFF"; return; }
   string hb;
   if(!ReadFileText(g_dir + "bridge_heartbeat.txt", hb) || (long)TimeGMT() - StringToInteger(hb) > 30)
      g_ai_status = "BRIDGE_DOWN";
  }

//=================================================================== reconciliation
void AdoptOrphan(ulong &tickets[])
  {
   int n = ArraySize(tickets);
   // sort by open time
   for(int i = 0; i < n; i++)
      for(int j = i + 1; j < n; j++)
        {
         PositionSelectByTicket(tickets[i]);
         long ti = PositionGetInteger(POSITION_TIME);
         PositionSelectByTicket(tickets[j]);
         long tj = PositionGetInteger(POSITION_TIME);
         if(tj < ti) { ulong x = tickets[i]; tickets[i] = tickets[j]; tickets[j] = x; }
        }
   g.exists = true;
   g.basket_id = "ORPHAN-" + IntegerToString((long)TimeGMT());
   g.n_legs = MathMin(n, LRB_MAX_LEGS);
   ArrayResize(g.pos_ids, 0);
   for(int i = 0; i < g.n_legs; i++)
     {
      PositionSelectByTicket(tickets[i]);
      g.legs[i].index = i + 1;
      g.legs[i].side = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
      g.legs[i].planned_volume = PositionGetDouble(POSITION_VOLUME);
      g.legs[i].filled_volume = g.legs[i].planned_volume;
      g.legs[i].fill_price = PositionGetDouble(POSITION_PRICE_OPEN);
      g.legs[i].state = LEG_FILLED;
      g.legs[i].ticket = tickets[i];
      g.legs[i].closed = false;
      AddPosId((ulong)PositionGetInteger(POSITION_IDENTIFIER));
      if(i == 0)
        {
         g.direction = g.legs[0].side;
         g.start_server = (datetime)PositionGetInteger(POSITION_TIME);
         g.start_utc = (datetime)((long)g.start_server - ServerUtcOffsetSec());
        }
     }
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   g.start_equity = bal;
   g.risk_money = InpRiskPct / 100.0 * bal;
   g.target_money = InpTargetPct / 100.0 * bal;
   g.has_levels = false;            // boundaries unknown: price stops off, money/time/guard exits on
   g.adding_allowed = false;
   g.block_reason = "ORPHAN_ADOPTED";
   g.orphan = true;
   g.phase = PHASE_ACTIVE;
   g.max_float_loss = 0;
   g.max_gross = 0;
   Log("ORPHAN_ADOPTED", IntegerToString(n) + " positions");
  }

bool StartupReconcile()
  {
   if(!TerminalInfoInteger(TERMINAL_CONNECTED)) return false;          // wait for the broker
   if(!HistorySelect(TimeTradeServer() - 30 * 86400, TimeTradeServer() + 3600)) return false;
   ulong t[];
   int n = OurPositions(t);
   if(!g.exists)
     {
      if(n > 0) { AdoptOrphan(t); g_phase = PHASE_ACTIVE; }
      else g_phase = PHASE_FLAT;
      Log("RECONCILED", StringFormat("positions=%d local_state=0", n));
      g_dirty = true;
      return true;
     }
   if(n == 0 && (g.phase == PHASE_ACTIVE || g.phase == PHASE_OPENING || g.phase == PHASE_CLOSING))
     {
      bool had_fill = false;
      for(int i = 0; i < g.n_legs; i++) if(g.legs[i].state == LEG_FILLED || g.legs[i].state == LEG_PARTIAL || g.legs[i].state == LEG_SENT) had_fill = true;
      if(had_fill)
        {
         if(g.close_reason == "") g.close_reason = "CLOSED_WHILE_OFFLINE";
         g.phase = PHASE_CLOSING;
         g_phase = PHASE_CLOSING;
        }
      else { g.exists = false; g_phase = PHASE_FLAT; }
      Log("RECONCILED", "positions=0 local_state=1");
      g_dirty = true;
      return true;
     }
   g_phase = g.phase;
   Log("RECONCILED", StringFormat("positions=%d local_state=1 phase=%s", n, PhaseName(g.phase)));
   g_dirty = true;
   return true;
  }

int PendingLeg()
  {
   for(int i = 0; i < g.n_legs; i++) if(g.legs[i].state == LEG_SENT) return i;
   return -1;
  }

int NextPlannedLeg()
  {
   for(int i = 0; i < g.n_legs; i++) if(g.legs[i].state == LEG_PLANNED) return i;
   return -1;
  }

bool LegKnown(const ulong ticket)
  {
   for(int i = 0; i < g.n_legs; i++) if(g.legs[i].ticket == ticket && ticket != 0) return true;
   return false;
  }

void ReconcilePositions()
  {
   ulong t[];
   int n = OurPositions(t);
   for(int i = 0; i < n; i++)
     {
      if(LegKnown(t[i])) continue;
      PositionSelectByTicket(t[i]);
      int side = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
      datetime pt = (datetime)PositionGetInteger(POSITION_TIME);
      int pl = PendingLeg();
      string cmt = PositionGetString(POSITION_COMMENT);
      bool comment_ok = (StringFind(cmt, "LRB|") < 0) || (pl >= 0 && StringFind(cmt, "|L" + IntegerToString(pl + 1)) >= 0);
      if(pl >= 0 && g.legs[pl].side == side && comment_ok &&
         (long)pt >= (long)g.legs[pl].sent_time + ServerUtcOffsetSec() - 5)
        {
         AckLeg(pl, t[i], PositionGetDouble(POSITION_VOLUME), PositionGetDouble(POSITION_PRICE_OPEN), true);
         continue;
        }
      AddPosId((ulong)PositionGetInteger(POSITION_IDENTIFIER));
      Log("UNEXPECTED_POSITION", IntegerToString((long)t[i]));
      if(g.phase != PHASE_CLOSING) BeginClose("EMERGENCY_UNEXPECTED_POSITION");
     }
   for(int i = 0; i < g.n_legs; i++)
     {
      if((g.legs[i].state == LEG_FILLED || g.legs[i].state == LEG_PARTIAL) && !g.legs[i].closed)
        {
         bool present = false;
         for(int j = 0; j < n; j++) if(t[j] == g.legs[i].ticket) present = true;
         if(!present)
           {
            g.legs[i].closed = true;
            g_dirty = true;
            if(g.phase != PHASE_CLOSING)
              {
               Log("EXTERNAL_CLOSE_DETECTED", IntegerToString((long)g.legs[i].ticket));
               BeginClose("EMERGENCY_EXTERNAL_CLOSE");
              }
           }
        }
     }
   int pl = PendingLeg();
   if(pl >= 0 && TimeGMT() - g.legs[pl].sent_time > InpPendingAckSec)
     {
      g.legs[pl].state = LEG_FAILED;
      ExecError("LEG_ACK_TIMEOUT leg " + IntegerToString(pl + 1));
      g.adding_allowed = false;
      g.block_reason = "LEG_ACK_TIMEOUT";
      g_dirty = true;
      if(g.phase == PHASE_OPENING) { Log("BASKET_ABORTED"); g.exists = false; g_phase = PHASE_FLAT; }
     }
  }

void Rearm()
  {
   int nx = NextPlannedLeg();
   MqlTick tk;
   if(nx < 0 || !g.has_levels || !SymbolInfoTick(_Symbol, tk)) { g.armed = false; return; }
   g.armed = g.legs[nx].side > 0 ? (tk.ask < g.lv.upper) : (tk.bid > g.lv.lower);
  }

void StopAdding(const string reason)
  {
   g.adding_allowed = false;
   g.block_reason = reason;
   for(int i = 0; i < g.n_legs; i++) if(g.legs[i].state == LEG_PLANNED) g.legs[i].state = LEG_SKIPPED;
   Log("STOP_ADDING", reason);
   g_dirty = true;
  }

void AckLeg(const int i, const ulong ticket, const double vol, const double price, const bool late)
  {
   g.legs[i].ticket = ticket;
   g.legs[i].filled_volume = vol;
   g.legs[i].fill_price = price;
   bool partial = vol + 1e-9 < g.legs[i].planned_volume;
   g.legs[i].state = partial ? LEG_PARTIAL : LEG_FILLED;
   if(PositionSelectByTicket(ticket)) AddPosId((ulong)PositionGetInteger(POSITION_IDENTIFIER));
   AddPosId(ticket);
   if(i == 0)
     {
      LevelsFromAnchor(g.direction, price, g.lv.distance, InpStopDistMult, g.lv);
      g.has_levels = true;
      g.phase = PHASE_ACTIVE;
      g_phase = PHASE_ACTIVE;
     }
   Log("LEG_FILLED", StringFormat("leg=%d side=%d vol=%.2f price=%.6f partial=%d late=%d", i + 1, g.legs[i].side, vol, price, partial, late));
   if(partial && i + 1 < g.n_legs) StopAdding("PARTIAL_FILL");
   if(i + 1 >= g.n_legs && g.adding_allowed) { g.adding_allowed = false; g.block_reason = "MAX_LEGS_REACHED"; }
   Rearm();
   if(InpCatastrophicSL && g.has_levels) SetCatastrophicSL(ticket, g.legs[i].side);
   g_leg_rid = "";               // a new leg index needs a fresh approval
   g_leg_dec.valid = false;
   g_dirty = true;
  }

void SetCatastrophicSL(const ulong ticket, const int side)
  {
   double extra = InpCatastrophicExtraMult * g.lv.stop_distance;
   double sl = side > 0 ? g.lv.stop_low - extra : g.lv.stop_high + extra;
   sl = NormalizeDouble(sl, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   if(!g_trade.PositionModify(ticket, sl, 0.0)) AddError("catastrophic SL modify failed " + IntegerToString(g_trade.ResultRetcode()));
  }

//=================================================================== exits
string ExitReason(const MqlTick &tk, const PnL &p)
  {
   if(gd.kill == KILL_FLATTEN) return "KILL_SWITCH";
   if(gd.daily_halt) return "DAILY_LOSS_LIMIT";
   if(gd.dd_halt) return "ACCOUNT_DRAWDOWN_LIMIT";
   double net = p.Net();
   if(net <= -g.risk_money) return "BASKET_LOSS_LIMIT";
   if(g.has_levels)
     {
      double ne = NormalizeDouble(NetExposure(), 8);
      if(ne >= 0 && tk.bid <= g.lv.stop_low) return "PRICE_STOP";
      if(ne <= 0 && tk.ask >= g.lv.stop_high) return "PRICE_STOP";
     }
   double ml = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   if(AccountInfoDouble(ACCOUNT_MARGIN) > 0 && ml > 0 && ml < InpEmergencyMarginPct) return "EMERGENCY_MARGIN";
   if(TimeGMT() - g.start_utc >= InpMaxBasketHours * 3600) return "MAX_DURATION";
   if(net >= g.target_money) return "PROFIT_TARGET";
   return "";
  }

void BeginClose(const string reason)
  {
   PnL p;
   BasketPnL(p);
   g.phase = PHASE_CLOSING;
   g_phase = PHASE_CLOSING;
   g.adding_allowed = false;
   if(g.block_reason == "") g.block_reason = "CLOSING";
   g.close_reason = reason;
   g.close_trigger_pnl = p.Net();
   g.close_started = TimeGMT();
   g.last_close_attempt = 0;
   Log("CLOSE_BEGIN", StringFormat("reason=%s trigger_pnl=%.2f", reason, g.close_trigger_pnl));
   g_dirty = true;
  }

void ProcessClose()
  {
   ulong t[];
   int n = OurPositions(t);
   if(n > 0 && TimeGMT() - g.last_close_attempt >= InpCloseRetrySec)
     {
      g.last_close_attempt = TimeGMT();
      g.close_attempts++;
      for(int i = 0; i < n; i++)
        {
         if(PositionSelectByTicket(t[i])) AddPosId((ulong)PositionGetInteger(POSITION_IDENTIFIER));
         if(!g_trade.PositionClose(t[i], InpDeviationPts) ||
            (g_trade.ResultRetcode() != TRADE_RETCODE_DONE && g_trade.ResultRetcode() != TRADE_RETCODE_DONE_PARTIAL))
            ExecError(StringFormat("CLOSE_FAILED ticket=%I64u retcode=%u %s", t[i], g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription()));
        }
      n = OurPositions(t);
      if(n > 0 && g.close_attempts % 10 == 0) Log("CLOSE_STILL_PENDING", IntegerToString(n));
     }
   if(n == 0) Finalize();
  }

void Finalize()
  {
   double realized = FinalRealized();
   int filled = 0;
   for(int i = 0; i < g.n_legs; i++) if(g.legs[i].state == LEG_FILLED || g.legs[i].state == LEG_PARTIAL) filled++;
   string rep = StringFormat("reason=%s legs=%d/%d trigger_pnl=%.2f realized_pnl=%.2f risk=%.2f target=%.2f max_float_loss=%.2f orphan=%d",
                             g.close_reason, filled, g.n_legs, g.close_trigger_pnl, realized, g.risk_money,
                             g.target_money, g.max_float_loss, g.orphan);
   Log("BASKET_CLOSED", rep);
   int h = FileOpen(g_dir + "baskets.csv", FILE_READ | FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI | FILE_SHARE_READ);
   if(h != INVALID_HANDLE)
     {
      FileSeek(h, 0, SEEK_END);
      FileWriteString(h, StringFormat("%s,%s,%d,%s,%s,%d,%.2f,%.2f\r\n", TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS),
                                      g.basket_id, g.direction, TimeToString(g.start_utc, TIME_DATE | TIME_SECONDS),
                                      g.close_reason, filled, g.close_trigger_pnl, realized));
      FileClose(h);
     }
   g.exists = false;
   g_phase = PHASE_FLAT;
   gd.cooldown_until = TimeGMT() + InpCooldownMin * 60;
   g_leg_rid = "";
   g_dirty = true;
  }

//=================================================================== recovery legs
string LegRiskCheck(const int i, const MqlTick &tk)
  {
   double vol = g.legs[i].planned_volume;
   if(GrossExposure() + vol > InpMaxGrossLots + 1e-9) return "MAX_GROSS_EXPOSURE";
   if(MathAbs(NetExposure() + g.legs[i].side * vol) > InpMaxNetLots + 1e-9) return "MAX_NET_EXPOSURE";
   double m = 0;
   double price = g.legs[i].side > 0 ? tk.ask : tk.bid;
   if(!OrderCalcMargin(g.legs[i].side > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, vol, price, m)) return "MARGIN_CALC_FAILED";
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double projected = AccountInfoDouble(ACCOUNT_MARGIN) + m;
   if(projected > 0 && eq / projected * 100.0 < InpMinMarginLevelPct) return "PROJECTED_MARGIN_LEVEL";
   if(eq - projected < InpMinFreeMarginAfterPct / 100.0 * eq) return "INSUFFICIENT_FREE_MARGIN";
   return "";
  }

void MaintainLegApproval(const MqlTick &tk)
  {
   if(InpLegApproval != LEGAPPR_REQUIRED || !g.adding_allowed || !g.has_levels) return;
   int nx = NextPlannedLeg();
   if(nx < 1) return;
   if(g_leg_rid != "")
     {
      AIDecisionRec d;
      if(ReadDecision(g_leg_rid, d)) { g_leg_dec = d; }
     }
   bool fresh = g_leg_dec.valid && g_leg_dec.leg_index == nx + 1 && (double)TimeGMT() < g_leg_dec.expires_at - 15;
   bool waiting = g_leg_rid != "" && !g_leg_dec.valid && TimeGMT() - g_leg_req_time < InpAIResponseTimeoutSec;
   if(!fresh && !waiting)
     {
      g_leg_rid = NewRequestId("RECOVERY_LEG");
      g_leg_req_time = TimeGMT();
      g_leg_dec.valid = false;
      if(!WriteDecisionRequest(g_leg_rid, "RECOVERY_LEG", g.legs[nx].side, nx + 1, tk, g.lv.distance))
         AddError("leg request write failed");
     }
  }

void MaybeAddLeg(const MqlTick &tk)
  {
   if(!g.adding_allowed || !g.has_levels || PendingLeg() >= 0) return;
   if(EntriesBlockedReason() != "") return;            // exits stay active; no new exposure
   int i = NextPlannedLeg();
   if(i < 0) { StopAdding("MAX_LEGS_REACHED"); return; }
   bool armed_now, fired;
   double slip;
   if(g.legs[i].side > 0) { armed_now = tk.ask < g.lv.upper; fired = tk.ask >= g.lv.upper; slip = tk.ask - g.lv.upper; }
   else                   { armed_now = tk.bid > g.lv.lower; fired = tk.bid <= g.lv.lower; slip = g.lv.lower - tk.bid; }
   if(!g.armed) { if(armed_now) { g.armed = true; g_dirty = true; } return; }
   if(!fired) return;
   if(slip > InpMaxTriggerSlipPts * g_spec.point) { StopAdding("GAP_BEYOND_TRIGGER"); return; }
   if(TimeTradeServer() - tk.time > InpMaxTickAgeSec) return;
   if(tk.ask - tk.bid > InpMaxLegSpreadPts * g_spec.point) { Log("LEG_DEFERRED_SPREAD"); return; }
   string why = LegRiskCheck(i, tk);
   if(why != "") { StopAdding(why); return; }
   if(InpLegApproval == LEGAPPR_REQUIRED)
     {
      string v = ValidateDecision(g_leg_dec, "RECOVERY_LEG", g.legs[i].side, i + 1);
      // If AI blocks, the basket stays exposed: stop adding, keep deterministic exits,
      // and never assume a later leg will rescue it.
      if(v != "OK") { StopAdding("AI_LEG_" + v); return; }
     }
   SendLeg(i, tk);
  }

void SendLeg(const int i, const MqlTick &tk)
  {
   g.legs[i].attempts++;
   g.legs[i].state = LEG_SENT;
   g.legs[i].sent_time = TimeGMT();
   g.legs[i].request_id = g.basket_id + ":L" + IntegerToString(i + 1) + ":" + IntegerToString(g.legs[i].attempts);
   string cmt = "LRB|" + StringSubstr(g.basket_id, MathMax(0, StringLen(g.basket_id) - 12)) + "|L" + IntegerToString(i + 1);
   SaveState();                                  // persist SENT before the order leaves (restart safety)
   bool ok = g.legs[i].side > 0 ? g_trade.Buy(g.legs[i].planned_volume, _Symbol, 0, 0, 0, cmt)
                                : g_trade.Sell(g.legs[i].planned_volume, _Symbol, 0, 0, 0, cmt);
   uint rc = g_trade.ResultRetcode();
   Log("LEG_SENT", StringFormat("leg=%d side=%d vol=%.2f rid=%s retcode=%u", i + 1, g.legs[i].side, g.legs[i].planned_volume, g.legs[i].request_id, rc));
   if(rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_DONE_PARTIAL)
     {
      ulong pos_id = g_trade.ResultOrder();     // position identifier == opening order ticket
      ulong t[];
      int n = OurPositions(t);
      for(int j = 0; j < n; j++)
        {
         PositionSelectByTicket(t[j]);
         if((ulong)PositionGetInteger(POSITION_IDENTIFIER) == pos_id)
           {
            AckLeg(i, t[j], PositionGetDouble(POSITION_VOLUME), PositionGetDouble(POSITION_PRICE_OPEN), false);
            return;
           }
        }
      return;   // position not visible yet: stays SENT, reconciliation acknowledges it
     }
   if(rc == TRADE_RETCODE_PLACED || rc == TRADE_RETCODE_TIMEOUT || rc == 0) return;   // unknown outcome -> reconcile
   g.legs[i].state = LEG_PLANNED;
   ExecError(StringFormat("LEG_SEND_FAILED leg=%d retcode=%u %s", i + 1, rc, g_trade.ResultRetcodeDescription()));
   if(g.legs[i].attempts >= InpLegMaxAttempts)
     {
      g.legs[i].state = LEG_FAILED;
      if(i == 0) { Log("BASKET_ABORTED"); g.exists = false; g_phase = PHASE_FLAT; gd.cooldown_until = TimeGMT() + 60; }
      else StopAdding("LEG_SEND_FAILED");
     }
   g_dirty = true;
  }

//=================================================================== new basket
string BasketMarginCheck(const int direction, const SizingResult &sz, const MqlTick &tk)
  {
   double total = 0;
   for(int i = 0; i < g_sp.n_legs; i++)
     {
      int side = LegSide(direction, i);
      double m = 0;
      if(!OrderCalcMargin(side > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, sz.volumes[i], side > 0 ? tk.ask : tk.bid, m))
         return "MARGIN_CALC_FAILED";
      total += m;                                 // conservative: no hedge reduction assumed
     }
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double projected = AccountInfoDouble(ACCOUNT_MARGIN) + total;
   if(projected > 0 && eq / projected * 100.0 < InpMinMarginLevelPct) return "PROJECTED_MARGIN_LEVEL";
   if(eq - projected < InpMinFreeMarginAfterPct / 100.0 * eq) return "INSUFFICIENT_FREE_MARGIN";
   return "";
  }

bool PrepareEntry(const int direction, const double atr, const MqlTick &tk, SizingResult &sz, string &why)
  {
   why = EntryFilters(tk);
   if(why != "") return false;
   ComputeBaseLot(direction, AccountInfoDouble(ACCOUNT_EQUITY), atr, tk.ask - tk.bid, RiskRoom(), g_sp, g_spec, sz);
   if(!sz.ok) { why = sz.reason; return false; }
   why = BasketMarginCheck(direction, sz, tk);
   return why == "";
  }

void OpenBasket(const int direction, const SizingResult &sz, const MqlTick &tk)
  {
   g.exists = true;
   g.basket_id = StringFormat("%04I64d%I64x", InpMagic % 10000, (long)TimeGMT());
   g.direction = direction;
   g.phase = PHASE_OPENING;
   g_phase = PHASE_OPENING;
   g.n_legs = g_sp.n_legs;
   for(int i = 0; i < g.n_legs; i++)
     {
      g.legs[i].index = i + 1;
      g.legs[i].side = LegSide(direction, i);
      g.legs[i].planned_volume = sz.volumes[i];
      g.legs[i].state = LEG_PLANNED;
      g.legs[i].attempts = 0;
      g.legs[i].ticket = 0;
      g.legs[i].filled_volume = 0;
      g.legs[i].fill_price = 0;
      g.legs[i].closed = false;
     }
   g.start_utc = TimeGMT();
   g.start_server = TimeTradeServer();
   g.start_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   g.risk_money = sz.risk_budget;
   g.target_money = InpTargetPct / 100.0 * g.start_equity;
   g.has_levels = false;
   g.lv.distance = sz.distance;
   g.lv.stop_distance = sz.stop_distance;
   g.adding_allowed = true;
   g.block_reason = "";
   g.armed = false;
   ArrayResize(g.pos_ids, 0);
   g.close_reason = "";
   g.close_trigger_pnl = 0;
   g.close_attempts = 0;
   g.orphan = false;
   g.max_float_loss = 0;
   g.max_gross = 0;
   Log("BASKET_OPEN", StringFormat("dir=%d %s", direction, sz.explain));
   SendLeg(0, tk);
  }

void MaybeOpen(const MqlTick &tk)
  {
   if(EntriesBlockedReason() != "") { g_entry_rid = ""; return; }
   // 1) a pending AI request for this bar?
   if(g_entry_rid != "")
     {
      datetime cur_bar = iTime(_Symbol, InpSignalTF, 1);
      if(cur_bar != g_entry_bar) { Log("ENTRY_REJECTED", "AI_RESPONSE_BAR_EXPIRED"); g_entry_rid = ""; return; }
      AIDecisionRec d;
      if(ReadDecision(g_entry_rid, d))
        {
         string v = ValidateDecision(d, "ENTRY", g_entry_dir, 0);
         string rid = g_entry_rid;
         g_entry_rid = "";
         if(v != "OK") { Log("ENTRY_REJECTED", "AI_" + v + " rid=" + rid); return; }
         int dir2;
         double atr, ef, es;
         datetime bt;
         if(!BaselineSignal(dir2, atr, ef, es, bt) || dir2 != g_entry_dir) { Log("ENTRY_REJECTED", "SIGNAL_CHANGED"); return; }
         SizingResult sz;
         string why;
         if(!PrepareEntry(g_entry_dir, atr, tk, sz, why)) { Log("ENTRY_REJECTED", why); return; }
         OpenBasket(g_entry_dir, sz, tk);
         return;
        }
      if(TimeGMT() > g_entry_deadline)
        {
         g_ai_status = "TIMEOUT";
         Log("ENTRY_REJECTED", "AI_TIMEOUT rid=" + g_entry_rid);
         g_entry_rid = "";
        }
      return;
     }
   // 2) evaluate the baseline once per closed bar
   datetime bar = iTime(_Symbol, InpSignalTF, 1);
   if(bar == 0 || bar == gd.last_signal_bar) return;
   gd.last_signal_bar = bar;
   g_dirty = true;
   int direction;
   double atr, ef, es;
   datetime bt;
   if(!BaselineSignal(direction, atr, ef, es, bt) || direction == 0) return;
   SizingResult sz;
   string why;
   if(!PrepareEntry(direction, atr, tk, sz, why)) { Log("ENTRY_REJECTED", why + (sz.explain != "" ? " | " + sz.explain : "")); return; }
   if(InpAIMode == AI_OFF) { OpenBasket(direction, sz, tk); return; }
   g_entry_rid = NewRequestId("ENTRY");
   g_entry_bar = bar;
   g_entry_dir = direction;
   g_entry_deadline = TimeGMT() + InpAIResponseTimeoutSec;
   if(!WriteDecisionRequest(g_entry_rid, "ENTRY", direction, 0, tk, sz.distance))
     { AddError("entry request write failed"); g_entry_rid = ""; }
  }

//=================================================================== telemetry
void WriteStatus()
  {
   PnL p;
   p.floating = 0; p.open_swap = 0; p.realized = 0; p.commissions = 0; p.est_close = 0; p.open_volume = 0;
   if(g.exists) BasketPnL(p);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   string legs = "";
   if(g.exists)
      for(int i = 0; i < g.n_legs; i++)
         legs += (i ? "," : "") + "{" + JInt("index", i + 1) + "," + JStr("side", g.legs[i].side > 0 ? "BUY" : "SELL") + "," +
                 JNum("planned_volume", g.legs[i].planned_volume, 2) + "," + JStr("state", LegStateName(g.legs[i].state)) + "," +
                 JInt("ticket", (long)g.legs[i].ticket) + "," + JNum("filled_volume", g.legs[i].filled_volume, 2) + "," +
                 JNum("fill_price", g.legs[i].fill_price, digits) + "}";
   string basket = "{" + JStr("basket_id", g.exists ? g.basket_id : "") + "," + JStr("phase", PhaseName(g_phase)) + "," +
                   JStr("direction", g.exists ? (g.direction > 0 ? "BUY" : "SELL") : "") + "," +
                   JNum("upper", g.exists ? g.lv.upper : 0.0, digits) + "," + JNum("lower", g.exists ? g.lv.lower : 0.0, digits) + "," +
                   JNum("stop_low", g.exists ? g.lv.stop_low : 0.0, digits) + "," + JNum("stop_high", g.exists ? g.lv.stop_high : 0.0, digits) + "," +
                   JNum("net_pnl", p.Net(), 2) + "," + JNum("floating", p.floating, 2) + "," + JNum("realized", p.realized, 2) + "," +
                   JNum("commissions", p.commissions, 2) + "," + JNum("swap", p.open_swap, 2) + "," +
                   JNum("target_money", g.exists ? g.target_money : 0.0, 2) + "," + JNum("risk_money", g.exists ? g.risk_money : 0.0, 2) + "," +
                   JBool("adding_allowed", g.exists && g.adding_allowed) + "," + JStr("block_reason", g.exists ? g.block_reason : "") + "," +
                   JStr("close_reason", g.exists ? g.close_reason : "") + ",\"legs\":[" + legs + "]}";
   string errs = "";
   for(int i = 0; i < ArraySize(g_errors); i++) errs += (i ? "," : "") + "\"" + JsonEscape(g_errors[i]) + "\"";
   string s = "{" + JStr("terminal_id", InpTerminalId) + "," + JStr("ea_version", LRB_VERSION) + "," +
              JInt("time_utc", (long)TimeGMT()) + "," + JStr("symbol", _Symbol) + "," + JInt("magic", InpMagic) + "," +
              JNum("balance", AccountInfoDouble(ACCOUNT_BALANCE), 2) + "," + JNum("equity", AccountInfoDouble(ACCOUNT_EQUITY), 2) + "," +
              JNum("margin", AccountInfoDouble(ACCOUNT_MARGIN), 2) + "," + JNum("margin_level", AccountInfoDouble(ACCOUNT_MARGIN_LEVEL), 2) + "," +
              JStr("currency", AccountInfoString(ACCOUNT_CURRENCY)) + "," +
              JBool("is_demo", AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO) + "," +
              JBool("trading_allowed", g_trading_permitted) + "," + JBool("daily_halt", gd.daily_halt) + "," +
              JBool("dd_halt", gd.dd_halt) + "," + JStr("kill", KillName(gd.kill)) + "," +
              JNum("sod_equity", gd.sod_equity, 2) + "," + JNum("peak_equity", gd.peak_equity, 2) + "," +
              JNum("daily_loss_limit_pct", InpDailyLossPct, 2) + "," + JNum("max_drawdown_pct", InpMaxDrawdownPct, 2) + "," +
              JNum("risk_pct", InpRiskPct, 2) + "," + JNum("target_pct", InpTargetPct, 2) + "," +
              JStr("ai_mode", InpAIMode == AI_OFF ? "OFF" : "FILTER") + "," + JStr("ai_status", g_ai_status) + "," +
              JStr("last_ai_decision", g_last_ai) + "," + JStr("entries_blocked_reason", EntriesBlockedReason()) + "," +
              "\"errors\":[" + errs + "],\"basket\":" + basket + "}";
   WriteFileAtomic(g_dir + "status.json", s);
  }

//=================================================================== main loop
void Process()
  {
   MqlTick tk;
   if(!SymbolInfoTick(_Symbol, tk) || tk.bid <= 0 || tk.ask <= 0) return;
   UpdateKill();
   UpdateGuards(TimeGMT());
   UpdateBridgeStatus();
   if(g_phase == PHASE_RECONCILE)
     {
      if(!StartupReconcile()) return;
     }
   if(g.exists && (g.phase == PHASE_OPENING || g.phase == PHASE_ACTIVE || g.phase == PHASE_CLOSING)) ReconcilePositions();
   if(g.exists && g.phase == PHASE_ACTIVE)
     {
      PnL p;
      BasketPnL(p);
      g.max_float_loss = MathMin(g.max_float_loss, p.floating + p.open_swap + p.realized + p.commissions);
      g.max_gross = MathMax(g.max_gross, p.open_volume);
      string reason = ExitReason(tk, p);
      if(reason != "") BeginClose(reason);
     }
   if(g.exists && g.phase == PHASE_CLOSING) ProcessClose();
   else if(g.exists && g.phase == PHASE_ACTIVE) { MaintainLegApproval(tk); MaybeAddLeg(tk); }
   if(!g.exists && g_phase == PHASE_FLAT) MaybeOpen(tk);
   if(TimeGMT() - g_last_status >= InpStatusEverySec) { WriteStatus(); g_last_status = TimeGMT(); }
   if(g_dirty) SaveState();
  }

int OnInit()
  {
   if(!ParseProgression()) { Print("LRB: invalid progression / max positions"); return INIT_PARAMETERS_INCORRECT; }
   if(InpRiskPct <= 0 || InpRiskPct > 5 || InpTargetPct <= 0 || InpSizingSafety < 0 || InpSizingSafety >= 1)
     { Print("LRB: invalid risk parameters"); return INIT_PARAMETERS_INCORRECT; }
   if(InpAllowLive && InpDemoOnly) { Print("LRB: AllowLive requires DemoOnly=false"); return INIT_PARAMETERS_INCORRECT; }
   if(MQLInfoInteger(MQL_TESTER) && (InpAIMode != AI_OFF || InpLegApproval != LEGAPPR_OFF))
      Print("LRB: WARNING - the AI bridge cannot run in the Strategy Tester; AI requests will time out (entries blocked).");
   LoadParams();
   if(g_spec.tick_size <= 0 || g_spec.tick_value_loss <= 0 || g_spec.volume_step <= 0)
     { Print("LRB: symbol specification unavailable"); return INIT_FAILED; }
   g_dir = LrbDir(InpMagic);
   FolderCreate("LRB", FILE_COMMON);
   FolderCreate(StringSubstr(g_dir, 0, StringLen(g_dir) - 1), FILE_COMMON);
   g_trade.SetExpertMagicNumber((ulong)InpMagic);
   g_trade.SetDeviationInPoints(InpDeviationPts);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);
   g.exists = false;
   gd.day_key = -1;
   gd.kill = (int)InpKillOnStart;
   g_leg_dec.valid = false;
   if(LoadState()) Print("LRB: local state loaded - will be verified against broker positions");
   if(InpResetDrawdownHalt) { gd.dd_halt = false; gd.peak_equity = AccountInfoDouble(ACCOUNT_EQUITY); Log("DD_HALT_RESET"); }
   if(InpKillOnStart != KILL_NONE) GlobalVariableSet("LRB_KILL_" + IntegerToString(InpMagic), (double)InpKillOnStart);
   g_block_init = AccountPermission();
   g_trading_permitted = (g_block_init == "");
   if(!g_trading_permitted) Print("LRB: NEW ENTRIES DISABLED: ", g_block_init, " (existing basket positions are still managed)");
   g_phase = PHASE_RECONCILE;   // never trade before reconciling with actual broker positions
   EventSetMillisecondTimer(500);
   Log("INIT", StringFormat("v%s symbol=%s magic=%I64d demo=%d hedging=%d permitted=%d", LRB_VERSION, _Symbol, InpMagic,
                           AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO,
                           AccountInfoInteger(ACCOUNT_MARGIN_MODE) == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING, g_trading_permitted));
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   SaveState();
   Log("DEINIT", IntegerToString(reason));
  }

void OnTick()  { Process(); }
void OnTimer() { Process(); }
//+------------------------------------------------------------------+
