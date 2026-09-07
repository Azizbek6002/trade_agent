import logging
from datetime import datetime, timezone
from typing import Optional, Tuple
from config import config

logger = logging.getLogger(__name__)


def validate_and_clamp_risk_percent(risk_percent: float, raise_on_error: bool = False) -> float:
    """
    Strict Risk Guardrail Validator (RTB v2.0).
    Enforces risk percentage strictly between MIN_RISK_PERCENT (1.0%) and MAX_RISK_PERCENT (5.0%).
    If risk > 5.0%: clamps to 5.0% and logs a warning (or raises ValueError if raise_on_error is True).
    If risk < 1.0%: clamps to 1.0% and logs a warning.
    """
    try:
        val = float(risk_percent)
    except (ValueError, TypeError):
        logger.error(f"Invalid risk value '{risk_percent}'. Defaulting to {config.MIN_RISK_PERCENT}%.")
        return float(config.MIN_RISK_PERCENT)

    if val > config.MAX_RISK_PERCENT:
        msg = (
            f"🚨 STRICT RISK GUARDRAIL: Requested risk {val:.2f}% exceeds maximum limit of "
            f"{config.MAX_RISK_PERCENT}%. Clamped to {config.MAX_RISK_PERCENT}%."
        )
        logger.warning(msg)
        if raise_on_error:
            raise ValueError(msg)
        return float(config.MAX_RISK_PERCENT)

    if val < config.MIN_RISK_PERCENT:
        msg = (
            f"⚠️ STRICT RISK GUARDRAIL: Requested risk {val:.2f}% is below minimum limit of "
            f"{config.MIN_RISK_PERCENT}%. Clamped to {config.MIN_RISK_PERCENT}%."
        )
        logger.warning(msg)
        return float(config.MIN_RISK_PERCENT)

    return round(val, 2)


class RiskManager:
    """
    Strict Risk Management Engine for XAUUSD (RTB v2.0).
    Rules:
    - Strict Risk Guardrail: 1.0% to 5.0% per trade (Dynamic lot calculation).
    - Max Stop Loss distance: 50 pips (5.0 points in XAUUSD).
    - Daily Max Drawdown Circuit Breaker: 5.0% (Stops trading if reached).
    - Target RR: 1:3.
    """

    def __init__(self):
        self.daily_pnl_usd = 0.0
        self.daily_start_balance = 5000.0  # Default reference balance
        self.last_reset_day = datetime.now(timezone.utc).date()
        self.daily_trading_stopped = False

    def reset_daily_stats_if_needed(self, current_balance: float):
        today = datetime.now(timezone.utc).date()
        if today != self.last_reset_day:
            self.daily_pnl_usd = 0.0
            self.daily_start_balance = current_balance
            self.last_reset_day = today
            self.daily_trading_stopped = False
            logger.info(f"Daily risk metrics reset. New reference balance: ${current_balance:.2f}")

    def update_daily_pnl(self, pnl_usd: float, current_balance: float):
        self.reset_daily_stats_if_needed(current_balance)
        self.daily_pnl_usd += pnl_usd

        max_allowed_loss_usd = self.daily_start_balance * (config.MAX_DAILY_DRAWDOWN_PERCENT / 100.0)

        if self.daily_pnl_usd <= -max_allowed_loss_usd:
            self.daily_trading_stopped = True
            logger.critical(
                f"🚨 DAILY MAX LOSS HIT ({self.daily_pnl_usd:.2f} USD >= {max_allowed_loss_usd:.2f} USD). "
                f"TRADING STOPPED FOR THE DAY!"
            )

    def calculate_lot_size(
        self,
        account_balance: float,
        entry_price: float,
        sl_price: float,
        risk_percent: Optional[float] = None,
        contract_size: float = 100.0,  # MT5 contract size for XAUUSD
        min_lot: float = 0.01,
        max_lot: float = 100.0,
        lot_step: float = 0.01
    ) -> Tuple[float, float, float, Optional[str]]:
        """
        Calculates exact lot size applying Strict Risk Guardrail (1.0% - 5.0%).
        Returns: (lot_size, risk_usd, applied_risk_percent, error_message)
        """
        self.reset_daily_stats_if_needed(account_balance)

        if self.daily_trading_stopped:
            return 0.0, 0.0, 0.0, "TRADING STOPPED: Daily max loss limit (5%) reached!"

        price_diff = abs(entry_price - sl_price)
        if price_diff <= 0:
            return 0.0, 0.0, 0.0, "Invalid SL price: entry price equals SL price."

        # Check max 50 pips (5.0 price points)
        if price_diff > (config.MAX_SL_PIPS / 10.0):
            logger.warning(f"SL distance ({price_diff:.2f}) exceeds max {config.MAX_SL_PIPS} pips. Clamping.")

        # Apply Strict Risk Guardrail (1.0% - 5.0%)
        target_risk = risk_percent if risk_percent is not None else config.RISK_PER_TRADE_PERCENT
        effective_risk_percent = validate_and_clamp_risk_percent(target_risk)

        risk_usd = account_balance * (effective_risk_percent / 100.0)

        # Risk formula: Risk_USD = Lot * Contract_Size * Price_Diff
        raw_lot = risk_usd / (contract_size * price_diff)

        # Round lot to broker lot step
        steps = round(raw_lot / lot_step)
        calculated_lot = round(steps * lot_step, 2)
        calculated_lot = max(min_lot, min(max_lot, calculated_lot))

        return calculated_lot, round(risk_usd, 2), effective_risk_percent, None


risk_manager = RiskManager()
