//+------------------------------------------------------------------+
//| Limited Recovery Basket AI - shared definitions                  |
//| Research/demo software. No profit guarantee.                     |
//+------------------------------------------------------------------+
#ifndef LRB_DEFINES_MQH
#define LRB_DEFINES_MQH

#define LRB_VERSION      "0.1.0"
#define LRB_MAX_LEGS     10
#define LRB_STATE_VER    1

enum ENUM_LRB_PHASE   { PHASE_RECONCILE=0, PHASE_FLAT=1, PHASE_OPENING=2, PHASE_ACTIVE=3, PHASE_CLOSING=4 };
enum ENUM_LRB_LEG     { LEG_PLANNED=0, LEG_SENT=1, LEG_FILLED=2, LEG_PARTIAL=3, LEG_FAILED=4, LEG_SKIPPED=5 };
enum ENUM_LRB_KILL    { KILL_NONE=0, KILL_HALT=1, KILL_FLATTEN=2 };
enum ENUM_LRB_AIMODE  { AI_OFF=0, AI_FILTER=1 };
enum ENUM_LRB_LEGAPPR { LEGAPPR_OFF=0, LEGAPPR_REQUIRED=1 };

string PhaseName(int p)
  {
   switch(p)
     {
      case PHASE_RECONCILE: return "RECONCILE";
      case PHASE_FLAT:      return "FLAT";
      case PHASE_OPENING:   return "OPENING";
      case PHASE_ACTIVE:    return "ACTIVE";
      case PHASE_CLOSING:   return "CLOSING";
     }
   return "?";
  }

string LegStateName(int s)
  {
   switch(s)
     {
      case LEG_PLANNED: return "PLANNED";
      case LEG_SENT:    return "SENT";
      case LEG_FILLED:  return "FILLED";
      case LEG_PARTIAL: return "PARTIAL";
      case LEG_FAILED:  return "FAILED";
      case LEG_SKIPPED: return "SKIPPED";
     }
   return "?";
  }

string KillName(int k) { return k==KILL_HALT ? "HALT" : (k==KILL_FLATTEN ? "FLATTEN" : "NONE"); }

struct LegRecord
  {
   int      index;          // 1-based
   int      side;           // +1 BUY, -1 SELL
   double   planned_volume;
   int      state;
   int      attempts;
   ulong    ticket;         // position ticket (== position identifier on hedging accounts)
   double   filled_volume;
   double   fill_price;
   datetime sent_time;      // UTC
   string   request_id;
   bool     closed;
  };

struct Levels
  {
   double upper;
   double lower;
   double stop_low;
   double stop_high;
   double distance;
   double stop_distance;
  };

struct AIDecisionRec
  {
   bool     valid;
   string   request_id;
   string   kind;
   int      leg_index;
   string   decision;
   string   direction;
   string   model_version;
   double   data_ts;
   double   expires_at;
   string   reasons;
  };

// Folder in <Common>\Files shared by the EA, the bridge EA and (optionally) the Python service.
string LrbDir(long magic) { return "LRB\\" + IntegerToString(magic) + "\\"; }

#endif
