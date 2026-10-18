//+------------------------------------------------------------------+
//| LRB_ExportTicks.mq5 - export bid/ask ticks to CSV (UTC epoch ms)  |
//| Output: <Common>\Files\LRB\ticks_<symbol>_<from>_<to>.csv         |
//| Columns: time_msc_utc,bid,ask  (read by lrb-backtest)            |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input datetime InpFrom    = D'2025.01.01 00:00';  // Server time
input datetime InpTo      = D'2025.07.01 00:00';  // Server time
input int      InpChunkDays = 7;

void OnStart()
  {
   long off_ms = (long)MathRound((double)(TimeTradeServer() - TimeGMT()) / 900.0) * 900 * 1000;
   FolderCreate("LRB", FILE_COMMON);
   string fname = StringFormat("LRB\\ticks_%s_%s_%s.csv", _Symbol, TimeToString(InpFrom, TIME_DATE), TimeToString(InpTo, TIME_DATE));
   StringReplace(fname, ".", "");
   StringReplace(fname, "csv", ".csv");
   int h = FileOpen(fname, FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI);
   if(h == INVALID_HANDLE) { Print("cannot open ", fname); return; }
   FileWriteString(h, "time_msc_utc,bid,ask\r\n");
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   long total = 0;
   double last_bid = 0, last_ask = 0;
   for(datetime t = InpFrom; t < InpTo; t += InpChunkDays * 86400)
     {
      datetime t2 = (datetime)MathMin((long)InpTo, (long)t + InpChunkDays * 86400);
      MqlTick ticks[];
      int n = CopyTicksRange(_Symbol, ticks, COPY_TICKS_INFO, (ulong)t * 1000, (ulong)t2 * 1000 - 1);
      if(n < 0) { Print("CopyTicksRange failed: ", GetLastError()); continue; }
      for(int i = 0; i < n; i++)
        {
         if((ticks[i].flags & TICK_FLAG_BID) != 0) last_bid = ticks[i].bid;
         if((ticks[i].flags & TICK_FLAG_ASK) != 0) last_ask = ticks[i].ask;
         if(last_bid <= 0 || last_ask <= 0) continue;
         FileWriteString(h, StringFormat("%I64d,%s,%s\r\n", ticks[i].time_msc - off_ms,
                                         DoubleToString(last_bid, digits), DoubleToString(last_ask, digits)));
         total++;
        }
      Print("exported up to ", TimeToString(t2), " total=", total);
     }
   FileClose(h);
   Print("done: ", total, " ticks -> Common\\Files\\", fname,
         " (NOTE: UTC conversion uses the CURRENT server offset; DST changes inside the range shift times by 1h)");
  }
