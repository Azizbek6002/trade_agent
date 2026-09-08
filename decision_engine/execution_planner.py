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

    def calculate_zone_levels(self, direction: str, zone_min: float, zone_max: float) -> list:
        """
        Splits entry zone into 3 proportional entry levels across the range:
        e.g. For SELL 4435-4440 (range=5): 4436.7, 4438.4, 4440.0
        e.g. For BUY 4435-4440 (range=5): 4438.3, 4436.7, 4435.0
        """
        diff = round(zone_max - zone_min, 2)
        if diff < 1.0:
            return [round((zone_min + zone_max) / 2.0, 2)]

        step = diff / 3.0
        if direction == "SELL":
            p1 = round(zone_min + step, 2)
            p2 = round(zone_min + 2.0 * step, 2)
            p3 = round(zone_max, 2)
            return [p1, p2, p3]
        else:
            p1 = round(zone_max - step, 2)
            p2 = round(zone_max - 2.0 * step, 2)
            p3 = round(zone_min, 2)
            return [p1, p2, p3]

    async def evaluate_and_execute_setup(
        self,
        setup: Dict[str, Any],
        custom_risk_percent: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        End-to-end setup execution gatekeeper with 3-Step Zone Grid:
        1. Checks Economic News Filter & Circuit Breaker.
        2. Retrieves account balance & symbol parameters.
        3. Divides zone into 3 entry levels (Limit / Market).
        4. Calculates exact lot size for each level using strict risk guardrail.
        5. Dispatches orders to MT5.
        6. Logs trades to SQLite `trades` table.
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

        direction = setup["direction"]
        zone_min = float(setup["zone_min"])
        zone_max = float(setup["zone_max"])
        sl_price = float(setup["sl"]) if setup.get("sl") else None

        if not sl_price:
            return {"success": False, "reason": "Missing Stop Loss in setup."}

        # Check if price already breached SL
        if direction == "BUY" and current_price <= sl_price:
            return {"success": False, "reason": f"Narx allaqachon Stop Loss ({sl_price}) darajasidan o'tib ketgan."}
        if direction == "SELL" and current_price >= sl_price:
            return {"success": False, "reason": f"Narx allaqachon Stop Loss ({sl_price}) darajasidan o'tib ketgan."}

        # 3. Calculate 3 grid levels across the zone
        levels = self.calculate_zone_levels(direction, zone_min, zone_max)

        db_risk = await db.get_setting("risk_percent", str(config.RISK_PER_TRADE_PERCENT))
        target_risk = custom_risk_percent or float(db_risk)
        sym_info = mt5_bridge.get_symbol_info()
        sources_list = [s.get("source", "UNKNOWN") for s in setup.get("sources", [])]

        executed_orders = []

        for idx, lvl_price in enumerate(levels):
            # Decide if this level is Market or Limit:
            if direction == "SELL":
                if current_price >= lvl_price - 0.3:
                    order_type = "MARKET"
                    order_price = round(current_price, 2)
                else:
                    order_type = "LIMIT"
                    order_price = round(lvl_price, 2)
            else:  # BUY
                if current_price <= lvl_price + 0.3:
                    order_type = "MARKET"
                    order_price = round(current_price, 2)
                else:
                    order_type = "LIMIT"
                    order_price = round(lvl_price, 2)

            # Calculate TP for this entry (shared setup TP or 1:3 RR)
            tp_price = setup.get("tp")
            tp_targets = setup.get("tp_targets") or []
            if not tp_price and len(tp_targets) > 0 and tp_targets[0] is not None:
                tp_price = float(tp_targets[0])

            if not tp_price and sl_price:
                sl_dist = abs(order_price - sl_price)
                target_rr = getattr(config, "TARGET_RR", 3.0)
                tp_price = round(order_price - (target_rr * sl_dist), 2) if direction == "SELL" else round(order_price + (target_rr * sl_dist), 2)

            # Calculate lot size with risk
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
                logger.warning(f"Level {idx+1} lot calculation error: {err}")
                continue

            # Dispatch order to MT5 Bridge
            mt5_res = mt5_bridge.execute_order(
                action=direction,
                order_type=order_type,
                price=order_price,
                lot=lot,
                sl=sl_price,
                tp=tp_price,
                comment=f"RTB L{idx+1} {order_type}"
            )

            if not mt5_res.get("success"):
                logger.warning(f"Level {idx+1} execution rejected: {mt5_res.get('error')}")
                continue

            ticket = mt5_res["ticket"]
            status_val = mt5_res.get("status", "OPEN")

            trade_id = await db.open_trade(
                ticket=ticket,
                direction=direction,
                entry_price=order_price,
                sl_price=sl_price,
                tp_price=tp_price or 0.0,
                risk_percent=eff_risk,
                risk_usd=risk_usd,
                lot_size=lot,
                sources=sources_list,
                status=status_val
            )

            logger.info(
                f"🎉 Grid Level {idx+1}/{len(levels)} #{ticket} placed: {direction} {order_type} "
                f"{lot} lots @ {order_price} | SL: {sl_price}, TP: {tp_price} | Status: {status_val}"
            )

            executed_orders.append({
                "ticket": ticket,
                "trade_id": trade_id,
                "order_type": order_type,
                "price": order_price,
                "lot": lot,
                "status": status_val
            })

        if executed_orders:
            return {
                "success": True,
                "orders": executed_orders,
                "count": len(executed_orders),
                "ticket": executed_orders[0]["ticket"],
                "lot": executed_orders[0]["lot"],
                "price": executed_orders[0]["price"]
            }
        else:
            return {"success": False, "reason": "No grid orders could be executed"}

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
