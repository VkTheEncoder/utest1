#!/usr/bin/env python3
import os
import logging
import subprocess
import tempfile
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
import requests

#─── Load config ────────────────────────────────────────────────────────────────
load_dotenv()
API_ID   = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
API_BASE = os.getenv("ANIWATCH_API_BASE", "http://localhost:4000/api/v2/hianime")

if not API_ID or not API_HASH:
    raise RuntimeError("API_ID/API_HASH must be set in .env")

#─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)

#─── Helpers ────────────────────────────────────────────────────────────────────
def fetch_json(path, **params):
    r = requests.get(f"{API_BASE}{path}", params=params)
    r.raise_for_status()
    return r.json().get("data", {})

def extract_slug_ep_from_query(url: str):
    p = urlparse(url)
    parts = p.path.strip("/").split("/")
    slug = parts[-1]
    qs = parse_qs(p.query)
    eps = qs.get("ep") or []
    if not eps:
        raise ValueError("No ?ep= in URL")
    return slug, eps[0]

def remux_hls(m3u8_url: str, referer: str|None, out_path: str):
    cmd = ["ffmpeg", "-y"]
    if referer:
        cmd += ["-headers", f"Referer: {referer}\r\n"]
    cmd += ["-i", m3u8_url, "-c", "copy", out_path]
    subprocess.run(cmd, check=True)

#─── Telethon client ────────────────────────────────────────────────────────────
client = TelegramClient('hianime_user_session', API_ID, API_HASH)

#─── 2) SHOW PAGE: list episodes ─────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r'https?://hianimez?\.to/watch/[^?]+\??$'))
async def show_handler(event):
    url = event.raw_text.strip()
    await event.reply("🔍 Fetching episode list…")
    try:
        slug = urlparse(url).path.strip("/").split("/")[-1]
        data = fetch_json(f"/anime/{slug}/episodes")
        eps = data.get("episodes", [])
        if not eps:
            return await event.reply("ℹ️ No episodes found.")
        buttons = [
            [Button.inline(f"Ep {e['number']}", f"EP|{slug}|{e['number']}")]
            for e in eps
        ]
        await event.reply("📺 Select an episode:", buttons=buttons)
    except Exception as e:
        logging.exception("Error in show_handler")
        await event.reply(f"❌ Failed to list episodes: {e}")

#─── 1) & 5) & 3) EPISODE CALLBACK: choose quality & download subtitles ──────────
@client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"EP|")))
async def episode_callback(event):
    data = event.data.decode().split("|", 2)
    _, slug, ep = data
    # Fetch sources now to build quality buttons:
    try:
        info = fetch_json("/episode/sources", animeEpisodeId=f"{slug}?ep={ep}",
                          server="hd-1", category="sub")
        sources = info.get("sources", [])
        referer = info.get("headers", {}).get("Referer")
        # filter HLS sources
        hls = [s for s in sources if s.get("type")=="hls" or s.get("url","").endswith(".m3u8")]
        if not hls:
            return await event.edit("⚠️ No HLS sources found.")
        buttons = [
            [Button.inline(s.get("quality","auto"), f"Q|{slug}|{ep}|{i}")]
            for i, s in enumerate(hls)
        ]
        await event.edit("🎚 Choose quality:", buttons=buttons)
        # Store in-session so the next handler can fetch
        event.client._hls_cache = (hls, referer)
    except Exception as e:
        logging.exception("Error fetching sources")
        await event.edit(f"❌ Failed to get sources: {e}")

#─── QUALITY CALLBACK: download/remux/upload + subtitles ─────────────────────────
@client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"Q|")))
async def quality_callback(event):
    data = event.data.decode().split("|", 4)
    _, slug, ep, idx = data
    idx = int(idx)
    hls, referer = event.client._hls_cache
    m3u8 = hls[idx]["url"]

    status = await event.edit("⏳ Downloading & remuxing…")
    try:
        os.makedirs("downloads", exist_ok=True)
        out_mp4 = f"downloads/{slug}_{ep}.mp4"
        remux_hls(m3u8, referer, out_mp4)
        await status.edit("💾 Downloaded! now uploading video…")
        await event.reply(file=out_mp4)

        # 3) subtitles
        tracks = info = fetch_json("/episode/sources", animeEpisodeId=f"{slug}?ep={ep}",
                                   server="hd-1", category="sub").get("tracks", [])
        for t in tracks:
            if t.get("kind")=="captions":
                url = t["file"]
                lang = t.get("label","sub").split()[0].lower()
                r = requests.get(url)
                r.raise_for_status()
                path = f"downloads/{slug}_{ep}_{lang}.vtt"
                with open(path, "wb") as f: f.write(r.content)
                await event.reply(file=path)

        await status.edit("✅ All done!")
    except subprocess.CalledProcessError as e:
        logging.exception("ffmpeg error")
        await status.edit(f"❌ ffmpeg failed: {e}")
    except requests.HTTPError as e:
        logging.exception("download error")
        await status.edit(f"❌ Download error: {e}")
    except Exception as e:
        logging.exception("unexpected error")
        await status.edit(f"❌ Error: {e}")

#─── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    client.start()
    print("🚀 Hianime userbot running…")
    client.run_until_disconnected()

if __name__ == "__main__":
    main()
