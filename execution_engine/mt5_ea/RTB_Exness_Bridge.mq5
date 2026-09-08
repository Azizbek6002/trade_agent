//+------------------------------------------------------------------+
//|                                         RTB_Exness_Bridge.mq5    |
//|                             Robo Trader Boy v2.0 Execution Bridge|
//|                                  Copyright 2026, @coder_zik      |
//+------------------------------------------------------------------+
#property copyright "Robo Trader Boy v2.0 - coder_zik"
#property link      "https://t.me/coder_zik"
#property version   "2.00"
#property description "Professional MQL5 Execution Bridge for Exness MT5"
#property description "Connects Exness MT5 terminal directly to RTB FastAPI AI Decision Engine"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>
#include <Trade\AccountInfo.mqh>

//--- Input Parameters
input group "=== RTB SERVER SETTINGS ==="
input string   InpServerUrl       = "http://127.0.0.1:8000"; // Server URL (Localhost or Cloudflare)
input string   InpAdminKey        = "7266764356"; // Admin Authorization Key
input int      InpPollIntervalMs  = 800;          // Polling Interval (ms)

input group "=== TRADING SETTINGS ==="
input string   InpSymbolOverride  = "";           // Symbol override (e.g. XAUUSDm, leave empty for current chart)
input ulong    InpMagicNumber     = 20260907;     // Magic Number
input int      InpSlippagePoints  = 25;           // Max Slippage (Points)

//--- Global Objects
CTrade         m_trade;
CPositionInfo  m_position;
COrderInfo     m_order;
CAccountInfo   m_account;

//--- State variables
string         g_active_symbol   = "";
string         g_clean_server_url = "";
datetime       g_last_ping_time  = 0;
int            g_ping_count      = 0;
int            g_executed_count  = 0;
string         g_last_status_msg = "Starting...";
bool           g_is_busy         = false;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Clean server URL (strip trailing slashes)
   g_clean_server_url = InpServerUrl;
   while(StringLen(g_clean_server_url) > 0 && StringSubstr(g_clean_server_url, StringLen(g_clean_server_url) - 1, 1) == "/")
   {
      g_clean_server_url = StringSubstr(g_clean_server_url, 0, StringLen(g_clean_server_url) - 1);
   }

   // Determine symbol
   if(StringLen(InpSymbolOverride) > 0)
      g_active_symbol = InpSymbolOverride;
   else
      g_active_symbol = _Symbol;

   // Select symbol in Market Watch
   SymbolSelect(g_active_symbol, true);

   // Configure CTrade
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(InpSlippagePoints);

   // Configure Exness Filling Mode (IOC -> FOK -> RETURN)
   uint filling = (uint)SymbolInfoInteger(g_active_symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_IOC) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else if((filling & SYMBOL_FILLING_FOK) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   else
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);

   // Set timer for polling
   int poll_ms = InpPollIntervalMs;
   if(poll_ms < 200) poll_ms = 200;
   EventSetMillisecondTimer(poll_ms);

   PrintFormat("🟢 RTB Exness Bridge initialized! Symbol: %s, Server: %s, Interval: %d ms",
               g_active_symbol, g_clean_server_url, poll_ms);

   UpdateChartHUD("INIT OK - Waiting first heartbeat...");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
   Print("🔴 RTB Exness Bridge stopped.");
}

//+------------------------------------------------------------------+
//| Timer function: Polls server and executes orders                 |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(g_is_busy) return;
   g_is_busy = true;

   SendHeartbeatAndPoll();

   g_is_busy = false;
}

