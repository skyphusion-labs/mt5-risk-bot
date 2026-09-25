#property strict
#property copyright "skyphusion"
#property description "File mailbox for straightedge MT4 adapter. FILE_COMMON."

input int Slippage = 30;
input int ReconcileMagic = 0;   // 0 = report every position that has no stop

bool gBusy = false;

// Report every position that is open with no stop loss. A position that
// predates this session is NOT adopted: this Expert only reports it, so a
// human decides. Silence here is how an orphan from a previous session
// becomes permanent.
void ReportUnmanaged()
{
   int total = OrdersTotal();
   int found = 0;
   for(int i=0; i<total; i++)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderType() > OP_SELL)
         continue;
      if(ReconcileMagic != 0 && OrderMagicNumber() != ReconcileMagic)
         continue;
      if(OrderStopLoss() != 0)
         continue;
      found++;
      Print("mt4riskbot UNMANAGED ticket=", OrderTicket(),
            " symbol=", OrderSymbol(),
            " type=", OrderType(),
            " lots=", DoubleToString(OrderLots(), 2),
            " magic=", OrderMagicNumber(),
            " opened=", TimeToString(OrderOpenTime()),
            " -- open with NO STOP, not adopted by this session");
   }
   if(found > 0)
      Print("mt4riskbot ", found,
            " position(s) open with NO STOP at startup. Close or protect them in the terminal.");
}

int OnInit()
{
   if(!EventSetMillisecondTimer(100))
      EventSetTimer(1);
   ReportUnmanaged();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   Process();
}

void OnTick()
{
   Process();
}

