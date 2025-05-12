# config.py
import os
from dotenv import load_dotenv

load_dotenv()

API_ID   = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
API_BASE = os.getenv("ANIWATCH_API_BASE", "http://localhost:4000/api/v2/hianime")

if not API_ID or not API_HASH:
    raise RuntimeError("API_ID and API_HASH must be set in .env")
