#!/usr/bin/env python3
import os
import logging
import subprocess
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
import requests

#─── CONFIG ─────────────────────────────────────────────────────────────────────
load_dotenv()
API_ID   = int(os.getenv("API_ID",   0))
API_HASH = os.getenv("API_HASH", "")
API_BASE = os.getenv("ANIWATCH_API_BASE", "http://localhost:4000/api/v2/hianime")

if not API_ID or not API_HASH:
    raise RuntimeError("API_ID/API_HASH not set in .env")

logging.basicConfig(level=logging.INFO)
client = TelegramClient('hianime_user_session', API_ID, API_HASH)

#─── GLOBAL STATE CACHE ─────────────────────────────────────────────────────────
# key = f"{chat_id}:{slug}:{ep}"
STATE = {}

#─── HELPERS ────────────────────────────────────────────────────────────────────
def fetch_sources(slug, ep):
    resp = requests.get(
        f"{API_BASE}/episode/sources",
        params={"animeEpisodeId": f"{slug}?ep={ep}", "server": "hd-1", "category": "sub"}
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    return data.get("sources", []), data.get("headers", {}).get("Referer")

def ascii_progress(current, total, length=20):
    pct = int(current / total * 100) if total else 0
    filled = int(pct / 100 * length)
    bar = "█" * filled + "─" * (length - filled)
    return f"[{bar}] {pct:3d}%"

def remux_hls(m3u8_url: str, referer: str|None, out_path: str):
    cmd = ["ffmpeg", "-y"]
    if referer:
        cmd += ["-headers", f"Referer: {referer}\r\n"]
    cmd += ["-i", m3u8_url, "-c", "copy", out_path]
    subprocess.run(cmd, check=True)

#─── TELETHON CLIENT SETUP ───────────────────────────────────────────────────────
# Matches episode URLs
URL_REGEX = r'https?://hianimez?\.to/watch/[^?\s]+[?&]ep=\d+'

@client.on(events.NewMessage(pattern=URL_REGEX))
async def on_episode_link(event):
    url = event.raw_text.strip()
    p = urlparse(url)
    slug = p.path.strip("/").split("/")[-1]
    ep = parse_qs(p.query).get("ep", [None])[0]
    if not ep:
        return await event.reply("❌ No episode number found in URL.")
    sources, referer = fetch_sources(slug, ep)
    hls = [s for s in sources if s.get("type") == "hls" or s.get("url", "").endswith(".m3u8")]
    if not hls:
        return await event.reply("⚠️ No HLS sources available.")
    # Cache for callbacks
    STATE[f"{event.chat_id}:{slug}:{ep}"] = {"hls": hls, "referer": referer}
    buttons = [
        Button.inline(s.get("quality", "auto"), f"Q|{slug}|{ep}|{i}")
        for i, s in enumerate(hls)
    ]
    keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    await event.reply("<b>Select quality:</b>", buttons=keyboard, parse_mode="html")

@client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"Q|")))
async def on_quality(event):
    _, slug, ep, idx = event.data.decode().split("|")
    idx = int(idx)
    key = f"{event.chat_id}:{slug}:{ep}"
    info = STATE.get(key)
    if not info:
        return await event.answer("Session expired; please resend the link.", alert=True)
    hls_list = info["hls"]
    referer = info["referer"]
    stream = hls_list[idx]["url"]
    info["choice_idx"] = idx

    # Fetch full API data to get tracks
    resp = requests.get(
        f"{API_BASE}/episode/sources",
        params={"animeEpisodeId": f"{slug}?ep={ep}", "server": "hd-1", "category": "sub"}
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})

    tracks = data.get("tracks", [])
    if not tracks:
        return await event.edit("⚠️ No subtitles available.")

    info["tracks"] = tracks
    buttons = [
        Button.inline(t["label"], f"S|{slug}|{ep}|{i}")
        for i, t in enumerate(tracks)
    ]
    keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    await event.edit("<b>Select subtitle:</b>", buttons=keyboard, parse_mode="html")

@client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"S|")))
async def on_subtitle(event):
    # ensure downloads folder exists
    os.makedirs("downloads", exist_ok=True)

    _, slug, ep, tidx = event.data.decode().split("|")
    tidx = int(tidx)
    key = f"{event.chat_id}:{slug}:{ep}"
    info = STATE.get(key)
    if not info:
        return await event.answer("Session expired; restart by resending link.", alert=True)

    hls_url = info["hls"][info["choice_idx"]]["url"]
    referer = info["referer"]
    subtitle = info["tracks"][tidx]

    status = await event.edit("⏳ Starting download…", parse_mode="html")

    # Remux with progress
    out_mp4 = f"downloads/{slug}_{ep}.mp4"
    cmd = ["ffmpeg", "-y", "-headers", f"Referer: {referer}\r\n", "-i", hls_url,
           "-c", "copy", out_mp4]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
    total = None
    while True:
        line = proc.stderr.readline()
        if not line and proc.poll() is not None:
            break
        if "Duration:" in line:
            dur = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = dur.split(":")
            total = int(h) * 3600 + int(m) * 60 + float(s)
        if "time=" in line and total:
            ts = line.split("time=")[1].split(" ")[0]
            h, m, s = ts.split(":")
            cur = int(h) * 3600 + int(m) * 60 + float(s)
            bar = ascii_progress(cur, total)
            await status.edit(f"⏳ Remuxing… {bar}", parse_mode="html")

    # Download subtitle VTT
    vtt_resp = requests.get(subtitle["file"])
    vtt_resp.raise_for_status()
    out_vtt = f"downloads/{slug}_{ep}_{subtitle['label']}.vtt"
    with open(out_vtt, "wb") as f:
        f.write(vtt_resp.content)
    await status.edit("💾 Subtitle downloaded, uploading video…", parse_mode="html")

    # Upload video with progress callback
    async def progress_cb(sent, total_bytes):
        bar = ascii_progress(sent, total_bytes)
        await status.edit(f"🚀 Uploading… {bar}", parse_mode="html")

    await event.reply(file=out_mp4, progress_callback=progress_cb)
    await event.reply(file=out_vtt)
    await status.edit("<b>✅ Completed!</b>", parse_mode="html")

def main():
    client.start()
    print("🚀 Hianime userbot running…")
    client.run_until_disconnected()

if __name__ == "__main__":
    main()
