# handlers.py
import os, asyncio, logging
from telethon import events, Button
from fetcher import (
    search_anime,
    fetch_episodes,
    fetch_sources_and_referer,
    fetch_tracks,
)
from downloader import remux_hls, download_subtitle

STATE: dict = {}  # per-chat queue & cache

async def register_handlers(client):
    # ── SEARCH COMMAND ─────────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r"^/search (.+)"))
    async def on_search(event):
        query = event.pattern_match.group(1)
        msg = await event.reply("🔍 Searching…")
        animes = search_anime(query)
        if not animes:
            return await msg.edit("❌ No results.")
        buttons = [
            [Button.inline(a["name"], f"ANIME|{a['id']}")]
            for a in animes
        ]
        await msg.edit("🔎 Select anime:", buttons=buttons)

    # ── ANIME SELECTED ─────────────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"ANIME|")))
    async def on_select_anime(event):
        anime_id = event.data.decode().split("|", 1)[1]
        msg = await event.edit("📺 Fetching episodes…")
        eps = fetch_episodes(anime_id)
        if not eps:
            return await msg.edit("ℹ️ No episodes found.")
        # build buttons: one-per-episode + an ALL button
        rows = [[Button.inline("▶ Download ALL", f"ALL|{anime_id}")]]
        for ep in eps:
            label = f"Ep {ep['number']}: {ep.get('title','')}"
            rows.append([Button.inline(label, f"EP|{ep['episodeId']}")])
        await msg.edit("📃 Choose one or ALL:", buttons=rows)

    # ── SINGLE EPISODE ──────────────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"EP|")))
    async def on_single_episode(event):
        episode_id = event.data.decode().split("|",1)[1]
        await event.answer()  # remove “loading”
        await _download_episode(event.chat_id, episode_id, event)

    # ── QUEUE “ALL” EPISODES ───────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"ALL|")))
    async def on_all(event):
        anime_id = event.data.decode().split("|",1)[1]
        await event.answer()
        eps = fetch_episodes(anime_id)
        queue = [ep["episodeId"] for ep in eps]
        STATE[event.chat_id] = {"queue": queue}
        await event.edit(f"📥 Queued {len(queue)} episodes. Starting…")
        # process queue in background
        asyncio.create_task(_process_queue(event.chat_id))

# ── CORE DOWNLOAD LOGIC ─────────────────────────────────────────────────────────
async def _download_episode(chat_id: int, episode_id: str, ctx_event=None):
    """
    Fetch best-quality HLS + English subtitle, remux & send.
    If ctx_event is provided, uses it to edit status; else uses send_message.
    """
    # pick status updaters
    if ctx_event:
        edit = ctx_event.edit
    else:
        edit = lambda text, **k: client.send_message(chat_id, text, **k)

    status = await edit(f"⏳ Downloading {episode_id}…", parse_mode="html")

    # 1) HLS sources
    sources, referer = fetch_sources_and_referer(episode_id)
    hls = [s for s in sources if s.get("type")=="hls"]
    # pick highest quality
    def qval(s):
        q = s.get("quality","0p").rstrip("p")
        return int(q) if q.isdigit() else 0
    best = max(hls, key=qval)
    # 2) remux
    out_mp4 = f"downloads/{episode_id}.mp4"
    remux_hls(best["url"], referer, out_mp4)
    await status.edit("💾 Video ready, fetching subtitle…")

    # 3) English subtitle
    tracks = fetch_tracks(episode_id)
    eng = next((t for t in tracks if t.get("label","").lower().startswith("english")), None)
    if eng:
        sub_path = download_subtitle(eng, "downloads", episode_id)
    else:
        sub_path = None

    # 4) send files
    await client.send_file(chat_id, out_mp4, caption=f"<b>{episode_id}</b>", parse_mode="html")
    if sub_path:
        await client.send_file(chat_id, sub_path)
    await status.edit("✅ Done!")

async def _process_queue(chat_id: int):
    """
    Pops from STATE[chat_id]['queue'] until empty,
    sequencing downloads one after the other.
    """
    state = STATE.get(chat_id, {})
    queue = state.get("queue", [])
    while queue:
        episode_id = queue.pop(0)
        try:
            await _download_episode(chat_id, episode_id)
        except Exception as e:
            logging.exception("Queue download failed")
            await client.send_message(chat_id, f"❌ Failed on {episode_id}: {e}")
    await client.send_message(chat_id, "🏁 All done!")
