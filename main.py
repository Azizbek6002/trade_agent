import asyncio
import logging
import sys
from config import config, BASE_DIR
from database import init_db_sync, db
from execution_engine.mt5_bridge import mt5_bridge
from execution_engine.position_monitor import position_monitor
from telegram.bot_ui import start_bot
from telegram.listener import telegram_listener

# Ensure data directory exists
(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "data" / "system.log")
    ]
)

logger = logging.getLogger(__name__)

async def main():
    logger.info("==================================================")
    logger.info("🤖 Starting RTB v2.0 (Robo Trader Boy) XAUUSD AI System...")
    logger.info("==================================================")

    # 1. Initialize Database
    init_db_sync()
    logger.info("Database initialized successfully.")

    # 2. Connect MetaTrader 5
    mt5_success = mt5_bridge.initialize()
    if mt5_success:
        logger.info(f"MetaTrader 5 Connected to Symbol: {config.MT5_SYMBOL}")
    else:
        logger.warning("MetaTrader 5 initialization pending. Simulated execution active.")

    # 3. Create Async Tasks
    tasks = [
        asyncio.create_task(position_monitor.start_monitoring()),
    ]

    # 4. Start Telegram Userbot Listener if credentials present
    if config.TELEGRAM_API_ID and config.TELEGRAM_API_HASH:
        tasks.append(asyncio.create_task(telegram_listener.start()))

    # 5. Start Telegram Bot UI if token present
    if config.TELEGRAM_BOT_TOKEN:
        tasks.append(asyncio.create_task(start_bot()))
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not provided in .env file. Bot UI standing by.")

    # 6. Start Telegram Mini App / FastAPI Web Server
    import uvicorn
    from webapp.server import app as webapp_app

    uvicorn_config = uvicorn.Config(
        webapp_app,
        host=config.WEBAPP_HOST,
        port=config.WEBAPP_PORT,
        log_level="warning"
    )
    server = uvicorn.Server(uvicorn_config)
    tasks.append(asyncio.create_task(server.serve()))
    logger.info(f"📱 Telegram Mini App Server running at http://{config.WEBAPP_HOST}:{config.WEBAPP_PORT}")

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("XAUUSD AI Trading System stopped.")
    except Exception as e:
        logger.critical(f"FATAL: Main system encountered unhandled error: {e}", exc_info=True)

