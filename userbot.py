#!/usr/bin/env python3
import os
import logging
import subprocess
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from telethon import TelegramClient, events
import requests

#─── Load config ────────────────────────────────────────────────────────────────
load_dotenv()
API_ID   = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
API_BASE = os.getenv(
    "ANIWATCH_API_BASE",
    "http://localhost:4000/api/v2/hianime"
)

if not API_ID or not API_HASH:
    raise RuntimeError("API_ID/API_HASH not set in .env")

#─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)

#─── Helpers ────────────────────────────────────────────────────────────────────
def extract_slug_ep(url: str) -> tuple[str, str]:
    """
    - slug comes from the path segment after /watch/
    - ep comes from the first 'ep' query parameter
    """
    p = urlparse(url)
    parts = p.path.strip("/").split("/")
    # parts == ["watch", "<slug>"]
    slug = parts[1] if len(parts) >= 2 else parts[-1]

    qs = parse_qs(p.query)
    eps = qs.get("ep") or qs.get("EP") or []
    if not eps:
        raise RuntimeError("No 'ep' query parameter found")
    ep = eps[0]
    return slug, ep

def get_m3u8_and_referer(slug: str, ep: str) -> tuple[str, str|None]:
    resp = requests.get(
        f"{API_BASE}/episode/sources",
        params={
            "animeEpisodeId": f"{slug}?ep={ep}",
            "server": "hd-1",
            "category": "sub",
        }
    )
    resp.raise_for_status()
    data    = resp.json().get("data", {})
    for s in data.get("sources", []):
        if s.get("type") == "hls" or s.get("url", "").endswith(".m3u8"):
            referer = data.get("headers", {}).get("Referer")
            return s["url"], referer
    raise RuntimeError("No HLS source found")

def remux_hls(m3u8_url: str, referer: str|None, out_path: str) -> None:
    cmd = ["ffmpeg", "-y"]
    if referer:
        cmd += ["-headers", f"Referer: {referer}\r\n"]
    cmd += ["-i", m3u8_url, "-c", "copy", out_path]
    subprocess.run(cmd, check=True)

#─── Telethon client & handler ─────────────────────────────────────────────────
client = TelegramClient('hianime_user_session', API_ID, API_HASH)

# Only catch URLs on hianime.to or hianimez.to that include ?ep=<digits>
EP_REGEX = r'https?://hianimez?\.to/watch/[^?\s]+[?&]ep=\d+'

@client.on(events.NewMessage(pattern=EP_REGEX))
async def handler(event):
    url = event.raw_text.strip()
    await event.reply("⏳ Fetching & remuxing…")
    try:
        slug, ep = extract_slug_ep(url)
        m3u8, ref = get_m3u8_and_referer(slug, ep)

        os.makedirs("downloads", exist_ok=True)
        out = f"downloads/{slug}_{ep}.mp4"
        remux_hls(m3u8, ref, out)

        await event.reply(file=out)
    except Exception as e:
        logging.exception("Error processing URL")
        await event.reply(f"❌ Failed: {e}")

def main():
    client.start()  # first run prompts for phone & code (unless you’re using StringSession)
    print("✅ Userbot running—send any hianime.to/watch/... link with ?ep= to your chats.")
    client.run_until_disconnected()

if __name__ == "__main__":
    main()
