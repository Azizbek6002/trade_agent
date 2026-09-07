import aiohttp
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple, Optional
from config import config
from database import db
from risk_manager.risk_manager import risk_manager

logger = logging.getLogger(__name__)

# Keywords that denote high-impact market moving news for USD / Gold
HIGH_IMPACT_KEYWORDS = [
    "CPI", "CONSUMER PRICE INDEX", "CORE CPI",
    "NON-FARM", "NFP", "NONFARM PAYROLLS",
    "FOMC", "FED INTEREST RATE", "FEDERAL FUNDS RATE",
    "UNEMPLOYMENT RATE", "PPI", "PRODUCER PRICE INDEX",
    "GDP", "JACKSON HOLE", "POWELL SPEECH"
]


class EconomicCalendarFilter:
    """
    High-Impact Economic News Filter & Trading Gatekeeper (RTB v2.0).
    Features:
    - Auto-pauses trading 15 minutes before and 15 minutes after High-Impact USD news (CPI, NFP, FOMC).
    - Rate-limited async calendar fetching with robust caching.
    - Master gatekeeper `is_trading_allowed()` combining:
      1. News Pause status
      2. Daily Max Drawdown Circuit Breaker
      3. SQLite settings `trading_enabled` flag
    """

    def __init__(self):
        # Primary & fallback open calendar feeds
        self.api_urls = [
            "https://npoint.io/docs/forexfactory",
            "https://raw.githubusercontent.com/datasets/forex-calendar/master/data.json"
        ]
        self.cached_events: List[Dict] = []
        self.last_fetch: Optional[datetime] = None
        self._manual_events: List[Dict] = []  # For testing & user-specified calendar events
        self.fetch_cooldown_seconds = 1800  # 30 minutes cache

    def add_manual_event(self, title: str, event_time: datetime, impact: str = "HIGH", currency: str = "USD"):
        """Allows injecting or testing specific high-impact events."""
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        self._manual_events.append({
            "title": title,
            "date": event_time.isoformat(),
            "impact": impact.upper(),
            "currency": currency.upper()
        })
        logger.info(f"Manual economic event registered: {title} at {event_time.strftime('%Y-%m-%d %H:%M UTC')}")

    def clear_manual_events(self):
        self._manual_events.clear()

    async def fetch_high_impact_news(self) -> List[Dict]:
        """Fetches high impact news for USD with local caching."""
        now = datetime.now(timezone.utc)

        # Return cache if within cooldown
        if self.last_fetch and (now - self.last_fetch).total_seconds() < self.fetch_cooldown_seconds:
            return self.cached_events + self._manual_events

        fetched_events = []
        try:
            async with aiohttp.ClientSession() as session:
                for url in self.api_urls:
                    try:
                        async with session.get(url, timeout=4) as response:
                            if response.status == 200:
                                data = await response.json()
                                if isinstance(data, list):
                                    for event in data:
                                        curr = str(event.get("currency", "")).upper()
                                        impact = str(event.get("impact", "")).upper()
                                        title = str(event.get("title", "")).upper()

                                        is_usd = curr in ["USD", "ALL"]
                                        is_high = impact in ["HIGH", "RED", "CRITICAL"] or any(k in title for k in HIGH_IMPACT_KEYWORDS)

                                        if is_usd and is_high:
                                            fetched_events.append(event)
                                    if fetched_events:
                                        self.cached_events = fetched_events
                                        self.last_fetch = now
                                        break
                    except Exception:
                        continue
        except Exception as e:
            logger.debug(f"Calendar fetch notice: {e}. Using cached and manual events.")

        return self.cached_events + self._manual_events

    async def is_news_pause_active(self) -> Tuple[bool, str]:
        """
        Checks if the current moment falls within the pre-news or post-news pause window
        (Default: 15 minutes before and 15 minutes after high-impact event).
        """
        events = await self.fetch_high_impact_news()
        now = datetime.now(timezone.utc)

        pre_margin = timedelta(minutes=config.NEWS_PAUSE_MINUTES_BEFORE)
        post_margin = timedelta(minutes=config.NEWS_PAUSE_MINUTES_AFTER)

        for event in events:
            try:
                date_str = event.get("date")
                if not date_str:
                    continue

                # Parse ISO timestamp
                if date_str.endswith("Z"):
                    event_dt = datetime.fromisoformat(date_str[:-1]).replace(tzinfo=timezone.utc)
                else:
                    event_dt = datetime.fromisoformat(date_str)
                    if event_dt.tzinfo is None:
                        event_dt = event_dt.replace(tzinfo=timezone.utc)

                pause_start = event_dt - pre_margin
                pause_end = event_dt + post_margin

                if pause_start <= now <= pause_end:
                    title = event.get("title", "High-Impact News")
                    mins_diff = int((event_dt - now).total_seconds() / 60.0)
                    timing_str = f"starts in {mins_diff} min" if mins_diff > 0 else f"passed {-mins_diff} min ago"
                    reason = f"🚫 HIGH-IMPACT NEWS PAUSE: {title} ({timing_str}) at {event_dt.strftime('%H:%M UTC')}"
                    logger.warning(reason)
                    return True, reason
            except Exception:
                continue

        return False, "No active news restrictions"

    async def is_trading_allowed(self) -> Tuple[bool, str]:
        """
        Master Trading Gatekeeper.
        Evaluates:
        1. System Master Switch (`trading_enabled` in DB)
        2. Daily Max Drawdown Circuit Breaker (5%)
        3. High-Impact News Filter Window (15 min pre/post)
        """
        # 1. Check Master Switch in Settings
        trading_enabled_setting = await db.get_setting("trading_enabled", "1")
        if trading_enabled_setting == "0":
            return False, "Trading paused: Master switch 'trading_enabled' is set to 0."

        # 2. Check Daily Max Drawdown Breaker
        if risk_manager.daily_trading_stopped:
            return False, "Trading halted: Daily maximum drawdown limit (5.0%) hit!"

        # 3. Check News Filter
        news_paused, news_reason = await self.is_news_pause_active()
        if news_paused:
            return False, news_reason

        return True, "Trading allowed: All risk and news gatekeepers clear."


news_filter = EconomicCalendarFilter()
