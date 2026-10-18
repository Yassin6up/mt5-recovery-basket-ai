//+------------------------------------------------------------------+
//| Minimal JSON helpers for the flat service response.              |
//| LIMITATION: not a general parser. Reads top-level scalar values  |
//| by key; keys we read must be unique in the document (true for    |
//| the service's DecisionResponse).                                 |
//+------------------------------------------------------------------+
#ifndef LRB_JSON_MQH
#define LRB_JSON_MQH

string JsonEscape(const string s)
  {
   string r = s;
   StringReplace(r, "\\", "\\\\");
   StringReplace(r, "\"", "\\\"");
   StringReplace(r, "\n", "\\n");
   StringReplace(r, "\r", "\\r");
   StringReplace(r, "\t", "\\t");
   return r;
  }

string JStr(const string k, const string v)   { return "\"" + k + "\":\"" + JsonEscape(v) + "\""; }
string JNum(const string k, const double v, const int digits=8) { return "\"" + k + "\":" + DoubleToString(v, digits); }
string JInt(const string k, const long v)     { return "\"" + k + "\":" + IntegerToString(v); }
string JBool(const string k, const bool v)    { return "\"" + k + "\":" + (v ? "true" : "false"); }

// Position of the first character of the value for "key", or -1.
int JsonValuePos(const string json, const string key)
  {
   string pat = "\"" + key + "\"";
   int from = 0;
   while(true)
     {
      int p = StringFind(json, pat, from);
      if(p < 0) return -1;
      int i = p + StringLen(pat);
      int n = StringLen(json);
      while(i < n && (StringGetCharacter(json, i) == ' ' || StringGetCharacter(json, i) == '\t')) i++;
      if(i < n && StringGetCharacter(json, i) == ':')
        {
         i++;
         while(i < n && (StringGetCharacter(json, i) == ' ' || StringGetCharacter(json, i) == '\t')) i++;
         return i;
        }
      from = p + 1;
     }
   return -1;
  }

bool JsonGetString(const string json, const string key, string &out)
  {
   int i = JsonValuePos(json, key);
   if(i < 0 || StringGetCharacter(json, i) != '"') return false;
   int n = StringLen(json);
   string r = "";
   for(int j = i + 1; j < n; j++)
     {
      ushort c = StringGetCharacter(json, j);
      if(c == '\\' && j + 1 < n) { j++; r += ShortToString(StringGetCharacter(json, j)); continue; }
      if(c == '"') { out = r; return true; }
      r += ShortToString(c);
     }
   return false;
  }

bool JsonGetNumber(const string json, const string key, double &out)
  {
   int i = JsonValuePos(json, key);
   if(i < 0) return false;
   int n = StringLen(json);
   int j = i;
   while(j < n)
     {
      ushort c = StringGetCharacter(json, j);
      if((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E') j++;
      else break;
     }
   if(j == i) return false;
   out = StringToDouble(StringSubstr(json, i, j - i));
   return true;
  }

// Returns the raw array text (e.g. ["A","B"]) without brackets, joined by '|'.
bool JsonGetStringArray(const string json, const string key, string &joined)
  {
   int i = JsonValuePos(json, key);
   if(i < 0 || StringGetCharacter(json, i) != '[') return false;
   int e = StringFind(json, "]", i);
   if(e < 0) return false;
   string inner = StringSubstr(json, i + 1, e - i - 1);
   StringReplace(inner, "\"", "");
   StringReplace(inner, ",", "|");
   joined = inner;
   return true;
  }

#endif
