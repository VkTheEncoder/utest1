# handlers.py
import os, time, asyncio
from urllib.parse import urlparse, parse_qs

from telethon import events, Button

from fetcher import (
    search_anime,
    fetch_episodes,
    fetch_sources_and_referer,
    fetch_tracks
)
from downloader import remux_with_progress, download_subtitle

# Per‐chat in‐memory state
STATE: dict[int, dict] = {}

# Regex patterns
URL_EP  = r'https?://hianimez?\.to/watch/[^?\s]+[?&]ep=\d+'
CMD_SRCH = r'^/search (.+)'

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
    # ── SEARCH ──────────────────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=CMD_SRCH))
    async def on_search(event):
        query = event.pattern_match.group(1)
        msg   = await event.reply("🔍 Searching…")
        animes = search_anime(query)
        if not animes:
            return await msg.edit("❌ No results.")

        buttons = [[Button.inline(a["name"], f"ANIME|{a['id']}")] for a in animes]
        await msg.edit("🔎 Select an anime:", buttons=buttons)

    # ── SELECT ANIME ─────────────────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"ANIME|")))
    async def on_select_anime(event):
        anime_id = event.data.decode().split("|",1)[1]
        msg      = await event.edit("📺 Fetching episodes…")
        eps      = fetch_episodes(anime_id)
        if not eps:
            return await msg.edit("ℹ️ No episodes found.")

        # Build one “Download ALL” button + one per episode
        rows = [[Button.inline("▶ Download ALL", f"ALL|{anime_id}")]]
        for ep in eps:
            label = f"Ep {ep['number']}"
            rows.append([Button.inline(label, f"EP|{ep['episodeId']}")])

        await msg.edit("📃 Choose an episode or ALL:", buttons=rows)

    # ── DOWNLOAD SINGLE EPISODE ─────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"EP|")))
    async def on_single_episode(event):
        episode_id = event.data.decode().split("|",1)[1]
        await event.answer()  # removes “Loading…” UI
        await _download_with_progress(event.chat_id, episode_id, client, event)

    # ── DOWNLOAD ALL EPISODES ────────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"ALL|")))
    async def on_all(event):
        anime_id = event.data.decode().split("|",1)[1]
        await event.answer()
        eps = fetch_episodes(anime_id)
        queue = [ep["episodeId"] for ep in eps]
        STATE[event.chat_id] = {"queue": queue}
        await event.edit(f"📥 Queued {len(queue)} episodes. Starting…")
        asyncio.create_task(_process_queue(event.chat_id, client))

# ── CORE DOWNLOAD FUNCTION ─────────────────────────────────────────────────────
async def _download_with_progress(chat_id, episode_id, client, ctx_event=None):
    """
    Downloads one episode (HLS→MP4 + English .vtt) with rich
    download/upload progress cards. If ctx_event is provided,
    uses it to edit a single status message.
    """
    # 1) Sources & Referer
    sources, referer = fetch_sources_and_referer(episode_id.split("?")[0],
                                                parse_qs(episode_id.split("?")[1])["ep"][0])
    # pick best HLS by numeric quality
    hls_list = [s for s in sources if s.get("type")=="hls"]
    best = max(hls_list, key=lambda s: int(s.get("quality","0p")[:-1]))
    m3u8 = best["url"]

    # 2) find English subtitle if any
    tracks = fetch_tracks(episode_id.split("?")[0], parse_qs(episode_id.split("?")[1])["ep"][0])
    eng = next((t for t in tracks if "english" in t.get("label","").lower()), None)

    # 3) status message setup
    if ctx_event:
        status = await ctx_event.edit("⏳ Preparing download…", parse_mode="html")
    else:
        status = await client.send_message(chat_id, "⏳ Preparing download…", parse_mode="html")

    # 4) Download & remux with progress
    out_mp4 = f"downloads/{episode_id}.mp4"
    start   = time.time()
    def dl_cb(transferred, total, t0):
        now = time.time()
        card = build_progress_card("📥 Downloading File", transferred, total, t0, now)
        return status.edit(card, parse_mode="html")

    remux_with_progress(m3u8, referer, out_mp4, dl_cb)

    # 5) Download subtitle
    if eng:
        await status.edit("💾 Downloading subtitle…", parse_mode="html")
        sub_path = download_subtitle(eng, "downloads", episode_id)
    else:
        sub_path = None

    # 6) Upload with progress
    await status.edit("🚀 Uploading video…", parse_mode="html")
    up_start = time.time()
    async def up_cb(sent, total):
        now = time.time()
        card = build_progress_card("📤 Uploading File", sent, total, up_start, now)
        await status.edit(card, parse_mode="html")

    await client.send_file(chat_id, out_mp4, progress_callback=up_cb)
    if sub_path:
        await client.send_file(chat_id, sub_path)

    await status.edit("<b>✅ Completed!</b>", parse_mode="html")

# ── QUEUE PROCESSOR ─────────────────────────────────────────────────────────────
async def _process_queue(chat_id, client):
    state = STATE.get(chat_id, {})
    queue = state.get("queue", [])
    while queue:
        ep_id = queue.pop(0)
        try:
            await _download_with_progress(chat_id, ep_id, client)
        except Exception as e:
            logging.exception("Queue download failed")
            await client.send_message(chat_id, f"❌ Failed on {ep_id}: {e}")
    await client.send_message(chat_id, "🏁 All done!")
