# handlers.py

import os
import logging
import asyncio

from telethon import events, Button
from config import STATE
import fetcher
import downloader

# Where all downloads go
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "./downloads")


async def register_handlers(client):
    # ── /search command: list matching anime ────────────────────────────────────
    @client.on(events.NewMessage(
        incoming=True,
        outgoing=True,
        pattern=r'^/search(?:@[\w_]+)?\s+(.+)$'
    ))
    async def search_handler(event):
        query   = event.pattern_match.group(1).strip()
        chat_id = event.chat_id

        try:
            results = fetcher.search_anime(query)
        except Exception as e:
            logging.exception("Search failed")
            return await event.reply(f"❌ Search error: {e}")

        if not results:
            return await event.reply("🔍 No results found.")

        state = STATE.setdefault(chat_id, {})
        # store id→title for later
        state.setdefault("anime_meta", {})
        for a in results[:5]:
            state["anime_meta"][a["id"]] = a["name"]

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
        state    = STATE.setdefault(chat_id, {})

        anime_name = state.get("anime_meta", {}).get(anime_id, anime_id)
        state["current_anime_name"] = anime_name

        try:
            eps = fetcher.fetch_episodes(anime_id)
        except Exception:
            logging.exception("Failed to fetch episodes")
            return await event.edit(
                f"❌ Could not load episodes for **{anime_name}**",
                parse_mode="markdown"
            )

        if not eps:
            return await event.edit("⚠️ No episodes found.")

        # queue and map episodeId→number
        state["queue"] = [e["episodeId"] for e in eps]
        state["episodes_map"] = {e["episodeId"]: e["number"] for e in eps}

        buttons = [
            [Button.inline(f"{e['number']}. {e.get('title','')}",
                           data=f"EP|{e['episodeId']}".encode())]
            for e in eps
        ]
        buttons.append([Button.inline("▶️ Download All", data=f"ALL|{anime_id}".encode())])

        await event.edit(
            f"📺 Found {len(eps)} episodes of **{anime_name}**.\nPick one or Download All:",
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
        queue   = STATE.get(chat_id, {}).get("queue", [])
        if not queue:
            return await event.edit("⚠️ Nothing queued.")

        await event.edit("✅ Queued all episodes. Starting downloads…")
        asyncio.create_task(_process_queue(event.client, chat_id))



async def _download_episode(client, chat_id: int, episode_id: str, ctx_event=None):
    """
    Downloads one episode (video + subtitle), renames the MP4 to
    "<Anime Title> ep-<No>.mp4", and sends both files.
    Subtitle selection prioritizes: eng-2.vtt, en.vtt, eng.vtt, english.vtt
    """
    state      = STATE.get(chat_id, {})
    anime_name = state.get("current_anime_name", episode_id)
    ep_num     = state.get("episodes_map", {}).get(episode_id, "")
    safe_anime = "".join(c for c in anime_name if c.isalnum() or c in " _-").strip()

    # choose edit vs new message
    if ctx_event:
        edit_fn = ctx_event.edit
    else:
        edit_fn = lambda txt, **k: client.send_message(chat_id, txt, **k)

    status = await edit_fn(
        f"⏳ Downloading **{anime_name}** ep-{ep_num}…",
        parse_mode="markdown"
    )

    try:
        out_dir = os.path.join(DOWNLOAD_DIR, safe_anime)
        os.makedirs(out_dir, exist_ok=True)

        # 1) Fetch & remux HLS → MP4
        sources, referer = fetcher.fetch_sources_and_referer(episode_id)
        m3u8     = sources[0].get("url") or sources[0].get("file")
        mp4_name = f"{safe_anime} ep-{ep_num}.mp4"
        out_mp4  = os.path.join(out_dir, mp4_name)

        await asyncio.get_event_loop().run_in_executor(
            None,
            downloader.remux_hls,
            m3u8, referer, out_mp4
        )

        # 2) Pick subtitle by filename priority
        tracks   = fetcher.fetch_tracks(episode_id)
        sub_path = None

        priority = ["eng-2.vtt", "en.vtt", "eng.vtt", "english.vtt"]
        selected = None

        for want in priority:
            for tr in tracks:
                url   = tr.get("file") or tr.get("url") or ""
                fname = url.split("/")[-1].lower()
                if fname == want:
                    selected = tr
                    break
            if selected:
                break

        if selected:
            try:
                sub_path = downloader.download_subtitle(selected, out_dir, episode_id)
            except Exception:
                logging.exception("Subtitle download failed for %s", want)

        # 3) Send video
        await client.send_file(
            chat_id,
            out_mp4,
            caption=f"▶️ **{anime_name}** ep-{ep_num}",
            parse_mode="markdown"
        )

        # 4) Send subtitle only if it matches our priority list
        if sub_path and os.path.exists(sub_path):
            await client.send_file(
                chat_id,
                sub_path,
                caption="📄 Subtitle",
                file_name=os.path.basename(sub_path)
            )

    except Exception:
        logging.exception("Download error")
        await client.send_message(
            chat_id,
            f"❌ Failed downloading **{anime_name}** ep-{ep_num}"
        )

    finally:
        await status.delete()


async def _process_queue(client, chat_id: int):
    """
    Drain the STATE queue for this chat, one episode at a time.
    """
    queue = STATE.get(chat_id, {}).get("queue", [])
    while queue:
        ep = queue.pop(0)
        try:
            await _download_episode(client, chat_id, ep)
        except Exception:
            logging.exception("Queued download failed")
            await client.send_message(chat_id, f"❌ Error on ep-{ep}")
    await client.send_message(chat_id, "✅ All downloads complete!")
