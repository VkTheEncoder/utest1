# handlers.py
import os
import time
import logging
from urllib.parse import urlparse, parse_qs

from telethon import events, Button
from fetcher import fetch_sources_and_referer, fetch_tracks
from downloader import remux_with_progress, download_subtitle

# In-memory state per chat
STATE = {}

# Regex to catch episode URLs
URL_EP = r'https?://hianimez?\.to/watch/[^?\s]+[?&]ep=\d+'

def build_progress_card(
    title: str,
    transferred: int,
    total: int,
    start: float,
    now: float
) -> str:
    """
    Returns an HTML-formatted progress card, e.g.:

    📥 Downloading File

    Size: 32.00 MB of 202.23 MB
    ⚡ Speed: 1.60 MB/s
    ⏱ Time Elapsed: 20s
    ⏳ ETA: 1m 46s
    📊 Progress: 15.8%
    """
    elapsed = now - start
    pct     = transferred / total * 100 if total else 0
    speed   = transferred / elapsed     if elapsed > 0 else 0
    eta     = (total - transferred) / speed if speed > 0 else 0

    return (
        f"<b>{title}</b>\n\n"
        f"Size: {transferred/1e6:.2f} MB of {total/1e6:.2f} MB\n"
        f"⚡ Speed: {speed/1e6:.2f} MB/s\n"
        f"⏱ Time Elapsed: {int(elapsed)}s\n"
        f"⏳ ETA: {int(eta)}s\n"
        f"📊 Progress: {pct:.1f}%"
    )

@client.on(events.NewMessage(pattern=URL_EP))
async def on_episode_link(event):
    url    = event.text.strip()
    parsed = urlparse(url)
    slug   = parsed.path.strip("/").split("/")[-1]
    ep     = parse_qs(parsed.query).get("ep", [None])[0]

    if not ep:
        return await event.reply("❌ Couldn’t find `ep=` in that URL.")

    # Fetch HLS sources + Referer
    sources, referer = fetch_sources_and_referer(slug, ep)
    hls_list = [s for s in sources if s.get("type") == "hls"]
    if not hls_list:
        return await event.reply("⚠️ No HLS streams available.")

    # Cache for the quality callback
    STATE[event.chat_id] = {
        "slug":      slug,
        "ep":        ep,
        "hls_list":  hls_list,
        "referer":   referer,
    }

    # Build inline buttons for each quality
    buttons = [
        Button.inline(s.get("quality", "auto"), f"Q|{i}")
        for i, s in enumerate(hls_list)
    ]
    keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]

    await event.reply(
        "<b>Select quality:</b>",
        buttons=keyboard,
        parse_mode="html"
    )

@client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"Q|")))
async def on_quality(event):
    # Parse which quality index
    _, idx_s = event.data.decode().split("|")
    idx      = int(idx_s)
    st       = STATE.get(event.chat_id)

    if not st:
        return await event.answer("Session expired; please resend the link.", alert=True)

    slug, ep, referer = st["slug"], st["ep"], st["referer"]
    m3u8_url         = st["hls_list"][idx]["url"]

    # Pick the first English subtitle track if it exists
    tracks = fetch_tracks(slug, ep)
    eng = next(
        (t for t in tracks if "english" in t.get("label","").lower()),
        None
    )

    # Status placeholder (we'll keep editing this message)
    status = await event.edit(
        "⏳ Preparing download…",
        parse_mode="html"
    )

    # 1) Download & remux with live progress
    out_mp4 = f"downloads/{slug}_{ep}.mp4"
    start    = time.time()

    def download_callback(transferred, total, ts):
        """Called every second by remux_with_progress."""
        now = time.time()
        card = build_progress_card(
            "📥 Downloading File",
            transferred, total, ts, now
        )
        # schedule the edit
        return event.edit(card, parse_mode="html")

    remux_with_progress(m3u8_url, referer, out_mp4, download_callback)

    # 2) Download subtitle if found
    if eng:
        await status.edit("💾 Downloading subtitle…", parse_mode="html")
        sub_path = download_subtitle(eng, "downloads", f"{slug}_{ep}")
    else:
        sub_path = None

    # 3) Upload with progress
    await status.edit("🚀 Uploading video…", parse_mode="html")
    up_start = time.time()

    async def upload_callback(sent, total):
        now = time.time()
        card = build_progress_card(
            "📤 Uploading File",
            sent, total, up_start, now
        )
        await event.edit(card, parse_mode="html")

    await event.reply(file=out_mp4, progress_callback=upload_callback)
    if sub_path:
        await event.reply(file=sub_path)

    # Final “all done” message
    await event.edit("<b>✅ Completed!</b>", parse_mode="html")
