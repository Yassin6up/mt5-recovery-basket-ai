//+------------------------------------------------------------------+
//| LRB_AIBridge.mq5 - network bridge for LRB_EA (NO trading)        |
//|                                                                  |
//| Attach to a SEPARATE chart (any symbol). Each chart's EA runs in |
//| its own thread, so a slow/blocked WebRequest here can never      |
//| delay LRB_EA's local risk checks or exits.                       |
//|                                                                  |
//| - Reads   <Common>\Files\LRB\<magic>\req_entry.json / req_leg.json|
//| - POSTs   them to the decision service with HMAC-SHA256 auth     |
//| - Verifies the response signature + request id                   |
//| - Writes  resp_<request_id>.json (or an {"error":..} file)       |
//| - Posts   status.json as telemetry for the read-only dashboard   |
//| This EA has no order-execution code.                             |
//|                                                                  |
//| Setup: Tools > Options > Expert Advisors > "Allow WebRequest for |
//| listed URL" and add the service URL (e.g. http://127.0.0.1:8787).|
//+------------------------------------------------------------------+
#property copyright "Limited Recovery Basket AI - research software"
#property version   "0.10"
#property strict

#include <LRB\Defines.mqh>
#include <LRB\Json.mqh>
#include <LRB\Hmac.mqh>

input long   InpMagic             = 26101801;                 // Must match LRB_EA
input string InpServiceUrl        = "http://127.0.0.1:8787";  // Loopback HTTP, or https:// for remote
input string InpKeyId             = "ea1";
input string InpSecretFile        = "LRB\\hmac_secret.txt";    // In <Common>\Files, ACL-restricted
input int    InpTimeoutMs         = 3000;                     // Short timeout
input int    InpTelemetryEverySec = 10;
input int    InpPollMs            = 500;

string   g_secret = "";
string   g_dir;
string   g_last_rid[2];
datetime g_last_tel = 0;
long     g_tel_seq = 0;

bool ReadText(const string name, string &out)
  {
   if(!FileIsExist(name, FILE_COMMON)) return false;
   int h = FileOpen(name, FILE_READ | FILE_TXT | FILE_COMMON | FILE_ANSI | FILE_SHARE_READ);
   if(h == INVALID_HANDLE) return false;
   out = "";
   while(!FileIsEnding(h)) { string l = FileReadString(h); out += (out == "" ? "" : "\n") + l; }
   FileClose(h);
   return true;
  }

bool WriteAtomic(const string name, const string content)
  {
   string tmp = name + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI);
   if(h == INVALID_HANDLE) return false;
   FileWriteString(h, content);
   FileClose(h);
   return FileMove(tmp, FILE_COMMON, name, FILE_COMMON | FILE_REWRITE);
  }

string HeaderValue(const string headers, const string name)
  {
   string low = headers;
   StringToLower(low);
   string key = name;
   StringToLower(key);
   int p = StringFind(low, key + ":");
   if(p < 0) return "";
   int s = p + StringLen(key) + 1;
   int e = StringFind(headers, "\r\n", s);
   if(e < 0) e = StringFind(headers, "\n", s);
   if(e < 0) e = StringLen(headers);
   string v = StringSubstr(headers, s, e - s);
   StringTrimLeft(v);
   StringTrimRight(v);
   return v;
  }

