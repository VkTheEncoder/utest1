# hianime-api/config.py
import os
from dotenv import load_dotenv
load_dotenv()

ANIWATCH_API_BASE = os.getenv("ANIWATCH_API_BASE")
if not ANIWATCH_API_BASE:
    raise RuntimeError("ANIWATCH_API_BASE must be set in .env")
