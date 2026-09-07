import logging
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
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

TEMPLATES_DIR = BASE_DIR / "webapp" / "templates"


# Request Models
class RiskUpdateRequest(BaseModel):
    risk_percent: float = Field(..., description="Risk percent between 1.0% and 5.0%")


class KillSwitchRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="Set bot active state, or omit to toggle")


class CloseTradeRequest(BaseModel):
    ticket: int = Field(..., description="MT5 Position ticket number")
    lot: Optional[float] = Field(None, description="Optional partial lot to close")


class ChatMessageRequest(BaseModel):
    message: str = Field(..., description="Message from user")


class AddChannelRequest(BaseModel):
    channel_id: str = Field(..., description="Channel ID or username")
    title: str = Field(..., description="Channel title")
    weight: Optional[float] = Field(1.5, description="Channel source weight")


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

        # 4. Signals and Trades
        latest_signals = await db.get_latest_signals(limit=10)
        trade_history = await db.get_trade_history(limit=10)
        performance = await db.get_performance_summary()

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
            "performance": performance
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


@app.post("/api/trades/close")
async def close_trade(req: CloseTradeRequest) -> Dict[str, Any]:
    """
    Manually closes an open position from the Mini App.
    """
    try:
        res = mt5_bridge.close_position(ticket=req.ticket, lot=req.lot)
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
# AI CHAT ENDPOINT (GEMINI PRO)
# ==========================================

@app.post("/api/ai/chat")
async def ai_chat_endpoint(req: ChatMessageRequest) -> Dict[str, Any]:
    """
    Direct Senior Gold Trader AI chat powered by Gemini Pro with live market context.
    """
    try:
        price_info = mt5_bridge.get_current_price()
        active_channels = await db.get_active_channels()
        recent_signals = await db.get_latest_signals(limit=5)
        current_risk = await db.get_setting("risk_percent", str(config.RISK_PER_TRADE_PERCENT))

        system_context = {
            "current_price": price_info.get("mid"),
            "channels": active_channels,
            "recent_signals": recent_signals,
            "risk_percent": current_risk
        }

        reply = groq_parser.chat_with_ai(req.message, system_context)
        return {"success": True, "reply": reply}
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
        await db.add_channel(
            channel_id=req.channel_id,
            title=req.title,
            weight=req.weight or 1.5
        )
        return {"success": True, "message": f"Kanal '{req.title}' muvaffaqiyatli qo'shildi"}
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.delete("/api/channels/{channel_id}")
async def remove_channel_endpoint(channel_id: str) -> Dict[str, Any]:
    """Removes/deactivates a channel."""
    try:
        await db.remove_channel(channel_id)
        return {"success": True, "message": "Kanal kuzatuvdan olib tashlandi"}
    except Exception as e:
        logger.error(f"Error removing channel: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

