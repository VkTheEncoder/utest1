# main.py
import logging
from telethon import TelegramClient
from config import API_ID, API_HASH
from handlers import register_handlers

logging.basicConfig(level=logging.INFO)

def main():
    client = TelegramClient('hianime_user_session', API_ID, API_HASH)
    register_handlers(client)
    client.start()
    print("🚀 Userbot running…")
    client.run_until_disconnected()

if __name__ == "__main__":
    main()
