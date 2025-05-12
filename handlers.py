import os
import time
import logging
from urllib.parse import urlparse, parse_qs
from telethon import events, Button
from fetcher import fetch_sources_and_referer, fetch_tracks
from downloader import remux_with_progress, download_subtitle

# In-memory state per chat
STATE = {}

def build_progress_card(
    title: str,
    transferred: int,
    total: int,
    start: float,
    now: float
) -> str:
    elapsed = now - start
    pct = transferred / total * 100 if total else 0
    speed = transferred / elapsed if elapsed>0 else 0
    eta = (total - transferred) / speed if speed>0 else 0

    return (
        f"<b>{title}</b>\n\n"
        f"Size: {transferred/1e6:.2f} MB of {total/1e6:.2f} MB\n"
        f"⚡ Speed: {speed/1e6:.2f} MB/s\n"
        f"⏱ Elapsed: {int(elapsed)}s\n"
        f"⏳ ETA: {int(eta)}s\n"
        f"📊 Progress: {pct:.1f}%"
    )

URL_EP = r'https?://hianimez?\.to/watch/[^?\s]+[?&]ep=\d+'

@client.on(events.NewMessage(pattern=URL_EP))
async def on_episode_link(event):
    url = event.text.strip()
    p = urlparse(url)
    slug = p.path.strip("/").split("/")[-1]
    ep   = parse_qs(p.query).get("ep", [None])[0]
    if not ep:
        return await event.reply("❌ Missing `ep=` in URL.")

    sources, referer = fetch_sources_and_referer(slug, ep)
    hls = [s for s in sources if s.get("type")=="hls"]
    if not hls:
        return await event.reply("⚠️ No HLS streams found.")

    # Cache for callback
    STATE[event.chat_id] = {
        "slug": slug,
        "ep": ep,
        "hls": hls,
        "referer": referer
    }

    # Pick best quality by numeric trailing
    buttons = [
        Button.inline(s.get("quality","auto"), f"Q|{i}")
        for i,s in enumerate(hls)
    ]
    kb = [buttons[i:i+2] for i in range(0,len(buttons),2)]
    await event.reply("<b>Select quality:</b>", buttons=kb, parse_mode="html")

@client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"Q|")))
async def on_quality(event):
    idx = int(event.data.decode().split("|")[1])
    st  = STATE.get(event.chat_id)
    if not st:
        return await event.answer("Session expired.", alert=True)

    slug, ep, referer = st["slug"], st["ep"], st["referer"]
    m3u8 = st["hls"][idx]["url"]

    # pick English subtitle if any
    tracks = fetch_tracks(slug, ep)
    eng = next((t for t in tracks if "english" in t.get("label","").lower()), None)

    status = await event.edit("⏳ Preparing download…", parse_mode="html")

    out_mp4 = f"downloads/{slug}_{ep}.mp4"
    # DOWNLOAD & REMUX with live card updates
    def dl_cb(transferred, total, start):
        now = time.time()
        text = build_progress_card("📥 Downloading File", transferred, total, start, now)
        # we must schedule the edit onto the event loop
        return event.edit(text, parse_mode="html")

    remux_with_progress(m3u8, referer, out_mp4, dl_cb)

    # SUBTITLE
    if eng:
        await status.edit("💾 Downloading subtitle…", parse_mode="html")
        sub_path = download_subtitle(eng, "downloads", f"{slug}_{ep}")
    else:
        sub_path = None

    # UPLOAD with progress card
    start = time.time()

    async def up_cb(sent, total):
        now = time.time()
        text = build_progress_card("📤 Uploading File", sent, total, start, now)
        await event.edit(text, parse_mode="html")

    await event.edit("🚀 Uploading file…", parse_mode="html")
    await event.reply(file=out_mp4, progress_callback=up_cb)
    if sub_path:
        await event.reply(file=sub_path)
    await event.edit("<b>✅ Completed!</b>", parse_mode="html")
