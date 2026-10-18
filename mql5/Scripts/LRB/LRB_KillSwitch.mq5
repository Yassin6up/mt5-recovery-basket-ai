//+------------------------------------------------------------------+
//| LRB_KillSwitch.mq5 - manual kill switch for LRB_EA               |
//| HALT    : no new baskets/legs; existing basket keeps its exits.  |
//| FLATTEN : close every basket position now and halt.              |
//| NONE    : clear (only after review).                             |
//| Sets terminal global variable LRB_KILL_<magic> AND the file      |
//| <Common>\Files\LRB\<magic>\KILL (file wins if present: delete it |
//| to clear a file-based kill).                                     |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict
#include <LRB\Defines.mqh>

input long          InpMagic = 26101801;
input ENUM_LRB_KILL InpMode  = KILL_FLATTEN;

void OnStart()
  {
   string gv = "LRB_KILL_" + IntegerToString(InpMagic);
   GlobalVariableSet(gv, (double)InpMode);
   GlobalVariablesFlush();
   string f = LrbDir(InpMagic) + "KILL";
   if(InpMode == KILL_NONE) FileDelete(f, FILE_COMMON);
   else
     {
      int h = FileOpen(f, FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI);
      if(h != INVALID_HANDLE) { FileWriteString(h, KillName(InpMode)); FileClose(h); }
     }
   Print("LRB kill switch for magic ", InpMagic, " set to ", KillName(InpMode));
  }
