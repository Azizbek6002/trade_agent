import asyncio
from telethon import TelegramClient
from config import config, BASE_DIR

async def check():
    session_path = str(BASE_DIR / "data" / "userbot_session")
    client = TelegramClient(session_path, config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)
    await client.connect()
    
    is_auth = await client.is_user_authorized()
    print(f"Is Userbot Authorized: {is_auth}")
    
    if is_auth:
        me = await client.get_me()
        print(f"Authenticated Account: {me.first_name} {me.last_name or ''} (@{me.username or 'No Username'}) - ID: {me.id}")
    else:
        print("Userbot session is NOT authorized yet.")
        
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(check())