void Process()
{
   if(gBusy)
      return;
   if(!FileIsExist("mt4_risk_bot.req", FILE_COMMON))
      return;
   gBusy = true;
   int share = FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE;
   int h = FileOpen("mt4_risk_bot.req", share);
   if(h == INVALID_HANDLE)
   {
      gBusy = false;
      return;
   }
   string body = "";
   while(!FileIsEnding(h))
      body = body + FileReadString(h) + "\n";
   FileClose(h);
   FileDelete("mt4_risk_bot.req", FILE_COMMON);
   string reply = Handle(body);
   int w = FileOpen("mt4_risk_bot.res.tmp", FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(w != INVALID_HANDLE)
   {
      FileWriteString(w, reply);
      FileFlush(w);
      FileClose(w);
      FileDelete("mt4_risk_bot.res", FILE_COMMON);
      FileMove("mt4_risk_bot.res.tmp", FILE_COMMON, "mt4_risk_bot.res", FILE_COMMON);
   }
   gBusy = false;
}

string KV(string body, string key)
{
   string lines[];
   int n = StringSplit(body, StringGetCharacter("\n", 0), lines);
   string prefix = key + "=";
   int plen = StringLen(prefix);
   for(int i=0; i<n; i++)
   {
      string line = lines[i];
      if(StringLen(line) > 0 && StringGetCharacter(line, 0) == '\r')
         line = StringSubstr(line, 1);
      int last = StringLen(line) - 1;
      if(last >= 0 && StringGetCharacter(line, last) == '\r')
         line = StringSubstr(line, 0, last);
      if(StringFind(line, prefix) == 0)
         return StringSubstr(line, plen);
   }
   return "";
}

string Ok(string id)
{
   return "id=" + id + "\nok=1\n";
}

// Strip the characters the wire uses as structure. The Python side does the
// same on the way out (`_wire`, mt4_live.py:93-95); doing it on only one side
// is what let a broker comment containing a pipe shift every field after it.
// Both ends sanitise and neither trusts the other.
// Framing characters only: `_wire` also forces ASCII, which MQL4 has no cheap
// equivalent for. That asymmetry is stated in docs/MT4.md.
string Wire(string s)
{
   StringReplace(s, "\r", " ");
   StringReplace(s, "\n", " ");
   StringReplace(s, "|", "/");
   return s;
}

string Fail(string id, int err, string msg)
{
   return "id=" + id + "\nok=0\nretcode=" + IntegerToString(err) + "\nerror=" + msg + "\n";
}

// Every failure from a trade handler states what it left behind.
// survivor=0 means the book was checked and nothing survived. A positive
// value is a live ticket the desk must deal with. The field is always
// present, so an adapter can tell an Expert that answered 0 from an
// Expert too old to answer at all.
string FailTrade(string id, int err, string msg, int survivor)
{
   return Fail(id, err, msg) + "survivor_ticket=" + IntegerToString(survivor) + "\n";
}

int Tf(string name)
{
   if(name == "M1" || name == "1") return PERIOD_M1;
   if(name == "M5" || name == "5") return PERIOD_M5;
   if(name == "M15" || name == "15") return PERIOD_M15;
   if(name == "M30" || name == "30") return PERIOD_M30;
   if(name == "H1" || name == "60") return PERIOD_H1;
   if(name == "H4" || name == "240") return PERIOD_H4;
   if(name == "D1" || name == "1440") return PERIOD_D1;
   if(name == "W1") return PERIOD_W1;
   if(name == "MN1") return PERIOD_MN1;
   return PERIOD_H1;
}

int PendingType(string side, string kind)
{
   if(kind == "limit")
      return (side == "buy") ? OP_BUYLIMIT : OP_SELLLIMIT;
   return (side == "buy") ? OP_BUYSTOP : OP_SELLSTOP;
}

string KindOf(int typ)
{
   if(typ == OP_BUYLIMIT || typ == OP_SELLLIMIT) return "limit";
   if(typ == OP_BUYSTOP || typ == OP_SELLSTOP) return "stop";
   return "";
}

string SideOf(int typ)
{
   if(typ == OP_BUY || typ == OP_BUYLIMIT || typ == OP_BUYSTOP) return "buy";
   return "sell";
}

string ClipComment(string c)
{
   if(StringLen(c) <= 31)
      return c;
   return StringSubstr(c, 0, 31);
}

bool VolumeOk(string sym, double vol)
{
   double minlot = MarketInfo(sym, MODE_MINLOT);
   double maxlot = MarketInfo(sym, MODE_MAXLOT);
   double step = MarketInfo(sym, MODE_LOTSTEP);
   if(vol + 1e-8 < minlot) return false;
   if(vol - 1e-8 > maxlot) return false;
   if(step <= 0) return true;
   double steps = vol / step;
   return MathAbs(steps - MathRound(steps)) < 1e-6;
}

bool StopsOk(string sym, int typ, double price, double sl, double tp)
{
   double point = MarketInfo(sym, MODE_POINT);
   double level = MarketInfo(sym, MODE_STOPLEVEL) * point;
   RefreshRates();
   double bid = MarketInfo(sym, MODE_BID);
   double ask = MarketInfo(sym, MODE_ASK);
   if(typ == OP_BUY)
   {
      if(sl > 0 && bid - sl < level) return false;
      if(tp > 0 && tp - ask < level) return false;
      return true;
   }
   if(typ == OP_SELL)
   {
      if(sl > 0 && sl - ask < level) return false;
      if(tp > 0 && bid - tp < level) return false;
      return true;
   }
   if(typ == OP_BUYLIMIT)
   {
      if(ask - price < level) return false;
      if(sl > 0 && price - sl < level) return false;
      if(tp > 0 && tp - price < level) return false;
      return true;
   }
   if(typ == OP_SELLLIMIT)
   {
      if(price - bid < level) return false;
      if(sl > 0 && sl - price < level) return false;
      if(tp > 0 && price - tp < level) return false;
      return true;
   }
   if(typ == OP_BUYSTOP)
   {
      if(price - ask < level) return false;
      if(sl > 0 && price - sl < level) return false;
      if(tp > 0 && tp - price < level) return false;
      return true;
   }
   if(typ == OP_SELLSTOP)
   {
      if(bid - price < level) return false;
      if(sl > 0 && sl - price < level) return false;
      if(tp > 0 && price - tp < level) return false;
      return true;
   }
   return true;
}

// `err` carries the error OUT. GetLastError() clears the register on read, so
// a caller that reads it a second time gets 0 and reports a rejection with no
// reason. The loop is the only place that can still see it.
int SendRetry(string sym, int typ, double vol, double price, int slip, string comment, int magic, int &err)
{
   int ticket = -1;
   err = 0;
   for(int i=0; i<8; i++)
   {
      RefreshRates();
      if(typ == OP_BUY) price = MarketInfo(sym, MODE_ASK);
      if(typ == OP_SELL) price = MarketInfo(sym, MODE_BID);
      ticket = OrderSend(sym, typ, vol, price, slip, 0, 0, comment, magic, 0, clrNONE);
      if(ticket >= 0)
         return ticket;
      err = GetLastError();
      if(err != 146 && err != 128 && err != 141)
         break;
      Sleep(50);
   }
   return -1;
}

// Same contract as SendRetry. The OrderSelect branch used to return without
// reading the register at all, which left the caller reading a DIFFERENT call's
// error rather than nothing; that is a wrong answer, not a missing one.
bool ModifyRetry(int ticket, double price, double sl, double tp, int &err)
{
   err = 0;
   for(int i=0; i<5; i++)
   {
      RefreshRates();
      if(!OrderSelect(ticket, SELECT_BY_TICKET))
      {
         err = GetLastError();
         return false;
      }
      if(OrderModify(ticket, price, sl, tp, 0, clrNONE))
         return true;
      err = GetLastError();
      if(err != 146 && err != 1)
         return false;
      Sleep(50);
   }
   return false;
}

// 1 = still on the book, 0 = confirmed gone, -1 = cannot tell.
// Leaves the ticket selected when it returns 1.
int TicketState(int ticket)
{
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return -1;
   if(OrderCloseTime() != 0)
      return 0;
   return 1;
}

// Close a position this Expert has just opened but could not protect.
// Returns true ONLY when the book confirms the position is gone. OrderClose's
// own return value is recorded and logged, never treated as proof: it is a
// claim about the book, and the book is the artifact.
bool RollbackPosition(int ticket, string sym, int slip)
{
   for(int i=0; i<6; i++)
   {
      int state = TicketState(ticket);
      if(state == 0)
         return true;
      if(state == 1)
      {
         RefreshRates();
         double px = (OrderType() == OP_BUY) ? MarketInfo(sym, MODE_BID) : MarketInfo(sym, MODE_ASK);
         bool sent = OrderClose(ticket, OrderLots(), px, slip, clrNONE);
         if(!sent)
            Print("mt4riskbot rollback close failed ticket=", ticket, " err=", GetLastError());
      }
      Sleep(50);
   }
   return TicketState(ticket) == 0;
}

// Same contract for a pending order that was placed but could not be protected.
bool RollbackPending(int ticket)
{
   for(int i=0; i<6; i++)
   {
      int state = TicketState(ticket);
      if(state == 0)
         return true;
      if(state == 1)
      {
         bool sent = OrderDelete(ticket);
         if(!sent)
            Print("mt4riskbot rollback delete failed ticket=", ticket, " err=", GetLastError());
      }
      Sleep(50);
   }
   return TicketState(ticket) == 0;
}

string Handle(string body)
{
   string id = KV(body, "id");
   string op = KV(body, "op");
   if(op == "ping")
      return Ok(id) + "time=" + IntegerToString((int)TimeCurrent()) + "\n";
   if(op == "account")
      return AccountReply(id);
   if(op == "tick")
      return TickReply(id, KV(body, "symbol"));
   if(op == "symbol")
      return SymbolReply(id, KV(body, "symbol"));
   if(op == "select")
      return SelectReply(id, KV(body, "symbol"));
   if(op == "rates")
      return RatesReply(id, KV(body, "symbol"), KV(body, "timeframe"), KV(body, "count"));
   if(op == "positions")
      return BookReply(id, KV(body, "magic"), false);
   if(op == "orders")
      return BookReply(id, KV(body, "magic"), true);
   if(op == "check_market")
      return CheckMarket(id, body, false);
   if(op == "market")
      return CheckMarket(id, body, true);
   if(op == "check_working")
      return CheckWorking(id, body, false);
   if(op == "working")
      return CheckWorking(id, body, true);
   if(op == "modify_position")
      return ModifyPos(id, body);
   if(op == "modify_working")
      return ModifyPend(id, body);
   if(op == "cancel")
      return CancelOrder(id, body);
   if(op == "close")
      return ClosePos(id, body);
   if(op == "close_by")
      return CloseBy(id, body);
   return Fail(id, 1, "unsupported");
}

string AccountReply(string id)
{
   int mode = IsDemo() ? 0 : 2;
   int allowed = (IsTradeAllowed() && IsExpertEnabled() && IsConnected()) ? 1 : 0;
   int expert = IsExpertEnabled() ? 1 : 0;
   return Ok(id)
      + "login=" + IntegerToString(AccountNumber()) + "\n"
      + "balance=" + DoubleToString(AccountBalance(), 2) + "\n"
      + "equity=" + DoubleToString(AccountEquity(), 2) + "\n"
      + "margin=" + DoubleToString(AccountMargin(), 2) + "\n"
      + "margin_free=" + DoubleToString(AccountFreeMargin(), 2) + "\n"
      + "profit=" + DoubleToString(AccountProfit(), 2) + "\n"
      + "currency=" + Wire(AccountCurrency()) + "\n"
      + "leverage=" + IntegerToString(AccountLeverage()) + "\n"
      + "trade_mode=" + IntegerToString(mode) + "\n"
      + "trade_allowed=" + IntegerToString(allowed) + "\n"
      + "trade_expert=" + IntegerToString(expert) + "\n"
      + "name=" + Wire(AccountName()) + "\n"
      + "server=" + Wire(AccountServer()) + "\n";
}

string TickReply(string id, string sym)
{
   if(sym == "")
      return Fail(id, 1, "symbol");
   RefreshRates();
   int digits = (int)MarketInfo(sym, MODE_DIGITS);
   return Ok(id)
      + "bid=" + DoubleToString(MarketInfo(sym, MODE_BID), digits) + "\n"
      + "ask=" + DoubleToString(MarketInfo(sym, MODE_ASK), digits) + "\n"
      + "time=" + IntegerToString((int)TimeCurrent()) + "\n";
}

string SymbolReply(string id, string sym)
{
   if(sym == "")
      return Fail(id, 1, "symbol");
   SymbolSelect(sym, true);
   int digits = (int)MarketInfo(sym, MODE_DIGITS);
   return Ok(id)
      + "digits=" + IntegerToString(digits) + "\n"
      + "point=" + DoubleToString(MarketInfo(sym, MODE_POINT), digits) + "\n"
      + "volume_min=" + DoubleToString(MarketInfo(sym, MODE_MINLOT), 8) + "\n"
      + "volume_max=" + DoubleToString(MarketInfo(sym, MODE_MAXLOT), 8) + "\n"
      + "volume_step=" + DoubleToString(MarketInfo(sym, MODE_LOTSTEP), 8) + "\n"
      + "tick_value=" + DoubleToString(MarketInfo(sym, MODE_TICKVALUE), 8) + "\n"
      + "tick_size=" + DoubleToString(MarketInfo(sym, MODE_TICKSIZE), digits) + "\n"
      + "contract_size=" + DoubleToString(MarketInfo(sym, MODE_LOTSIZE), 0) + "\n"
      + "stops_level=" + IntegerToString((int)MarketInfo(sym, MODE_STOPLEVEL)) + "\n"
      + "freeze_level=" + IntegerToString((int)MarketInfo(sym, MODE_FREEZELEVEL)) + "\n"
      + "spread=" + IntegerToString((int)MarketInfo(sym, MODE_SPREAD)) + "\n";
}

string SelectReply(string id, string sym)
{
   if(sym == "")
      return Fail(id, 1, "symbol");
   if(!SymbolSelect(sym, true))
      return Fail(id, 1, "select");
   return Ok(id);
}

// A price series exists per symbol AND timeframe, and MT4 builds one only once
// something asks for it. A terminal with H4 charts open and a desk configured
// for H1 therefore has H1 history for nothing, which was measured on the live
// rig as EURUSD bars=0 / ATR=nan while XAUUSD, the one charted symbol, read
// bars=200. Three things this handler did not do, each of which left a cold
// symbol permanently untradeable with nothing said anywhere:
//
//  1. It never selected the symbol. Every other symbol-scoped handler does
//     (SymbolReply, SelectReply). `rates` worked only because Engine.start()
//     happens to call `select` for each configured symbol first, which is call
//     order, not a guarantee this handler made for itself.
//  2. At iBars()==0 it set n=0, so the row loop never executed and NO
//     price-series function was reached at all: no iTime, iOpen, iClose,
//     iVolume, no ArrayCopyRates. iBars alone is not the documented download
//     trigger; the iXXX series access is, and it reports 4066
//     ERR_HISTORY_WILL_UPDATED while the request is in flight. The handler
//     early-returned on exactly the series it needed to ask for, so the desk
//     could not warm a symbol no matter how many times it asked.
//  3. It discarded GetLastError() and emitted only `n=`. That made `ok=1 n=0`
//     one wire value for three different worlds: downloading right now, not
//     served by this broker under this name, and genuinely empty. A desk
//     cannot choose between "wait" and "name it and stop" from a value that
//     cannot tell those apart, and n=0 is the reassuring reading of the three.
//
// No Sleep here. This handler IS the mailbox (Process(), single threaded behind
// gBusy) and the adapter's bridge times out at 5 s, so a retry loop in here
// would stall every other op and blow that timeout. This end triggers the
// fetch once and reports what happened; the desk owns the bounded wait
// (straightedge/history.py:preflight).
string RatesReply(string id, string sym, string tfName, string countStr)
{
   if(sym == "")
      return Fail(id, 1, "symbol");
   int tf = Tf(tfName);
   int want = (int)StringToInteger(countStr);
   if(want <= 0) want = 1;
   bool selected = SymbolSelect(sym, true);
   ResetLastError();
   int total = iBars(sym, tf);
   int history_error = 0;
   if(total < want)
   {
      ResetLastError();
      double first = iClose(sym, tf, 0);
      history_error = GetLastError();
      ResetLastError();
      total = iBars(sym, tf);
      if(total <= 0 && first != 0.0)
         Print("mt4riskbot rates ", sym, " ", tfName,
               " served a close with zero bars; history state is inconsistent");
   }
   int n = want;
   if(n > total) n = total;
   string rows = "";
   int digits = (int)MarketInfo(sym, MODE_DIGITS);
   int written = 0;
   for(int i=n-1; i>=0; i--)
   {
      string line = IntegerToString((int)iTime(sym, tf, i))
         + "|" + DoubleToString(iOpen(sym, tf, i), digits)
         + "|" + DoubleToString(iHigh(sym, tf, i), digits)
         + "|" + DoubleToString(iLow(sym, tf, i), digits)
         + "|" + DoubleToString(iClose(sym, tf, i), digits)
         + "|" + IntegerToString((int)iVolume(sym, tf, i));
      rows = rows + "row" + IntegerToString(written) + "=" + line + "\n";
      written++;
   }
   // bars_total, selected and history_error are emitted on EVERY rates reply,
   // including the healthy one. A field present only on failure cannot be told
   // from an Expert too old to emit it at all, which is the partition
   // survivor_ticket exists for above.
   return Ok(id)
      + "n=" + IntegerToString(written) + "\n"
      + "bars_total=" + IntegerToString(total) + "\n"
      + "selected=" + IntegerToString(selected ? 1 : 0) + "\n"
      + "history_error=" + IntegerToString(history_error) + "\n"
      + rows;
}

string BookReply(string id, string magicStr, bool pending)
{
   int want = (int)StringToInteger(magicStr);
   string rows = "";
   int written = 0;
   int total = OrdersTotal();
   for(int i=0; i<total; i++)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      int typ = OrderType();
      bool isPend = (typ > OP_SELL);
      if(isPend != pending)
         continue;
      if(want != 0 && OrderMagicNumber() != want)
         continue;
      int digits = (int)MarketInfo(OrderSymbol(), MODE_DIGITS);
      string line;
      if(pending)
      {
         line = IntegerToString(OrderTicket())
            + "|" + Wire(OrderSymbol())
            + "|" + SideOf(typ)
            + "|" + KindOf(typ)
            + "|" + DoubleToString(OrderLots(), 2)
            + "|" + DoubleToString(OrderOpenPrice(), digits)
            + "|" + DoubleToString(OrderStopLoss(), digits)
            + "|" + DoubleToString(OrderTakeProfit(), digits)
            + "|" + IntegerToString(OrderMagicNumber())
            + "|" + Wire(OrderComment())
            + "|" + IntegerToString((int)OrderOpenTime());
      }
      else
      {
         line = IntegerToString(OrderTicket())
            + "|" + Wire(OrderSymbol())
            + "|" + SideOf(typ)
            + "|" + DoubleToString(OrderLots(), 2)
            + "|" + DoubleToString(OrderOpenPrice(), digits)
            + "|" + DoubleToString(OrderStopLoss(), digits)
            + "|" + DoubleToString(OrderTakeProfit(), digits)
            + "|" + DoubleToString(OrderClosePrice(), digits)
            + "|" + DoubleToString(OrderProfit(), 2)
            + "|" + IntegerToString(OrderMagicNumber())
            + "|" + Wire(OrderComment())
            + "|" + DoubleToString(OrderSwap(), 2)
            + "|" + IntegerToString((int)OrderOpenTime());
      }
      rows = rows + "row" + IntegerToString(written) + "=" + line + "\n";
      written++;
   }
   return Ok(id) + "n=" + IntegerToString(written) + "\n" + rows;
}

