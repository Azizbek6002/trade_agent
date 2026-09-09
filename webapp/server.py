import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from config import config, BASE_DIR
from database import db
from execution_engine.mt5_bridge import mt5_bridge
from news_filter.economic_calendar import news_filter
from risk_manager.risk_manager import validate_and_clamp_risk_percent
from ai_parser.groq_parser import groq_parser

logger = logging.getLogger(__name__)

# FastAPI Application Instance
app = FastAPI(
    title="RTB v2.0 Telegram Mini App API",
    description="FastAPI Backend for Robo Trader Boy Telegram Mini App",
    version="2.0.0"
)

# Enable CORS for Telegram WebApp and local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def verify_admin_access(request: Request, call_next):
    """
    Security gatekeeper: strictly restricts all API endpoints to the authorized Administrator.
    """
    if request.url.path.startswith("/api/"):
        admin_key = request.headers.get("X-Admin-Key") or request.query_params.get("admin_key")
        client_host = request.client.host if request.client else ""
        is_local = client_host in ("127.0.0.1", "::1", "localhost")
        expected_key = str(config.ADMIN_TELEGRAM_ID)

        if not is_local and (not admin_key or admin_key != expected_key):
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error": "⛔ Kirish taqiqlangan! Ushbu tizim faqat administrator uchun mo'ljallangan. @coder_zik ga murojaat qiling."
                }
            )

    response = await call_next(request)
    if config.NGROK_DOMAIN:
        try:
            response.set_cookie(
                key="abuse_interstitial",
                value=config.NGROK_DOMAIN,
                max_age=604800,
                path="/",
                samesite="none",
                secure=True
            )
        except Exception:
            pass
    return response


TEMPLATES_DIR = BASE_DIR / "webapp" / "templates"


# Request Models
class RiskUpdateRequest(BaseModel):
    risk_percent: float = Field(..., description="Risk percent between 1.0% and 5.0%")


class KillSwitchRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="Set bot active state, or omit to toggle")


class CloseTradeRequest(BaseModel):
    ticket: int = Field(..., description="MT5 Position ticket number")
    lot: Optional[float] = Field(None, description="Optional partial lot to close")


class OpenTradeRequest(BaseModel):
    action: str = Field(..., description="BUY or SELL")
    order_type: str = Field("MARKET", description="MARKET or LIMIT")
    price: Optional[float] = Field(None, description="Entry price (optional for MARKET)")
    lot: float = Field(0.01, description="Lot size")
    sl: Optional[float] = Field(None, description="Stop Loss price")
    tp: Optional[float] = Field(None, description="Take Profit price")


class ChatMessageRequest(BaseModel):
    message: str = Field(..., description="Message from user")


class AddChannelRequest(BaseModel):
    channel_id: str = Field(..., description="Channel ID or username")
    title: str = Field(..., description="Channel title")
    weight: Optional[float] = Field(1.5, ge=1.0, le=10.0, description="Channel source weight (1.0 to 10.0)")


class DeleteChannelRequest(BaseModel):
    channel_id: str = Field(..., description="Channel ID or username to delete")


class MT5HeartbeatRequest(BaseModel):
    account: Optional[int] = None
    server: Optional[str] = None
    broker: Optional[str] = None
    balance: Optional[float] = 5000.0
    equity: Optional[float] = 5000.0
    margin: Optional[float] = 0.0
    free_margin: Optional[float] = 5000.0
    leverage: Optional[int] = 2000
    currency: Optional[str] = "USD"
    symbol: Optional[str] = "XAUUSD"
    bid: Optional[float] = None
    ask: Optional[float] = None
    open_positions: Optional[list] = []


class MT5ConfirmRequest(BaseModel):
    action_id: str
    success: bool
    ticket: Optional[int] = 0
    price: Optional[float] = 0.0
    lot: Optional[float] = 0.0
    error: Optional[str] = ""


# ==========================================
# ENDPOINTS
# ==========================================

@app.get("/", response_class=FileResponse)
async def serve_index():
    """Serves the Telegram Mini App HTML UI."""
    index_path = TEMPLATES_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Mini App UI template not found")
    return FileResponse(str(index_path), media_type="text/html")


