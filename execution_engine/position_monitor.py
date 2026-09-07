import asyncio
import logging
from database import db
from execution_engine.mt5_bridge import mt5_bridge
from risk_manager.risk_manager import risk_manager

logger = logging.getLogger(__name__)

class PositionMonitor:
    """
    Monitors open trades continuously:
    - At +1R profit -> Moves SL to Break-Even (BE).
    - At +3R profit -> Executes Full TP.
    - Updates PnL and triggers risk manager daily drawdown breaker on losses.
    """

    def __init__(self, check_interval_sec: int = 5):
        self.check_interval_sec = check_interval_sec
        self.is_running = False

    async def start_monitoring(self):
        self.is_running = True
        logger.info("Position Monitor background worker started.")

        while self.is_running:
            try:
                await self._check_open_positions()
            except Exception as e:
                logger.error(f"Error in position monitor: {e}")
            await asyncio.sleep(self.check_interval_sec)

    async def _check_open_positions(self):
        # Fetch active signals and current price
        recent_signals = await db.get_recent_active_signals(max_age_hours=4)
        price_info = mt5_bridge.get_current_price()
        current_price = price_info["mid"]

        # In live mode, iterate open trades from MT5 or DB
        # Check BE (+1R) and TP (+3R) logic
        pass

    async def process_position_update(
        self,
        trade_id: int,
        ticket: int,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
        current_price: float,
        risk_usd: float
    ):
        sl_distance = abs(entry - sl)
        if sl_distance == 0:
            return

        current_move = (current_price - entry) if direction == "BUY" else (entry - current_price)
        current_r = current_move / sl_distance

        # Check +1R Break Even Trigger
        if current_r >= 1.0 and current_r < 3.0:
            be_price = entry + 0.20 if direction == "BUY" else entry - 0.20
            success = mt5_bridge.modify_sl(ticket, be_price)
            if success:
                logger.info(f"Trade #{ticket} reached +1R ({current_r:.2f}R). SL moved to Break Even ({be_price}).")

        # Check SL Hit (-1R)
        elif (direction == "BUY" and current_price <= sl) or (direction == "SELL" and current_price >= sl):
            pnl_usd = -risk_usd
            await db.close_trade(trade_id, "CLOSED_SL", pnl_usd, -1.0, "Stop Loss Hit")
            account_info = mt5_bridge.get_account_info()
            risk_manager.update_daily_pnl(pnl_usd, account_info["balance"])
            
            logger.info(f"Trade #{ticket} closed at SL (-1R). Loss: -${risk_usd:.2f}")

        # Check Full TP Hit (+3R)
        elif (direction == "BUY" and current_price >= tp) or (direction == "SELL" and current_price <= tp):
            pnl_usd = risk_usd * 3.0
            await db.close_trade(trade_id, "CLOSED_TP", pnl_usd, 3.0, "Full Take Profit Hit (+3R)")
            account_info = mt5_bridge.get_account_info()
            risk_manager.update_daily_pnl(pnl_usd, account_info["balance"])
            logger.info(f"🎉 Trade #{ticket} HIT FULL TP (+3R)! Profit: +${pnl_usd:.2f}")

position_monitor = PositionMonitor()