string CheckMarket(string id, string body, bool send)
{
   string sym = KV(body, "symbol");
   string side = KV(body, "side");
   double vol = StringToDouble(KV(body, "volume"));
   double sl = StringToDouble(KV(body, "sl"));
   double tp = StringToDouble(KV(body, "tp"));
   int magic = (int)StringToInteger(KV(body, "magic"));
   int slip = (int)StringToInteger(KV(body, "deviation"));
   if(slip <= 0) slip = Slippage;
   if(sym == "" || (side != "buy" && side != "sell"))
      return FailTrade(id, 1, "symbol", 0);
   if(!IsTradeAllowed() || !IsExpertEnabled())
      return FailTrade(id, 133, "trade_disabled", 0);
   SymbolSelect(sym, true);
   if(!VolumeOk(sym, vol))
      return FailTrade(id, 131, "invalid_volume", 0);
   int typ = (side == "buy") ? OP_BUY : OP_SELL;
   RefreshRates();
   double price = (typ == OP_BUY) ? MarketInfo(sym, MODE_ASK) : MarketInfo(sym, MODE_BID);
   if(!StopsOk(sym, typ, price, sl, tp))
      return FailTrade(id, 130, "invalid_stops", 0);
   if(!send)
      return Ok(id) + "ticket=0\nprice=" + DoubleToString(price, (int)MarketInfo(sym, MODE_DIGITS)) + "\n";
   int sendErr = 0;
   int ticket = SendRetry(sym, typ, vol, price, slip, ClipComment(KV(body, "comment")), magic, sendErr);
   if(ticket < 0)
      return FailTrade(id, sendErr, "OrderSend", 0);
   if(sl > 0 || tp > 0)
   {
      // A position exists from here on. Failing to select it is not a reason
      // to abandon it; it is a reason to roll it back.
      double openPrice = 0;
      int modErr = 0;
      if(OrderSelect(ticket, SELECT_BY_TICKET))
         openPrice = OrderOpenPrice();
      else
         modErr = GetLastError();
      if(openPrice <= 0 || !ModifyRetry(ticket, openPrice, sl, tp, modErr))
      {
         if(!RollbackPosition(ticket, sym, slip))
            return FailTrade(id, modErr, "sl_modify_failed_position_live", ticket);
         return FailTrade(id, modErr, "sl_modify_failed", 0);
      }
   }
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return Ok(id) + "ticket=" + IntegerToString(ticket) + "\n";
   int digits = (int)MarketInfo(sym, MODE_DIGITS);
   return Ok(id)
      + "ticket=" + IntegerToString(ticket) + "\n"
      + "volume=" + DoubleToString(OrderLots(), 2) + "\n"
      + "price=" + DoubleToString(OrderOpenPrice(), digits) + "\n";
}