@app.get("/api/status")
async def get_system_status() -> Dict[str, Any]:
    """
    Returns full real-time operational status for Mini App UI:
    - MT5 Account (Balance, Equity, Margin)
    - Live XAUUSD Price
    - Open Positions & PnL
    - DB Settings (Risk %, Kill-switch)
    - News Filter Status
    - Recent Signals & Trade History
    """
    try:
        # 1. MT5 Account & Market Data
        account = mt5_bridge.get_account_info()
        price = mt5_bridge.get_current_price()
        open_positions = mt5_bridge.get_open_positions()
        if not open_positions:
            open_trades = await db.get_open_trades()
            cur_price = price.get("mid", 4438.9)
            for tr in open_trades:
                is_buy = tr.get("direction") == "BUY"
                entry = float(tr.get("entry_price", cur_price))
                lot = float(tr.get("lot_size", 0.01))
                diff = (cur_price - entry) if is_buy else (entry - cur_price)
                calc_profit = round(diff * lot * 100.0, 2)
                open_positions.append({
                    "ticket": tr.get("ticket"),
                    "direction": tr.get("direction"),
                    "price_open": entry,
                    "price_current": cur_price,
                    "sl": tr.get("sl_price"),
                    "tp": tr.get("tp_price"),
                    "lot": lot,
                    "profit": calc_profit
                })

        # 2. Settings from SQLite
        risk_setting_val = await db.get_setting("risk_percent", str(config.RISK_PER_TRADE_PERCENT))
        try:
            current_risk = float(risk_setting_val)
        except (ValueError, TypeError):
            current_risk = config.RISK_PER_TRADE_PERCENT

        killswitch_val = await db.get_setting("trading_enabled", "1")
        trading_enabled = killswitch_val == "1"

        # 3. News Filter Status
        news_allowed, news_reason = await news_filter.is_trading_allowed()
        upcoming_news = await news_filter.fetch_high_impact_news()

        # 4. Signals, Trades, and Channels
        latest_signals = await db.get_latest_signals(limit=10)
        trade_history = await db.get_trade_history(limit=10)
        performance = await db.get_performance_summary()
        active_channels = await db.get_active_channels()

        return {
            "success": True,
            "status": "ONLINE" if trading_enabled else "PAUSED",
            "trading_enabled": trading_enabled,
            "account": account,
            "symbol": config.MT5_SYMBOL,
            "price": price,
            "settings": {
                "risk_percent": current_risk,
                "min_risk": config.MIN_RISK_PERCENT,
                "max_risk": config.MAX_RISK_PERCENT,
                "target_rr": config.TARGET_RR,
                "max_sl_pips": config.MAX_SL_PIPS
            },
            "news_filter": {
                "trading_allowed": news_allowed,
                "reason": news_reason,
                "upcoming_news": upcoming_news[:3]
            },
            "open_positions": open_positions,
            "latest_signals": latest_signals,
            "trade_history": trade_history,
            "performance": performance,
            "channels": active_channels,
            "ea_bridge": mt5_bridge.get_ea_status()
        }
    except Exception as e:
        logger.error(f"Error fetching Mini App status: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.post("/api/settings/risk")
async def update_risk_setting(req: RiskUpdateRequest) -> Dict[str, Any]:
    """
    Updates the system risk percentage with Strict Risk Guardrail [1.0% - 5.0%].
    """
    try:
        # Validate and clamp via core guardrail function
        clamped_risk = validate_and_clamp_risk_percent(req.risk_percent)
        await db.set_setting("risk_percent", str(clamped_risk), "Risk per trade in percent [1.0 - 5.0]")
        
        logger.info(f"Mini App updated risk setting: requested={req.risk_percent}%, applied={clamped_risk}%")
        return {
            "success": True,
            "risk_percent": clamped_risk,
            "requested_risk": req.risk_percent,
            "was_clamped": clamped_risk != req.risk_percent,
            "message": f"Risk muvaffaqiyatli {clamped_risk}% ga o'rnatildi"
        }
    except Exception as e:
        logger.error(f"Failed to update risk setting: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.post("/api/settings/killswitch")
async def toggle_killswitch(req: KillSwitchRequest) -> Dict[str, Any]:
    """
    Toggles or sets the trading kill-switch (trading_enabled).
    """
    try:
        if req.enabled is not None:
            new_state = req.enabled
        else:
            current = await db.get_setting("trading_enabled", "1")
            new_state = (current != "1")

        db_val = "1" if new_state else "0"
        await db.set_setting("trading_enabled", db_val, "Master kill-switch for automated trading")

        status_str = "YOQILDI (Faol)" if new_state else "O'CHIRILDI (To'xtatildi)"
        logger.info(f"Mini App Kill-switch updated: {status_str}")

        return {
            "success": True,
            "trading_enabled": new_state,
            "message": f"Avtomatik savdo {status_str}"
        }
    except Exception as e:
        logger.error(f"Failed to toggle kill-switch: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.post("/api/trades/open")
async def open_trade_endpoint(req: OpenTradeRequest) -> Dict[str, Any]:
    """
    Manually or programmatically executes a trade via the MT5 Bridge.
    Routes to connected Exness MT5 EA, Native MT5, or Simulator.
    """
    try:
        cur_price = mt5_bridge.get_current_price()["mid"]
        price = req.price if req.price is not None else cur_price
        res = await asyncio.to_thread(
            mt5_bridge.execute_order,
            action=req.action.upper(),
            order_type=req.order_type.upper(),
            price=price,
            lot=req.lot,
            sl=req.sl or 0.0,
            tp=req.tp or 0.0,
            comment="RTB Manual/API"
        )
        if res.get("success"):
            ticket = res.get("ticket", 0)
            status_val = res.get("status", "OPEN")
            await db.open_trade(
                ticket=ticket,
                direction=req.action.upper(),
                entry_price=res.get("price", price),
                sl_price=req.sl or 0.0,
                tp_price=req.tp or 0.0,
                risk_percent=1.0,
                risk_usd=0.0,
                lot_size=req.lot,
                status=status_val
            )
        return res
    except Exception as e:
        logger.error(f"Error opening trade: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.post("/api/trades/close")
async def close_trade(req: CloseTradeRequest) -> Dict[str, Any]:
    """
    Manually closes an open position from the Mini App.
    """
    try:
        res = await asyncio.to_thread(mt5_bridge.close_position, ticket=req.ticket, lot=req.lot)
        if res.get("success"):
            # Update DB trade status if ticket matches
            open_trades = await db.get_open_trades()
            for tr in open_trades:
                if tr["ticket"] == req.ticket:
                    await db.close_trade(
                        trade_id=tr["id"],
                        status="CLOSED_MANUAL",
                        pnl_usd=0.0,
                        pnl_r=0.0,
                        close_reason="Closed manually from Mini App"
                    )
                    break

            return {
                "success": True,
                "ticket": req.ticket,
                "message": f"#{req.ticket} raqamli pozitsiya muvaffaqiyatli yopildi"
            }
        else:
            return {
                "success": False,
                "ticket": req.ticket,
                "error": res.get("error", "Pozitsiyani yopib bo'lmadi")
            }
    except Exception as e:
        logger.error(f"Error closing position #{req.ticket}: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


# ==========================================
# EXNESS MT5 EA BRIDGE ENDPOINTS
# ==========================================

@app.post("/api/mt5/heartbeat")
async def mt5_ea_heartbeat(req: MT5HeartbeatRequest) -> Dict[str, Any]:
    """
    Heartbeat and Telemetry endpoint called by remote MQL5 EA (RTB_Exness_Bridge.mq5).
    Synchronizes balance, equity, live quotes, and returns pending trade actions.
    """
    try:
        data = req.dict()
        pending_actions = mt5_bridge.handle_ea_heartbeat(data)
        return {
            "success": True,
            "server_time": time.time(),
            "actions": pending_actions
        }
    except Exception as e:
        logger.error(f"Error handling MT5 EA heartbeat: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.get("/api/mt5/actions")
async def mt5_get_actions() -> Dict[str, Any]:
    """
    Polls pending actions for MT5 EA.
    """
    try:
        actions = mt5_bridge.get_pending_ea_actions()
        return {
            "success": True,
            "actions": actions
        }
    except Exception as e:
        logger.error(f"Error getting pending MT5 actions: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.post("/api/mt5/confirm")
async def mt5_ea_confirm(req: MT5ConfirmRequest) -> Dict[str, Any]:
    """
    Confirmation callback called by remote MQL5 EA once an action is executed on MT5.
    """
    try:
        mt5_bridge.confirm_ea_action(
            action_id=req.action_id,
            success=req.success,
            ticket=req.ticket or 0,
            price=req.price or 0.0,
            lot=req.lot or 0.0,
            error=req.error or ""
        )
        return {"success": True, "action_id": req.action_id, "confirmed": True}
    except Exception as e:
        logger.error(f"Error confirming MT5 EA action: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.get("/api/mt5/status")
async def mt5_ea_status() -> Dict[str, Any]:
    """
    Returns current status of the remote Exness MT5 EA connection.
    """
    return {
        "success": True,
        "ea_bridge": mt5_bridge.get_ea_status()
    }


@app.get("/api/mt5/download-ea")
async def download_ea_script():
    """
    Serves the RTB_Exness_Bridge.mq5 Expert Advisor source file for download.
    """
    ea_file = BASE_DIR / "execution_engine" / "mt5_ea" / "RTB_Exness_Bridge.mq5"
    if not ea_file.exists():
        raise HTTPException(status_code=404, detail="RTB_Exness_Bridge.mq5 file not found")
    return FileResponse(
        str(ea_file),
        media_type="text/plain",
        filename="RTB_Exness_Bridge.mq5"
    )


# ==========================================
# AI CHAT ENDPOINT (GEMINI PRO)
# ==========================================

@app.post("/api/ai/chat")
async def ai_chat_endpoint(req: ChatMessageRequest) -> Dict[str, Any]:
    """
    Direct Senior Gold Trader AI chat powered by Gemini Flash with live market context.
    """
    try:
        price_info = mt5_bridge.get_current_price()
        active_channels = await db.get_active_channels()
        recent_signals = await db.get_latest_signals(limit=5)
        current_risk = await db.get_setting("risk_percent", str(config.RISK_PER_TRADE_PERCENT))

        system_context = {
            "current_price": price_info.get("mid"),
            "bid_price": price_info.get("bid"),
            "ask_price": price_info.get("ask"),
            "channels": active_channels,
            "recent_signals": recent_signals,
            "risk_percent": current_risk
        }

        # Non-blocking async execution with 20s timeout
        reply = await asyncio.wait_for(
            asyncio.to_thread(groq_parser.chat_with_ai, req.message, system_context),
            timeout=20.0
        )
        return {"success": True, "reply": reply}
    except asyncio.TimeoutError:
        logger.warning("AI Chat execution timed out after 20s")
        return JSONResponse(
            status_code=504,
            content={
                "success": False,
                "error": "Timeout",
                "reply": "Ustoz, tahlil serverida javob berish biroz kechikdi. Iltimos, savolingizni qayta yuboring."
            }
        )
    except Exception as e:
        logger.error(f"AI Chat error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e), "reply": f"Xatolik yuz berdi: {str(e)}"})


# ==========================================
# CHANNELS CRUD ENDPOINTS
# ==========================================

@app.get("/api/channels")
async def get_channels_endpoint() -> Dict[str, Any]:
    """Returns all active channels being monitored."""
    try:
        channels = await db.get_active_channels()
        return {"success": True, "channels": channels}
    except Exception as e:
        logger.error(f"Error fetching channels: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.post("/api/channels")
async def add_channel_endpoint(req: AddChannelRequest) -> Dict[str, Any]:
    """Adds a new channel to monitor."""
    try:
        chan_id = req.channel_id.strip()
        title = req.title.strip()
        if not chan_id or not title:
            return JSONResponse(status_code=400, content={"success": False, "error": "Kanal nomi va ID kiritilishi shart"})

        weight = float(req.weight if req.weight is not None else 1.5)
        if weight < 1.0 or weight > 10.0:
            return JSONResponse(status_code=400, content={"success": False, "error": "Kanal og'irligi 1.0 dan 10.0 gacha bo'lishi shart"})

        weight = round(max(1.0, min(10.0, weight)), 2)

        await db.add_channel(
            channel_id=chan_id,
            title=title,
            weight=weight
        )
        channels = await db.get_active_channels()
        return {"success": True, "message": f"Kanal '{title}' (og'irlik: {weight}) muvaffaqiyatli qo'shildi", "channels": channels}
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.post("/api/channels/delete")
async def delete_channel_post(req: DeleteChannelRequest) -> Dict[str, Any]:
    """Deletes a channel via POST JSON (100% reliable across all Telegram WebViews)."""
    try:
        await db.remove_channel(req.channel_id)
        channels = await db.get_active_channels()
        return {"success": True, "message": "Kanal muvaffaqiyatli o'chirildi", "channels": channels}
    except Exception as e:
        logger.error(f"Error removing channel: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.delete("/api/channels/{channel_id:path}")
async def remove_channel_endpoint(channel_id: str) -> Dict[str, Any]:
    """Removes/deactivates a channel."""
    try:
        await db.remove_channel(channel_id)
        channels = await db.get_active_channels()
        return {"success": True, "message": "Kanal kuzatuvdan olib tashlandi", "channels": channels}
    except Exception as e:
        logger.error(f"Error removing channel: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

