#!/usr/bin/env python3
import os, logging, subprocess
from urllib.parse import urlparse
from dotenv import load_dotenv
from telethon import TelegramClient, events
import requests

#─── Load config ────────────────────────────────────────────────────────────────
load_dotenv()
API_ID   = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
API_BASE = os.getenv("ANIWATCH_API_BASE",
                     "http://localhost:4000/api/v2/hianime")

if not API_ID or not API_HASH:
    raise RuntimeError("API_ID/API_HASH not set in .env")

#─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)

#─── HLS helpers ────────────────────────────────────────────────────────────────
def extract_slug_ep(url: str):
    parts = urlparse(url).path.strip("/").split("/")
    return parts[-2], parts[-1].split("-")[-1]

def get_m3u8_and_referer(slug, ep):
    resp = requests.get(
        f"{API_BASE}/episode/sources",
        params={
            "animeEpisodeId": f"{slug}?ep={ep}",
            "server": "hd-1",
            "category": "sub"
        }
    )
    resp.raise_for_status()
    data    = resp.json().get("data", {})
    for s in data.get("sources", []):
        if s.get("type")=="hls" or s.get("url","").endswith(".m3u8"):
            return s["url"], data.get("headers",{}).get("Referer")
    raise RuntimeError("No HLS source found")

def remux_hls(m3u8, referer, out_path):
    cmd = ["ffmpeg", "-y"]
    if referer:
        cmd += ["-headers", f"Referer: {referer}\r\n"]
    cmd += ["-i", m3u8, "-c", "copy", out_path]
    subprocess.run(cmd, check=True)

#─── Telethon client & handler ─────────────────────────────────────────────────
client = TelegramClient('hianime_user_session', API_ID, API_HASH)

@client.on(events.NewMessage(pattern=r'https?://hianime\.to/watch/.*'))
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
    client.start()  # first run: enter phone & code
    print("✅ Userbot running—send any hianime.to/watch/... link in your chats.")
    client.run_until_disconnected()

if __name__ == "__main__":
    main()
