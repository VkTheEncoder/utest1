# handlers.py

import os
import logging
import asyncio

from telethon import events, Button
from config import STATE
import fetcher
import downloader

# Base folder where we'll store per-episode downloads
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "./downloads")


async def register_handlers(client):
    # ── /search command: list matching anime ────────────────────────────────────
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

        buttons = [
            [Button.inline(a["name"], data=f"ANIME|{a['id']}".encode())]
            for a in results[:5]
        ]
        await event.reply("🔍 Select an anime:", buttons=buttons)


    # ── Anime selected: fetch its episodes ────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"ANIME|")))
    async def on_select_anime(event):
        await event.answer()
        anime_id = event.data.decode().split("|", 1)[1]
        chat_id  = event.chat_id

        # fetch_episodes returns list of { number, title, episodeId, … }
        try:
            eps = fetcher.fetch_episodes(anime_id)
        except Exception as e:
            logging.exception("Failed to fetch episodes")
            return await event.edit(f"❌ Could not load episodes for `{anime_id}`")

        if not eps:
            return await event.edit("⚠️ No episodes found.")

        # store queue for “Download All”
        STATE.setdefault(chat_id, {})["queue"] = [e["episodeId"] for e in eps]

        # button per episode
        buttons = [
            [Button.inline(f"{e['number']}. {e.get('title','')}", data=f"EP|{e['episodeId']}".encode())]
            for e in eps
        ]
        # plus a “download all” button
        buttons.append([Button.inline("▶️ Download All", data=f"ALL|{anime_id}".encode())])

        await event.edit(
            f"📺 Found {len(eps)} episodes. Pick one or Download All:",
            buttons=buttons
        )


    # ── Single-episode download ───────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"EP|")))
    async def on_single_episode(event):
        await event.answer()
        episode_id = event.data.decode().split("|",1)[1]
        await _download_episode(event.client, event.chat_id, episode_id, ctx_event=event)


    # ── “Download All” handler ────────────────────────────────────────────────
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"ALL|")))
    async def on_all(event):
        await event.answer()
        chat_id = event.chat_id
        queue = STATE.get(chat_id, {}).get("queue", [])
        if not queue:
            return await event.edit("⚠️ Nothing queued.")

        await event.edit("✅ Queued all episodes. Starting…")
        asyncio.create_task(_process_queue(event.client, chat_id))



async def _download_episode(client, chat_id: int, episode_id: str, ctx_event=None):
    """
    Downloads a single episode (video + subtitle) and sends it.
    """
    # choose edit vs send_message
    if ctx_event:
        edit_fn = ctx_event.edit
    else:
        edit_fn = lambda txt, **k: client.send_message(chat_id, txt, **k)

    status = await edit_fn(f"⏳ Downloading `{episode_id}`…", parse_mode="markdown")

    try:
        # prepare folder
        out_dir = os.path.join(DOWNLOAD_DIR, episode_id)
        os.makedirs(out_dir, exist_ok=True)

        # 1) get HLS source + referer
        sources, referer = fetcher.fetch_sources_and_referer(episode_id)
        if not sources:
            raise RuntimeError("No video sources available")
        m3u8_url = sources[0].get("url") or sources[0].get("file")
        if not m3u8_url:
            raise RuntimeError("Malformed source record")

        # 2) remux to MP4 (blocks thread, so run in executor)
        out_mp4 = os.path.join(out_dir, f"{episode_id}.mp4")
        await asyncio.get_event_loop().run_in_executor(
            None,
            downloader.remux_hls,
            m3u8_url,
            referer,
            out_mp4
        )

        # 3) fetch subtitles
        tracks = fetcher.fetch_tracks(episode_id)
        sub_path = None
        for tr in tracks:
            label = tr.get("label", tr.get("lang", "")).split()[0].lower()
            if label in ("en", "eng", "english"):
                sub_path = downloader.download_subtitle(tr, out_dir, episode_id)
                break

        # 4) send video
        await client.send_file(
            chat_id,
            out_mp4,
            caption=f"▶️ `{episode_id}`",
            parse_mode="markdown"
        )

        # 5) send subtitle if found
        if sub_path and os.path.exists(sub_path):
            await client.send_file(
                chat_id,
                sub_path,
                caption="📄 Subtitle",
                file_name=os.path.basename(sub_path)
            )
        else:
            logging.info("No subtitle found for %s", episode_id)

    except Exception as e:
        logging.exception("Download error")
        await client.send_message(chat_id, f"❌ Error with `{episode_id}`: {e}")

    finally:
        await status.delete()


async def _process_queue(client, chat_id: int):
    """
    Drain the STATE queue for this chat, one epi at a time.
    """
    queue = STATE.get(chat_id, {}).get("queue", [])
    while queue:
        ep = queue.pop(0)
        try:
            await _download_episode(client, chat_id, ep)
        except Exception:
            logging.exception("Queued download failed")
            await client.send_message(chat_id, f"❌ Failed on `{ep}`")

    await client.send_message(chat_id, "✅ All downloads complete!")
