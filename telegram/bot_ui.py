import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp
from config import config
from database import db
from ai_parser.groq_parser import groq_parser
from execution_engine.mt5_bridge import mt5_bridge
from decision_engine.execution_planner import execution_planner

logger = logging.getLogger(__name__)

dp = Dispatcher()


def get_app_keyboard() -> InlineKeyboardMarkup:
    """Returns clean single Web App button."""
    if config.WEBAPP_URL.startswith("https://"):
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 RTB MINI APP'NI OCHISH", web_app=WebAppInfo(url=config.WEBAPP_URL))]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 RTB MINI APP", callback_data="local_app_info")]
        ])


@dp.callback_query(F.data == "local_app_info")
async def local_app_info_handler(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        f"📱 **RTB Mini App havolasi:**\n`{config.WEBAPP_URL}`\n\n"
        f"Ushbu havolani brauzeringizda ochib to'liq boshqarishingiz mumkin.",
        parse_mode="Markdown"
    )


@dp.message(CommandStart())
@dp.message(Command("app", "webapp"))
async def start_cmd(message: types.Message):
    welcome_text = (
        "🤖 **ROBO TRADER BOY (RTB) v2.0 — XAUUSD AI SYSTEM**\n\n"
        "Assalomu alaykum ustoz! Barcha boshqaruv paneli, real vaqt bozori, "
        "statistika, risk sozlamalari va AI chat endi yagona **RTB Mini App** ichida mujassam.\n\n"
        "👇 Pastdagi tugmani bosing va tizimni to'liq boshqaring:"
    )
    await message.answer(welcome_text, reply_markup=get_app_keyboard(), parse_mode="Markdown")


@dp.message(F.text)
async def handle_direct_messages(message: types.Message):
    """
    Direct message processor:
    1. If message is a trade signal (BUY/SELL), parses with Gemini Pro and auto-executes on MT5.
    2. Otherwise, directs user to the Mini App or provides concise AI response.
    """
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
                f"✅ **SIGNAL MT5 GA YUBORILDI & OCHILDI!**\n\n"
                f"• Ticket: `#{ticket}`\n"
                f"• Aktiv: `XAUUSD` ({parsed['direction']})\n"
                f"• Lot: `{lot}` lot @ `{price}`\n"
                f"• Risk: `{risk}%` (${exec_res.get('risk_usd')})\n"
                f"• SL: `{parsed.get('sl')}` | TP: `{setup['tp']}`\n\n"
                f"📱 Holatni kuzatish uchun Mini App'ni oching:"
            )
        else:
            reply = (
                f"⚠️ **SIGNAL QABUL QILINDI (ID: {sig_id}), LEKIN ORDER OCHILMADI**\n\n"
                f"Sabab: {exec_res.get('reason')}\n\n"
                f"📱 Mini App orqali sozlamalarni tekshiring:"
            )
        await message.answer(reply, reply_markup=get_app_keyboard(), parse_mode="Markdown")
        return

    # General chat redirection to Mini App
    general_reply = (
        "💬 Ustoz, savdo signallari, AI maslahatchi bilan jonli muloqot va barcha tahlillar "
        "uchun **RTB Mini App**'ning **AI Chat** bo'limidan foydalanishingiz mumkin."
    )
    await message.answer(general_reply, reply_markup=get_app_keyboard(), parse_mode="Markdown")


async def start_bot():
    if config.TELEGRAM_BOT_TOKEN:
        logger.info("Starting Robo Trader Boy (RTB) v2.0 Telegram Bot (Pure Mini App Bridge)...")
        bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        await bot.delete_webhook(drop_pending_updates=True)

        # Configure Menu Button to point directly to Mini App
        if config.WEBAPP_URL.startswith("https://"):
            try:
                await bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(text="RTB App", web_app=WebAppInfo(url=config.WEBAPP_URL))
                )
                logger.info(f"Menu Button set to Mini App URL: {config.WEBAPP_URL}")
            except Exception as e:
                logger.warning(f"Could not set Telegram Menu Button: {e}")

        await dp.start_polling(bot)
