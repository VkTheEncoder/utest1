from telethon import TelegramClient
from config import API_ID, API_HASH
from handlers import register_handlers
import asyncio

client = TelegramClient('hianime_session', API_ID, API_HASH)

async def main():
    await client.start()
    await register_handlers(client)
    print("🚀 Bot is up")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
