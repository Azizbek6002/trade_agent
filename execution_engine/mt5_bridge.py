import logging
import time
from typing import Dict, List, Optional, Any
import pandas as pd
from config import config

logger = logging.getLogger(__name__)

# Try importing MetaTrader5 natively (Windows or Wine python)
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 python module not installed natively. Operating in Simulated / Bridge mode.")


class MT5Bridge:
    """
    Exness MT5 Execution Bridge (RTB v2.0).
    Lightweight, fast interface for MetaTrader 5 terminal:
    - Order Execution: Market (BUY/SELL) and Pending (BUY_LIMIT/SELL_LIMIT).
    - Exness Filling Modes Fallback (IOC -> FOK -> RETURN).
    - Position Management: Modify SL/TP, Partial/Full Close, Cancel Pending.
    - Real-time ticks and symbol specifications.
    - Seamless simulated fallback for development/testing on Linux.
    """

    def __init__(self, symbol: str = config.MT5_SYMBOL):
        self.symbol = symbol
        self.is_connected = False
        self._simulated_positions: Dict[int, Dict[str, Any]] = {}
        self._simulated_ticket_counter = 100000
        self._last_price: Dict[str, float] = {"bid": 4391.116, "ask": 4391.376, "mid": 4391.246}
        self._last_price_time: float = 0.0

    def initialize(self) -> bool:
        if not MT5_AVAILABLE:
            logger.info("MT5 native module missing. Operating in high-performance Simulator mode.")
            self.is_connected = True
            return True

        try:
            if not mt5.initialize():
                logger.error(f"MT5 initialize failed: {mt5.last_error()}")
                return False

            account = config.MT5_ACCOUNT
            password = config.MT5_PASSWORD
            server = config.MT5_SERVER

            if account and password:
                authorized = mt5.login(account, password=password, server=server)
                if not authorized:
                    logger.error(f"MT5 Login failed for account {account}: {mt5.last_error()}")
                    return False

            # Select symbol
            if not mt5.symbol_select(self.symbol, True):
                # Try fallback standard symbol if suffix missing (e.g. XAUUSD instead of XAUUSDm)
                alt_sym = "XAUUSD" if self.symbol.endswith("m") else f"{self.symbol}m"
                if mt5.symbol_select(alt_sym, True):
                    self.symbol = alt_sym
                    logger.info(f"Switched MT5 symbol to {self.symbol}")
                else:
                    logger.error(f"Failed to select symbol {self.symbol} in MT5.")
                    return False

            self.is_connected = True
            logger.info(f"🟢 Exness MT5 Connected successfully. Server: {server}, Symbol: {self.symbol}")
            return True
        except Exception as e:
            logger.error(f"Error during MT5 initialization: {e}")
            return False

    def get_account_info(self) -> Dict[str, float]:
        if MT5_AVAILABLE and self.is_connected:
            try:
                info = mt5.account_info()
                if info:
                    return {
                        "balance": float(info.balance),
                        "equity": float(info.equity),
                        "margin": float(info.margin),
                        "free_margin": float(info.margin_free),
                        "currency": getattr(info, "currency", "USD")
                    }
            except Exception as e:
                logger.warning(f"Failed to read MT5 account info: {e}")

        # Default fallback / simulation
        return {
            "balance": 5000.0,
            "equity": 5000.0,
            "margin": 0.0,
            "free_margin": 5000.0,
            "currency": "USD"
        }

    def get_symbol_info(self) -> Dict[str, Any]:
        """Returns symbol specification parameters for risk and lot calculation."""
        if MT5_AVAILABLE and self.is_connected:
            try:
                sym_info = mt5.symbol_info(self.symbol)
                if sym_info:
                    return {
                        "symbol": self.symbol,
                        "point": sym_info.point,
                        "spread": sym_info.spread,
                        "contract_size": float(sym_info.trade_contract_size or 100.0),
                        "min_lot": float(sym_info.volume_min or 0.01),
                        "max_lot": float(sym_info.volume_max or 100.0),
                        "lot_step": float(sym_info.volume_step or 0.01)
                    }
            except Exception as e:
                logger.warning(f"Failed to fetch symbol info: {e}")

        # Standard Exness XAUUSD specifications
        return {
            "symbol": self.symbol,
            "point": 0.01,
            "spread": 20,
            "contract_size": 100.0,  # 100 oz per lot in Gold
            "min_lot": 0.01,
            "max_lot": 100.0,
            "lot_step": 0.01
        }

    def get_current_price(self) -> Dict[str, float]:
        """
        Returns real-time live market price formatted with Exness 3-decimal precision:
        e.g. Bid: 4391.116, Ask: 4391.376 (standard ~0.260 spread).
        """
        if MT5_AVAILABLE and self.is_connected:
            try:
                tick = mt5.symbol_info_tick(self.symbol)
                if tick and getattr(tick, "bid", 0) > 0:
                    bid = round(float(tick.bid), 3)
                    ask = round(float(tick.ask), 3)
                    mid = round((bid + ask) / 2.0, 3)
                    return {"bid": bid, "ask": ask, "mid": mid}
            except Exception as e:
                logger.warning(f"Failed to get live MT5 tick: {e}")

        # Real-time live market feed for Linux / Bridge mode
        now = time.time()
        if now - self._last_price_time < 2.0 and self._last_price:
            return self._last_price

        try:
            import urllib.request
            import json
            req = urllib.request.Request(
                "https://api.binance.com/api/v3/ticker/bookTicker?symbol=PAXGUSDT",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                market_bid = float(data["bidPrice"])
                market_ask = float(data["askPrice"])
                mid = (market_bid + market_ask) / 2.0

                # Exness Gold typical spread is 0.260 points (26 pips) with 3 decimals
                bid = round(mid - 0.130, 3)
                ask = round(mid + 0.130, 3)
                mid_rounded = round(mid, 3)

                self._last_price = {"bid": bid, "ask": ask, "mid": mid_rounded}
                self._last_price_time = now
                return self._last_price
        except Exception as err:
            logger.debug(f"Live market price fetcher fallback: {err}")
            # Micro dynamic jitter around previous price if network hiccup
            import random
            jitter = round(random.uniform(-0.025, 0.025), 3)
            current_mid = round(self._last_price.get("mid", 4391.246) + jitter, 3)
            self._last_price = {
                "bid": round(current_mid - 0.130, 3),
                "ask": round(current_mid + 0.130, 3),
                "mid": current_mid
            }
            self._last_price_time = now
            return self._last_price

    def execute_order(
        self,
        action: str,            # "BUY" or "SELL"
        order_type: str,        # "MARKET" or "LIMIT"
        price: float,
        lot: float,
        sl: float,
        tp: float,
        comment: str = "RTB v2.0"
    ) -> Dict[str, Any]:
        """
        Executes order on Exness MT5 with multi-filling mode fallback.
        Supports Market and Limit orders.
        """
        lot = round(float(lot), 2)
        price = round(float(price), 2)
        sl = round(float(sl), 2) if sl else 0.0
        tp = round(float(tp), 2) if tp else 0.0

        if not MT5_AVAILABLE or not self.is_connected:
            self._simulated_ticket_counter += 1
            ticket = self._simulated_ticket_counter
            self._simulated_positions[ticket] = {
                "ticket": ticket,
                "action": action,
                "order_type": order_type,
                "price": price,
                "lot": lot,
                "sl": sl,
                "tp": tp,
                "status": "OPEN",
                "open_time": time.time()
            }
            logger.info(
                f"[SIMULATOR] Order opened #{ticket}: {action} {order_type} | Lot: {lot} @ {price} | SL: {sl}, TP: {tp}"
            )
            return {
                "success": True,
                "ticket": ticket,
                "price": price,
                "lot": lot,
                "order_type": order_type,
                "comment": "Simulated Execution"
            }

        # Determine MT5 order constants
        is_limit = "LIMIT" in order_type.upper()
        if is_limit:
            mt5_action = mt5.TRADE_ACTION_PENDING
            mt5_type = mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
        else:
            mt5_action = mt5.TRADE_ACTION_DEAL
            mt5_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

        # Exness filling modes fallback order: IOC -> FOK -> RETURN
        filling_modes = [
            getattr(mt5, "ORDER_FILLING_IOC", 1),
            getattr(mt5, "ORDER_FILLING_FOK", 0),
            getattr(mt5, "ORDER_FILLING_RETURN", 2)
        ]

        last_comment = ""
        for filling_mode in filling_modes:
            request = {
                "action": mt5_action,
                "symbol": self.symbol,
                "volume": lot,
                "type": mt5_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 25,
                "magic": 20260907,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }

            try:
                result = mt5.order_send(request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(
                        f"🟢 MT5 Order executed successfully! Ticket #{result.order} "
                        f"({action} {lot} lots @ {result.price}, Filling: {filling_mode})"
                    )
                    return {
                        "success": True,
                        "ticket": int(result.order),
                        "price": float(result.price),
                        "lot": lot,
                        "order_type": order_type,
                        "comment": "Order Send Done"
                    }
                else:
                    last_comment = result.comment if result else "Unknown error"
                    logger.debug(f"Filling mode {filling_mode} rejected: {last_comment}. Trying next mode...")
            except Exception as e:
                last_comment = str(e)

        logger.error(f"❌ MT5 Order execution failed after trying all filling modes: {last_comment}")
        return {"success": False, "error": last_comment}

    def modify_sl(self, ticket: int, new_sl: float) -> bool:
        """Convenience method to modify Stop Loss."""
        return self.modify_sl_tp(ticket, new_sl=new_sl)

    def modify_sl_tp(self, ticket: int, new_sl: Optional[float] = None, new_tp: Optional[float] = None) -> bool:
        """Modifies Stop Loss and/or Take Profit on an open position or pending order."""
        if not MT5_AVAILABLE or not self.is_connected:
            if ticket in self._simulated_positions:
                if new_sl is not None:
                    self._simulated_positions[ticket]["sl"] = round(new_sl, 2)
                if new_tp is not None:
                    self._simulated_positions[ticket]["tp"] = round(new_tp, 2)
            logger.info(f"[SIMULATOR] Modified SL/TP for ticket #{ticket} -> SL: {new_sl}, TP: {new_tp}")
            return True

        try:
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                logger.warning(f"Position #{ticket} not found in MT5 to modify.")
                return False

            pos = positions[0]
            target_sl = round(new_sl, 2) if new_sl is not None else pos.sl
            target_tp = round(new_tp, 2) if new_tp is not None else pos.tp

            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "symbol": pos.symbol,
                "sl": target_sl,
                "tp": target_tp
            }
            res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"Position #{ticket} SL/TP updated -> SL: {target_sl}, TP: {target_tp}")
                return True
            else:
                logger.error(f"Failed to modify SL/TP for #{ticket}: {res.comment if res else 'Unknown'}")
                return False
        except Exception as e:
            logger.error(f"Error modifying SL/TP: {e}")
            return False

    def close_position(self, ticket: int, lot: Optional[float] = None) -> Dict[str, Any]:
        """
        Closes an open position on MT5.
        If lot is provided, executes partial close; otherwise closes full volume.
        """
        if not MT5_AVAILABLE or not self.is_connected:
            pos = self._simulated_positions.pop(ticket, None)
            close_price = 2655.0
            close_lot = lot or (pos.get("lot") if pos else 0.01)
            logger.info(f"[SIMULATOR] Position #{ticket} closed ({close_lot} lots @ {close_price})")
            return {"success": True, "ticket": ticket, "close_price": close_price, "lot": close_lot}

        try:
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                return {"success": False, "error": f"Position #{ticket} not found"}

            pos = positions[0]
            close_lot = lot if lot is not None else pos.volume
            close_lot = round(min(close_lot, pos.volume), 2)

            tick = mt5.symbol_info_tick(pos.symbol)
            close_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": ticket,
                "symbol": pos.symbol,
                "volume": close_lot,
                "type": close_type,
                "price": close_price,
                "deviation": 25,
                "magic": 20260907,
                "comment": "RTB Close Position",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"Position #{ticket} closed successfully at {res.price}")
                return {"success": True, "ticket": ticket, "close_price": res.price, "lot": close_lot}
            else:
                comment = res.comment if res else "Order Send failed"
                logger.error(f"Failed to close position #{ticket}: {comment}")
                return {"success": False, "error": comment}
        except Exception as e:
            logger.error(f"Error in close_position: {e}")
            return {"success": False, "error": str(e)}

    def cancel_order(self, ticket: int) -> bool:
        """Cancels a pending limit order on MT5."""
        if not MT5_AVAILABLE or not self.is_connected:
            self._simulated_positions.pop(ticket, None)
            logger.info(f"[SIMULATOR] Order #{ticket} cancelled.")
            return True

        try:
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": ticket
            }
            res = mt5.order_send(request)
            return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)
        except Exception as e:
            logger.error(f"Error cancelling pending order #{ticket}: {e}")
            return False

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Returns all open positions for the monitored symbol."""
        if not MT5_AVAILABLE or not self.is_connected:
            return list(self._simulated_positions.values())

        try:
            positions = mt5.positions_get(symbol=self.symbol)
            if positions is None:
                return []
            result = []
            for p in positions:
                result.append({
                    "ticket": p.ticket,
                    "direction": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                    "price_open": p.price_open,
                    "price_current": p.price_current,
                    "sl": p.sl,
                    "tp": p.tp,
                    "lot": p.volume,
                    "profit": p.profit
                })
            return result
        except Exception as e:
            logger.error(f"Error getting open positions: {e}")
            return []


mt5_bridge = MT5Bridge()
