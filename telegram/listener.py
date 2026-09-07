import asyncio
import logging
import time
from typing import Optional, Dict, Any, List
from telethon import TelegramClient, events
from config import config, BASE_DIR
from database import db
from ai_parser.groq_parser import groq_parser

logger = logging.getLogger(__name__)


class TelegramChannelListener:
    """
    RTB v2.0 Telethon Userbot Channel Listener.
    Monitors Telegram channels (TraderStage, Gold VIPs, and custom channels) 24/7.
    Features:
    - Robust channel matching by ID (-100 prefix or raw), username (@TraderStage), or title.
    - Captures both text messages and photo captions.
    - Listens to new messages and edited messages.
    - Anti-spam / duplicate message cache.
    - Uses Gemini Pro Smart Parser to extract clean signals.
    """

    def __init__(self):
        self.api_id = config.TELEGRAM_API_ID
        self.api_hash = config.TELEGRAM_API_HASH
        self.client = None
        self._recent_hashes: Dict[int, float] = {}  # {hash: timestamp}
        self._cache_ttl_sec = 600.0  # 10 minutes duplicate protection

    def _is_duplicate_message(self, chat_id: Any, text: str) -> bool:
        """Returns True if the exact same message was processed recently."""
        now = time.time()
        # Clean expired hashes
        self._recent_hashes = {h: t for h, t in self._recent_hashes.items() if (now - t) < self._cache_ttl_sec}

        msg_hash = hash(f"{chat_id}:{text.strip()}")
        if msg_hash in self._recent_hashes:
            return True

        self._recent_hashes[msg_hash] = now
        return False

    @staticmethod
    def _normalize_id(raw_id: Any) -> str:
        """Strips negative prefixes and -100 for reliable ID comparison."""
        s = str(raw_id).strip()
        if s.startswith("-100"):
            return s[4:]
        elif s.startswith("-"):
            return s[1:]
        return s

    async def _match_channel(self, event, active_channels: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Matches incoming event to an active channel in the database by:
        1. Numerical Chat ID (normalized)
        2. Username (@TraderStage)
        3. Channel Title
        """
        raw_chat_id = str(event.chat_id)
        norm_chat_id = self._normalize_id(raw_chat_id)

        chat_obj = None
        chat_username = ""
        chat_title = ""

        try:
            chat_obj = await event.get_chat()
            chat_username = (getattr(chat_obj, "username", "") or "").lstrip("@").lower()
            chat_title = (getattr(chat_obj, "title", "") or "").lower()
        except Exception:
            pass

        for ch in active_channels:
            ch_raw_id = str(ch.get("channel_id", "")).strip()
            ch_norm_id = self._normalize_id(ch_raw_id)
            ch_title = (ch.get("title", "") or "").lower()

            # 1. Match by normalized numerical ID
            if ch_norm_id and ch_norm_id == norm_chat_id:
                return ch

            # 2. Match by username (e.g., TraderStage or @TraderStage)
            ch_user_clean = ch_raw_id.lstrip("@").lower()
            if chat_username and ch_user_clean == chat_username:
                return ch

            # 3. Match by exact title
            if chat_title and ch_title and chat_title == ch_title:
                return ch

        return None

    async def start(self):
        if not self.api_id or not self.api_hash:
            logger.warning("Telegram API_ID / API_HASH missing. Userbot listener paused.")
            return

        session_path = str(BASE_DIR / "data" / "userbot_session")

        try:
            self.client = TelegramClient(session_path, self.api_id, self.api_hash)
            await self.client.connect()

            # Check if authorized without blocking stdin
            if not await self.client.is_user_authorized():
                logger.warning(
                    "⚠️ Telegram Userbot session is not authorized yet! "
                    "To enable 24/7 channel monitoring, run 'python3 setup_userbot.py' in terminal once."
                )
                return

            # Handler for both NewMessage and MessageEdited
            async def handle_incoming_channel_event(event, event_type: str = "NEW"):
                try:
                    active_channels = await db.get_active_channels()
                    matched_channel = await self._match_channel(event, active_channels)

                    if not matched_channel:
                        return

                    # Extract text from message body or photo caption
                    text = event.text or getattr(event.message, "message", "") or getattr(event.message, "caption", "") or ""
                    text = text.strip()

                    if not text:
                        return

                    # Duplicate check
                    if self._is_duplicate_message(event.chat_id, text):
                        logger.debug(f"Skipping duplicate message from {matched_channel.get('title')}")
                        return

                    channel_title = matched_channel.get("title", f"Channel_{event.chat_id}")
                    logger.info(f"📨 [{event_type}] Message from monitored channel '{channel_title}': {text[:70]}...")

                    # Parse signal using Gemini Pro Smart Parser
                    parsed = groq_parser.parse_message(text)

                    if parsed.get("is_signal") and parsed.get("direction") != "NEUTRAL":
                        sig_id = await db.save_signal(
                            source_type="CHANNEL",
                            source_name=channel_title,
                            direction=parsed["direction"],
                            zone_min=parsed["zone_min"],
                            zone_max=parsed["zone_max"],
                            sl=parsed.get("sl") or 0.0,
                            tp_targets=parsed.get("tp_targets", []),
                            raw_text=text
                        )
                        logger.info(
                            f"🎯 SIGNAL DETECTED & SAVED (ID: {sig_id}) from '{channel_title}'!\n"
                            f"   • Asset: XAUUSD\n"
                            f"   • Action: {parsed['direction']} ({parsed.get('order_type', 'MARKET')})\n"
                            f"   • Entry Zone: {parsed['zone_min']} - {parsed['zone_max']}\n"
                            f"   • SL: {parsed.get('sl')} | TP: {parsed.get('tp_targets')}"
                        )

                        # Auto-execute trade on Exness MT5 via Execution Planner
                        setup = {
                            "direction": parsed["direction"],
                            "zone_min": parsed["zone_min"],
                            "zone_max": parsed["zone_max"],
                            "sl": parsed.get("sl"),
                            "tp": (parsed.get("tp_targets", [None]) or [None])[0],
                            "tp_targets": parsed.get("tp_targets", []),
                            "confidence_score": 85.0,
                            "sources": [{"source": channel_title, "weight": 1.5}]
                        }
                        from decision_engine.execution_planner import execution_planner
                        exec_res = await execution_planner.evaluate_and_execute_setup(setup)
                        if exec_res.get("success"):
                            logger.info(f"🚀 Real Signal executed on Exness MT5: Ticket #{exec_res.get('ticket')} ({exec_res.get('lot')} lot @ {exec_res.get('price')})")
                        else:
                            logger.info(f"⏸ Signal execution evaluated: {exec_res.get('reason')}")
                except Exception as e:
                    logger.error(f"Error handling channel event ({event_type}): {e}")

            # Register event handlers
            @self.client.on(events.NewMessage)
            async def new_message_handler(event):
                await handle_incoming_channel_event(event, event_type="NEW")

            @self.client.on(events.MessageEdited)
            async def edited_message_handler(event):
                await handle_incoming_channel_event(event, event_type="EDITED")

            logger.info("🟢 RTB v2.0 Telethon Channel Listener active and monitoring channels.")
            await self.client.run_until_disconnected()

        except Exception as e:
            logger.warning(f"Telegram Userbot Listener paused (Session error: {e}). Bot UI and RTB Engine continue running normally.")


telegram_listener = TelegramChannelListener()
