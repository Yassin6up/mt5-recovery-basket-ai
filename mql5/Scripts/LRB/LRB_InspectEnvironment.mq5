//+------------------------------------------------------------------+
//| LRB_InspectEnvironment.mq5                                       |
//| Read-only. Writes <Common>\Files\LRB\inspect_<symbol>.json with  |
//| account, symbol, session, margin and tick-history facts needed   |
//| before any demo deployment. Places NO orders.                    |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict
#include <LRB\Json.mqh>

input int InpTickDays = 30;   // How many days of tick history to probe

string DayName(int d)
  {
   string n[7] = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"};
   return n[d];
  }

void OnStart()
  {
   string s = _Symbol;
   string j = "{";
   j += JStr("generated_utc", TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS)) + ",";
   j += JStr("terminal_build", IntegerToString(TerminalInfoInteger(TERMINAL_BUILD))) + ",";
   j += JStr("terminal_path", TerminalInfoString(TERMINAL_PATH)) + ",";
   j += JStr("common_data_path", TerminalInfoString(TERMINAL_COMMONDATA_PATH)) + ",";
   j += JBool("connected", (TerminalInfoInteger(TERMINAL_CONNECTED) != 0)) + ",";
   j += JBool("algo_trading_enabled", (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0)) + ",";
   j += JStr("broker_company", AccountInfoString(ACCOUNT_COMPANY)) + ",";
   j += JStr("broker_server", AccountInfoString(ACCOUNT_SERVER)) + ",";
   j += JStr("account_currency", AccountInfoString(ACCOUNT_CURRENCY)) + ",";
   long tm = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   j += JStr("account_trade_mode", tm == ACCOUNT_TRADE_MODE_DEMO ? "DEMO" : (tm == ACCOUNT_TRADE_MODE_CONTEST ? "CONTEST" : "REAL")) + ",";
   long mm = AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   j += JStr("margin_mode", mm == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING ? "RETAIL_HEDGING" : (mm == ACCOUNT_MARGIN_MODE_RETAIL_NETTING ? "RETAIL_NETTING" : "EXCHANGE")) + ",";
   j += JBool("hedging_supported", mm == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING) + ",";
   j += JInt("leverage", AccountInfoInteger(ACCOUNT_LEVERAGE)) + ",";
   j += JNum("margin_call_level", AccountInfoDouble(ACCOUNT_MARGIN_SO_CALL), 2) + ",";
   j += JNum("stop_out_level", AccountInfoDouble(ACCOUNT_MARGIN_SO_SO), 2) + ",";
   long som = AccountInfoInteger(ACCOUNT_MARGIN_SO_MODE);
   j += JStr("stop_out_mode", som == ACCOUNT_STOPOUT_MODE_PERCENT ? "PERCENT" : "MONEY") + ",";
   j += JNum("balance", AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";
   j += JNum("equity", AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",";
   j += JInt("server_utc_offset_sec", (long)(TimeTradeServer() - TimeGMT())) + ",";
   // symbol specification
   j += "\"symbol\":{";
   j += JStr("name", s) + "," + JStr("description", SymbolInfoString(s, SYMBOL_DESCRIPTION)) + ",";
   j += JInt("digits", SymbolInfoInteger(s, SYMBOL_DIGITS)) + ",";
   j += JNum("point", SymbolInfoDouble(s, SYMBOL_POINT), 10) + ",";
   j += JNum("contract_size", SymbolInfoDouble(s, SYMBOL_TRADE_CONTRACT_SIZE), 2) + ",";
   j += JNum("tick_size", SymbolInfoDouble(s, SYMBOL_TRADE_TICK_SIZE), 10) + ",";
   j += JNum("tick_value", SymbolInfoDouble(s, SYMBOL_TRADE_TICK_VALUE), 6) + ",";
   j += JNum("tick_value_profit", SymbolInfoDouble(s, SYMBOL_TRADE_TICK_VALUE_PROFIT), 6) + ",";
   j += JNum("tick_value_loss", SymbolInfoDouble(s, SYMBOL_TRADE_TICK_VALUE_LOSS), 6) + ",";
   j += JNum("volume_min", SymbolInfoDouble(s, SYMBOL_VOLUME_MIN), 4) + ",";
   j += JNum("volume_max", SymbolInfoDouble(s, SYMBOL_VOLUME_MAX), 4) + ",";
   j += JNum("volume_step", SymbolInfoDouble(s, SYMBOL_VOLUME_STEP), 4) + ",";
   j += JNum("volume_limit", SymbolInfoDouble(s, SYMBOL_VOLUME_LIMIT), 4) + ",";
   j += JNum("margin_initial", SymbolInfoDouble(s, SYMBOL_MARGIN_INITIAL), 4) + ",";
   j += JNum("margin_hedged", SymbolInfoDouble(s, SYMBOL_MARGIN_HEDGED), 4) + ",";
   j += JBool("margin_hedged_use_leg", (SymbolInfoInteger(s, SYMBOL_MARGIN_HEDGED_USE_LEG) != 0)) + ",";
   j += JInt("calc_mode", SymbolInfoInteger(s, SYMBOL_TRADE_CALC_MODE)) + ",";
   j += JInt("trade_mode", SymbolInfoInteger(s, SYMBOL_TRADE_MODE)) + ",";
   j += JInt("execution_mode", SymbolInfoInteger(s, SYMBOL_TRADE_EXEMODE)) + ",";
   j += JInt("filling_mode_flags", SymbolInfoInteger(s, SYMBOL_FILLING_MODE)) + ",";
   j += JInt("stops_level", SymbolInfoInteger(s, SYMBOL_TRADE_STOPS_LEVEL)) + ",";
   j += JInt("freeze_level", SymbolInfoInteger(s, SYMBOL_TRADE_FREEZE_LEVEL)) + ",";
   j += JInt("spread_current_points", SymbolInfoInteger(s, SYMBOL_SPREAD)) + ",";
   j += JBool("spread_float", (SymbolInfoInteger(s, SYMBOL_SPREAD_FLOAT) != 0)) + ",";
   j += JNum("swap_long", SymbolInfoDouble(s, SYMBOL_SWAP_LONG), 4) + ",";
   j += JNum("swap_short", SymbolInfoDouble(s, SYMBOL_SWAP_SHORT), 4) + ",";
   j += JInt("swap_mode", SymbolInfoInteger(s, SYMBOL_SWAP_MODE)) + ",";
   j += JInt("swap_rollover3days", SymbolInfoInteger(s, SYMBOL_SWAP_ROLLOVER3DAYS)) + ",";
   double m1 = 0;
   MqlTick tk;
   SymbolInfoTick(s, tk);
   OrderCalcMargin(ORDER_TYPE_BUY, s, 1.0, tk.ask, m1);
   j += JNum("margin_for_1_lot_buy", m1, 2) + ",";
   j += JStr("currency_base", SymbolInfoString(s, SYMBOL_CURRENCY_BASE)) + ",";
   j += JStr("currency_profit", SymbolInfoString(s, SYMBOL_CURRENCY_PROFIT)) + ",";
   j += JStr("currency_margin", SymbolInfoString(s, SYMBOL_CURRENCY_MARGIN));
   j += "},";
   // trading sessions (server time)
   j += "\"trade_sessions_server_time\":{";
   for(int d = 0; d < 7; d++)
     {
      j += (d ? "," : "") + "\"" + DayName(d) + "\":[";
      datetime f, t;
      for(uint i = 0; i < 10 && SymbolInfoSessionTrade(s, (ENUM_DAY_OF_WEEK)d, i, f, t); i++)
         j += (i ? "," : "") + "\"" + TimeToString(f, TIME_MINUTES) + "-" + TimeToString(t, TIME_MINUTES) + "\"";
      j += "]";
     }
   j += "},";
   // tick history availability (bid/ask)
   MqlTick ticks[];
   ulong from = (ulong)(TimeCurrent() - InpTickDays * 86400) * 1000;
   int n = CopyTicksRange(s, ticks, COPY_TICKS_INFO, from, (ulong)TimeCurrent() * 1000);
   j += JInt("tick_history_days_requested", InpTickDays) + ",";
   j += JInt("tick_history_count", n) + ",";
   if(n > 0)
     {
      j += JStr("tick_history_first", TimeToString(ticks[0].time, TIME_DATE | TIME_SECONDS)) + ",";
      j += JStr("tick_history_last", TimeToString(ticks[n - 1].time, TIME_DATE | TIME_SECONDS)) + ",";
     }
   j += JInt("tick_history_error", GetLastError());
   j += "}";
   FolderCreate("LRB", FILE_COMMON);
   string fname = "LRB\\inspect_" + s + ".json";
   int h = FileOpen(fname, FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI);
   if(h != INVALID_HANDLE) { FileWriteString(h, j); FileClose(h); }
   Print("LRB inspection written to Common\\Files\\", fname);
   Print(j);
  }
