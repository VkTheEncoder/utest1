# handlers.py

import logging
import asyncio

from telethon import events
from config import STATE
import fetcher
import downloader


async def register_handlers(client):
    # ── /search command handler ────────────────────────────────────────────────
    @client.on(events.NewMessage(
        incoming=True,
        outgoing=True,
        pattern=r'^/search(?:@[\w_]+)?\s+(.+)$'
    ))
    async def search_handler(event):
        """
        Handles both bot (/search query) and userbot (/search query) messages.
        Extracts the query, calls fetcher.search_anime, and replies with up to 5 results.
        """
        query = event.pattern_match.group(1).strip()
        try:
            results = fetcher.search_anime(query)
        except Exception as e:
            logging.exception("Search request failed")
            return await event.reply(f"❌ Search error: {e}")

        if not results:
            return await event.reply("🔍 No results found.")

        # Build and send the reply
        lines = [f"🔍 Results for “{query}”:"]  
        for anime in results[:5]:
            name = anime.get("name", "Unknown")
            aid  = anime.get("id",   "—")
            lines.append(f"• {name}  (ID: {aid})")

        await event.reply("\n".join(lines))


    # ── Single‐episode callback ─────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"EP|")))
    async def on_single_episode(event):
        episode_id = event.data.decode().split("|", 1)[1]
        await event.answer()
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

        # populate the queue however you fetch your IDs:
        episodes = fetcher.get_all_episode_ids()
        STATE.setdefault(chat_id, {})["queue"] = episodes.copy()

        await event.respond("✅ Queued all episodes. Starting downloads…")
        # Process in background
        asyncio.create_task(_process_queue(event.client, chat_id))



async def _download_episode(client, chat_id: int, episode_id: str, ctx_event=None):
    """
    Downloads/remuxes a single episode and sends it.
    Uses ctx_event.edit(...) if provided, else sends a new message.
    """
    # choose whether we edit or send a fresh message
    if ctx_event:
        edit_fn = ctx_event.edit
    else:
        edit_fn = lambda text, **k: client.send_message(chat_id, text, **k)

    status = await edit_fn(
        f"⏳ Downloading <b>{episode_id}</b>…",
        parse_mode="html"
    )

    try:
        url     = fetcher.get_url(episode_id)
        out_mp4 = downloader.remux(url, episode_id)
        await client.send_file(
            chat_id,
            out_mp4,
            caption=f"<b>{episode_id}</b>",
            parse_mode="html"
        )
    finally:
        # always remove the “downloading” notice
        await status.delete()


async def _process_queue(client, chat_id: int):
    """
    Processes all queued episodes, one by one.
    """
    state = STATE.get(chat_id, {})
    queue = state.get("queue", [])

    while queue:
        episode_id = queue.pop(0)
        try:
            await _download_episode(client, chat_id, episode_id)
        except Exception as e:
            logging.exception("Queue download failed")
            await client.send_message(
                chat_id,
                f"❌ Failed on {episode_id}: {e}"
            )

    await client.send_message(chat_id, "✅ All done!")