string CheckWorking(string id, string body, bool send)
{
   string sym = KV(body, "symbol");
   string side = KV(body, "side");
   string kind = KV(body, "kind");
   double vol = StringToDouble(KV(body, "volume"));
   double price = StringToDouble(KV(body, "price"));
   double sl = StringToDouble(KV(body, "sl"));
   double tp = StringToDouble(KV(body, "tp"));
   int magic = (int)StringToInteger(KV(body, "magic"));
   if(sym == "" || (side != "buy" && side != "sell") || (kind != "limit" && kind != "stop"))
      return FailTrade(id, 1, "symbol", 0);
   if(!IsTradeAllowed() || !IsExpertEnabled())
      return FailTrade(id, 133, "trade_disabled", 0);
   SymbolSelect(sym, true);
   if(!VolumeOk(sym, vol))
      return FailTrade(id, 131, "invalid_volume", 0);
   int typ = PendingType(side, kind);
   if(!StopsOk(sym, typ, price, sl, tp))
      return FailTrade(id, 130, "invalid_stops", 0);
   if(!send)
      return Ok(id) + "ticket=0\n";
   int sendErr = 0;
   int ticket = SendRetry(sym, typ, vol, price, Slippage, ClipComment(KV(body, "comment")), magic, sendErr);
   if(ticket < 0)
      return FailTrade(id, sendErr, "OrderSend", 0);
   if(sl > 0 || tp > 0)
   {
      int modErr = 0;
      if(!ModifyRetry(ticket, price, sl, tp, modErr))
      {
         if(!RollbackPending(ticket))
            return FailTrade(id, modErr, "sl_modify_failed_order_live", ticket);
         return FailTrade(id, modErr, "sl_modify_failed", 0);
      }
   }
   return Ok(id) + "ticket=" + IntegerToString(ticket) + "\nprice=" + DoubleToString(price, (int)MarketInfo(sym, MODE_DIGITS)) + "\n";
}

