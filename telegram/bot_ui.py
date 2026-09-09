import aiosqlite
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp, MenuButtonDefault
from config import config
from database import db
from ai_parser.groq_parser import groq_parser
from execution_engine.mt5_bridge import mt5_bridge
from decision_engine.execution_planner import execution_planner

logger = logging.getLogger(__name__)

dp = Dispatcher()


def get_app_keyboard() -> InlineKeyboardMarkup:
    """Returns pure Telegram Mini App button for authorized admin."""
    if config.WEBAPP_URL.startswith("https://"):
        admin_url = f"{config.WEBAPP_URL}?admin_key={config.ADMIN_TELEGRAM_ID}"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 RTB MINI APP", web_app=WebAppInfo(url=admin_url))]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 RTB MINI APP", callback_data="local_app_info")]
        ])


def is_admin(user_id: int | None) -> bool:
    """Check if the user is authorized administrator."""
    if not user_id:
        return False
    return bool(config.ADMIN_TELEGRAM_ID and user_id == config.ADMIN_TELEGRAM_ID)


DENIED_MESSAGE = (
    "❌ <b>Kechirasiz, siz bundan foydalana olmaysiz!</b>\n\n"
    "O'zingizga boshqa agent qilish uchun @coder_zik ga murojaat qiling."
)


@dp.callback_query(F.data == "local_app_info")
async def local_app_info_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Siz bundan foydalana olmaysiz! @coder_zik ga murojaat qiling.", show_alert=True)
        return

    await callback.answer()
    admin_url = f"{config.WEBAPP_URL}?admin_key={config.ADMIN_TELEGRAM_ID}"
    await callback.message.answer(
        f"📱 <b>RTB Mini App havolasi:</b>\n<code>{admin_url}</code>\n\n"
        f"Ushbu havolani brauzeringizda ochib to'liq boshqarishingiz mumkin.",
        parse_mode="HTML"
    )


@dp.message(Command(commands=["start", "app", "webapp"]))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id if message.from_user else None
    user_name = message.from_user.full_name if message.from_user else "Unknown"
    logger.info(f"📨 [START] Received command from user_id: {user_id} ({user_name})")

    if not is_admin(user_id):
        logger.warning(f"⛔ Unauthorized /start attempt from user_id: {user_id} (Admin ID: {config.ADMIN_TELEGRAM_ID})")
        try:
            await message.bot.set_chat_menu_button(
                chat_id=message.chat.id,
                menu_button=MenuButtonDefault()
            )
        except Exception:
            pass
        denied_msg = (
            f"❌ <b>Kechirasiz, siz bundan foydalana olmaysiz!</b>\n"
            f"Sizning Telegram ID: <code>{user_id}</code>\n\n"
            f"Ruxsat olish uchun @coder_zik ga murojaat qiling."
        )
        await message.answer(denied_msg, parse_mode="HTML")
        return

    logger.info(f"✅ Admin {user_id} authorized! Configuring Menu Button & sending Mini App link...")
    # For admin: ensure web app menu button is set for admin's chat
    if config.WEBAPP_URL.startswith("https://"):
        try:
            admin_url = f"{config.WEBAPP_URL}?admin_key={config.ADMIN_TELEGRAM_ID}"
            await message.bot.set_chat_menu_button(
                chat_id=message.chat.id,
                menu_button=MenuButtonWebApp(text="RTB App", web_app=WebAppInfo(url=admin_url))
            )
        except Exception as e:
            logger.warning(f"Could not set chat menu button: {e}")

    admin_url = f"{config.WEBAPP_URL}?admin_key={config.ADMIN_TELEGRAM_ID}"
    welcome_text = (
        "🤖 <b>ROBO TRADER BOY (RTB) v2.0 — XAUUSD AI SYSTEM</b>\n\n"
        "Assalomu alaykum ustoz! Barcha boshqaruv paneli, real vaqt bozori, "
        "statistika, risk sozlamalari va AI yordamchi endi to'g'ridan-to'g'ri <b>RTB Mini App</b> ichida ishlaydi.\n\n"
        "👇 Pastdagi <b>'🚀 RTB MINI APP'</b> tugmasini bosing:"
    )
    await message.answer(welcome_text, reply_markup=get_app_keyboard(), parse_mode="HTML")
    logger.info(f"🚀 Sent welcome message with Mini App keyboard to Admin {user_id}")


