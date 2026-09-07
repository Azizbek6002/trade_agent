import logging
from typing import Dict, Optional, Any
from news_filter.economic_calendar import news_filter
from risk_manager.risk_manager import risk_manager
from execution_engine.mt5_bridge import mt5_bridge
from database import db
from config import config

logger = logging.getLogger(__name__)


class ExecutionPlanner:
    """
    RTB v2.0 Trade Execution Planner.
    Coordinates:
    1. News & Risk Gatekeepers (`news_filter.is_trading_allowed()`).
    2. Execution type decision (Pending LIMIT vs MARKET order).
    3. Dynamic Lot Size calculation with Strict Risk Guardrail (1.0% - 5.0%).
    4. Execution dispatch via `mt5_bridge`.
    """

    def decide_execution_type(self, setup: Dict[str, Any], current_price: float) -> Dict[str, Any]:
        direction = setup["direction"]
        zone_min = setup["zone_min"]
        zone_max = setup["zone_max"]
        confidence = setup.get("confidence_score", 0.0)

        # High confidence setup (>= 75%) -> Place Limit Order
        if confidence >= 75.0:
            limit_price = zone_max if direction == "BUY" else zone_min
            return {
                "execution_mode": "LIMIT",
                "order_type": "LIMIT",
                "price": round(limit_price, 2),
                "reason": f"High confidence consensus setup ({confidence}%)"
            }

        # Price currently inside entry zone -> Market Order
        is_price_in_zone = zone_min <= current_price <= zone_max
        if is_price_in_zone:
            return {
                "execution_mode": "MARKET",
                "order_type": "MARKET",
                "price": round(current_price, 2),
                "reason": f"Price inside consensus zone [{zone_min} - {zone_max}]"
            }

        return {
            "execution_mode": "WAIT",
            "order_type": "NONE",
            "price": None,
            "reason": f"Waiting for price ({current_price}) to reach entry zone [{zone_min} - {zone_max}]"
        }

    async def evaluate_and_execute_setup(
        self,
        setup: Dict[str, Any],
        custom_risk_percent: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        End-to-end setup execution gatekeeper:
        1. Checks Economic News Filter & Circuit Breaker.
        2. Retrieves account balance & symbol parameters.
        3. Calculates exact lot size using strict guardrail.
        4. Dispatches order to MT5.
        5. Logs trade to SQLite `trades` table.
        """
        # 1. Gatekeeper Check
        allowed, gate_reason = await news_filter.is_trading_allowed()
        if not allowed:
            logger.warning(f"Setup execution rejected by gatekeeper: {gate_reason}")
            return {"success": False, "reason": gate_reason}

        # 2. Get live market price & account balance
        price_info = mt5_bridge.get_current_price()
        current_price = price_info["mid"]
        account_info = mt5_bridge.get_account_info()
        balance = account_info.get("balance", 5000.0)

        # 3. Determine order execution parameters
        exec_plan = self.decide_execution_type(setup, current_price)
        if exec_plan["execution_mode"] == "WAIT":
            return {"success": False, "reason": exec_plan["reason"]}

        order_price = exec_plan["price"]
        sl_price = setup.get("sl")
        tp_price = setup.get("tp") or (setup.get("tp_targets", [None])[0])

        if not sl_price:
            return {"success": False, "reason": "Missing Stop Loss in setup."}

        # 4. Dynamic Lot Calculation (Strict Guardrail 1.0% - 5.0%)
        db_risk = await db.get_setting("risk_percent", str(config.RISK_PER_TRADE_PERCENT))
        target_risk = custom_risk_percent or float(db_risk)

        sym_info = mt5_bridge.get_symbol_info()
        lot, risk_usd, eff_risk, err = risk_manager.calculate_lot_size(
            account_balance=balance,
            entry_price=order_price,
            sl_price=sl_price,
            risk_percent=target_risk,
            contract_size=sym_info["contract_size"],
            min_lot=sym_info["min_lot"],
            max_lot=sym_info["max_lot"],
            lot_step=sym_info["lot_step"]
        )

        if err:
            return {"success": False, "reason": err}

        # 5. Dispatch Order to MT5 Bridge
        direction = setup["direction"]
        mt5_res = mt5_bridge.execute_order(
            action=direction,
            order_type=exec_plan["order_type"],
            price=order_price,
            lot=lot,
            sl=sl_price,
            tp=tp_price,
            comment=f"RTB {setup.get('confidence_score', 0)}%"
        )

        if not mt5_res.get("success"):
            return {"success": False, "reason": mt5_res.get("error", "MT5 execution failed")}

        ticket = mt5_res["ticket"]

        # 6. Record open trade in SQLite
        sources_list = [s.get("source", "UNKNOWN") for s in setup.get("sources", [])]
        trade_id = await db.open_trade(
            ticket=ticket,
            direction=direction,
            entry_price=order_price,
            sl_price=sl_price,
            tp_price=tp_price or 0.0,
            risk_percent=eff_risk,
            risk_usd=risk_usd,
            lot_size=lot,
            sources=sources_list
        )

        logger.info(
            f"🎉 Trade #{ticket} successfully executed & logged (DB ID: {trade_id})! "
            f"{direction} {lot} lots @ {order_price} | Risk: {eff_risk}% (${risk_usd})"
        )

        return {
            "success": True,
            "trade_id": trade_id,
            "ticket": ticket,
            "lot": lot,
            "price": order_price,
            "risk_usd": risk_usd,
            "risk_percent": eff_risk
        }


execution_planner = ExecutionPlanner()