string ModifyPos(string id, string body)
{
   int ticket = (int)StringToInteger(KV(body, "ticket"));
   double sl = StringToDouble(KV(body, "sl"));
   double tp = StringToDouble(KV(body, "tp"));
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return Fail(id, 4108, "not_found");
   if(OrderType() > OP_SELL)
      return Fail(id, 1, "not_position");
   int modErr = 0;
   if(!ModifyRetry(ticket, OrderOpenPrice(), sl, tp, modErr))
      return Fail(id, modErr, "OrderModify");
   return Ok(id) + "ticket=" + IntegerToString(ticket) + "\n";
}

string ModifyPend(string id, string body)
{
   int ticket = (int)StringToInteger(KV(body, "ticket"));
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return Fail(id, 4108, "not_found");
   if(OrderType() <= OP_SELL)
      return Fail(id, 1, "not_pending");
   double price = OrderOpenPrice();
   double sl = OrderStopLoss();
   double tp = OrderTakeProfit();
   string p = KV(body, "price");
   string s = KV(body, "sl");
   string t = KV(body, "tp");
   if(p != "") price = StringToDouble(p);
   if(s != "") sl = StringToDouble(s);
   if(t != "") tp = StringToDouble(t);
   int modErr = 0;
   if(!ModifyRetry(ticket, price, sl, tp, modErr))
      return Fail(id, modErr, "OrderModify");
   return Ok(id) + "ticket=" + IntegerToString(ticket) + "\n";
}