//+------------------------------------------------------------------+
//| Sends Heartbeat and Polls Pending Actions                        |
//+------------------------------------------------------------------+
void SendHeartbeatAndPoll()
{
   // 1. Gather account & symbol telemetry
   double balance     = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity      = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin      = AccountInfoDouble(ACCOUNT_MARGIN);
   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   long   leverage    = AccountInfoInteger(ACCOUNT_LEVERAGE);
   string currency    = AccountInfoString(ACCOUNT_CURRENCY);
   long   account_num = AccountInfoInteger(ACCOUNT_LOGIN);
   string broker      = AccountInfoString(ACCOUNT_COMPANY);
   string srv_name    = AccountInfoString(ACCOUNT_SERVER);

   MqlTick tick;
   SymbolInfoTick(g_active_symbol, tick);
   double bid = tick.bid;
   double ask = tick.ask;

   // 2. Build positions JSON
   string positions_json = "[";
   int total_pos = PositionsTotal();
   int added = 0;
   for(int i = 0; i < total_pos; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && m_position.SelectByTicket(ticket))
      {
         string pos_sym = m_position.Symbol();
         string pos_type = (m_position.PositionType() == POSITION_TYPE_BUY) ? "BUY" : "SELL";
         if(added > 0) positions_json += ",";
         positions_json += StringFormat(
            "{\"ticket\":%I64u,\"symbol\":\"%s\",\"type\":\"%s\",\"volume\":%.2f,\"price_open\":%.3f,\"sl\":%.3f,\"tp\":%.3f,\"profit\":%.2f,\"magic\":%I64u,\"comment\":\"%s\"}",
            ticket, pos_sym, pos_type, m_position.Volume(), m_position.PriceOpen(),
            m_position.StopLoss(), m_position.TakeProfit(), m_position.Profit(),
            m_position.Magic(), m_position.Comment()
         );
         added++;
      }
   }
   // Also add pending limit orders
   int total_orders = OrdersTotal();
   for(int i = 0; i < total_orders; i++)
   {
      ulong ord_ticket = OrderGetTicket(i);
      if(ord_ticket > 0 && m_order.Select(ord_ticket))
      {
         ENUM_ORDER_TYPE otype = m_order.OrderType();
         string type_str = "";
         if(otype == ORDER_TYPE_BUY_LIMIT) type_str = "BUY_LIMIT";
         else if(otype == ORDER_TYPE_SELL_LIMIT) type_str = "SELL_LIMIT";
         else if(otype == ORDER_TYPE_BUY_STOP) type_str = "BUY_STOP";
         else if(otype == ORDER_TYPE_SELL_STOP) type_str = "SELL_STOP";

         if(type_str != "")
         {
            if(added > 0) positions_json += ",";
            positions_json += StringFormat(
               "{\"ticket\":%I64u,\"symbol\":\"%s\",\"type\":\"%s\",\"volume\":%.2f,\"price_open\":%.3f,\"sl\":%.3f,\"tp\":%.3f,\"profit\":0.0,\"magic\":%I64u,\"comment\":\"%s\",\"is_pending\":true}",
               ord_ticket, m_order.Symbol(), type_str, m_order.VolumeInitial(), m_order.PriceOpen(),
               m_order.StopLoss(), m_order.TakeProfit(), m_order.Magic(), m_order.Comment()
            );
            added++;
         }
      }
   }
   positions_json += "]";

   // 3. Construct Heartbeat payload
   string payload = StringFormat(
      "{\"account\":%I64u,\"server\":\"%s\",\"broker\":\"%s\",\"balance\":%.2f,\"equity\":%.2f,\"margin\":%.2f,\"free_margin\":%.2f,\"leverage\":%d,\"currency\":\"%s\",\"symbol\":\"%s\",\"bid\":%.3f,\"ask\":%.3f,\"open_positions\":%s}",
      account_num, srv_name, broker, balance, equity, margin, free_margin, leverage, currency, g_active_symbol, bid, ask, positions_json
   );

   // 4. Send WebRequest
   string url = g_clean_server_url + "/api/mt5/heartbeat?admin_key=" + InpAdminKey;
   string headers = "Content-Type: application/json\r\nX-Admin-Key: " + InpAdminKey + "\r\n";
   char post_data[];
   char result_data[];
   string result_headers;

   StringToCharArray(payload, post_data, 0, WHOLE_ARRAY, CP_UTF8);
   ArrayResize(post_data, ArraySize(post_data) - 1); // remove null terminator

   ResetLastError();
   int res = WebRequest("POST", url, headers, 3000, post_data, result_data, result_headers);

   if(res == -1)
   {
      int err = GetLastError();
      g_last_status_msg = StringFormat("WebRequest Error: %d (Check Tools -> Options -> Allow WebRequest)", err);
      UpdateChartHUD(g_last_status_msg);
      return;
   }

   g_last_ping_time = TimeCurrent();
   g_ping_count++;

   // 5. Parse response
   string response_str = CharArrayToString(result_data, 0, WHOLE_ARRAY, CP_UTF8);
   ProcessServerResponse(response_str);

   UpdateChartHUD("CONNECTED 🟢 (Real Exness Live)");
}

//+------------------------------------------------------------------+
//| Process Server Response and Execute Actions                      |
//+------------------------------------------------------------------+
void ProcessServerResponse(const string &response)
{
   if(StringLen(response) == 0) return;

   // Check if actions list is present
   int actions_idx = StringFind(response, "\"actions\":[");
   if(actions_idx < 0) return;

   int start_arr = actions_idx + 10;
   int end_arr = StringFind(response, "]", start_arr);
   if(end_arr <= start_arr) return;

   string actions_content = StringSubstr(response, start_arr, end_arr - start_arr);
   if(StringLen(actions_content) < 5) return; // empty array `[]`

   // Split actions by `{`
   int cur = 0;
   while(cur < StringLen(actions_content))
   {
      int obj_start = StringFind(actions_content, "{", cur);
      if(obj_start < 0) break;
      int obj_end = StringFind(actions_content, "}", obj_start);
      if(obj_end < 0) break;

      string action_json = StringSubstr(actions_content, obj_start, obj_end - obj_start + 1);
      ExecuteSingleAction(action_json);

      cur = obj_end + 1;
   }
}

