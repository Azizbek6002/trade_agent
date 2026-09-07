import asyncio
from telethon import TelegramClient
from config import config, BASE_DIR

async def main():
    print("==================================================")
    print("📱 TELEGRAM USERBOT AUTHENTICATION SETUP")
    print("==================================================")
    session_path = str(BASE_DIR / "data" / "userbot_session")
    client = TelegramClient(session_path, config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)
    
    await client.start()
    me = await client.get_me()
    print(f"\n✅ Userbot successfully authenticated as: {me.first_name} (@{me.username})")
    print("Session saved. Channel listener is ready to run 24/7 in main.py!")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