string CancelOrder(string id, string body)
{
   int ticket = (int)StringToInteger(KV(body, "ticket"));
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return Fail(id, 4108, "not_found");
   if(OrderType() <= OP_SELL)
      return Fail(id, 1, "not_pending");
   if(!OrderDelete(ticket))
      return Fail(id, GetLastError(), "OrderDelete");
   return Ok(id) + "ticket=" + IntegerToString(ticket) + "\n";
}

string ClosePos(string id, string body)
{
   int ticket = (int)StringToInteger(KV(body, "ticket"));
   double vol = StringToDouble(KV(body, "volume"));
   double price = StringToDouble(KV(body, "price"));
   int slip = (int)StringToInteger(KV(body, "deviation"));
   if(slip <= 0) slip = Slippage;
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
      return Fail(id, 4108, "not_found");
   if(OrderType() > OP_SELL)
      return Fail(id, 1, "not_position");
   string sym = OrderSymbol();
   RefreshRates();
   if(price <= 0)
      price = (OrderType() == OP_BUY) ? MarketInfo(sym, MODE_BID) : MarketInfo(sym, MODE_ASK);
   if(vol <= 0 || vol > OrderLots() + 1e-8)
      vol = OrderLots();
   if(!OrderClose(ticket, vol, price, slip, clrNONE))
      return Fail(id, GetLastError(), "OrderClose");
   return Ok(id) + "ticket=" + IntegerToString(ticket) + "\nvolume=" + DoubleToString(vol, 2) + "\n";
}

string CloseBy(string id, string body)
{
   int ticket = (int)StringToInteger(KV(body, "ticket"));
   int other = (int)StringToInteger(KV(body, "other"));
   if(!OrderCloseBy(ticket, other, clrNONE))
      return Fail(id, GetLastError(), "OrderCloseBy");
   return Ok(id) + "ticket=" + IntegerToString(ticket) + "\n";
}
