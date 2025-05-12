# main.py
import logging
from telethon import TelegramClient
from config import API_ID, API_HASH
from handlers import register_handlers

logging.basicConfig(level=logging.INFO)

client = TelegramClient('hianime_session', API_ID, API_HASH)

async def main():
    # Start the session (user or bot)
    await client.start()
    # Wire up all your handlers
    await register_handlers(client)
    print("🚀 Bot is up!")
    # Keep running until interrupted
    await client.run_until_disconnected()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