//+------------------------------------------------------------------+
//| Execute a single action requested by the Python AI server        |
//+------------------------------------------------------------------+
void ExecuteSingleAction(const string &json)
{
   string action_id = JsonGetString(json, "action_id");
   string type      = JsonGetString(json, "type");
   string sym       = JsonGetString(json, "symbol");
   if(sym == "") sym = g_active_symbol;

   string action    = JsonGetString(json, "action");       // BUY or SELL
   string ord_type  = JsonGetString(json, "order_type");   // MARKET or LIMIT
   double lot       = JsonGetDouble(json, "lot");
   double price     = JsonGetDouble(json, "price");
   double sl        = JsonGetDouble(json, "sl");
   double tp        = JsonGetDouble(json, "tp");
   ulong  ticket    = (ulong)JsonGetLong(json, "ticket");
   string comment   = JsonGetString(json, "comment");
   if(comment == "") comment = "RTB v2.0";

   PrintFormat("📥 Received Action [%s]: Type=%s, Sym=%s, Act=%s, Lot=%.2f, Price=%.2f",
               action_id, type, sym, action, lot, price);

   bool success = false;
   ulong executed_ticket = 0;
   double exec_price = 0.0;
   string error_msg = "";

   if(type == "OPEN_ORDER")
   {
      // Select symbol
      SymbolSelect(sym, true);
      MqlTick tick;
      SymbolInfoTick(sym, tick);

      if(ord_type == "MARKET" || ord_type == "DEAL")
      {
         if(action == "BUY")
         {
            exec_price = tick.ask;
            if(m_trade.Buy(lot, sym, exec_price, sl, tp, comment))
            {
               success = true;
               executed_ticket = m_trade.ResultOrder();
               exec_price = m_trade.ResultPrice();
            }
            else
            {
               error_msg = StringFormat("BUY Failed: %s (Retcode: %d)", m_trade.ResultComment(), m_trade.ResultRetcode());
            }
         }
         else // SELL
         {
            exec_price = tick.bid;
            if(m_trade.Sell(lot, sym, exec_price, sl, tp, comment))
            {
               success = true;
               executed_ticket = m_trade.ResultOrder();
               exec_price = m_trade.ResultPrice();
            }
            else
            {
               error_msg = StringFormat("SELL Failed: %s (Retcode: %d)", m_trade.ResultComment(), m_trade.ResultRetcode());
            }
         }
      }
      else // PENDING LIMIT ORDER
      {
         if(action == "BUY")
         {
            if(m_trade.BuyLimit(lot, price, sym, sl, tp, ORDER_TIME_GTC, 0, comment))
            {
               success = true;
               executed_ticket = m_trade.ResultOrder();
               exec_price = price;
            }
            else
            {
               error_msg = StringFormat("BUY LIMIT Failed: %s (Retcode: %d)", m_trade.ResultComment(), m_trade.ResultRetcode());
            }
         }
         else // SELL LIMIT
         {
            if(m_trade.SellLimit(lot, price, sym, sl, tp, ORDER_TIME_GTC, 0, comment))
            {
               success = true;
               executed_ticket = m_trade.ResultOrder();
               exec_price = price;
            }
            else
            {
               error_msg = StringFormat("SELL LIMIT Failed: %s (Retcode: %d)", m_trade.ResultComment(), m_trade.ResultRetcode());
            }
         }
      }
   }
   else if(type == "CLOSE_ORDER")
   {
      if(ticket > 0)
      {
         if(lot > 0)
            success = m_trade.PositionClose(ticket);
         else
            success = m_trade.PositionClose(ticket);

         if(success)
         {
            executed_ticket = ticket;
            exec_price = m_trade.ResultPrice();
         }
         else
         {
            error_msg = StringFormat("Close #{%I64u} Failed: %s", ticket, m_trade.ResultComment());
         }
      }
      else
      {
         error_msg = "Invalid ticket for close";
      }
   }
   else if(type == "CANCEL_ORDER")
   {
      if(ticket > 0)
      {
         success = m_trade.OrderDelete(ticket);
         if(success)
         {
            executed_ticket = ticket;
         }
         else
         {
            error_msg = StringFormat("Cancel #{%I64u} Failed: %s", ticket, m_trade.ResultComment());
         }
      }
   }
   else if(type == "MODIFY_ORDER")
   {
      if(ticket > 0)
      {
         success = m_trade.PositionModify(ticket, sl, tp);
         if(success)
         {
            executed_ticket = ticket;
         }
         else
         {
            error_msg = StringFormat("Modify #{%I64u} Failed: %s", ticket, m_trade.ResultComment());
         }
      }
   }

   if(success)
   {
      g_executed_count++;
      PrintFormat("✅ Action [%s] EXECUTED SUCCESSFULLY! Ticket=#%I64u, Price=%.3f",
                  action_id, executed_ticket, exec_price);
   }
   else
   {
      PrintFormat("❌ Action [%s] FAILED: %s", action_id, error_msg);
   }

   // Send confirmation back to server
   SendConfirmation(action_id, success, executed_ticket, exec_price, lot, error_msg);
}