@dp.message(Command("channels"))
async def channels_cmd(message: types.Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    channels = await db.get_active_channels()
    if not channels:
        await message.answer(
            "📡 <b>Hozircha kuzatilayotgan kanallar yo'q.</b>\n\n"
            "Yangi kanal qo'shish uchun:\n"
            "1. Mini App ichidagi <b>'Kanal qo'shish'</b> tugmasidan foydalaning, yoki\n"
            "2. Botga quyidagi buyruqni yuboring:\n"
            "<code>/addchannel @kanal_nomi Kanal Sarlavhasi</code>",
            parse_mode="HTML"
        )
        return
    text = f"📡 <b>Kuzatilayotgan kanallar ({len(channels)} ta):</b>\n\n"
    for ch in channels:
        text += f"• <b>{ch.get('title') or ch.get('channel_id')}</b> (<code>{ch.get('channel_id')}</code>) | Og'irlik: {ch.get('weight', 1.5)}\n"
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("addchannel"))
async def addchannel_cmd(message: types.Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(
            "⚠️ <b>Format:</b> <code>/addchannel &lt;id_yoki_username&gt; &lt;nomi&gt;</code>\n"
            "Misol: <code>/addchannel @trader_stage TraderStage VIP</code>",
            parse_mode="HTML"
        )
        return
    ch_id = parts[1].strip()
    title = parts[2].strip() if len(parts) > 2 else ch_id
    await db.add_channel(channel_id=ch_id, title=title)
    await message.answer(
        f"✅ <b>Kanal muvaffaqiyatli qo'shildi!</b>\n\n"
        f"• Kanal: <code>{ch_id}</code>\n"
        f"• Nomi: <b>{title}</b>\n\n"
        f"Userbot ushbu kanaldan kelgan XAUUSD signallarini avtomatik tahlil qiladi.",
        parse_mode="HTML"
    )


@dp.message(Command("cleardata"))
async def cleardata_cmd(message: types.Message):
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("DELETE FROM trades")
        await conn.execute("DELETE FROM signals")
        await conn.execute("DELETE FROM channels")
        try:
            await conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('trades', 'signals', 'channels')")
        except Exception:
            pass
        await conn.commit()
    mt5_bridge._simulated_positions.clear()
    await message.answer(
        "🧹 <b>Barcha tarix, signallar, demo savdolar va kanallar to'liq tozalandi!</b>\n\n"
        "Tizim 100% toza holatda ishga tushirildi. Yangi kanallarni Mini App yoki <code>/addchannel</code> orqali qo'shishingiz mumkin.",
        reply_markup=get_app_keyboard(),
        parse_mode="HTML"
    )



@dp.message(F.text)
async def handle_direct_messages(message: types.Message):
    """
    Direct message processor:
    1. If message is a trade signal (BUY/SELL), parses with Gemini Pro and auto-executes on MT5.
    2. Otherwise, directs user to the Mini App or provides concise AI response.
    """
    user_id = message.from_user.id if message.from_user else None
    if not is_admin(user_id):
        try:
            await message.bot.set_chat_menu_button(
                chat_id=message.chat.id,
                menu_button=MenuButtonDefault()
            )
        except Exception:
            pass
        await message.answer(DENIED_MESSAGE, parse_mode="HTML")
        return

    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return

    # Check if text is a trade signal
    parsed = groq_parser.parse_message(text)
    if parsed.get("is_signal") and parsed.get("direction") != "NEUTRAL":
        # Save signal
        sig_id = await db.save_signal(
            source_type="USER",
            source_name="USER (Telegram)",
            direction=parsed["direction"],
            zone_min=parsed["zone_min"],
            zone_max=parsed["zone_max"],
            sl=parsed.get("sl") or 0.0,
            tp_targets=parsed.get("tp_targets", []),
            raw_text=text
        )

        setup = {
            "direction": parsed["direction"],
            "zone_min": parsed["zone_min"],
            "zone_max": parsed["zone_max"],
            "sl": parsed.get("sl"),
            "tp": (parsed.get("tp_targets", [None]) or [None])[0],
            "tp_targets": parsed.get("tp_targets", []),
            "confidence_score": 90.0,
            "sources": [{"source": "USER", "weight": 3.0}]
        }

        exec_res = await execution_planner.evaluate_and_execute_setup(setup)
        if exec_res.get("success"):
            ticket = exec_res.get("ticket")
            lot = exec_res.get("lot")
            price = exec_res.get("price")
            risk = exec_res.get("risk_percent")
            reply = (
                f"✅ <b>SIGNAL MT5 GA YUBORILDI & OCHILDI!</b>\n\n"
                f"• Ticket: <code>#{ticket}</code>\n"
                f"• Aktiv: <code>XAUUSD</code> ({parsed['direction']})\n"
                f"• Lot: <code>{lot}</code> lot @ <code>{price}</code>\n"
                f"• Risk: <code>{risk}%</code> (${exec_res.get('risk_usd')})\n"
                f"• SL: <code>{parsed.get('sl')}</code> | TP: <code>{setup['tp']}</code>\n\n"
                f"📱 Holatni kuzatish uchun Mini App'ni oching:"
            )
        else:
            reply = (
                f"⚠️ <b>SIGNAL QABUL QILINDI (ID: {sig_id}), LEKIN ORDER OCHILMADI</b>\n\n"
                f"Sabab: {exec_res.get('reason')}\n\n"
                f"📱 Mini App orqali sozlamalarni tekshiring:"
            )
        await message.answer(reply, reply_markup=get_app_keyboard(), parse_mode="HTML")
        return

    # General chat redirection to Mini App
    general_reply = (
        "💬 Ustoz, savdo signallari, AI maslahatchi bilan jonli muloqot va barcha tahlillar "
        "uchun <b>RTB Mini App</b>'ning <b>AI Chat</b> bo'limidan foydalanishingiz mumkin."
    )
    await message.answer(general_reply, reply_markup=get_app_keyboard(), parse_mode="HTML")


async def start_bot():
    if not config.TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not provided. Bot UI paused.")
        return

    logger.info("Starting Robo Trader Boy (RTB) v2.0 Telegram Bot (Pure Mini App Bridge)...")
    from aiogram.client.session.aiohttp import AiohttpSession
    session = AiohttpSession(timeout=45.0)
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN, session=session)

    # 1. Start Tunnel Watchdog in background
    from tunnel_manager import tunnel_manager
    asyncio.create_task(tunnel_manager.start_watchdog(bot))

    # 2. Resilient Polling Loop (auto-reconnects on network loss / wake from sleep)
    while True:
        try:
            # Clean old webhooks
            try:
                await bot.delete_webhook(drop_pending_updates=True)
            except Exception as e:
                logger.warning(f"Webhook cleanup notice (will retry): {e}")

            # Reset GLOBAL default menu button so strangers cannot access Mini App
            try:
                await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
            except Exception as e:
                logger.warning(f"Could not reset global menu button: {e}")

            # Configure Menu Button EXCLUSIVELY for the admin
            if config.ADMIN_TELEGRAM_ID and config.WEBAPP_URL.startswith("https://"):
                try:
                    admin_url = f"{config.WEBAPP_URL}?admin_key={config.ADMIN_TELEGRAM_ID}"
                    await bot.set_chat_menu_button(
                        chat_id=config.ADMIN_TELEGRAM_ID,
                        menu_button=MenuButtonWebApp(text="RTB App", web_app=WebAppInfo(url=admin_url))
                    )
                    logger.info(f"Menu Button set exclusively for Admin {config.ADMIN_TELEGRAM_ID}")
                except Exception as e:
                    logger.warning(f"Could not set Telegram Menu Button for admin: {e}")

            logger.info("Starting dp.start_polling(bot, polling_timeout=15)...")
            await dp.start_polling(bot, polling_timeout=15, handle_signals=False)
            logger.info("dp.start_polling finished cleanly.")
            break
        except Exception as e:
            logger.error(f"Telegram Bot network/polling error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)