// Signed POST. Returns HTTP status (or -1). Verifies the response signature for 200 responses.
int SignedPost(const string path, const string rid, const string body, string &resp_body, string &err)
  {
   string ts = IntegerToString((long)TimeGMT());
   uchar bbytes[];
   StrBytes(body, bbytes);
   string canon = "POST\n" + path + "\n" + ts + "\n" + rid + "\n" + Sha256Hex(bbytes);
   string sig = HmacSha256HexStr(g_secret, canon);
   string headers = "Content-Type: application/json\r\nX-LRB-Key-Id: " + InpKeyId + "\r\nX-LRB-Request-Id: " + rid +
                    "\r\nX-LRB-Timestamp: " + ts + "\r\nX-LRB-Signature: " + sig + "\r\n";
   char data[], result[];
   int n = ArraySize(bbytes);
   ArrayResize(data, n);
   for(int i = 0; i < n; i++) data[i] = (char)bbytes[i];
   string rheaders;
   ResetLastError();
   int status = WebRequest("POST", InpServiceUrl + path, headers, InpTimeoutMs, data, result, rheaders);
   if(status == -1) { err = "WEBREQUEST_FAILED err=" + IntegerToString(GetLastError()) + " (URL allowed in Options?)"; return -1; }
   uchar ub[];
   int m = ArraySize(result);
   ArrayResize(ub, m);
   for(int i = 0; i < m; i++) ub[i] = (uchar)result[i];
   resp_body = CharArrayToString(ub, 0, m, CP_UTF8);
   if(status != 200) { err = "HTTP_" + IntegerToString(status); return status; }
   string rts = HeaderValue(rheaders, "X-LRB-Timestamp");
   string rsig = HeaderValue(rheaders, "X-LRB-Signature");
   string rrid = HeaderValue(rheaders, "X-LRB-Request-Id");
   if(rrid != rid) { err = "RESPONSE_REQUEST_ID_MISMATCH"; return -2; }
   if(MathAbs((double)((long)TimeGMT() - StringToInteger(rts))) > 30) { err = "RESPONSE_TIMESTAMP_SKEW"; return -2; }
   string rcanon = "200\n" + rid + "\n" + rts + "\n" + Sha256Hex(ub);
   if(!HexEquals(HmacSha256HexStr(g_secret, rcanon), rsig)) { err = "RESPONSE_SIGNATURE_INVALID"; return -2; }
   return 200;
  }

void ProcessRequest(const int slot, const string fname)
  {
   string body;
   if(!ReadText(g_dir + fname, body)) return;
   FileDelete(g_dir + fname, FILE_COMMON);
   string rid;
   if(!JsonGetString(body, "request_id", rid) || rid == "") return;
   if(rid == g_last_rid[slot]) return;                 // never send the same request twice
   g_last_rid[slot] = rid;
   string resp, err = "";
   int st = SignedPost("/v1/decision", rid, body, resp, err);
   string out;
   if(st == 200) out = resp;
   else out = "{" + JStr("request_id", rid) + "," + JStr("error", err) + "}";   // EA treats as unavailable
   if(st != 200) Print("LRB bridge: decision ", rid, " failed: ", err);
   WriteAtomic(g_dir + "resp_" + rid + ".json", out);
  }

void PostTelemetry()
  {
   string body;
   if(!ReadText(g_dir + "status.json", body)) return;
   g_tel_seq++;
   string rid = StringFormat("T-%I64d-%I64d-%I64d", InpMagic % 100000, (long)TimeGMT(), g_tel_seq % 1000);
   string resp, err;
   if(SignedPost("/v1/telemetry", rid, body, resp, err) != 200) Print("LRB bridge: telemetry failed: ", err);
  }

int OnInit()
  {
   if(!HmacSelfTest()) { Print("LRB bridge: HMAC self-test FAILED"); return INIT_FAILED; }
   string s;
   if(!ReadText(InpSecretFile, s)) { Print("LRB bridge: secret file not found in Common\\Files: ", InpSecretFile); return INIT_FAILED; }
   StringTrimLeft(s);
   StringTrimRight(s);
   if(StringLen(s) < 32) { Print("LRB bridge: secret shorter than 32 chars"); return INIT_FAILED; }
   g_secret = s;
   if(StringFind(InpServiceUrl, "http://") == 0 && StringFind(InpServiceUrl, "http://127.0.0.1") != 0 &&
      StringFind(InpServiceUrl, "http://localhost") != 0)
     { Print("LRB bridge: plain HTTP is only allowed to loopback; use https:// for remote services"); return INIT_PARAMETERS_INCORRECT; }
   g_dir = LrbDir(InpMagic);
   g_last_rid[0] = "";
   g_last_rid[1] = "";
   EventSetMillisecondTimer(MathMax(100, InpPollMs));
   Print("LRB bridge started for magic ", InpMagic, " -> ", InpServiceUrl, " (secret loaded, not logged)");
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason) { EventKillTimer(); }

void OnTimer()
  {
   WriteAtomic(g_dir + "bridge_heartbeat.txt", IntegerToString((long)TimeGMT()));
   ProcessRequest(0, "req_entry.json");
   ProcessRequest(1, "req_leg.json");
   if(TimeGMT() - g_last_tel >= InpTelemetryEverySec) { g_last_tel = TimeGMT(); PostTelemetry(); }
  }

void OnTick() {}
//+------------------------------------------------------------------+