//+------------------------------------------------------------------+
//| Sends confirmation of executed action back to server             |
//+------------------------------------------------------------------+
void SendConfirmation(const string &action_id, bool success, ulong ticket, double price, double lot, const string &error_msg)
{
   string payload = StringFormat(
      "{\"action_id\":\"%s\",\"success\":%s,\"ticket\":%I64u,\"price\":%.3f,\"lot\":%.2f,\"error\":\"%s\"}",
      action_id, (success ? "true" : "false"), ticket, price, lot, error_msg
   );

   string url = g_clean_server_url + "/api/mt5/confirm?admin_key=" + InpAdminKey;
   string headers = "Content-Type: application/json\r\nX-Admin-Key: " + InpAdminKey + "\r\n";
   char post_data[];
   char result_data[];
   string result_headers;

   StringToCharArray(payload, post_data, 0, WHOLE_ARRAY, CP_UTF8);
   ArrayResize(post_data, ArraySize(post_data) - 1);

   WebRequest("POST", url, headers, 3000, post_data, result_data, result_headers);
}

//+------------------------------------------------------------------+
//| HUD Chart Overlay                                                |
//+------------------------------------------------------------------+
void UpdateChartHUD(const string &status)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   long login     = AccountInfoInteger(ACCOUNT_LOGIN);
   string broker  = AccountInfoString(ACCOUNT_COMPANY);

   string hud = "";
   hud += "======================================================\n";
   hud += "🤖 ROBO TRADER BOY v2.0 - EXNESS MT5 BRIDGE\n";
   hud += "======================================================\n";
   hud += StringFormat("Account: %I64u | Broker: %s\n", login, broker);
   hud += StringFormat("Balance: $%.2f | Equity: $%.2f\n", balance, equity);
   hud += StringFormat("Monitored Symbol: %s | Magic: %I64u\n", g_active_symbol, InpMagicNumber);
   hud += StringFormat("Server URL: %s\n", g_clean_server_url);
   hud += StringFormat("Status: %s\n", status);
   hud += StringFormat("Heartbeats: %d | Executed Trades: %d\n", g_ping_count, g_executed_count);
   hud += StringFormat("Last Ping: %s\n", TimeToString(g_last_ping_time, TIME_SECONDS));
   hud += "======================================================\n";
   hud += "Authorized for Admin: @coder_zik\n";
   hud += "======================================================\n";

   Comment(hud);
}

//+------------------------------------------------------------------+
//| Lightweight Standalone JSON Parsers                              |
//+------------------------------------------------------------------+
string JsonGetString(const string &json, const string &key)
{
   string search = "\"" + key + "\":\"";
   int pos = StringFind(json, search);
   if(pos < 0) return "";
   int start = pos + StringLen(search);
   int end = StringFind(json, "\"", start);
   if(end < 0) return "";
   return StringSubstr(json, start, end - start);
}

double JsonGetDouble(const string &json, const string &key)
{
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if(pos < 0) return 0.0;
   int start = pos + StringLen(search);
   int end = start;
   while(end < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end);
      if(ch == ',' || ch == '}' || ch == ']' || ch == '\r' || ch == '\n') break;
      end++;
   }
   string val_str = StringSubstr(json, start, end - start);
   StringTrimLeft(val_str);
   StringTrimRight(val_str);
   // Remove quotes if present
   if(StringLen(val_str) > 0 && StringGetCharacter(val_str, 0) == '\"')
      val_str = StringSubstr(val_str, 1, StringLen(val_str) - 2);
   return StringToDouble(val_str);
}

long JsonGetLong(const string &json, const string &key)
{
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if(pos < 0) return 0;
   int start = pos + StringLen(search);
   int end = start;
   while(end < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end);
      if(ch == ',' || ch == '}' || ch == ']' || ch == '\r' || ch == '\n') break;
      end++;
   }
   string val_str = StringSubstr(json, start, end - start);
   StringTrimLeft(val_str);
   StringTrimRight(val_str);
   if(StringLen(val_str) > 0 && StringGetCharacter(val_str, 0) == '\"')
      val_str = StringSubstr(val_str, 1, StringLen(val_str) - 2);
   return StringToInteger(val_str);
}
