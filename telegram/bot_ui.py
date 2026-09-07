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
    """Returns clean single Web App button for authorized admin."""
    if config.WEBAPP_URL.startswith("https://"):
        admin_url = f"{config.WEBAPP_URL}?admin_key={config.ADMIN_TELEGRAM_ID}"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 RTB MINI APP'NI OCHISH", web_app=WebAppInfo(url=admin_url))]
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


@dp.message(CommandStart())
@dp.message(Command("app", "webapp"))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id if message.from_user else None
    if not is_admin(user_id):
        try:
            # Explicitly clear Menu Button for strangers so they cannot access Mini App
            await message.bot.set_chat_menu_button(
                chat_id=message.chat.id,
                menu_button=MenuButtonDefault()
            )
        except Exception:
            pass
        await message.answer(DENIED_MESSAGE, parse_mode="HTML")
        return

    # For admin: ensure web app menu button is set for admin's chat
    if config.WEBAPP_URL.startswith("https://"):
        try:
            admin_url = f"{config.WEBAPP_URL}?admin_key={config.ADMIN_TELEGRAM_ID}"
            await message.bot.set_chat_menu_button(
                chat_id=message.chat.id,
                menu_button=MenuButtonWebApp(text="RTB App", web_app=WebAppInfo(url=admin_url))
            )
        except Exception:
            pass

    welcome_text = (
        "🤖 <b>ROBO TRADER BOY (RTB) v2.0 — XAUUSD AI SYSTEM</b>\n\n"
        "Assalomu alaykum ustoz! Barcha boshqaruv paneli, real vaqt bozori, "
        "statistika, risk sozlamalari va AI chat endi yagona <b>RTB Mini App</b> ichida mujassam.\n\n"
        "👇 Pastdagi tugmani bosing va tizimni to'liq boshqaring:"
    )
    await message.answer(welcome_text, reply_markup=get_app_keyboard(), parse_mode="HTML")


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
    if config.TELEGRAM_BOT_TOKEN:
        logger.info("Starting Robo Trader Boy (RTB) v2.0 Telegram Bot (Pure Mini App Bridge)...")
        bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        await bot.delete_webhook(drop_pending_updates=True)

        # 1. Reset GLOBAL default menu button so strangers/non-admins never see the Web App button
        try:
            await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
            logger.info("Global Telegram Menu Button reset to Default (restricted for strangers).")
        except Exception as e:
            logger.warning(f"Could not reset global menu button: {e}")

        # 2. Configure Menu Button EXCLUSIVELY for the admin
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

        await dp.start_polling(bot)
