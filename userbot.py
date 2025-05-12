#!/usr/bin/env python3
import os, logging, subprocess, time
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
import requests

#─── Config ────────────────────────────────────────────────────────────────────
load_dotenv()
API_ID   = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
API_BASE = os.getenv("ANIWATCH_API_BASE", "http://localhost:4000/api/v2/hianime")
if not API_ID or not API_HASH:
    raise RuntimeError("API_ID/API_HASH not set")

logging.basicConfig(level=logging.INFO)
client = TelegramClient('session', API_ID, API_HASH)

#─── In-memory state ─────────────────────────────────────────────────────────────
# key = f"{chat_id}:{slug}:{ep}"
STATE = {}

#─── Helpers ────────────────────────────────────────────────────────────────────
def fetch_sources(slug, ep):
    data = requests.get(
        f"{API_BASE}/episode/sources",
        params={"animeEpisodeId":f"{slug}?ep={ep}","server":"hd-1","category":"sub"}
    ).json()["data"]
    return data["sources"], data["headers"].get("Referer")

def ascii_progress(current, total, length=20):
    pct = int(current/total * 100) if total else 0
    filled = int(pct/100 * length)
    bar = "█"*filled + "─"*(length-filled)
    return f"[{bar}] {pct:3d}%"

#─── 1) CATCH EPISODE URL & SHOW QUALITY BUTTONS ─────────────────────────────────
@client.on(events.NewMessage(pattern=r'https?://hianimez?\.to/watch/[^?\s]+[?&]ep=\d+'))
async def on_episode_link(event):
    url = event.raw_text.strip()
    p = urlparse(url); slug = p.path.split("/")[-1]
    ep = parse_qs(p.query)["ep"][0]

    # fetch your HLS streams
    sources, referer = fetch_sources(slug, ep)
    hls = [s for s in sources if s["type"]=="hls"]
    if not hls:
        return await event.reply("❌ No HLS source found.")

    # save to STATE
    STATE[f"{event.chat_id}:{slug}:{ep}"] = {"hls":hls, "referer":referer}

    # send an HTML-formatted prompt with inline buttons
    buttons = [
        Button.inline(f"{s.get('quality','auto')}", f"Q|{slug}|{ep}|{i}")
        for i,s in enumerate(hls)
    ]
    await event.reply(
        "<b>Select quality:</b>",
        buttons=[buttons[i:i+2] for i in range(0,len(buttons),2)],
        parse_mode="html"
    )

#─── 2) QUALITY CALLBACK → ASK WHICH SUBTITLES ─────────────────────────────────
@client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"Q|")))
async def on_quality(event):
    _, slug, ep, idx = event.data.decode().split("|")
    key = f"{event.chat_id}:{slug}:{ep}"
    info = STATE.get(key)
    if not info:
        return await event.answer("Session expired, please resend the link.", alert=True)

    idx = int(idx)
    info["choice_idx"] = idx

    # fetch tracks for subtitles
    data = requests.get(
        f"{API_BASE}/episode/sources",
        params={"animeEpisodeId":f"{slug}?ep={ep}","server":"hd-1","category":"sub"}
    ).json()["data"]
    tracks = [t for t in data.get("tracks",[]) if t["kind"]=="captions"]
    if not tracks:
        return await event.edit("⚠️ No subtitles available for this episode.")

    # save tracks and prompt
    info["tracks"] = tracks
    buttons = [
        Button.inline(t["label"], f"S|{slug}|{ep}|{i}")
        for i,t in enumerate(tracks)
    ]
    await event.edit(
        "<b>Select subtitle:</b>",
        buttons=[buttons[i:i+2] for i in range(0,len(buttons),2)],
        parse_mode="html"
    )

#─── 3) SUBTITLE CALLBACK → DOWNLOAD/PROGRESS/UPLOAD ────────────────────────────
@client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"S|")))
async def on_subtitle(event):
    _, slug, ep, tidx = event.data.decode().split("|")
    key = f"{event.chat_id}:{slug}:{ep}"
    info = STATE.get(key)
    if not info:
        return await event.answer("Session expired, please resend link.", alert=True)

    hls = info["hls"][info["choice_idx"]]["url"]
    referer = info["referer"]
    subtitle = info["tracks"][int(tidx)]  # selected track

    status = await event.edit("⏳ Starting download…", parse_mode="html")

    # 3a) remux with ffmpeg & show pseudo-progress
    out_mp4 = f"downloads/{slug}_{ep}.mp4"
    cmd = ["ffmpeg","-y","-headers",f"Referer: {referer}\r\n","-i",hls,
           "-c","copy",out_mp4]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
    total_dur = None
    while True:
        line = proc.stderr.readline()
        if not line and proc.poll() is not None:
            break
        # try to parse "Duration: 00:23:52.10" to total seconds
        if "Duration:" in line:
            h,m,s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            total_dur = int(h)*3600+int(m)*60+float(s)
        # parse "time=00:01:23.45"
        if "time=" in line and total_dur:
            ts = line.split("time=")[1].split(" ")[0]
            h,m,s = ts.split(":")
            cur = int(h)*3600+int(m)*60+float(s)
            bar = ascii_progress(cur, total_dur)
            await status.edit(f"⏳ Remuxing… {bar}")

    # 3b) download chosen subtitle
    vtt_url = subtitle["file"]
    vtt_resp = requests.get(vtt_url)
    vtt_resp.raise_for_status()
    out_vtt = f"downloads/{slug}_{ep}_{subtitle['label']}.vtt"
    open(out_vtt,"wb").write(vtt_resp.content)
    await status.edit("💾 Subtitle downloaded, uploading video…")

    # 3c) upload video with Telethon progress_callback
    async def upload_progress(sent, total):
        bar = ascii_progress(sent, total)
        await status.edit(f"🚀 Uploading… {bar}")

    await event.reply(file=out_mp4, progress_callback=upload_progress)
    await event.reply(file=out_vtt)

    await status.edit("<b>✅ Completed!</b>", parse_mode="html")

def main():
    client.start()
    print("🚀 Userbot started")
    client.run_until_disconnected()

if __name__=="__main__":
    main()
