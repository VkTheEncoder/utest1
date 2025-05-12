# handlers.py
import os
import time
import asyncio
import logging
from urllib.parse import urlparse, parse_qs

from telethon import events, Button

from fetcher import (
    search_anime,
    fetch_episodes,
    fetch_sources_and_referer,
    fetch_tracks
)
from downloader import remux_with_progress, download_subtitle

# Per-chat state
STATE: dict[int, dict] = {}

# Command & URL patterns
CMD_SRCH = r'^/search (.+)'
URL_EP   = r'https?://hianimez?\.to/watch/[^?\s]+[?&]ep=\d+'


def build_progress_card(title, transferred, total, start, now):
    elapsed = now - start
    pct     = transferred / total * 100 if total else 0
    speed   = transferred / elapsed     if elapsed>0 else 0
    eta     = (total - transferred) / speed if speed>0 else 0

    return (
        f"<b>{title}</b>\n\n"
        f"Size: {transferred/1e6:.2f} MB of {total/1e6:.2f} MB\n"
        f"⚡ Speed: {speed/1e6:.2f} MB/s\n"
        f"⏱ Elapsed: {int(elapsed)}s\n"
        f"⏳ ETA: {int(eta)}s\n"
        f"📊 Progress: {pct:.1f}%"
    )


async def register_handlers(client):
    # ── /search ────────────────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=CMD_SRCH))
    async def on_search(event):
        query = event.pattern_match.group(1)
        msg   = await event.reply("🔍 Searching…")
        results = search_anime(query)
        if not results:
            return await msg.edit("❌ No matches.")
        buttons = [[Button.inline(a["name"], f"ANIME|{a['id']}")] for a in results]
        await msg.edit("🔎 Select an anime:", buttons=buttons)

    # ── ANIME ► EPISODES ──────────────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"ANIME|")))
    async def on_select_anime(event):
        anime_id = event.data.decode().split("|",1)[1]
        msg = await event.edit("📺 Fetching episodes…")
        eps = fetch_episodes(anime_id)
        if not eps:
            return await msg.edit("ℹ️ No episodes found.")
        rows = [[Button.inline("▶ Download ALL", f"ALL|{anime_id}")]]
        for ep in eps:
            rows.append([Button.inline(f"Ep {ep['number']}", f"EP|{ep['episodeId']}")])
        await msg.edit("📃 Choose an episode or ALL:", buttons=rows)

    # ── EPISODE ► SINGLE DOWNLOAD ───────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"EP|")))
    async def on_single_episode(event):
        episode_id = event.data.decode().split("|",1)[1]
        await event.answer()
        await _download_with_progress(event.chat_id, episode_id, client, event)

    # ── EPISODE ► QUEUE ALL ─────────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"ALL|")))
    async def on_all(event):
        anime_id = event.data.decode().split("|",1)[1]
        await event.answer()
        eps = fetch_episodes(anime_id)
        queue = [ep["episodeId"] for ep in eps]
        STATE[event.chat_id] = {"queue": queue}
        await event.edit(f"📥 Queued {len(queue)} episodes. Starting…")
        asyncio.create_task(_process_queue(event.chat_id, client))


async def _download_with_progress(chat_id, episode_id, client, ctx_event=None):
    # 1) fetch sources
    sources, referer = fetch_sources_and_referer(episode_id)
    hls_list = [s for s in sources if s.get("type")=="hls"]
    best = max(hls_list, key=lambda s: int(s.get("quality","0p")[:-1]))
    m3u8 = best["url"]

    # 2) subtitle
    tracks = fetch_tracks(episode_id)
    eng = next((t for t in tracks if "english" in t.get("label","").lower()), None)

    # 3) status message
    if ctx_event:
        status = await ctx_event.edit("⏳ Preparing download…", parse_mode="html")
    else:
        status = await client.send_message(chat_id, "⏳ Preparing download…", parse_mode="html")

    # 4) download/remux
    out_mp4 = f"downloads/{episode_id}.mp4"
    t0 = time.time()
    def dl_cb(transferred, total, start):
        card = build_progress_card("📥 Downloading File", transferred, total, start, time.time())
        asyncio.create_task(status.edit(card, parse_mode="html"))

    remux_with_progress(m3u8, referer, out_mp4, dl_cb)

    # 5) subtitle file
    if eng:
        await status.edit("💾 Downloading subtitle…", parse_mode="html")
        sub_path = download_subtitle(eng, "downloads", episode_id)
    else:
        sub_path = None

    # 6) upload
    await status.edit("🚀 Uploading video…", parse_mode="html")
    up0 = time.time()
    async def up_cb(sent, total):
        card = build_progress_card("📤 Uploading File", sent, total, up0, time.time())
        asyncio.create_task(status.edit(card, parse_mode="html"))

    await client.send_file(chat_id, out_mp4, progress_callback=up_cb)
    if sub_path:
        await client.send_file(chat_id, sub_path)

    await status.edit("<b>✅ Completed!</b>", parse_mode="html")


async def _process_queue(chat_id, client):
    queue = STATE.get(chat_id, {}).get("queue", [])
    while queue:
        eid = queue.pop(0)
        try:
            await _download_with_progress(chat_id, eid, client)
        except Exception as e:
            logging.exception("Queue error")
            await client.send_message(chat_id, f"❌ Failed on {eid}: {e}")
    await client.send_message(chat_id, "🏁 All done!")
