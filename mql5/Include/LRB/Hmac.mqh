//+------------------------------------------------------------------+
//| HMAC-SHA256 built on CryptEncode(CRYPT_HASH_SHA256).             |
//| Canonical strings match python/src/lrb/service/security.py       |
//+------------------------------------------------------------------+
#ifndef LRB_HMAC_MQH
#define LRB_HMAC_MQH

bool Sha256(const uchar &data[], uchar &out[])
  {
   uchar nokey[];
   return CryptEncode(CRYPT_HASH_SHA256, data, nokey, out) == 32;
  }

string BytesToHex(const uchar &b[])
  {
   string s = "";
   for(int i = 0; i < ArraySize(b); i++) s += StringFormat("%02x", b[i]);
   return s;
  }

// ASCII/UTF-8 string -> bytes WITHOUT the terminating zero.
void StrBytes(const string s, uchar &out[])
  {
   ArrayResize(out, 0);
   int n = StringToCharArray(s, out, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 0 && out[n - 1] == 0) ArrayResize(out, n - 1);
  }

string Sha256Hex(const uchar &data[])
  {
   uchar h[];
   if(!Sha256(data, h)) return "";
   return BytesToHex(h);
  }

string HmacSha256Hex(const uchar &key_in[], const uchar &msg[])
  {
   const int B = 64;
   uchar key[];
   if(ArraySize(key_in) > B) { if(!Sha256(key_in, key)) return ""; }
   else ArrayCopy(key, key_in);
   int klen = ArraySize(key);
   ArrayResize(key, B);
   for(int i = klen; i < B; i++) key[i] = 0;

   uchar inner[], outer[];
   int m = ArraySize(msg);
   ArrayResize(inner, B + m);
   for(int i = 0; i < B; i++) inner[i] = (uchar)(key[i] ^ 0x36);
   for(int i = 0; i < m; i++) inner[B + i] = msg[i];
   uchar ih[];
   if(!Sha256(inner, ih)) return "";

   ArrayResize(outer, B + 32);
   for(int i = 0; i < B; i++) outer[i] = (uchar)(key[i] ^ 0x5c);
   for(int i = 0; i < 32; i++) outer[B + i] = ih[i];
   uchar oh[];
   if(!Sha256(outer, oh)) return "";
   return BytesToHex(oh);
  }

string HmacSha256HexStr(const string key, const string msg)
  {
   uchar k[], m[];
   StrBytes(key, k);
   StrBytes(msg, m);
   return HmacSha256Hex(k, m);
  }

// Constant-time comparison of two hex strings.
bool HexEquals(const string a, const string b)
  {
   if(StringLen(a) != StringLen(b) || StringLen(a) == 0) return false;
   int diff = 0;
   for(int i = 0; i < StringLen(a); i++) diff |= (StringGetCharacter(a, i) ^ StringGetCharacter(b, i));
   return diff == 0;
  }

// Self-test against RFC 4231 test case 2 ("Jefe"). Call from OnInit.
bool HmacSelfTest()
  {
   string got = HmacSha256HexStr("Jefe", "what do ya want for nothing?");
   return got == "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843";
  }

#endif
