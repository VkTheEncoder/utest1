# handlers.py

import os
import logging
import asyncio

from telethon import events, Button
from config import STATE
import fetcher
import downloader


async def register_handlers(client):
    # ── /search command: shows inline buttons for anime results ───────────────
    @client.on(events.NewMessage(
        incoming=True,
        outgoing=True,
        pattern=r'^/search(?:@[\w_]+)?\s+(.+)$'
    ))
    async def search_handler(event):
        query = event.pattern_match.group(1).strip()
        try:
            results = fetcher.search_anime(query)
        except Exception as e:
            logging.exception("Search failed")
            return await event.reply(f"❌ Search error: {e}")

        if not results:
            return await event.reply("🔍 No results found.")

        # Build an inline keyboard: one button per anime
        buttons = [
            [Button.inline(anime["name"], data=f"ANIME|{anime['id']}".encode())]
            for anime in results[:5]
        ]
        await event.reply(
            "🔍 Select an anime:",
            buttons=buttons
        )


    # ── Anime selected: list its episodes ─────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"ANIME|")))
    async def on_select_anime(event):
        await event.answer()
        anime_id = event.data.decode().split("|", 1)[1]

        # Fetch episode list for this anime
        try:
            episodes = fetcher.get_episode_list(anime_id)  # → [{ "id": "...", "title": "Episode 1" }, …]
        except Exception as e:
            logging.exception("Failed to fetch episodes")
            return await event.edit(f"❌ Could not load episodes for `{anime_id}`")

        # Store episodes in STATE so "Download All" can access them
        chat_id = event.chat_id
        STATE.setdefault(chat_id, {})["queue"] = [ep["id"] for ep in episodes]

        # Build buttons: one per episode, plus a "Download All" button
        buttons = [
            [Button.inline(ep["title"], data=f"EP|{ep['id']}".encode())]
            for ep in episodes
        ]
        buttons.append([Button.inline("Download All ▶️", data=f"ALL|{anime_id}".encode())])

        await event.edit(
            f"📺 `{len(episodes)}` episodes found. Pick one or download all:",
            buttons=buttons,
            parse_mode="markdown"
        )


    # ── Single‐episode callback ─────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"EP|")))
    async def on_single_episode(event):
        await event.answer()
        episode_id = event.data.decode().split("|", 1)[1]
        await _download_episode(
            event.client,
            event.chat_id,
            episode_id,
            ctx_event=event
        )


    # ── “Download All” callback ────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"ALL|")))
    async def on_all(event):
        await event.answer()
        chat_id = event.chat_id

        # The queue was populated in on_select_anime
        queue = STATE.get(chat_id, {}).get("queue", [])
        if not queue:
            return await event.edit("❌ Nothing in queue to download.")

        await event.edit("✅ Queued all episodes. Starting downloads…")
        asyncio.create_task(_process_queue(event.client, chat_id))


async def _download_episode(client, chat_id: int, episode_id: str, ctx_event=None):
    """
    Downloads one episode (video + subtitle) and sends it.
    Edits ctx_event if provided, else sends a fresh message.
    """
    # Choose edit vs. new message
    if ctx_event:
        edit_fn = ctx_event.edit
    else:
        edit_fn = lambda text, **k: client.send_message(chat_id, text, **k)

    status = await edit_fn(f"⏳ Downloading `{episode_id}`…", parse_mode="markdown")

    try:
        # 1) Download & remux video
        url     = fetcher.get_url(episode_id)
        out_mp4 = downloader.remux(url, episode_id)

        # 2) Attempt subtitle download, checking common codes
        sub_path = None
        for lang in ("en", "eng", "english"):
            try:
                sub_url = fetcher.get_subtitle_url(episode_id, lang)
                sub_path = downloader.download_subtitle(sub_url, episode_id, lang)
                break
            except Exception:
                continue

        # 3) Send video file
        await client.send_file(
            chat_id,
            out_mp4,
            caption=f"▶️ `{episode_id}`",
            parse_mode="markdown"
        )

        # 4) If subtitle found, send it too
        if sub_path and os.path.exists(sub_path):
            await client.send_file(
                chat_id,
                sub_path,
                caption="📄 Subtitle",
                file_name=os.path.basename(sub_path)
            )
        else:
            # quietly log; no more “not found” errors to user
            logging.info("No subtitle found for %s", episode_id)

    finally:
        # remove the “downloading” notice
        await status.delete()


async def _process_queue(client, chat_id: int):
    """
    Processes all queued episode IDs in STATE[chat_id]['queue'] one by one.
    """
    queue = STATE.get(chat_id, {}).get("queue", [])

    while queue:
        episode_id = queue.pop(0)
        try:
            await _download_episode(client, chat_id, episode_id)
        except Exception:
            logging.exception("Failed during queued download")
            await client.send_message(
                chat_id,
                f"❌ Error downloading `{episode_id}`"
            )

    await client.send_message(chat_id, "✅ All done!")
